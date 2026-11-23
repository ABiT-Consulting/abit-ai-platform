"""Durable diagnostics and bounded retries for reconciliation of an original run.

This module never submits development work, changes invocation/control state, or
creates a scheduler. Callers retain all original identity, context, authorization
and publication guards. Eligibility is a lower bound for the existing event/scan
loop, not a promise that a job will run at that time.

Only fixed metadata is appended to the existing hash-chained governance audit.
No prompts, responses, credentials, exception text, or lease tokens are recorded.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import sqlite3
import time
from uuid import uuid4

START = "original_recovery_started"
STAGE = "original_recovery_stage"
END = "original_recovery_finished"
PROGRESS = "original_recovery_progress"
ACTOR = "server-original-recovery"
IDENTITY_KEYS = (
    "invocation_id", "erp_project", "erp_task", "agent_id", "target_role",
    "automation_run_id", "packet_hash", "lease_fence",
)
STAGES = frozenset({
    "original_observation", "original_reconciliation", "publication_reconciliation",
})
OUTCOMES = frozenset({
    "original_pending", "original_recovered", "publication_pending", "published",
    "authorization_held", "context_changed", "identity_changed", "validation_failed",
    "observation_timeout", "publication_timeout", "reconciliation_timeout",
    "observation_failed", "publication_failed", "reconciliation_failed", "interrupted",
})
ERROR_OUTCOMES = OUTCOMES - {
    "original_pending", "original_recovered", "publication_pending", "published",
}
_ACTIVE = ContextVar("original_recovery_attempt", default=None)
log = logging.getLogger(__name__)


class RecoveryContextChanged(ValueError):
    """Fresh business context differs; the original evidence remains fenced."""


class RecoveryIdentityChanged(ValueError):
    """An attempt cannot be rebound to another original run or packet."""


class RecoveryStageTimeout(TimeoutError):
    def __init__(self, stage):
        self.stage = stage
        super().__init__("Original recovery stage timed out")


@dataclass(frozen=True)
class RecoveryPolicy:
    # The relay observation already has a 35s HTTP timeout. The 45s envelope
    # includes account/receipt reads. Publication permits one bounded ERP call;
    # the overall reconciliation permits artifact reads and existing gates.
    observation_timeout_seconds: float = 45
    publication_timeout_seconds: float = 60
    reconciliation_timeout_seconds: float = 300
    backoff_initial_seconds: int = 60
    backoff_max_seconds: int = 1800

    def __post_init__(self):
        for key in ("observation_timeout_seconds", "publication_timeout_seconds",
                    "reconciliation_timeout_seconds"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1800:
                raise ValueError("Invalid original recovery timeout")
        if (type(self.backoff_initial_seconds) is not int
                or type(self.backoff_max_seconds) is not int
                or not 1 <= self.backoff_initial_seconds <= self.backoff_max_seconds <= 1800):
            raise ValueError("Invalid original recovery backoff")

    @classmethod
    def from_config(cls, config=None):
        config = config or {}
        return cls(**{
            name: config["agentic_recovery_" + name]
            for name in cls.__dataclass_fields__
            if "agentic_recovery_" + name in config
        })

    def timeout(self, stage):
        if stage not in STAGES:
            raise ValueError("Invalid original recovery stage")
        return {
            "original_observation": self.observation_timeout_seconds,
            "publication_reconciliation": self.publication_timeout_seconds,
            "original_reconciliation": self.reconciliation_timeout_seconds,
        }[stage]

    def delay(self, count):
        return min(self.backoff_max_seconds,
                   self.backoff_initial_seconds * (2 ** min(max(count - 1, 0), 12)))


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    binding: str
    identity: tuple
    stage: str
    started_at: int
    deadline_at: int
    attempt_count: int
    backoff_seconds: int

    @property
    def invocation_id(self):
        return self.identity[0]

    @property
    def project(self):
        return self.identity[1]


def _ledger():
    # No application/env import is needed by the read-only diagnostic helper.
    from open_webui.utils import agentic_governance
    return agentic_governance


def _row(db, invocation):
    value = db.execute(
        "SELECT " + ",".join(IDENTITY_KEYS) + ",state,submitted_at "
        "FROM governance_invocation WHERE invocation_id=?", (invocation,),
    ).fetchone()
    return dict(value) if value else None


def _binding(row):
    identity = tuple(row[key] for key in IDENTITY_KEYS)
    if not identity[5] or not identity[6] or type(identity[7]) is not int:
        raise RecoveryIdentityChanged("Original run identity is unavailable")
    encoded = json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()
    return identity, hashlib.sha256(encoded).hexdigest()


def _exists(db, table):
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone())


def _last(db, invocation, kind, binding, attempt=None):
    query = ("SELECT payload,created_at FROM governance_event "
             "WHERE invocation_id=? AND kind=? AND json_extract(payload,'$.binding')=?")
    args = [invocation, kind, binding]
    if attempt:
        query += " AND json_extract(payload,'$.attempt_id')=?"
        args.append(attempt)
    value = db.execute(query + " ORDER BY seq DESC LIMIT 1", args).fetchone()
    return json.loads(value["payload"]) if value else None


def _append(db, row, kind, payload):
    _ledger()._event(db, row["erp_project"], row["invocation_id"], kind, ACTOR, payload)


def _attempt(payload):
    return Attempt(
        payload["attempt_id"], payload["binding"], tuple(payload["identity"]),
        payload["stage"], payload["started_at"], payload["deadline_at"],
        payload["attempt_count"], payload["backoff_seconds"],
    )


def _assert_identity(db, attempt):
    row = _row(db, attempt.invocation_id)
    if not row or _binding(row) != (attempt.identity, attempt.binding):
        raise RecoveryIdentityChanged("Original recovery identity changed")
    started = _last(db, attempt.invocation_id, START, attempt.binding, attempt.attempt_id)
    if not started or _attempt(started) != attempt:
        raise RecoveryIdentityChanged("Original recovery attempt does not match its audit")
    return row


def _held(db, row):
    control = db.execute(
        "SELECT state FROM governance_project_control WHERE erp_project=?",
        (row["erp_project"],),
    ).fetchone()
    if not control or control[0] != "enabled" or row["state"] in {
        "cancel_requested", "cancelled", "awaiting_approval",
    }:
        return True
    if _exists(db, "governance_task_control"):
        control = db.execute(
            "SELECT state FROM governance_task_control WHERE project=? AND task=?",
            (row["erp_project"], row["erp_task"]),
        ).fetchone()
        if control and control[0] != "enabled":
            return True
    return False


def _saved(db, row):
    if not _exists(db, "governance_result"):
        return None
    # Select only exact receipt identity fields, never the event's result body.
    value = db.execute(
        "SELECT phase,created_at,updated_at, "
        "CASE WHEN json_valid(event_json) THEN json_extract(event_json,'$.event_id') END AS event_id, "
        "CASE WHEN json_valid(event_json) THEN json_extract(event_json,'$.invocation_id') END AS event_invocation, "
        "CASE WHEN json_valid(event_json) THEN json_extract(event_json,'$.project_id') END AS event_project, "
        "CASE WHEN json_valid(event_json) THEN json_extract(event_json,'$.task_id') END AS event_task, "
        "CASE WHEN json_valid(event_json) THEN json_extract(event_json,'$.run_id') END AS event_run, "
        "CASE WHEN json_valid(receipt_json) THEN json_extract(receipt_json,'$.event_id') END AS receipt_event, "
        "CASE WHEN json_valid(receipt_json) THEN json_extract(receipt_json,'$.task_id') END AS receipt_task, "
        "CASE WHEN json_valid(receipt_json) THEN json_extract(receipt_json,'$.run_id') END AS receipt_run, "
        "CASE WHEN json_valid(receipt_json) THEN json_extract(receipt_json,'$.durable') END AS durable, "
        "CASE WHEN json_valid(receipt_json) THEN json_extract(receipt_json,'$.modified') END AS modified, "
        "CASE WHEN json_valid(receipt_json) THEN json_extract(receipt_json,'$.status') END AS status "
        "FROM governance_result WHERE invocation_id=? AND run_id=?",
        (row["invocation_id"], row["automation_run_id"]),
    ).fetchone()
    if not value:
        return None
    result = dict(value)
    result["verified_publication"] = bool(
        result["phase"] == "published" and result["durable"] == 1
        and result["modified"] and result["status"] and result["event_id"]
        and result["event_invocation"] == row["invocation_id"]
        and result["event_project"] == row["erp_project"]
        and result["event_task"] == row["erp_task"]
        and result["event_run"] == row["automation_run_id"]
        and result["receipt_event"] == result["event_id"]
        and result["receipt_task"] == row["erp_task"]
        and result["receipt_run"] == row["automation_run_id"]
    )
    return result


def _evidence(db, row, now):
    """Read timestamps from immutable result identity; never use invocation updated_at."""
    values = []
    run = row["automation_run_id"]
    if _exists(db, "governance_local_result"):
        local = db.execute(
            "SELECT created_at FROM governance_local_result WHERE invocation_id=? AND run_id=?",
            (row["invocation_id"], run),
        ).fetchone()
        if local:
            values.append(("local_result_saved", local[0]))
    result = _saved(db, row)
    if result:
        values.append(("result_ready", result["created_at"]))
        if result["verified_publication"]:
            values.append(("publication_confirmed", result["updated_at"]))
    return [(kind, at) for kind, at in values if type(at) is int and 0 < at <= now]


def _capture_progress(db, row, binding, now):
    for kind, at in _evidence(db, row, now):
        present = db.execute(
            "SELECT 1 FROM governance_event WHERE invocation_id=? AND kind=? "
            "AND json_extract(payload,'$.binding')=? "
            "AND json_extract(payload,'$.evidence_kind')=? LIMIT 1",
            (row["invocation_id"], PROGRESS, binding, kind),
        ).fetchone()
        if not present:
            _append(db, row, PROGRESS, {
                "schema_version": 1, "binding": binding,
                "evidence_kind": kind, "evidence_at": at,
            })


def _finish(db, row, attempt, outcome, now):
    if outcome not in OUTCOMES:
        raise ValueError("Invalid original recovery outcome")
    previous = _last(db, attempt.invocation_id, END, attempt.binding, attempt.attempt_id)
    if previous:
        if previous["outcome"] != outcome:
            raise ValueError("Original recovery completion is immutable")
        return previous
    if outcome == "published" and _success_outcome(db, row, attempt.stage) != "published":
        raise ValueError("Publication completion requires the original saved receipt")
    _capture_progress(db, row, attempt.binding, now)
    published = outcome == "published"
    stage = ("held" if _held(db, row) else
             "awaiting_reconciliation" if published and row["state"] in {"running", "uncertain"} else
             "ready_for_handoff" if published else
             "awaiting_publication" if outcome.startswith("publication") else
             "held" if outcome in {"context_changed", "identity_changed", "validation_failed",
                                  "authorization_held"} else
             "awaiting_original")
    payload = {
        "schema_version": 1, "attempt_id": attempt.attempt_id,
        "binding": attempt.binding, "outcome": outcome,
        "finished_at": now, "stage": stage,
        "error_code": outcome if outcome in ERROR_OUTCOMES else None,
        # This is eligibility for another guarded original-run observation,
        # never permission to publish, dispatch, or replay development.
        "next_eligible_recovery_at": None if published else now + attempt.backoff_seconds,
    }
    _append(db, row, END, payload)
    return payload


def begin_attempt(invocation, *, stage, config=None, db_path=None):
    policy = RecoveryPolicy.from_config(config)
    timeout = policy.timeout(stage)
    now = int(time.time())
    with _ledger()._transaction(db_path) as db:
        row = _row(db, invocation)
        if not row or row["submitted_at"] is None or not row["automation_run_id"]:
            # Never turn the legacy unsubmitted path into a safe retry claim.
            return None
        identity, binding = _binding(row)
        prior = _last(db, invocation, START, binding)
        if prior:
            completed = _last(db, invocation, END, binding, prior["attempt_id"])
            if not completed:
                active = _last(db, invocation, STAGE, binding, prior["attempt_id"])
                deadline = active["deadline_at"] if active else prior["deadline_at"]
                if now <= deadline:
                    return None  # Repeated events share one in-flight attempt.
                # A crash cannot masquerade as a live worker indefinitely. This
                # closes diagnostics only, never any invocation or task lease.
                _finish(db, row, _attempt(prior), "interrupted", now)
                return None  # Always back off before a subsequent safe read.
            eligible = completed["next_eligible_recovery_at"]
            if eligible is not None and now < eligible:
                return None
            if completed["outcome"] == "published":
                saved = _saved(db, row)
                if (stage != "original_reconciliation"
                        or row["state"] not in {"running", "uncertain"}
                        or not saved or not saved["verified_publication"]):
                    return None
                # Publication can precede a crash before fenced execution finish.
                # Only the existing guarded original-settlement path may reenter;
                # this never authorizes publishing again or submitting a model.
        count = db.execute(
            "SELECT count(*) FROM governance_event WHERE invocation_id=? AND kind=? "
            "AND json_extract(payload,'$.binding')=?",
            (invocation, START, binding),
        ).fetchone()[0] + 1
        payload = {
            "schema_version": 1, "attempt_id": uuid4().hex,
            "binding": binding, "identity": identity, "stage": stage,
            "started_at": now, "deadline_at": now + max(1, math.ceil(timeout)),
            "attempt_count": count, "backoff_seconds": policy.delay(count),
        }
        _capture_progress(db, row, binding, now)
        _append(db, row, START, payload)
        return _attempt(payload)


def finish_attempt(attempt, outcome, *, db_path=None):
    with _ledger()._transaction(db_path) as db:
        row = _assert_identity(db, attempt)
        return _finish(db, row, attempt, outcome, int(time.time()))


def _record_stage(attempt, stage, timeout, db_path):
    with _ledger()._transaction(db_path) as db:
        row = _assert_identity(db, attempt)
        if _last(db, attempt.invocation_id, END, attempt.binding, attempt.attempt_id):
            raise RecoveryIdentityChanged("Original recovery attempt is already closed")
        now = int(time.time())
        deadline = min(attempt.deadline_at, now + max(1, math.ceil(timeout)))
        _append(db, row, STAGE, {
            "schema_version": 1, "attempt_id": attempt.attempt_id, "binding": attempt.binding,
            "stage": stage, "started_at": now, "deadline_at": deadline,
        })


async def bounded_stage(stage, operation, *, config=None):
    """Bound one existing operation; the callable is never invoked on a skipped retry."""
    policy = RecoveryPolicy.from_config(config)
    timeout = policy.timeout(stage)
    context = _ACTIVE.get()
    previous = context.get("stage") if context else None
    if context:
        _record_stage(context["attempt"], stage, timeout, context["db_path"])
        context["stage"] = stage
    succeeded = False
    try:
        value = await asyncio.wait_for(operation(), timeout=timeout)
        succeeded = True
        return value
    except asyncio.TimeoutError as exc:
        if isinstance(exc, RecoveryStageTimeout):
            raise
        raise RecoveryStageTimeout(stage) from exc
    except Exception as exc:
        # Retain the caller's exception type and message; audit only the fixed
        # stage code. The first (innermost) stage is the factual failure source.
        if not hasattr(exc, "_original_recovery_stage"):
            exc._original_recovery_stage = stage
        raise
    finally:
        if context:
            context["stage"] = previous
            if succeeded and previous:
                _record_stage(context["attempt"], previous, policy.timeout(previous), context["db_path"])


def _success_outcome(db, row, stage):
    saved = _saved(db, row)
    if saved:
        return "published" if saved["verified_publication"] else "publication_pending"
    if _held(db, row):
        return "authorization_held"
    if _exists(db, "governance_local_result") and db.execute(
        "SELECT 1 FROM governance_local_result WHERE invocation_id=? AND run_id=?",
        (row["invocation_id"], row["automation_run_id"]),
    ).fetchone():
        return "original_recovered"
    return "publication_pending" if stage == "publication_reconciliation" else "original_pending"


def _error_outcome(exc, stage):
    observed_stage = getattr(exc, "_original_recovery_stage", stage)
    stage = observed_stage if observed_stage in STAGES else stage
    if isinstance(exc, RecoveryContextChanged):
        return "context_changed"
    if isinstance(exc, RecoveryIdentityChanged):
        return "identity_changed"
    if isinstance(exc, RecoveryStageTimeout):
        return {
            "original_observation": "observation_timeout",
            "publication_reconciliation": "publication_timeout",
            "original_reconciliation": "reconciliation_timeout",
        }[exc.stage]
    if isinstance(exc, asyncio.CancelledError):
        return "interrupted"
    if isinstance(exc, ValueError):
        return "validation_failed"
    return {
        "original_observation": "observation_failed",
        "publication_reconciliation": "publication_failed",
        "original_reconciliation": "reconciliation_failed",
    }[stage]


async def run_recovery(invocation, operation, *, stage, config=None, db_path=None, notify=None):
    """Serialize/bound a guarded original recovery; never accept a replacement job."""
    attempt = begin_attempt(invocation, stage=stage, config=config, db_path=db_path)
    if attempt is None:
        return None
    token = _ACTIVE.set({"attempt": attempt, "db_path": db_path})
    completion = None
    try:
        result = await bounded_stage(stage, operation, config=config)
    except BaseException as exc:
        # Do not hide cancellation or an authorization/context failure from the
        # original caller. The durable audit contains a closed code, not its text.
        completion = finish_attempt(attempt, _error_outcome(exc, stage), db_path=db_path)
        raise
    else:
        with _ledger()._transaction(db_path) as db:
            row = _assert_identity(db, attempt)
            completion = _finish(db, row, attempt, _success_outcome(db, row, stage), int(time.time()))
        return result
    finally:
        _ACTIVE.reset(token)
        # A published receipt can precede a failed original-fence settlement.
        # That existing reentry path has no due time and must not self-wake.
        due = completion.get("next_eligible_recovery_at") if completion else None
        wake = bool(completion and completion["stage"] != "held" and (
            (completion["outcome"] in _DUE_OUTCOMES and type(due) is int
             and due > completion["finished_at"])
            or (completion["outcome"] == "published" and completion["stage"] == "ready_for_handoff")
        ))
        if wake and notify is not None:
            try:
                notify()  # Wake the existing loop; never create another timer.
            except Exception:
                log.exception("Original recovery wake signal unavailable; retained durable fallback")


def _snapshot(db, invocation, *, now=None):
    """Return only allowlisted metadata on the caller's existing read-only snapshot."""
    now = int(time.time()) if now is None else now
    result = {
        "last_recovery_attempt_at": None, "last_recovery_outcome": None,
        "last_progress_at": None, "next_eligible_recovery_at": None,
        "stage": None, "attempt_count": None, "error_code": None,
        "responsible_agent_id": None,
    }
    row = _row(db, invocation)
    if not row:
        return result
    if not row["automation_run_id"]:
        from open_webui.utils.agentic_preclaim_recovery import snapshot as preclaim_snapshot
        full = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation,)).fetchone()
        return preclaim_snapshot(db, full, now=now)
    try:
        _, binding = _binding(row)
    except RecoveryIdentityChanged:
        return result
    result["responsible_agent_id"] = row["agent_id"]
    if not _exists(db, "governance_event"):
        return result
    evidence = db.execute(
        "SELECT json_extract(payload,'$.evidence_kind') AS kind, "
        "json_extract(payload,'$.evidence_at') AS at FROM governance_event "
        "WHERE invocation_id=? AND kind=? AND json_extract(payload,'$.binding')=?",
        (invocation, PROGRESS, binding),
    ).fetchall()
    # A captured timestamp wins over a subsequently repeated publication receipt.
    by_kind = {value["kind"]: value["at"] for value in evidence
               if type(value["at"]) is int and 0 < value["at"] <= now}
    for kind, at in _evidence(db, row, now):
        by_kind.setdefault(kind, at)
    result["last_progress_at"] = max(by_kind.values(), default=None)
    started = _last(db, invocation, START, binding)
    if not started:
        return result
    result.update(last_recovery_attempt_at=started["started_at"],
                  attempt_count=started["attempt_count"])
    completed = _last(db, invocation, END, binding, started["attempt_id"])
    if completed:
        result.update(last_recovery_outcome=completed["outcome"], stage=completed["stage"],
                      error_code=completed["error_code"],
                      next_eligible_recovery_at=completed["next_eligible_recovery_at"])
    else:
        active = _last(db, invocation, STAGE, binding, started["attempt_id"])
        deadline = active["deadline_at"] if active else started["deadline_at"]
        if now > deadline:
            # Reading status never writes an inferred completion event.
            result.update(last_recovery_outcome="interrupted", stage="interrupted",
                          error_code="interrupted")
        else:
            result["stage"] = active["stage"] if active else started["stage"]
    if (result["last_recovery_outcome"] == "published"
            and row["state"] in {"running", "uncertain"}):
        result["stage"] = "awaiting_reconciliation"
        result["next_eligible_recovery_at"] = None
    if _held(db, row):
        result["stage"] = "held"
    return result


def snapshot(db, invocation, *, now=None):
    """A missing/old audit journal does not erase otherwise valid delivery evidence."""
    try:
        return _snapshot(db, invocation, now=now)
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return {
            "last_recovery_attempt_at": None, "last_recovery_outcome": None,
            "last_progress_at": None, "next_eligible_recovery_at": None,
            "stage": None, "attempt_count": None,
            "error_code": "recovery_journal_unavailable", "responsible_agent_id": None,
        }


def in_recovery():
    """Allow an existing completion path to add stage bounds only during recovery."""
    return _ACTIVE.get() is not None


# Only fixed observation/publication conditions contribute a shorter wait.
# Unclassified failures, context/identity rejection and authorization holds
# still require their existing authorized event or ordinary fallback handling.
_DUE_OUTCOMES = frozenset({
    "original_pending", "original_recovered", "publication_pending",
    "observation_timeout", "publication_timeout", "reconciliation_timeout", "interrupted",
})


def _wakeup_candidates(db):
    """Read the existing bounded recovery window; never claim or mutate it."""
    projects = [value[0] for value in db.execute(
        "SELECT DISTINCT erp_project FROM governance_agent ORDER BY erp_project")]
    for project in projects:
        # Process already-received ERP changes before prioritizing this project.
        # A pending Pause/Stop/context event must not be overtaken by recovery.
        if _exists(db, "governance_inbox") and db.execute(
                "SELECT 1 FROM governance_inbox WHERE erp_project=? AND state='pending' LIMIT 1",
                (project,)).fetchone():
            continue
        # Keep the history limit inside the subquery. Filtering before LIMIT
        # would incorrectly admit older originals outside the established window.
        # Never materialize prompts/work packets just to choose scheduling order.
        has_results = _exists(db, "governance_result")
        result_filter = (
            " AND (recent.state!='running' OR EXISTS(SELECT 1 FROM governance_result r "
            "WHERE r.invocation_id=recent.invocation_id AND r.run_id=recent.automation_run_id))"
            " AND (recent.state!='execution_finished' OR EXISTS(SELECT 1 FROM governance_result r "
            "WHERE r.invocation_id=recent.invocation_id AND r.run_id=recent.automation_run_id "
            "AND r.phase!='published'))"
        ) if has_results else " AND recent.state='uncertain'"
        rows = db.execute(
            "SELECT recent.* FROM (SELECT invocation_id,erp_project,erp_task,agent_id,target_role,"
            "automation_run_id,packet_hash,lease_fence,state,submitted_at "
            "FROM governance_invocation WHERE erp_project=? "
            "ORDER BY created_at DESC,invocation_id LIMIT 100) recent "
            "WHERE recent.submitted_at IS NOT NULL AND recent.automation_run_id IS NOT NULL "
            "AND recent.target_role!='it_manager' "
            "AND recent.state IN ('running','uncertain','execution_finished')" + result_filter,
            (project,),
        ).fetchall()
        for row in rows:
            if (row["submitted_at"] is None or not row["automation_run_id"]
                    or row["target_role"] == "it_manager"
                    or row["state"] not in {"running", "uncertain", "execution_finished"}
                    or _held(db, row)):
                continue
            saved = _saved(db, row)
            if row["state"] == "running" and not saved:
                continue
            if row["state"] == "execution_finished" and (
                    not saved or saved["phase"] == "published"):
                continue
            try:
                _, binding = _binding(row)
            except RecoveryIdentityChanged:
                continue
            started = _last(db, row["invocation_id"], START, binding)
            if not started:
                continue
            completed = _last(db, row["invocation_id"], END, binding, started["attempt_id"])
            if completed:
                if completed["outcome"] not in _DUE_OUTCOMES:
                    continue
                due = completed["next_eligible_recovery_at"]
            else:
                active = _last(db, row["invocation_id"], STAGE, binding, started["attempt_id"])
                due = (active["deadline_at"] if active else started["deadline_at"]) + 1
            if type(due) is int:
                yield {"erp_project": project, "invocation_id": row["invocation_id"], "eligible_at": due}


def next_wakeup_delay(fallback_seconds, *, db_path=None, now=None):
    """Read the nearest eligible original-run clock for the same one loop."""
    now = int(time.time()) if now is None else now
    with _ledger().connect(db_path) as db:
        return min([fallback_seconds, *(
            max(1, row["eligible_at"] - now) for row in _wakeup_candidates(db)
        )])


def due_recoveries(*, limit=1, db_path=None, now=None):
    """Oldest due originals, within existing controls and history bounds.

    This list is scheduling advice only. The original run_recovery transaction
    must acquire the current attempt and revalidate its unchanged authority.
    """
    if type(limit) is not int or not 1 <= limit <= 4:
        raise ValueError("Recovery priority batch must be between one and four")
    now = int(time.time()) if now is None else now
    with _ledger().connect(db_path) as db:
        rows = [row for row in _wakeup_candidates(db) if row["eligible_at"] <= now]
    return sorted(rows, key=lambda row: (
        row["eligible_at"], row["erp_project"], row["invocation_id"]
    ))[:limit]

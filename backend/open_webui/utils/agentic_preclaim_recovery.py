"""Audited read-only preflight retries before an original execution is claimed.

Only a typed native-channel read outage can defer queued work. This journal is
not an execution lease. Existing claim, ownership, context and control gates
remain authoritative; no blocked execution is rearmed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from uuid import uuid4

from fastapi import HTTPException

START = "preclaim_recovery_started"
END = "preclaim_recovery_finished"
ACTOR = "server-preclaim-recovery"
IDENTITY = ("invocation_id", "erp_project", "erp_task", "agent_id", "target_role",
            "task_revision", "packet_hash", "lease_fence")
READ_STAGES = frozenset({"project_gate", "work_packet", "claim_project_gate", "claim_work_packet"})
OUTCOMES = frozenset({"deferred", "interrupted", "claimed", "waiting_project", "blocked", "held"})
ERROR_CODES = frozenset({"native_channel_not_connected", "native_read_interrupted"})


class PreclaimERPUnavailable(HTTPException):
    """Preserve a typed GET transport failure through the ordinary HTTP boundary."""
    def __init__(self, error):
        from open_webui.utils.agentic_erp_channel import ERPReadUnavailable
        if not isinstance(error, ERPReadUnavailable) or error.code not in ERROR_CODES:
            raise ValueError("A typed native ERP read outage is required")
        super().__init__(503, "ERP Project read transport is temporarily unavailable")
        self.code = error.code


@dataclass(frozen=True)
class PreclaimPolicy:
    # Three ordinary ERP GET attempts take at most 27s. This envelope also
    # permits the existing bounded relay preflight and final fresh packet/gates.
    attempt_timeout_seconds: int = 300
    backoff_initial_seconds: int = 30
    backoff_max_seconds: int = 1800

    def __post_init__(self):
        if (type(self.attempt_timeout_seconds) is not int
                or not 30 <= self.attempt_timeout_seconds <= 600
                or type(self.backoff_initial_seconds) is not int
                or type(self.backoff_max_seconds) is not int
                or not 1 <= self.backoff_initial_seconds <= self.backoff_max_seconds <= 1800):
            raise ValueError("Invalid preclaim recovery policy")

    @classmethod
    def from_config(cls, config=None):
        config = config or {}
        return cls(**{k: config["agentic_preclaim_" + k]
                      for k in cls.__dataclass_fields__ if "agentic_preclaim_" + k in config})

    def delay(self, count):
        return min(self.backoff_max_seconds,
                   self.backoff_initial_seconds * 2 ** min(max(count - 1, 0), 12))


def _ledger():
    from open_webui.utils import agentic_governance
    return agentic_governance


def _identity(row):
    value = tuple(row[k] for k in IDENTITY)
    return value, _ledger()._digest(value)


def _last(db, invocation, kind, binding, attempt_id=None):
    sql = ("SELECT payload FROM governance_event WHERE invocation_id=? AND kind=? "
           "AND json_extract(payload,'$.binding')=?")
    values = [invocation, kind, binding]
    if attempt_id is not None:
        sql += " AND json_extract(payload,'$.attempt_id')=?"
        values.append(attempt_id)
    value = db.execute(sql + " ORDER BY seq DESC LIMIT 1", values).fetchone()
    return json.loads(value[0]) if value else None


def _held(db, row):
    control = db.execute("SELECT state FROM governance_project_control WHERE erp_project=?",
                         (row["erp_project"],)).fetchone()
    if not control or control[0] != "enabled":
        return True
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_task_control'").fetchone():
        task = db.execute("SELECT state FROM governance_task_control WHERE project=? AND task=?",
                          (row["erp_project"], row["erp_task"])).fetchone()
        if task and task[0] != "enabled":
            return True
    return False


def _never_claimed(db, row):
    return bool(row and row["state"] == "queued" and row["submitted_at"] is None
                and not row["automation_run_id"] and row["dispatch_attempts"] == 0
                and not db.execute(
                    "SELECT 1 FROM governance_event WHERE invocation_id=? AND kind='execution_claimed' LIMIT 1",
                    (row["invocation_id"],)).fetchone())


def _append(db, row, kind, payload):
    _ledger()._event(db, row["erp_project"], row["invocation_id"], kind, ACTOR, payload)


def _finish(db, row, attempt, outcome, *, error_code=None, now=None):
    if outcome not in OUTCOMES or (error_code is not None and error_code not in ERROR_CODES):
        raise ValueError("Invalid preclaim recovery outcome")
    if (outcome == "deferred") != (error_code is not None):
        raise ValueError("Only a typed read outage may defer an original preflight")
    now = int(time.time()) if now is None else now
    prior = _last(db, row["invocation_id"], END, attempt["binding"], attempt["attempt_id"])
    if prior:
        if prior["outcome"] != outcome or prior["error_code"] != error_code:
            raise ValueError("Original preclaim completion is immutable")
        return prior
    retryable = outcome in {"deferred", "interrupted"}
    if retryable and not _never_claimed(db, row):
        raise ValueError("A claimed execution cannot be deferred as preflight")
    payload = {
        "schema_version": 1, "attempt_id": attempt["attempt_id"], "binding": attempt["binding"],
        "outcome": outcome, "error_code": error_code, "finished_at": now,
        "next_eligible_recovery_at": now + attempt["backoff_seconds"] if retryable else None,
    }
    _append(db, row, END, payload)
    if outcome == "deferred":
        db.execute("UPDATE governance_invocation SET reason=? WHERE invocation_id=?",
                   ("Waiting for authenticated ERP read transport", row["invocation_id"]))
    return payload


def begin_attempt(invocation, *, config=None, db_path=None):
    policy = PreclaimPolicy.from_config(config)
    with _ledger()._transaction(db_path) as db:
        row = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation,)).fetchone()
        if not _never_claimed(db, row) or _held(db, row):
            return None
        identity, binding = _identity(row)
        prior = _last(db, invocation, START, binding)
        now = int(time.time())
        failures = 0
        if prior:
            end = _last(db, invocation, END, binding, prior["attempt_id"])
            if not end:
                if now <= prior["deadline_at"]:
                    return None
                _finish(db, row, prior, "interrupted", now=now)
                return None
            due = end["next_eligible_recovery_at"]
            if due is not None and now < due:
                return None
            failures = prior["failure_count"] if end["outcome"] in {"deferred", "interrupted"} else 0
        count = db.execute(
            "SELECT count(*) FROM governance_event WHERE invocation_id=? AND kind=? "
            "AND json_extract(payload,'$.binding')=?", (invocation, START, binding)).fetchone()[0] + 1
        attempt = {
            "schema_version": 1, "attempt_id": uuid4().hex, "binding": binding,
            "identity": list(identity), "started_at": now,
            "deadline_at": now + policy.attempt_timeout_seconds, "attempt_count": count,
            "failure_count": failures + 1, "backoff_seconds": policy.delay(failures + 1),
        }
        _append(db, row, START, attempt)
        return attempt


def assert_claim(db, row, attempt):
    """Called inside the existing atomic invocation claim, before any mutation."""
    identity, binding = _identity(row)
    if (not _never_claimed(db, row) or _held(db, row)
            or list(identity) != attempt.get("identity") or binding != attempt.get("binding")
            or _last(db, row["invocation_id"], START, binding) != attempt
            or _last(db, row["invocation_id"], END, binding, attempt["attempt_id"])
            or int(time.time()) > attempt["deadline_at"]):
        raise ValueError("Preclaim identity, controls or attempt changed")


def finish_attempt(invocation, attempt, outcome, *, error_code=None, db_path=None):
    with _ledger()._transaction(db_path) as db:
        row = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation,)).fetchone()
        if not row or _identity(row) != (tuple(attempt["identity"]), attempt["binding"]):
            raise ValueError("Preclaim identity changed")
        if _last(db, invocation, START, attempt["binding"], attempt["attempt_id"]) != attempt:
            raise ValueError("Preclaim audit identity changed")
        return _finish(db, row, attempt, outcome, error_code=error_code)


def retryable_code(error, stage):
    from open_webui.utils.agentic_erp_channel import ERPReadUnavailable
    if stage in READ_STAGES and isinstance(error, (ERPReadUnavailable, PreclaimERPUnavailable)):
        return error.code if error.code in ERROR_CODES else None
    return None


def snapshot(db, row, *, now=None):
    """Read-only metadata; a status check cannot start/finish a retry or progress."""
    result = {"last_recovery_attempt_at": None, "last_recovery_outcome": None,
              "last_progress_at": None, "next_eligible_recovery_at": None,
              "stage": None, "attempt_count": None, "error_code": None,
              "responsible_agent_id": row["agent_id"]}
    _, binding = _identity(row)
    start = _last(db, row["invocation_id"], START, binding)
    if not start:
        return result
    now = int(time.time()) if now is None else now
    result.update(last_recovery_attempt_at=start["started_at"], attempt_count=start["attempt_count"])
    end = _last(db, row["invocation_id"], END, binding, start["attempt_id"])
    if end:
        result.update(last_recovery_outcome=end["outcome"], error_code=end["error_code"],
                      next_eligible_recovery_at=end["next_eligible_recovery_at"],
                      stage="waiting_erp" if end["outcome"] == "deferred" else
                            "preclaim_interrupted" if end["outcome"] == "interrupted" else
                            "queued" if end["outcome"] == "waiting_project" else end["outcome"])
    else:
        result["stage"] = "preclaim_interrupted" if now > start["deadline_at"] else "preclaim_check"
    if _held(db, row):
        result["stage"] = "held"
    return result

def settle_exception(invocation, attempt, error, stage, *, db_path=None):
    """Fence late failures/interruptions against a newer attempt before any write."""
    import asyncio

    with _ledger()._transaction(db_path) as db:
        row = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation,)).fetchone()
        if (not row or _identity(row) != (tuple(attempt["identity"]), attempt["binding"])
                or _last(db, invocation, START, attempt["binding"]) != attempt):
            return None
        prior = _last(db, invocation, END, attempt["binding"], attempt["attempt_id"])
        if prior:
            return prior  # An old coroutine cannot revise a completed audit.
        if row["automation_run_id"] or row["submitted_at"] is not None or row["dispatch_attempts"] != 0:
            return None  # No post-claim outcome can acquire preclaim retry authority.
        if _held(db, row) or row["state"] in {"cancelled", "cancel_requested"}:
            return _finish(db, row, attempt, "held")
        if not _never_claimed(db, row):
            return None
        if isinstance(error, asyncio.CancelledError) or int(time.time()) > attempt["deadline_at"]:
            return _finish(db, row, attempt, "interrupted")
        code = retryable_code(error, stage)
        if code is not None:
            return _finish(db, row, attempt, "deferred", error_code=code)
        # Same original blocked transition, now atomic with its attempt fence.
        reason = str(error)[:500]
        db.execute("UPDATE governance_invocation SET state='blocked',reason=?,updated_at=? WHERE invocation_id=?",
                   (reason, int(time.time()), invocation))
        _ledger()._release(db, row)
        _ledger()._event(db, row["erp_project"], invocation, "unsubmitted_preflight_blocked",
                        "executor", {"reason": reason})
        return _finish(db, row, attempt, "blocked")


def next_wakeup_delay(fallback_seconds, *, db_path=None, now=None):
    """Nearest eligible preclaim audit for the existing single fallback loop.

    No timer/task is created here. Holds and historical blocked rows contribute
    no due time. A one-second floor lets a spawned delivery acquire its journal
    entry before a coalesced wake can cause another pass.
    """
    now = int(time.time()) if now is None else now
    delay = fallback_seconds
    with _ledger().connect(db_path) as db:
        rows = db.execute(
            "SELECT * FROM governance_invocation WHERE state='queued' AND submitted_at IS NULL "
            "AND automation_run_id IS NULL AND dispatch_attempts=0").fetchall()
        for row in rows:
            if _held(db, row):
                continue
            _, binding = _identity(row)
            started = _last(db, row["invocation_id"], START, binding)
            if not started:
                continue
            end = _last(db, row["invocation_id"], END, binding, started["attempt_id"])
            due = end["next_eligible_recovery_at"] if end else started["deadline_at"] + 1
            if due is not None:
                delay = min(delay, max(1, due - now))
    return delay

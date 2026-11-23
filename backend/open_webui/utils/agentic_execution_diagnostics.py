"""Read-only, bounded operator diagnostics; no recovery, dispatch or model calls.

This code runs only inside the authenticated governance service. It never grants
an operator access to database paths, prompts, replies, admission proofs or keys.
The two journal snapshots are observations, not a cross-journal completion proof.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from open_webui.utils.agentic_original_observation import latest_original_observation

PROJECT = re.compile(r"PROJ-[A-Za-z0-9-]{1,60}")
TASK = re.compile(r"TASK-[A-Za-z0-9-]{1,60}")
PROVIDER_STATES = frozenset({"prepared", "dispatching", "delegated", "unknown", "unavailable"})
PROVIDER_REASONS = frozenset({
    "unverified_dot_receipt", "verified_pre_submission_busy",
    "verified_pre_submission_rejection", "delivery_outcome_unknown",
    "unverified_governance_scope_receipt",
})
CALL_STATES = frozenset({"pending", "running", "completed", "uncertain", "failed", "rejected", "prepared"})
OPERATIONS = frozenset({
    "context.read", "artifact.read", "artifact.write", "pm_review.write",
    "attachment.read", "attachment.present", "repository.prepare", "repository.read",
    "repository.write", "repository.patch", "repository.mkdir", "repository.test",
    "repository.deliver", "repository.delivery_status", "repository.verify_delivery",
    "dependency.verify", "dependency.status",
})
PUBLICATION_PHASES = frozenset({"pending", "uncertain", "published"})
INVOCATION_STATES = frozenset({
    "queued", "running", "uncertain", "cancel_requested", "execution_finished",
    "awaiting_approval", "cancelled", "blocked", "failed", "verified", "handed_off",
})
ROLES = frozenset({"solution_architect", "project_manager", "developer", "qa", "operations", "it_manager"})


@contextmanager
def _readonly(path):
    target = Path(path)
    # mode=ro must fail if the journal is missing: never initialize or migrate it.
    db = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def _exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _enum(value, allowed):
    return value if value in allowed else "other"


def _hash(value):
    if value is None:
        return None
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return hashlib.sha256(raw).hexdigest() if len(raw) <= 400000 else None



def _packet_context(raw, stored_hash, project, task, role, agent):
    """Return only verified hash metadata, never stored packet content."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 200000:
        return {"lookup": "unavailable"}
    try:
        packet = json.loads(raw)
        if not isinstance(packet, dict) or type(packet.get("version", packet.get("schema_version"))) is not int:
            return {"lookup": "invalid"}
        if packet.get("version", packet.get("schema_version")) != 1:
            return {"lookup": "invalid"}
        def digest(value):
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > 200000:
                raise ValueError("Packet exceeds the native evidence limit")
            return hashlib.sha256(encoded).hexdigest()
        full_hash = digest(packet)
        content_hash = packet.get("content_sha256")
        if (not isinstance(stored_hash, str) or full_hash != stored_hash
                or not isinstance(content_hash, str)
                or digest({k: v for k, v in packet.items() if k != "content_sha256"}) != content_hash):
            return {"lookup": "integrity_mismatch"}
        binding = packet.get("binding")
        if (packet.get("project_id") != project or packet.get("task_id") != task
                or not isinstance(binding, dict) or binding.get("project_id") != project
                or binding.get("role") != role or binding.get("agent_id") != agent):
            return {"lookup": "binding_mismatch"}
        def exact_hash(value):
            return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None
        backlog = packet.get("project_backlog")
        backlog = backlog if isinstance(backlog, dict) else {}
        srs = packet.get("srs")
        srs = srs if isinstance(srs, dict) else {}
        return {
            "lookup": "verified",
            "packet_sha256": full_hash,
            "content_sha256": content_hash,
            "task_revision_sha256": exact_hash(packet.get("task_revision")),
            "srs_sha256": exact_hash(srs.get("sha256")),
            "activity_sha256": digest(packet.get("activity", [])),
            "backlog_context_sha256": exact_hash(backlog.get("context_sha256")),
            "backlog_snapshot_sha256": exact_hash(backlog.get("snapshot_sha256")),
        }
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return {"lookup": "invalid"}


def _provider(db, row):
    if not row["automation_run_id"] or row["submitted_at"] is None:
        return {"lookup": "not_submitted"}
    if not row["owner_id"]:
        return {"lookup": "binding_unavailable"}
    if db is None or not _exists(db, "dot_primary_runs"):
        return {"lookup": "journal_unavailable"}
    values = db.execute(
        "SELECT status, reason, length(CAST(response_text AS BLOB)) AS response_bytes, "
        "CASE WHEN length(CAST(response_text AS BLOB))<=200000 THEN diagnostic_sha256(response_text) END AS response_sha256, "
        "CASE WHEN receipt IS NOT NULL AND length(receipt)>0 THEN 1 ELSE 0 END AS receipt_present, "
        "CASE WHEN scope_receipt_json IS NOT NULL AND length(scope_receipt_json)>0 THEN 1 ELSE 0 END AS scope_receipt_present, "
        "created_at,updated_at FROM dot_primary_runs "
        "WHERE owner=? AND automation_id=? AND original_run_id=? LIMIT 2",
        (row["owner_id"], row["agent_id"], row["automation_run_id"]),
    ).fetchall()
    if len(values) != 1:
        return {"lookup": "not_found" if not values else "ambiguous"}
    value = dict(values[0])
    return {
        "lookup": "found", "status": _enum(value["status"], PROVIDER_STATES),
        "reason_code": None if value["reason"] is None else _enum(value["reason"], PROVIDER_REASONS),
        "response_present": bool(value["response_bytes"]),
        "response_sha256": value["response_sha256"],
        "receipt_present": bool(value["receipt_present"]),
        "scope_receipt_present": bool(value["scope_receipt_present"]),
        "created_at": value["created_at"], "updated_at": value["updated_at"],
    }


def _calls(db, invocation):
    if not (_exists(db, "governance_gateway_call") and _exists(db, "governance_gateway_session")):
        return {"available": False, "groups": []}
    operations, states = sorted(OPERATIONS), sorted(CALL_STATES)
    op_marks, state_marks = ",".join("?" for _ in operations), ",".join("?" for _ in states)
    # Aggregate only allowlisted names; arbitrary tool input and output is never selected.
    sql = (
        f"SELECT CASE WHEN c.operation IN ({op_marks}) THEN c.operation ELSE 'other' END AS operation, "
        f"CASE WHEN c.state IN ({state_marks}) THEN c.state ELSE 'other' END AS state, count(*) AS count "
        "FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id "
        "WHERE s.invocation_id=? GROUP BY 1,2 ORDER BY 1,2"
    )
    return {"available": True, "groups": [dict(v) for v in db.execute(sql, (*operations, *states, invocation))]}


def _publication(db, invocation):
    result = {"local_result_present": False, "publication_present": False, "phase": None}
    if _exists(db, "governance_local_result"):
        result["local_result_present"] = bool(db.execute(
            "SELECT 1 FROM governance_local_result WHERE invocation_id=? LIMIT 1", (invocation,)
        ).fetchone())
    if _exists(db, "governance_result"):
        value = db.execute("SELECT phase FROM governance_result WHERE invocation_id=?", (invocation,)).fetchone()
        if value:
            result.update(publication_present=True, phase=_enum(value["phase"], PUBLICATION_PHASES))
    return result


def read_project_diagnostics(project, task="", after=0, limit=100, *, governance_path=None, provider_path=None):
    if not isinstance(project, str) or not PROJECT.fullmatch(project):
        raise ValueError("Invalid project identity")
    if not isinstance(task, str) or (task and not TASK.fullmatch(task)):
        raise ValueError("Invalid task identity")
    if type(after) is not int or not 0 <= after <= 9223372036854775807 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Invalid diagnostic page bounds")
    if governance_path is None or provider_path is None:
        from open_webui.env import DATA_DIR
        from open_webui.utils import agentic_governance
        governance_path = agentic_governance.path() if governance_path is None else governance_path
        provider_path = Path(DATA_DIR) / "abitdot-primary.sqlite" if provider_path is None else provider_path
    result = {
        "project": project, "task": task, "read_only": True, "runtime_available": False,
        "audit_after": after, "audit_limit": limit, "audit_next": after,
        "audit": [], "invocations": [], "invocation_limit": 100, "invocations_truncated": False,
        "semantics": "Journal observations only; no verified completion, recovery, cancellation or new execution.",
    }
    try:
        with _readonly(governance_path) as db:
            rows = db.execute(
                "SELECT i.invocation_id,i.erp_project,i.erp_task,i.target_role,i.agent_id,i.automation_run_id,"
                "i.state,i.dispatch_attempts,i.submitted_at,i.updated_at,a.owner_id,i.packet_hash, "
                "CASE WHEN length(CAST(i.work_packet AS BLOB))<=200000 THEN i.work_packet END AS diagnostic_work_packet "
                "FROM governance_invocation i LEFT JOIN governance_agent a "
                "ON a.agent_id=i.agent_id AND a.erp_project=i.erp_project AND a.role=i.target_role "
                "WHERE i.erp_project=? AND (?='' OR i.erp_task=?) "
                "ORDER BY i.created_at DESC,i.invocation_id LIMIT 101",
                (project, task, task),
            ).fetchall()
            truncated = len(rows) > 100
            rows = rows[:100]
            audit = [dict(v) for v in db.execute(
                "SELECT seq,invocation_id,kind,created_at,event_hash FROM governance_event "
                "WHERE erp_project=? AND seq>? AND (?='' OR invocation_id IN "
                "(SELECT invocation_id FROM governance_invocation WHERE erp_project=? AND erp_task=?)) "
                "ORDER BY seq LIMIT ?", (project, after, task, project, task, limit),
            )]
            provider = None
            try:
                with _readonly(provider_path) as provider:
                    provider.create_function("diagnostic_sha256", 1, _hash, deterministic=True)
                    diagnostics = _invocations(db, provider, rows)
            except (OSError, sqlite3.Error):
                diagnostics = _invocations(db, None, rows)
            result.update(runtime_available=True, invocations=diagnostics, audit=audit, invocations_truncated=truncated,
                          audit_next=audit[-1]["seq"] if audit else after)
    except (OSError, sqlite3.Error):
        result["error_code"] = "DIAGNOSTIC_JOURNAL_UNAVAILABLE"
    return result


def _invocations(db, provider, rows):
    from open_webui.utils.agentic_recovery_telemetry import snapshot as recovery_snapshot
    items = []
    for row in rows:
        item = {key: row[key] for key in (
            "invocation_id", "erp_task", "automation_run_id", "dispatch_attempts", "submitted_at", "updated_at"
        )}
        item.update(
            role=_enum(row["target_role"], ROLES), state=_enum(row["state"], INVOCATION_STATES),
            provider_original=_provider(provider, row), gateway_calls=_calls(db, row["invocation_id"]),
            result_publication=_publication(db, row["invocation_id"]),
            original_observation=latest_original_observation(db, row),
            recovery=recovery_snapshot(db, row["invocation_id"]),
            packet_context=_packet_context(row["diagnostic_work_packet"], row["packet_hash"],
                                           row["erp_project"], row["erp_task"], row["target_role"], row["agent_id"]),
        )
        items.append(item)
    return items


STATUS_DIAGNOSTIC_SEMANTICS = (
    "Original-run journal observations only; not a completion, recovery or retry authorization."
)


def _status_unavailable(code):
    return {
        "schema_version": 1,
        "read_only": True,
        "available": False,
        "error_code": code,
        "semantics": STATUS_DIAGNOSTIC_SEMANTICS,
    }


def enrich_status_invocations(project, invocations, *, governance_path=None, provider_path=None):
    """Add bounded observations to the exact public rows already selected by status.

    Existing fields, ordering and state meanings remain unchanged. The authenticated
    caller chooses the Project, never a journal path or provider identity. A changed
    row between the two read snapshots produces unavailable metadata, not a join
    across different original runs. No missing row is added to the public list.
    """
    if not isinstance(invocations, list) or len(invocations) > 100:
        raise ValueError("Invalid existing status row bounds")
    if any(not isinstance(row, dict) or "delivery_diagnostics" in row for row in invocations):
        raise ValueError("Invalid existing status row shape")
    if not invocations:
        return []
    result = read_project_diagnostics(
        project, governance_path=governance_path, provider_path=provider_path
    )
    by_id = {row["invocation_id"]: row for row in result["invocations"]}
    enriched = []
    for original in invocations:
        row = by_id.get(original.get("invocation_id"))
        if not result["runtime_available"]:
            detail = _status_unavailable("DIAGNOSTIC_JOURNAL_UNAVAILABLE")
        elif row is None:
            detail = _status_unavailable("DIAGNOSTIC_NOT_IN_SNAPSHOT")
        elif any(original.get(key) != row.get(key) for key in (
            "invocation_id", "erp_task", "automation_run_id", "state",
            "dispatch_attempts", "submitted_at", "updated_at"
        )) or original.get("target_role") != row.get("role"):
            detail = _status_unavailable("DIAGNOSTIC_SNAPSHOT_CHANGED")
        elif (row["packet_context"].get("lookup") == "verified"
              and original.get("packet_hash") != row["packet_context"].get("packet_sha256")):
            detail = _status_unavailable("DIAGNOSTIC_SNAPSHOT_CHANGED")
        else:
            detail = {
                "schema_version": 1,
                "read_only": True,
                "available": True,
                "semantics": STATUS_DIAGNOSTIC_SEMANTICS,
                "provider_original": row["provider_original"],
                "gateway_calls": row["gateway_calls"],
                "result_publication": row["result_publication"],
                "original_observation": row["original_observation"],
                "packet_context": row["packet_context"],
                "recovery": row["recovery"],
            }
        enriched.append({**original, "delivery_diagnostics": detail})
    return enriched

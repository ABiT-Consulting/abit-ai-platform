"""Read-only automation admission projection; never opens, settles or rearms work.

The service's existing maintenance module remains the admission authority.
This helper is intentionally lazy-loaded so source trees without that installed
module report unavailable instead of silently treating missing guards as ready.
Authorization belongs to the caller's existing owner/access checks.
"""
from __future__ import annotations

from contextlib import closing
import importlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, TypedDict


class MaintenanceDenied(RuntimeError):
    """Fallback exception when the installed maintenance module is unavailable."""


class AdmissionStatus(TypedDict):
    state: str
    code: str
    reason: str
    gate_state: str | None
    gate_epoch: int | None
    operator_owner: str | None
    operator_action: str | None
    operator_command_id: str | None
    lease_id: str | None
    lease_state: str | None
    original_run_id: str | None
    original_chat_url: str | None
    next_action: str
    evidence: str
    denial_observed: bool


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_CHAT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _./:@+()=-]{0,159}\Z")
_UNAVAILABLE = "Maintenance admission could not be verified. Original work remains protected."
_VERIFY = "Obtain the current service-owned maintenance state and original receipt before retrying."
_MAX_BLOCKED = 10000


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _label(value: Any) -> str | None:
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else None


def _module(maintenance=None):
    if maintenance is not None:
        return maintenance
    try:
        return importlib.import_module("open_webui.utils.agentic_maintenance")
    except (ImportError, ModuleNotFoundError):
        raise MaintenanceDenied(_UNAVAILABLE) from None


def _exception(module):
    candidate = getattr(module, "MaintenanceDenied", None)
    return candidate if isinstance(candidate, type) and issubclass(candidate, Exception) else MaintenanceDenied


def _path(module, db_path):
    path = Path(db_path if db_path is not None else module.path())
    if not path.is_absolute():
        raise _exception(module)(_UNAVAILABLE)
    return path


def _base() -> AdmissionStatus:
    return {
        "state": "blocked", "code": "admission_unavailable", "reason": _UNAVAILABLE,
        "gate_state": None, "gate_epoch": None, "operator_owner": None,
        "operator_action": None, "operator_command_id": None,
        "lease_id": None, "lease_state": None, "original_run_id": None,
        "original_chat_url": None, "next_action": _VERIFY,
        "evidence": "No verified original run binding is available.",
        "denial_observed": False,
    }


def _json_object(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
        return None

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _immutable_audit(db):
    """Recognize the existing unconditional append-only guards, not their names alone."""
    rows = db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='audit' AND name IN ('audit_no_update','audit_no_delete')"
    ).fetchall()
    if len(rows) != 2:
        return False
    for row in rows:
        operation = "update" if row["name"] == "audit_no_update" else "delete"
        sql = re.sub(r"\s+", " ", row["sql"] or "").strip().rstrip(";").lower()
        expected = (
            "create trigger " + row["name"] + " before " + operation + " on audit "
            "begin select raise(abort,'maintenance audit is immutable'); end"
        )
        if sql != expected:
            return False
    return True


def _original_binding(db, database, lease, automation_id):
    unavailable = (None, None, "The original lease has no verified immutable run binding.")
    if not _immutable_audit(db):
        return unavailable
    rows = db.execute(
        "SELECT payload,created_ns FROM audit WHERE kind='admitted' AND "
        "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.lease') END=? "
        "ORDER BY seq LIMIT 2", (lease["id"],),
    ).fetchall()
    if len(rows) != 1:
        return unavailable
    payload = _json_object(rows[0]["payload"])
    if not payload or any(
        payload.get(key) != value for key, value in {
            "lease": lease["id"], "kind": "automation", "reference": automation_id,
            "epoch": lease["epoch"], "parent": lease["parent_id"],
        }.items()
    ):
        return unavailable
    run_id = payload.get("run_id")
    if (not _identifier(run_id) or type(payload.get("epoch")) is not int
            or type(rows[0]["created_ns"]) is not int
            or rows[0]["created_ns"] < lease["created_ns"]):
        return unavailable
    target = database.parent.parent / "webui.db"
    if target.is_symlink():
        return unavailable
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5)) as records:
            records.row_factory = sqlite3.Row
            records.execute("PRAGMA query_only=ON")
            records.execute("BEGIN")
            row = records.execute(
                "SELECT r.id,r.chat_id,a.user_id FROM automation_run r "
                "JOIN automation a ON a.id=r.automation_id "
                "WHERE r.id=? AND r.automation_id=?",
                (run_id, automation_id),
            ).fetchone()
            if row is None:
                return unavailable
            chat_url = None
            if isinstance(row["chat_id"], str) and _CHAT_ID.fullmatch(row["chat_id"]):
                chat = records.execute(
                    "SELECT user_id,CASE WHEN json_valid(chat) THEN "
                    "json_extract(chat,'$.meta.automation_id') END AS automation_id "
                    "FROM chat WHERE id=?", (row["chat_id"],)
                ).fetchone()
                if (chat and chat["user_id"] == row["user_id"]
                        and chat["automation_id"] == automation_id):
                    chat_url = "/c/" + row["chat_id"]
            return run_id, chat_url, "Original run identity verified from immutable admission audit and owned stored run."
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return unavailable


def blocked_automation_references(*, maintenance=None, db_path=None) -> set[str]:
    """Return fenced automation IDs before scheduler claims; any read failure denies."""
    module = None
    try:
        module = _module(maintenance)
        database = _path(module, db_path)
        with module._db(database, write=False) as db:
            gate = db.execute("SELECT state,epoch FROM gate WHERE id=1").fetchone()
            if (gate is None or gate["state"] not in {"open", "draining", "sealed"}
                    or type(gate["epoch"]) is not int or gate["epoch"] < 0):
                raise ValueError("Invalid gate snapshot")
            rows = db.execute(
                "SELECT reference FROM lease WHERE kind='automation' "
                "AND state IN ('active','unresolved') LIMIT ?",
                (_MAX_BLOCKED + 1,),
            ).fetchall()
            if len(rows) > _MAX_BLOCKED or any(not _identifier(row["reference"]) for row in rows):
                raise ValueError("Unbounded or invalid admission references")
            return {row["reference"] for row in rows}
    except Exception:
        raise _exception(module)(_UNAVAILABLE) from None


def admission_status(automation_id: str, *, maintenance=None, db_path=None) -> AdmissionStatus:
    """Observe the fixed gate and this automation's original; never create or mutate state."""
    result = _base()
    if not _identifier(automation_id):
        return result
    try:
        module = _module(maintenance)
        database = _path(module, db_path)
        with module._db(database, write=False) as db:
            gate = db.execute("SELECT state,epoch,actor,command_id FROM gate WHERE id=1").fetchone()
            if (gate is None or gate["state"] not in {"open", "draining", "sealed"}
                    or type(gate["epoch"]) is not int or not 0 <= gate["epoch"] <= 9223372036854775807):
                return result
            result.update(
                gate_state=gate["state"], gate_epoch=gate["epoch"],
                operator_owner=_label(gate["actor"]), operator_command_id=_label(gate["command_id"]),
            )
            operator = db.execute(
                "SELECT payload FROM audit WHERE kind='operator_control' AND event_id=?",
                (gate["command_id"],),
            ).fetchone()
            action = _json_object(operator["payload"]) if operator else None
            if (action and action.get("actor") == gate["actor"]
                    and action.get("action") in {"close", "seal", "open"}
                    and type(action.get("expected_epoch")) is int
                    and action["expected_epoch"] == gate["epoch"] - 1):
                result["operator_action"] = action["action"]
            elif gate["command_id"] == "bootstrap" and gate["epoch"] == 0:
                result["operator_action"] = "bootstrap"
            leases = db.execute(
                "SELECT id,kind,reference,epoch,created_ns,state,parent_id FROM lease "
                "WHERE kind='automation' AND reference=? AND state IN ('active','unresolved') LIMIT 2",
                (automation_id,),
            ).fetchall()
            if len(leases) > 1:
                return result
            lease = leases[0] if leases else None
            if lease:
                if (not _identifier(lease["id"]) or type(lease["epoch"]) is not int
                        or type(lease["created_ns"]) is not int):
                    return result
                result.update(lease_id=lease["id"], lease_state=lease["state"])
                try:
                    original, url, evidence = _original_binding(db, database, lease, automation_id)
                except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
                    original, url, evidence = None, None, "Original run evidence could not be verified."
                result.update(original_run_id=original, original_chat_url=url, evidence=evidence)
            policy = module._scope_policy(database)
            if policy is not None:
                project = policy.get("project_id") if isinstance(policy, dict) else None
                if (not _identifier(project) or policy.get("mode") != "governed_project_only"):
                    return result
                result.update(
                    state="blocked", code="scope_restricted",
                    reason="Admission is restricted to governed project " + project + ".",
                    next_action="Ask the recorded maintenance operator to review the authorized project scope. Preserve this original work.",
                )
            elif gate["state"] != "open":
                result.update(
                    state="waiting", code="maintenance_closed",
                    reason="Maintenance admission is " + gate["state"] + "; no new execution is admitted.",
                    next_action="Wait for the recorded operator to complete maintenance and reopen admission through the supported lifecycle.",
                )
            elif lease:
                unresolved = lease["state"] == "unresolved"
                result.update(
                    state="blocked" if unresolved else "waiting",
                    code="original_unresolved" if unresolved else "original_active",
                    reason=("An original admission has an unresolved outcome."
                            if unresolved else "An existing admission still owns this automation; worker activity is not established by the lease alone."),
                    next_action="Verify the original worker and reconcile its exact saved result through the supported lifecycle before another execution.",
                )
            else:
                result.update(
                    state="ready", code="ready", reason="Admission is open for this automation.",
                    next_action="The next trigger must still pass the existing atomic admission check.",
                    evidence="No active or unresolved automation lease was found in this read-only snapshot.",
                )
        return result
    except Exception:
        # Preserve any already-verified original references while reporting that
        # the complete admission snapshot is unavailable. Never return ready.
        result.update(state="blocked", code="admission_unavailable", reason=_UNAVAILABLE, next_action=_VERIFY)
        return result


_DENIALS = {
    "Maintenance admission is closed; original work is preserved": (
        "waiting", "maintenance_closed",
        "Execution was denied before worker creation because maintenance admission was closed.",
        "Use the recorded operator and supported maintenance lifecycle before retrying.",
    ),
    "Admission is restricted to the approved governed Project; original work is preserved": (
        "blocked", "scope_restricted",
        "Execution was denied before worker creation by the governed-project admission scope.",
        "Ask the recorded operator to review the authorized scope; do not switch models to bypass it.",
    ),
    "An original admission remains active or unresolved": (
        "blocked", "original_retained",
        "Execution was denied before worker creation because an original admission still retained ownership.",
        "Verify the original worker and reconcile its exact result through the supported lifecycle.",
    ),
    "Original scheduler batch admission changed": (
        "blocked", "batch_admission_changed",
        "The scheduler batch admission changed before worker creation.",
        "Reconcile the original scheduler batch and its existing claims before retrying.",
    ),
    "Scheduled original run identity differs": (
        "blocked", "original_identity_changed",
        "The scheduled run identity did not match the admitted original; no worker was started.",
        "Reconcile the original run identity and admission audit before retrying.",
    ),
}


def denial_status(automation_id: str, exc: Exception, *, maintenance=None, db_path=None) -> AdmissionStatus:
    """Keep the actual denial primary even when a later snapshot becomes ready."""
    result = admission_status(automation_id, maintenance=maintenance, db_path=db_path)
    try:
        denied_type = _exception(_module(maintenance))
    except Exception:
        denied_type = MaintenanceDenied
    classification = _DENIALS.get(str(exc)) if isinstance(exc, denied_type) else None
    state, code, reason, action = classification or (
        "blocked", "admission_unavailable", _UNAVAILABLE, _VERIFY,
    )
    if code == "original_retained" and result["lease_state"] in {"active", "unresolved"}:
        code = "original_" + result["lease_state"]
        state = "waiting" if result["lease_state"] == "active" else "blocked"
    result.update(state=state, code=code, reason=reason, next_action=action, denial_observed=True)
    return result

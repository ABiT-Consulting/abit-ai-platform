"""Bounded original-observation metadata; never a receipt or execution authority.

Only the existing authenticated original read installs a task-local collector.
No prompt, reply, URL, root selector, nonce, capability or scope proof is stored.
The diagnostic is append-only and cannot dispatch, publish or change run state.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

EVENT_KIND = "original_observation"
EVENT_ACTOR = "system:original-observer"
CODE_STATUS = {
    "ORIGINAL_TAB_MISSING": 404,
    "ORIGINAL_ACCOUNT_MISMATCH": 403,
    "ORIGINAL_CONVERSATION_MISMATCH": 409,
    "ORIGINAL_BINDING_CONFLICT": 409,
    "ORIGINAL_ROOT_MISMATCH": 409,
    "ORIGINAL_BINDING_STORE_UNAVAILABLE": 503,
    "ORIGINAL_ROOT_NOT_VISIBLE": 200,
    "ORIGINAL_ENVELOPE_AMBIGUOUS": 200,
    "ORIGINAL_REPLY_NOT_VERIFIED": 200,
    "ORIGINAL_REPLY_PROTOCOL_UNRECOGNIZED": 200,
    "ORIGINAL_REPLY_ROWS_ABSENT": 200,
    "ORIGINAL_REPLY_ROOT_LINK_CANDIDATE_OUTSIDE_TURN": 200,
    "ORIGINAL_REPLY_ROOT_LINK_REJECTED": 200,
    "ORIGINAL_REPLY_CONTRACT_ABSENT": 200,
    "ORIGINAL_REPLY_CONTRACT_MULTIPLE": 200,
    "ORIGINAL_REPLY_CONTRACT_MALFORMED": 200,
    "ORIGINAL_REPLY_CONTRACT_IDENTITY_MISMATCH": 200,
    "ORIGINAL_REPLY_CONTRACT_OVERSIZED": 200,
    "ORIGINAL_REPLY_CONTRACT_INDENTED": 200,
    "ORIGINAL_REPLY_TERMINAL_UNSTABLE": 200,
    "ORIGINAL_COPY_CONTROL_MISSING": 200,
    "ORIGINAL_COPY_CONTROL_AMBIGUOUS": 200,
    "ORIGINAL_COPY_PROBE_CHANGED": 200,
    "ORIGINAL_COPY_CAPABILITY_PRESENT": 200,
    "ORIGINAL_COPY_PERMISSION_PROMPT": 200,
    "ORIGINAL_COPY_PERMISSION_DENIED": 200,
    "ORIGINAL_COPY_PERMISSION_UNAVAILABLE": 200,
    "ORIGINAL_COPY_PROBE_UNAVAILABLE": 200,
    "ORIGINAL_COPY_CONTROL_DISABLED": 200,
    "ORIGINAL_COPY_BROWSER_BUSY": 200,
    "ORIGINAL_COPY_ACK_STALE": 200,
    "ORIGINAL_COPY_ACK_UNAVAILABLE": 200,
    "ORIGINAL_COPY_READ_UNAVAILABLE": 200,
    "ORIGINAL_COPY_SOURCE_EMPTY": 200,
    "ORIGINAL_COPY_SOURCE_OVERSIZED": 200,
    "ORIGINAL_COPY_SOURCE_UNREPRESENTABLE": 200,
    "ORIGINAL_COPY_SOURCE_CHANGED": 200,
    "ORIGINAL_COPY_RAW_CONTRACT_ABSENT": 200,
    "ORIGINAL_COPY_RAW_CONTRACT_MULTIPLE": 200,
    "ORIGINAL_COPY_RAW_CONTRACT_MALFORMED": 200,
    "ORIGINAL_COPY_RAW_CONTRACT_IDENTITY_MISMATCH": 200,
    "ORIGINAL_COPY_RAW_CONTRACT_OVERSIZED": 200,
    "ORIGINAL_COPY_RAW_CONTRACT_INDENTED": 200,
    "ORIGINAL_COPY_RAW_UNVERIFIED": 200,
    "ORIGINAL_GENERATING": 200,
    "ORIGINAL_TERMINAL_VERIFIED": 200,
    "ORIGINAL_REJECTED_BEFORE_MODEL": 200,
    "ORIGINAL_BINDING_MISSING": 200,
}
_BINDING_FIELDS = (
    "invocation_id", "erp_project", "erp_task", "target_role", "agent_id",
    "automation_run_id", "packet_hash", "submitted_at",
)
_collector = ContextVar("original_observation_collector", default=None)


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()).hexdigest()


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _original_matches(row, automation, envelope):
    try:
        raw = row["work_packet"]
        if not isinstance(raw, str) or len(raw.encode()) > 200000:
            return False
        packet = json.loads(raw)
        if not isinstance(packet, dict) or _digest(packet) != row["packet_hash"]:
            return False
        packet_digest = _digest({k: v for k, v in packet.items() if k != "content_sha256"})
        if packet.get("content_sha256") != packet_digest:
            return False
        owner = getattr(automation, "user_id", None)
        scope = envelope["governance_scope"]["scope"]
        if not isinstance(scope, dict):
            return False
        expected = {"owner": owner, "agent_id": row["agent_id"],
                    "invocation_id": row["invocation_id"], "run_id": row["automation_run_id"],
                    "project_id": row["erp_project"], "task_id": row["erp_task"],
                    "role": row["target_role"], "work_packet_digest": packet_digest}
        return (bool(_binding(row, owner))
                and envelope.get("owner_id") == owner
                and envelope.get("automation_id") == row["agent_id"]
                and envelope.get("run_id") == row["automation_run_id"]
                and all(scope.get(key) == value for key, value in expected.items()))
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError, RecursionError):
        return False


@contextmanager
def collect_original_observation(envelope, row, automation):
    """Keep the existing observe_original(envelope) signature and return shape."""
    collected = []
    state = None
    try:
        if _original_matches(row, automation, envelope):
            state = {"task": _task(), "envelope_hash": _digest(envelope),
                     "nonce": None, "used": False, "items": collected}
    except (TypeError, ValueError, UnicodeError, RecursionError):
        pass  # Optional metadata cannot make a legacy envelope unreadable.
    token = _collector.set(state)
    try:
        yield collected
    finally:
        _collector.reset(token)


def _active(envelope):
    state = _collector.get()
    if state is None or state["task"] is not _task():
        return None  # Child tasks may inherit context, never its authority.
    try:
        return state if state["envelope_hash"] == _digest(envelope) else None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


def new_observation_id(envelope):
    state = _active(envelope)
    if state is None:
        return None
    state["nonce"], state["used"] = uuid4().hex, False
    return state["nonce"]


_SELECTION_KEYS = {
    "mode", "root_source", "turn_rows", "selected_rows", "visible_rows",
    "root_references", "unmatched_references", "agov_markers",
    "alternate_attributes", "candidate", "stage",
}
_SELECTION_OPTIONAL = {"copy_controls", "copy_acknowledged", "source_bytes", "source_sha256", "control_diagnostic",
                       "source_kind", "literal_status", "literal_blocks", "literal_contracts", "copy_failure", "copy_permission"}
_LITERAL_FALLBACK = {"control_missing", "control_ambiguous", "control_disabled",
                    "permission_prompt", "permission_denied", "permission_unavailable",
                    "ack_stale", "ack_unavailable", "read_unavailable"}
_LITERAL_STATUSES = {"block_absent", "block_multiple", "block_unexposed", "block_unsafe", "block_limit",
                    "text_transformed", "text_mismatch", "contract_not_single_line", "source_oversized",
                    "source_unrepresentable", "selection_changed", "source_observed", "unavailable"}
_CONTROL_COUNTS = {
    "buttons", "role_buttons", "visible", "copy", "ack", "copy_visible", "body", "nested",
    "foreign", "unlabelled", "disabled", "accepted", "copy_body", "copy_nested",
    "copy_hidden", "copy_opacity", "menus",
}


def _selection(value):
    """Fixed counts/flags and an optional stable-source hash; never reply text."""
    if (not isinstance(value, dict) or not _SELECTION_KEYS.issubset(value)
            or set(value) - (_SELECTION_KEYS | _SELECTION_OPTIONAL)
            or value["mode"] not in ("linked_terminal", "linked_selection_rejected", "bounded_original_turn_candidate")
            or value["root_source"] not in ("full_envelope", "restored_root")
            or value["stage"] not in ("selection", "control", "permission", "acknowledgement", "read", "validation", "complete", "busy")
            or type(value["candidate"]) is not bool):
        return None
    for key in ("turn_rows", "selected_rows", "visible_rows", "root_references",
                "unmatched_references", "agov_markers", "alternate_attributes", "copy_controls"):
        if key in value and (type(value[key]) is not int
                or not 0 <= value[key] <= (15 if key == "alternate_attributes" else 65535)):
            return None
    if "copy_acknowledged" in value and type(value["copy_acknowledged"]) is not bool:
        return None
    source = "source_bytes" in value or "source_sha256" in value
    literal = "literal_status" in value
    if literal:
        if (not isinstance(value["literal_status"], str) or value["literal_status"] not in _LITERAL_STATUSES
                or not isinstance(value.get("copy_failure"), str) or value["copy_failure"] not in _LITERAL_FALLBACK
                or type(value.get("literal_blocks")) is not int or not 0 <= value["literal_blocks"] <= 128
                or type(value.get("literal_contracts")) is not int or not 0 <= value["literal_contracts"] <= value["literal_blocks"]
                or ("copy_permission" in value and value["copy_permission"] not in ("granted", "prompt", "denied", "unavailable"))):
            return None
        if source:
            if (value.get("source_kind") != "literal_pre_code" or value["literal_status"] != "source_observed"
                    or value["literal_contracts"] != 1 or value.get("copy_acknowledged") is not False):
                return None
        elif "source_kind" in value or value["literal_status"] == "source_observed":
            return None
    elif any(key in value for key in ("source_kind", "literal_blocks", "literal_contracts", "copy_failure", "copy_permission")):
        return None
    if source:
        digest = value.get("source_sha256")
        if (type(value.get("source_bytes")) is not int or not 1 <= value["source_bytes"] <= 250000
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or (not literal and value.get("copy_acknowledged") is not True)
                or value["stage"] not in ("validation", "complete")):
            return None
    if "control_diagnostic" in value:
        diagnostic = value["control_diagnostic"]
        if (not isinstance(diagnostic, dict) or set(diagnostic) != _CONTROL_COUNTS | {"permission"}
                or any(type(diagnostic[key]) is not int or not 0 <= diagnostic[key] <= 512 for key in _CONTROL_COUNTS)
                or diagnostic["permission"] not in ("granted", "prompt", "denied", "unavailable")
                or value["stage"] != "control" or value["candidate"] is not True
                or value.get("copy_acknowledged") is not False
                or "source_bytes" in value or "source_sha256" in value):
            return None
        return {**value, "control_diagnostic": dict(diagnostic)}
    return dict(value)


def capture_transport_observation(envelope, payload, account, status, data):
    """Accept metadata only from this exact authenticated request and scope.

    The caller already uses the fixed local endpoint, redirects disabled and
    the existing primary credential. The random echo binds this read to its
    current response; it is neither part of the immutable envelope nor authority.
    A malformed diagnostic must never change the existing receipt decision.
    """
    state = _active(envelope)
    if state is None or state["used"] or not state["nonce"]:
        return False
    try:
        if not isinstance(data, dict) or not isinstance(payload, dict):
            return False
        code = data.get("observation_code")
        if (not isinstance(code, str) or CODE_STATUS.get(code) != status
                or payload.get("observation_id") != state["nonce"]
                or data.get("observation_id") != state["nonce"]
                or data.get("read_only") is not True
                or data.get("owner") != envelope.get("owner_id")
                or data.get("job_id") != payload.get("job_id")
                or data.get("account_id") != account
                or not isinstance(envelope.get("governance_scope"), dict)):
            return False
        from open_webui.utils.agentic_relay_scope import scope_receipt
        scope_receipt(envelope, account, data.get("scope_receipt"))
        if code == "ORIGINAL_TERMINAL_VERIFIED":
            from open_webui.utils.abitdot_primary_relay import receipt_from_relay
            if (not isinstance(data.get("root_message_id"), str) or not data["root_message_id"]
                    or not isinstance(data.get("reply_message_id"), str) or not data["reply_message_id"]
                    or receipt_from_relay(envelope, data, account).get("status") != "accepted"):
                return False
        elif code == "ORIGINAL_REJECTED_BEFORE_MODEL":
            from open_webui.utils.abitdot_original_rejection import verify_rejection
            verify_rejection(envelope, data)
        elif data.get("dot_handoff") is not False or data.get("response") != "":
            return False
        selection = _selection(data["selection"]) if "selection" in data else None
        if "selection" in data and selection is None:
            return False
        state["used"] = True
        state["items"].append({"code": code, "checked_at": int(time.time()),
                               **({"selection": selection} if selection is not None else {})})
        return True
    except Exception:
        # Diagnostic validation cannot authorize or reject a provider receipt.
        return False


def _valid_observation(value):
    return (
        isinstance(value, dict) and set(value) in ({"code", "checked_at"}, {"code", "checked_at", "selection"})
        and isinstance(value["code"], str) and value["code"] in CODE_STATUS
        and type(value["checked_at"]) is int and value["checked_at"] > 0
        and ("selection" not in value or _selection(value["selection"]) is not None)
    )


def _binding(row, owner):
    if not isinstance(owner, str) or not owner:
        return None
    try:
        value = {key: row[key] for key in _BINDING_FIELDS}
    except (KeyError, IndexError, TypeError):
        return None
    if (any(not isinstance(value[key], str) or not value[key]
            for key in _BINDING_FIELDS if key != "submitted_at")
            or type(value["submitted_at"]) is not int or value["submitted_at"] <= 0):
        return None
    value["owner_id"] = owner
    return _digest(value)


def append_original_observation(row, automation, observation, *, db_path=None):
    """Append against a rechecked original only; no invocation update occurs."""
    if not _valid_observation(observation):
        return False
    now = int(time.time())
    if not now - 60 <= observation["checked_at"] <= now:
        return False
    owner = getattr(automation, "user_id", None)
    expected = _binding(row, owner)
    if not expected:
        return False
    from open_webui.utils import agentic_governance as ledger
    target = Path(db_path) if db_path is not None else ledger.path()
    if not target.is_file():
        return False  # This observation never creates a missing journal.
    with ledger._transaction(target) as db:
        current = db.execute(
            "SELECT i.*,a.owner_id FROM governance_invocation i "
            "JOIN governance_agent a ON a.agent_id=i.agent_id "
            "AND a.erp_project=i.erp_project AND a.role=i.target_role "
            "WHERE i.invocation_id=?", (row["invocation_id"],),
        ).fetchone()
        if (current is None or current["owner_id"] != owner
                or _binding(current, owner) != expected
                or current["work_packet"] != row["work_packet"]):
            return False
        ledger._event(db, current["erp_project"], current["invocation_id"],
                      EVENT_KIND, EVENT_ACTOR, {
                          "schema_version": 1, **observation, "binding_sha256": expected,
                      })
    return True


def latest_original_observation(db, row):
    """Return only bounded metadata from the exact latest original event."""
    expected = _binding(row, row["owner_id"])
    if not expected:
        return None
    event = db.execute(
        "SELECT CASE WHEN length(CAST(payload AS BLOB))<=1536 THEN payload END AS payload,"
        "created_at FROM governance_event WHERE erp_project=? AND invocation_id=? "
        "AND kind=? AND actor=? ORDER BY seq DESC LIMIT 1",
        (row["erp_project"], row["invocation_id"], EVENT_KIND, EVENT_ACTOR),
    ).fetchone()
    if event is None or not isinstance(event["payload"], str):
        return None
    try:
        payload = json.loads(event["payload"])
        if (not isinstance(payload, dict)
                or set(payload) not in ({"schema_version", "code", "checked_at", "binding_sha256"},
                                        {"schema_version", "code", "checked_at", "binding_sha256", "selection"})
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or payload["binding_sha256"] != expected
                or not _valid_observation({key: payload[key] for key in ("code", "checked_at", "selection") if key in payload})
                or type(event["created_at"]) is not int
                or payload["checked_at"] > event["created_at"]):
            return None
        return {key: payload[key] for key in ("code", "checked_at", "selection") if key in payload}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None

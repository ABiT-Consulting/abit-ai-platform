"""Validate original ERP result receipts without authorizing publication replay."""
from __future__ import annotations

from copy import deepcopy
import re

HEX256 = re.compile(r"[0-9a-f]{64}")


def _digest(value):
    from open_webui.utils.agentic_bridge import digest
    return digest(value)


def validate_receipt(event, receipt):
    if not isinstance(event, dict) or not isinstance(receipt, dict):
        raise ValueError("Original ERP receipt requires its exact event")
    for key in ("event_id", "task_id", "run_id"):
        if not isinstance(event.get(key), str) or not event[key] or receipt.get(key) != event[key]:
            raise ValueError("ERP receipt original identity differs")
    if receipt.get("durable") is not True:
        raise ValueError("ERP receipt is not durable")
    if any(not isinstance(receipt.get(key), str) or not receipt[key].strip()
           for key in ("modified", "status")):
        raise ValueError("ERP receipt has no verified business readback")
    if "duplicate" in receipt and type(receipt["duplicate"]) is not bool:
        raise ValueError("ERP receipt duplicate marker is not a boolean")
    # These are optional in the established native receipt. If a newer native
    # contract supplies them, it cannot contradict the immutable event.
    for key in ("project_id", "invocation_id", "agent_id", "role", "task_revision",
                "work_packet_digest"):
        if key in receipt and receipt[key] != event.get(key):
            raise ValueError("ERP receipt event binding differs")
    return receipt


def semantic_receipt(event, receipt):
    validate_receipt(event, receipt)
    # Only this transport marker is excluded; unknown business fields remain
    # immutable. The first complete raw receipt stays in the existing journal.
    return {key: value for key, value in receipt.items() if key != "duplicate"}


def observed_receipt(event, response):
    message = response.get("message") if isinstance(response, dict) else None
    if not isinstance(message, dict):
        raise ValueError("ERP original receipt observation is malformed")
    if message.get("exists") is False:
        if message.get("event_id") != event.get("event_id"):
            raise ValueError("ERP negative receipt observation names another event")
        return None
    if message.get("exists") is not True:
        raise ValueError("ERP original receipt existence is unverified")
    content_sha256 = message.get("content_sha256")
    if (not isinstance(content_sha256, str) or not HEX256.fullmatch(content_sha256)
            or content_sha256 != _digest(event)):
        raise ValueError("Native ERP inbox digest differs from the exact saved event")
    if "receipt" in message:
        receipt = message["receipt"]
        if set(message) - {"exists", "content_sha256", "event_id", "receipt"}:
            raise ValueError("ERP receipt observation wrapper is unsupported")
        if "event_id" in message and message["event_id"] != event.get("event_id"):
            raise ValueError("ERP receipt observation wrapper identity differs")
    else:
        receipt = {key: value for key, value in message.items()
                   if key not in {"exists", "content_sha256"}}
    validate_receipt(event, receipt)
    return receipt, content_sha256


def validate_saved_receipt(saved):
    event = saved["event"]
    if (event.get("invocation_id") != saved["invocation_id"]
            or event.get("run_id") != saved["run_id"]
            or _digest(saved["result"]) != saved["result_sha256"]):
        raise ValueError("Saved publication identity or result integrity differs")
    return validate_receipt(event, saved["receipt"])


def prepare_same_event_retry(saved, *, native_contract=None):
    """Pure preparation only: no caller, transport, state write or enabled retry.

    An absent GET receipt alone is deliberately insufficient. A later reviewed
    activation must bind installed native contract hashes, fresh permissions,
    controls and context, and the unchanged event to a bounded audited POST.
    This function cannot mark a retry safe or perform it.
    """
    event = saved["event"]
    if (saved.get("phase") != "uncertain" or event.get("invocation_id") != saved.get("invocation_id")
            or event.get("run_id") != saved.get("run_id")
            or _digest(saved["result"]) != saved.get("result_sha256")):
        raise ValueError("Only an exact uncertain saved event can be prepared")
    if native_contract is not None:
        raise ValueError("Native contract activation is not implemented in this source-only preparation")
    return {
        "enabled": False, "event": deepcopy(event), "event_sha256": _digest(event),
        "invocation_id": saved["invocation_id"], "run_id": saved["run_id"],
        "required_gate": "reviewed_installed_native_contract_and_current_authorization",
    }

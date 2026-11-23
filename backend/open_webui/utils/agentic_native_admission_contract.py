"""Pure versioned contracts for fresh, fixed-profile native admission.

Validation is not authentication. Only the registered root broker accepts these
from its twice-verified Core process over the existing challenge/HMAC transport.
No object supplied by a model, ERP form or public API is an admission attestation.
"""
from __future__ import annotations
import re

if __package__ == "open_webui.utils":
    from open_webui.utils.agentic_native_contract import (
        canonical, digest, exact, require, sha, validate_scope, validate_maintenance,
        validate_gate, OPERATION, PROFILE_ID)
else:
    from agentic_native_contract import (
        canonical, digest, exact, require, sha, validate_scope, validate_maintenance,
        validate_gate, OPERATION, PROFILE_ID)

MODE = "per_invocation_v2"
PROJECT = "PROJ-0202"
ACTION_FIELDS = ("project_id", "task_id", "task_revision", "actor", "repository",
                 "environment", "operation", "risk", "parameters")
ATTESTATION_KEYS = frozenset((
    "schema_version", "kind", "scope", "subject_sha256", "action", "approval",
    "consumption", "lease_token_sha256", "lease_expires_at", "start_receipt_sha256",
    "erp_baseline_sha256", "maintenance_admission", "prepared_at"))
APPROVAL_KEYS = frozenset(("schema_version", "approval_id", "project_id", "task_id",
    "action_fingerprint", "approved_by", "approved_at", "expires_at", "status", "used_by_action_id"))
CONSUMPTION_KEYS = frozenset(("consumed", "action_id", "approval_id", "action_fingerprint",
    "invocation_id", "run_id", "project_id", "task_id", "receipt_id", "duplicate"))


def bounded_text(value, code, maximum=254):
    require(type(value) is str and 0 < len(value) <= maximum and
            not any(ord(c) < 32 for c in value), code)
    return value


def fingerprint(action):
    return digest({key: action[key] for key in ACTION_FIELDS})


def validate_attestation(value, now, *, current=True):
    exact(value, ATTESTATION_KEYS, "NATIVE_ADMISSION_ATTESTATION_SCHEMA")
    scope = validate_scope(value["scope"])
    require(scope["project_id"] == PROJECT and type(value["schema_version"]) is int and
            value["schema_version"] == 2 and value["kind"] == "abit_native_admission",
            "NATIVE_ADMISSION_FIXED_PROJECT_REQUIRED")
    for key in ("subject_sha256", "lease_token_sha256", "start_receipt_sha256",
                "erp_baseline_sha256"):
        sha(value[key])
    require(type(value["prepared_at"]) is int and type(value["lease_expires_at"]) is int and
            0 <= value["prepared_at"] < value["lease_expires_at"],
            "NATIVE_ADMISSION_LEASE_WINDOW")
    validate_maintenance(value["maintenance_admission"], scope["invocation_id"])
    action, approval, consumed = value["action"], value["approval"], value["consumption"]
    exact(action, set(ACTION_FIELDS) | {"schema_version", "action_id", "requested_at"},
          "NATIVE_ADMISSION_ACTION_SCHEMA")
    exact(action["actor"], {"project_id", "agent_id", "role", "erp_user"},
          "NATIVE_ADMISSION_ACTOR_SCHEMA")
    require(action["actor"] == {key: scope[key] for key in action["actor"]} and
            all(action[key] == scope[key] for key in
                ("project_id", "task_id", "task_revision", "repository", "environment")) and
            type(action["schema_version"]) is int and action["schema_version"] == 1 and
            action["operation"] == OPERATION and action["risk"] == "privileged" and
            action["parameters"] == {"profile_id": PROFILE_ID} and
            type(action["requested_at"]) is int and 0 <= action["requested_at"] <= value["prepared_at"] and
            type(action["action_id"]) is str and
            re.fullmatch(r"agov-action-[a-f0-9]{48}", action["action_id"]),
            "NATIVE_ADMISSION_ACTION_SCOPE")
    exact(approval, APPROVAL_KEYS, "NATIVE_ADMISSION_APPROVAL_SCHEMA")
    bounded_text(approval["approval_id"], "NATIVE_ADMISSION_APPROVAL_ID")
    approver = bounded_text(approval["approved_by"], "NATIVE_ADMISSION_APPROVER")
    require(not approver.lower().startswith("ai-") and approver != scope["erp_user"] and
            type(approval["schema_version"]) is int and approval["schema_version"] == 1 and
            all(approval[key] == scope[key] for key in ("project_id", "task_id")) and
            approval["action_fingerprint"] == fingerprint(action) and
            approval["used_by_action_id"] == action["action_id"] and approval["status"] == "approved" and
            type(approval["approved_at"]) is int and type(approval["expires_at"]) is int and
            0 <= approval["approved_at"] <= value["prepared_at"] < approval["expires_at"],
            "NATIVE_ADMISSION_CURRENT_CONSUMED_APPROVAL")
    exact(consumed, CONSUMPTION_KEYS, "NATIVE_ADMISSION_CONSUMPTION_SCHEMA")
    bounded_text(consumed["receipt_id"], "NATIVE_ADMISSION_RECEIPT_ID")
    require(consumed["consumed"] is True and type(consumed["duplicate"]) is bool and
            consumed["action_id"] == action["action_id"] and
            consumed["approval_id"] == approval["approval_id"] and
            consumed["action_fingerprint"] == fingerprint(action) and
            all(consumed[key] == scope[key] for key in
                ("invocation_id", "run_id", "project_id", "task_id")),
            "NATIVE_ADMISSION_CONSUMPTION_SCOPE")
    if current:
        require(type(now) is int and value["prepared_at"] <= now <= value["prepared_at"] + 30 and
                now < min(value["lease_expires_at"], approval["expires_at"]),
                "NATIVE_ADMISSION_ATTESTATION_EXPIRED")
    return value


def validate_admission_grant(grant, payload, now):
    attestation = payload.get("attestation") if type(payload) is dict else None
    exact(payload, {"attestation"}, "NATIVE_ADMISSION_PAYLOAD_SCHEMA")
    validate_attestation(attestation, now)
    exact(grant, {"authorized", "mode", "request_payload_sha256", "attestation_sha256",
                  "maintenance_admission", "issued_at", "expires_at"}, "NATIVE_ADMISSION_GRANT_SCHEMA")
    require(grant["authorized"] is True and grant["mode"] == "admit" and
            grant["request_payload_sha256"] == digest(payload) and
            grant["attestation_sha256"] == digest(attestation) and
            grant["maintenance_admission"] == attestation["maintenance_admission"] and
            type(grant["issued_at"]) is int and type(grant["expires_at"]) is int and
            attestation["prepared_at"] <= grant["issued_at"] <= now < grant["expires_at"] <=
            min(grant["issued_at"] + 15, attestation["lease_expires_at"],
                attestation["approval"]["expires_at"]),
            "NATIVE_ADMISSION_FRESH_GRANT_REQUIRED")
    return grant


def readiness(scope, subject_sha256):
    validate_scope(scope)
    require(scope["project_id"] == PROJECT, "NATIVE_ADMISSION_FIXED_PROJECT_REQUIRED")
    sha(subject_sha256)
    return {"schema_version": 2, "kind": "abit_native_admission_readiness", "scope": scope,
            "subject_sha256": subject_sha256, "authority_issued": False,
            "capacity_reserved": False, "nonce": None}


def validate_readiness(value, scope):
    require(type(value) is dict and value == readiness(scope, value.get("subject_sha256")),
            "NATIVE_ADMISSION_READINESS_CHANGED")
    return value


def observation(attestation, gate=None):
    validate_attestation(attestation, 0, current=False)
    if gate is not None:
        validate_gate(gate, attestation["scope"], gate["issued_at"], admission=False)
        require(gate.get("schema_version") == 2 and gate.get("admission_sha256") == digest(attestation) and
                gate.get("subject_sha256") == attestation["subject_sha256"],
                "NATIVE_ADMISSION_GATE_CHANGED")
    return {"schema_version": 2, "kind": "abit_native_admission_receipt",
            "attestation_sha256": digest(attestation),
            "state": "admitted" if gate is not None else "uncertain",
            "gate": gate, "automatic_replay_permitted": False}


def validate_observation(value, attestation):
    require(type(value) is dict and value == observation(attestation, value.get("gate")),
            "NATIVE_ADMISSION_OBSERVATION_CHANGED")
    return value

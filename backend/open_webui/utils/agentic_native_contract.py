"""Bounded contracts for the fixed native verification lane.

This is a separate binding around the unchanged native v4 authority. It does not
issue authority, choose commands, or turn verification into independent QA.
"""
from __future__ import annotations
import hashlib
import json
import re

PROFILE_ID = "bigrro-proj0202-fixed-v1"
OPERATION = "verify_native_source"
REPOSITORY = "https://github.com/asma2rjoub-dotcom/bigrro-platform"
WORKER_HEAD = "0dcaa6f4b003cbd2a9408c24c9302dcbfd189dc4"
WORKER_TREE = "87a42a0d33a451fa3dc9d864e91eecc05b2e8602"
PLAN_SHA = "8da55a6cd825b44bf58070f82c08357521321ffbbf092bb4af199492a9a36c62"
PROFILE_SHA = "8da4952439ea45568f74fa1afe53fa112beb1c104e735194fda0d2cbd34a1633"
RUNTIME_SHA = "a2f018a7f52cff73aa448230a40836000459ec226fde24832be4c2c85ce41fe5"
SCOPE_KEYS = frozenset(("invocation_id", "run_id", "project_id", "task_id", "task_revision",
    "project_revision", "agent_id", "erp_user", "role", "packet_sha256", "context_sha256",
    "srs_revision", "srs_sha256", "lease_fence", "repository", "environment", "profile_id"))
EXECUTION_KEYS = frozenset(("nonce", "authority_sha256", "execution_manifest_sha256",
    "worker_head", "worker_tree", "plan_sha256", "executor_profile_sha256", "runtime_manifest_sha256"))
RECEIPT_KEYS = frozenset(("schema_version", "state", "binding_sha256", "gate_sha256", "nonce",
    "authority_sha256", "execution_manifest_sha256", "worker_head", "outcome_sha256",
    "worker_result_sha256", "reservation_sha256", "cleanup_confirmed", "operations_count",
    "automatic_replay_permitted", "authority_issued"))


def require(value, code):
    if not value:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def exact(value, keys, code):
    require(type(value) is dict and set(value) == set(keys), code)


def sha(value):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value), "NATIVE_DIGEST_INVALID")
    return value


def nonce(value):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{32}", value) and value != "0" * 32,
            "NATIVE_NONCE_INVALID")
    return value


def validate_scope(value):
    exact(value, SCOPE_KEYS, "NATIVE_SCOPE_INVALID")
    for key in SCOPE_KEYS - {"lease_fence"}:
        require(type(value[key]) is str and 0 < len(value[key]) <= 254 and
                not any(ord(char) < 32 for char in value[key]), "NATIVE_SCOPE_VALUE_INVALID")
    for key in ("packet_sha256", "context_sha256", "srs_sha256"):
        sha(value[key])
    require(re.fullmatch(r"PROJ-[A-Za-z0-9-]{1,60}", value["project_id"]) and
            re.fullmatch(r"TASK-[A-Za-z0-9-]{1,60}", value["task_id"]) and
            re.fullmatch(r"[A-Za-z0-9_.:@+-]{1,160}", value["invocation_id"]) and
            re.fullmatch(r"[a-f0-9-]{36}", value["run_id"]) and
            re.fullmatch(r"[a-f0-9-]{36}", value["agent_id"]) and
            type(value["lease_fence"]) is int and value["lease_fence"] > 0 and
            value["role"] == "it_manager" and value["profile_id"] == PROFILE_ID and
            value["repository"].removesuffix(".git").rstrip("/") == REPOSITORY and
            value["environment"] == "ABIT-DEV-LINUX",
            "NATIVE_SCOPE_FIXED_LANE_REQUIRED")
    return value


def validate_execution(value):
    exact(value, EXECUTION_KEYS, "NATIVE_EXECUTION_INVALID")
    nonce(value["nonce"])
    for key in EXECUTION_KEYS - {"nonce", "worker_head", "worker_tree"}:
        sha(value[key])
    require(value["worker_head"] == WORKER_HEAD and value["worker_tree"] == WORKER_TREE and
            value["plan_sha256"] == PLAN_SHA and value["executor_profile_sha256"] == PROFILE_SHA and
            value["runtime_manifest_sha256"] == RUNTIME_SHA, "NATIVE_WORKER_SCOPE_CHANGED")
    return value


def validate_gate(value, scope, now, *, admission=True):
    exact(value, {"schema_version", "kind", "scope", "execution", "issued_at", "expires_at"},
          "NATIVE_GATE_INVALID")
    validate_scope(value["scope"])
    validate_execution(value["execution"])
    require(type(value["schema_version"]) is int and value["schema_version"] == 1 and
            value["kind"] == "abit_native_verification_gate" and value["scope"] == scope,
            "NATIVE_GATE_SCOPE_CHANGED")
    issued, expires = value["issued_at"], value["expires_at"]
    require(type(issued) is int and type(expires) is int and
            0 < expires - issued <= 7200 and issued <= now,
            "NATIVE_GATE_WINDOW_INVALID")
    if admission:
        require(now < expires, "NATIVE_GATE_EXPIRED")
    return value


def validate_maintenance(value, invocation):
    exact(value, {"lease_id", "kind", "reference", "epoch"}, "NATIVE_MAINTENANCE_PROOF_INVALID")
    require(type(value["lease_id"]) is str and re.fullmatch(r"[a-f0-9]{48}", value["lease_id"]) and
            value["kind"] == "governed" and value["reference"] == invocation and
            type(value["epoch"]) is int and value["epoch"] >= 0,
            "NATIVE_MAINTENANCE_SCOPE_CHANGED")
    return value


def validate_binding(value, gate):
    exact(value, {"schema_version", "scope", "execution", "gate_sha256", "lease_token_sha256",
                  "start_receipt_sha256", "erp_baseline_sha256", "action_id", "native_approval_sha256", "maintenance_admission", "created_at"}, "NATIVE_BINDING_INVALID")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1 and
            value["scope"] == gate["scope"] and value["execution"] == gate["execution"] and
            value["gate_sha256"] == digest(gate) and type(value["created_at"]) is int and
            gate["issued_at"] <= value["created_at"] < gate["expires_at"],
            "NATIVE_BINDING_SCOPE_CHANGED")
    sha(value["lease_token_sha256"])
    sha(value["start_receipt_sha256"])
    sha(value["erp_baseline_sha256"])
    sha(value["native_approval_sha256"])
    validate_maintenance(value["maintenance_admission"], value["scope"]["invocation_id"])
    require(type(value["action_id"]) is str and re.fullmatch(r"agov-action-[a-f0-9]{48}", value["action_id"]),
            "NATIVE_ACTION_BINDING_INVALID")
    return value


def validate_receipt(value, binding):
    exact(value, RECEIPT_KEYS, "NATIVE_RECEIPT_INVALID")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1 and
            value["state"] in {"uncertain", "running", "completed", "failed_retained"} and
            value["binding_sha256"] == digest(binding) and
            value["gate_sha256"] == binding["gate_sha256"] and
            all(value[key] == binding["execution"][key] for key in
                ("nonce", "authority_sha256", "execution_manifest_sha256", "worker_head")) and
            value["automatic_replay_permitted"] is False and value["authority_issued"] is False and
            type(value["cleanup_confirmed"]) is bool and type(value["operations_count"]) is int,
            "NATIVE_RECEIPT_SCOPE_CHANGED")
    for key in ("outcome_sha256", "worker_result_sha256", "reservation_sha256"):
        require(value[key] is None or (type(value[key]) is str and
                re.fullmatch(r"[a-f0-9]{64}", value[key])), "NATIVE_RECEIPT_DIGEST_INVALID")
    require(value["operations_count"] in {0, 10}, "NATIVE_RECEIPT_OPERATION_COUNT_INVALID")
    if value["state"] == "completed":
        require(value["cleanup_confirmed"] and value["operations_count"] == 10 and
                all(value[key] for key in ("outcome_sha256", "worker_result_sha256", "reservation_sha256")),
                "NATIVE_COMPLETION_UNCONFIRMED")
    else:
        require(value["operations_count"] == 0, "NATIVE_NONTERMINAL_SUCCESS_CLAIM")
    return value

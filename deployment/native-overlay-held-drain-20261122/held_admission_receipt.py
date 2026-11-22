"""New, operation-specific frozen receipt using the retained native HMAC transport.

This module neither reads a secret nor opens a socket nor changes a gate. The
root holder must capture the same current Core close and relay ingress seal twice,
keep both continuously held, and retain the exclusive release lock until native
outcome reconciliation. A signature is not a remotely enforced future lock.

The prior F33 schema/epoch/receipt is deliberately not accepted. The existing
control key is supplied only by separately reviewed fixed-path operator code.
"""
from __future__ import annotations
import base64
import datetime
import hashlib
import hmac
import json
import re

PURPOSE = "native-two-role-leaf-repair-held-admission-v1"
TABLE = "abit_native_leaf_20261122"
ADMISSION_SECONDS = 120
OPERATION_SECONDS = 600
BODY_SECONDS = 420
HOLDER_RESERVE_SECONDS = 60
MAX_ENVELOPE = 32768
SHA = re.compile(r"[a-f0-9]{64}\Z")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
BINDING_KEYS = {"operation_id", "controller_sha256", "manifest_sha256", "holder_sha256",
                "core", "relay", "native_preflight_sha256", "restore_receipt_sha256",
                "holder_lease_expires_at"}
CORE_KEYS = {"pid", "start_ticks", "epoch", "close_command_sha256", "actor_sha256",
             "control_receipt_sha256", "maintenance_source_sha256", "history_sha256"}
RELAY_KEYS = {"pid", "start_ticks", "source_sha256", "table_name", "table_handle",
              "objects_sha256", "preserved_rules_sha256", "keeper_proof_sha256",
              "quiet_seconds_required"}
OBSERVATION_KEYS = {"captured_at", "holder_sha256", "core", "relay", "counts",
                    "core_state", "relay_state", "native_preflight_sha256"}
COUNTS = {"current_core_active_or_unresolved": 0, "governance_running": 0,
          "codex_running": 0, "active_http_handlers": 0, "active_relay_turns": 0,
          "erp_active_turns": 0, "erp_inspections": 0, "usable_ingress": 0,
          "node_network_work": 0}


class Held(ValueError):
    pass


def need(value, code):
    if not value:
        raise Held(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def equal(left, right):
    # Avoid Python bool/int equality weakening an immutable typed binding.
    return canonical(left) == canonical(right)


def unique(items):
    value = {}
    for key, item in items:
        need(key not in value, "DUPLICATE_HOLD_RECEIPT_KEY")
        value[key] = item
    return value


def instant(value):
    need(type(value) is str and TIME.fullmatch(value), "EXACT_HOLD_TIME_REQUIRED")
    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        raise Held("INVALID_HOLD_TIME") from None
    return result


def utc(value):
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def validate_binding(binding):
    need(type(binding) is dict and set(binding) == BINDING_KEYS, "EXACT_OVERLAY_BINDING_REQUIRED")
    for key in BINDING_KEYS - {"core", "relay", "holder_lease_expires_at"}:
        need(type(binding[key]) is str and SHA.fullmatch(binding[key]), "UNBOUND_OVERLAY_DIGEST")
    instant(binding["holder_lease_expires_at"])
    core, relay = binding["core"], binding["relay"]
    need(type(core) is dict and set(core) == CORE_KEYS, "EXACT_CORE_HOLD_BINDING_REQUIRED")
    need(type(relay) is dict and set(relay) == RELAY_KEYS, "EXACT_RELAY_HOLD_BINDING_REQUIRED")
    for row, names in ((core, ("pid", "start_ticks", "epoch")),
                       (relay, ("pid", "start_ticks", "table_handle", "quiet_seconds_required"))):
        for name in names:
            need(type(row[name]) is int and 0 < row[name] < 2**63, "EXACT_POSITIVE_HOLD_IDENTITY")
    need(3 <= relay["quiet_seconds_required"] <= 92, "BOUNDED_RELAY_QUIET_INTERVAL")
    need(relay["table_name"] == TABLE, "REVIEWED_RELAY_TABLE_REQUIRED")
    for row, names in ((core, CORE_KEYS - {"pid", "start_ticks", "epoch"}),
                       (relay, RELAY_KEYS - {"pid", "start_ticks", "table_handle",
                                             "quiet_seconds_required", "table_name"})):
        need(all(type(row[name]) is str and SHA.fullmatch(row[name]) for name in names),
             "EXACT_HELD_PROOF_DIGEST_REQUIRED")
    return binding


def validate_observation(binding, observation, *, now):
    validate_binding(binding)
    need(type(observation) is dict and set(observation) == OBSERVATION_KEYS,
         "EXACT_CURRENT_HOLD_OBSERVATION_REQUIRED")
    need(equal(observation["core"], binding["core"]) and equal(observation["relay"], binding["relay"])
         and observation["holder_sha256"] == binding["holder_sha256"]
         and observation["native_preflight_sha256"] == binding["native_preflight_sha256"],
         "CURRENT_HOLD_IDENTITY_OR_NATIVE_PREFLIGHT_CHANGED")
    need(observation["core_state"] == "draining", "CURRENT_CORE_CLOSE_NOT_HELD")
    need(type(observation["relay_state"]) is dict and equal(observation["relay_state"], {
        "ingress_sealed": True, "keeper_held": True, "draining": False,
        "server_closed": False, "quiet_seconds": binding["relay"]["quiet_seconds_required"]}),
         "CURRENT_RELAY_SEAL_OR_IDLE_NOT_HELD")
    need(type(observation["counts"]) is dict and equal(observation["counts"], COUNTS),
         "CURRENT_WORK_NOT_QUIESCENT")
    captured = instant(observation["captured_at"])
    need(captured <= now + 2 and 0 <= now - captured <= 5, "HOLD_OBSERVATION_NOT_CURRENT")
    return observation


def make_receipt(binding, first, final, key, *, now):
    """Pure signing primitive; callers must actually capture twice under the hold."""
    validate_observation(binding, first, now=now)
    validate_observation(binding, final, now=now)
    need(equal({k: v for k, v in first.items() if k != "captured_at"},
               {k: v for k, v in final.items() if k != "captured_at"})
         and instant(first["captured_at"]) <= instant(final["captured_at"]),
         "HOLD_CHANGED_BETWEEN_OBSERVATIONS")
    need(type(key) is bytes and 32 <= len(key.strip()) <= 4096, "EXISTING_CONTROL_KEY_REQUIRED")
    observed = instant(final["captured_at"])
    need(instant(binding["holder_lease_expires_at"]) >= observed + OPERATION_SECONDS + HOLDER_RESERVE_SECONDS,
         "CONTINUOUS_HOLDER_LEASE_TOO_SHORT")
    payload = {"schema_version": 1, "purpose": PURPOSE, "site": "erp.abit.ai",
               "definition_id": 950, "binding": binding, "observed_at": utc(observed),
               "admission_expires_at": utc(observed + ADMISSION_SECONDS),
               "operation_expires_at": utc(observed + OPERATION_SECONDS),
               "current_observation_sha256": sha(canonical(final)),
               "continuous_hold_required": True, "historical_outcomes_settled": False}
    envelope = {"payload": payload,
                "hmac_sha256": hmac.new(key.strip(), canonical(payload), hashlib.sha256).hexdigest()}
    raw = canonical(envelope)
    need(len(raw) <= MAX_ENVELOPE, "HOLD_RECEIPT_SIZE_BOUND")
    return base64.b64encode(raw).decode()


def verify(encoded, key, binding, *, now, admission=None):
    """Admit once within120s; same journaled admission permits mutations for600s.

    The caller creates the returned admission with O_EXCL in its private journal.
    Its body is separately capped at420s from that admission, also bounded by the
    signed600s window. The physical adapter also uses an unrenewed monotonic cap.
    It supplies that exact stored record on later calls, never manufactures or
    refreshes it. Read-only outcome observation does not require an unexpired
    mutation window. No new operation or service call is authorized by that fact.
    """
    validate_binding(binding)
    need(type(encoded) is str and 0 < len(encoded) <= MAX_ENVELOPE * 4 // 3 + 4
         and type(key) is bytes and 32 <= len(key.strip()) <= 4096,
         "BOUNDED_EXISTING_CONTROL_HMAC_REQUIRED")
    try:
        raw = base64.b64decode(encoded, validate=True)
        need(len(raw) <= MAX_ENVELOPE, "HOLD_RECEIPT_SIZE_BOUND")
        def invalid(value):
            raise Held("NONFINITE_HOLD_RECEIPT")
        envelope = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, TypeError) as error:
        if isinstance(error, Held):
            raise
        raise Held("INVALID_HOLD_RECEIPT_ENCODING") from None
    need(type(envelope) is dict and set(envelope) == {"payload", "hmac_sha256"}
         and type(envelope["payload"]) is dict and type(envelope["hmac_sha256"]) is str
         and SHA.fullmatch(envelope["hmac_sha256"]), "EXACT_HOLD_ENVELOPE_REQUIRED")
    payload = envelope["payload"]
    need(hmac.compare_digest(hmac.new(key.strip(), canonical(payload), hashlib.sha256).hexdigest(),
                             envelope["hmac_sha256"]), "INVALID_HOLD_SIGNATURE")
    need(set(payload) == {"schema_version", "purpose", "site", "definition_id", "binding",
                         "observed_at", "admission_expires_at", "operation_expires_at", "current_observation_sha256",
                         "continuous_hold_required", "historical_outcomes_settled"}
         and type(payload["schema_version"]) is int and payload["schema_version"] == 1
         and type(payload["definition_id"]) is int and payload["definition_id"] == 950
         and payload["purpose"] == PURPOSE and payload["site"] == "erp.abit.ai"
         and payload["continuous_hold_required"] is True and payload["historical_outcomes_settled"] is False
         and equal(payload["binding"], binding), "EXACT_NEW_OPERATION_HOLD_REQUIRED")
    need(type(payload["current_observation_sha256"]) is str
         and SHA.fullmatch(payload["current_observation_sha256"]), "CURRENT_OBSERVATION_DIGEST_REQUIRED")
    observed = instant(payload["observed_at"])
    admission_expires = instant(payload["admission_expires_at"])
    operation_expires = instant(payload["operation_expires_at"])
    need(admission_expires - observed == ADMISSION_SECONDS
         and operation_expires - observed == OPERATION_SECONDS
         and instant(binding["holder_lease_expires_at"]) >= operation_expires + HOLDER_RESERVE_SECONDS
         and observed <= now < operation_expires, "HOLD_OPERATION_WINDOW_EXPIRED_OR_FUTURE")
    stable = {"receipt_sha256": sha(raw), "operation_id": binding["operation_id"],
              "admission_expires_at": payload["admission_expires_at"],
              "operation_expires_at": payload["operation_expires_at"]}
    if admission is None:
        need(now < admission_expires, "HOLD_INITIAL_ADMISSION_EXPIRED")
        admitted = {**stable, "admitted_at": utc(now),
                    "body_deadline_at": utc(min(now + BODY_SECONDS, operation_expires))}
    else:
        need(type(admission) is dict and set(admission) == set(stable) | {"admitted_at", "body_deadline_at"}
             and equal({key: admission[key] for key in stable}, stable),
             "EXACT_ORIGINAL_JOURNALED_ADMISSION_REQUIRED")
        admitted_time = instant(admission["admitted_at"])
        need(observed <= admitted_time < admission_expires and admitted_time <= now,
             "ORIGINAL_ADMISSION_TIME_INVALID")
        need(instant(admission["body_deadline_at"]) == min(admitted_time + BODY_SECONDS, operation_expires)
             and now < instant(admission["body_deadline_at"]), "NATIVE_BODY_DEADLINE_EXPIRED_OR_CHANGED")
        admitted = admission
    return {"admission": admitted, "payload_sha256": sha(canonical(payload)),
            "core_epoch": binding["core"]["epoch"], "relay_table_handle": binding["relay"]["table_handle"],
            "continuous_root_hold_required": True, "new_authority_or_business_admission": False}

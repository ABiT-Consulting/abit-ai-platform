"""Core's durable once-only admission intent, after real ERP consumption.

This module never creates ERP approval, native authority or capacity evidence.
An unacknowledged admission is read through its original attestation, not sent
again. A later launch still needs the existing independent one-use launch grant.
"""
from __future__ import annotations
import json
import time
from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_native_contract import canonical, digest, require
from open_webui.utils.agentic_native_admission_contract import (
    MODE, validate_attestation, validate_observation, validate_admission_grant)


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_native_admission(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,
        action_id TEXT NOT NULL UNIQUE,consumption_receipt_id TEXT NOT NULL UNIQUE,
        attestation_sha256 TEXT NOT NULL UNIQUE,attestation_json TEXT NOT NULL,
        phase TEXT NOT NULL,grant_issued_at INTEGER,receipt_json TEXT,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS governance_native_admission_immutable
        BEFORE UPDATE OF invocation_id,run_id,action_id,consumption_receipt_id,
        attestation_sha256,attestation_json,created_at ON governance_native_admission
        BEGIN SELECT RAISE(ABORT,'Native admission is immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS governance_native_admission_no_delete
        BEFORE DELETE ON governance_native_admission
        BEGIN SELECT RAISE(ABORT,'Native admission originals must be retained'); END""")


def decode(row):
    if row is None:
        return None
    result = dict(row)
    result["attestation"] = json.loads(result.pop("attestation_json"))
    result["receipt"] = json.loads(result.pop("receipt_json")) if result["receipt_json"] else None
    validate_attestation(result["attestation"], 0, current=False)
    value = result["attestation"]
    require(result["attestation_sha256"] == digest(value) and
            result["invocation_id"] == value["scope"]["invocation_id"] and
            result["run_id"] == value["scope"]["run_id"] and
            result["action_id"] == value["action"]["action_id"] and
            result["consumption_receipt_id"] == value["consumption"]["receipt_id"],
            "NATIVE_ADMISSION_RECORD_CHANGED")
    if result["receipt"] is not None:
        validate_observation(result["receipt"], value)
    return result


def original(invocation, db_path=None):
    with ledger.connect(db_path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_native_admission'").fetchone():
            return None
        return decode(db.execute("SELECT * FROM governance_native_admission WHERE invocation_id=?",
                                 (invocation,)).fetchone())


def current_proof(db, row, packet, approval, subject, now, db_path):
    from open_webui.utils import agentic_native_verification as v
    # All SQLite ownership/action/start checks share the enclosing transaction.
    # Maintenance is checked against the actual sibling database, never a copy.
    current = v._owned(db, row, packet, now)
    action, consumed = v.consumed_approval(db, current, packet)
    saved = db.execute("SELECT action_json FROM governance_action WHERE action_id=? AND invocation_id=?",
                       (action["action_id"], current["invocation_id"])).fetchone()
    action = json.loads(saved["action_json"])
    entry = db.execute("SELECT receipt_json,lease_token_sha256 FROM governance_start WHERE invocation_id=?",
                       (current["invocation_id"],)).fetchone()
    require(entry is not None and entry["receipt_json"] and
            entry["lease_token_sha256"] == v.token_hash(current["lease_token"]),
            "NATIVE_ADMISSION_CONFIRMED_START_REQUIRED")
    start = json.loads(entry["receipt_json"])
    return validate_attestation({
        "schema_version": 2, "kind": "abit_native_admission",
        "scope": v.scope_for(current, packet), "subject_sha256": subject,
        "action": action, "approval": approval, "consumption": consumed,
        "lease_token_sha256": v.token_hash(current["lease_token"]),
        "lease_expires_at": current["lease_expires_at"],
        "start_receipt_sha256": digest(start),
        "erp_baseline_sha256": digest(v._baseline(db, current, packet, start)),
        "maintenance_admission": v.maintenance_proof(current["invocation_id"], db_path),
        "prepared_at": now,
    }, now)


def begin(row, packet, approval, subject, db_path=None):
    now = int(time.time())
    with ledger._transaction(db_path) as db:
        schema(db)
        prior = decode(db.execute("SELECT * FROM governance_native_admission WHERE invocation_id=?",
                                  (row["invocation_id"],)).fetchone())
        require(prior is None, "NATIVE_ADMISSION_ORIGINAL_OBSERVATION_ONLY")
        attestation = current_proof(db, row, packet, approval, subject, now, db_path)
        db.execute("""INSERT INTO governance_native_admission
            (invocation_id,run_id,action_id,consumption_receipt_id,attestation_sha256,
             attestation_json,phase,created_at,updated_at)
            VALUES(?,?,?,?,?,?,'admission_unknown',?,?)""",
            (row["invocation_id"], attestation["scope"]["run_id"], attestation["action"]["action_id"],
             attestation["consumption"]["receipt_id"], digest(attestation), canonical(attestation), now, now))
        ledger._event(db, row["erp_project"], row["invocation_id"], "native_admission_intent",
                      "native-verification", {"attestation_sha256": digest(attestation),
                                               "automatic_replay_permitted": False})
    return original(row["invocation_id"], db_path)


def grant(row, packet, approval, payload, db_path=None):
    now = int(time.time())
    with ledger._transaction(db_path) as db:
        schema(db)
        saved = decode(db.execute("SELECT * FROM governance_native_admission WHERE invocation_id=?",
                                  (row["invocation_id"],)).fetchone())
        require(saved is not None and saved["phase"] == "admission_unknown" and
                saved["grant_issued_at"] is None and payload == {"attestation": saved["attestation"]},
                "NATIVE_ADMISSION_GRANT_ALREADY_USED_OR_CHANGED")
        original_value = saved["attestation"]
        live = current_proof(db, row, packet, approval, original_value["subject_sha256"], now, db_path)
        # A renewed lease may not extend this already-frozen attempt's window.
        require(live["lease_expires_at"] >= original_value["lease_expires_at"],
                "NATIVE_ADMISSION_LEASE_SHORTENED")
        live["prepared_at"] = original_value["prepared_at"]
        live["lease_expires_at"] = original_value["lease_expires_at"]
        require(live == original_value, "NATIVE_ADMISSION_CURRENT_PROOF_CHANGED")
        answer = {
            "authorized": True, "mode": "admit", "request_payload_sha256": digest(payload),
            "attestation_sha256": digest(original_value),
            "maintenance_admission": original_value["maintenance_admission"],
            "issued_at": now, "expires_at": min(now + 15, original_value["lease_expires_at"],
                                               approval["expires_at"]),
        }
        validate_admission_grant(answer, payload, now)
        db.execute("UPDATE governance_native_admission SET grant_issued_at=?,updated_at=? WHERE invocation_id=?",
                   (now, now, row["invocation_id"]))
        ledger._event(db, row["erp_project"], row["invocation_id"], "native_admission_grant",
                      "native-verification", {"attestation_sha256": digest(original_value),
                                               "expires_at": answer["expires_at"]})
    return answer


def accept(invocation, receipt, db_path=None):
    with ledger._transaction(db_path) as db:
        schema(db)
        saved = decode(db.execute("SELECT * FROM governance_native_admission WHERE invocation_id=?",
                                  (invocation,)).fetchone())
        require(saved is not None, "NATIVE_ADMISSION_INTENT_MISSING")
        validate_observation(receipt, saved["attestation"])
        if saved["receipt"] is not None and saved["receipt"]["state"] == "admitted":
            require(receipt == saved["receipt"], "NATIVE_ADMISSION_TERMINAL_CHANGED")
        db.execute("UPDATE governance_native_admission SET phase=?,receipt_json=?,updated_at=? WHERE invocation_id=?",
                   (receipt["state"], canonical(receipt), int(time.time()), invocation))
    return receipt


class NativeAdmissionClient:
    """Factory for an exact native-only BrokerClient subclass, with no new IT mode."""
    @staticmethod
    def create(socket_path, key_file, authorize):
        from copy import deepcopy
        import asyncio
        import secrets
        from open_webui.utils import agentic_it_broker as wire

        class Client(wire.BrokerClient):
            async def request(self, operation, payload):
                if operation not in {"admit", "observe_admission"}:
                    return await super().request(operation, payload)
                request = {"schema_version": 1, "operation": operation, "payload": payload,
                           "requested_at": int(time.time()), "nonce": secrets.token_hex(24)}
                require(len(canonical(request).encode()) <= wire.MAX_FRAME - 256,
                        "NATIVE_ADMISSION_FRAME_TOO_LARGE")
                reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.path), 5)
                try:
                    require(wire._peer(writer)[1] == self.server_uid, "NATIVE_ADMISSION_ROOT_PEER_CHANGED")
                    await wire._send(writer, request, self.key)
                    challenge = await wire._read(reader, self.key)
                    require(set(challenge) == {"challenge", "request_sha256", "expires_at"} and
                            challenge["request_sha256"] == digest(request) and
                            type(challenge["expires_at"]) is int and
                            int(time.time()) < challenge["expires_at"] <= int(time.time()) + 30,
                            "NATIVE_ADMISSION_CHALLENGE_INVALID")
                    answer = await wire.call(self.authorize, operation, deepcopy(payload))
                    require(type(answer) is dict and answer.get("authorized") is True,
                            "NATIVE_ADMISSION_NOT_AUTHORIZED")
                    await wire._send(writer, {**challenge, "grant": answer}, self.key)
                    response = await wire._read(reader, self.key)
                    require(response.get("request_sha256") == digest(request) and response.get("ok") is True,
                            "NATIVE_ADMISSION_ROOT_DENIED")
                    return response["result"]
                finally:
                    writer.close()
                    await writer.wait_closed()
        return Client(socket_path, key_file, authorize=authorize)

"""Core-owned one-use run/lease binding for fixed native verification.

Only a peer-authenticated root broker can supply gates or completion receipts.
The ledger never stores raw lease secrets in this lane. An uncertain launch is
observed through its original binding; it is never dispatched a second time.
"""
from __future__ import annotations
import hashlib
import json
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import packet_digest, stable_run_id, same_business_context
from open_webui.utils.agentic_native_contract import (
    OPERATION, PROFILE_ID, canonical, digest, require, validate_scope, validate_gate,
    validate_binding, validate_receipt,
)

MANAGED = {"source_modified", "packet_id", "content_sha256", "implementation_user", "implementation_run_id"}


def token_hash(token):
    require(type(token) is str and 0 < len(token) <= 256, "NATIVE_LEASE_SECRET_MISSING")
    return hashlib.sha256(token.encode()).hexdigest()


def selected_packet(packet):
    action = packet.get("tool_action")
    return isinstance(action, dict) and action.get("operation") == OPERATION


def scope_for(row, packet):
    action = packet.get("tool_action")
    require(type(action) is dict and set(action) == {"request_key", "operation", "risk", "parameters"} and
            action["operation"] == OPERATION and action["risk"] == "privileged" and
            action["parameters"] == {"profile_id": PROFILE_ID}, "NATIVE_ACTION_EXACT_PROFILE_REQUIRED")
    require(row["target_role"] == "it_manager" and packet["binding"]["role"] == "it_manager" and
            packet["binding"]["agent_id"] == row["agent_id"] and
            packet["binding"]["project_id"] == row["erp_project"] and
            packet["project_id"] == row["erp_project"] and packet["task_id"] == row["erp_task"] and
            packet["task_revision"] == row["task_revision"] and
            row["packet_hash"] == ledger.work_packet_digest(packet) and OPERATION in packet.get("tool_scopes", []),
            "NATIVE_PACKET_IDENTITY_CHANGED")
    return validate_scope({
        "invocation_id": row["invocation_id"],
        "run_id": row.get("automation_run_id") or stable_run_id(row["invocation_id"]),
        "project_id": row["erp_project"], "task_id": row["erp_task"],
        "task_revision": row["task_revision"], "project_revision": packet["project_revision"],
        "agent_id": row["agent_id"], "erp_user": packet["binding"]["erp_user"],
        "role": row["target_role"], "packet_sha256": row["packet_hash"],
        "context_sha256": row["context_hash"], "srs_revision": packet["srs"]["revision"],
        "srs_sha256": packet["srs"]["sha256"], "lease_fence": row["lease_fence"],
        "repository": packet["repository"], "environment": packet["environment"],
        "profile_id": PROFILE_ID,
    })


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_native_verification(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,nonce TEXT NOT NULL UNIQUE,
        binding_sha256 TEXT NOT NULL UNIQUE,binding_json TEXT NOT NULL,gate_json TEXT NOT NULL,
        phase TEXT NOT NULL,grant_issued_at INTEGER,receipt_json TEXT,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS governance_native_binding_immutable
        BEFORE UPDATE OF invocation_id,run_id,nonce,binding_sha256,binding_json,gate_json,created_at
        ON governance_native_verification BEGIN
        SELECT RAISE(ABORT,'Native run/nonce binding is immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS governance_native_no_delete
        BEFORE DELETE ON governance_native_verification BEGIN
        SELECT RAISE(ABORT,'Native original attempts must be retained'); END""")


def original(invocation, db_path=None):
    with ledger.connect(db_path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_native_verification'").fetchone():
            return None
        row = db.execute("SELECT * FROM governance_native_verification WHERE invocation_id=?", (invocation,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["binding"] = json.loads(result.pop("binding_json"))
    result["gate"] = json.loads(result.pop("gate_json"))
    result["receipt"] = json.loads(result.pop("receipt_json")) if result["receipt_json"] else None
    validate_gate(result["gate"], result["binding"]["scope"], int(time.time()), admission=False)
    validate_binding(result["binding"], result["gate"])
    require(result["binding_sha256"] == digest(result["binding"]) and
            result["nonce"] == result["binding"]["execution"]["nonce"] and
            result["run_id"] == result["binding"]["scope"]["run_id"], "NATIVE_ORIGINAL_CHANGED")
    if result["receipt"] is not None:
        validate_receipt(result["receipt"], result["binding"])
    return result


def _owned(db, row, packet, now):
    current = ledger._owned(db, row["invocation_id"], row["automation_run_id"], row["lease_token"], {"running"})
    require(current is not None and type(current["lease_expires_at"]) is int and
            current["lease_expires_at"] > now, "NATIVE_CURRENT_LEASE_REQUIRED")
    current = dict(current)
    require(scope_for(current, packet) == scope_for(row, packet), "NATIVE_CURRENT_FENCE_CHANGED")
    control = db.execute("SELECT state FROM governance_project_control WHERE erp_project=?",
                         (row["erp_project"],)).fetchone()
    require(control is not None and control[0] == "enabled", "NATIVE_PROJECT_HELD")
    ledger._task_gate(db, row["erp_project"], row["erp_task"])
    actor = db.execute("SELECT erp_user,role FROM governance_agent WHERE agent_id=? AND erp_project=?",
                       (row["agent_id"], row["erp_project"])).fetchone()
    require(actor is not None and tuple(actor) == (packet["binding"]["erp_user"], "it_manager"),
            "NATIVE_REGISTERED_ACTOR_CHANGED")
    return current


def consumed_approval(db, row, packet):
    from open_webui.utils.agentic_it_execution import make_action
    from open_webui.utils.agentic_contracts.validator import action_fingerprint
    action = make_action(row, packet)
    require(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action'").fetchone(),
            "NATIVE_ERP_APPROVAL_INTENT_MISSING")
    saved = db.execute("SELECT * FROM governance_action WHERE action_id=? AND invocation_id=?",
                       (action["action_id"], row["invocation_id"])).fetchone()
    require(saved is not None and saved["state"] == "uncertain" and saved["approval_json"],
            "NATIVE_CONSUMED_ERP_APPROVAL_REQUIRED")
    original_action = json.loads(saved["action_json"])
    require(action_fingerprint(original_action) == action_fingerprint(action),
            "NATIVE_APPROVED_ACTION_CHANGED")
    consumed = json.loads(saved["approval_json"])
    require(consumed.get("consumed") is True and consumed.get("action_id") == action["action_id"] and
            consumed.get("action_fingerprint") == action_fingerprint(action) and
            isinstance(consumed.get("approval_id"), str) and consumed["approval_id"] and
            isinstance(consumed.get("receipt_id"), str) and consumed["receipt_id"],
            "NATIVE_CONSUMED_ERP_RECEIPT_INVALID")
    return action, consumed


def _baseline(db, row, packet, start):
    """Only our exact published approval checkpoint may advance the start CAS."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action_wait'").fetchone():
        return start
    pending = db.execute("SELECT * FROM governance_action_wait WHERE invocation_id=?",
                         (row["invocation_id"],)).fetchone()
    if pending is None:
        return start
    require(pending["checkpoint_phase"] == "published" and pending["checkpoint_json"] and
            pending["checkpoint_receipt_json"], "NATIVE_APPROVAL_CHECKPOINT_UNCONFIRMED")
    event, receipt = json.loads(pending["checkpoint_json"]), json.loads(pending["checkpoint_receipt_json"])
    require(event.get("type") == "run.checkpoint" and
            event.get("invocation_id") == row["invocation_id"] and
            event.get("run_id") == row["automation_run_id"] == receipt.get("run_id") and
            event.get("project_id") == row["erp_project"] and
            event.get("task_id") == row["erp_task"] == receipt.get("task_id") and
            event.get("task_revision") == row["task_revision"] and
            event.get("work_packet_digest") == packet_digest(packet) and
            event.get("expected_modified") == start["modified"] and
            event.get("event_id") == receipt.get("event_id") and
            isinstance(receipt.get("modified"), str) and receipt["modified"],
            "NATIVE_APPROVAL_CHECKPOINT_CHANGED")
    from open_webui.utils.agentic_it_execution import make_action
    from open_webui.utils.agentic_contracts.validator import action_fingerprint
    require(action_fingerprint(json.loads(pending["action_json"])) ==
            action_fingerprint(make_action(row, packet)), "NATIVE_APPROVAL_CHECKPOINT_ACTION_CHANGED")
    return receipt


def admission_receipt(row, packet, db_path=None):
    from open_webui.utils.agentic_start_recovery import original_start_receipt
    start = original_start_receipt(row, db_path)
    with ledger.connect(db_path) as db:
        return _baseline(db, row, packet, start)


def bind_original(row, packet, gate, db_path=None):
    from open_webui.utils.agentic_start_recovery import original_start_receipt
    now = int(time.time())
    scope = scope_for(row, packet)
    validate_gate(gate, scope, now)
    start = original_start_receipt(row, db_path)
    with ledger.connect(db_path) as db:
        action, consumed = consumed_approval(db, row, packet)
        baseline = _baseline(db, row, packet, start)
    binding = {"schema_version": 1, "scope": scope, "execution": gate["execution"],
               "gate_sha256": digest(gate), "lease_token_sha256": token_hash(row["lease_token"]),
               "start_receipt_sha256": digest(start), "erp_baseline_sha256": digest(baseline),
               "action_id": action["action_id"],
               "native_approval_sha256": digest(consumed), "created_at": now}
    validate_binding(binding, gate)
    with ledger._transaction(db_path) as db:
        _schema(db)
        _owned(db, row, packet, now)
        current_action, current_consumed = consumed_approval(db, row, packet)
        require(current_action["action_id"] == binding["action_id"] and
                digest(current_consumed) == binding["native_approval_sha256"],
                "NATIVE_ERP_CONSUMPTION_CHANGED")
        require(digest(_baseline(db, row, packet, start)) == binding["erp_baseline_sha256"],
                "NATIVE_ERP_BASELINE_CHANGED")
        prior = db.execute("SELECT binding_json,gate_json FROM governance_native_verification WHERE invocation_id=?",
                           (row["invocation_id"],)).fetchone()
        if prior:
            saved = json.loads(prior["binding_json"])
            # Re-observation keeps the original timestamp and byte identity.
            binding["created_at"] = saved["created_at"]
            require(canonical(binding) == prior["binding_json"] and canonical(gate) == prior["gate_json"],
                    "NATIVE_ORIGINAL_REBIND_DENIED")
        else:
            db.execute("""INSERT INTO governance_native_verification
                (invocation_id,run_id,nonce,binding_sha256,binding_json,gate_json,phase,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'bound',?,?)""",
                (row["invocation_id"], scope["run_id"], gate["execution"]["nonce"], digest(binding),
                 canonical(binding), canonical(gate), now, now))
            ledger._event(db, row["erp_project"], row["invocation_id"], "native_verification_bound",
                          "native-verification", {"binding_sha256": digest(binding), "gate_sha256": digest(gate)})
    return original(row["invocation_id"], db_path)


def begin_launch(row, packet, db_path=None):
    """The only launch transition. Even an unacknowledged connection is not retried."""
    now = int(time.time())
    with ledger._transaction(db_path) as db:
        _schema(db)
        _owned(db, row, packet, now)
        saved = db.execute("SELECT * FROM governance_native_verification WHERE invocation_id=?",
                           (row["invocation_id"],)).fetchone()
        require(saved is not None, "NATIVE_ORIGINAL_MISSING")
        binding, gate = json.loads(saved["binding_json"]), json.loads(saved["gate_json"])
        validate_gate(gate, scope_for(row, packet), now)
        require(binding["lease_token_sha256"] == token_hash(row["lease_token"]),
                "NATIVE_LEASE_REPLACED")
        if saved["phase"] != "bound":
            return False
        db.execute("UPDATE governance_native_verification SET phase='launch_unknown',updated_at=? WHERE invocation_id=?",
                   (now, row["invocation_id"]))
        ledger._event(db, row["erp_project"], row["invocation_id"], "native_launch_intent",
                      "native-verification", {"binding_sha256": saved["binding_sha256"],
                                              "automatic_replay_permitted": False})
    return True


def issue_launch_grant(row, packet, payload, db_path=None):
    """Fresh challenge response; a second challenge cannot mint another launch."""
    now = int(time.time())
    with ledger._transaction(db_path) as db:
        _schema(db)
        current = _owned(db, row, packet, now)
        saved = db.execute("SELECT * FROM governance_native_verification WHERE invocation_id=?",
                           (row["invocation_id"],)).fetchone()
        require(saved is not None and saved["phase"] == "launch_unknown" and saved["grant_issued_at"] is None,
                "NATIVE_LAUNCH_GRANT_ALREADY_USED_OR_ABSENT")
        binding, gate = json.loads(saved["binding_json"]), json.loads(saved["gate_json"])
        require(payload == {"binding": binding, "gate_sha256": digest(gate)} and
                binding["lease_token_sha256"] == token_hash(current["lease_token"]),
                "NATIVE_LAUNCH_PAYLOAD_CHANGED")
        validate_gate(gate, scope_for(current, packet), now)
        action, consumed = consumed_approval(db, current, packet)
        require(action["action_id"] == binding["action_id"] and
                digest(consumed) == binding["native_approval_sha256"], "NATIVE_CONSUMED_APPROVAL_CHANGED")
        entry = db.execute("SELECT receipt_json,lease_token_sha256 FROM governance_start WHERE invocation_id=?",
                           (row["invocation_id"],)).fetchone()
        require(entry is not None and entry["receipt_json"] and
                entry["lease_token_sha256"] == binding["lease_token_sha256"] and
                digest(json.loads(entry["receipt_json"])) == binding["start_receipt_sha256"],
                "NATIVE_CONFIRMED_START_CHANGED")
        require(digest(_baseline(db, current, packet, json.loads(entry["receipt_json"]))) ==
                binding["erp_baseline_sha256"], "NATIVE_ADMISSION_CHECKPOINT_CHANGED")
        db.execute("UPDATE governance_native_verification SET grant_issued_at=?,updated_at=? WHERE invocation_id=?",
                   (now, now, row["invocation_id"]))
        grant = {"authorized": True, "mode": "execute", "request_payload_sha256": digest(payload),
                 "binding_sha256": digest(binding), "issued_at": now,
                 "expires_at": min(now + 15, current["lease_expires_at"], gate["expires_at"])}
        ledger._event(db, row["erp_project"], row["invocation_id"], "native_launch_grant",
                      "native-verification", {"binding_sha256": digest(binding), "expires_at": grant["expires_at"]})
        return grant


def accept_receipt(invocation, receipt, db_path=None):
    saved = original(invocation, db_path)
    require(saved is not None and saved["phase"] != "bound", "NATIVE_NO_LAUNCH_INTENT")
    validate_receipt(receipt, saved["binding"])
    with ledger._transaction(db_path) as db:
        current = db.execute("SELECT * FROM governance_native_verification WHERE invocation_id=?", (invocation,)).fetchone()
        require(current is not None and current["binding_sha256"] == saved["binding_sha256"],
                "NATIVE_RECEIPT_BINDING_CHANGED")
        prior = json.loads(current["receipt_json"]) if current["receipt_json"] else None
        if prior and prior["state"] in {"completed", "failed_retained"}:
            require(prior == receipt, "NATIVE_TERMINAL_RECEIPT_IMMUTABLE")
            return prior
        db.execute("UPDATE governance_native_verification SET phase=?,receipt_json=?,updated_at=? WHERE invocation_id=?",
                   (receipt["state"], canonical(receipt), int(time.time()), invocation))
        ledger._event(db, saved["binding"]["scope"]["project_id"], invocation, "native_receipt_observed",
                      "native-verification", {"state": receipt["state"], "receipt_sha256": digest(receipt)})
    return receipt


def public_status(invocation, db_path=None):
    saved = original(invocation, db_path)
    if saved is None:
        return {"invocation_id": invocation, "available": False, "phase": None,
                "binding_sha256": None, "gate_sha256": None, "receipt": None,
                "automatic_replay_permitted": False, "independent_qa_approved": False}
    return {"invocation_id": invocation, "available": True, "phase": saved["phase"],
            "binding_sha256": saved["binding_sha256"], "gate_sha256": saved["binding"]["gate_sha256"],
            "receipt": saved["receipt"], "automatic_replay_permitted": False,
            "independent_qa_approved": False}


class NativeRuntime:
    """Internal typed runtime; JSON configuration alone cannot grant root authority."""
    def __init__(self, client, *, project_gate, config, db_path=None):
        from open_webui.utils.agentic_it_broker import BrokerClient
        require(isinstance(client, BrokerClient), "NATIVE_PEER_AUTHENTICATED_CLIENT_REQUIRED")
        self.client, self.project_gate, self.config, self.db_path = client, project_gate, config, db_path
        self.row = self.packet = self.bridge = self.before_submit = None
        self.prepare_claimed = False

    async def _fresh(self, *, claimed):
        from open_webui.utils.agentic_bridge import ERPBridge
        own = self.bridge is None
        bridge = self.bridge or ERPBridge(self.config)
        try:
            _, gate = await self.project_gate(self.row["erp_project"], self.config)
            require(gate.get("enabled") is True and gate.get("pm_task_automation_enabled") is True and
                    gate.get("assigned_user") == "ai-projects@abitcons.com" and
                    gate.get("project_status") in {"Open", "Active", "In Progress"},
                    "NATIVE_CURRENT_PROJECT_GATE_DENIED")
            fresh = await bridge.packet(self.row["erp_project"], self.row["erp_task"],
                                        "it_manager", self.row["agent_id"])
            require(same_business_context(self.packet, fresh, MANAGED if claimed else ()),
                    "NATIVE_CURRENT_PACKET_CHANGED")
            current = ledger.find(self.row["invocation_id"], self.db_path, include_private=True)
            require(current is not None and scope_for(current, self.packet) == scope_for(self.row, self.packet),
                    "NATIVE_CURRENT_SCOPE_CHANGED")
            if claimed:
                from open_webui.utils.agentic_start_recovery import original_start_receipt
                receipt = admission_receipt(current, self.packet, self.db_path)
                require(fresh["source_modified"] == receipt["modified"], "NATIVE_ERP_START_CHANGED")
            else:
                require(current["state"] == "queued" and current["submitted_at"] is None and
                        current["automation_run_id"] is None and current["dispatch_attempts"] == 0,
                        "NATIVE_FRESH_QUEUED_TASK_REQUIRED")
            return current
        finally:
            if own:
                await bridge.close()

    async def authorize(self, operation, payload):
        if operation == "prepare":
            await self._fresh(claimed=self.prepare_claimed)
            require(payload == {"scope": scope_for(self.row, self.packet)}, "NATIVE_PREPARE_SCOPE_CHANGED")
            return {"authorized": True, "mode": operation, "request_payload_sha256": digest(payload)}
        saved = original(self.row["invocation_id"], self.db_path)
        require(saved is not None, "NATIVE_ORIGINAL_MISSING")
        if operation == "execute":
            require(callable(self.before_submit), "NATIVE_CURRENT_AUTHORITY_CALLBACK_MISSING")
            await self.before_submit()
            current = await self._fresh(claimed=True)
            from open_webui.utils.agentic_tool_gateway import _approval
            with ledger.connect(self.db_path) as db:
                action, consumed = consumed_approval(db, current, self.packet)
            approval, _ = await _approval(self.bridge, current, action)
            now = int(time.time())
            require(isinstance(approval, dict) and approval.get("status") == "approved" and
                    approval.get("approval_id") == consumed["approval_id"] and
                    approval.get("action_fingerprint") == consumed["action_fingerprint"] and
                    approval.get("used_by_action_id") == action["action_id"] and
                    type(approval.get("approved_at")) is int and type(approval.get("expires_at")) is int and
                    approval["approved_at"] <= now < approval["expires_at"],
                    "NATIVE_CURRENT_CONSUMED_APPROVAL_REQUIRED")
            return issue_launch_grant(current, self.packet, payload, self.db_path)
        require(operation == "observe" and payload == {"binding": saved["binding"],
                "gate_sha256": saved["binding"]["gate_sha256"]}, "NATIVE_ORIGINAL_OBSERVATION_CHANGED")
        # Read-only original observation may finish after expiry/Stop. No grant
        # to dispatch, publish or hand off is created by this response.
        return {"authorized": True, "mode": operation, "request_payload_sha256": digest(payload),
                "binding_sha256": saved["binding_sha256"]}

    async def prepare_gate(self, row, packet, *, bridge=None, claimed=False):
        self.row, self.packet, self.bridge, self.prepare_claimed = row, packet, bridge, claimed
        response = await self.client.request("prepare", {"scope": scope_for(row, packet)})
        require(type(response) is dict and set(response) == {"gate", "gate_sha256"} and
                response["gate_sha256"] == digest(response["gate"]), "NATIVE_ROOT_GATE_REPLY_INVALID")
        validate_gate(response["gate"], scope_for(row, packet), int(time.time()))
        return response["gate"]

    async def submit(self, row, packet, gate, bridge, before_submit):
        self.row, self.packet, self.bridge, self.before_submit = row, packet, bridge, before_submit
        await before_submit()
        saved = bind_original(row, packet, gate, self.db_path)
        fresh = begin_launch(row, packet, self.db_path)
        payload = {"binding": saved["binding"], "gate_sha256": saved["binding"]["gate_sha256"]}
        receipt = await self.client.request("execute" if fresh else "observe", payload)
        return accept_receipt(row["invocation_id"], receipt, self.db_path)

    async def observe(self, row, packet, bridge):
        self.row, self.packet, self.bridge = row, packet, bridge
        saved = original(row["invocation_id"], self.db_path)
        require(saved is not None and saved["phase"] != "bound", "NATIVE_ORIGINAL_NOT_DISPATCHED")
        receipt = await self.client.request("observe", {"binding": saved["binding"],
                                                       "gate_sha256": saved["binding"]["gate_sha256"]})
        return accept_receipt(row["invocation_id"], receipt, self.db_path)


def configured_runtime(config, *, project, project_gate, db_path=None):
    from open_webui.utils.agentic_it_broker import BrokerClient
    selected = config.get("agentic_native_verification") or {}
    if not selected or selected.get("enabled") is not True:
        return None
    require(type(selected) is dict and set(selected) == {"enabled", "socket_path", "key_file", "projects"} and
            type(selected["projects"]) is list and 0 < len(selected["projects"]) <= 20 and
            len(set(selected["projects"])) == len(selected["projects"]) and
            project in selected["projects"], "NATIVE_PROJECT_REGISTRATION_REQUIRED")
    holder = {}
    async def authorize(operation, payload):
        return await holder["runtime"].authorize(operation, payload)
    client = BrokerClient(selected["socket_path"], selected["key_file"], authorize=authorize)
    result = NativeRuntime(client, project_gate=project_gate, config=config, db_path=db_path)
    holder["runtime"] = result
    return result

"""Offline actual-ledger/native-adapter boundary tests. No provider or root action."""
from __future__ import annotations
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
import types
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T


class NativeBridge:
    def __init__(self, fixture):
        self.f = fixture
        self.erp = self
        self.secret = "isolated-fixture"
        self.approval = None
        self.calls = []
        self.started = False
        self.modified = "modified-started"
        self.drafts = {}
        self.receipts = {}

    async def packet(self, project, task, role, agent, *args):
        packet = self.f.packet(task, role, project)
        if self.started:
            packet["source_modified"] = self.modified
        packet["content_sha256"] = self.f.bridge_module.packet_digest(packet)
        return packet

    async def request(self, method, path, **kwargs):
        operation = path.rsplit(".", 1)[-1]
        self.calls.append(operation)
        args = kwargs.get("json", {})
        if operation == "request_action_approval":
            self.drafts[args["approval_id"]] = deepcopy(args)
            return {"message": {"approval_id": args["approval_id"]}}
        if operation == "get_result_receipt":
            event = kwargs["params"]["event_id"]
            return {"message": {"exists": event in self.receipts, "receipt": self.receipts.get(event)}}
        if operation == "get_action_approval":
            return {"message": deepcopy(self.approval)}
        if operation == "consume_action_approval":
            self.approval["used_by_action_id"] = args["action"]["action_id"]
            return {"message": {"consumed": True, "approval_id": self.approval["approval_id"],
                "action_id": args["action"]["action_id"], "action_fingerprint": args["action_fingerprint"],
                "receipt_id": "native-consumption-fixture"}}
        raise AssertionError("Unexpected external operation: " + operation)

    async def publish(self, event):
        self.modified = "modified-checkpoint"
        receipt = {key:event[key] for key in ("event_id","run_id","task_id")}
        receipt["modified"] = self.modified
        self.receipts[event["event_id"]] = receipt
        return receipt

    async def close(self):
        pass


class NativeCases(Fixture):
    def setUp(self):
        super().setUp()
        self.c = self.load("agentic_native_contract")
        self.gateway = self.load("agentic_tool_gateway")
        self.start = self.load("agentic_start_recovery")
        self.it = self.load("agentic_it_execution")
        self.wire = self.load("agentic_it_broker")
        self.n = self.load("agentic_native_verification")
        self.root = self.load("agentic_native_broker")
        self.adapter_module = self.load("agentic_native_execution")
        self.validator = __import__("open_webui.utils.agentic_contracts.validator", fromlist=["validator"])
        self.bridge = NativeBridge(self)
        self.config = {"agentic_tool_gateway": {"projects": {P: {
            "host_id": socket.gethostname(), "operations": ["verify_native_source"],
            "environment_class": "test", "roots": {}, "services": []}}}}
        self.artifacts = self.load("agentic_artifacts")
        self.artifact_path = Path(self.temp.name) / "artifacts"
        root_patch = patch.object(self.artifacts, "artifact_directory", return_value=self.artifact_path)
        root_patch.start(); self.addCleanup(root_patch.stop)
        self.operations = []
        self.native_gate = None
        self.wire_error = None
        self.before_grant = None
        self.root_launch_admitted = False
        outer = self
        class MemoryClient(self.wire.BrokerClient):
            def __init__(self):
                self.runtime = None
            async def request(self, operation, payload):
                outer.operations.append(operation)
                if outer.before_grant and operation == "execute":
                    outer.before_grant()
                grant = await self.runtime.authorize(operation, deepcopy(payload))
                outer.assertTrue(grant["authorized"])
                outer.assertEqual(grant["request_payload_sha256"], outer.c.digest(payload))
                if operation == "prepare":
                    return {"gate": deepcopy(outer.native_gate), "gate_sha256": outer.c.digest(outer.native_gate)}
                if operation == "execute":
                    # This runs only after the real Core final grant succeeds.
                    outer.root_launch_admitted = True
                    if outer.wire_error:
                        raise outer.wire_error
                if operation == "observe" and not outer.root_launch_admitted:
                    raise FileNotFoundError("Original root BINDING receipt absent")
                binding = payload["binding"]
                receipt = outer.root.empty_receipt(binding)
                receipt.update(state="completed", outcome_sha256="a" * 64,
                    worker_result_sha256="b" * 64, reservation_sha256="c" * 64,
                    cleanup_confirmed=True, operations_count=10)
                return receipt
        self.client = MemoryClient()
        self.runtime = self.n.NativeRuntime(self.client, project_gate=self.gate,
                                           config=self.config, db_path=self.path)
        self.client.runtime = self.runtime
        self.adapter = self.adapter_module.NativeVerificationAdapter(
            None, self.gate, self.config, self.path, self.runtime)

    def packet(self, task=T, role="it_manager", project=P):
        value = super().packet(task, role, project)
        value.update(repository="https://github.com/asma2rjoub-dotcom/bigrro-platform",
                     environment="native-verification", tool_scopes=["verify_native_source"],
                     tool_action={"request_key": "native-profile-fixture", "operation": "verify_native_source",
                                  "risk": "privileged", "parameters": {"profile_id": "bigrro-proj0202-fixed-v1"}})
        value["content_sha256"] = self.bridge_module.packet_digest(value)
        return value

    def gate_for(self, row, packet, value="1" * 32):
        now = int(time.time())
        return {"schema_version": 1, "kind": "abit_native_verification_gate",
                "scope": self.n.scope_for(row, packet),
                "execution": {"nonce": value, "authority_sha256": "2" * 64,
                    "execution_manifest_sha256": "3" * 64, "worker_head": self.c.WORKER_HEAD,
                    "worker_tree": self.c.WORKER_TREE, "plan_sha256": self.c.PLAN_SHA,
                    "executor_profile_sha256": self.c.PROFILE_SHA, "runtime_manifest_sha256": self.c.RUNTIME_SHA},
                "issued_at": now - 1, "expires_at": now + 600}

    def queued(self, ident="native-invocation-fixture"):
        return self.enqueue(ident, role="it_manager")

    def claimed(self, ident="native-invocation-fixture", *, approval=True):
        self.queued(ident)
        run = self.bridge_module.stable_run_id(ident)
        row = self.g.claim_invocation(ident, run, gate=self.gate_value, db_path=self.path)
        packet = json.loads(row["work_packet"])
        event = {"schema_version": 1, "event_id": "agov-start-" + self.bridge_module.digest(ident)[:48],
                 "type": "run.started", "project_id": P, "task_id": T,
                 "task_revision": packet["task_revision"], "run_id": run, "invocation_id": ident,
                 "role": "it_manager", "agent_id": row["agent_id"], "summary": "Fixture exact claim",
                 "expected_modified": packet["source_modified"],
                 "expected_project_revision": packet["project_revision"],
                 "work_packet_digest": self.bridge_module.packet_digest(packet), "artifacts": []}
        self.start.record_start_intent(row, event, self.path)
        receipt = {key: event[key] for key in ("event_id", "task_id", "run_id")}
        receipt.update(modified="modified-started", status="Working", durable=True, duplicate=False)
        self.start.record_start_receipt(ident, receipt, self.path)
        self.g.mark_submitted(ident, run, row["lease_token"], gate=self.gate_value, db_path=self.path)
        self.bridge.started = True
        row = self.g.find(ident, self.path, include_private=True)
        self.native_gate = self.gate_for(row, packet)
        if approval:
            action = self.it.make_action(row, packet)
            now = int(time.time())
            self.bridge.approval = {"schema_version": 1, "approval_id": "approval-native-fixture",
                "project_id": P, "task_id": T, "action_fingerprint": self.validator.action_fingerprint(action),
                "approved_by": "human@example.invalid", "approved_at": now - 1, "expires_at": now + 600,
                "status": "approved", "used_by_action_id": None}
        return row, packet

    async def submit(self, row, packet):
        return await self.adapter.submit(row, packet, row["automation_run_id"], None,
                                         self.bridge, before_submit=AsyncMock())

    def consumed(self, row, packet):
        action = self.it.make_action(row, packet)
        consumed = {"consumed": True, "approval_id": "approval-native-fixture",
                    "action_id": action["action_id"],
                    "action_fingerprint": self.validator.action_fingerprint(action), "receipt_id": "consumed-fixture"}
        with self.gateway._connect(self.path) as db:
            db.execute("INSERT INTO governance_action VALUES(?,?,?,?,?,'uncertain',?,NULL,?,?)",
                       (action["action_id"], row["invocation_id"], self.c.digest(action), self.c.canonical(action),
                        "{}", self.c.canonical(consumed), int(time.time()), int(time.time())))
        return consumed

    def bind(self):
        row, packet = self.claimed()
        self.consumed(row, packet)
        saved = self.n.bind_original(row, packet, self.native_gate, self.path)
        return row, packet, saved

    async def test_unconfigured_lane_holds_before_any_client_or_claim(self):
        row = self.queued()
        packet = self.packet()
        adapter = self.adapter_module.NativeVerificationAdapter(None, self.gate, self.config, self.path)
        with self.assertRaises(self.gateway.CapabilityUnavailable):
            await adapter.preflight(row, packet)
        self.assertEqual(self.operations, [])
        self.assertEqual(self.g.find(row["invocation_id"], self.path)["state"], "queued")

    async def test_preflight_reads_root_gate_without_claim_or_authority_consumption(self):
        row = self.queued(); packet = self.packet()
        self.native_gate = self.gate_for(row, packet)
        self.runtime.bridge = self.bridge
        with patch.object(self.n, "configured_runtime", return_value=self.runtime):
            # Existing bridge avoids any connector import in the offline fixture.
            with patch("open_webui.utils.agentic_bridge.ERPBridge", return_value=self.bridge):
                await self.adapter.preflight(row, packet)
        self.assertEqual(self.operations, ["prepare"])
        self.assertEqual(self.bridge.calls, [])
        self.assertIsNone(self.n.original(row["invocation_id"], self.path))

    def test_scope_rejects_model_nonce_command_wrong_role_and_ungranted_capability(self):
        row = self.queued()
        for mutate in (lambda p: p["tool_action"]["parameters"].update(nonce="1"*32),
                       lambda p: p["tool_action"]["parameters"].update(command="true"),
                       lambda p: p["binding"].update(role="developer"),
                       lambda p: p.update(tool_scopes=[])):
            packet = self.packet(); mutate(packet)
            with self.assertRaises(ValueError):
                self.n.scope_for(row, packet)

    def test_work_packet_schema_additive_operation_and_minimum_risk(self):
        packet = self.packet()
        self.assertEqual(self.validator.validate_work_packet(packet), [])
        packet["tool_action"]["risk"] = "routine"
        packet["content_sha256"] = self.bridge_module.packet_digest(packet)
        self.assertIn("Native verification requires its exact privileged fixed profile",
                      self.validator.validate_work_packet(packet))
        self.assertEqual(self.validator.MIN_RISK["restart_service"], "privileged")
        self.assertEqual(self.validator.MIN_RISK["verify_native_source"], "privileged")
        changes = self.load("agentic_it_changes")
        self.assertEqual(set(changes.OPERATIONS), {"install_official_package", "restart_service", "extend_capacity"})

    def test_gate_wrong_scope_expiry_and_published_worker_pin_reject(self):
        row = self.queued(); packet = self.packet(); gate = self.gate_for(row, packet)
        for mutate in (lambda g: g["scope"].update(lease_fence=99),
                       lambda g: g.update(expires_at=g["issued_at"]),
                       lambda g: g["execution"].update(worker_head="a"*40),
                       lambda g: g["execution"].update(nonce="0"*32)):
            broken = deepcopy(gate); mutate(broken)
            with self.assertRaises(ValueError):
                self.c.validate_gate(broken, gate["scope"], int(time.time()))

    def test_missing_consumed_erp_approval_prevents_binding(self):
        row, packet = self.claimed()
        with self.assertRaisesRegex(ValueError, "APPROVAL"):
            self.n.bind_original(row, packet, self.native_gate, self.path)
        self.assertIsNone(self.n.original(row["invocation_id"], self.path))

    def test_missing_start_receipt_prevents_binding(self):
        row, packet = self.claimed(); self.consumed(row, packet)
        with self.g._transaction(self.path) as db:
            db.execute("UPDATE governance_start SET receipt_json=NULL")
        with self.assertRaises(ValueError):
            self.n.bind_original(row, packet, self.native_gate, self.path)

    def test_exact_binding_has_no_raw_lease_secret_and_cannot_rebind_nonce(self):
        row, packet, saved = self.bind()
        self.assertNotIn(row["lease_token"], self.c.canonical(saved))
        self.assertEqual(saved["binding"]["scope"]["run_id"], row["automation_run_id"])
        self.assertEqual(saved["binding"]["lease_token_sha256"], self.n.token_hash(row["lease_token"]))
        again = self.n.bind_original(row, packet, self.native_gate, self.path)
        self.assertEqual(again["binding"], saved["binding"])
        changed = deepcopy(self.native_gate); changed["execution"]["nonce"] = "5"*32
        with self.assertRaisesRegex(ValueError, "REBIND"):
            self.n.bind_original(row, packet, changed, self.path)

    def test_sqlite_binding_and_delete_guards_preserve_original(self):
        row, packet, saved = self.bind()
        with self.g.connect(self.path) as db:
            for sql in ("UPDATE governance_native_verification SET nonce='other'",
                        "DELETE FROM governance_native_verification"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
        self.assertEqual(self.n.original(row["invocation_id"], self.path)["binding"], saved["binding"])

    async def test_actual_gateway_human_approval_consumes_once_before_native_binding(self):
        row, packet = self.claimed()
        record = await self.submit(row, packet)
        self.assertEqual(self.operations, ["prepare", "execute"])
        self.assertEqual(self.bridge.calls.count("consume_action_approval"), 1)
        result = json.loads(record["response_text"].removeprefix("AGOV RESULT: "))
        self.assertEqual(result["outcome"], "ready_for_review")
        self.assertTrue(all(t["status"] == "skipped" for t in result["test_results"]))
        self.assertEqual(self.validator.validate_result(result, packet), [])
        self.assertFalse(self.n.public_status(row["invocation_id"], self.path)["independent_qa_approved"])
        repeated = await self.submit(row, packet)
        self.assertEqual(repeated, record)
        self.assertEqual(self.operations.count("execute"), 1)
        self.assertEqual(self.bridge.calls.count("consume_action_approval"), 1)

    async def test_no_approval_or_self_expired_revoked_approval_never_launches(self):
        row, packet = self.claimed()
        original = deepcopy(self.bridge.approval)
        for change in (None, {"approved_by":"ai-operations@abitcons.com"},
                       {"expires_at":int(time.time())-1}, {"status":"revoked"}):
            self.bridge.approval = None if change is None else {**original, **change}
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
            self.assertEqual(self.operations, [])
            self.assertIsNone(self.n.original(row["invocation_id"], self.path))

    async def test_unknown_launch_ack_is_original_observation_never_second_execute(self):
        row, packet = self.claimed()
        self.wire_error = TimeoutError("simulated lost native ACK")
        with self.assertRaises(TimeoutError):
            await self.submit(row, packet)
        self.assertEqual(self.operations.count("execute"), 1)
        # Gateway's recovery hook may already have retained the original result.
        saved = self.n.original(row["invocation_id"], self.path)
        self.assertNotEqual(saved["phase"], "bound")
        self.wire_error = None
        await self.runtime.observe(row, packet, self.bridge)
        await self.submit(row, packet)
        self.assertEqual(self.operations.count("execute"), 1)
        self.assertEqual(self.bridge.calls.count("consume_action_approval"), 1)

    async def test_pause_at_final_challenge_denies_native_grant(self):
        row, packet = self.claimed()
        self.before_grant = lambda: self.controls.task_control(P, T, "pause", actor="human", db_path=self.path)
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        saved = self.n.original(row["invocation_id"], self.path)
        self.assertIsNone(saved["grant_issued_at"])

    async def test_lease_replaced_at_final_challenge_denies_native_grant(self):
        row, packet = self.claimed()
        def replace():
            with self.g._transaction(self.path) as db:
                db.execute("UPDATE governance_invocation SET lease_token='replaced'")
        self.before_grant = replace
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        self.assertIsNone(self.n.original(row["invocation_id"], self.path)["grant_issued_at"])

    async def test_task_fence_replaced_at_final_challenge_denies_native_grant(self):
        row, packet = self.claimed()
        def replace():
            with self.g._transaction(self.path) as db:
                db.execute("UPDATE governance_task_lease SET fence=fence+1")
        self.before_grant = replace
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        self.assertIsNone(self.n.original(row["invocation_id"], self.path)["grant_issued_at"])

    def test_expired_lease_and_stop_deny_launch_transition(self):
        row, packet, saved = self.bind()
        with self.g._transaction(self.path) as db:
            db.execute("UPDATE governance_invocation SET lease_expires_at=?", (int(time.time()),))
        with self.assertRaisesRegex(ValueError, "LEASE"):
            self.n.begin_launch(row, packet, self.path)
        self.assertEqual(self.n.original(row["invocation_id"], self.path)["phase"], "bound")

    def test_one_grant_only_and_approval_changed_denies(self):
        row, packet, saved = self.bind()
        self.assertTrue(self.n.begin_launch(row, packet, self.path))
        self.assertFalse(self.n.begin_launch(row, packet, self.path))
        payload = {"binding": saved["binding"], "gate_sha256": saved["binding"]["gate_sha256"]}
        grant = self.n.issue_launch_grant(row, packet, payload, self.path)
        self.assertGreater(grant["expires_at"], int(time.time()))
        with self.assertRaisesRegex(ValueError, "ALREADY_USED"):
            self.n.issue_launch_grant(row, packet, payload, self.path)

    def test_terminal_receipt_immutable_and_cross_nonce_denied(self):
        row, packet, saved = self.bind()
        self.n.begin_launch(row, packet, self.path)
        receipt = self.root.empty_receipt(saved["binding"])
        bad = deepcopy(receipt); bad["nonce"] = "f"*32
        with self.assertRaises(ValueError):
            self.n.accept_receipt(row["invocation_id"], bad, self.path)
        receipt.update(state="failed_retained", outcome_sha256="a"*64)
        self.n.accept_receipt(row["invocation_id"], receipt, self.path)
        changed = deepcopy(receipt); changed["outcome_sha256"] = "b"*64
        with self.assertRaisesRegex(ValueError, "IMMUTABLE"):
            self.n.accept_receipt(row["invocation_id"], changed, self.path)

    def test_public_receipt_rejects_content_or_unconfirmed_completion(self):
        row, packet, saved = self.bind()
        receipt = self.root.empty_receipt(saved["binding"])
        for update in ({"stdout":"private output"}, {"state":"completed"},
                       {"operations_count":True}, {"authority_issued":True}):
            with self.assertRaises(ValueError):
                self.c.validate_receipt({**receipt, **update}, saved["binding"])

    async def test_read_only_original_observation_after_expiry_and_stop_has_no_launch(self):
        row, packet = self.claimed()
        await self.submit(row, packet)
        self.controls.task_control(P, T, "stop", actor="human", db_path=self.path)
        current = self.g.find(row["invocation_id"], self.path, include_private=True)
        before = self.operations.count("execute")
        receipt = await self.runtime.observe(current, packet, self.bridge)
        self.assertEqual(receipt["state"], "completed")
        self.assertEqual(self.operations.count("execute"), before)

    def test_root_fixed_controller_argv_has_no_user_commands_or_numeric_root_property(self):
        value = "1"*32; state = self.root.ROOT/"attempts"/value
        argv = self.root.controller_argv(value, state)
        self.assertEqual(argv[-4:], [str(self.root.CONTROL/"controller.py"),"--execute","--nonce",value])
        self.assertIn("--system", argv)
        self.assertIn("--property=Restart=no", argv)
        self.assertFalse(any("User=0" in x or "Group=0" in x or x in {"sh","bash","-c"} for x in argv))
        with self.assertRaises(ValueError):
            self.root.controller_argv("1"*32+";true", state)

    def test_root_reader_rejects_unprivileged_or_symlink_sources(self):
        path = Path(self.temp.name)/"not-root.json";path.write_text("{}");path.chmod(0o400)
        with self.assertRaises(ValueError):
            self.root.root_document(path)
        link = Path(self.temp.name)/"link";link.symlink_to(path)
        with self.assertRaises((ValueError,OSError)):
            self.root.root_document(link)
        with self.assertRaises(ValueError):
            self.root.strict_json(b'{"x":1,"x":2}')
        with self.assertRaises(ValueError):
            self.root.strict_json(b'{"x":NaN}')

    def root_documents(self, binding, *, passed=True):
        value = binding["execution"]["nonce"]
        outcome = {"purpose":"PROJ0202_NATIVE_FIXED_WORKER","task":"TASK-2026-06457","nonce":value,
                   "worker_head":self.c.WORKER_HEAD,"status":"PASSED" if passed else "FAILED_RETAINED",
                   "failure_code":None,"cleanup":{"confirmed":True},
                   "authority_sha256":binding["execution"]["authority_sha256"],
                   "retry_authorized":False,"original_attempts_modified":False}
        bootstrap = {"purpose":"PROJ0202_NATIVE_FIXED_WORKER","nonce":value,"status":"PASSED",
            "process":{"exit_code":0},"worker_result_sha256":"a"*64,"descendant_cessation":{"confirmed":True},
            "output_capture":{"confirmed":True},"release_issued":True,"retry_authorized":False}
        outcome["outcome"] = bootstrap
        context = {"purpose":"PROJ0202_NATIVE_FIXED_WORKER","nonce":value,
            "manifest_sha256":binding["execution"]["execution_manifest_sha256"],
            "authority_sha256":binding["execution"]["authority_sha256"]}
        reservation = {"state":"CONSUMED","nonce":value,"authority_sha256":context["authority_sha256"],
            "head_sha":self.c.WORKER_HEAD,"tree_sha":self.c.WORKER_TREE,"plan_sha256":self.c.PLAN_SHA,
            "executor_profile_sha256":self.c.PROFILE_SHA,"runtime_manifest_sha256":self.c.RUNTIME_SHA}
        preexec = {**context,"reservation_sha256":self.c.digest(reservation)}
        documents = {"OUTCOME.json":outcome,"BOOTSTRAP_RESULT.json":bootstrap,"CONTEXT.json":context,
                     "PREEXEC.json":preexec,"RESERVATION.json":reservation}
        def read(path, *args, **kwargs):
            item = documents[Path(path).name]
            return deepcopy(item), self.c.digest(item)
        return documents, read

    def test_root_completion_requires_pinned_context_bootstrap_reservation_and_cleanup(self):
        row, packet, saved = self.bind()
        documents, read = self.root_documents(saved["binding"])
        with patch.object(self.root, "root_document", side_effect=read):
            receipt = self.root.observe_worker(saved["binding"])
            self.assertEqual(receipt["state"], "completed")
            self.assertEqual(receipt["operations_count"],10)
            documents["CONTEXT.json"]["manifest_sha256"]="f"*64
            with self.assertRaisesRegex(ValueError, "CONTEXT"):
                self.root.observe_worker(saved["binding"])

    def test_root_failed_or_absent_outcome_never_claims_native_success(self):
        row, packet, saved = self.bind()
        documents, read = self.root_documents(saved["binding"],passed=False)
        with patch.object(self.root, "root_document", side_effect=read):
            receipt = self.root.observe_worker(saved["binding"])
            self.assertEqual(receipt["state"],"failed_retained")
            self.assertEqual(receipt["operations_count"],0)
        with patch.object(self.root, "root_document", side_effect=FileNotFoundError()):
            self.assertEqual(self.root.observe_worker(saved["binding"])["state"],"uncertain")

    async def test_root_launch_intent_is_create_once_and_unknown_ack_not_repeated(self):
        row, packet, saved = self.bind()
        root = Path(self.temp.name)/"root-service";(root/"attempts").mkdir(parents=True)
        service = object.__new__(self.root.RootNativeService)
        def directory(path):
            self.assertTrue(Path(path).is_relative_to(root))
            return os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        def read(path,*args,**kwargs):
            raw=Path(path).read_bytes();return json.loads(raw),hashlib.sha256(raw).hexdigest()
        launch=AsyncMock(side_effect=TimeoutError("ack lost"))
        payload={"binding":saved["binding"],"gate_sha256":saved["binding"]["gate_sha256"]}
        grant={"binding_sha256":saved["binding_sha256"],"maintenance_admission":saved["binding"]["maintenance_admission"],"issued_at":int(time.time()),"expires_at":int(time.time())+10}
        with patch.object(self.root,"ROOT",root), patch.object(self.root,"root_directory",side_effect=directory), \
             patch.object(self.root,"load_gate",return_value=self.native_gate), \
             patch.object(self.root,"verify_inputs",return_value=saved["binding"]["execution"]), \
             patch.object(self.root,"root_document",side_effect=read), \
             patch.object(self.root,"launch_controller_once",launch), \
             patch.object(self.root,"observe_worker",return_value=self.root.empty_receipt(saved["binding"])):
            with self.assertRaises(TimeoutError):
                await service.launch(payload,grant)
            result=await service.launch(payload,grant)
            self.assertEqual(result["state"],"uncertain")
            self.assertEqual(launch.await_count,1)
            state=root/"attempts"/saved["nonce"]
            self.assertTrue((state/"LAUNCH_INTENT.json").is_file())
            self.assertFalse((state/"LAUNCH_ACK.json").exists())
            await service.observe(payload)
            self.assertEqual(launch.await_count,1)

    async def test_root_partial_nonce_directory_fails_closed_without_launch(self):
        row, packet, saved = self.bind()
        root=Path(self.temp.name)/"root-service";(root/"attempts"/saved["nonce"]).mkdir(parents=True)
        service=object.__new__(self.root.RootNativeService)
        payload={"binding":saved["binding"],"gate_sha256":saved["binding"]["gate_sha256"]}
        grant={"binding_sha256":saved["binding_sha256"],"maintenance_admission":saved["binding"]["maintenance_admission"],"issued_at":int(time.time()),"expires_at":int(time.time())+10}
        with patch.object(self.root,"ROOT",root), patch.object(self.root,"root_directory",side_effect=lambda p:os.open(p,os.O_RDONLY|os.O_DIRECTORY)), \
             patch.object(self.root,"load_gate",return_value=self.native_gate), patch.object(self.root,"verify_inputs"), \
             patch.object(self.root,"root_document",side_effect=FileNotFoundError()), \
             patch.object(self.root,"launch_controller_once",AsyncMock()) as launch:
            with self.assertRaises(FileNotFoundError):
                await service.launch(payload,grant)
            launch.assert_not_awaited()


    async def test_existing_human_approval_wait_resumes_exact_native_run_once(self):
        row, packet = self.claimed(approval=False)
        with self.assertRaises(self.gateway.ApprovalRequired) as caught:
            await self.submit(row,packet)
        await self.it.suspend(row["invocation_id"],packet,self.adapter,self.bridge,
                              {"modified":"modified-started"},caught.exception,self.path)
        with self.g.connect(self.path) as db:
            pending=dict(db.execute("SELECT * FROM governance_action_wait WHERE invocation_id=?",
                                    (row["invocation_id"],)).fetchone())
        self.assertEqual(pending["checkpoint_phase"],"published")
        action=self.it.make_action(row,packet);now=int(time.time())
        self.bridge.approval={"schema_version":1,"approval_id":pending["approval_id"],"project_id":P,"task_id":T,
            "action_fingerprint":self.validator.action_fingerprint(action),"approved_by":"human@example.invalid",
            "approved_at":now-1,"expires_at":now+600,"status":"approved","used_by_action_id":None}
        completed=[]
        async def complete(current, original_packet, record, *args):
            completed.append((current,original_packet,record))
            return {"completed_original":current["automation_run_id"]}
        executor=types.ModuleType("open_webui.utils.agentic_executor")
        executor.complete_original=complete
        automations=types.ModuleType("open_webui.models.automations")
        automations.Automations=types.SimpleNamespace(get_by_id=AsyncMock(return_value=types.SimpleNamespace(id=row["agent_id"])))
        import sys
        with patch.dict(sys.modules,{"open_webui.utils.agentic_executor":executor,"open_webui.models.automations":automations}),              patch.object(self.adapter_module,"configured_runtime",return_value=self.runtime):
            value=await self.adapter_module.resume(row["invocation_id"],config=self.config,project_gate=self.gate,
                                                   bridge=self.bridge,db_path=self.path)
        self.assertEqual(value,{"completed_original":row["automation_run_id"]})
        self.assertEqual(self.operations.count("execute"),1)
        self.assertEqual(self.bridge.calls.count("consume_action_approval"),1)
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0][0]["invocation_id"],row["invocation_id"])


    async def test_actual_native_status_route_requires_control_auth_exact_project_and_is_read_only(self):
        import ast, hmac, typing
        import fastapi, pydantic, httpx
        router_file=Path(__file__).resolve().parents[1]/"open_webui/routers/agentic_governance.py"
        tree=ast.parse(router_file.read_text())
        names={"_auth","NativeVerificationReceipt","NativeVerificationStatus","native_verification_status"}
        nodes=[node for node in tree.body if getattr(node,"name",None) in names]
        router=fastapi.APIRouter()
        namespace={"Path":Path,"os":os,"hmac":hmac,"DATA_DIR":Path(self.temp.name),
            "HTTPException":fastapi.HTTPException,"Header":fastapi.Header,
            "BaseModel":pydantic.BaseModel,"Field":pydantic.Field,"ConfigDict":pydantic.ConfigDict,
            "Literal":typing.Literal,"router":router,"PROJECT_ID":self.g.PROJECT_ID,
            "find":lambda invocation:self.g.find(invocation,self.path)}
        import sys
        module=types.ModuleType("native_route_fixture")
        module.__dict__.update(namespace);namespace=module.__dict__
        registration=patch.dict(sys.modules,{module.__name__:module});registration.start()
        self.addCleanup(registration.stop)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(router_file),"exec"),namespace)
        app=fastapi.FastAPI();app.include_router(router,prefix="/api/v1/agentic-governance")
        row,packet,saved=self.bind()
        key=Path(self.temp.name)/"control-token";key.write_text("isolated-native-fixture-"+"x"*40)
        actual_status=self.n.public_status
        with self.g.connect(self.path) as db:
            before=db.execute("SELECT COUNT(*) FROM governance_event").fetchone()[0]
        with patch.dict(os.environ,{"ABIT_ERP_CONTROL_TOKEN_FILE":str(key)}),              patch.object(self.n,"public_status",side_effect=lambda invocation:actual_status(invocation,self.path)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://fixture.invalid") as client:
                url="/api/v1/agentic-governance/projects/"+P+"/native-verification/"+row["invocation_id"]
                self.assertEqual((await client.get(url)).status_code,401)
                self.assertEqual((await client.get(url,headers={"X-ABIT-ERP-Control":"z"*48})).status_code,401)
                response=await client.get(url,headers={"X-ABIT-ERP-Control":key.read_text()})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()["phase"],"bound")
                self.assertNotIn(row["lease_token"],response.text)
                self.assertNotIn(saved["binding"]["lease_token_sha256"],response.text)
                self.assertFalse(response.json()["independent_qa_approved"])
                wrong=url.replace(P,"PROJ-0091")
                self.assertEqual((await client.get(wrong,headers={"X-ABIT-ERP-Control":key.read_text()})).status_code,404)
                self.assertEqual((await client.post(url,headers={"X-ABIT-ERP-Control":key.read_text()})).status_code,405)
        schema=app.openapi()
        operation=schema["paths"][url.replace(P,"{project_id}").replace(row["invocation_id"],"{invocation_id}")]["get"]
        self.assertIn("NativeVerificationStatus",json.dumps(operation))
        self.assertTrue(any(x["name"]=="X-ABIT-ERP-Control" for x in operation["parameters"]))
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM governance_event").fetchone()[0],before)
        self.assertEqual(self.operations,[])

    async def test_actual_delivery_selector_preserves_dot_and_ordinary_it_routes(self):
        import ast,sys
        router_file=Path(__file__).resolve().parents[1]/"open_webui/routers/agentic_governance.py"
        node=next(node for node in ast.parse(router_file.read_text()).body if getattr(node,"name",None)=="_deliver")
        namespace={"json":json,"_config":lambda:{},"_gate":self.gate,"_advance":AsyncMock(),
                   "_reconciliation_wakeup":lambda app:asyncio.Event()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(router_file),"exec"),namespace)
        class Dot:
            def __init__(self,app):pass
        executor=types.ModuleType("open_webui.utils.agentic_executor")
        executor.DotAdapter=Dot;executor.execute=AsyncMock(return_value=None)
        cases=[("it_manager","verify_native_source","NativeVerificationAdapter"),
               ("it_manager","restart_service","ITAdapter"),
               ("developer","verify_native_source","Dot"),
               ("project_manager","verify_native_source","Dot")]
        for role,operation,wanted in cases:
            row={"target_role":role,"work_packet":json.dumps({"tool_action":{"operation":operation}})}
            with patch.dict(sys.modules,{"open_webui.utils.agentic_executor":executor}),                  patch.object(self.g,"find",return_value=row) as lookup:
                await namespace["_deliver"](None,"original-routing-fixture")
                self.assertEqual(type(executor.execute.await_args.kwargs["adapter"]).__name__,wanted)
                lookup.assert_called_once_with("original-routing-fixture",include_private=True)
        self.assertEqual(self.operations,[])


    def maintenance_db(self):
        return self.path.parent / "maintenance-admission" / "gate.sqlite"

    def test_exact_maintenance_admission_missing_before_binding_denies(self):
        row, packet = self.claimed()
        self.consumed(row, packet)
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("UPDATE lease SET state='unresolved' WHERE reference=?", (row["invocation_id"],))
        with self.assertRaisesRegex(Exception, "original admission is not active"):
            self.n.bind_original(row, packet, self.native_gate, self.path)
        self.assertIsNone(self.n.original(row["invocation_id"], self.path))

    async def test_sealed_maintenance_at_final_challenge_denies_before_grant(self):
        row, packet = self.claimed()
        def seal_fixture():
            # Fault injection only: normal maintenance seal rejects an active original.
            with sqlite3.connect(self.maintenance_db()) as db:
                db.execute("UPDATE gate SET state='sealed' WHERE id=1")
        self.before_grant = seal_fixture
        with self.assertRaisesRegex(Exception, "original admission is not active"):
            await self.submit(row, packet)
        saved = self.n.original(row["invocation_id"], self.path)
        self.assertIsNone(saved["grant_issued_at"])
        self.assertEqual(saved["phase"], "launch_unknown")

    def test_replaced_maintenance_lease_or_epoch_denies_original_launch(self):
        row, packet, saved = self.bind()
        self.n.begin_launch(row, packet, self.path)
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("UPDATE lease SET id=?,epoch=epoch+1 WHERE reference=?", ("f"*48,row["invocation_id"]))
        payload={"binding":saved["binding"],"gate_sha256":saved["binding"]["gate_sha256"]}
        with self.assertRaisesRegex(ValueError, "MAINTENANCE_ADMISSION_REPLACED"):
            self.n.issue_launch_grant(row, packet, payload, self.path)
        self.assertIsNone(self.n.original(row["invocation_id"],self.path)["grant_issued_at"])

    def test_changed_maintenance_process_identity_denies_before_launch_intent(self):
        row, packet, saved = self.bind()
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("UPDATE lease SET process_start='0' WHERE reference=?",(row["invocation_id"],))
        with self.assertRaisesRegex(Exception, "process identity changed"):
            self.n.begin_launch(row,packet,self.path)
        self.assertEqual(self.n.original(row["invocation_id"],self.path)["phase"],"bound")

    async def test_draining_original_finishes_but_receipt_alone_does_not_settle_admission(self):
        row,packet=self.claimed()
        from open_webui.utils import agentic_maintenance as maintenance
        before=maintenance.require_active("governed",row["invocation_id"],db_path=self.maintenance_db())
        epoch=maintenance.status(self.maintenance_db())["epoch"]
        closed=maintenance.control("close",expected_epoch=epoch,command_id="fixture-close-native",actor="fixture-operator",db_path=self.maintenance_db())
        result=await self.submit(row,packet)
        self.assertIsNotNone(result)
        self.assertEqual(self.operations.count("execute"),1)
        saved=self.n.original(row["invocation_id"],self.path)
        self.assertEqual(saved["binding"]["maintenance_admission"],before)
        self.assertEqual(maintenance.require_active("governed",row["invocation_id"],db_path=self.maintenance_db()),before)
        with self.assertRaisesRegex(Exception,"zero unresolved leases"):
            maintenance.control("seal",expected_epoch=closed["epoch"],command_id="fixture-seal-native",actor="fixture-operator",db_path=self.maintenance_db())

    async def test_root_rejects_grant_without_exact_maintenance_proof_before_any_launch(self):
        row,packet,saved=self.bind()
        service=object.__new__(self.root.RootNativeService)
        payload={"binding":saved["binding"],"gate_sha256":saved["binding"]["gate_sha256"]}
        grant={"binding_sha256":saved["binding_sha256"],"issued_at":int(time.time()),"expires_at":int(time.time())+10}
        with patch.object(service,"original",return_value=(saved["binding"],self.native_gate,Path("/unreached"))), \
             patch.object(self.root,"verify_inputs") as verify, \
             patch.object(self.root,"launch_controller_once",AsyncMock()) as launch:
            with self.assertRaisesRegex(ValueError,"FRESH_CORE_GRANT_REQUIRED"):
                await service.launch(payload,grant)
            verify.assert_not_called();launch.assert_not_awaited()


    def service_registration(self, service):
        base=Path(self.n.__file__).parent
        pid=os.getpid()
        start=int(Path(f"/proc/{pid}/stat").read_text().rsplit(") ",1)[1].split()[19])
        return {"schema_version":1,"enabled":True,
                "module_sha256":{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in service.MODULE_NAMES},
                "core_peer":{"pid":pid,"uid":os.getuid(),"gid":os.getgid(),"start_ticks":start,
                             "cmdline_sha256":hashlib.sha256(Path(f"/proc/{pid}/cmdline").read_bytes()).hexdigest(),
                             "boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip()}}

    def test_root_registration_is_default_denied_and_has_no_arbitrary_resource_fields(self):
        service=self.load("agentic_native_service")
        registration=self.service_registration(service)
        self.assertEqual(service.parse_registration(json.dumps(registration)),registration)
        changes=[{"enabled":False},{"command":["arbitrary"]},{"module_sha256":dict.fromkeys(service.MODULE_NAMES,"PENDING_DENY")},
                 {"core_peer":{**registration["core_peer"],"uid":0}}]
        for change in changes:
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):service.parse_registration(json.dumps({**registration,**change}))
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            service.parse_registration('{"enabled":false,"enabled":true}')

    def test_exact_kernel_peer_proof_rejects_pid_generation_uid_command_and_boot_drift(self):
        service=self.load("agentic_native_service")
        peer=self.service_registration(service)["core_peer"]
        args=(peer["pid"],peer["uid"],peer["gid"])
        self.assertEqual(service.process_proof(peer,*args),peer)
        for key,value in (("pid",peer["pid"]+1),("uid",peer["uid"]+1),("gid",peer["gid"]+1),
                          ("start_ticks",peer["start_ticks"]+1),("cmdline_sha256","a"*64),("boot_id","a"*36)):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError,"CHANGED"):
                    service.process_proof({**peer,key:value},*args)

    def test_fixed_root_entrypoint_rejects_wrong_identity_location_or_arguments_before_reads(self):
        service=self.load("agentic_native_service")
        with patch.object(service,"source_bytes") as read:
            with self.assertRaisesRegex(ValueError,"FIXED_ROOT_ENTRYPOINT"):
                service.main()
            with patch.object(service.os,"geteuid",return_value=0),patch.object(service.sys,"argv",["fixed"]):
                with self.assertRaisesRegex(ValueError,"FIXED_ROOT_ENTRYPOINT"):service.main()
            with patch.object(service.os,"geteuid",return_value=0),patch.object(service,"__file__",str(service.SERVICE/"agentic_native_service.py")), \
                 patch.object(service.sys,"argv",["fixed","--command=other"]):
                with self.assertRaisesRegex(ValueError,"FIXED_ROOT_ENTRYPOINT"):service.main()
            read.assert_not_called()

    def test_root_standalone_bundle_loads_only_exact_bytes_without_core_imports(self):
        import builtins,sys
        service=self.load("agentic_native_service")
        registration=self.service_registration(service)
        base=Path(self.n.__file__).parent
        reads=[];original_import=builtins.__import__
        def read(path,limit):
            self.assertEqual(Path(path).parent,service.SERVICE);self.assertIn(Path(path).name,service.MODULE_NAMES)
            reads.append(Path(path).name);return (base/Path(path).name).read_bytes()
        def guarded_import(name,*args,**kwargs):
            if name.startswith("open_webui"):raise AssertionError("Root bundle imported writable application")
            return original_import(name,*args,**kwargs)
        with patch.dict(sys.modules,{}),patch.object(service,"source_bytes",side_effect=read), \
             patch.object(builtins,"__import__",side_effect=guarded_import):
            modules=service.load_modules(registration)
            self.assertEqual(reads,list(service.MODULE_NAMES))
            self.assertEqual(set(modules),{"agentic_native_contract","agentic_native_broker"})
            self.assertEqual(modules["agentic_native_broker"].CONTROL_HASHES,self.root.CONTROL_HASHES)
            self.assertEqual(modules["agentic_native_broker"].digest({"x":"unicode-Ω"}),self.c.digest({"x":"unicode-Ω"}))

    def test_root_bundle_hash_mismatch_rejects_before_any_module_execution(self):
        import sys
        service=self.load("agentic_native_service")
        registration=self.service_registration(service)
        registration["module_sha256"]["agentic_native_broker.py"]="f"*64
        base=Path(self.n.__file__).parent
        with patch.dict(sys.modules,{"agentic_native_contract":"unchanged-contract","agentic_native_broker":"unchanged-broker"}), \
             patch.object(service,"source_bytes",side_effect=lambda path,limit:(base/Path(path).name).read_bytes()):
            with self.assertRaisesRegex(ValueError,"BUNDLE_BYTES_CHANGED"):service.load_modules(registration)
            self.assertEqual(sys.modules["agentic_native_contract"],"unchanged-contract")
            self.assertEqual(sys.modules["agentic_native_broker"],"unchanged-broker")

    async def test_actual_retained_wire_framing_interoperates_and_rejects_corrupted_mac(self):
        class Writer:
            def __init__(self):self.raw=b""
            def write(self,raw):self.raw+=raw
            async def drain(self):pass
        key=b"x"*32;body={"schema_version":1,"message":"unicode-Ω","nonce":"f"*48}
        for sender,receiver in ((self.root,self.wire),(self.wire,self.root)):
            writer=Writer();await sender._send(writer,body,key)
            reader=asyncio.StreamReader();reader.feed_data(writer.raw);reader.feed_eof()
            self.assertEqual(await receiver._read(reader,key),body)
            reader=asyncio.StreamReader();reader.feed_data(writer.raw);reader.feed_eof()
            with self.assertRaisesRegex(ValueError,"authentication"):
                await receiver._read(reader,b"z"*32)

    async def test_root_service_close_waits_for_accepted_launch_and_blocks_new_handlers(self):
        server=object.__new__(self.root.NativeBrokerServer)
        server.closing=False;server.server=types.SimpleNamespace(close=lambda:None,wait_closed=AsyncMock())
        server.path=Path(self.temp.name)/"absent-broker.sock"
        wait=asyncio.Event()
        accepted=asyncio.create_task(wait.wait());server.accepted={accepted}
        closing=asyncio.create_task(server.close())
        await asyncio.sleep(0)
        self.assertTrue(server.closing);self.assertFalse(closing.done())
        wait.set();await closing
        self.assertTrue(accepted.done())

    def test_maintenance_zero_epoch_is_valid_but_negative_or_boolean_epoch_is_rejected(self):
        proof={"lease_id":"a"*48,"kind":"governed","reference":"original-fixture","epoch":0}
        self.assertEqual(self.c.validate_maintenance(proof,"original-fixture"),proof)
        for value in (-1,True):
            with self.assertRaises(ValueError):self.c.validate_maintenance({**proof,"epoch":value},"original-fixture")


    async def test_one_root_gate_survives_actual_queued_claim_start_and_approved_submit(self):
        row=self.queued("native-single-gate-transition")
        packet=self.packet()
        scope_before=self.n.scope_for(row,packet)
        original_gate=self.gate_for(row,packet)
        self.native_gate=deepcopy(original_gate)
        self.runtime.bridge=self.bridge
        with patch("open_webui.utils.agentic_bridge.ERPBridge",return_value=self.bridge):
            await self.adapter.preflight(row,packet)
        self.assertEqual(self.operations,["prepare"])
        self.assertEqual(self.g.find(row["invocation_id"],self.path)["state"],"queued")
        # Reuse the fixture's start-event/ERP-response setup while executing the
        # real claim_invocation exactly once against this already queued row.
        # The helper's enqueue is suppressed; the claim itself is not mocked.
        with patch.object(self,"queued",return_value=row):
            current,claimed_packet=self.claimed(row["invocation_id"])
        self.assertEqual(current["lease_fence"],row["lease_fence"])
        self.assertEqual(current["dispatch_attempts"],1)
        self.assertEqual(current["automation_run_id"],scope_before["run_id"])
        self.assertEqual(self.n.scope_for(current,claimed_packet),scope_before)
        self.native_gate=deepcopy(original_gate)
        result=await self.submit(current,claimed_packet)
        self.assertIsNotNone(result)
        saved=self.n.original(current["invocation_id"],self.path)
        self.assertEqual(saved["gate"],original_gate)
        self.assertEqual(saved["binding"]["gate_sha256"],self.c.digest(original_gate))
        self.assertEqual(self.operations,["prepare","prepare","execute"])
        self.assertEqual(self.bridge.calls.count("consume_action_approval"),1)
        self.assertEqual(saved["phase"],"completed")

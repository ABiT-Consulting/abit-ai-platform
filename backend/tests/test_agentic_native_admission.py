"""Offline real SQLite/state transitions and ordinary FS publisher tests.

ERP HTTP, protected UID/kernel/capacity and provider verifier are controlled
fixtures. No credentials, actual native controls, systemd or network are used.
"""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch, AsyncMock

import test_agentic_native_verification as legacy
import test_agentic_controls_events as fixtures

PROJECT = "PROJ-0202"
SUBJECT = "8" * 64


class CoreAdmissionCases(legacy.NativeCases):
    def setUp(self):
        self.patches = [patch.object(legacy, "P", PROJECT), patch.object(fixtures, "P", PROJECT)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        super().setUp()
        self.ac = self.load("agentic_native_admission_contract")
        self.ad = self.load("agentic_native_admission")
        self.runtime.per_run = True
        self.admit_ack_lost = self.launch_ack_lost = False
        self.challenge_hook = None
        self.root_admitted = None
        self.root_launches = 0
        self.pending_observations = 0
        self.observe_hook = None
        outer = self

        class ERP(legacy.NativeBridge):
            async def request(self, method, path, **kwargs):
                result = await super().request(method, path, **kwargs)
                if path.endswith("consume_action_approval"):
                    args = kwargs["json"]
                    result["message"].update({key: args[key] for key in
                        ("invocation_id", "run_id", "project_id", "task_id")})
                    result["message"]["duplicate"] = False
                return result

        self.bridge = ERP(self)

        class Client(self.wire.BrokerClient):
            def __init__(self):
                self.runtime = outer.runtime

            async def request(self, operation, payload):
                outer.operations.append(operation)
                if outer.challenge_hook:
                    outer.challenge_hook(operation)
                grant = await self.runtime.authorize(operation, deepcopy(payload))
                outer.assertTrue(grant["authorized"])
                if operation == "prepare":
                    return outer.ac.readiness(payload["scope"], SUBJECT)
                if operation == "admit":
                    outer.ac.validate_admission_grant(grant, payload, int(time.time()))
                    a = payload["attestation"]
                    now = int(time.time())
                    gate = outer.gate_for(outer.g.find(a["scope"]["invocation_id"], outer.path,
                                                     include_private=True), outer.packet())
                    gate.update(schema_version=2, kind="abit_native_per_invocation_gate",
                                scope=a["scope"], subject_sha256=SUBJECT, admission_sha256=outer.c.digest(a),
                                issued_at=now, expires_at=min(now+7200, a["approval"]["expires_at"]))
                    outer.root_admitted = outer.ac.observation(a, gate)
                    if outer.admit_ack_lost:
                        raise TimeoutError("Fixture lost admission acknowledgement")
                    return deepcopy(outer.root_admitted)
                if operation == "observe_admission":
                    a = payload["attestation"]
                    return deepcopy(outer.root_admitted or outer.ac.observation(a))
                binding = payload["binding"]
                if operation == "execute":
                    outer.root_launches += 1
                    if outer.launch_ack_lost:
                        raise TimeoutError("Fixture lost launch acknowledgement")
                if operation == "observe" and not outer.root_launches:
                    raise FileNotFoundError("No original native launch")
                receipt = outer.root.empty_receipt(binding)
                if operation == "observe" and outer.observe_hook:
                    outer.observe_hook()
                if outer.pending_observations:
                    outer.pending_observations -= 1
                    return receipt
                receipt.update(state="completed", outcome_sha256="a"*64, worker_result_sha256="b"*64,
                               reservation_sha256="c"*64, cleanup_confirmed=True, operations_count=10)
                return receipt

        self.runtime.client = Client()

    def packet(self, task=fixtures.T, role="it_manager", project=PROJECT):
        return super().packet(task, role, project)

    def queued(self, ident="native-invocation-fixture"):
        return self.enqueue(ident, role="it_manager", project=PROJECT)

    def consumed_full(self, row, packet):
        value = super().consumed(row, packet)
        value.update({key: row[name] for key, name in (
            ("invocation_id","invocation_id"),("run_id","automation_run_id"),
            ("project_id","erp_project"),("task_id","erp_task"))})
        value["duplicate"] = False
        with self.gateway._connect(self.path) as db:
            db.execute("UPDATE governance_action SET approval_json=? WHERE action_id=?",
                       (self.c.canonical(value), value["action_id"]))
        self.bridge.approval["used_by_action_id"] = value["action_id"]
        return value

    async def test_fresh_preflight_has_no_admission_intent_or_authority(self):
        row = self.queued()
        self.runtime.bridge = self.bridge
        with patch("open_webui.utils.agentic_bridge.ERPBridge", return_value=self.bridge):
            await self.adapter.preflight(row, self.packet())
        self.assertEqual(self.operations, ["prepare"])
        self.assertIsNone(self.ad.original(row["invocation_id"], self.path))
        self.assertIsNone(self.root_admitted)
        self.assertEqual(self.bridge.calls, [])

    async def test_actual_gateway_consume_precedes_admit_and_launch(self):
        row, packet = self.claimed()
        result = await self.submit(row, packet)
        self.assertIsNotNone(result)
        self.assertEqual(self.operations, ["prepare", "admit", "execute"])
        saved = self.ad.original(row["invocation_id"], self.path)
        self.assertEqual(saved["phase"], "admitted")
        self.assertEqual(saved["attestation"]["consumption"]["run_id"], row["automation_run_id"])
        self.assertEqual(saved["attestation"]["approval"]["used_by_action_id"], saved["action_id"])
        self.assertEqual(self.root_launches, 1)
        self.assertEqual(self.n.public_status(row["invocation_id"], self.path)["receipt"]["state"], "completed")

    async def test_no_human_approval_cannot_issue(self):
        row, packet = self.claimed(approval=False)
        with self.assertRaises(self.gateway.ApprovalRequired):
            await self.submit(row, packet)
        self.assertNotIn("admit", self.operations)
        self.assertIsNone(self.ad.original(row["invocation_id"], self.path))

    async def test_lost_admission_ack_is_original_observation_no_launch(self):
        row, packet = self.claimed()
        self.admit_ack_lost = True
        with self.assertRaises(TimeoutError):
            await self.submit(row, packet)
        self.assertEqual(self.operations.count("admit"), 1)
        self.assertEqual(self.root_launches, 0)
        await self.runtime.observe_admission(row, packet, self.bridge)
        self.assertEqual(self.operations.count("admit"), 1)
        self.assertEqual(self.root_launches, 0)
        self.assertEqual(self.ad.original(row["invocation_id"], self.path)["phase"], "admitted")

    async def test_lost_launch_ack_observes_without_second_launch(self):
        row, packet = self.claimed()
        self.launch_ack_lost = True
        result = await self.submit(row, packet)
        self.assertIsNotNone(result)
        await self.runtime.observe(row, packet, self.bridge)
        self.assertEqual(self.operations.count("admit"), 1)
        self.assertEqual(self.operations.count("execute"), 1)
        self.assertEqual(self.root_launches, 1)

    async def test_stop_at_admission_challenge_prevents_root_creation(self):
        row, packet = self.claimed()
        def stopped(operation):
            if operation == "admit":
                self.g.project_control(PROJECT, "stop", actor="human@example.invalid",
                                       policy_revision="project-revision-1", db_path=self.path)
        self.challenge_hook = stopped
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        self.assertIsNone(self.root_admitted)
        self.assertEqual(self.root_launches, 0)

    async def test_changed_packet_at_admission_challenge_denied(self):
        row, packet = self.claimed()
        def changed(operation):
            if operation == "admit":
                self.bridge.modified = "foreign-modified"
        self.challenge_hook = changed
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        self.assertIsNone(self.root_admitted)
        self.assertEqual(self.root_launches, 0)

    async def test_revoked_approval_at_challenge_denied(self):
        row, packet = self.claimed()
        self.challenge_hook = lambda op: self.bridge.approval.update(status="revoked") if op=="admit" else None
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        self.assertIsNone(self.root_admitted)
        self.assertEqual(self.root_launches, 0)

    async def test_registered_owner_change_at_challenge_denies(self):
        row, packet = self.claimed()
        def change(operation):
            if operation == "admit":
                self.users["it_manager"] = "foreign-owner@example.invalid"
        self.challenge_hook = change
        with self.assertRaises(ValueError):
            await self.submit(row, packet)
        self.assertIsNone(self.root_admitted)

    def test_expired_core_lease_denies_before_admission_intent(self):
        row, packet = self.claimed()
        self.consumed_full(row, packet)
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_invocation SET lease_expires_at=? WHERE invocation_id=?",
                       (int(time.time()) - 1, row["invocation_id"]))
        with self.assertRaises(ValueError):
            self.ad.begin(row, packet, self.bridge.approval, SUBJECT, self.path)
        self.assertIsNone(self.ad.original(row["invocation_id"], self.path))

    def test_missing_actual_maintenance_lease_denies_admission(self):
        import sqlite3
        row, packet = self.claimed()
        self.consumed_full(row, packet)
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("DELETE FROM lease WHERE reference=?", (row["invocation_id"],))
        with self.assertRaises(Exception):
            self.ad.begin(row, packet, self.bridge.approval, SUBJECT, self.path)
        self.assertIsNone(self.ad.original(row["invocation_id"], self.path))

    def test_uncertain_admission_is_visible_without_approval_data(self):
        row, packet, saved = self.fixture_intent()
        status = self.n.public_status(row["invocation_id"], self.path)
        self.assertEqual(status["phase"], "admission_unknown")
        self.assertEqual(status["admission"]["attestation_sha256"], saved["attestation_sha256"])
        self.assertNotIn("consumption", status["admission"])
        self.assertNotIn("approval", status["admission"])


    async def test_ack_pending_terminal_observes_once_original_until_normal_result(self):
        row, packet = self.claimed()
        self.pending_observations = 3
        pause = AsyncMock()
        with patch.object(self.n.asyncio, "sleep", pause):
            result = await self.submit(row, packet)
        self.assertIsNotNone(result)
        self.assertEqual(self.operations.count("execute"), 1)
        self.assertEqual(self.operations.count("admit"), 1)
        self.assertEqual(self.operations.count("observe"), 3)
        self.assertEqual(pause.await_count, 3)
        self.assertEqual(self.root_launches, 1)
        self.assertEqual(self.n.original(row["invocation_id"], self.path)["phase"], "completed")
        self.assertEqual(self.gateway.get_action(row["invocation_id"], self.adapter.action["action_id"], self.path)["state"], "completed")

    async def test_original_poll_timeout_keeps_uncertainty_for_existing_recovery(self):
        row, packet = self.claimed()
        self.pending_observations = 100
        with patch.object(self.n, "NATIVE_MAX_OBSERVATION_SECONDS", 0):
            with self.assertRaises(RuntimeError):
                await self.submit(row, packet)
        self.assertEqual(self.operations.count("execute"), 1)
        self.assertEqual(self.root_launches, 1)
        self.assertEqual(self.gateway.get_action(row["invocation_id"], self.adapter.action["action_id"], self.path)["state"], "uncertain")
        self.pending_observations = 0
        receipt = await self.runtime.observe(row, packet, self.bridge)
        self.assertEqual(receipt["state"], "completed")
        self.assertEqual(self.operations.count("execute"), 1)

    async def test_stop_during_original_observation_retains_receipt_without_publication(self):
        row, packet = self.claimed()
        self.pending_observations = 1
        self.observe_hook = lambda: self.g.request_cancel(row["invocation_id"], "human@example.invalid", self.path)
        with patch.object(self.n.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
        self.assertEqual(self.g.find(row["invocation_id"], self.path)["state"], "cancel_requested")
        self.assertEqual(self.n.original(row["invocation_id"], self.path)["receipt"]["state"], "completed")
        self.assertEqual(self.root_launches, 1)
        self.assertEqual(self.gateway.get_action(row["invocation_id"], self.adapter.action["action_id"], self.path)["state"], "uncertain")

    async def test_changed_packet_during_observation_blocks_return_for_publication(self):
        row, packet = self.claimed()
        self.pending_observations = 1
        self.observe_hook = lambda: setattr(self.bridge, "modified", "foreign-after-start")
        with patch.object(self.n.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
        self.assertEqual(self.root_launches, 1)
        self.assertEqual(self.gateway.get_action(row["invocation_id"], self.adapter.action["action_id"], self.path)["state"], "uncertain")

    async def test_replaced_fence_during_observation_blocks_completion(self):
        row, packet = self.claimed()
        self.pending_observations = 1
        def replace():
            with self.g.connect(self.path) as db:
                db.execute("UPDATE governance_invocation SET lease_fence=lease_fence+1 WHERE invocation_id=?", (row["invocation_id"],))
        self.observe_hook = replace
        with patch.object(self.n.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
        self.assertEqual(self.root_launches, 1)

    async def test_changed_owner_during_observation_blocks_completion(self):
        row, packet = self.claimed()
        self.pending_observations = 1
        self.observe_hook = lambda: self.users.update(it_manager="foreign-owner@example.invalid")
        with patch.object(self.n.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
        self.assertEqual(self.root_launches, 1)

    async def test_expired_lease_during_original_observation_does_not_reauthorize(self):
        row, packet = self.claimed()
        self.pending_observations = 1
        def expire():
            with self.g.connect(self.path) as db:
                db.execute("UPDATE governance_invocation SET lease_expires_at=? WHERE invocation_id=?", (int(time.time())-1,row["invocation_id"]))
        self.observe_hook = expire
        with patch.object(self.n.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
        self.assertEqual(self.root_launches, 1)

    async def test_revoked_approval_after_start_blocks_completion(self):
        row, packet = self.claimed()
        self.pending_observations = 1
        self.observe_hook = lambda: self.bridge.approval.update(status="revoked")
        with patch.object(self.n.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ValueError):
                await self.submit(row, packet)
        self.assertEqual(self.root_launches, 1)


    async def test_actual_approval_resume_keeps_heartbeat_until_original_terminal(self):
        import sys
        row, packet = self.claimed(approval=False)
        with self.assertRaises(self.gateway.ApprovalRequired) as caught:
            await self.submit(row,packet)
        await self.it.suspend(row["invocation_id"],packet,self.adapter,self.bridge,
            {"modified":"modified-started"},caught.exception,self.path)
        with self.g.connect(self.path) as db:
            pending=dict(db.execute("SELECT * FROM governance_action_wait WHERE invocation_id=?",(row["invocation_id"],)).fetchone())
        action=self.it.make_action(row,packet);now=int(time.time())
        self.bridge.approval={"schema_version":1,"approval_id":pending["approval_id"],"project_id":PROJECT,"task_id":fixtures.T,
            "action_fingerprint":self.validator.action_fingerprint(action),"approved_by":"human@example.invalid",
            "approved_at":now-1,"expires_at":now+600,"status":"approved","used_by_action_id":None}
        completed=[];beats=[];first_beat=asyncio.Event();real_sleep=asyncio.sleep;heartbeat=self.g.heartbeat
        def measured_heartbeat(*args,**kwargs):
            result=heartbeat(*args,**kwargs);beats.append(result);first_beat.set();return result
        async def bounded_clock(seconds):
            if seconds == 30:
                await real_sleep(0)
            else:
                await asyncio.wait_for(first_beat.wait(),1)
                await real_sleep(0)
        async def complete(current,original_packet,record,*args):
            completed.append((current,original_packet,record));return {"completed_original":current["automation_run_id"]}
        executor=types.ModuleType("open_webui.utils.agentic_executor");executor.complete_original=complete
        automations=types.ModuleType("open_webui.models.automations")
        automations.Automations=types.SimpleNamespace(get_by_id=AsyncMock(return_value=types.SimpleNamespace(id=row["agent_id"])))
        self.pending_observations=2
        with patch.dict(sys.modules,{"open_webui.utils.agentic_executor":executor,"open_webui.models.automations":automations}), \
                patch.object(self.adapter_module,"configured_runtime",return_value=self.runtime), \
                patch.object(self.g,"heartbeat",side_effect=measured_heartbeat), \
                patch.object(self.n.asyncio,"sleep",side_effect=bounded_clock):
            result=await self.adapter_module.resume(row["invocation_id"],config=self.config,project_gate=self.gate,
                bridge=self.bridge,db_path=self.path)
        self.assertEqual(result,{"completed_original":row["automation_run_id"]})
        self.assertGreaterEqual(len(beats),1);self.assertTrue(all(beats))
        self.assertEqual(self.operations.count("execute"),1);self.assertEqual(self.operations.count("observe"),2)
        self.assertEqual(self.bridge.calls.count("consume_action_approval"),1);self.assertEqual(len(completed),1)

    async def test_timeout_then_existing_native_recovery_completes_original_without_launch(self):
        import sys
        row,packet=self.claimed();self.pending_observations=100
        with patch.object(self.n,"NATIVE_MAX_OBSERVATION_SECONDS",0),self.assertRaises(RuntimeError):
            await self.submit(row,packet)
        self.g.finish_execution(row["invocation_id"],row["automation_run_id"],row["lease_token"],"uncertain",db_path=self.path)
        self.pending_observations=0;completed=[]
        async def complete(current,original_packet,record,*args):
            completed.append(record);return {"original":current["invocation_id"]}
        executor=types.ModuleType("open_webui.utils.agentic_executor");executor.complete_original=complete
        with patch.dict(sys.modules,{"open_webui.utils.agentic_executor":executor}):
            result=await self.adapter_module.recover_completed(row["invocation_id"],config=self.config,
                project_gate=self.gate,bridge=self.bridge,db_path=self.path,adapter=self.adapter,
                automation=types.SimpleNamespace(id=row["agent_id"]))
        self.assertEqual(result,{"original":row["invocation_id"]});self.assertEqual(len(completed),1)
        self.assertEqual(self.operations.count("execute"),1);self.assertEqual(self.operations.count("admit"),1)
        self.assertEqual(self.bridge.calls.count("consume_action_approval"),1)


    def fixture_intent(self):
        row, packet = self.claimed()
        self.consumed_full(row, packet)
        saved = self.ad.begin(row, packet, deepcopy(self.bridge.approval), SUBJECT, self.path)
        return row, packet, saved

    def test_second_admission_grant_denied_transactionally(self):
        row, packet, saved = self.fixture_intent()
        payload = {"attestation":saved["attestation"]}
        self.ad.grant(row, packet, self.bridge.approval, payload, self.path)
        with self.assertRaisesRegex(ValueError, "ALREADY_USED"):
            self.ad.grant(row, packet, self.bridge.approval, payload, self.path)

    def test_admission_intent_cannot_be_recreated_or_deleted(self):
        row, packet, saved = self.fixture_intent()
        with self.assertRaisesRegex(ValueError, "OBSERVATION_ONLY"):
            self.ad.begin(row, packet, self.bridge.approval, SUBJECT, self.path)
        import sqlite3
        with self.g.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM governance_native_admission")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE governance_native_admission SET attestation_json='{}'")

    def test_receipt_and_owner_mutation_before_grant_denied(self):
        row, packet, saved = self.fixture_intent()
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_action SET approval_json='{}'")
        with self.assertRaises(ValueError):
            self.ad.grant(row, packet, self.bridge.approval, {"attestation":saved["attestation"]}, self.path)
        self.assertIsNone(self.ad.original(row["invocation_id"], self.path)["grant_issued_at"])

    def test_lease_fence_replacement_denies_grant(self):
        row, packet, saved = self.fixture_intent()
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_invocation SET lease_fence=lease_fence+1 WHERE invocation_id=?",
                       (row["invocation_id"],))
        with self.assertRaises(ValueError):
            self.ad.grant(row, packet, self.bridge.approval, {"attestation":saved["attestation"]}, self.path)

    def test_exact_attestation_negative_matrix(self):
        _, _, saved = self.fixture_intent()
        value = saved["attestation"]
        mutations = (
            lambda a:a["consumption"].update(consumed=False),
            lambda a:a["consumption"].update(run_id="f"*36),
            lambda a:a["consumption"].update(task_id="TASK-other"),
            lambda a:a["consumption"].update(receipt_id=""),
            lambda a:a["consumption"].update(duplicate=0),
            lambda a:a["consumption"].update(extra="forged"),
            lambda a:a["approval"].update(status="revoked"),
            lambda a:a["approval"].update(used_by_action_id="wrong"),
            lambda a:a["approval"].update(approved_by="ai-developer@abitcons.com"),
            lambda a:a["approval"].update(expires_at=a["prepared_at"]),
            lambda a:a["action"]["parameters"].update(command="true"),
            lambda a:a["scope"].update(project_id="PROJ-0196"),
            lambda a:a.update(prepared_at=int(time.time())-31),
            lambda a:a.update(lease_expires_at=int(time.time())-1),
            lambda a:a["maintenance_admission"].update(reference="another"),
        )
        for mutation in mutations:
            candidate=deepcopy(value);mutation(candidate)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    self.ac.validate_attestation(candidate,int(time.time()))

    def test_receipt_cannot_substitute_gate_subject_or_attestation(self):
        row, packet, saved = self.fixture_intent()
        gate=self.gate_for(row,packet);gate.update(schema_version=2,kind="abit_native_per_invocation_gate",
            subject_sha256=SUBJECT,admission_sha256=self.c.digest(saved["attestation"]))
        accepted=self.ac.observation(saved["attestation"],gate)
        for field in ("subject_sha256","admission_sha256"):
            value=deepcopy(accepted);value["gate"][field]="f"*64
            with self.assertRaises(ValueError):self.ad.accept(row["invocation_id"],value,self.path)

    def test_legacy_schema1_remains_valid_and_new_window_is_bounded(self):
        row, packet, saved = self.fixture_intent()
        gate=self.gate_for(row,packet)
        self.c.validate_gate(gate,gate["scope"],int(time.time()))
        gate.update(schema_version=2,kind="abit_native_per_invocation_gate",
                    subject_sha256=SUBJECT,admission_sha256=self.c.digest(saved["attestation"]))
        gate["expires_at"]=gate["issued_at"]+7201
        with self.assertRaises(ValueError):self.c.validate_gate(gate,gate["scope"],int(time.time()))


# The suite selects only methods declared above; the original test class is not
# duplicated here. Existing legacy behavior is run once by the focused runner.
def suite():
    return unittest.TestSuite(CoreAdmissionCases(name) for name in CoreAdmissionCases.__dict__
                              if name.startswith("test_"))


def load_tests(loader, tests, pattern):
    """Expose only this module's cases to standard unittest discovery."""
    return suite()


if __name__ == "__main__":
    result=unittest.TextTestRunner(verbosity=2).run(suite())
    raise SystemExit(not result.wasSuccessful())

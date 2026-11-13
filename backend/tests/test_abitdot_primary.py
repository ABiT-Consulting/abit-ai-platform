"""Dot-first routing: owner/project context, receipt, idempotency and non-replay."""
from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "open_webui/utils/abitdot_primary.py"
spec = importlib.util.spec_from_file_location("primary_dot_contract", SOURCE)
dot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dot)
DOT_ID = "65f7e516-abc8-4c3f-9ffa-e8afca9cfaed~6ac25fb93f1c8192931d7692e8dc9a0e"
DOT_URL = "https://chatgpt.com/dots/" + DOT_ID


def payload(run_id="manual-1"):
    return dot.build_project_envelope(
        owner="owner-1", dot_id=DOT_ID, run_id=run_id,
        automation={
            "id":"automation-1", "user_id":"owner-1",
            "name":"PROJECT MANAGER-INTERMARKET INDOGUNA.",
            "data":{
                "prompt":"Exact original request " + "implement ERP scope "*160,
                "model_id":"omniroute.abit-chatgpt-relay/project-management",
                "fallback_model_id":"codex-cli-linux-dev",
                "token":"should-not-leak",
            },
            "meta":{"erp_task":"TASK-2026-05824","erp_url":"https://erp.abit.ai"},
        },
        project={
            "id":"folder-1", "user_id":"owner-1", "name":"InterMarket Group",
            "data":{"system_prompt":"Project instructions for PROJ-0196"},
            "meta":{"chatgpt_project_id":"g-p-00001111222233334444555566667777",
                    "chatgpt_project_url":"https://chatgpt.com/g/g-p-00001111222233334444555566667777"},
        },
        previous_chat={
            "id":"chat-1", "user_id":"owner-1",
            "meta":{"automation_id":"automation-1"},
            "history":{"messages":{"old":{"role":"assistant","content":"Checked previous task"}}},
        },
    )


class ProjectContractTests(unittest.TestCase):
    def test_gate_off_and_owner_only(self):
        env={"ABIT_DOT_PRIMARY_ENABLED":"false","ABIT_CHATGPT_DOT_URL":DOT_URL,
             "ABIT_CHATGPT_DOT_OWNER_ID":"owner-1"}
        self.assertIsNone(dot.primary_dot_for_owner("owner-1",env))
        env["ABIT_DOT_PRIMARY_ENABLED"]="true"
        self.assertEqual(dot.primary_dot_for_owner("owner-1",env),DOT_ID)
        self.assertIsNone(dot.primary_dot_for_owner("owner-2",env))
        env["ABIT_CHATGPT_DOT_URL"]=DOT_URL+"?prompt=poison"
        self.assertIsNone(dot.primary_dot_for_owner("owner-1",env))
        env["ABIT_CHATGPT_DOT_URL"]=DOT_URL.replace("chatgpt.com","chatgpt.com.evil.tld")
        self.assertIsNone(dot.primary_dot_for_owner("owner-1",env))

    def test_full_original_prompt_and_related_project_not_fallback(self):
        p=payload()
        self.assertTrue(p["execution_policy"]["dot_primary"])
        self.assertEqual(p["primary_responder"],"abitdot")
        self.assertEqual(p["related_project"]["name"],"InterMarket Group")
        self.assertEqual(p["related_project"]["saved_instructions"],
                         "Project instructions for PROJ-0196")
        self.assertEqual(p["related_project"]["chatgpt_project_id"],
                         "g-p-00001111222233334444555566667777")
        self.assertGreater(len(p["task"]["original_prompt"]),3000)
        self.assertEqual(p["task"]["meta"]["erp_task"],"TASK-2026-05824")
        self.assertEqual(p["previous_chat"]["history"]["messages"]["old"]["content"],
                         "Checked previous task")
        self.assertIn("Do not use Work mode",p["task"]["instructions"])
        self.assertIn("ERP task status links",p["task"]["instructions"])

    def test_secrets_redacted_and_idempotency_stable(self):
        p=payload()
        self.assertEqual(p["task"]["data"]["token"],"[redacted]")
        self.assertEqual(p["idempotency_key"],payload()["idempotency_key"])
        self.assertNotEqual(p["idempotency_key"],payload("manual-2")["idempotency_key"])

    def test_prevent_cross_owner_project_and_wrong_chat(self):
        with self.assertRaises(PermissionError):
            dot.build_project_envelope(
                owner="owner-x",automation={"id":"a","user_id":"owner-x",
                "data":{"prompt":"test"}},run_id="r1",dot_id=DOT_ID,
                project={"user_id":"some-other-owner","id":"foreign"})
        with self.assertRaises(PermissionError):
            dot.build_project_envelope(
                owner="owner-x",automation={"id":"a","user_id":"owner-x",
                "data":{"prompt":"test"}},run_id="r1",dot_id=DOT_ID,
                previous_chat={"user_id":"owner-x","meta":{"automation_id":"b"}})


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"dot_primary.sqlite"
        self.envelope=payload()

    async def test_verified_ack_prevents_duplicate_execution(self):
        sender=AsyncMock(return_value={
            "owner_id":"owner-1","automation_id":"automation-1",
            "run_id":"manual-1","dot_id":DOT_ID,
            "idempotency_key":self.envelope["idempotency_key"],
            "status":"accepted","receipt_id":"dot-verified-ack-1",
        })
        first=await dot.deliver(self.envelope,sender,self.path)
        second=await dot.deliver(self.envelope,sender,self.path)
        self.assertEqual(first["state"],"delegated")
        self.assertEqual(second["state"],"delegated")
        self.assertTrue(second["duplicate"])
        self.assertEqual(sender.await_count,1)
        with dot.ledger(self.path) as db:
            state=db.execute("SELECT status FROM dot_primary_runs").fetchone()["status"]
            self.assertEqual(state,"delegated")  # NOT completed

    async def test_second_manual_run_waits_for_accepted_dot_task(self):
        sender=AsyncMock(side_effect=lambda e: {
            "owner_id":e["owner_id"], "automation_id":e["automation_id"],
            "run_id":e["run_id"], "dot_id":e["dot_id"],
            "idempotency_key":e["idempotency_key"],
            "status":"accepted","receipt_id":"verified-dot-ack-second-guard",
        })
        first=await dot.deliver(self.envelope,sender,self.path)
        second=await dot.deliver(payload(run_id="manual-2"),sender,self.path)
        self.assertEqual(first["state"],"delegated")
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["state"],"delegated")
        self.assertEqual(sender.await_count,1)
        with dot.ledger(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dot_primary_runs")
                             .fetchone()[0],1)

    async def test_owner_run_evidence_excludes_other_users_context(self):
        sender=AsyncMock(return_value={
            "owner_id":"owner-1","automation_id":"automation-1",
            "run_id":"manual-1","dot_id":DOT_ID,
            "idempotency_key":self.envelope["idempotency_key"],
            "status":"accepted","receipt_id":"verified_owner_reply_123",
            "dot_turn_result":"Verified short Dot response",
            "dot_conversation_url":"https://chatgpt.com/c/owner-conversation",
        })
        saved=await dot.deliver(self.envelope,sender,self.path)
        self.assertEqual(saved["state"],"delegated")
        visible=dot.read_owner_primary_runs("owner-1","automation-1",self.path)
        self.assertEqual(len(visible),1)
        self.assertEqual(visible[0]["response_text"],"Verified short Dot response")
        self.assertEqual(visible[0]["conversation_url"],
                         "https://chatgpt.com/c/owner-conversation")
        self.assertNotIn("envelope_json",visible[0])
        self.assertEqual(dot.read_owner_primary_runs("owner-2","automation-1",self.path),[])
        self.assertEqual(dot.read_owner_primary_runs("owner-1","unrelated-automation",self.path),[])

    async def test_missing_owner_or_delivery_receipt_does_not_claim_success(self):
        sender=AsyncMock(return_value={"status":"accepted","receipt_id":"fake-id"})
        result=await dot.deliver(self.envelope,sender,self.path)
        self.assertEqual(result["state"],"unknown")
        self.assertIsNone(result["receipt_id"])

    async def test_timeout_outcome_unknown_and_no_second_dispatch(self):
        async def hang(_):
            await asyncio.sleep(.3)
        first=await dot.deliver(self.envelope,hang,self.path,timeout_seconds=.01)
        self.assertEqual(first["state"],"unknown")
        second=await dot.deliver(self.envelope,hang,self.path,timeout_seconds=.01)
        self.assertEqual(second["state"],"unknown")
        self.assertTrue(second["duplicate"])

    async def test_missing_supported_adapter_does_not_send_anywhere(self):
        result=await dot.deliver(self.envelope,None,self.path)
        self.assertEqual(result["state"],"unknown")


class SourceIntegrationTests(unittest.TestCase):
    def test_dot_first_before_normal_model_dispatch_and_api_status_intact(self):
        source=(ROOT/"open_webui/utils/automations.py").read_text()
        start=source.index("async def _execute_automation_once(")
        body=source[start:]
        self.assertLess(body.index("if await _run_with_primary_dot_if_ready("),
                        body.index("model_id = automation.data['model_id']"))
        self.assertLess(body.index("if await _run_with_primary_dot_if_ready("),
                        body.index("from open_webui.utils.abitdot_failover import owns_automation"))
        self.assertIn("await _record_run(automation.id, 'success'",body)
        self.assertIn("await _record_run(automation.id, 'error'",body)
        self.assertIn("build_saved_selected_context",body)


class RunnerHookTests(unittest.IsolatedAsyncioTestCase):
    """Run the actual Dot-primary entry hook with isolated dependencies."""

    async def asyncSetUp(self):
        import sys
        import os
        from types import ModuleType
        from unittest.mock import patch
        self.files=tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.folder=SimpleNamespace(model_dump=lambda: {
            "id":"folder-1", "user_id":"owner-1",
            "name":"InterMarket Group",
            "data":{"system_prompt":"Actual saved instructions"},
            "meta":{"chatgpt_project_id":"g-p-00001111222233334444555566667777"},
        })
        self.chat=SimpleNamespace(
            id="chat-1",
            chat={"meta":{"automation_id":"automation-1"},
                  "history":{"messages":{"prev":{
                      "role":"assistant","content":"Earlier checkpoint"}}}},
        )
        self.automation=SimpleNamespace(
            id="automation-1", user_id="owner-1", name="InterMarket coordinator",
            folder_id="folder-1",
            data={"prompt":"Exact original prompt",
                  "fallback_model_id":"codex-cli-linux-dev",
                  "model_id":"omniroute.abit-chatgpt-relay/project-management"},
            meta={"erp_task":"TASK-2026-05824"},
        )
        self.sent=AsyncMock()
        self.results=AsyncMock()
        self.Folders=SimpleNamespace(get_folder_by_id_and_user_id=AsyncMock(
            return_value=self.folder))
        source=(ROOT/"open_webui/utils/automations.py").read_text()
        tree=ast.parse(source)
        function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                      and n.name=="_run_with_primary_dot_if_ready")
        self.ns={
            "Folders":self.Folders,
            "_load_reusable_chat":AsyncMock(return_value=(self.chat,None,[])),
            "_record_run":self.results, "os":os,
            "uuid4":__import__("uuid").uuid4,
        }
        self.app=SimpleNamespace(state=SimpleNamespace(
            ABIT_DOT_PRIMARY_SEND=self.sent))
        exec(compile(ast.Module(body=[function],type_ignores=[]),
                     "<source dot-first runner>", "exec"),self.ns)
        registered=ModuleType("open_webui.utils.abitdot_primary")
        for name in ("primary_dot_for_owner","build_project_envelope","deliver"):
            setattr(registered,name,getattr(dot,name))
        env_stub=ModuleType("open_webui.env")
        env_stub.DATA_DIR=self.files.name
        self.registered=registered
        self.relay_sender=AsyncMock(return_value={"status":"outcome_unknown"})
        relay_module=ModuleType("open_webui.utils.abitdot_primary_relay")
        relay_module.send_via_existing_relay=self.relay_sender
        self.relay_module=relay_module
        self.env_stub=env_stub
        self.config={
            "ABIT_DOT_PRIMARY_ENABLED":"true",
            "ABIT_CHATGPT_DOT_URL":DOT_URL,
            "ABIT_CHATGPT_DOT_OWNER_ID":"owner-1",
        }

    async def run_hook(self, sender=None):
        import sys
        with patch.dict(__import__("os").environ,self.config), patch.dict(
            sys.modules, {
                "open_webui.utils.abitdot_primary":self.registered,
                "open_webui.utils.abitdot_primary_relay":self.relay_module,
                "open_webui.env":self.env_stub,
            },
        ):
            self.app.state.ABIT_DOT_PRIMARY_SEND=sender
            return await self.ns["_run_with_primary_dot_if_ready"](
                self.app,self.automation,SimpleNamespace(id="owner-1"),
                "Full original expanded prompt",None,
            )

    async def test_verified_dot_receipt_delegates_without_original_model(self):
        async def accepted(message):
            self.assertEqual(message["related_project"]["name"],"InterMarket Group")
            self.assertEqual(message["task"]["original_prompt"],
                             "Full original expanded prompt")
            self.assertEqual(message["previous_chat"]["meta"]["automation_id"],
                             "automation-1")
            return {
                "owner_id":message["owner_id"],
                "automation_id":message["automation_id"],
                "run_id":message["run_id"],
                "dot_id":message["dot_id"],
                "idempotency_key":message["idempotency_key"],
                "status":"accepted",
                "receipt_id":"verified-from-real-adapter",
            }
        handled=await self.run_hook(accepted)
        self.assertTrue(handled)
        self.assertEqual(self.results.await_args.args[:2],
                         ("automation-1","delegated"))
        self.assertEqual(self.results.await_args.kwargs["chat_id"],"chat-1")

    async def test_unverified_relay_receipt_never_runs_a_legacy_provider(self):
        handled=await self.run_hook(None)
        self.assertTrue(handled)
        self.assertEqual(self.relay_sender.await_count,1)
        self.assertEqual(self.results.await_args.args[:2],
                         ("automation-1","deferred"))
        self.assertTrue(self.results.await_args.kwargs["error"])
        self.assertEqual(self.results.await_args.kwargs["chat_id"],"chat-1")

    async def test_existing_relay_is_default_primary_sender(self):
        async def verified(envelope):
            return {
                "owner_id": envelope["owner_id"],
                "automation_id": envelope["automation_id"],
                "run_id": envelope["run_id"],
                "dot_id": envelope["dot_id"],
                "idempotency_key": envelope["idempotency_key"],
                "status": "accepted", "receipt_id": "verified_relay_reply_receipt",
                "dot_turn_result":"Related project context was accepted.",
            }
        self.relay_sender.side_effect=verified
        handled=await self.run_hook(None)
        self.assertTrue(handled)
        self.assertEqual(self.relay_sender.await_count,1)
        self.assertEqual(self.results.await_args.args[:2],
                         ("automation-1","delegated"))

    async def test_different_owner_never_receives_another_users_context(self):
        self.automation.user_id="owner-2"
        handled=await self.run_hook(None)
        self.assertFalse(handled)
        self.assertEqual(self.results.await_count,0)
        self.assertEqual(self.Folders.get_folder_by_id_and_user_id.await_count,0)

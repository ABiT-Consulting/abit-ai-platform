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

    def test_exact_repeated_project_prompt_is_referenced_not_repeated(self):
        text="Repeatable original full source prompt " + ("task context "*120)
        project=dot.build_project_envelope(
            owner="owner-1",dot_id=DOT_ID,run_id="one-turn",
            automation={
                "id":"automation-1","user_id":"owner-1","name":"sample",
                "data":{"prompt":text,"other_setting":"preserved"},
                "meta":{"erp_task":"TASK-2026-05824"},
            },
            previous_chat={
                "user_id":"owner-1","meta":{"automation_id":"automation-1"},
                "history":{"messages":{
                    "old-user":{"role":"user","content":text},
                    "old-assistant":{"role":"assistant","content":"Unique earlier checkpoint"},
                }},
            },
        )
        self.assertEqual(project["task"]["original_prompt"],text)
        self.assertEqual(project["task"]["data"]["prompt"],
                         "[verbatim in task.original_prompt]")
        self.assertEqual(project["previous_chat"]["history"]["messages"]["old-user"]["content"],
                         "[verbatim in task.original_prompt]")
        self.assertEqual(project["previous_chat"]["history"]["messages"]["old-assistant"]["content"],
                         "Unique earlier checkpoint")

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
    def test_dot_first_before_legacy_model_and_relay_guard(self):
        source=(ROOT/"open_webui/utils/automations.py").read_text()
        start=source.index("async def _execute_automation_once(")
        body=source[start:]
        self.assertLess(body.index("from open_webui.utils.abitdot_primary_runner import run_primary_dot"),
                        body.index("from open_webui.utils.abitdot_failover import owns_automation"))
        self.assertLess(body.index("from open_webui.utils.abitdot_primary_runner import run_primary_dot"),
                        body.index("model_id = automation.data['model_id']"))
        self.assertIn("await _record_run(automation.id, 'success'",body)
        self.assertIn("await _record_run(automation.id, 'error'",body)
        self.assertIn("build_saved_selected_context",body)


class RunnerHookTests(unittest.IsolatedAsyncioTestCase):
    """Execute the actual separate Linux primary runner with isolated models."""

    async def asyncSetUp(self):
        import os,sys,logging
        from types import ModuleType
        self.files=tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.folder=SimpleNamespace(model_dump=lambda:{
            "id":"folder-1","user_id":"owner-1","name":"InterMarket Group",
            "data":{"system_prompt":"Actual saved instructions"},
            "meta":{"chatgpt_project_id":"g-p-00001111222233334444555566667777"},
        })
        self.chat=SimpleNamespace(id="chat-1",chat={
            "meta":{"automation_id":"automation-1"},
            "history":{"messages":{"prev":{"role":"assistant","content":"Earlier checkpoint"}}},
        })
        self.automation=SimpleNamespace(
            id="automation-1",user_id="owner-1",name="InterMarket coordinator",
            folder_id="folder-1",
            data={"prompt":"Exact original prompt",
                  "fallback_model_id":"codex-cli-linux-dev",
                  "model_id":"omniroute.abit-chatgpt-relay/project-management"},
            meta={"erp_task":"TASK-2026-05824"},
        )
        self.results=AsyncMock()
        self.folder_lookup=AsyncMock(return_value=self.folder)
        self.recent_chat=AsyncMock(return_value=self.chat)
        self.relay_sender=AsyncMock(return_value={"status":"outcome_unknown"})
        self.app=SimpleNamespace(state=SimpleNamespace(ABIT_DOT_PRIMARY_SEND=None))

        source=(ROOT/"open_webui/utils/abitdot_primary_runner.py").read_text()
        tree=ast.parse(source)
        function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                      and n.name=="run_primary_dot")
        self.ns={
            "os":os,"Path":Path,"uuid4":__import__("uuid").uuid4,
            "log":logging.getLogger("test-dot-primary"),
            "Folders":SimpleNamespace(get_folder_by_id_and_user_id=self.folder_lookup),
            "_recent_owned_chat":self.recent_chat,
            "primary_dot_for_owner":dot.primary_dot_for_owner,
            "build_project_envelope":dot.build_project_envelope,
            "deliver":dot.deliver,"send_via_existing_relay":self.relay_sender,
        }
        exec(compile(ast.Module(body=[function],type_ignores=[]),
                     "<source-dot-primary-runner>","exec"),self.ns)

        automations_stub=ModuleType("open_webui.utils.automations")
        automations_stub._record_run=self.results
        automations_stub._with_erp_task_context=lambda a,p:p
        task_stub=ModuleType("open_webui.utils.task")
        task_stub.prompt_template=AsyncMock(return_value="Full original expanded prompt")
        env_stub=ModuleType("open_webui.env")
        env_stub.DATA_DIR=self.files.name
        self.stubs={
            "open_webui.utils.automations":automations_stub,
            "open_webui.utils.task":task_stub,
            "open_webui.env":env_stub,
        }
        self.env={
            "ABIT_DOT_PRIMARY_ENABLED":"true",
            "ABIT_CHATGPT_DOT_URL":DOT_URL,
            "ABIT_CHATGPT_DOT_OWNER_ID":"owner-1",
            "ABIT_DOT_PRIMARY_CANARY_AUTOMATION_ID":"automation-1",
        }

    async def execute(self, sender=None):
        import os,sys
        with patch.dict(os.environ,self.env),patch.dict(sys.modules,self.stubs):
            self.app.state.ABIT_DOT_PRIMARY_SEND=sender
            return await self.ns["run_primary_dot"](
                self.app,self.automation,SimpleNamespace(id="owner-1"),
                scheduled_run_id=None,
            )

    async def test_verified_relay_receipt_records_delegated_not_completed(self):
        async def accepted(message):
            self.assertEqual(message["related_project"]["name"],"InterMarket Group")
            self.assertEqual(message["task"]["original_prompt"],"Full original expanded prompt")
            self.assertEqual(message["previous_chat"]["meta"]["automation_id"],"automation-1")
            return {
                "owner_id":message["owner_id"],"automation_id":message["automation_id"],
                "run_id":message["run_id"],"dot_id":message["dot_id"],
                "idempotency_key":message["idempotency_key"],
                "status":"accepted","receipt_id":"verified-relay-reply-001",
                "dot_turn_result":"A verified Dot answer.",
            }
        self.assertTrue(await self.execute(accepted))
        self.assertEqual(self.results.await_args.args[:2],("automation-1","delegated"))
        self.assertEqual(self.results.await_args.kwargs["chat_id"],"chat-1")

    async def test_default_authenticated_sender_and_unknown_are_not_replayed(self):
        self.assertTrue(await self.execute())
        self.assertEqual(self.relay_sender.await_count,1)
        self.assertEqual(self.results.await_args.args[:2],("automation-1","deferred"))
        self.assertIn("unknown",self.results.await_args.kwargs["error"])
        self.assertEqual(self.results.await_args.kwargs["chat_id"],"chat-1")
        self.results.reset_mock()
        self.assertTrue(await self.execute())
        self.assertEqual(self.relay_sender.await_count,1)
        self.assertEqual(self.results.await_args.args[:2],("automation-1","deferred"))

    async def test_canary_prevents_other_agents_from_disruption(self):
        self.automation.id="different-automation"
        self.assertFalse(await self.execute())
        self.assertEqual(self.folder_lookup.await_count,0)
        self.assertEqual(self.results.await_count,0)
        self.assertEqual(self.relay_sender.await_count,0)

    async def test_different_account_does_not_invoke_dot(self):
        self.automation.user_id="other-owner"
        self.assertFalse(await self.execute())
        self.assertEqual(self.folder_lookup.await_count,0)
        self.assertEqual(self.results.await_count,0)

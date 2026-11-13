"""Actual local HTTP adapter tests (never contact ChatGPT or live ABIT.AI)."""
from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
def load(name, rel):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / rel
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

primary = load("dot_contract_isolated", "open_webui/utils/abitdot_primary.py")
adapter = load("dot_relay_isolated", "open_webui/utils/abitdot_primary_relay.py")
DOT_ID = "65f7e516-abc8-4c3f-9ffa-e8afca9cfaed~6ac25fb93f1c8192931d7692e8dc9a0e"
DOT_URL = "https://chatgpt.com/dots/" + DOT_ID
ACCOUNT = "a" * 64


def envelope():
    return primary.build_project_envelope(
        owner="owner-for-test", dot_id=DOT_ID, run_id="scheduled-42",
        automation={
            "id": "agent-for-test", "user_id": "owner-for-test",
            "name": "InterMarket execution coordinator",
            "data": {
                "prompt": "Execute original prompt from the project, PROJ-0196. "
                          + "Implement and test. " * 155,
                "fallback_model_id": "codex-cli-linux-dev",
                "password": "do-not-send-password",
            },
            "meta": {"erp_task": "TASK-2026-05824"},
        },
        project={
            "id": "folder-1", "user_id": "owner-for-test",
            "name": "InterMarket SBODesk",
            "data": {"system_prompt": "Use the authorized Linux project and ERPNext"},
            "meta": {"chatgpt_project_id": "g-p-00001111222233334444555566667777"},
        },
        previous_chat={
            "user_id": "owner-for-test",
            "meta": {"automation_id": "agent-for-test"},
            "history": {"messages": {"previous": {
                "role": "assistant", "content": "Earlier checkpoint"
            }}},
        },
    )


class RelayAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.mode = "good"
        self.api = web.Application()
        self.api.router.add_post("/dot/handoff", self.answer)
        self.runner = web.AppRunner(self.api)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        sock = self.site._server.sockets[0]
        self.url = "http://127.0.0.1:" + str(sock.getsockname()[1])
        self.addAsyncCleanup(self.runner.cleanup)
        fake_module = types.ModuleType("open_webui.utils.abitdot_primary")
        fake_module.primary_dot_for_owner = primary.primary_dot_for_owner
        self.patches = [
            patch.dict(sys.modules, {"open_webui.utils.abitdot_primary": fake_module}),
            patch.dict(os.environ, {
                "ABIT_DOT_PRIMARY_ENABLED": "true",
                "ABIT_CHATGPT_DOT_OWNER_ID": "owner-for-test",
                "ABIT_CHATGPT_DOT_URL": DOT_URL,
            }),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def answer(self, request):
        self.calls.append(await request.json())
        if self.mode == "no-send":
            return web.json_response({
                "ok": False, "safe_to_fallback": True,
                "code": "DOT_PRE_SUBMIT_FAILED"
            }, status=424)
        if self.mode == "uncertain":
            return web.json_response({
                "ok": False, "safe_to_fallback": False,
                "code": "DOT_OUTCOME_UNCERTAIN"
            }, status=409)
        if self.mode == "wrong-account":
            return web.json_response({
                "dot_handoff": True, "account_id": "b"*64,
                "response": "It was another user's session."
            })
        if self.mode == "redirect":
            raise web.HTTPFound(location="https://example.invalid/")
        return web.json_response({
            "dot_handoff": True, "account_id": ACCOUNT,
            "response": "I have checked the project and recorded the next eligible task.",
            "conversation_url": "https://chatgpt.com/c/saved-owner-dot-conversation",
        })

    async def call(self, env=None):
        return await adapter.send_via_existing_relay(
            env or envelope(), base_url=self.url,
            token="synthetic-auth-token", account_id=ACCOUNT, timeout_seconds=8,
        )

    async def test_existing_owner_auth_relay_carries_full_context(self):
        p = envelope()
        answer = await self.call(p)
        self.assertEqual(answer["status"], "accepted")
        self.assertEqual(answer["dot_id"], DOT_ID)
        self.assertEqual(answer["owner_id"], "owner-for-test")
        self.assertEqual(answer["run_id"], "scheduled-42")
        self.assertEqual(answer["dot_conversation_url"],
                         "https://chatgpt.com/c/saved-owner-dot-conversation")
        self.assertEqual(len(self.calls),1)
        sent=self.calls[0]
        self.assertEqual(sent["channel"],"abitdot")
        self.assertTrue(sent["job_id"].startswith("aibit_"))
        self.assertEqual(len(sent["job_id"]),54)
        self.assertEqual(sent["account_id"],ACCOUNT)
        self.assertIn("InterMarket SBODesk",sent["message"])
        self.assertIn("TASK-2026-05824",sent["message"])
        self.assertIn("Use the authorized Linux project",sent["message"])
        self.assertIn("Earlier checkpoint",sent["message"])
        self.assertIn("Execute original prompt",sent["message"])
        self.assertNotIn("do-not-send-password",sent["message"])
        with tempfile.TemporaryDirectory() as tmp:
            with primary.ledger(Path(tmp)/"dot.sqlite") as db:
                won,_=primary.claim(db,p)
                self.assertTrue(won)
                saved=primary.record_receipt(db,p,answer)
                self.assertEqual(saved["state"],"delegated")
                reply=db.execute("SELECT response_text,conversation_url FROM dot_primary_runs").fetchone()
                self.assertIn("recorded the next eligible task",reply["response_text"])
                self.assertEqual(reply["conversation_url"],answer["dot_conversation_url"])

    async def test_token_and_account_are_only_sent_to_local_relay(self):
        await self.call()
        self.assertEqual(len(self.calls),1)
        self.assertIn("Original project, instructions, prompt",
                      self.calls[0]["message"])
        self.assertNotIn("synthetic-auth-token",self.calls[0]["message"])
        self.assertTrue(self.calls[0]["job_id"].startswith("aibit_"))

    async def test_explicit_pre_submit_rejection_is_not_a_dot_completion(self):
        self.mode="no-send"
        answer=await self.call()
        self.assertEqual(answer["status"],"rejected_before_submit")
        self.assertNotIn("receipt_id",answer)

    async def test_ambiguous_send_does_not_auto_retry(self):
        self.mode="uncertain"
        answer=await self.call()
        self.assertEqual(answer["status"],"outcome_unknown")
        self.assertEqual(len(self.calls),1)

    async def test_wrong_account_not_accepted(self):
        self.mode="wrong-account"
        answer=await self.call()
        self.assertEqual(answer["status"],"outcome_unknown")

    async def test_redirect_not_followed(self):
        self.mode="redirect"
        answer=await self.call()
        self.assertEqual(answer["status"],"outcome_unknown")

    async def test_owner_mismatch_prevents_network_request(self):
        p=envelope()
        p["owner_id"]="other-owner"
        with self.assertRaises(adapter.RelayPreflightError):
            await self.call(p)
        self.assertEqual(len(self.calls),0)

    async def test_relay_host_cannot_be_rebound_to_external_service(self):
        with self.assertRaises(adapter.RelayPreflightError):
            await adapter.send_via_existing_relay(
                envelope(), base_url="https://malicious.example/",
                token="synthetic", account_id=ACCOUNT,
            )
        self.assertEqual(len(self.calls),0)

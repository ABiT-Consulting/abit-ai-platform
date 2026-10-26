"""Isolated relay-browser sandbox authorization tests; never contact the live relay."""
import ast
import copy
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import SecretStr

from test_chatgpt_relay_optional_project import TestOptionalProject, ROUTER, ACCOUNT, PROJECT, CONV


class TestRelaySandbox(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture = TestOptionalProject()
        await fixture.asyncSetUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.ns = fixture.ns
        self.sent = []
        self.user = SimpleNamespace(id="u1", role="admin")
        self.ns["ACCOUNT_ID"] = re.compile(r"^[a-f0-9]{64}$")
        self.ns["_credentials"] = lambda token_file=None: ("test-token", "fingerprint")
        async def relay(method, endpoint, token, payload, timeout):
            self.sent.append((method, endpoint, token, payload, timeout))
            return {"ok": True, "image": "data:image/jpeg;base64,test"}
        self.ns["_relay"] = relay
        wanted = {"_browser_scope", "_browser_command", "open_relay_browser",
                  "view_relay_browser", "input_relay_browser"}
        source = ast.parse(ROUTER.read_text(encoding="utf-8-sig"))
        nodes = []
        for original in source.body:
            if isinstance(original, (ast.FunctionDef, ast.AsyncFunctionDef)) and original.name in wanted:
                node = copy.deepcopy(original)
                node.decorator_list = []
                nodes.append(node)
        self.ns.update({"Depends": lambda f: None, "get_verified_user": lambda: None, "RelayBrowserInput": SimpleNamespace, "RelayBrowserRequest": SimpleNamespace})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTER), "exec"), self.ns)

    def job(self, *, owner="u1", account=ACCOUNT, chat_id="chat1", project="", url=None):
        with self.fixture.db() as db:
            db.execute("""INSERT INTO jobs
                (owner,account,job_id,chat_id,project_id,message_hash,status,
                 conversation_url,created_at,updated_at) VALUES
                (?,?,?,?,?,?,?,?,?,?)""",
                (owner, account, owner + str(time.time_ns()), chat_id, project,
                 "hash", "running", url, int(time.time()), int(time.time())))
            db.commit()

    async def test_verified_chat_binding_only(self):
        self.fixture.bind("")
        result = await self.ns["_browser_scope"](self.user, "chat1")
        self.assertEqual(result, {
            "conversation_url": f"https://chatgpt.com/c/{CONV}", "account_id": ACCOUNT
        })
        self.assertEqual(self.sent, [])  # No blocked /account relay request.

    async def test_active_job_url_is_usable_while_relay_is_running(self):
        self.job(url=f"https://chatgpt.com/c/{CONV}")
        scope = await self.ns["_browser_scope"](self.user, "chat1")
        self.assertEqual(scope["account_id"], ACCOUNT)
        await self.ns["view_relay_browser"](SimpleNamespace(chat_id="chat1"), self.user)
        self.assertEqual(self.sent[0][1], "/browser/view")
        self.assertEqual(self.sent[0][3]["conversation_url"], f"https://chatgpt.com/c/{CONV}")
        self.assertEqual(self.sent[0][3]["account_id"], ACCOUNT)
        self.assertNotIn("cookie", self.sent[0][3])

    async def test_browser_open_does_not_take_untrusted_url(self):
        self.fixture.bind(PROJECT)
        form = SimpleNamespace(chat_id="chat1", conversation_url="https://evil.test/")
        await self.ns["open_relay_browser"](form, self.user)
        self.assertEqual(self.sent[-1][3]["conversation_url"],
            f"https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}")

    async def test_text_is_forwarded_but_secret_is_not_retained(self):
        self.fixture.bind("")
        form = SimpleNamespace(
            chat_id="chat1", kind="text", text=SecretStr("correct the prompt"),
            model_dump=lambda exclude: {"kind": "text", "chat_id": "untrusted"}
        )
        await self.ns["input_relay_browser"](form, self.user)
        self.assertEqual(self.sent[-1][3]["text"], "correct the prompt")
        self.assertEqual(self.sent[-1][3]["chat_id"], "chat1")
        self.assertEqual(self.sent[-1][3]["owner"], "u1")

    async def test_other_user_and_unknown_chat_denied(self):
        self.job(owner="u2", url=f"https://chatgpt.com/c/{CONV}")
        for user, chat in [(self.user, "other"), (SimpleNamespace(id="u2"), "chat1"),
                           (self.user, "")]:
            with self.assertRaises(HTTPException) as raised:
                await self.ns["_browser_scope"](user, chat)
            self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(self.sent, [])

    async def test_unverified_urls_and_accounts_denied(self):
        for account, project, url in [
            (ACCOUNT, "", "https://chatgpt.com/"),
            (ACCOUNT, "", "https://chatgpt.com/g/" + PROJECT + "/c/" + CONV),
            ("malformed", "", f"https://chatgpt.com/c/{CONV}"),
        ]:
            self.job(account=account, project=project, url=url)
        with self.assertRaises(HTTPException) as raised:
            await self.ns["_browser_scope"](self.user, "chat1")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.sent, [])

if __name__ == "__main__":
    unittest.main()

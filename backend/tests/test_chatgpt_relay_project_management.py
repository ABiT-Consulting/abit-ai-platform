"""Isolated tests for AIBIT -> ChatGPT project creation, without browser/live DB."""
import ast
import asyncio
import re
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException, Depends
from pydantic import BaseModel

SOURCE = Path(__file__).resolve().parents[1] / "open_webui/routers/chatgpt_relay.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
wanted = {"ProjectCreateForm", "create_project"}
nodes = []
for node in tree.body:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
        node.decorator_list = []
        nodes.append(node)
compiled = compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec")
PID = "g-p-aibittest1234567"
ACCOUNT = "a" * 64
MODEL = "omniroute.abit-chatgpt-relay/project-management"


class CreateProjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sqlite = Path(self.tmp.name) / "mapping.sqlite"
        self.calls = []
        self.existing = []
        self.created = False
        self.fail_instructions = False
        self.folder = SimpleNamespace(
            id="folder-a", meta={"some_preexisting_setting": "keep"},
            data={"files": ["existing"], "model_ids": [MODEL]}
        )

        @contextmanager
        def db():
            conn = sqlite3.connect(str(self.sqlite))
            conn.row_factory = sqlite3.Row
            conn.execute(
                "CREATE TABLE IF NOT EXISTS projects "
                "(owner TEXT, account TEXT, project_id TEXT, folder_id TEXT)"
            )
            try:
                yield conn
            finally:
                conn.close()

        async def authorized(user):
            if user.role != "admin":
                raise HTTPException(403, "Forbidden")
            return user

        async def account_context():
            return "fake-token", ACCOUNT, False

        async def fake_relay(method, route, token, payload=None, timeout=30):
            self.calls.append((method, route))
            if route == "/account":
                return {"account_id": ACCOUNT}
            if route == "/projects" and method == "GET":
                return {"account_id": ACCOUNT, "complete": True,
                        "projects": [{"id": PID, "name": row} for row in self.existing]}
            if route == "/projects/create":
                self.created = True
                return {"ok": True, "project": {"id": PID, "name": payload["name"]}}
            if route == "/projects/instructions":
                self.assertEqual(payload["instructions"], "Instructions for ChatGPT")
                return {"success": not self.fail_instructions}
            raise AssertionError((method, route))

        async def sync(user):
            self.calls.append(("LOCAL", "sync"))
            with db() as conn:
                conn.execute("INSERT INTO projects VALUES(?,?,?,?)",
                             (user.id, ACCOUNT, PID, self.folder.id))
                conn.commit()
            return {"synced": 1}

        async def get_folder(folder_id, user_id):
            return self.folder

        async def update_folder(folder_id, user_id, form):
            self.folder.data = {**self.folder.data, **form.data}
            return self.folder

        self.ns = {
            "__builtins__": __builtins__,
            "BaseModel": BaseModel,
            "HTTPException": HTTPException, "Depends": Depends, "get_verified_user": lambda: None,
            "PROJECT_ID": re.compile(r"^g-p-[A-Za-z0-9_-]{5,190}$"),
            "MODEL_ID": MODEL,
            "_authorized": authorized,
            "_account_context": account_context,
            "_relay": fake_relay,
            "_verify_account": lambda p: p["account_id"],
            "sync_projects": sync,
            "_db": db,
            "Folders": SimpleNamespace(
                get_folder_by_id_and_user_id=get_folder,
                update_folder_by_id_and_user_id=update_folder),
            "FolderUpdateForm": lambda **kwargs: SimpleNamespace(**kwargs),
        }
        exec(compiled, self.ns)

    async def test_create_and_instructions_synced_in_both_places(self):
        result = await self.ns["create_project"](
            self.ns["ProjectCreateForm"](name=" New AIBIT Project ",
                                         instructions="Instructions for ChatGPT"),
            SimpleNamespace(id="admin", role="admin"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["folder_id"], "folder-a")
        self.assertIn(("POST", "/projects/create"), self.calls)
        self.assertIn(("POST", "/projects/instructions"), self.calls)
        self.assertIn(("LOCAL", "sync"), self.calls)
        self.assertEqual(self.folder.data["model_ids"], [MODEL])
        self.assertEqual(self.folder.data["system_prompt"], "Instructions for ChatGPT")
        self.assertEqual(self.folder.data["files"], ["existing"])

    async def test_existing_project_name_never_creates_duplicate(self):
        self.existing = ["Example"]
        with self.assertRaises(HTTPException) as ctx:
            await self.ns["create_project"](
                self.ns["ProjectCreateForm"](name=" example "),
                SimpleNamespace(id="admin", role="admin"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertNotIn(("POST", "/projects/create"), self.calls)

    async def test_upstream_instruction_failure_warns_not_to_retry(self):
        self.fail_instructions = True
        with self.assertRaises(HTTPException) as ctx:
            await self.ns["create_project"](
                self.ns["ProjectCreateForm"](
                    name="Different project", instructions="Instructions for ChatGPT"),
                SimpleNamespace(id="admin", role="admin"))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("do not create", str(ctx.exception.detail).lower())
        self.assertEqual(self.calls.count(("POST", "/projects/create")), 1)
        self.assertNotIn(("LOCAL", "sync"), self.calls)

    async def test_reject_invalid_or_oversized_input_before_browser(self):
        with self.assertRaises(HTTPException) as ctx:
            await self.ns["create_project"](
                self.ns["ProjectCreateForm"](name="   "),
                SimpleNamespace(id="admin", role="admin"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()

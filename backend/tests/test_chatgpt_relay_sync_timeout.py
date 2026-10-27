"""Isolated regression checks for Cloudflare-safe ChatGPT catalog sync."""
import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parents[1] / "open_webui/routers/chatgpt_relay.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in ("_relay", "sync_projects")]
compiled = compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec")


class TimedOutSession:
    async def __aenter__(self):
        raise asyncio.TimeoutError()

    async def __aexit__(self, *args):
        return False


class CatalogTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

        async def relay(method, endpoint, token, timeout):
            self.calls.append((method, endpoint, timeout))
            return {"ok": True, "complete": True, "account_id": "a" * 64,
                    "projects": [{"id": "g-p-testproject", "name": "Test Project"}]}

        async def import_catalog(user, account, projects):
            return {"synced": len(projects), "account_id": account}

        self.ns = {
            "__builtins__": __builtins__, "asyncio": asyncio,
            "aiohttp": SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs,
                                       ClientSession=lambda **kwargs: TimedOutSession(),
                                       ClientError=OSError),
            "HTTPException": HTTPException, "CATALOG_URL": "http://127.0.0.1:8808",
            "router": SimpleNamespace(post=lambda path: lambda fn: fn),
            "Depends": lambda fn: None, "get_verified_user": lambda: None,
            "_authorized": lambda user: asyncio.sleep(0),
            "_credentials": lambda: ("unused", "unused"),
            "_relay": relay,
            "_verify_account": lambda data: data["account_id"],
            "PROJECT_ID": __import__("re").compile(r"^g-p-[A-Za-z0-9_-]{5,190}$"),
            "_import_project_catalog": import_catalog,
        }
        exec(compiled, self.ns)
        self.ns["_relay"] = relay

    async def test_catalog_timeout_returns_retryable_error_before_proxy_deadline(self):
        real_relay = self.ns["_relay"]
        # Re-execute with a session that fails instead of using the sync fake.
        isolated = dict(self.ns)
        isolated.pop("_relay")
        exec(compiled, isolated)
        with self.assertRaises(HTTPException) as raised:
            await isolated["_relay"]("GET", "/projects", "unused")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("No projects were changed", raised.exception.detail)
        with self.assertRaises(HTTPException) as raised:
            await isolated["_relay"]("POST", "/send", "unused")
        self.assertEqual(raised.exception.status_code, 504)
        self.assertIsNotNone(real_relay)

    async def test_sync_uses_bounded_deadline_and_validates_catalog(self):
        result = await self.ns["sync_projects"](SimpleNamespace(id="admin-test", role="admin"))
        self.assertEqual(result["synced"], 1)
        self.assertEqual(self.calls, [("GET", "/projects", 65)])


if __name__ == "__main__":
    unittest.main()

"""Isolated MCP protocol/security/queue tests. No live DB, bot, or callback traffic.

Run with Python from the source checkout. A sibling telegram_mcp.py is accepted
for draft review; otherwise the canonical backend source file is loaded.
"""
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


# The protocol tests also run in a minimal review environment. The application's
# separate integration suite exercises real FastAPI routing and real OAuth.
try:
    from fastapi import HTTPException
except ModuleNotFoundError:
    fake_fastapi = types.ModuleType("fastapi")
    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None):
            self.status_code, self.detail, self.headers = status_code, detail, headers
    class APIRouter:
        def post(self, _path):
            return lambda func: func
    class Response:
        def __init__(self, content=None, status_code=200, headers=None):
            self.body = b"" if content is None else content
            self.status_code, self.headers = status_code, headers or {}
    class JSONResponse(Response):
        def __init__(self, content, status_code=200, headers=None):
            super().__init__(json.dumps(content).encode(), status_code, headers)
    fake_fastapi.APIRouter, fake_fastapi.HTTPException, fake_fastapi.Request = APIRouter, HTTPException, object
    fake_responses = types.ModuleType("fastapi.responses")
    fake_responses.JSONResponse, fake_responses.Response = JSONResponse, Response
    sys.modules["fastapi"], sys.modules["fastapi.responses"] = fake_fastapi, fake_responses

for package in ("open_webui", "open_webui.routers", "open_webui.utils"):
    module = types.ModuleType(package)
    module.__path__ = []
    sys.modules[package] = module

bridge = types.ModuleType("open_webui.utils.telegram_bridge")
oauth = types.ModuleType("open_webui.routers.telegram_oauth")
async def _unconfigured_auth(*args, **kwargs):
    raise AssertionError("Test authentication was not configured")
oauth.authenticate_mcp = _unconfigured_auth
sys.modules[bridge.__name__], sys.modules[oauth.__name__] = bridge, oauth

HERE = Path(__file__).resolve()
SOURCE = HERE.with_name("telegram_mcp.py")
if not SOURCE.exists():
    SOURCE = HERE.parent.parent / "backend" / "open_webui" / "routers" / "telegram_mcp.py"
spec = importlib.util.spec_from_file_location("telegram_mcp_under_test", SOURCE)
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


class FakeRequest:
    def __init__(self, value, headers=None, raw=None):
        self.raw = json.dumps(value).encode() if raw is None else raw
        self.headers = {key.lower(): val for key, val in (headers or {}).items()}
        self.state = types.SimpleNamespace()
    async def stream(self):
        yield self.raw


class TelegramMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="abit_telegram_mcp_test_")
        self.db_path = str(Path(self.temp.name) / "isolated.sqlite")
        self.clock = 1792832400.0
        self.users = {"owner-a", "owner-b"}
        self.grants = set(self.users)
        self.grant_ids = {"owner-a": "grant-a", "owner-b": "grant-b"}
        self.grant_records = {
            grant_id: {"owner": owner, "valid": True, "scopes": {"telegram:read", "telegram:reply"}}
            for owner, grant_id in self.grant_ids.items()
        }
        self.bindings = {"owner-a": {"id": "binding-a"}, "owner-b": {"id": "binding-b"}}
        self.calls, self.auth_calls = [], []
        self.delivery_status = 200
        self.callback_error = None
        self.secret = "whsec_" + base64.b64encode(b"a" * 32).decode()
        bridge.db = self.db
        bridge.now = lambda: self.clock
        bridge.encrypt_secret = lambda text: "encrypted:" + base64.b64encode(text.encode()).decode()
        bridge.decrypt_secret = lambda cipher: base64.b64decode(cipher.removeprefix("encrypted:")).decode()
        bridge.get_binding = lambda owner: self.bindings.get(owner)
        bridge.get_config = lambda: {"public_url": "https://app.example.com"}
        bridge.active_user = self.active_user
        bridge.public_status = self.public_status
        bridge.list_messages = self.list_messages
        bridge.reply_to_message = self.reply
        oauth.owner_connected = self.owner_connected
        mcp.authenticate_mcp = self.authenticate
        self.real_callback_post = mcp._callback_post
        self.post_patch = patch.object(mcp, "_callback_post", self.callback)
        self.post_patch.start()
        with self.db() as conn:
            conn.execute("""CREATE TABLE tg_messages (
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,binding_id TEXT NOT NULL,
                telegram_message_id INTEGER,text TEXT,created_at REAL,status TEXT,
                response_text TEXT,error TEXT)""")

    def tearDown(self):
        self.post_patch.stop()
        self.temp.cleanup()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    async def active_user(self, owner):
        return types.SimpleNamespace(id=owner) if owner in self.users else None

    async def public_status(self, owner):
        return {"linked": owner in self.bindings, "subscription_active": mcp.owner_subscribed(owner)}

    def owner_connected(self, owner, grant_id=None, required_scope=None):
        if owner not in self.grants:
            return False
        candidates = [self.grant_records.get(grant_id)] if grant_id else self.grant_records.values()
        return any(record and record["owner"] == owner and record["valid"]
                   and (not required_scope or required_scope in record["scopes"]) for record in candidates)

    async def authenticate(self, request, required_scope=None):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.grants:
            raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
        self.auth_calls.append((token, required_scope))
        scopes = request.headers.get("x-test-scopes", "telegram:read telegram:reply").split()
        if required_scope not in scopes:
            raise HTTPException(status_code=403, detail="Insufficient scope")
        request.state.telegram_oauth_grant_id = self.grant_ids[token]
        return types.SimpleNamespace(id=token)

    def list_messages(self, owner, limit=30, before=None):
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM tg_messages WHERE owner=? ORDER BY rowid DESC LIMIT ?", (owner, limit))]

    async def reply(self, owner, message_id, text):
        with self.db() as conn:
            row = conn.execute("SELECT * FROM tg_messages WHERE id=? AND owner=?", (message_id, owner)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Unavailable")
            if row["response_text"] and row["response_text"] != text:
                raise HTTPException(status_code=409, detail="Already replied")
            conn.execute("UPDATE tg_messages SET response_text=?,status='reply_queued' WHERE id=? AND owner=?", (text, message_id, owner))
        return {"message_id": message_id, "status": "reply_queued"}

    async def callback(self, url, body, headers):
        payload = json.loads(body)
        self.calls.append((url, payload, headers, body))
        await asyncio.sleep(0)
        if payload.get("type") == "verification":
            return 200, json.dumps({"challenge": payload["challenge"]}).encode(), None
        if self.callback_error:
            raise mcp.CallbackError(self.callback_error)
        return self.delivery_status, b"{}", 5 if self.delivery_status == 429 else None

    def message(self, message_id, owner="owner-a", binding_id=None):
        with self.db() as conn:
            conn.execute("""INSERT INTO tg_messages
                (id,owner,binding_id,telegram_message_id,text,created_at,status)
                VALUES (?,?,?,?,?,?,'received')""",
                (message_id, owner, binding_id or self.bindings[owner]["id"], 1, "Hello " + message_id, self.clock))

    def params(self, url="https://hooks.example.com/callback-a", secret=None, ttl=3600000):
        return {"name": mcp.EVENT_NAME, "arguments": {},
                "delivery": {"mode": "webhook", "url": url, "secret": secret or self.secret},
                "cursor": None, "ttlMs": ttl}

    async def subscribe(self, owner="owner-a", **kwargs):
        return await mcp._subscribe(owner, self.params(**kwargs), self.grant_ids[owner])

    def events(self):
        return [item for item in self.calls if "eventId" in item[1]]

    async def rpc(self, method, params=None, owner="owner-a", headers=None):
        params = dict(params or {})
        params.setdefault("_meta", {"io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                                    "io.modelcontextprotocol/clientCapabilities": {}})
        supplied = {"authorization": "Bearer " + owner, "mcp-protocol-version": mcp.PROTOCOL_VERSION,
                    "mcp-method": method}
        if method == "tools/call":
            supplied["mcp-name"] = params.get("name", "")
        supplied.update(headers or {})
        response = await mcp.mcp_endpoint(FakeRequest({"jsonrpc": "2.0", "id": 9, "method": method, "params": params}, supplied))
        return response.status_code, json.loads(response.body)

    def delivery_rows(self):
        with mcp._db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM tg_event_deliveries ORDER BY rowid")]

    async def test_discovery_and_event_catalog_are_modern(self):
        status, body = await self.rpc("server/discover")
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["resultType"], "complete")
        self.assertIn("events", body["result"]["capabilities"])
        _, body = await self.rpc("events/list")
        event = body["result"]["events"][0]
        self.assertEqual(event["name"], mcp.EVENT_NAME)
        self.assertFalse(event["inputSchema"]["additionalProperties"])

    async def test_mismatched_protocol_or_method_is_rejected(self):
        status, body = await self.rpc("events/list", headers={"mcp-method": "tools/list"})
        self.assertEqual((status, body["error"]["code"]), (400, -32020))
        status, body = await self.rpc("events/list", headers={"mcp-protocol-version": "unknown"})
        self.assertEqual((status, body["error"]["code"]), (400, -32020))

    async def test_unsupported_version_and_missing_capabilities(self):
        params = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2030-01-01",
                            "io.modelcontextprotocol/clientCapabilities": {}}}
        status, body = await self.rpc("ping", params, headers={"mcp-protocol-version": "2030-01-01"})
        self.assertEqual((status, body["error"]["code"]), (400, -32022))
        status, body = await self.rpc("ping", {"_meta": {"io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION}})
        self.assertEqual((status, body["error"]["code"]), (400, -32602))

    async def test_unknown_method_and_origin(self):
        status, body = await self.rpc("unknown")
        self.assertEqual((status, body["error"]["code"]), (404, -32601))
        status, _ = await self.rpc("ping", headers={"origin": "https://untrusted.example"})
        self.assertEqual(status, 403)

    async def test_oversized_body_is_rejected(self):
        request = FakeRequest({}, raw=b" " * (mcp.MAX_BODY_BYTES + 1))
        response = await mcp.mcp_endpoint(request)
        self.assertEqual(response.status_code, 413)

    async def test_oauth_challenge_and_scope_are_preserved(self):
        with self.assertRaises(HTTPException) as caught:
            await self.rpc("tools/list", owner="invalid")
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("WWW-Authenticate", caught.exception.headers)
        with self.assertRaises(HTTPException) as caught:
            await self.rpc("tools/call", {"name": "reply_to_telegram_message", "arguments": {"message_id": "m", "text": "answer"}},
                           headers={"x-test-scopes": "telegram:read"})
        self.assertEqual(caught.exception.status_code, 403)

    async def test_tool_schema_scopes_and_invalid_filters(self):
        _, result = await self.rpc("tools/list")
        scopes = {tool["name"]: tool["securitySchemes"][0]["scopes"] for tool in result["result"]["tools"]}
        self.assertEqual(scopes["reply_to_telegram_message"], ["telegram:reply"])
        status, body = await self.rpc("events/subscribe", {**self.params(), "arguments": {"owner": "owner-b"}})
        self.assertEqual((status, body["error"]["code"]), (400, -32602))
        self.assertEqual(self.calls, [])

    async def test_read_and_reply_are_owner_scoped(self):
        self.message("private-b", "owner-b")
        status, body = await self.rpc("tools/call", {"name": "list_telegram_messages", "arguments": {}})
        self.assertEqual(body["result"]["structuredContent"]["messages"], [])
        status, body = await self.rpc("tools/call", {"name": "reply_to_telegram_message", "arguments": {"message_id": "private-b", "text": "answer"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])

    async def test_reply_is_queued_and_repeat_conflict_is_tool_error(self):
        self.message("private-a")
        params = {"name": "reply_to_telegram_message", "arguments": {"message_id": "private-a", "text": "answer"}}
        _, body = await self.rpc("tools/call", params)
        self.assertEqual(body["result"]["structuredContent"]["status"], "reply_queued")
        _, repeat = await self.rpc("tools/call", params)
        self.assertEqual(body["result"]["structuredContent"], repeat["result"]["structuredContent"])
        params["arguments"]["text"] = "different"
        _, body = await self.rpc("tools/call", params)
        self.assertTrue(body["result"]["isError"])

    async def test_subscription_is_verified_persisted_and_idempotent(self):
        first = await self.subscribe()
        second = await self.subscribe()
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(mcp.owner_subscribed("owner-a"))
        with mcp._db() as conn:
            rows = conn.execute("SELECT * FROM tg_subscriptions").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(self.secret, rows[0]["secret_enc"])
        self.assertEqual(first["cursor"], None)

    async def test_another_callback_cannot_silently_replace_active_dot(self):
        await self.subscribe()
        with self.assertRaises(mcp.ProtocolError):
            await self.subscribe(url="https://hooks.example.com/another-chat")
        self.assertEqual(len(self.calls), 1)

    async def test_cross_owner_unsubscribe_cannot_revoke_link(self):
        await self.subscribe()
        params = self.params()
        mcp._unsubscribe("owner-b", params)
        self.assertTrue(mcp.owner_subscribed("owner-a"))
        mcp._unsubscribe("owner-a", params)
        mcp._unsubscribe("owner-a", params)
        self.assertFalse(mcp.owner_subscribed("owner-a"))

    async def test_callback_verification_rejects_wrong_echo_and_redirect(self):
        async def wrong_echo(*args):
            return 200, b'{"challenge":"wrong"}', None
        with patch.object(mcp, "_callback_post", wrong_echo):
            with self.assertRaises(mcp.ProtocolError) as caught:
                await self.subscribe()
        self.assertEqual(caught.exception.code, -32015)
        self.assertFalse(mcp.owner_subscribed("owner-a"))
        async def redirect(*args):
            return 302, b"{}", None
        with patch.object(mcp, "_callback_post", redirect):
            with self.assertRaises(mcp.ProtocolError):
                await self.subscribe()

    async def test_disconnect_during_verification_does_not_reactivate(self):
        async def disconnect(url, body, headers):
            mcp.revoke_subscriptions("owner-a")
            return await self.callback(url, body, headers)
        with patch.object(mcp, "_callback_post", disconnect):
            with self.assertRaises(mcp.ProtocolError):
                await self.subscribe()
        self.assertFalse(mcp.owner_subscribed("owner-a"))

    async def test_invalid_signing_secret_and_ttl_are_rejected(self):
        for secret in ("plain", "whsec_!", "whsec_" + base64.b64encode(b"short").decode()):
            with self.assertRaises(mcp.ProtocolError):
                await self.subscribe(secret=secret)
        for ttl in (True, 0, -1, float("inf"), 10 ** 1000):
            with self.assertRaises(mcp.ProtocolError):
                await self.subscribe(ttl=ttl)
        self.assertEqual(self.calls, [])

    async def test_secret_rotation_dual_signs_exact_body(self):
        await self.subscribe()
        second = "whsec_" + base64.b64encode(b"b" * 32).decode()
        await self.subscribe(secret=second)
        self.message("rotate")
        await mcp.deliver_pending_events()
        _, payload, headers, raw = self.events()[0]
        prefix = (headers["webhook-id"] + "." + headers["webhook-timestamp"] + ".").encode()
        signatures = headers["webhook-signature"].split()
        self.assertEqual(len(signatures), 2)
        for key in (b"a" * 32, b"b" * 32):
            expected = "v1," + base64.b64encode(hmac.new(key, prefix + raw, hashlib.sha256).digest()).decode()
            self.assertIn(expected, signatures)
        self.assertEqual(headers["webhook-id"], payload["eventId"])

    async def test_delivery_and_restart_do_not_repeat_accepted_event(self):
        await self.subscribe()
        self.message("one")
        await mcp.deliver_pending_events()
        await mcp.deliver_pending_events()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.delivery_rows()[0]["status"], "delivered")
        self.assertEqual(self.list_messages("owner-a")[0]["status"], "notified")
        self.assertIsNone(self.list_messages("owner-a")[0]["response_text"])

    async def test_multiple_workers_keep_arrival_order_and_claim_once(self):
        await self.subscribe()
        self.message("z-first")
        self.message("a-second")
        await asyncio.gather(mcp.deliver_pending_events(), mcp.deliver_pending_events())
        self.assertEqual([event[1]["data"]["message_id"] for event in self.events()], ["z-first", "a-second"])

    async def test_already_notified_messages_do_not_starve_new_messages(self):
        await self.subscribe()
        for index in range(55):
            self.message("old-" + str(index))
        mcp._enqueue_events()
        with mcp._db() as conn:
            conn.execute("UPDATE tg_event_deliveries SET status='delivered'")
            conn.execute("UPDATE tg_messages SET status='notified'")
        self.message("new")
        await mcp.deliver_pending_events()
        self.assertEqual([event[1]["data"]["message_id"] for event in self.events()], ["new"])

    async def test_lease_recovery_uses_same_event_id(self):
        await self.subscribe()
        self.message("crashed")
        mcp._enqueue_events()
        claim = mcp._claim_delivery()
        self.clock += 61
        await mcp.deliver_pending_events()
        self.assertEqual(self.events()[0][1]["eventId"], claim["id"])
        self.assertEqual(self.delivery_rows()[0]["attempts"], 2)

    async def test_transient_retry_is_bounded_and_preserves_event_id(self):
        await self.subscribe()
        self.message("retry")
        self.delivery_status = 429
        for _ in range(7):
            await mcp.deliver_pending_events()
            self.clock += 121
        self.assertEqual(len(self.events()), mcp.MAX_DELIVERY_ATTEMPTS)
        self.assertEqual(len({event[1]["eventId"] for event in self.events()}), 1)
        self.assertEqual(self.delivery_rows()[0]["status"], "failed")

    async def test_terminal_410_and_413_are_not_retried(self):
        await self.subscribe()
        for status in (410, 413):
            self.message("terminal-" + str(status))
            self.delivery_status = status
            await mcp.deliver_pending_events()
        self.clock += 3600
        await mcp.deliver_pending_events()
        self.assertEqual(len(self.events()), 2)
        self.assertTrue(all(row["status"] == "failed" for row in self.delivery_rows()))

    async def test_disabled_owner_and_revoked_grant_block_delivery(self):
        await self.subscribe()
        self.message("disabled")
        self.users.remove("owner-a")
        await mcp.deliver_pending_events()
        self.assertEqual(self.events(), [])
        self.users.add("owner-a")
        self.message("revoked-grant")
        self.grants.remove("owner-a")
        await mcp.deliver_pending_events()
        self.assertEqual(self.events(), [])

    async def test_binding_revoked_during_await_blocks_delivery(self):
        await self.subscribe()
        self.message("unlink-race")
        async def revoke_while_loading(owner):
            self.bindings.pop(owner, None)
            return types.SimpleNamespace(id=owner)
        bridge.active_user = revoke_while_loading
        await mcp.deliver_pending_events()
        self.assertEqual(self.events(), [])

    async def test_subscribed_grant_revocation_and_scope_loss_stop_delivery_with_other_grant(self):
        await self.subscribe()
        self.grant_records["second-grant-a"] = {
            "owner": "owner-a", "valid": True, "scopes": {"telegram:read", "telegram:reply"}
        }
        self.grant_records["grant-a"]["valid"] = False
        self.assertTrue(self.owner_connected("owner-a"))
        self.assertFalse(mcp.owner_subscribed("owner-a"))
        self.message("revoked-exact-grant")
        await mcp.deliver_pending_events()
        self.assertEqual(self.events(), [])
        self.grant_records["grant-a"]["valid"] = True
        self.grant_records["grant-a"]["scopes"] = {"telegram:reply"}
        self.assertTrue(self.owner_connected("owner-a", required_scope="telegram:read"))
        self.assertFalse(mcp.owner_subscribed("owner-a"))
        self.message("lost-read-scope")
        await mcp.deliver_pending_events()
        self.assertEqual(self.events(), [])

    async def test_expiry_and_status_before_bot_configuration(self):
        bridge.get_config = lambda: (_ for _ in ()).throw(AssertionError("Do not need bot config"))
        self.assertFalse(mcp.owner_subscribed("owner-a"))
        await self.subscribe(ttl=60000)
        self.clock += 61
        self.message("expired")
        await mcp.deliver_pending_events()
        self.assertFalse(mcp.owner_subscribed("owner-a"))
        self.assertEqual(self.events(), [])

    async def test_callback_addresses_reject_private_mapped_and_idna(self):
        bad = ["http://hooks.example.com/cb", "https://127.0.0.1/cb", "https://[::1]/cb",
               "https://[::ffff:127.0.0.1]/cb", "https://10.0.0.1/cb", "https://169.254.169.254/cb",
               "https://224.0.0.1/cb", "https://user:pass@hooks.example.com/cb",
               "https://hooks.example.com/cb#fragment", "https://１２７.０.０.１/cb"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(mcp.ProtocolError):
                mcp._validate_callback_url(url)
        self.assertTrue(mcp._public_ip("8.8.8.8"))
        self.assertFalse(mcp._public_ip("::ffff:10.0.0.1"))
        self.assertEqual(mcp._validate_callback_url("https://hooks.example.com/cb"), "https://hooks.example.com/cb")

    async def test_resolver_rejects_mixed_public_private_answers(self):
        class Resolver:
            async def resolve(self, *args):
                return [{"host": "8.8.8.8"}, {"host": "10.0.0.1"}]
            async def close(self):
                pass
        with patch.object(mcp.aiohttp.resolver, "DefaultResolver", Resolver):
            resolver = mcp._PublicResolver()
            with self.assertRaises(OSError):
                await resolver.resolve("hooks.example.com", 443)
            await resolver.close()

    async def test_callback_http_disables_redirects_proxy_env_and_dns_cache(self):
        options = {}
        class FakeResolver:
            async def close(self):
                pass
        class FakeContent:
            async def iter_chunked(self, _limit):
                yield b"{}"
        class FakeResponse:
            status, headers, content = 302, {}, FakeContent()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class FakeSession:
            def __init__(self, **kwargs):
                options["session"] = kwargs
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def post(self, url, **kwargs):
                options["post"] = kwargs
                return FakeResponse()
        def connector(**kwargs):
            options["connector"] = kwargs
            return object()
        with patch.object(mcp, "_PublicResolver", FakeResolver), patch.object(mcp.aiohttp, "TCPConnector", connector), patch.object(mcp.aiohttp, "ClientSession", FakeSession):
            status, _, _ = await self.real_callback_post("https://hooks.example.com/cb", b"{}", {})
        self.assertEqual(status, 302)
        self.assertFalse(options["session"]["trust_env"])
        self.assertFalse(options["post"]["allow_redirects"])
        self.assertFalse(options["connector"]["use_dns_cache"])

    async def test_callback_reads_complete_chunked_body_and_caps_total(self):
        chunks = [b'{"chall', b'enge":', b'"complete"}']
        consumed = []
        class FakeResolver:
            async def close(self):
                pass
        class FakeContent:
            async def iter_chunked(self, _limit):
                for chunk in chunks:
                    consumed.append(len(chunk))
                    await asyncio.sleep(0)
                    yield chunk
        class FakeResponse:
            status, headers, content = 200, {}, FakeContent()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class FakeSession:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def post(self, *args, **kwargs):
                return FakeResponse()
        with patch.object(mcp, "_PublicResolver", FakeResolver), patch.object(mcp.aiohttp, "TCPConnector", lambda **kwargs: object()), patch.object(mcp.aiohttp, "ClientSession", FakeSession):
            _, raw, _ = await self.real_callback_post("https://hooks.example.com/cb", b"{}", {})
            self.assertEqual(json.loads(raw), {"challenge": "complete"})
            self.assertEqual(raw, b"".join(chunks))
            chunks[:] = [b"x" * 16384] * 4 + [b"x", b"must-not-read"]
            consumed.clear()
            with self.assertRaises(mcp.CallbackError) as caught:
                await self.real_callback_post("https://hooks.example.com/cb", b"{}", {})
            self.assertEqual(caught.exception.reason, "response_too_large")
            self.assertEqual(len(consumed), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Isolated Telegram transport tests. No live DB, bot, account, or network is used."""
import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEST_DATA = tempfile.TemporaryDirectory(prefix="abit-telegram-tests-")
env = ModuleType("open_webui.env")
env.DATA_DIR = Path(TEST_DATA.name)
env.WEBUI_SECRET_KEY = "isolated-telegram-test-secret"
sys.modules["open_webui.env"] = env

from fastapi import FastAPI, HTTPException, Request
import httpx

auth = ModuleType("open_webui.utils.auth")
async def test_user(request: Request):
    name = request.headers.get("x-test-user")
    if not name:
        raise HTTPException(401, "Test authentication required")
    return SimpleNamespace(id=name, role="admin" if name == "admin" else "user")
auth.get_verified_user = test_user
sys.modules["open_webui.utils.auth"] = auth

# Public status collaborators are tested separately in their own suites.
oauth = ModuleType("open_webui.routers.telegram_oauth")
oauth.owner_connected = lambda owner: False
oauth.revoke_owner = lambda owner: None
sys.modules["open_webui.routers.telegram_oauth"] = oauth
mcp = ModuleType("open_webui.routers.telegram_mcp")
mcp.owner_subscribed = lambda owner: False
mcp.revoke_subscriptions = lambda owner: None
sys.modules["open_webui.routers.telegram_mcp"] = mcp

from open_webui.utils import telegram_bridge as bridge
from open_webui.routers import telegram


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge.LEDGER = Path(TEST_DATA.name) / (str(uuid.uuid4()) + ".sqlite")
        self.active = patch.object(
            bridge, "active_user", AsyncMock(side_effect=lambda owner: None if owner == "disabled" else SimpleNamespace(id=owner,role="user"))
        )
        self.active.start()
        self.bot = patch.object(bridge, "telegram_call", AsyncMock())
        self.call = self.bot.start()
        with bridge.db() as conn:
            conn.execute(
                "INSERT INTO tg_config(id,token,bot_id,bot_username,public_url,webhook_secret,configured_by,updated_at)"
                " VALUES(1,?,?,?,?,?,?,?)",
                (bridge.encrypt_secret("123456:synthetic_token_never_sent_to_telegram"),
                 "123456","AbitSyntheticBot","https://ai.example.invalid",bridge.encrypt_secret("test-webhook-secret"),"admin",bridge.now())
            )
            conn.commit()
        self.app = FastAPI()
        self.app.include_router(telegram.router, prefix="/api/v1/telegram")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.bot.stop()
        self.active.stop()

    def update(self, uid=101, text="Hello", update_id=1, message_id=1, kind="private", is_bot=False):
        return {
            "update_id":update_id,
            "message":{"message_id":message_id,"text":text,
                "from":{"id":uid,"is_bot":is_bot,"first_name":"Test","last_name":"Person","username":"test_person"},
                "chat":{"id":uid,"type":kind}}
        }

    async def pair(self, owner="alice", uid=101, update_id=1):
        pair = bridge.create_pairing(owner)
        token=parse_qs(urlparse(pair["deep_link"]).query)["start"][0]
        await bridge.accept_update(self.update(uid, "/start " + token, update_id=update_id))
        bridge.confirm_pairing(owner,pair["id"])
        with bridge.db() as conn:
            conn.execute("DELETE FROM tg_outbox")
            conn.commit()
        return pair

    async def test_qr_is_local_expiring_and_claim_requires_confirmation(self):
        pair=bridge.create_pairing("alice")
        token=parse_qs(urlparse(pair["deep_link"]).query)["start"][0]
        self.assertEqual(len(token),48)
        self.assertTrue(bridge.PAIR_TOKEN.fullmatch(token))
        self.assertIn("<svg",base64.b64decode(pair["qr_data_url"].split(",",1)[1]).decode())
        with bridge.db() as conn:
            raw=dict(conn.execute("SELECT * FROM tg_pairings").fetchone())
        self.assertEqual(raw["token_hash"],bridge.digest(token))
        self.assertNotIn(token,str(raw))
        await bridge.accept_update(self.update(text="/start " + token))
        self.assertIsNone(bridge.get_binding("alice"))
        self.assertEqual((await bridge.public_status("alice"))["pairing"]["status"],"claimed")
        with self.assertRaises(HTTPException):
            bridge.confirm_pairing("mallory",pair["id"])
        bridge.confirm_pairing("alice",pair["id"])
        self.assertEqual(bridge.get_binding("alice")["telegram_user_id"],"101")
        with self.assertRaises(HTTPException):
            bridge.confirm_pairing("alice",pair["id"])

    async def test_expired_and_cancelled_codes_cannot_claim(self):
        pair=bridge.create_pairing("alice")
        token=parse_qs(urlparse(pair["deep_link"]).query)["start"][0]
        with bridge.db() as conn:
            conn.execute("UPDATE tg_pairings SET expires_at=?",(bridge.now()-1,))
            conn.commit()
        await bridge.accept_update(self.update(text="/start "+token))
        with self.assertRaises(HTTPException):
            bridge.confirm_pairing("alice",pair["id"])
        with bridge.db() as conn:
            conn.execute("UPDATE tg_pairings SET created_at=?",(bridge.now()-3,))
            conn.commit()
        newer=bridge.create_pairing("alice")
        bridge.cancel_pairing("alice",newer["id"])
        await bridge.accept_update(self.update(text="/start "+parse_qs(urlparse(newer["deep_link"]).query)["start"][0],update_id=2))
        self.assertIsNone((await bridge.public_status("alice"))["pairing"])

    async def test_claim_cannot_be_stolen_by_second_telegram_user(self):
        pair=bridge.create_pairing("alice")
        token=parse_qs(urlparse(pair["deep_link"]).query)["start"][0]
        await bridge.accept_update(self.update(uid=101,text="/start "+token))
        await bridge.accept_update(self.update(uid=202,text="/start "+token,update_id=2))
        bridge.confirm_pairing("alice",pair["id"])
        self.assertEqual(bridge.get_binding("alice")["telegram_user_id"],"101")
        other=bridge.create_pairing("bob")
        token2=parse_qs(urlparse(other["deep_link"]).query)["start"][0]
        await bridge.accept_update(self.update(uid=101,text="/start "+token2,update_id=3))
        with self.assertRaises(HTTPException):
            bridge.confirm_pairing("bob",other["id"])

    async def test_duplicates_and_out_of_order_updates_preserve_single_inbound(self):
        await self.pair()
        update=self.update(text="Question one",update_id=30,message_id=30)
        await asyncio.gather(bridge.accept_update(update),bridge.accept_update(update))
        await bridge.accept_update(self.update(text="Earlier update",update_id=20,message_id=20))
        await bridge.accept_update(self.update(text="Question one",update_id=31,message_id=30))
        self.assertEqual(len(bridge.list_messages("alice")),2)
        self.assertEqual(len(bridge.event_candidates()),2)
        self.assertEqual(bridge.list_messages("bob"),[])
        self.call.assert_not_called()

    async def test_group_bot_and_unlinked_messages_never_reach_dot(self):
        await self.pair()
        await bridge.accept_update(self.update(text="group data",kind="group",update_id=2))
        await bridge.accept_update(self.update(text="bot data",is_bot=True,update_id=3))
        await bridge.accept_update(self.update(uid=202,text="stranger",update_id=4))
        self.assertEqual(bridge.event_candidates(),[])
        self.assertEqual(bridge.list_messages("alice"),[])

    async def test_reply_is_scoped_idempotent_and_chunked(self):
        await self.pair()
        await bridge.accept_update(self.update(text="Question",update_id=2,message_id=2))
        mid=bridge.list_messages("alice")[0]["id"]
        text="A" * 4000 + "\U0001f600" * 2000
        with self.assertRaises(HTTPException) as denied:
            await bridge.reply_to_message("bob",mid,text)
        self.assertEqual(denied.exception.status_code,404)
        first=await bridge.reply_to_message("alice",mid,text)
        second=await bridge.reply_to_message("alice",mid,text)
        self.assertEqual(first["status"],"reply_queued")
        self.assertTrue(second["duplicate"])
        with self.assertRaises(HTTPException):
            await bridge.reply_to_message("alice",mid,"Different")
        with bridge.db() as conn:
            rows=conn.execute("SELECT * FROM tg_outbox ORDER BY part").fetchall()
        self.assertEqual("".join(r["text"] for r in rows),text)
        self.assertTrue(all(len(r["text"].encode("utf-16-le"))//2<=3900 for r in rows))
        self.call.side_effect=[{"message_id":i} for i in range(len(rows))]
        for _ in rows:
            with bridge.db() as conn:
                conn.execute("DELETE FROM tg_chat_pacing")
                conn.commit()
            self.assertTrue(await bridge.deliver_outbox_once())
        self.assertEqual(bridge.list_messages("alice")[0]["status"],"replied")
        self.assertFalse(await bridge.deliver_outbox_once())

    async def test_unlink_cancels_pending_reply_and_revokes_old_message_target(self):
        await self.pair()
        await bridge.accept_update(self.update(text="Question",update_id=2,message_id=2))
        mid=bridge.list_messages("alice")[0]["id"]
        await bridge.reply_to_message("alice",mid,"Answer")
        bridge.unlink("alice")
        self.assertFalse(await bridge.deliver_outbox_once())
        with self.assertRaises(HTTPException):
            await bridge.reply_to_message("alice",mid,"Answer")
        self.assertEqual(bridge.event_candidates(),[])
        self.assertEqual(bridge.list_messages("alice")[0]["status"],"cancelled")
        self.call.assert_not_called()

    async def test_disabled_account_is_rechecked_before_delivery(self):
        await self.pair(owner="disabled")
        # The disabled owner cannot enqueue data or receive a dot response.
        await bridge.accept_update(self.update(text="Question",update_id=2,message_id=2))
        self.assertEqual(bridge.list_messages("disabled"),[])
        with self.assertRaises(HTTPException) as err:
            await bridge.reply_to_message("disabled","missing","Answer")
        self.assertEqual(err.exception.status_code,403)

    async def test_429_retries_send_only_and_timeout_does_not_regenerate(self):
        await self.pair()
        await bridge.accept_update(self.update(text="Question",update_id=2,message_id=2))
        mid=bridge.list_messages("alice")[0]["id"]
        await bridge.reply_to_message("alice",mid,"Answer")
        self.call.side_effect=bridge.TelegramFailure(429,retry_after=4)
        await bridge.deliver_outbox_once()
        with bridge.db() as conn:
            row=conn.execute("SELECT state,available_at FROM tg_outbox").fetchone()
            self.assertEqual(row["state"],"pending")
            self.assertGreater(row["available_at"],bridge.now())
            conn.execute("UPDATE tg_outbox SET available_at=0")
            conn.execute("DELETE FROM tg_chat_pacing")
            conn.commit()
        self.call.side_effect=bridge.TelegramFailure(0,ambiguous=True)
        await bridge.deliver_outbox_once()
        self.assertEqual(bridge.list_messages("alice")[0]["status"],"delivery_unknown")
        self.assertFalse(await bridge.deliver_outbox_once())
        same=await bridge.reply_to_message("alice",mid,"Answer")
        self.assertTrue(same["duplicate"])
        self.assertEqual(same["status"],"delivery_unknown")

    async def test_restart_does_not_resend_uncertain_delivery(self):
        await self.pair()
        await bridge.accept_update(self.update(text="Question",update_id=2,message_id=2))
        mid=bridge.list_messages("alice")[0]["id"]
        await bridge.reply_to_message("alice",mid,"Answer")
        with bridge.db() as conn:
            conn.execute("UPDATE tg_outbox SET state='sending',updated_at=?",(bridge.now()-121,))
            conn.commit()
        self.assertFalse(await bridge.deliver_outbox_once())
        self.assertEqual(bridge.list_messages("alice")[0]["status"],"delivery_unknown")
        self.call.assert_not_called()

    async def test_webhook_requires_secret_and_rejects_oversize_body(self):
        path="/api/v1/telegram/webhook"
        self.assertEqual((await self.client.post(path,json=self.update())).status_code,403)
        self.assertEqual((await self.client.post(path,headers={"X-Telegram-Bot-Api-Secret-Token":"wrong"},json=self.update())).status_code,403)
        headers={"X-Telegram-Bot-Api-Secret-Token":"test-webhook-secret"}
        good=await self.client.post(path,headers=headers,json=self.update())
        self.assertEqual(good.status_code,200)
        self.assertEqual((await self.client.post(path,headers=headers,content="x"*262145)).status_code,413)

    async def test_ui_routes_enforce_auth_admin_and_no_cache(self):
        base="/api/v1/telegram"
        self.assertEqual((await self.client.get(base+"/status")).status_code,401)
        status=await self.client.get(base+"/status",headers={"x-test-user":"alice"})
        self.assertEqual(status.status_code,200)
        self.assertEqual(status.headers["cache-control"],"no-store")
        self.assertNotIn("test-webhook-secret",status.text)
        self.assertNotIn("synthetic_token",status.text)
        config={"token":"123456:synthetic_token_never_sent","public_url":"https://ai.example.invalid"}
        self.assertEqual((await self.client.post(base+"/config",headers={"x-test-user":"alice"},json=config)).status_code,403)
        self.assertEqual((await self.client.get(base+"/status",headers={"x-test-user":"disabled"})).status_code,403)

    async def test_unlinked_sender_cannot_flood_reply_queue(self):
        for n in range(20):
            await bridge.accept_update(self.update(uid=202,text="unlinked",update_id=n,message_id=n))
        with bridge.db() as conn:
            count=conn.execute("SELECT COUNT(*) FROM tg_outbox").fetchone()[0]
        self.assertEqual(count,5)
        self.assertEqual(bridge.event_candidates(),[])

    async def test_slow_event_callback_does_not_block_telegram_outbox(self):
        callback_started = asyncio.Event()
        outbox_progress = asyncio.Event()
        calls = 0
        async def slow_callback():
            callback_started.set()
            await asyncio.Event().wait()
        async def outbound():
            nonlocal calls
            calls += 1
            if calls >= 2:
                outbox_progress.set()
        with patch.object(mcp, "deliver_pending_events", slow_callback, create=True), \
             patch.object(bridge, "deliver_outbox_once", outbound):
            bridge.start_worker(self.app)
            tasks = self.app.state.telegram_bridge_workers
            try:
                await asyncio.wait_for(callback_started.wait(), 2)
                await asyncio.wait_for(outbox_progress.wait(), 3)
            finally:
                await bridge.stop_worker(self.app)
            self.assertTrue(all(task.done() for task in tasks))
            self.assertEqual(self.app.state.telegram_bridge_workers, ())

    async def test_existing_webhook_is_not_taken_over_without_explicit_choice(self):
        self.call.side_effect=[
            {"id":123456,"is_bot":True,"username":"AbitSyntheticBot"},
            {"url":"https://another.example.invalid/hook"}
        ]
        with self.assertRaises(HTTPException) as err:
            await bridge.configure_bot("admin","123456:synthetic_token_never_sent","https://ai.example.invalid")
        self.assertEqual(err.exception.status_code,409)
        self.assertEqual([c.args[0] for c in self.call.call_args_list],["getMe","getWebhookInfo"])


if __name__=="__main__":
    unittest.main(verbosity=2)

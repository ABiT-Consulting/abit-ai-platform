"""Account-scoped Telegram transport for the user's real ChatGPT dot.

No model fallback is used. Incoming messages are persisted for MCP Events;
the connected dot returns its answer through an owner-scoped reply tool.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager, nullcontext
import hashlib
import hmac
import io
import json
import logging
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlparse
import uuid

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from open_webui.env import DATA_DIR, WEBUI_SECRET_KEY

# Telegram puts the bot credential in its URL. Suppress optional HTTP tracing
# for this transport so a URL recorder cannot capture the credential.
try:
    from opentelemetry.instrumentation.utils import suppress_instrumentation as _suppress_telemetry
except ImportError:
    _suppress_telemetry = nullcontext

log = logging.getLogger(__name__)
LEDGER = Path(DATA_DIR) / "abit-telegram.sqlite"
PAIR_TTL = 300
MAX_TEXT = 16000
BOT_TOKEN = re.compile(r"^[0-9]{5,20}:[A-Za-z0-9_-]{20,200}$")
PAIR_TOKEN = re.compile(r"^abit_[A-Za-z0-9_-]{43}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS tg_config (
 id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL, bot_id TEXT NOT NULL,
 bot_username TEXT NOT NULL, public_url TEXT NOT NULL, webhook_secret TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, configured_by TEXT NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tg_pairings (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 telegram_user_id TEXT, telegram_chat_id TEXT, telegram_username TEXT, display_name TEXT
);
CREATE INDEX IF NOT EXISTS tg_pairings_owner ON tg_pairings(owner,status);
CREATE TABLE IF NOT EXISTS tg_bindings (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL UNIQUE, bot_id TEXT NOT NULL,
 telegram_user_id TEXT NOT NULL, telegram_chat_id TEXT NOT NULL,
 telegram_username TEXT, display_name TEXT, linked_at INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS tg_binding_identity
 ON tg_bindings(bot_id,telegram_user_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS tg_updates (
 bot_id TEXT NOT NULL, update_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(bot_id,update_id)
);
CREATE TABLE IF NOT EXISTS tg_messages (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, binding_id TEXT NOT NULL,
 telegram_message_id TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'received', response_text TEXT, error TEXT,
 UNIQUE(binding_id,telegram_message_id)
);
CREATE INDEX IF NOT EXISTS tg_messages_owner ON tg_messages(owner,created_at);
CREATE TABLE IF NOT EXISTS tg_outbox (
 id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, owner TEXT, binding_id TEXT,
 chat_id TEXT NOT NULL, text TEXT NOT NULL, message_id TEXT,
 part INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, available_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL, telegram_message_id TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS tg_outbox_pending ON tg_outbox(state,available_at);
CREATE TABLE IF NOT EXISTS tg_chat_pacing (
 bot_id TEXT NOT NULL, chat_id TEXT NOT NULL, next_send INTEGER NOT NULL,
 PRIMARY KEY(bot_id,chat_id)
);
"""


def now():
    return int(time.time())


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def db():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LEDGER), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(SCHEMA)
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cipher():
    if not WEBUI_SECRET_KEY:
        raise HTTPException(503, "A persistent application secret is required for Telegram.")
    key = hashlib.sha256(("abit-telegram-v1:" + WEBUI_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(text):
    return _cipher().encrypt(text.encode()).decode()


def decrypt_secret(text):
    try:
        return _cipher().decrypt(text.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        raise HTTPException(503, "Telegram credentials need to be configured again.") from None


def get_config():
    with db() as conn:
        row = conn.execute("SELECT * FROM tg_config WHERE id=1").fetchone()
    return dict(row) if row else {}


def get_binding(owner):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM tg_bindings WHERE owner=? AND active=1", (owner,)
        ).fetchone()
    return dict(row) if row else None


async def active_user(owner):
    # A retained OAuth grant must not reactivate a disabled application account.
    from open_webui.utils.auth import get_verified_user_by_id
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.auths import Auth
    user = await get_verified_user_by_id(owner)
    if not user:
        return None
    async with get_async_db_context() as session:
        credential = await session.get(Auth, owner)
        if credential is not None and not credential.active:
            return None
    return user


def _url(value):
    try:
        parsed = urlparse(value.strip())
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or parsed.port not in (None, 443)
                or any(ord(c) < 33 for c in value)):
            raise ValueError()
        # This is a displayed/configured public origin, never a request target.
        if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
            raise ValueError()
        return "https://" + parsed.netloc.rstrip("/")
    except (ValueError, AttributeError):
        raise HTTPException(400, "Enter the application's public HTTPS origin, for example https://ai.example.com.") from None


class TelegramFailure(Exception):
    def __init__(self, code=0, retry_after=None, ambiguous=False):
        self.code = code
        self.retry_after = retry_after
        self.ambiguous = ambiguous
        super().__init__("Telegram request did not complete")


async def telegram_call(method, payload, token=None):
    """Never include the credential-bearing Telegram URL in errors or logs."""
    if token is None:
        config = get_config()
        if not config or not config["enabled"]:
            raise TelegramFailure(503)
        token = decrypt_secret(config["token"])
    with _suppress_telemetry():
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), trust_env=False
            ) as session:
                async with session.post(
                    "https://api.telegram.org/bot" + token + "/" + method,
                    json=payload, allow_redirects=False
                ) as response:
                    try:
                        data = await response.json(content_type=None)
                    except (ValueError, TypeError):
                        raise TelegramFailure(response.status, ambiguous=True) from None
                    if response.status != 200 or not isinstance(data, dict) or not data.get("ok"):
                        code = int(data.get("error_code") or response.status) if isinstance(data, dict) else response.status
                        retry = (data.get("parameters") or {}).get("retry_after") if isinstance(data, dict) else None
                        raise TelegramFailure(code, retry_after=retry, ambiguous=code >= 500)
                    return data.get("result")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise TelegramFailure(0, ambiguous=True) from None


async def configure_bot(owner, token, public_url, replace_existing_webhook=False):
    token = token.strip()
    if not BOT_TOKEN.fullmatch(token):
        raise HTTPException(400, "Enter a valid BotFather token.")
    origin = _url(public_url)
    previous = get_config()
    try:
        me = await telegram_call("getMe", {}, token)
        if not isinstance(me, dict) or not me.get("is_bot") or not re.fullmatch(r"[A-Za-z0-9_]{5,64}", str(me.get("username", ""))):
            raise HTTPException(400, "The Telegram token did not identify a bot.")
        info = await telegram_call("getWebhookInfo", {}, token)
        webhook_url = origin + "/api/v1/telegram/webhook"
        if isinstance(info, dict) and info.get("url") and info["url"] != webhook_url and not replace_existing_webhook:
            raise HTTPException(409, "This bot already has another webhook. Confirm moving the bot before replacing it.")
        if previous and previous["bot_id"] != str(me["id"]):
            with db() as conn:
                linked = conn.execute("SELECT 1 FROM tg_bindings WHERE active=1 LIMIT 1").fetchone()
            if linked:
                raise HTTPException(409, "Unlink existing Telegram accounts before changing the bot.")
        # Keep a previous same-bot secret so in-flight callbacks remain valid.
        secret = (decrypt_secret(previous["webhook_secret"])
                  if previous and previous["bot_id"] == str(me["id"])
                  else secrets.token_urlsafe(32))
        candidate = (
            encrypt_secret(token), str(me["id"]), me["username"], origin,
            encrypt_secret(secret), owner, now()
        )
        # Register only after all local validation/encryption has succeeded.
        # Pending updates are preserved; no polling process competes for them.
        await telegram_call("setWebhook", {
            "url": webhook_url, "secret_token": secret,
            "allowed_updates": ["message"], "drop_pending_updates": False,
        }, token)
        with db() as conn:
            conn.execute(
                "INSERT INTO tg_config(id,token,bot_id,bot_username,public_url,webhook_secret,configured_by,updated_at)"
                " VALUES(1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "token=excluded.token,bot_id=excluded.bot_id,bot_username=excluded.bot_username,"
                "public_url=excluded.public_url,webhook_secret=excluded.webhook_secret,"
                "configured_by=excluded.configured_by,updated_at=excluded.updated_at,enabled=1", candidate
            )
            conn.commit()
    except TelegramFailure as exc:
        message = "Telegram rejected the bot token." if exc.code in (401, 404) else "Telegram could not verify or configure this bot. Please retry."
        raise HTTPException(502, message) from None
    return await public_status(owner)


async def bot_health():
    """Admin-only Telegram webhook diagnosis; never return the bot token or secret."""
    config = get_config()
    if not config or not config["enabled"]:
        return {"configured": False, "webhook_matches": False,
                "pending_update_count": 0, "last_error_date": None}
    try:
        info = await telegram_call("getWebhookInfo", {}, decrypt_secret(config["token"]))
    except TelegramFailure:
        raise HTTPException(502, "Telegram could not verify this bot's webhook. Check the bot token and outbound connectivity.") from None
    if not isinstance(info, dict):
        raise HTTPException(502, "Telegram returned invalid webhook status.")
    return {
        "configured": True,
        "webhook_matches": info.get("url") == config["public_url"] + "/api/v1/telegram/webhook",
        "pending_update_count": int(info.get("pending_update_count") or 0),
        "last_error_date": info.get("last_error_date"),
    }


async def public_status(owner):
    config = get_config()
    binding = get_binding(owner)
    with db() as conn:
        pair = conn.execute(
            "SELECT id,status,expires_at,telegram_username,display_name FROM tg_pairings "
            "WHERE owner=? AND status IN ('pending','claimed') AND expires_at>? "
            "ORDER BY created_at DESC LIMIT 1", (owner, now())
        ).fetchone()
    # Kept in their own modules to avoid coupling application auth to transport.
    from open_webui.routers import telegram_oauth, telegram_mcp
    dot_connected = telegram_oauth.owner_connected(owner)
    subscription_active = telegram_mcp.owner_subscribed(owner)
    return {
        "configured": bool(config and config["enabled"]),
        "bot_username": config.get("bot_username"),
        "public_url": config.get("public_url"),
        "linked": bool(binding),
        "binding": ({k: binding[k] for k in ("id", "telegram_username", "display_name", "linked_at")} if binding else None),
        "pairing": dict(pair) if pair else None,
        "dot_connected": bool(dot_connected),
        "dot_subscription_active": bool(subscription_active),
        "mcp_url": config["public_url"] + "/api/v1/telegram/mcp" if config else None,
    }


def create_pairing(owner):
    config = get_config()
    if not config or not config["enabled"]:
        raise HTTPException(409, "An administrator must configure the Telegram bot first.")
    if get_binding(owner):
        raise HTTPException(409, "Telegram is already linked. Disconnect it before linking another account.")
    # Import lazily so an unconfigured integration does not affect application startup.
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        raise HTTPException(503, "The Telegram QR dependency is missing from this installation.") from None
    identifier = str(uuid.uuid4())
    token = "abit_" + secrets.token_urlsafe(32)
    expires = now() + PAIR_TTL
    deep_link = "https://t.me/" + config["bot_username"] + "?start=" + token
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(deep_link)
    qr.make(fit=True)
    stream = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(stream)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Generating another QR invalidates the previous one, including a claim.
        recent = conn.execute(
            "SELECT created_at FROM tg_pairings WHERE owner=? ORDER BY created_at DESC LIMIT 1", (owner,)
        ).fetchone()
        if recent and now() - recent["created_at"] < 2:
            raise HTTPException(429, "Wait a moment before generating another QR code.")
        conn.execute("UPDATE tg_pairings SET status='cancelled' WHERE owner=? AND status IN ('pending','claimed')", (owner,))
        conn.execute(
            "INSERT INTO tg_pairings(id,owner,token_hash,status,created_at,expires_at) VALUES(?,?,?,'pending',?,?)",
            (identifier, owner, digest(token), now(), expires)
        )
        conn.commit()
    return {
        "id": identifier, "status": "pending", "expires_at": expires,
        "deep_link": deep_link,
        "qr_data_url": "data:image/svg+xml;base64," + base64.b64encode(stream.getvalue()).decode(),
    }


def cancel_pairing(owner, identifier):
    with db() as conn:
        conn.execute(
            "UPDATE tg_pairings SET status='cancelled' WHERE id=? AND owner=? AND status IN ('pending','claimed')",
            (identifier, owner)
        )
        conn.commit()


def _queue_notice(conn, key, config, chat_id, text):
    # An unlinked sender must not monopolize the outbound queue with commands.
    recent = conn.execute(
        "SELECT COUNT(*) FROM tg_outbox WHERE bot_id=? AND chat_id=? "
        "AND message_id IS NULL AND updated_at>?",
        (config["bot_id"], str(chat_id), now() - 60)
    ).fetchone()[0]
    if recent >= 5:
        return
    conn.execute(
        "INSERT OR IGNORE INTO tg_outbox(id,bot_id,chat_id,text,available_at,updated_at) VALUES(?,?,?,?,?,?)",
        (key, config["bot_id"], str(chat_id), text, now(), now())
    )


def confirm_pairing(owner, identifier):
    config = get_config()
    if not config or not config["enabled"]:
        raise HTTPException(409, "Telegram is not configured.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pair = conn.execute("SELECT * FROM tg_pairings WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if not pair or pair["status"] != "claimed" or pair["expires_at"] <= now():
            raise HTTPException(409, "This pairing has expired or has not been confirmed in Telegram.")
        existing = conn.execute(
            "SELECT owner FROM tg_bindings WHERE active=1 AND "
            "(owner=? OR (bot_id=? AND telegram_user_id=?))",
            (owner, config["bot_id"], pair["telegram_user_id"])
        ).fetchone()
        if existing:
            raise HTTPException(409, "This Telegram account or application account is already linked.")
        conn.execute("DELETE FROM tg_bindings WHERE owner=? AND active=0", (owner,))
        conn.execute(
            "INSERT INTO tg_bindings(id,owner,bot_id,telegram_user_id,telegram_chat_id,telegram_username,display_name,linked_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), owner, config["bot_id"], pair["telegram_user_id"],
             pair["telegram_chat_id"], pair["telegram_username"], pair["display_name"], now())
        )
        conn.execute("UPDATE tg_pairings SET status='confirmed' WHERE id=?", (identifier,))
        _queue_notice(conn, "confirmed:" + identifier, config, pair["telegram_chat_id"],
                      "Telegram is linked to your abit.ai account. Messages will be answered after you connect abitdot in the application.")
        conn.commit()


def _unlink(conn, owner):
    conn.execute("UPDATE tg_bindings SET active=0 WHERE owner=?", (owner,))
    conn.execute("UPDATE tg_pairings SET status='cancelled' WHERE owner=? AND status IN ('pending','claimed')", (owner,))
    conn.execute("UPDATE tg_messages SET status='cancelled',error='Telegram account disconnected.' "
                 "WHERE owner=? AND status NOT IN ('replied','cancelled')", (owner,))
    conn.execute("UPDATE tg_outbox SET state='cancelled' WHERE owner=? AND state='pending'", (owner,))


def unlink(owner):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _unlink(conn, owner)
        conn.commit()


async def accept_update(update):
    """Called only after webhook authentication. Retried updates never repeat actions."""
    config = get_config()
    if not config or not config["enabled"]:
        raise HTTPException(503, "Telegram is not configured.")
    if not isinstance(update, dict) or type(update.get("update_id")) is not int:
        raise HTTPException(400, "Invalid Telegram update.")
    message = update.get("message")
    if not isinstance(message, dict):
        return
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if (not isinstance(sender, dict) or not isinstance(chat, dict)
            or chat.get("type") != "private" or sender.get("is_bot") is not False
            or type(sender.get("id")) is not int or type(chat.get("id")) is not int
            or sender["id"] <= 0 or sender["id"] != chat["id"]):
        return
    sender_id = str(sender["id"])
    with db() as conn:
        binding_row = conn.execute(
            "SELECT * FROM tg_bindings WHERE bot_id=? AND telegram_user_id=? AND active=1",
            (config["bot_id"], sender_id)
        ).fetchone()
    binding = dict(binding_row) if binding_row else None
    user_valid = bool(binding and await active_user(binding["owner"]))
    text = message.get("text")
    text = text.strip() if isinstance(text, str) else ""
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    note_key = "notice:" + config["bot_id"] + ":" + str(update["update_id"])
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            "INSERT OR IGNORE INTO tg_updates(bot_id,update_id,created_at) VALUES(?,?,?)",
            (config["bot_id"], update["update_id"], now())
        ).rowcount
        if not inserted:
            return
        # Recheck within the transaction: unlink/relink may have happened while verifying the user.
        current = conn.execute(
            "SELECT * FROM tg_bindings WHERE bot_id=? AND telegram_user_id=? AND active=1",
            (config["bot_id"], sender_id)
        ).fetchone()
        if not current or not binding or current["id"] != binding["id"]:
            binding = None
            user_valid = False
        if command == "/start":
            pair = None
            if PAIR_TOKEN.fullmatch(argument):
                pair = conn.execute(
                    "SELECT * FROM tg_pairings WHERE token_hash=? AND expires_at>? AND status='pending'",
                    (digest(argument), now())
                ).fetchone()
            if pair:
                conflict = conn.execute(
                    "SELECT 1 FROM tg_bindings WHERE bot_id=? AND telegram_user_id=? AND active=1",
                    (config["bot_id"], sender_id)
                ).fetchone()
                if conflict:
                    notice = "This Telegram account is already linked. Disconnect it before linking again."
                else:
                    display = " ".join(str(sender.get(k) or "") for k in ("first_name", "last_name")).strip()[:160]
                    conn.execute(
                        "UPDATE tg_pairings SET status='claimed',telegram_user_id=?,telegram_chat_id=?,"
                        "telegram_username=?,display_name=? WHERE id=? AND status='pending'",
                        (sender_id, str(chat["id"]), str(sender.get("username") or "")[:64], display, pair["id"])
                    )
                    notice = "Return to abit.ai and confirm the Telegram account shown there to finish linking."
            else:
                notice = ("You are linked to abit.ai. Send a message for abitdot, or /unlink to disconnect."
                          if user_valid else "Open abitdot > Telegram in abit.ai and scan a new linking QR code.")
            _queue_notice(conn, note_key, config, chat["id"], notice)
        elif command == "/unlink":
            if binding:
                _unlink(conn, binding["owner"])
            _queue_notice(conn, note_key, config, chat["id"], "Telegram is disconnected from abit.ai.")
        elif command in ("/help", "/status"):
            _queue_notice(conn, note_key, config, chat["id"],
                          "Send text for abitdot. Use /unlink to disconnect. View your connection and replies in abit.ai > abitdot > Telegram."
                          if user_valid else "Link your Telegram account using the QR code in abit.ai > abitdot > Telegram.")
        elif not user_valid:
            _queue_notice(conn, note_key, config, chat["id"],
                          "Link an active abit.ai account using the Telegram QR code before chatting.")
        elif not text or len(text) > MAX_TEXT or type(message.get("message_id")) is not int:
            _queue_notice(conn, note_key, config, chat["id"], "Please send a text message of up to 16,000 characters.")
        else:
            # Bounded queue protects the app if Telegram is flooded or the dot is disconnected.
            queued = conn.execute(
                "SELECT COUNT(*) FROM tg_messages WHERE owner=? AND status IN ('received','notified','reply_queued')",
                (binding["owner"],)
            ).fetchone()[0]
            if queued >= 100:
                _queue_notice(conn, note_key, config, chat["id"],
                              "Your message queue is full. Check the abitdot connection in abit.ai before sending more.")
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO tg_messages(id,owner,binding_id,telegram_message_id,text,created_at) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), binding["owner"], binding["id"], str(message["message_id"]), text, now())
                )
        conn.commit()


def list_messages(owner, limit=30, before=None):
    limit = max(1, min(int(limit), 100))
    with db() as conn:
        params = [owner]
        clause = ""
        if before:
            row = conn.execute("SELECT created_at,rowid AS sequence FROM tg_messages WHERE owner=? AND id=?", (owner, before)).fetchone()
            if not row:
                raise HTTPException(404, "Message was not found.")
            clause = " AND (created_at<? OR (created_at=? AND rowid<?))"
            params += [row["created_at"], row["created_at"], row["sequence"]]
        rows = conn.execute(
            "SELECT id,text,created_at,status,response_text,error FROM tg_messages "
            "WHERE owner=?" + clause + " ORDER BY created_at DESC,rowid DESC LIMIT ?", (*params, limit)
        ).fetchall()
    return [dict(row) for row in rows]


def event_candidates(limit=50):
    with db() as conn:
        rows = conn.execute(
            "SELECT m.* FROM tg_messages m JOIN tg_bindings b ON b.id=m.binding_id AND b.owner=m.owner "
            "WHERE b.active=1 AND m.status IN ('received','notified') AND m.response_text IS NULL "
            "ORDER BY m.created_at,m.rowid LIMIT ?", (max(1, min(int(limit), 200)),)
        ).fetchall()
    return [dict(row) for row in rows]


def split_reply(text):
    # Telegram entity offsets use UTF-16; cap both code points and UTF-16 units.
    parts, chunk, units = [], [], 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > 3900:
            parts.append("".join(chunk))
            chunk, units = [], 0
        chunk.append(char)
        units += size
    if chunk:
        parts.append("".join(chunk))
    return parts


async def reply_to_message(owner, message_id, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        raise HTTPException(400, "Reply must contain 1 to 32,000 characters.")
    if not await active_user(owner):
        raise HTTPException(403, "This application account is not active.")
    config = get_config()
    if not config or not config["enabled"]:
        raise HTTPException(409, "Telegram is not configured.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT m.*,b.telegram_chat_id,b.active,b.bot_id FROM tg_messages m "
            "JOIN tg_bindings b ON b.id=m.binding_id AND b.owner=m.owner WHERE m.id=? AND m.owner=?",
            (message_id, owner)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Message was not found.")
        if not row["active"] or row["bot_id"] != config["bot_id"] or row["status"] == "cancelled":
            raise HTTPException(409, "The Telegram account for this message has been disconnected.")
        if row["response_text"] is not None:
            if row["response_text"] != text:
                raise HTTPException(409, "This message already has a different reply.")
            return {"message_id": message_id, "status": row["status"], "duplicate": True}
        conn.execute("UPDATE tg_messages SET response_text=?,status='reply_queued',error=NULL WHERE id=?", (text, message_id))
        for index, part in enumerate(split_reply(text)):
            conn.execute(
                "INSERT INTO tg_outbox(id,bot_id,owner,binding_id,chat_id,text,message_id,part,available_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("reply:" + message_id + ":" + str(index), config["bot_id"], owner, row["binding_id"],
                 row["telegram_chat_id"], part, message_id, index, now(), now())
            )
        conn.commit()
    return {"message_id": message_id, "status": "reply_queued", "duplicate": False}


def _finish_outbox(identifier, state, telegram_message_id=None, error=None, delay=0):
    with db() as conn:
        conn.execute(
            "UPDATE tg_outbox SET state=?,telegram_message_id=?,error=?,available_at=?,updated_at=? WHERE id=?",
            (state, str(telegram_message_id) if telegram_message_id is not None else None, error, now() + delay, now(), identifier)
        )
        row = conn.execute("SELECT message_id FROM tg_outbox WHERE id=?", (identifier,)).fetchone()
        if row and row["message_id"]:
            parts = [r["state"] for r in conn.execute("SELECT state FROM tg_outbox WHERE message_id=?", (row["message_id"],))]
            status = ("replied" if all(p == "sent" for p in parts) else
                      "delivery_unknown" if "unknown" in parts else
                      "delivery_failed" if "failed" in parts else
                      "cancelled" if "cancelled" in parts else "reply_queued")
            conn.execute("UPDATE tg_messages SET status=?,error=? WHERE id=? AND status<>'cancelled'",
                         (status, error, row["message_id"]))
        conn.commit()


async def deliver_outbox_once():
    config = get_config()
    if not config or not config["enabled"]:
        return False
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        stale = conn.execute(
            "SELECT id FROM tg_outbox WHERE state='sending' AND updated_at<?", (now() - 120,)
        ).fetchall()
        for row in stale:
            conn.execute("UPDATE tg_outbox SET state='unknown',error='Delivery interrupted; check Telegram before resending.' WHERE id=?", (row["id"],))
        conn.execute(
            "UPDATE tg_messages SET status='delivery_unknown',error='Delivery interrupted; check Telegram before resending.' "
            "WHERE id IN (SELECT message_id FROM tg_outbox WHERE state='unknown') AND status<>'cancelled'"
        )
        row = conn.execute(
            "SELECT o.* FROM tg_outbox o LEFT JOIN tg_chat_pacing p ON p.bot_id=o.bot_id AND p.chat_id=o.chat_id "
            "WHERE o.state='pending' AND o.available_at<=? AND o.bot_id=? AND (p.next_send IS NULL OR p.next_send<=?) "
            "AND NOT EXISTS (SELECT 1 FROM tg_outbox other WHERE other.bot_id=o.bot_id AND other.chat_id=o.chat_id "
            "AND other.state='sending') "
            "AND NOT EXISTS (SELECT 1 FROM tg_outbox prior WHERE prior.message_id=o.message_id AND prior.part<o.part AND prior.state<>'sent') "
            "ORDER BY o.available_at,o.rowid LIMIT 1", (now(), config["bot_id"], now())
        ).fetchone()
        if row:
            conn.execute("UPDATE tg_outbox SET state='sending',attempts=attempts+1,updated_at=? WHERE id=?", (now(), row["id"]))
            conn.execute(
                "INSERT INTO tg_chat_pacing(bot_id,chat_id,next_send) VALUES(?,?,?) "
                "ON CONFLICT(bot_id,chat_id) DO UPDATE SET next_send=excluded.next_send",
                (row["bot_id"], row["chat_id"], now() + 1)
            )
        conn.commit()
    if not row:
        return False
    row = dict(row)
    if row["owner"]:
        user = await active_user(row["owner"])
        # Re-read the binding after the awaited user check, immediately before send.
        binding = get_binding(row["owner"])
        if not user or not binding or binding["id"] != row["binding_id"]:
            _finish_outbox(row["id"], "cancelled", error="Account disconnected or disabled.")
            return True
    payload = {"chat_id": row["chat_id"], "text": row["text"]}
    # Plain text prevents Markdown/HTML content from changing the output semantics.
    try:
        result = await telegram_call("sendMessage", payload)
        if not isinstance(result, dict) or type(result.get("message_id")) is not int:
            raise TelegramFailure(0, ambiguous=True)
        _finish_outbox(row["id"], "sent", telegram_message_id=result["message_id"])
    except TelegramFailure as exc:
        if exc.code == 429 and row["attempts"] < 8:
            try:
                delay = max(1, min(int(exc.retry_after or 5), 86400))
            except (ValueError, TypeError):
                delay = 5
            _finish_outbox(row["id"], "pending", error="Telegram rate limited; delivery will retry.", delay=delay)
        else:
            state = "unknown" if exc.ambiguous else "failed"
            error = ("Telegram delivery is uncertain. Check Telegram before sending again."
                     if exc.ambiguous else "Telegram could not deliver the reply. Check that the bot is not blocked.")
            _finish_outbox(row["id"], state, error=error)
    return True


async def _worker_loop(deliver, name, cleanup=False):
    """Keep callback retries independent of outbound Telegram delivery."""
    next_cleanup = 0
    while True:
        try:
            config = get_config()
            if config and config["enabled"]:
                await deliver()
                if cleanup and now() >= next_cleanup:
                    with db() as conn:
                        conn.execute("DELETE FROM tg_updates WHERE created_at<?", (now() - 7 * 86400,))
                        conn.execute("DELETE FROM tg_pairings WHERE expires_at<?", (now() - 86400,))
                        conn.commit()
                    next_cleanup = now() + 3600
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exceptions from transports may contain URLs; log only their type.
            log.warning("Telegram %s worker cycle failed (%s)", name, type(exc).__name__)
        await asyncio.sleep(1)


def start_worker(app):
    from open_webui.routers import telegram_mcp
    app.state.telegram_bridge_workers = (
        asyncio.create_task(_worker_loop(deliver_outbox_once, "outbox", cleanup=True)),
        asyncio.create_task(_worker_loop(telegram_mcp.deliver_pending_events, "events")),
    )


async def stop_worker(app):
    tasks = getattr(app.state, "telegram_bridge_workers", ())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    app.state.telegram_bridge_workers = ()

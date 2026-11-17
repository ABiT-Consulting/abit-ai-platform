"""Slack DM transport for an account-scoped ChatGPT dot, never a model fallback."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import html
import json
import logging
import os
import re
import uuid

import httpx
from fastapi import HTTPException
from open_webui.utils import slack_connection as connection

log = logging.getLogger(__name__)
MAX_TEXT = 32000
MAX_REPLY = 8000
SCHEMA = """
CREATE TABLE IF NOT EXISTS sl_updates (
 app_id TEXT NOT NULL, event_id TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(app_id,event_id)
);
CREATE TABLE IF NOT EXISTS sl_messages (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, binding_id TEXT NOT NULL,
 channel_id TEXT NOT NULL, slack_ts TEXT NOT NULL, thread_ts TEXT NOT NULL,
 text TEXT NOT NULL, created_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'received',
 response_text TEXT, error TEXT, UNIQUE(binding_id,channel_id,slack_ts)
);
CREATE INDEX IF NOT EXISTS sl_messages_owner ON sl_messages(owner,created_at);
CREATE TABLE IF NOT EXISTS sl_outbox (
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
 binding_id TEXT NOT NULL, channel_id TEXT NOT NULL, thread_ts TEXT NOT NULL,
 text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, available_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL, slack_ts TEXT, error TEXT, claim_token TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sl_outbox_pending ON sl_outbox(state,available_at);
"""
now = connection.now
active_user = connection.active_user
encrypt_secret = connection.encrypt_token
decrypt_secret = connection.decrypt_token


@contextmanager
def db():
    with connection.db() as conn:
        conn.executescript(SCHEMA)
        if "claim_token" not in {row[1] for row in conn.execute("PRAGMA table_info(sl_outbox)")}:
            conn.execute("ALTER TABLE sl_outbox ADD COLUMN claim_token TEXT NOT NULL DEFAULT ''")
        yield conn


def get_config():
    cfg = connection.config()
    return {**cfg, "enabled": os.getenv("ABIT_SLACK_WORKER_ENABLED", "true").lower() != "false"} if cfg else {}


def get_binding(owner):
    cfg = connection.config()
    if not cfg:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM slack_connections WHERE owner=? AND client_id=?",
                           (owner, cfg["client_id"])).fetchone()
    if not row or not row["binding_id"] or not {"chat:write", "im:history"}.issubset(row["scope"].split(",")):
        return None
    return {**dict(row), "id": row["binding_id"]}


async def public_status(owner):
    from open_webui.routers import slack_oauth, slack_mcp
    result = connection.status(owner)
    cfg = connection.config()
    result.update(
        dot_connected=slack_oauth.owner_connected(owner),
        dot_subscription_active=slack_mcp.owner_subscribed(owner),
        mcp_url=cfg["public_url"] + "/api/v1/slack/mcp" if cfg else None,
    )
    return result


def verify_signature(raw, timestamp, supplied):
    cfg = connection.config()
    if not cfg:
        raise HTTPException(503, "Slack is not configured.")
    try:
        stamp = int(timestamp)
    except (ValueError, TypeError):
        raise HTTPException(403, "Invalid Slack request authentication.") from None
    if abs(now() - stamp) > 300 or not re.fullmatch(r"v0=[0-9a-f]{64}", supplied or ""):
        raise HTTPException(403, "Invalid Slack request authentication.")
    expected = "v0=" + hmac.new(cfg["signing_secret"].encode(),
                               b"v0:" + timestamp.encode() + b":" + raw,
                               hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, "Invalid Slack request authentication.")


async def accept_update(payload):
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid Slack event.")
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        if not isinstance(challenge, str) or not 1 <= len(challenge) <= 256:
            raise HTTPException(400, "Invalid Slack verification.")
        return {"challenge": challenge}
    if payload.get("type") != "event_callback":
        return {"ok": True}
    event = payload.get("event")
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid Slack event.")
    app_id, team_id, event_id = payload.get("api_app_id"), payload.get("team_id"), payload.get("event_id")
    if not all(isinstance(v, str) and 1 <= len(v) <= 100 for v in (app_id, team_id, event_id)):
        raise HTTPException(400, "Invalid Slack event identity.")
    if event.get("type") in {"app_uninstalled", "tokens_revoked"}:
        bots = (event.get("tokens") or {}).get("bot", []) if isinstance(event.get("tokens"), dict) else []
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cfg = connection.config()
            event_time = payload.get("event_time")
            if not cfg or not isinstance(event_time, (int, float)) or isinstance(event_time, bool):
                return {"ok": True}
            if not conn.execute("INSERT OR IGNORE INTO sl_updates VALUES (?,?,?)", (app_id, event_id, now())).rowcount:
                return {"ok": True}
            # Slack retries may be re-signed; do not apply an old uninstall to a new installation.
            rows = conn.execute("SELECT owner,bot_user_id FROM slack_connections WHERE team_id=? AND app_id=? "
                                "AND client_id=? AND linked_at<?",
                                (team_id, app_id, cfg["client_id"], event_time)).fetchall()
            for row in rows:
                if event["type"] == "app_uninstalled" or (isinstance(bots, list) and row["bot_user_id"] in bots):
                    connection.invalidate_relay(conn, row["owner"])
                    conn.execute("DELETE FROM slack_connections WHERE owner=?", (row["owner"],))
        return {"ok": True}
    # No channel/group history, bot loops, edits, deletions, file payloads or other users.
    if (event.get("type") != "message" or event.get("channel_type") != "im"
            or event.get("subtype") or event.get("bot_id") or event.get("bot_profile")):
        return {"ok": True}
    channel, user_id = event.get("channel"), event.get("user")
    text, ts = event.get("text"), event.get("ts")
    thread_ts = event.get("thread_ts") or ts
    if (not isinstance(channel, str) or not re.fullmatch(r"D[A-Z0-9]{1,79}", channel)
            or not isinstance(user_id, str) or not connection.IDENTITY.fullmatch(user_id)
            or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT
            or not isinstance(ts, str) or not re.fullmatch(r"[0-9]{1,20}\.[0-9]{1,10}", ts)
            or not isinstance(thread_ts, str) or not re.fullmatch(r"[0-9]{1,20}\.[0-9]{1,10}", thread_ts)):
        return {"ok": True}
    cfg = connection.config()
    with db() as conn:
        row = conn.execute("SELECT owner,binding_id,linked_at FROM slack_connections WHERE team_id=? "
                           "AND slack_user_id=? AND app_id=? AND client_id=?",
                           (team_id, user_id, app_id, cfg["client_id"])).fetchone()
    if not row or not row["binding_id"] or float(ts) < row["linked_at"] or not await active_user(row["owner"]):
        return {"ok": True}
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Do not accept under a new identity if disconnect/relink happened during auth.
        valid = conn.execute("SELECT 1 FROM slack_connections WHERE owner=? AND binding_id=?",
                             (row["owner"], row["binding_id"])).fetchone()
        if not valid:
            return {"ok": True}
        added = conn.execute("INSERT OR IGNORE INTO sl_updates VALUES (?,?,?)",
                             (app_id, event_id, now())).rowcount
        if added:
            conn.execute("INSERT OR IGNORE INTO sl_messages "
                         "(id,owner,binding_id,channel_id,slack_ts,thread_ts,text,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), row["owner"], row["binding_id"], channel, ts, thread_ts, text, now()))
    return {"ok": True}


def list_messages(owner, limit=30, before=None):
    limit = max(1, min(int(limit), 100))
    with db() as conn:
        params, clause = [owner], ""
        if before:
            row = conn.execute("SELECT rowid FROM sl_messages WHERE owner=? AND id=?", (owner, before)).fetchone()
            if not row:
                raise HTTPException(404, "Message was not found.")
            clause = " AND rowid<?"
            params.append(row[0])
        rows = conn.execute(
            "SELECT id,text,created_at,status,response_text,error FROM sl_messages "
            "WHERE owner=?" + clause + " ORDER BY rowid DESC LIMIT ?", (*params, limit)).fetchall()
    return [dict(row) for row in rows]


async def reply_to_message(owner, message_id, text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPLY:
        raise HTTPException(400, "Reply must contain 1 to 8,000 characters.")
    if not await active_user(owner):
        raise HTTPException(403, "This application account is not active.")
    binding = get_binding(owner)
    if not binding:
        raise HTTPException(409, "Slack is not connected.")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT m.* FROM sl_messages m JOIN slack_connections c "
                           "ON c.owner=m.owner AND c.binding_id=m.binding_id "
                           "WHERE m.id=? AND m.owner=? AND m.binding_id=?",
                           (message_id, owner, binding["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Message was not found.")
        if row["status"] == "cancelled":
            raise HTTPException(409, "This connection was disconnected.")
        if row["response_text"] is not None:
            if row["response_text"] != text:
                raise HTTPException(409, "This message already has a different reply.")
            return {"message_id": message_id, "status": row["status"], "duplicate": True}
        conn.execute("UPDATE sl_messages SET response_text=?,status='reply_queued',error=NULL WHERE id=?",
                     (text, message_id))
        conn.execute("INSERT INTO sl_outbox "
                     "(id,message_id,owner,binding_id,channel_id,thread_ts,text,available_at,updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     ("reply:" + message_id, message_id, owner, binding["id"], row["channel_id"],
                      row["thread_ts"], text, now(), now()))
    return {"message_id": message_id, "status": "reply_queued", "duplicate": False}


class SlackFailure(Exception):
    def __init__(self, ambiguous=False, retry_after=None):
        self.ambiguous, self.retry_after = ambiguous, retry_after


async def send_reply(binding, row):
    # Fixed provider endpoint, no automatic retries or redirects. Never log exceptions/headers.
    data = {"channel": row["channel_id"], "thread_ts": row["thread_ts"],
            "text": html.escape(row["text"], quote=False), "mrkdwn": False,
            "parse": "none", "unfurl_links": False, "unfurl_media": False,
            "client_msg_id": str(uuid.uuid5(uuid.NAMESPACE_URL, row["id"]))}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            response = await client.post("https://slack.com/api/chat.postMessage",
                                         headers={"Authorization": "Bearer " + decrypt_secret(binding["token"])},
                                         json=data)
            if response.status_code == 429:
                try:
                    delay = max(1, min(86400, int(response.headers.get("Retry-After", "5"))))
                except ValueError:
                    delay = 5
                raise SlackFailure(retry_after=delay)
            if response.status_code >= 500:
                raise SlackFailure(ambiguous=True)
            if not response.is_success:
                raise SlackFailure()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise SlackFailure(ambiguous=True) from None
    if not isinstance(result, dict):
        raise SlackFailure(ambiguous=True)
    if result.get("ok") is not True:
        raise SlackFailure()
    if (result.get("channel") != row["channel_id"] or not isinstance(result.get("ts"), str)):
        raise SlackFailure(ambiguous=True)
    return result["ts"]


def finish(row, state, error=None, ts=None, delay=0):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if state == "pending":
            # A definite rate-limit rejection is retryable only while this exact
            # message/connection remains authorized. Disconnect during dispatch
            # must never resurrect a cancelled dot reply on the next cycle.
            authorized = conn.execute("SELECT 1 FROM sl_messages m JOIN slack_connections c "
                                      "ON c.owner=m.owner AND c.binding_id=m.binding_id "
                                      "WHERE m.id=? AND m.status='reply_queued' AND m.binding_id=?",
                                      (row["message_id"], row["binding_id"])).fetchone()
            if not authorized:
                state, error = "cancelled", "Connection was disconnected before retry."
        changed = conn.execute("UPDATE sl_outbox SET state=?,error=?,slack_ts=?,available_at=?,updated_at=? "
                               "WHERE id=? AND state='sending' AND claim_token=?",
                               (state, error, ts, now() + delay, now(), row["id"], row["claim_token"])).rowcount
        if changed:
            status = {"sent": "replied", "unknown": "delivery_unknown", "failed": "delivery_failed",
                      "cancelled": "cancelled", "pending": "reply_queued"}[state]
            conn.execute("UPDATE sl_messages SET status=?,error=? WHERE id=? AND status<>'cancelled'",
                         (status, error, row["message_id"]))


async def deliver_outbox_once():
    if not get_config().get("enabled"):
        return False
    claim_token = str(uuid.uuid4())
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE sl_outbox SET state='pending',claim_token='' WHERE state='claimed' AND updated_at<?", (now() - 120,))
        conn.execute("UPDATE sl_outbox SET state='unknown' ,error='Delivery interrupted; check Slack before resending.' "
                     "WHERE state='sending' AND updated_at<?", (now() - 120,))
        conn.execute("UPDATE sl_messages SET status='delivery_unknown',error='Delivery interrupted; check Slack before resending.' "
                     "WHERE id IN (SELECT message_id FROM sl_outbox WHERE state='unknown') AND status<>'cancelled'")
        row = conn.execute("SELECT * FROM sl_outbox o WHERE o.state='pending' AND o.available_at<=? "
                           "AND NOT EXISTS (SELECT 1 FROM sl_outbox busy WHERE busy.channel_id=o.channel_id "
                           "AND busy.binding_id=o.binding_id AND busy.state IN ('claimed','sending')) "
                           "ORDER BY available_at,rowid LIMIT 1", (now(),)).fetchone()
        if row:
            conn.execute("UPDATE sl_outbox SET state='claimed',attempts=attempts+1,updated_at=?,claim_token=? WHERE id=?",
                         (now(), claim_token, row["id"]))
    if not row:
        return False
    row = {**dict(row), "claim_token": claim_token}
    user = await active_user(row["owner"])
    binding = get_binding(row["owner"])
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        valid = conn.execute("SELECT 1 FROM sl_outbox o JOIN sl_messages m ON m.id=o.message_id "
                             "JOIN slack_connections c ON c.owner=o.owner AND c.binding_id=o.binding_id "
                             "WHERE o.id=? AND o.state='claimed' AND m.status='reply_queued' "
                             "AND o.binding_id=? AND o.claim_token=?", (row["id"], row["binding_id"], claim_token)).fetchone()
        if not user or not binding or binding["id"] != row["binding_id"] or not valid:
            changed = conn.execute("UPDATE sl_outbox SET state='cancelled' WHERE id=? AND state='claimed' AND claim_token=?", (row["id"], claim_token)).rowcount
            if changed:
                conn.execute("UPDATE sl_messages SET status='cancelled' WHERE id=? AND status='reply_queued'", (row["message_id"],))
            return True
        # The durable dispatch boundary: disconnect cancels pending/claimed work.
        # Once sending, a request may already reach Slack and is never blindly retried.
        conn.execute("UPDATE sl_outbox SET state='sending',updated_at=? WHERE id=? AND state='claimed' AND claim_token=?",
                     (now(), row["id"], claim_token))
    try:
        ts = await send_reply(binding, row)
        finish(row, "sent", ts=ts)
    except SlackFailure as failure:
        if failure.retry_after is not None and row["attempts"] < 7:
            finish(row, "pending", error="Slack rate limited; delivery will retry.", delay=failure.retry_after)
        else:
            finish(row, "unknown" if failure.ambiguous else "failed",
                   error="Delivery is uncertain; check Slack before resending." if failure.ambiguous
                   else "Slack could not deliver this reply.")
    return True


async def worker_loop(deliver):
    while True:
        try:
            if get_config().get("enabled"):
                await deliver()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Slack worker failed (%s)", type(exc).__name__)
        await asyncio.sleep(1)


def start_worker(app):
    from open_webui.routers import slack_mcp
    if not get_config().get("enabled"):
        app.state.slack_bridge_workers = ()
        return
    app.state.slack_bridge_workers = (
        asyncio.create_task(worker_loop(deliver_outbox_once)),
        asyncio.create_task(worker_loop(slack_mcp.deliver_pending_events)),
    )


async def stop_worker(app):
    tasks = getattr(app.state, "slack_bridge_workers", ())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    app.state.slack_bridge_workers = ()

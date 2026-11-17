"""Account-scoped Slack OAuth connections, separate from the Telegram bridge.

Bot permissions are limited to direct messages with this app and replies.
Secrets stay server-side; OAuth state is single-use and browser-bound.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from open_webui.env import DATA_DIR, WEBUI_SECRET_KEY

LEDGER = Path(DATA_DIR) / "abit-slack.sqlite"
STATE_TTL = 600
COOKIE_NAME = "__Secure-abit-slack-state"
CALLBACK_PATH = "/api/v1/slack/callback"
OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43}$")
IDENTITY = re.compile(r"^[A-Z][A-Z0-9]{1,79}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_oauth_states (
 state_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, browser_hash TEXT NOT NULL,
 expires_at INTEGER NOT NULL, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS slack_states_owner ON slack_oauth_states(owner);
CREATE TABLE IF NOT EXISTS slack_connections (
 owner TEXT PRIMARY KEY, team_id TEXT NOT NULL, team_name TEXT NOT NULL,
 slack_user_id TEXT NOT NULL, token TEXT NOT NULL, scope TEXT NOT NULL,
 linked_at INTEGER NOT NULL, client_id TEXT NOT NULL,
 UNIQUE(team_id, slack_user_id, client_id)
);
"""


def now():
    return int(time.time())


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def config():
    client_id = os.getenv("ABIT_SLACK_CLIENT_ID", "").strip()
    client_secret = os.getenv("ABIT_SLACK_CLIENT_SECRET", "").strip()
    signing_secret = os.getenv("ABIT_SLACK_SIGNING_SECRET", "").strip()
    origin = os.getenv("ABIT_SLACK_PUBLIC_URL", "").strip().rstrip("/")
    parsed = urlparse(origin)
    if (not re.fullmatch(r"[0-9]+\.[0-9]+", client_id)
            or not client_secret or not signing_secret or not WEBUI_SECRET_KEY
            or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path or any(ord(c) < 33 for c in origin)):
        return None
    return {"client_id": client_id, "client_secret": client_secret,
            "redirect_uri": origin + CALLBACK_PATH, "public_url": origin,
            "signing_secret": signing_secret}


@contextmanager
def db():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LEDGER), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(slack_connections)")}
    for name in ("binding_id", "app_id", "bot_user_id"):
        if name not in columns:
            try:
                conn.execute(f"ALTER TABLE slack_connections ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                if name not in {row[1] for row in conn.execute("PRAGMA table_info(slack_connections)")}:
                    raise
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def encrypt_token(value):
    if not WEBUI_SECRET_KEY:
        raise HTTPException(503, "A persistent application secret is required.")
    key = hashlib.sha256(("abit-slack-v1:" + WEBUI_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key)).encrypt(value.encode()).decode()


async def active_user(owner):
    # Share the existing active-account policy, not Telegram connections or data.
    from open_webui.utils.telegram_bridge import active_user as check
    return await check(owner)


def status(owner):
    cfg = config()
    with db() as conn:
        row = conn.execute(
            "SELECT team_id, team_name, slack_user_id, linked_at, client_id "
            "FROM slack_connections WHERE owner=?", (owner,)
        ).fetchone()
    linked = bool(row)  # Keep local grants visible and removable if configuration changes.
    return {
        "configured": bool(cfg), "linked": linked,
        "workspace": {"id": row["team_id"], "name": row["team_name"]} if linked else None,
        "slack_user_id": row["slack_user_id"] if linked else None,
        "linked_at": row["linked_at"] if linked else None,
    }


def begin(owner):
    cfg = config()
    if not cfg:
        raise HTTPException(503, "Slack is not configured. Contact the application administrator.")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires_at = now() + STATE_TTL
    with db() as conn:
        conn.execute("DELETE FROM slack_oauth_states WHERE owner=? OR ABS(expires_at)<=?", (owner, now()))
        conn.execute("INSERT INTO slack_oauth_states VALUES (?,?,?,?,?,?)",
                     (digest(state), owner, digest(browser), expires_at,
                      cfg["client_id"], cfg["redirect_uri"]))
    query = urlencode({"client_id": cfg["client_id"], "scope": "chat:write,im:history",
                       "redirect_uri": cfg["redirect_uri"], "state": state})
    return {"authorization_url": "https://slack.com/oauth/v2/authorize?" + query,
            "expires_at": expires_at}, browser


def consume(state, browser):
    if not OPAQUE.fullmatch(state or "") or not OPAQUE.fullmatch(browser or ""):
        return None
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM slack_oauth_states WHERE state_hash=? AND browser_hash=?",
            (digest(state), digest(browser))
        ).fetchone()
        if not row or row["expires_at"] <= now():
            return None
        conn.execute("UPDATE slack_oauth_states SET expires_at=-expires_at WHERE state_hash=?", (digest(state),))
    cfg = config()
    if (row["expires_at"] <= now() or not cfg or row["client_id"] != cfg["client_id"]
            or row["redirect_uri"] != cfg["redirect_uri"]):
        return None
    return dict(row)


async def exchange(code, cfg):
    # Fixed Slack endpoint, no redirects, credentials in POST body, never URLs.
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post("https://slack.com/api/oauth.v2.access",
                                         data={"client_id": cfg["client_id"],
                                               "client_secret": cfg["client_secret"],
                                               "redirect_uri": cfg["redirect_uri"],
                                               "code": code})
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Slack authorization could not be completed. Please reconnect.") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise HTTPException(502, "Slack authorization could not be completed. Please reconnect.")
    return result


def save_connection(owner, payload, cfg, entry):
    team = payload.get("team") or {}
    account = payload.get("authed_user") or {}
    if not isinstance(team, dict) or not isinstance(account, dict):
        raise HTTPException(502, "Slack returned an invalid account.")
    team_id, user_id = team.get("id"), account.get("id")
    token = payload.get("access_token")
    scopes = set(str(payload.get("scope", "")).split(","))
    app_id, bot_user_id = payload.get("app_id"), payload.get("bot_user_id")
    if (payload.get("is_enterprise_install") or not isinstance(team_id, str)
            or not IDENTITY.fullmatch(team_id) or not isinstance(user_id, str)
            or not IDENTITY.fullmatch(user_id) or not isinstance(token, str)
            or not token or len(token) > 4096 or not {"chat:write", "im:history"}.issubset(scopes)
            or payload.get("token_type") != "bot" or not isinstance(app_id, str)
            or not IDENTITY.fullmatch(app_id) or not isinstance(bot_user_id, str)
            or not IDENTITY.fullmatch(bot_user_id)):
        raise HTTPException(502, "Slack did not grant the required workspace account connection.")
    # Rotation is deliberately not silently accepted without a refresh worker.
    if payload.get("expires_in") or payload.get("refresh_token"):
        raise HTTPException(502, "This Slack app requires token rotation, which is not supported yet.")
    name = str(team.get("name") or team_id)[:200]
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                "SELECT 1 FROM slack_oauth_states WHERE state_hash=? AND owner=? AND expires_at=?",
                (entry["state_hash"], owner, -entry["expires_at"])
            ).fetchone()
            if not pending or entry["expires_at"] <= now():
                raise HTTPException(409, "This connection request was cancelled or replaced.")
            conn.execute(
                "INSERT INTO slack_connections (owner,team_id,team_name,slack_user_id,token,scope,"
                "linked_at,client_id,binding_id,app_id,bot_user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(owner) DO UPDATE SET team_id=excluded.team_id, "
                "team_name=excluded.team_name, slack_user_id=excluded.slack_user_id, "
                "token=excluded.token, scope=excluded.scope, linked_at=excluded.linked_at, "
                "client_id=excluded.client_id,binding_id=excluded.binding_id,app_id=excluded.app_id,bot_user_id=excluded.bot_user_id",
                (owner, team_id, name, user_id, encrypt_token(token),
                 ",".join(sorted(scopes)), now(), cfg["client_id"], str(uuid.uuid4()), app_id, bot_user_id))
            invalidate_relay(conn, owner)
            conn.execute("DELETE FROM slack_oauth_states WHERE state_hash=?", (entry["state_hash"],))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This Slack account is already connected to another ABiT account.") from None


def disconnect(owner):
    # Remove only this user's local grant. Never uninstall a shared workspace app.
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invalidate_relay(conn, owner)
        conn.execute("DELETE FROM slack_connections WHERE owner=?", (owner,))
        conn.execute("DELETE FROM slack_oauth_states WHERE owner=?", (owner,))
    return status(owner)


def decrypt_token(value):
    key = hashlib.sha256(("abit-slack-v1:" + WEBUI_SECRET_KEY).encode()).digest()
    try:
        return Fernet(base64.urlsafe_b64encode(key)).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        raise HTTPException(503, "Slack credentials must be configured again.") from None


def invalidate_relay(conn, owner):
    """Invalidate owner state in the same transaction as relink/disconnect."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "sl_oauth_grants" in tables:
        conn.execute("UPDATE sl_oauth_grants SET revoked=1 WHERE owner=?", (owner,))
        conn.execute("UPDATE sl_oauth_codes SET used_at=? WHERE owner=? AND used_at IS NULL", (now(), owner))
        conn.execute("UPDATE sl_oauth_requests SET status='revoked' WHERE owner=? AND status='pending'", (owner,))
    if "sl_subscriptions" in tables:
        conn.execute("UPDATE sl_subscriptions SET active=0,verification_nonce='' WHERE owner=?", (owner,))
        conn.execute("UPDATE sl_event_deliveries SET status='cancelled',lease_token='',lease_until=0 WHERE owner=? AND status IN ('pending','sending')", (owner,))
    if "sl_messages" in tables:
        conn.execute("UPDATE sl_messages SET status='delivery_unknown',error='Reply was already dispatched; check Slack before resending.' "
                     "WHERE owner=? AND id IN (SELECT message_id FROM sl_outbox WHERE state='sending')", (owner,))
        conn.execute("UPDATE sl_messages SET status='cancelled' WHERE owner=? AND status NOT IN ('replied','delivery_unknown','delivery_failed')", (owner,))
        conn.execute("UPDATE sl_outbox SET state='cancelled' WHERE owner=? AND state IN ('pending','claimed')", (owner,))

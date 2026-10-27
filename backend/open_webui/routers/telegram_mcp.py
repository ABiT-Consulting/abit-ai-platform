"""Owner-scoped Telegram tools and MCP Events for a connected ChatGPT dot.

This module never runs a model. Events wake the conversation which subscribed;
only its authenticated reply tool can enqueue a response for Telegram delivery.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import secrets
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from yarl import URL

from open_webui.routers import telegram_oauth
from open_webui.routers.telegram_oauth import authenticate_mcp
from open_webui.utils import telegram_bridge as bridge

router = APIRouter()
log = logging.getLogger(__name__)
PROTOCOL_VERSION = "2026-07-28"
EVENT_NAME = "telegram.message_received"
READ_SCOPE = "telegram:read"
REPLY_SCOPE = "telegram:reply"
MAX_BODY_BYTES = 262144
MAX_REPLY_CHARS = 32000
DEFAULT_TTL_SECONDS = 86400
MAX_TTL_SECONDS = 604800
ROTATION_SECONDS = 300
DELIVERY_LEASE_SECONDS = 60
MAX_DELIVERY_ATTEMPTS = 5
MAX_DELIVERY_AGE_SECONDS = 900


class ProtocolError(Exception):
    def __init__(self, code, message, *, status=400, data=None):
        super().__init__(message)
        self.code, self.message, self.status, self.data = code, message, status, data


class CallbackError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@contextmanager
def _db():
    with bridge.db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS tg_subscriptions (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, grant_id TEXT NOT NULL DEFAULT '', event_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL, callback_url TEXT NOT NULL,
            secret_enc TEXT NOT NULL DEFAULT '', previous_secret_enc TEXT NOT NULL DEFAULT '',
            previous_secret_until REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, expires_at REAL NOT NULL,
            verified_at REAL NOT NULL DEFAULT 0, verification_nonce TEXT NOT NULL DEFAULT ''
        )""")
        # Earlier development snapshots had owner-only subscriptions. They must
        # stay inactive for delivery until a verified refresh binds a grant.
        if "grant_id" not in {row[1] for row in conn.execute("PRAGMA table_info(tg_subscriptions)")}:
            try:
                conn.execute("ALTER TABLE tg_subscriptions ADD COLUMN grant_id TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                if "grant_id" not in {row[1] for row in conn.execute("PRAGMA table_info(tg_subscriptions)")}:
                    raise
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS tg_subscriptions_active_owner
            ON tg_subscriptions(owner) WHERE active = 1""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tg_event_deliveries (
            id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, message_id TEXT NOT NULL,
            owner TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL, lease_token TEXT NOT NULL DEFAULT '',
            lease_until REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            first_attempt REAL, completed_at REAL, error TEXT,
            UNIQUE(subscription_id, message_id)
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS tg_event_deliveries_due
            ON tg_event_deliveries(status, next_attempt, lease_until)""")
        yield conn


def _begin(conn):
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def owner_subscribed(owner):
    """Safe status check; works before the Telegram bot is configured."""
    with _db() as conn:
        row = conn.execute(
            "SELECT grant_id FROM tg_subscriptions WHERE owner=? AND active=1 AND expires_at>? LIMIT 1",
            (owner, bridge.now()),
        ).fetchone()
    return bool(row and row["grant_id"] and telegram_oauth.owner_connected(
        owner, grant_id=row["grant_id"], required_scope=READ_SCOPE
    ))


def revoke_subscriptions(owner):
    """Invalidate active subscriptions and concurrent verification attempts."""
    with _db() as conn:
        _begin(conn)
        conn.execute(
            "UPDATE tg_subscriptions SET active=0, verification_nonce='', updated_at=? WHERE owner=?",
            (bridge.now(), owner),
        )
        conn.execute(
            """UPDATE tg_event_deliveries SET status='cancelled', lease_token='', lease_until=0
               WHERE owner=? AND status IN ('pending','sending')""", (owner,)
        )
        conn.commit()


def _iso(value):
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _secret_bytes(secret):
    try:
        if not isinstance(secret, str) or not secret.startswith("whsec_") or len(secret) > 128:
            raise ValueError()
        decoded = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(decoded) <= 64:
            raise ValueError()
        return decoded
    except (ValueError, TypeError):
        raise ProtocolError(-32602, "A valid Standard Webhooks signing secret is required.") from None


def _signature_headers(subscription_id, webhook_id, body, secret, previous_secret=None):
    signed_at = str(int(bridge.now()))
    material = (webhook_id + "." + signed_at + ".").encode("utf-8") + body
    signatures = []
    for key_text in (secret, previous_secret):
        if key_text:
            digest = hmac.new(_secret_bytes(key_text), material, hashlib.sha256).digest()
            signatures.append("v1," + base64.b64encode(digest).decode("ascii"))
    return {
        "Content-Type": "application/json", "webhook-id": webhook_id,
        "webhook-timestamp": signed_at, "webhook-signature": " ".join(signatures),
        "X-MCP-Subscription-Id": subscription_id,
    }


def _public_ip(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified
                                     or ip.is_loopback or ip.is_link_local))


def _validate_callback_url(url):
    if not isinstance(url, str) or not url or len(url) > 4096 or any(ord(c) < 33 for c in url):
        raise ProtocolError(-32602, "A public HTTPS callback URL is required.")
    try:
        parsed = urlsplit(url)
        normalized = URL(url)
        host = normalized.raw_host
        port = normalized.port
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.fragment or "%" in host
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
        host.encode("idna")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host.lower().rstrip(".") == "localhost":
                raise ValueError()
        else:
            if not _public_ip(host):
                raise ValueError()
    except (ValueError, UnicodeError):
        raise ProtocolError(-32602, "A public HTTPS callback URL is required.") from None
    return str(normalized)


class _PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate the DNS answers the connector actually uses, not a separate lookup."""
    def __init__(self):
        self.delegate = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self.delegate.resolve(host, port, family)
        if not records or any(not _public_ip(record["host"]) for record in records):
            raise OSError("Callback destination is not a public address")
        return records

    async def close(self):
        await self.delegate.close()


async def _callback_post(url, body, headers):
    url = _validate_callback_url(url)
    if len(body) > MAX_BODY_BYTES:
        raise CallbackError("payload_too_large")
    resolver = _PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, family=socket.AF_UNSPEC)
    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=10), trust_env=False
        ) as session:
            async with session.post(url, data=body, headers=headers, allow_redirects=False) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    if len(raw) + len(chunk) > 65536:
                        raise CallbackError("response_too_large")
                    raw.extend(chunk)
                retry_after = None
                try:
                    candidate = float(response.headers.get("Retry-After", ""))
                    if math.isfinite(candidate) and candidate >= 0:
                        retry_after = min(candidate, 300)
                except (ValueError, TypeError):
                    pass
                return response.status, bytes(raw), retry_after
    except asyncio.TimeoutError:
        raise CallbackError("timeout") from None
    except aiohttp.ClientError:
        raise CallbackError("connection_failed") from None
    finally:
        await resolver.close()


async def _verify_callback(subscription_id, url, secret):
    challenge = secrets.token_urlsafe(32)
    body = _json_bytes({"type": "verification", "challenge": challenge})
    headers = _signature_headers(subscription_id, "msg_verification_" + secrets.token_hex(16), body, secret)
    try:
        status, raw, _ = await _callback_post(url, body, headers)
        if not 200 <= status < 300:
            raise CallbackError("challenge_failed")
        decoded = json.loads(raw)
        echo = decoded.get("challenge") if isinstance(decoded, dict) else None
        if not isinstance(echo, str) or not hmac.compare_digest(echo.encode("utf-8"), challenge.encode("utf-8")):
            raise CallbackError("challenge_failed")
    except (ValueError, UnicodeError):
        raise CallbackError("challenge_failed") from None


def _event_params(params, subscribing):
    if params.get("name") != EVENT_NAME or params.get("arguments", {}) != {}:
        raise ProtocolError(-32602, "This event accepts only the connected account and no filter arguments.")
    delivery = params.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("mode") != "webhook":
        raise ProtocolError(-32602, "Webhook delivery is required.")
    url = _validate_callback_url(delivery.get("url"))
    if subscribing:
        _secret_bytes(delivery.get("secret"))
        if params.get("cursor") is not None:
            raise ProtocolError(-32602, "This event does not support replay cursors.")
    return delivery, url


def _subscription_id(owner, url):
    identity = _json_bytes([owner, url, EVENT_NAME, {}])
    return "sub_" + hashlib.sha256(identity).hexdigest()


async def _subscribe(owner, params, grant_id):
    if not isinstance(grant_id, str) or not grant_id or not telegram_oauth.owner_connected(
        owner, grant_id=grant_id, required_scope=READ_SCOPE
    ):
        raise ProtocolError(-32012, "An active Telegram read grant is required.", status=403)
    delivery, url = _event_params(params, True)
    ttl_ms = params.get("ttlMs", DEFAULT_TTL_SECONDS * 1000)
    if ttl_ms is None:
        ttl_ms = DEFAULT_TTL_SECONDS * 1000
    if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, (int, float)):
        raise ProtocolError(-32602, "ttlMs must be a positive lifetime or null.")
    try:
        requested_ttl = float(ttl_ms)
    except (ValueError, OverflowError):
        raise ProtocolError(-32602, "ttlMs must be a positive lifetime or null.") from None
    if not math.isfinite(requested_ttl) or requested_ttl <= 0:
        raise ProtocolError(-32602, "ttlMs must be a positive lifetime or null.")
    ttl = max(60, min(requested_ttl / 1000, MAX_TTL_SECONDS))
    current = bridge.now()
    subscription_id = _subscription_id(owner, url)
    nonce = secrets.token_hex(16)
    with _db() as conn:
        _begin(conn)
        conn.execute("UPDATE tg_subscriptions SET active=0 WHERE active=1 AND expires_at<=?", (current,))
        existing = conn.execute(
            "SELECT id FROM tg_subscriptions WHERE owner=? AND active=1 AND id<>?", (owner, subscription_id)
        ).fetchone()
        if existing:
            raise ProtocolError(-32602, "Another conversation is connected. Disconnect it in ABiT AI before connecting this one.")
        conn.execute("""INSERT INTO tg_subscriptions
            (id,owner,grant_id,event_name,arguments_json,callback_url,created_at,updated_at,expires_at,verification_nonce)
            VALUES (?,?,?,?,'{}',?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET verification_nonce=excluded.verification_nonce,
              updated_at=excluded.updated_at""",
            (subscription_id, owner, grant_id, EVENT_NAME, url, current, current, current + ttl, nonce))
        conn.commit()
    try:
        await _verify_callback(subscription_id, url, delivery["secret"])
    except CallbackError as exc:
        raise ProtocolError(-32015, "Callback verification failed.", data={"reason": exc.reason}) from None
    if await bridge.active_user(owner) is None:
        raise ProtocolError(-32012, "The connected account is no longer active.", status=403)
    if not telegram_oauth.owner_connected(owner, grant_id=grant_id, required_scope=READ_SCOPE):
        raise ProtocolError(-32012, "The Telegram authorization was revoked during verification.", status=403)
    with _db() as conn:
        _begin(conn)
        existing = conn.execute("SELECT * FROM tg_subscriptions WHERE id=? AND owner=?", (subscription_id, owner)).fetchone()
        if not existing or existing["verification_nonce"] != nonce:
            raise ProtocolError(-32602, "The connection changed during verification. Connect again.")
        other = conn.execute(
            "SELECT id FROM tg_subscriptions WHERE owner=? AND active=1 AND id<>? AND expires_at>?",
            (owner, subscription_id, bridge.now()),
        ).fetchone()
        if other:
            raise ProtocolError(-32602, "Another conversation is connected. Disconnect it in ABiT AI first.")
        old_secret = bridge.decrypt_secret(existing["secret_enc"]) if existing["secret_enc"] else ""
        previous = existing["secret_enc"] if old_secret and old_secret != delivery["secret"] else existing["previous_secret_enc"]
        overlap_until = bridge.now() + ROTATION_SECONDS if old_secret and old_secret != delivery["secret"] else existing["previous_secret_until"]
        expires_at = bridge.now() + ttl
        conn.execute("UPDATE tg_subscriptions SET active=0 WHERE owner=? AND active=1 AND expires_at<=?", (owner, bridge.now()))
        conn.execute("""UPDATE tg_subscriptions SET active=1,grant_id=?,secret_enc=?,previous_secret_enc=?,
            previous_secret_until=?,verified_at=?,updated_at=?,expires_at=?,verification_nonce=''
            WHERE id=? AND owner=? AND verification_nonce=?""",
            (grant_id, bridge.encrypt_secret(delivery["secret"]), previous, overlap_until, bridge.now(),
             bridge.now(), expires_at, subscription_id, owner, nonce))
        conn.commit()
    return {"id": subscription_id, "refreshBefore": _iso(expires_at), "cursor": None, "truncated": False}


def _unsubscribe(owner, params):
    _, url = _event_params(params, False)
    subscription_id = _subscription_id(owner, url)
    with _db() as conn:
        _begin(conn)
        conn.execute("UPDATE tg_subscriptions SET active=0,verification_nonce='',updated_at=? WHERE id=? AND owner=?",
                     (bridge.now(), subscription_id, owner))
        conn.execute("""UPDATE tg_event_deliveries SET status='cancelled',lease_token='',lease_until=0
            WHERE subscription_id=? AND owner=? AND status IN ('pending','sending')""", (subscription_id, owner))
        conn.commit()
    return {}


def _tool(name, description, properties, required, read_only):
    scheme = [{"type": "oauth2", "scopes": [READ_SCOPE if read_only else REPLY_SCOPE]}]
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        "securitySchemes": scheme, "_meta": {"securitySchemes": scheme},
        "annotations": {"readOnlyHint": read_only, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": not read_only},
    }


TOOLS = [
    _tool("get_telegram_status", "Check the connected ABiT AI account's Telegram link and event subscription.", {}, [], True),
    _tool("list_telegram_messages", "Read recent Telegram messages and reply status for the connected account.",
          {"limit": {"type": "integer", "minimum": 1, "maximum": 100},
           "before": {"type": "string", "description": "Message cursor returned by an earlier lookup, if available."}}, [], True),
    _tool("reply_to_telegram_message", "Queue one reply to an incoming private Telegram message owned by the connected account. Repeated calls for the same message do not send another reply.",
          {"message_id": {"type": "string", "minLength": 1, "maxLength": 128},
           "text": {"type": "string", "minLength": 1, "maxLength": MAX_REPLY_CHARS}}, ["message_id", "text"], False),
]

EVENT = {
    "name": EVENT_NAME,
    "description": "An incoming private Telegram message linked to the connected ABiT AI account.",
    "delivery": ["webhook"],
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "payloadSchema": {"type": "object", "properties": {
        "message_id": {"type": "string"}, "text": {"type": "string"},
        "received_at": {"type": "string", "format": "date-time"}},
        "required": ["message_id", "text", "received_at"], "additionalProperties": False},
}


def _validate_tool_args(name, args):
    if not isinstance(args, dict):
        raise ProtocolError(-32602, "Tool arguments must be an object.")
    definition = next((item for item in TOOLS if item["name"] == name), None)
    if definition is None:
        raise ProtocolError(-32602, "Unknown tool.")
    schema = definition["inputSchema"]
    if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
        raise ProtocolError(-32602, "Invalid tool arguments.")
    if name == "list_telegram_messages":
        limit = args.get("limit", 30)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ProtocolError(-32602, "limit must be an integer from 1 to 100.")
        if "before" in args and (not isinstance(args["before"], str) or len(args["before"]) > 128):
            raise ProtocolError(-32602, "Invalid message cursor.")
    if name == "reply_to_telegram_message":
        if not isinstance(args.get("message_id"), str) or not 1 <= len(args["message_id"]) <= 128:
            raise ProtocolError(-32602, "A message ID is required.")
        if not isinstance(args.get("text"), str) or not args["text"].strip() or len(args["text"]) > MAX_REPLY_CHARS:
            raise ProtocolError(-32602, "A non-empty reply within the permitted length is required.")


def _header_name(value):
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            raise ProtocolError(-32020, "Malformed MCP name header.") from None
    return value


def _validate_origin(request):
    origin = request.headers.get("origin")
    if origin:
        public_url = bridge.get_config().get("public_url", "")
        permitted = {"https://chatgpt.com", "https://chat.openai.com"}
        if public_url:
            parsed = urlsplit(public_url)
            permitted.add(parsed.scheme + "://" + parsed.netloc)
        if origin not in permitted:
            raise ProtocolError(-32600, "Origin is not permitted.", status=403)


def _validate_transport(request, method, params):
    if method == "initialize":
        return False
    version = request.headers.get("mcp-protocol-version")
    if version in {"2025-03-26", "2025-06-18", "2025-11-25"}:
        if method.startswith("events/") or method == "server/discover":
            raise ProtocolError(-32022, "MCP Events requires protocol 2026-07-28.",
                                data={"requested": version, "supported": [PROTOCOL_VERSION]})
        return False
    meta = params.get("_meta", {})
    if not isinstance(meta, dict):
        raise ProtocolError(-32602, "Request metadata must be an object.")
    if not version or version != meta.get("io.modelcontextprotocol/protocolVersion"):
        raise ProtocolError(-32020, "MCP protocol header must match request metadata.")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(-32022, "Unsupported protocol version.",
                            data={"requested": version, "supported": [PROTOCOL_VERSION]})
    if not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
        raise ProtocolError(-32602, "Client capabilities are required.")
    if request.headers.get("mcp-method") != method:
        raise ProtocolError(-32020, "MCP method header must match the request.")
    if method == "tools/call" and _header_name(request.headers.get("mcp-name", "")) != params.get("name"):
        raise ProtocolError(-32020, "MCP name header must match the requested tool.")
    return True


def _rpc_error(request_id, exc):
    error = {"code": exc.code, "message": exc.message}
    if exc.data is not None:
        error["data"] = exc.data
    body = {"jsonrpc": "2.0", "error": error}
    if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
        body["id"] = request_id
    return JSONResponse(body, status_code=exc.status, headers={"Cache-Control": "no-store"})


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    request_id = None
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                raise ProtocolError(-32600, "Request is too large.", status=413)
            raw.extend(chunk)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ProtocolError(-32700, "Invalid JSON.") from None
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
            raise ProtocolError(-32600, "A single JSON-RPC request is required.")
        request_id = body.get("id")
        if "id" in body and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
            raise ProtocolError(-32600, "The request ID must be a string or integer.")
        method, params = body["method"], body.get("params", {})
        if not isinstance(params, dict):
            raise ProtocolError(-32602, "Request parameters must be an object.")
        _validate_origin(request)
        scope = REPLY_SCOPE if method == "tools/call" and params.get("name") == "reply_to_telegram_message" else READ_SCOPE
        user = await authenticate_mcp(request, required_scope=scope)
        owner = user.id
        if "id" not in body:
            if method == "notifications/initialized":
                return Response(status_code=202)
            raise ProtocolError(-32600, "This operation requires a request ID.")
        modern = _validate_transport(request, method, params)
        if method == "server/discover":
            result = {"supportedVersions": [PROTOCOL_VERSION], "capabilities": {"tools": {}, "events": {}},
                      "instructions": "These tools access only the connected account's private Telegram link. Reply using the original message_id; a queued reply is not a confirmed delivery."}
        elif method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "abitdot-telegram", "version": "1.0.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            if params.get("cursor"):
                raise ProtocolError(-32602, "There are no further tool pages.")
            result = {"tools": TOOLS}
        elif method == "events/list":
            if params.get("cursor"):
                raise ProtocolError(-32602, "There are no further event pages.")
            result = {"events": [EVENT]}
        elif method == "events/subscribe":
            result = await _subscribe(owner, params, getattr(request.state, "telegram_oauth_grant_id", None))
        elif method == "events/unsubscribe":
            result = _unsubscribe(owner, params)
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments", {})
            _validate_tool_args(name, args)
            try:
                if name == "get_telegram_status":
                    value = await bridge.public_status(owner)
                elif name == "list_telegram_messages":
                    value = {"messages": bridge.list_messages(owner, limit=args.get("limit", 30), before=args.get("before"))}
                else:
                    value = await bridge.reply_to_message(owner, args["message_id"], args["text"])
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                          "structuredContent": value, "isError": False}
            except (HTTPException, ValueError) as exc:
                status_code = getattr(exc, "status_code", 400)
                message = "The requested Telegram operation is unavailable for this account."
                if status_code == 404:
                    message = "That Telegram message is not available to the connected account."
                elif status_code == 409:
                    message = "This message cannot accept another reply in its current state."
                result = {"content": [{"type": "text", "text": message}], "isError": True}
        else:
            raise ProtocolError(-32601, "Method not found.", status=404)
        if modern:
            result["resultType"] = "complete"
            result.setdefault("_meta", {})["io.modelcontextprotocol/serverInfo"] = {"name": "abitdot-telegram", "version": "1.0.0"}
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result}, headers={"Cache-Control": "no-store"})
    except ProtocolError as exc:
        return _rpc_error(request_id, exc)
    except HTTPException:
        raise
    except ValueError:
        return _rpc_error(request_id, ProtocolError(-32602, "The requested message or reply is not available to this account."))
    except Exception as exc:
        log.warning("Telegram MCP request failed (%s)", type(exc).__name__)
        return _rpc_error(request_id, ProtocolError(-32603, "The Telegram connection could not complete this request.", status=500))


def _enqueue_events():
    current = bridge.now()
    with _db() as conn:
        _begin(conn)
        conn.execute("UPDATE tg_subscriptions SET active=0 WHERE active=1 AND expires_at<=?", (current,))
        rows = conn.execute("""SELECT m.id AS message_id,m.owner,s.id AS subscription_id
            FROM tg_messages m JOIN tg_subscriptions s ON s.owner=m.owner AND s.active=1
            WHERE s.expires_at>? AND m.status IN ('received','notified')
              AND COALESCE(m.response_text,'')=''
              AND NOT EXISTS (SELECT 1 FROM tg_event_deliveries d
                  WHERE d.message_id=m.id AND d.subscription_id=s.id)
            ORDER BY m.rowid LIMIT 200""", (current,)).fetchall()
        for item in rows:
            event_id = "tg_evt_" + hashlib.sha256((item["subscription_id"] + ":" + item["message_id"]).encode()).hexdigest()
            conn.execute("""INSERT OR IGNORE INTO tg_event_deliveries
                (id,subscription_id,message_id,owner,status,next_attempt,created_at)
                VALUES (?,?,?,?,'pending',?,?)""",
                (event_id, item["subscription_id"], item["message_id"], item["owner"], current, current))
        conn.commit()


def _claim_delivery():
    current = bridge.now()
    token = secrets.token_hex(16)
    with _db() as conn:
        _begin(conn)
        row = conn.execute("""SELECT d.*,m.binding_id,m.text,m.created_at AS message_created_at,
                s.callback_url,s.secret_enc,s.previous_secret_enc,s.previous_secret_until,s.grant_id
            FROM tg_event_deliveries d JOIN tg_subscriptions s ON s.id=d.subscription_id
            JOIN tg_messages m ON m.id=d.message_id
            WHERE s.active=1 AND s.expires_at>? AND d.next_attempt<=?
              AND (d.status='pending' OR (d.status='sending' AND d.lease_until<=?))
              AND m.status IN ('received','notified') AND COALESCE(m.response_text,'')=''
              AND NOT EXISTS (SELECT 1 FROM tg_event_deliveries busy
                  WHERE busy.owner=d.owner AND busy.id<>d.id AND busy.status='sending' AND busy.lease_until>?)
              AND NOT EXISTS (SELECT 1 FROM tg_event_deliveries older
                  JOIN tg_messages previous ON previous.id=older.message_id
                  WHERE older.owner=d.owner AND older.subscription_id=d.subscription_id
                    AND older.id<>d.id AND older.status IN ('pending','sending')
                    AND previous.rowid<m.rowid AND previous.status IN ('received','notified')
                    AND COALESCE(previous.response_text,'')='')
            ORDER BY m.rowid LIMIT 1""", (current, current, current, current)).fetchone()
        if row is None:
            return None
        conn.execute("""UPDATE tg_event_deliveries SET status='sending',attempts=attempts+1,
            lease_token=?,lease_until=?,first_attempt=COALESCE(first_attempt,?) WHERE id=?""",
            (token, current + DELIVERY_LEASE_SECONDS, current, row["id"]))
        conn.commit()
        result = dict(row)
        result.update(lease_token=token, attempts=row["attempts"] + 1, first_attempt=row["first_attempt"] or current)
        return result


def _delivery_still_active(item):
    if not item["grant_id"] or not telegram_oauth.owner_connected(
        item["owner"], grant_id=item["grant_id"], required_scope=READ_SCOPE
    ):
        return False
    binding = bridge.get_binding(item["owner"])
    if not binding or binding.get("id", binding.get("binding_id")) != item["binding_id"]:
        return False
    with _db() as conn:
        return conn.execute("""SELECT 1 FROM tg_event_deliveries d
            JOIN tg_subscriptions s ON s.id=d.subscription_id JOIN tg_messages m ON m.id=d.message_id
            WHERE d.id=? AND d.status='sending' AND d.lease_token=? AND d.lease_until>?
              AND s.active=1 AND s.expires_at>? AND s.grant_id=? AND m.binding_id=?
              AND m.status IN ('received','notified') AND COALESCE(m.response_text,'')=''""",
            (item["id"], item["lease_token"], bridge.now(), bridge.now(), item["grant_id"], item["binding_id"])).fetchone() is not None


def _complete_delivery(item, status, error=None, retry_after=None):
    current = bridge.now()
    with _db() as conn:
        _begin(conn)
        changed = conn.execute("""UPDATE tg_event_deliveries SET status=?,error=?,completed_at=?,
            next_attempt=?,lease_token='',lease_until=0
            WHERE id=? AND status='sending' AND lease_token=?""",
            (status, error, None if status == "pending" else current,
             current + (retry_after or 0), item["id"], item["lease_token"])).rowcount
        if changed and status == "delivered":
            conn.execute("UPDATE tg_messages SET status='notified',error=NULL WHERE id=? AND status='received' AND COALESCE(response_text,'')=''", (item["message_id"],))
        elif changed and status == "failed":
            conn.execute("UPDATE tg_messages SET error=? WHERE id=? AND status IN ('received','notified') AND COALESCE(response_text,'')=''",
                         ("The connected ChatGPT conversation did not accept this message. Check its connection and send your message again.", item["message_id"]))
        conn.commit()


async def deliver_pending_events():
    """Deliver durable events once per worker cycle; leases protect multiple workers."""
    _enqueue_events()
    for _ in range(20):
        item = _claim_delivery()
        if not item:
            return
        try:
            if item["attempts"] > MAX_DELIVERY_ATTEMPTS or bridge.now() - item["first_attempt"] >= MAX_DELIVERY_AGE_SECONDS:
                _complete_delivery(item, "failed", "retry_budget_exhausted")
                continue
            if await bridge.active_user(item["owner"]) is None or not _delivery_still_active(item):
                _complete_delivery(item, "cancelled")
                continue
            secret = bridge.decrypt_secret(item["secret_enc"])
            previous = bridge.decrypt_secret(item["previous_secret_enc"]) if item["previous_secret_enc"] and item["previous_secret_until"] > bridge.now() else None
            event = {"eventId": item["id"], "name": EVENT_NAME,
                     "timestamp": _iso(item["message_created_at"]),
                     "data": {"message_id": item["message_id"], "text": item["text"],
                              "received_at": _iso(item["message_created_at"])}, "cursor": None}
            body = _json_bytes(event)
            headers = _signature_headers(item["subscription_id"], item["id"], body, secret, previous)
            if not _delivery_still_active(item):
                _complete_delivery(item, "cancelled")
                continue
            status, _, retry_after = await _callback_post(item["callback_url"], body, headers)
            if 200 <= status < 300:
                _complete_delivery(item, "delivered")
                continue
            transient = status in {408, 425, 429} or status >= 500
            reason = "callback_http_" + str(status)
        except asyncio.CancelledError:
            _complete_delivery(item, "pending", "worker_cancelled", retry_after=1)
            raise
        except CallbackError as exc:
            reason, transient, retry_after = exc.reason, exc.reason in {"timeout", "connection_failed"}, None
        except Exception as exc:
            reason, transient, retry_after = "delivery_configuration_error", False, None
            log.warning("Telegram event delivery failed (%s)", type(exc).__name__)
        exhausted = item["attempts"] >= MAX_DELIVERY_ATTEMPTS or bridge.now() - item["first_attempt"] >= MAX_DELIVERY_AGE_SECONDS
        if transient and not exhausted:
            delay = max(retry_after or 0, min(120, 2 ** item["attempts"]) + secrets.randbelow(1000) / 1000)
            _complete_delivery(item, "pending", reason, delay)
        else:
            _complete_delivery(item, "failed", reason)

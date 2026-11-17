"""Event-driven, owner-scoped abitdot recovery on the Linux ABIT.AI backend.

This is not a scheduler or a Dot polling service. The original ChatGPT
relay job is never retried automatically after an uncertain submission.
The existing selected-model fallback runs only after proven Dot non-delivery.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from urllib.parse import urlparse
from uuid import uuid4

log = logging.getLogger(__name__)
DOT_STATE_SQL = """
CREATE TABLE IF NOT EXISTS abitdot_relay_events (
 id TEXT PRIMARY KEY,
 owner TEXT NOT NULL, event_key TEXT NOT NULL,
 chat_id TEXT NOT NULL, automation_id TEXT,
 job_id TEXT, status TEXT NOT NULL,
 failure TEXT NOT NULL, context_json TEXT NOT NULL,
 response TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(owner,event_key)
);
CREATE INDEX IF NOT EXISTS idx_abitdot_relay_owner
 ON abitdot_relay_events(owner,created_at);
"""
DOT_ACTIVE = {"dispatching", "unknown"}
OWNER_ROUTE_PATTERN = re.compile(
    r"/dots/[A-Za-z0-9_-]{8,180}~[A-Za-z0-9_-]{8,180}"
)
JOB_ID_PATTERN = re.compile(r"aibit_[0-9a-f]{48}")


def dot_url_for(owner: str) -> str:
    """Dot use is opt-in for exactly the owner of the original automation."""
    url = os.getenv("ABIT_CHATGPT_DOT_URL", "").strip().rstrip("/")
    if not owner or os.getenv("ABIT_CHATGPT_DOT_OWNER_ID", "") != owner:
        return ""
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "chatgpt.com"
            or parsed.query or parsed.fragment or parsed.username
            or parsed.password or not OWNER_ROUTE_PATTERN.fullmatch(parsed.path)):
        return ""
    return url


def safe_context(value, depth=0):
    """Keep the complete original prompt and non-secret project context."""
    if depth > 24:
        return "[nested context omitted]"
    if isinstance(value, dict):
        result = {}
        for key, v in value.items():
            name = str(key).lower()
            if any(secret in name for secret in (
                "secret", "password", "token", "cookie", "authorization",
                "private_key", "api_key", "access_key", "credential",
            )):
                result[key] = "[redacted]"
            else:
                result[key] = safe_context(v, depth+1)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_context(item, depth+1) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def recovered_selected_model_messages(chat_data, original_job, project_instructions=""):
    """Recreate an exact saved user turn without resending it to ChatGPT.

    Return None for tool/image attachments, another model's response or a
    missing original turn. The alternate model must reconcile side effects.
    """
    chat_data = chat_data or {}
    messages = (chat_data.get("history") or {}).get("messages") or {}
    assistant = messages.get(original_job.get("response_message_id"))
    if (not isinstance(assistant, dict)
            or assistant.get("role") != "assistant"
            or assistant.get("selectedModelId")):
        return None
    cursor = assistant.get("parentId")
    seen = set()
    branch = []
    while isinstance(cursor, str) and cursor and cursor not in seen and len(branch) < 60:
        seen.add(cursor)
        message = messages.get(cursor)
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if (role not in ("user", "assistant")
                or message.get("files") or message.get("tool_calls")
                or isinstance(message.get("content"), list)):
            return None
        body = message.get("content")
        if not isinstance(body, str):
            return None
        if role == "assistant" and (message.get("error") or not message.get("done")):
            # Never present uncertain ChatGPT text as a completed assistant turn.
            cursor = message.get("parentId")
            continue
        branch.append({"role": role, "content": body})
        cursor = message.get("parentId")
    branch.reverse()
    if not branch or branch[-1]["role"] != "user" or not branch[-1]["content"].strip():
        return None
    caution = (
        "Recovery of the original ABIT.AI agent request after a failed "
        "ChatGPT Project response. The previous relay may have performed "
        "some actions. FIRST inspect live ERPNext task status, Git source, "
        "existing work and the original side effects. Do not repeat actions "
        "already applied. Keep the original task ID and owner, record "
        "verified evidence, and never report an ERP task completed before "
        "its acceptance tests pass. No Work mode. Slack only for a genuine "
        "showstopper or required approval."
    )
    prefix = [{"role": "system", "content": caution}]
    if isinstance(project_instructions, str) and project_instructions.strip():
        prefix.append({"role": "system", "content": project_instructions.strip()})
    if original_job.get("response"):
        prefix.append({"role": "system", "content":
            "UNVERIFIED partial earlier ChatGPT output (not a completed answer): "
            + str(original_job["response"])[:5000]})
    return prefix + branch


async def build_saved_selected_context(app, user, automation, chat, job, token):
    """Reuse the original owned ChatGPT user turn for the selected model.

    This is authorized through the existing automation owner and normal model
    dispatcher. It does not generate a new ChatGPT browser message.
    """
    from open_webui.models.folders import Folders
    from open_webui.utils.automations import _build_request
    chosen = (automation.data or {}).get("fallback_model_id")
    primary = (automation.data or {}).get("model_id")
    if (not isinstance(chosen, str) or not chosen.strip() or
            chosen == primary or not job or not job.get("response_message_id")):
        return None
    folder = None
    if getattr(chat, "folder_id", None):
        folder = await Folders.get_folder_by_id_and_user_id(
            chat.folder_id, automation.user_id
        )
    instructions = ((folder.data or {}).get("system_prompt") if folder else "") or ""
    messages = recovered_selected_model_messages(chat.chat, job, instructions)
    if not messages:
        return None
    request = _build_request(app, token=token)
    payload = {
        "model": primary, "messages": messages,
        "stream": False,
        "metadata": {
            "chat_id": chat.id,
            "message_id": job["response_message_id"],
            "automation_id": automation.id,
            "fallback_model_id": chosen,
        },
    }
    return (request, payload, user, chosen.strip())


def initialize(db):
    db.executescript(DOT_STATE_SQL)


def event_key(owner: str, chat_id: str, turn: str) -> str:
    return hashlib.sha256(
        json.dumps([owner, chat_id, turn], separators=(",", ":")).encode()
    ).hexdigest()


def append_event(db, owner, chat_id, turn, job, context, reason):
    """Deduplicate the identical source turn without mutating its relay job."""
    key = event_key(owner, chat_id, turn)
    now = int(time.time())
    initialize(db)
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "INSERT OR IGNORE INTO abitdot_relay_events "
            "(id,owner,event_key,chat_id,automation_id,job_id,status,failure,"
            "context_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'blocked',?,?,?,?)",
            (str(uuid4()), owner, key, chat_id, context.get("automation_id"),
             (job or {}).get("job_id"), str(reason)[:1000],
             json.dumps({"job": safe_context(job or {}),
                         "context": safe_context(context)},
                        ensure_ascii=False, default=str),
             now, now),
        )
        row = db.execute(
            "SELECT * FROM abitdot_relay_events "
            "WHERE owner=? AND event_key=?", (owner, key),
        ).fetchone()
        db.commit()
        return dict(row)
    except BaseException:
        db.rollback()
        raise


async def capture_event(owner, chat_id, reason, automation=None, job_id=None):
    """Read the actual current Linux ERP-linked automation, not a second task."""
    from open_webui.models.chats import Chats
    from open_webui.models.automations import Automations
    from open_webui.models.folders import Folders
    from open_webui.routers.chatgpt_relay import _db

    chat = await Chats.get_chat_by_id_and_user_id(chat_id, owner)
    if not chat:
        return None
    content = chat.chat or {}
    automation_id = getattr(automation, "id", None) or (
        (content.get("meta") or {}).get("automation_id")
    )
    if automation is None and automation_id:
        automation = await Automations.get_by_id(automation_id)
    if not automation or automation.user_id != owner:
        return None
    folder = None
    if chat.folder_id:
        folder = await Folders.get_folder_by_id_and_user_id(chat.folder_id, owner)
    history = content.get("history") or {}
    with _db() as db:
        if job_id:
            row = db.execute(
                "SELECT * FROM jobs WHERE owner=? AND chat_id=? AND job_id=?",
                (owner, chat_id, job_id),
            ).fetchone()
            if not row:
                return None
        else:
            row = db.execute(
                "SELECT * FROM jobs WHERE owner=? AND chat_id=? "
                "ORDER BY created_at DESC LIMIT 1", (owner, chat_id),
            ).fetchone()
        job = dict(row) if row else None
        turn = (job or {}).get("job_id") or history.get("currentId")
        if not turn:
            return None
        context = {
            "automation_id": automation.id,
            "automation_name": automation.name,
            "original_prompt": automation.data.get("prompt"),
            "automation_data": automation.data,
            "automation_meta": automation.meta,
            "chat_id": chat_id,
            "chat": content,
            "folder": folder.model_dump() if folder else None,
            "origin_message_id": history.get("currentId"),
            "erp_status_policy": (
                "ABIT.AI owns ERP task links, status changes and evidence. "
                "Do not invent completed work; preserve the existing ERP "
                "task-status synchronization and only report verified outcomes."
            ),
        }
        return append_event(db, owner, chat_id, turn, job, context, reason)


def claim_event(db, owner, event_id):
    db.execute("BEGIN IMMEDIATE")
    try:
        result = db.execute(
            "UPDATE abitdot_relay_events SET status='dispatching',updated_at=? "
            "WHERE id=? AND owner=? AND status='blocked'",
            (int(time.time()), event_id, owner),
        )
        db.commit()
        return result.rowcount == 1
    except BaseException:
        db.rollback()
        raise


def finish_event(db, owner, event_id, status, detail, response=None):
    db.execute(
        "UPDATE abitdot_relay_events SET status=?,failure=?,response=?,updated_at=? "
        "WHERE owner=? AND id=? AND status='dispatching'",
        (status, str(detail)[:1000], (response or "")[:250000],
         int(time.time()), owner, event_id),
    )
    db.commit()


def prompt_for_dot(event):
    snapshot = json.loads(event["context_json"])
    return (
        "abitdot — ABIT.AI event-triggered recovery from ChatGPT Project Relay.\n"
        "Continue the EXACT assigned project task and original prompt below. "
        "Work autonomously using your authorized connectors. Check the ERP "
        "task and predecessor dependencies before making changes. Preserve "
        "all existing task-status and activity controls in erp.abit.ai. "
        "Verify the previous ChatGPT attempt is not still operating; never "
        "duplicate work, commits, agents or SAP postings.\n"
        "No Dot polling, no Work mode. Use Slack only for a genuine showstopper "
        "or a permission/approval requirement. Do not bypass production, "
        "security or financial approval rules.\n"
        "On completion report actual actions, tests, ERP task ID, status and "
        "remaining blockers. Correlation ID: " + event["id"] + "\n\n"
        "COMPLETE ORIGINAL PROJECT/TASK CONTEXT:\n"
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    )


async def _send_to_dot(owner, source_job, message, account):
    import aiohttp
    from open_webui.routers.chatgpt_relay import MESSAGE_URL, MESSAGE_TOKEN_FILE, _credentials
    token, _ = _credentials(MESSAGE_TOKEN_FILE)
    job_id = (source_job or {}).get("job_id")
    if not JOB_ID_PATTERN.fullmatch(str(job_id or "")):
        raise ValueError("A verified originating relay job ID is required")
    payload = {
        "owner": owner, "job_id": job_id, "message": message,
        "account_id": account, "channel": "abitdot",
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=225), trust_env=False,
        ) as session:
            async with session.post(
                MESSAGE_URL.rstrip("/") + "/dot/handoff", json=payload,
                headers={"Authorization": "Bearer " + token},
                allow_redirects=False,
            ) as reply:
                data = await reply.json()
                if reply.status == 200 and isinstance(data, dict):
                    if (data.get("dot_handoff") is True
                            and data.get("account_id") == account
                            and isinstance(data.get("response"), str)
                            and data["response"].strip()):
                        return "accepted", data["response"]
                    return "unknown", None
                if (reply.status in (404, 424)
                        and (reply.status == 404 or
                             data.get("safe_to_fallback") is True)):
                    return "unavailable", None
                return "unknown", None
    except aiohttp.ClientConnectorError:
        # A refused connection cannot have delivered the message.
        return "unavailable", None
    except Exception:
        log.warning("abitdot response uncertain; no second executor permitted")
        return "unknown", None


async def dispatch(owner, chat_id, reason, automation=None, account=None, job_id=None):
    """One event → one Dot send; never replays after an ambiguous outcome."""
    from open_webui.routers.chatgpt_relay import _db
    event = await capture_event(owner, chat_id, reason, automation, job_id=job_id)
    if not event:
        return {"state": "not_applicable", "safe_to_fallback": True}
    if not dot_url_for(owner):
        with _db() as db:
            initialize(db)
            db.execute(
                "UPDATE abitdot_relay_events SET status='unavailable',updated_at=? "
                "WHERE owner=? AND id=? AND status='blocked'",
                (int(time.time()), owner, event["id"]),
            )
            db.commit()
        return {"state": "unavailable", "safe_to_fallback": True,
                "event_id": event["id"]}
    with _db() as db:
        initialize(db)
        fresh = db.execute(
            "SELECT * FROM abitdot_relay_events WHERE owner=? AND id=?",
            (owner, event["id"]),
        ).fetchone()
        if fresh["status"] != "blocked":
            return {"state": fresh["status"], "event_id":event["id"],
                    "safe_to_fallback":fresh["status"]=="unavailable",
                    "reply": fresh["response"], "duplicate": True}
        if not claim_event(db, owner, event["id"]):
            return {"state": "dispatching", "safe_to_fallback": False,
                    "event_id":event["id"]}
    job = json.loads(event["context_json"]).get("job") or {}
    if job.get("status") in ("queued", "running"):
        # A live/accepted original turn takes precedence. The Dot may not
        # start the same execution until its outcome is reconciled.
        with _db() as db:
            finish_event(db, owner, event["id"], "blocked", "prior_turn_still_active")
        return {"state":"unknown","event_id":event["id"],
                "safe_to_fallback":False}
    if job.get("status") == "completed":
        with _db() as db:
            finish_event(db, owner, event["id"], "unavailable",
                         "prior_turn_already_completed")
        return {"state":"completed","event_id":event["id"],
                "safe_to_fallback":False}
    if not JOB_ID_PATTERN.fullmatch(str(job.get("job_id") or "")):
        # A first-turn browser capacity error has no relay-ledger job.
        # Its saved assistant message ID still provides durable identity.
        context = json.loads(event["context_json"]).get("context") or {}
        message_id = context.get("origin_message_id")
        if not isinstance(message_id, str) or not message_id:
            with _db() as db:
                finish_event(db, owner, event["id"], "unavailable",
                             "no_stable_turn_identity")
            return {"state":"unavailable","safe_to_fallback":True,
                    "event_id":event["id"]}
        synthetic = "aibit_" + hashlib.sha256(
            (owner + "|" + chat_id + "|" + message_id).encode()
        ).hexdigest()[:48]
        job = {**job, "job_id": synthetic}
    if not account:
        account = job.get("account")
    if not (isinstance(account, str) and re.fullmatch(r"[0-9a-f]{64}",account)):
        outcome, response = "unavailable", None
    else:
        message = prompt_for_dot(event)
        if len(message) > 160000:
            outcome, response = "unavailable", None
        else:
            try:
                outcome, response = await _send_to_dot(owner, job, message, account)
            except ValueError:
                outcome, response = "unavailable", None
            except Exception:
                # The request may already have reached ChatGPT / abitdot.
                # Never send the same task to another executor on uncertainty.
                log.warning("abitdot dispatch result was not verifiable")
                outcome, response = "unknown", None
    with _db() as db:
        finish_event(db, owner, event["id"], outcome, reason, response)
    return {"state":outcome, "safe_to_fallback":outcome=="unavailable",
            "event_id":event["id"], "reply":response}


async def owns_automation(owner, automation_id):
    """One read on existing scheduled entry; no extra polling."""
    from open_webui.routers.chatgpt_relay import _db
    with _db() as db:
        initialize(db)
        return db.execute(
            "SELECT 1 FROM abitdot_relay_events "
            "WHERE owner=? AND automation_id=? AND status IN ('dispatching','unknown') "
            "LIMIT 1", (owner,automation_id),
        ).fetchone() is not None

def commit_verified_dot_reply(owner, job_id, response):
    """Save a real final Dot response in the same local relay job/assistant ID.

    This does not update the ERP task status, which remains handled by the
    existing erp_task_runtime hooks, acceptance criteria and assignments.
    """
    from open_webui.routers.chatgpt_relay import _db
    if not isinstance(response, str) or not response.strip():
        return False
    with _db() as db:
        result = db.execute(
            "UPDATE jobs SET status='completed', phase='dot_completed', "
            "fallback_model_id='abitdot', primary_error=error, "
            "error=NULL,response=?,updated_at=? "
            "WHERE owner=? AND job_id=? "
            "AND status IN ('failed','unknown') "
            "AND (response IS NULL OR response='' "
            "OR phase IN ('receiving_response','waiting_for_response'))",
            (response[:250000], int(time.time()), owner, job_id),
        )
        db.commit()
        return result.rowcount == 1


def active_for_chat(owner, chat_id):
    """Keep scheduled runs from duplicating an unresolved Dot handoff."""
    from open_webui.routers.chatgpt_relay import _db
    with _db() as db:
        initialize(db)
        return db.execute(
            "SELECT 1 FROM abitdot_relay_events WHERE owner=? AND chat_id=? "
            "AND status IN ('dispatching','unknown') LIMIT 1",
            (owner,chat_id),
        ).fetchone() is not None

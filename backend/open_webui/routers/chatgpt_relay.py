"""AIBIT-owned ChatGPT browser relay: account-scoped catalog and durable jobs.

All verified AIBIT administrators may use the shared Dev-02 browser session.
Local projects, jobs and conversation mappings remain scoped to each user. No ChatGPT credentials are returned to clients.
The separate SQLite ledger avoids changing Open WebUI's chat tables.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from open_webui.env import DATA_DIR
from open_webui.models.chats import Chats
from open_webui.models.folders import FolderForm, FolderUpdateForm, Folders
from open_webui.models.models import Models
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()
MODEL_ID = "omniroute.abit-chatgpt-relay/project-management"
CATALOG_URL = "http://127.0.0.1:8808"
MESSAGE_URL = "http://127.0.0.1:8808"
TOKEN_FILE = Path(r"C:\ProgramData\ABiT\erp-chatgpt-relay.token")
MESSAGE_TOKEN_FILE = TOKEN_FILE
LEDGER = Path(DATA_DIR) / "aibit-chatgpt-relay.sqlite"
PROJECT_ID = re.compile(r"^g-p-[A-Za-z0-9_-]{5,190}$")
JOB_ID = re.compile(r"^aibit_[a-f0-9]{48}$")
CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{5,190}$")
ACCOUNT_ID = re.compile(r"^[a-f0-9]{64}$")


def _credentials(token_file=TOKEN_FILE):
    # Never use a token supplied by a browser or request body.
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(503, "ChatGPT relay credentials are unavailable to AIBIT.") from exc
    if not token:
        raise HTTPException(503, "ChatGPT relay is not connected.")
    # Connection fingerprint is retained for legacy-mapping reconciliation.
    # Active account identity is verified separately inside the browser.
    identity = hashlib.sha256(token.encode()).hexdigest()
    return token, identity


async def _authorized(user):
    model = await Models.get_model_by_id(MODEL_ID)
    if not model or user.role != "admin":
        raise HTTPException(403, "ChatGPT account access is not authorized.")
    return user


@contextmanager
def _db():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LEDGER), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        owner TEXT NOT NULL, account TEXT NOT NULL, project_id TEXT NOT NULL,
        folder_id TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
        synced_at INTEGER NOT NULL,
        PRIMARY KEY(owner, account, project_id),
        UNIQUE(owner, folder_id)
    );
    CREATE TABLE IF NOT EXISTS bindings (
        owner TEXT NOT NULL, account TEXT NOT NULL, chat_id TEXT NOT NULL,
        project_id TEXT NOT NULL, conversation_id TEXT, conversation_url TEXT,
        PRIMARY KEY(owner, chat_id)
    );
    CREATE TABLE IF NOT EXISTS jobs (
        owner TEXT NOT NULL, account TEXT NOT NULL, job_id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL, project_id TEXT NOT NULL,
        message_hash TEXT NOT NULL, status TEXT NOT NULL,
        response TEXT, error TEXT, response_message_id TEXT,
        phase TEXT, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_aibit_jobs_chat ON jobs(owner,chat_id,updated_at);
    CREATE TABLE IF NOT EXISTS sync_state (
        owner TEXT NOT NULL, account TEXT NOT NULL, updated_at INTEGER NOT NULL,
        count INTEGER NOT NULL, PRIMARY KEY(owner,account)
    );
    """)
    # Idempotent, additive migration. Existing job data is preserved.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "response_message_id" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN response_message_id TEXT")
        conn.commit()
    if "phase" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN phase TEXT")
        conn.commit()
    try:
        yield conn
    finally:
        conn.close()


async def _relay(method, endpoint, token, payload=None, timeout=25, url=CATALOG_URL):
    timeout_config = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout_config) as session:
            async with session.request(
                method, url + endpoint,
                headers={"Authorization": "Bearer " + token},
                json=payload if method == "POST" else None,
            ) as response:
                if response.status == 401:
                    raise HTTPException(503, "ChatGPT browser connection expired. Reconnect the relay.")
                if response.status >= 400:
                    # The owned-tab relay provides a stage-specific error
                    # message. Surface a tightly bounded, non-secret reason
                    # rather than discarding all 502 diagnostics.
                    try:
                        failure = await response.json()
                    except Exception:
                        failure = {}
                    reason = str(failure.get("message") or failure.get("error") or "") if isinstance(failure, dict) else ""
                    accepted = (
                        "navigation timed out",
                        "composer",
                        "login required",
                        "rate limit",
                        "busy",
                        "project",
                        "response",
                        "unavailable",
                        "timed out",
                    )
                    if not any(label in reason.lower() for label in accepted):
                        reason = "Unknown relay failure"
                    raise HTTPException(502, f"ChatGPT relay HTTP {response.status}: {reason[:240]}")
                try:
                    data = await response.json()
                except Exception as exc:
                    raise HTTPException(502, "ChatGPT relay returned invalid JSON.") from exc
                if not isinstance(data, dict):
                    raise HTTPException(502, "ChatGPT relay returned an invalid response.")
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        # A POST timeout is ambiguous. Never automatically resubmit it.
        raise HTTPException(504, "ChatGPT relay timed out; check job status before retrying.") from exc



def _verify_account(payload):
    account = payload.get("account_id") if isinstance(payload, dict) else None
    if not isinstance(account, str) or not ACCOUNT_ID.fullmatch(account):
        raise HTTPException(502, "ChatGPT relay did not verify the active account identity.")
    return account


async def _account_context(owner=None, allow_offline=False):
    token, _ = _credentials()
    try:
        payload = await _relay("GET", "/account", token, timeout=28)
        return token, _verify_account(payload), False
    except HTTPException:
        if not allow_offline or not owner:
            raise
        # Cached data belongs only to this AIBIT administrator. Never use an
        # offline account for sending, because its login may have changed.
        with _db() as db:
            row = db.execute(
                "SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 1",
                (owner,),
            ).fetchone()
        if not row:
            raise
        return token, row["account"], True


def _status(owner, account):
    with _db() as db:
        rows = db.execute(
            "SELECT count,updated_at FROM sync_state WHERE owner=? AND account=?",
            (owner, account),
        ).fetchone()
        count = db.execute(
            "SELECT count(*) FROM projects WHERE owner=? AND account=?",
            (owner, account),
        ).fetchone()[0]
    return {"count": count, "last_sync": rows["updated_at"] if rows else None,
            "cached": bool(rows)}


@router.get("/status")
async def status(user=Depends(get_verified_user)):
    await _authorized(user)
    _, account, offline = await _account_context(user.id, allow_offline=True)
    return {**_status(user.id, account), "offline": offline}


@router.get("/projects")
async def get_projects(user=Depends(get_verified_user)):
    await _authorized(user)
    _, account, offline = await _account_context(user.id, allow_offline=True)
    with _db() as db:
        rows = db.execute(
            "SELECT project_id,folder_id,name,url,synced_at FROM projects "
            "WHERE owner=? AND account=? ORDER BY name,project_id",
            (user.id, account),
        ).fetchall()
    return {"projects": [dict(row) for row in rows], **_status(user.id, account), "offline": offline}


@router.post("/sync")
async def sync_projects(user=Depends(get_verified_user)):
    await _authorized(user)
    token, _ = _credentials()
    payload = await _relay("GET", "/projects", token, timeout=110)
    account = _verify_account(payload)
    if payload.get("ok") is not True or not isinstance(payload.get("projects"), list):
        raise HTTPException(502, "ChatGPT project catalog was incomplete; cached projects were kept.")
    if payload.get("complete") is not True:
        raise HTTPException(502, "Relay has not verified complete ChatGPT pagination; cached projects were preserved.")
    received = payload["projects"]
    if not received:
        raise HTTPException(502, "ChatGPT returned no projects; cached projects were kept.")
    projects = {}
    for row in received:
        if not isinstance(row, dict):
            raise HTTPException(502, "Malformed ChatGPT project catalog; no changes saved.")
        identifier = row.get("chatgpt_project_id") or row.get("id")
        name = row.get("name")
        if not isinstance(identifier, str) or not PROJECT_ID.fullmatch(identifier):
            raise HTTPException(502, "Invalid project ID from ChatGPT; no changes saved.")
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 180):
            raise HTTPException(502, "Invalid project name from ChatGPT; no changes saved.")
        projects[identifier] = name.strip()

    # The relay's /projects implements cursor pagination internally. AIBIT
    # consumes only its complete response, never the ChatGPT sidebar.
    with _db() as db:
        existing = {row["project_id"]: row["folder_id"] for row in db.execute(
            "SELECT project_id,folder_id FROM projects WHERE owner=? AND account=?",
            (user.id, account),
        )}
        # Legacy AIBIT mappings used a credential fingerprint instead of the
        # verified ChatGPT account identity. Reconcile ONLY an exact project
        # ID confirmed in this complete authenticated catalog for this owner.
        # Never match by name, and never migrate another user's mapping.
        legacy_candidates = {}
        for row in db.execute(
            "SELECT project_id,folder_id,account FROM projects WHERE owner=? AND account<>?",
            (user.id, account),
        ):
            if row["project_id"] in projects and row["project_id"] not in existing:
                legacy_candidates.setdefault(row["project_id"], []).append(
                    (row["folder_id"], row["account"])
                )
    taken = set()
    timestamp = int(time.time())
    for identifier, name in sorted(projects.items()):
        visible_name = "ChatGPT - " + name
        if visible_name.casefold() in taken:
            visible_name += " [" + identifier[-8:] + "]"
        taken.add(visible_name.casefold())
        folder_id = existing.get(identifier)
        legacy = legacy_candidates.get(identifier) or []
        if not folder_id and len(legacy) == 1:
            # Same owner, same stable upstream project ID, verified membership.
            # Updating the mapping (not inserting a new local folder) preserves
            # chats, files, instructions, and the unique folder ID.
            candidate_id, previous_account = legacy[0]
            candidate = await Folders.get_folder_by_id_and_user_id(candidate_id, user.id)
            if candidate and (candidate.meta or {}).get("chatgpt_project_id") == identifier:
                with _db() as db:
                    db.execute(
                        "UPDATE projects SET account=? WHERE owner=? AND account=? AND project_id=?",
                        (account, user.id, previous_account, identifier),
                    )
                    db.commit()
                folder_id = candidate_id
        folder = await Folders.get_folder_by_id_and_user_id(folder_id, user.id) if folder_id else None
        if folder is None:
            folder = await Folders.insert_new_folder(
                user.id,
                FolderForm(
                    name=visible_name,
                    meta={"chatgpt_project_id": identifier, "chatgpt_account": account},
                    data={"model_ids": [MODEL_ID]},
                ),
            )
            if not folder:
                raise HTTPException(500, "Could not create a local project folder; retry sync.")
        elif (folder.name != visible_name
              or (folder.meta or {}).get("chatgpt_account") != account):
            # Repair only this mapped ChatGPT folder, preserving its stable ID,
            # existing chats, files, instructions and unrelated metadata.
            updated = await Folders.update_folder_by_id_and_user_id(
                folder.id, user.id,
                FolderUpdateForm(name=visible_name, meta={
                    "chatgpt_project_id": identifier, "chatgpt_account": account,
                }),
            )
            if updated is None:
                raise HTTPException(409, "A synced project name conflicts with a local folder.")
        with _db() as db:
            db.execute(
                "INSERT INTO projects(owner,account,project_id,folder_id,name,url,synced_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner,account,project_id) "
                "DO UPDATE SET folder_id=excluded.folder_id,name=excluded.name,"
                "url=excluded.url,synced_at=excluded.synced_at",
                (user.id, account, identifier, folder.id, name,
                 "https://chatgpt.com/g/" + identifier + "/project", timestamp),
            )
            db.commit()
    with _db() as db:
        db.execute(
            "INSERT INTO sync_state(owner,account,updated_at,count) VALUES(?,?,?,?) "
            "ON CONFLICT(owner,account) DO UPDATE SET updated_at=excluded.updated_at,count=excluded.count",
            (user.id, account, timestamp, len(projects)),
        )
        db.commit()
    return {"synced": len(projects), **_status(user.id, account)}


def _job_record(owner, job_id):
    with _db() as db:
        record = db.execute(
            "SELECT * FROM jobs WHERE owner=? AND job_id=?", (owner, job_id)
        ).fetchone()
    return dict(record) if record else None


async def _recover_completed_reply(job):
    """Write an already completed upstream reply into its original AIBIT chat.

    Never create a new chat or invent a message: only update the saved assistant
    placeholder belonging to the recorded owner. This is idempotent on refresh.
    """
    if job["status"] != "completed" or not job.get("response_message_id"):
        return False
    chat = await Chats.get_chat_by_id_and_user_id(job["chat_id"], job["owner"])
    if not chat:
        return False
    original = await Chats.get_message_by_id_and_message_id(
        job["chat_id"], job["response_message_id"]
    )
    if not original or original.get("role") != "assistant":
        return False
    if original.get("content") == job["response"] and original.get("done"):
        return True
    # A partial produced by this exact relay job may be revised by ChatGPT.
    # Never overwrite another model's or another request's response.
    if (original.get("content") and original["content"] != job["response"]
            and not (original.get("model") == MODEL_ID and not original.get("done"))):
        return False
    patched = {**original, "content": job["response"], "done": True,
               "model": MODEL_ID, "id": job["response_message_id"]}
    return bool(await Chats.upsert_message_to_chat_by_id_and_message_id(
        job["chat_id"], job["response_message_id"], patched
    ))


def _verified_conversation_url(url, project_id, conversation_id):
    """Validate a ChatGPT project conversation, including canonical slug URLs.

    ChatGPT routes may have /g/g-p-ID-project-title/c/CONVERSATION.
    Matching /g/g-p-ID/ alone wrongly rejects real successful replies.
    """
    if not isinstance(url, str) or not isinstance(conversation_id, str):
        return False
    try:
        parsed = urlparse(url)
        path = [segment for segment in parsed.path.strip("/").split("/") if segment]
    except Exception:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "chatgpt.com"
        and len(path) == 4
        and path[0] == "g"
        and (path[1] == project_id or path[1].startswith(project_id + "-"))
        and path[2] == "c"
        and path[3] == conversation_id
        and bool(CONVERSATION_ID.fullmatch(conversation_id))
    )


async def _relay_snapshots(token, body, on_event):
    """Read ordered current-request text snapshots from the owned browser relay.

    The response is NDJSON, not simulated token streaming; every snapshot is
    a full replacement to support upstream edits and Markdown revisions.
    """
    timeout = aiohttp.ClientTimeout(total=420, sock_read=240)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            MESSAGE_URL + "/relay",
            headers={"Authorization": "Bearer " + token, "Accept": "application/x-ndjson"},
            json={**body, "stream": True},
        ) as response:
            if response.status != 200:
                raise HTTPException(502, f"Relay stream HTTP {response.status}")
            buffer = b""
            async for chunk in response.content.iter_chunked(32768):
                buffer += chunk
                if len(buffer) > 1048576:
                    raise HTTPException(502, "Relay progress event too large")
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise HTTPException(502, "Invalid relay progress event")
                    kind = event.get("type")
                    if kind == "heartbeat":
                        continue
                    if kind == "error":
                        error_message = str(event.get("message") or "Relay stream failed")[:250]
                        upstream_code = int(event.get("status") or 502)
                        # 504 after a confirmed submit is indeterminate, not
                        # proof that generation or delivery did not occur.
                        raise HTTPException(504 if upstream_code == 504 else 502, error_message)
                    if kind in ("progress", "status"):
                        await on_event(event)
                    if kind == "complete":
                        return event.get("result")
    raise HTTPException(504, "Relay stream ended without a verified completion")


async def _persist_job_progress(job, event):
    data = event if event.get("type") == "status" else event
    phase = str(data.get("state") or ("receiving_response" if data.get("partial") else "running"))
    allowed = {"opening_project", "message_submitted", "waiting_for_response",
               "receiving_response", "running"}
    if phase not in allowed:
        phase = "running"
    part = data.get("response") if event.get("type") == "progress" else None
    if isinstance(part, str) and part:
        # Persist only a verified current-job snapshot into this owner's
        # existing assistant placeholder. Do not create additional messages.
        chat = await Chats.get_chat_by_id_and_user_id(job["chat_id"], job["owner"])
        if chat and job.get("response_message_id"):
            original = await Chats.get_message_by_id_and_message_id(
                job["chat_id"], job["response_message_id"]
            )
            if (original and original.get("role") == "assistant"
                    and not original.get("done")
                    and original.get("aibit_relay_job_id") in (None, job["job_id"])
                    and not (original.get("content") and
                             original.get("aibit_relay_job_id") != job["job_id"])):
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    job["chat_id"], job["response_message_id"],
                    {**original, "content": part[:250000], "done": False,
                     "aibit_relay_job_id": job["job_id"]},
                    touch=False,
                )
    with _db() as db:
        if isinstance(part, str) and part:
            db.execute(
                "UPDATE jobs SET status='running',phase=?,response=?,updated_at=? WHERE owner=? AND job_id=? "
                "AND status IN ('queued','running')",
                (phase, part[:250000], int(time.time()), job["owner"], job["job_id"]),
            )
        else:
            db.execute(
                "UPDATE jobs SET status='running',phase=?,updated_at=? WHERE owner=? AND job_id=? "
                "AND status IN ('queued','running')",
                (phase, int(time.time()), job["owner"], job["job_id"]),
            )
        db.commit()


async def _legacy_worker(job_id, token, account, payload):
    """Submit once through the plain relay with its shared browser lock.

    Unlike the BIGRRO-specific async relay, this endpoint never adds ERP tool
    prompts. Its request has no repeat-safe upstream idempotency, so any
    ambiguous failure stays UNKNOWN and is never auto-resubmitted.
    """
    job = None
    try:
        job = _job_record_by_id(job_id)
        result = await _relay_snapshots(
            token, payload,
            lambda event: _persist_job_progress(job, event),
        )
        project = result.get("project") or {}
        response = result.get("response")
        conv = result.get("conversation_id")
        conv_url = result.get("conversation_url")
        if (result.get("success") is not True
            or result.get("account_id") != account
            or project.get("id") != job["project_id"]
            or not isinstance(response, str) or not response.strip()
            or not _verified_conversation_url(conv_url, job["project_id"], conv)):
            raise HTTPException(502, "ChatGPT did not confirm a response inside the selected project.")
        with _db() as db:
            binding = db.execute(
                "SELECT project_id FROM bindings WHERE owner=? AND chat_id=?",
                (job["owner"], job["chat_id"]),
            ).fetchone()
            if binding and binding["project_id"] != job["project_id"]:
                raise HTTPException(409, "Existing chat belongs to a different ChatGPT project.")
            db.execute(
                "INSERT INTO bindings(owner,account,chat_id,project_id,conversation_id,conversation_url)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(owner,chat_id) DO UPDATE SET "
                "conversation_id=excluded.conversation_id,conversation_url=excluded.conversation_url",
                (job["owner"], account, job["chat_id"], job["project_id"], conv, conv_url),
            )
            db.execute("UPDATE jobs SET status='completed',phase='completed',response=?,updated_at=? WHERE job_id=?",
                       (response, int(time.time()), job_id))
            db.commit()
        await _recover_completed_reply(_job_record(job["owner"], job_id))
    except HTTPException as exc:
        # After the upstream explicitly acknowledged submission, a missing
        # observer result does not prove the request failed. Preserve it for
        # account/project/turn-verified reconciliation, never an auto-retry.
        current = _job_record(job["owner"], job_id) if job else None
        submitted = (current or {}).get("phase") in (
            "message_submitted", "waiting_for_response", "receiving_response",
        )
        uncertain = exc.status_code == 504 or submitted
        with _db() as db:
            db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE job_id=?",
                       ("unknown" if uncertain else "failed",
                        str(exc.detail)[:550], int(time.time()), job_id))
            db.commit()
    except Exception:
        log.exception("AIBIT ChatGPT relay job failed")
        with _db() as db:
            db.execute("UPDATE jobs SET status='unknown',error=?,updated_at=? WHERE job_id=?",
                       ("Relay outcome uncertain; inspect the ChatGPT conversation before retrying.",
                        int(time.time()), job_id))
            db.commit()


def _job_record_by_id(job_id):
    with _db() as db:
        record = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(record) if record else None


def _reconcile_stale(job):
    if job["status"] in ("queued", "running") and int(time.time()) - job["updated_at"] > 600:
        with _db() as db:
            db.execute("UPDATE jobs SET status='unknown',error=?,updated_at=? WHERE job_id=?",
                       ("Relay worker may have restarted; inspect ChatGPT before resending.",
                        int(time.time()), job["job_id"]))
            db.commit()
        return _job_record(job["owner"], job["job_id"])
    return job


@router.get("/jobs")
async def list_jobs(user=Depends(get_verified_user)):
    await _authorized(user)
    # Job status is local owner-scoped evidence. Never acquire the shared
    # ChatGPT browser lock for a poll: a running generation owns that lock and
    # status polling must remain available while it is busy.
    with _db() as db:
        latest = db.execute(
            "SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 1",
            (user.id,),
        ).fetchone()
        account = latest["account"] if latest else None
        rows = [dict(row) for row in db.execute(
            "SELECT job_id FROM jobs WHERE owner=? AND account=? ORDER BY created_at DESC LIMIT 15",
            (user.id, account),
        )]
    result = []
    for row in rows:
        item = _job_record(user.id, row["job_id"])
        item = _reconcile_stale(item)
        if item["status"] == "completed":
            await _recover_completed_reply(item)
        result.append({key: item[key] for key in (
            "job_id", "chat_id", "project_id", "status", "phase", "response", "error", "updated_at", "response_message_id", "created_at"
        )})
    return {"jobs": result, "offline": False, "source": "local_job_ledger"}


async def complete_chat(form_data, metadata, user):
    """Intercept the selected relay model instead of generic OmniRoute transport."""
    await _authorized(user)
    token, account, _ = await _account_context()
    chat_id = metadata.get("chat_id")
    reply_id = metadata.get("message_id")
    if not isinstance(chat_id, str) or not chat_id or not isinstance(reply_id, str) or not reply_id:
        raise HTTPException(400, "Relay requires a saved AIBIT chat and message ID.")
    messages = form_data.get("messages") or []
    last = messages[-1] if messages else None
    if not isinstance(last, dict) or last.get("role") != "user" or not isinstance(last.get("content"), str):
        raise HTTPException(400, "Relay accepts only an explicit user text message, not automated task prompts.")
    message = last["content"].strip()
    if not message or len(message) > 100000:
        raise HTTPException(400, "Relay requires a nonempty text message under 100,000 characters.")
    folder_id = metadata.get("folder_id")
    saved_folder = await Chats.get_chat_folder_id(chat_id, user.id)
    if saved_folder:
        folder_id = saved_folder
    with _db() as db:
        binding = db.execute(
            "SELECT * FROM bindings WHERE owner=? AND chat_id=?", (user.id, chat_id)
        ).fetchone()
        project = db.execute(
            "SELECT * FROM projects WHERE owner=? AND account=? AND folder_id=?",
            (user.id, account, folder_id),
        ).fetchone()
        if binding:
            if binding["account"] != account or (project and project["project_id"] != binding["project_id"]):
                raise HTTPException(409, "This conversation is bound to another ChatGPT project or account.")
            project = db.execute(
                "SELECT * FROM projects WHERE owner=? AND account=? AND project_id=?",
                (user.id, account, binding["project_id"]),
            ).fetchone()
        if not project:
            raise HTTPException(400, "Select a synced ChatGPT project before sending a relay message.")
        pending = db.execute(
            "SELECT job_id FROM jobs WHERE owner=? AND chat_id=? AND status IN ('queued','running','unknown')",
            (user.id, chat_id),
        ).fetchone()
    # Before every send, revalidate the same logged-in account, the complete
    # project catalog, and membership in the exact selected project.
    catalog = await _relay("GET", "/projects", token, timeout=110)
    if catalog.get("complete") is not True or _verify_account(catalog) != account:
        raise HTTPException(503, "ChatGPT account or project catalog changed. Reconnect and sync again.")
    if not any(isinstance(item, dict) and item.get("id") == project["project_id"]
               for item in catalog.get("projects") or []):
        raise HTTPException(403, "Selected ChatGPT project is no longer accessible in this account.")
    digest = hashlib.sha256((user.id + "|" + chat_id + "|" + reply_id).encode()).hexdigest()[:48]
    job_id = "aibit_" + digest
    message_hash = hashlib.sha256(message.encode()).hexdigest()
    existing = _job_record(user.id, job_id)
    if existing and (existing["message_hash"] != message_hash or existing["project_id"] != project["project_id"]):
        raise HTTPException(409, "Relay request ID was reused for different content.")
    if pending and pending["job_id"] != job_id:
        raise HTTPException(409, "An earlier ChatGPT relay message is still processing. Check job status.")
    if not existing:
        with _db() as db:
            db.execute(
                "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,response_message_id,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (user.id, account, job_id, chat_id, project["project_id"], message_hash,
                 reply_id, "queued", int(time.time()), int(time.time())),
            )
            db.commit()
        payload = {
            "job_id": job_id,
            "thread_id": "aibit:" + user.id + ":" + chat_id,
            "chatgpt_project_id": project["project_id"],
            "project_name": project["name"],
            "message": message,
        }
        if binding and binding["conversation_url"]:
            payload["conversation_url"] = binding["conversation_url"]
        # Schedule once after persisting the AIBIT job. A page refresh cannot
        # cause a second upstream POST. The background worker updates the
        # durable ledger even if the browser disconnects.
        message_token, _ = _credentials(MESSAGE_TOKEN_FILE)
        asyncio.create_task(_legacy_worker(job_id, message_token, account, payload))
    deadline = time.monotonic() + 220
    while time.monotonic() < deadline:
        job = _reconcile_stale(_job_record(user.id, job_id))
        if job["status"] == "completed":
            return {
                "id": job_id, "object": "chat.completion", "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": job["response"]},
                             "finish_reason": "stop"}],
            }
        if job["status"] in ("failed", "unknown"):
            raise HTTPException(502, job["error"] or "ChatGPT relay requires review.")
        await asyncio.sleep(3)
    raise HTTPException(504, "ChatGPT relay is still running. Open project relay status; do not resend this turn.")


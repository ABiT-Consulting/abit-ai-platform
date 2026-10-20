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
CATALOG_URL = "http://127.0.0.1:8793"
MESSAGE_URL = "http://127.0.0.1:8794"
TOKEN_FILE = Path(r"C:\ProgramData\ABiT\BIGRRORelay\relay-token.txt")
MESSAGE_TOKEN_FILE = Path(r"C:\ProgramData\ABiT\erp-chatgpt-relay.token")
LEDGER = Path(DATA_DIR) / "aibit-chatgpt-relay.sqlite"
PROJECT_ID = re.compile(r"^g-p-[A-Za-z0-9_-]{5,190}$")
JOB_ID = re.compile(r"^aibit_[a-f0-9]{48}$")
CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{5,190}$")


def _credentials(token_file=TOKEN_FILE):
    # Never use a token supplied by a browser or request body.
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(503, "ChatGPT relay credentials are unavailable to AIBIT.") from exc
    if not token:
        raise HTTPException(503, "ChatGPT relay is not connected.")
    # Account/connection namespace: a token rotation deliberately requires a
    # fresh mapping reconciliation, instead of accidentally crossing accounts.
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
        response TEXT, error TEXT, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_aibit_jobs_chat ON jobs(owner,chat_id,updated_at);
    CREATE TABLE IF NOT EXISTS sync_state (
        owner TEXT NOT NULL, account TEXT NOT NULL, updated_at INTEGER NOT NULL,
        count INTEGER NOT NULL, PRIMARY KEY(owner,account)
    );
    """)
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
                    raise HTTPException(502, f"ChatGPT relay returned HTTP {response.status}.")
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
    _, account = _credentials()
    return _status(user.id, account)


@router.get("/projects")
async def get_projects(user=Depends(get_verified_user)):
    await _authorized(user)
    _, account = _credentials()
    with _db() as db:
        rows = db.execute(
            "SELECT project_id,folder_id,name,url,synced_at FROM projects "
            "WHERE owner=? AND account=? ORDER BY name,project_id",
            (user.id, account),
        ).fetchall()
    return {"projects": [dict(row) for row in rows], **_status(user.id, account)}


@router.post("/sync")
async def sync_projects(user=Depends(get_verified_user)):
    await _authorized(user)
    token, account = _credentials()
    payload = await _relay("GET", "/projects", token, timeout=100)
    if payload.get("ok") is not True or not isinstance(payload.get("projects"), list):
        raise HTTPException(502, "ChatGPT project catalog was incomplete; cached projects were kept.")
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
    taken = set()
    timestamp = int(time.time())
    for identifier, name in sorted(projects.items()):
        visible_name = "ChatGPT Â· " + name
        if visible_name.casefold() in taken:
            visible_name += " [" + identifier[-8:] + "]"
        taken.add(visible_name.casefold())
        folder_id = existing.get(identifier)
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
        elif folder.name != visible_name:
            updated = await Folders.update_folder_by_id_and_user_id(
                folder.id, user.id, FolderUpdateForm(name=visible_name)
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


async def _legacy_worker(job_id, token, account, payload):
    """Submit once through the plain relay with its shared browser lock.

    Unlike the BIGRRO-specific async relay, this endpoint never adds ERP tool
    prompts. Its request has no repeat-safe upstream idempotency, so any
    ambiguous failure stays UNKNOWN and is never auto-resubmitted.
    """
    job = None
    try:
        job = _job_record_by_id(job_id)
        with _db() as db:
            db.execute("UPDATE jobs SET status='running',updated_at=? WHERE job_id=?",
                       (int(time.time()), job_id))
            db.commit()
        result = await _relay("POST", "/relay", token, payload, timeout=330, url=MESSAGE_URL)
        project = result.get("project") or {}
        response = result.get("response")
        conv = result.get("conversation_id")
        conv_url = result.get("conversation_url")
        parsed = urlparse(conv_url) if isinstance(conv_url, str) else None
        if (result.get("success") is not True
            or project.get("id") != job["project_id"]
            or not isinstance(response, str) or not response.strip()
            or not isinstance(conv, str) or not CONVERSATION_ID.fullmatch(conv)
            or not parsed or parsed.scheme != "https" or parsed.hostname != "chatgpt.com"
            or ("/g/" + job["project_id"] + "/") not in parsed.path):
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
            db.execute("UPDATE jobs SET status='completed',response=?,updated_at=? WHERE job_id=?",
                       (response, int(time.time()), job_id))
            db.commit()
    except HTTPException as exc:
        with _db() as db:
            db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE job_id=?",
                       ("unknown" if exc.status_code == 504 else "failed",
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
    _, account = _credentials()
    with _db() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT job_id FROM jobs WHERE owner=? AND account=? ORDER BY created_at DESC LIMIT 15",
            (user.id, account),
        )]
    result = []
    for row in rows:
        item = _job_record(user.id, row["job_id"])
        item = _reconcile_stale(item)
        result.append({key: item[key] for key in (
            "job_id", "chat_id", "project_id", "status", "response", "error", "updated_at"
        )})
    return {"jobs": result}


async def complete_chat(form_data, metadata, user):
    """Intercept the selected relay model instead of generic OmniRoute transport."""
    await _authorized(user)
    token, account = _credentials()
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
                "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?, ?,?)",
                (user.id, account, job_id, chat_id, project["project_id"], message_hash,
                 "queued", int(time.time()), int(time.time())),
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


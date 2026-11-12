"""AIBIT-owned ChatGPT browser relay: account-scoped catalog and durable jobs.

All verified AIBIT administrators may use the shared Linux browser session.
Local projects, jobs and conversation mappings remain scoped to each user. No ChatGPT credentials are returned to clients.
The separate SQLite ledger avoids changing Open WebUI's chat tables.
"""
from __future__ import annotations

import asyncio
import copy
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
from pydantic import BaseModel, SecretStr
from fastapi.responses import JSONResponse
from open_webui.env import DATA_DIR
from open_webui.models.chats import Chats
from open_webui.models.folders import FolderForm, FolderUpdateForm, Folders
from open_webui.models.models import Models
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chatgpt_relay_input import parse_explicit_user_turn, stage_images, cleanup_images

log = logging.getLogger(__name__)
class PrivateRelayResponse(JSONResponse):
    def __init__(self, *args, **kwargs):
        kwargs["headers"] = {**(kwargs.get("headers") or {}), "Cache-Control": "no-store"}
        super().__init__(*args, **kwargs)


router = APIRouter(default_response_class=PrivateRelayResponse)
_PROJECT_IMPORT_LOCK = asyncio.Lock()
MODEL_ID = "omniroute.abit-chatgpt-relay/project-management"
# An empty project ID denotes a general ChatGPT conversation in the ledger.
GENERAL_PROJECT_ID = ""
CATALOG_URL = os.environ.get("ABIT_CHATGPT_RELAY_URL", "http://127.0.0.1:8819")
MESSAGE_URL = CATALOG_URL
TOKEN_FILE = Path(os.environ.get("ABIT_CHATGPT_RELAY_TOKEN_FILE", r"C:\ProgramData\ABiT\erp-chatgpt-relay.token"))
MESSAGE_TOKEN_FILE = TOKEN_FILE
FALLBACK_MODEL_ID = os.environ.get("ABIT_CHATGPT_BROWSER_FALLBACK_MODEL", "omniroute.auto/chat")
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
    if "conversation_url" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN conversation_url TEXT")
        conn.commit()
    if "handoff_claimed_at" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN handoff_claimed_at INTEGER")
        conn.commit()
    if "fallback_model_id" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN fallback_model_id TEXT")
        conn.commit()
    if "primary_error" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN primary_error TEXT")
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
                    "account",
                        "conversation",
                        "browser",
                        "pointer",
                        "sign-in",
                        "project",
                        "response",
                        "unavailable",
                        "timed out",
                    )
                    if not any(label in reason.lower() for label in accepted):
                        reason = "Unknown relay failure"
                    code = response.status if response.status in (400, 403, 409, 423) else 502
                    raise HTTPException(code, f"ChatGPT relay HTTP {response.status}: {reason[:240]}")
                try:
                    data = await response.json()
                except Exception as exc:
                    raise HTTPException(502, "ChatGPT relay returned invalid JSON.") from exc
                if not isinstance(data, dict):
                    raise HTTPException(502, "ChatGPT relay returned an invalid response.")
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        if method == "GET" and endpoint == "/projects":
            # Catalog reads are safe to retry, but never outlive the public proxy.
            # No local folders have been changed if this request times out.
            raise HTTPException(
                503,
                "ChatGPT project sync timed out: the browser is busy or unavailable. "
                "No projects were changed. Try again when the relay is ready.",
            ) from exc
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
    return {**_status(user.id, account), "offline": offline, "account_id": account}


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
    payload = await _relay("GET", "/projects", token, timeout=65)
    account = _verify_account(payload)
    if payload.get("ok") is not True or not isinstance(payload.get("projects"), list):
        raise HTTPException(502, "ChatGPT project catalog was incomplete; cached projects were kept.")
    if payload.get("complete") is not True:
        raise HTTPException(502, "Relay has not verified complete ChatGPT pagination; cached projects were preserved.")
    received = payload["projects"]
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

    return await _import_project_catalog(user, account, projects)


async def _import_project_catalog(user, account, projects, instructions=None):
    instructions = instructions or {}
    async with _PROJECT_IMPORT_LOCK:
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
            # Different linked accounts may have identically named projects. Preserve
            # existing folders and chats rather than colliding or merging by name.
            named = await Folders.get_folder_by_parent_id_and_user_id_and_name(None, user.id, visible_name)
            if named and (not folder or named.id != folder.id):
                if (named.meta or {}).get("chatgpt_project_id") == identifier and (named.meta or {}).get("chatgpt_account") == account:
                    folder = named
                else:
                    visible_name += " [" + account[-6:] + "-" + identifier[-6:] + "]"
            if folder is None:
                folder = await Folders.insert_new_folder(
                    user.id,
                    FolderForm(
                        name=visible_name,
                        meta={"chatgpt_project_id": identifier, "chatgpt_account": account},
                        data={"model_ids": [MODEL_ID], **({"system_prompt": instructions[identifier]} if identifier in instructions else {})},
                    ),
                )
                if not folder:
                    raise HTTPException(500, "Could not create a local project folder; retry sync.")
            elif (folder.name != visible_name
                  or (folder.meta or {}).get("chatgpt_account") != account
                  or (folder.data or {}).get("model_ids") != [MODEL_ID]
                  or identifier in instructions):
                # Repair only this mapped ChatGPT folder; keep its instructions,
                # files and unrelated metadata while enforcing the relay model.
                updated = await Folders.update_folder_by_id_and_user_id(
                    folder.id, user.id,
                    FolderUpdateForm(
                        name=visible_name,
                        meta={**(folder.meta or {}), "chatgpt_project_id": identifier,
                              "chatgpt_account": account},
                        data={**(folder.data or {}), "model_ids": [MODEL_ID],
                              **({"system_prompt": instructions[identifier]} if identifier in instructions else {})},
                    ),
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
        return {"synced": len(projects), **_status(user.id, account), "account_id": account}


class ProjectCreateForm(BaseModel):
    name: str
    instructions: str = ""
    request_id: str


@router.post("/projects/create")
async def create_project(form: ProjectCreateForm, user=Depends(get_verified_user)):
    """The owned browser creates and verifies name/instructions under one lock."""
    await _authorized(user)
    name, instructions = form.name.strip(), form.instructions.strip()
    if (not 1 <= len(name) <= 120 or len(instructions) > 12000
            or re.search(r"[\x00-\x1f]", name)
            or not re.fullmatch(r"[a-fA-F0-9-]{36}", form.request_id)):
        raise HTTPException(400, "Invalid project name, instructions or creation request.")
    token, account, _ = await _account_context()
    created = await _relay("POST", "/projects/create-synced", token, {
        "name": name, "instructions": instructions, "owner": user.id,
        "request_id": form.request_id, "account_id": account,
    }, timeout=180)
    project = created.get("project")
    if not isinstance(project, dict):
        raise HTTPException(502, "ChatGPT returned an invalid project result. Retry the same creation request.")
    project_id = project.get("chatgpt_project_id") or project.get("id")
    if (created.get("ok") is not True or _verify_account(created) != account
            or created.get("instructions_saved") is not True
            or not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id)
            or project.get("name") != name):
        raise HTTPException(502, "ChatGPT project was not fully verified. Retry the same creation request; do not create a second project.")
    await _import_project_catalog(user, account, {project_id: name}, {project_id: instructions})
    with _db() as db:
        mapped = db.execute(
            "SELECT folder_id FROM projects WHERE owner=? AND account=? AND project_id=?",
            (user.id, account, project_id),
        ).fetchone()
    if not mapped:
        raise HTTPException(502, "ChatGPT project exists but local mapping is incomplete. Retry the same request.")
    return {"ok": True, "project_id": project_id, "folder_id": mapped["folder_id"],
            "replayed": bool(created.get("replayed"))}


async def enforce_project_model(form_data, user):
    """Prevent alternate/direct/multi-model sends to a saved synchronized project."""
    model = form_data.get("model")
    entries = form_data.get("message_ids") or []
    alternatives = (list(entries) if isinstance(entries, dict) else
                    [row.get("model_id") for row in entries if isinstance(row, dict)])
    if model == MODEL_ID and not (form_data.get("model_item") or {}).get("direct") and all(mid == MODEL_ID for mid in alternatives):
        return
    chat_id = form_data.get("chat_id")
    folder_id = form_data.get("folder_id")
    if isinstance(chat_id, str) and await Chats.is_chat_owner(chat_id, user.id):
        # Saved scope wins over a spoofed or omitted folder_id in the request.
        folder_id = await Chats.get_chat_folder_id(chat_id, user.id)
        with _db() as db:
            binding = db.execute("SELECT project_id FROM bindings WHERE owner=? AND chat_id=?", (user.id, chat_id)).fetchone()
        if binding and binding["project_id"]:
            raise HTTPException(409, "ChatGPT Relay is locked for this synchronized project.")
    if isinstance(folder_id, str) and folder_id:
        folder = await Folders.get_folder_by_id(folder_id)
        if folder and (folder.meta or {}).get("chatgpt_project_id"):
            raise HTTPException(409, "ChatGPT Relay is locked for this synchronized project.")


@router.get("/account/status")
async def account_status(user=Depends(get_verified_user)):
    await _authorized(user)
    token, _ = _credentials()
    return await _relay("POST", "/account/relink/status", token, {"owner": user.id}, timeout=35)


class RelinkInput(BaseModel):
    kind: str
    x: float | None = None
    y: float | None = None
    text: SecretStr | None = None
    direction: int = 1
    key: str | None = None


@router.post("/account/relink/start")
async def start_account_relink(user=Depends(get_verified_user)):
    await _authorized(user)
    # The Chrome account session is shared: never switch it during active sends.
    with _db() as db:
        pending = db.execute(
            "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
    if pending:
        raise HTTPException(409, "Finish active ChatGPT Relay messages before changing accounts.")
    token, _ = _credentials()
    return await _relay("POST", "/account/relink/start", token, {"owner": user.id}, timeout=50)


@router.post("/account/relink/view")
async def view_account_relink(user=Depends(get_verified_user)):
    await _authorized(user)
    token, _ = _credentials()
    return await _relay("POST", "/account/relink/view", token, {"owner": user.id}, timeout=40)


@router.post("/account/relink/input")
async def input_account_relink(form: RelinkInput, user=Depends(get_verified_user)):
    await _authorized(user)
    token, _ = _credentials()
    return await _relay("POST", "/account/relink/input", token,
                        {"owner": user.id, **form.model_dump(exclude={"text"}),
                         "text": form.text.get_secret_value() if form.text else None}, timeout=40)


@router.post("/account/relink/cancel")
async def cancel_account_relink(user=Depends(get_verified_user)):
    await _authorized(user)
    token, _ = _credentials()
    return await _relay("POST", "/account/relink/cancel", token, {"owner": user.id}, timeout=30)



class RelayBrowserRequest(BaseModel):
    chat_id: str


class RelayBrowserInput(RelayBrowserRequest):
    kind: str
    x: float | None = None
    y: float | None = None
    direction: int = 1
    text: SecretStr | None = None
    key: str | None = None


async def _browser_scope(user, chat_id: str):
    """Resolve the exact user-owned ChatGPT conversation without browser locks.

    The ChatGPT send may hold the relay lock for several minutes waiting for
    approval, so this must only read the durable ledger. The relay itself
    independently checks the *current* logged-in account on every interaction.
    """
    await _authorized(user)
    if not isinstance(chat_id, str) or not chat_id or len(chat_id) > 200:
        raise HTTPException(404, "Saved AIBIT conversation was not found.")
    if not await Chats.is_chat_owner(chat_id, user.id):
        raise HTTPException(404, "Saved AIBIT conversation was not found.")
    with _db() as db:
        jobs = [dict(row) for row in db.execute(
            "SELECT * FROM jobs WHERE owner=? AND chat_id=? "
            "AND conversation_url IS NOT NULL ORDER BY created_at DESC,updated_at DESC",
            (user.id, chat_id),
        ).fetchall()]
        binding = db.execute(
            "SELECT account,project_id,conversation_url,conversation_id FROM bindings "
            "WHERE owner=? AND chat_id=?", (user.id, chat_id),
        ).fetchone()
    for job in jobs:
        url = _safe_conversation_url(job["conversation_url"], job["project_id"])
        if url and ACCOUNT_ID.fullmatch(job["account"]):
            return {"conversation_url": url, "account_id": job["account"]}
    if binding:
        url = _safe_conversation_url(binding["conversation_url"], binding["project_id"])
        if (url and ACCOUNT_ID.fullmatch(binding["account"])
                and _verified_conversation_url(url, binding["project_id"], binding["conversation_id"])):
            return {"conversation_url": url, "account_id": binding["account"]}
    raise HTTPException(409, "This chat has no verified relay conversation to display yet.")


async def _browser_command(user, chat_id: str, operation: str, form: RelayBrowserInput | None = None):
    payload = await _browser_scope(user, chat_id)
    if form is not None:
        payload.update(form.model_dump(exclude={"text", "chat_id"}))
        payload["text"] = form.text.get_secret_value() if form.text else None
    # Server-derived identity only: browser callers may never pick another owner or chat.
    payload["owner"] = user.id
    payload["chat_id"] = chat_id
    token, _ = _credentials()
    try:
        return await _relay(
            "POST", "/browser/" + operation, token, payload,
            timeout=50 if operation == "open" else 32,
        )
    except Exception:
        if operation in ("open", "view"):
            # A browser read error is an event, not permission to resubmit.
            # Completed jobs must never be executed a second time.
            from open_webui.utils.abitdot_failover import dispatch
            latest = _latest_chat_job(user.id, chat_id)
            if latest and latest["status"] in ("failed", "unknown"):
                await dispatch(user.id, chat_id, "relay_chat_unreadable",
                               account=latest["account"])
        raise


@router.post("/browser/open")
async def open_relay_browser(form: RelayBrowserRequest, user=Depends(get_verified_user)):
    return await _browser_command(user, form.chat_id, "open")


@router.post("/browser/view")
async def view_relay_browser(form: RelayBrowserRequest, user=Depends(get_verified_user)):
    return await _browser_command(user, form.chat_id, "view")


@router.post("/browser/input")
async def input_relay_browser(form: RelayBrowserInput, user=Depends(get_verified_user)):
    return await _browser_command(user, form.chat_id, "input", form)


def _latest_chat_job(owner, chat_id):
    with _db() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE owner=? AND chat_id=? "
            "ORDER BY created_at DESC LIMIT 1", (owner, chat_id),
        ).fetchone()
    return dict(row) if row else None


def _latest_unfinished_job(owner, chat_id):
    """Select the actual unresolved turn, not a later failed opening attempt."""
    with _db() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE owner=? AND chat_id=? "
            "AND status IN ('queued','running','unknown') "
            "ORDER BY updated_at DESC LIMIT 1", (owner, chat_id),
        ).fetchone()
    return dict(row) if row else None


def _has_unfinished_job(owner, chat_id):
    """Scheduled runs must not append over an accepted or uncertain relay turn."""
    with _db() as db:
        return db.execute(
            "SELECT 1 FROM jobs WHERE owner=? AND chat_id=? "
            "AND status IN ('queued','running','unknown') LIMIT 1", (owner, chat_id)
        ).fetchone() is not None


def _job_record(owner, job_id):
    with _db() as db:
        record = db.execute(
            "SELECT * FROM jobs WHERE owner=? AND job_id=?", (owner, job_id)
        ).fetchone()
    return dict(record) if record else None


def claim_postsubmit_handoff(owner, job_id):
    """Atomically claim one secondary-model handoff; never resend ChatGPT."""
    with _db() as db:
        now = int(time.time())
        result = db.execute(
            "UPDATE jobs SET handoff_claimed_at=?,updated_at=? "
            "WHERE owner=? AND job_id=? AND status='unknown' AND phase='fallback_ready' "
            "AND handoff_claimed_at IS NULL AND (response IS NULL OR response='')",
            (now, now, owner, job_id),
        )
        db.commit()
        return result.rowcount == 1


def finish_postsubmit_handoff(owner, job_id, succeeded, reason=None):
    """Keep the original relay error in audit; terminalize a failed handoff."""
    with _db() as db:
        result = db.execute(
            "UPDATE jobs SET status=?,phase=?,error=CASE WHEN ? THEN error ELSE ? END,"
            "updated_at=? WHERE owner=? AND job_id=? AND status='unknown' "
            "AND phase='fallback_ready' AND handoff_claimed_at IS NOT NULL",
            (
                "handed_off" if succeeded else "failed",
                "fallback_ready" if succeeded else "fallback_failed",
                int(bool(succeeded)),
                str(reason or "Selected secondary model failed")[:550],
                int(time.time()), owner, job_id,
            ),
        )
        db.commit()
        return result.rowcount == 1


def save_postsubmit_fallback_result(owner, job_id, model_id, content):
    """Commit one verified secondary answer to its original job."""
    if (not isinstance(model_id, str) or not model_id or model_id == MODEL_ID
            or not isinstance(content, str) or not content.strip()):
        return False
    with _db() as db:
        result = db.execute(
            "UPDATE jobs SET status='completed',phase='fallback_completed',"
            "fallback_model_id=?,primary_error=error,error=NULL,response=?,updated_at=? "
            "WHERE owner=? AND job_id=? AND status='unknown' AND phase='fallback_ready' "
            "AND handoff_claimed_at IS NOT NULL AND (response IS NULL OR response='')",
            (model_id, content[:250000], int(time.time()), owner, job_id),
        )
        db.commit()
        return result.rowcount == 1


def abort_postsubmit_handoff(owner, job_id, reason):
    """Expose a terminal error if the configured secondary model is unavailable."""
    with _db() as db:
        result = db.execute(
            "UPDATE jobs SET status='failed',phase='fallback_failed',error=?,updated_at=? "
            "WHERE owner=? AND job_id=? AND status='unknown' AND phase='fallback_ready' "
            "AND handoff_claimed_at IS NULL",
            (str(reason)[:550], int(time.time()), owner, job_id),
        )
        db.commit()
        return result.rowcount == 1


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
    target_model = job.get("fallback_model_id") or MODEL_ID
    if (original.get("content") == job["response"] and original.get("done")
            and not original.get("error") and original.get("model") == target_model):
        return True
    # A partial produced by this exact relay job may be revised by ChatGPT.
    # Never overwrite another model's or another request's response.
    if (original.get("content") and original["content"] != job["response"]
            and not (original.get("model") == MODEL_ID and not original.get("done"))):
        return False
    patched = {**original, "content": job["response"], "done": True,
               "model": target_model, "id": job["response_message_id"],
               "error": None, "aibit_relay_job_id": job["job_id"]}
    if target_model != MODEL_ID:
        patched["selectedModelId"] = target_model
        patched["modelName"] = target_model
        patched["relayPhase"] = "fallback_completed"
        patched["relayError"] = ""
    return bool(await Chats.upsert_message_to_chat_by_id_and_message_id(
        job["chat_id"], job["response_message_id"], patched
    ))


def _verified_conversation_url(url, project_id, conversation_id):
    """Validate the exact ChatGPT conversation scope: general or synced project.

    Project URLs may use /g/g-p-ID-project-title/c/CONVERSATION.
    General conversations use /c/CONVERSATION without a project prefix.
    """
    if not isinstance(url, str) or not isinstance(conversation_id, str):
        return False
    try:
        parsed = urlparse(url)
        path = [segment for segment in parsed.path.strip("/").split("/") if segment]
    except Exception:
        return False
    if (parsed.scheme != "https" or parsed.hostname != "chatgpt.com"
            or not CONVERSATION_ID.fullmatch(conversation_id)):
        return False
    if project_id == GENERAL_PROJECT_ID:
        # General chats must be /c/ID. Never accept project-scoped URLs.
        return len(path) == 2 and path[0] == "c" and path[1] == conversation_id
    return (
        bool(PROJECT_ID.fullmatch(project_id))
        and len(path) == 4
        and path[0] == "g"
        and (path[1] == project_id or path[1].startswith(project_id + "-"))
        and path[2] == "c"
        and path[3] == conversation_id
    )


def _safe_conversation_url(url, project_id):
    """Return only a verified ChatGPT conversation link, without query secrets."""
    if not isinstance(url, str) or not isinstance(project_id, str):
        return None
    try:
        parsed = urlparse(url)
        # Reject credentials, lookalike domains, ports and ambiguous paths.
        if parsed.scheme != "https" or parsed.netloc != "chatgpt.com":
            return None
        path = parsed.path.rstrip("/")
        if "//" in path or any(ord(c) < 32 for c in url):
            return None
        conversation_id = path.rsplit("/", 1)[-1]
        if not _verified_conversation_url(url, project_id, conversation_id):
            return None
        return "https://chatgpt.com" + path
    except (ValueError, TypeError):
        return None


def _job_conversation_url(job):
    """Resolve only this owner's job or exact account/chat/project binding."""
    url = _safe_conversation_url(job.get("conversation_url"), job["project_id"])
    if url:
        return url
    with _db() as db:
        binding = db.execute(
            "SELECT conversation_url,conversation_id FROM bindings "
            "WHERE owner=? AND account=? AND chat_id=? AND project_id=?",
            (job["owner"], job["account"], job["chat_id"], job["project_id"]),
        ).fetchone()
    if binding and _verified_conversation_url(
        binding["conversation_url"], job["project_id"], binding["conversation_id"]
    ):
        return _safe_conversation_url(binding["conversation_url"], job["project_id"])
    return None


async def _relay_snapshots(token, body, on_event):
    """Read ordered current-request text snapshots from the owned browser relay.

    The response is NDJSON, not simulated token streaming; every snapshot is
    a full replacement to support upstream edits and Markdown revisions.
    """
    # A healthy relay emits heartbeats even before ChatGPT produces text.
    # Bound connection and stream silence independently of generation time.
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=300)
    last_heartbeat = 0.0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            MESSAGE_URL + "/relay",
            headers={"Authorization": "Bearer " + token, "Accept": "application/x-ndjson"},
            json={**body, "stream": True},
        ) as response:
            if response.status != 200:
                if response.status == 503:
                    try:
                        error = await response.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError):
                        error = {}
                    if isinstance(error, dict) and error.get("code") in (
                        "CHATGPT_BROWSER_BUSY", "CHATGPT_BROWSER_RATE_LIMIT",
                    ):
                        raise BrowserFallbackRequired("browser_busy")
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
                        now = time.monotonic()
                        if now - last_heartbeat >= 20:
                            await on_event({"type": "heartbeat"})
                            last_heartbeat = now
                        continue
                    if kind == "error":
                        if event.get("code") in ("CHATGPT_BROWSER_BUSY", "CHATGPT_BROWSER_RATE_LIMIT"):
                            raise BrowserFallbackRequired("browser_busy")
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
    if event.get("type") == "heartbeat":
        # Queued jobs must not be marked stale while the shared browser is
        # processing another user's turn; never claim a submission occurred.
        with _db() as db:
            db.execute(
                "UPDATE jobs SET updated_at=?,phase=COALESCE(phase,'waiting_for_browser') "
                "WHERE owner=? AND job_id=? AND status IN ('queued','running')",
                (int(time.time()), job["owner"], job["job_id"]),
            )
            db.commit()
        return
    data = event
    phase = str(data.get("state") or ("receiving_response" if data.get("partial") else "running"))
    allowed = {"opening_project", "message_submitted", "waiting_for_response",
               "receiving_response", "running"}
    if phase not in allowed:
        phase = "running"
    # Save the URL as soon as the upstream acknowledges this submitted turn.
    # It must survive timeouts even when no assistant text has arrived yet.
    conversation_url = _safe_conversation_url(data.get("conversation_url"), job["project_id"])
    if (conversation_url and data.get("account_id") == job["account"]
            and phase in {"message_submitted", "waiting_for_response", "receiving_response"}):
        with _db() as db:
            db.execute(
                "UPDATE jobs SET conversation_url=? WHERE owner=? AND account=? AND job_id=? "
                "AND (conversation_url IS NULL OR conversation_url=?)",
                (conversation_url, job["owner"], job["account"], job["job_id"], conversation_url),
            )
            db.commit()
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
                    and (not original.get("content") or original.get("model") == MODEL_ID)):
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    job["chat_id"], job["response_message_id"],
                    {**original, "content": part[:250000], "done": False,
                     "model": MODEL_ID, "aibit_relay_job_id": job["job_id"]},
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
    if data.get("extended_wait") is True:
        log.info("ChatGPT is still working. Monitoring the original relay job %s", job["job_id"])
    elif phase == "waiting_for_response" and job.get("phase") != phase:
        log.info("Waiting for ChatGPT response (relay job %s)", job["job_id"])
    job["phase"] = phase


async def _prefer_abitdot(job, account, fallback_context=None):
    """Try abitdot first, then preserve existing selected-model semantics.

    This event is triggered by the actual relay failure, not a monitoring job.
    A doubtful response is not permission to replay the task elsewhere.
    """
    if not job or job.get("response") or job["status"] == "completed":
        return "completed"
    from open_webui.utils.abitdot_failover import dispatch, commit_verified_dot_reply
    result = await dispatch(job["owner"], job["chat_id"],
                            "relay_job_failed:" + str(job.get("phase")),
                            account=account, job_id=job["job_id"])
    if result["state"] == "accepted":
        if commit_verified_dot_reply(job["owner"], job["job_id"],
                                     result.get("reply")):
            await _recover_completed_reply(_job_record(job["owner"], job["job_id"]))
            return "dot_completed"
        return "unknown"
    if not result.get("safe_to_fallback"):
        return "unknown"
    # The original selected-model handoff is untouched and remains limited
    # to its original safe eligibility conditions and durable claim.
    current = _job_record(job["owner"], job["job_id"])
    if (fallback_context and current and current["status"] == "unknown"
            and current.get("phase") == "fallback_ready"):
        await _run_background_handoff(current, fallback_context)
        return "selected_model"
    return "unavailable"


async def _run_background_handoff(job, context):
    """Continue the selected secondary model independently of the browser socket.

    Only the exact owned assistant placeholder is updated; the original ChatGPT
    submission is not repeated. A durable claim is required before any provider
    call, and the normal model dispatcher enforces access controls.
    """
    if not context:
        return False
    request, original_data, user, fallback_id = context
    if (not isinstance(fallback_id, str) or not fallback_id.strip()
            or fallback_id == MODEL_ID):
        return False
    if not claim_postsubmit_handoff(job["owner"], job["job_id"]):
        return False
    try:
        from starlette.requests import Request
        from open_webui.utils.chat import _generate_primary_chat_completion
        from open_webui.utils.model_fallback import _has_current_tool_work
        data = copy.deepcopy(original_data)
        if (not isinstance(data.get("messages"), list)
                or _has_current_tool_work(data["messages"])
                or any(message.get("tool_calls") or message.get("role") in ("tool", "function")
                       for message in data["messages"] if isinstance(message, dict))
                or data.get("tools") or payload_has_images(data)):
            raise ValueError("Secondary model handoff cannot replay tool or image work")
        model = request.app.state.MODELS.get(fallback_id)
        if (not model or model.get("owned_by") == "arena"
                or model.get("pipe") or model.get("direct")):
            raise ValueError("Configured secondary model is unavailable")
        data["model"] = fallback_id
        data["stream"] = False
        extra = {**(getattr(request.state, "metadata", None) or {}),
                 **(data.get("metadata") or {}),
                 "fallback_attempted": True,
                 "fallback_from": MODEL_ID,
                 "fallback_model_id": fallback_id,
                 "model_id": fallback_id,
                 "model": model}
        data["metadata"] = extra
        clone_scope = {**request.scope, "state": dict(request.scope.get("state") or {})}
        scoped_request = Request(clone_scope)
        scoped_request.state.metadata = extra
        response = await _generate_primary_chat_completion(
            scoped_request, data, user, bypass_filter=False,
        )
        if not isinstance(response, dict):
            raise ValueError("Secondary model did not return a verified final answer")
        choices = response.get("choices")
        answer = ((choices or [{}])[0].get("message") or {}).get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Secondary model returned no final answer")
        existing = await Chats.get_message_by_id_and_message_id(
            job["chat_id"], job["response_message_id"],
        )
        if not existing or existing.get("role") != "assistant":
            raise ValueError("Original response placeholder was removed")
        if (existing.get("selectedModelId")
                or (existing.get("content") and existing.get("done")
                    and not existing.get("error"))):
            raise ValueError("An assistant already answered this exact turn")
        if not save_postsubmit_fallback_result(
            job["owner"], job["job_id"], fallback_id, answer,
        ):
            raise ValueError("Secondary answer was already accepted or superseded")
        await _recover_completed_reply(_job_record(job["owner"], job["job_id"]))
        log.info("Recovered ChatGPT relay job %s with selected secondary model %s",
                 job["job_id"], fallback_id)
        return True
    except asyncio.CancelledError:
        finish_postsubmit_handoff(
            job["owner"], job["job_id"], False, "Secondary handoff interrupted",
        )
        raise
    except Exception as exc:
        log.warning("Secondary handoff could not finish job %s: %s",
                    job["job_id"], type(exc).__name__)
        finish_postsubmit_handoff(
            job["owner"], job["job_id"], False,
            "Selected secondary model could not complete the handoff.",
        )
        return False


def payload_has_images(form_data):
    """Reject an automatic text-only handoff when it would lose images."""
    for message in form_data.get("messages") or []:
        if not isinstance(message, dict):
            return True
        content = message.get("content")
        if isinstance(content, list):
            return True
        if message.get("files"):
            return True
    return False


async def _legacy_worker(job_id, token, account, payload, fallback_context=None):
    """Submit once through the plain relay with its shared browser lock.

    Unlike the BIGRRO-specific async relay, this endpoint never adds ERP tool
    prompts. Its request has no repeat-safe upstream idempotency, so any
    ambiguous failure stays UNKNOWN and is never auto-resubmitted.
    """
    job = None
    try:
        job = _job_record_by_id(job_id)
        result = await _relay_snapshots(
            token, {**payload, "account_id": account},
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
            raise HTTPException(502, "ChatGPT did not confirm a response in the expected conversation.")
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
            db.execute(
                "UPDATE jobs SET status='completed',phase='completed',response=?,conversation_url=?,error=NULL,updated_at=? "
                "WHERE job_id=? AND owner=? AND account=?",
                (response, _safe_conversation_url(conv_url, job["project_id"]),
                 int(time.time()), job_id, job["owner"], account),
            )
            db.commit()
        await _recover_completed_reply(_job_record(job["owner"], job_id))
    except BrowserFallbackRequired:
        # Fail over only on an explicit pre-submit capacity refusal. If the
        # submission event was observed, keep the original outcome for review.
        current = _job_record(job["owner"], job_id) if job else None
        submitted = (current or {}).get("phase") in (
            "message_submitted", "waiting_for_response", "receiving_response",
        )
        with _db() as db:
            db.execute(
                "UPDATE jobs SET status=?,phase=?,error=?,updated_at=? WHERE job_id=?",
                ("unknown" if submitted else "failed",
                 ("fallback_ready" if not (current or {}).get("response") else "needs_review")
                 if submitted else "fallback_ready",
                 "Upstream outcome requires review" if submitted else "Browser busy before submission",
                 int(time.time()), job_id),
            )
            db.commit()
        if not (current or {}).get("response"):
            await _prefer_abitdot(_job_record(job["owner"], job_id),
                                  account, fallback_context if submitted else None)
    except HTTPException as exc:
        # After the upstream explicitly acknowledged submission, a missing
        # observer result does not prove the request failed. Preserve it for
        # account/project/turn-verified reconciliation, never an auto-retry.
        current = _job_record(job["owner"], job_id) if job else None
        submitted = (current or {}).get("phase") in (
            "message_submitted", "waiting_for_response", "receiving_response",
        )
        uncertain = exc.status_code == 504 or submitted
        detail = str(exc.detail or "")
        rate_limited = (
            submitted and not (current or {}).get("response")
            and (exc.status_code == 429 or "http 429" in detail.lower()
                 or "rate limit" in detail.lower()
                 or "too many requests" in detail.lower())
        )
        with _db() as db:
            db.execute("UPDATE jobs SET status=?,phase=CASE WHEN ? THEN 'fallback_ready' ELSE phase END,"
                       "error=?,updated_at=? WHERE job_id=? AND status IN ('queued','running')",
                       ("unknown" if uncertain else "failed", int(bool(rate_limited)),
                        detail[:550], int(time.time()), job_id))
            db.commit()
        # The Dot precedes the existing selected-model fallback.
        # A verified no-send failure may fall through to the old model;
        # an unknown primary execution stays protected against replay.
        definite_no_send = (
            not submitted and not uncertain and
            any(term in detail.lower() for term in (
                "no message was sent", "no message sent",
                "no submission performed",
            ))
        )
        if definite_no_send:
            with _db() as db:
                db.execute(
                    "UPDATE jobs SET status='unknown',phase='fallback_ready' "
                    "WHERE owner=? AND job_id=? AND status='failed' "
                    "AND (response IS NULL OR response='')",
                    (job["owner"], job_id),
                )
                db.commit()
        if not (current or {}).get("response"):
            await _prefer_abitdot(
                _job_record(job["owner"], job_id), account,
                fallback_context if (rate_limited or definite_no_send) else None,
            )
    except Exception:
        log.exception("AIBIT ChatGPT relay job failed")
        with _db() as db:
            db.execute("UPDATE jobs SET status='unknown',error=?,updated_at=? WHERE job_id=? "
                       "AND status IN ('queued','running')",
                       ("Relay outcome uncertain; inspect the ChatGPT conversation before retrying.",
                        int(time.time()), job_id))
            db.commit()
        if job:
            await _prefer_abitdot(_job_record(job["owner"], job_id), account)
    finally:
        if payload.get("input_attachments"):
            cleanup_images(payload["input_attachments"], job_id)


def _job_record_by_id(job_id):
    with _db() as db:
        record = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(record) if record else None


def _reconcile_stale(job):
    if (job["status"] == "unknown" and job.get("phase") == "fallback_ready"
            and job.get("handoff_claimed_at")
            and int(time.time()) - job["handoff_claimed_at"] > 900):
        finish_postsubmit_handoff(
            job["owner"], job["job_id"], False,
            "Selected secondary model handoff exceeded its recovery window.",
        )
        return _job_record(job["owner"], job["job_id"])
    if job["status"] in ("queued", "running") and int(time.time()) - job["updated_at"] > 600:
        with _db() as db:
            db.execute("UPDATE jobs SET status='unknown',error=?,updated_at=? WHERE job_id=? "
                       "AND status IN ('queued','running') AND updated_at=?",
                       ("Relay worker stopped reporting progress. Check the original ChatGPT conversation.",
                        int(time.time()), job["job_id"], job["updated_at"]))
            db.commit()
        return _job_record(job["owner"], job["job_id"])
    return job


@router.get("/jobs")
async def list_jobs(user=Depends(get_verified_user), chat_id: str | None = None):
    await _authorized(user)
    if chat_id is not None and not await Chats.is_chat_owner(chat_id, user.id):
        raise HTTPException(404, "Saved AIBIT conversation was not found.")
    # Job status is local owner-scoped evidence. Never acquire the shared
    # ChatGPT browser lock for a poll: a running generation owns that lock and
    # status polling must remain available while it is busy.
    with _db() as db:
        latest = db.execute(
            "SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 1",
            (user.id,),
        ).fetchone()
        account = latest["account"] if latest else None
        if chat_id is not None:
            # Opening an older chat must not depend on the last 15 global jobs
            # or on the user ever having synced a project for general chat.
            rows = [dict(row) for row in db.execute(
                "SELECT job_id FROM jobs WHERE owner=? AND chat_id=? ORDER BY created_at DESC",
                (user.id, chat_id),
            )]
        else:
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
        public_job = {key: item[key] for key in (
            "job_id", "chat_id", "project_id", "status", "phase", "response", "error", "updated_at", "response_message_id", "created_at", "fallback_model_id"
        )}
        public_job["conversation_url"] = _job_conversation_url(item)
        result.append(public_job)
    return {"jobs": result, "offline": False, "source": "local_job_ledger"}


class BrowserFallbackRequired(Exception):
    """Secondary-model handoff signal, never permission to resend to ChatGPT."""

    def __init__(self, reason="browser_busy", postsubmit_job_id=None):
        super().__init__(reason)
        self.postsubmit_job_id = postsubmit_job_id


async def _browser_fallback_reason():
    """Bounded, read-only capacity probe. Never submit or retry a user turn."""
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(MESSAGE_URL.rstrip("/") + "/health") as response:
                if response.status != 200:
                    return "browser_offline"
                health = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return "browser_offline"
    if not isinstance(health, dict) or not health.get("ready"):
        return "browser_unavailable"
    if health.get("rate_limited"):
        return "browser_rate_limited"
    capacity = max(1, int(health.get("max_parallel_relay_turns") or 1))
    if health.get("browser_busy") or int(health.get("active_relay_turns") or 0) >= capacity:
        return "browser_busy"
    return None


async def _ensure_chat_title(chat_id: str, user_id: str, user_message: str) -> str | None:
    """Replace the placeholder title with this chat's first user request."""
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
    if not chat:
        return None
    current = str(getattr(chat, "title", "") or "").strip()
    if current and current.casefold() not in {"new chat", "new conversation"}:
        return current
    title = " ".join(str(user_message or "").split())
    if not title:
        return current or None
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    await Chats.update_chat_title_by_id(chat_id, title)
    return title


async def complete_chat(form_data, metadata, user, request=None):
    """Intercept the selected relay model instead of generic OmniRoute transport."""
    await _authorized(user)
    chat_id = metadata.get("chat_id")
    reply_id = metadata.get("message_id")
    if not isinstance(chat_id, str) or not chat_id or not isinstance(reply_id, str) or not reply_id:
        raise HTTPException(400, "Relay requires a saved AIBIT chat and message ID.")
    messages = form_data.get("messages") or []
    last = messages[-1] if messages else None
    message, input_images, message_hash = await parse_explicit_user_turn(last, user)
    if not await Chats.is_chat_owner(chat_id, user.id):
        raise HTTPException(404, "Saved AIBIT conversation was not found.")

    # The saved folder is authoritative; browser metadata cannot change scope.
    folder_id = await Chats.get_chat_folder_id(chat_id, user.id)
    with _db() as db:
        binding = db.execute(
            "SELECT * FROM bindings WHERE owner=? AND chat_id=?", (user.id, chat_id)
        ).fetchone()
        folder_account = db.execute(
            "SELECT account FROM projects WHERE owner=? AND folder_id=? LIMIT 1",
            (user.id, folder_id),
        ).fetchone() if folder_id else None
        last_sync = db.execute(
            "SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 1",
            (user.id,),
        ).fetchone()
    # Only use owner-scoped, previously verified identities. The relay checks
    # the actual logged-in account INSIDE the locked browser before any send.
    # This avoids the global 12-minute browser queue for each local message.
    account = (binding["account"] if binding else
               folder_account["account"] if folder_account else
               last_sync["account"] if last_sync else None)
    if account and ACCOUNT_ID.fullmatch(account):
        token, _ = _credentials(MESSAGE_TOKEN_FILE)
    else:
        token, account, _ = await _account_context()
    with _db() as db:
        project = db.execute(
            "SELECT * FROM projects WHERE owner=? AND account=? AND folder_id=?",
            (user.id, account, folder_id),
        ).fetchone()
        folder_project = project
        if binding:
            if binding["account"] != account or (project and project["project_id"] != binding["project_id"]):
                raise HTTPException(409, "This conversation is bound to another ChatGPT project or account.")
            if binding["project_id"] == GENERAL_PROJECT_ID:
                # General conversations never silently become project chats.
                project = None
            else:
                # Keep the previous binding if its folder was removed.
                project = db.execute(
                    "SELECT * FROM projects WHERE owner=? AND account=? AND project_id=?",
                    (user.id, account, binding["project_id"]),
                ).fetchone()
                if not project:
                    raise HTTPException(403, "Bound ChatGPT project is no longer synced to this account.")
        pending = db.execute(
            # An old UNKNOWN remains audited and is never automatically retried.
            # Do not let historical uncertain turns block every new, explicitly
            # composed message forever. Recent ambiguous turns stay guarded.
            "SELECT job_id FROM jobs WHERE owner=? AND chat_id=? "
            "AND (status IN ('queued','running') OR "
            "(status='unknown' AND updated_at>=?)) "
            "ORDER BY created_at DESC LIMIT 1",
            (user.id, chat_id, int(time.time()) - 3600),
        ).fetchone()

    if folder_id and not folder_project:
        # An out-of-date synced project is NOT a request for general chat.
        folder = await Folders.get_folder_by_id_and_user_id(folder_id, user.id)
        if folder and (folder.meta or {}).get("chatgpt_project_id"):
            raise HTTPException(409, "Selected ChatGPT project needs to be synced again.")

    project_id = project["project_id"] if project else GENERAL_PROJECT_ID
    # Membership was verified at project sync and is tied to this owner's
    # folder. The relay opens the exact project ID and checks account identity
    # before submitting. Re-fetching all projects here caused pre-send timeouts.
    digest = hashlib.sha256((user.id + "|" + chat_id + "|" + reply_id).encode()).hexdigest()[:48]
    job_id = "aibit_" + digest
    existing = _job_record(user.id, job_id)
    if existing and (existing["message_hash"] != message_hash or existing["project_id"] != project_id):
        raise HTTPException(409, "Relay request ID was reused for different content.")
    if pending and pending["job_id"] != job_id:
        raise HTTPException(409, "An earlier ChatGPT relay message is still processing. Check job status.")
    # An existing relay job may already be submitted. Never send that turn to
    # a second provider, even if the browser is currently busy or offline.
    if not existing and not pending:
        fallback_reason = await _browser_fallback_reason()
        if fallback_reason:
            # An unavailable relay cannot execute the original Project chat.
            # Try the owner's Dot through the same authenticated relay first.
            from open_webui.utils.abitdot_failover import dispatch
            dot = await dispatch(user.id, chat_id,
                                 "relay_pre_submit_" + fallback_reason,
                                 account=account)
            if dot["state"] == "accepted":
                return {
                    "id": dot["event_id"], "object": "chat.completion",
                    "created": int(time.time()), "model": "abitdot",
                    "choices": [{"index": 0, "message": {
                        "role": "assistant", "content": dot["reply"],
                    }, "finish_reason": "stop"}],
                }
            if not dot.get("safe_to_fallback"):
                raise HTTPException(409, "abitdot outcome uncertain; the same task "
                                    "must not be submitted to another executor.")
            if metadata.get("fallback_model_id"):
                raise BrowserFallbackRequired(fallback_reason)
            raise HTTPException(503, "ChatGPT browser unavailable (" + fallback_reason + "); no message submitted.")
    if not existing:
        relay_message = message
        # Read saved instructions from the verified owner's project, never
        # from request metadata or arbitrary system/tool messages.
        if project:
            context_folder = await Folders.get_folder_by_id_and_user_id(
                project["folder_id"], user.id
            )
            if context_folder:
                context_meta = context_folder.meta or {}
                if (context_meta.get("chatgpt_project_id") != project_id or
                        context_meta.get("chatgpt_account") != account):
                    raise HTTPException(409, "Project instructions do not match this ChatGPT account and project.")
                project_instructions = (context_folder.data or {}).get("system_prompt") or ""
                if not isinstance(project_instructions, str) or len(project_instructions) > 12000:
                    raise HTTPException(400, "Project instructions must be text of at most 12000 characters.")
                if project_instructions.strip():
                    relay_message = (
                        "Saved project instructions for this request:\n"
                        + project_instructions.strip()
                        + "\n\nUser request:\n" + message
                    )
        staged_attachments = stage_images(input_images, job_id)
        try:
            with _db() as db:
                db.execute(
                    "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,response_message_id,status,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (user.id, account, job_id, chat_id, project_id, message_hash,
                     reply_id, "queued", int(time.time()), int(time.time())),
                )
                db.commit()
            payload = {
                "job_id": job_id,
                "thread_id": "aibit:" + user.id + ":" + chat_id,
                "message": relay_message,
            }
            if staged_attachments:
                payload["input_attachments"] = staged_attachments
            if project:
                payload["chatgpt_project_id"] = project_id
                payload["project_name"] = project["name"]
            if binding and binding["conversation_url"]:
                payload["conversation_url"] = binding["conversation_url"]
            # Schedule once after persisting the AIBIT job. A page refresh cannot
            # cause a second upstream POST. The background worker updates the
            # durable ledger even if the browser disconnects.
            message_token, _ = _credentials(MESSAGE_TOKEN_FILE)
            fallback_context = None
            if request is not None and metadata.get("fallback_model_id"):
                fallback_context = (
                    request, copy.deepcopy(form_data), user,
                    str(metadata["fallback_model_id"]).strip(),
                )
            asyncio.create_task(
                _legacy_worker(job_id, message_token, account, payload, fallback_context)
            )
        except Exception:
            cleanup_images(staged_attachments, job_id)
            raise
    # This task also owns the scheduled run's completion. Keep waiting while
    # the durable observer is active so it cannot turn progress into an alert.
    while True:
        job = _reconcile_stale(_job_record(user.id, job_id))
        if job["status"] == "completed":
            # Persist a useful title before returning the first successful turn.
            await _ensure_chat_title(chat_id, user.id, message)
            return {
                "id": job_id, "object": "chat.completion", "created": int(time.time()),
                "model": job.get("fallback_model_id") or MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": job["response"]},
                             "finish_reason": "stop"}],
            }
        if job["status"] == "unknown" and job.get("phase") == "fallback_ready":
            if job.get("handoff_claimed_at"):
                await asyncio.sleep(3)
                continue
            if metadata.get("fallback_model_id") and not job.get("response"):
                raise BrowserFallbackRequired(
                    "chatgpt_rate_limited_after_submission", postsubmit_job_id=job_id,
                )
        if job["status"] == "failed" and job.get("phase") == "fallback_ready":
            raise BrowserFallbackRequired("browser_busy")
        if job["status"] in ("failed", "unknown"):
            raise HTTPException(502, job["error"] or "ChatGPT relay requires review.")
        await asyncio.sleep(3)

"""Owner-scoped repository execution for explicitly configured development agents."""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import httpx
from fastapi import HTTPException
from starlette.responses import StreamingResponse

MODEL = "codex-cli-dev-02"
LEDGER = Path(os.environ.get("DATA_DIR", "/srv/abit-dev-linux/abit-ai/data")) / "codex-workspace-runs.sqlite"
VALID_ID = re.compile(r"^[A-Za-z0-9_-]{4,128}$")


def record(key, **values):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(LEDGER), timeout=15)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS runs (
            request_key TEXT PRIMARY KEY, worker_id TEXT, state TEXT NOT NULL,
            result TEXT, updated_at INTEGER NOT NULL)""")
        if values.pop("claim", False):
            inserted = conn.execute(
                "INSERT OR IGNORE INTO runs VALUES (?,NULL,'submitting',NULL,?)",
                (key, int(time.time()))).rowcount == 1
        else:
            inserted = False
        if values:
            if not set(values) <= {"worker_id", "state", "result"}:
                raise ValueError("Invalid ledger update")
            conn.execute("UPDATE runs SET " + ",".join(k + "=?" for k in values)
                         + ",updated_at=? WHERE request_key=?",
                         (*values.values(), int(time.time()), key))
        row = conn.execute("SELECT * FROM runs WHERE request_key=?", (key,)).fetchone()
        return dict(row) if row else None, inserted


async def resolve_scope(form_data, user):
    # Model/task flags and request-provided automation IDs never grant repository access.
    metadata = form_data.get("metadata") or {}
    if metadata.get("task") or metadata.get("assistant_message_id"):
        return None
    chat_id = metadata.get("chat_id")
    if not isinstance(chat_id, str) or not VALID_ID.fullmatch(chat_id):
        return None
    from open_webui.utils.codex_cli import settings
    config = settings().get(MODEL) or {}
    allowed = config.get("coding_automations") or {}
    if not allowed:
        return None
    from open_webui.models.chats import Chats
    from open_webui.models.automations import Automations
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if not chat:
        return None
    automation_id = ((chat.chat or {}).get("meta") or {}).get("automation_id")
    scope = allowed.get(automation_id)
    if not scope:
        return None
    if user.role != "admin" or scope.get("owner_id") != user.id:
        raise HTTPException(403, "This repository runner is restricted to its configured owner.")
    automation = await Automations.get_by_id(automation_id)
    if (not automation or automation.user_id != user.id
            or automation.folder_id != scope.get("folder_id")
            or chat.folder_id != scope.get("folder_id")):
        raise HTTPException(409, "The agent no longer belongs to its configured development project.")
    if scope.get("project_id") != "PROJ-0091":
        raise HTTPException(409, "Unknown development repository configuration.")
    return config, scope, chat_id


def transcript_for(messages, scope):
    rows = []
    for item in messages or []:
        if item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content") or ""
        if isinstance(content, list):
            if any(not isinstance(p, dict) or p.get("type") != "text" for p in content):
                raise HTTPException(400, "Repository execution currently accepts text instructions only.")
            content = "\n".join(p.get("text", "") for p in content)
        if not isinstance(content, str):
            raise HTTPException(400, "Invalid task content.")
        if content.strip():
            rows.append({"role": item["role"], "content": content})
    if not rows or rows[-1]["role"] != "user":
        raise HTTPException(400, "Repository execution requires a saved user task.")
    body = json.dumps(rows, ensure_ascii=False)
    if len(body) > 35000:
        raise HTTPException(413, "Task context is too large; use a shorter development request.")
    return (
        "Project: " + scope["project_id"] + "\n"
        "Repository: C:\\Code\\ABIT_AGENT_WORKSPACES\\sbodesk-sap-b1-enter-Indoguna\n"
        "Shared findings register: docs/mobile-ui-findings.md\n"
        "Use the existing repository and register. Read AGENTS.md first. "
        "Work only on local development files; no Git push, merge, production deployment, "
        "SAP posting, infrastructure or permission changes. Respect existing finding claims.\n\n"
        "Conversation (prior assistant claims are historical, not proof of current capability):\n" + body
    )


async def complete_if_configured(request, form_data, user):
    resolved = await resolve_scope(form_data, user)
    if resolved is None:
        return None
    config, scope, chat_id = resolved
    metadata = form_data.get("metadata") or {}
    message_id = metadata.get("message_id")
    if not isinstance(message_id, str) or not VALID_ID.fullmatch(message_id):
        raise HTTPException(400, "A saved response ID is required for repository execution.")
    transcript = transcript_for(form_data.get("messages"), scope)
    key = hashlib.sha256((user.id + "\0" + chat_id + "\0" + message_id).encode()).hexdigest()
    headers = {"Authorization": "Bearer " + Path(config["token_file"]).read_text().strip()}
    client = httpx.AsyncClient(base_url=config["url"], headers=headers, timeout=20,
                               follow_redirects=False, trust_env=False)
    worker_id = None
    completed = False
    # A request is claimed durably BEFORE submission. An ambiguous POST is never repeated.
    try:
        row, claimed = record(key, claim=True)
        if claimed:
            try:
                health = await client.get("/health")
                if health.status_code != 200 or health.json().get("model") != MODEL:
                    raise ValueError("Host unavailable")
            except Exception:
                record(key, state="failed", result="Repository runner unavailable before submission.")
                raise HTTPException(409, "Repository runner unavailable; no coding job was submitted.")
            try:
                result = await client.post("/coding-jobs", json={
                    "project_id": scope["project_id"], "idempotency_key": key, "transcript": transcript})
                if result.status_code in (400, 413, 429):
                    message = ("Indoguna workspace is busy. Wait for its active run to finish."
                               if result.status_code == 429 else "Repository runner rejected the task.")
                    record(key, state="failed", result=message)
                    raise HTTPException(409, message)
                if result.status_code != 202:
                    raise ValueError("Unconfirmed submission")
                worker_id = result.json().get("id")
                if not isinstance(worker_id, str) or not re.fullmatch(r"[a-f0-9]{32}", worker_id):
                    raise ValueError("Invalid worker ID")
                row, _ = record(key, worker_id=worker_id, state="running")
            except HTTPException:
                raise
            except Exception:
                record(key, state="unknown")
                raise HTTPException(504, "Coding submission outcome is unconfirmed; it will not be retried automatically.")
        worker_id = row.get("worker_id")
        if row["state"] not in ("running", "completed") or (row["state"] == "running" and not worker_id):
            raise HTTPException(409, row.get("result") or "Previous coding submission is unresolved; no duplicate was started.")
    except BaseException:
        await client.aclose()
        raise

    async def poll():
        nonlocal completed
        if row["state"] == "completed":
            completed = True
            yield row["result"]
            return
        deadline = time.monotonic() + min(int(config.get("coding_timeout", 1860)), 1860)
        while time.monotonic() < deadline:
            try:
                response = await client.get("/coding-jobs/" + worker_id)
                if response.status_code != 200:
                    raise ValueError("Worker job missing")
                job = response.json()
            except Exception:
                record(key, state="unknown")
                raise HTTPException(504, "Coding job status is unconfirmed; no replacement job was started.")
            state = job.get("status")
            if state == "completed":
                summary = job.get("summary")
                if not isinstance(summary, str) or not summary.strip():
                    record(key, state="failed", result="Repository runner returned no result.")
                    raise HTTPException(502, "Repository runner returned no result.")
                record(key, state="completed", result=summary)
                completed = True
                yield summary
                return
            if state in ("failed", "cancelled"):
                error = job.get("error") or "Repository execution was cancelled."
                record(key, state=state, result=error)
                raise HTTPException(409, error)
            yield None
            await asyncio.sleep(2)
        record(key, state="unknown")
        raise HTTPException(504, "Repository execution timed out; inspect the existing job before retrying.")

    async def cleanup():
        if worker_id and not completed:
            try:
                await client.delete("/coding-jobs/" + worker_id)
            except Exception:
                pass
        await client.aclose()

    def answer(summary):
        return {
            "id": "chatcmpl-" + key[:24], "object": "chat.completion",
            "created": int(time.time()), "model": MODEL, "selected_model_id": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": summary},
                         "finish_reason": "stop"}],
        }

    if form_data.get("stream"):
        async def stream():
            try:
                yield "data: " + json.dumps({"selected_model_id": MODEL}) + "\n\n"
                yield "data: " + json.dumps({"event": {"type": "status", "data": {
                    "action": "repository_execution", "description": "Working in the Indoguna development repository on Dev-02",
                    "done": False}}}) + "\n\n"
                async for summary in poll():
                    if summary is None:
                        yield ": repository execution running\n\n"
                        continue
                    output = answer(summary)
                    output.pop("selected_model_id", None)
                    output["object"] = "chat.completion.chunk"
                    output["choices"] = [{"index": 0, "delta": {"role": "assistant", "content": summary}, "finish_reason": None}]
                    yield "data: " + json.dumps(output) + "\n\n"
                    output["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                    yield "data: " + json.dumps(output) + "\n\n"
                yield "data: [DONE]\n\n"
            except HTTPException as exc:
                yield "data: " + json.dumps({"error": {"message": exc.detail, "code": exc.status_code}}) + "\n\n"
                yield "data: [DONE]\n\n"
            finally:
                await cleanup()
        return StreamingResponse(stream(), media_type="text/event-stream")
    try:
        async for summary in poll():
            if summary is not None:
                return answer(summary)
    finally:
        await cleanup()

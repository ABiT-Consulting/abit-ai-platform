"""Owner-scoped abitdot-first automation contract.

No undocumented ChatGPT endpoint, browser automation, Work mode, Dot poller, or
unverified Slack bot-to-bot invocation is used here. Delivery requires an
explicitly registered, owner-authorized adapter that returns a verifiable
acceptance receipt for the SAME automation run. Dot chooses how to execute.

This module changes neither ERP task statuses nor model fallback configuration.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS dot_primary_runs(
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    automation_id TEXT NOT NULL,
    original_run_id TEXT NOT NULL,
    dot_id TEXT NOT NULL,
    status TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    receipt TEXT,
    response_text TEXT,
    conversation_url TEXT,
    reason TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(owner, automation_id, original_run_id)
);
CREATE INDEX IF NOT EXISTS idx_dot_primary_by_automation
  ON dot_primary_runs(owner, automation_id, created_at);
"""
DOT_ID = re.compile(r"^[A-Za-z0-9_-]{8,190}(?:~[A-Za-z0-9_-]{8,190})?$")
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
SECRET_FIELDS = ("password", "secret", "token", "cookie", "api_key",
                 "authorization", "credentials", "private_key", "access_key")


def scrub(value: Any, depth: int = 0) -> Any:
    if depth > 28:
        return "[context depth exceeded]"
    if isinstance(value, dict):
        return {str(k): "[redacted]" if any(term in str(k).lower() for term in SECRET_FIELDS)
                else scrub(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [scrub(v, depth + 1) for v in value]
    if value is None or isinstance(value, (str, float, int, bool)):
        return value
    return str(value)


def primary_dot_for_owner(owner: str, environ: dict | None = None) -> str | None:
    """Require explicit enabling and the exact originating user's Dot.

    A private personal Dot must never receive another user's automations.
    No Dot API is inferred from the browser URL; this only selects identity.
    """
    import os
    from urllib.parse import urlparse
    env = environ if environ is not None else os.environ
    if str(env.get("ABIT_DOT_PRIMARY_ENABLED", "false")).lower() != "true":
        return None
    configured_owner = env.get("ABIT_CHATGPT_DOT_OWNER_ID")
    if not configured_owner or owner != configured_owner:
        return None
    url = urlparse(str(env.get("ABIT_CHATGPT_DOT_URL", "")))
    if (url.scheme != "https" or url.netloc != "chatgpt.com"
            or url.query or url.fragment or url.username or url.password
            or not url.path.startswith("/dots/")):
        return None
    dot_id = url.path[len("/dots/"):].rstrip("/")
    if not DOT_ID.fullmatch(dot_id):
        return None
    return dot_id


def dot_primary_key(owner: str, automation_id: str, run_id: str) -> str:
    if not all(isinstance(v, str) and v for v in (owner, automation_id, run_id)):
        raise ValueError("Dot primary needs a verified owner, automation and run")
    return hashlib.sha256(json.dumps(
        [owner, automation_id, run_id], separators=(",", ":")
    ).encode()).hexdigest()


def build_project_envelope(
    *,
    owner: str, automation: dict, run_id: str, dot_id: str,
    project: dict | None = None,
    previous_chat: dict | None = None,
    prompt: str | None = None,
) -> dict:
    """Preserve the actual related project and full prompt, never guess a project.

    The caller MUST verify project/chat ownership using the application's
    authorized owner-scoped accessors before passing them here.
    """
    if not isinstance(automation, dict) or not isinstance(automation.get("id"), str):
        raise ValueError("Automation identity is required")
    if not isinstance(owner, str) or not owner or automation.get("user_id") != owner:
        raise PermissionError("Automation owner mismatch")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("A stable automation run ID is required")
    if not isinstance(dot_id, str) or not DOT_ID.fullmatch(dot_id):
        raise ValueError("The verified personal Dot ID is required")
    original = prompt if prompt is not None else (automation.get("data") or {}).get("prompt")
    if not isinstance(original, str) or not original.strip():
        raise ValueError("The original automation prompt is missing")

    project = project or {}
    previous_chat = previous_chat or {}
    if project and project.get("user_id") != owner:
        raise PermissionError("The related project belongs to another owner")
    if previous_chat and previous_chat.get("user_id") != owner:
        raise PermissionError("Previous chat belongs to another owner")
    if previous_chat and (previous_chat.get("meta") or {}).get("automation_id") != automation["id"]:
        raise PermissionError("Previous chat belongs to another automation")

    folder = scrub(project)
    previous = scrub(previous_chat)
    context = {
        "run_id": run_id,
        "automation_id": automation["id"],
        "owner_id": owner,
        "dot_id": dot_id,
        "primary_responder": "abitdot",
        "source": "ABIT.AI Agentic Governance",
        "task": {
            "title": automation.get("name"),
            "original_prompt": original,
            "instructions": "Execute autonomously using the related project and verified "
                            "connected tools. Choose appropriate models, agents and "
                            "authorized execution environment. Do not use Work mode. "
                            "Never duplicate existing active task ownership, project work, "
                            "SAP postings, PRs or commits. Preserve ERP task status links "
                            "and evidence. Slack only for genuine showstoppers, and "
                            "respect required approvals for material actions.",
            "data": scrub(automation.get("data") or {}),
            "meta": scrub(automation.get("meta") or {}),
        },
        "related_project": {
            "id": folder.get("id") if folder else None,
            "name": folder.get("name") if folder else None,
            "chatgpt_project_id": (folder.get("meta") or {}).get("chatgpt_project_id"),
            "saved_instructions": (folder.get("data") or {}).get("system_prompt")
                                  or (folder.get("data") or {}).get("instructions"),
            "project_link": (folder.get("meta") or {}).get("chatgpt_project_url"),
        },
        "previous_chat": previous,
        "execution_policy": {
            "dot_primary": True,
            "allow_unverified_chatgpt_replay": False,
            "requires_verified_owner": True,
            "no_dot_polling": True,
            "no_work_mode": True,
            "erp_task_status_authority": "ABIT.AI / ERPNext",
        },
    }
    context["idempotency_key"] = dot_primary_key(owner, automation["id"], run_id)
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > 220_000:
        raise ValueError("Project context is too large for a safe Dot handoff")
    return context


@contextmanager
def ledger(path: str | Path):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(file), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.executescript(SCHEMA)
    # Additive only: retain any previous owner-scoped Dot ledger snapshots.
    columns = {row["name"] for row in db.execute("PRAGMA table_info(dot_primary_runs)")}
    for name in ("response_text", "conversation_url"):
        if name not in columns:
            db.execute(f"ALTER TABLE dot_primary_runs ADD COLUMN {name} TEXT")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def claim(db, envelope: dict):
    """One durable claim per original owner/automation/run, no duplicate tasks."""
    key = envelope["idempotency_key"]
    now = int(time.time())
    db.execute("BEGIN IMMEDIATE")
    try:
        # A different scheduled/manual run of this same automation must not
        # dispatch while an earlier Dot delegation is in progress or unknown.
        existing_active = db.execute(
            "SELECT * FROM dot_primary_runs WHERE owner=? AND automation_id=? "
            "AND id<>? AND status IN ('dispatching','delegated','unknown') "
            "ORDER BY created_at DESC LIMIT 1",
            (envelope["owner_id"], envelope["automation_id"], key),
        ).fetchone()
        if existing_active is not None:
            db.commit()
            return False, dict(existing_active)
        db.execute(
            "INSERT OR IGNORE INTO dot_primary_runs "
            "(id,owner,automation_id,original_run_id,dot_id,status,"
            "envelope_json,created_at,updated_at) VALUES(?,?,?,?,?,'prepared',?,?,?)",
            (key, envelope["owner_id"], envelope["automation_id"],
             envelope["run_id"], envelope["dot_id"],
             json.dumps(envelope, ensure_ascii=False, default=str), now, now)
        )
        row = db.execute("SELECT * FROM dot_primary_runs WHERE id=?", (key,)).fetchone()
        if row["owner"] != envelope["owner_id"] or row["dot_id"] != envelope["dot_id"]:
            raise PermissionError("The Dot identity changed after the run began")
        updated = db.execute(
            "UPDATE dot_primary_runs SET status='dispatching',updated_at=? "
            "WHERE id=? AND status='prepared'", (now, key)
        )
        db.commit()
        return updated.rowcount == 1, dict(row)
    except BaseException:
        db.rollback()
        raise


def record_receipt(db, envelope: dict, outcome: dict):
    """A Dot response is a delegation acknowledgment, never ERP completion."""
    if not isinstance(outcome, dict):
        raise ValueError("Invalid Dot receipt")
    valid = (
        outcome.get("owner_id") == envelope["owner_id"]
        and outcome.get("automation_id") == envelope["automation_id"]
        and outcome.get("run_id") == envelope["run_id"]
        and outcome.get("dot_id") == envelope["dot_id"]
        and outcome.get("idempotency_key") == envelope["idempotency_key"]
    )
    if not valid:
        state, reason, receipt = "unknown", "unverified_dot_receipt", None
    elif outcome.get("status") == "accepted" and isinstance(outcome.get("receipt_id"), str) and len(outcome["receipt_id"]) >= 8:
        state, reason, receipt = "delegated", None, outcome["receipt_id"]
    elif outcome.get("status") == "rejected_before_submit":
        state, reason, receipt = "unavailable", "verified_pre_submission_rejection", None
    else:
        state, reason, receipt = "unknown", "delivery_outcome_unknown", None
    reply = outcome.get("dot_turn_result") if state == "delegated" else None
    url = outcome.get("dot_conversation_url") if state == "delegated" else None
    if not isinstance(reply, str) or len(reply) > 250000:
        reply = None
    if not isinstance(url, str) or not url.startswith("https://chatgpt.com/"):
        url = None
    db.execute(
        "UPDATE dot_primary_runs SET status=?,reason=?,receipt=?,"
        "response_text=?,conversation_url=?,updated_at=? "
        "WHERE id=? AND status='dispatching'",
        (state, reason, receipt, reply, url, int(time.time()),
         envelope["idempotency_key"])
    )
    db.commit()
    return {"state": state, "receipt_id": receipt, "reason": reason}


async def deliver(envelope: dict, send_verified, path: str | Path, timeout_seconds: int = 60):
    """Invoke only a documented, owner-authenticated Dot delivery adapter.

    NEVER pass an ordinary ChatGPT text model, generic browser URL, or Slack
    bot message as send_verified. They do not establish a Dot acceptance.
    """
    with ledger(path) as db:
        winner, existing = claim(db, envelope)
    if not winner:
        return {"state": existing["status"], "duplicate": True,
                "receipt_id": existing["receipt"]}
    if not callable(send_verified):
        with ledger(path) as db:
            return record_receipt(db, envelope, {})
    try:
        result = await asyncio.wait_for(send_verified(envelope), timeout=timeout_seconds)
    except Exception:
        result = {}
    with ledger(path) as db:
        return record_receipt(db, envelope, result)



def read_owner_primary_runs(
    owner: str, automation_id: str, path: str | Path, limit: int = 20,
) -> list[dict]:
    """Owner-scoped, read-only status and Dot reply evidence.

    Does not expose the private project prompt or any other owner's data.
    The actual ERP task and its completion evidence remain authoritative.
    """
    target = Path(path)
    if not target.is_file() or not owner or not automation_id:
        return []
    n = max(1, min(int(limit), 50))
    with ledger(target) as db:
        rows = db.execute(
            "SELECT id,original_run_id,status,reason,receipt,"
            "response_text,conversation_url,created_at,updated_at "
            "FROM dot_primary_runs WHERE owner=? AND automation_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (owner, automation_id, n),
        ).fetchall()
    return [dict(row) for row in rows]

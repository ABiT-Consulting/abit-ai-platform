"""ERP Task sidebar control API for a privileged Frappe server-side proxy.

Never accept a browser-supplied automation ID: resolve the Task's existing ABIT.AI
link from ERP, validate the owner and agent's exact ERP metadata, and use the
scheduler's durable store for ERP-managed runs. This module must be deployed
alongside open_webui.utils.erp_task_runtime; the Windows source checkout alone
does not contain that Linux scheduler.
"""
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from open_webui.internal.db import get_async_session
from open_webui.models.automations import Automation, AutomationRun
from open_webui.models.chats import Chat
from open_webui.tasks import stop_item_tasks
from open_webui.env import DATA_DIR
from open_webui.models.users import Users

router = APIRouter()
TASK_RE = re.compile(r"^TASK-[A-Za-z0-9-]{1,64}$")
AGENT_RE = re.compile(r"^/automations/([0-9a-fA-F-]{32,36})$")
CHAT_RE = re.compile(r"^/c/([0-9a-fA-F-]{32,36})$")
PROJECT_CHAT_RE = re.compile(r"^/g/g-p-[a-zA-Z0-9-]{32,150}/c/([0-9a-fA-F-]{32,36})$")


class ControlCommand(BaseModel):
    action: str = "status"


def safe_chat_url(raw, *, chatgpt=False):
    """No JS, redirects, userinfo, untrusted hosts, or arbitrary deep links."""
    try:
        url = urlsplit(str(raw or ""))
        hosts = {"chatgpt.com", "chat.openai.com"} if chatgpt else {"abit.ai"}
        if (url.scheme != "https" or url.netloc not in hosts or url.query or url.fragment
                or not (CHAT_RE.fullmatch(url.path) or (chatgpt and PROJECT_CHAT_RE.fullmatch(url.path)))):
            return None
        return url.geturl()
    except ValueError:
        return None


def task_agent_id(task):
    raw = str(task.get("custom_abit_ai_agent_url") or "")
    try:
        url = urlsplit(raw)
        match = AGENT_RE.fullmatch(url.path)
        if url.scheme == "https" and url.netloc == "abit.ai" and not url.query and not url.fragment and match:
            return match.group(1)
    except ValueError:
        pass
    return None


def legacy_state(run):
    if not run:
        return "Not Started"
    if run.get("status") == "Cancelled":
        return "Stopped" if run.get("synced") else "Stopping"
    states = {
        "Preparing": "Queued", "Working": "Running",
        "Pending Review": "Completed", "Completed": "Completed",
        "Failed": "Failed", "Blocked": "Blocked",
    }
    return states.get(run.get("status"), "Queued")


def relay_job(chat_id, owner):
    """Return the authoritative relay job status for this user's exact chat.

    Unknown and queued jobs are never interpreted as confirmed stopped.
    """
    try:
        from open_webui.env import DATA_DIR
        ledger = Path(DATA_DIR) / "aibit-chatgpt-relay.sqlite"
        if not ledger.is_file() or not chat_id or not owner:
            return None
        with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            rows = conn.execute(
                "SELECT status, conversation_url FROM jobs "
                "WHERE owner=? AND chat_id=? ORDER BY updated_at DESC LIMIT 100",
                (owner, chat_id),
            ).fetchall()
        if not rows:
            return None
        active = any(row[0] not in ("completed", "failed") for row in rows)
        return {"active": active, "terminal": not active,
                "conversation_url": next((row[1] for row in rows if row[1]), None)}
    except (sqlite3.Error, OSError, ValueError):
        return None

def standard_state(agent, latest, task):
    if not agent:
        return str(task.get("custom_ai_relay_execution_status") or "Not Started")
    manual = (agent.meta or {}).get("erp_operator_stop")
    if manual:
        if not latest or latest.status == "running":
            return "Stopping"
        model_id = str((agent.data or {}).get("model_id") or "")
        if "chatgpt-relay" in model_id:
            upstream = relay_job(latest.chat_id, agent.user_id)
            return "Stopped" if upstream and upstream["terminal"] else "Stopping"
        return "Stopped"
    if latest and latest.status == "running":
        return "Running"
    if agent.is_active and agent.next_run_at is not None:
        return "Queued"
    if latest:
        return {"success": "Completed", "error": "Failed", "cancelled": "Stopped"}.get(
            latest.status, "Not Started"
        )
    return str(task.get("custom_ai_relay_execution_status") or "Not Started")


async def task_context(task_id, db):
    if not TASK_RE.fullmatch(task_id):
        raise HTTPException(400, "Invalid ERP Task identifier")
    try:
        from open_webui.utils.erp_task_resolution import ERPClient, Store
        from open_webui.utils.erp_task_runtime import CONFIG, runtime_state
    except ImportError:
        raise HTTPException(503, "ERP task scheduler is not installed") from None
    cfg_path = os.environ.get(CONFIG)
    if not cfg_path:
        raise HTTPException(503, "ERP task scheduler is disabled")
    try:
        cfg = json.loads(Path(cfg_path).read_text())
        erp = ERPClient(cfg["erp_token_file"])
        try:
            task = await erp.get("Task", task_id)
            assigned = await erp.assigned(task)
        finally:
            await erp.client.aclose()
    except Exception:
        raise HTTPException(503, "ERP Task could not be verified") from None
    agent_id = task_agent_id(task)
    if task.get("custom_abit_ai_agent_url") and not agent_id:
        raise HTTPException(409, "ERP Task has an invalid agent link")
    agent = await db.get(Automation, agent_id) if agent_id else None
    if agent and ((agent.meta or {}).get("erp_task") != task_id
                  or (agent.meta or {}).get("erp_url") != "https://erp.abit.ai/desk/task/" + task_id):
        raise HTTPException(409, "ERP agent ownership or Task mapping mismatch")
    # A linked Task may not yet have a saved agent, e.g. intake is still queued.
    legacy = Store(runtime_state()).get(task_id)
    if legacy and agent_id and legacy.get("agent") != agent_id:
        raise HTTPException(409, "ERP scheduler and Task link disagree")
    if agent and legacy and (agent.meta or {}).get("erp_managed") is not True:
        # A retired legacy cycle may still exist after a scheduler migration.
        legacy = None
    if agent_id and not agent and (not legacy or legacy.get("agent") != agent_id):
        raise HTTPException(409, "Linked ABIT.AI agent is missing")
    latest = await db.scalar(
        select(AutomationRun).where(AutomationRun.automation_id == agent.id)
        .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc()).limit(1)
    ) if agent else None
    # A verified task-to-agent binding is sufficient for an existing agent;
    # require the AI assignee only when creating a new agent for an unlinked Task.
    eligible = bool(assigned or (agent and (agent.meta or {}).get("erp_task") == task_id)
                    or (not agent and task.get("project") == "PROJ-0197"
                        and task.get("status") == "Open" and not task.get("is_group")
                        and not task.get("is_template")))
    return cfg, task, eligible, agent, legacy, latest


async def response_for(task, assigned, agent, legacy, latest, db):
    if legacy and (not agent or (agent.meta or {}).get("erp_managed") is True):
        current = legacy_state(legacy)
        attempts = legacy.get("attempts") or []
        saved_attempts = ((agent.meta or {}).get("erp_run") or {}).get("attempts") or [] if agent else []
        chat_id = saved_attempts[-1].get("chat_id") if saved_attempts else None
        if not chat_id and attempts:
            from open_webui.utils.erp_task_resolution import attempt_chat_url
            chat_id = attempt_chat_url(attempts[-1]["id"]).rsplit("/", 1)[-1]
        progress = legacy.get("progress") or ""
    else:
        current = standard_state(agent, latest, task)
        chat_id = latest.chat_id if latest else None
        progress = ""
    abit_chat = None
    if chat_id:
        chat = await db.get(Chat, chat_id)
        if agent and chat and chat.user_id == agent.user_id:
            abit_chat = f"https://abit.ai/c/{chat_id}"
    abit_chat = abit_chat or safe_chat_url(task.get("custom_ai_relay_conversation_url"))
    upstream = relay_job(chat_id, agent.user_id) if (agent and abit_chat and chat_id) else None
    chatgpt_chat = safe_chat_url(upstream.get("conversation_url"), chatgpt=True) if upstream else None
    chatgpt_chat = chatgpt_chat or safe_chat_url(task.get("custom_chatgpt_chat_url"), chatgpt=True)
    chatgpt_chat = chatgpt_chat or safe_chat_url(task.get("custom_ai_relay_conversation_url"), chatgpt=True)
    has_link = bool(agent or legacy)
    terminal = current in ("Stopped", "Completed", "Failed", "Blocked", "Not Started")
    ready = bool(assigned and task.get("status") in ("Open", "Working") and terminal)
    can_start = bool(ready and (task.get("status") == "Open" or current == "Stopped")
                     and (has_link or task.get("project") == "PROJ-0197"))
    can_restart = bool(ready and has_link and current != "Not Started")
    can_stop = bool(has_link and current in ("Running", "Queued") and assigned)
    return {
        "connected": True,
        "status": current,
        "progress": str(progress)[:400],
        "agent_url": "https://abit.ai/automations/" + agent.id if agent else task.get("custom_abit_ai_agent_url"),
        "abit_chat_url": abit_chat,
        "chatgpt_chat_url": chatgpt_chat,
        "can_start": can_start,
        "can_stop": can_stop,
        "can_restart": can_restart,
    }


@router.post("/{task_id}")
async def control_erp_task_agent(
    request: Request, task_id: str, command: ControlCommand,
    control_secret: str = Header(default="", alias="X-ABIT-ERP-Control"),
    db=Depends(get_async_session),
):
    # This endpoint is for ERPNext's authenticated server-side proxy only.
    # Never accept an agent ID or an API key supplied by the browser.
    secret_path = Path(os.environ.get(
        "ABIT_ERP_CONTROL_TOKEN_FILE",
        str(Path(DATA_DIR).parent / "config/erp-assignment/control-token"),
    ))
    try:
        expected = secret_path.read_text().strip()
    except (OSError, ValueError):
        raise HTTPException(503, "ERP agent control is not configured") from None
    if not expected or not control_secret or not hmac.compare_digest(
        control_secret.encode(), expected.encode()
    ):
        raise HTTPException(401, "Invalid ERP control credential")
    if command.action not in {"status", "start", "stop", "restart"}:
        raise HTTPException(400, "Unsupported control action")
    cfg, task, assigned, agent, legacy, latest = await task_context(task_id, db)
    owner = await Users.get_user_by_email(cfg.get("owner_email") or "")
    if not owner or owner.role != "admin":
        raise HTTPException(503, "The configured ERP control owner is unavailable")
    if agent and agent.user_id != owner.id:
        raise HTTPException(404, "Agent not owned by the configured control account")
    if legacy and (legacy.get("scope") or {}).get("owner_id") != owner.id:
        raise HTTPException(404, "Task run does not belong to the configured control account")
    result = await response_for(task, assigned, agent, legacy, latest, db)
    if command.action == "status":
        return result
    flag = {"start": "can_start", "stop": "can_stop", "restart": "can_restart"}[command.action]
    if not result[flag]:
        raise HTTPException(409, "Agent action not allowed while " + result["status"])
    from open_webui.utils.erp_task_resolution import ERPClient, Store
    from open_webui.utils.erp_task_runtime import runtime_state
    if command.action == "stop":
        if legacy and (not agent or (agent.meta or {}).get("erp_managed") is True):
            # Durable cancellation fence: the existing ERP scheduler sends stop
            # to both external relay and CLI workers, waits for both receipts,
            # then marks the run synced. No automatic retry after cancellation.
            legacy["status"] = "Cancelled"
            legacy["progress"] = "Stop requested from ERP Task by an authorized user"
            legacy["operator_stop_requested_at"] = time.time()
            Store(runtime_state()).put(legacy)
            return {**result, "status": "Stopping", "can_start": False,
                    "can_restart": False, "can_stop": False,
                    "message": "Stop requested; awaiting worker confirmation."}
        # Standard scheduler: prevent the next scheduled execution and cancel
        # local chat tasks. Upstream ChatGPT relay may remain unconfirmed.
        agent.meta = {**(agent.meta or {}), "erp_operator_stop": time.time()}
        agent.is_active = False
        agent.next_run_at = None
        await db.commit()
        if latest and latest.chat_id:
            chat = await db.get(Chat, latest.chat_id)
            if chat and chat.user_id == owner.id:
                await stop_item_tasks(getattr(request.app.state, "redis", None), latest.chat_id)
        return {**result, "status": "Stopping",
                "can_start": False, "can_stop": False, "can_restart": False,
                "message": "Stop requested. An external relay requires separate confirmation."}
    # Start and Restart only after previous execution has ended.
    if latest and latest.status == "running":
        raise HTTPException(409, "Previous agent execution is still active")
    if not agent and not legacy:
        if command.action != "start" or task.get("project") != "PROJ-0197" or task.get("status") != "Open":
            raise HTTPException(409, "Task cannot start a new agent")
        # ERPNext's existing ToDo webhook is the source of truth for initial
        # agent creation. Preserve the existing human assignee(s); adding the AI
        # developer ToDo triggers the native ABIT.AI scheduler exactly once.
        erp = ERPClient(cfg["erp_token_file"])
        try:
            from open_webui.utils.erp_task_resolution import ASSIGNEE
            if not await erp.assigned(task):
                await erp.request("POST", "/api/method/frappe.desk.form.assign_to.add",
                                  json={"assign_to": json.dumps([ASSIGNEE]),
                                        "doctype": "Task", "name": task_id,
                                        "description": "Explicit Start requested in ERP Task sidebar"})
        finally:
            await erp.client.aclose()
        return {**result, "status": "Queued", "can_start": False, "can_stop": False,
                "can_restart": False, "message": "Agent assignment queued through the existing ERP scheduler."}
    if legacy and legacy.get("status") == "Cancelled" and not legacy.get("synced"):
        raise HTTPException(409, "Previous relay/CLI cancellation is unconfirmed")
    if task.get("status") == "Working":
        erp = ERPClient(cfg["erp_token_file"])
        try:
            await erp.save(task, {"status": "Open"})
        finally:
            await erp.client.aclose()
    if agent and (agent.meta or {}).get("erp_standard_agent") is True:
        agent.meta = {**(agent.meta or {}), "erp_operator_stop": None,
                      "erp_standard_armed": False}
        agent.is_active = False
        agent.next_run_at = None
        await db.commit()
    store = Store(runtime_state())
    if legacy and legacy.get("synced"):
        store.queue_reset(task_id)
    else:
        store.enqueue(task_id)
    return {**result, "status": "Queued", "can_start": False, "can_stop": True,
            "can_restart": False, "message": "Start queued with the ERP task scheduler."}
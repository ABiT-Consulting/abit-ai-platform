"""Owner-scoped primary routing for ABIT.AI Play and scheduled runs.

This module is intentionally separate from automations.py so the live Linux
ERP task/agent-status implementation is never replaced wholesale.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from open_webui.models.automations import AutomationRuns
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.utils.abitdot_primary import (
    build_project_envelope, deliver, primary_dot_for_owner,
)
from open_webui.utils.abitdot_primary_relay import send_via_existing_relay

log = logging.getLogger(__name__)


async def _recent_owned_chat(automation):
    """Reuse only a verified owned automation chat; no fresh ChatGPT turn."""
    if (automation.data.get("target") or {}).get("type") == "channel":
        return None
    for run in await AutomationRuns.get_by_automation(automation.id, limit=50):
        if run.status != "success" or not run.chat_id or run.chat_id.startswith("channel:"):
            continue
        candidate = await Chats.get_chat_by_id_and_user_id(
            run.chat_id, automation.user_id
        )
        if not candidate:
            continue
        meta = (candidate.chat or {}).get("meta") or {}
        if meta.get("automation_id") != automation.id or meta.get("agent_handoff"):
            continue
        return candidate
    return None


async def run_primary_dot(app, automation, user, scheduled_run_id=None) -> bool:
    """Return True if an enabled/authorized owner Dot path handled this run.

    Never silently fall through to the old selected model after a possible
    submitted Dot turn. Legacy routing is unchanged when Dot-primary is off
    for this user. An accepted Dot reply is a chat turn, not ERP completion.
    """
    # A targeted production canary keeps unrelated owner automations on
    # their existing model until this Dot route proves a real verified reply.
    canary = os.getenv("ABIT_DOT_PRIMARY_CANARY_AUTOMATION_ID", "").strip()
    if canary and canary != automation.id:
        return False
    dot_id = primary_dot_for_owner(automation.user_id)
    if not dot_id:
        return False

    # Live automations.py remains the authority for its ERP template
    # expansion, authorization, status recording, and scheduler semantics.
    from open_webui.utils.automations import _record_run, _with_erp_task_context
    from open_webui.utils.task import prompt_template
    try:
        project = None
        if automation.folder_id:
            folder = await Folders.get_folder_by_id_and_user_id(
                automation.folder_id, automation.user_id
            )
            if folder is None:
                await _record_run(
                    automation.id, "deferred",
                    error="Primary abitdot project was not found for this owner.",
                    scheduled_run_id=scheduled_run_id,
                )
                return True
            project = folder.model_dump()
        chat = await _recent_owned_chat(automation)
        saved = None
        if chat:
            saved = {
                "id": chat.id,
                "user_id": automation.user_id,
                "meta": (chat.chat or {}).get("meta"),
                "history": (chat.chat or {}).get("history"),
                "link": "https://abit.ai/c/" + chat.id,
            }
        prompt = await prompt_template(automation.data["prompt"], user)
        prompt = _with_erp_task_context(automation, prompt)
        run_id = scheduled_run_id or ("manual-" + uuid4().hex)
        envelope = build_project_envelope(
            owner=automation.user_id,
            automation={
                "id": automation.id,
                "user_id": automation.user_id,
                "name": automation.name,
                "data": automation.data,
                "meta": automation.meta,
            },
            run_id=run_id,
            dot_id=dot_id,
            project=project,
            previous_chat=saved,
            prompt=prompt,
        )
    except (ValueError, PermissionError, KeyError):
        log.warning("abitdot primary context rejected for automation %s", automation.id)
        await _record_run(
            automation.id, "deferred",
            error="Primary abitdot project or run ownership could not be verified.",
            scheduled_run_id=scheduled_run_id,
        )
        return True

    from open_webui.env import DATA_DIR

    result = await deliver(
        envelope,
        getattr(app.state, "ABIT_DOT_PRIMARY_SEND", None)
        or send_via_existing_relay,
        Path(DATA_DIR) / "abitdot-primary.sqlite",
        timeout_seconds=245,
    )
    state = result.get("state", "unknown")
    status = "delegated" if state == "delegated" else "deferred"
    error = None if status == "delegated" else (
        "abitdot primary " + state
        + "; original run preserved, no duplicate relay or model submission."
    )
    await _record_run(
        automation.id, status,
        chat_id=chat.id if chat else None,
        error=error,
        scheduled_run_id=scheduled_run_id,
    )
    return True

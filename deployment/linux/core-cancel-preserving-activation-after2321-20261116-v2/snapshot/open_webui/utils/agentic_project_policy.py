"""Fail-closed ERP Project authorization for project-scoped ABIT.AI agents.

A project is eligible only when it is actively open AND has an *open*
Project assignment to the shared ai-projects ERPNext User. Both the Frappe
Project _assign field and open Project ToDo records must agree.

This module has no ERP write or network side effects and is independently
unit-testable.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

REQUIRED_PROJECT_MANAGER = "ai-projects@abitcons.com"
# Unknown or terminal statuses fail closed; a held project must never execute.
ACTIVE_STATUSES = frozenset({"Open", "In Progress", "Active"})


def _project_assignees(assign_value: Any) -> list[str] | None:
    if isinstance(assign_value, str):
        try:
            assign_value = json.loads(assign_value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
    if not isinstance(assign_value, list) or not all(isinstance(user, str) and user for user in assign_value):
        return None
    return assign_value


def project_eligibility_reason(
    project: dict[str, Any],
    assign_value: Any,
    open_project_todos: Iterable[dict[str, Any]],
    *,
    project_id: str,
) -> str | None:
    """Return None only when live ERP evidence positively authorizes work.

    A missing ToDo, stale _assign field, failed Project query or unknown status
    must not enable provisioning, new invocations or recovery dispatch.
    """
    if not isinstance(project, dict) or project.get("name") != project_id:
        return "ERP Project identity mismatch"
    status = str(project.get("status") or "").strip()
    if status not in ACTIVE_STATUSES:
        return "ERP Project is on hold, closed, or not active"
    if project.get('custom_agentic_governance_enabled') not in (1, True, '1'):
        return 'ERP Project is not opted in to Agentic Governance'
    if project.get('custom_ai_project_manager_task_automation_enabled') not in (1, True, '1'):
        return 'ERP Project AI Project Manager Task Automation is disabled'
    assignees = _project_assignees(assign_value)
    if not assignees or REQUIRED_PROJECT_MANAGER not in assignees:
        return "ERP Project is not assigned to ai-projects"
    try:
        todos = tuple(open_project_todos)
    except TypeError:
        return "ERP Project assignment records are unavailable"
    for todo in todos:
        if not isinstance(todo, dict):
            continue
        if (
            todo.get("reference_type") == "Project"
            and todo.get("reference_name") == project_id
            and todo.get("allocated_to") == REQUIRED_PROJECT_MANAGER
            and todo.get("status") == "Open"
        ):
            return None
    return "ERP Project has no open ai-projects assignment"


def project_status_allowed(project: dict[str, Any], *, project_id: str) -> bool:
    """Cheap first-stage gate before additional ERP assignment queries."""
    return (
        isinstance(project, dict)
        and project.get("name") == project_id
        and str(project.get("status") or "").strip() in ACTIVE_STATUSES
    )

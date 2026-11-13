"""Authenticated ERP ↔ ABIT.AI Agentic Governance API v1.

ERP owns Project/Task and AI User identities. ABIT.AI owns per-project
Automations and durable agent-to-agent invocations. No SAP or ERP writes.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select

from open_webui.env import DATA_DIR
from open_webui.internal.db import get_async_db_context
from open_webui.models.automations import Automation, AutomationRun, AutomationModel, AutomationRuns, Automations
from open_webui.models.users import Users
from open_webui.utils.agentic_governance import (
    HANDOFFS,
    PROJECT_ID,
    ROLES,
    TASK_ID,
    RECONCILE_SECONDS,
    agent_id,
    enroll,
    enqueue,
    find,
    list_invocations,
    recoverable,
    roster,
    transition,
)
from open_webui.utils.automations import execute_automation
from open_webui.utils.agentic_project_policy import (
    REQUIRED_PROJECT_MANAGER,
    project_status_allowed,
    project_eligibility_reason,
)

log = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_ROLE_USERS = {
    "solution_architect": "ai-projects@abitcons.com",
    "project_manager": "ai-projects@abitcons.com",
    "developer": "ai-developer@abitcons.com",
    "qa": "ai-qa@abitcons.com",
    "operations": "ai-operations@abitcons.com",
    "it_manager": "ai-operations@abitcons.com",
}

ROLE_TITLES = {
    "solution_architect": "Solution Architect",
    "project_manager": "Project Manager",
    "developer": "AI Developer",
    "qa": "Independent QA",
    "operations": "Operations",
    "it_manager": "IT Manager",
}

ROLE_NOTES = {
    "solution_architect": (
        "Decompose the ERP Project SRS by algorithmic input, action, output and verification; "
        "produce inspectable UML and UI mockup artifacts, propose deduplicated ERP subtasks and "
        "acceptance tests via approved APIs. Never claim mockups were produced without files."
    ),
    "project_manager": (
        "Own the dependency-ordered project task tree; check active agents, leases and evidence "
        "before assigning. Send scoped requests to developers, operations, QA or IT via ABIT.AI API. "
        "Never mark a business task complete without independent verification."
    ),
    "developer": (
        "Verify authoritative repository, branch, instructions and existing work before editing. "
        "Implement safely, test and record exact evidence. Ask QA through authenticated agent API "
        "when a deliverable is ready. Never deploy to production without required approval."
    ),
    "qa": (
        "Independently test the required acceptance criteria, including security, regression and "
        "UI where relevant. Report reproducible defects with evidence and return work for correction. "
        "Do not approve your own implementation."
    ),
    "operations": (
        "Execute routine project operations using authorized systems of record. Verify permissions, "
        "task scope, outcomes and customer impact. Request IT/QA follow-ups through governed APIs."
    ),
    "it_manager": (
        "Inspect the exact authorized host, system, backups, log retention, service impact and "
        "recovery path before IT work. Official package installations and reversible disposal of "
        "identified temporary files may be permitted; preserve active/audit/security logs, "
        "database WAL, backups and business data. Require approval for risky production, "
        "destructive, sensitive security or material service-impacting actions."
    ),
}


class ProvisionInput(BaseModel):
    model_id: Literal["abitdot"] = "abitdot"


class AgentLink(BaseModel):
    role: Literal['solution_architect', 'project_manager', 'developer', 'qa', 'operations', 'it_manager']
    agent_id: str = Field(min_length=36, max_length=36)
    erp_user: str = Field(min_length=3, max_length=254)
    agent_url: str = Field(pattern=r'^https://abit\.ai/automations/[A-Za-z0-9-]{36}$')


class ProvisionResponse(BaseModel):
    project: str
    agents: list[AgentLink] = Field(min_length=6, max_length=6)
    trigger: Literal['api_only']
    polling: Literal['30-minute_reconciliation_fallback']


class AgentDiscoveryResponse(BaseModel):
    project: str
    agents: list[AgentLink] = Field(max_length=6)


class InvocationReceipt(BaseModel):
    invocation_id: str
    state: str
    created: bool
    project: str
    target_role: Literal['solution_architect', 'project_manager', 'developer', 'qa', 'operations', 'it_manager']


class InvocationHistoryResponse(BaseModel):
    project: str
    runs: list[dict] = Field(max_length=100)


class InvocationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    invocation_id: str = Field(min_length=16, max_length=160, pattern=r'^[A-Za-z0-9_.:@+-]+$')
    erp_task_id: str = Field(pattern=r'^TASK-[A-Za-z0-9-]{1,60}$')
    from_role: Literal['erp', 'solution_architect', 'project_manager', 'developer', 'qa', 'operations', 'it_manager']
    target_role: Literal['solution_architect', 'project_manager', 'developer', 'qa', 'operations', 'it_manager']
    task_revision: str = Field(min_length=1, max_length=100)
    context: str = Field(min_length=1, max_length=50000)
    from_agent_id: str | None = None
    source_invocation_id: str | None = None


def _auth(control_secret: str) -> None:
    # Reuse the already-deployed, server-to-server ERP Task control credential.
    # Do not issue API tokens to models or browsers; calls originate in trusted
    # ABIT.AI tools/ERP server-side proxy with an approved scoped identity.
    if not control_secret or len(control_secret) < 32:
        raise HTTPException(401, "Invalid ERP Agentic Governance credential")
    secret_file = Path(
        os.getenv(
            "ABIT_ERP_CONTROL_TOKEN_FILE",
            str(Path(DATA_DIR).parent / "config/erp-assignment/control-token"),
        )
    )
    try:
        expected = secret_file.read_text().strip()
    except OSError:
        raise HTTPException(503, "ERP Agentic Governance credential unavailable") from None
    if len(expected) < 32 or not control_secret or not hmac.compare_digest(control_secret, expected):
        raise HTTPException(401, "Invalid ERP Agentic Governance credential")


def _config() -> dict:
    cfgpath = os.getenv('ABIT_ERP_TASK_RESOLUTION_CONFIG')
    if not cfgpath:
        raise HTTPException(503, "ERP task integration not configured")
    try:
        return json.loads(Path(cfgpath).read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "ERP task configuration unavailable") from None


def _erp_failure(error, context):
    from open_webui.utils.erp_status_write import ERPResponseError

    if isinstance(error, ERPResponseError):
        return context + ': ' + str(error)[:700]
    return context + ' (' + type(error).__name__ + ')'


async def _erp_project(project: str, config: dict) -> dict:
    """Re-verify Project hold status and open ai-projects assignment on EVERY entry."""
    from open_webui.utils.erp_task_resolution import ERPClient

    if not PROJECT_ID.fullmatch(project):
        raise HTTPException(400, "Invalid ERP Project ID")
    erp = ERPClient(config["erp_token_file"])
    try:
        item = await erp.get("Project", project)
        if not project_status_allowed(item, project_id=project):
            raise HTTPException(409, "ERP Project is on hold, closed, or not active")
        # Preserve legacy pilot opt-in, but NEVER bypass the assignment gate.
        if item.get("custom_agentic_governance_enabled") not in (1, True, "1"):
            raise HTTPException(403, "ERP Project is not opted into Agentic Governance")
        if item.get('custom_ai_project_manager_task_automation_enabled') not in (1, True, '1'):
            raise HTTPException(403, 'ERP Project AI Project Manager Task Automation checkbox is disabled')
        assignment = await erp.request(
            "GET",
            "/api/method/frappe.client.get_value",
            params={
                "doctype": "Project",
                "filters": project,
                "fieldname": "_assign",
            },
        )
        payload = assignment.get("message") or {}
        # Both live records must agree: an open ToDo alone or stale _assign
        # alone must not authorize any model execution or new agent provision.
        todos = [
            row
            async for row in erp.rows(
                "ToDo",
                ["name", "allocated_to", "reference_type", "reference_name", "status"],
                [
                    ["reference_type", "=", "Project"],
                    ["reference_name", "=", project],
                    ["allocated_to", "=", REQUIRED_PROJECT_MANAGER],
                    ["status", "=", "Open"],
                ],
            )
        ]
        reason = project_eligibility_reason(
            item,
            payload.get("_assign"),
            todos,
            project_id=project,
        )
        if reason:
            raise HTTPException(403, reason)
        return item
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            503, _erp_failure(error, "ERP Project status or assignment could not be verified")
        ) from None
    finally:
        await erp.client.aclose()


async def _erp_task(project: str, task_id: str, revision: str, config: dict, target_role: str = "developer") -> dict:
    from open_webui.utils.erp_task_resolution import ERPClient

    if not TASK_ID.fullmatch(task_id):
        raise HTTPException(400, "Invalid ERP Task ID")
    erp = ERPClient(config["erp_token_file"])
    try:
        task = await erp.get("Task", task_id)
    except Exception:
        raise HTTPException(503, "ERP Task could not be verified") from None
    finally:
        await erp.client.aclose()
    if task.get("project") != project or task.get("is_group") or task.get("is_template"):
        raise HTTPException(403, "ERP Task scope mismatch")
    allowed = {"Pending Review", "QA Reviewing"} if target_role == "qa" else {"Open", "Overdue"}
    if task.get("status") not in allowed:
        raise HTTPException(409, "ERP Task is not eligible for this specialist role")
    if str(task.get("modified") or "") != revision:
        raise HTTPException(409, "ERP Task revision changed; refresh work packet")
    return task


async def _role_users(config: dict) -> dict[str, str]:
    from open_webui.utils.erp_task_resolution import ERPClient

    # Read the same shared role users in every project. Do not create new ERP Users.
    mapping = dict(DEFAULT_ROLE_USERS)
    optional = config.get("agentic_role_users") or {}
    if isinstance(optional, dict):
        mapping.update({k: v for k, v in optional.items() if k in ROLES and isinstance(v, str)})
    erp = ERPClient(config["erp_token_file"])
    try:
        for email in set(mapping.values()):
            record = await erp.get("User", email)
            if record.get("name") != email or int(record.get("enabled") or 0) != 1:
                raise HTTPException(409, "ERP role user is not enabled")
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, _erp_failure(error, "ERP role user identities could not be verified")) from None
    finally:
        await erp.client.aclose()
    return mapping


def _prompt(project: dict, role: str, project_id: str) -> str:
    name = str(project.get("project_name") or project_id)[:180]
    instructions = str(project.get("custom_ai_developer_instructions") or "")[:12000]
    return (
        f"ABIT.AI GOVERNANCE AGENT: {ROLE_TITLES[role]} for ERPNext Project {project_id} ({name}).\n"
        "ERPNext https://erp.abit.ai is the authority for Project instructions, tasks, "
        "assignments, comments and approvals. Run only the authenticated scoped task passed "
        "by ABIT.AI governance. Existing instructions, security and approvals outrank model text.\n"
        f"ROLE POLICY: {ROLE_NOTES[role]}\n"
        f"ERP PROJECT AI INSTRUCTIONS:\n{instructions}\n"
        "Agent-to-agent handoffs must go through authenticated ABIT.AI governance APIs, "
        "not a chat-only assertion or recurring polling. A success response is not a "
        "verified business task completion."
    )


@router.get("/health")
async def health():
    # No credentials or names: safe public readiness check.
    return {
        "service": "ABiT Agentic Governance API",
        "version": 1,
        "status": "ready",
        "event_driven": True,
        "reconciliation_fallback_seconds": RECONCILE_SECONDS,
        "roles": list(ROLES),
    }


@router.post("/projects/{project_id}/provision", response_model=ProvisionResponse)
async def provision(
    project_id: str,
    body: ProvisionInput,
    control_secret: str = Header(default="", alias="X-ABIT-ERP-Control"),
):
    _auth(control_secret)
    return await _provision_project(project_id, body.model_id)


async def _provision_project(project_id, model_id="abitdot"):
    cfg = _config()
    project = await _erp_project(project_id, cfg)
    role_users = await _role_users(cfg)
    user = await Users.get_user_by_email(cfg.get("owner_email") or "")
    if not user or user.role != "admin":
        raise HTTPException(503, "Configured ABIT.AI governance owner is unavailable")
    async with get_async_db_context() as db:
        for role in ROLES:
            aid = agent_id(project_id, role)
            existing = await db.get(Automation, aid)
            if existing:
                meta = existing.meta or {}
                if (
                    existing.user_id != user.id
                    or meta.get("agentic_project") != project_id
                    or meta.get("agentic_role") != role
                    or meta.get("erp_role_user") != role_users[role]
                ):
                    raise HTTPException(409, "Existing agent ID conflicts with governance scope")
        now = time.time_ns()
        for role in ROLES:
            aid = agent_id(project_id, role)
            if await db.get(Automation, aid):
                continue
            db.add(
                Automation(
                    id=aid,
                    user_id=user.id,
                    folder_id=None,
                    name=f"AGOV {project_id} — {ROLE_TITLES[role]}",
                    data={
                        "prompt": _prompt(project, role, project_id),
                        "model_id": model_id,
                        "continue_previous_chat": False,
                        "rrule": "DTSTART:20260101T000000Z\nRRULE:FREQ=DAILY;COUNT=1",
                    },
                    meta={
                        "agentic_project": project_id,
                        "agentic_role": role,
                        "erp_role_user": role_users[role],
                        "governance_managed": True,
                        "api_triggered_only": True,
                    },
                    is_active=True,
                    next_run_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.commit()
    try:
        records = enroll(project_id, user.id, role_users)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    from open_webui.utils.agentic_bridge import ERPBridge
    from open_webui.utils.agentic_governance import project_control

    bridge = ERPBridge(cfg)
    try:
        await bridge.register(project_id, str(project['modified']), records)
    except Exception as error:
        raise HTTPException(
            503,
            _erp_failure(
                error, 'Specialists exist in ABIT.AI; ERP binding verification is incomplete. No execution was started.'
            ),
        ) from None
    finally:
        await bridge.close()
    if project_control(project_id)['state'] == 'disabled':
        project_control(project_id, 'enable', actor='erp', policy_revision=str(project['modified']))
    return {
        "project": project_id,
        "agents": [
            {
                "agent_id": r["agent_id"],
                "erp_user": r["erp_user"],
                "role": r["role"],
                "agent_url": "https://abit.ai/automations/" + r["agent_id"],
            }
            for r in records
        ],
        "trigger": "api_only",
        "polling": "30-minute_reconciliation_fallback",
    }


@router.get("/projects/{project_id}/agents", response_model=AgentDiscoveryResponse)
async def agents(
    project_id: str,
    control_secret: str = Header(default="", alias="X-ABIT-ERP-Control"),
):
    _auth(control_secret)
    records = roster(project_id)
    return {
        "project": project_id,
        "agents": [
            {
                "role": r["role"],
                "agent_id": r["agent_id"],
                "erp_user": r["erp_user"],
                "agent_url": "https://abit.ai/automations/" + r["agent_id"],
            }
            for r in records
        ],
    }


async def _gate(project_id, config):
    item = await _erp_project(project_id, config)
    return item, {
        'enabled': True,
        'pm_task_automation_enabled': True,
        'project_status': item['status'],
        'assigned_user': REQUIRED_PROJECT_MANAGER,
        'revision': str(item['modified']),
    }


def _spawn(app, coroutine):
    # Keep strong references and cancel observers explicitly during shutdown;
    # upstream receipts/leases remain fenced rather than being replayed.
    tasks = getattr(app.state, 'agentic_execution_tasks', None)
    if tasks is None:
        tasks = set()
        app.state.agentic_execution_tasks = tasks
    task = asyncio.create_task(coroutine)
    tasks.add(task)

    def completed(done):
        tasks.discard(done)
        if not done.cancelled() and done.exception():
            log.error('Governance event remains pending for original recovery: %s', type(done.exception()).__name__)

    task.add_done_callback(completed)
    return task


async def _deliver(app, invocation_id: str, task=None, context=None):
    from open_webui.utils.agentic_executor import execute, DotAdapter
    from open_webui.utils.agentic_governance import find

    cfg = _config()
    row = find(invocation_id)
    if row and row['target_role'] == 'it_manager':
        from open_webui.utils.agentic_it_execution import ITAdapter

        adapter = ITAdapter(app, _gate, cfg)
    else:
        adapter = DotAdapter(app)
    receipt = await execute(invocation_id, project_gate=_gate, config=cfg, adapter=adapter)
    if receipt:
        await _advance(app, invocation_id, receipt)


async def _advance(app, invocation_id, receipt):
    from open_webui.utils.agentic_orchestrator import advance

    async def dispatch(ident):
        _spawn(app, _deliver(app, ident))

    return await advance(invocation_id, receipt, config=_config(), project_gate=_gate, dispatch=dispatch)


@router.post("/projects/{project_id}/invoke", status_code=202, response_model=InvocationReceipt)
async def invoke(
    project_id: str,
    body: InvocationInput,
    request: Request,
    control_secret: str = Header(default="", alias="X-ABIT-ERP-Control"),
):
    _auth(control_secret)
    cfg = _config()
    project, gate = await _gate(project_id, cfg)
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import ERPBridge

    bridge = ERPBridge(cfg)
    try:
        packet = await bridge.packet(
            project_id, body.erp_task_id, body.target_role, agent_id(project_id, body.target_role)
        )
        if body.task_revision not in {packet['task_revision'], packet.get('source_modified')}:
            raise HTTPException(409, 'ERP work packet changed; refresh before invocation')
        args = dict(
            invocation_id=body.invocation_id,
            project=project_id,
            task=body.erp_task_id,
            from_role=body.from_role,
            target_role=body.target_role,
            task_revision=packet['task_revision'],
            context=body.context,
            work_packet=packet,
            gate=gate,
            actor='authenticated-erp-bridge',
        )
        if body.from_role != 'erp':
            source = ledger.find(body.source_invocation_id or '', include_private=True)
            from open_webui.utils.agentic_result_store import get_result

            published = get_result(body.source_invocation_id or '')
            if (
                not source
                or source['erp_project'] != project_id
                or source['target_role'] != body.from_role
                or source['agent_id'] != body.from_agent_id
                or source['state'] != 'execution_finished'
                or not published
                or published['phase'] != 'published'
                or published['run_id'] != source['automation_run_id']
            ):
                raise HTTPException(403, 'An authenticated finished source invocation is required')
            if source['erp_task'] == body.erp_task_id:
                row, created = ledger.handoff(
                    **args,
                    source_invocation_id=source['invocation_id'],
                    source_run_id=source['automation_run_id'],
                    source_lease_token=source['lease_token'],
                )
            else:
                child = await bridge.erp.get('Task', body.erp_task_id)
                if (
                    child.get('project') != project_id
                    or child.get('parent_task') != source['erp_task']
                    or child.get('custom_agov_source_run') != source['automation_run_id']
                    or not child.get('custom_agov_dispatch_released')
                ):
                    raise HTTPException(403, 'Agent fanout requires a verified child task in the same ERP project')
                row, created = ledger.enqueue(**args, parent_invocation_id=source['invocation_id'])
        else:
            row, created = ledger.enqueue(**args)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    finally:
        await bridge.close()
    if created:
        _spawn(request.app, _deliver(request.app, body.invocation_id))
    return {
        'invocation_id': body.invocation_id,
        'state': row['state'],
        'created': created,
        'project': project_id,
        'target_role': body.target_role,
    }


@router.get("/projects/{project_id}/invocations", response_model=InvocationHistoryResponse)
async def invocations(project_id: str, control_secret: str = Header(default="", alias="X-ABIT-ERP-Control")):
    _auth(control_secret)
    # Never echo prompt/context, secrets or ERP private work packet contents.
    return {"project": project_id, "runs": list_invocations(project_id)}


async def reconciliation_loop(app):
    """ONE global 30-minute fallback for missed events and original receipts."""
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import ERPBridge, RPC
    from open_webui.utils.agentic_orchestrator import scan_project

    while True:
        try:
            cfg = _config()
            bridge = ERPBridge(cfg)
            try:
                await bridge.erp.request(
                    'POST', RPC + 'recover_outbox', json={}, headers={'X-ABIT-Agentic-Bridge': bridge.secret}
                )
            except Exception:
                log.exception('ERP outbox recovery unavailable; original events retained')
            finally:
                await bridge.close()
            result = ledger.recoverable()
            for ident in result['queued_for_reconciliation']:
                _spawn(app, _deliver(app, ident))
            for event in ledger.pending_events():
                try:
                    await _process_event(app, event['event_id'], event['payload'])
                except Exception:
                    log.exception('Governance event remains durably pending: %s', event['event_id'])
            with ledger.connect() as db:
                projects = [
                    r[0] for r in db.execute('SELECT DISTINCT erp_project FROM governance_agent ORDER BY erp_project')
                ]

            async def dispatch(ident):
                _spawn(app, _deliver(app, ident))

            for project in projects:
                await _recover_project(app, project)
                try:
                    await scan_project(project, config=cfg, project_gate=_gate, dispatch=dispatch)
                except Exception:
                    log.exception('Governance reconciliation preserved project %s', project)
        except asyncio.CancelledError:
            for task in list(getattr(app.state, 'agentic_execution_tasks', set())):
                task.cancel()
            await asyncio.gather(*getattr(app.state, 'agentic_execution_tasks', set()), return_exceptions=True)
            raise
        except Exception:
            log.exception('Governance recovery preserved existing requests')
        await asyncio.sleep(RECONCILE_SECONDS)


class ProjectControlInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start', 'pause', 'resume', 'stop', 'retry']
    command_id: str = Field(min_length=12, max_length=160, pattern=r'^[A-Za-z0-9_.:@+-]+$')
    expected_revision: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=254)
    task_id: str | None = Field(default=None, pattern=r'^TASK-[A-Za-z0-9-]{1,60}$')
    target_role: Literal['solution_architect', 'project_manager'] = 'project_manager'


class ERPEventInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1]
    event_id: str = Field(min_length=12, max_length=160, pattern=r'^[A-Za-z0-9_.:@+-]+$')
    type: Literal['project.changed', 'project.control', 'task.ready', 'backlog.ready', 'result.recorded']
    project: str = Field(pattern=r'^PROJ-[A-Za-z0-9-]{1,60}$')
    task: str = Field(default='', max_length=70)
    revision: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict, max_length=50)
    source_modified: str = Field(min_length=1, max_length=100)


class EventReceipt(BaseModel):
    event_id: str
    durable: bool
    duplicate: bool = False


class ProjectStatusResponse(BaseModel):
    project: str
    control: dict
    agents: list[dict]
    task_controls: list[dict]
    invocations: list[dict]
    audit: list[dict]
    execution_semantics: str


class CommandReceipt(BaseModel):
    project: str
    command_id: str
    durable: bool
    state: str


async def _process_event(app, event_id, event):
    from open_webui.utils.agentic_events import process_event
    from open_webui.utils.agentic_bridge import ERPBridge
    from open_webui.utils.agentic_orchestrator import scan_project

    cfg = _config()

    async def dispatch(ident):
        _spawn(app, _deliver(app, ident))

    async def scan(project):
        return await scan_project(project, config=cfg, project_gate=_gate, dispatch=dispatch)

    async def recover(project, task, explicit):
        return await _recover_project(app, project, task, explicit=explicit)

    return await process_event(
        event_id,
        event,
        config=cfg,
        project_gate=_gate,
        provision=_provision_project,
        bridge_factory=ERPBridge,
        dispatch=dispatch,
        scan=scan,
        recover=recover,
    )


async def _recover_project(app, project, task=None, *, explicit=False):
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import ERPBridge
    from open_webui.utils.agentic_result_store import get_result
    from open_webui.utils.agentic_executor import reconcile_publication
    from open_webui.utils.agentic_recovery import recover_original
    from open_webui.utils.agentic_it_execution import resume, recover_completed
    from open_webui.utils.agentic_unsubmitted import rearm
    from open_webui.utils.agentic_controls import task_control

    cfg = _config()
    bridge = ERPBridge(cfg)
    reconciled = []
    try:
        for public in ledger.list_invocations(project, limit=100):
            if task and public['erp_task'] != task:
                continue
            row = ledger.find(public['invocation_id'], include_private=True)
            ident = row['invocation_id']
            receipt = None
            try:
                saved = get_result(ident)
                if saved:
                    if saved['phase'] == 'pending':
                        if row['state'] not in {'running', 'uncertain', 'execution_finished'}:
                            continue
                        await _gate(project, cfg)
                        if (
                            ledger.project_control(project)['state'] != 'enabled'
                            or task_control(project, row['erp_task'])['state'] != 'enabled'
                        ):
                            continue
                    receipt = await reconcile_publication(ident, bridge)
                elif row['state'] == 'awaiting_approval':
                    receipt = await resume(ident, config=cfg, project_gate=_gate, bridge=bridge, app=app)
                elif row['state'] in {'uncertain', 'cancel_requested'}:
                    recovery = recover_completed if row['target_role'] == 'it_manager' else recover_original
                    receipt = await recovery(ident, config=cfg, project_gate=_gate, bridge=bridge, app=app)
                elif explicit and row['state'] == 'blocked' and not row['automation_run_id']:
                    _, gate = await _gate(project, cfg)
                    packet = await bridge.packet(project, row['erp_task'], row['target_role'], row['agent_id'])
                    rearm(ident, packet, gate, 'authenticated-erp-retry')
                    _spawn(app, _deliver(app, ident))
                    reconciled.append(ident)
                if receipt:
                    reconciled.append(ident)
                    await _gate(project, cfg)
                    if ledger.project_control(project)['state'] == 'enabled':
                        await _advance(app, ident, receipt)
            except Exception:
                log.exception('Original governance recovery retained its fence: %s', ident)
    finally:
        await bridge.close()
    return reconciled


@router.post('/events', response_model=EventReceipt, status_code=202)
async def receive_erp_event(body: ERPEventInput, request: Request):
    """Accept an HMAC-authenticated ERP outbox event once, before execution.

    Requires a timestamp within five minutes and exact signed event identity.
    Duplicate content returns the existing durable inbox receipt. Changed content
    under one ID is rejected. Model execution occurs asynchronously and is never
    implied by HTTP acceptance. No shared credential is returned to the browser.
    """
    from open_webui.utils.agentic_bridge import bridge_secret, verify_event
    from open_webui.utils.agentic_governance import receive_event

    event = body.model_dump()
    try:
        verify_event(event, dict(request.headers), bridge_secret(_config()))
        row, created = receive_event(body.event_id, body.project, event, actor='signed-erp')
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    if created:
        _spawn(request.app, _process_event(request.app, body.event_id, event))
    return EventReceipt(event_id=body.event_id, durable=True, duplicate=not created)


@router.get('/projects/{project_id}/status', response_model=ProjectStatusResponse)
async def project_status(project_id: str, control_secret: str = Header(default='', alias='X-ABIT-ERP-Control')):
    """ERP-only project controls, role registry, bounded run history and audit.

    Read access remains available after opt-out, Hold or Stop. Private prompts,
    packets, lease tokens and connector credentials are never included. A finished
    model execution is distinct from verified independent QA and ERP readback.
    """
    _auth(control_secret)
    if not PROJECT_ID.fullmatch(project_id):
        raise HTTPException(400, 'Invalid ERP Project ID')
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_controls import task_controls
    from open_webui.utils.agentic_events import effective_control

    return {
        'project': project_id,
        'control': effective_control(project_id),
        'task_controls': task_controls(project_id),
        'agents': [{k: v for k, v in r.items() if k != 'owner_id'} for r in roster(project_id)],
        'invocations': list_invocations(project_id, limit=100),
        'audit': ledger.audit_events(project_id),
        'execution_semantics': 'Queued -> Running -> execution_finished -> independent QA + ERP -> verified. Unknown upstream work remains fenced.',
    }


@router.post('/projects/{project_id}/control', response_model=CommandReceipt, status_code=202)
async def control_project(
    project_id: str,
    body: ProjectControlInput,
    request: Request,
    control_secret: str = Header(default='', alias='X-ABIT-ERP-Control'),
):
    """Idempotent ERP control: Start/Pause/Resume/Stop or reconcile original results.

    Start/Resume require the current opt-in checkbox, active Project and matching
    open ai-projects assignment. Pause/Stop remain available after revocation.
    Active Stop means cancellation requested until an upstream receipt confirms
    termination; Retry never blindly reruns an unknown/submitted model request.
    """
    _auth(control_secret)
    from open_webui.utils.agentic_governance import receive_event

    if not PROJECT_ID.fullmatch(project_id):
        raise HTTPException(400, 'Invalid ERP Project ID')
    cfg = _config()
    if body.action not in {'pause', 'stop'}:
        project = await _erp_project(project_id, cfg)
    else:
        from open_webui.utils.erp_task_resolution import ERPClient

        erp = ERPClient(cfg['erp_token_file'])
        try:
            project = await erp.get('Project', project_id)
        finally:
            await erp.client.aclose()
    if str(project['modified']) != body.expected_revision:
        raise HTTPException(409, 'ERP Project revision changed')
    if body.task_id:
        from open_webui.utils.erp_task_resolution import ERPClient

        erp = ERPClient(cfg['erp_token_file'])
        try:
            task = await erp.get('Task', body.task_id)
        finally:
            await erp.client.aclose()
        if task.get('project') != project_id:
            raise HTTPException(403, 'Task belongs to another ERP Project')
    event = {
        'schema_version': 1,
        'event_id': body.command_id,
        'type': 'project.control',
        'project': project_id,
        'task': body.task_id or '',
        'revision': body.expected_revision,
        'source_modified': body.expected_revision,
        'payload': body.model_dump(),
    }
    try:
        row, created = receive_event(body.command_id, project_id, event, actor=body.actor)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    if created:
        _spawn(request.app, _process_event(request.app, body.command_id, event))
    return {'project': project_id, 'command_id': body.command_id, 'durable': True, 'state': row['state']}

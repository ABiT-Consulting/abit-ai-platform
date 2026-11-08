import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_session
from open_webui.models.automations import (
    AutomationForm,
    AutomationListResponse,
    AutomationModel,
    AutomationResponse,
    AutomationRun,
    AutomationRunModel,
    AutomationRuns,
    Automations,
)
from open_webui.models.access_grants import AccessGrants, has_public_write_access_grant
from open_webui.models.channels import Channels
from open_webui.models.config import Config
from open_webui.models.folders import Folders
from open_webui.utils.access_control import has_permission
from open_webui.utils.automation_attachments import resolve_automation_attachments
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.automations import (
    execute_automation,
    next_n_runs_ns,
    next_run_ns,
    rrule_interval_seconds,
    validate_rrule,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter()

PAGE_ITEM_COUNT = 30


############################
# Helpers
############################


async def check_automations_permission(request, user):
    config = await Config.get_many('automations.enable', 'user.permissions')
    if not config.get('automations.enable'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    if user.role != 'admin' and not await has_permission(
        user.id, 'features.automations', config.get('user.permissions')
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


def check_automation_access(automation, user, *, mutation=False):
    if not automation or user.id != automation.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    if mutation and is_erp_managed(automation):
        raise HTTPException(409, 'This agent is managed by its ERP task')


def is_erp_managed(automation):
    meta = automation.meta or {}
    managed = meta.get('erp_managed')
    return bool(meta.get('erp_task') and (managed is True or (automation.id.startswith('erp-') and managed is not False)))


def erp_attempt_runs(automation: AutomationModel) -> list[AutomationRunModel]:
    run = automation.meta.get('erp_run') or automation.meta.get('erp_legacy_run') or {}
    attempts = run.get('previous_attempts', []) + run.get('attempts', [])
    return [
        AutomationRunModel(
            id=attempt['id'],
            automation_id=automation.id,
            chat_id=attempt.get('chat_id'),
            status='success' if attempt['phase'] == 'verified' else 'error' if attempt.get('finished') else 'running',
            error=attempt.get('stop_reason'),
            created_at=int(attempt['started'] * 1_000_000_000),
            attempt_number=attempt['number'],
            cycle=attempt.get('cycle'),
        )
        for attempt in reversed(attempts)
    ]


def automation_response_fields(automation: AutomationModel, last_run=None) -> dict:
    fields = automation.model_dump()
    if is_erp_managed(automation):
        last_run = next(iter(erp_attempt_runs(automation)), None)
        fields['last_run_at'] = last_run.created_at if last_run else None
    return {**fields, 'last_run': last_run}


async def check_automation_limits(request, user, rrule_str: str, db, is_create: bool = False):
    """Enforce global automation limits. Admins bypass all checks."""
    if user.role == 'admin':
        return

    # Max count (create only)
    if is_create:
        max_count = await Config.get('automations.max_count')
        if max_count:
            max_count = int(max_count)
            if max_count > 0 and await Automations.count_by_user(user.id, db=db) >= max_count:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=ERROR_MESSAGES.AUTOMATION_LIMIT_EXCEEDED(max_count),
                )

    # Min interval (create + update)
    min_interval = await Config.get('automations.min_interval')
    if min_interval:
        min_interval = int(min_interval)
        if min_interval > 0:
            interval = await rrule_interval_seconds(rrule_str)
            if interval is not None and interval < min_interval:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.AUTOMATION_TOO_FREQUENT(min_interval),
                )


async def check_automation_folder_access(folder_id: Optional[str], user, db: AsyncSession):
    if folder_id is None:
        return
    folder = await Folders.get_folder_by_id_and_user_id(folder_id, user.id, db=db)
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


async def check_automation_channel_access(form_data: AutomationForm, user, db: AsyncSession):
    target = form_data.data.target
    if not target or target.type != 'channel':
        return

    if not target.channel_id or not await Config.get('channels.enable'):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    channel = await Channels.get_channel_by_id(target.channel_id, db=db)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if user.role == 'admin':
        return
    if not await has_permission(user.id, 'features.channels', await Config.get('user.permissions')):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.DEFAULT(),
        )
    if channel.type in ['group', 'dm']:
        allowed = await Channels.is_user_channel_member(channel.id, user.id, db=db)
    else:
        allowed = has_public_write_access_grant(channel.access_grants) or await AccessGrants.has_access(
            user_id=user.id, resource_type='channel', resource_id=channel.id, permission='write', db=db
        )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


async def enrich_automation(automation: AutomationModel, db: AsyncSession, tz: str = None) -> AutomationResponse:
    """Full enrichment for single-item views (includes next_runs computation)."""
    last_run = None if is_erp_managed(automation) else await AutomationRuns.get_latest(automation.id, db=db)
    remaining = automation.attempts_remaining
    preview_count = 5 if remaining is None else min(5, remaining)
    next_runs = (
        await next_n_runs_ns(automation.data['rrule'], n=preview_count, tz=tz)
        if automation.is_active and automation.next_run_at is not None and preview_count
        else []
    )
    return AutomationResponse(
        **automation_response_fields(automation, last_run),
        next_runs=next_runs,
    )


############################
# GetAutomationItems (paginated)
############################


@router.get('/list')
async def get_automation_items(
    request: Request,
    query: Optional[str] = None,
    status: Optional[str] = None,
    folder_id: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    limit = PAGE_ITEM_COUNT
    page = max(1, page)
    skip = (page - 1) * limit

    result = await Automations.search_automations(
        user_id=user.id,
        query=query,
        status=status,
        folder_id=folder_id,
        skip=skip,
        limit=limit,
        db=db,
    )

    # Batch-fetch latest runs in a single query instead of N+1
    ids = [item.id for item in result.items if not is_erp_managed(item)]
    latest_runs = await AutomationRuns.get_latest_batch(ids, db=db) if ids else {}

    return {
        'items': [
            AutomationResponse(
                **automation_response_fields(item, latest_runs.get(item.id)),
            )
            for item in result.items
        ],
        'total': result.total,
    }


############################
# CreateNewAutomation
############################


@router.post('/create', response_model=AutomationResponse)
async def create_new_automation(
    request: Request,
    form_data: AutomationForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    await check_automation_folder_access(form_data.folder_id, user, db)
    await check_automation_channel_access(form_data, user, db)
    await resolve_automation_attachments(form_data.data.attachments, user, db=db)
    try:
        await validate_rrule(form_data.data.rrule, tz=user.timezone)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    await check_automation_limits(request, user, form_data.data.rrule, db, is_create=True)

    tz = user.timezone
    automation = await Automations.insert(user.id, form_data, await next_run_ns(form_data.data.rrule, tz=tz), db=db)
    response = await enrich_automation(automation, db, tz=tz)
    await publish_event(
        request,
        EVENTS.AUTOMATION_CREATED,
        actor=user,
        subject_id=automation.id,
        data={'name': automation.name, 'is_active': automation.is_active, 'folder_id': automation.folder_id},
    )
    return response


############################
# GetAutomationById
############################


@router.get('/{id}', response_model=AutomationResponse)
async def get_automation_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user)
    return await enrich_automation(automation, db, tz=user.timezone)


############################
# UpdateAutomationById
############################


@router.post('/erp/assignment', status_code=202)
async def erp_assignment(request: Request):
    from open_webui.utils.erp_task_runtime import receive_assignment

    return await receive_assignment(request)


@router.post('/{id}/erp-reset', status_code=202)
async def reset_erp_agent(
    request: Request, id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session),
):
    import os
    from open_webui.utils.erp_task_resolution import ERPClient, REOPENABLE, Resolver, Store
    from open_webui.utils.erp_task_runtime import CONFIG, runtime_state
    import json
    from pathlib import Path

    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user)
    task_id = (automation.meta or {}).get('erp_task')
    if not is_erp_managed(automation):
        raise HTTPException(404, 'Managed ERP agent not found')
    config_path = os.environ.get(CONFIG)
    if not config_path:
        raise HTTPException(503, 'ERP task resolution is disabled')
    store = Store(runtime_state())
    run = store.get(task_id)
    if not run or run['agent'] != automation.id or not run.get('synced') or run['status'] not in REOPENABLE:
        raise HTTPException(409, 'Only a synchronized terminal ERP agent can be reset')
    config = json.loads(Path(config_path).read_text())
    erp = ERPClient(config['erp_token_file'])
    resolver = Resolver(store, erp, None, None, None, config['projects'],
                        creator=config.get('standard_agent_creator'),
                        development_tasks=config.get('development_tasks', ()))
    try:
        task = await erp.get('Task', task_id)
        if task['status'] != 'Open' or not await resolver.authorized(task):
            raise HTTPException(409, 'The ERP task must be Open and authorized for this agent')
    finally:
        await erp.client.aclose()
    store.queue_reset(task_id)
    return {'queued': True}


@router.post('/{id}/update', response_model=AutomationResponse)
async def update_automation_by_id(
    request: Request,
    id: str,
    form_data: AutomationForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user, mutation=True)
    await check_automation_folder_access(form_data.folder_id, user, db)
    await check_automation_channel_access(form_data, user, db)
    await resolve_automation_attachments(form_data.data.attachments, user, db=db)

    try:
        await validate_rrule(form_data.data.rrule, tz=user.timezone)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    await check_automation_limits(request, user, form_data.data.rrule, db, is_create=False)

    tz = user.timezone
    updated = await Automations.update_by_id(id, form_data, await next_run_ns(form_data.data.rrule, tz=tz), db=db)
    response = await enrich_automation(updated, db, tz=tz)
    await publish_event(
        request,
        EVENTS.AUTOMATION_UPDATED,
        actor=user,
        subject_id=updated.id,
        data={'name': updated.name, 'is_active': updated.is_active, 'folder_id': updated.folder_id},
    )
    return response


############################
# ToggleAutomationById
############################


@router.post('/{id}/toggle', response_model=AutomationResponse)
async def toggle_automation_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user, mutation=True)
    if automation.is_stale:
        return await enrich_automation(automation, db, tz=user.timezone)
    toggled = await Automations.toggle(id, await next_run_ns(automation.data['rrule'], tz=user.timezone), db=db)
    response = await enrich_automation(toggled, db, tz=user.timezone)
    await publish_event(
        request,
        EVENTS.AUTOMATION_ENABLED if toggled.is_active else EVENTS.AUTOMATION_DISABLED,
        actor=user,
        subject_id=toggled.id,
        subject_type='automation',
        data={'name': toggled.name},
    )
    return response


############################
# RunAutomationById
############################


@router.post('/{id}/run')
async def run_automation_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user, mutation=True)
    asyncio.create_task(execute_automation(request.app, automation))
    await publish_event(
        request,
        EVENTS.AUTOMATION_RUN_STARTED,
        actor=user,
        subject_id=automation.id,
        data={'name': automation.name},
    )
    return await enrich_automation(automation, db, tz=user.timezone)


############################
# DeleteAutomationById
############################


@router.delete('/{id}/delete')
async def delete_automation_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user, mutation=True)
    await AutomationRuns.delete_by_automation(id, db=db)
    result = await Automations.delete(id, db=db)
    if result:
        await publish_event(
            request,
            EVENTS.AUTOMATION_DELETED,
            actor=user,
            subject_id=id,
            data={'name': automation.name},
        )
    return result


############################
# GetAutomationRuns
############################


@router.get('/{id}/runs', response_model=list[AutomationRunModel])
async def get_automation_runs(
    request: Request,
    id: str,
    skip: int = 0,
    limit: int = 50,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_automations_permission(request, user)
    automation = await Automations.get_by_id(id, db=db)
    check_automation_access(automation, user)
    if is_erp_managed(automation):
        start = max(0, skip)
        size = max(0, limit)
        attempts = erp_attempt_runs(automation)
        managed = attempts[start:start + size]
        if (automation.meta or {}).get('erp_standard_agent') and len(managed) < size:
            managed += await AutomationRuns.get_by_automation(
                id, skip=max(0, start - len(attempts)), limit=size - len(managed), db=db,
            )
        return managed
    runs = await AutomationRuns.get_by_automation(id, skip=skip, limit=limit, db=db)
    if (automation.meta or {}).get('erp_legacy_run') and len(runs) < limit:
        if skip:
            # Determine the offset into the archived worker runs.
            count = await db.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.automation_id == id))
            start = max(0, skip - count)
        else:
            start = 0
        runs += erp_attempt_runs(automation)[start:start + limit - len(runs)]
    return runs

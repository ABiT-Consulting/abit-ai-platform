"""
Automation utilities and unified scheduler.

RRULE helpers, scheduler worker loop, and execution logic.
Follows the utils/<feature>.py pattern (cf. utils/channels.py, utils/task.py).

The scheduler_worker_loop handles all time-based background work:
  - Automation execution (claim_due → execute)
  - Calendar event alerts (upcoming events → socket + webhook notifications)
  - One-shot chat timers

Environment:
    SCHEDULER_POLL_INTERVAL             – seconds between polls (default: 10)
    TIMER_POLL_INTERVAL                 – seconds between timer polls (default: 1)
    CALENDAR_ALERT_LOOKAHEAD_MINUTES   – default alert window (default: 5)
"""
from open_webui.utils import agentic_maintenance as _agov_maintenance
from open_webui.utils.automation_admission import blocked_automation_references, denial_status

import asyncio
import logging
import os
import random
import time
from datetime import timedelta
from typing import Optional
from uuid import uuid4

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_db
from open_webui.models.automations import SCHEDULED_RUN_LEASE_NS, AutomationModel, AutomationRuns, Automations
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.config import Config
from open_webui.models.folders import Folders
from open_webui.models.messages import MessageForm
from open_webui.models.users import Users
from open_webui.utils.auth import create_token
from open_webui.utils.misc import parse_duration
from open_webui.utils.recurrence import (
    _resolve_tz,
    next_n_runs_ns,
    next_run_ns,
    rrule_interval_seconds,
    validate_rrule,
)
from open_webui.utils.task import prompt_template
from open_webui.utils.terminals import get_terminal_server_url
from starlette.datastructures import Headers

log = logging.getLogger(__name__)

SCHEDULER_POLL_INTERVAL = int(os.getenv('SCHEDULER_POLL_INTERVAL', os.getenv('AUTOMATION_POLL_INTERVAL', '10')))
TIMER_POLL_INTERVAL = int(os.getenv('TIMER_POLL_INTERVAL', '1'))
CALENDAR_ALERT_LOOKAHEAD_MINUTES = int(os.getenv('CALENDAR_ALERT_LOOKAHEAD_MINUTES', '10'))


############################
# Worker Loop
############################


# Keep the old name as an alias so any stale imports still work.
async def automation_worker_loop(app) -> None:
    """Deprecated alias — use scheduler_worker_loop."""
    await scheduler_worker_loop(app)


async def scheduler_worker_loop(app):
    try:
        await _scheduler_worker_loop_impl(app)
    finally:
        resumed = getattr(app.state, 'erp_dot_resume_task', None)
        if resumed is not None:
            resumed.cancel()
            await asyncio.gather(resumed, return_exceptions=True)


async def _scheduler_worker_loop_impl(app) -> None:
    import os

    if not os.environ.get('ABIT_ERP_TASK_RESOLUTION_CONFIG'):
        return await _scheduler_worker_loop(app)

    from open_webui.utils.erp_task_runtime import scheduler_loop

    async def run_erp():
        while True:
            try:
                await scheduler_loop()
                return
            except Exception:
                log.exception('ERP task scheduler failed; reconciling after restart')
                await asyncio.sleep(5)

    async with asyncio.TaskGroup() as workers:
        workers.create_task(run_erp())
        await _scheduler_worker_loop(app)


async def _spawn_claimed_automation(app, automation, run_id):
    """Keep the final atomic guard; a lost admission race is not an executed failure."""
    try:
        _agov_maintenance.spawn_automation(
            lambda: execute_scheduled_automation(app, automation, run_id), automation.id, run_id=run_id
        )
    except _agov_maintenance.MaintenanceDenied as exc:
        denied = denial_status(automation.id, exc)
        await AutomationRuns.finish_claim(
            run_id, 'admission_denied',
            error='[' + denied['code'] + '] ' + denied['reason'] + ' Next action: ' + denied['next_action'],
        )
        log.warning('Scheduled automation %s claim %s was not started: %s', automation.id, run_id, denied['code'])


async def _scheduler_worker_loop(app) -> None:
    """Unified background scheduler for all time-based work.

    Handles:
      1. Automation execution  (ENABLE_AUTOMATIONS)
      2. Calendar event alerts (ENABLE_CALENDAR)

    Runs on every instance. Poll interval is configurable via
    SCHEDULER_POLL_INTERVAL env var (default: 10 seconds).
    """
    log.info(
        'Scheduler worker started (timer poll interval: %ss, scheduler poll interval: %ss)',
        TIMER_POLL_INTERVAL,
        SCHEDULER_POLL_INTERVAL,
    )
    from open_webui.utils.erp_dot_resume import start_held_runs
    await start_held_runs(app)
    next_scheduler_poll = 0.0
    last_admission_block = None

    while True:
        try:
            now = time.monotonic()
            # ── Timers ──
            try:
                from open_webui.utils.timers import claim_due_timers, execute_due_timer

                for timer_id, claim_id in await claim_due_timers(int(time.time_ns()), limit=10):
                    asyncio.create_task(execute_due_timer(app, timer_id, claim_id))
            except Exception:
                log.exception('Scheduler: timer error')

            if now < next_scheduler_poll:
                await asyncio.sleep(max(1, TIMER_POLL_INTERVAL))
                continue
            # Jitter to spread automation/calendar load across instances; timers keep a tight poll.
            next_scheduler_poll = now + SCHEDULER_POLL_INTERVAL + random.uniform(0, 2)

            # ── Automations ──
            if await Config.get('automations.enable'):
                try:
                    with _agov_maintenance.automation_batch():
                        now_ns = time.time_ns()
                        await AutomationRuns.expire_claims(now_ns)
                        excluded_ids = blocked_automation_references()
                        async with get_async_db() as db:
                            batch = await Automations.claim_due(now_ns, limit=10, db=db, excluded_ids=excluded_ids)
                        if batch:
                            log.info('Claimed %s due automation(s)', len(batch))
                        for automation, run_id in batch:
                            await _spawn_claimed_automation(app, automation, run_id)
                        last_admission_block = None
                except _agov_maintenance.MaintenanceDenied as exc:
                    # Log a transition once; polling is observational until admission changes.
                    reason = str(exc)
                    if reason != last_admission_block:
                        log.warning('Scheduler waiting for maintenance admission: %s', reason)
                    last_admission_block = reason
                except Exception:
                    log.exception('Scheduler: automation error')

            # ── Calendar Alerts ──
            if await Config.get('calendar.enable'):
                try:
                    await _check_calendar_alerts(app)
                except Exception:
                    log.exception('Scheduler: calendar alert error')

        except Exception:
            log.exception('Scheduler worker error')

        await asyncio.sleep(max(1, TIMER_POLL_INTERVAL))


##########################
# Execute
####################


async def execute_scheduled_automation(app, automation: AutomationModel, run_id: str) -> None:
    if not await AutomationRuns.renew_claim(run_id):
        return

    async def keep_claim_alive():
        while True:
            await asyncio.sleep(SCHEDULED_RUN_LEASE_NS / 1_000_000_000 / 3)
            try:
                if not await AutomationRuns.renew_claim(run_id):
                    return
            except Exception:
                log.exception('Could not renew automation run %s', run_id)

    heartbeat = asyncio.create_task(keep_claim_alive())
    try:
        if (automation.meta or {}).get('erp_continue_from'):
            from open_webui.utils.erp_task_runtime import standard_continuation_current

            try:
                current = await standard_continuation_current(automation, run_id)
            except Exception:
                log.warning('Could not verify ERP continuation %s before dispatch', run_id)
                current = False
            if not current:
                await AutomationRuns.finish_claim(run_id, 'error', error='ERP continuation could not be authorized.')
                return
        await execute_automation(app, automation, scheduled_run_id=run_id)
    finally:
        heartbeat.cancel()
        # A completed record is immutable. Cover cancellation and unexpected
        # exceptions that bypass the executor's normal outcome reporting.
        await AutomationRuns.finish_claim(run_id, 'error', error='Automation run interrupted')


async def _run_scheduled_completion(app, request, form_data, user) -> None:
    chat_id, message_id = form_data['chat_id'], form_data['id']
    request.state.automation_completion_tasks = []
    await app.state.CHAT_COMPLETION_HANDLER(request, form_data, user=user)
    tasks = request.state.automation_completion_tasks
    if not tasks:
        raise RuntimeError('Automation completion was not queued')
    for task in tasks:
        result = await task
        if isinstance(result, dict) and result.get('paused'):
            raise RuntimeError('Automation completion paused before finishing')

    if chat_id.startswith('channel:'):
        from open_webui.models.messages import Messages

        message = await Messages.get_message_by_id(message_id)
        if not message or not (message.meta or {}).get('done'):
            raise RuntimeError('Automation channel response did not finish')
        if error := (message.meta or {}).get('automation_error'):
            raise RuntimeError(error)
    else:
        message = await Chats.get_message_by_id_and_message_id(chat_id, message_id)
        if not message or not message.get('done'):
            raise RuntimeError('Automation chat response did not finish')
        if error := message.get('error'):
            raise RuntimeError(error.get('content', str(error)) if isinstance(error, dict) else str(error))


def _build_request(
    app,
    token: Optional[str] = None,
) -> Request:
    """Build a minimal ASGI Request for chat_completion.

    Mirrors the mock-request pattern used in main.py lifespan
    (model pre-fetch, tool server init) for consistency.

    When token is provided, attach it as
    request.state.token so session-auth tool servers and terminals can
    authenticate headless scheduled runs as the automation owner.
    """
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.0'},
        'method': 'POST',
        'path': '/api/v1/automations/internal',
        'query_string': b'',
        'headers': Headers({}).raw,
        'client': ('127.0.0.1', 0),
        'server': ('127.0.0.1', 80),
        'scheme': 'http',
        'app': app,
    }
    request = Request(scope)
    # Ensure request.state is initialized with required attributes
    request.state.token = HTTPAuthorizationCredentials(scheme='Bearer', credentials=token) if token else None
    request.state.enable_api_keys = False
    return request


def _automation_model_params(automation) -> dict:
    secondary = automation.data.get('fallback_model_id')
    secondary = secondary.strip() if isinstance(secondary, str) else ''
    if secondary == automation.data.get('model_id'):
        secondary = ''
    return {'fallback_model_id': secondary}


async def _resolve_model_defaults(app, model_id: str) -> dict:
    models = getattr(app.state, 'MODELS', {})
    model = models.get(model_id, {})
    meta = model.get('info', {}).get('meta', {})

    defaults = {
        'tool_ids': list(meta.get('toolIds') or []),
        'filter_ids': list(meta.get('defaultFilterIds') or []),
        'terminal_id': meta.get('terminalId'),
    }
    defaults = {key: value for key, value in defaults.items() if value}
    default_feature_ids = meta.get('defaultFeatureIds', [])
    if not default_feature_ids:
        return defaults

    capabilities = meta.get('capabilities') or {}
    features = {}

    # code_interpreter is excluded: it requires the frontend event emitter
    # and does not work in headless backend execution.
    feature_checks = {
        'web_search': await Config.get('web.search.enable'),
        'image_generation': await Config.get('image_generation.enable'),
    }

    for feature_id in default_feature_ids:
        if feature_id in feature_checks:
            # Feature must be: in defaultFeatureIds + capability enabled + admin enabled
            if capabilities.get(feature_id) and feature_checks[feature_id]:
                features[feature_id] = True

    if features:
        defaults['features'] = features
    return defaults


async def _set_terminal_cwd(app, server_id: str, user, cwd: str, chat_id: str) -> None:
    """Set the working directory on a terminal server via the proxy.

    Routes through the open-webui terminal proxy endpoint so that
    auth headers, orchestrator policy routing, and X-User-Id are
    handled correctly — same path the frontend uses.
    """
    import aiohttp
    from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

    connections = getattr(getattr(app, 'state', None), 'config', None)
    if connections is None:
        return
    connections = getattr(connections, 'TERMINAL_SERVER_CONNECTIONS', None) or []
    connection = next((c for c in connections if c.get('id') == server_id), None)
    if connection is None:
        log.warning(f'Terminal server {server_id} not found for CWD set')
        return

    base_url = get_terminal_server_url(connection)
    if not base_url:
        return

    target_url = f'{base_url}/files/cwd'

    headers = {'Content-Type': 'application/json', 'X-User-Id': user.id}
    if chat_id:
        headers['X-Session-Id'] = chat_id

    auth_type = connection.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers['Authorization'] = f'Bearer {connection.get("key", "")}'

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(
                target_url,
                json={'path': cwd},
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(f'Failed to set terminal CWD to {cwd}: HTTP {resp.status} — {body[:200]}')
    except Exception as e:
        log.warning(f'Failed to set terminal CWD: {e}')


async def _execute_channel_automation(
    app,
    automation: AutomationModel,
    user,
    prompt: str,
    model_id: str,
    token: str,
    scheduled_run_id: Optional[str] = None,
) -> None:
    target = automation.data.get('target') or {}
    channel_id = target.get('channel_id')
    if not channel_id or not await Config.get('channels.enable'):
        raise ValueError('Channel not found')

    model = getattr(app.state, 'MODELS', {}).get(model_id, {})
    request = _build_request(app, token=token)

    from open_webui.routers.channels import new_message_handler

    async with get_async_db() as db:
        user_message, channel = await new_message_handler(
            request,
            channel_id,
            MessageForm(
                content=prompt,
                data={},
                meta={'automation_id': automation.id},
            ),
            user,
            db,
        )
        response_parent_id = (
            user_message.parent_id
            if user_message.parent_id
            else (user_message.id if await Config.get('channels.model_response_mode', 'thread') == 'thread' else None)
        )
        assistant_message, channel = await new_message_handler(
            request,
            channel.id,
            MessageForm(
                parent_id=response_parent_id,
                content='',
                data={},
                meta={
                    'automation_id': automation.id,
                    'model_id': model_id,
                    'model_name': model.get('name', model_id),
                },
            ),
            user,
            db,
        )

    form_data = {
        **await _resolve_model_defaults(app, model_id),
        'model': model_id,
        'params': _automation_model_params(automation),
        'messages': [
            {
                'role': 'system',
                'content': f'You are {model.get("name", model_id)}, participating in a channel conversation. Be concise and conversational.',
            },
            {'role': 'user', 'content': f'{user.name if user else "User"}: {prompt}'},
        ],
        'stream': True,
        'chat_id': f'channel:{channel.id}',
        'id': assistant_message.id,
        'session_id': f'channel:{channel.id}',
        'automation_id': automation.id,
        'background_tasks': {},
    }
    if scheduled_run_id:
        await _run_scheduled_completion(app, request, form_data, user)
    else:
        await app.state.CHAT_COMPLETION_HANDLER(request, form_data, user=user)

    from open_webui.socket.main import sio

    await sio.emit(
        'automation:result',
        {
            'automation_id': automation.id,
            'name': automation.name,
            'chat_id': f'channel:{channel.id}',
            'message_id': assistant_message.id,
            'status': 'success',
        },
        room=f'user:{automation.user_id}',
    )

    await _record_run(automation.id, 'success', chat_id=f'channel:{channel.id}', scheduled_run_id=scheduled_run_id)
    await publish_event(
        app,
        EVENTS.AUTOMATION_RUN_COMPLETED,
        actor=user,
        subject_id=automation.id,
        data={'name': automation.name, 'channel_id': channel.id, 'message_id': assistant_message.id},
    )


def _automation_chat_context(chat_data: dict) -> tuple[str, list[dict]]:
    """Return the completed active branch; never append over a running response."""
    history = (chat_data or {}).get('history') or {}
    messages = history.get('messages') or {}
    current_id = history.get('currentId')
    if not current_id or current_id not in messages:
        raise RuntimeError('Previous automation chat has no active message to continue')
    last = messages[current_id]
    if last.get('role') != 'assistant' or last.get('done') is not True:
        raise RuntimeError('Previous automation response is still incomplete; next run will retry')
    branch = []
    seen = set()
    cursor = current_id
    while cursor is not None:
        if cursor in seen or cursor not in messages or len(seen) >= 1000:
            raise RuntimeError('Previous automation chat has invalid message ancestry')
        seen.add(cursor)
        msg = messages[cursor]
        if msg.get('role') in ('user', 'assistant', 'system', 'tool'):
            entry = {'role': msg['role'], 'content': msg.get('content') or ''}
            if msg.get('files'):
                entry['files'] = msg['files']
            branch.append(entry)
        cursor = msg.get('parentId')
    return current_id, list(reversed(branch))


def _with_erp_task_context(automation: AutomationModel, prompt: str) -> str:
    meta = automation.meta or {}
    task_id = meta.get('erp_task')
    if meta.get('erp_standard_agent') is not True or not isinstance(task_id, str) or not task_id:
        return prompt

    from open_webui.utils.erp_task_resolution import ERP

    if meta.get('erp_url') != f'{ERP}/desk/task/{task_id}':
        return prompt

    return (
        f'ERP task system for {task_id}: {ERP}. '
        'ABiT AI handles the agent link and run status through its server-side ERP integration. '
        'Focus on the assigned work. Leave agent linking and run status updates to ABiT AI, '
        'and report blockers only for work the task actually requires. '
        'A missing chat connector for those automatic updates is not a task blocker. '
        f'If the task explicitly requires other ERP actions, use only an authorized connection to {ERP}. '
        'Connectors for other ERP sites do not apply to this task.\n\n' + prompt
    )


@_agov_maintenance.guard_automation
async def execute_automation(
    app, automation: AutomationModel, scheduled_run_id: Optional[str] = None
) -> None:
    """Execute an automation through the full chat completion pipeline.

    Creates a real chat or channel message, then calls chat_completion exactly like the frontend:
    session_id + chat_id + message_id → async task → pipeline handles everything
    (filters, model params, knowledge/RAG, tools, DB saves, webhooks).
    """
    if (automation.meta or {}).get('governance_managed') is True:
        return
    try:
        user = await Users.get_user_by_id(automation.user_id)
        if not user:
            await _agov_maintenance.record_pre_submission('owner_not_found', _record_run, automation.id, 'error', error='User not found', scheduled_run_id=scheduled_run_id)
            await publish_event(
                app,
                EVENTS.AUTOMATION_RUN_FAILED,
                subject_id=automation.id,
                data={'name': automation.name, 'error': 'User not found'},
            )
            return

        # Re-gate the rehydrated owner: a demoted/deactivated or de-permissioned owner must not run.
        from open_webui.utils.access_control import has_permission

        if user.role not in ('user', 'admin') or (
            user.role != 'admin'
            and not await has_permission(user.id, 'features.automations', await Config.get('user.permissions'))
        ):
            error = 'Owner no longer permitted to run automations'
            await _agov_maintenance.record_pre_submission('owner_not_authorized', _record_run, automation.id, 'error', error=error, scheduled_run_id=scheduled_run_id)
            await publish_event(
                app,
                EVENTS.AUTOMATION_RUN_FAILED,
                actor=user,
                subject_id=automation.id,
                data={'name': automation.name, 'error': error},
            )
            return

        # Owner-authorized abitdot is the PRIMARY agent and receives the
        # complete project prompt before the historical ChatGPT relay guard.
        # Other users/agents retain their exact earlier model and ERP logic.
        from open_webui.utils.abitdot_primary import primary_dot_for_owner
        if primary_dot_for_owner(automation.user_id):
            from open_webui.utils.abitdot_primary_runner import run_primary_dot
            if await run_primary_dot(app, automation, user, scheduled_run_id):
                return

        # ABIT.AI remains authoritative for ERP task status and evidence.
        # Do not start another scheduled task while an unresolved abitdot
        # execution for this ChatGPT Relay agent is still being reconciled.
        if automation.data.get('model_id') == 'omniroute.abit-chatgpt-relay/project-management':
            from open_webui.utils.abitdot_failover import owns_automation
            if await owns_automation(automation.user_id, automation.id):
                await _record_run(
                    automation.id, 'deferred',
                    scheduled_run_id=scheduled_run_id,
                )
                return
        prompt = await prompt_template(automation.data['prompt'], user)
        prompt = _with_erp_task_context(automation, prompt)
        model_id = automation.data['model_id']
        try:
            expires_delta = parse_duration(str(await Config.get('automations.auth_token_expires_in', '1h')))
        except ValueError:
            expires_delta = None
        token = create_token(
            data={'id': user.id, 'typ': 'automation'},
            expires_delta=expires_delta or timedelta(hours=1),
        )

        target = automation.data.get('target') or {}
        if target.get('type') == 'channel':
            await _execute_channel_automation(app, automation, user, prompt, model_id, token, scheduled_run_id)
            return

        folder_id = automation.folder_id
        if folder_id and not await Folders.get_folder_by_id_and_user_id(folder_id, automation.user_id):
            await Automations.clear_folder_ids(automation.user_id, [folder_id])
            folder_id = None

        # Generate proper UUIDs for messages (same as frontend)
        user_msg_id = str(uuid4())
        # ERP activity sync reads the answer for this exact run, even in a reused chat.
        erp_agent = (automation.meta or {}).get('erp_standard_agent') is True
        assistant_msg_id = scheduled_run_id if scheduled_run_id and erp_agent else str(uuid4())

        parent_message_id = None
        previous_messages = []
        chat = None
        if automation.data.get('continue_previous_chat', True) is True:
            for run in await AutomationRuns.get_by_automation(automation.id, limit=50):
                if run.status != 'success' or not run.chat_id or run.chat_id.startswith('channel:'):
                    continue
                candidate = await Chats.get_chat_by_id_and_user_id(run.chat_id, automation.user_id)
                if not candidate:
                    continue
                candidate_meta = (candidate.chat or {}).get('meta') or {}
                if candidate_meta.get('automation_id') != automation.id or candidate_meta.get('agent_handoff'):
                    continue
                if model_id == 'omniroute.abit-chatgpt-relay/project-management':
                    from open_webui.routers.chatgpt_relay import (
                        _has_unfinished_job, _latest_unfinished_job, _prefer_abitdot,
                    )
                    if _has_unfinished_job(automation.user_id, candidate.id):
                        previous = _latest_unfinished_job(automation.user_id, candidate.id)
                        if previous and previous['status'] == 'unknown':
                            from open_webui.utils.abitdot_failover import build_saved_selected_context
                            fallback_context = await build_saved_selected_context(
                                app, user, automation, candidate, previous, token,
                            )
                            # Reconcile the original turn and its side effects
                            # without creating a new ERP task or user prompt.
                            await _prefer_abitdot(
                                previous, previous['account'], fallback_context,
                            )
                        await _record_run(
                            automation.id, 'deferred', chat_id=candidate.id,
                            scheduled_run_id=scheduled_run_id,
                        )
                        return
                parent_message_id, previous_messages = _automation_chat_context(candidate.chat)
                chat = candidate
                break
        if chat is not None:
            updated = await Chats.update_chat_by_id(
                chat.id,
                {
                    'models': [model_id],
                    'params': _automation_model_params(automation),
                    'history': {
                        'currentId': assistant_msg_id,
                        'messages': {
                            user_msg_id: {
                                'id': user_msg_id,
                                'parentId': parent_message_id,
                                'role': 'user',
                                'content': prompt,
                                'childrenIds': [assistant_msg_id],
                                'timestamp': int(time.time()),
                                'models': [model_id],
                            },
                            assistant_msg_id: {
                                'id': assistant_msg_id,
                                'parentId': user_msg_id,
                                'role': 'assistant',
                                'content': '',
                                'done': False,
                                'model': model_id,
                                'childrenIds': [],
                                'timestamp': int(time.time()),
                            },
                        },
                    },
                },
            )
            if not updated:
                raise RuntimeError('Failed to append automation run to previous chat')
            chat = updated
        else:
            chat_id = str(uuid4())
            chat = await Chats.insert_new_chat(
                chat_id,
                automation.user_id,
                ChatForm(
                    folder_id=folder_id,
                    chat={
                        'title': automation.name,
                        'models': [model_id],
                        'params': _automation_model_params(automation),
                        'history': {
                            'currentId': assistant_msg_id,
                            'messages': {
                                user_msg_id: {
                                    'id': user_msg_id,
                                    'parentId': None,
                                    'role': 'user',
                                    'content': prompt,
                                    'childrenIds': [assistant_msg_id],
                                    'timestamp': int(time.time()),
                                    'models': [model_id],
                                },
                                assistant_msg_id: {
                                    'id': assistant_msg_id,
                                    'parentId': user_msg_id,
                                    'role': 'assistant',
                                    'content': '',
                                    'done': False,
                                    'model': model_id,
                                    'childrenIds': [],
                                    'timestamp': int(time.time()),
                                },
                            },
                        },
                        'messages': [
                            {'role': 'user', 'content': prompt},
                        ],
                        'meta': {'automation_id': automation.id},
                    },
                ),
            )

        if not chat:
            error = 'Failed to create chat'
            await _agov_maintenance.record_pre_submission('chat_not_created', _record_run, automation.id, 'error', error=error, scheduled_run_id=scheduled_run_id)
            await publish_event(
                app,
                EVENTS.AUTOMATION_RUN_FAILED,
                actor=user,
                subject_id=automation.id,
                data={'name': automation.name, 'error': error},
            )
            return

        # Notify frontend to refresh chat list
        from open_webui.socket.main import sio

        await sio.emit(
            'events',
            {
                'chat_id': chat.id,
                'message_id': user_msg_id,
                'data': {'type': 'chat:list'},
            },
            room=f'user:{automation.user_id}',
        )

        # Build the same payload the frontend sends to /api/chat/completions
        form_data = {
            **await _resolve_model_defaults(app, model_id),
            'model': model_id,
            'params': _automation_model_params(automation),
            'messages': previous_messages + [{'role': 'user', 'content': prompt}],
            'stream': True,
            'chat_id': chat.id,
            'id': assistant_msg_id,
            'parent_id': parent_message_id,
            'user_message': {
                'id': user_msg_id,
                'parentId': parent_message_id,
                'role': 'user',
                'content': prompt,
            },
            'session_id': f'automation:{automation.id}',
            'automation_id': automation.id,
            'background_tasks': {},
        }
        # Call the full chat completion pipeline (same as POST /api/chat/completions).
        # The handler reference is stored on app.state to avoid circular imports.
        request = _build_request(app, token=token)
        if scheduled_run_id:
            await _run_scheduled_completion(app, request, form_data, user)
        else:
            await app.state.CHAT_COMPLETION_HANDLER(request, form_data, user=user)

        # Notify user
        from open_webui.socket.main import sio

        await sio.emit(
            'automation:result',
            {
                'automation_id': automation.id,
                'name': automation.name,
                'chat_id': chat.id,
                'status': 'success',
            },
            room=f'user:{automation.user_id}',
        )

        await _record_run(automation.id, 'success', chat_id=chat.id, scheduled_run_id=scheduled_run_id)
        await publish_event(
            app,
            EVENTS.AUTOMATION_RUN_COMPLETED,
            actor=user,
            subject_id=automation.id,
            data={'name': automation.name, 'chat_id': chat.id},
        )

    except Exception as e:
        log.exception(f'Automation {automation.id} failed')
        error = str(e)[:4000]
        await _record_run(automation.id, 'error', error=error, scheduled_run_id=scheduled_run_id)
        await publish_event(
            app,
            EVENTS.AUTOMATION_RUN_FAILED,
            subject_id=automation.id,
            data={'name': automation.name, 'error': error},
        )


####################
# Internals
####################


async def _check_calendar_alerts(app) -> None:
    """Check for upcoming calendar events and send alert notifications.

    De-duplication is DB-backed via meta.alerted_at — survives restarts
    and works across multiple instances.
    """
    from open_webui.models.calendar import CalendarEvents, CalendarEventUpdateForm
    from open_webui.socket.main import sio

    now_ns = int(time.time_ns())
    default_lookahead_ns = CALENDAR_ALERT_LOOKAHEAD_MINUTES * 60 * 1_000_000_000
    # Grace window covers one poll cycle + jitter so "At time of event"
    # alerts (alert_minutes=0) are not missed.
    grace_ns = (SCHEDULER_POLL_INTERVAL + 5) * 1_000_000_000

    async with get_async_db() as db:
        upcoming = await CalendarEvents.get_upcoming_events(now_ns, default_lookahead_ns, grace_ns=grace_ns, db=db)

    if not upcoming:
        return

    for event, user_tz in upcoming:
        # Skip if already alerted for this start time
        if event.meta and event.meta.get('alerted_at'):
            continue

        # Compute minutes until event starts
        minutes_until = max(0, int((event.start_at - now_ns) / (60 * 1_000_000_000)))

        alert_data = {
            'event_id': event.id,
            'title': event.title,
            'description': event.description or '',
            'start_at': event.start_at,
            'minutes_until': minutes_until,
            'calendar_id': event.calendar_id,
            'location': event.location or '',
        }

        await sio.emit(
            'events',
            {
                'data': {
                    'type': 'calendar:alert',
                    'data': alert_data,
                },
            },
            room=f'user:{event.user_id}',
        )

        # Mark as alerted in DB so it survives restarts / multi-instance
        try:
            await CalendarEvents.update_event_by_id(
                event.id,
                CalendarEventUpdateForm(meta={'alerted_at': now_ns}),
            )
        except Exception:
            log.debug('Failed to mark event %s as alerted', event.id, exc_info=True)

        # Send target notification if user has one configured
        try:
            time_str = f'in {minutes_until} min' if minutes_until > 0 else 'now'
            await publish_event(
                app,
                EVENTS.CALENDAR_ALERT,
                subject_id=event.id,
                subject_type='calendar.event',
                source='scheduler',
                data={
                    **alert_data,
                    'user_id': event.user_id,
                    'starts_in': time_str,
                    'message': f'{event.title}: starting {time_str}',
                },
                message=event.title,
            )
        except Exception:
            log.debug('Failed to send notification for calendar alert %s', event.id, exc_info=True)


@_agov_maintenance.record_automation_receipt
async def _record_run(
    automation_id: str,
    status: str,
    chat_id: str = None,
    error: str = None,
    scheduled_run_id: Optional[str] = None,
):
    if scheduled_run_id:
        await AutomationRuns.finish_claim(scheduled_run_id, status, chat_id=chat_id, error=error)
    else:
        async with get_async_db() as db:
            await AutomationRuns.insert(automation_id, status, chat_id=chat_id, error=error, db=db)

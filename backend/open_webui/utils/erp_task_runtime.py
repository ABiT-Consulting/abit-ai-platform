"""Adapters for the ERP scheduler, saved project agents, and exact model endpoints."""
import asyncio
import base64
from datetime import datetime, timezone
import hmac
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import func, select

from open_webui.env import DATA_DIR
from open_webui.internal.db import get_async_db_context
from open_webui.models.automations import Automation, AutomationRun, Automations
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.folders import Folders
from open_webui.models.users import Users
from open_webui.routers import chatgpt_relay
from open_webui.utils.erp_task_resolution import (
    AGENT_URL, DETAIL, ERP, EXECUTION_ID, HOST, INTAKE, PRIMARY, RELAY_STATUS, SECONDARY, TERMINAL, USER,
    Blocked, ERPClient, Resolver, Store, agent_url, audit_run_id, audit_status, task_revision,
)
from open_webui.utils.misc import get_output_text

log = logging.getLogger(__name__)
CONFIG = 'ABIT_ERP_TASK_RESOLUTION_CONFIG'


def normalized_repo(value):
    parsed = urlparse(value or '')
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?', parsed.path)):
        raise Blocked('The ERP project requires an explicit GitHub repository URL')
    return parsed.path.strip('/').removesuffix('.git').lower()


class ProjectAgents:
    def __init__(self, owner):
        self.owner = owner

    async def verify_project(self, project, scope):
        user = await Users.get_user_by_email(self.owner)
        if not user or user.role != 'admin' or user.id != scope['owner_id']:
            raise Blocked('The configured abit.ai agent owner is unavailable or unauthorized')
        folder_id = scope.get('folder_id')
        if folder_id and not await Folders.get_folder_by_id_and_user_id(folder_id, user.id):
            raise Blocked('The mapped abit.ai project is unavailable to the agent owner')
        expected = scope.get('chatgpt_project')
        account = scope.get('account')
        if scope.get('workspace_source') in {'task', 'standalone'} and not expected and not account:
            return
        if not re.fullmatch(r'g-p-[a-f0-9]{32}', expected or '') or not chatgpt_relay.ACCOUNT_ID.fullmatch(account or ''):
            raise Blocked('The mapped ChatGPT project or account is invalid')
        project_url = urlparse(project.get('custom_chatgpt_chat_url') or '')
        linked_project = re.match(r'^/g/(g-p-[a-f0-9]{32})(?:[-/]|$)', project_url.path)
        if (project_url.scheme != 'https' or project_url.netloc != 'chatgpt.com'
                or not linked_project or linked_project[1] != expected):
            raise Blocked('ERP ChatGPT project link does not match the approved mapping')
        with chatgpt_relay._db() as db:
            linked = db.execute(
                'SELECT 1 FROM projects WHERE owner=? AND folder_id=? AND account=? AND project_id=?',
                (user.id, scope['folder_id'], scope['account'], scope['chatgpt_project']),
            ).fetchone()
        if not linked:
            raise Blocked('The abit.ai project has no verified ChatGPT project binding')

    async def verify_scope(self, project, scope):
        if project.get('custom_ai_scheduler_enabled'):
            raise Blocked('ERP legacy scheduling is enabled for this project; exclusive ownership is required')
        if scope.get('workspace_source') == 'standalone':
            return
        if scope.get('workspace_source') == 'task':
            normalized_repo(scope['repository'])
            return
        if project.get('custom_ai_agent_environment') != HOST:
            raise Blocked('ERP project environment is not explicitly configured as ABIT-DEV-LINUX')
        if not all(scope.get(k) for k in ('repository', 'workspace', 'branch')):
            raise Blocked('No approved ABIT-DEV-LINUX repository, workspace and branch mapping')
        if normalized_repo(project.get('custom_github_repository')) != normalized_repo(scope['repository']):
            raise Blocked('ERP repository does not match the approved Linux workspace mapping')
        if project.get('custom_ai_agent_development_folder') != scope['workspace']:
            raise Blocked('ERP workspace does not match the approved ABIT-DEV-LINUX workspace')
        if project.get('custom_ai_agent_working_branch') != scope['branch']:
            raise Blocked('ERP branch does not match the approved development branch')

    async def instructions(self, scope):
        if not scope.get('folder_id'):
            return ''
        folder = await Folders.get_folder_by_id_and_user_id(scope['folder_id'], scope['owner_id'])
        return str((folder.data or {}).get('system_prompt') or '') if folder else ''

    async def destination(self, run, task):
        scope = run['scope']
        if not scope.get('account') or not scope.get('chatgpt_project'):
            return None
        name = str(task.get('custom_chatgpt_project') or '').strip()
        project_id = str(task.get('custom_chatgpt_project_id') or '').strip()
        with chatgpt_relay._db() as db:
            if name:
                rows = db.execute(
                    'SELECT DISTINCT project_id, name, url FROM projects WHERE owner=? AND account=? AND name=?',
                    (scope['owner_id'], scope['account'], name),
                ).fetchall()
                if project_id:
                    rows = [row for row in rows if row[0] == project_id]
                error = 'The ERP task ChatGPT Project does not match one synced project for this account'
            else:
                rows = db.execute(
                    'SELECT DISTINCT project_id, name, url FROM projects '
                    'WHERE owner=? AND account=? AND folder_id=? AND project_id=?',
                    (scope['owner_id'], scope['account'], scope['folder_id'], scope['chatgpt_project']),
                ).fetchall()
                if project_id and project_id != scope['chatgpt_project']:
                    raise Blocked('The ERP task ChatGPT Project ID has no matching selection')
                error = 'The ERP parent project has no verified ChatGPT destination'
        if len(rows) != 1 or not re.fullmatch(r'g-p-[a-f0-9]{32}', rows[0][0]):
            raise Blocked(error)
        selected_id, selected_name, url = rows[0]
        return {'project_id': selected_id, 'name': selected_name, 'url': url}

    async def check_standard_adoption(self, db, agent, scope, task_id):
        if (not agent or agent.user_id != scope['owner_id']
                or (agent.meta or {}).get('erp_standard_agent') is not True
                or agent.meta.get('erp_task') != task_id or agent.next_run_at is not None):
            raise Blocked('The linked standard agent must finish its scheduled run before switching to development')
        if await db.scalar(select(AutomationRun.id).where(
                AutomationRun.automation_id == agent.id, AutomationRun.status == 'running').limit(1)):
            raise Blocked('The linked standard agent is still running')

    async def ensure_agent(self, run, task):
        scope = run['scope']
        erp_url = ERP + '/desk/task/' + run['task']
        subject = str(task.get('subject') or '')
        description = str(task.get('description') or '')
        name = f"{run['task']} · {subject[:120]}"
        prompt = f"Managed ERP task {run['task']}\n\n{subject[:240]}\n\n{description[:16000]}"
        data = {'prompt': prompt, 'model_id': PRIMARY, 'fallback_model_id': SECONDARY,
                'rrule': 'DTSTART:20260101T000000Z\nRRULE:FREQ=DAILY;COUNT=1'}
        async with get_async_db_context() as db:
            agent = await db.get(Automation, run['agent'])
            if run.get('standard_agent') and (not agent or (agent.meta or {}).get('erp_managed') is not True):
                await self.check_standard_adoption(db, agent, scope, run['task'])
                agent.is_active = False
                agent.folder_id = scope.get('folder_id')
                agent.meta = {**agent.meta, 'erp_managed': True}
            if run.get('existing_agent'):
                if (not agent or agent.user_id != scope['owner_id'] or agent.folder_id != scope['folder_id']
                        or (agent.meta or {}).get('erp_task') != run['task']
                        or agent.meta.get('erp_url') != erp_url or agent.is_active
                        or agent.next_run_at is not None or agent.attempt_limit != 3
                        or agent.data.get('model_id') != PRIMARY or agent.data.get('fallback_model_id') != SECONDARY):
                    raise Blocked('The approved existing agent does not match this ERP task and project')
                if agent.meta.get('erp_managed') is not True:
                    prior_run = await db.scalar(select(AutomationRun.id)
                                                .where(AutomationRun.automation_id == agent.id).limit(1))
                    if prior_run or agent.attempts_used or agent.last_run_at is not None:
                        raise Blocked('The existing agent already has execution history')
                    agent.meta = {**agent.meta, 'erp_managed': True}
            if not agent:
                now = time.time_ns()
                db.add(Automation(
                    id=run['agent'], name=name,
                    user_id=scope['owner_id'], folder_id=scope['folder_id'], is_active=False,
                    data=data, meta={'erp_task': run['task'], 'erp_url': erp_url, 'erp_managed': True},
                    next_run_at=None, attempt_limit=3, created_at=now, updated_at=now,
                ))
            else:
                if (agent.user_id != scope['owner_id'] or agent.folder_id != scope['folder_id']
                        or (agent.meta or {}).get('erp_task') != run['task']
                        or agent.meta.get('erp_url') != erp_url or agent.is_active or agent.next_run_at is not None):
                    raise Blocked('Saved agent project or ownership does not match this ERP task')
                if agent.name != name or any(agent.data.get(k) != v for k, v in data.items()):
                    agent.name = name
                    agent.data = {**agent.data, **data}
                    agent.updated_at = time.time_ns()
            await db.commit()
        persisted = await Automations.get_by_id(run['agent'])
        if (not persisted or persisted.name != name
                or persisted.user_id != scope['owner_id'] or persisted.folder_id != scope['folder_id']
                or (persisted.meta or {}).get('erp_task') != run['task'] or persisted.is_active
                or (persisted.meta or {}).get('erp_url') != erp_url
                or (run.get('existing_agent') and persisted.meta.get('erp_managed') is not True)
                or persisted.next_run_at is not None or any(persisted.data.get(k) != v for k, v in data.items())):
            raise Blocked('Saved agent model, project or ownership configuration failed readback')

    async def attempt_chat(self, run, attempt):
        chat_id = str(uuid5(NAMESPACE_URL, 'https://abit.ai/erp-attempt/' + attempt['id']))
        user_id = str(uuid5(NAMESPACE_URL, chat_id + '/user'))
        reply_id = str(uuid5(NAMESPACE_URL, chat_id + '/assistant'))
        owner = run['scope']['owner_id']
        title = f"{run['task']} · Cycle {run['cycle']} · Attempt {attempt['number']}"
        request = attempt.get('request') or {}
        evidence = attempt.get('evidence') or {}
        progress = attempt.get('stop_reason') or (
            'Verified changes ready for review' if attempt['phase'] == 'verified'
            else 'Attempt finished' if attempt.get('finished') else run['progress']
        )
        response = [progress]
        tester = evidence.get('tester')
        if tester:
            checks = '\n'.join(f'- `{command}`' for command in tester.get('checks', []))
            response.append('**Independent testing sub-agent**\n\n' + tester['summary'] + ('\n\n' + checks if checks else ''))
        for label, output in (
            ('Codex CLI output', evidence.get('findings')),
            ('ChatGPT Browser Relay', attempt.get('findings')),
            ('Verification output', evidence.get('verification_output')),
            ('Checkout diagnostic', evidence.get('checkout_diagnostic')),
        ):
            if output:
                response.append(f'**{label}**\n\n{output}')
        reply = {'id': reply_id, 'parentId': user_id, 'childrenIds': [], 'role': 'assistant',
                 'content': '\n\n'.join(response), 'model': PRIMARY, 'done': bool(attempt.get('finished')),
                 'timestamp': int(attempt.get('finished') or attempt['started'])}
        existing = await Chats.get_chat_by_id(chat_id)
        if existing:
            if existing.user_id != owner or (existing.meta or {}).get('erp_attempt') != attempt['id']:
                raise Blocked('The saved ERP attempt chat does not belong to this agent')
            messages = (existing.chat.get('history') or {}).get('messages', {})
            current = messages.get(reply_id) or {}
            if current.get('content') != reply['content'] or current.get('done') != reply['done']:
                if not await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, reply_id, reply, touch=False):
                    raise Blocked('Could not save ERP attempt chat progress')
            return chat_id
        prompt = '\n\n'.join(part for part in (
            f"[{run['task']}]({ERP}/desk/task/{run['task']})",
            request.get('subject'), request.get('instructions'), request.get('description'),
        ) if part)
        message = {'id': user_id, 'parentId': None, 'childrenIds': [reply_id], 'role': 'user',
                   'content': prompt, 'timestamp': int(attempt['started']), 'models': [PRIMARY]}
        chat = await Chats.insert_new_chat(chat_id, owner, ChatForm(
            folder_id=run['scope']['folder_id'],
            chat={'title': title, 'models': [PRIMARY],
                  'history': {'currentId': reply_id, 'messages': {user_id: message, reply_id: reply}}},
        ), internal_meta={'erp_attempt': attempt['id'], 'automation_id': run['agent']})
        if not chat:
            raise Blocked('Could not create the ERP attempt chat')
        return chat_id

    async def progress(self, run):
        previous = []
        for archived in Store(runtime_state()).history(run['task']):
            for attempt in archived['attempts']:
                previous.append({
                    **{key: attempt[key] for key in ('id', 'number', 'started', 'phase', 'finished', 'stop_reason') if key in attempt},
                    'cycle': archived['cycle'],
                    'chat_id': await self.attempt_chat(archived, attempt),
                })
        attempts = [
            {**{key: value for key, value in attempt.items() if key not in {'request', 'announced'}},
             'cycle': run['cycle'], 'chat_id': await self.attempt_chat(run, attempt)}
            for attempt in run['attempts']
        ]
        async with get_async_db_context() as db:
            agent = await db.get(Automation, run['agent'])
            if not agent:
                return
            meta = agent.meta or {}
            if run.get('existing_agent') and meta.get('erp_managed') is not True:
                return
            if (meta.get('erp_task') != run['task'] or agent.user_id != run['scope']['owner_id']
                    or agent.folder_id != run['scope']['folder_id']):
                raise Blocked('The agent is no longer owned by this ERP task')
            current = attempts[-1] if attempts else {}
            agent.attempts_used = len(attempts)
            agent.meta = {**agent.meta, 'erp_run': {
                'status': run['status'], 'progress': run['progress'], 'host': HOST, 'user': USER,
                'primary': 'ChatGPT Browser Relay', 'secondary': 'Codex CLI', 'cycle': run['cycle'],
                'destination': run.get('destination'),
                'attempt': current.get('number', 0), 'started': current.get('started'),
                'deadline': current.get('deadline'), 'finished': current.get('finished'),
                'attempts': attempts, 'previous_attempts': previous,
            }}
            await db.commit()


class Worker:
    def __init__(self, kind):
        self.kind = kind

    def connection(self):
        if self.kind == 'relay':
            try:
                token, _ = chatgpt_relay._credentials(chatgpt_relay.MESSAGE_TOKEN_FILE)
            except HTTPException as exc:
                raise Blocked('ChatGPT Browser Relay connection is unavailable') from exc
            return chatgpt_relay.MESSAGE_URL, token
        try:
            from open_webui.utils.codex_cli import settings
        except ImportError as exc:
            raise Blocked('The deployed Codex CLI model integration is unavailable') from exc
        config = settings().get(SECONDARY)
        if not config:
            raise Blocked('Codex CLI on ABIT-DEV-LINUX is not configured')
        return config['url'], Path(config['token_file']).read_text().strip()

    async def request(self, operation, payload):
        url, token = self.connection()
        async with httpx.AsyncClient(base_url=url, timeout=8, follow_redirects=False, trust_env=False,
                                     headers={'Authorization': 'Bearer ' + token}) as client:
            result = await client.post('/erp-task/' + operation, json=payload)
            result.raise_for_status()
            return result.json()

    def identity(self, run, destination=None):
        scope = run['scope']
        project_id = (destination or {}).get('project_id') or scope.get('chatgpt_project')
        payload = {'owner': scope['owner_id'], 'project': run['project'], 'account': scope.get('account'),
                   'chatgpt_project': project_id, 'repository': scope.get('repository'),
                   'workspace': scope.get('workspace'), 'branch': scope.get('branch'), 'agent': run['agent']}
        if self.kind == 'cli' and scope.get('workspace_source'):
            payload.update(workspace_source=scope['workspace_source'], task=run['task'],
                           development=run.get('development', False))
        return payload

    async def preflight(self, run):
        if self.kind == 'relay':
            user = await Users.get_user_by_id(run['scope']['owner_id'])
            if not user or not await chatgpt_relay.Models.get_model_by_id(PRIMARY):
                raise Blocked('ChatGPT Browser Relay model is unavailable for this project')
        try:
            payload = {**self.identity(run, run.get('destination')), 'task': run['task']}
            if self.kind == 'cli':
                payload['development'] = run.get('development', False)
            result = await self.request('preflight', payload)
        except (httpx.HTTPError, OSError) as exc:
            raise Blocked(f'{self.kind} worker capability or connectivity check failed') from exc
        if not result.get('ready'):
            raise Blocked(result.get('error') or f'{self.kind} worker is unavailable')
        if self.kind == 'cli' and (result.get('host'), result.get('user'), result.get('model')) != (HOST, USER, SECONDARY):
            raise Blocked('Codex CLI worker host, runtime identity or model failed verification')

    def attempt(self, run, attempt):
        return {**self.identity(run, attempt.get('destination')), 'id': attempt['id'], 'deadline': attempt['deadline']}

    async def observe_or_start(self, run, attempt, task):
        payload = self.attempt(run, attempt)
        # Immutable request per attempt. An ambiguous response can be retried
        # against the worker's durable request ID without resubmitting the turn.
        snapshot = attempt.get('request') or {}
        if not snapshot:
            raise RuntimeError('Attempt request has not been recorded')
        payload.update(snapshot)
        if self.kind == 'cli':
            payload.update(development=run.get('development', False),
                           findings=attempt.get('findings', ''), previous=[
                {k: v for k, v in row.items() if k not in {'request', 'announced'}} for row in run['attempts'][:-1]
            ])
        try:
            return await self.request('observe', payload)
        except (httpx.HTTPError, OSError, Blocked):
            return {'status': 'uncertain', 'progress': f'Waiting for {self.kind} worker connectivity; observing the original request'}

    async def stop(self, run, attempt):
        return await self.request('stop', self.attempt(run, attempt))


def runtime_state():
    return Path(DATA_DIR) / 'erp-task-resolution.sqlite'


async def receive_assignment(request: Request):
    config_path = os.environ.get(CONFIG)
    if not config_path:
        raise HTTPException(404, 'ERP assignment intake is disabled')
    config = json.loads(Path(config_path).read_text())
    if not config.get('webhook_secret_file'):
        raise HTTPException(404, 'ERP assignment intake is disabled')
    secret = Path(config['webhook_secret_file']).read_bytes().strip()
    if len(secret) < 32:
        raise HTTPException(503, 'ERP webhook security is unavailable')
    signature = request.headers.get('X-Frappe-Webhook-Signature', '')
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 4096:
            raise HTTPException(413, 'ERP event exceeds the allowed size')
    expected = base64.b64encode(hmac.digest(secret, data, 'sha256')).decode('ascii')
    if not signature.isascii() or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, 'Invalid ERP webhook signature')
    try:
        payload = json.loads(data)
        task = payload['task_id']
        if not isinstance(task, str) or not re.fullmatch(r'TASK-[A-Za-z0-9-]{1,64}', task):
            raise ValueError('Invalid task')
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, 'ERP event requires a valid task_id') from None
    event_id = erp_revision = None
    if 'event_id' in payload or 'erp_revision' in payload:
        event_id = payload.get('event_id')
        revision = payload.get('erp_revision')
        if (not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}', event_id)
                or not isinstance(revision, str)
                or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?', revision)):
            raise HTTPException(422, 'ERP event requires a valid event_id and erp_revision')
        try:
            erp_revision = datetime.fromisoformat(revision).isoformat(sep=' ', timespec='microseconds')
        except ValueError:
            raise HTTPException(422, 'ERP event revision must be a valid Frappe modified timestamp') from None
    if config.get('only_tasks') is not None and task not in config['only_tasks']:
        return {'accepted': False}
    store = Store(runtime_state())
    try:
        queued = store.enqueue(task, event_id=event_id, erp_revision=erp_revision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    run = store.get(task)
    if run:
        return {'accepted': True, 'queued': queued, 'status': run['status'],
                'agent_url': agent_url(run['agent']) if run.get('linked') else None}
    return {'accepted': True, 'queued': queued, 'status': 'Queued' if queued else 'Already received'}


async def standard_folder(project, owner_id):
    url = urlparse(project.get('custom_chatgpt_chat_url') or '')
    match = re.match(r'^/g/(g-p-[a-f0-9]{32})(?:[-/]|$)', url.path)
    if url.scheme != 'https' or url.netloc != 'chatgpt.com' or not match:
        return None
    with chatgpt_relay._db() as db:
        rows = db.execute(
            'SELECT DISTINCT folder_id FROM projects WHERE owner=? AND project_id=? AND folder_id IS NOT NULL',
            (owner_id, match[1]),
        ).fetchall()
    if len(rows) != 1 or not await Folders.get_folder_by_id_and_user_id(rows[0][0], owner_id):
        return None
    return rows[0][0]


def standard_agent_id(task_id):
    return str(uuid5(NAMESPACE_URL, f'{ERP}/standard-agent/{task_id}'))


async def create_standard_agent(resolver, task_id, task=None, *, legacy_run=None):
    if task is None:
        task = await resolver.erp.get('Task', task_id)
    agent_id = legacy_run['agent'] if legacy_run else standard_agent_id(task_id)
    url = agent_url(agent_id)
    accepted_statuses = INTAKE | TERMINAL if legacy_run else INTAKE
    if (task['status'] not in accepted_statuses or task.get('is_group') or task.get('is_template')
            or task.get(EXECUTION_ID) not in (None, '', agent_id if legacy_run else None)):
        return
    if legacy_run and (not legacy_run.get('synced') or not legacy_run.get('linked')
                       or task.get(AGENT_URL) != url or task.get(EXECUTION_ID) != agent_id):
        return
    if not await resolver.authorized(task):
        return
    user = await Users.get_user_by_email(resolver.app.owner)
    if not user or user.role != 'admin':
        raise Blocked('The configured ABiT AI agent owner is unavailable')
    if task.get(AGENT_URL) not in (None, '', url):
        return
    task_link = f'{ERP}/desk/task/{task_id}'
    async with get_async_db_context() as db:
        agent = await db.get(Automation, agent_id)
        if agent is None and task.get(AGENT_URL) == url:
            return  # A deliberately deleted agent stays deleted.
        if agent is not None:
            meta = agent.meta or {}
            if agent.user_id != user.id or meta.get('erp_task') != task_id or meta.get('erp_url') != task_link:
                raise Blocked('The standard agent does not match the ERP task and owner')
            if legacy_run and meta.get('erp_managed') is True:
                running = await db.scalar(select(AutomationRun.id).where(
                    AutomationRun.automation_id == agent_id, AutomationRun.status == 'running').limit(1))
                if running or agent.is_active or agent.next_run_at is not None:
                    raise Blocked('The existing ERP agent must be idle before switching schedulers')
                meta = dict(meta)
                meta.update(erp_managed=False, erp_standard_agent=True, erp_standard_armed=False)
                meta['erp_legacy_run'] = meta.pop('erp_run', {})
                agent.meta = meta
                await db.commit()
            elif meta.get('erp_standard_agent') is not True or meta.get('erp_managed') is True:
                raise Blocked('The agent is unavailable to the Governance scheduler')
        if agent is None:
            now = time.time_ns()
            db.add(Automation(
                id=agent_id, user_id=user.id, name=task_id, is_active=False, next_run_at=None,
                data={'prompt': task_id, 'model_id': PRIMARY, 'fallback_model_id': SECONDARY,
                      'rrule': 'DTSTART:20260101T000000Z\nRRULE:FREQ=DAILY;COUNT=1'},
                attempt_limit=1, created_at=now, updated_at=now,
                meta={'erp_task': task_id, 'erp_url': task_link, 'erp_standard_agent': True},
            ))
            await db.commit()

    if task['status'] not in INTAKE:
        return
    if task.get(AGENT_URL) != url:
        await resolver.erp.save(task, {AGENT_URL: url})
        task = await resolver.erp.get('Task', task_id)
    if (task['status'] not in INTAKE or task.get(AGENT_URL) != url
            or task.get(EXECUTION_ID) not in (None, '', agent_id if legacy_run else None)
            or not await resolver.authorized(task)):
        return

    async with get_async_db_context() as db:
        agent = await db.get(Automation, agent_id)
        if (not agent or agent.user_id != user.id
                or (agent.meta or {}).get('erp_standard_agent') is not True or agent.meta.get('erp_managed')):
            return
        latest = await db.scalar(select(AutomationRun).where(AutomationRun.automation_id == agent_id)
                                 .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc()).limit(1))
        armed = agent.meta.get('erp_standard_armed')
        if armed and agent.next_run_at is not None:
            if agent.meta.get('erp_task_revision') not in (None, task_revision(task)):
                raise Blocked('Waiting for the scheduled run to release the changed task')
            return
        if armed and latest and latest.status == 'running':
            raise Blocked('Waiting for the previous standard run to finish')
        if armed and not latest:
            return
        revision = task.get('modified')
        if armed and revision == agent.meta.get('erp_open_revision'):
            return
        if armed:
            synced = agent.meta.get('erp_status_sync') or {}
            if synced.get('run_id') != latest.id or synced.get('status') != latest.status:
                # Reconciliation writes any outstanding terminal state first.
                raise Blocked('Waiting for the previous standard run status to reconcile')
            if synced.get('erp_revision') and revision == synced['erp_revision']:
                return

        try:
            project = await resolver.erp.get('Project', task['project']) if task.get('project') else {}
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (403, 404):
                raise
            project = {}
        folder_id = await standard_folder(project, user.id)
        subject = str(task.get('subject') or '')
        description = str(task.get('description') or '')[:16000]
        project_name = str(project.get('project_name') or task.get('project') or '')[:240]
        project_instructions = str(project.get('custom_ai_developer_instructions') or '')[:12000]
        context = '\n\n'.join(part for part in (
            f'ERP task {task_id}\n{task_link}', subject[:240], description,
            f'ERP project: {project_name}' if project_name else '', project_instructions,
        ) if part)
        prompt = ('Execute the assigned ERP task within the available context and tools. '
                  'Report the actual work, verification, and any blockers.\n\n' + context)
        development = resolver.development_task(task)
        if development:
            prompt = ('Implement the requested change using the authorized tools and context. '
                      'Run the relevant checks and report their results, the work performed, '
                      'and any remaining blockers.\n\n' + context)
        scheduled = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        agent.name = f'{task_id} · {subject[:120]}'
        agent.folder_id = folder_id
        agent.data = {**agent.data, 'prompt': prompt, 'model_id': PRIMARY, 'fallback_model_id': SECONDARY,
                      'rrule': f'DTSTART:{scheduled}\nRRULE:FREQ=DAILY;COUNT=1'}
        agent.meta = {**agent.meta, 'erp_standard_armed': True,
                      'erp_generation': (agent.meta.get('erp_generation') or (1 if armed else 0)) + 1,
                      'erp_open_revision': revision, 'erp_task_revision': task_revision(task),
                      'erp_ignore_run_id': latest.id if latest else None,
                      'erp_workspace_blocker': None, 'erp_standard_development': development}
        agent.is_active = True
        agent.next_run_at = time.time_ns()
        agent.attempt_limit = 1
        agent.attempts_used = 0
        agent.updated_at = time.time_ns()
        await db.commit()


async def remember_standard_status(agent_id, run_id, status, erp_revision=None):
    async with get_async_db_context() as db:
        agent = await db.get(Automation, agent_id)
        if (agent and (agent.meta or {}).get('erp_standard_agent') is True
                and not agent.meta.get('erp_managed') and agent.meta.get('erp_ignore_run_id') != run_id):
            agent.meta = {**agent.meta, 'erp_status_sync': {
                'run_id': run_id, 'status': status, 'erp_revision': erp_revision,
            }}
            await db.commit()


async def sync_standard_status(erp, task_id, agent_id, run_id, status, previous, meta, chat_id=None, owner_id=None):
    try:
        task = await erp.get('Task', task_id)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        task = None

    def linked(doc):
        return (doc and doc.get(AGENT_URL) == agent_url(agent_id)
                and doc.get(EXECUTION_ID) in (None, '', agent_id))

    expected = meta.get('erp_task_revision')
    if linked(task) and (not expected or task_revision(task) == expected):
        already_started = (
            (previous or {}).get('run_id') == run_id and previous.get('status') == 'running'
            or audit_run_id(task) == run_id and audit_status(task) == 'Working'
        )
        audit = {'run_id': run_id, 'agent': agent_url(agent_id), 'erp_revision': meta.get('erp_open_revision'),
                 'task_revision': expected, 'generation': meta.get('erp_generation', 1),
                 'evidence_url': f'https://abit.ai/c/{chat_id}' if chat_id else None}
        if meta.get('erp_workspace_blocker'):
            audit['review_reason'] = meta['erp_workspace_blocker']
        elif meta.get('erp_standard_development') and status == 'success':
            audit['review_reason'] = 'Review the work and verification evidence from the Governance run.'
        if task['status'] in INTAKE and not already_started:
            await erp.save(task, {'status': 'Working', RELAY_STATUS: 'Running', DETAIL: json.dumps({**audit, 'status': 'Working'})})
            task = await erp.get('Task', task_id)
            await remember_standard_status(agent_id, run_id, 'running', task.get('modified'))
            if status == 'running':
                return
        if status in {'success', 'error'}:
            terminal = 'Pending Review' if status == 'success' or meta.get('erp_workspace_blocker') else 'Failed'
            if (linked(task) and task['status'] == 'Working' and (not expected or task_revision(task) == expected)
                    and audit_run_id(task) in (None, run_id)):
                await erp.save(task, {'status': terminal, RELAY_STATUS: 'Failed' if status == 'error' else 'Completed', DETAIL: json.dumps({**audit, 'status': terminal})})
                task = await erp.get('Task', task_id)
            if (meta.get('erp_standard_development') and chat_id and linked(task) and task['status'] == terminal
                    and audit_run_id(task) == run_id and (not expected or task_revision(task) == expected)):
                message = await ChatMessages.get_message_by_id(f'{chat_id}-{run_id}')
                if message and message.user_id == owner_id and message.role == 'assistant':
                    text = get_output_text(message.output) or (message.content if isinstance(message.content, str) else '')
                    if text.strip():
                        await erp.add_run_comment(task_id, run_id, text.strip(), chat_id)
    terminal_revision = task.get('modified') if task and task['status'] not in INTAKE else None
    await remember_standard_status(agent_id, run_id, status, terminal_revision)


async def reconcile_standard_statuses(resolver):
    # The standard scheduler's durable run record is authoritative, including
    # when ABiT AI restarts or ERP is temporarily unreachable.
    latest_run = (
        select(AutomationRun.id).where(AutomationRun.automation_id == Automation.id)
        .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
        .limit(1).correlate(Automation).scalar_subquery()
    )
    synced = Automation.meta['erp_status_sync']
    async with get_async_db_context() as db:
        rows = (await db.execute(
            select(Automation.id, Automation.user_id, Automation.meta, AutomationRun.id, AutomationRun.status, AutomationRun.chat_id)
            .join(AutomationRun, AutomationRun.id == latest_run)
            .where(
                Automation.meta['erp_standard_agent'].as_boolean().is_(True),
                Automation.meta['erp_standard_armed'].as_boolean().is_(True),
                Automation.meta['erp_managed'].as_boolean().is_not(True),
                AutomationRun.status.in_(('running', 'success', 'error')),
                func.coalesce(Automation.meta['erp_ignore_run_id'].as_string(), '') != AutomationRun.id,
                (func.coalesce(synced['run_id'].as_string(), '') != AutomationRun.id)
                | (func.coalesce(synced['status'].as_string(), '') != AutomationRun.status),
            )
        )).all()
    if not rows:
        return
    owner = await Users.get_user_by_email(resolver.app.owner)
    if not owner or owner.role != 'admin':
        return
    for agent_id, owner_id, meta, run_id, status, chat_id in rows:
        task_id = meta.get('erp_task')
        if (owner_id != owner.id or not isinstance(task_id, str)
                or not re.fullmatch(r'TASK-[A-Za-z0-9-]{1,64}', task_id)):
            continue
        if agent_id != standard_agent_id(task_id):
            legacy = resolver.store.get(task_id)
            if not legacy or legacy['agent'] != agent_id or not legacy.get('linked') or not legacy.get('synced'):
                continue
        try:
            await sync_standard_status(resolver.erp, task_id, agent_id, run_id, status,
                                       meta.get('erp_status_sync'), meta, chat_id, owner_id)
        except Exception as error:
            log.warning('ERP status for %s will reconcile: %s', task_id, type(error).__name__)


async def dispatch_event(resolver, task, revision, creator=None):
    if creator:
        legacy = resolver.store.get(task)
        doc = await resolver.erp.get('Task', task)
        if legacy and not legacy.get('synced') and not legacy['attempts']:
            # No external worker can be active for a run without an attempt.
            legacy.update(status='Cancelled', synced=True, progress='Moved to the Governance scheduler')
            resolver.store.put(legacy)
        if legacy and not legacy.get('synced'):
            if doc['status'] in INTAKE:
                # Retire an existing worker before arming the same agent.
                if time.time() >= legacy.get('check_after', 0):
                    await resolver.advance(legacy)
                return
        else:
            await create_standard_agent(
                resolver, task, task=doc, legacy_run=legacy if legacy and legacy.get('linked') else None,
            )
    else:
        await resolver.erp.preflight()
        await resolver.refresh(task)
    if revision is None:
        resolver.store.discard_reset(task)
    else:
        resolver.store.acknowledge(task, revision)

async def scheduler_loop():
    # One scheduler on this service's persistent host, even if multiple app
    # processes enter lifespan. Child jobs have independent stop/deadline fences.
    config_path = os.environ.get(CONFIG)
    if not config_path:
        return
    lock_path = runtime_state().with_suffix('.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        config = json.loads(Path(config_path).read_text())
        erp = ERPClient(config['erp_token_file'])
        resolver = Resolver(Store(runtime_state()), erp, ProjectAgents(config['owner_email']),
                            Worker('relay'), Worker('cli'), config['projects'],
                            existing_agents=config.get('existing_agents'), creator=config.get('standard_agent_creator'),
                            development_tasks=config.get('development_tasks', ()), delivery_policy=config,
                            task_workspaces=config.get('task_workspaces'), standalone=config.get('standalone'))
        work = {}
        retry_at = {}
        creator = config.get('standard_agent_creator')
        poll_intake = config.get('poll_intake', False) and not creator
        last_scan = 0
        last_status_scan = 0
        try:
            while True:
                for task_id in [key for key, job in work.items() if job.done()]:
                    try:
                        work.pop(task_id).result()
                        retry_at.pop(task_id, None)
                    except Exception as exc:
                        retry_at[task_id] = time.monotonic() + 5
                        log.warning('ERP task %s will reconcile: %s', task_id, type(exc).__name__)
                for task in resolver.store.resets():
                    if task not in work and time.monotonic() >= retry_at.get(task, 0):
                        work[task] = asyncio.create_task(
                            dispatch_event(resolver, task, None, creator) if creator else resolver.reset(task)
                        )
                for task, revision in resolver.store.inbox():
                    if task not in work and time.monotonic() >= retry_at.get(task, 0):
                        work[task] = asyncio.create_task(dispatch_event(resolver, task, revision, creator))
                for run in resolver.store.pending():
                    if (run['task'] not in work and time.monotonic() >= retry_at.get(run['task'], 0)
                            and time.time() >= run.get('check_after', 0)):
                        work[run['task']] = asyncio.create_task(
                            dispatch_event(resolver, run['task'], None, creator)
                            if creator and not run['attempts'] else resolver.advance(run)
                        )
                if poll_intake and time.monotonic() - last_scan >= 5 and 'intake' not in work:
                    work['intake'] = asyncio.create_task(scan(resolver, work, config.get('only_tasks')))
                    last_scan = time.monotonic()
                if creator and time.monotonic() - last_status_scan >= 2 and 'standard-status' not in work:
                    work['standard-status'] = asyncio.create_task(reconcile_standard_statuses(resolver))
                    last_status_scan = time.monotonic()
                await asyncio.sleep(1)
        finally:
            for job in work.values():
                job.cancel()
            await asyncio.gather(*work.values(), return_exceptions=True)
            await erp.client.aclose()


async def scan(resolver, work, only_tasks):
    await resolver.erp.preflight()
    async for assignment in resolver.erp.assignments():
        task = assignment['reference_name']
        if only_tasks is not None and task not in only_tasks:
            continue
        if task not in work and not resolver.store.get(task):
            work[task] = asyncio.create_task(resolver.intake(task))

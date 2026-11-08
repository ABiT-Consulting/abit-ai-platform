"""Persistent, opt-in ERPNext task intake for managed project agents."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx

from open_webui.utils.erp_workspace import WorkspaceConfigurationError, approved_workspace

ERP = 'https://erp.abit.ai'
ASSIGNEE = 'ai-developer@abitcons.com'
ASSIGNEE_NAME = 'ABIT AI Developer Agent'
PRIMARY = 'omniroute.abit-chatgpt-relay/project-management'
SECONDARY = 'codex-cli-linux-dev'
HOST = 'ABIT-DEV-LINUX'
USER = os.environ.get('ABIT_ERP_WORKER_USER', '')
LIMIT = 3
DURATION = 1200
DELIVERY_WAIT = 900
INTAKE = {'Open', 'Overdue'}
REOPENABLE = {'Completed', 'Pending Review', 'Failed'}
TERMINAL = REOPENABLE | {'Cancelled'}
EXECUTION_ID = 'custom_ai_relay_execution_id'
AGENT_URL = 'custom_abit_ai_agent_url'
DETAIL = 'custom_ai_relay_execution_detail'
RELAY_STATUS = 'custom_ai_relay_execution_status'


class Blocked(Exception):
    """An explicit prerequisite is missing."""


def key(*parts):
    return hashlib.sha256('\0'.join(map(str, parts)).encode()).hexdigest()


TASK_INPUTS = (
    'owner', 'project', 'type', 'subject', 'description', 'expected_time',
    'custom_chatgpt_project', 'custom_chatgpt_project_id', 'custom_acceptance_criteria',
    'custom_ai_dev_delivery_required',
)


def task_revision(task):
    inputs = {field: task.get(field) for field in TASK_INPUTS}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def attempt_chat_url(attempt_id):
    return 'https://abit.ai/c/' + str(uuid5(NAMESPACE_URL, 'https://abit.ai/erp-attempt/' + attempt_id))


def execution_run_id(run):
    return key(ERP, run['agent'], run['cycle'])


def task_audit(task):
    try:
        audit = json.loads(task.get(DETAIL) or '{}')
        return audit if isinstance(audit, dict) else {}
    except (TypeError, ValueError):
        return {}


def audit_run_id(task):
    return task_audit(task).get('run_id')


def audit_status(task):
    return task_audit(task).get('status')


def agent_url(agent):
    return 'https://abit.ai/automations/' + agent


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='seconds')


class ERPClient:
    def __init__(self, token_file):
        token = Path(token_file).read_text().strip()
        self.client = httpx.AsyncClient(base_url=ERP, timeout=8, follow_redirects=False,
                                        headers={'Authorization': 'token ' + token})

    async def request(self, method, path, **kwargs):
        retries = 2 if method == 'GET' else 0
        for attempt in range(retries + 1):
            try:
                response = await self.client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as error:
                if attempt == retries or error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise
            except httpx.TransportError:
                if attempt == retries:
                    raise
            await asyncio.sleep(attempt + 1)

    async def get(self, dt, name):
        doc = (await self.request('GET', f'/api/resource/{quote(dt, safe="")}/{quote(name, safe="")}'))['data']
        if dt == 'Task' and AGENT_URL not in doc:
            try:
                field = await self.get('Custom Field', 'Task-' + AGENT_URL)
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
            else:
                if (field.get('dt'), field.get('fieldname'), field.get('fieldtype'), field.get('options'),
                        field.get('hidden')) == ('Task', AGENT_URL, 'Data', 'URL', 0):
                    doc[AGENT_URL] = None
        return doc

    async def rows(self, dt, fields, filters):
        start = 0
        while True:
            result = (await self.request('GET', '/api/resource/' + quote(dt, safe=''), params={
                'fields': json.dumps(fields), 'filters': json.dumps(filters),
                'limit_page_length': 100, 'limit_start': start, 'order_by': 'name asc',
            }))['data']
            for row in result:
                yield row
            if len(result) < 100:
                return
            start += len(result)

    async def preflight(self):
        user = await self.get('User', ASSIGNEE)
        if (user.get('name'), user.get('full_name'), user.get('enabled')) != (ASSIGNEE, ASSIGNEE_NAME, 1):
            raise Blocked('ERP AI Developer assignment identity is unverified')
        field = await self.get('Property Setter', 'Task-status-options')
        if (field.get('doc_type'), field.get('field_name'), field.get('property')) != ('Task', 'status', 'options'):
            raise Blocked('ERP Task status override could not be verified')
        if not {'Working', 'Pending Review', 'Completed', 'Failed'} <= set(field['value'].splitlines()):
            raise Blocked('ERP Task requires Working, Pending Review and Failed status options')
        async for _ in self.rows('Workflow', ['name'], [['document_type', '=', 'Task'], ['is_active', '=', 1]]):
            raise Blocked('ERP Task has a workflow that requires explicit transition support')

    async def assignments(self, task_id=None):
        filters = [['reference_type', '=', 'Task'], ['status', '=', 'Open'], ['allocated_to', '=', ASSIGNEE]]
        if task_id:
            filters.append(['reference_name', '=', task_id])
        async for row in self.rows('ToDo', ['name', 'reference_name'], filters):
            yield row

    async def assigned(self, task):
        # The Task resource detail omits Frappe's internal _assign column.
        result = await self.request('GET', '/api/method/frappe.client.get_value', params={
            'doctype': 'Task', 'filters': task['name'], 'fieldname': '_assign',
        })
        if ASSIGNEE not in json.loads((result.get('message') or {}).get('_assign') or '[]'):
            return False
        async for _ in self.assignments(task['name']):
            return True
        return False

    async def save(self, task, changes):
        if all(task.get(field) == value for field, value in changes.items()):
            return
        # Frappe save checks the document's original modified timestamp. A
        # stale response is reconciled with a fresh read on the next tick.
        await self.request('POST', '/api/method/frappe.client.save', json={'doc': {**task, **changes, 'doctype': 'Task'}})
        fresh = await self.get('Task', task['name'])
        if any(fresh.get(field) != value for field, value in changes.items()):
            raise Blocked('ERP update did not pass readback')


class Store:
    def __init__(self, path):
        self.path = path
        with closing(self.open()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS erp_runs (task TEXT PRIMARY KEY, state TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS erp_intake (task TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS erp_intake_events (
                event_id TEXT PRIMARY KEY, task TEXT NOT NULL, erp_revision TEXT NOT NULL
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS erp_intake_events_task_revision '
                       'ON erp_intake_events (task, erp_revision)')
            db.execute('CREATE TABLE IF NOT EXISTS erp_resets (task TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS erp_history (task TEXT, cycle INTEGER, state TEXT, PRIMARY KEY(task, cycle))')

    def open(self):
        return sqlite3.connect(self.path, timeout=5)

    def get(self, task):
        with closing(self.open()) as db:
            row = db.execute('SELECT state FROM erp_runs WHERE task=?', (task,)).fetchone()
        return json.loads(row[0]) if row else None

    def pending(self):
        with closing(self.open()) as db:
            rows = db.execute('SELECT state FROM erp_runs').fetchall()
        return [run for (value,) in rows if not (run := json.loads(value)).get('synced')]

    def put(self, run):
        with closing(self.open()) as db, db:
            db.execute('INSERT INTO erp_runs VALUES (?,?) ON CONFLICT(task) DO UPDATE SET state=excluded.state',
                       (run['task'], json.dumps(run)))

    def enqueue(self, task, *, event_id=None, erp_revision=None):
        if (event_id is None) != (erp_revision is None):
            raise ValueError('ERP event ID and revision must be supplied together')
        with closing(self.open()) as db, db:
            if event_id is not None:
                # Record the receipt and the queue change under one writer lock.
                db.execute('BEGIN IMMEDIATE')
                received = db.execute(
                    'SELECT task, erp_revision FROM erp_intake_events WHERE event_id=?', (event_id,),
                ).fetchone()
                if received:
                    if received != (task, erp_revision):
                        raise ValueError('ERP event ID has already been used for a different task or revision')
                    return False
                (latest,) = db.execute(
                    'SELECT MAX(erp_revision) FROM erp_intake_events WHERE task=?', (task,),
                ).fetchone()
                db.execute('INSERT INTO erp_intake_events VALUES (?,?,?)', (event_id, task, erp_revision))
                if latest is not None and erp_revision <= latest:
                    return False
            db.execute('INSERT INTO erp_intake VALUES (?, 1) '
                       'ON CONFLICT(task) DO UPDATE SET revision=revision+1', (task,))
        return True

    def inbox(self):
        with closing(self.open()) as db:
            return db.execute('SELECT task, revision FROM erp_intake ORDER BY rowid LIMIT 100').fetchall()

    def acknowledge(self, task, revision):
        with closing(self.open()) as db, db:
            db.execute('DELETE FROM erp_intake WHERE task=? AND revision=?', (task, revision))

    def queue_reset(self, task):
        with closing(self.open()) as db, db:
            db.execute('INSERT OR IGNORE INTO erp_resets VALUES (?)', (task,))

    def resets(self):
        with closing(self.open()) as db:
            return [task for task, in db.execute('SELECT task FROM erp_resets')]

    def discard_reset(self, task):
        with closing(self.open()) as db, db:
            db.execute('DELETE FROM erp_resets WHERE task=?', (task,))

    def history(self, task):
        with closing(self.open()) as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT state FROM erp_history WHERE task=? ORDER BY cycle', (task,))]

    def reset(self, run, *, development=False, task=None, scope=None):
        with closing(self.open()) as db, db:
            db.execute('INSERT INTO erp_history VALUES (?,?,?)',
                       (run['task'], run['cycle'], json.dumps(run)))
            run.update(cycle=run['cycle'] + 1, attempts=[], synced=False, development=development,
                       status='Preparing', progress='ERP task reopened. Verifying configuration')
            for field in ('check_after', 'erp_started', 'superseded', 'task_revision', 'erp_revision', 'awaiting_delivery'):
                run.pop(field, None)
            if scope is not None:
                run['scope'] = scope
            if task is not None:
                run.update(task_revision=task_revision(task), erp_revision=task.get('modified'))
            db.execute('UPDATE erp_runs SET state=? WHERE task=?', (json.dumps(run), run['task']))
            db.execute('DELETE FROM erp_resets WHERE task=?', (run['task'],))


class Resolver:
    def __init__(self, store, erp, app, relay, cli, projects, clock=time.time, *, existing_agents=None, creator=None, development_tasks=(), delivery_policy=None, task_workspaces=None, standalone=None):
        self.store, self.erp, self.app = store, erp, app
        self.relay, self.cli, self.projects = relay, cli, projects
        self.clock = clock
        self.existing_agents = existing_agents or {}
        self.creator = creator
        self.development_tasks = frozenset(development_tasks)
        self.delivery_policy = delivery_policy or {}
        self.task_workspaces = task_workspaces
        self.standalone = standalone

    def workspace(self, task):
        try:
            return approved_workspace(task, self.projects, self.task_workspaces, self.standalone)
        except WorkspaceConfigurationError as error:
            raise Blocked(str(error)) from error

    async def project(self, task, scope):
        if scope.get('workspace_source') == 'standalone' or not task.get('project'):
            return {}
        return await self.erp.get('Project', task['project'])

    def development_task(self, task):
        return (str(task.get('type') or '').strip().casefold() in {'development', 'bug'}
                or task['name'] in self.development_tasks)

    async def authorized(self, task):
        return await self.erp.assigned(task)


    async def intake(self, task_id, task=None, *, standard_agent=None):
        if self.store.get(task_id):
            return
        if task is None:
            task = await self.erp.get('Task', task_id)
        existing_agent = self.existing_agents.get(task_id)
        linked_url = task.get(AGENT_URL)
        expected_link = agent_url(standard_agent or existing_agent) if standard_agent or existing_agent else None
        if (task['status'] not in INTAKE or task.get('is_group') or task.get('is_template')
                or (task.get(EXECUTION_ID) or None) != existing_agent
                or (linked_url and linked_url != expected_link)
                or task.get('custom_ai_scheduler_dispatch_at')
                or task.get(RELAY_STATUS) not in (None, '', 'Not Started', 'Blocked')
                or not await self.authorized(task)):
            return
        project_id = task.get('project')
        try:
            scope = self.workspace(task)
            if not scope:
                raise Blocked('No approved abit.ai project and ABIT-DEV-LINUX workspace mapping')
            project = await self.project(task, scope)
            await self.app.verify_project(project, scope)
        except Blocked as error:
            await self.erp.save(task, {RELAY_STATUS: 'Blocked', DETAIL: str(error)})
            return
        self.store.put({
            'task': task_id, 'project': project_id,
            'agent': standard_agent or existing_agent or 'erp-' + key(ERP, task_id)[:32],
            'existing_agent': bool(existing_agent), 'standard_agent': bool(standard_agent),
            'development': self.development_task(task),
            'task_revision': task_revision(task), 'erp_revision': task.get('modified'),
            'scope': scope, 'cycle': 1, 'attempts': [], 'status': 'Preparing', 'progress': 'Verifying configuration',
            'primary': PRIMARY, 'secondary': SECONDARY, 'host': HOST, 'user': USER,
        })
        await self.advance(self.store.get(task_id))

    async def refresh(self, task_id):
        task = await self.erp.get('Task', task_id)
        run = self.store.get(task_id)
        if not run:
            if self.workspace(task):
                return await self.intake(task_id, task)
            return
        if (not run.get('linked') or task.get(EXECUTION_ID) != run['agent']
                or task.get('project') != run['project'] or not await self.authorized(task)):
            return
        await self.app.ensure_agent(run, task)
        if run.get('synced') and run['status'] in REOPENABLE | {'Cancelled'} and task['status'] == 'Open':
            return await self.reset(task_id, task)
        if not run['attempts'] or run['attempts'][-1].get('finished'):
            try:
                destination = await self.app.destination(run, task)
            except Blocked:
                destination = None
            if run.get('destination') != destination:
                run['destination'] = destination
                self.store.put(run)
                await self.app.progress(run)

    async def reset(self, task_id, task=None, *, superseded=False):
        run = self.store.get(task_id)
        if task is None:
            task = await self.erp.get('Task', task_id)
        allowed = REOPENABLE | {'Cancelled'}
        entry = INTAKE | ({'Working'} if superseded else set())
        if (not run or not run.get('synced') or run['status'] not in allowed
                or task.get(EXECUTION_ID) != run['agent'] or task['status'] not in entry
                or not await self.authorized(task)):
            self.store.discard_reset(task_id)
            return
        scope = self.workspace(task)
        if not scope or task.get('project') != run['project']:
            raise Blocked('The approved ERP workspace is unavailable for this reopened task')
        if any(scope.get(field) != run['scope'].get(field) for field in ('owner_id', 'folder_id')):
            raise Blocked('The approved workspace does not match the saved agent owner and folder')
        project = await self.project(task, scope)
        await self.app.verify_project(project, scope)
        await self.app.verify_scope(project, scope)
        self.store.reset(run, development=self.development_task(task), task=task, scope=scope)
        await self.advance(run)

    async def advance(self, run):
        if run.get('synced'):
            return
        attempt = run['attempts'][-1] if run['attempts'] else None
        if attempt and not attempt.get('finished') and self.clock() >= attempt['deadline'] and not attempt.get('stop_reason'):
            # Deadline is independent of ERP availability and model progress.
            attempt['stop_reason'] = '20-minute attempt deadline reached'
            self.store.put(run)
            await self.stop(run, attempt)
            return
        try:
            task = await self.erp.get('Task', run['task'])
            assigned = await self.authorized(task)
        except (httpx.HTTPError, OSError, ValueError):
            # ERP or network downtime must not consume a development cycle.
            # The worker lease still enforces its deadline independently.
            run['check_after'] = self.clock() + 5
            self.store.put(run)
            return
        owned = task.get(EXECUTION_ID) == run['agent'] or (
            not task.get(EXECUTION_ID) and not task.get('custom_ai_scheduler_dispatch_at')
            and not any(a.get('announced') for a in run['attempts'])
            and task.get(RELAY_STATUS) in (None, '', 'Not Started', 'Blocked')
        )
        task_state_ok = task['status'] in INTAKE | {'Working', run['status']}
        spec = task_revision(task)
        if 'task_revision' not in run:
            # Existing runs predate the specification fingerprint.
            run.update(task_revision=spec, erp_revision=task.get('modified'),
                       erp_started=any(item.get('announced') for item in run['attempts']))
            self.store.put(run)
        same_scope = assigned and owned and task.get('project') == run['project']
        changed = run['task_revision'] != spec
        reopened = task['status'] in INTAKE and (
            run.get('erp_started') or audit_run_id(task) == execution_run_id(run) and audit_status(task) == 'Working'
        )
        if run['status'] != 'Cancelled' and (not same_scope or not task_state_ok or changed or reopened):
            run['status'] = 'Cancelled'
            run['superseded'] = bool(same_scope and task['status'] in INTAKE | {'Working'} and (changed or reopened))
            run['progress'] = ('ERP task was edited or reopened. Retiring the previous run.' if run['superseded']
                               else 'Task was cancelled, reassigned, moved or taken over in ERP')
            self.store.put(run)
        if run['status'] == 'Cancelled':
            if attempt and not attempt.get('finished'):
                attempt['stop_reason'] = run['progress']
                self.store.put(run)
                await self.stop(run, attempt)
                return
            await self.app.progress(run)
            run['synced'] = True
            self.store.put(run)
            if run.get('superseded') and same_scope and task['status'] in INTAKE | {'Working'}:
                if self.creator and task['status'] in INTAKE:
                    self.store.enqueue(run['task'])
                else:
                    await self.reset(run['task'], task, superseded=True)
            return
        if run['status'] in TERMINAL:
            await self.sync(task, run)
            await self.app.progress(run)
            run['synced'] = True
            self.store.put(run)
            return
        if attempt and attempt.get('stop_reason') and not attempt.get('finished'):
            await self.stop(run, attempt)
            return

        try:
            approved = self.workspace(task)
        except Blocked:
            approved = None
        if approved and not run['attempts'] and all(
            approved.get(k) == run['scope'].get(k) for k in ('owner_id', 'folder_id', 'account', 'chatgpt_project')
        ):
            run['scope'] = approved
        if approved != run['scope']:
            run['progress'] = 'The approved project mapping changed; reconcile the existing agent'
            if attempt and not attempt.get('finished'):
                attempt['stop_reason'] = run['progress']
                self.store.put(run)
                await self.stop(run, attempt)
            else:
                run['status'] = 'Pending Review'
                self.store.put(run)
                await self.advance(run)
            return

        if run.get('awaiting_delivery'):
            delivery = run['awaiting_delivery']
            await self.finish_verified(task, run, attempt, delivery['result'])
            if run['status'] in TERMINAL:
                await self.advance(run)
            return

        if not attempt or attempt.get('finished'):
            try:
                project = await self.project(task, run['scope'])
                await self.app.verify_project(project, run['scope'])
                await self.app.ensure_agent(run, task)
                if not run.get('linked'):
                    link = {EXECUTION_ID: run['agent'], DETAIL: self.audit(run)}
                    if AGENT_URL in task:
                        link[AGENT_URL] = agent_url(run['agent'])
                    await self.erp.save(task, link)
                    run['linked'] = True
                    self.store.put(run)
                    task = await self.erp.get('Task', run['task'])
                    if (not await self.authorized(task) or task['status'] not in INTAKE | {'Working'}
                            or task_revision(task) != run['task_revision']):
                        return
                if AGENT_URL not in task:
                    raise Blocked('ERP Task requires a visible ABiT AI Agent URL field before execution')
                run['destination'] = None
                await self.app.verify_scope(project, run['scope'])
                uses_relay = bool(run['scope'].get('account') and run['scope'].get('chatgpt_project'))
                if uses_relay:
                    try:
                        run['destination'] = await self.app.destination(run, task)
                        await self.relay.preflight(run)
                    except Blocked:
                        if not run.get('development'):
                            raise
                        uses_relay = False
                await self.cli.preflight(run)
                local_instructions = await self.app.instructions(run['scope'])
                instructions = '\n\n'.join(text for text in (
                    local_instructions, str(project.get('custom_ai_developer_instructions') or ''),
                ) if text)
                description = str(task.get('description') or '')
                if len(instructions) > 12000 or len(description) > 16000:
                    raise Blocked('Project instructions or Task description exceeds the verified relay context limit')
            except Blocked as error:
                run['progress'] = str(error)
                if not attempt:
                    run['status'] = 'Blocked'
                run['check_after'] = self.clock() + 15
                self.store.put(run)
                if not attempt:
                    await self.erp.save(task, {RELAY_STATUS: 'Blocked',
                                               DETAIL: self.audit(run) if run.get('linked') else str(error)})
                await self.app.progress(run)
                return
            run['status'] = 'Working'
            run.pop('check_after', None)
            now = self.clock()
            number = len(run['attempts']) + 1
            attempt = {'number': number, 'id': key(run['agent'], run['cycle'], number),
                       'started': now, 'deadline': now + DURATION,
                       'phase': 'relay' if uses_relay else 'cli', 'uses_relay': uses_relay,
                       'destination': run['destination'],
                       'request': {'task': run['task'], 'subject': str(task.get('subject') or '')[:240],
                                   'description': description, 'instructions': instructions}}
            run['attempts'].append(attempt)
            run['progress'] = 'Starting ChatGPT Browser Relay' if uses_relay else 'Starting Codex CLI on ABIT-DEV-LINUX'
            self.store.put(run)
        if self.clock() >= attempt['deadline']:
            return await self.advance(run)

        driver = self.relay if attempt['phase'] == 'relay' else self.cli
        result = await driver.observe_or_start(run, attempt, task)
        status = result['status']
        if status in {'running', 'completed'} and not run.get('erp_started'):
            # Save Working only after the worker confirms execution. Preserve
            # the durable worker request if this ERP write must be retried.
            await self.sync(task, run)
            run['erp_started'] = True
            attempt['announced'] = True
            self.store.put(run)
            await self.app.progress(run)
        if status in {'queued', 'running', 'waiting', 'uncertain'}:
            progress = result.get('progress') or status
            if progress != run['progress']:
                run['progress'] = progress
                self.store.put(run)
                await self.app.progress(run)
            return
        if attempt['phase'] == 'relay' and status == 'completed':
            attempt['findings'] = result['findings']
            attempt['phase'] = 'cli'
            run['progress'] = 'Handing off to Codex CLI on ABIT-DEV-LINUX'
            self.store.put(run)
            await self.app.progress(run)
            return await self.advance(run)
        if attempt['phase'] == 'cli' and status == 'completed':
            attempt['evidence'] = result.get('evidence', {})
            if (result.get('verified') is True and self.clock() < attempt['deadline']
                    and (not run.get('development') or result.get('testing_passed') is True)):
                await self.finish_verified(task, run, attempt, result)
                await self.advance(run)
                return
        attempt['stop_reason'] = result.get('error') or 'Repository verification did not pass'
        self.store.put(run)
        await self.stop(run, attempt)

    async def stop(self, run, attempt):
        # Idempotent revoke is sent to both, including workers whose start
        # response was lost. Neither absence nor a disconnected poll is proof.
        relay_stop = (self.relay.stop(run, attempt) if attempt.get('uses_relay', True)
                      else asyncio.sleep(0, result={'confirmed': True}))
        results = await asyncio.gather(relay_stop, self.cli.stop(run, attempt), return_exceptions=True)
        if isinstance(results[1], dict) and results[1].get('evidence'):
            attempt['evidence'] = results[1]['evidence']
        if isinstance(results[0], dict) and results[0].get('findings'):
            attempt['findings'] = results[0]['findings']
        if not all(isinstance(result, dict) and result.get('confirmed') is True for result in results):
            run['progress'] = 'Waiting for relay and CLI stop confirmation. ' + attempt['stop_reason']
            self.store.put(run)
            await self.app.progress(run)
            return
        cli = results[1]
        verified_before_deadline = (
            run['status'] != 'Cancelled' and cli.get('status') == 'completed'
            and cli.get('verified') is True
            and (not run.get('development') or cli.get('testing_passed') is True)
            and isinstance(cli.get('finished_at'), (int, float))
            and cli['finished_at'] <= attempt['deadline']
            and attempt['stop_reason'] == '20-minute attempt deadline reached'
        )
        if verified_before_deadline:
            attempt['worker_finished'] = cli['finished_at']
            attempt.pop('stop_reason')
        if verified_before_deadline:
            task = await self.erp.get('Task', run['task'])
            attempt['evidence'] = cli.get('evidence', {})
            await self.finish_verified(task, run, attempt, cli)
        else:
            await self.finish(run, attempt, success=False)
        # Start immediately after confirmed stop, subject to fresh ERP authorization.
        if run['status'] != 'Cancelled':
            await self.advance(run)

    async def finish_verified(self, task, run, attempt, result):
        review = None
        if run.get('development'):
            from open_webui.utils.erp_task_delivery import evaluate_delivery

            decision = evaluate_delivery(task=task, result=result, attempt_id=attempt['id'],
                                         automation_id=run['agent'], config=self.delivery_policy, now=self.clock())
            review = decision.blocker
            delivery = run.get('awaiting_delivery')
            if decision.awaiting_receipt and (not delivery or self.clock() < delivery['deadline']):
                if not delivery:
                    run['awaiting_delivery'] = {
                        'result': result, 'deadline': self.clock() + DELIVERY_WAIT,
                    }
                    attempt.update(finished=self.clock(), phase='delivery')
                run.update(progress='Independent testing passed. Waiting for signed DEV delivery proof.',
                           check_after=self.clock() + 5)
                self.store.put(run)
                if not delivery:
                    await self.app.progress(run)
                return
            run.pop('awaiting_delivery', None)
            if decision.proof:
                attempt.setdefault('evidence', {})['delivery'] = decision.proof
        await self.finish(run, attempt, success=True, review=review)

    async def finish(self, run, attempt, success, review=None):
        attempt['finished'] = self.clock()
        attempt['phase'] = 'verified' if success else 'stopped'
        if success and review:
            run.update(status='Pending Review', progress=review)
        elif success and run.get('development'):
            run.update(status='Completed', progress='Independent testing and required delivery checks passed')
        elif success:
            run.update(status='Pending Review', progress='Verified changes ready for review')
        elif run['status'] == 'Cancelled':
            pass
        elif self.creator or len(run['attempts']) >= LIMIT:
            status = 'Pending Review' if run.get('development') else 'Failed'
            reason = ('The previous worker ended without a verified pass. Reopen the ERP task to use Governance.'
                      if self.creator and len(run['attempts']) < LIMIT else
                      f'{LIMIT} cycles ended without a verified pass.')
            run.update(status=status, progress=f"{reason} {attempt['stop_reason']}")
        else:
            run['progress'] = 'Execution stopped. Retrying. ' + attempt['stop_reason']
        self.store.put(run)
        await self.app.progress(run)

    async def sync(self, task, run):
        status = run['status']
        # Leave external terminal states and unrelated execution claims alone.
        if (task['status'] not in INTAKE | {'Working', status}
                or task.get(EXECUTION_ID) not in (None, '', run['agent'])
                or task_revision(task) != run.get('task_revision', task_revision(task))):
            return
        if status in TERMINAL and run.get('erp_started'):
            linked_run = audit_run_id(task)
            if linked_run and linked_run != execution_run_id(run):
                return
        if status == 'Working' and AGENT_URL not in task:
            raise Blocked('ERP Task requires a visible ABiT AI Agent URL field before execution')
        relay_status = {'Working': 'Running', 'Pending Review': 'Completed',
                        'Completed': 'Completed', 'Failed': 'Failed'}[status]
        changes = {
            'status': status, EXECUTION_ID: run['agent'], DETAIL: self.audit(run),
            RELAY_STATUS: relay_status,
            'custom_ai_relay_retry_count': max(0, len(run['attempts']) - 1),
        }
        if AGENT_URL in task:
            changes[AGENT_URL] = agent_url(run['agent'])
        await self.erp.save(task, changes)

    def audit(self, run):
        def audit_attempts(state):
            attempts = []
            for attempt in state['attempts']:
                row = {k: v for k, v in attempt.items() if k not in {'request', 'announced'}}
                row.update(started=stamp(attempt['started']), deadline=stamp(attempt['deadline']))
                if attempt.get('finished'):
                    row['finished'] = stamp(attempt['finished'])
                attempts.append(row)
            return attempts

        history = [{'cycle': state['cycle'], 'status': state['status'], 'attempts': audit_attempts(state)}
                   for state in self.store.history(run['task'])]
        last_attempt = run['attempts'][-1] if run['attempts'] else None
        return json.dumps({'agent': agent_url(run['agent']), 'cycle': run['cycle'],
                           'run_id': execution_run_id(run), 'erp_revision': run.get('erp_revision'),
                           'task_revision': run.get('task_revision'), 'status': run['status'],
                           'evidence_url': attempt_chat_url(last_attempt['id']) if last_attempt else None,
                           'primary': PRIMARY, 'secondary': SECONDARY, 'host': HOST, 'user': USER,
                           'destination': run.get('destination'),
                           'progress': run['progress'], 'attempts': audit_attempts(run),
                           'previous_cycles': history}, indent=2, ensure_ascii=False)

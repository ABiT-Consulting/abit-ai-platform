"""Separate owner-scoped abitdot conversations for ERP implementation and QA."""

import asyncio
from contextlib import asynccontextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import time
from uuid import NAMESPACE_URL, uuid5

from open_webui.utils.erp_dot_workflow import POLICY, task_context

# QA keeps its general DATA_DIR isolated; only the owner-verified ERP Dot
# journal and dispatch lock are shared with the application.
STATE_DIR = None

QA_EVIDENCE_ROOT = Path('/srv/abit-dev-linux/abit-ai/qa-service/evidence')


@asynccontextmanager
async def dispatch_slot():
    from open_webui.env import DATA_DIR

    with (Path(STATE_DIR or DATA_DIR) / 'erp-dot-dispatch.lock').open('a') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


async def synced_task_project(task, owner, project):
    """Resolve task overrides against this owner's actual synced account."""
    from open_webui.models.folders import Folders
    from open_webui.routers.chatgpt_relay import _db
    from open_webui.utils.abitdot_primary_relay import relay_account_for_owner

    name = str(task.get('custom_chatgpt_project') or '').strip()
    selected = str(task.get('custom_chatgpt_project_id') or '').strip()
    if not name and not selected and not project:
        return None  # A standalone task need not open a ChatGPT Project.
    account = await relay_account_for_owner(owner)
    clauses, values = ['owner=?', 'account=?'], [owner, account]
    if name:
        clauses.append('name=?')
        values.append(name)
    elif not selected:
        clauses.append('folder_id=?')
        values.append(project['id'])
    if selected:
        if not re.fullmatch(r'g-p-[a-f0-9]{32}', selected):
            raise ValueError('The ERP task ChatGPT Project ID is invalid')
        clauses.append('project_id=?')
        values.append(selected)
    with _db() as db:
        rows = db.execute(
            'SELECT DISTINCT project_id,name,url,folder_id FROM projects WHERE ' + ' AND '.join(clauses), values
        ).fetchall()
    if len(rows) != 1 or not re.fullmatch(r'g-p-[a-f0-9]{32}', rows[0]['project_id'] or ''):
        raise ValueError('The task must resolve to exactly one project in the verified owner account')
    row = rows[0]
    folder = await Folders.get_folder_by_id_and_user_id(row['folder_id'], owner) if row['folder_id'] else None
    if folder is None:
        raise ValueError('The task ChatGPT Project has no verified owner folder')
    result = folder.model_dump()
    result['meta'] = {
        **(result.get('meta') or {}),
        'chatgpt_project_id': row['project_id'],
        'chatgpt_project_url': row['url'],
    }
    return result


async def dispatch_task_dot(
    erp,
    task,
    owner,
    automation_id,
    run_id,
    prompt,
    role,
    project=None,
    *,
    before_submit=None,
    policy=None,
    relay_scope_preflight=None,
):
    from open_webui.env import DATA_DIR
    from open_webui.utils.abitdot_primary import (
        build_project_envelope,
        deliver,
        primary_dot_for_owner,
        read_owner_primary_runs,
    )
    from open_webui.utils.abitdot_primary_relay import send_via_existing_relay

    dot_id = primary_dot_for_owner(owner)
    if not dot_id:
        raise ValueError('ERP abitdot is not enabled for the verified task owner')
    selection = dict(task)
    for _ in range(2):
        if (
            selection.get('custom_chatgpt_project')
            or selection.get('custom_chatgpt_project_id')
            or not selection.get('parent_task')
        ):
            break
        parent = await erp.get('Task', selection['parent_task'])
        if parent.get('project') != task.get('project'):
            raise ValueError('The repair parent belongs to another project')
        selection = parent
    project = await synced_task_project(selection, owner, project)
    context = await task_context(erp, task, role)
    full_prompt = (
        (POLICY if policy is None else policy)
        + '\n\n'
        + prompt
        + '\n\nVerified ERP context:\n'
        + json.dumps(context, ensure_ascii=False)
    )
    envelope = build_project_envelope(
        owner=owner,
        automation={
            'id': automation_id,
            'user_id': owner,
            'name': f"{role}: {task['name']} — {task.get('subject', '')}",
            'data': {'prompt': full_prompt},
            'meta': {
                'erp_task': task['name'],
                'erp_role': role,
                'erp_revision': context['latest_run'],
                'governance_managed': policy is not None,
            },
        },
        run_id=run_id,
        dot_id=dot_id,
        project=project,
        prompt=full_prompt,
    )
    if policy is not None:
        from copy import deepcopy
        from open_webui.utils.agentic_bridge import digest
        from open_webui.utils.agentic_relay_scope import _proof

        if before_submit is None or not isinstance(relay_scope_preflight, dict):
            raise ValueError('Governed Dot dispatch requires its exact verified scope preflight')
        initial = _proof(relay_scope_preflight, relay_scope_preflight['scope'], now=int(time.time()), fresh=False)
        scope = initial['scope']
        if any(
            scope.get(k) != v
            for k, v in {
                'owner': owner,
                'agent_id': automation_id,
                'run_id': run_id,
                'task_id': task['name'],
                'project_id': task['project'],
                'role': role,
                'dot_id': dot_id,
            }.items()
        ):
            raise ValueError('Scope preflight belongs to a different original ERP execution')
        envelope['task']['meta'].update(
            governance_project=task['project'],
            governance_packet_digest=scope['work_packet_digest'],
            governance_scope_digest=digest(scope),
        )
        envelope['governance_scope'] = {
            'protocol_version': 1,
            'scope': deepcopy(scope),
            'preflight_proof': initial,
            'submission_proof': None,
        }
    path = Path(STATE_DIR or DATA_DIR) / 'abitdot-primary.sqlite'

    async def send_original(value):
        return await send_via_existing_relay(value, timeout_seconds=3610, stream=True)

    async with dispatch_slot():
        # A queued claim may wait a long time. Revalidate it at the actual
        # submission boundary, not just before entering the queue.
        from open_webui.utils.erp_task_resolution import task_revision, task_audit
        from open_webui.utils.erp_status_write import ERPStateChanged

        fresh = await erp.get('Task', task['name'])
        if (
            task_revision(fresh) != task_revision(task)
            or fresh.get('status') != task.get('status')
            or task_audit(fresh).get('run_id') != task_audit(task).get('run_id')
        ):
            raise ERPStateChanged('ERP task changed while queued; no Dot request was submitted')
        if fresh.get('custom_agentic_managed') and before_submit is None:
            from open_webui.utils.erp_status_write import ERPStateChanged

            raise ERPStateChanged('This governed Task requires its scoped project execution fence')
        if role == 'developer' and not await erp.assigned(fresh):
            raise ERPStateChanged('ERP developer assignment changed while queued; no Dot request was submitted')
        for attempt in range(20):
            if before_submit is not None:
                proof = await before_submit()
                if policy is not None:
                    from open_webui.utils.agentic_relay_scope import handoff_scope

                    envelope['governance_scope']['submission_proof'] = proof
                    envelope['governance_scope'] = handoff_scope(envelope, scope['account_id'])
            result = await deliver(envelope, send_original, path, timeout_seconds=3620)
            if result.get('state') != 'prepared':
                break
            # Only a verified pre-submission busy response permits retry.
            # No existing Dot execution is polled or replayed.
            await asyncio.sleep(3)
    if result.get('state') == 'unavailable':
        raise ValueError('The verified abitdot relay rejected this run before submission')
    if result.get('state') != 'delegated':
        return None
    records = read_owner_primary_runs(owner, automation_id, path, limit=50)
    record = next((row for row in records if row['original_run_id'] == run_id), None)
    if not record or record['status'] != 'delegated' or not record.get('response_text'):
        return None
    return record


async def adopt_proven_unsubmitted_start(erp, task, automation, run_id):
    """Repair an old failed status stamp only with proof it never reached Dot."""
    from open_webui.env import DATA_DIR
    from open_webui.utils.erp_task_resolution import DETAIL, RELAY_STATUS, task_audit
    from open_webui.utils.erp_status_write import ERPStateChanged

    audit = task_audit(task)
    previous_id = audit.get('run_id')
    if task.get('status') != 'Working' or not previous_id or previous_id == run_id:
        return
    root = Path(STATE_DIR or DATA_DIR)
    with sqlite3.connect('file:' + str(root / 'webui.db') + '?mode=ro', uri=True) as db:
        previous = db.execute(
            'SELECT automation_id,status,chat_id FROM automation_run WHERE id=?', (previous_id,)
        ).fetchone()
        active = db.execute(
            "SELECT id FROM automation_run WHERE automation_id=? AND status='running'", (automation.id,)
        ).fetchall()
    with sqlite3.connect('file:' + str(root / 'abitdot-primary.sqlite') + '?mode=ro', uri=True) as db:
        submitted = db.execute(
            'SELECT owner,status,response_text FROM dot_primary_runs WHERE automation_id=? AND original_run_id=?',
            (automation.id, previous_id),
        ).fetchone()
    meta = automation.meta or {}
    if meta.get('erp_continue_from') == previous_id:
        from open_webui.utils.erp_task_runtime import standard_continuation_current, standard_reported_outcome

        if (
            previous
            and previous[0] == automation.id
            and previous[1] == 'success'
            and previous[2]
            and active == [(run_id,)]
            and submitted
            and submitted[:2] == (automation.user_id, 'delegated')
            and standard_reported_outcome(submitted[2] or '', require_blocker_evidence=True) == 'continue'
            and await standard_continuation_current(automation, run_id)
        ):
            # This is a verified new unit of remaining work, not a replay of the
            # completed prior turn. The existing status contract stamps its ID.
            return
        raise ERPStateChanged('ERP continuation no longer matches its completed original turn; no replay permitted')
    if previous != (automation.id, 'error', None) or active != [(run_id,)] or submitted:
        raise ERPStateChanged('ERP task retains another run; its original delivery must be reconciled before execution')
    updated = {
        **audit,
        'run_id': run_id,
        'status': 'Working',
        'generation': meta.get('erp_generation'),
        'task_revision': meta.get('erp_task_revision'),
        'review_reason': 'Resumed the original queued execution after a verified pre-submission status failure.',
    }
    await erp.save(task, {DETAIL: json.dumps(updated), RELAY_STATUS: 'Running'})


async def save_dot_reply(automation, run_id, prompt, record):
    from open_webui.models.chats import ChatForm, Chats
    from open_webui.models.chat_messages import ChatMessages

    chat_id = str(uuid5(NAMESPACE_URL, f'erp-dot:{automation.user_id}:{automation.id}:{run_id}'))
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, automation.user_id)
    if chat is None:
        user_id = 'request-' + run_id
        model = 'abitdot'
        chat = await Chats.insert_new_chat(
            chat_id,
            automation.user_id,
            ChatForm(
                folder_id=automation.folder_id,
                chat={
                    'title': automation.name,
                    'models': [model],
                    'meta': {
                        'automation_id': automation.id,
                        'erp_dot': True,
                        'dot_conversation_url': record.get('conversation_url'),
                        'dot_root_message_id': record.get('root_message_id'),
                        'dot_reply_message_id': record.get('reply_message_id'),
                    },
                    'history': {
                        'currentId': run_id,
                        'messages': {
                            user_id: {
                                'id': user_id,
                                'role': 'user',
                                'content': prompt,
                                'parentId': None,
                                'childrenIds': [run_id],
                                'timestamp': int(time.time()),
                            },
                            run_id: {
                                'id': run_id,
                                'role': 'assistant',
                                'content': record['response_text'],
                                'done': True,
                                'model': model,
                                'parentId': user_id,
                                'childrenIds': [],
                                'timestamp': int(time.time()),
                            },
                        },
                    },
                },
            ),
        )
    message = await ChatMessages.get_message_by_id(f'{chat_id}-{run_id}')
    if (
        not chat
        or not message
        or message.user_id != automation.user_id
        or message.chat_id != chat_id
        or message.role != 'assistant'
        or message.done is not True
        or message.content != record['response_text']
    ):
        raise RuntimeError('The exact ERP Dot response did not pass saved-message readback')
    return chat_id


async def run_erp_primary_dot(app, automation, user, run_id):
    from open_webui.models.folders import Folders
    from open_webui.utils.automations import _record_run, _with_erp_task_context
    from open_webui.utils.erp_task_resolution import ERPClient, AGENT_URL, agent_url, task_revision
    from open_webui.utils.erp_task_runtime import sync_standard_status
    from open_webui.utils.task import prompt_template

    manual_run = run_id is None
    heartbeat = None
    if not run_id:
        # Manual Play also needs an atomic claim, rather than a random untracked
        # Dot submission which could overlap the scheduled occurrence.
        from sqlalchemy import select, update
        from uuid import uuid4
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import Automation, AutomationRun

        now = time.time_ns()
        async with get_async_db_context() as db:
            running = (
                select(AutomationRun.id)
                .where(AutomationRun.automation_id == automation.id, AutomationRun.status == 'running')
                .exists()
            )
            claimed = await db.execute(
                update(Automation)
                .where(
                    Automation.id == automation.id,
                    Automation.user_id == user.id,
                    Automation.updated_at == automation.updated_at,
                    (
                        Automation.next_run_at == automation.next_run_at
                        if automation.next_run_at is not None
                        else Automation.next_run_at.is_(None)
                    ),
                    ~running,
                )
                .values(updated_at=now, next_run_at=None)
            )
            if claimed.rowcount != 1:
                return True  # Existing scheduled/active work retains ownership.
            run_id = str(uuid4())
            db.add(
                AutomationRun(
                    id=run_id,
                    automation_id=automation.id,
                    status='running',
                    created_at=now,
                    lease_until=now + 600_000_000_000,
                )
            )
            await db.commit()
    config = json.loads(Path(os.environ['ABIT_ERP_TASK_RESOLUTION_CONFIG']).read_text())
    erp = ERPClient(config['erp_token_file'])
    if manual_run:
        from open_webui.models.automations import AutomationRuns

        async def keep_manual_claim():
            while True:
                await asyncio.sleep(60)
                if not await AutomationRuns.renew_claim(run_id):
                    return

        heartbeat = asyncio.create_task(keep_manual_claim())
    try:
        meta = automation.meta or {}
        task_id = meta['erp_task']
        task = await erp.get('Task', task_id)
        if task.get('custom_agentic_managed'):
            await _record_run(
                automation.id,
                'deferred',
                error='Task is owned by ERP project governance; no legacy submission.',
                scheduled_run_id=run_id,
            )
            return True
        if (
            task.get(AGENT_URL) != agent_url(automation.id)
            or meta.get('erp_url') != f'https://erp.abit.ai/desk/task/{task_id}'
            or task_revision(task) != meta.get('erp_task_revision')
            or not await erp.assigned(task)
            or task['status'] not in {'Open', 'Overdue', 'Working'}
        ):
            await _record_run(
                automation.id, 'deferred', error='ERP task ownership or input changed.', scheduled_run_id=run_id
            )
            return True
        project = None
        if automation.folder_id:
            folder = await Folders.get_folder_by_id_and_user_id(automation.folder_id, user.id)
            if folder is None:
                raise ValueError('ERP project does not belong to the agent owner')
            project = folder.model_dump()
        from open_webui.utils.erp_status_write import task_write_lock

        async with task_write_lock(task_id):
            task = await erp.get('Task', task_id)
            if task.get('custom_agentic_managed'):
                await _record_run(
                    automation.id,
                    'deferred',
                    error='Task moved to project governance before submission.',
                    scheduled_run_id=run_id,
                )
                return True
            await adopt_proven_unsubmitted_start(erp, task, automation, run_id)
            await sync_standard_status(
                erp, task_id, automation.id, run_id, 'running', meta.get('erp_status_sync'), meta, owner_id=user.id
            )
            task = await erp.get('Task', task_id)
        prompt = _with_erp_task_context(automation, await prompt_template(automation.data['prompt'], user))
        try:
            record = await dispatch_task_dot(erp, task, user.id, automation.id, run_id, prompt, 'developer', project)
        except ValueError as error:
            from open_webui.utils.erp_task_resolution import DETAIL, RELAY_STATUS, task_audit
            from open_webui.utils.erp_dot_workflow import create_issue_children

            fresh = await erp.get('Task', task_id)
            if (
                fresh['status'] != 'Working'
                or fresh.get(AGENT_URL) != agent_url(automation.id)
                or task_revision(fresh) != meta.get('erp_task_revision')
                or not await erp.assigned(fresh)
            ):
                raise
            summary = 'ERP abitdot execution prerequisite: ' + str(error)
            await erp.save(
                fresh,
                {
                    'status': 'Blocked',
                    RELAY_STATUS: 'Blocked',
                    DETAIL: json.dumps({**task_audit(fresh), 'status': 'Blocked', 'review_reason': summary}),
                },
            )
            await erp.add_run_comment(task_id, 'dot-prerequisite-' + run_id, summary, None)
            await create_issue_children(
                erp,
                await erp.get('Task', task_id),
                run_id,
                [
                    {
                        'subject': 'Restore task execution: ' + str(error)[:115],
                        'description': summary,
                        'acceptance_criteria': 'Verify the owner/account/project binding and relay readiness, '
                        'then resume the parent through its related ChatGPT Project.',
                    }
                ],
            )
            await _record_run(automation.id, 'error', error=summary, scheduled_run_id=run_id)
            return True
        if record is None:
            await erp.add_run_comment(
                task_id,
                'dot-delivery-' + run_id,
                'abitdot delivery is unresolved. The task remains Working; reconcile the '
                'existing Dot run before any retry to prevent duplicate execution.',
                None,
            )
            await _record_run(
                automation.id,
                'deferred',
                error='ERP abitdot delivery unresolved; do not replay.',
                scheduled_run_id=run_id,
            )
            return True
        chat_id = await save_dot_reply(automation, run_id, prompt, record)
        # This marks a returned model turn. The outcome contract still decides
        # Working / Blocked / Pending Review; it never treats a reply as completion.
        await _record_run(automation.id, 'success', chat_id=chat_id, scheduled_run_id=run_id)
        return True
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await erp.client.aclose()


async def read_qa_evidence(erp, task_id, run_id, file_ids):
    if not isinstance(file_ids, list) or len(file_ids) > 3:
        raise ValueError('QA must reference at most three ERP evidence files')
    images = []
    for file_id in dict.fromkeys(file_ids):
        if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}', file_id):
            raise ValueError('Invalid QA evidence file ID')
        file = await erp.get('File', file_id)
        url = file.get('file_url', '')
        if (
            file.get('attached_to_doctype') != 'Task'
            or file.get('attached_to_name') != task_id
            or not file.get('is_private')
            or not str(file.get('file_name', '')).startswith(f'qa-{run_id}-')
            or not re.fullmatch(r'/private/files/[^/?#\\]+\.png', url)
        ):
            raise ValueError('QA evidence does not match this task and review run')
        async with erp.client.stream('GET', url) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes(65536):
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError('QA screenshot exceeds the evidence limit')
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('QA evidence is not a PNG screenshot')
        images.append(bytes(data))
    return images


def read_local_qa_evidence(task_id, run_id, paths):
    """Read only run-scoped screenshot artifacts; ERP credentials stay server-side."""
    if not isinstance(paths, list) or len(paths) > 3:
        raise ValueError('QA must reference at most three local evidence screenshots')
    expected = QA_EVIDENCE_ROOT / task_id / run_id
    images = []
    for value in dict.fromkeys(paths):
        if not isinstance(value, str):
            raise ValueError('QA screenshot path must be a string')
        path = Path(value)
        if path.parent != expected or not path.name.startswith('qa-' + run_id + '-') or path.suffix != '.png':
            raise ValueError('Local screenshot does not belong to this task and review run')
        directories = []
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directories.append(os.open(QA_EVIDENCE_ROOT, flags))
            directories.append(os.open(task_id, flags, dir_fd=directories[-1]))
            directories.append(os.open(run_id, flags, dir_fd=directories[-1]))
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directories[-1])
            with os.fdopen(fd, 'rb') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                    raise ValueError('QA evidence must be a protected regular file')
                data = file.read(2_000_001)
        finally:
            for fd in reversed(directories):
                os.close(fd)
        if len(data) > 2_000_000 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('QA evidence must be a PNG screenshot below the size limit')
        images.append(data)
    return images


async def run_dot_review(erp, task, project, run_id, owner_email):
    from open_webui.models.folders import Folders
    from open_webui.models.users import Users
    from open_webui.utils.erp_task_runtime import standard_folder
    from open_webui.utils.erp_task_resolution import task_revision
    from open_webui.utils.erp_dot_workflow import parse_issues, parse_contract_json

    owner = await Users.get_user_by_email(owner_email)
    if not owner or owner.role != 'admin':
        raise ValueError('The configured ERP QA owner is unavailable')
    folder_id = await standard_folder(project, owner.id) if project else None
    folder = await Folders.get_folder_by_id_and_user_id(folder_id, owner.id) if folder_id else None
    identity = {'task': task['name'], 'revision': task_revision(task), 'run_id': run_id}
    prompt = (
        'Perform independent QA of this exact deliverable using the related ChatGPT Project. '
        'Use connected tools to test actual acceptance criteria, including browser workflows where applicable. '
        'Create real browser PNG screenshots under ' + str(QA_EVIDENCE_ROOT / task['name'] / run_id) + '/ '
        'with filenames qa-' + run_id + '-1.png etc. Return those absolute evidence_paths. '
        'Save PNGs with group mcpdev and mode 0640 so the QA service can read them; '
        'do not grant group or world write permission. '
        'ABiT AI will upload them privately using its dedicated QA credential; do not read or copy credentials. '
        'Do not change the task status or assignments. Return one plain-text line:\n'
        'ERP QA RESULT: '
        + json.dumps(
            {
                **identity,
                'status': 'pass|fail|blocked',
                'summary': 'tests performed and observed results',
                'evidence_paths': ['absolute run-scoped PNG path'],
            }
        )
        + '\n'
        'Include ERP ISSUES for each blocking issue or failed acceptance criterion. '
        'A missing test plan is work to investigate, not permission to approve. '
        'Use an independent internal review/delegation execution session with this exact review context. '
        'The relay uses a separate root request in the owner Dot feed; a new visible DM URL is not required. '
        'The server handles private attachment after your result; do not block on its not-yet-created ERP File ID.'
    )
    try:
        record = await dispatch_task_dot(
            erp, task, owner.id, 'erp-qa-' + task['name'], run_id, prompt, 'qa', folder.model_dump() if folder else None
        )
    except ValueError as error:
        summary = 'QA abitdot prerequisite failed: ' + str(error)
        return {'status': 'blocked', 'summary': summary, 'screenshots': [], 'issues': parse_issues('', summary)}
    if record is None:
        # Leave the review claim unresolved. The next reconciliation uses the same
        # durable run, so a lost reply cannot create a second QA conversation.
        raise RuntimeError('QA abitdot delivery is unresolved; preserve the existing review claim')
    return await decode_saved_dot_review(erp, task, run_id, record['response_text'])


async def decode_saved_dot_review(erp, task, run_id, response_text):
    """Validate a saved original verdict/evidence without any relay submission."""
    from open_webui.utils.erp_task_resolution import task_revision
    from open_webui.utils.erp_dot_workflow import parse_issues, parse_contract_json

    identity = {'task': task['name'], 'revision': task_revision(task), 'run_id': run_id}
    try:
        rows = [
            line[len('ERP QA RESULT:') :].strip()
            for line in response_text.splitlines()
            if line.startswith('ERP QA RESULT:')
        ]
        if len(rows) != 1:
            raise ValueError('QA did not return exactly one result bound to this review')
        result = parse_contract_json(rows[0])
        if (
            not isinstance(result, dict)
            or any(result.get(k) != v for k, v in identity.items())
            or result.get('status') not in {'pass', 'fail', 'blocked'}
            or not isinstance(result.get('summary'), str)
            or not result['summary'].strip()
        ):
            raise ValueError('QA result did not match the task, input revision and run')
        result['issues'] = parse_issues(
            response_text, result['summary'] if result['status'] in {'fail', 'blocked'} else None
        )
        result['screenshots'] = (
            read_local_qa_evidence(task['name'], run_id, result['evidence_paths'])
            if result.get('evidence_paths')
            else await read_qa_evidence(erp, task['name'], run_id, result.get('evidence_files', []))
        )
    except (ValueError, TypeError, OSError) as error:
        summary = 'QA report verification failed: ' + str(error)
        return {'status': 'blocked', 'summary': summary, 'screenshots': [], 'issues': parse_issues('', summary)}
    if result['status'] in {'pass', 'fail'} and not result['screenshots']:
        result.update(status='blocked', summary='QA verdict lacks verified screenshot evidence. ' + result['summary'])
    return result

"""Reconcile explicit ERP task assignments to the independent QA agent."""
from open_webui.utils import agentic_maintenance as _agov_maintenance

from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from open_webui.utils.erp_pending_review_qa import ReviewStore, TASK_ID, normalize_outcome, requires_delivery
from open_webui.utils.erp_task_resolution import ERP, task_revision

log = logging.getLogger(__name__)
QA_USER = 'ai-qa@abitcons.com'
REVIEWING = 'QA Reviewing'
APPROVED = 'QA APPROVED'
REJECTED = 'QA REJECTED'
STATUS_OPTIONS = frozenset({REVIEWING, APPROVED, REJECTED})
ATTEMPT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
ERP_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?')


def assigned_review_id(task, todo_id, attempt=None):
    scope = f"{ERP}/assigned-qa/{task['name']}/{todo_id}/{task_revision(task)}"
    if attempt is not None:
        scope += f'/attempt/{attempt}'
    return str(uuid5(NAMESPACE_URL, scope))


def is_qa_todo(todo, task_id):
    return (todo.get('allocated_to') == QA_USER and todo.get('status') == 'Open'
            and todo.get('reference_type') == 'Task' and todo.get('reference_name') == task_id)


async def assignment_current(erp, task_id, todo_id):
    result = await erp.request('GET', '/api/method/frappe.client.get_value', params={
        'doctype': 'Task', 'filters': task_id, 'fieldname': '_assign',
    })
    assigned = json.loads((result.get('message') or {}).get('_assign') or '[]')
    if not isinstance(assigned, list) or QA_USER not in assigned:
        return False
    todo = await erp.get('ToDo', todo_id)
    return is_qa_todo(todo, task_id)


def task_current(task, status):
    if task.get('custom_agentic_managed'):
        return False
    from open_webui.utils.erp_dot_workflow import enabled
    from open_webui.utils.erp_task_resolution import task_audit
    group_ok = not task.get('is_group') or enabled() and task_audit(task).get('issue_parent')
    return (task.get('status') == status and group_ok and not task.get('is_template')
            and isinstance(task.get('modified'), str) and bool(task['modified']))


@_agov_maintenance.legacy_qa
async def review_assigned_task(erp, config, store, todo, policy, run_assigned_review, publish_review):
    task_id, todo_id = todo['reference_name'], todo['name']
    task = await erp.get('Task', task_id)
    attempt = policy.get('attempt') if isinstance(policy, dict) else None
    if attempt is not None and (not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt)):
        raise ValueError('Invalid assigned QA plan attempt')
    run_id = assigned_review_id(task, todo_id, attempt)
    record = store.get(task_id, run_id)
    if record and record[1] == 'done':
        return
    if not (task_current(task, 'Pending Review') or task_current(task, REVIEWING)):
        if record:
            store.settle(task_id, run_id)
        return
    if not await assignment_current(erp, task_id, todo_id):
        if record:
            store.settle(task_id, run_id)
        return
    if task['status'] == 'Pending Review':
        await erp.save(task, {'status': REVIEWING})
        task = await erp.get('Task', task_id)
        if (not task_current(task, REVIEWING) or assigned_review_id(task, todo_id, attempt) != run_id
                or not await assignment_current(erp, task_id, todo_id)):
            return
    from open_webui.utils.erp_dot_workflow import enabled, executable_group
    if enabled():
        if not await executable_group(erp, task):
            return
        policy = {'dot': True}
    if not isinstance(policy, dict) or not policy:
        return

    project = await erp.get('Project', task['project']) if task.get('project') else None
    project_modified = (project or {}).get('modified')
    binding = hashlib.sha256(json.dumps(
        [task['modified'], project_modified, policy], sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    if record:
        if record[0] != binding:
            store.settle(task_id, run_id)
            return
        if record[1] == 'testing' and not enabled():
            store.finish_test(task_id, run_id, {
                'status': 'blocked', 'summary': 'QA was interrupted before the result could be verified.',
                'screenshots': [],
            })
    else:
        if not store.claim(task_id, run_id, binding):
            return
    if not record or (record[1] == 'testing' and enabled()):
        if enabled():
            from open_webui.utils.erp_dot_execution import run_dot_review
            reviewed = await run_dot_review(erp, task, project, run_id, config['owner_email'])
        else:
            reviewed = await run_assigned_review(task, project, run_id, policy)
        outcome = normalize_outcome(reviewed)
        if outcome['status'] == 'fail' and not outcome['screenshots']:
            outcome = {**outcome, 'status': 'blocked',
                       'summary': 'QA reported a failure without a verified screenshot. ' + outcome['summary']}
        store.finish_test(task_id, run_id, outcome)

    _, _, outcome = store.get(task_id, run_id)
    fresh = await erp.get('Task', task_id)
    if (not task_current(fresh, REVIEWING) or fresh['modified'] != task['modified']
            or assigned_review_id(fresh, todo_id, attempt) != run_id or not await assignment_current(erp, task_id, todo_id)):
        store.settle(task_id, run_id)
        return
    if outcome['status'] == 'pass' and requires_delivery(fresh, config):
        outcome = {**outcome, 'status': 'blocked',
                   'summary': 'QA passed, but independent DEV delivery evidence is still required. ' + outcome['summary']}
    uploaded = await publish_review(erp, task_id, run_id, outcome)
    if outcome['status'] in {'pass', 'fail'}:
        if type(uploaded) is not int or uploaded < 1 or uploaded != len(set(outcome['screenshots'])):
            raise RuntimeError('ERP has not confirmed every QA screenshot')
        fresh = await erp.get('Task', task_id)
        if (task_current(fresh, REVIEWING) and fresh['modified'] == task['modified']
                and assigned_review_id(fresh, todo_id, attempt) == run_id and await assignment_current(erp, task_id, todo_id)):
            current_project = await erp.get('Project', fresh['project']) if fresh.get('project') else None
            if ((current_project or {}).get('modified') == project_modified
                    and (outcome['status'] == 'fail' or not requires_delivery(fresh, config))):
                status = APPROVED if outcome['status'] == 'pass' else REJECTED
                await erp.save(fresh, {'status': status})
    if enabled():
        from open_webui.utils.erp_dot_workflow import create_issue_children, parse_issues, resume_parent, close_assignment
        from open_webui.utils.erp_task_resolution import ERPClient, Store
        # QA publishes verdicts with its own identity. Repair creation and
        # developer assignment use the existing developer service credential.
        repair_erp = ERPClient(config['erp_token_file'])
        try:
            fresh = await repair_erp.get('Task', task_id)
            if fresh.get('status') in {REVIEWING, REJECTED} and outcome['status'] in {'fail', 'blocked'}:
                issues = outcome.get('issues') or parse_issues('', outcome['summary'])
                await create_issue_children(repair_erp, fresh, run_id, issues)
                await close_assignment(erp, task_id, QA_USER)
            elif fresh.get('status') == APPROVED:
                await close_assignment(erp, task_id, QA_USER)
                await resume_parent(repair_erp, fresh, Store(Path(store.path).with_name('erp-task-resolution.sqlite')))
        finally:
            await repair_erp.client.aclose()
    store.settle(task_id, run_id)


def erp_timestamp(value):
    if not isinstance(value, str) or not ERP_TIMESTAMP.fullmatch(value):
        raise ValueError('Expected an ERP ToDo creation timestamp')
    return datetime.fromisoformat(value)


async def reconcile_assigned_qa(resolver, config, run_assigned_review):
    settings = config.get('pending_review_qa')
    if not isinstance(settings, dict):
        return
    policies = settings.get('assigned_tasks', {})
    if not isinstance(policies, dict):
        return
    raw_since = settings.get('assignment_since')
    since = erp_timestamp(raw_since) if raw_since is not None else None

    from open_webui.utils.erp_review_evidence import publish_review

    erp = resolver.erp
    fields = ['name', 'allocated_to', 'status', 'reference_type', 'reference_name', 'creation']
    filters = [['allocated_to', '=', QA_USER], ['status', '=', 'Open'], ['reference_type', '=', 'Task']]
    store = ReviewStore(Path(resolver.store.path).with_name('erp-pending-review-qa.sqlite'))
    seen = set()
    todos = [todo async for todo in erp.rows('ToDo', fields, filters)]
    priority = settings.get('priority_tasks', [])
    priority = priority if isinstance(priority, list) else []
    todos.sort(key=lambda todo: (todo.get('reference_name') not in priority,
                               priority.index(todo.get('reference_name')) if todo.get('reference_name') in priority else 0))
    for todo in todos:
        task_id = todo.get('reference_name')
        selected = settings.get('only_tasks')
        if selected is not None and task_id not in selected:
            continue
        if (not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id)
                or not isinstance(todo.get('name'), str) or not todo['name']
                or task_id in seen or not is_qa_todo(todo, task_id)):
            continue
        from open_webui.utils.erp_dot_workflow import enabled, task_enabled
        if enabled() and not task_enabled(task_id):
            continue
        if not enabled() and since is not None and task_id not in policies:
            try:
                if erp_timestamp(todo.get('creation')) < since:
                    continue
            except ValueError:
                continue
        seen.add(task_id)
        try:
            await review_assigned_task(erp, config, store, todo, policies.get(task_id),
                                       run_assigned_review, publish_review)
        except Exception as exc:
            log.warning('ERP assigned QA for %s will reconcile: %s', task_id, type(exc).__name__)

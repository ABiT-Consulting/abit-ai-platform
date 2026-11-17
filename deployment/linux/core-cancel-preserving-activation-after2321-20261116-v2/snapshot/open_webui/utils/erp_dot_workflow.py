"""ERP task context, repair children and event-driven developer/QA handoffs.

All writes use the existing ERP client and its timestamp/readback guards.
An uncertain child creation is reconciled by its marker, never blindly retried.
No service, timer, Dot poller, credentials, or alternate ERP endpoint is added.
"""
from contextlib import contextmanager
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import sqlite3

from open_webui.utils.erp_task_resolution import (
    ASSIGNEE, DETAIL, ERP, RELAY_STATUS, task_audit, task_revision,
)

QA_USER = 'ai-qa@abitcons.com'
DONE = {'Completed', 'QA APPROVED'}
TASK_ID = re.compile(r'TASK-[A-Za-z0-9-]{1,64}')
MAX_ISSUES = 5
MAX_DEPTH = 2
STATE_DIR = None

POLICY = """Use this run's separate execution request and context to resolve the exact ERP task.
Open the related ChatGPT Project when project instructions, files or context are
needed. Use its verified project link; never substitute another project. Read
the task, parent, children, dependencies, Activity and latest checkpoint first.
Execute and verify the actual deliverable through the authorized connected tools.
For development, finish implementation, DEV delivery and acceptance checks.
For QA, use an independent review/delegation session to test the developer's deliverable;
do not approve based on the developer's claim. Report concrete test evidence.
Keep working while an authorized next action exists. Identify each distinct
blocking issue with a measurable repair outcome and acceptance criteria. The
server creates or reuses a child under this task and assigns it to AI Developer.
Do not create duplicate children yourself. A parent waits for required children
and their QA, then resumes its own acceptance checks. Never close a parent solely
because its children passed. Access or approval blockers must name the external
owner and required action; assigning a repair does not grant that permission.
Return a single line before the final ERP RESULT line:
ERP ISSUES: [{"subject":"specific repair outcome","description":"findings and attempts","acceptance_criteria":"observable verification"}]
Use an empty array when there are no blocking issues. Do not create generic
copies of the parent. At most five issues and two new child levels per branch.
"""


def task_enabled(task_id):
    scope = {v.strip() for v in os.getenv('ABIT_ERP_DOT_WORKFLOW_TASKS', '').split(',') if v.strip()}
    return enabled() and (not scope or task_id in scope)


def enabled():
    return os.getenv('ABIT_ERP_DOT_WORKFLOW_ENABLED', 'false').lower() == 'true'


@contextmanager
def issue_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS issue (
        id TEXT PRIMARY KEY, parent TEXT NOT NULL, revision TEXT NOT NULL,
        source_run TEXT NOT NULL, spec TEXT NOT NULL,
        phase TEXT NOT NULL DEFAULT 'prepared', child TEXT
    )''')
    try:
        yield db
        db.commit()
    finally:
        db.close()


def issue_path():
    from open_webui.env import DATA_DIR
    return Path(STATE_DIR or DATA_DIR) / 'erp-dot-issues.sqlite'


def parse_contract_json(value):
    """Decode JSON first; repair only invalid ASCII Markdown punctuation escapes.

    Pairwise scanning preserves JSON escapes (including literal backslashes) in
    mixed payloads. Unknown alphabetic escapes remain invalid JSON.
    """
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        repaired = []
        index = 0
        while index < len(value):
            char = value[index]
            if char == "\\" and index + 1 < len(value):
                following = value[index + 1]
                if following in '"\\/bfnrtu':
                    repaired.append(value[index:index + 2])
                elif following in "!#$%&'()*+,-.:;<=>?@[]^_`{|}~":
                    repaired.append(following)
                else:
                    repaired.append(value[index:index + 2])
                index += 2
            else:
                repaired.append(char)
                index += 1
        return json.loads(''.join(repaired))


def parse_issues(report, summary=None):
    rows = [line[len('ERP ISSUES:'):].strip() for line in report.splitlines()
            if line.startswith('ERP ISSUES:')]
    if len(rows) > 1:
        raise ValueError('Multiple ERP issue reports')
    issues = parse_contract_json(rows[0]) if rows else []
    if not isinstance(issues, list) or len(issues) > MAX_ISSUES:
        raise ValueError('ERP issue report must contain at most five repairs')
    result = []
    for spec in issues:
        if not isinstance(spec, dict):
            raise ValueError('Invalid ERP issue')
        clean = {}
        for name, limit in [('subject', 140), ('description', 4000), ('acceptance_criteria', 2000)]:
            value = spec.get(name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError('Each repair needs a subject, findings and acceptance criteria')
            clean[name] = value.strip()
        if clean not in result:
            result.append(clean)
    if not result and summary:
        # Preserve an actual observed blocker if an older agent omits the new field.
        result = [{'subject': 'Resolve blocker: ' + ' '.join(summary.split())[:115],
                   'description': summary[:4000],
                   'acceptance_criteria': 'Verify the reported blocker is resolved, record evidence, '
                                          'and rerun the parent acceptance checks.'}]
    return result


async def task_context(erp, task, role):
    project = await erp.get('Project', task['project']) if task.get('project') else {}
    info = await erp.request('GET', '/api/method/frappe.desk.form.load.get_docinfo',
                             params={'doctype': 'Task', 'name': task['name']})
    timeline = info.get('docinfo') or {}
    children = []
    async for row in erp.rows('Task', ['name', 'subject', 'status', 'parent_task'],
                             [['parent_task', '=', task['name']]]):
        if len(children) >= 100:
            raise ValueError('Task tree exceeds the context limit; inspect it through ERP tools')
        children.append(row)
    fields = ('name', 'subject', 'description', 'status', 'parent_task', 'depends_on',
              'custom_acceptance_criteria', 'custom_chatgpt_project', 'custom_chatgpt_project_id')
    context = {'role': role, 'task': {k: task.get(k) for k in fields}, 'children': children,
               'erp_task_url': f"{ERP}/desk/task/{task['name']}",
               'project': {k: project.get(k) for k in (
                   'name', 'project_name', 'custom_chatgpt_chat_url', 'custom_ai_developer_instructions',
                   'custom_github_repository', 'custom_ai_agent_environment',
                   'custom_ai_agent_development_folder', 'custom_ai_agent_working_branch')},
               'latest_run': task_audit(task)}
    context['activity'] = [{key: row.get(key) for key in ('name', 'owner', 'creation', 'content')}
                           for row in sorted(timeline.get('comments') or [],
                                             key=lambda row: str(row.get('creation', '')))[-6:]]
    for row in context['activity']:
        row['content'] = str(row.get('content') or '')[:6000]
    context['evidence_files'] = [{key: row.get(key) for key in ('name', 'file_name', 'file_url', 'is_private')}
                                  for row in (timeline.get('attachments') or [])[-12:]]
    if task.get('parent_task'):
        parent = await erp.get('Task', task['parent_task'])
        if parent.get('project') != task.get('project'):
            raise ValueError('Parent project does not match task project')
        context['parent'] = {k: parent.get(k) for k in fields}
    return context


async def ensure_assignment(erp, task_id, user):
    async def present():
        async for row in erp.rows('ToDo', ['name'], [
            ['reference_type', '=', 'Task'], ['reference_name', '=', task_id],
            ['allocated_to', '=', user], ['status', '=', 'Open'],
        ]):
            return True
        return False
    if not await present():
        await erp.request('POST', '/api/method/frappe.desk.form.assign_to.add', json={
            'doctype': 'Task', 'name': task_id, 'assign_to': json.dumps([user]),
            'description': 'Resolve and verify the assigned ERP task through abitdot.',
            'notify': 0,
        })
    if not await present():
        raise RuntimeError('ERP did not confirm the task assignment')


async def close_assignment(erp, task_id, user):
    await erp.request('POST', '/api/method/frappe.desk.form.assign_to.remove', json={
        'doctype': 'Task', 'name': task_id, 'assign_to': user,
    })
    async for _ in erp.rows('ToDo', ['name'], [
        ['reference_type', '=', 'Task'], ['reference_name', '=', task_id],
        ['allocated_to', '=', user], ['status', '=', 'Open'],
    ]):
        raise RuntimeError('ERP did not confirm release of the completed QA assignment')


async def children_complete(erp, task):
    found = False
    async for child in erp.rows('Task', ['name', 'status'], [['parent_task', '=', task['name']]]):
        found = True
        if child.get('status') not in DONE:
            return False
    return found


async def executable_group(erp, task):
    return (not task.get('is_group') or
            bool(task_audit(task).get('issue_parent') and await children_complete(erp, task)))


async def create_issue_children(erp, task, run_id, issues, path=None):
    if not issues:
        return []
    if task.get('status') not in {'Blocked', 'QA REJECTED', 'QA Reviewing'}:
        return []
    # Descendants created by this workflow carry a durable depth marker.
    depth = task_audit(task).get('issue_depth', 0)
    if type(depth) is not int or depth < 0 or depth >= MAX_DEPTH:
        await erp.add_run_comment(task['name'], 'issue-depth-' + run_id,
                                  'Repair depth limit reached. Keep this task open and resolve its '
                                  'recorded blocker before further breakdown.', None)
        return []
    path = path or issue_path()
    children = []
    revision = task_revision(task)
    for spec in issues[:MAX_ISSUES]:
        if spec['subject'].casefold() == str(task.get('subject', '')).strip().casefold():
            raise ValueError('A repair child cannot repeat its parent')
        key = hashlib.sha256(json.dumps([task['name'], revision, spec['subject'].casefold()], sort_keys=True).encode()).hexdigest()
        marker = 'ABIT-ISSUE:' + key
        with issue_db(path) as db:
            db.execute('INSERT OR IGNORE INTO issue(id,parent,revision,source_run,spec) VALUES(?,?,?,?,?)',
                       (key, task['name'], revision, run_id, json.dumps(spec)))
            record = dict(db.execute('SELECT * FROM issue WHERE id=?', (key,)).fetchone())
        child = None
        async for candidate in erp.rows('Task', ['name', 'project', 'parent_task', 'description'],
                                        [['parent_task', '=', task['name']], ['description', 'like', '%' + marker + '%']]):
            if (candidate.get('project') == task.get('project') and
                    candidate.get('parent_task') == task['name'] and marker in candidate.get('description', '')):
                child = candidate['name']
                break
        if not child and record['phase'] == 'prepared':
            fresh = await erp.get('Task', task['name'])
            if task_revision(fresh) != revision or fresh['status'] != task['status']:
                raise RuntimeError('Parent changed before repair creation')
            await erp.save(fresh, {'is_group': 1, DETAIL: json.dumps({
                **task_audit(fresh), 'issue_parent': True, 'issue_depth': depth,
            })})
            description = '<p>' + escape(marker) + '</p><p>' + escape(spec['description']) + '</p>'
            description += '<p><strong>Acceptance criteria</strong><br>' + escape(spec['acceptance_criteria']) + '</p>'
            description += '<p>Source run: ' + escape(run_id) + '</p>'
            with issue_db(path) as db:
                claimed = db.execute("UPDATE issue SET phase='posting' WHERE id=? AND phase='prepared'", (key,)).rowcount
            if claimed:
                payload = {'subject': spec['subject'], 'description': description,
                           'parent_task': task['name'], 'project': task.get('project'), 'status': 'Open',
                           DETAIL: json.dumps({'issue_depth': depth + 1, 'issue_source': key})}
                for field in ('company', 'type', 'custom_chatgpt_project', 'custom_chatgpt_project_id'):
                    if task.get(field):
                        payload[field] = task[field]
                response = await erp.request('POST', '/api/resource/Task', json=payload)
                child = response.get('data', {}).get('name')
                if not isinstance(child, str) or not TASK_ID.fullmatch(child):
                    raise RuntimeError('ERP did not return the saved repair child')
        if not child:
            # A request may already have committed. Leave it for exact-marker readback.
            raise RuntimeError('Repair submission is uncertain; reconcile its marker before retrying')
        saved = await erp.get('Task', child)
        if (saved.get('parent_task') != task['name'] or saved.get('project') != task.get('project')
                or marker not in saved.get('description', '')):
            raise RuntimeError('Repair child failed ERP readback')
        await ensure_assignment(erp, child, ASSIGNEE)
        with issue_db(path) as db:
            db.execute("UPDATE issue SET phase='assigned', child=? WHERE id=?", (child, key))
        await erp.add_run_comment(task['name'], 'issue-' + key,
                                  f"Repair child {child} assigned to {ASSIGNEE}.\n{ERP}/desk/task/{child}", None)
        children.append(child)
    return children


async def developer_result(erp, task, run_id, report):
    if not enabled():
        return
    if task['status'] == 'Pending Review':
        await ensure_assignment(erp, task['name'], QA_USER)
    elif task['status'] == 'Blocked':
        summary = next((line[8:].strip() for line in report.splitlines() if line.startswith('Blocker:')), None)
        issues = parse_issues(report, summary or 'Investigate the external prerequisite reported by the developer.')
        await create_issue_children(erp, task, run_id, issues)


async def reconcile_repairs(erp):
    """Recover only already recorded writes using the existing ERP scheduler.

    This never submits to or polls Dot. In particular, a lost HTTP response
    leaves a posting claim whose marker must be found before any assignment.
    """
    if not enabled() or not issue_path().is_file():
        return
    with issue_db(issue_path()) as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM issue WHERE phase IN ('prepared','posting') LIMIT 25")]
    for row in rows:
        parent = await erp.get('Task', row['parent'])
        if task_revision(parent) != row['revision']:
            continue
        await create_issue_children(erp, parent, row['source_run'], [json.loads(row['spec'])])


async def resume_parent(erp, child, queue):
    if child.get('status') not in DONE or not child.get('parent_task'):
        return
    parent = await erp.get('Task', child['parent_task'])
    audit = task_audit(parent)
    if (not audit.get('issue_parent') or parent.get('project') != child.get('project')
            or parent.get('status') not in {'Blocked', 'QA REJECTED', 'QA Reviewing'}
            or not await children_complete(erp, parent)):
        return
    await ensure_assignment(erp, parent['name'], ASSIGNEE)
    await erp.save(parent, {'status': 'Open', RELAY_STATUS: 'Not Started'})
    queue.enqueue(parent['name'])
    await erp.add_run_comment(parent['name'], 'resume-' + str(audit.get('run_id', child['name'])),
                              'Required repair children passed QA. Resume this task and verify its own acceptance criteria.', None)

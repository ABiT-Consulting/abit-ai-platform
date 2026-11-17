"""Bounded project execution and deterministic native backlog pickup.

The lane is acquired in the existing SQLite claim transaction, never by a model
or status read. ERP owns enrollment and emits the original signed Start event.
"""
from __future__ import annotations
import json
import time


def _exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def concurrency(packet):
    """Only the native server policy may raise the default single-task limit."""
    policy = packet.get('project_execution_policy', {'max_concurrency': 1})
    if (not isinstance(policy, dict) or set(policy) != {'max_concurrency'}
            or type(policy['max_concurrency']) is not int
            or not 1 <= policy['max_concurrency'] <= 8):
        raise ValueError('Invalid native project execution concurrency policy')
    return policy['max_concurrency']


def _schema(db):
    if _exists(db, 'governance_project_lane'):
        columns = db.execute('PRAGMA table_info(governance_project_lane)').fetchall()
        if [row[1] for row in columns if row[5]] == ['project']:
            # Migrate existing single-task ownership in the caller's claim
            # transaction; no invocation, lease or execution history is reset.
            db.execute('ALTER TABLE governance_project_lane RENAME TO governance_project_lane_v1')
            db.execute("""CREATE TABLE governance_project_lane(
                project TEXT NOT NULL,task TEXT NOT NULL,invocation_id TEXT NOT NULL,
                updated_at INTEGER NOT NULL,PRIMARY KEY(project,task))""")
            db.execute('INSERT INTO governance_project_lane SELECT * FROM governance_project_lane_v1')
            db.execute('DROP TABLE governance_project_lane_v1')
    db.execute("""CREATE TABLE IF NOT EXISTS governance_project_lane(
        project TEXT NOT NULL,task TEXT NOT NULL,invocation_id TEXT NOT NULL,
        updated_at INTEGER NOT NULL,PRIMARY KEY(project,task))""")


def validate_graph(rows):
    """Validate the complete native dependency/hierarchy graph before pickup."""
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('Project dependency graph is missing or exceeds 500 Tasks')
    if any(not isinstance(row, dict) or not isinstance(row.get('task_id'), str) for row in rows):
        raise ValueError('Project dependency graph has an invalid Task identity')
    graph = {row['task_id']: set() for row in rows}
    if len(graph) != len(rows):
        raise ValueError('Project dependency graph has duplicate Task identities')
    for row in rows:
        ident, dependencies = row['task_id'], row.get('dependencies')
        if (not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies)
                or len(dependencies) != len(set(dependencies))):
            raise ValueError('Project Task has invalid dependency data: ' + ident)
        missing = set(dependencies) - set(graph)
        if missing:
            raise ValueError('Project dependency is missing or outside this project: ' + sorted(missing)[0])
        graph[ident].update(dependencies)
        parent = row.get('parent_task') or ''
        if not isinstance(parent, str) or parent and parent not in graph:
            raise ValueError('Project parent is missing or outside this project: ' + ident)
        if parent:
            graph[parent].add(ident)  # Parent acceptance waits for every direct child.
    visited, visiting = set(), set()
    def visit(ident):
        if ident in visiting:
            raise ValueError('Project dependency/hierarchy cycle contains Task: ' + ident)
        if ident in visited:
            return
        visiting.add(ident)
        for dep in sorted(graph[ident]):
            visit(dep)
        visiting.remove(ident)
        visited.add(ident)
    for ident in sorted(graph):
        visit(ident)
    return graph


def _published_human_blocker(db, row, *, review=False, paused=False):
    if not _exists(db, 'governance_result'):
        return False
    saved = db.execute("SELECT * FROM governance_result WHERE invocation_id=? AND phase='published'",
                       (row['invocation_id'],)).fetchone()
    if not saved or saved['run_id'] != row['automation_run_id']:
        return False
    if (_exists(db, 'governance_gateway_session') and _exists(db, 'governance_gateway_call')
            and db.execute(
                "SELECT 1 FROM governance_gateway_call c JOIN governance_gateway_session s "
                "ON s.session_id=c.session_id WHERE s.invocation_id=? AND c.state!='completed' LIMIT 1",
                (row['invocation_id'],)).fetchone()):
        return False  # Published model blocker cannot settle a possible tool side effect.
    if (_exists(db, 'governance_action') and db.execute(
            "SELECT 1 FROM governance_action WHERE invocation_id=? AND state='uncertain' LIMIT 1",
            (row['invocation_id'],)).fetchone()):
        return False
    try:
        result, event, receipt = (json.loads(saved[key]) for key in
                                  ('result_json', 'event_json', 'receipt_json'))
    except (ValueError, TypeError):
        return False
    from open_webui.utils import agentic_governance as ledger
    return bool((paused or result.get('outcome') == 'blocked')
        and result.get('invocation_id') == row['invocation_id']
        and saved['result_sha256'] == ledger._digest(result)
        and event.get('result_contract') == result
        and all(event.get(key) == value for key, value in (
            ('invocation_id', row['invocation_id']), ('project_id', row['erp_project']),
            ('task_id', row['erp_task']), ('run_id', row['automation_run_id'])))
        and (event.get('type') in {'run.ready','run.blocked','run.checkpoint','qa.passed','qa.failed'}
             if paused else event.get('type') == ('pm.review.checkpoint' if review else 'run.blocked'))
        and receipt.get('durable') is True
        and receipt.get('task_id') == row['erp_task']
        and receipt.get('run_id') == row['automation_run_id']
        and receipt.get('event_id') == event.get('event_id')
        and (receipt.get('pm_review_state') == 'held' and receipt.get('status') == 'QA APPROVED'
             if review else bool(receipt.get('status')) if paused else receipt.get('status') == 'Blocked'))


def _paused_finished(db, row):
    if not _exists(db, 'governance_task_control'):
        return False
    control = db.execute('SELECT state FROM governance_task_control WHERE project=? AND task=?',
                         (row['erp_project'], row['erp_task'])).fetchone()
    return bool(control and control[0] in {'paused','stopped'}
                and _published_human_blocker(db, row, paused=True))


def _live_tasks(db, project):
    waiting = set()
    if _exists(db, 'governance_lifecycle_wait'):
        waiting = {r[0] for r in db.execute(
            "SELECT source FROM governance_lifecycle_wait WHERE project=? AND phase='waiting'", (project,))}
    return {r['erp_task'] for r in db.execute(
        """SELECT * FROM governance_invocation WHERE erp_project=?
        AND state IN ('running','uncertain','cancel_requested','execution_finished','awaiting_approval')""",
        (project,)) if r['state'] != 'execution_finished' or (r['invocation_id'] not in waiting
            and not _published_human_blocker(db, r) and not _paused_finished(db, r))}


def _pending_review_tasks(db, project):
    if not _exists(db, 'governance_pm_review'):
        return {r[0] for r in db.execute(
            """SELECT DISTINCT i.erp_task FROM governance_verification v
            JOIN governance_invocation i ON i.invocation_id=v.invocation_id
            WHERE i.erp_project=?""", (project,))}
    pending = set()
    for entry in db.execute(
        """SELECT i.erp_task,r.phase,r.review_invocation_id FROM governance_verification v
        JOIN governance_invocation i ON i.invocation_id=v.invocation_id
        LEFT JOIN governance_pm_review r ON r.qa_invocation_id=v.qa_invocation_id
        WHERE i.erp_project=? AND COALESCE(r.phase,'')!='completed'
        AND v.rowid=(SELECT MAX(v2.rowid) FROM governance_verification v2
          JOIN governance_invocation i2 ON i2.invocation_id=v2.invocation_id
          WHERE i2.erp_project=i.erp_project AND i2.erp_task=i.erp_task)""", (project,)):
        review = (db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',
                             (entry['review_invocation_id'],)).fetchone()
                  if entry['phase'] == 'held' else None)
        if review and _published_human_blocker(db, review, review=True):
            continue  # Only unrelated work may proceed; this prerequisite is not complete.
        pending.add(entry['erp_task'])
    return pending


def _queued(db, project):
    from open_webui.utils import agentic_governance as ledger
    result = []
    for row in db.execute(
        """SELECT * FROM governance_invocation WHERE erp_project=? AND state='queued'
        AND submitted_at IS NULL ORDER BY
        CASE WHEN source_invocation_id IS NOT NULL THEN 0 ELSE 1 END,created_at,invocation_id""", (project,)):
        try:
            ledger._task_gate(db, project, row['erp_task'])
        except ValueError:
            continue
        result.append(row)
    def order(row):
        packet = json.loads(row['work_packet'] or '{}')
        native = next((entry for entry in packet.get('project_backlog', {}).get('tasks', [])
                       if entry.get('task_id') == row['erp_task']), {})
        return (0 if row['source_invocation_id'] else 1,
                {'Urgent': 0, 'High': 1, 'Medium': 2, 'Low': 3}.get(native.get('priority'), 2),
                native.get('due_date') or '9999', row['created_at'], row['invocation_id'])
    return sorted(result, key=order)


def admit(db, row):
    """Acquire one Task slot for its entire lifecycle under BEGIN IMMEDIATE."""
    _schema(db)
    project, task = row['erp_project'], row['erp_task']
    maximum = concurrency(json.loads(row['work_packet']))
    live = _live_tasks(db, project)
    pending = _pending_review_tasks(db, project)
    queued = _queued(db, project)
    retained = live | pending | {r['erp_task'] for r in queued}
    for lane in db.execute('SELECT task FROM governance_project_lane WHERE project=?', (project,)).fetchall():
        if lane['task'] not in retained:
            db.execute('DELETE FROM governance_project_lane WHERE project=? AND task=?', (project, lane['task']))
    lanes = {r[0] for r in db.execute('SELECT task FROM governance_project_lane WHERE project=?', (project,))}
    occupied = live | pending | lanes
    if task not in occupied:
        remaining = maximum - len(occupied)
        candidates = []
        for candidate in queued:
            ident = candidate['erp_task']
            if ident not in occupied and ident not in candidates:
                candidates.append(ident)
        if remaining <= 0 or task not in candidates[:remaining]:
            return False
    # Same-task PM/developer/QA transitions keep their original slot, including
    # when a reduced policy is below existing occupancy. Never cancel live work.
    db.execute('INSERT INTO governance_project_lane VALUES(?,?,?,?) '
               'ON CONFLICT(project,task) DO UPDATE SET invocation_id=excluded.invocation_id,updated_at=excluded.updated_at',
               (project, task, row['invocation_id'], int(time.time())))
    return True


def busy(db, project, maximum=1):
    """Queued work, QA and uncertain submissions reserve their Task slots."""
    tasks = _live_tasks(db, project) | _pending_review_tasks(db, project)
    tasks.update(row['erp_task'] for row in _queued(db, project))
    return len(tasks) >= maximum


async def pickup_next(project, snapshot, *, bridge, db_path, task_done):
    """Select one exact idle native Task, then ask ERP for a signed handoff."""
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import digest
    maximum = concurrency(snapshot)
    rows = snapshot['tasks']
    validate_graph(rows)
    with ledger._transaction(db_path) as db:
        if busy(db, project, maximum):
            return None
    developer = next((r['erp_user'] for r in ledger.roster(project, db_path)
                      if r['role'] == 'developer'), None)
    if not developer:
        return None
    by_id = {r['task_id']: r for r in rows}
    priority = {'Urgent': 0, 'High': 1, 'Medium': 2, 'Low': 3}
    candidates = sorted(rows, key=lambda r: (
        priority.get(r.get('priority'), 2), r.get('due_date') or '9999', r['task_id']))
    for row in candidates:
        ident = row['task_id']
        if (row.get('managed') is not False or row.get('status') not in {'Open','Overdue'}
                or not isinstance(row.get('is_group'), bool) or not isinstance(row.get('assignees'), list)
                or set(row['assignees']) - {developer}):
            continue
        with ledger.connect(db_path) as db:
            try:
                ledger._task_gate(db, project, ident)
            except ValueError:
                continue
        dependencies = row.get('dependencies')
        if not isinstance(dependencies, list) or any(dep not in by_id for dep in dependencies):
            continue  # Missing prerequisite data never proves independence.
        children = [child['task_id'] for child in rows if child.get('parent_task') == ident]
        if not all([await task_done(bridge, project, dep, db_path)
                    for dep in sorted(set(dependencies + children))]):
            continue
        body = {'project_id': project, 'task_id': ident,
                'expected_task_revision': row['task_revision'],
                'expected_owner_context_sha256': row['owner_context_sha256'],
                'idempotency_key': 'pickup-' + digest([
                    project, ident, row['task_revision'], row['owner_context_sha256']])[:48]}
        response = await bridge.erp.request(
            'POST', '/api/method/abit_ai_assistant.agentic_control.prepare_project_pickup',
            json=body, headers={'X-ABIT-Agentic-Bridge': bridge.secret})
        result = response.get('message') or {}
        if result.get('state') == 'Waiting':
            return result
        if (result.get('state') != 'Accepted' or result.get('project_id') != project
                or result.get('task_id') != ident or result.get('target_role') != 'project_manager'
                or result.get('executor_role') != 'developer' or not result.get('command_id')):
            raise ValueError('ERP did not confirm the exact signed project pickup')
        return result
    return None

"""Serial project execution and deterministic native backlog pickup.

The lane is acquired in the existing SQLite claim transaction, never by a model
or status read. ERP owns enrollment and emits the original signed Start event.
"""
from __future__ import annotations
import json
import time


def _exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_project_lane(
        project TEXT PRIMARY KEY,task TEXT NOT NULL,invocation_id TEXT NOT NULL,
        updated_at INTEGER NOT NULL)""")


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
    return result


def admit(db, row):
    """True for one task's whole lifecycle; caller already holds BEGIN IMMEDIATE.

    Running/uncertain legacy work is observed before creating a lane. A parent
    waiting for children yields its lane; independently verified QA retains it
    until its actual PM review finishes, including the review-enqueue gap.
    """
    _schema(db)
    project, task = row['erp_project'], row['erp_task']
    live = _live_tasks(db, project)
    pending = _pending_review_tasks(db, project)
    queued = _queued(db, project)
    lane = db.execute('SELECT * FROM governance_project_lane WHERE project=?', (project,)).fetchone()
    if lane and lane['task'] not in live | pending | {r['erp_task'] for r in queued}:
        db.execute('DELETE FROM governance_project_lane WHERE project=?', (project,))
        lane = None
    # Existing possible submissions always win over a new project task.
    if live - {task} or pending - {task}:
        return False
    if lane:
        return lane['task'] == task
    if live or pending:
        selected = task
    else:
        if not queued or queued[0]['invocation_id'] != row['invocation_id']:
            return False
        selected = queued[0]['erp_task']
    db.execute('INSERT INTO governance_project_lane VALUES(?,?,?,?)',
               (project, selected, row['invocation_id'], int(time.time())))
    return True


def busy(db, project):
    """Native pickup cannot jump existing queued work, QA or an uncertain run."""
    return bool(_live_tasks(db, project) or _pending_review_tasks(db, project) or _queued(db, project))


async def pickup_next(project, snapshot, *, bridge, db_path, task_done):
    """Select one exact idle native Task, then ask ERP for a signed handoff."""
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import digest
    with ledger._transaction(db_path) as db:
        if busy(db, project):
            return None
    developer = next((r['erp_user'] for r in ledger.roster(project, db_path)
                      if r['role'] == 'developer'), None)
    if not developer:
        return None
    rows = snapshot['tasks']
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

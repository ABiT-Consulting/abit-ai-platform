"""Project and Task commands share the original fenced invocation history."""

import json
import time
from open_webui.utils import agentic_governance as ledger


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_task_control(
        project TEXT NOT NULL,task TEXT NOT NULL,state TEXT NOT NULL,actor TEXT NOT NULL,
        updated_at INTEGER NOT NULL,PRIMARY KEY(project,task))''')
    db.execute('''CREATE TABLE IF NOT EXISTS governance_control_wait(
        invocation_id TEXT PRIMARY KEY,project TEXT NOT NULL,task TEXT NOT NULL,phase TEXT NOT NULL)''')


def allowed(db, project, task):
    return ledger._task_gate(db, project, task)


def task_control(project, task, action='status', *, actor='', db_path=None):
    if not ledger.PROJECT_ID.fullmatch(project) or not ledger.TASK_ID.fullmatch(task):
        raise ValueError('Invalid Task control scope')
    with ledger._transaction(db_path) as db:
        schema(db)
        if action == 'status':
            row = db.execute(
                'SELECT * FROM governance_task_control WHERE project=? AND task=?', (project, task)
            ).fetchone()
            return dict(row) if row else {'project': project, 'task': task, 'state': 'enabled'}
        ledger._actor(actor)
        if action not in {'start', 'pause', 'resume', 'stop'}:
            raise ValueError('Unsupported Task control')
        state = {'start': 'enabled', 'resume': 'enabled', 'pause': 'paused', 'stop': 'stopped'}[action]
        db.execute(
            'INSERT INTO governance_task_control VALUES(?,?,?,?,?) ON CONFLICT(project,task) DO UPDATE SET state=excluded.state,actor=excluded.actor,updated_at=excluded.updated_at',
            (project, task, state, actor, int(time.time())),
        )
        ledger._event(db, project, None, 'task_' + action, actor, {'task': task, 'state': state})
        if action == 'stop':
            for row in db.execute(
                "SELECT * FROM governance_invocation WHERE erp_project=? AND erp_task=? AND state IN ('queued','running','uncertain','execution_finished','awaiting_approval')",
                (project, task),
            ).fetchall():
                ledger._cancel(db, row, actor)
    if action == 'pause':
        freeze_queued(project, task, actor, db_path)
    return task_control(project, task, db_path=db_path)


def task_controls(project, db_path=None):
    with ledger.connect(db_path) as db:
        schema(db)
        result = [
            dict(row)
            for row in db.execute('SELECT * FROM governance_task_control WHERE project=? ORDER BY task', (project,))
        ]
        for item in result:
            active = db.execute(
                "SELECT 1 FROM governance_invocation WHERE erp_project=? AND erp_task=? AND state IN ('running','uncertain','cancel_requested') LIMIT 1",
                (project, item['task']),
            ).fetchone()
            item['effective_state'] = (
                item['state'] + '_requested' if active and item['state'] in {'paused', 'stopped'} else item['state']
            )
        return result


def freeze_queued(project, task=None, actor='erp-revocation', db_path=None):
    with ledger._transaction(db_path) as db:
        schema(db)
        sql = "SELECT * FROM governance_invocation WHERE erp_project=? AND state='queued' AND submitted_at IS NULL"
        args = [project]
        if task:
            sql += ' AND erp_task=?'
            args.append(task)
        for row in db.execute(sql, args).fetchall():
            # These are durable queued claims, not an interruption of an
            # external operation. Resume must revalidate the exact packet.
            db.execute(
                "UPDATE governance_invocation SET state='blocked',reason='ERP control prevents new work',updated_at=? WHERE invocation_id=?",
                (int(time.time()), row['invocation_id']),
            )
            db.execute(
                "INSERT OR REPLACE INTO governance_control_wait VALUES(?,?,?,'waiting')",
                (row['invocation_id'], project, row['erp_task']),
            )
            ledger._release(db, row)
            ledger._event(db, project, row['invocation_id'], 'queued_work_blocked_by_control', actor, {})


def waiting(project, task=None, db_path=None):
    with ledger.connect(db_path) as db:
        schema(db)
        sql = "SELECT invocation_id FROM governance_control_wait WHERE project=? AND phase='waiting'"
        args = [project]
        if task:
            sql += ' AND task=?'
            args.append(task)
        return [row[0] for row in db.execute(sql, args)]


def resume_controlled(ident, packet, gate, actor, db_path=None):
    with ledger._transaction(db_path) as db:
        schema(db)
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (ident,)).fetchone()
        wait = db.execute(
            "SELECT 1 FROM governance_control_wait WHERE invocation_id=? AND phase='waiting'", (ident,)
        ).fetchone()
        if not row or not wait or row['state'] != 'blocked' or row['submitted_at'] is not None:
            return False
        if row['packet_hash'] != ledger.work_packet_digest(packet):
            raise ValueError('Queued work changed during pause; a new authorized generation is required')
        ledger._gate(db, row['erp_project'], gate)
        allowed(db, row['erp_project'], row['erp_task'])
        fence = ledger._acquire(db, row['erp_project'], row['erp_task'], ident)
        db.execute(
            "UPDATE governance_invocation SET state='queued',lease_fence=?,reason=NULL,updated_at=? WHERE invocation_id=?",
            (fence, int(time.time()), ident),
        )
        db.execute("UPDATE governance_control_wait SET phase='resumed' WHERE invocation_id=?", (ident,))
        ledger._event(db, row['erp_project'], ident, 'original_queued_work_resumed', actor, {'fence': fence})
        return True

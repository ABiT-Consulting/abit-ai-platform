"""Durable executor ownership inside an existing governed task/run lease.

Engine epochs never replace the ERP invocation, WorkPacket or task fence.
A crashed/unknown process is held, not retried or handed to another executor.
"""
from __future__ import annotations

import json
import time
from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS governance_execution_policy(
        project_id TEXT PRIMARY KEY,strategy TEXT NOT NULL,fallback_json TEXT NOT NULL,
        actor TEXT NOT NULL,updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS governance_executor(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,packet_hash TEXT NOT NULL,
        task_fence INTEGER NOT NULL,executor TEXT NOT NULL,epoch INTEGER NOT NULL,
        state TEXT NOT NULL,pid INTEGER,exit_code INTEGER,checkpoint_json TEXT,
        handoff_reason TEXT,record_json TEXT,updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS governance_executor_event(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,invocation_id TEXT NOT NULL,
        epoch INTEGER NOT NULL,kind TEXT NOT NULL,body_json TEXT NOT NULL,created_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS governance_executor_checkpoint(
        checkpoint_id TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,body_json TEXT NOT NULL,created_at INTEGER NOT NULL);
      CREATE TRIGGER IF NOT EXISTS executor_event_no_update BEFORE UPDATE ON governance_executor_event
        BEGIN SELECT RAISE(ABORT,'Executor audit is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS executor_event_no_delete BEFORE DELETE ON governance_executor_event
        BEGIN SELECT RAISE(ABORT,'Executor audit is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS executor_checkpoint_no_update BEFORE UPDATE ON governance_executor_checkpoint
        BEGIN SELECT RAISE(ABORT,'Executor checkpoint is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS executor_checkpoint_no_delete BEFORE DELETE ON governance_executor_checkpoint
        BEGIN SELECT RAISE(ABORT,'Executor checkpoint is immutable'); END;
    """)


def connect(db_path=None):
    # Call schema outside transactions: sqlite executescript commits implicitly.
    context = ledger.connect(db_path)
    return context


def initialize(db_path=None):
    with connect(db_path) as db:
        schema(db)


def event(db, row, kind, body):
    db.execute('INSERT INTO governance_executor_event(invocation_id,epoch,kind,body_json,created_at) VALUES(?,?,?,?,?)',
               (row['invocation_id'], row['epoch'], kind, canonical(body), int(time.time())))


def policy(project, db_path=None):
    initialize(db_path)
    with connect(db_path) as db:
        found = db.execute('SELECT * FROM governance_execution_policy WHERE project_id=?', (project,)).fetchone()
    return ({'strategy': found['strategy'], 'fallback_on': json.loads(found['fallback_json'])}
            if found else {'strategy': 'codex_first', 'fallback_on': ['quota']})


def set_policy(project, strategy, fallback_on, actor, db_path=None):
    if not ledger.PROJECT_ID.fullmatch(project) or strategy not in {'codex_first', 'chatgpt_dot_first'}:
        raise ValueError('Invalid execution strategy or Project')
    if (not isinstance(fallback_on, list) or len(set(fallback_on)) != len(fallback_on)
            or not set(fallback_on) <= {'quota', 'cli_unavailable'}):
        raise ValueError('Only confirmed quota or CLI absence can enable fallback')
    ledger._actor(actor)
    initialize(db_path)
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO governance_execution_policy VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET strategy=excluded.strategy,fallback_json=excluded.fallback_json,actor=excluded.actor,updated_at=excluded.updated_at',
                   (project, strategy, canonical(sorted(fallback_on)), actor, int(time.time())))
        ledger._event(db, project, None, 'execution_policy_changed', actor,
                      {'strategy': strategy, 'fallback_on': sorted(fallback_on)})
        db.commit()
    return policy(project, db_path)


def get(invocation, db_path=None, *, private=False):
    initialize(db_path)
    with connect(db_path) as db:
        row = db.execute('SELECT * FROM governance_executor WHERE invocation_id=?', (invocation,)).fetchone()
    if not row:
        return None
    value = dict(row)
    for name in ('checkpoint_json', 'record_json'):
        value[name[:-5]] = json.loads(value.pop(name) or 'null')
    if not private:
        for key in ('pid', 'record', 'packet_hash'):
            value.pop(key, None)
        # Checkpoints contain protected source paths/test logs. Status exports metadata only.
        checkpoint = value.pop('checkpoint')
        value['checkpoint_id'] = checkpoint['checkpoint_id'] if checkpoint else None
    return value


def owned(db, row):
    current = ledger._owned(db, row['invocation_id'], row['automation_run_id'], row['lease_token'], {'running'})
    if (not current or any(current[key] != row[key] for key in
                          ('packet_hash', 'lease_fence', 'erp_task', 'erp_project', 'agent_id'))):
        raise ValueError('Executor lost the original task/run fence')
    return current


def begin(row, db_path=None):
    initialize(db_path)
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        owned(db, row)
        if db.execute('SELECT 1 FROM governance_executor WHERE invocation_id=?', (row['invocation_id'],)).fetchone():
            raise ValueError('Original executor already exists; reconcile without replay')
        db.execute('INSERT INTO governance_executor VALUES(?,?,?,?,?,1,?,NULL,NULL,NULL,NULL,NULL,?)',
                   (row['invocation_id'], row['automation_run_id'], row['packet_hash'],
                    row['lease_fence'], 'codex', 'starting', int(time.time())))
        event(db, {'invocation_id': row['invocation_id'], 'epoch': 1}, 'codex_intent', {})
        db.commit()


def archive_checkpoint(db, row, encoded):
    value = json.loads(encoded)
    prior = db.execute('SELECT body_json FROM governance_executor_checkpoint WHERE checkpoint_id=?',
                       (value['checkpoint_id'],)).fetchone()
    if prior and prior['body_json'] != encoded:
        raise ValueError('Verified checkpoint identity has different immutable content')
    db.execute('INSERT OR IGNORE INTO governance_executor_checkpoint VALUES(?,?,?,?)',
               (value['checkpoint_id'], row['invocation_id'], encoded, int(time.time())))


def change(row, expected, state, db_path=None, **values):
    allowed = {'pid', 'exit_code', 'record_json', 'checkpoint_json', 'handoff_reason'}
    if not set(values) <= allowed:
        raise ValueError('Unsupported executor update')
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT * FROM governance_executor WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
        if not found or found['state'] not in expected:
            raise ValueError('Executor state changed; original ownership remains held')
        if any(found[k] != v for k, v in (('run_id', row['automation_run_id']),
                                         ('packet_hash', row['packet_hash']), ('task_fence', row['lease_fence']))):
            raise ValueError('Executor identity changed')
        if 'checkpoint_json' in values:
            archive_checkpoint(db, row, values['checkpoint_json'])
        assignments = ','.join(k + '=?' for k in values)
        db.execute('UPDATE governance_executor SET state=?,updated_at=?' + (',' + assignments if assignments else '') + ' WHERE invocation_id=?',
                   (state, int(time.time()), *values.values(), row['invocation_id']))
        event(db, found, state, {'exit_code': values.get('exit_code'), 'reason': values.get('handoff_reason')})
        db.commit()


def handoff(row, checkpoint, reason, db_path=None):
    """Only called after process-group join AND completion of every local tool call."""
    if reason not in {'quota', 'cli_unavailable'}:
        raise ValueError('Ordinary execution errors cannot authorize fallback')
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        owned(db, row)
        current = db.execute('SELECT * FROM governance_executor WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
        if (not current or current['executor'] != 'codex' or current['state'] != 'exited'
                or current['epoch'] != 1 or checkpoint['run_id'] != current['run_id']
                or checkpoint['task_revision'] != row['task_revision']
                or checkpoint['work_packet_digest'] != json.loads(row['work_packet'])['content_sha256']):
            raise ValueError('Codex must conclusively exit with an exact verified checkpoint before handoff')
        unresolved = db.execute("""SELECT 1 FROM governance_gateway_call c JOIN governance_gateway_session s
          ON s.session_id=c.session_id WHERE s.invocation_id=? AND c.state!='completed'""",
                                (row['invocation_id'],)).fetchone()
        if unresolved:
            raise ValueError('Unresolved gateway action prevents executor handoff')
        archive_checkpoint(db, row, canonical(checkpoint))
        db.execute("""UPDATE governance_executor SET executor='dot',epoch=2,state='handoff_ready',
          checkpoint_json=?,handoff_reason=?,updated_at=? WHERE invocation_id=?""",
                   (canonical(checkpoint), reason, int(time.time()), row['invocation_id']))
        event(db, {'invocation_id': row['invocation_id'], 'epoch': 2}, 'fenced_handoff',
              {'reason': reason, 'checkpoint_id': checkpoint['checkpoint_id'], 'run_id': row['automation_run_id']})
        db.commit()


def guard_client(db, invocation, client_hash):
    """Check the executor under the same transaction as the gateway receipt or intent."""
    from hashlib import sha256

    # An admitted session can predate installation of the executor journal.
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_executor'"
    ).fetchone() is None:
        return
    current = db.execute(
        'SELECT executor FROM governance_executor WHERE invocation_id=?', (invocation,)
    ).fetchone()
    local = sha256(('codex:' + invocation).encode()).hexdigest()
    if current and ((current['executor'] == 'codex') != (client_hash == local)):
        raise ValueError('Gateway client is fenced by the current executor epoch')


def replace_admission(db, session, admission, connection_hash):
    """Called inside the normal gateway admission transaction with fresh ERP proofs."""
    current = db.execute('SELECT * FROM governance_executor WHERE invocation_id=?',
                         (session['invocation_id'],)).fetchone()
    if (not current or current['executor'] != 'dot' or current['state'] != 'handoff_ready'
            or current['epoch'] != 2 or not current['checkpoint_json']):
        raise ValueError('Original gateway admission is immutable; no replacement send')
    if db.execute("SELECT 1 FROM governance_gateway_call WHERE session_id=? AND state!='completed'",
                  (session['session_id'],)).fetchone():
        raise ValueError('An unresolved original action prevents admission transfer')
    db.execute('UPDATE governance_gateway_session SET admission_json=?,connection_hash=?,client_session_hash=NULL WHERE session_id=?',
               (admission, connection_hash, session['session_id']))
    db.execute("UPDATE governance_executor SET state='dot_admitted',updated_at=? WHERE invocation_id=?",
               (int(time.time()), session['invocation_id']))
    event(db, current, 'gateway_transferred', {'previous_admission_sha256': digest(json.loads(session['admission_json'])),
                                             'previous_admission': json.loads(session['admission_json'])})

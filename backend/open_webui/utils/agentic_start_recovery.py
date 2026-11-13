"""Recover a positively acknowledged ERP start before any submission fence.

Only the original immutable start event may be observed. This module never
prepares a run, posts a start callback, submits a model or executes a host action.
The executor must still acquire its shared ownership/slot and call before_submit.
"""

from __future__ import annotations
import hashlib
import json
import secrets
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest

RECEIPT_RPC = '/api/method/abit_ai_assistant.agentic_control.get_result_receipt'
MANAGED = {'source_modified', 'packet_id', 'content_sha256', 'implementation_user', 'implementation_run_id'}


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_start(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,packet_hash TEXT NOT NULL,
        lease_fence INTEGER NOT NULL,lease_token_sha256 TEXT NOT NULL,event_json TEXT NOT NULL,
        event_sha256 TEXT NOT NULL,receipt_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)''')


def _token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _unsubmitted(db, row, states):
    if (
        not row
        or row['state'] not in states
        or row['submitted_at'] is not None
        or not row['automation_run_id']
        or not row['lease_token']
    ):
        return False
    if not ledger._owned(db, row['invocation_id'], row['automation_run_id'], row['lease_token'], states):
        return False
    # A returned result or host intent is incompatible with a never-submitted
    # start. Preserve it for original-receipt recovery, never infer a safe retry.
    for table in ('governance_local_result', 'governance_result', 'governance_action'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            if db.execute(
                'SELECT 1 FROM ' + table + ' WHERE invocation_id=? LIMIT 1', (row['invocation_id'],)
            ).fetchone():
                return False
    return True


def _identity(row, event):
    packet = json.loads(row['work_packet'])
    expected = {
        'schema_version': 1,
        'event_id': 'agov-start-' + digest(row['invocation_id'])[:48],
        'type': 'run.started',
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'run_id': row['automation_run_id'],
        'invocation_id': row['invocation_id'],
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'work_packet_digest': packet_digest(packet),
        'expected_modified': packet['source_modified'],
        'expected_project_revision': packet['project_revision'],
        'artifacts': [],
    }
    if any(event.get(key) != value for key, value in expected.items()):
        raise ValueError('Start event does not match its original immutable run and packet')
    if len(canonical(event).encode()) > 24000:
        raise ValueError('Original start event exceeds the journal bound')


def record_start_intent(row, event, db_path=None):
    """Write before the first ERP publish; identical writes are idempotent."""
    with ledger._transaction(db_path) as db:
        _schema(db)
        current = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        if not _unsubmitted(db, current, {'running'}):
            raise ValueError('Start journal requires the original unsubmitted active run')
        current = dict(current)
        _identity(current, event)
        if any(
            current.get(key) != row.get(key)
            for key in ('automation_run_id', 'lease_token', 'lease_fence', 'packet_hash')
        ):
            raise ValueError('Original start identity changed before journaling')
        value = (
            current['automation_run_id'],
            current['packet_hash'],
            current['lease_fence'],
            _token(current['lease_token']),
            canonical(event),
            digest(event),
        )
        prior = db.execute(
            'SELECT run_id,packet_hash,lease_fence,lease_token_sha256,event_json,event_sha256 FROM governance_start WHERE invocation_id=?',
            (row['invocation_id'],),
        ).fetchone()
        if prior:
            if tuple(prior) != value:
                raise ValueError('Original start intent is immutable')
            return False
        now = int(time.time())
        db.execute(
            'INSERT INTO governance_start VALUES(?,?,?,?,?,?,?,NULL,?,?)', (row['invocation_id'], *value, now, now)
        )
        ledger._event(
            db,
            current['erp_project'],
            current['invocation_id'],
            'original_start_journaled',
            'executor',
            {'event_sha256': value[-1]},
        )
        return True


def _matching(row, entry):
    if not entry:
        return False
    return (
        entry['run_id'] == row['automation_run_id']
        and entry['packet_hash'] == row['packet_hash']
        and entry['lease_fence'] == row['lease_fence']
        and row['lease_token']
        and secrets.compare_digest(entry['lease_token_sha256'], _token(row['lease_token']))
    )


def _receipt(entry, value):
    if not isinstance(value, dict):
        raise ValueError('Original ERP start receipt is missing')
    payload = value.get('receipt', value)
    if not isinstance(payload, dict):
        raise ValueError('Original ERP start receipt is invalid')
    result = {key: item for key, item in payload.items() if key not in {'exists', 'content_sha256'}}
    event = json.loads(entry['event_json'])
    sha = value.get('content_sha256', payload.get('content_sha256'))
    if sha is not None and sha != entry['event_sha256']:
        raise ValueError('ERP start receipt content hash differs from the saved original event')
    expected_status = 'QA Reviewing' if event['role'] == 'qa' else 'Working'
    if (
        any(result.get(key) != event[key] for key in ('event_id', 'task_id', 'run_id'))
        or result.get('durable') is not True
        or result.get('status') != expected_status
        or not isinstance(result.get('modified'), str)
        or not result['modified']
    ):
        raise ValueError('ERP start receipt does not confirm the original active run')
    return result


def record_start_receipt(invocation_id, receipt, db_path=None):
    with ledger._transaction(db_path) as db:
        _schema(db)
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not row or not _matching(row, entry):
            raise ValueError('Original start journal identity changed')
        value = canonical(_receipt(entry, receipt))
        if entry['receipt_json'] and entry['receipt_json'] != value:
            raise ValueError('Original ERP start receipt is immutable')
        db.execute(
            'UPDATE governance_start SET receipt_json=?,updated_at=? WHERE invocation_id=?',
            (value, int(time.time()), invocation_id),
        )
        return json.loads(value)


async def get_confirmed_start(row, bridge, project_gate, config, db_path=None):
    """Observe a positive receipt and unchanged current scope; unknown is inert."""
    with ledger.connect(db_path) as db:
        _schema(db)
        current = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
        if not _unsubmitted(db, current, {'uncertain'}) or not _matching(current, entry):
            return None
        current = dict(current)
        entry = dict(entry)
        if any(
            current.get(key) != row.get(key)
            for key in ('automation_run_id', 'lease_token', 'lease_fence', 'packet_hash')
        ):
            return None
    if entry['receipt_json']:
        receipt = _receipt(entry, json.loads(entry['receipt_json']))
    else:
        event = json.loads(entry['event_json'])
        response = await bridge.erp.request(
            'GET',
            RECEIPT_RPC,
            params={key: event[key] for key in ('event_id', 'project_id', 'task_id')},
            headers={'X-ABIT-Agentic-Bridge': bridge.secret},
        )
        value = response.get('message') if isinstance(response, dict) else None
        if not isinstance(value, dict) or value.get('exists') is not True:
            return None
        receipt = _receipt(entry, value)
    _, gate = await project_gate(current['erp_project'], config)
    packet = json.loads(current['work_packet'])
    fresh = await bridge.packet(
        current['erp_project'], current['erp_task'], current['target_role'], current['agent_id']
    )
    if any(packet.get(key) != fresh.get(key) for key in (set(packet) | set(fresh)) - MANAGED):
        raise ValueError('WorkPacket changed after the original ERP start')
    if fresh.get('source_modified') != receipt['modified']:
        raise ValueError('ERP Task changed after the original start receipt')
    with ledger._transaction(db_path) as db:
        latest = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        if not _unsubmitted(db, latest, {'uncertain'}) or not _matching(latest, entry):
            return None
        ledger._gate(db, current['erp_project'], gate)
        ledger._task_gate(db, current['erp_project'], current['erp_task'])
    return record_start_receipt(row['invocation_id'], receipt, db_path)


def reclaim_same(invocation_id, receipt, *, gate, db_path=None):
    """CAS only the positively confirmed original run back to its active lease."""
    with ledger._transaction(db_path) as db:
        _schema(db)
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not _unsubmitted(db, row, {'uncertain'}) or not _matching(row, entry) or not entry['receipt_json']:
            return None
        if canonical(_receipt(entry, receipt)) != entry['receipt_json']:
            raise ValueError('Reclaim requires the saved exact original start receipt')
        ledger._gate(db, row['erp_project'], gate)
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        now = int(time.time())
        db.execute(
            "UPDATE governance_invocation SET state='running',lease_expires_at=?,reason=NULL,updated_at=? WHERE invocation_id=? AND state='uncertain' AND submitted_at IS NULL",
            (now + ledger.LEASE_SECONDS, now, invocation_id),
        )
        ledger._event(
            db,
            row['erp_project'],
            invocation_id,
            'original_unsubmitted_start_reclaimed',
            'executor',
            {'run_id': row['automation_run_id'], 'fence': row['lease_fence']},
        )
        return dict(
            db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        )

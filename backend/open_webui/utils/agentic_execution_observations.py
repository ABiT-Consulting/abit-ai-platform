"""Durable native visibility for uncertain original executions.

This server observation is separate from agent results. It never settles a
submission, releases a lease, creates QA proof or authorizes replay.
"""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger(__name__)
RPC = '/api/method/abit_ai_assistant.agentic_control.record_execution_observation'
REASONS = frozenset({'execution_failed', 'lease_expired', 'submission_unconfirmed', 'cancellation_unconfirmed'})


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_execution_observation(
        event_id TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,project TEXT NOT NULL,
        event_json TEXT NOT NULL,phase TEXT NOT NULL DEFAULT 'pending',
        receipt_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""")


def _event(row, reason):
    if not isinstance(reason, str) or reason not in REASONS:
        raise ValueError('Unsupported execution observation reason')
    from open_webui.utils.agentic_bridge import canonical, digest, packet_digest
    try:
        packet = json.loads(row['work_packet'])
    except (TypeError, ValueError):
        raise ValueError('Original execution observation packet is malformed') from None
    if not isinstance(packet, dict):
        raise ValueError('Original execution observation packet is malformed')
    if digest(packet) != row['packet_hash']:
        raise ValueError('Original execution observation packet hash changed')
    event = {
        'schema_version': 1,
        'event_id': 'agov-observe-' + digest([row['invocation_id'], row['automation_run_id'], reason])[:48],
        'type': 'execution.observation',
        'project_id': row['erp_project'], 'task_id': row['erp_task'],
        'run_id': row['automation_run_id'], 'invocation_id': row['invocation_id'],
        'role': row['target_role'], 'agent_id': row['agent_id'],
        'work_packet_digest': packet_digest(packet), 'reason': reason,
    }
    return event


def stage(db, row, reason):
    """Called within the original ledger transaction, before any network call."""
    if not row['automation_run_id'] or not row['packet_hash']:
        return None
    from open_webui.utils.agentic_bridge import canonical
    event = _event(row, reason)
    _schema(db)
    encoded, now = canonical(event), int(time.time())
    prior = db.execute('SELECT event_json FROM governance_execution_observation WHERE event_id=?',
                       (event['event_id'],)).fetchone()
    if prior and prior[0] != encoded:
        raise ValueError('Execution observation identity changed')
    db.execute("""INSERT OR IGNORE INTO governance_execution_observation(
        event_id,invocation_id,project,event_json,created_at,updated_at) VALUES(?,?,?,?,?,?)""",
        (event['event_id'], row['invocation_id'], row['erp_project'], encoded, now, now))
    return event['event_id']


def acknowledged_modified(row, original_modified, fresh_modified, db_path=None, *, fresh_packet=None):
    """Only an exact authenticated observation chain may advance the native CAS.

    Original start receipts and packet bytes are never amended. Any human,
    owner or business-input change must still pass the existing fresh packet
    and role gates, and an unrelated timestamp cannot use this provenance.
    """
    if fresh_modified == original_modified:
        return original_modified
    if (not isinstance(original_modified, str) or not original_modified
            or not isinstance(fresh_modified, str) or not fresh_modified):
        raise ValueError('Native observation modification provenance is unavailable')
    try:
        original_packet = json.loads(row['work_packet'])
    except (TypeError, ValueError):
        raise ValueError('Original execution observation packet is malformed') from None
    def owner_fingerprint(packet):
        snapshot = packet.get('project_backlog') if isinstance(packet, dict) else None
        rows = snapshot.get('tasks') if isinstance(snapshot, dict) else None
        if not isinstance(rows, list) or any(not isinstance(task, dict) for task in rows):
            return None
        own = [task for task in rows if task.get('task_id') == row['erp_task']]
        value = own[0].get('owner_context_sha256') if len(own) == 1 else None
        return value if isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) else None
    original_owner = owner_fingerprint(original_packet)
    if original_owner is None or owner_fingerprint(fresh_packet) != original_owner:
        raise ValueError('Original Task ownership changed after the execution observation')
    from open_webui.utils import agentic_governance as ledger
    with ledger.connect(db_path) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_execution_observation'").fetchone()
        records = db.execute("SELECT * FROM governance_execution_observation WHERE invocation_id=? AND phase='published'",
                             (row['invocation_id'],)).fetchall() if exists else []
    edges = {}
    for saved in records:
        try:
            event, receipt = json.loads(saved['event_json']), json.loads(saved['receipt_json'])
        except (TypeError, ValueError):
            raise ValueError('Native observation journal is malformed') from None
        if not isinstance(event, dict) or not isinstance(receipt, dict):
            raise ValueError('Native observation journal is malformed')
        if event != _event(row, event.get('reason')) or saved['event_id'] != event['event_id']:
            raise ValueError('Native observation original identity changed')
        if (not isinstance(receipt, dict) or receipt.get('durable') is not True
                or any(receipt.get(key) != event[key]
                       for key in ('event_id', 'project_id', 'task_id', 'run_id'))):
            raise ValueError('Native observation receipt identity changed')
        if not isinstance(receipt.get('status'), str) or receipt['status'] not in {'Recorded', 'Superseded'}:
            raise ValueError('Native observation receipt status changed')
        if receipt['status'] == 'Superseded':
            continue
        previous, modified = receipt.get('previous_modified'), receipt.get('modified')
        if (not isinstance(previous, str) or not previous or len(previous) > 160
                or not isinstance(modified, str) or not modified or len(modified) > 160):
            raise ValueError('Native observation lacks exact modification provenance')
        if previous != modified:
            edges.setdefault(previous, set()).add(modified)
    cursor, visited = original_modified, set()
    while cursor != fresh_modified:
        successors = edges.get(cursor, set())
        if cursor in visited or len(successors) != 1 or len(visited) >= 100:
            raise ValueError('Task changed outside its exact native execution observations')
        visited.add(cursor)
        cursor = next(iter(successors))
    return cursor


def seed_uncertain(project, db_path=None):
    """Recover preexisting unknown originals without creating another execution."""
    from open_webui.utils import agentic_governance as ledger
    with ledger._transaction(db_path) as db:
        for row in db.execute("""SELECT * FROM governance_invocation WHERE erp_project=?
                AND state IN ('uncertain','cancel_requested') AND automation_run_id IS NOT NULL""",
                (project,)).fetchall():
            reason = ('cancellation_unconfirmed' if row['state'] == 'cancel_requested'
                      else 'submission_unconfirmed' if row['submitted_at'] is None else 'execution_failed')
            stage(db, row, reason)


async def deliver_pending(bridge, *, project=None, invocation_id=None, db_path=None):
    """Finite fair retry pass; uncertain receipts retain their immutable identity."""
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import canonical, signing_headers
    if (project is not None and (not isinstance(project, str) or not ledger.PROJECT_ID.fullmatch(project))
            or invocation_id is not None and (not isinstance(invocation_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_.:@+-]{16,160}', invocation_id))):
        raise ValueError('Invalid execution observation delivery scope')
    scope, args = "phase='pending'", []
    if project is not None:
        scope += ' AND project=?'; args.append(project)
    if invocation_id is not None:
        scope += ' AND invocation_id=?'; args.append(invocation_id)
    with ledger._transaction(db_path) as db:
        _schema(db)
        ceiling = db.execute('SELECT COALESCE(MAX(rowid),0) FROM governance_execution_observation WHERE ' + scope,
                             args).fetchone()[0]
    delivered, cursor = [], 0
    while cursor < ceiling:
        with ledger.connect(db_path) as db:
            rows = [dict(row) for row in db.execute(
                'SELECT rowid AS delivery_cursor,* FROM governance_execution_observation WHERE ' + scope
                + ' AND rowid>? AND rowid<=? ORDER BY rowid LIMIT 100', [*args, cursor, ceiling])]
        if not rows:
            break
        cursor = rows[-1]['delivery_cursor']
        for row in rows:
            try:
                event = json.loads(row['event_json'])
                if (not isinstance(event, dict) or event.get('event_id') != row['event_id']
                        or event.get('project_id') != row['project']
                        or event.get('invocation_id') != row['invocation_id']):
                    raise ValueError('Original execution observation journal identity changed')
                headers = {**signing_headers(event, bridge.secret), 'X-ABIT-Agentic-Bridge': bridge.secret}
                response = await bridge.erp.request('POST', RPC, json={'event': canonical(event)}, headers=headers)
                receipt = response.get('message') or {}
                if (not isinstance(receipt, dict) or receipt.get('durable') is not True
                        or not isinstance(receipt.get('status'), str)
                        or receipt['status'] not in {'Recorded', 'Superseded'}
                        or any(receipt.get(key) != event[key]
                               for key in ('event_id', 'project_id', 'task_id', 'run_id'))):
                    raise ValueError('ERP did not confirm the exact durable execution observation')
                if receipt['status'] == 'Recorded' and any(
                        not isinstance(receipt.get(key), str) or not receipt[key] or len(receipt[key]) > 160
                        for key in ('previous_modified', 'modified')):
                    raise ValueError('ERP observation receipt lacks exact modification provenance')
                with ledger._transaction(db_path) as db:
                    db.execute("""UPDATE governance_execution_observation SET phase='published',
                        receipt_json=?,updated_at=? WHERE event_id=? AND event_json=? AND phase='pending'""",
                        (canonical(receipt), int(time.time()), row['event_id'], row['event_json']))
                delivered.append(row['event_id'])
            except Exception as error:
                # No arbitrary provider, response or exception text enters ERP/logs.
                log.warning('Native execution observation remains pending: %s (%s)',
                            row['event_id'], type(error).__name__)
    return delivered

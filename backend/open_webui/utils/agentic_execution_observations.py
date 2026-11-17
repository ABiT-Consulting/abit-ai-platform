"""Durable native visibility for uncertain original executions.

This server observation is separate from agent results. It never settles a
submission, releases a lease, creates QA proof or authorizes replay.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)
RPC = '/api/method/abit_ai_assistant.agentic_control.record_execution_observation'
REASONS = frozenset({'execution_failed', 'lease_expired', 'submission_unconfirmed', 'cancellation_unconfirmed'})


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_execution_observation(
        event_id TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,project TEXT NOT NULL,
        event_json TEXT NOT NULL,phase TEXT NOT NULL DEFAULT 'pending',
        receipt_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""")


def stage(db, row, reason):
    """Called within the original ledger transaction, before any network call."""
    if reason not in REASONS:
        raise ValueError('Unsupported execution observation reason')
    if not row['automation_run_id'] or not row['packet_hash']:
        return None
    from open_webui.utils.agentic_bridge import canonical, digest, packet_digest
    packet = json.loads(row['work_packet'])
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


async def deliver_pending(bridge, *, project=None, db_path=None):
    """Retry the exact immutable native observation; unknown responses stay pending."""
    from open_webui.utils import agentic_governance as ledger
    from open_webui.utils.agentic_bridge import canonical, signing_headers
    with ledger._transaction(db_path) as db:
        _schema(db)
        where, args = (" AND project=?", [project]) if project else ('', [])
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM governance_execution_observation WHERE phase='pending'" + where
            + ' ORDER BY created_at,event_id LIMIT 100', args)]
    delivered = []
    for row in rows:
        event = json.loads(row['event_json'])
        headers = {**signing_headers(event, bridge.secret), 'X-ABIT-Agentic-Bridge': bridge.secret}
        try:
            response = await bridge.erp.request('POST', RPC, json={'event': canonical(event)}, headers=headers)
            receipt = response.get('message') or {}
            if (not isinstance(receipt, dict) or receipt.get('durable') is not True
                    or receipt.get('status') not in {'Recorded', 'Superseded'}
                    or any(receipt.get(key) != event[key]
                           for key in ('event_id', 'project_id', 'task_id', 'run_id'))):
                raise ValueError('ERP did not confirm the exact durable execution observation')
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

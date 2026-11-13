"""Structured IT execution through trusted adapters; never a broad Dot session."""

from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest
from open_webui.utils.agentic_tool_gateway import execute_action, ApprovalRequired, CapabilityUnavailable

RPC = '/api/method/abit_ai_assistant.agentic_approval_actions.'


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_action_wait(
        invocation_id TEXT PRIMARY KEY,action_json TEXT NOT NULL,action_fingerprint TEXT NOT NULL,
        approval_id TEXT NOT NULL,phase TEXT NOT NULL,created_at INTEGER NOT NULL)''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(governance_action_wait)')}
    for name in ('request_json', 'checkpoint_json', 'checkpoint_phase', 'checkpoint_receipt_json'):
        if name not in columns:
            db.execute('ALTER TABLE governance_action_wait ADD COLUMN ' + name + ' TEXT')
    if 'renewals' not in columns:
        db.execute('ALTER TABLE governance_action_wait ADD COLUMN renewals INTEGER NOT NULL DEFAULT 0')


class ITAdapter:
    def __init__(self, app, project_gate, config, db_path=None, adapters=None):
        self.app = app
        self.project_gate = project_gate
        self.config = config
        self.db_path = db_path
        self.adapters = adapters
        self.action = None

    async def preflight(self, row, packet):
        template = packet.get('tool_action')
        if not isinstance(template, dict):
            raise CapabilityUnavailable(
                'IT requires a structured ERP tool action; broad owner-relay tools are not an IT executor'
            )
        profile = ((self.config.get('agentic_tool_gateway') or {}).get('projects') or {}).get(row['erp_project'])
        if not isinstance(profile, dict) or template['operation'] not in profile.get('operations', []):
            raise CapabilityUnavailable('No authorized infrastructure adapter profile is configured for this project')
        if template['operation'] not in {'inspect', 'cleanup_disposable'} and template['operation'] not in (
            self.adapters or {}
        ):
            raise CapabilityUnavailable('No trusted adapter is implemented for this operation')

    async def prepare(self, row, packet, run_id):
        from open_webui.utils.agentic_executor import DotAdapter

        # Reuse binding/run validation only; submit never invokes Dot.
        return await DotAdapter(self.app).prepare(row, packet, run_id)

    async def submit(self, row, packet, run_id, automation, bridge, *, before_submit):
        await before_submit()
        action = make_action(row, packet)
        self.action = action
        receipt = await execute_action(
            row['invocation_id'],
            action,
            project_gate=self.project_gate,
            config=self.config,
            bridge=bridge,
            adapters=self.adapters,
            db_path=self.db_path,
        )
        return make_record(row, packet, receipt, self.config)

    async def record(self, automation, run_id, record):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import AutomationRun

        async with get_async_db_context() as db:
            original = await db.get(AutomationRun, run_id)
            if (
                original
                and original.automation_id == automation.id
                and original.status == 'success'
                and original.chat_id is None
            ):
                return
            if not original or original.automation_id != automation.id or original.status != 'running':
                raise ValueError('Original IT run changed before trusted adapter receipt')
            original.status = 'success'
            original.chat_id = None
            original.error = None
            if hasattr(original, 'lease_until'):
                original.lease_until = None
            await db.commit()


def make_action(row, packet):
    template = packet['tool_action']
    return {
        'schema_version': 1,
        'action_id': 'agov-action-' + digest([row['invocation_id'], template])[:48],
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'actor': packet['binding'],
        'repository': packet['repository'],
        'environment': packet['environment'],
        'operation': template['operation'],
        'risk': template['risk'],
        'parameters': template['parameters'],
        'requested_at': int(time.time()),
    }


def make_record(row, packet, receipt, config):
    if not receipt or receipt.get('state') != 'completed' or receipt.get('invocation_id') != row['invocation_id']:
        raise RuntimeError('Original infrastructure operation remains uncertain; no repeat')
    from open_webui.utils.agentic_artifacts import artifact_directory, ensure_directory

    root = artifact_directory(row, config)
    ensure_directory(root)
    content = canonical(receipt).encode()
    path = root / 'trusted-action-receipt.json'
    import os, hashlib

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    except FileExistsError:
        from open_webui.utils.agentic_artifacts import local_bytes

        _, prior = local_bytes(root, 'artifact://' + path.name)
        if prior != content:
            raise ValueError('Original infrastructure receipt bytes changed')
    else:
        with os.fdopen(fd, 'wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
            os.fchmod(file.fileno(), 0o640)
    result = {
        'schema_version': 1,
        'invocation_id': row['invocation_id'],
        'actor': packet['binding'],
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'work_packet_digest': packet_digest(packet),
        'outcome': 'ready_for_review',
        'summary': 'Trusted infrastructure adapter completed the exact governed action. Independent QA must verify the Task acceptance criteria.',
        'test_results': [
            {
                'test_id': test['id'],
                'status': 'skipped',
                'observed': 'Adapter receipt recorded; independent acceptance testing remains required.',
                'evidence_ids': ['action-receipt'],
            }
            for test in packet['acceptance_tests']
        ],
        'evidence': [
            {
                'id': 'action-receipt',
                'kind': 'test_log',
                'uri': 'artifact://' + path.name,
                'sha256': hashlib.sha256(content).hexdigest(),
                'requirement_ids': packet['requirement_ids'],
            }
        ],
        'implementation_invocation_id': None,
        'implementation_agent_id': None,
        'defects': [],
    }
    return {
        'response_text': 'AGOV RESULT: ' + canonical(result),
        'transport': 'trusted-tool-gateway',
        'model_requests': 0,
    }


async def suspend(invocation, packet, adapter, bridge, receipt, error, db_path=None):
    row = ledger.find(invocation, db_path, include_private=True)
    action = adapter.action
    if not row or not action:
        raise ValueError('Approval wait has no original bound action')
    approval_id = 'agov-approval-' + digest([invocation, error.action_fingerprint])[:48]
    with ledger._transaction(db_path) as db:
        _table(db)
        current = ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'running'})
        if not current:
            raise ValueError('Original action lease changed before approval wait')
        prior = db.execute(
            'SELECT action_fingerprint FROM governance_action_wait WHERE invocation_id=?', (invocation,)
        ).fetchone()
        if prior and prior[0] != error.action_fingerprint:
            raise ValueError('Original pending action changed')
        db.execute(
            "INSERT OR IGNORE INTO governance_action_wait(invocation_id,action_json,action_fingerprint,approval_id,phase,created_at) VALUES(?,?,?,?,'waiting',?)",
            (invocation, canonical(action), error.action_fingerprint, approval_id, int(time.time())),
        )
        db.execute(
            "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL,updated_at=? WHERE invocation_id=?",
            (int(time.time()), invocation),
        )
        ledger._event(
            db,
            row['erp_project'],
            invocation,
            'action_awaiting_approval',
            'trusted-tool-gateway',
            {
                'action_id': action['action_id'],
                'approval_id': approval_id,
                'action_fingerprint': error.action_fingerprint,
            },
        )
    args = {
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'run_id': row['automation_run_id'],
        'invocation_id': invocation,
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'action': action,
        'action_fingerprint': error.action_fingerprint,
        'approval_id': approval_id,
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    event = {
        'schema_version': 1,
        'event_id': 'agov-action-wait-' + digest(invocation)[:48],
        'type': 'run.checkpoint',
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'run_id': row['automation_run_id'],
        'invocation_id': invocation,
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'summary': 'Awaiting current human ERP approval for exact action '
        + action['action_id']
        + '. No infrastructure operation was performed.',
        'expected_modified': receipt['modified'],
        'expected_project_revision': packet['project_revision'],
        'work_packet_digest': packet_digest(packet),
        'artifacts': [],
    }
    with ledger._transaction(db_path) as db:
        _table(db)
        db.execute(
            "UPDATE governance_action_wait SET request_json=coalesce(request_json,?),checkpoint_json=coalesce(checkpoint_json,?),checkpoint_phase=coalesce(checkpoint_phase,'pending') WHERE invocation_id=?",
            (canonical(args), canonical(event), invocation),
        )
    await reconcile_wait(invocation, bridge, db_path)
    return None  # A checkpoint is not a completed result and cannot trigger QA.


async def reconcile_wait(invocation, bridge, db_path=None):
    """Replay only the idempotent approval draft; observe unknown checkpoint acks."""
    with ledger.connect(db_path) as db:
        _table(db)
        pending = db.execute('SELECT * FROM governance_action_wait WHERE invocation_id=?', (invocation,)).fetchone()
    if not pending or not pending['request_json'] or not pending['checkpoint_json']:
        return False
    args = json.loads(pending['request_json'])
    expiry = datetime.fromisoformat(args['expires_at'].replace('Z', '+00:00'))
    if expiry <= datetime.now(timezone.utc):
        from open_webui.utils.agentic_tool_gateway import get_action

        if get_action(invocation, args['action']['action_id'], db_path) is not None:
            raise ValueError('An existing host intent cannot receive a renewed approval')
        if pending['renewals'] >= 3:
            raise ValueError('Approval renewal budget exhausted; human intervention required')
        args['approval_id'] = (
            'agov-approval-'
            + digest([invocation, pending['action_fingerprint'], 'renewal', pending['renewals'] + 1])[:48]
        )
        args['expires_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with ledger._transaction(db_path) as db:
            updated = db.execute(
                'UPDATE governance_action_wait SET request_json=?,approval_id=?,renewals=renewals+1 WHERE invocation_id=? AND renewals=?',
                (canonical(args), args['approval_id'], invocation, pending['renewals']),
            )
            if updated.rowcount != 1:
                return False
            row = db.execute(
                'SELECT erp_project FROM governance_invocation WHERE invocation_id=?', (invocation,)
            ).fetchone()
            ledger._event(
                db,
                row[0],
                invocation,
                'expired_approval_draft_renewed',
                'trusted-tool-gateway',
                {'approval_id': args['approval_id']},
            )
    await bridge.erp.request(
        'POST', RPC + 'request_action_approval', json=args, headers={'X-ABIT-Agentic-Bridge': bridge.secret}
    )
    if pending['checkpoint_phase'] == 'published':
        return True
    event = json.loads(pending['checkpoint_json'])
    if pending['checkpoint_phase'] == 'pending':
        with ledger._transaction(db_path) as db:
            if (
                db.execute(
                    "UPDATE governance_action_wait SET checkpoint_phase='uncertain' WHERE invocation_id=? AND checkpoint_phase='pending'",
                    (invocation,),
                ).rowcount
                != 1
            ):
                return False
        receipt = await bridge.publish(event)
    else:
        response = await bridge.erp.request(
            'GET',
            '/api/method/abit_ai_assistant.agentic_control.get_result_receipt',
            params={'event_id': event['event_id'], 'project_id': event['project_id'], 'task_id': event['task_id']},
            headers={'X-ABIT-Agentic-Bridge': bridge.secret},
        )
        value = response.get('message') or {}
        if value.get('exists') is not True:
            return False
        receipt = {k: v for k, v in value.get('receipt', value).items() if k not in {'exists', 'content_sha256'}}
    if (
        receipt.get('event_id') != event['event_id']
        or receipt.get('run_id') != event['run_id']
        or receipt.get('task_id') != event['task_id']
        or not receipt.get('modified')
    ):
        raise ValueError('Approval checkpoint receipt does not match the original run')
    with ledger._transaction(db_path) as db:
        db.execute(
            "UPDATE governance_action_wait SET checkpoint_phase='published',checkpoint_receipt_json=? WHERE invocation_id=?",
            (canonical(receipt), invocation),
        )
    return True


async def resume(invocation, *, config, project_gate, bridge, app, db_path=None, adapters=None):
    from open_webui.utils.agentic_executor import complete_original

    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row['state'] != 'awaiting_approval':
        return None
    with ledger.connect(db_path) as db:
        _table(db)
        pending = db.execute('SELECT * FROM governance_action_wait WHERE invocation_id=?', (invocation,)).fetchone()
    if not pending:
        return None
    if not await reconcile_wait(invocation, bridge, db_path):
        return None
    action = json.loads(pending['action_json'])
    action['requested_at'] = int(time.time())
    packet = json.loads(row['work_packet'])
    fresh = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    for key in (set(packet) | set(fresh)) - {
        'source_modified',
        'packet_id',
        'content_sha256',
        'implementation_user',
        'implementation_run_id',
    }:
        if packet.get(key) != fresh.get(key):
            raise ValueError('Original infrastructure context changed while awaiting approval')
    await project_gate(row['erp_project'], config)
    with ledger._transaction(db_path) as db:
        current = ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'awaiting_approval'})
        control = db.execute(
            'SELECT state FROM governance_project_control WHERE erp_project=?', (row['erp_project'],)
        ).fetchone()
        if not current or not control or control[0] != 'enabled':
            return None
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        db.execute(
            "UPDATE governance_invocation SET state='running',lease_expires_at=?,updated_at=? WHERE invocation_id=?",
            (int(time.time()) + 120, int(time.time()), invocation),
        )
        ledger._event(
            db,
            row['erp_project'],
            invocation,
            'approved_action_resumed',
            'trusted-tool-gateway',
            {'action_id': action['action_id']},
        )
    beat = None
    try:

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                if not ledger.heartbeat(invocation, row['automation_run_id'], row['lease_token'], db_path):
                    return

        beat = asyncio.create_task(heartbeat())
        result = await execute_action(
            invocation,
            action,
            project_gate=project_gate,
            config=config,
            bridge=bridge,
            adapters=adapters,
            db_path=db_path,
        )
        record = make_record(row, packet, result, config)
        from open_webui.models.automations import Automations

        automation = await Automations.get_by_id(row['agent_id'])
        adapter = ITAdapter(app, project_gate, config, db_path, adapters)
        return await complete_original(
            row,
            packet,
            record,
            adapter,
            automation,
            bridge,
            {'modified': fresh['source_modified']},
            project_gate,
            config,
            db_path,
        )
    except ApprovalRequired:
        with ledger._transaction(db_path) as db:
            if ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'running'}):
                db.execute(
                    "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL WHERE invocation_id=?",
                    (invocation,),
                )
        return None
    except Exception as error:
        from open_webui.utils.agentic_tool_gateway import get_action

        if get_action(invocation, action['action_id'], db_path) is None:
            # No durable host intent exists: expiry/denial/preflight failure is
            # known before execution, so a fresh human decision may wake the
            # SAME original run and action. Unknown host intents stay fenced.
            with ledger._transaction(db_path) as db:
                if ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'running'}):
                    db.execute(
                        "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL,reason=? WHERE invocation_id=?",
                        ((type(error).__name__ + ': ' + str(error))[:500], invocation),
                    )
            return None
        ledger.finish_execution(
            invocation,
            row['automation_run_id'],
            row['lease_token'],
            'uncertain',
            reason='Original infrastructure action/result requires receipt reconciliation; do not rerun',
            db_path=db_path,
        )
        raise
    except BaseException:
        ledger.finish_execution(
            invocation,
            row['automation_run_id'],
            row['lease_token'],
            'uncertain',
            reason='Original infrastructure action/result requires receipt reconciliation; do not rerun',
            db_path=db_path,
        )
        raise
    finally:
        if beat:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)


async def recover_completed(
    invocation, *, config, project_gate, bridge, app=None, db_path=None, adapter=None, automation=None
):
    """Recover an already committed adapter receipt without calling execute_action."""
    from open_webui.utils.agentic_tool_gateway import get_action
    from open_webui.utils.agentic_executor import complete_original

    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row['target_role'] != 'it_manager' or row['state'] not in {'uncertain', 'cancel_requested'}:
        return None
    packet = json.loads(row['work_packet'])
    action = make_action(row, packet)
    result = get_action(invocation, action['action_id'], db_path)
    if not result or result['state'] != 'completed':
        return None
    fresh = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    managed = {
        'source_modified',
        'packet_id',
        'content_sha256',
        'implementation_user',
        'implementation_run_id',
        'attachments',
    }
    if any(packet.get(k) != fresh.get(k) for k in (set(packet) | set(fresh)) - managed):
        raise ValueError('Infrastructure context changed before original receipt recovery')
    if row['state'] != 'cancel_requested':
        await project_gate(row['erp_project'], config)
    record = make_record(row, packet, result, config)
    if automation is None:
        from open_webui.models.automations import Automations

        automation = await Automations.get_by_id(row['agent_id'])
    return await complete_original(
        row,
        packet,
        record,
        adapter or ITAdapter(app, project_gate, config, db_path),
        automation,
        bridge,
        {'modified': fresh['source_modified']},
        project_gate,
        config,
        db_path,
    )

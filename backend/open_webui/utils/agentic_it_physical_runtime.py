"""Bind physical execution/recovery to original Core and native approval receipts.

Only an internal typed runtime can register this path. Current execution checks
fresh authority; historical receipt reconciliation never authorizes execution.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_it_changes import ChangeReaders, TrustedChangeExecutor, change_adapters, digest, OPERATIONS
from open_webui.utils.agentic_contracts.validator import action_fingerprint
from open_webui.utils import agentic_tool_gateway as gateway


def require(value, message):
    if not value:
        raise ValueError(message)


def original_run(invocation, db_path=None):
    row = ledger.find(invocation, db_path, include_private=True)
    require(row and row['target_role'] == 'it_manager' and row.get('automation_run_id'), 'Original IT run is unavailable')
    return row['automation_run_id']


class PhysicalRuntime:
    def __init__(self, readers, executor):
        require(isinstance(readers, ChangeReaders) and isinstance(executor, TrustedChangeExecutor)
                and callable(getattr(executor, 'supports', None)), 'Typed physical readers, executor and resource registry required')
        self.readers, self.executor = readers, executor

    def _original(self, invocation, action_id, db_path):
        row = ledger.find(invocation, db_path, include_private=True)
        if not row or row['target_role'] != 'it_manager' or not row.get('automation_run_id') or not row.get('work_packet'):
            return None
        with ledger.connect(db_path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action'").fetchone():
                return None
            saved = db.execute('SELECT * FROM governance_action WHERE action_id=? AND invocation_id=?', (action_id, invocation)).fetchone()
        if saved is None:
            return None
        saved = dict(saved)
        action, before = json.loads(saved['action_json']), json.loads(saved['preflight_json'])
        if action.get('operation') not in OPERATIONS or before.get('kind') != 'it_change_plan' \
                or not before.get('plan_digest') or not action.get('parameters', {}).get('plan_digest'):
            return None  # Unregistered/historical adapters remain untouched.
        packet = json.loads(row['work_packet'])
        require(action.get('action_id') == action_id and action['actor'] == packet['binding']
                and all(action.get(key) == packet.get(key) for key in ('project_id', 'task_id', 'task_revision', 'repository', 'environment')),
                'Original physical action scope differs from its immutable invocation')
        template = packet.get('tool_action')
        require(isinstance(template, dict) and all(action.get(key) == template.get(key) for key in ('operation', 'risk', 'parameters')),
                'Original physical action differs from its immutable Task operation')
        require(saved['request_hash'] == gateway._digest([action_id, action_fingerprint(action)])
                and before.get('plan_digest') == action['parameters'].get('plan_digest'), 'Original physical action fingerprint differs')
        consumed = json.loads(saved['approval_json'] or 'null')
        require(isinstance(consumed, dict) and consumed.get('consumed') is True and consumed.get('receipt_id')
                and consumed.get('approval_id') and consumed.get('action_id') == action_id
                and consumed.get('invocation_id') == invocation and consumed.get('run_id') == row['automation_run_id']
                and consumed.get('action_fingerprint') == action_fingerprint(action)
                and consumed.get('project_id') == row['erp_project'] and consumed.get('task_id') == row['erp_task'],
                'Original consumed ERP approval receipt is unavailable or differs')
        return row, saved, action, before, consumed

    async def _native_approval(self, bridge, row, action, consumed):
        # Native historical reader verifies its immutable stored action digest.
        # It permits receipts under Hold/Stop and performs no new consumption.
        args = {'project_id': row['erp_project'], 'task_id': row['erp_task'], 'run_id': row['automation_run_id'],
                'invocation_id': row['invocation_id'], 'role': row['target_role'], 'agent_id': row['agent_id'],
                'action': action, 'action_fingerprint': action_fingerprint(action), 'approval_id': consumed['approval_id']}
        response = await bridge.erp.request('POST', gateway.RPC + 'get_action_approval', json=args,
                                            headers={'X-ABIT-Agentic-Bridge': bridge.secret})
        approval = response.get('message')
        require(isinstance(approval, dict) and approval.get('approval_id') == consumed['approval_id']
                and approval.get('action_fingerprint') == consumed['action_fingerprint']
                and approval.get('used_by_action_id') == action['action_id']
                and approval.get('project_id') == row['erp_project'] and approval.get('task_id') == row['erp_task'],
                'Native original approval consumption does not match this exact physical action')
        return approval

    def adapters(self, *, bridge, project_gate, config, db_path):
        registry = None
        async def consumed_grant(action, scope, before):
            original = self._original(scope['invocation_id'], action['action_id'], db_path)
            require(original is not None, 'Original physical action intent is unavailable')
            row, saved, stored_action, stored_before, consumed = original
            require(saved['state'] == 'uncertain' and stored_action == action and stored_before == before,
                    'Physical dispatch does not match its exact original intent')
            profile = gateway._profile(config, row['erp_project'])
            await gateway._fresh(row['invocation_id'], action, project_gate, config, bridge, profile,
                                 registry[action['operation']], db_path)
            approval = await self._native_approval(bridge, row, action, consumed)
            now = int(time.time())
            require(approval.get('status') == 'approved' and type(approval.get('approved_at')) is int
                    and type(approval.get('expires_at')) is int and approval['approved_at'] <= now < approval['expires_at'],
                    'New physical execution requires a currently valid original human approval')
            return {**consumed, 'task_revision': action['task_revision']}
        registry = change_adapters(readers=self.readers, executor=self.executor, grant_checker=consumed_grant)
        return registry

    async def reconcile_original(self, invocation, action_id, *, bridge, db_path):
        original = self._original(invocation, action_id, db_path)
        if original is None:
            return None
        row, saved, action, before, consumed = original
        if saved['state'] == 'completed':
            return gateway.get_action(invocation, action_id, db_path)
        require(saved['state'] == 'uncertain', 'Original physical action phase cannot be reconciled')
        await self._native_approval(bridge, row, action, consumed)
        # observe_original validates the protected authenticated host receipt.
        # No current gate/lease/preflight or postcondition authorizes a replay.
        receipt = await self.executor.observe_original(action_id, invocation, before['plan_digest'])
        if not isinstance(receipt, dict) or receipt.get('state') != 'completed':
            return None
        require(receipt.get('action_id') == action_id and receipt.get('invocation_id') == invocation
                and receipt.get('run_id') == row['automation_run_id'] and receipt.get('plan_digest') == before['plan_digest']
                and receipt.get('receipt_id') and receipt.get('receipt_mac')
                and isinstance(receipt.get('postcondition'), dict) and receipt['postcondition'].get('verified') is True,
                'Authenticated physical receipt tuple differs')
        safe = deepcopy(receipt)
        require(len(gateway._json(safe).encode())<=32000,'Authenticated original host receipt exceeds its evidence bound')
        result = {'executed': True, 'plan_digest': before['plan_digest'], 'original_receipt': safe,
                  'reconciled_original_receipt': True, 'new_host_execution': False}
        result['readback'] = {**receipt['postcondition'], 'executed': False,
            'attribution': 'Authenticated original host completion observation; current state acceptance remains independent.'}
        with gateway._connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM governance_action WHERE action_id=? AND invocation_id=?', (action_id, invocation)).fetchone()
            require(current and all(current[key] == saved[key] for key in ('request_hash', 'action_json', 'preflight_json', 'approval_json')),
                    'Original physical action changed during receipt reconciliation')
            if current['state'] == 'uncertain':
                db.execute("UPDATE governance_action SET state='completed',result_json=?,updated_at=? WHERE action_id=? AND invocation_id=? AND state='uncertain'",
                           (gateway._json(result), int(time.time()), action_id, invocation))
                gateway._event(db, action_id, 'original_physical_receipt_reconciled',
                               {'receipt_sha256': digest(safe), 'new_host_execution': False})
            else:
                require(current['state'] == 'completed', 'Original physical action phase changed')
            db.execute('COMMIT')
        return gateway.get_action(invocation, action_id, db_path)

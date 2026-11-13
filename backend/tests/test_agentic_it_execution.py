"""Real ledger/gateway IT flow with fake ERP and only temporary-file inspection."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import time
import types
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T


class ApprovalBridge:
    def __init__(self, fixture):
        self.f = fixture
        self.erp = self
        self.secret = 'fixture-only'
        self.calls = []
        self.drafts = {}
        self.checkpoints = {}
        self.approval = None
        self.lose_draft = False
        self.lose_checkpoint = False
        self.absent_checkpoint = False

    async def packet(self, *args, **kwargs):
        return self.f.packet(role='it_manager')

    async def request(self, method, path, **kwargs):
        operation = path.rsplit('.', 1)[-1]
        self.calls.append((method, operation, deepcopy(kwargs)))
        args = kwargs.get('json', {})
        if operation == 'request_action_approval':
            key = args['approval_id']
            prior = self.drafts.setdefault(key, deepcopy(args))
            if prior != args:
                raise AssertionError('Approval draft changed under original key')
            if self.lose_draft:
                self.lose_draft = False
                raise TimeoutError('Draft committed; response lost')
            return {'message': {'approval_id': key}}
        if operation == 'get_result_receipt':
            key = kwargs['params']['event_id']
            value = self.checkpoints.get(key)
            return {
                'message': (
                    {'exists': False}
                    if self.absent_checkpoint or not value
                    else {'exists': True, 'receipt': deepcopy(value)}
                )
            }
        if operation == 'get_action_approval':
            return {'message': deepcopy(self.approval)}
        if operation == 'consume_action_approval':
            return {
                'message': {
                    'consumed': True,
                    'approval_id': args['approval_id'],
                    'action_id': args['action']['action_id'],
                    'action_fingerprint': args['action_fingerprint'],
                    'receipt_id': 'approval-consumption-fixture',
                }
            }
        raise AssertionError((method, path))

    async def publish(self, event):
        self.calls.append(('POST', 'publish', deepcopy(event)))
        receipt = {key: event[key] for key in ('event_id', 'run_id', 'task_id')}
        receipt['modified'] = 'modified-checkpoint'
        self.checkpoints[event['event_id']] = receipt
        if self.lose_checkpoint:
            self.lose_checkpoint = False
            raise TimeoutError('Checkpoint committed; response lost')
        return receipt

    def count(self, operation):
        return sum(call[1] == operation for call in self.calls)


class ITExecution(Fixture):
    def setUp(self):
        super().setUp()
        self.template = {'request_key': 'fixture-inspect', 'operation': 'inspect', 'risk': 'routine', 'parameters': {}}
        self.gateway = self.load('agentic_tool_gateway')
        self.artifacts = self.load('agentic_artifacts')
        self.it = self.load('agentic_it_execution')
        self.contracts = __import__('open_webui.utils.agentic_contracts.validator', fromlist=['validator'])
        self.root = Path(self.temp.name)
        self.artifact_root = self.root / 'evidence'
        root_patch = patch.object(self.artifacts, 'artifact_directory', return_value=self.artifact_root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.config = {
            'agentic_tool_gateway': {
                'projects': {
                    P: {
                        'host_id': socket.gethostname(),
                        'roots': {'fixture': str(self.root)},
                        'services': [],
                        'operations': ['inspect', 'restart_service', 'install_official_package'],
                        'environment_class': 'development',
                    }
                }
            }
        }
        self.bridge = ApprovalBridge(self)
        self.host_calls = 0
        self.completed = []

        async def complete(row, packet, record, adapter, automation, bridge, receipt, gate, config, db_path):
            self.completed.append((deepcopy(row), deepcopy(packet), deepcopy(record)))
            self.g.finish_execution(row['invocation_id'], row['automation_run_id'], row['lease_token'], db_path=db_path)
            return {'completed_original': row['automation_run_id']}

        executor = types.ModuleType('open_webui.utils.agentic_executor')
        executor.complete_original = complete
        executor.DotAdapter = types.SimpleNamespace(
            submit=AsyncMock(side_effect=AssertionError('IT may never call Dot'))
        )
        automations = types.ModuleType('open_webui.models.automations')
        automations.Automations = types.SimpleNamespace(
            get_by_id=AsyncMock(return_value=types.SimpleNamespace(id='fixture-automation'))
        )
        self.runtime_patch = patch.dict(
            sys.modules, {'open_webui.utils.agentic_executor': executor, 'open_webui.models.automations': automations}
        )
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def packet(self, task=T, role='developer', project=P):
        value = super().packet(task, role, project)
        if role == 'it_manager':
            value['tool_action'] = deepcopy(self.template)
            value['tool_scopes'] = ['inspect_infrastructure', 'execute_infrastructure']
            value['content_sha256'] = self.bridge_module.packet_digest(value)
        return value

    def adapter(self):
        return self.it.ITAdapter(None, self.gate, self.config, self.path, self.registry())

    def registry(self):
        async def before(*args):
            return {'fixture_preflight': True}

        async def execute(*args):
            self.host_calls += 1
            return {'fixture_registered_handler_completed': True}

        return {'restart_service': self.gateway.TrustedAdapter(before, execute, capability='execute_infrastructure')}

    def privileged(self):
        self.template = {
            'request_key': 'fixture-restart',
            'operation': 'restart_service',
            'risk': 'privileged',
            'parameters': {},
        }

    def state(self, row):
        return self.g.find(row['invocation_id'], self.path, include_private=True)

    def pending(self, row):
        with self.g.connect(self.path) as db:
            return dict(
                db.execute(
                    'SELECT * FROM governance_action_wait WHERE invocation_id=?', (row['invocation_id'],)
                ).fetchone()
            )

    async def wait(self):
        self.privileged()
        row = self.running(role='it_manager')
        adapter = self.adapter()
        packet = self.packet(role='it_manager')
        try:
            await adapter.submit(row, packet, row['automation_run_id'], None, self.bridge, before_submit=AsyncMock())
        except self.gateway.ApprovalRequired as error:
            await self.it.suspend(
                row['invocation_id'], packet, adapter, self.bridge, {'modified': 'modified-started'}, error, self.path
            )
        else:
            raise AssertionError('Privileged fixture requires human approval')
        return row, adapter

    async def resume(self, row):
        return await self.it.resume(
            row['invocation_id'],
            config=self.config,
            project_gate=self.gate,
            bridge=self.bridge,
            app=None,
            db_path=self.path,
            adapters=self.registry(),
        )

    def approve(self, row, **override):
        action = self.it.make_action(row, self.packet(role='it_manager'))
        now = int(time.time())
        self.bridge.approval = {
            'schema_version': 1,
            'approval_id': self.pending(row)['approval_id'],
            'project_id': P,
            'task_id': T,
            'action_fingerprint': self.contracts.action_fingerprint(action),
            'approved_by': 'human@example.invalid',
            'approved_at': now - 1,
            'expires_at': now + 600,
            'status': 'approved',
            'used_by_action_id': None,
            **override,
        }

    async def test_preflight_rejects_missing_action_and_unimplemented_operation(self):
        row = self.enqueue(role='it_manager')
        packet = self.packet(role='it_manager')
        del packet['tool_action']
        with self.assertRaises(self.gateway.CapabilityUnavailable):
            await self.adapter().preflight(row, packet)
        packet = self.packet(role='it_manager')
        packet['tool_action']['operation'] = 'install_official_package'
        with self.assertRaises(self.gateway.CapabilityUnavailable):
            await self.adapter().preflight(row, packet)
        self.assertEqual(self.state(row)['state'], 'queued')
        self.assertEqual(self.host_calls, 0)

    async def test_readonly_inspect_records_real_receipt_without_dot_or_model(self):
        row = self.running(role='it_manager')
        packet = self.packet(role='it_manager')
        before = AsyncMock()
        adapter = self.adapter()
        await adapter.preflight(row, packet)
        record = await adapter.submit(row, packet, row['automation_run_id'], None, self.bridge, before_submit=before)
        before.assert_awaited_once()
        self.assertEqual(record['model_requests'], 0)
        self.assertEqual(record['transport'], 'trusted-tool-gateway')
        result = json.loads(record['response_text'].removeprefix('AGOV RESULT: '))
        receipt = json.loads((self.artifact_root / 'trusted-action-receipt.json').read_text())
        self.assertGreater(receipt['result']['roots']['fixture']['total_bytes'], 0)
        self.assertTrue(receipt['result']['read_only'])
        self.assertEqual(result['outcome'], 'ready_for_review')
        self.assertEqual(result['test_results'][0]['status'], 'skipped')
        self.assertEqual(os.stat(self.artifact_root / 'trusted-action-receipt.json').st_mode & 0o777, 0o640)
        self.assertEqual(self.it.make_record(row, packet, receipt, self.config), record)
        receipt['result']['changed'] = True
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            self.it.make_record(row, packet, receipt, self.config)

    async def test_privileged_wait_and_approval_resume_same_run_action_once(self):
        row, adapter = await self.wait()
        action_id = adapter.action['action_id']
        run = row['automation_run_id']
        self.assertEqual(self.state(row)['state'], 'awaiting_approval')
        self.assertIsNone(self.gateway.get_action(row['invocation_id'], action_id, self.path))
        self.assertIsNone(await self.resume(row))
        self.assertEqual(self.state(row)['automation_run_id'], run)
        self.assertEqual(self.host_calls, 0)
        self.approve(row)
        result = await self.resume(row)
        self.assertEqual(result, {'completed_original': run})
        self.assertEqual(self.host_calls, 1)
        self.assertEqual(self.gateway.get_action(row['invocation_id'], action_id, self.path)['state'], 'completed')
        self.assertIsNone(await self.resume(row))
        self.assertEqual(self.host_calls, 1)
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)

    async def test_lost_draft_response_repairs_only_original_draft_and_checkpoint(self):
        self.bridge.lose_draft = True
        with self.assertRaises(TimeoutError):
            await self.wait()
        row = self.g.list_invocations(P, self.path)[0]
        row = self.state(row)
        pending = self.pending(row)
        self.assertEqual(row['state'], 'awaiting_approval')
        self.assertEqual(pending['checkpoint_phase'], 'pending')
        original = json.loads(pending['request_json'])
        self.assertTrue(await self.it.reconcile_wait(row['invocation_id'], self.bridge, self.path))
        self.assertEqual(self.pending(row)['checkpoint_phase'], 'published')
        self.assertEqual(len(self.bridge.drafts), 1)
        self.assertEqual(next(iter(self.bridge.drafts.values())), original)
        self.assertEqual(self.host_calls, 0)

    async def test_lost_checkpoint_response_uses_receipt_get_without_repeat_post(self):
        self.bridge.lose_checkpoint = True
        with self.assertRaises(TimeoutError):
            await self.wait()
        row = self.state(self.g.list_invocations(P, self.path)[0])
        self.assertEqual(self.pending(row)['checkpoint_phase'], 'uncertain')
        self.bridge.absent_checkpoint = True
        self.assertFalse(await self.it.reconcile_wait(row['invocation_id'], self.bridge, self.path))
        self.bridge.absent_checkpoint = False
        self.assertTrue(await self.it.reconcile_wait(row['invocation_id'], self.bridge, self.path))
        self.assertEqual(self.bridge.count('publish'), 1)
        self.assertEqual(self.bridge.count('get_result_receipt'), 2)
        self.assertEqual(self.host_calls, 0)

    async def test_expired_denied_or_self_approval_keeps_original_wait_without_host_intent(self):
        row, adapter = await self.wait()
        for override in (
            {'expires_at': int(time.time()) - 1},
            {'status': 'revoked'},
            {'approved_by': 'ai-operations@abitcons.com'},
        ):
            self.approve(row, **override)
            self.assertIsNone(await self.resume(row))
            self.assertEqual(self.state(row)['state'], 'awaiting_approval')
            self.assertEqual(self.state(row)['automation_run_id'], row['automation_run_id'])
            self.assertIsNone(self.gateway.get_action(row['invocation_id'], adapter.action['action_id'], self.path))
            self.assertEqual(self.host_calls, 0)

    async def test_expired_draft_renews_id_with_fresh_human_decision_without_action_replay(self):
        row, adapter = await self.wait()
        pending = self.pending(row)
        args = json.loads(pending['request_json'])
        old = args['approval_id']
        args['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.g._transaction(self.path) as db:
            db.execute(
                'UPDATE governance_action_wait SET request_json=? WHERE invocation_id=?',
                (json.dumps(args), row['invocation_id']),
            )
        self.assertIsNone(await self.resume(row))
        renewed = self.pending(row)
        self.assertNotEqual(renewed['approval_id'], old)
        self.assertEqual(renewed['renewals'], 1)
        self.assertEqual(self.host_calls, 0)
        self.approve(row)
        await self.resume(row)
        self.assertEqual(self.host_calls, 1)
        self.assertEqual(self.completed[0][0]['automation_run_id'], row['automation_run_id'])

    async def test_paused_task_and_pm_checkbox_off_block_before_host_execution(self):
        row, _ = await self.wait()
        self.approve(row)
        self.controls.task_control(P, T, 'pause', actor='human@example.invalid', db_path=self.path)
        with self.assertRaises(ValueError):
            await self.resume(row)
        self.assertEqual(self.host_calls, 0)
        self.controls.task_control(P, T, 'resume', actor='human@example.invalid', db_path=self.path)
        self.gate_value['pm_task_automation_enabled'] = False
        self.assertIsNone(await self.resume(row))
        self.assertEqual(self.host_calls, 0)
        self.assertEqual(self.state(row)['state'], 'awaiting_approval')

    async def test_completed_gateway_receipt_recovery_never_executes_host_again(self):
        row = self.running(role='it_manager')
        record = await self.adapter().submit(
            row, self.packet(role='it_manager'), row['automation_run_id'], None, self.bridge, before_submit=AsyncMock()
        )
        self.g.finish_execution(
            row['invocation_id'], row['automation_run_id'], row['lease_token'], 'uncertain', db_path=self.path
        )
        with patch.object(self.it, 'execute_action', AsyncMock(side_effect=AssertionError('No host replay'))):
            result = await self.it.recover_completed(
                row['invocation_id'], config=self.config, project_gate=self.gate, bridge=self.bridge, db_path=self.path
            )
        self.assertEqual(result, {'completed_original': row['automation_run_id']})
        self.assertEqual(self.completed[0][2], record)

    async def test_unknown_gateway_result_is_not_recovered_or_reexecuted(self):
        row, _ = await self.wait()
        with self.g._transaction(self.path) as db:
            db.execute(
                "UPDATE governance_invocation SET state='uncertain' WHERE invocation_id=?", (row['invocation_id'],)
            )
        with patch.object(self.it, 'execute_action', AsyncMock(side_effect=AssertionError('No host replay'))):
            self.assertIsNone(
                await self.it.recover_completed(
                    row['invocation_id'],
                    config=self.config,
                    project_gate=self.gate,
                    bridge=self.bridge,
                    db_path=self.path,
                )
            )
        self.assertEqual(self.host_calls, 0)
        self.assertEqual(self.completed, [])

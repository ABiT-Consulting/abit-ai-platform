"""A positive original ERP start receipt recovers one never-submitted run only."""

from copy import deepcopy
import json
from unittest.mock import patch
from test_agentic_controls_events import Fixture, P, T


class StartBridge:
    def __init__(self, fixture):
        self.f = fixture
        self.erp = self
        self.secret = 'fixture-only'
        self.calls = []
        self.exists = True
        self.receipt = None
        self.sha = None
        self.changed = None

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        if method != 'GET' or not path.endswith('get_result_receipt'):
            raise AssertionError('Only original receipt GET is authorized')
        return {
            'message': (
                {'exists': False}
                if not self.exists
                else {'exists': True, **deepcopy(self.receipt), 'content_sha256': self.sha}
            )
        }

    async def packet(self, *args):
        packet = self.f.packet()
        packet['source_modified'] = 'modified-started'
        if self.changed:
            self.changed(packet)
        packet['content_sha256'] = self.f.bridge_module.packet_digest(packet)
        return packet


class StartRecovery(Fixture):
    def setUp(self):
        super().setUp()
        self.start = self.load('agentic_start_recovery')
        self.bridge = StartBridge(self)

    def original(self):
        queued = self.enqueue()
        row = self.g.claim_invocation(
            queued['invocation_id'], 'run-original-fixture', gate=self.gate_value, db_path=self.path
        )
        packet = json.loads(row['work_packet'])
        event = {
            'schema_version': 1,
            'event_id': 'agov-start-' + self.bridge_module.digest(row['invocation_id'])[:48],
            'type': 'run.started',
            'project_id': P,
            'task_id': T,
            'task_revision': packet['task_revision'],
            'run_id': row['automation_run_id'],
            'invocation_id': row['invocation_id'],
            'role': row['target_role'],
            'agent_id': row['agent_id'],
            'summary': 'Original governed execution claimed.',
            'expected_modified': packet['source_modified'],
            'expected_project_revision': packet['project_revision'],
            'work_packet_digest': self.bridge_module.packet_digest(packet),
            'artifacts': [],
        }
        self.bridge.receipt = {key: event[key] for key in ('event_id', 'task_id', 'run_id')}
        self.bridge.receipt.update(modified='modified-started', status='Working', durable=True, duplicate=False)
        self.bridge.sha = self.bridge_module.digest(event)
        self.start.record_start_intent(row, event, self.path)
        self.g.finish_execution(
            row['invocation_id'], row['automation_run_id'], row['lease_token'], 'uncertain', db_path=self.path
        )
        return self.g.find(row['invocation_id'], self.path, include_private=True), event

    async def confirm(self, row):
        return await self.start.get_confirmed_start(row, self.bridge, self.gate, {}, self.path)

    def state(self, row):
        return self.g.find(row['invocation_id'], self.path, include_private=True)

    async def test_lost_ack_positive_get_reclaims_identical_run_and_fence_once(self):
        row, event = self.original()
        receipt = await self.confirm(row)
        self.assertEqual(receipt, self.bridge.receipt)
        self.assertEqual(self.bridge.calls[0][0], 'GET')
        result = self.start.reclaim_same(row['invocation_id'], receipt, gate=self.gate_value, db_path=self.path)
        self.assertEqual(result['state'], 'running')
        self.assertIsNone(result['submitted_at'])
        for key in ('automation_run_id', 'lease_token', 'lease_fence', 'packet_hash', 'dispatch_attempts'):
            self.assertEqual(result[key], row[key])
        self.assertIsNone(
            self.start.reclaim_same(row['invocation_id'], receipt, gate=self.gate_value, db_path=self.path)
        )
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)

    async def test_missing_receipt_remains_uncertain_without_new_run_or_start_post(self):
        row, _ = self.original()
        self.bridge.exists = False
        self.assertIsNone(await self.confirm(row))
        self.assertEqual(self.state(row), row)
        self.assertTrue(all(call[0] == 'GET' for call in self.bridge.calls))

    async def test_wrong_event_hash_or_run_receipt_fails_without_reclaim(self):
        row, _ = self.original()
        self.bridge.sha = '0' * 64
        with self.assertRaisesRegex(ValueError, 'content hash'):
            await self.confirm(row)
        self.bridge.sha = None
        self.bridge.receipt['run_id'] = 'other-run'
        with self.assertRaisesRegex(ValueError, 'original active run'):
            await self.confirm(row)
        self.assertEqual(self.state(row), row)

    async def test_changed_business_inputs_or_post_start_task_cas_denied(self):
        row, _ = self.original()
        self.bridge.changed = lambda packet: packet.update(objective='Changed human scope')
        with self.assertRaisesRegex(ValueError, 'WorkPacket changed'):
            await self.confirm(row)
        self.bridge.changed = lambda packet: packet.update(source_modified='later-human-modified')
        with self.assertRaisesRegex(ValueError, 'Task changed'):
            await self.confirm(row)
        self.assertEqual(self.state(row), row)

    async def test_revoked_pm_checkbox_and_local_task_pause_cannot_reclaim(self):
        row, _ = self.original()
        self.gate_value['pm_task_automation_enabled'] = False
        with self.assertRaises(ValueError):
            await self.confirm(row)
        self.gate_value['pm_task_automation_enabled'] = True
        self.controls.task_control(P, T, 'pause', actor='human@example.invalid', db_path=self.path)
        with self.assertRaises(ValueError):
            await self.confirm(row)
        self.assertEqual(self.state(row), row)

    async def test_fence_replacement_denies_even_an_original_positive_receipt(self):
        row, _ = self.original()
        with self.g._transaction(self.path) as db:
            db.execute('UPDATE governance_task_lease SET fence=fence+1 WHERE erp_project=? AND erp_task=?', (P, T))
        self.assertIsNone(await self.confirm(row))
        self.assertEqual(self.bridge.calls, [])

    async def test_submitted_original_is_never_resumed_through_start_recovery(self):
        row, _ = self.original()
        with self.g._transaction(self.path) as db:
            db.execute('UPDATE governance_invocation SET submitted_at=1 WHERE invocation_id=?', (row['invocation_id'],))
        self.assertIsNone(await self.confirm(row))
        self.assertEqual(self.bridge.calls, [])

    async def test_journal_and_receipt_are_immutable_and_missing_intent_fails_closed(self):
        row, event = self.original()
        self.assertEqual(
            self.start.record_start_receipt(row['invocation_id'], self.bridge.receipt, self.path), self.bridge.receipt
        )
        altered = {**self.bridge.receipt, 'modified': 'other-modified'}
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.start.record_start_receipt(row['invocation_id'], altered, self.path)
        with self.g._transaction(self.path) as db:
            db.execute('DELETE FROM governance_start WHERE invocation_id=?', (row['invocation_id'],))
        self.assertIsNone(await self.confirm(row))
        self.assertEqual(self.state(row), row)

    async def test_confirmed_receipt_cache_still_checks_fresh_gate_and_task_inputs(self):
        row, _ = self.original()
        await self.confirm(row)
        self.bridge.exists = False
        self.assertEqual(await self.confirm(row), self.bridge.receipt)
        self.assertEqual(len(self.bridge.calls), 1)
        self.bridge.changed = lambda packet: packet.update(environment='other-environment')
        with self.assertRaisesRegex(ValueError, 'WorkPacket changed'):
            await self.confirm(row)

    async def test_existing_local_result_or_host_intent_prevents_resubmission(self):
        row, _ = self.original()
        with self.g._transaction(self.path) as db:
            db.execute('CREATE TABLE governance_local_result(invocation_id TEXT)')
            db.execute('INSERT INTO governance_local_result VALUES(?)', (row['invocation_id'],))
        self.assertIsNone(await self.confirm(row))
        self.assertEqual(self.bridge.calls, [])

    async def test_reclaim_rechecks_controls_and_exact_original_receipt(self):
        row, _ = self.original()
        receipt = await self.confirm(row)
        with self.assertRaisesRegex(ValueError, 'exact original'):
            self.start.reclaim_same(
                row['invocation_id'], {**receipt, 'modified': 'fake-modified'}, gate=self.gate_value, db_path=self.path
            )
        self.controls.task_control(P, T, 'pause', actor='human@example.invalid', db_path=self.path)
        with self.assertRaises(ValueError):
            self.start.reclaim_same(row['invocation_id'], receipt, gate=self.gate_value, db_path=self.path)
        self.assertEqual(self.state(row), row)

    async def test_bad_start_event_cannot_be_journaled_before_publish(self):
        queued = self.enqueue()
        row = self.g.claim_invocation(
            queued['invocation_id'], 'run-original-fixture', gate=self.gate_value, db_path=self.path
        )
        with self.assertRaisesRegex(ValueError, 'immutable run'):
            self.start.record_start_intent(row, {'type': 'run.started'}, self.path)

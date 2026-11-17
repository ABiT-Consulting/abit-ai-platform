"""A positive original ERP start receipt recovers one never-submitted run only."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock, patch
import types
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
        readiness = patch.object(self.start, 'READINESS_ROOT', self.path.parent / 'absent-readiness-proofs')
        readiness.start()
        self.addCleanup(readiness.stop)
        self.bridge = StartBridge(self)

    def original(self, *, stable=False, mark_uncertain=True):
        queued = self.enqueue()
        row = self.g.claim_invocation(
            queued['invocation_id'], self.bridge_module.stable_run_id(queued['invocation_id']) if stable else 'run-original-fixture', gate=self.gate_value, db_path=self.path
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
        if mark_uncertain:
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


class ObservationStartBridge(StartBridge):
    """Authenticated controlled ERP peer, retaining native immutable receipts."""
    def __init__(self, fixture):
        super().__init__(fixture)
        self.secret = 'offline-only-exact-observation-signature-0001'
        self.modified = 'modified-started'
        self.status = 'Working'
        self.observations = {}

    async def packet(self, *args):
        packet = self.f.packet()
        packet['source_modified'] = self.modified
        if self.changed:
            self.changed(packet)
        packet['content_sha256'] = self.f.bridge_module.packet_digest(packet)
        return packet

    async def get(self, doctype, name):
        assert (doctype, name) == ('Task', T)
        return {'name': T, 'project': P, 'status': self.status}

    async def request(self, method, path, **kwargs):
        if not path.endswith('record_execution_observation'):
            return await super().request(method, path, **kwargs)
        self.calls.append((method, path, deepcopy(kwargs)))
        assert method == 'POST'
        event = json.loads(kwargs['json']['event'])
        self.f.bridge_module.verify_event(event, kwargs['headers'], self.secret)
        row = self.f.g.find(event['invocation_id'], self.f.path, include_private=True)
        assert (event['project_id'], event['task_id'], event['run_id'], event['agent_id']) == (
            P, T, row['automation_run_id'], row['agent_id'])
        assert event['work_packet_digest'] == json.loads(row['work_packet'])['content_sha256']
        if event['event_id'] not in self.observations:
            previous = self.modified
            status = 'Recorded' if self.status == 'Working' else 'Superseded'
            if status == 'Recorded':
                self.modified = 'modified-native-observation'
                self.status = 'Blocked'
            self.observations[event['event_id']] = {
                **{key: event[key] for key in ('event_id', 'project_id', 'task_id', 'run_id')},
                'durable': True, 'status': status, 'previous_modified': previous,
                'modified': self.modified}
        return {'message': deepcopy(self.observations[event['event_id']])}


class ObservationStartRecovery(Fixture):
    original = StartRecovery.original
    state = StartRecovery.state

    def setUp(self):
        super().setUp()
        self.start = self.load('agentic_start_recovery')
        readiness = patch.object(self.start, 'READINESS_ROOT', self.path.parent / 'absent-readiness-proofs')
        readiness.start(); self.addCleanup(readiness.stop)
        status = self.load('erp_status_write')
        status.LOCK_ROOT = self.path.parent / 'task-write-locks'
        self.bridge = ObservationStartBridge(self)

    def observation_fixture(self, *, ownership=True, mark_uncertain=True):
        from test_agentic_planning import backlog, task_row
        original_packet = self.packet()
        if ownership:
            original_packet['project_backlog'] = backlog(P, [task_row(T)])
        original_packet['content_sha256'] = self.bridge_module.packet_digest(original_packet)
        selected = patch.object(self, 'packet', side_effect=lambda *args: deepcopy(original_packet))
        selected.start(); self.addCleanup(selected.stop)
        self.bridge = ObservationStartBridge(self)
        row, event = self.original(stable=True, mark_uncertain=mark_uncertain)
        automation = types.SimpleNamespace(id=row['agent_id'], user_id='owner-fixture', meta={
            'agentic_project': P, 'agentic_role': 'developer',
            'erp_role_user': original_packet['binding']['erp_user'], 'governance_managed': True})
        return row, event, automation

    async def deliver_observation(self):
        observations = self.load('agentic_execution_observations')
        self.assertEqual(len(await observations.deliver_pending(self.bridge, db_path=self.path)), 1)
        self.assertEqual(self.bridge.status, 'Blocked')
        return observations

    async def recover_observed(self, row, automation, adapter):
        recovery = self.load('agentic_recovery')
        with patch.object(recovery, 'FORMAT_ROOT', self.path.parent / 'absent-format-proofs'):
            return await recovery.recover_original(row['invocation_id'], config={},
                project_gate=self.gate, bridge=self.bridge, db_path=self.path,
                reader=AsyncMock(return_value=None), adapter=adapter, automation=automation)

    async def test_known_start_then_observation_recovers_same_first_submission_once(self):
        row, event, automation = self.observation_fixture()
        original_receipt = deepcopy(self.bridge.receipt)
        await self.deliver_observation()
        from test_agentic_planning import backlog, task_row
        self.bridge.changed = lambda packet: packet.update(project_backlog=backlog(P,
            packet['project_backlog']['tasks'] + [task_row('TASK-unrelated-new-work')]))
        sends = []
        async def submit(claimed, packet, run_id, specialist, bridge, *, before_submit):
            await before_submit()
            sends.append((claimed['invocation_id'], run_id, claimed['lease_token'], claimed['lease_fence']))
            return None  # A lost original provider reply is uncertainty, never replacement authority.
        adapter = types.SimpleNamespace(submit=AsyncMock(side_effect=submit))
        self.assertIsNone(await self.recover_observed(row, automation, adapter))
        current = self.state(row)
        self.assertIsNotNone(current['submitted_at'])
        self.assertEqual(sends, [(row['invocation_id'], row['automation_run_id'], row['lease_token'], row['lease_fence'])])
        for key in ('invocation_id', 'automation_run_id', 'lease_token', 'lease_fence', 'work_packet', 'packet_hash', 'dispatch_attempts'):
            self.assertEqual(current[key], row[key])
        self.assertEqual(self.start.original_start_receipt(current, self.path), original_receipt)
        self.assertEqual(self.bridge.receipt, original_receipt)
        self.assertIsNone(await self.recover_observed(current, automation, adapter))
        self.assertEqual(adapter.submit.await_count, 1)
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)
        posts = [path for method, path, _ in self.bridge.calls if method == 'POST']
        self.assertEqual(posts, ['/api/method/abit_ai_assistant.agentic_control.record_execution_observation'])

    async def test_observation_cas_is_used_for_publication_without_rewriting_start(self):
        row, event, automation = self.observation_fixture()
        original_receipt = deepcopy(self.bridge.receipt)
        await self.deliver_observation()
        async def submit(claimed, packet, run_id, specialist, bridge, *, before_submit):
            await before_submit()
            return {'response_text': 'original controlled provider response'}
        adapter = types.SimpleNamespace(submit=AsyncMock(side_effect=submit))
        executor = self.load('agentic_executor')
        completion = AsyncMock(return_value={'original_callback': True})
        with patch.object(executor, 'complete_original', completion):
            result = await self.recover_observed(row, automation, adapter)
        self.assertEqual(result, {'original_callback': True})
        forwarded = completion.await_args.args[6]
        self.assertEqual(forwarded, {**original_receipt, 'modified': self.bridge.modified})
        self.assertEqual(self.start.original_start_receipt(self.state(row), self.path), original_receipt)
        self.assertEqual(adapter.submit.await_count, 1)

    async def test_human_modified_owner_srs_or_semantic_edit_after_observation_denies_submission(self):
        row, event, automation = self.observation_fixture()
        await self.deliver_observation()
        from test_agentic_planning import backlog
        def owner_edit(packet):
            packet['project_backlog']['tasks'][0]['owner_context_sha256'] = 'c'*64
            packet['project_backlog'] = backlog(P, packet['project_backlog']['tasks'])
        cases = [lambda packet: packet.update(source_modified='later-human-modified'),
                 owner_edit,
                 lambda packet: packet['srs'].update(sha256='f'*64),
                 lambda packet: packet.update(objective='Changed business acceptance')]
        adapter = types.SimpleNamespace(submit=AsyncMock())
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.bridge.changed = mutation
                with self.assertRaises(ValueError):
                    await self.recover_observed(row, automation, adapter)
                adapter.submit.assert_not_awaited()
                self.assertEqual(self.state(row), row)
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)

    async def test_human_edit_between_receipt_check_and_actual_submission_is_denied(self):
        row, event, automation = self.observation_fixture()
        await self.deliver_observation()
        sends = []
        async def submit(claimed, packet, run_id, specialist, bridge, *, before_submit):
            bridge.changed = lambda fresh: fresh.update(source_modified='raced-human-edit')
            await before_submit()
            sends.append(run_id)
        adapter = types.SimpleNamespace(submit=AsyncMock(side_effect=submit))
        with self.assertRaisesRegex(ValueError, 'Task changed after original claim'):
            await self.recover_observed(row, automation, adapter)
        self.assertEqual(sends, [])
        current = self.state(row)
        self.assertIsNone(current['submitted_at'])
        for key in ('invocation_id', 'automation_run_id', 'lease_token', 'lease_fence', 'work_packet', 'packet_hash', 'dispatch_attempts'):
            self.assertEqual(current[key], row[key])
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)

    async def test_normal_first_submission_rejects_new_human_owner_without_modified_change(self):
        row, event, automation = self.observation_fixture(mark_uncertain=False)
        from test_agentic_planning import backlog
        def human_owner(packet):
            packet['project_backlog']['tasks'][0]['owner_context_sha256'] = 'c'*64
            packet['project_backlog'] = backlog(P, packet['project_backlog']['tasks'])
        self.bridge.changed = human_owner
        self.assertEqual((await self.bridge.packet())['source_modified'], self.bridge.receipt['modified'])
        sends = []
        async def submit(claimed, packet, run_id, specialist, bridge, *, before_submit):
            await before_submit()
            sends.append(run_id)
        adapter = types.SimpleNamespace(submit=AsyncMock(side_effect=submit))
        executor = self.load('agentic_executor')
        observations = self.load('agentic_execution_observations')
        with patch.object(observations, 'acknowledged_modified', side_effect=AssertionError('Normal same-CAS submission cannot rely on observations')):
            with self.assertRaisesRegex(ValueError, 'Work packet changed before actual relay submission'):
                await executor.submit_bound_original(row, json.loads(row['work_packet']), automation,
                    adapter, self.bridge, self.bridge.receipt, self.gate, {}, self.path)
        self.assertEqual(sends, [])
        self.assertEqual(self.state(row), row)
        self.assertIsNone(self.state(row)['submitted_at'])

    async def test_pending_superseded_wrong_or_disconnected_receipt_cannot_advance_cas(self):
        row, event, automation = self.observation_fixture()
        observations = self.load('agentic_execution_observations')
        original_modified = self.bridge.receipt['modified']
        fresh = await self.bridge.packet()
        fresh['source_modified'] = 'modified-native-observation'
        with self.assertRaises(ValueError):
            observations.acknowledged_modified(row, original_modified, fresh['source_modified'], self.path, fresh_packet=fresh)
        await self.deliver_observation()
        self.assertEqual(observations.acknowledged_modified(row, original_modified, fresh['source_modified'],
            self.path, fresh_packet=fresh), fresh['source_modified'])
        with self.g.connect(self.path) as db:
            saved = db.execute('SELECT event_id,receipt_json FROM governance_execution_observation').fetchone()
        correct = json.loads(saved['receipt_json'])
        for replacement in (None, [], {**correct, 'status': []},
                            {**correct, 'status': 'Superseded'},
                            {**correct, 'status': 'Unrecognized'},
                            {key: value for key, value in correct.items() if key not in {'previous_modified', 'modified'}},
                            {**correct, 'run_id': 'foreign-run'},
                            {**correct, 'previous_modified': 'unrelated-human-write'}):
            with self.subTest(receipt=replacement):
                with self.g.connect(self.path) as db:
                    db.execute('UPDATE governance_execution_observation SET receipt_json=? WHERE event_id=?',
                               (json.dumps(replacement), saved['event_id']))
                with self.assertRaises(ValueError):
                    observations.acknowledged_modified(row, original_modified, fresh['source_modified'],
                        self.path, fresh_packet=fresh)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_execution_observation SET receipt_json=? WHERE event_id=?',
                       (json.dumps(correct), saved['event_id']))
        for bad in ('z'*64, None):
            changed = deepcopy(fresh)
            changed['project_backlog']['tasks'][0]['owner_context_sha256'] = bad
            with self.subTest(owner_fingerprint=bad), self.assertRaises(ValueError):
                observations.acknowledged_modified(row, original_modified, fresh['source_modified'],
                    self.path, fresh_packet=changed)
        changed = deepcopy(fresh); changed['project_backlog']['tasks'] = [None]
        with self.assertRaises(ValueError):
            observations.acknowledged_modified(row, original_modified, fresh['source_modified'],
                self.path, fresh_packet=changed)
        for malformed in ('{bad JSON', 'null', '[]'):
            with self.g.connect(self.path) as db:
                db.execute('UPDATE governance_execution_observation SET event_json=? WHERE event_id=?',
                           (malformed, saved['event_id']))
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                observations.acknowledged_modified(row, original_modified, fresh['source_modified'],
                    self.path, fresh_packet=fresh)

    async def test_lease_expiry_then_superseded_seed_observation_keeps_only_exact_owned_cas(self):
        row, event, automation = self.observation_fixture(mark_uncertain=False)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET lease_expires_at=0 WHERE invocation_id=?', (row['invocation_id'],))
        self.assertEqual(self.g.recoverable(self.path)['stale_marked_uncertain'], 1)
        observations = await self.deliver_observation()
        self.assertEqual(len(self.bridge.observations), 1)
        observations.seed_uncertain(P, self.path)
        self.assertEqual(len(await observations.deliver_pending(self.bridge, db_path=self.path)), 1)
        with self.g.connect(self.path) as db:
            saved = db.execute('SELECT event_json,receipt_json FROM governance_execution_observation').fetchall()
        reasons = {json.loads(entry['event_json'])['reason']: json.loads(entry['receipt_json'])['status'] for entry in saved}
        self.assertEqual(reasons, {'lease_expired': 'Recorded', 'submission_unconfirmed': 'Superseded'})
        current = self.state(row)
        fresh = await self.bridge.packet()
        self.assertEqual(observations.acknowledged_modified(current, self.bridge.receipt['modified'],
            fresh['source_modified'], self.path, fresh_packet=fresh), self.bridge.modified)
        receipt = await self.start.get_confirmed_start(current, self.bridge, self.gate, {}, self.path)
        self.assertEqual(receipt, self.bridge.receipt)
        self.assertIsNone(self.state(row)['submitted_at'])
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)

    async def test_legacy_packet_without_original_ownership_evidence_keeps_strict_cas(self):
        row, event, automation = self.observation_fixture(ownership=False)
        self.assertNotIn('project_backlog', json.loads(row['work_packet']))
        await self.deliver_observation()
        adapter = types.SimpleNamespace(submit=AsyncMock())
        with self.assertRaisesRegex(ValueError, 'ERP Task changed after the original start receipt'):
            await self.recover_observed(row, automation, adapter)
        adapter.submit.assert_not_awaited()
        self.assertEqual(self.state(row), row)
        self.assertNotIn('project_backlog', json.loads(self.state(row)['work_packet']))

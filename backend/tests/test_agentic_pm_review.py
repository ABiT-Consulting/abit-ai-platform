"""Actual ledger/executor and controlled PM turn with real private-file bytes.

ERP source-proof boundaries are separately exercised by the native ERP tests.
This test never calls a paid provider or imports the application against live DB.
"""

from __future__ import annotations

from copy import deepcopy
import importlib
import json
import types
import unittest
from unittest.mock import AsyncMock

import test_agentic_executor_flow as executor_fixture

PROJECT, TASK, ROLE_USERS, SECRET = (
    getattr(executor_fixture, name) for name in ('PROJECT', 'TASK', 'ROLE_USERS', 'SECRET')
)


class PMReview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = executor_fixture.ExecutorFlow(
            'test_developer_then_separate_qa_pass_with_real_artifact_bytes_and_receipt'
        )
        await self.f.asyncSetUp()
        self.addCleanup(self.f.doCleanups)
        self.pm = importlib.import_module('open_webui.utils.agentic_pm_review')
        self.dispatch = AsyncMock()
        self.original_request = self.f.erp.request
        self.original_packet = self.f.erp.packet
        self.f.erp.request = self.request
        self.f.erp.packet = self.packet
        self.frozen = None
        self.pm_events = []
        self.lose = set()
        self.lose_before = set()
        self.bad_target = False
        self.extra_backlog = False
        self.pm_results = []
        self.control_sequence = 0
        self.native_intents = {}
        self.native_acks = {}
        self.resume_proofs = []
        secret = self.f.root / 'bridge.secret'
        secret.write_text(SECRET)
        self.f.config['agentic_bridge_token_file'] = str(secret)
        self.f.adapter.outcomes['project_manager'] = 'reviewed'

        def result_mutator(result):
            if result['actor']['role'] != 'project_manager':
                return result
            review = self.review
            result['implementation_invocation_id'] = review['implementation_invocation_id']
            result['implementation_agent_id'] = review['implementation_agent_id']
            result['test_results'][0]['test_id'] = 'PM-QA-REVIEW'
            if result['outcome'] == 'reviewed':
                result['qa_review_ack'] = self.f.bridge_module.digest(review)
            return result

        self.f.adapter.result_mutator = result_mutator

    @property
    def review(self):
        with self.f.ledger.connect(self.f.db) as db:
            return self.pm._verified_source(db, 'qa-fixture-review-00001')[1]

    def packet(self, role):
        if role != 'project_manager' or self.f.erp.task['status'] != 'QA APPROVED':
            return self.original_packet(role)
        if self.frozen:
            packet = deepcopy(self.frozen)
            current = self.pm.review_id(PROJECT, TASK, packet['qa_review'], packet.get('pm_review_continuation'))
            prior = next((event for event in reversed(self.pm_results) if event['invocation_id'] == current), None)
            generation = (packet.get('pm_review_continuation') or {}).get('generation', 0)
            if prior and prior['result_contract']['outcome'] in {'continue', 'blocked'} and generation < 3:
                receipt = self.f.erp.receipts[prior['event_id']]
                value = {
                    'previous_invocation_id': current,
                    'previous_result_sha256': self.f.bridge_module.digest(prior['result_contract']),
                    'previous_receipt_sha256': self.f.bridge_module.digest(self.pm.receipt_identity(receipt)),
                    'generation': generation + 1,
                }
                if prior['result_contract']['outcome'] == 'blocked':
                    eligible = [
                        proof
                        for proof in self.resume_proofs
                        if proof['resume_control_sequence'] > receipt['pm_review_control_sequence']
                    ]
                    if not eligible:
                        return packet
                    value.update(sorted(eligible, key=lambda proof: proof['resume_control_sequence'])[0])
                packet['pm_review_continuation'] = value
                packet['packet_id'] = 'fixture-pm-continuation-' + self.f.bridge_module.digest(value)[:32]
                packet['content_sha256'] = self.f.bridge_module.packet_digest(packet)
            return packet
        packet = self.original_packet(role)
        packet['qa_review'] = self.review
        task = {
            'task_id': TASK,
            'task_revision': self.f.bridge_module.digest(self.f.erp.task),
            'source_modified': self.f.erp.task['modified'],
            'subject': 'Controlled feature',
            'description': packet['objective'],
            'status': 'QA APPROVED',
            'priority': 'Medium',
            'due_date': '',
            'parent_task': '',
            'is_group': False,
            'dependencies': [],
            'assignees': [ROLE_USERS['qa']],
            'owner_context_sha256': self.f.bridge_module.digest([]),
            'managed': True,
            'dispatch_released': True,
            'executor_role': 'developer',
            'srs_revision': 'fixture-srs-v1',
            'requirement_ids': packet['requirement_ids'],
            'acceptance_tests': 'One original invocation',
            'source_run_id': '',
        }
        tasks = [task]
        if self.extra_backlog:
            tasks.append(
                {
                    **task,
                    'task_id': 'TASK-unrelated-fixture',
                    'status': 'Completed',
                    'task_revision': self.f.bridge_module.digest('unrelated-business-change'),
                }
            )
        context = [
            {
                'task_id': row['task_id'],
                'task_revision': row['task_revision'],
                'owner_context_sha256': row['owner_context_sha256'],
            }
            for row in tasks
            if row['task_id'] != TASK
        ]
        packet['project_backlog'] = {
            'project_id': PROJECT,
            'complete': True,
            'total_count': len(tasks),
            'page_size': 50,
            'snapshot_sha256': self.f.bridge_module.digest({'project_id': PROJECT, 'tasks': tasks}),
            'context_sha256': self.f.bridge_module.digest({'project_id': PROJECT, 'tasks': context}),
            'context_excluded_task_ids': [TASK],
            'tasks': tasks,
        }
        packet['acceptance_tests'] = [
            {
                'id': 'PM-QA-REVIEW',
                'requirement_ids': packet['requirement_ids'],
                'method': 'readback',
                'expected': 'Acknowledge exact QA approval and coordinate next work.',
            }
        ]
        packet['content_sha256'] = self.f.bridge_module.packet_digest(packet)
        return packet

    async def request(self, method, path, **kwargs):
        if 'agentic_control_fence.' in path:
            if path.endswith('get_control_intent'):
                scope = kwargs['params']['task_id']
                return {
                    'message': {
                        'project_id': PROJECT,
                        'task_id': scope,
                        'intent': self.native_intents.get(scope),
                        'execution_veto': False,
                        'project_veto': False,
                    }
                }
            if path.endswith('get_control_application_receipt'):
                args = kwargs['params']
                return {
                    'message': deepcopy(
                        self.native_acks.get(
                            (args['task_id'], args['command_id'], args['control_sequence']), {'exists': False}
                        )
                    )
                }
            if path.endswith('acknowledge_control'):
                ack = kwargs['json']['ack']
                self.f.bridge_module.verify_event(ack, kwargs['headers'], SECRET)
                receipt = {'exists': True, 'durable': True, **ack}
                self.native_acks[(ack['task_id'], ack['command_id'], ack['control_sequence'])] = receipt
                self.control_sequence = max(self.control_sequence, ack['control_sequence'])
                return {'message': receipt}
        if 'agentic_pm_review.receive_result' not in path:
            result = await self.original_request(method, path, **kwargs)
            if path.endswith('upload_file') and kwargs['data']['doctype'] == 'AI Governance Record':
                self.f.erp.files[result['message']['name']]['attached_to_doctype'] = 'AI Governance Record'
            return result
        event = kwargs['json']['event']
        self.f.bridge_module.verify_event(event, kwargs['headers'], SECRET)
        self.pm_events.append(deepcopy(event))
        if event['event_id'] in self.f.erp.receipts:
            return {'message': deepcopy(self.f.erp.receipts[event['event_id']])}
        if event['type'] in self.lose_before:
            raise TimeoutError('No acknowledgment; original coordination is fenced')
        self.assertEqual('QA APPROVED', self.f.erp.task['status'])
        self.assertEqual(self.f.erp.task['modified'], event['expected_modified'])
        if event['type'] == 'pm.review.started':
            self.frozen = deepcopy(event['work_packet'])
        receipt = {
            'event_id': event['event_id'],
            'run_id': event['run_id'],
            'task_id': TASK,
            'modified': self.f.erp.task['modified'],
            'status': 'QA APPROVED',
            'durable': True,
            'pm_review_state': (
                'started'
                if event['type'] == 'pm.review.started'
                else ('reviewed' if event['type'] == 'pm.reviewed' else 'held')
            ),
        }
        if event['type'] == 'pm.review.started':
            name = 'AGOV-' + self.f.bridge_module.digest(['pm_review_start', [PROJECT, event['invocation_id']]])[:48]
            receipt['artifact_target'] = {
                'doctype': 'AI Governance Record',
                'name': name if not self.bad_target else 'AGOV-wrong',
            }
        self.f.erp.receipts[event['event_id']] = receipt
        if event['type'] == 'pm.review.checkpoint':
            receipt['pm_review_control_sequence'] = self.control_sequence
        if event['type'] != 'pm.review.started':
            self.pm_results.append(deepcopy(event))
        if event['type'] in self.lose:
            self.lose.remove(event['type'])
            raise TimeoutError('ERP committed original PM journal; response lost')
        return {'message': deepcopy(receipt)}

    async def human_resume(self, sequence, scope=TASK, action='resume'):
        intent = {
            'project_id': PROJECT,
            'task_id': scope,
            'command_id': 'human-control-' + str(sequence),
            'control_sequence': sequence,
            'action': action,
            'actor': 'alice@example.invalid',
            'retained_veto': False,
        }
        self.native_intents[scope] = intent
        controls = importlib.import_module('open_webui.utils.agentic_control_intents')
        event = {'project': PROJECT, 'task': scope, 'revision': self.f.erp.project['modified'], 'payload': intent}
        self.assertTrue(await controls.apply_current(event, self.f.bridge, db_path=self.f.db))
        ack = self.native_acks[(scope, intent['command_id'], sequence)]
        if ack['applied_state'] == 'enabled':
            self.resume_proofs.append({'resume_command_id': intent['command_id'], 'resume_control_sequence': sequence})

    async def approved(self):
        dev, _, _ = await self.f.enqueue()
        await self.f.execute(dev['invocation_id'])
        qa, _, _ = await self.f.handoff(dev['invocation_id'], 'qa', 'qa-fixture-review-00001')
        receipt = await self.f.execute(qa['invocation_id'])
        saved = self.f.modules['agentic_result_store'].get_result(qa['invocation_id'], self.f.db)
        self.f.ledger.verify_completion(
            dev['invocation_id'],
            qa['invocation_id'],
            self.f.bridge_module.digest(saved['result']['evidence']),
            self.f.bridge_module.digest(receipt),
            'independent-qa-fixture',
            db_path=self.f.db,
        )
        return qa

    async def ensure(self):
        return await self.pm.ensure_review(
            'qa-fixture-review-00001',
            config=self.f.config,
            project_gate=self.f.project_gate,
            dispatch=self.dispatch,
            db_path=self.f.db,
            bridge=self.f.bridge,
        )

    async def test_actual_pm_turn_follows_verified_qa_without_reopening_task_or_duplicate_run(self):
        await self.approved()
        before = deepcopy(self.f.erp.task)
        files = {k for k, v in self.f.erp.files.items() if v['attached_to_doctype'] == 'Task'}
        self.assertFalse(self.pm.review_complete(PROJECT, TASK, self.f.db))
        row = await self.ensure()
        repeated = await self.ensure()
        self.assertEqual(row['invocation_id'], repeated['invocation_id'])
        self.assertEqual(1, self.dispatch.await_count)
        receipt = await self.f.execute(row['invocation_id'])
        self.assertIsNotNone(receipt)
        self.assertTrue(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        self.assertTrue(self.pm.review_complete(PROJECT, TASK, self.f.db))
        self.assertEqual(before, self.f.erp.task)
        self.assertEqual(files, {k for k, v in self.f.erp.files.items() if v['attached_to_doctype'] == 'Task'})
        self.assertEqual(['developer', 'qa', 'project_manager'], [v[2] for v in self.f.adapter.calls])
        self.assertEqual(['pm.review.started', 'pm.reviewed'], [v['type'] for v in self.pm_events])
        self.assertEqual(
            1, len([f for f in self.f.erp.files.values() if f['attached_to_doctype'] == 'AI Governance Record'])
        )
        self.assertTrue(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))

    async def test_unverified_or_caller_asserted_pm_review_cannot_enqueue(self):
        await self.approved()
        packet = self.packet('project_manager')
        _, gate = await self.f.project_gate(PROJECT, self.f.config)
        with self.assertRaisesRegex(ValueError, 'verified QA review factory'):
            self.f.ledger.enqueue(
                invocation_id='forged-pm-review-00001',
                project=PROJECT,
                task=TASK,
                from_role='erp',
                target_role='project_manager',
                task_revision=packet['task_revision'],
                context='Caller claims QA passed',
                work_packet=packet,
                gate=gate,
                actor='caller',
                db_path=self.f.db,
            )
        with self.f.ledger.connect(self.f.db) as db:
            db.execute(
                "UPDATE governance_invocation SET state='execution_finished' WHERE invocation_id='qa-fixture-review-00001'"
            )
        with self.assertRaisesRegex(ValueError, 'verified independent QA'):
            await self.ensure()
        self.assertFalse([v for v in self.f.adapter.calls if v[2] == 'project_manager'])

    async def test_missing_factory_after_verification_is_recovered_from_latest_qa(self):
        await self.approved()
        self.assertEqual(['qa-fixture-review-00001'], self.pm.pending_reviews(PROJECT, self.f.db))
        await self.ensure()
        self.assertEqual([], self.pm.pending_reviews(PROJECT, self.f.db))

    async def test_queued_review_reuses_original_packet_after_unrelated_backlog_changes(self):
        await self.approved()
        row = await self.ensure()
        original = self.f.ledger.find(row['invocation_id'], self.f.db, include_private=True)
        self.extra_backlog = True
        current = self.packet('project_manager')
        self.assertNotEqual(json.loads(original['work_packet'])['project_backlog'], current['project_backlog'])
        repeated = await self.ensure()
        self.assertEqual(row['invocation_id'], repeated['invocation_id'])
        self.assertEqual(
            original['work_packet'],
            self.f.ledger.find(row['invocation_id'], self.f.db, include_private=True)['work_packet'],
        )
        self.assertEqual(1, self.dispatch.await_count)
        receipt = await self.f.execute(row['invocation_id'])
        self.assertTrue(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        self.assertEqual(1, len([call for call in self.f.adapter.calls if call[2] == 'project_manager']))

    async def test_queued_review_does_not_reuse_changed_task_or_srs_context(self):
        await self.approved()
        row = await self.ensure()
        self.f.erp.task['revision'] = 'changed-business-inputs'
        with self.assertRaisesRegex(ValueError, 'differs from the verified QA'):
            await self.ensure()
        self.f.erp.task['revision'] = 'task-inputs-1'
        self.f.erp.mutate_packet = lambda packet: {**packet, 'srs': {**packet['srs'], 'sha256': '9' * 64}}
        with self.assertRaisesRegex(ValueError, 'business context changed'):
            await self.ensure()
        self.assertFalse([call for call in self.f.adapter.calls if call[2] == 'project_manager'])

    async def test_lost_pm_result_ack_reads_original_journal_without_model_or_file_replay(self):
        await self.approved()
        row = await self.ensure()
        self.lose.add('pm.reviewed')
        self.assertIsNone(await self.f.execute(row['invocation_id']))
        calls = len(self.f.adapter.calls)
        uploads = len(self.f.erp.uploads)
        receipt = await self.f.executor.reconcile_publication(row['invocation_id'], self.f.bridge, self.f.db)
        self.assertTrue(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        self.assertEqual(calls, len(self.f.adapter.calls))
        self.assertEqual(uploads, len(self.f.erp.uploads))

    async def test_unknown_pm_result_ack_stays_fenced_without_reposting_or_replaying(self):
        await self.approved()
        row = await self.ensure()
        self.lose_before.add('pm.reviewed')
        self.assertIsNone(await self.f.execute(row['invocation_id']))
        self.assertIsNone(await self.f.executor.reconcile_publication(row['invocation_id'], self.f.bridge, self.f.db))
        await self.ensure()
        self.assertEqual(1, len([v for v in self.f.adapter.calls if v[2] == 'project_manager']))
        self.assertFalse(self.pm.review_complete(PROJECT, TASK, self.f.db))

    async def test_lost_pm_start_ack_recovers_same_original_before_first_submission(self):
        await self.approved()
        row = await self.ensure()
        self.lose.add('pm.review.started')
        self.assertIsNone(await self.f.execute(row['invocation_id']))
        original = self.f.ledger.find(row['invocation_id'], self.f.db, include_private=True)
        self.assertIsNone(original['submitted_at'])
        self.assertFalse([v for v in self.f.adapter.calls if v[2] == 'project_manager'])
        automation = types.SimpleNamespace(
            id=row['agent_id'],
            user_id='verified-owner',
            meta={
                'governance_managed': True,
                'agentic_project': PROJECT,
                'agentic_role': 'project_manager',
                'erp_role_user': ROLE_USERS['project_manager'],
            },
        )

        async def no_original(*args):
            return None

        recovery = importlib.import_module('open_webui.utils.agentic_recovery')
        receipt = await recovery.recover_original(
            row['invocation_id'],
            config=self.f.config,
            project_gate=self.f.project_gate,
            bridge=self.f.bridge,
            db_path=self.f.db,
            reader=no_original,
            adapter=self.f.adapter,
            automation=automation,
        )
        self.assertIsNotNone(receipt)
        self.assertTrue(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        calls = [v for v in self.f.adapter.calls if v[2] == 'project_manager']
        self.assertEqual([(row['invocation_id'], original['automation_run_id'], 'project_manager')], calls)

    async def test_blocked_pm_review_holds_downstream_without_reopening_feature_or_auto_repeat(self):
        await self.approved()
        before = deepcopy(self.f.erp.task)
        row = await self.ensure()
        self.f.adapter.outcomes['project_manager'] = 'blocked'
        receipt = await self.f.execute(row['invocation_id'])
        self.assertIsNotNone(receipt)
        self.assertFalse(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        self.assertFalse(self.pm.review_complete(PROJECT, TASK, self.f.db))
        self.assertEqual('blocked', self.f.ledger.find(row['invocation_id'], self.f.db)['state'])
        await self.ensure()
        self.assertEqual(1, self.dispatch.await_count)
        self.assertEqual(before, self.f.erp.task)

    async def test_cross_record_pm_artifact_target_cannot_publish_evidence(self):
        await self.approved()
        row = await self.ensure()
        self.bad_target = True
        uploads = len(self.f.erp.uploads)
        self.assertIsNone(await self.f.execute(row['invocation_id']))
        self.assertEqual(uploads, len(self.f.erp.uploads))
        self.assertFalse(self.pm.review_complete(PROJECT, TASK, self.f.db))

    async def test_human_resumed_blocked_then_continue_then_reviewed_preserves_every_old_turn(self):
        await self.approved()
        feature = deepcopy(self.f.erp.task)
        rows = []
        receipts = []
        row = await self.ensure()
        self.f.adapter.outcomes['project_manager'] = 'blocked'
        receipt = await self.f.execute(row['invocation_id'])
        self.assertFalse(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        rows.append(row)
        receipts.append(receipt)
        self.assertEqual(['qa-fixture-review-00001'], self.pm.pending_reviews(PROJECT, self.f.db))
        await self.human_resume(1)
        row = await self.ensure()
        self.assertNotEqual(rows[0]['invocation_id'], row['invocation_id'])
        self.f.adapter.outcomes['project_manager'] = 'continue'
        receipt = await self.f.execute(row['invocation_id'])
        self.assertFalse(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        rows.append(row)
        receipts.append(receipt)
        row = await self.ensure()
        self.f.adapter.outcomes['project_manager'] = 'reviewed'
        receipt = await self.f.execute(row['invocation_id'])
        self.assertTrue(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
        rows.append(row)
        receipts.append(receipt)
        head = self.f.ledger.find(row['invocation_id'], self.f.db)
        for old, old_receipt in zip(rows, receipts):
            self.pm.finish_review(old['invocation_id'], old_receipt, self.f.db)
        for _ in range(3):
            self.assertEqual(head['invocation_id'], (await self.ensure())['invocation_id'])
        self.assertTrue(self.pm.review_complete(PROJECT, TASK, self.f.db))
        self.assertEqual(feature, self.f.erp.task)
        self.assertEqual(3, len([call for call in self.f.adapter.calls if call[2] == 'project_manager']))
        self.assertEqual(3, len({call[1] for call in self.f.adapter.calls if call[2] == 'project_manager'}))

    async def test_blocked_review_rejects_stale_resume_and_allows_fresh_project_resume_ack(self):
        await self.approved()
        await self.human_resume(5)
        row = await self.ensure()
        self.f.adapter.outcomes['project_manager'] = 'blocked'
        receipt = await self.f.execute(row['invocation_id'])
        self.pm.finish_review(row['invocation_id'], receipt, self.f.db)
        self.assertEqual(row['invocation_id'], (await self.ensure())['invocation_id'])
        await self.human_resume(6, scope='')
        next_row = await self.ensure()
        self.assertNotEqual(row['invocation_id'], next_row['invocation_id'])
        self.assertEqual(1, len([call for call in self.f.adapter.calls if call[2] == 'project_manager']))

    async def test_known_continue_is_bounded_to_three_new_logical_turns(self):
        await self.approved()
        self.f.adapter.outcomes['project_manager'] = 'continue'
        last = None
        for generation in range(4):
            row = await self.ensure()
            receipt = await self.f.execute(row['invocation_id'])
            self.assertFalse(self.pm.finish_review(row['invocation_id'], receipt, self.f.db))
            last = row
        self.assertEqual(last['invocation_id'], (await self.ensure())['invocation_id'])
        self.assertEqual(4, len([call for call in self.f.adapter.calls if call[2] == 'project_manager']))
        self.assertIn('human ERP intervention', self.f.ledger.find(last['invocation_id'], self.f.db)['reason'])


if __name__ == '__main__':
    unittest.main()

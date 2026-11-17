"""Real SQLite project claims and native pickup boundaries; no network/live DB."""
import asyncio
import importlib
import json
import sys
import types
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import test_agentic_ledger_fences as fixture

P, T, GATE, g = fixture.P, fixture.T, fixture.GATE, fixture.g
SECOND = 'TASK-2026-second'


class ProjectLane(unittest.TestCase):
    packet = fixture.DurableLedger.packet
    enqueue = fixture.DurableLedger.enqueue
    race = fixture.DurableLedger.race
    handoff = fixture.DurableLedger.handoff

    def setUp(self):
        fixture.DurableLedger.setUp(self)
        self.modules = patch.dict(sys.modules, {'open_webui.utils.agentic_governance': g})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.q = importlib.import_module('open_webui.utils.agentic_project_queue')

    def claim(self, ident='invocation-00000001'):
        return g.claim_invocation(ident, 'run-' + ident, gate=GATE,
                                  db_path=self.db, serialize_project=True)

    def second(self):
        return self.enqueue('invocation-00000002', task=SECOND,
                            work_packet=self.packet(task=SECOND))

    def finish(self, row, **kw):
        g.mark_submitted(row['invocation_id'], row['automation_run_id'], row['lease_token'],
                         gate=GATE, db_path=self.db)
        g.finish_execution(row['invocation_id'], row['automation_run_id'], row['lease_token'],
                           db_path=self.db, **kw)

    def test_distinct_concurrent_tasks_have_one_claim_and_preserve_other_queue(self):
        self.enqueue(); self.second()
        results = self.race(lambda n: self.claim('invocation-0000000' + str(n + 1)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        second = g.find('invocation-00000002', self.db)
        self.assertEqual(second['state'], 'queued')
        self.assertEqual(second['dispatch_attempts'], 0)
        self.assertIsNone(second['automation_run_id'])

    def test_finished_developer_keeps_lane_through_qa_handoff(self):
        self.enqueue(); self.second()
        source = self.claim(); self.finish(source)
        self.assertIsNone(self.claim('invocation-00000002'))
        self.handoff(source)
        self.assertIsNotNone(self.claim('handoff-0000000001'))
        self.assertIsNone(self.claim('invocation-00000002'))

    def test_existing_uncertain_run_without_lane_fences_new_task(self):
        self.enqueue(); self.second()
        original = g.claim_invocation('invocation-00000001', 'original-run',
                                      gate=GATE, db_path=self.db)
        self.finish(original, outcome='uncertain')
        self.assertIsNone(self.claim('invocation-00000002'))
        self.assertEqual(g.find('invocation-00000001', self.db)['state'], 'uncertain')

    def test_waiting_parent_yields_lane_to_child_task(self):
        self.enqueue(); self.second()
        source = self.claim(); self.finish(source)
        with g.connect(self.db) as db:
            db.execute("CREATE TABLE governance_lifecycle_wait(source TEXT PRIMARY KEY,project TEXT,phase TEXT)")
            db.execute("INSERT INTO governance_lifecycle_wait VALUES(?,?,'waiting')",
                       (source['invocation_id'], P))
        self.assertIsNotNone(self.claim('invocation-00000002'))

    def test_verified_qa_gap_keeps_lane_until_pm_review_completed(self):
        self.enqueue(); self.second()
        source = self.claim(); self.finish(source)
        self.handoff(source)
        qa = self.claim('handoff-0000000001'); self.finish(qa, qa_verdict='pass')
        g.verify_completion(source['invocation_id'], qa['invocation_id'], 'a'*64,
                            'b'*64, 'independent-proof', db_path=self.db)
        # Exact QA proof exists but no PM review journal/turn has been created.
        self.assertIsNone(self.claim('invocation-00000002'))
        with g.connect(self.db) as db:
            db.execute("CREATE TABLE governance_pm_review(qa_invocation_id TEXT,phase TEXT,review_invocation_id TEXT)")
            db.execute("INSERT INTO governance_pm_review VALUES(?,'completed',NULL)", (qa['invocation_id'],))
        self.assertIsNotNone(self.claim('invocation-00000002'))

    def test_native_concurrency_admits_two_independent_tasks_and_fences_third(self):
        packet = self.packet(); packet['project_execution_policy'] = {'max_concurrency': 2}
        self.enqueue(work_packet=packet)
        packet2 = self.packet(task=SECOND); packet2['project_execution_policy'] = {'max_concurrency': 2}
        self.enqueue('invocation-00000002', task=SECOND, work_packet=packet2)
        packet3 = self.packet(task='TASK-third'); packet3['project_execution_policy'] = {'max_concurrency': 2}
        self.enqueue('invocation-00000003', task='TASK-third', work_packet=packet3)
        results = self.race(lambda n: self.claim('invocation-0000000' + str(n + 1)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 2)
        self.assertIsNone(self.claim('invocation-00000003'))
        self.assertEqual(g.find('invocation-00000003', self.db)['dispatch_attempts'], 0)
        with g.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_project_lane').fetchone()[0], 2)

    def test_single_lane_schema_migrates_without_losing_original_owner(self):
        self.enqueue(); self.second()
        with g.connect(self.db) as db:
            db.execute('CREATE TABLE governance_project_lane(project TEXT PRIMARY KEY,task TEXT,invocation_id TEXT,updated_at INTEGER)')
            db.execute('INSERT INTO governance_project_lane VALUES(?,?,?,0)', (P, T, 'invocation-00000001'))
        self.assertIsNone(self.claim('invocation-00000002'))
        self.assertIsNotNone(self.claim())
        with g.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT task,invocation_id FROM governance_project_lane').fetchone()[:],
                             (T, 'invocation-00000001'))

    def test_queued_priority_and_due_date_order_precede_creation_order(self):
        packet = self.packet(); packet['project_backlog'] = {'tasks': [{'task_id': T, 'priority': 'Low', 'due_date': ''}]}
        self.enqueue(work_packet=packet)
        packet2 = self.packet(task=SECOND); packet2['project_backlog'] = {'tasks': [{'task_id': SECOND, 'priority': 'Urgent', 'due_date': '2026-11-18'}]}
        self.enqueue('invocation-00000002', task=SECOND, work_packet=packet2)
        self.assertIsNone(self.claim())
        self.assertIsNotNone(self.claim('invocation-00000002'))

    def publish_blocker(self, row, *, phase='published', status='Blocked'):
        result = {'invocation_id': row['invocation_id'], 'outcome': 'blocked'}
        event = {'invocation_id': row['invocation_id'], 'project_id': P,
                 'task_id': row['erp_task'], 'run_id': row['automation_run_id'],
                 'event_id': 'actual-blocker-event', 'type': 'run.blocked', 'result_contract': result}
        receipt = {'event_id': 'actual-blocker-event', 'task_id': row['erp_task'],
                   'run_id': row['automation_run_id'], 'status': status, 'durable': True}
        with g.connect(self.db) as db:
            db.execute("""CREATE TABLE governance_result(invocation_id TEXT PRIMARY KEY,run_id TEXT,
                result_sha256 TEXT,result_json TEXT,event_json TEXT,phase TEXT,receipt_json TEXT)""")
            db.execute('INSERT INTO governance_result VALUES(?,?,?,?,?,?,?)',
                       (row['invocation_id'], row['automation_run_id'], g._digest(result),
                        json.dumps(result), json.dumps(event), phase, json.dumps(receipt)))

    def test_durable_native_blocker_yields_without_releasing_original_task_lease(self):
        self.enqueue(); self.second()
        source = self.claim(); self.finish(source)
        self.publish_blocker(source)
        self.assertIsNotNone(self.claim('invocation-00000002'))
        original = g.find(source['invocation_id'], self.db)
        self.assertEqual(original['state'], 'execution_finished')
        with g.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT invocation_id FROM governance_task_lease WHERE erp_task=?',
                                        (T,)).fetchone()[0], source['invocation_id'])

    def test_durable_blocker_does_not_settle_uncertain_tool_call(self):
        self.enqueue(); self.second()
        source = self.claim(); self.finish(source)
        self.publish_blocker(source)
        with g.connect(self.db) as db:
            db.execute('CREATE TABLE governance_action(invocation_id TEXT,state TEXT)')
            db.execute("INSERT INTO governance_action VALUES(?,'uncertain')", (source['invocation_id'],))
        self.assertIsNone(self.claim('invocation-00000002'))
        with g.connect(self.db) as db:
            db.execute("UPDATE governance_action SET state='completed'")
        self.assertIsNotNone(self.claim('invocation-00000002'))

    def test_paused_published_finished_task_yields_but_running_pause_retains_fence(self):
        self.enqueue(); self.second()
        source = self.claim()
        controls = importlib.import_module('open_webui.utils.agentic_controls')
        controls.task_control(P, T, 'pause', actor='owner', db_path=self.db)
        self.assertIsNone(self.claim('invocation-00000002'))
        self.finish(source)
        self.publish_blocker(source, status='Pending Review')
        self.assertIsNotNone(self.claim('invocation-00000002'))

    def test_unpublished_or_mismatched_blocker_does_not_yield(self):
        self.enqueue(); self.second()
        source = self.claim(); self.finish(source)
        self.publish_blocker(source, phase='pending')
        self.assertIsNone(self.claim('invocation-00000002'))
        with g.connect(self.db) as db:
            db.execute("UPDATE governance_result SET phase='published',receipt_json=?",
                       (json.dumps({'durable': True, 'status': 'Blocked', 'task_id': 'TASK-other'}),))
        self.assertIsNone(self.claim('invocation-00000002'))

    def test_paused_queued_task_does_not_starve_next_eligible_task(self):
        self.enqueue(); self.second()
        controls = importlib.import_module('open_webui.utils.agentic_controls')
        controls.task_control(P, T, 'pause', actor='owner', db_path=self.db)
        self.assertIsNotNone(self.claim('invocation-00000002'))


class NativePickup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = ProjectLane()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.q, self.db = self.fixture.q, self.fixture.db
        self.calls, self.done = [], set()
        self.bridge = types.SimpleNamespace(secret='offline-only', erp=types.SimpleNamespace(request=self.request))
        self.rows = [self.row('TASK-low', priority='Low'),
                     self.row('TASK-dependent', priority='Urgent', dependencies=['TASK-low']),
                     self.row('TASK-ready', priority='High')]

    def row(self, ident, **kw):
        return {'task_id': ident, 'task_revision': ident + '-revision',
                'owner_context_sha256': 'a'*64, 'managed': False, 'status': 'Open',
                'is_group': False, 'assignees': [], 'dependencies': [], 'parent_task': '',
                'priority': 'Medium', 'due_date': '', **kw}

    async def request(self, method, url, **kw):
        self.calls.append((method, url, deepcopy(kw)))
        body = kw['json']
        return {'message': {'state': 'Accepted', 'project_id': P,
                'task_id': body['task_id'], 'command_id': 'signed-native-start',
                'target_role': 'project_manager', 'executor_role': 'developer'}}

    async def task_done(self, bridge, project, task, path):
        return task in self.done

    async def pickup(self):
        return await self.q.pickup_next(P, {'tasks': self.rows}, bridge=self.bridge,
                                       db_path=self.db, task_done=self.task_done)

    async def test_selects_one_dependency_ready_task_by_priority_using_signed_api(self):
        result = await self.pickup()
        self.assertEqual(result['task_id'], 'TASK-ready')
        self.assertEqual(len(self.calls), 1)
        method, url, kw = self.calls[0]
        self.assertEqual(method, 'POST')
        self.assertTrue(url.endswith('agentic_control.prepare_project_pickup'))
        self.assertEqual(kw['json']['expected_task_revision'], 'TASK-ready-revision')
        self.assertEqual(kw['json']['expected_owner_context_sha256'], 'a'*64)
        # Same retry must reuse the exact command admission identity.
        await self.pickup()
        self.assertEqual(self.calls[0][2]['json'], self.calls[1][2]['json'])

    async def test_existing_queue_prevents_native_pickup(self):
        self.fixture.enqueue()
        self.assertIsNone(await self.pickup())
        self.assertEqual(self.calls, [])

    async def test_preserves_human_owners_groups_controls_and_missing_dependencies(self):
        self.rows = [self.row('TASK-human', assignees=['human@example.test']),
                     self.row('TASK-group', is_group=True),
                     self.row('TASK-group-child', parent_task='TASK-group', assignees=['human@example.test']),
                     self.row('TASK-paused')]
        controls = importlib.import_module('open_webui.utils.agentic_controls')
        controls.task_control(P, 'TASK-paused', 'stop', actor='owner', db_path=self.db)
        self.assertIsNone(await self.pickup())
        self.assertEqual(self.calls, [])

    async def test_missing_dependency_and_mixed_hierarchy_cycles_fail_before_native_write(self):
        cases = [
            [self.row('TASK-missing', dependencies=['TASK-other'])],
            [self.row('TASK-a', dependencies=['TASK-b']), self.row('TASK-b', dependencies=['TASK-a'])],
            [self.row('TASK-parent'), self.row('TASK-child', parent_task='TASK-parent', dependencies=['TASK-parent'])],
            [self.row('TASK-child', parent_task='TASK-other')],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                self.rows = rows
                with self.assertRaisesRegex(ValueError, 'dependency|parent'):
                    await self.pickup()
                self.assertEqual(self.calls, [])

    async def test_due_date_orders_equal_priority_ready_tasks(self):
        self.rows = [self.row('TASK-later', due_date='2026-11-20'),
                     self.row('TASK-earlier', due_date='2026-11-18')]
        self.assertEqual((await self.pickup())['task_id'], 'TASK-earlier')

    async def test_native_concurrency_allows_another_task_but_not_duplicate_pickup(self):
        self.fixture.enqueue()
        self.assertIsNone(await self.pickup())
        snapshot = {'tasks': self.rows, 'project_execution_policy': {'max_concurrency': 2}}
        self.assertEqual((await self.q.pickup_next(P, snapshot, bridge=self.bridge,
            db_path=self.db, task_done=self.task_done))['task_id'], 'TASK-ready')

    async def test_malformed_native_concurrency_fails_closed(self):
        for limit in (True, 0, 9, '2'):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'concurrency policy'):
                await self.q.pickup_next(P, {'tasks': self.rows,
                    'project_execution_policy': {'max_concurrency': limit}},
                    bridge=self.bridge, db_path=self.db, task_done=self.task_done)
        self.assertEqual(self.calls, [])

    async def test_child_completion_releases_parent_dependency_candidate(self):
        self.done.add('TASK-low')
        result = await self.pickup()
        self.assertEqual(result['task_id'], 'TASK-dependent')

    async def test_parent_is_selected_for_own_acceptance_only_after_children_verified(self):
        self.rows = [self.row('TASK-parent', is_group=True, priority='Urgent'),
                     self.row('TASK-child', parent_task='TASK-parent', priority='Low')]
        self.assertEqual((await self.pickup())['task_id'], 'TASK-child')
        self.done.add('TASK-child')
        self.assertEqual((await self.pickup())['task_id'], 'TASK-parent')
        self.assertEqual(len(self.calls), 2)

    async def test_native_wait_does_not_create_core_invocation_or_try_another_task(self):
        async def waiting(*a, **kw):
            self.calls.append(kw)
            return {'message': {'state': 'Waiting', 'reason': 'Original task is still working'}}
        self.bridge.erp.request = waiting
        result = await self.pickup()
        self.assertEqual(result['state'], 'Waiting')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(g.list_invocations(P, self.db), [])

    async def test_mismatched_native_receipt_fails_closed(self):
        async def wrong(*a, **kw):
            return {'message': {'state': 'Accepted', 'project_id': 'PROJ-other',
                                'task_id': 'TASK-ready', 'command_id': 'bad'}}
        self.bridge.erp.request = wrong
        with self.assertRaisesRegex(ValueError, 'exact signed project pickup'):
            await self.pickup()


if __name__ == '__main__':
    unittest.main()

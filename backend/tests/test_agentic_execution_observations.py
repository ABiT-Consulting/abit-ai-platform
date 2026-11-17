"""Durable signed failure observations preserve original ownership and retries."""
import importlib
import json
import sys
import types
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_agentic_ledger_fences as fixture

g, P, T, GATE = fixture.g, fixture.P, fixture.T, fixture.GATE


class Observations(unittest.IsolatedAsyncioTestCase):
    packet = fixture.DurableLedger.packet
    enqueue = fixture.DurableLedger.enqueue

    async def asyncSetUp(self):
        fixture.DurableLedger.setUp(self)
        modules = patch.dict(sys.modules, {'open_webui.utils.agentic_governance': g})
        modules.start()
        self.addCleanup(modules.stop)
        self.o = importlib.import_module('open_webui.utils.agentic_execution_observations')
        self.b = importlib.import_module('open_webui.utils.agentic_bridge')
        packet = self.packet()
        packet['content_sha256'] = self.b.packet_digest(packet)
        self.enqueue(work_packet=packet)
        self.row = g.claim_invocation('invocation-00000001', 'original-run', gate=GATE, db_path=self.db)
        self.calls = []
        self.bridge = types.SimpleNamespace(secret='offline-only-bridge-secret-long-enough',
            erp=types.SimpleNamespace(request=self.request))

    async def request(self, method, url, **kwargs):
        event = json.loads(kwargs['json']['event'])
        self.calls.append((method, url, deepcopy(event), kwargs['headers']))
        return {'message': {key: event[key] for key in ('event_id', 'project_id', 'task_id', 'run_id')}
                | {'durable': True, 'status': 'Recorded',
                   'previous_modified': 'modified-started', 'modified': 'modified-observation'}}

    def finish(self):
        g.mark_submitted(self.row['invocation_id'], self.row['automation_run_id'],
                         self.row['lease_token'], gate=GATE, db_path=self.db)
        g.finish_execution(self.row['invocation_id'], self.row['automation_run_id'],
                           self.row['lease_token'], 'uncertain', reason='private provider failure', db_path=self.db)

    async def test_failure_stages_native_digest_not_full_ledger_digest_and_retains_lease(self):
        self.finish()
        with g.connect(self.db) as db:
            saved = db.execute('SELECT * FROM governance_execution_observation').fetchone()
            event = json.loads(saved['event_json'])
            self.assertEqual(saved['phase'], 'pending')
            original = g.find(self.row['invocation_id'], self.db, include_private=True)
            self.assertNotEqual(original['packet_hash'], event['work_packet_digest'])
            self.assertEqual(event['work_packet_digest'], json.loads(original['work_packet'])['content_sha256'])
            self.assertNotIn('private provider failure', saved['event_json'])
            validator = importlib.import_module('open_webui.utils.agentic_contracts.validator')
            self.assertEqual(validator.schema_errors('execution_observation', event), [])
            self.assertEqual(db.execute('SELECT invocation_id FROM governance_task_lease').fetchone()[0],
                             self.row['invocation_id'])
        self.assertEqual(await self.o.deliver_pending(self.bridge, db_path=self.db), [event['event_id']])
        self.assertEqual(self.b.verify_event(event, self.calls[0][3], self.bridge.secret), event['event_id'])
        self.assertEqual(g.find(self.row['invocation_id'], self.db)['state'], 'uncertain')
        self.assertEqual(await self.o.deliver_pending(self.bridge, db_path=self.db), [])
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_acknowledgment_retries_identical_event_without_another_execution(self):
        self.finish()
        async def lost(*args, **kwargs):
            await self.request(*args, **kwargs)
            raise TimeoutError('private response')
        self.bridge.erp.request = lost
        self.assertEqual(await self.o.deliver_pending(self.bridge, db_path=self.db), [])
        self.bridge.erp.request = self.request
        self.assertEqual(len(await self.o.deliver_pending(self.bridge, db_path=self.db)), 1)
        self.assertEqual(self.calls[0][2], self.calls[1][2])
        self.assertEqual(len(g.list_invocations(P, self.db)), 1)
        self.assertEqual(g.find(self.row['invocation_id'], self.db)['dispatch_attempts'], 1)

    async def test_wrong_receipt_stays_pending_and_native_superseded_receipt_settles_journal(self):
        self.finish()
        async def wrong(*args, **kwargs):
            result = await self.request(*args, **kwargs)
            result['message']['run_id'] = 'other-run'
            return result
        self.bridge.erp.request = wrong
        self.assertEqual(await self.o.deliver_pending(self.bridge, db_path=self.db), [])
        async def superseded(*args, **kwargs):
            result = await self.request(*args, **kwargs)
            result['message']['status'] = 'Superseded'
            return result
        self.bridge.erp.request = superseded
        self.assertEqual(len(await self.o.deliver_pending(self.bridge, db_path=self.db)), 1)
        self.assertEqual(g.find(self.row['invocation_id'], self.db)['state'], 'uncertain')

    async def test_expired_lease_stages_failure_in_same_recovery_transaction(self):
        with g.connect(self.db) as db:
            db.execute('UPDATE governance_invocation SET lease_expires_at=0')
        self.assertEqual(g.recoverable(self.db)['stale_marked_uncertain'], 1)
        with g.connect(self.db) as db:
            event = json.loads(db.execute('SELECT event_json FROM governance_execution_observation').fetchone()[0])
        self.assertEqual(event['reason'], 'lease_expired')
        self.assertEqual(g.find(self.row['invocation_id'], self.db)['state'], 'uncertain')

    async def test_legacy_uncertainty_is_seeded_once_and_cannot_rewrite_original_packet(self):
        with g.connect(self.db) as db:
            db.execute("UPDATE governance_invocation SET state='uncertain'")
        before = g.find(self.row['invocation_id'], self.db, include_private=True)
        self.o.seed_uncertain(P, self.db)
        self.o.seed_uncertain(P, self.db)
        with g.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_execution_observation').fetchone()[0], 1)
            db.execute("UPDATE governance_invocation SET work_packet='{}'")
        with self.assertRaisesRegex(ValueError, 'packet hash changed'):
            self.o.seed_uncertain(P, self.db)
        self.assertEqual(g.find(self.row['invocation_id'], self.db)['automation_run_id'], before['automation_run_id'])

    def stage_clone(self, index, *, project=P):
        clone = {**self.row, 'invocation_id': 'invocation-observation-poison-' + str(index).zfill(4),
                 'automation_run_id': 'original-observation-' + str(index), 'erp_project': project}
        with g._transaction(self.db) as db:
            return self.o.stage(db, clone, 'execution_failed')

    async def test_first_100_native_denials_cannot_starve_later_valid_project_observation(self):
        poison = [self.stage_clone(index) for index in range(100)]
        other = self.stage_clone(1000, project='PROJ-other')
        self.finish()
        class NativeDenied(Exception):
            def __init__(self, code): self.status_code = code
        async def native(method, url, **kwargs):
            result = await self.request(method, url, **kwargs)
            event = self.calls[-1][2]
            if event['event_id'] in poison:
                raise NativeDenied(403 if poison.index(event['event_id']) % 2 else 409)
            return result
        self.bridge.erp.request = native
        with g.connect(self.db) as db:
            leases = [tuple(row) for row in db.execute('SELECT * FROM governance_task_lease')]
            originals = {row['event_id']: row['event_json'] for row in db.execute('SELECT * FROM governance_execution_observation')}
        with patch.object(self.o.log, 'warning'):
            delivered = await self.o.deliver_pending(self.bridge, project=P, db_path=self.db)
        self.assertEqual(len(delivered), 1)
        visited = [call[2]['event_id'] for call in self.calls]
        self.assertEqual(len(visited), 101)
        self.assertEqual(len(set(visited)), 101)
        self.assertNotIn(other, visited)
        self.assertEqual(set(visited) - set(poison), set(delivered))
        with g.connect(self.db) as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM governance_execution_observation')]
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM governance_task_lease')], leases)
        self.assertEqual({row['event_id']: row['event_json'] for row in rows}, originals)
        self.assertEqual({row['event_id'] for row in rows if row['phase'] == 'pending'}, set(poison) | {other})
        self.assertEqual(g.find(self.row['invocation_id'], self.db)['state'], 'uncertain')

    async def test_immediate_invocation_filter_does_not_wait_for_poison_backlog(self):
        poison = [self.stage_clone(index) for index in range(100)]
        self.finish()
        delivered = await self.o.deliver_pending(self.bridge, project=P,
            invocation_id=self.row['invocation_id'], db_path=self.db)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]['invocation_id'], self.row['invocation_id'])
        with g.connect(self.db) as db:
            pending = {row[0] for row in db.execute("SELECT event_id FROM governance_execution_observation WHERE phase='pending'")}
        self.assertEqual(pending, set(poison))

    async def test_delivery_snapshot_defers_new_rows_and_visits_each_original_once(self):
        self.finish()
        later = []
        async def native(method, url, **kwargs):
            result = await self.request(method, url, **kwargs)
            if not later:
                later.append(self.stage_clone(999))
            return result
        self.bridge.erp.request = native
        delivered = await self.o.deliver_pending(self.bridge, project=P, db_path=self.db)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(later[0], delivered)
        with g.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT phase FROM governance_execution_observation WHERE event_id=?', later).fetchone()[0], 'pending')
        next_pass = await self.o.deliver_pending(self.bridge, project=P, db_path=self.db)
        self.assertEqual(next_pass, later)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len({call[2]['event_id'] for call in self.calls}), 2)

    async def test_malformed_or_scope_changed_journal_is_retained_without_blocking_valid_rows(self):
        bad_json = self.stage_clone(1)
        wrong_project = self.stage_clone(2)
        with g.connect(self.db) as db:
            db.execute('UPDATE governance_execution_observation SET event_json=? WHERE event_id=?', ('{broken', bad_json))
            saved = db.execute('SELECT event_json FROM governance_execution_observation WHERE event_id=?', (wrong_project,)).fetchone()[0]
            changed = json.loads(saved); changed['project_id'] = 'PROJ-other'
            db.execute('UPDATE governance_execution_observation SET event_json=? WHERE event_id=?', (json.dumps(changed), wrong_project))
        self.finish()
        with patch.object(self.o.log, 'warning'):
            delivered = await self.o.deliver_pending(self.bridge, project=P, db_path=self.db)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]['invocation_id'], self.row['invocation_id'])
        with g.connect(self.db) as db:
            self.assertEqual({row[0] for row in db.execute("SELECT event_id FROM governance_execution_observation WHERE phase='pending'")},
                             {bad_json, wrong_project})


class PolicyCompatibility(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('policy_compat_bridge', fixture.ROOT / 'agentic_bridge.py')
        self.b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.b)
        self.old = {'schema_version': 1, 'project_id': P, 'task_id': T,
                    'binding': {'role': 'developer'}, 'task_revision': 'original-inputs',
                    'project_backlog': {'complete': True, 'project_id': P,
                        'tasks': [{'task_id': T, 'owner_context_sha256': 'b'*64}],
                        'context_sha256': 'a'*64}}
        self.old['project_backlog']['snapshot_sha256'] = self.b.digest({'project_id': P, 'tasks': self.old['project_backlog']['tasks']})
        self.old['packet_id'] = 'erp-packet-' + self.b.digest(self.old)[:48]
        self.old['content_sha256'] = self.b.packet_digest(self.old)
        self.fresh = deepcopy(self.old)
        self.fresh['project_execution_policy'] = {'max_concurrency': 1}
        backlog = self.fresh['project_backlog']
        backlog['project_execution_policy'] = {'max_concurrency': 1}
        backlog['snapshot_sha256'] = self.b.digest({'project_id': P, 'tasks': backlog['tasks'],
            'project_execution_policy': {'max_concurrency': 1}})
        self.fresh.pop('packet_id'); self.fresh.pop('content_sha256')
        self.fresh['packet_id'] = 'erp-packet-' + self.b.digest(self.fresh)[:48]
        self.fresh['content_sha256'] = self.b.packet_digest(self.fresh)

    def test_adding_only_default_policy_recognizes_original_without_rewriting_either_packet(self):
        old, fresh = deepcopy(self.old), deepcopy(self.fresh)
        self.assertTrue(self.b.default_policy_upgrade_matches(self.old, self.fresh))
        self.assertTrue(self.b.same_business_context(self.old, self.fresh, {'content_sha256', 'packet_id'}))
        self.assertEqual(self.old, old)
        self.assertEqual(self.fresh, fresh)

    def test_nondefault_policy_or_business_drift_never_normalizes(self):
        self.fresh['project_execution_policy']['max_concurrency'] = 2
        self.assertFalse(self.b.default_policy_upgrade_matches(self.old, self.fresh))
        self.assertFalse(self.b.same_business_context(self.old, self.fresh, {'content_sha256', 'packet_id'}))
        self.fresh['project_execution_policy']['max_concurrency'] = 1
        self.fresh['task_revision'] = 'edited-inputs'
        self.assertFalse(self.b.default_policy_upgrade_matches(self.old, self.fresh))
        self.assertFalse(self.b.same_business_context(self.old, self.fresh, {'content_sha256', 'packet_id'}))

    def test_null_boolean_or_out_of_range_policy_cannot_authorize_default_upgrade(self):
        for policy in (None, {'max_concurrency': True}, {'max_concurrency': '1'},
                       {'max_concurrency': 0}, {'max_concurrency': 9}):
            with self.subTest(policy=policy):
                self.fresh['project_execution_policy'] = policy
                self.assertFalse(self.b.default_policy_upgrade_matches(self.old, self.fresh))
                self.assertFalse(self.b.same_business_context(self.old, self.fresh, {'content_sha256', 'packet_id'}))

    def test_own_task_owner_proof_must_be_unique_valid_and_unchanged_in_complete_backlog(self):
        for rows in ([], [{'task_id': T}],
                     [{'task_id': T, 'owner_context_sha256': 'z'*64}],
                     [{'task_id': T, 'owner_context_sha256': 'c'*64}],
                     self.old['project_backlog']['tasks'] * 2):
            fresh = deepcopy(self.old); fresh['project_backlog']['tasks'] = rows
            with self.subTest(rows=rows):
                self.assertFalse(self.b.same_business_context(self.old, fresh))
        old = deepcopy(self.old); old['project_backlog']['tasks'] = []
        self.assertFalse(self.b.same_business_context(old, old))
        old.pop('project_backlog'); fresh = deepcopy(old)
        self.assertTrue(self.b.same_business_context(old, fresh))

    def test_unverified_capture_digest_cannot_be_normalized(self):
        self.fresh['project_backlog']['snapshot_sha256'] = 'b'*64
        self.assertFalse(self.b.default_policy_upgrade_matches(self.old, self.fresh))


if __name__ == '__main__':
    unittest.main()

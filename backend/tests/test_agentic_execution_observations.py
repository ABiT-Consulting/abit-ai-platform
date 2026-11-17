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
                | {'durable': True, 'status': 'Recorded'}}

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


class PolicyCompatibility(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('policy_compat_bridge', fixture.ROOT / 'agentic_bridge.py')
        self.b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.b)
        self.old = {'schema_version': 1, 'project_id': P, 'task_id': T,
                    'binding': {'role': 'developer'}, 'task_revision': 'original-inputs',
                    'project_backlog': {'complete': True, 'project_id': P, 'tasks': [],
                        'context_sha256': 'a'*64}}
        self.old['project_backlog']['snapshot_sha256'] = self.b.digest({'project_id': P, 'tasks': []})
        self.old['packet_id'] = 'erp-packet-' + self.b.digest(self.old)[:48]
        self.old['content_sha256'] = self.b.packet_digest(self.old)
        self.fresh = deepcopy(self.old)
        self.fresh['project_execution_policy'] = {'max_concurrency': 1}
        backlog = self.fresh['project_backlog']
        backlog['project_execution_policy'] = {'max_concurrency': 1}
        backlog['snapshot_sha256'] = self.b.digest({'project_id': P, 'tasks': [],
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

    def test_unverified_capture_digest_cannot_be_normalized(self):
        self.fresh['project_backlog']['snapshot_sha256'] = 'b'*64
        self.assertFalse(self.b.default_policy_upgrade_matches(self.old, self.fresh))


if __name__ == '__main__':
    unittest.main()

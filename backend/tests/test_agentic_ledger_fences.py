"""SQLite concurrency/crash regressions; no application import, network or live DB."""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'
env = types.ModuleType('open_webui.env')
env.DATA_DIR = '/tmp/unused-agov-fences'
spec = importlib.util.spec_from_file_location('ledger_fences', ROOT / 'agentic_governance.py')
g = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'open_webui.env': env}):
    spec.loader.exec_module(g)
P = 'PROJ-0091'
T = 'TASK-2026-06074'
OWNER = 'verified-owner'
GATE = {
    'enabled': True,
    'pm_task_automation_enabled': True,
    'project_status': 'Open',
    'assigned_user': 'ai-projects@abitcons.com',
    'revision': 'project-rev-1',
}


class DurableLedger(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'ledger.sqlite'
        g.enroll(
            P,
            OWNER,
            {
                'project_manager': 'ai-projects@abitcons.com',
                'developer': 'ai-developer@abitcons.com',
                'qa': 'ai-qa@abitcons.com',
                'it_manager': 'ai-operations@abitcons.com',
            },
            self.db,
        )
        g.project_control(P, 'enable', actor='owner@example.test', policy_revision='project-rev-1', db_path=self.db)

    def packet(self, rev='task-1', **extra):
        return {
            'version': 1,
            'project': P,
            'task': T,
            'task_revision': rev,
            'input_revision': 'source-input-1',
            **extra,
        }

    def enqueue(self, ident='invocation-00000001', **extra):
        data = dict(
            invocation_id=ident,
            project=P,
            task=T,
            from_role='project_manager',
            target_role='developer',
            task_revision='task-1',
            context='Exact scoped task',
            work_packet=self.packet(),
            gate=GATE,
            actor='authenticated-pm',
        )
        data.update(extra)
        return g.enqueue(**data, db_path=self.db)

    def claim(self, ident='invocation-00000001', run='run-1'):
        return g.claim_invocation(ident, run, gate=GATE, db_path=self.db)

    def finished(self, ident='invocation-00000001', run='run-1'):
        self.enqueue(ident)
        claim = self.claim(ident, run)
        self.assertTrue(g.mark_submitted(ident, run, claim['lease_token'], gate=GATE, db_path=self.db))
        self.assertTrue(g.finish_execution(ident, run, claim['lease_token'], db_path=self.db))
        return claim

    def handoff(self, claim, ident='handoff-0000000001', **extra):
        data = dict(
            source_invocation_id=claim['invocation_id'],
            source_run_id=claim['automation_run_id'],
            source_lease_token=claim['lease_token'],
            invocation_id=ident,
            project=P,
            task=T,
            from_role='developer',
            target_role='qa',
            task_revision='task-qa-2',
            context='Independent acceptance tests',
            work_packet=self.packet('task-qa-2'),
            gate=GATE,
            actor='authenticated-developer',
        )
        data.update(extra)
        return g.handoff(**data, db_path=self.db)

    def race(self, fn):
        barrier = Barrier(2)

        def worker(index):
            barrier.wait()
            try:
                return fn(index)
            except ValueError as e:
                return str(e)

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_parallel_task_reservations_have_exactly_one_winner(self):
        result = self.race(lambda n: self.enqueue('parallel-invocation-000' + str(n)))
        self.assertEqual(sum(isinstance(v, tuple) and v[1] for v in result), 1)
        self.assertEqual(len(g.list_invocations(P, self.db)), 1)

    def test_duplicate_concurrent_event_creates_one_queue_record(self):
        result = self.race(lambda n: self.enqueue())
        self.assertEqual(sorted(v[1] for v in result), [False, True])
        self.assertEqual(len(g.list_invocations(P, self.db)), 1)

    def test_parallel_claim_has_one_token_and_no_second_executor(self):
        self.enqueue()
        result = self.race(lambda n: self.claim())
        self.assertEqual(sum(isinstance(v, dict) for v in result), 1)
        self.assertEqual(g.find('invocation-00000001', self.db)['dispatch_attempts'], 1)

    def test_different_packet_same_id_is_rejected(self):
        self.enqueue()
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.enqueue(work_packet=self.packet(srs_revision='changed'))

    def test_packet_fingerprint_is_canonical_and_versioned(self):
        packet = self.packet()
        self.assertEqual(g.work_packet_digest(packet), g.work_packet_digest(dict(reversed(list(packet.items())))))
        self.assertNotEqual(g.work_packet_digest(packet), g.work_packet_digest(self.packet(activity_revision='new')))
        for version in (True, 2, '1', None):
            with self.assertRaises(ValueError):
                g.work_packet_digest({'version': version})

    def test_packet_scope_and_revision_cannot_be_forged(self):
        for key, value in [
            ('project', 'PROJ-other'),
            ('task', 'TASK-other'),
            ('task_id', 'TASK-other'),
            ('task_revision', 'stale'),
        ]:
            with self.assertRaisesRegex(ValueError, 'identity'):
                self.enqueue(work_packet={**self.packet(), key: value})

    def test_project_pause_overrides_even_valid_live_gate(self):
        self.enqueue()
        g.project_control(P, 'pause', actor='owner', db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.claim()
        g.project_control(P, 'resume', actor='owner', db_path=self.db)
        self.assertIsNotNone(self.claim())

    def test_project_disabled_by_default_until_explicit_enable(self):
        other = 'PROJ-other'
        g.enroll(other, OWNER, {'developer': 'ai-developer@abitcons.com'}, self.db)
        self.assertEqual(g.project_control(other, db_path=self.db)['state'], 'disabled')
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.enqueue(project=other, work_packet={'version': 1})

    def test_live_checkbox_status_and_exact_assignee_fail_closed(self):
        self.enqueue()
        for field, value in [
            ('enabled', False),
            ('project_status', 'Completed'),
            ('assigned_user', 'ai-developer@abitcons.com'),
            ('revision', ''),
        ]:
            with self.assertRaisesRegex(ValueError, 'Live ERP'):
                g.claim_invocation('invocation-00000001', gate={**GATE, field: value}, db_path=self.db)

    def test_bind_run_is_immutable_and_requires_fence_token(self):
        self.enqueue()
        claim = self.claim(run=None)
        token = claim['lease_token']
        self.assertFalse(g.bind_run(claim['invocation_id'], 'wrong', 'run-1', self.db))
        self.assertTrue(g.bind_run(claim['invocation_id'], token, 'run-1', self.db))
        self.assertFalse(g.bind_run(claim['invocation_id'], token, 'run-2', self.db))

    def test_final_submission_rechecks_pause_and_cancellation(self):
        self.enqueue()
        claim = self.claim()
        g.project_control(P, 'pause', actor='owner', db_path=self.db)
        with self.assertRaises(ValueError):
            g.mark_submitted(claim['invocation_id'], 'run-1', claim['lease_token'], gate=GATE, db_path=self.db)
        g.project_control(P, 'resume', actor='owner', db_path=self.db)
        g.request_cancel(claim['invocation_id'], 'owner', self.db)
        self.assertFalse(
            g.mark_submitted(claim['invocation_id'], 'run-1', claim['lease_token'], gate=GATE, db_path=self.db)
        )

    def test_submission_fence_can_be_taken_only_once(self):
        self.enqueue()
        claim = self.claim()
        self.assertTrue(
            g.mark_submitted(claim['invocation_id'], 'run-1', claim['lease_token'], gate=GATE, db_path=self.db)
        )
        self.assertFalse(
            g.mark_submitted(claim['invocation_id'], 'run-1', claim['lease_token'], gate=GATE, db_path=self.db)
        )

    def test_expired_running_becomes_uncertain_without_replay(self):
        self.enqueue()
        claim = self.claim()
        with g.connect(self.db) as db:
            db.execute(
                'UPDATE governance_invocation SET lease_expires_at=1 WHERE invocation_id=?', (claim['invocation_id'],)
            )
        report = g.recoverable(self.db)
        self.assertEqual(report['stale_marked_uncertain'], 1)
        self.assertEqual(report['queued_for_reconciliation'], [])
        self.assertIsNone(self.claim())
        with self.assertRaisesRegex(ValueError, 'active or uncertain'):
            self.enqueue('other-invocation-00001')

    def test_heartbeat_extends_current_run_and_rejects_wrong_owner(self):
        self.enqueue()
        claim = self.claim()
        self.assertFalse(g.heartbeat(claim['invocation_id'], 'wrong', claim['lease_token'], self.db))
        self.assertTrue(g.heartbeat(claim['invocation_id'], 'run-1', claim['lease_token'], self.db))
        self.assertEqual(g.recoverable(self.db)['stale_marked_uncertain'], 0)

    def test_timeout_does_not_release_task_fence(self):
        self.enqueue()
        claim = self.claim()
        g.mark_submitted(claim['invocation_id'], 'run-1', claim['lease_token'], gate=GATE, db_path=self.db)
        g.finish_execution(claim['invocation_id'], 'run-1', claim['lease_token'], 'uncertain', db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'active or uncertain'):
            self.enqueue('other-invocation-00001')

    def test_confirmed_unsubmitted_retries_are_bounded(self):
        self.enqueue()
        for attempt in range(3):
            claim = self.claim()
            self.assertTrue(
                g.defer_unsubmitted(
                    claim['invocation_id'], 'run-1', claim['lease_token'], proof='not_called', db_path=self.db
                )
            )
        self.assertEqual(g.find(claim['invocation_id'], self.db)['state'], 'blocked')
        self.assertIsNone(self.claim())

    def test_unknown_rejection_or_timeout_is_never_retry_proof(self):
        self.enqueue()
        claim = self.claim()
        for proof in ('timeout', 'http_500', 'unknown'):
            with self.assertRaises(ValueError):
                g.defer_unsubmitted(claim['invocation_id'], 'run-1', claim['lease_token'], proof=proof, db_path=self.db)
        g.mark_submitted(claim['invocation_id'], 'run-1', claim['lease_token'], gate=GATE, db_path=self.db)
        self.assertFalse(
            g.defer_unsubmitted(
                claim['invocation_id'], 'run-1', claim['lease_token'], proof='not_called', db_path=self.db
            )
        )

    def test_queued_cancel_is_terminal_but_active_cancel_keeps_fence(self):
        self.enqueue()
        self.assertEqual(g.request_cancel('invocation-00000001', 'owner', self.db)['state'], 'cancelled')
        self.enqueue('other-invocation-00001')
        claim = self.claim('other-invocation-00001')
        self.assertEqual(g.request_cancel(claim['invocation_id'], 'owner', self.db)['state'], 'cancel_requested')
        self.assertIsNone(g.claim_invocation(claim['invocation_id'], gate=GATE, db_path=self.db))
        with self.assertRaises(ValueError):
            self.enqueue('third-invocation-00001')
        with self.assertRaises(ValueError):
            g.confirm_cancel(claim['invocation_id'], 'run-1', claim['lease_token'], '', self.db)
        self.assertTrue(
            g.confirm_cancel(
                claim['invocation_id'], 'run-1', claim['lease_token'], 'verified-upstream-receipt', self.db
            )
        )
        self.enqueue('third-invocation-00001')

    def test_reply_after_stop_does_not_invent_cancel_acknowledgment(self):
        self.enqueue()
        claim = self.claim()
        g.request_cancel(claim['invocation_id'], 'owner', self.db)
        self.assertTrue(g.finish_execution(claim['invocation_id'], 'run-1', claim['lease_token'], db_path=self.db))
        self.assertEqual(g.find(claim['invocation_id'], self.db)['state'], 'cancel_requested')

    def test_project_stop_cancels_queued_and_fences_active(self):
        self.enqueue()
        self.enqueue('different-task-job-0001', task='TASK-other', work_packet={'version': 1})
        claim = self.claim()
        g.project_control(P, 'stop', actor='owner', db_path=self.db)
        self.assertEqual(g.find(claim['invocation_id'], self.db)['state'], 'cancel_requested')
        self.assertEqual(g.find('different-task-job-0001', self.db)['state'], 'cancelled')

    def test_same_task_handoff_waits_for_model_to_finish(self):
        self.enqueue()
        claim = self.claim()
        with self.assertRaisesRegex(ValueError, 'finished source'):
            self.handoff(claim)
        self.assertEqual(g.find(claim['invocation_id'], self.db)['state'], 'running')
        self.assertEqual(len(g.list_invocations(P, self.db)), 1)

    def test_handoff_is_atomic_replay_safe_and_fences_old_executor(self):
        source = self.finished()
        one, created = self.handoff(source)
        self.assertTrue(created)
        two, created = self.handoff(source)
        self.assertFalse(created)
        self.assertEqual(one, two)
        self.assertEqual(g.find(source['invocation_id'], self.db)['state'], 'handed_off')
        self.assertGreater(one['lease_fence'], source['lease_fence'])
        self.assertFalse(g.heartbeat(source['invocation_id'], 'run-1', source['lease_token'], self.db))
        self.assertFalse(
            g.mark_submitted(source['invocation_id'], 'run-1', source['lease_token'], gate=GATE, db_path=self.db)
        )

    def test_parallel_handoff_has_one_successor(self):
        source = self.finished()
        result = self.race(lambda n: self.handoff(source, 'handoff-parallel-000' + str(n)))
        self.assertEqual(sum(isinstance(v, tuple) and v[1] for v in result), 1)
        self.assertEqual(len(g.list_invocations(P, self.db)), 2)

    def test_failed_handoff_rolls_back_source_and_task_lease(self):
        source = self.finished()
        with self.assertRaises(ValueError):
            self.handoff(source, target_role='unknown')
        self.assertEqual(g.find(source['invocation_id'], self.db)['state'], 'execution_finished')
        self.assertTrue(self.handoff(source)[1])

    def test_handoff_rejects_changed_duplicate_or_wrong_source_identity(self):
        source = self.finished()
        self.handoff(source)
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.handoff(source, context='Different work')
        with self.assertRaisesRegex(ValueError, 'finished source'):
            self.handoff(source, 'handoff-other-0001', source_lease_token='wrong')

    def test_execution_success_is_not_erp_verified_completion(self):
        source = self.finished()
        self.assertEqual(g.find(source['invocation_id'], self.db)['state'], 'execution_finished')
        with self.assertRaises(ValueError):
            g.verify_completion(
                source['invocation_id'], source['invocation_id'], 'a' * 64, 'b' * 64, 'owner', db_path=self.db
            )

    def test_qa_requires_explicit_pass_and_distinct_implementer(self):
        source = self.finished()
        qa, _ = self.handoff(source)
        claim = self.claim(qa['invocation_id'], 'qa-run')
        g.mark_submitted(qa['invocation_id'], 'qa-run', claim['lease_token'], gate=GATE, db_path=self.db)
        g.finish_execution(qa['invocation_id'], 'qa-run', claim['lease_token'], qa_verdict='fail', db_path=self.db)
        with self.assertRaises(ValueError):
            g.verify_completion(
                source['invocation_id'], qa['invocation_id'], 'a' * 64, 'b' * 64, 'owner', db_path=self.db
            )

    def test_independent_qa_and_erp_readback_complete_once(self):
        source = self.finished()
        qa, _ = self.handoff(source)
        claim = self.claim(qa['invocation_id'], 'qa-run')
        g.mark_submitted(qa['invocation_id'], 'qa-run', claim['lease_token'], gate=GATE, db_path=self.db)
        g.finish_execution(qa['invocation_id'], 'qa-run', claim['lease_token'], qa_verdict='pass', db_path=self.db)
        self.assertTrue(
            g.verify_completion(
                source['invocation_id'], qa['invocation_id'], 'a' * 64, 'b' * 64, 'erp-publisher', db_path=self.db
            )
        )
        self.assertFalse(
            g.verify_completion(
                source['invocation_id'], qa['invocation_id'], 'a' * 64, 'b' * 64, 'erp-publisher', db_path=self.db
            )
        )
        self.assertEqual(g.find(source['invocation_id'], self.db)['state'], 'verified')
        with self.assertRaisesRegex(ValueError, 'idempotency content'):
            g.verify_completion(
                source['invocation_id'], qa['invocation_id'], 'c' * 64, 'b' * 64, 'erp-publisher', db_path=self.db
            )

    def test_inbox_duplicate_and_changed_content_conflict(self):
        first, created = g.receive_event('erp-event-000001', P, {'revision': 'one'}, db_path=self.db)
        self.assertTrue(created)
        second, created = g.receive_event('erp-event-000001', P, {'revision': 'one'}, db_path=self.db)
        self.assertFalse(created)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, 'different content'):
            g.receive_event('erp-event-000001', P, {'revision': 'two'}, db_path=self.db)
        self.enqueue(event_id='erp-event-000001')
        self.assertEqual(g.pending_events(db_path=self.db), [])

    def test_control_event_acknowledgment_is_attributed_and_idempotent(self):
        g.receive_event('erp-control-event-0001', P, {'command': 'pause'}, db_path=self.db)
        self.assertTrue(g.acknowledge_event('erp-control-event-0001', P, actor='erp-owner', db_path=self.db))
        self.assertFalse(g.acknowledge_event('erp-control-event-0001', P, actor='erp-owner', db_path=self.db))
        self.assertEqual(g.pending_events(db_path=self.db), [])
        with self.assertRaises(ValueError):
            g.acknowledge_event('erp-control-event-0001', 'PROJ-other', db_path=self.db)

    def test_finished_planner_can_attribute_different_verified_child_task(self):
        self.enqueue(from_role='erp', target_role='project_manager')
        source = self.claim()
        g.finish_execution(source['invocation_id'], 'run-1', source['lease_token'], db_path=self.db)
        row, created = self.enqueue(
            'child-task-invocation-0001',
            task='TASK-child',
            work_packet={'version': 1, 'parent_task': T},
            parent_invocation_id=source['invocation_id'],
        )
        self.assertTrue(created)
        self.assertEqual(row['parent_invocation_id'], source['invocation_id'])
        with self.assertRaisesRegex(ValueError, 'finished source'):
            self.enqueue(
                'other-child-invocation-0001',
                task='TASK-other',
                work_packet={'version': 1, 'parent_task': 'TASK-wrong'},
                parent_invocation_id=source['invocation_id'],
            )

    def test_legacy_queue_requires_explicit_workpacket_migration_before_new_executor(self):
        self.enqueue(work_packet=None, gate=None)
        with self.assertRaisesRegex(ValueError, 'versioned WorkPacket'):
            self.claim()

    def test_qa_evidence_for_different_inputs_cannot_verify_source(self):
        source = self.finished()
        qa, _ = self.handoff(source, work_packet=self.packet('task-qa-2', input_revision='changed-inputs'))
        claim = self.claim(qa['invocation_id'], 'qa-run')
        g.finish_execution(qa['invocation_id'], 'qa-run', claim['lease_token'], qa_verdict='pass', db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'different task inputs'):
            g.verify_completion(
                source['invocation_id'], qa['invocation_id'], 'a' * 64, 'b' * 64, 'erp-owner', db_path=self.db
            )

    def test_audit_is_append_only_hash_chained_and_status_excludes_private_payload(self):
        self.enqueue(context='private-context-marker')
        claim = self.claim()
        g.request_cancel(claim['invocation_id'], 'owner', self.db)
        events = g.audit_events(P, db_path=self.db)
        previous = ''
        for e in events:
            self.assertEqual(e['previous_hash'], previous)
            self.assertEqual(
                e['event_hash'],
                g._digest(
                    [
                        e['event_id'],
                        e['erp_project'],
                        e['invocation_id'],
                        e['kind'],
                        e['actor'],
                        e['payload'],
                        e['created_at'],
                        previous,
                    ]
                ),
            )
            previous = e['event_hash']
        self.assertNotIn('private-context-marker', json.dumps(events))
        public = g.find(claim['invocation_id'], self.db)
        for field in ('lease_token', 'context', 'work_packet'):
            self.assertNotIn(field, public)
        with g.connect(self.db) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                db.execute('DELETE FROM governance_event')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                db.execute("UPDATE governance_event SET actor='spoof'")

    def test_old_fourteen_column_database_upgrades_without_losing_history(self):
        migrated = Path(self.temp.name) / 'old.sqlite'
        with sqlite3.connect(migrated) as db:
            db.executescript("""
                CREATE TABLE governance_agent(agent_id TEXT PRIMARY KEY, erp_project TEXT NOT NULL,
                    erp_user TEXT NOT NULL, role TEXT NOT NULL, owner_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL, UNIQUE(erp_project,role));
                CREATE TABLE governance_invocation(invocation_id TEXT PRIMARY KEY,
                    erp_project TEXT NOT NULL, erp_task TEXT NOT NULL, from_role TEXT NOT NULL,
                    target_role TEXT NOT NULL, agent_id TEXT NOT NULL, context_hash TEXT NOT NULL,
                    context TEXT NOT NULL, task_revision TEXT NOT NULL, state TEXT NOT NULL,
                    automation_run_id TEXT, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL, reason TEXT);
            """)
            ident = g.agent_id(P, 'developer')
            db.execute(
                'INSERT INTO governance_agent VALUES(?,?,?,?,?,?)',
                (ident, P, 'ai-developer@abitcons.com', 'developer', OWNER, 1),
            )
            db.execute(
                'INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    'historical-invocation-0001',
                    P,
                    T,
                    'project_manager',
                    'developer',
                    ident,
                    'original-hash',
                    'private original context',
                    'original-revision',
                    'running',
                    'original-run',
                    1,
                    1,
                    'original diagnostic',
                ),
            )
        record = g.find('historical-invocation-0001', migrated)
        self.assertEqual(record['automation_run_id'], 'original-run')
        self.assertEqual(record['context_hash'], 'original-hash')
        self.assertEqual(record['packet_version'], 0)
        self.assertEqual(g.recoverable(migrated)['stale_marked_uncertain'], 1)
        self.assertEqual(g.find('historical-invocation-0001', migrated)['state'], 'uncertain')
        self.assertEqual(g.recoverable(migrated)['queued_for_reconciliation'], [])

    def test_legacy_mutation_cannot_bypass_versioned_fences(self):
        self.enqueue()
        with self.assertRaisesRegex(ValueError, 'fenced'):
            g.transition('invocation-00000001', 'queued', 'completed', db_path=self.db)


if __name__ == '__main__':
    unittest.main()

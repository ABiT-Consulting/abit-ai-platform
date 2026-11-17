"""Exact native readiness transport projection at the continuation trust boundary.

Real temporary start/result/lifecycle ledgers; controlled ERP reads and responses.
No network, model, live service or GitHub mutations.
"""
from copy import deepcopy
import json
import socket
import sqlite3
import unittest
from unittest.mock import patch

import test_denied_start_readiness as readiness


class NativeReadinessContinuation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ('connect', 'connect_ex'):
            guard = patch.object(socket.socket, name, side_effect=AssertionError('Network disabled in native projection regression'))
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS disabled in native projection regression'))
        guard.start()
        self.addCleanup(guard.stop)
        self.f = readiness.DeniedReadiness('test_human_readiness_posts_once_and_preserves_core_original')
        self.addCleanup(self.f.doCleanups)
        # Supply a complete native snapshot before the original is enqueued.
        # The same seal function runs for human readiness and never edits the
        # already stored original WorkPacket or its start event.
        def seal(value):
            backlog = value['project_backlog']
            for item in backlog['tasks']:
                defaults = {
                    'task_revision': '2' * 64, 'description': 'Controlled readiness fixture',
                    'parent_task': '', 'is_group': False, 'dependencies': [], 'assignees': [],
                    'owner_context_sha256': '3' * 64, 'managed': True, 'dispatch_released': True,
                    'executor_role': 'developer', 'srs_revision': value['srs']['revision'],
                    'requirement_ids': value['requirement_ids'], 'acceptance_tests': '[]',
                    'source_run_id': '', 'priority': 'Medium', 'due_date': '',
                }
                for key, item_value in defaults.items():
                    item.setdefault(key, deepcopy(item_value))
            backlog.update(project_id=value['project_id'], total_count=len(backlog['tasks']),
                           page_size=50, context_excluded_task_ids=[])
            context = [{key: item[key] for key in ('task_id', 'task_revision', 'owner_context_sha256')}
                       for item in backlog['tasks']]
            backlog['context_sha256'] = self.f.b.digest({'project_id': value['project_id'], 'tasks': context})
            backlog['snapshot_sha256'] = self.f.b.digest({'project_id': value['project_id'], 'tasks': backlog['tasks']})
            return readiness.DeniedReadiness.seal_packet(self.f, value)
        self.f.seal_packet = seal
        self.f.setUp()
        # Definitive provider absence requires a readable canonical ledger,
        # not a missing file. All data here belongs to this temporary fixture.
        with sqlite3.connect(self.f.path.parent / 'abitdot-primary.sqlite') as provider:
            provider.execute('CREATE TABLE IF NOT EXISTS dot_primary_runs(original_run_id TEXT)')
        self.helper = self.f.candidate('agentic_delivery_continuation')
        self.orchestrator = self.f.candidate('agentic_orchestrator')

    async def prepare_published(self):
        f = self.f
        receipt = await f.confirm()
        self.assertIsNotNone(receipt)
        self.assertEqual(len(f.posts()), 1)
        row = f.start.reclaim_same(f.ident, receipt, gate=f.gate_value, db_path=f.path)
        self.assertEqual(row['state'], 'running')
        self.assertTrue(f.g.mark_submitted(f.ident, row['automation_run_id'], row['lease_token'],
                                          gate=f.gate_value, db_path=f.path))
        self.row = f.g.find(f.ident, f.path, include_private=True)
        self.packet = deepcopy(f.packet_value)
        result, event = f.result_and_event()
        result.pop('tests')
        result['test_results'] = []
        event.update(type='run.checkpoint', acceptance_passed=False, result_contract=deepcopy(result))
        from open_webui.utils.agentic_contracts.validator import validate_result
        self.assertEqual(validate_result(result, self.packet), [])
        f.store.save_local_result(f.ident, self.row['automation_run_id'], result, f.path)
        self.assertTrue(f.store.save_result(f.ident, self.row['automation_run_id'], result, event, f.path))
        self.assertFalse(f.store.save_result(f.ident, self.row['automation_run_id'], result, event, f.path))
        self.raw_result, self.core_event = deepcopy(result), deepcopy(event)
        self.receipt = {'event_id': event['event_id'], 'task_id': self.row['erp_task'],
                        'run_id': self.row['automation_run_id'], 'modified': 'fixture-checkpoint-published',
                        'status': 'Working', 'durable': True}
        f.store.publication_receipt(f.ident, self.receipt, f.path)
        with f.g.connect(f.path) as db:
            self.native_event = json.loads(db.execute('SELECT event_json FROM governance_result WHERE invocation_id=?', (f.ident,)).fetchone()[0])
            self.initial = self.original_bytes(db)
        self.assertNotEqual(self.native_event['work_packet_digest'], self.packet['content_sha256'])

    def original_bytes(self, db):
        return {
            'packet': tuple(db.execute('SELECT work_packet,packet_hash,automation_run_id,lease_fence,lease_token FROM governance_invocation WHERE invocation_id=?', (self.f.ident,)).fetchone()),
            'local': tuple(db.execute('SELECT * FROM governance_local_result WHERE invocation_id=?', (self.f.ident,)).fetchone()),
            'result': tuple(db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (self.f.ident,)).fetchone()),
            'audits': [tuple(row) for row in db.execute('SELECT * FROM governance_event WHERE invocation_id=? ORDER BY seq', (self.f.ident,))],
        }

    async def test_exact_projection_keeps_core_bytes_and_native_lifecycle_hash(self):
        await self.prepare_published()
        with self.f.g.connect(self.f.path) as db:
            before = db.total_changes
            for _ in range(2):
                result, event, receipt = self.helper.published(db, self.row)
                self.assertEqual((result, event, receipt), (self.raw_result, self.native_event, self.receipt))
            self.assertEqual(db.total_changes, before)
            self.assertEqual(self.original_bytes(db), self.initial)
        changed = deepcopy(self.core_event)
        changed['work_packet_digest'] = self.native_event['work_packet_digest']
        changed['result_contract']['work_packet_digest'] = self.native_event['work_packet_digest']
        self.assertEqual(changed, self.native_event)
        self.f.g.finish_execution(self.f.ident, self.row['automation_run_id'], self.row['lease_token'],
                                  'execution_finished', db_path=self.f.path)
        source = self.f.g.find(self.f.ident, self.f.path, include_private=True)
        child, created = self.orchestrator._resume(source, self.packet, self.f.gate_value, self.f.path, [], 'continue')
        self.assertTrue(created)
        with self.f.g.connect(self.f.path) as db:
            parent = dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (self.f.ident,)).fetchone())
            edge, publication = self.helper._edge(db, child, parent)
        self.assertEqual(edge['event_sha256'], self.f.b.digest(self.native_event))
        self.assertNotEqual(edge['event_sha256'], self.f.b.digest(self.core_event))
        self.assertEqual(publication[1:], (self.raw_result, self.native_event))

    async def test_canonical_replay_partial_projection_and_other_event_edits_are_rejected(self):
        await self.prepare_published()
        changes = [
            lambda event: event.update(deepcopy(self.core_event)),
            lambda event: event.__setitem__('work_packet_digest', self.packet['content_sha256']),
            lambda event: event['result_contract'].__setitem__('work_packet_digest', self.packet['content_sha256']),
            lambda event: event.__setitem__('work_packet_digest', '0' * 64),
            lambda event: event['result_contract'].__setitem__('work_packet_digest', '1' * 64),
            lambda event: event.__setitem__('summary', 'Changed native summary'),
            lambda event: event['result_contract'].__setitem__('outcome', 'blocked'),
            lambda event: event.__setitem__('acceptance_passed', True),
            lambda event: event.__setitem__('artifacts', [{'id': 'injected'}]),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index), self.f.g.connect(self.f.path) as db:
                db.execute('BEGIN')
                changed = deepcopy(self.native_event)
                change(changed)
                db.execute('UPDATE governance_result SET event_json=? WHERE invocation_id=?',
                           (self.f.b.canonical(changed), self.f.ident))
                try:
                    with self.assertRaises(ValueError):
                        self.helper.published(db, self.row)
                finally:
                    db.rollback()
                self.assertEqual(self.original_bytes(db), self.initial)

    async def test_native_projection_requires_one_exact_server_audit_and_hash(self):
        await self.prepare_published()
        for mode in ('missing', 'duplicate', 'actor', 'project', 'payload', 'hash'):
            with self.subTest(mode=mode), self.f.g.connect(self.f.path) as db:
                db.execute('BEGIN')
                # Corruption is confined to a rolled-back temporary database.
                # Production append-only guards remain present and unchanged.
                db.execute('DROP TRIGGER governance_event_no_update')
                db.execute('DROP TRIGGER governance_event_no_delete')
                audit = dict(db.execute("SELECT * FROM governance_event WHERE invocation_id=? AND kind='readiness_native_result'",
                                        (self.f.ident,)).fetchone())
                if mode == 'missing':
                    db.execute('DELETE FROM governance_event WHERE event_id=?', (audit['event_id'],))
                elif mode == 'duplicate':
                    self.f.g._event(db, audit['erp_project'], self.f.ident, audit['kind'], audit['actor'], json.loads(audit['payload']))
                else:
                    if mode == 'actor':
                        audit['actor'] = 'not-the-readiness-policy'
                    elif mode == 'project':
                        audit['erp_project'] = 'PROJ-OTHER'
                    elif mode == 'payload':
                        payload = json.loads(audit['payload'])
                        payload['pointers'].append('/outcome')
                        audit['payload'] = self.f.b.canonical(payload)
                    audit['event_hash'] = '0' * 64 if mode == 'hash' else self.f.g._digest([
                        audit[key] for key in ('event_id', 'erp_project', 'invocation_id', 'kind',
                                               'actor', 'payload', 'created_at', 'previous_hash')])
                    db.execute('UPDATE governance_event SET actor=?,erp_project=?,payload=?,event_hash=? WHERE event_id=?',
                               tuple(audit[key] for key in ('actor', 'erp_project', 'payload', 'event_hash', 'event_id')))
                try:
                    with self.assertRaises(ValueError):
                        self.helper.published(db, self.row)
                finally:
                    db.rollback()
                self.assertEqual(self.original_bytes(db), self.initial)

    async def test_no_projection_cannot_discard_a_previous_readiness_result_audit(self):
        await self.prepare_published()
        with self.f.g.connect(self.f.path) as db:
            db.execute('BEGIN')
            db.execute('DELETE FROM governance_start WHERE invocation_id=?', (self.f.ident,))
            db.execute('UPDATE governance_result SET event_json=? WHERE invocation_id=?',
                       (self.f.b.canonical(self.core_event), self.f.ident))
            try:
                with self.assertRaises(ValueError):
                    self.helper.published(db, self.row)
            finally:
                db.rollback()
            self.assertEqual(self.original_bytes(db), self.initial)

    async def test_native_start_receipt_local_result_and_original_fence_are_rederived(self):
        await self.prepare_published()
        for mode in ('receipt', 'local-result', 'fence', 'token', 'duplicate-proof'):
            with self.subTest(mode=mode), self.f.g.connect(self.f.path) as db:
                db.execute('BEGIN')
                row = deepcopy(self.row)
                if mode == 'receipt':
                    value = json.loads(db.execute('SELECT receipt_json FROM governance_start WHERE invocation_id=?',
                                                   (self.f.ident,)).fetchone()[0])
                    value['durable'] = False
                    db.execute('UPDATE governance_start SET receipt_json=? WHERE invocation_id=?',
                               (self.f.b.canonical(value), self.f.ident))
                elif mode == 'local-result':
                    db.execute('DELETE FROM governance_local_result WHERE invocation_id=?', (self.f.ident,))
                elif mode == 'fence':
                    row['lease_fence'] += 1
                elif mode == 'token':
                    row['lease_token'] = 'different-original-lease'
                else:
                    self.f.g._event(db, row['erp_project'], self.f.ident, 'original_start_readiness_receipt',
                                    'server-readiness-policy', {})
                try:
                    with self.assertRaises(ValueError):
                        self.helper.published(db, row)
                finally:
                    db.rollback()
                self.assertEqual(self.original_bytes(db), self.initial)


if __name__ == '__main__':
    unittest.main()

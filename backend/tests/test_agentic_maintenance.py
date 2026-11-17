"""Real SQLite admission races and original-receipt recovery; no live state."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/utils/agentic_maintenance.py'


class Maintenance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gate = self.root / 'maintenance-admission/gate.sqlite'
        spec = importlib.util.spec_from_file_location('maintenance_under_test', SOURCE)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        original = self.m.path
        self.m.path = lambda data_dir=None: original(self.root if data_dir is None else data_dir)

    def command(self, action, epoch, ident):
        return self.m.control(
            action, expected_epoch=epoch, command_id=ident, actor='synthetic-operator', db_path=self.gate
        )

    def result(self, ident, *, receipt_changes=None):
        ledger = self.root / 'ledger.sqlite'
        with sqlite3.connect(ledger) as db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS governance_result(invocation_id TEXT PRIMARY KEY,run_id TEXT,event_json TEXT,phase TEXT,receipt_json TEXT)'
            )
            event = {
                'event_id': 'event-' + ident,
                'task_id': 'TASK-test',
                'run_id': 'original-' + ident,
                'invocation_id': ident,
                'type': 'qa.passed',
            }
            receipt = {
                'event_id': event['event_id'],
                'task_id': event['task_id'],
                'run_id': event['run_id'],
                'durable': True,
                'modified': 'native-revision',
                'status': 'QA APPROVED',
                **(receipt_changes or {}),
            }
            db.execute(
                'INSERT OR REPLACE INTO governance_result VALUES(?,?,?,?,?)',
                (ident, event['run_id'], json.dumps(event), 'published', json.dumps(receipt)),
            )
        return self.m.governed_receipt(ident, ledger)

    async def test_gate_close_and_admission_are_one_real_transaction(self):
        self.command('close', 0, 'initial-close')
        self.command('open', 1, 'initial-open')
        barrier = threading.Barrier(17)

        def worker(index):
            barrier.wait()
            try:
                return self.m.acquire('governed', 'unit-' + str(index), db_path=self.gate)
            except self.m.MaintenanceDenied:
                return None

        def close():
            barrier.wait()
            return self.command('close', 2, 'race-close')

        with ThreadPoolExecutor(max_workers=17) as pool:
            futures = [pool.submit(worker, i) for i in range(16)]
            closing = pool.submit(close)
            leases = [r for r in [f.result() for f in futures] if r]
            closing.result()
        snapshot = self.m.status(self.gate)
        self.assertEqual(len(leases), snapshot['active_admissions'])
        self.assertFalse(snapshot['drained'])
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('governed', 'after-close', db_path=self.gate)
        for lease in leases:
            lease.finish(self.result(lease.reference))
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])
        self.assertTrue(self.command('seal', 3, 'seal-race')['drained'])

    async def test_unresolved_or_old_dead_process_lease_never_expires(self):
        lease = self.m.acquire('governed', 'unknown-original', db_path=self.gate)
        lease.unresolved()
        with sqlite3.connect(self.gate) as db:
            db.execute('UPDATE lease SET pid=?,created_ns=0', (2147483000,))
        self.command('close', 0, 'close-unknown')
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])
        with self.assertRaisesRegex(self.m.MaintenanceDenied, 'zero unresolved'):
            self.command('seal', 1, 'unsafe-seal')
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.Receipt('governed', 'unknown-original', 'caller-asserted')
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])

    async def test_exact_original_callback_settles_after_close_without_new_admission(self):
        self.m.acquire('governed', 'original', db_path=self.gate)
        self.command('close', 0, 'close-callback')
        with self.assertRaises(self.m.MaintenanceDenied):
            self.result('original', receipt_changes={'run_id': 'replacement'})
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])
        proof = self.result('original')
        self.assertEqual(1, self.m.settle_reference('governed', 'original', proof, db_path=self.gate))
        self.assertEqual(0, self.m.settle_reference('governed', 'original', proof, db_path=self.gate))
        self.assertTrue(self.command('seal', 1, 'seal-callback')['drained'])
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('governed', 'original', db_path=self.gate, recovery_receipt=proof)

    async def test_control_epoch_idempotency_rollback_and_immutable_audit(self):
        first = self.command('close', 0, 'same-command')
        self.assertEqual(first, self.command('close', 0, 'same-command'))
        with self.assertRaises(self.m.MaintenanceDenied):
            self.command('open', 0, 'stale-open')
        with self.assertRaises(self.m.MaintenanceDenied):
            self.command('open', 1, 'same-command')
        self.command('seal', 1, 'sealed')
        self.assertTrue(self.m.status(self.gate)['drained'])
        with sqlite3.connect(self.gate) as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE audit SET payload='{}'")
        reopened = self.command('open', 2, 'rollback-open')
        self.assertEqual('open', reopened['state'])

    async def test_protected_owner_modes_symlinks_and_readonly_status(self):
        self.assertEqual('uninitialised', self.m.status(self.gate)['state'])
        self.assertFalse(self.gate.parent.exists())
        self.gate.parent.mkdir(mode=0o755)
        self.gate.parent.chmod(0o755)  # Independent of the service's restrictive umask.
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('governed', 'bad-mode', db_path=self.gate)
        self.gate.parent.chmod(0o700)
        target = self.root / 'do-not-touch'
        target.write_text('unchanged')
        self.gate.symlink_to(target)
        with self.assertRaises((OSError, self.m.MaintenanceDenied)):
            self.m.acquire('governed', 'symlink', db_path=self.gate)
        self.assertEqual('unchanged', target.read_text())

    async def test_batch_transfer_closes_claim_to_background_task_race(self):
        with sqlite3.connect(self.root / 'webui.db') as db:
            db.execute(
                'CREATE TABLE automation_run(id TEXT,automation_id TEXT,status TEXT,chat_id TEXT,created_at INTEGER)'
            )

        @self.m.record_automation_receipt
        async def record(automation_id, status):
            with sqlite3.connect(self.root / 'webui.db') as db:
                db.execute(
                    'INSERT INTO automation_run VALUES(?,?,?,?,?)',
                    ('original-run', automation_id, status, 'owned-chat', time.time_ns()),
                )

        async def dispatch():
            await record('automation-one', 'success')

        with self.m.automation_batch(db_path=self.gate) as parent:
            self.command('close', 0, 'close-during-claim')
            task = self.m.spawn_automation(dispatch, 'automation-one')
            self.assertEqual(2, self.m.status(self.gate)['active_admissions'])
            with self.assertRaises(self.m.MaintenanceDenied):
                self.command('seal', 1, 'seal-before-transfer')
        await task
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('automation', 'late-child', db_path=self.gate, parent=parent)
        self.assertTrue(self.command('seal', 1, 'seal-after-transfer')['drained'])

    async def test_manual_play_denied_before_factory_and_errors_remain_unresolved(self):
        called = []

        async def factory():
            called.append(True)
            raise RuntimeError('unknown provider outcome')

        self.command('close', 0, 'close-play')
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.spawn_automation(factory, 'manual-one')
        self.assertEqual([], called)
        self.command('open', 1, 'open-play')
        with self.assertRaises(RuntimeError):
            await self.m.spawn_automation(factory, 'manual-one')
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.spawn_automation(factory, 'manual-one')
        self.assertEqual([True], called)

    async def test_open_gate_poll_reuses_same_original_lease_but_close_never_bypasses(self):
        first = self.m.acquire('erp', 'TASK-one', db_path=self.gate)
        first.unresolved()
        second = self.m.continue_owned('erp', 'TASK-one', db_path=self.gate)
        self.assertEqual(first.id, second.id)
        second.unresolved()
        self.command('close', 0, 'close-poll')
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.continue_owned('erp', 'TASK-one', db_path=self.gate)
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])

    async def test_broker_requires_exact_live_original_admission_even_after_close(self):
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.require_active('governed', 'original', db_path=self.gate)
        lease = self.m.acquire('governed', 'original', db_path=self.gate)
        self.command('close', 0, 'close-broker')
        proof = self.m.require_active('governed', 'original', db_path=self.gate)
        self.assertEqual(lease.id, proof['lease_id'])
        self.assertNotIn('token', proof)
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.require_active('governed', 'another-original', db_path=self.gate)
        with sqlite3.connect(self.gate) as db:
            db.execute("UPDATE lease SET process_start='recycled-pid' WHERE id=?", (lease.id,))
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.require_active('governed', 'original', db_path=self.gate)
        lease.unresolved()
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.require_active('governed', 'original', db_path=self.gate)
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])

    async def test_legacy_ERP_intake_settles_only_confirmed_no_attempt_or_synced_state(self):
        called = []
        store = types.SimpleNamespace(path=self.root / 'erp.sqlite', get=lambda task: None)
        resolver = types.SimpleNamespace(store=store)

        @self.m.legacy_erp
        async def intake(self, task, creator=None, revision=None):
            called.append(task)

        await intake(resolver, 'TASK-one', creator='persistent-agent', revision='native-revision')
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])
        store.get = lambda task: {'attempts': [{'run': 'unknown-original'}], 'synced': False}
        await intake(resolver, 'TASK-two', creator='persistent-agent', revision='native-revision')
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])
        self.command('close', 0, 'close-ERP-intake')
        with self.assertRaises(self.m.MaintenanceDenied):
            await intake(resolver, 'TASK-three', creator='persistent-agent')
        self.assertEqual(['TASK-one', 'TASK-two'], called)

    async def test_committed_pre_send_error_settles_but_matching_generic_error_does_not(self):
        with sqlite3.connect(self.root / 'webui.db') as db:
            db.execute(
                'CREATE TABLE automation_run(id TEXT,automation_id TEXT,status TEXT,chat_id TEXT,created_at INTEGER)'
            )

        @self.m.record_automation_receipt
        async def record(automation_id, status, error=None):
            with sqlite3.connect(self.root / 'webui.db') as db:
                db.execute(
                    'INSERT INTO automation_run VALUES(?,?,?,?,?)',
                    (str(time.time_ns()), automation_id, status, None, time.time_ns()),
                )

        async def no_user():
            await self.m.record_pre_submission(
                'owner_not_found', record, 'known-missing', 'error', error='User not found'
            )

        await self.m.spawn_automation(no_user, 'known-missing')
        await self.m.spawn_automation(no_user, 'known-missing')
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])

        async def unknown():
            await record('unknown-provider', 'error', error='User not found')

        await self.m.spawn_automation(unknown, 'unknown-provider')
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.spawn_automation(unknown, 'unknown-provider')

    async def test_exact_Dot_original_receipts_settle_only_positive_terminal_or_never_sent(self):
        with sqlite3.connect(self.root / 'webui.db') as db:
            db.execute(
                'CREATE TABLE automation_run(id TEXT,automation_id TEXT,status TEXT,chat_id TEXT,created_at INTEGER)'
            )
        with sqlite3.connect(self.root / 'abitdot-primary.sqlite') as db:
            db.execute(
                'CREATE TABLE dot_primary_runs(id TEXT,owner TEXT,automation_id TEXT,original_run_id TEXT,dot_id TEXT,status TEXT,envelope_json TEXT,receipt TEXT,response_text TEXT,reason TEXT)'
            )

        @self.m.record_automation_receipt
        async def record(automation_id, status, scheduled_run_id=None):
            with sqlite3.connect(self.root / 'webui.db') as db:
                db.execute(
                    'INSERT INTO automation_run VALUES(?,?,?,?,?)',
                    (scheduled_run_id, automation_id, status, None, time.time_ns()),
                )

        cases = [
            ('prepared', 'verified_pre_submission_busy', None, None, True),
            ('unavailable', 'verified_pre_submission_rejection', None, None, True),
            ('delegated', None, 'original-receipt', 'completed original reply', True),
            ('delegated', None, 'accepted-only', None, False),
            ('prepared', None, None, None, False),
            ('unknown', 'delivery_outcome_unknown', None, None, False),
        ]
        for index, (state, reason, receipt, reply, safe) in enumerate(cases):
            auto, run = 'automation-' + str(index), 'run-' + str(index)
            key = self.m._digest(['owner', auto, run])
            envelope = {
                'owner_id': 'owner',
                'automation_id': auto,
                'run_id': run,
                'dot_id': 'exact-dot',
                'idempotency_key': key,
            }
            with sqlite3.connect(self.root / 'abitdot-primary.sqlite') as db:
                db.execute(
                    'INSERT INTO dot_primary_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (key, 'owner', auto, run, 'exact-dot', state, json.dumps(envelope), receipt, reply, reason),
                )

            async def original(auto=auto, run=run):
                await record(auto, 'deferred', scheduled_run_id=run)

            await self.m.spawn_automation(original, auto)
            self.assertEqual(not safe, self.m.has_lease('automation', auto, db_path=self.gate))
        self.assertEqual(3, self.m.status(self.gate)['active_admissions'])

    async def test_manual_Dot_completed_original_uses_exact_server_link_not_latest_other_turn(self):
        with sqlite3.connect(self.root / 'webui.db') as db:
            db.execute(
                'CREATE TABLE automation_run(id TEXT,automation_id TEXT,status TEXT,chat_id TEXT,created_at INTEGER)'
            )
        with sqlite3.connect(self.root / 'abitdot-primary.sqlite') as db:
            db.execute(
                'CREATE TABLE dot_primary_runs(id TEXT,owner TEXT,automation_id TEXT,original_run_id TEXT,dot_id TEXT,status TEXT,envelope_json TEXT,receipt TEXT,response_text TEXT,reason TEXT)'
            )
        auto, run = 'manual-automation', 'manual-original-dot-run'
        key = self.m._digest(['owner', auto, run])
        envelope = {
            'owner_id': 'owner',
            'automation_id': auto,
            'run_id': run,
            'dot_id': 'exact-dot',
            'idempotency_key': key,
        }
        with sqlite3.connect(self.root / 'abitdot-primary.sqlite') as db:
            db.execute(
                'INSERT INTO dot_primary_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                (
                    key,
                    'owner',
                    auto,
                    run,
                    'exact-dot',
                    'delegated',
                    json.dumps(envelope),
                    'original-receipt',
                    'completed original reply',
                    None,
                ),
            )

        @self.m.record_automation_receipt
        async def record(automation_id, status):
            with sqlite3.connect(self.root / 'webui.db') as db:
                db.execute(
                    'INSERT INTO automation_run VALUES(?,?,?,?,?)',
                    ('native-manual-run-' + str(time.time_ns()), automation_id, status, None, time.time_ns()),
                )

        async def actual():
            await self.m.record_dot_outcome(envelope, record, auto, 'delegated')

        await self.m.spawn_automation(actual, auto)
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])

        async def wrong():
            await self.m.record_dot_outcome({**envelope, 'run_id': 'another-turn'}, record, auto, 'delegated')

        await self.m.spawn_automation(wrong, auto)
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])

    async def test_closed_ERP_terminal_receipt_publishes_but_cannot_reset_or_start(self):
        file = self.root / 'erp.sqlite'
        value = {
            'task': 'TASK-terminal',
            'status': 'Completed',
            'attempts': [{'id': 'original-attempt', 'finished': 123, 'phase': 'verified'}],
            'synced': False,
        }
        with sqlite3.connect(file) as db:
            db.execute('CREATE TABLE erp_runs(task TEXT PRIMARY KEY,state TEXT)')
            db.execute('INSERT INTO erp_runs VALUES(?,?)', (value['task'], json.dumps(value)))

        def get(task):
            with sqlite3.connect(file) as db:
                row = db.execute('SELECT state FROM erp_runs WHERE task=?', (task,)).fetchone()
                return json.loads(row[0]) if row else None

        def put(run):
            with sqlite3.connect(file) as db:
                db.execute('UPDATE erp_runs SET state=? WHERE task=?', (json.dumps(run), run['task']))

        resolver = types.SimpleNamespace(store=types.SimpleNamespace(path=file, get=get))
        original = self.m.acquire('erp', value['task'], db_path=self.gate)
        original.unresolved()
        self.command('close', 0, 'close-terminal-ERP')
        calls = []

        @self.m.legacy_erp
        async def advance(self, run):
            calls.append('publish-original')
            run['synced'] = True
            put(run)

        await advance(resolver, get(value['task']))
        self.assertEqual(['publish-original'], calls)
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])

        @self.m.legacy_erp
        async def reset(self, task_id):
            calls.append('new-run')

        @self.m.legacy_erp
        async def advance(self, run):
            await reset(self, run['task'])

        with self.assertRaisesRegex(self.m.MaintenanceDenied, 'cannot reopen'):
            await advance(resolver, get(value['task']))
        self.assertEqual(['publish-original'], calls)
        self.assertEqual(1, self.m.status(self.gate)['active_admissions'])

    async def test_QA_exact_published_receipt_recovery_cannot_claim_another_test(self):
        qa = self.root / 'erp-pending-review-qa.sqlite'
        with sqlite3.connect(qa) as db:
            db.execute('CREATE TABLE review(task TEXT,run TEXT,binding TEXT,phase TEXT,status TEXT)')
            db.execute(
                'INSERT INTO review VALUES(?,?,?,?,?)', ('TASK-one', 'run-one', 'exact-binding', 'publish', 'pass')
            )
        m = self.m

        class Store:
            path = qa

            @m.qa_claim
            def claim(self, task, run, binding):
                raise AssertionError('A recovery must never claim a new test')

        store = Store()
        namespace = {'assigned_review_id': lambda *args: 'run-one'}
        exec(
            'async def review_assigned_task(erp,config,store,todo,policy,run_assigned_review,publish_review):\n store.claim(todo["reference_name"],"run-two","changed-binding")\n',
            namespace,
        )
        wrapped = self.m.legacy_qa(namespace['review_assigned_task'])

        async def get(*args):
            return {'name': 'TASK-one'}

        self.command('close', 0, 'close-QA')
        with self.assertRaisesRegex(self.m.MaintenanceDenied, 'cannot start tests'):
            await wrapped(
                types.SimpleNamespace(get=get),
                {},
                store,
                {'reference_name': 'TASK-one', 'name': 'TODO-one'},
                {},
                None,
                None,
            )
        with sqlite3.connect(qa) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM review').fetchone()[0])

    async def test_QA_old_different_run_receipt_cannot_admit_new_run_while_closed(self):
        qa = self.root / 'erp-pending-review-qa.sqlite'
        with sqlite3.connect(qa) as db:
            db.execute('CREATE TABLE review(task TEXT,run TEXT,binding TEXT,phase TEXT,status TEXT)')
            db.execute('INSERT INTO review VALUES(?,?,?,?,?)', ('TASK-one', 'old-run', 'old-binding', 'done', 'pass'))
        namespace = {'assigned_review_id': lambda *args: 'new-run'}
        exec(
            'async def review_assigned_task(erp,config,store,todo,policy,run_assigned_review,publish_review):\n raise AssertionError("No new test may start")\n',
            namespace,
        )

        async def get(*args):
            return {'name': 'TASK-one'}

        self.command('close', 0, 'close-old-QA')
        with self.assertRaises(self.m.MaintenanceDenied):
            await self.m.legacy_qa(namespace['review_assigned_task'])(
                types.SimpleNamespace(get=get),
                {},
                types.SimpleNamespace(path=qa),
                {'reference_name': 'TASK-one', 'name': 'TODO-one'},
                {},
                None,
                None,
            )

    async def test_QA_original_publish_callback_finishes_while_closed_without_new_test(self):
        qa = self.root / 'erp-pending-review-qa.sqlite'
        with sqlite3.connect(qa) as db:
            db.execute('CREATE TABLE review(task TEXT,run TEXT,binding TEXT,phase TEXT,status TEXT)')
            db.execute(
                'INSERT INTO review VALUES(?,?,?,?,?)', ('TASK-one', 'run-one', 'exact-binding', 'publish', 'pass')
            )
        original = self.m.acquire('qa', 'TASK-one:run-one', db_path=self.gate)
        original.unresolved()
        namespace = {'assigned_review_id': lambda *args: 'run-one', 'sqlite3': sqlite3}
        exec(
            'async def review_assigned_task(erp,config,store,todo,policy,run_assigned_review,publish_review):\n with sqlite3.connect(store.path) as db: db.execute("UPDATE review SET phase=\'done\' WHERE task=\'TASK-one\' AND run=\'run-one\'")\n',
            namespace,
        )

        async def get(*args):
            return {'name': 'TASK-one'}

        self.command('close', 0, 'close-original-QA')
        await self.m.legacy_qa(namespace['review_assigned_task'])(
            types.SimpleNamespace(get=get),
            {},
            types.SimpleNamespace(path=qa),
            {'reference_name': 'TASK-one', 'name': 'TODO-one'},
            {},
            None,
            None,
        )
        self.assertEqual(0, self.m.status(self.gate)['active_admissions'])
        self.assertTrue(self.command('seal', 1, 'seal-original-QA')['drained'])

    async def test_CLI_status_close_seal_open_are_explicit_audited_local_operations(self):
        def cli(*args):
            r = subprocess.run(
                [sys.executable, str(SOURCE), *args, '--data-dir', str(self.root)],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(r.stdout)

        self.assertEqual('uninitialised', cli('status')['state'])
        self.assertEqual(
            'draining',
            cli('close', '--expected-epoch', '0', '--command-id', 'cli-close', '--actor', 'operator')['state'],
        )
        self.assertTrue(
            cli('seal', '--expected-epoch', '1', '--command-id', 'cli-seal', '--actor', 'operator')['drained']
        )
        self.assertEqual(
            'open', cli('open', '--expected-epoch', '2', '--command-id', 'cli-open', '--actor', 'operator')['state']
        )


if __name__ == '__main__':
    unittest.main()

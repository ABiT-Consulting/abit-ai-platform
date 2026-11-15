"""Protected real SQLite recovery; no production DB, services or network."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import test_agentic_controls_events as events
import test_agentic_start_recovery as original
P = events.P

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'open_webui/utils/agentic_maintenance.py'
if not SOURCE.is_file():
    SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/utils/agentic_maintenance.py'


class MaintenanceStartRecovery(events.Fixture):
    def setUp(self):
        super().setUp()
        self.start = self.load('agentic_start_recovery')
        self.bridge = original.StartBridge(self)
        spec = importlib.util.spec_from_file_location('open_webui.utils.agentic_maintenance', SOURCE)
        self.m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.m
        self.utils.agentic_maintenance = self.m
        spec.loader.exec_module(self.m)
        self.gate_db = Path(self.temp.name) / 'maintenance-admission/gate.sqlite'
        self.m.control('close', expected_epoch=0, command_id='close-initial', actor='fixture', db_path=self.gate_db)
        self.m.control('seal', expected_epoch=1, command_id='seal-initial', actor='fixture', db_path=self.gate_db)
        self.m.configure_scope('restrict-project', expected_epoch=2, command_id='scope-original', actor='fixture',
                               project_id=P, db_path=self.gate_db)
        self.m.control('open', expected_epoch=2, command_id='open-scoped', actor='fixture', db_path=self.gate_db)
        self.m.control('close', expected_epoch=3, command_id='close-restart', actor='fixture', db_path=self.gate_db)
        self.m.control('open', expected_epoch=4, command_id='open-restart', actor='fixture', db_path=self.gate_db)
        with self.g._transaction(self.path) as db:
            db.execute("CREATE TABLE governance_gateway_session(session_id TEXT PRIMARY KEY,invocation_id TEXT UNIQUE,scope_json TEXT,admission_json TEXT,connection_hash TEXT,client_session_hash TEXT,state TEXT,created_at INTEGER)")
        self.row, self.event = original.StartRecovery.original(self)
        self.start.record_start_receipt(self.row['invocation_id'], self.bridge.receipt, self.path)
        self.old = self.m._OWNED[(str(self.gate_db), 'governed', self.row['invocation_id'])]
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait(timeout=5)
        self.dead_pid = dead.pid
        self.set_owner(self.dead_pid, 'dead-process-start')
        self.governance_before = self.governance_rows()
        self.maintenance_before = self.leases()[0]

    def set_owner(self, pid, start, state='active'):
        with self.m._db(self.gate_db) as db:
            db.execute('UPDATE lease SET pid=?,process_start=?,state=? WHERE id=?', (pid, start, state, self.old.id))

    def governance_rows(self):
        with self.g.connect(self.path) as db:
            return {table: [dict(r) for r in db.execute('SELECT * FROM ' + table)] for table in
                    ('governance_invocation', 'governance_task_lease', 'governance_start', 'governance_event')}

    def resume_original(self):
        with self.g._transaction(self.path) as db:
            return self.m.recover_governed_start(self.row['invocation_id'], db, db_path=self.gate_db)

    def leases(self):
        with self.m._db(self.gate_db, write=False) as db:
            return [dict(r) for r in db.execute('SELECT * FROM lease ORDER BY created_ns,id')]

    async def test_dead_active_owner_transfers_same_run_at_open5_and_preserves_history(self):
        lease = self.resume_original()
        self.assertEqual(self.m.status(self.gate_db)['epoch'], 5)
        self.assertFalse(lease.recovering)
        self.assertEqual(self.m.require_active('governed', self.row['invocation_id'], db_path=self.gate_db)['lease_id'], lease.id)
        old, new = self.leases()
        self.assertEqual(old['state'], 'finished')
        self.assertEqual(new['parent_id'], old['id'])
        self.assertEqual(new['pid'], os.getpid())
        self.assertEqual(new['state'], 'active')
        self.assertEqual(len(old['receipt_sha256']), 64)
        for key in ('id', 'kind', 'reference', 'token_sha256', 'epoch', 'pid', 'process_start', 'created_ns', 'parent_id'):
            self.assertEqual(old[key], self.maintenance_before[key])
        self.assertEqual(self.governance_before, self.governance_rows())
        with self.m._db(self.gate_db) as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE audit SET payload='{}'")

    async def test_governance_rollback_reuses_exact_owned_active_without_second_transfer(self):
        with self.assertRaisesRegex(RuntimeError, 'crash-window'):
            with self.g._transaction(self.path) as db:
                first = self.m.recover_governed_start(self.row['invocation_id'], db, db_path=self.gate_db)
                db.execute("UPDATE governance_invocation SET state='running' WHERE invocation_id=?", (self.row['invocation_id'],))
                raise RuntimeError('crash-window')
        before = self.leases()
        second = self.resume_original()
        self.assertIs(first, second)
        self.assertEqual(before, self.leases())
        self.assertEqual(self.governance_before, self.governance_rows())
        # Even a shape-valid changed receipt cannot reuse the audited proof.
        with self.assertRaises(RuntimeError):
            with self.g._transaction(self.path) as db:
                db.execute('UPDATE governance_start SET receipt_json=?', (
                    self.start.canonical({**self.bridge.receipt, 'modified': 'changed-after-transfer'}),))
                with self.assertRaisesRegex(self.m.MaintenanceDenied, 'proof changed'):
                    self.m.recover_governed_start(self.row['invocation_id'], db, db_path=self.gate_db)
                raise RuntimeError('rollback-changed-proof')
        # A subsequent restart can transfer that same audited but now dead admission.
        with self.m._db(self.gate_db) as db:
            db.execute('UPDATE lease SET pid=?,process_start=? WHERE id=?', (self.dead_pid, 'gone-again', first.id))
        self.m._OWNED.clear()
        third = self.resume_original()
        self.assertNotEqual(third.id, first.id)
        self.assertEqual(len(self.leases()), 3)
        self.assertEqual(self.m.require_active('governed', self.row['invocation_id'], db_path=self.gate_db)['lease_id'], third.id)

    async def test_exact_self_owned_active_and_unresolved_reuse_original_token(self):
        self.set_owner(os.getpid(), self.m._start(), 'active')
        lease = self.resume_original()
        self.assertIs(lease, self.old)
        self.assertEqual(len(self.leases()), 1)
        lease.unresolved()
        self.assertIs(self.resume_original(), lease)
        self.assertEqual(self.leases()[-1]['state'], 'active')

    async def test_missing_changed_receipt_event_fence_submission_and_session_deny_without_transfer(self):
        before = self.leases()
        changes = [
            ("UPDATE governance_start SET receipt_json=NULL", ()),
            ("UPDATE governance_start SET event_sha256=?", ('0' * 64,)),
            ("UPDATE governance_start SET receipt_json=?", (json.dumps({**self.bridge.receipt, 'run_id': 'forged-run'}),)),
            ("UPDATE governance_task_lease SET fence=fence+1", ()),
            ("UPDATE governance_invocation SET submitted_at=1", ()),
            ("INSERT INTO governance_gateway_session VALUES('session',?,'{}','{}','hash',NULL,'active',0)", (self.row['invocation_id'],)),
            ("CREATE TABLE governance_local_result(invocation_id TEXT)", ()),
        ]
        for query, args in changes:
            with self.subTest(query=query):
                with self.assertRaises(RuntimeError):
                    with self.g._transaction(self.path) as db:
                        db.execute(query, args)
                        if 'CREATE TABLE' in query:
                            db.execute('INSERT INTO governance_local_result VALUES(?)', (self.row['invocation_id'],))
                        with self.assertRaises(self.m.MaintenanceDenied):
                            self.m.recover_governed_start(self.row['invocation_id'], db, db_path=self.gate_db)
                        raise RuntimeError('rollback-fixture')
                self.assertEqual(before, self.leases())

    async def test_live_foreign_owner_and_missing_cached_token_deny(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'])
        try:
            start = Path('/proc/' + str(proc.pid) + '/stat').read_text().split(') ', 1)[1].split()[19]
            self.set_owner(proc.pid, start)
            before = self.leases()
            with self.assertRaisesRegex(self.m.MaintenanceDenied, 'remains live'):
                self.resume_original()
            self.assertEqual(before, self.leases())
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        self.set_owner(self.dead_pid, 'gone')
        self.resume_original()
        self.m._OWNED.clear()
        with self.assertRaisesRegex(self.m.MaintenanceDenied, 'still owns'):
            self.resume_original()

    async def test_scope_wrong_closed_and_unlocked_governance_deny(self):
        target = self.m._scope_path(self.gate_db)
        policy = json.loads(target.read_text())
        target.write_text(json.dumps({**policy, 'project_id': 'PROJ-OTHER'}))
        with self.assertRaisesRegex(self.m.MaintenanceDenied, 'restricted'):
            self.resume_original()
        target.write_text(json.dumps(policy))
        self.m.control('close', expected_epoch=5, command_id='close-denial', actor='fixture', db_path=self.gate_db)
        with self.assertRaisesRegex(self.m.MaintenanceDenied, 'open maintenance gate'):
            self.resume_original()
        with self.g.connect(self.path) as db:
            with self.assertRaisesRegex(self.m.MaintenanceDenied, 'locked governance'):
                self.m.recover_governed_start(self.row['invocation_id'], db, db_path=self.gate_db)

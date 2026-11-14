"""Real SQLite/private-file first-Project admission; no production state."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/utils/agentic_maintenance.py'


class ScopedAdmission(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gate = self.root / 'maintenance-admission/gate.sqlite'
        spec = importlib.util.spec_from_file_location('scoped_admission_under_test', SOURCE)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.control('close', expected_epoch=0, command_id='close-1', actor='operator', db_path=self.gate)
        self.m.control('seal', expected_epoch=1, command_id='seal-2', actor='operator', db_path=self.gate)

    def restrict(self, project='PROJ-FIRST', command='restrict-2', epoch=2):
        return self.m.configure_scope(
            'restrict-project',
            expected_epoch=epoch,
            command_id=command,
            actor='operator',
            project_id=project,
            db_path=self.gate,
        )

    def opened(self):
        self.restrict()
        return self.m.control('open', expected_epoch=2, command_id='open-3', actor='operator', db_path=self.gate)

    def test_wrong_or_missing_project_and_all_legacy_kinds_deny_before_lease_insertion(self):
        status = self.opened()
        self.assertEqual(status['admission_scope']['mode'], 'governed_project_only')
        self.assertEqual(status['admission_scope']['project_id'], 'PROJ-FIRST')
        for kind in ('batch', 'automation', 'erp', 'qa'):
            with self.assertRaises(self.m.MaintenanceDenied):
                self.m.acquire(kind, 'unchanged-original', governance_project='PROJ-FIRST', db_path=self.gate)
        for project in (None, 'PROJ-OTHER'):
            with self.assertRaises(self.m.MaintenanceDenied):
                self.m.acquire('governed', 'original-invocation', governance_project=project, db_path=self.gate)
        with sqlite3.connect(self.gate) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM lease').fetchone()[0], 0)
        original = self.m.acquire('governed', 'original-invocation', governance_project='PROJ-FIRST', db_path=self.gate)
        original.unresolved()
        continued = self.m.continue_owned('governed', 'original-invocation', db_path=self.gate)
        self.assertEqual(continued.id, original.id)

    def test_policy_change_requires_exact_sealed_epoch_and_zero_original_leases(self):
        with self.assertRaises(self.m.MaintenanceDenied):
            self.restrict(epoch=1)
        self.opened()
        with self.assertRaises(self.m.MaintenanceDenied):
            self.restrict('PROJ-OTHER', 'other', epoch=3)
        lease = self.m.acquire('governed', 'original', governance_project='PROJ-FIRST', db_path=self.gate)
        self.m.control('close', expected_epoch=3, command_id='close-4', actor='operator', db_path=self.gate)
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.configure_scope(
                'remove-scope',
                expected_epoch=4,
                command_id='remove',
                actor='operator',
                expected_policy_sha256=self.m.status(self.gate)['admission_scope']['sha256'],
                db_path=self.gate,
            )
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.control('seal', expected_epoch=4, command_id='seal-5', actor='operator', db_path=self.gate)
        lease.finish(self.m._proof('governed', 'original', {'server_terminal_receipt': 'synthetic'}))
        self.m.control('seal', expected_epoch=4, command_id='seal-5', actor='operator', db_path=self.gate)
        pin = self.m.status(self.gate)['admission_scope']['sha256']
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.configure_scope(
                'remove-scope',
                expected_epoch=5,
                command_id='remove-wrong',
                actor='operator',
                expected_policy_sha256='0' * 64,
                db_path=self.gate,
            )
        kwargs = dict(
            expected_epoch=5, command_id='remove-5', actor='operator', expected_policy_sha256=pin, db_path=self.gate
        )
        self.assertEqual(self.m.configure_scope('remove-scope', **kwargs)['admission_scope'], {'mode': 'unrestricted'})
        self.assertEqual(self.m.configure_scope('remove-scope', **kwargs)['admission_scope'], {'mode': 'unrestricted'})
        self.m.control('open', expected_epoch=5, command_id='open-6', actor='operator', db_path=self.gate)
        self.assertIsNotNone(self.m.acquire('automation', 'preserved-old-job', db_path=self.gate))

    def test_private_policy_metadata_malformed_and_duplicate_keys_fail_closed(self):
        self.opened()
        target = self.m._scope_path(self.gate)
        original = target.read_bytes()
        for bad in (b'{', b'{}', original.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')):
            target.write_bytes(bad)
            with self.assertRaises(self.m.MaintenanceDenied):
                self.m.acquire('governed', 'no-claim', governance_project='PROJ-FIRST', db_path=self.gate)
        target.write_bytes(original)
        target.chmod(0o644)
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('governed', 'no-claim', governance_project='PROJ-FIRST', db_path=self.gate)
        target.chmod(0o600)
        other = target.with_name('not-the-policy')
        target.rename(other)
        target.symlink_to(other)
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('governed', 'no-claim', governance_project='PROJ-FIRST', db_path=self.gate)
        with sqlite3.connect(self.gate) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM lease').fetchone()[0], 0)

    def test_original_removal_after_unlink_before_audit_recovers_without_second_unlink(self):
        original = self.restrict()
        kwargs = dict(
            expected_epoch=2,
            command_id='remove-original',
            actor='operator',
            expected_policy_sha256=original['admission_scope']['sha256'],
            db_path=self.gate,
        )
        unlink = Path.unlink
        calls = []

        def counted(path, *args, **kwargs):
            calls.append(path)
            return unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', counted):
            with patch.object(self.m, '_audit', side_effect=RuntimeError('simulated audit commit failure')):
                with self.assertRaises(RuntimeError):
                    self.m.configure_scope('remove-scope', **kwargs)
            self.assertFalse(self.m._scope_path(self.gate).exists())
            with self.assertRaises(self.m.MaintenanceDenied):
                self.m.configure_scope('remove-scope', **{**kwargs, 'actor': 'different-operator'})
            result = self.m.configure_scope('remove-scope', **kwargs)
            self.assertEqual(result['admission_scope'], {'mode': 'unrestricted'})
            self.assertEqual(calls, [self.m._scope_path(self.gate)])
        with sqlite3.connect(self.gate) as db:
            self.assertEqual(
                db.execute('SELECT COUNT(*) FROM audit WHERE event_id=?', ('remove-original',)).fetchone()[0], 1
            )

    def test_exact_server_terminal_receipt_can_bookkeep_but_no_new_legacy_dispatch(self):
        self.opened()
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('erp', 'old-task', recovery_receipt=True, db_path=self.gate)
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.Receipt('erp', 'old-task', 'model-asserted-digest')
        store = self.root / 'original.sqlite'
        value = {'task': 'old-task', 'status': 'Completed', 'attempts': [{'finished': True, 'phase': 'verified'}]}
        with sqlite3.connect(store) as db:
            db.execute('CREATE TABLE erp_runs(task TEXT,state TEXT)')
            db.execute('INSERT INTO erp_runs VALUES(?,?)', ('old-task', json.dumps(value)))
        proof = self.m._erp_terminal_receipt(types.SimpleNamespace(path=store), 'old-task')
        lease = self.m.acquire('erp', 'old-task', recovery_receipt=proof, db_path=self.gate)
        self.assertTrue(lease.recovering)
        lease.unresolved()
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.continue_owned('erp', 'old-task', db_path=self.gate)
        lease = self.m.acquire('erp', 'old-task', recovery_receipt=proof, db_path=self.gate)
        self.assertTrue(lease.recovering)
        lease.finish(proof)
        with self.assertRaises(self.m.MaintenanceDenied):
            self.m.acquire('erp', 'old-task', db_path=self.gate)

    def test_real_governance_claim_uses_ledger_project_and_preserves_same_queued_identity(self):
        # Reuse the existing real-ledger setup, not a fake acquire assertion.
        from test_agentic_ledger_fences import DurableLedger, P, GATE, g

        fixture = DurableLedger('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from open_webui.utils import agentic_maintenance as actual

        gate = actual.path(fixture.db.parent)
        actual.control('close', expected_epoch=0, command_id='close', actor='operator', db_path=gate)
        actual.control('seal', expected_epoch=1, command_id='seal', actor='operator', db_path=gate)
        actual.configure_scope(
            'restrict-project',
            expected_epoch=2,
            command_id='scope',
            actor='operator',
            project_id='PROJ-OTHER',
            db_path=gate,
        )
        actual.control('open', expected_epoch=2, command_id='open', actor='operator', db_path=gate)
        fixture.enqueue()
        self.assertIsNone(fixture.claim())
        original = g.find('invocation-00000001', fixture.db)
        self.assertEqual((original['state'], original['dispatch_attempts']), ('queued', 0))
        pin = actual.status(gate)['admission_scope']['sha256']
        actual.control('close', expected_epoch=3, command_id='close-again', actor='operator', db_path=gate)
        actual.control('seal', expected_epoch=4, command_id='seal-again', actor='operator', db_path=gate)
        actual.configure_scope(
            'remove-scope',
            expected_epoch=5,
            command_id='remove',
            actor='operator',
            expected_policy_sha256=pin,
            db_path=gate,
        )
        actual.configure_scope(
            'restrict-project',
            expected_epoch=5,
            command_id='correct-scope',
            actor='operator',
            project_id=P,
            db_path=gate,
        )
        actual.control('open', expected_epoch=5, command_id='reopen', actor='operator', db_path=gate)
        claimed = fixture.claim()
        self.assertEqual(claimed['invocation_id'], original['invocation_id'])
        self.assertEqual(claimed['dispatch_attempts'], 1)
        self.assertEqual(len(g.list_invocations(P, fixture.db)), 1)


if __name__ == '__main__':
    unittest.main()

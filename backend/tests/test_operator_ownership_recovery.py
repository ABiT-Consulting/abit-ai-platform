import fcntl
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[2] / 'deployment' / 'operator_ownership_recovery.py'
SPEC = importlib.util.spec_from_file_location('operator_ownership_recovery_under_test', MODULE)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)

NOW = 2_000_000_000.0
SCOPE = 'linkedin-core-seven-file-release:' + recovery.APPROVED_MANIFEST
RECEIVER_ID = recovery.RECEIVER


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def setup_case(tmp_path, workstreams=None, jobs=None):
    owner_path = tmp_path / 'operator-session.json'
    owner = {
        'owner': recovery.EXPECTED_OWNER, 'state': recovery.OWNER_STATE,
        'phase': 'PNPM_FIRST_OPERATOR_CREATE_ONLY_INSTALLATION',
        'updated_at': iso(NOW - 10000), 'lease_expires_at': iso(NOW - 1000),
        'released_at': None, 'operator_jobs_in_progress': jobs or [],
        'active_workstreams': workstreams if workstreams is not None else ['workstream-A'],
        'original_unknowns': {'lease': recovery.REQUIRED_PRESERVED_HOLDS['maintenance_lease'],
                              'dot_event': recovery.REQUIRED_PRESERVED_HOLDS['legacy_dot_event']},
        'history': [{'id': 'original-admission-1', 'state': 'unresolved'}],
    }
    raw = json.dumps(owner, sort_keys=True).encode()
    owner_path.write_bytes(raw); os.chmod(owner_path, 0o640)
    lock_paths = tuple(tmp_path / name for name in (
        'operator-session.json.lock', 'operator-session.json.claim.lock',
        '.operator-session-review.lock', 'operator-session.lock'))
    for path in lock_paths: path.touch(mode=0o660)
    fence_path = tmp_path / 'operator-session-fence-receipt.json'
    receipt = {
        'schema_version': 1, 'status': 'FENCED_OR_PRESERVED',
        'previous_owner': recovery.EXPECTED_OWNER, 'owner_record_sha256': sha(raw),
        'issued_at': iso(NOW - 100),
        'owner_session': {'disposition': 'absent', 'absence_verified': True, 'evidence_sha256': 'c' * 64, 'processes': []},
        'workstreams': {name: {'disposition': 'fenced', 'evidence_sha256': 'a' * 64,
            'processes': [{'pid': 99999999, 'start_ticks': 12345}]} for name in owner['active_workstreams']},
        'jobs': {name: {'disposition': 'fenced',
            'processes': [{'pid': 99999998, 'start_ticks': 12346}]} for name in owner['operator_jobs_in_progress']},
        'preserved_uncertain_work': recovery.REQUIRED_PRESERVED_HOLDS,
    }
    fence_path.write_text(json.dumps(receipt)); os.chmod(fence_path, 0o600)
    return owner_path, fence_path, lock_paths, owner, sha(raw)


def request(owner_sha, **overrides):
    value = {
        'admin_id': 'authenticated-admin-001', 'admin_role': 'admin',
        'expected_owner': recovery.EXPECTED_OWNER, 'expected_record_sha256': owner_sha,
        'receiving_operator': recovery.RECEIVER, 'receiving_principal_id': RECEIVER_ID,
        'reason': 'Recover the expired owner after the trusted host fence is verified.',
        'scope': SCOPE, 'execution_expires_at': iso(NOW + 1800),
        'recovery_id': str(uuid.uuid4()), 'now': NOW, 'require_root': False,
    }
    value.update(overrides); return value


def call(owner_path, fence_path, lock_paths, owner_sha, audit_dir, **overrides):
    trusted_uid = overrides.pop('trusted_fence_uid', os.geteuid())
    return recovery.recover_expired_operator(
        **request(owner_sha, **overrides), owner_file=owner_path, fence_file=fence_path,
        audit_dir=audit_dir, trusted_fence_uid=trusted_uid, trusted_audit_uid=os.geteuid(), lock_paths=lock_paths)


class OperatorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp.name)
        self.owner, self.fence, self.locks, self.original, self.digest = setup_case(self.tmp)
        self.audit = self.tmp / 'audit'

    def tearDown(self): self.temp.cleanup()

    def test_unauthorized_and_expired_lease_alone_are_refused(self):
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'administrator role'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit, admin_role='user')
        self.fence.unlink()
        with self.assertRaises(FileNotFoundError): call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)

    def test_concurrent_renewal_or_record_drift_wins_cas(self):
        renewed = dict(self.original); renewed['lease_expires_at'] = iso(NOW + 60)
        self.owner.write_text(json.dumps(renewed, sort_keys=True))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'hash changed'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertEqual(json.loads(self.owner.read_text())['owner'], recovery.EXPECTED_OWNER)

    def test_missing_workstream_fence_and_missing_hold_preservation_fail(self):
        data = json.loads(self.fence.read_text()); data['workstreams'] = {}
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'every active workstream'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)
        data['workstreams'] = {'workstream-A': {'disposition': 'fenced', 'evidence_sha256': 'a'*64,
            'processes': [{'pid': 99999999, 'start_ticks': 12345}]}}
        data['preserved_uncertain_work'] = {}; self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'preserve both'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)

    def test_fence_receipt_trust_and_freshness_are_required(self):
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'trusted host administrator'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit, trusted_fence_uid=os.geteuid()+1)
        data = json.loads(self.fence.read_text()); data['issued_at'] = iso(NOW - 900)
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'fresh'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)

    def test_competing_recovery_attempts_are_single_winner_and_preserve_history(self):
        first = call(self.owner, self.fence, self.locks, self.digest, self.audit)
        current = json.loads(self.owner.read_text())
        self.assertEqual(first['status'], 'ADMINISTRATOR_RECOVERY_COMMITTED')
        self.assertEqual(current['owner'], recovery.RECEIVER)
        self.assertEqual(current['history'], self.original['history'])
        self.assertEqual(current['active_workstreams'], self.original['active_workstreams'])
        self.assertEqual(current['original_unknowns'], self.original['original_unknowns'])
        self.assertEqual(current['operator_recovery']['status'], 'COMMITTED')
        self.assertFalse(first['maintenance_gate_mutated']); self.assertFalse(first['agent_started'])
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'hash changed'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertEqual(sha(self.owner.read_bytes()), first['new_record_sha256'])

    def test_receiver_ack_is_one_use_and_creates_private_scope_binding(self):
        first = call(self.owner, self.fence, self.locks, self.digest, self.audit)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'receiver identity'):
            recovery.acknowledge_recovered_operator(receiver_id='wrong', receiver_token=first['receiver_ack_token'],
                expected_record_sha256=first['new_record_sha256'], owner_file=self.owner,
                audit_dir=self.audit, now=NOW, require_root=False, trusted_audit_uid=os.geteuid(), lock_paths=self.locks)
        ack = recovery.acknowledge_recovered_operator(receiver_id=RECEIVER_ID,
            receiver_token=first['receiver_ack_token'], expected_record_sha256=first['new_record_sha256'],
            owner_file=self.owner, audit_dir=self.audit, now=NOW, require_root=False, trusted_audit_uid=os.geteuid(), lock_paths=self.locks)
        self.assertEqual(ack['status'], 'RECEIVER_ACKNOWLEDGED')
        self.assertEqual(stat.S_IMODE(Path(ack['receiver_binding_path']).stat().st_mode), 0o600)
        validated = recovery.validate_recovered_operator(self.owner, SCOPE, now=NOW, trusted_uid=os.geteuid())
        self.assertEqual(validated['owner'], recovery.RECEIVER)
        self.assertTrue(validated['receiver_acknowledged'])
        self.assertTrue(validated['release_activation_binding_required'])
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'changed before receiver acknowledgment'):
            recovery.acknowledge_recovered_operator(receiver_id=RECEIVER_ID,
                receiver_token=first['receiver_ack_token'], expected_record_sha256=first['new_record_sha256'],
                owner_file=self.owner, audit_dir=self.audit, now=NOW, require_root=False, trusted_audit_uid=os.geteuid(), lock_paths=self.locks)

    def test_old_execution_binding_is_invalidated_by_record_revision(self):
        first = call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertNotEqual(first['new_record_sha256'], self.digest)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'receiver acknowledgment'):
            recovery.validate_recovered_operator(self.owner, SCOPE, now=NOW, trusted_uid=os.geteuid())
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'owner record changed'):
            recovery.acknowledge_recovered_operator(receiver_id=RECEIVER_ID,
                receiver_token=first['receiver_ack_token'], expected_record_sha256=self.digest,
                owner_file=self.owner, audit_dir=self.audit, now=NOW, require_root=False, trusted_audit_uid=os.geteuid(), lock_paths=self.locks)

    def test_interrupted_transition_leaves_orphan_intent_and_fails_closed(self):
        def crash(*_args, **_kwargs): raise OSError('simulated crash before owner atomic replace')
        with patch.object(recovery, '_atomic_replace', crash):
            with self.assertRaisesRegex(OSError, 'simulated crash'):
                call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'interrupted recovery intent'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)

    def test_active_job_inventory_must_also_be_fenced(self):
        # Build an independent nested fixture directory.
        d = self.tmp / 'jobcase'; d.mkdir()
        owner, fence, locks, original, digest = setup_case(d, jobs=['job-1'])
        data = json.loads(fence.read_text()); data['jobs'] = {}; fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'every in-progress operator job'):
            call(owner, fence, locks, digest, d / 'audit')

    def test_preserved_workstream_requires_live_pid_start_ticks_and_supervisor_cgroup(self):
        data = json.loads(self.fence.read_text())
        data['workstreams']['workstream-A'] = {
            'disposition': 'preserved', 'evidence_sha256': 'b' * 64,
            'processes': [{'pid': 43210, 'start_ticks': 76543,
                          'supervisor_cgroup': '/system.slice/relay.service'}],
        }
        self.fence.write_text(json.dumps(data))
        with patch.object(recovery, '_proc_start_ticks', return_value=76543), \
             patch.object(recovery, '_proc_cgroup', return_value='0::/system.slice/relay.service\n'):
            result = call(self.owner, self.fence, self.locks, self.digest, self.audit)
        self.assertEqual(result['status'], 'ADMINISTRATOR_RECOVERY_COMMITTED')

    def test_owner_session_absence_must_be_positive_not_inferred(self):
        data = json.loads(self.fence.read_text()); data.pop('owner_session')
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'previous owner Chat/session'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit)

    def test_core_bound_work_cannot_be_marked_preserved_across_core_restart(self):
        data = json.loads(self.fence.read_text())
        data['workstreams']['workstream-A'] = {
            'disposition': 'preserved', 'evidence_sha256': 'b' * 64,
            'processes': [{'pid': 43210, 'start_ticks': 76543,
                           'supervisor_cgroup': '/system.slice/abit-ai.service'}],
        }
        self.fence.write_text(json.dumps(data))
        with patch.object(recovery, '_proc_start_ticks', return_value=76543), \
             patch.object(recovery, '_proc_cgroup', return_value='0::/system.slice/abit-ai.service\n'):
            with self.assertRaisesRegex(recovery.RecoveryDenied, 'Core-bound work'):
                call(self.owner, self.fence, self.locks, self.digest, self.audit)

    def test_existing_coordination_lock_contention_refuses_recovery(self):
        lock = self.locks[0]
        with lock.open('r+') as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(recovery.RecoveryDenied, 'lock is busy'):
                call(self.owner, self.fence, self.locks, self.digest, self.audit)
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)

    def test_production_root_boundary_is_mandatory(self):
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'root administrator boundary'):
            call(self.owner, self.fence, self.locks, self.digest, self.audit, require_root=True)


if __name__ == '__main__':
    unittest.main()

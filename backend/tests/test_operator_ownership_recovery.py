import fcntl
import hashlib
import json
import os
import stat
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util

MODULE = Path(__file__).resolve().parents[2] / 'deployment' / 'operator_ownership_recovery.py'
SPEC = importlib.util.spec_from_file_location('operator_ownership_recovery_under_test', MODULE)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)

NOW = 2_000_000_000.0
SCOPE = 'linkedin-core-seven-file-release:' + recovery.APPROVED_MANIFEST
RECEIVER_ID = 'registered-abit-user-principal-0001'
ADMIN = {'id': 'jimran@abitcons.com', 'uid': os.geteuid(), 'role': 'host_administrator', 'auth': 'sudo'}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_evidence(directory, name, body):
    path = directory / name
    raw = body.encode()
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return {'evidence_file': name, 'evidence_sha256': sha(raw)}


def setup_case(tmp_path, jobs=None):
    evidence_dir = tmp_path / 'fence-evidence'
    evidence_dir.mkdir(mode=0o700)
    os.chmod(evidence_dir, 0o700)
    owner_path = tmp_path / 'operator-session.json'
    owner = {
        'owner': recovery.EXPECTED_OWNER, 'state': recovery.OWNER_STATE,
        'phase': 'PNPM_FIRST_OPERATOR_CREATE_ONLY_INSTALLATION',
        'updated_at': iso(NOW - 10000), 'lease_expires_at': iso(NOW - 1000),
        'released_at': None, 'operator_jobs_in_progress': jobs or [],
        'active_workstreams': list(recovery.EXPECTED_WORKSTREAMS),
        'original_unknowns': {'lease': recovery.REQUIRED_PRESERVED_HOLDS['maintenance_lease'],
                              'dot_event': recovery.REQUIRED_PRESERVED_HOLDS['legacy_dot_event']},
        'history': [{'id': 'original-admission-1', 'state': 'unresolved'}],
    }
    raw = json.dumps(owner, sort_keys=True).encode()
    owner_path.write_bytes(raw)
    os.chmod(owner_path, 0o640)
    lock_paths = tuple(tmp_path / name for name in (
        'operator-session.json.lock', 'operator-session.json.claim.lock',
        '.operator-session-review.lock', 'operator-session.lock'))
    for path in lock_paths:
        path.touch(mode=0o660)

    owner_session = {'disposition': 'absent', 'absence_verified': True, 'processes': [],
                     **write_evidence(evidence_dir, 'owner-session.json', 'verified owner session absence')}
    workstreams = {}
    for index, name in enumerate(owner['active_workstreams']):
        workstreams[name] = {
            'disposition': 'fenced', 'inventory_complete': True,
            'processes': [{'pid': 99999999-index, 'start_ticks': 12345+index}],
            'provider_requests': [], 'browser_sessions': [],
            **write_evidence(evidence_dir, f'workstream-{index}.json', f'{name}: provider and browser inventory complete; no active external work'),
        }
    job_rows = {}
    for index, job_id in enumerate(owner['operator_jobs_in_progress']):
        job_rows[job_id] = {
            'disposition': 'fenced', 'inventory_complete': True,
            'processes': [{'pid': 99999990-index, 'start_ticks': 23456+index}],
            'provider_requests': [], 'browser_sessions': [],
            **write_evidence(evidence_dir, f'job-{index}.json', f'{job_id}: job inventory and external work checked'),
        }
    fence_path = tmp_path / 'operator-session-fence-receipt.json'
    receipt = {
        'schema_version': 2, 'status': 'FENCED_OR_PRESERVED', 'no_replay': True,
        'previous_owner': recovery.EXPECTED_OWNER, 'owner_record_sha256': sha(raw),
        'issued_at': iso(NOW - 100), 'owner_session': owner_session,
        'workstreams': workstreams, 'jobs': job_rows,
        'preserved_uncertain_work': recovery.REQUIRED_PRESERVED_HOLDS,
    }
    fence_path.write_text(json.dumps(receipt, sort_keys=True))
    os.chmod(fence_path, 0o600)
    return owner_path, fence_path, evidence_dir, lock_paths, owner, sha(raw)


def request(owner_sha, **overrides):
    value = {
        'expected_owner': recovery.EXPECTED_OWNER, 'expected_record_sha256': owner_sha,
        'receiving_operator': recovery.RECEIVER, 'receiving_principal_id': RECEIVER_ID,
        'reason': 'Recover the expired owner after independently verified host fencing.',
        'scope': SCOPE, 'execution_expires_at': iso(NOW + 1800),
        'recovery_id': str(uuid.uuid4()),
    }
    value.update(overrides)
    return value


class OperatorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp.name)
        os.chmod(self.tmp, 0o2770)
        self.owner, self.fence, self.evidence, self.locks, self.original, self.digest = setup_case(self.tmp)
        self.audit = self.tmp / 'audit'
        self.ack_spool = self.tmp / 'ack-spool'
        self.patches = [
            patch.object(recovery, 'OWNER_FILE', self.owner),
            patch.object(recovery, 'FENCE_FILE', self.fence),
            patch.object(recovery, 'FENCE_EVIDENCE_DIR', self.evidence),
            patch.object(recovery, 'AUDIT_DIR', self.audit),
            patch.object(recovery, 'ACK_SPOOL_DIR', self.ack_spool),
            patch.object(recovery, 'SERVICE_UID', os.geteuid()),
            patch.object(recovery, 'SERVICE_GID', os.getegid()),
            patch.object(recovery, '_receiver_ack_key', return_value=b'test-receiver-ack-signing-key-32-bytes'),
            patch.object(recovery, 'LOCK_FILES', self.locks),
            patch.object(recovery, '_admin_identity', return_value=ADMIN),
            patch.object(recovery, '_trusted_uid', return_value=os.geteuid()),
            patch.object(recovery.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.geteuid())),
            patch.object(recovery.time, 'time', return_value=NOW),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def call(self, owner_sha=None, **overrides):
        return recovery.recover_expired_operator(**request(owner_sha or self.digest, **overrides))

    def token(self, result):
        return Path(result['receiver_token_path']).read_text().strip()

    def receiver_receipt(self, result, token=None, expected_sha=None, user_id=RECEIVER_ID):
        return recovery.record_receiver_acknowledgment(
            authenticated_user=SimpleNamespace(id=user_id),
            receiver_token=token or self.token(result),
            expected_record_sha256=expected_sha or result['new_record_sha256'],
        )

    def ack(self, result, token=None, expected_sha=None, user_id=RECEIVER_ID):
        self.receiver_receipt(result, token=token, expected_sha=expected_sha, user_id=user_id)
        return recovery.apply_receiver_acknowledgment(result['recovery_id'])

    def test_unauthorized_administrator_context_denied(self):
        with patch.object(recovery, '_admin_identity', side_effect=recovery.RecoveryDenied('not sudo')):
            with self.assertRaisesRegex(recovery.RecoveryDenied, 'not sudo'):
                self.call()
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)

    def test_actual_sudo_identity_must_match_os_account_and_parent(self):
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'sudo principal'):
            recovery._validate_sudo_identity(1000, 'other-user', 'other-user', '/usr/bin/sudo', 0, 'abit-dev-linux')
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'sudo principal'):
            recovery._validate_sudo_identity(1000, 'jimran@abitcons.com', 'other-user', '/usr/bin/sudo', 0, 'abit-dev-linux')
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'directly through sudo'):
            recovery._validate_sudo_identity(1000, 'jimran@abitcons.com', 'jimran@abitcons.com', '/bin/bash', 0, 'abit-dev-linux')
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'root administrator invocation'):
            recovery._validate_sudo_identity(1000, 'jimran@abitcons.com', 'jimran@abitcons.com', '/usr/bin/sudo', 1000, 'abit-dev-linux')

    def test_expiry_without_positive_fence_receipt_is_refused(self):
        self.fence.unlink()
        with self.assertRaises(FileNotFoundError):
            self.call()
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)

    def test_concurrent_renewal_or_record_drift_wins_cas(self):
        renewed = dict(self.original)
        renewed['lease_expires_at'] = iso(NOW + 60)
        self.owner.write_text(json.dumps(renewed, sort_keys=True))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'hash changed'):
            self.call()
        self.assertEqual(json.loads(self.owner.read_text())['owner'], recovery.EXPECTED_OWNER)

    def test_missing_workstream_fence_and_missing_hold_preservation_fail(self):
        data = json.loads(self.fence.read_text())
        data['workstreams'] = {}
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'every active workstream'):
            self.call()
        data['workstreams'] = json.loads(setup_case.__name__ and self.fence.read_text())['workstreams'] if False else data['workstreams']
        # Restore a valid row set from a fresh isolated fixture.
        fresh = self.tmp / 'fresh'; fresh.mkdir()
        owner, fence, evidence, locks, original, digest = setup_case(fresh)
        data = json.loads(fence.read_text()); data['preserved_uncertain_work'] = {}
        fence.write_text(json.dumps(data))
        with patch.object(recovery, 'OWNER_FILE', owner), patch.object(recovery, 'FENCE_FILE', fence), \
             patch.object(recovery, 'FENCE_EVIDENCE_DIR', evidence), patch.object(recovery, 'AUDIT_DIR', fresh/'audit'), \
             patch.object(recovery, 'LOCK_FILES', locks):
            with self.assertRaisesRegex(recovery.RecoveryDenied, 'preserve both'):
                self.call(digest)

    def test_fence_evidence_is_verified_not_just_hash_syntax(self):
        data = json.loads(self.fence.read_text())
        data['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]['evidence_sha256'] = 'a' * 64
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'evidence file ownership, mode or hash'):
            self.call()

    def test_provider_and_browser_inventory_must_be_explicit_and_resolved(self):
        data = json.loads(self.fence.read_text())
        row = data['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]
        row.pop('browser_sessions')
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'explicitly inventory browser_sessions'):
            self.call()
        data = json.loads(setup_case(self.tmp/'second')[1].read_text()) if False else json.loads(self.fence.read_text())
        row = data['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]
        row['browser_sessions'] = []
        row['provider_requests'] = [{'id': 'provider-call-unknown', 'disposition': 'unknown',
                                     **write_evidence(self.evidence, 'provider-unknown.json', 'provider result unknown')}]
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'unknown/unfenced outcome'):
            self.call()

    def test_root_owned_fence_receipt_and_freshness_are_required(self):
        os.chmod(self.fence, 0o644)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'root-owned mode 0600'):
            self.call()
        os.chmod(self.fence, 0o600)
        data = json.loads(self.fence.read_text()); data['issued_at'] = iso(NOW - 900)
        self.fence.write_text(json.dumps(data))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'fresh'):
            self.call()

    def test_competing_recovery_attempts_are_single_winner_and_preserve_history(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        def worker():
            barrier.wait()
            try:
                results.append(self.call(recovery_id=str(uuid.uuid4())))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        current = json.loads(self.owner.read_text())
        self.assertEqual(current['owner'], recovery.RECEIVER)
        self.assertEqual(current['history'], self.original['history'])
        self.assertEqual(current['active_workstreams'], self.original['active_workstreams'])
        self.assertEqual(current['original_unknowns'], self.original['original_unknowns'])
        self.assertEqual(current['operator_recovery']['status'], 'COMMIT_PENDING_AUDIT')
        self.assertFalse(results[0]['maintenance_gate_mutated'])
        self.assertFalse(results[0]['agent_started'])

    def test_authenticated_receiver_only_and_one_use_challenge(self):
        result = self.call()
        token = self.token(result)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'not the enrolled receiving operator'):
            self.ack(result, token=token, user_id='other-abit-user')
        ack = self.ack(result, token=token)
        self.assertEqual(ack['status'], 'RECEIVER_ACKNOWLEDGED')
        self.assertEqual(stat.S_IMODE(Path(ack['receiver_binding_path']).stat().st_mode), 0o640)
        validated = recovery.validate_recovered_operator(recovery.RECEIVER, SCOPE)
        self.assertTrue(validated['receiver_acknowledged'])
        self.assertTrue(validated['release_activation_binding_required'])
        replay = self.ack(result, token=token)
        self.assertEqual(replay['status'], 'RECEIVER_ACK_APPLICATION_ALREADY_COMMITTED')
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'challenge was already consumed'):
            self.receiver_receipt(result, token='x' * 48)

    def test_previous_owner_cannot_validate_or_reuse_recovered_execution(self):
        result = self.call()
        self.ack(result)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'stale or non-recovered operator'):
            recovery.validate_recovered_operator(recovery.EXPECTED_OWNER, SCOPE)

    def test_already_recovered_owner_is_rejected(self):
        result = self.call()
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'expected unreleased active owner record'):
            self.call(owner_sha=result['new_record_sha256'])

    def test_interrupted_before_owner_replace_leaves_orphan_and_blocks_retry(self):
        def crash(*_args, **_kwargs):
            raise OSError('simulated crash before owner atomic replace')
        with patch.object(recovery, '_atomic_replace', crash):
            with self.assertRaisesRegex(OSError, 'simulated crash'):
                self.call()
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'interrupted recovery artifact'):
            self.call()

    def test_crash_after_owner_replace_requires_and_supports_commit_marker_reconciliation(self):
        original_write = recovery._write_exclusive
        def crash_on_commit(path, data, *args, **kwargs):
            if Path(path).name.endswith('.committed.json'):
                raise OSError('simulated crash after owner replacement')
            return original_write(path, data, *args, **kwargs)
        with patch.object(recovery, '_write_exclusive', crash_on_commit):
            with self.assertRaisesRegex(OSError, 'after owner replacement'):
                self.call()
        pending_raw = self.owner.read_bytes()
        pending_sha = sha(pending_raw)
        self.assertEqual(json.loads(pending_raw)['operator_recovery']['status'], 'COMMIT_PENDING_AUDIT')
        reconciled = recovery.reconcile_commit(pending_sha)
        self.assertEqual(reconciled['status'], 'COMMIT_MARKER_RECONCILED')
        result = {'new_record_sha256': pending_sha, 'recovery_id': json.loads(pending_raw)['operator_recovery']['recovery_id'],
                  'receiver_token_path': str(self.audit / (json.loads(pending_raw)['operator_recovery']['recovery_id'] + '.receiver-token'))}
        self.ack(result)

    def test_receiver_ack_crash_after_binding_is_idempotently_recoverable(self):
        result = self.call()
        token = self.token(result)
        self.receiver_receipt(result, token=token)
        def crash(*_args, **_kwargs):
            raise OSError('simulated crash after receiver binding')
        with patch.object(recovery, '_atomic_replace', crash):
            with self.assertRaisesRegex(OSError, 'after receiver binding'):
                recovery.apply_receiver_acknowledgment(result['recovery_id'])
        self.assertEqual(json.loads(self.owner.read_text())['operator_recovery']['status'], 'COMMIT_PENDING_AUDIT')
        ack = recovery.apply_receiver_acknowledgment(result['recovery_id'])
        self.assertEqual(ack['status'], 'RECEIVER_ACKNOWLEDGED')

    def test_process_pid_reuse_is_distinguished_from_unreadable_proc(self):
        with patch.object(recovery, '_proc_start_ticks', return_value=987654):
            self.assertIsNone(recovery._validate_processes([{'pid': 43210, 'start_ticks': 12345}], 'fenced'))
        with patch.object(recovery, '_proc_start_ticks', side_effect=recovery.RecoveryDenied('permission denied')):
            with self.assertRaisesRegex(recovery.RecoveryDenied, 'permission denied'):
                recovery._validate_processes([{'pid': 43210, 'start_ticks': 12345}], 'fenced')

    def test_preserved_provider_work_is_recorded_not_replayed(self):
        data = json.loads(self.fence.read_text())
        row = data['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]
        row['provider_requests'] = [{'id': 'provider-unknown-01', 'disposition': 'preserved',
                                     **write_evidence(self.evidence, 'provider-unknown-01.json', 'unresolved provider result; preserve and never replay')}]
        self.fence.write_text(json.dumps(data))
        result = self.call()
        self.assertEqual(result['status'], 'ADMINISTRATOR_RECOVERY_COMMITTED_AWAITING_RECEIVER_ACK')
        intent = json.loads((self.audit / (result['recovery_id'] + '.intent.json')).read_text())
        self.assertEqual(intent['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]['provider_requests'][0]['disposition'], 'preserved')
        self.assertTrue(intent['no_replay'])

    def test_generation_tampering_invalidates_receiver_binding(self):
        result = self.call()
        self.ack(result)
        owner = json.loads(self.owner.read_text())
        owner['operator_recovery']['generation'] = 2
        self.owner.write_text(json.dumps(owner, sort_keys=True))
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'recovered operator scope or manifest mismatch'):
            recovery.validate_recovered_operator(recovery.RECEIVER, SCOPE)

    def test_partial_audit_write_failure_leaves_owner_unchanged_and_blocks_retry(self):
        original_write = recovery._write_exclusive
        def fail_intent(path, data, *args, **kwargs):
            if Path(path).name.endswith('.intent.json'):
                raise OSError('simulated partial intent write failure')
            return original_write(path, data, *args, **kwargs)
        with patch.object(recovery, '_write_exclusive', fail_intent):
            with self.assertRaisesRegex(OSError, 'partial intent'):
                self.call()
        self.assertEqual(sha(self.owner.read_bytes()), self.digest)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'interrupted recovery artifact'):
            self.call()

    def test_forged_receiver_ack_receipt_is_rejected(self):
        result = self.call()
        receipt = self.receiver_receipt(result)
        path = Path(receipt['receipt_path'])
        forged = json.loads(path.read_text())
        forged['receiver_id'] = 'attacker-user'
        path.write_text(json.dumps(forged, sort_keys=True))
        os.chmod(path, 0o600)
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'signature is invalid'):
            recovery.apply_receiver_acknowledgment(result['recovery_id'])
        self.assertEqual(json.loads(self.owner.read_text())['operator_recovery']['status'], 'COMMIT_PENDING_AUDIT')

    def test_symlinked_fence_evidence_is_rejected(self):
        data = json.loads(self.fence.read_text())
        row = data['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]
        target = self.evidence / 'target-evidence.json'
        target.write_text('evidence'); os.chmod(target, 0o600)
        link = self.evidence / 'linked-evidence.json'
        link.symlink_to(target)
        row['evidence_file'] = link.name
        row['evidence_sha256'] = sha(target.read_bytes())
        self.fence.write_text(json.dumps(data))
        with self.assertRaises(OSError):
            self.call()

    def test_preserved_browser_session_is_bound_and_recorded(self):
        data = json.loads(self.fence.read_text())
        row = data['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]
        row['browser_sessions'] = [{'id': 'browser-session-unknown-01', 'disposition': 'preserved',
                                    **write_evidence(self.evidence, 'browser-session-unknown-01.json', 'browser state retained; no replay')}]
        self.fence.write_text(json.dumps(data))
        result = self.call()
        intent = json.loads((self.audit / (result['recovery_id'] + '.intent.json')).read_text())
        self.assertEqual(intent['workstreams'][recovery.EXPECTED_WORKSTREAMS[0]]['browser_sessions'][0]['disposition'], 'preserved')

    def test_recovered_owner_waits_for_authenticated_receiver_before_any_execution(self):
        result = self.call()
        with self.assertRaisesRegex(recovery.RecoveryDenied, 'receiver acknowledgment'):
            recovery.validate_recovered_operator(recovery.RECEIVER, SCOPE)
        current = json.loads(self.owner.read_text())
        self.assertEqual(current['owner'], recovery.RECEIVER)
        self.assertEqual(current['operator_recovery']['status'], 'COMMIT_PENDING_AUDIT')
        self.assertTrue(current['operator_recovery']['requires_fresh_receiver_acknowledgment'])
        self.assertFalse(result['agent_started'])


    def test_api_receiver_route_uses_authenticated_user_not_caller_receiver_id(self):
        import ast
        route_path = Path(__file__).resolve().parents[1] / 'open_webui/routers/agentic_governance.py'
        tree = ast.parse(route_path.read_text())
        route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == 'acknowledge_expired_operator_route')
        source = ast.get_source_segment(route_path.read_text(), route)
        self.assertIn('Depends(get_current_user)', route_path.read_text())
        self.assertIn('authenticated_user=user', source)
        self.assertNotIn('receiver_id=', source)
        model = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == 'ExpiredOperatorAcknowledgmentInput')
        fields = {node.target.id for node in model.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
        self.assertEqual(fields, {'expected_record_sha256', 'receiver_token'})

    def test_backend_import_module_matches_protected_cli_module(self):
        backend_module = Path(__file__).resolve().parents[1] / 'open_webui/utils/operator_ownership_recovery.py'
        self.assertEqual(sha(backend_module.read_bytes()), sha(MODULE.read_bytes()))

    def test_recovery_never_changes_maintenance_gate_or_starts_agent(self):
        result = self.call()
        self.assertFalse(result['maintenance_gate_mutated'])
        self.assertFalse(result['admissions_mutated'])
        self.assertFalse(result['provider_outcomes_reconciled'])
        self.assertFalse(result['agent_started'])
        self.assertEqual(result['requires_fresh_linkedin_activation_binding'], True)


if __name__ == '__main__':
    unittest.main()

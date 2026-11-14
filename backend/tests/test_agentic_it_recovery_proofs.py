"""Actual protected source/backup/restored bytes and MACs; no host resource mutation."""

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_agentic_it_changes import changes
from test_agentic_it_signature import signature
import test_agentic_tool_gateway as gateway_tests

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'
spec = importlib.util.spec_from_file_location('recovery_proof_fixture', ROOT / 'agentic_it_recovery_proofs.py')
recovery = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'recovery_proof_fixture': recovery, 'open_webui.utils.agentic_it_changes': changes,
                             'open_webui.utils.agentic_it_signature': signature,
                             'open_webui.utils.agentic_tool_gateway': gateway_tests.module}):
    spec.loader.exec_module(recovery)


class RecoveryEvidence(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.subject = self.root / 'owned-subject'
        self.subject.write_bytes(b'actual synthetic resource baseline bytes\n')
        self.proofs = self.root / 'proofs'
        self.proofs.mkdir(mode=0o700)
        key = self.proofs / 'receipt-key'
        key.write_bytes(secrets.token_bytes(32))
        key.chmod(0o600)
        self.store = recovery.RecoveryProofStore(self.proofs, key)
        self.scope = {'project_id': 'PROJ-fixture', 'repository': 'https://github.com/ABiT-Consulting/abit-ai-platform', 'environment': 'test'}
        self.record = {**self.scope, 'resource_id': 'owned-resource', 'operation': 'restart_service',
                       'exclusive_to_project': True, 'capacity_path': str(self.root),
                       'recovery_probe_policy': 'actual-byte-parse-fixture'}
        self.profile = {'host_id': socket.gethostname(), 'expected_uid': os.geteuid(),
                        'recovery_subject_roots': [str(self.root)], 'capacity_roots': [str(self.root)]}
        self.probe = recovery.FunctionalRestoreProbe('actual-byte-parse-fixture', lambda path, *args:
            path.read_bytes() == b'actual synthetic resource baseline bytes\n')
        self.impact = {'scope': self.scope, 'resource_id': 'owned-resource', 'operation': 'restart_service',
                       'purpose': 'Only the disposable owned test resource changes'}

    async def mint(self):
        proof = await self.store.mint('owned-proof', self.record, self.scope, self.profile, self.subject, self.probe,
            expires_at=int(time.time()) + 600, retention_until=int(time.time()) + 86400, impact=self.impact)
        self.record.update(recovery_proof_id=proof['proof_id'], recovery_receipt_sha256=proof['receipt_sha256'])
        return proof

    async def test_actual_distinct_backup_restore_bytes_and_fresh_capacity_are_verified(self):
        proof = await self.mint()
        safety = await self.store.safety(self.record, self.scope, self.profile)
        self.assertTrue(safety['backup']['verified'])
        self.assertTrue(safety['restore']['verified'])
        self.assertEqual(proof['backup_sha256'], safety['backup']['sha256'])
        self.assertEqual(proof['restore_sha256'], safety['restore']['sha256'])
        folder = self.proofs / 'owned-proof'
        self.assertNotEqual((folder / 'backup.bin').stat().st_ino, (folder / 'restored.bin').stat().st_ino)
        self.assertEqual(self.subject.read_bytes(), (folder / 'restored.bin').read_bytes())
        self.assertGreater(safety['capacity']['free_bytes'], 0)
        self.assertEqual(os.geteuid(), safety['privilege']['effective_uid'])

    async def test_tampered_backup_or_current_subject_is_denied(self):
        await self.mint()
        self.subject.write_bytes(b'concurrent resource baseline changed')
        with self.assertRaisesRegex(ValueError, 'baseline changed'):
            await self.store.safety(self.record, self.scope, self.profile)
        self.subject.write_bytes(b'actual synthetic resource baseline bytes\n')
        (self.proofs / 'owned-proof/backup.bin').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'backup or restored bytes changed'):
            await self.store.safety(self.record, self.scope, self.profile)

    async def test_forged_receipt_boolean_with_matching_outer_hash_cannot_replace_MAC(self):
        await self.mint()
        file = self.proofs / 'owned-proof/receipt.json'
        body = json.loads(file.read_bytes())
        body['body']['verified'] = True
        body['mac'] = '0' * 64
        encoded = json.dumps(body).encode()
        file.write_bytes(encoded)
        self.record['recovery_receipt_sha256'] = hashlib.sha256(encoded).hexdigest()
        with self.assertRaisesRegex(ValueError, 'authentication failed'):
            await self.store.safety(self.record, self.scope, self.profile)

    async def test_other_project_and_modified_retention_cannot_reuse_proof(self):
        await self.mint()
        other = {**self.scope, 'project_id': 'OTHER'}
        with self.assertRaisesRegex(ValueError, 'scope or lifetime'):
            await self.store.safety(self.record, other, self.profile)
        (self.proofs / 'owned-proof/retention.json').write_bytes(b'{"retain_until":1}')
        with self.assertRaisesRegex(ValueError, 'retention or reviewed impact'):
            await self.store.safety(self.record, self.scope, self.profile)

    async def test_failed_or_serialized_functional_probe_never_mints_success(self):
        with self.assertRaisesRegex(ValueError, 'server functional probe'):
            await self.store.mint('untrusted', self.record, self.scope, self.profile, self.subject, {'verified': True},
                expires_at=int(time.time()) + 60, retention_until=int(time.time()) + 600, impact=self.impact)
        failed = recovery.FunctionalRestoreProbe('actual-failing-probe', lambda path, *args: False)
        self.record['recovery_probe_policy'] = failed.policy_id
        with self.assertRaisesRegex(ValueError, 'functional recovery probe'):
            await self.store.mint('failed', self.record, self.scope, self.profile, self.subject, failed,
                expires_at=int(time.time()) + 60, retention_until=int(time.time()) + 600, impact=self.impact)
        self.assertFalse((self.proofs / 'failed/receipt.json').exists())

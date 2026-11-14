"""Actual temporary OpenPGP signatures/index hashes; never downloads or installs."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_agentic_it_changes import changes

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'
spec = importlib.util.spec_from_file_location('archive_signature_fixture', ROOT / 'agentic_it_signature.py')
signature = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'open_webui.utils.agentic_it_changes': changes}):
    spec.loader.exec_module(signature)


class ArchiveSignature(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.temp.name) / 'fixture-key-home'
        cls.home.mkdir(mode=0o700)
        cls.command(['--quick-generate-key', 'Synthetic IT test <fixture@example.invalid>', 'ed25519', 'sign', '1d'])
        text = cls.command(['--with-colons', '--list-keys']).decode()
        cls.fingerprint = next(line.split(':')[9] for line in text.splitlines() if line.startswith('fpr:'))
        cls.key_bytes = cls.command(['--export', cls.fingerprint])

    @classmethod
    def command(cls, args):
        result = subprocess.run(['/usr/bin/gpg', '--no-options', '--homedir', str(cls.home), '--batch',
                                 '--pinentry-mode', 'loopback', '--passphrase', '', *args],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
        if result.returncode:
            raise RuntimeError('Synthetic GPG fixture command failed')
        return result.stdout

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['/usr/bin/gpgconf', '--homedir', str(cls.home), '--kill', 'gpg-agent'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'synthetic.deb'
        self.package.write_bytes(b'Synthetic fixture bytes, not an installable production package')
        self.artifact = {'artifact_id': 'fixture-package', 'sha256': hashlib.sha256(self.package.read_bytes()).hexdigest(),
                         'size': self.package.stat().st_size}
        self.record = {'package': 'fixture-package', 'version': '1.2-3', 'architecture': 'amd64',
                       'artifact_sha256': self.artifact['sha256'], 'official_source_id': 'fixture-archive',
                       'archive_filename': 'pool/main/f/fixture/fixture-package_1.2-3_amd64.deb'}
        self.keyring = self.root / 'archive.gpg'
        self.keyring.write_bytes(self.key_bytes)
        self.index = self.root / 'Packages.gz'
        self.catalog = self.root / 'InRelease'
        self.source = {'catalog_path': str(self.catalog), 'index_path': str(self.index),
            'index_relative_path': 'main/binary-amd64/Packages.gz', 'keyring_path': str(self.keyring),
            'signer_fingerprint': self.fingerprint, 'release_origin': 'SyntheticFixture', 'codename': 'fixture',
            'catalog_expires_at': int(time.time()) + 3600}
        self.profile = {'expected_uid': os.geteuid(), 'package_cache_roots': [str(self.root)],
                        'keyring_roots': [str(self.root)], 'keyring_uid': os.geteuid()}
        self.paragraph = '\n'.join(['Package: fixture-package', 'Version: 1.2-3', 'Architecture: amd64',
            'Filename: ' + self.record['archive_filename'], 'Size: ' + str(self.artifact['size']),
            'SHA256: ' + self.artifact['sha256']]) + '\n'
        self.sign_index(self.paragraph)
        self.verifier = signature.DebianArchiveVerifier()

    def sign_index(self, text, expired=False):
        self.index.write_bytes(gzip.compress(text.encode(), mtime=0))
        now = datetime.now(timezone.utc)
        date = format_datetime(now - timedelta(minutes=1), usegmt=True)
        expiry = format_datetime(now + timedelta(hours=-1 if expired else 1), usegmt=True)
        release = self.root / 'Release'
        release.write_text('Origin: SyntheticFixture\nCodename: fixture\nDate: ' + date + '\nValid-Until: ' + expiry
            + '\nSHA256:\n ' + hashlib.sha256(self.index.read_bytes()).hexdigest() + ' ' + str(self.index.stat().st_size)
            + ' ' + self.source['index_relative_path'] + '\n')
        self.command(['--yes', '--local-user', self.fingerprint, '--output', str(self.catalog), '--clearsign', str(release)])
        self.source['catalog_sha256'] = hashlib.sha256(self.catalog.read_bytes()).hexdigest()

    async def verify(self):
        return await self.verifier.verify(self.record, self.source, self.artifact, self.profile)

    async def test_actual_gpgv_authenticates_complete_exact_package_chain(self):
        proof = await self.verify()
        self.assertTrue(proof['verified'])
        self.assertEqual(self.fingerprint, proof['signer_fingerprint'])
        self.assertEqual(self.artifact['sha256'], proof['artifact_sha256'])
        self.assertGreater(proof['valid_until'], int(time.time()))

    async def test_tampered_index_cannot_reuse_valid_release_signature(self):
        self.index.write_bytes(self.index.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'not authenticated'):
            await self.verify()

    async def test_wrong_signer_or_wrong_artifact_bytes_are_rejected(self):
        self.source['signer_fingerprint'] = 'F' * 40
        with self.assertRaisesRegex(ValueError, 'signer fingerprint differs'):
            await self.verify()
        self.source['signer_fingerprint'] = self.fingerprint
        self.artifact['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'package bytes'):
            await self.verify()

    async def test_expired_signed_metadata_denied_even_with_future_operator_expiry(self):
        self.sign_index(self.paragraph, expired=True)
        with self.assertRaisesRegex(ValueError, 'metadata expired'):
            await self.verify()

    async def test_ambiguous_authenticated_package_record_denied(self):
        self.sign_index(self.paragraph + '\n' + self.paragraph)
        with self.assertRaisesRegex(ValueError, 'appear once'):
            await self.verify()

    async def test_group_writable_signature_input_or_changed_catalog_denied(self):
        self.catalog.chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'protected bounded regular'):
            await self.verify()
        self.catalog.chmod(0o600)
        self.catalog.write_bytes(self.catalog.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'catalog hash differs'):
            await self.verify()

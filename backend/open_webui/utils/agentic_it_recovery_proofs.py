"""Protected, authenticated actual byte-restore evidence for fixed IT resources.

Only reviewed server setup code can mint a proof, after copying and independently
checking restored bytes and running its typed functional probe. JSON cannot mint
an assertion. Safety reads verify the receipt MAC, current files and expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import time

from open_webui.utils.agentic_it_changes import canonical, digest, _read_protected
from open_webui.utils.agentic_it_signature import protected_bytes
from open_webui.utils.agentic_tool_gateway import _open_dir

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}')


def require(value, message):
    if not value:
        raise ValueError(message)


@dataclass(frozen=True)
class FunctionalRestoreProbe:
    policy_id: str
    verify: object

    def __post_init__(self):
        require(isinstance(self.policy_id, str) and ID.fullmatch(self.policy_id) and callable(self.verify),
                'Typed server recovery probe required')


class RecoveryProofStore:
    def __init__(self, root, key_file):
        self.root, self.key_file = Path(root), Path(key_file)
        require(self.root.resolve() == self.root and self.root.is_dir()
                and self.root.stat().st_uid == os.geteuid() and stat.S_IMODE(self.root.stat().st_mode) == 0o700,
                'Recovery proof store must be an existing private operator directory')
        key, meta = protected_bytes(str(self.key_file), [str(self.root)], os.geteuid(), 128)
        require(32 <= len(key) <= 64 and stat.S_IMODE(self.key_file.stat().st_mode) == 0o600,
                'Private recovery authentication key required')
        self._key = key

    def _mac(self, body):
        return hmac.new(self._key, b'abit-it-recovery-v1\0' + canonical(body).encode(), hashlib.sha256).hexdigest()

    def _resource_digest(self, record):
        return digest({key: value for key, value in record.items()
                       if key not in {'recovery_proof_id', 'recovery_receipt_sha256'}})

    def _write(self, path, content):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _copy(self, source, target, roots, uid):
        identity = _read_protected(str(source), roots, uid, 2_000_000_000)
        read = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(read)
            require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    == tuple(identity[key] for key in ('device', 'inode', 'size', 'mtime_ns')), 'Recovery source changed')
            value = hashlib.sha256()
            write = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(write, 'wb') as stream:
                while chunk := os.read(read, 1_048_576):
                    value.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            require(value.hexdigest() == identity['sha256'], 'Recovery copy source changed')
        finally:
            os.close(read)
        require(_read_protected(str(target), [str(target.parent)], os.geteuid(), 2_000_000_000)['sha256'] == identity['sha256'],
                'Actual recovery copy readback differs')
        return identity

    async def mint(self, proof_id, record, scope, profile, subject, probe, *, expires_at, retention_until, impact):
        require(isinstance(proof_id, str) and len(proof_id) <= 80 and ID.fullmatch(proof_id) and isinstance(probe, FunctionalRestoreProbe),
                'Exact recovery ID and server functional probe required')
        now = int(time.time())
        require(type(expires_at) is int and now < expires_at <= now + 86400
                and type(retention_until) is int and retention_until >= expires_at, 'Bounded recovery proof lifetime required')
        expected = {key: scope[key] for key in ('project_id', 'repository', 'environment')}
        require(profile['host_id'] == socket.gethostname(), 'Recovery host identity differs')
        require(all(record.get(key) == value for key, value in expected.items()) and record.get('exclusive_to_project') is True,
                'Recovery resource is not exclusive to this exact project')
        require(record.get('recovery_probe_policy') == probe.policy_id, 'Exact reviewed functional probe policy required')
        require(isinstance(impact, dict) and impact.get('scope') == expected
                and impact.get('resource_id') == record['resource_id'] and impact.get('operation') == record['operation'],
                'Exact server-reviewed resource impact required')
        folder = self.root / proof_id
        folder.mkdir(mode=0o700)
        # Actual protected source -> backup -> distinct restored bytes. These
        # are private snapshots, never targets reachable through cleanup APIs.
        backup, restored = folder / 'backup.bin', folder / 'restored.bin'
        original = self._copy(subject, backup, profile['recovery_subject_roots'], profile['expected_uid'])
        self._copy(backup, restored, [str(folder)], os.geteuid())
        recovered_meta = _read_protected(str(restored), [str(folder)], os.geteuid(), 2_000_000_000)
        require(recovered_meta['sha256'] == original['sha256']
                and backup.stat().st_ino != restored.stat().st_ino, 'Independent physical byte restore did not verify')
        result = probe.verify(restored, record, scope, profile)
        if inspect.isawaitable(result):
            result = await result
        require(result is True, 'Actual typed functional recovery probe did not pass')
        policy = {'schema_version': 1, 'proof_id': proof_id, 'retain_until': retention_until,
                  'resource_id': record['resource_id'], 'classification': 'protected_it_recovery'}
        self._write(folder / 'retention.json', canonical(policy).encode())
        self._write(folder / 'impact.json', canonical(impact).encode())
        body = {'schema_version': 1, 'proof_id': proof_id, 'scope': expected, 'host_id': profile['host_id'],
                'resource_id': record['resource_id'], 'operation': record['operation'],
                'resource_policy_sha256': self._resource_digest(record),
                'subject_path': str(subject), 'subject_sha256': original['sha256'],
                'backup_sha256': original['sha256'], 'restore_sha256': recovered_meta['sha256'],
                'functional_probe_policy': probe.policy_id, 'functional_probe_passed_at': int(time.time()),
                'expires_at': expires_at, 'retention_sha256': digest(policy), 'impact_sha256': digest(impact)}
        encoded = canonical({'body': body, 'mac': self._mac(body)}).encode()
        self._write(folder / 'receipt.json', encoded)
        return {'proof_id': proof_id, 'receipt_sha256': hashlib.sha256(encoded).hexdigest(),
                'backup_sha256': original['sha256'], 'restore_sha256': recovered_meta['sha256']}

    async def safety(self, record, scope, profile):
        proof_id = record.get('recovery_proof_id')
        require(isinstance(proof_id, str) and len(proof_id) <= 80 and ID.fullmatch(proof_id), 'Exact protected recovery proof ID required')
        folder = self.root / proof_id
        require(folder.resolve() == folder and folder.stat().st_uid == os.geteuid()
                and stat.S_IMODE(folder.stat().st_mode) == 0o700, 'Recovery proof directory identity changed')
        raw, _ = protected_bytes(str(folder / 'receipt.json'), [str(folder)], os.geteuid(), 64000)
        require(hashlib.sha256(raw).hexdigest() == record.get('recovery_receipt_sha256'), 'Pinned recovery receipt changed')
        wrapper = json.loads(raw)
        body = wrapper['body']
        require(hmac.compare_digest(str(wrapper['mac']), self._mac(body)), 'Recovery receipt authentication failed')
        expected = {key: scope[key] for key in ('project_id', 'repository', 'environment')}
        now = int(time.time())
        require(body['scope'] == expected and body['host_id'] == profile['host_id'] == socket.gethostname() and body['proof_id'] == proof_id
                and body['resource_id'] == record['resource_id'] and body['operation'] == record['operation']
                and body['resource_policy_sha256'] == self._resource_digest(record)
                and body['functional_probe_policy'] == record.get('recovery_probe_policy')
                and type(body['expires_at']) is int and now < body['expires_at']
                and type(body['functional_probe_passed_at']) is int and body['functional_probe_passed_at'] <= now,
                'Recovery proof scope or lifetime differs')
        for name, field in [('backup.bin', 'backup_sha256'), ('restored.bin', 'restore_sha256')]:
            require(_read_protected(str(folder / name), [str(folder)], os.geteuid(), 2_000_000_000)['sha256'] == body[field],
                    'Actual backup or restored bytes changed')
        require(body['backup_sha256'] == body['restore_sha256'] == body['subject_sha256'], 'Backup/restore identity differs')
        # The exact subject has to remain the backed-up baseline until admission.
        require(_read_protected(body['subject_path'], profile['recovery_subject_roots'], profile['expected_uid'],
                                2_000_000_000)['sha256'] == body['subject_sha256'], 'Backed-up resource baseline changed')
        retention, _ = protected_bytes(str(folder / 'retention.json'), [str(folder)], os.geteuid(), 16384)
        impact, _ = protected_bytes(str(folder / 'impact.json'), [str(folder)], os.geteuid(), 16384)
        require(hashlib.sha256(retention).hexdigest() == body['retention_sha256']
                and hashlib.sha256(impact).hexdigest() == body['impact_sha256'], 'Actual retention or reviewed impact policy changed')
        policy = json.loads(retention)
        require(policy['resource_id'] == record['resource_id'] and policy['retain_until'] >= body['expires_at'] > now,
                'Actual backup retention policy expired or changed')
        capacity_path = record['capacity_path']
        require(isinstance(capacity_path, str) and any(Path(capacity_path).is_relative_to(Path(root))
            for root in profile.get('capacity_roots', [])), 'Capacity path outside registered roots')
        fd = _open_dir(capacity_path)
        try:
            filesystem, capacity = os.fstat(fd), os.fstatvfs(fd)
        finally:
            os.close(fd)
        return {'host_id': profile['host_id'], 'checked_at': now, 'project_id': scope['project_id'],
                'environment': scope['environment'],
                'backup': {'verified': True, 'reference': proof_id + '-backup', 'sha256': body['backup_sha256'], 'expires_at': body['expires_at']},
                'restore': {'verified': True, 'reference': proof_id + '-restore', 'sha256': body['restore_sha256'], 'expires_at': body['expires_at']},
                'retention': {'verified': True, 'policy_sha256': body['retention_sha256'], 'expires_at': body['expires_at']},
                'capacity': {'filesystem_id': str(filesystem.st_dev), 'free_bytes': capacity.f_bavail * capacity.f_frsize,
                             'total_bytes': capacity.f_blocks * capacity.f_frsize},
                'impact': {'reviewed': True, 'exclusive_to_project': True, 'sha256': body['impact_sha256']},
                'privilege': {'authorized': os.geteuid() == profile['expected_uid'], 'effective_uid': os.geteuid()}}

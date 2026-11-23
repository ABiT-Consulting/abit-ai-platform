"""Fail-closed, audited recovery of an expired orchestration owner.

This module is source-only until separately approved and installed. It never
opens or mutates the maintenance-admission database and never starts work.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OWNER_FILE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
FENCE_FILE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session-fence-receipt.json')
AUDIT_DIR = OWNER_FILE.parent / 'operator-session-recovery-audit'
LOCK_FILES = (
    OWNER_FILE.with_name('operator-session.json.lock'),
    OWNER_FILE.with_name('operator-session.json.claim.lock'),
    OWNER_FILE.with_name('.operator-session-review.lock'),
    OWNER_FILE.with_name('operator-session.lock'),
)
EXPECTED_OWNER = 'chat-authorized-root-orchestration-recovery-20261122T1007'
RECEIVER = 'chat-linkedin-admission-recovery-8c1bc230-20261123'
APPROVED_MANIFEST = '955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250'
REQUIRED_PRESERVED_HOLDS = {
    'maintenance_lease': '210450ddbef3399ebc0259a970eb28b3a2aa035a30fa29b2',
    'legacy_dot_event': 'b73e264f-43ba-4596-a6b6-ca97bf8e33a1',
}
OWNER_STATE = 'ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY'
HEX_RE = re.compile(r'^[a-f0-9]{64}$')


class RecoveryDenied(RuntimeError):
    """A fail-closed ownership recovery rejection."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_regular(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_bytes:
            raise RecoveryDenied(f'unsafe or oversized file: {path.name}')
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            part = os.read(fd, min(65536, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        data = b''.join(chunks)
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) or len(data) != info.st_size:
            raise RecoveryDenied(f'file changed during read: {path.name}')
        return data, info
    finally:
        os.close(fd)


def _proc_start_ticks(pid: int) -> int | None:
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        tail = raw[raw.rfind(')') + 2:].split()
        return int(tail[19])  # proc stat field 22 (starttime), after pid/comm
    except (OSError, ValueError, IndexError):
        return None


def _proc_cgroup(pid: int) -> str | None:
    try:
        return Path(f'/proc/{pid}/cgroup').read_text()
    except OSError:
        return None


def _validate_processes(row: dict, disposition: str) -> None:
    processes = row.get('processes')
    if not isinstance(processes, list) or not processes:
        raise RecoveryDenied('each workstream must identify its affected process set')
    seen: set[tuple[int, int]] = set()
    for proc in processes:
        if not isinstance(proc, dict):
            raise RecoveryDenied('invalid process evidence')
        pid, ticks = proc.get('pid'), proc.get('start_ticks')
        if type(pid) is not int or pid <= 1 or type(ticks) is not int or ticks <= 0 or (pid, ticks) in seen:
            raise RecoveryDenied('invalid or duplicate PID/start-tick identity')
        seen.add((pid, ticks))
        live_ticks = _proc_start_ticks(pid)
        if disposition == 'fenced':
            if live_ticks == ticks:
                raise RecoveryDenied(f'fenced process is still live: pid {pid}')
        elif disposition == 'preserved':
            if live_ticks != ticks:
                raise RecoveryDenied(f'preserved process identity is no longer live: pid {pid}')
            cgroup = _proc_cgroup(pid) or ''
            supervisor = proc.get('supervisor_cgroup')
            if not isinstance(supervisor, str) or not supervisor.startswith('/') or supervisor not in cgroup:
                raise RecoveryDenied(f'preserved process lacks verified supervisor cgroup: pid {pid}')
            if '/system.slice/abit-ai.service' in cgroup:
                raise RecoveryDenied('Core-bound work cannot be claimed preserved across the approved Core restart')
        else:
            raise RecoveryDenied('unsupported workstream disposition')


def _validate_fence_receipt(path: Path, owner: dict, owner_sha: str, now: float, trusted_uid: int) -> tuple[dict, str]:
    raw, info = _read_regular(path, max_bytes=512 * 1024)
    if info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise RecoveryDenied('fence receipt must be written by the trusted host administrator and not group/world writable')
    try:
        receipt = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RecoveryDenied('fence receipt is not valid JSON') from None
    if receipt.get('schema_version') != 1 or receipt.get('status') != 'FENCED_OR_PRESERVED':
        raise RecoveryDenied('trusted work-fence receipt is missing or incomplete')
    if receipt.get('owner_record_sha256') != owner_sha or receipt.get('previous_owner') != owner.get('owner'):
        raise RecoveryDenied('fence receipt is not bound to the exact current owner record')
    issued = receipt.get('issued_at')
    try:
        issued_at = datetime.fromisoformat(issued.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        raise RecoveryDenied('fence receipt timestamp is invalid') from None
    if issued_at > now or now - issued_at > 300 or issued_at <= _timestamp(owner['lease_expires_at']):
        raise RecoveryDenied('fence receipt must be fresh and issued after owner expiry')
    workstreams = owner.get('active_workstreams') or []
    jobs = owner.get('operator_jobs_in_progress') or []
    owner_session = receipt.get('owner_session')
    if not isinstance(owner_session, dict) or owner_session.get('disposition') not in ('absent', 'fenced'):
        raise RecoveryDenied('previous owner Chat/session requires explicit absence or fence evidence')
    if not isinstance(owner_session.get('evidence_sha256'), str) or not HEX_RE.fullmatch(owner_session['evidence_sha256']):
        raise RecoveryDenied('previous owner session evidence hash is required')
    session_processes = owner_session.get('processes')
    if not isinstance(session_processes, list):
        raise RecoveryDenied('previous owner session process inventory is required')
    if owner_session['disposition'] == 'absent':
        if session_processes or owner_session.get('absence_verified') is not True:
            raise RecoveryDenied('owner-session absence must be positively verified, not inferred from missing Chat metadata')
    else:
        if not session_processes:
            raise RecoveryDenied('fenced owner session must identify its original process identities')
        _validate_processes({'processes': session_processes}, 'fenced')
    rows = receipt.get('workstreams')
    if not isinstance(workstreams, list) or not all(isinstance(x, str) for x in workstreams):
        raise RecoveryDenied('owner workstream inventory is invalid')
    if not isinstance(rows, dict) or set(rows) != set(workstreams):
        raise RecoveryDenied('every active workstream needs an explicit fence/preservation receipt')
    for name, row in rows.items():
        if not isinstance(row, dict) or row.get('evidence_sha256') is None or not HEX_RE.fullmatch(str(row['evidence_sha256'])):
            raise RecoveryDenied(f'workstream evidence is incomplete: {name}')
        if row.get('disposition') not in ('fenced', 'preserved'):
            raise RecoveryDenied(f'workstream is neither fenced nor independently preserved: {name}')
        _validate_processes(row, row['disposition'])
    job_rows = receipt.get('jobs')
    if not isinstance(job_rows, dict) or set(job_rows) != set(jobs):
        raise RecoveryDenied('every in-progress operator job needs an explicit fence/preservation receipt')
    for job_id, row in job_rows.items():
        if not isinstance(row, dict) or row.get('disposition') not in ('fenced', 'preserved'):
            raise RecoveryDenied(f'operator job is not safely fenced/preserved: {job_id}')
        _validate_processes(row, row['disposition'])
    return receipt, _sha(raw)


def _timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except (AttributeError, ValueError):
        raise RecoveryDenied('owner lease timestamp is invalid') from None


def _write_exclusive(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace(path: Path, data: bytes, mode: int, uid: int | None = None, gid: int | None = None) -> None:
    fd, temp_name = tempfile.mkstemp(prefix='.operator-recovery-', dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        if uid is not None and gid is not None:
            os.fchown(fd, uid, gid)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def recover_expired_operator(
    *, admin_id: str, admin_role: str, expected_owner: str, expected_record_sha256: str,
    receiving_operator: str, receiving_principal_id: str, reason: str, scope: str, execution_expires_at: str,
    recovery_id: str, owner_file: Path = OWNER_FILE, fence_file: Path = FENCE_FILE,
    audit_dir: Path = AUDIT_DIR, trusted_fence_uid: int = 0, now: float | None = None,
    require_root: bool = True, lock_paths: tuple[Path, ...] | None = None, trusted_audit_uid: int = 0,
) -> dict:
    """Perform one administrator-authorized CAS recovery; never starts work."""
    if admin_role != 'admin' or not admin_id:
        raise RecoveryDenied('authenticated administrator role required')
    if require_root and (os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'jimran@abitcons.com' or os.uname().nodename != 'abit-dev-linux'):
        raise RecoveryDenied('existing root administrator boundary required: sudo from jimran@abitcons.com on abit-dev-linux')
    if expected_owner != EXPECTED_OWNER or receiving_operator != RECEIVER or receiving_principal_id != receiving_operator:
        raise RecoveryDenied('recovery is limited to the exact enrolled owner and receiver principal')
    if expected_record_sha256 != expected_record_sha256.lower() or not HEX_RE.fullmatch(expected_record_sha256):
        raise RecoveryDenied('exact owner record SHA-256 required')
    if scope != 'linkedin-core-seven-file-release:' + APPROVED_MANIFEST:
        raise RecoveryDenied('scope is not the already-approved immutable LinkedIn release')
    if not isinstance(reason, str) or len(reason.strip()) < 20 or len(reason) > 1000:
        raise RecoveryDenied('a specific administrator recovery reason is required')
    try:
        uuid.UUID(recovery_id)
    except (ValueError, TypeError, AttributeError):
        raise RecoveryDenied('valid unique recovery ID required') from None
    current_time = time.time() if now is None else now
    expiry = _timestamp(execution_expires_at)
    if expiry <= current_time or expiry - current_time > 3600:
        raise RecoveryDenied('new execution window must be future-dated and at most one hour')

    owner_file = Path(owner_file)
    audit_dir = Path(audit_dir)
    if owner_file.is_symlink() or not owner_file.is_file():
        raise RecoveryDenied('owner record must be a regular non-symlink file')
    parent = owner_file.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o002:
        raise RecoveryDenied('owner coordination directory is unsafe')

    with ExitStack() as stack:
        active_locks = lock_paths or (LOCK_FILES if owner_file == OWNER_FILE else tuple(owner_file.with_name(n) for n in ('operator-session.json.lock', 'operator-session.json.claim.lock', '.operator-session-review.lock', 'operator-session.lock')))
        for lock_path in sorted(active_locks + (owner_file.with_name('.operator-session-recovery.lock'),)):
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o660)
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RecoveryDenied(f'owner coordination lock is busy: {lock_path.name}') from None
            stack.callback(fcntl.flock, fd, fcntl.LOCK_UN)

        old_raw, old_info = _read_regular(owner_file)
        old_sha = _sha(old_raw)
        if old_sha != expected_record_sha256:
            raise RecoveryDenied('owner record revision/hash changed; refresh before recovery')
        old = json.loads(old_raw)
        if old.get('owner') != expected_owner or old.get('state') != OWNER_STATE or old.get('released_at') is not None:
            raise RecoveryDenied('expected unreleased active owner record not found')
        if _timestamp(old.get('lease_expires_at')) >= current_time:
            raise RecoveryDenied('owner lease is not expired; concurrent renewal wins')
        if old.get('operator_recovery', {}).get('status') == 'COMMITTED':
            raise RecoveryDenied('owner record already has a committed recovery')

        if audit_dir.exists():
            audit_info = audit_dir.stat(follow_symlinks=False)
            if audit_dir.is_symlink() or not audit_dir.is_dir() or audit_info.st_uid != trusted_audit_uid or stat.S_IMODE(audit_info.st_mode) != 0o700:
                raise RecoveryDenied('recovery audit directory must be trusted-owner mode 0700')
            for orphan in audit_dir.glob('*.intent.json'):
                try:
                    intent = json.loads(orphan.read_text())
                except (OSError, ValueError):
                    raise RecoveryDenied('unreadable prior recovery intent requires manual reconciliation') from None
                if intent.get('previous_record_sha256') == old_sha:
                    raise RecoveryDenied('an interrupted recovery intent for this owner revision requires manual reconciliation')
        else:
            audit_dir.mkdir(mode=0o700)
            os.chmod(audit_dir, 0o700)
            audit_info = audit_dir.stat(follow_symlinks=False)
            if audit_info.st_uid != trusted_audit_uid:
                raise RecoveryDenied('recovery audit directory owner is not trusted')

        fence, fence_sha = _validate_fence_receipt(fence_file, old, old_sha, current_time, trusted_fence_uid)
        holds = fence.get('preserved_uncertain_work')
        if holds != REQUIRED_PRESERVED_HOLDS:
            raise RecoveryDenied('fence receipt must explicitly preserve both original uncertain holds')

        recovery_id = str(uuid.UUID(recovery_id))
        receiver_token = secrets.token_urlsafe(32)
        intent_path = audit_dir / (recovery_id + '.intent.json')
        snapshot_path = audit_dir / (recovery_id + '.previous-owner.json')
        _write_exclusive(snapshot_path, old_raw, 0o600)
        receipt = {
            'schema_version': 1, 'status': 'COMMIT_INTENT', 'recovery_id': recovery_id,
            'administrator': {'id': admin_id, 'role': 'admin'}, 'reason': reason.strip(),
            'previous_owner': expected_owner, 'previous_record_sha256': old_sha,
            'previous_record_snapshot_sha256': _sha(old_raw),
            'receiving_operator': receiving_operator, 'receiving_principal_id': receiving_principal_id, 'scope': scope,
            'execution_window': {'expires_at': execution_expires_at},
            'work_fence_receipt_sha256': fence_sha,
            'workstreams': fence['workstreams'], 'jobs': fence['jobs'],
            'preserved_uncertain_work': REQUIRED_PRESERVED_HOLDS,
            'maintenance_gate_mutated': False, 'admissions_mutated': False,
            'provider_outcomes_reconciled': False, 'agent_started': False,
            'created_at': datetime.fromtimestamp(current_time, timezone.utc).isoformat(),
        }
        receipt_raw = _canonical(receipt)
        _write_exclusive(intent_path, receipt_raw, 0o600)
        receipt_sha = _sha(receipt_raw)

        new = dict(old)
        new['owner'] = receiving_operator
        new['previous_owner'] = expected_owner
        new['previous_session_sha256'] = old_sha
        new['lease_expires_at'] = execution_expires_at
        new['released_at'] = None
        new['state'] = OWNER_STATE
        new['phase'] = 'ADMIN_RECOVERED_AWAITING_RECEIVER_ACKNOWLEDGMENT'
        new['updated_at'] = datetime.fromtimestamp(current_time, timezone.utc).isoformat()
        new['operator_recovery'] = {
            'schema_version': 1, 'status': 'COMMITTED', 'generation': int(old.get('operator_recovery', {}).get('generation', 0)) + 1,
            'recovery_id': recovery_id, 'administrator_id': admin_id,
            'previous_owner': expected_owner, 'previous_record_sha256': old_sha,
            'receiving_operator': receiving_operator, 'scope': scope,
            'execution_expires_at': execution_expires_at,
            'work_fence_receipt_sha256': fence_sha, 'audit_receipt_sha256': receipt_sha,
            'audit_receipt_path': str(intent_path), 'requires_fresh_receiver_acknowledgment': True,
            'requires_fresh_activation_binding': True, 'receiver_ack_token_sha256': _sha(receiver_token.encode()),
            'receiving_principal_id': receiving_principal_id,
        }
        new_raw = _canonical(new) + b'\n'
        # Re-read immediately before the atomic replacement: CAS against any concurrent writer.
        latest, latest_info = _read_regular(owner_file)
        if _sha(latest) != old_sha or (latest_info.st_dev, latest_info.st_ino) != (old_info.st_dev, old_info.st_ino):
            raise RecoveryDenied('owner record drifted during recovery; no transition applied')
        if os.geteuid() != 0 and (os.geteuid() != old_info.st_uid or os.getegid() != old_info.st_gid):
            raise RecoveryDenied('only the original owner or root may atomically replace the owner record')
        _atomic_replace(owner_file, new_raw, stat.S_IMODE(old_info.st_mode), old_info.st_uid, old_info.st_gid)
        return {
            'status': 'ADMINISTRATOR_RECOVERY_COMMITTED', 'recovery_id': recovery_id,
            'previous_owner': expected_owner, 'receiving_operator': receiving_operator,
            'previous_record_sha256': old_sha, 'new_record_sha256': _sha(new_raw),
            'audit_receipt_sha256': receipt_sha, 'work_fence_receipt_sha256': fence_sha,
            'execution_expires_at': execution_expires_at,
            'requires_fresh_receiver_acknowledgment': True,
            'requires_fresh_activation_binding': True,
            'receiver_ack_token': receiver_token,
            'maintenance_gate_mutated': False, 'admissions_mutated': False,
            'provider_outcomes_reconciled': False, 'agent_started': False,
        }



def acknowledge_recovered_operator(
    *, receiver_id: str, receiver_token: str, expected_record_sha256: str,
    owner_file: Path = OWNER_FILE, audit_dir: Path = AUDIT_DIR, now: float | None = None,
    require_root: bool = True, lock_paths: tuple[Path, ...] | None = None, trusted_audit_uid: int = 0,
) -> dict:
    """Receiver proves possession of the one-use handoff capability and gets a protected binding."""
    if not receiver_id or not receiver_token or not HEX_RE.fullmatch(expected_record_sha256):
        raise RecoveryDenied('authenticated receiver and exact current record hash required')
    if require_root and (os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'jimran@abitcons.com' or os.uname().nodename != 'abit-dev-linux'):
        raise RecoveryDenied('existing root administrator boundary required for receiver acknowledgment')
    owner_file = Path(owner_file)
    with ExitStack() as stack:
        active_locks = lock_paths or (LOCK_FILES if owner_file == OWNER_FILE else tuple(owner_file.with_name(n) for n in ('operator-session.json.lock', 'operator-session.json.claim.lock', '.operator-session-review.lock', 'operator-session.lock')))
        for lock_path in sorted(active_locks + (owner_file.with_name('.operator-session-recovery.lock'),)):
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o660)
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RecoveryDenied(f'owner coordination lock is busy: {lock_path.name}') from None
            stack.callback(fcntl.flock, fd, fcntl.LOCK_UN)
        raw, info = _read_regular(owner_file)
        if _sha(raw) != expected_record_sha256:
            raise RecoveryDenied('owner record changed before receiver acknowledgment')
        owner = json.loads(raw)
        recovery = owner.get('operator_recovery') or {}
        if recovery.get('status') != 'COMMITTED' or recovery.get('receiving_principal_id') != receiver_id:
            raise RecoveryDenied('receiver identity does not match committed handoff')
        if recovery.get('receiver_acknowledged_at') or recovery.get('receiver_binding_sha256'):
            raise RecoveryDenied('receiver acknowledgment is already consumed')
        if not secrets.compare_digest(recovery.get('receiver_ack_token_sha256', ''), _sha(receiver_token.encode())):
            raise RecoveryDenied('one-use receiver acknowledgment capability is invalid')
        current_time = time.time() if now is None else now
        if _timestamp(owner.get('lease_expires_at')) <= current_time:
            raise RecoveryDenied('recovered execution window expired before acknowledgment')
        audit_path = Path(recovery['audit_receipt_path'])
        audit_raw, _ = _read_regular(audit_path, max_bytes=512 * 1024)
        if _sha(audit_raw) != recovery.get('audit_receipt_sha256'):
            raise RecoveryDenied('administrator recovery audit receipt changed')
        audit_dir = Path(audit_dir)
        audit_info = audit_dir.stat(follow_symlinks=False)
        if audit_dir.is_symlink() or not audit_dir.is_dir() or audit_info.st_uid != trusted_audit_uid or stat.S_IMODE(audit_info.st_mode) != 0o700:
            raise RecoveryDenied('recovery audit directory must be trusted-owner mode 0700')
        binding_path = audit_dir / (recovery['recovery_id'] + '.receiver-binding.json')
        binding = {
            'schema_version': 1, 'status': 'RECEIVER_ACKNOWLEDGED',
            'recovery_id': recovery['recovery_id'], 'generation': recovery['generation'],
            'receiver': owner['owner'], 'receiver_principal_id': receiver_id,
            'scope': recovery['scope'], 'manifest_sha256': APPROVED_MANIFEST,
            'execution_expires_at': owner['lease_expires_at'],
            'previous_owner_record_sha256': recovery['previous_record_sha256'],
            'administrator_audit_sha256': recovery['audit_receipt_sha256'],
            'preserved_uncertain_work': REQUIRED_PRESERVED_HOLDS,
            'acknowledged_at': datetime.fromtimestamp(current_time, timezone.utc).isoformat(),
            'publishing_permitted': False, 'new_agent_execution_started': False,
        }
        binding_raw = _canonical(binding) + b'\n'
        _write_exclusive(binding_path, binding_raw, 0o600)
        recovery['receiver_acknowledged_at'] = binding['acknowledged_at']
        recovery['receiver_ack_token_sha256'] = None
        recovery['receiver_binding_path'] = str(binding_path)
        recovery['receiver_binding_sha256'] = _sha(binding_raw)
        recovery['requires_fresh_receiver_acknowledgment'] = False
        # The LinkedIn activation controller must still mint its own exact protected binding.
        recovery['requires_fresh_activation_binding'] = True
        owner['operator_recovery'] = recovery
        new_raw = _canonical(owner) + b'\n'
        latest, latest_info = _read_regular(owner_file)
        if _sha(latest) != expected_record_sha256 or (latest_info.st_dev, latest_info.st_ino) != (info.st_dev, info.st_ino):
            raise RecoveryDenied('owner record drifted during receiver acknowledgment')
        _atomic_replace(owner_file, new_raw, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
        return {
            'status': 'RECEIVER_ACKNOWLEDGED', 'owner': owner['owner'],
            'record_sha256': _sha(new_raw), 'receiver_binding_sha256': _sha(binding_raw),
            'receiver_binding_path': str(binding_path), 'release_activation_binding_still_required': True, 'scope': recovery['scope'],
        }


def validate_recovered_operator(owner_file: Path, expected_scope: str, now: float | None = None, trusted_uid: int = 0) -> dict:
    """Execution-path guard: stale owner bindings cannot survive a recovery."""
    raw, _ = _read_regular(Path(owner_file))
    owner = json.loads(raw)
    recovery = owner.get('operator_recovery') or {}
    if owner.get('state') != OWNER_STATE or recovery.get('status') != 'COMMITTED':
        raise RecoveryDenied('administrator recovery is not committed')
    if recovery.get('receiving_operator') != owner.get('owner') or recovery.get('scope') != expected_scope:
        raise RecoveryDenied('recovered operator or scope binding mismatch')
    if recovery.get('requires_fresh_receiver_acknowledgment') is not False:
        raise RecoveryDenied('receiver acknowledgment is still required')
    if _timestamp(owner.get('lease_expires_at')) <= (time.time() if now is None else now):
        raise RecoveryDenied('recovered operator execution window expired')
    binding_path = Path(recovery.get('receiver_binding_path', ''))
    binding_raw, binding_info = _read_regular(binding_path, max_bytes=128 * 1024)
    if binding_info.st_uid != trusted_uid or stat.S_IMODE(binding_info.st_mode) != 0o600 or _sha(binding_raw) != recovery.get('receiver_binding_sha256'):
        raise RecoveryDenied('fresh protected receiver binding missing or changed')
    binding = json.loads(binding_raw)
    if binding.get('recovery_id') != recovery.get('recovery_id') or binding.get('generation') != recovery.get('generation') or binding.get('receiver') != owner.get('owner') or binding.get('scope') != expected_scope or binding.get('manifest_sha256') != APPROVED_MANIFEST:
        raise RecoveryDenied('fresh activation binding does not match recovered owner')
    receipt_path = Path(recovery.get('audit_receipt_path', ''))
    receipt_raw, info = _read_regular(receipt_path, max_bytes=512 * 1024)
    if info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) != 0o600 or _sha(receipt_raw) != recovery.get('audit_receipt_sha256'):
        raise RecoveryDenied('recovery audit receipt missing or changed')
    receipt = json.loads(receipt_raw)
    if receipt.get('recovery_id') != recovery.get('recovery_id') or receipt.get('previous_record_sha256') != recovery.get('previous_record_sha256'):
        raise RecoveryDenied('recovery audit lineage mismatch')
    return {'owner': owner['owner'], 'generation': recovery['generation'], 'record_sha256': _sha(raw), 'audit_sha256': _sha(receipt_raw), 'receiver_acknowledged': True, 'release_activation_binding_required': True}

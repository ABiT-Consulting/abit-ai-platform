"""Fail-closed, audited recovery of an expired orchestration owner.

This module is source-only until separately approved and installed. It never
opens or mutates the maintenance-admission database and never starts work.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import secrets
import stat
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OWNER_FILE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
FENCE_FILE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session-fence-receipt.json')
AUDIT_DIR = OWNER_FILE.parent / 'operator-session-recovery-audit'
FENCE_EVIDENCE_DIR = OWNER_FILE.parent / 'operator-session-fence-evidence'
LOCK_FILES = (
    OWNER_FILE.with_name('operator-session.json.lock'),
    OWNER_FILE.with_name('operator-session.json.claim.lock'),
    OWNER_FILE.with_name('.operator-session-review.lock'),
    OWNER_FILE.with_name('operator-session.lock'),
)
EXPECTED_OWNER = 'chat-authorized-root-orchestration-recovery-20261122T1007'
RECEIVER = 'chat-linkedin-admission-recovery-8c1bc230-20261123'
APPROVED_COMMIT = '8c1bc2308fdf693659520103559863682333299b'
EXPECTED_WORKSTREAMS = ('original-relay-gateway-recovery', 'core-accepted-original-publication', 'proj0202-r4-independent-review-and-new-stage', 'native-erp-readonly-preflight')
BASENAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
SERVICE_UID = 999
SERVICE_GID = 988
ACK_SPOOL_DIR = Path('/srv/abit-dev-linux/abit-ai/data/maintenance-admission/operator-recovery-acks')
ACK_KEY_FILE = Path('/var/lib/abit-ai-operator-recovery/receiver-ack.key')
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


def _trusted_uid() -> int:
    return 0


def _validate_sudo_identity(sudo_uid: int, sudo_user: str, account_name: str,
                            parent_exe: str, effective_uid: int, hostname: str) -> dict:
    if effective_uid != 0 or hostname != 'abit-dev-linux':
        raise RecoveryDenied('root administrator invocation on abit-dev-linux required')
    if account_name != 'jimran@abitcons.com' or sudo_user != account_name:
        raise RecoveryDenied('sudo principal is not the enrolled ABiT host administrator')
    if Path(parent_exe).name != 'sudo':
        raise RecoveryDenied('recovery must be invoked directly through sudo')
    return {'id': account_name, 'uid': sudo_uid, 'role': 'host_administrator', 'auth': 'sudo'}


def _admin_identity() -> dict:
    try:
        sudo_uid = int(os.environ.get('SUDO_UID', ''))
        account = pwd.getpwuid(sudo_uid)
        parent_exe = os.readlink(f'/proc/{os.getppid()}/exe')
    except (ValueError, KeyError, OSError):
        raise RecoveryDenied('sudo identity or parent process could not be verified') from None
    return _validate_sudo_identity(sudo_uid, os.environ.get('SUDO_USER', ''), account.pw_name,
                                   parent_exe, os.geteuid(), os.uname().nodename)


def _open_dir(path: Path) -> int:
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RecoveryDenied('absolute normalized directory path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            if part in ('', '.'):
                continue
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_regular(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> tuple[bytes, os.stat_result]:
    path = Path(path)
    if path.name in ('', '.', '..'):
        raise RecoveryDenied('regular file path required')
    dir_fd = _open_dir(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_bytes:
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
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ) or len(data) != before.st_size:
                raise RecoveryDenied(f'file changed during read: {path.name}')
            return data, before
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def _proc_start_ticks(pid: int) -> int | None:
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
    except FileNotFoundError:
        return None
    except ProcessLookupError:
        return None
    except OSError as error:
        raise RecoveryDenied(f'cannot positively inspect process {pid}: {error.__class__.__name__}') from None
    try:
        tail = raw[raw.rfind(')') + 2:].split()
        if len(tail) <= 19:
            raise ValueError
        return int(tail[19])
    except (ValueError, IndexError):
        raise RecoveryDenied(f'process identity is malformed: pid {pid}') from None


def _proc_cgroup(pid: int) -> str:
    try:
        return Path(f'/proc/{pid}/cgroup').read_text()
    except OSError as error:
        raise RecoveryDenied(f'cannot verify process cgroup {pid}: {error.__class__.__name__}') from None


def _validate_processes(processes: list, disposition: str) -> None:
    if not isinstance(processes, list):
        raise RecoveryDenied('process inventory must be an explicit list')
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
            cgroup = _proc_cgroup(pid)
            supervisor = proc.get('supervisor_cgroup')
            if not isinstance(supervisor, str) or not supervisor.startswith('/') or '..' in supervisor.split('/'):
                raise RecoveryDenied(f'preserved process supervisor cgroup is invalid: pid {pid}')
            paths = [line.split(':', 2)[2] for line in cgroup.splitlines() if line.count(':') >= 2]
            if not any(path == supervisor or path.startswith(supervisor.rstrip('/') + '/') for path in paths):
                raise RecoveryDenied(f'preserved process is outside its recorded supervisor cgroup: pid {pid}')
            if any(path == '/system.slice/abit-ai.service' or path.startswith('/system.slice/abit-ai.service/') for path in paths):
                raise RecoveryDenied('Core-bound work cannot be claimed preserved across the approved Core restart')
        else:
            raise RecoveryDenied('unsupported workstream disposition')



def _evidence_hash(row: dict, label: str) -> str:
    name = row.get('evidence_file')
    expected = row.get('evidence_sha256')
    if not isinstance(name, str) or not BASENAME_RE.fullmatch(name) or not isinstance(expected, str) or not HEX_RE.fullmatch(expected):
        raise RecoveryDenied(f'{label} must name a bounded evidence file and SHA-256')
    raw, info = _read_regular(FENCE_EVIDENCE_DIR / name, max_bytes=MAX_EVIDENCE_BYTES)
    if info.st_uid != _trusted_uid() or stat.S_IMODE(info.st_mode) != 0o600 or _sha(raw) != expected:
        raise RecoveryDenied(f'{label} evidence file ownership, mode or hash differs')
    return expected


def _validate_work_inventory(row: dict, label: str) -> None:
    if row.get('inventory_complete') is not True:
        raise RecoveryDenied(f'{label} provider/browser inventory is not declared complete')
    _evidence_hash(row, label)
    _validate_processes(row.get('processes'), row.get('disposition'))
    for key in ('provider_requests', 'browser_sessions'):
        items = row.get(key)
        if not isinstance(items, list):
            raise RecoveryDenied(f'{label} must explicitly inventory {key}')
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip():
                raise RecoveryDenied(f'{label} {key}[{index}] has no stable identity')
            if item.get('disposition') not in ('fenced', 'preserved'):
                raise RecoveryDenied(f'{label} {key}[{index}] has an unknown/unfenced outcome')
            _evidence_hash(item, f'{label} {key}[{index}]')


def _validate_fence_receipt(path: Path, owner: dict, owner_sha: str, now: float, trusted_uid: int) -> tuple[dict, str]:
    raw, info = _read_regular(path, max_bytes=512 * 1024)
    if info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) != 0o600:
        raise RecoveryDenied('fence receipt must be root-owned mode 0600')
    try:
        receipt = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RecoveryDenied('fence receipt is not valid JSON') from None
    if receipt.get('schema_version') != 2 or receipt.get('status') != 'FENCED_OR_PRESERVED' or receipt.get('no_replay') is not True:
        raise RecoveryDenied('trusted no-replay work-fence receipt is missing or incomplete')
    if receipt.get('owner_record_sha256') != owner_sha or receipt.get('previous_owner') != owner.get('owner'):
        raise RecoveryDenied('fence receipt is not bound to the exact current owner record')
    issued = receipt.get('issued_at')
    try:
        issued_at = datetime.fromisoformat(issued.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        raise RecoveryDenied('fence receipt timestamp is invalid') from None
    if issued_at > now or now - issued_at > 300 or issued_at <= _timestamp(owner['lease_expires_at']):
        raise RecoveryDenied('fence receipt must be fresh and issued after owner expiry')
    workstreams = owner.get('active_workstreams')
    jobs = owner.get('operator_jobs_in_progress')
    if not isinstance(workstreams, list) or tuple(workstreams) != EXPECTED_WORKSTREAMS:
        raise RecoveryDenied('active workstream set changed; independent re-review required')
    if not isinstance(jobs, list) or len(jobs) != len(set(jobs)) or not all(isinstance(x, str) for x in jobs):
        raise RecoveryDenied('operator job inventory is malformed')
    if FENCE_EVIDENCE_DIR.is_symlink() or FENCE_EVIDENCE_DIR.resolve() != FENCE_EVIDENCE_DIR.absolute():
        raise RecoveryDenied('fence evidence path must not contain symlinks')
    evidence_info = FENCE_EVIDENCE_DIR.lstat()
    if not stat.S_ISDIR(evidence_info.st_mode) or evidence_info.st_uid != trusted_uid or stat.S_IMODE(evidence_info.st_mode) != 0o700:
        raise RecoveryDenied('fence evidence directory must be root-owned mode 0700')
    owner_session = receipt.get('owner_session')
    if not isinstance(owner_session, dict) or owner_session.get('disposition') not in ('absent', 'fenced'):
        raise RecoveryDenied('previous owner Chat/session requires explicit absence or fence evidence')
    _evidence_hash(owner_session, 'previous owner session')
    session_processes = owner_session.get('processes')
    if not isinstance(session_processes, list):
        raise RecoveryDenied('previous owner session process inventory is required')
    if owner_session['disposition'] == 'absent':
        if session_processes or owner_session.get('absence_verified') is not True:
            raise RecoveryDenied('owner-session absence must be positively verified, not inferred from missing Chat metadata')
    else:
        if not session_processes:
            raise RecoveryDenied('fenced owner session must identify its original process identities')
        _validate_processes(session_processes, 'fenced')
    rows = receipt.get('workstreams')
    if not isinstance(rows, dict) or set(rows) != set(workstreams):
        raise RecoveryDenied('every active workstream needs an explicit fence/preservation receipt')
    for name, row in rows.items():
        if not isinstance(row, dict) or row.get('disposition') not in ('fenced', 'preserved'):
            raise RecoveryDenied(f'workstream is neither fenced nor independently preserved: {name}')
        _validate_work_inventory(row, name)
    job_rows = receipt.get('jobs')
    if not isinstance(job_rows, dict) or set(job_rows) != set(jobs):
        raise RecoveryDenied('every in-progress operator job needs an explicit fence/preservation receipt')
    for job_id, row in job_rows.items():
        if not isinstance(row, dict) or row.get('disposition') not in ('fenced', 'preserved'):
            raise RecoveryDenied(f'operator job is not safely fenced/preserved: {job_id}')
        _validate_work_inventory(row, f'operator job {job_id}')
    if receipt.get('preserved_uncertain_work') != REQUIRED_PRESERVED_HOLDS:
        raise RecoveryDenied('fence receipt must explicitly preserve both original uncertain holds')
    return receipt, _sha(raw)


def _timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except (AttributeError, ValueError):
        raise RecoveryDenied('owner lease timestamp is invalid') from None


def _write_exclusive(path: Path, data: bytes, mode: int = 0o600, uid: int | None = None, gid: int | None = None) -> None:
    path = Path(path)
    dir_fd = _open_dir(path.parent)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=dir_fd)
        try:
            os.fchmod(fd, mode)
            if uid is not None and gid is not None:
                os.fchown(fd, uid, gid)
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('short write')
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _atomic_replace(path: Path, data: bytes, mode: int, uid: int, gid: int) -> None:
    path = Path(path)
    dir_fd = _open_dir(path.parent)
    temp_name = f'.{path.name}.recovery-{secrets.token_hex(12)}'
    try:
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=dir_fd)
        try:
            os.fchmod(fd, mode)
            os.fchown(fd, uid, gid)
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('short write')
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        os.close(dir_fd)


def _audit_paths(recovery_id: str) -> dict:
    return {
        'snapshot': AUDIT_DIR / f'{recovery_id}.previous-owner.json',
        'intent': AUDIT_DIR / f'{recovery_id}.intent.json',
        'token': AUDIT_DIR / f'{recovery_id}.receiver-token',
        'commit': AUDIT_DIR / f'{recovery_id}.committed.json',
        'ack_intent': AUDIT_DIR / f'{recovery_id}.ack-intent.json',
        'binding': AUDIT_DIR / f'{recovery_id}.receiver-binding.json',
    }


def _write_or_verify(path: Path, data: bytes, mode: int = 0o600, uid: int | None = None, gid: int | None = None) -> str:
    try:
        _write_exclusive(path, data, mode, uid, gid)
    except FileExistsError:
        existing, info = _read_regular(path, max_bytes=MAX_EVIDENCE_BYTES)
        if existing != data or (uid is not None and info.st_uid != uid) or (gid is not None and info.st_gid != gid) or stat.S_IMODE(info.st_mode) != mode:
            raise RecoveryDenied(f'existing recovery artifact differs: {path.name}') from None
    return _sha(data)


def _ensure_private_dir(path: Path, uid: int) -> None:
    path = Path(path)
    parent_fd = _open_dir(path.parent)
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent_fd)
            os.chmod(path.name, 0o700, dir_fd=parent_fd, follow_symlinks=False)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise RecoveryDenied(f'{path.name} must be a real trusted-owner mode-0700 directory')
    finally:
        os.close(parent_fd)


@contextmanager
def _owner_lock_set():
    dir_fd = _open_dir(OWNER_FILE.parent)
    with ExitStack() as stack:
        try:
            lock_names = sorted([Path(x).name for x in LOCK_FILES] + ['.operator-session-recovery.lock'])
            for name in lock_names:
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o660, dir_fd=dir_fd)
                info = os.fstat(fd)
                expected_admin_uid = pwd.getpwnam('jimran@abitcons.com').pw_uid
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid not in (0, expected_admin_uid)
                        or info.st_gid not in (0, SERVICE_GID)
                        or stat.S_IMODE(info.st_mode) & 0o007):
                    os.close(fd)
                    raise RecoveryDenied(f'unsafe owner coordination lock: {name}')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    raise RecoveryDenied(f'owner coordination lock is busy: {name}') from None
                stack.callback(os.close, fd)
                stack.callback(fcntl.flock, fd, fcntl.LOCK_UN)
            yield
        finally:
            os.close(dir_fd)


def _read_owner(admin_uid: int) -> tuple[bytes, dict, os.stat_result]:
    raw, info = _read_regular(OWNER_FILE)
    try:
        owner = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RecoveryDenied('owner record is not valid JSON') from None
    parent_fd = _open_dir(OWNER_FILE.parent)
    try:
        parent = os.fstat(parent_fd)
    finally:
        os.close(parent_fd)
    if (not stat.S_ISDIR(parent.st_mode) or info.st_uid != admin_uid or parent.st_uid != admin_uid
            or parent.st_gid != SERVICE_GID or stat.S_IMODE(parent.st_mode) != 0o2770 or (parent.st_mode & stat.S_ISGID) == 0):
        raise RecoveryDenied('owner record or coordination directory ownership is not trusted')
    if stat.S_IMODE(info.st_mode) != 0o640:
        raise RecoveryDenied('owner record mode changed from the enrolled 0640 contract')
    return raw, owner, info


def _check_orphaned_attempts(owner_sha: str) -> None:
    if not AUDIT_DIR.exists():
        return
    if AUDIT_DIR.is_symlink() or not AUDIT_DIR.is_dir() or AUDIT_DIR.stat().st_uid != _trusted_uid() or stat.S_IMODE(AUDIT_DIR.stat().st_mode) != 0o700:
        raise RecoveryDenied('recovery audit directory is not private')
    for orphan in list(AUDIT_DIR.glob('*.intent.json')) + list(AUDIT_DIR.glob('*.previous-owner.json')):
        raw, _ = _read_regular(orphan, max_bytes=512 * 1024)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise RecoveryDenied('unreadable orphaned recovery artifact requires manual reconciliation') from None
        if value.get('previous_record_sha256') == owner_sha or value.get('owner_record_sha256') == owner_sha or _sha(raw) == owner_sha:
            raise RecoveryDenied('interrupted recovery artifact for this owner revision requires manual reconciliation')


def _verified_commit_marker(owner: dict, expected_pre_ack_sha: str) -> tuple[dict, bytes]:
    recovery = owner.get('operator_recovery') or {}
    paths = _audit_paths(recovery.get('recovery_id', ''))
    marker_raw, marker_info = _read_regular(paths['commit'], max_bytes=128 * 1024)
    if marker_info.st_uid != _trusted_uid() or marker_info.st_gid != SERVICE_GID or stat.S_IMODE(marker_info.st_mode) != 0o640:
        raise RecoveryDenied('commit marker ownership/mode differs')
    try:
        marker = json.loads(marker_raw)
    except (ValueError, UnicodeDecodeError):
        raise RecoveryDenied('commit marker is malformed') from None
    if (marker.get('status') != 'COMMITTED' or marker.get('recovery_id') != recovery.get('recovery_id')
            or marker.get('intent_sha256') != recovery.get('audit_intent_sha256')
            or marker.get('owner_record_sha256') != expected_pre_ack_sha
            or marker.get('previous_record_sha256') != recovery.get('previous_record_sha256')
            or marker.get('no_replay') is not True):
        raise RecoveryDenied('commit marker does not bind the exact owner transition')
    return marker, marker_raw


def recover_expired_operator(
    *, expected_owner: str, expected_record_sha256: str, receiving_operator: str,
    receiving_principal_id: str, reason: str, scope: str,
    execution_expires_at: str, recovery_id: str,
) -> dict:
    """Perform one administrator-authorized CAS recovery; never starts work."""
    administrator = _admin_identity()
    if expected_owner != EXPECTED_OWNER or receiving_operator != RECEIVER:
        raise RecoveryDenied('recovery is limited to the exact enrolled previous owner and receiver label')
    if not isinstance(receiving_principal_id, str) or not receiving_principal_id.strip() or len(receiving_principal_id) > 128:
        raise RecoveryDenied('registered receiving ABIT.AI principal ID is required')
    if expected_record_sha256 != expected_record_sha256.lower() or not HEX_RE.fullmatch(expected_record_sha256):
        raise RecoveryDenied('exact owner record SHA-256 required')
    if scope != 'linkedin-core-seven-file-release:' + APPROVED_MANIFEST:
        raise RecoveryDenied('scope is not the approved seven-file LinkedIn correction')
    if not isinstance(reason, str) or len(reason.strip()) < 20 or len(reason) > 1000:
        raise RecoveryDenied('specific administrator recovery reason is required')
    try:
        recovery_id = str(uuid.UUID(recovery_id))
    except (ValueError, TypeError, AttributeError):
        raise RecoveryDenied('valid unique recovery ID required') from None
    current_time = time.time()
    expiry = _timestamp(execution_expires_at)
    if expiry <= current_time or expiry - current_time > 3600:
        raise RecoveryDenied('new execution window must be future-dated and at most one hour')

    with _owner_lock_set():
        old_raw, old, old_info = _read_owner(administrator['uid'])
        old_sha = _sha(old_raw)
        if old_sha != expected_record_sha256:
            raise RecoveryDenied('owner record revision/hash changed; refresh before recovery')
        if old.get('owner') != expected_owner or old.get('state') != OWNER_STATE or old.get('released_at') is not None:
            raise RecoveryDenied('expected unreleased active owner record not found')
        if _timestamp(old.get('lease_expires_at')) >= current_time:
            raise RecoveryDenied('owner lease is not expired; concurrent renewal wins')
        if 'operator_recovery' in old:
            raise RecoveryDenied('owner record already has a recovery generation or partial recovery')
        if tuple(old.get('active_workstreams') or ()) != EXPECTED_WORKSTREAMS:
            raise RecoveryDenied('active workstream set changed; independent re-review required')

        _ensure_private_dir(AUDIT_DIR, _trusted_uid())
        _check_orphaned_attempts(old_sha)
        fence, fence_sha = _validate_fence_receipt(FENCE_FILE, old, old_sha, current_time, _trusted_uid())
        paths = _audit_paths(recovery_id)
        if any(path.exists() for path in paths.values()):
            raise RecoveryDenied('recovery ID already has artifacts; do not reuse it')

        receiver_token = secrets.token_urlsafe(32)
        intent = {
            'schema_version': 2, 'status': 'COMMIT_INTENT', 'recovery_id': recovery_id,
            'administrator': administrator, 'reason': reason.strip(),
            'previous_owner': expected_owner, 'previous_record_sha256': old_sha,
            'previous_record_snapshot_sha256': _sha(old_raw),
            'receiving_operator': receiving_operator, 'receiving_principal_id': receiving_principal_id,
            'scope': scope, 'manifest_commit': APPROVED_COMMIT, 'manifest_sha256': APPROVED_MANIFEST,
            'execution_window': {'expires_at': execution_expires_at},
            'work_fence_receipt_sha256': fence_sha,
            'workstreams': fence['workstreams'], 'jobs': fence['jobs'], 'owner_session': fence['owner_session'],
            'preserved_uncertain_work': REQUIRED_PRESERVED_HOLDS,
            'maintenance_gate_mutated': False, 'admissions_mutated': False,
            'provider_outcomes_reconciled': False, 'agent_started': False, 'no_replay': True,
            'created_at': datetime.fromtimestamp(current_time, timezone.utc).isoformat(),
        }
        intent_raw = _canonical(intent)
        _write_exclusive(paths['snapshot'], old_raw, 0o600)
        _write_exclusive(paths['intent'], intent_raw, 0o600)
        intent_sha = _sha(intent_raw)
        _write_exclusive(paths['token'], (receiver_token + '\n').encode(), 0o600)

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
            'schema_version': 2, 'status': 'COMMIT_PENDING_AUDIT', 'generation': 1,
            'recovery_id': recovery_id, 'administrator_id': administrator['id'],
            'previous_owner': expected_owner, 'previous_record_sha256': old_sha,
            'receiving_operator': receiving_operator, 'receiving_principal_id': receiving_principal_id,
            'scope': scope, 'manifest_commit': APPROVED_COMMIT, 'manifest_sha256': APPROVED_MANIFEST,
            'execution_expires_at': execution_expires_at,
            'work_fence_receipt_sha256': fence_sha, 'audit_intent_sha256': intent_sha,
            'audit_intent_path': str(paths['intent']), 'commit_marker_path': str(paths['commit']),
            'receiver_token_path': str(paths['token']), 'receiver_token_sha256': _sha(receiver_token.encode()),
            'requires_fresh_receiver_acknowledgment': True, 'requires_fresh_activation_binding': True,
            'maintenance_gate_mutated': False, 'admissions_mutated': False,
            'provider_outcomes_reconciled': False, 'agent_started': False, 'no_replay': True,
        }
        new_raw = _canonical(new) + b'\n'
        latest, _, latest_info = _read_owner(administrator['uid'])
        if _sha(latest) != old_sha or (latest_info.st_dev, latest_info.st_ino) != (old_info.st_dev, old_info.st_ino):
            raise RecoveryDenied('owner record drifted during recovery; no transition applied')
        _atomic_replace(OWNER_FILE, new_raw, stat.S_IMODE(old_info.st_mode), old_info.st_uid, old_info.st_gid)
        committed_owner_sha = _sha(new_raw)
        marker = {
            'schema_version': 1, 'status': 'COMMITTED', 'recovery_id': recovery_id,
            'previous_record_sha256': old_sha, 'owner_record_sha256': committed_owner_sha,
            'intent_sha256': intent_sha, 'administrator_id': administrator['id'],
            'committed_at': datetime.fromtimestamp(time.time(), timezone.utc).isoformat(),
            'maintenance_gate_mutated': False, 'agent_started': False, 'no_replay': True,
        }
        _write_exclusive(paths['commit'], _canonical(marker), 0o640, _trusted_uid(), SERVICE_GID)
        return {
            'status': 'ADMINISTRATOR_RECOVERY_COMMITTED_AWAITING_RECEIVER_ACK',
            'recovery_id': recovery_id, 'administrator': administrator['id'],
            'previous_owner': expected_owner, 'receiving_operator': receiving_operator,
            'receiving_principal_id': receiving_principal_id,
            'previous_record_sha256': old_sha, 'new_record_sha256': committed_owner_sha,
            'audit_intent_sha256': intent_sha, 'commit_marker_sha256': _sha(_canonical(marker)),
            'receiver_token_path': str(paths['token']), 'execution_expires_at': execution_expires_at,
            'requires_authenticated_receiver_acknowledgment': True,
            'requires_fresh_linkedin_activation_binding': True,
            'maintenance_gate_mutated': False, 'admissions_mutated': False,
            'provider_outcomes_reconciled': False, 'agent_started': False,
        }


def reconcile_commit(expected_record_sha256: str) -> dict:
    """Finish the audit marker after a crash that already changed the owner record."""
    administrator = _admin_identity()
    if not HEX_RE.fullmatch(expected_record_sha256):
        raise RecoveryDenied('exact pending owner record hash required')
    with _owner_lock_set():
        raw, owner, _ = _read_owner(administrator['uid'])
        current_sha = _sha(raw)
        if current_sha != expected_record_sha256:
            raise RecoveryDenied('pending owner record hash changed')
        recovery = owner.get('operator_recovery') or {}
        if recovery.get('status') != 'COMMIT_PENDING_AUDIT':
            raise RecoveryDenied('owner record is not in the recoverable audit-pending state')
        paths = _audit_paths(recovery['recovery_id'])
        intent_raw, intent_info = _read_regular(paths['intent'], max_bytes=512 * 1024)
        if intent_info.st_uid != _trusted_uid() or stat.S_IMODE(intent_info.st_mode) != 0o600 or _sha(intent_raw) != recovery.get('audit_intent_sha256'):
            raise RecoveryDenied('recovery intent is missing or changed')
        snapshot, snapshot_info = _read_regular(paths['snapshot'])
        if snapshot_info.st_uid != _trusted_uid() or stat.S_IMODE(snapshot_info.st_mode) != 0o600 or _sha(snapshot) != recovery.get('previous_record_sha256'):
            raise RecoveryDenied('previous-owner snapshot is missing or changed')
        token_raw, token_info = _read_regular(paths['token'], max_bytes=256)
        if token_info.st_uid != _trusted_uid() or stat.S_IMODE(token_info.st_mode) != 0o600 or _sha(token_raw.strip()) != recovery.get('receiver_token_sha256'):
            raise RecoveryDenied('one-use receiver token artifact is missing or changed')
        marker = {
            'schema_version': 1, 'status': 'COMMITTED', 'recovery_id': recovery['recovery_id'],
            'previous_record_sha256': recovery['previous_record_sha256'],
            'owner_record_sha256': current_sha, 'intent_sha256': recovery['audit_intent_sha256'],
            'administrator_id': recovery['administrator_id'],
            'committed_at': datetime.fromtimestamp(time.time(), timezone.utc).isoformat(),
            'maintenance_gate_mutated': False, 'agent_started': False, 'no_replay': True,
        }
        marker_raw = _canonical(marker)
        _write_or_verify(paths['commit'], marker_raw, 0o640, _trusted_uid(), SERVICE_GID)
        return {'status': 'COMMIT_MARKER_RECONCILED', 'recovery_id': recovery['recovery_id'],
                'owner_record_sha256': current_sha, 'commit_marker_sha256': _sha(marker_raw),
                'receiver_token_path': str(paths['token']),
                'requires_authenticated_receiver_acknowledgment': True}


def _receiver_ack_key() -> bytes:
    raw, info = _read_regular(ACK_KEY_FILE, max_bytes=256)
    parent_fd = _open_dir(ACK_KEY_FILE.parent)
    try:
        parent_info = os.fstat(parent_fd)
    finally:
        os.close(parent_fd)
    if (parent_info.st_uid != _trusted_uid() or stat.S_IMODE(parent_info.st_mode) != 0o700
            or info.st_uid != _trusted_uid() or info.st_gid != SERVICE_GID or stat.S_IMODE(info.st_mode) != 0o640 or len(raw.strip()) < 32):
        raise RecoveryDenied('receiver acknowledgment signing key ownership/mode/length differs')
    return raw.strip()


def _ack_payload_signature(payload: dict, key: bytes) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def record_receiver_acknowledgment(*, authenticated_user: Any, receiver_token: str, expected_record_sha256: str) -> dict:
    """Persist an authenticated receiver assertion; only the root CLI may apply it."""
    receiver_id = getattr(authenticated_user, 'id', None)
    if not isinstance(receiver_id, str) or not receiver_id or not isinstance(receiver_token, str):
        raise RecoveryDenied('authenticated ABIT.AI receiver session required')
    if not HEX_RE.fullmatch(expected_record_sha256):
        raise RecoveryDenied('exact current owner record hash required')
    raw, _ = _read_regular(OWNER_FILE)
    current_sha = _sha(raw)
    owner = json.loads(raw)
    recovery = owner.get('operator_recovery') or {}
    if recovery.get('receiving_principal_id') != receiver_id:
        raise RecoveryDenied('authenticated user is not the enrolled receiving operator')
    if recovery.get('status') == 'COMMITTED':
        existing_path = ACK_SPOOL_DIR / f"{recovery.get('recovery_id')}.receiver-ack.json"
        existing_raw, existing_info = _read_regular(existing_path, max_bytes=16 * 1024)
        if existing_info.st_uid != SERVICE_UID or stat.S_IMODE(existing_info.st_mode) != 0o600:
            raise RecoveryDenied('existing receiver acknowledgment receipt ownership/mode differs')
        existing = json.loads(existing_raw)
        signature = existing.pop('signature', '')
        if (not hmac.compare_digest(signature, _ack_payload_signature(existing, _receiver_ack_key()))
                or existing.get('token_sha256') != _sha(receiver_token.encode())
                or existing.get('owner_record_sha256') != recovery.get('pre_ack_owner_record_sha256')):
            raise RecoveryDenied('receiver challenge was already consumed or does not match')
        return {'status': 'AUTHENTICATED_RECEIVER_ACK_ALREADY_RECORDED',
                'recovery_id': recovery['recovery_id'], 'receipt_path': str(existing_path),
                'receipt_sha256': _sha(existing_raw), 'root_administrator_application_required': False}
    if (owner.get('state') != OWNER_STATE or owner.get('released_at') is not None
            or recovery.get('receiving_operator') != owner.get('owner')
            or recovery.get('scope') != 'linkedin-core-seven-file-release:' + APPROVED_MANIFEST
            or recovery.get('manifest_sha256') != APPROVED_MANIFEST):
        raise RecoveryDenied('owner record or recovery scope is not valid for receiver acknowledgment')
    if recovery.get('status') != 'COMMIT_PENDING_AUDIT' or current_sha != expected_record_sha256:
        raise RecoveryDenied('owner record is not in the exact audit-committed receiver-acknowledgment state')
    if _timestamp(owner.get('lease_expires_at')) <= time.time():
        raise RecoveryDenied('recovered execution window expired before acknowledgment')
    _verified_commit_marker(owner, current_sha)
    paths = _audit_paths(recovery['recovery_id'])
    token_raw, token_info = _read_regular(paths['token'], max_bytes=256)
    if token_info.st_uid != _trusted_uid() or stat.S_IMODE(token_info.st_mode) != 0o600:
        raise RecoveryDenied('receiver challenge token file ownership/mode differs')
    if not secrets.compare_digest(recovery.get('receiver_token_sha256', ''), _sha(receiver_token.encode())) or not secrets.compare_digest(token_raw.strip().decode(), receiver_token):
        raise RecoveryDenied('one-use receiver challenge is invalid')

    _ensure_private_dir(ACK_SPOOL_DIR, SERVICE_UID)
    receipt_path = ACK_SPOOL_DIR / f"{recovery['recovery_id']}.receiver-ack.json"
    key = _receiver_ack_key()
    payload = {
        'schema_version': 1, 'status': 'AUTHENTICATED_RECEIVER_ACK',
        'recovery_id': recovery['recovery_id'], 'receiver_id': receiver_id,
        'owner_record_sha256': current_sha, 'token_sha256': _sha(receiver_token.encode()),
        'commit_marker_sha256': _sha(_read_regular(paths['commit'])[0]),
        'acknowledged_at': datetime.fromtimestamp(time.time(), timezone.utc).isoformat(),
    }
    signature = _ack_payload_signature(payload, key)
    signed = {**payload, 'signature': signature}
    signed_raw = _canonical(signed)
    try:
        _write_exclusive(receipt_path, signed_raw, 0o600)
    except FileExistsError:
        existing_raw, existing_info = _read_regular(receipt_path, max_bytes=16 * 1024)
        if existing_info.st_uid != SERVICE_UID or stat.S_IMODE(existing_info.st_mode) != 0o600:
            raise RecoveryDenied('existing receiver acknowledgment receipt ownership/mode differs') from None
        existing = json.loads(existing_raw)
        existing_signature = existing.pop('signature', '')
        if (not hmac.compare_digest(existing_signature, _ack_payload_signature(existing, key))
                or existing.get('receiver_id') != receiver_id
                or existing.get('owner_record_sha256') != current_sha
                or existing.get('token_sha256') != _sha(receiver_token.encode())):
            raise RecoveryDenied('existing receiver acknowledgment receipt differs') from None
        signed_raw = existing_raw
    return {
        'status': 'AUTHENTICATED_RECEIVER_ACK_RECORDED',
        'recovery_id': recovery['recovery_id'], 'receipt_path': str(receipt_path),
        'receipt_sha256': _sha(signed_raw), 'owner_record_sha256': current_sha,
        'root_administrator_application_required': True,
    }


def apply_receiver_acknowledgment(recovery_id: str) -> dict:
    """Root-administered application of a receiver-signed, service-owned receipt."""
    administrator = _admin_identity()
    try:
        recovery_id = str(uuid.UUID(recovery_id))
    except (ValueError, TypeError, AttributeError):
        raise RecoveryDenied('valid recovery ID required') from None
    receipt_path = ACK_SPOOL_DIR / f'{recovery_id}.receiver-ack.json'
    receipt_raw, receipt_info = _read_regular(receipt_path, max_bytes=16 * 1024)
    if receipt_info.st_uid != SERVICE_UID or receipt_info.st_gid != SERVICE_GID or stat.S_IMODE(receipt_info.st_mode) != 0o600:
        raise RecoveryDenied('receiver acknowledgment spool receipt ownership/mode differs')
    try:
        signed = json.loads(receipt_raw)
        signature = signed.pop('signature')
    except (ValueError, KeyError, UnicodeDecodeError):
        raise RecoveryDenied('receiver acknowledgment receipt is malformed') from None
    key = _receiver_ack_key()
    if not hmac.compare_digest(signature, _ack_payload_signature(signed, key)):
        raise RecoveryDenied('receiver acknowledgment signature is invalid')
    if signed.get('schema_version') != 1 or signed.get('status') != 'AUTHENTICATED_RECEIVER_ACK' or signed.get('recovery_id') != recovery_id:
        raise RecoveryDenied('receiver acknowledgment receipt scope is invalid')

    with _owner_lock_set():
        current_raw, current_owner, current_info = _read_owner(administrator['uid'])
        current_sha = _sha(current_raw)
        recovery = current_owner.get('operator_recovery') or {}
        if (current_owner.get('state') != OWNER_STATE or current_owner.get('released_at') is not None
                or recovery.get('receiving_operator') != current_owner.get('owner')
                or recovery.get('scope') != 'linkedin-core-seven-file-release:' + APPROVED_MANIFEST
                or recovery.get('manifest_sha256') != APPROVED_MANIFEST):
            raise RecoveryDenied('owner record or recovery scope is not valid for acknowledgment application')
        if recovery.get('receiving_principal_id') != signed.get('receiver_id'):
            raise RecoveryDenied('signed receiver identity does not match enrolled receiving operator')
        if recovery.get('status') == 'COMMITTED':
            if recovery.get('pre_ack_owner_record_sha256') == signed.get('owner_record_sha256') and recovery.get('receiver_ack_receipt_sha256') == _sha(receipt_raw):
                return {'status': 'RECEIVER_ACK_APPLICATION_ALREADY_COMMITTED', 'owner_record_sha256': current_sha,
                        'recovery_id': recovery_id, 'receiver_ack_receipt_sha256': _sha(receipt_raw)}
            raise RecoveryDenied('a different receiver acknowledgment is already committed')
        if recovery.get('status') != 'COMMIT_PENDING_AUDIT' or current_sha != signed.get('owner_record_sha256'):
            raise RecoveryDenied('signed receiver acknowledgment is stale or owner state has changed')
        if _timestamp(current_owner.get('lease_expires_at')) <= time.time():
            raise RecoveryDenied('recovered execution window expired before acknowledgment application')
        marker, marker_raw = _verified_commit_marker(current_owner, current_sha)
        if signed.get('commit_marker_sha256') != _sha(marker_raw) or signed.get('token_sha256') != recovery.get('receiver_token_sha256'):
            raise RecoveryDenied('receiver receipt is not bound to the current commit marker/challenge')

        paths = _audit_paths(recovery_id)
        ack_intent = {
            'schema_version': 1, 'status': 'RECEIVER_ACK_APPLIED',
            'recovery_id': recovery_id, 'receiver_id': signed['receiver_id'],
            'owner_record_sha256': current_sha, 'receiver_ack_receipt_sha256': _sha(receipt_raw),
            'acknowledged_at': signed['acknowledged_at'], 'commit_marker_sha256': _sha(marker_raw),
        }
        ack_raw = _canonical(ack_intent)
        _write_or_verify(paths['ack_intent'], ack_raw)
        binding = {
            'schema_version': 1, 'status': 'RECEIVER_ACKNOWLEDGED',
            'recovery_id': recovery_id, 'generation': recovery['generation'],
            'receiver': current_owner['owner'], 'receiver_principal_id': signed['receiver_id'],
            'scope': recovery['scope'], 'manifest_commit': APPROVED_COMMIT,
            'manifest_sha256': APPROVED_MANIFEST,
            'execution_expires_at': current_owner['lease_expires_at'],
            'previous_owner_record_sha256': recovery['previous_record_sha256'],
            'pre_ack_owner_record_sha256': current_sha,
            'administrator_audit_intent_sha256': recovery['audit_intent_sha256'],
            'commit_marker_sha256': _sha(marker_raw), 'ack_intent_sha256': _sha(ack_raw),
            'receiver_ack_receipt_sha256': _sha(receipt_raw),
            'preserved_uncertain_work': REQUIRED_PRESERVED_HOLDS,
            'acknowledged_at': signed['acknowledged_at'],
            'publishing_permitted': False, 'new_agent_execution_started': False,
        }
        binding_raw = _canonical(binding)
        binding_sha = _write_or_verify(paths['binding'], binding_raw, 0o640, _trusted_uid(), SERVICE_GID)

        updated = dict(current_owner)
        updated_recovery = dict(recovery)
        updated_recovery.update({
            'status': 'COMMITTED', 'pre_ack_owner_record_sha256': current_sha,
            'receiver_acknowledged_at': signed['acknowledged_at'],
            'receiver_ack_intent_sha256': _sha(ack_raw),
            'receiver_ack_receipt_sha256': _sha(receipt_raw),
            'receiver_binding_path': str(paths['binding']), 'receiver_binding_sha256': binding_sha,
            'requires_fresh_receiver_acknowledgment': False,
            'requires_fresh_activation_binding': True,
        })
        updated['operator_recovery'] = updated_recovery
        updated['phase'] = 'ADMIN_RECOVERED_ACKNOWLEDGED_AWAITING_LINKEDIN_ACTIVATION_BINDING'
        updated['updated_at'] = datetime.fromtimestamp(time.time(), timezone.utc).isoformat()
        new_raw = _canonical(updated) + b'\n'
        latest, _, latest_info = _read_owner(current_info.st_uid)
        if _sha(latest) != current_sha or (latest_info.st_dev, latest_info.st_ino) != (current_info.st_dev, current_info.st_ino):
            raise RecoveryDenied('owner record drifted during receiver acknowledgment application')
        _atomic_replace(OWNER_FILE, new_raw, stat.S_IMODE(current_info.st_mode), current_info.st_uid, current_info.st_gid)
        return {
            'status': 'RECEIVER_ACKNOWLEDGED', 'owner': updated['owner'], 'recovery_id': recovery_id,
            'record_sha256': _sha(new_raw), 'receiver_binding_sha256': binding_sha,
            'receiver_binding_path': str(paths['binding']), 'release_activation_binding_still_required': True,
            'publishing_permitted': False, 'new_agent_execution_started': False,
        }


def validate_recovered_operator(expected_receiver: str, expected_scope: str) -> dict:
    """Read-only execution-path guard for the recovered owner and receiver proof."""
    raw, _ = _read_regular(OWNER_FILE)
    current_sha = _sha(raw)
    owner = json.loads(raw)
    recovery = owner.get('operator_recovery') or {}
    if (owner.get('state') != OWNER_STATE or owner.get('released_at') is not None
            or owner.get('owner') != expected_receiver or recovery.get('receiving_operator') != expected_receiver):
        raise RecoveryDenied('stale or non-recovered operator identity')
    if recovery.get('status') != 'COMMITTED' or recovery.get('requires_fresh_receiver_acknowledgment') is not False:
        raise RecoveryDenied('administrator recovery/receiver acknowledgment is incomplete')
    if (recovery.get('scope') != expected_scope or recovery.get('manifest_sha256') != APPROVED_MANIFEST
            or recovery.get('manifest_commit') != APPROVED_COMMIT or recovery.get('generation') != 1
            or recovery.get('previous_owner') != EXPECTED_OWNER):
        raise RecoveryDenied('recovered operator scope or manifest mismatch')
    if _timestamp(owner.get('lease_expires_at')) <= time.time():
        raise RecoveryDenied('recovered operator execution window expired')
    marker, marker_raw = _verified_commit_marker(owner, recovery.get('pre_ack_owner_record_sha256', ''))
    binding_path = Path(recovery.get('receiver_binding_path', ''))
    binding_raw, binding_info = _read_regular(binding_path, max_bytes=128 * 1024)
    if binding_info.st_uid != _trusted_uid() or binding_info.st_gid != SERVICE_GID or stat.S_IMODE(binding_info.st_mode) != 0o640 or _sha(binding_raw) != recovery.get('receiver_binding_sha256'):
        raise RecoveryDenied('fresh protected receiver binding missing or changed')
    binding = json.loads(binding_raw)
    if (binding.get('recovery_id') != recovery.get('recovery_id')
            or binding.get('generation') != recovery.get('generation')
            or binding.get('receiver') != owner.get('owner')
            or binding.get('scope') != expected_scope
            or binding.get('manifest_sha256') != APPROVED_MANIFEST
            or binding.get('manifest_commit') != APPROVED_COMMIT
            or binding.get('receiver_principal_id') != recovery.get('receiving_principal_id')
            or binding.get('pre_ack_owner_record_sha256') != recovery.get('pre_ack_owner_record_sha256')
            or binding.get('administrator_audit_intent_sha256') != recovery.get('audit_intent_sha256')
            or binding.get('commit_marker_sha256') != _sha(marker_raw)
            or binding.get('ack_intent_sha256') != recovery.get('receiver_ack_intent_sha256')
            or binding.get('receiver_ack_receipt_sha256') != recovery.get('receiver_ack_receipt_sha256')):
        raise RecoveryDenied('receiver binding does not match the committed recovery generation')
    return {
        'owner': owner['owner'], 'generation': recovery['generation'],
        'record_sha256': current_sha, 'commit_marker_verified': bool(marker),
        'receiver_acknowledged': True, 'release_activation_binding_required': True,
        'publishing_permitted': False, 'new_agent_execution_started': False,
    }

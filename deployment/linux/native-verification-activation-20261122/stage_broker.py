"""Stage only the reviewed root broker with a disabled registration.

No key, per-task gate, worker authority, nonce, socket, service start/enable, Core
configuration edit or provider command is created. Unknown existing objects hold.
"""
from pathlib import Path
import datetime
import fcntl
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys

PACKAGE = Path('/srv/abit-dev-linux/abit-ai/deployment/native-verification-20261122T1615')
STAGE = Path('/var/lib/abit-ai-native-verification-20261122T1615')
BUNDLE = Path('/var/lib/abit/native-verification/service')
UNIT = Path('/etc/systemd/system/abit-native-verification-broker.service')
LEASE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
OWNER = 'chat-authorized-root-orchestration-recovery-20261122T1007'
GLOBAL_LOCK = Path('/var/lock/abit-ai-governance-release.lock')
SOURCE_MANIFEST_SHA = '77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6'
CONFIG = Path('/srv/abit-dev-linux/abit-ai/config/erp-assignment/resolver.json')
MODULES = {'agentic_native_contract.py', 'agentic_native_broker.py', 'agentic_native_service.py'}
JOURNAL = STAGE / 'default-off-broker-staging.json'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_regular(path, maximum=2 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_size <= maximum, 'Fixed source file metadata differs')
        value = stream.read(maximum + 1)
        require(len(value) <= maximum, 'Fixed source file size exceeded')
        return value


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def trusted_directory(path):
    require(path.is_absolute() and path.resolve() == path, 'Root directory alias differs')
    for ancestor in [Path('/'), *reversed(path.parents[:-1]), path]:
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and not stat.S_IMODE(info.st_mode) & 0o022,
                'Root directory is not exclusively administered')


def owned_file(path, data, mode):
    trusted_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == mode
            and read_regular(path) == data, 'Owned broker file differs')


def create_exact(path, data, mode):
    trusted_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fchown(stream.fileno(), 0, 0)
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def save_journal(value, initial=False):
    encoded = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    if initial:
        create_exact(JOURNAL, encoded, 0o600)
    else:
        temporary = STAGE / ('default-off-broker-' + str(os.getpid()) + '.tmp')
        create_exact(temporary, encoded, 0o600)
        os.replace(temporary, JOURNAL)
        fsync_directory(STAGE)


def operator():
    require(os.geteuid() == 0 and os.environ.get('SUDO_USER') == 'jimran@abitcons.com'
            and socket.gethostname() == 'abit-dev-linux', 'Existing authorized operator required')
    lease = json.loads(LEASE.read_text())
    require(lease.get('owner') == OWNER
            and lease.get('state') == 'ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY'
            and datetime.datetime.fromisoformat(lease['lease_expires_at'].replace('Z', '+00:00')) >
            datetime.datetime.now(datetime.timezone.utc), 'Operator lease expired or replaced')


def inactive():
    result = subprocess.run(['/usr/bin/systemctl', 'show', 'abit-native-verification-broker.service',
                             '-p', 'ActiveState', '-p', 'MainPID'],
                            capture_output=True, text=True, timeout=10, check=False)
    require(result.returncode in (0, 4), 'Broker unit status unavailable')
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    require(values.get('ActiveState') == 'inactive' and values.get('MainPID') == '0',
            'Broker is already active or has an unsettled transition')


def default_off():
    value = json.loads(read_regular(CONFIG))
    require(type(value) is dict, 'Core configuration is not an object')
    selected = value.get('agentic_native_verification')
    require(selected is None or (type(selected) is dict and selected.get('enabled') is False),
            'Core native route must remain disabled')


def inputs():
    encoded = read_regular(PACKAGE / 'source-manifest.json')
    require(digest(encoded) == SOURCE_MANIFEST_SHA, 'Reviewed source manifest differs')
    manifest = json.loads(encoded)
    rows = manifest['root_bundle_files']
    require({row['path'] for row in rows} == MODULES and len(rows) == 3,
            'Unexpected broker source set')
    result = {}
    for row in rows:
        require(row['target'] == str(BUNDLE / row['path'])
                and row['mode'] == '0400' and row['uid'] == row['gid'] == 0,
                'Broker source target differs')
        data = read_regular(PACKAGE / 'root-bundle' / row['path'])
        require(digest(data) == row['sha256'], 'Broker source changed')
        result[BUNDLE / row['path']] = (data, 0o400)
    registration = read_regular(PACKAGE / 'root-registration.template.json')
    unit = read_regular(PACKAGE / 'abit-native-verification-broker.service')
    require(digest(registration) == manifest['supporting_files']['root-registration.template.json']
            and digest(unit) == manifest['supporting_files']['abit-native-verification-broker.service'],
            'Disabled registration or unit changed')
    value = json.loads(registration)
    require(value.get('enabled') is False and value['core_peer']['pid'] == 0
            and value['core_peer']['cmdline_sha256'] == 'PENDING_DENY',
            'Registration must be disabled and unbound')
    result[BUNDLE / 'registration.json'] = (registration, 0o400)
    result[UNIT] = (unit, 0o644)
    return result


def apply():
    operator()
    trusted_directory(STAGE)
    inactive()
    default_off()
    payload = inputs()
    expected = {str(path): {'sha256': digest(data), 'mode': oct(mode)}
                for path, (data, mode) in payload.items()}
    fresh = not os.path.lexists(JOURNAL)
    if fresh:
        require(not os.path.lexists(BUNDLE.parent), 'Native broker prefix already exists')
        require(all(not os.path.lexists(path) for path in payload),
                'Unknown preexisting broker files cannot be adopted')
        record = {'schema_version': 1, 'source_manifest_sha256': SOURCE_MANIFEST_SHA,
                  'phase': 'intent_saved', 'started_utc': now(), 'targets': expected,
                  'keys_created': False, 'authority_issued': False, 'service_started': False}
        save_journal(record, initial=True)
    else:
        encoded = read_regular(JOURNAL)
        owned_file(JOURNAL, encoded, 0o600)
        record = json.loads(encoded)
        require(record.get('schema_version') == 1
                and record.get('source_manifest_sha256') == SOURCE_MANIFEST_SHA
                and record.get('targets') == expected
                and record.get('phase') in ('intent_saved', 'staged_disabled'),
                'Original staging intent differs')
    for path in (BUNDLE.parent.parent, BUNDLE.parent, BUNDLE):
        operator()
        if not os.path.lexists(path):
            trusted_directory(path.parent)
            path.mkdir(mode=0o700)
            fsync_directory(path.parent)
        trusted_directory(path)
    require(set(BUNDLE.iterdir()) <= {path for path in payload if path.parent == BUNDLE},
            'Unexpected broker bundle contents')
    for path, (data, mode) in payload.items():
        operator()
        default_off()
        if not os.path.lexists(path):
            create_exact(path, data, mode)
        owned_file(path, data, mode)
    inactive()
    default_off()
    record.update(phase='staged_disabled', finished_utc=now(),
                  service_started=False, daemon_reloaded=False, service_enabled=False,
                  core_configuration_modified=False, keys_created=False,
                  runtime_socket_created=False, authority_issued=False, per_task_gate_created=False)
    save_journal(record)
    print(json.dumps({'state': 'DEFAULT_OFF_BUNDLE_STAGED_SERVICE_NOT_STARTED',
                      'source_manifest_sha256': SOURCE_MANIFEST_SHA,
                      'receipt_sha256': digest(read_regular(JOURNAL)),
                      'files': len(payload), 'service_started': False,
                      'authority_issued': False}), flush=True)


def main():
    require(len(sys.argv) == 1, 'Broker staging accepts no runtime arguments')
    operator()
    fd = os.open(GLOBAL_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                'Shared Core release lock differs')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apply()
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()

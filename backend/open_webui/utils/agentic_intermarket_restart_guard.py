"""Fixed read-only InterMarket restart sample shared by broker and package observer.

No arguments, configuration, credentials, mutation, service command selection or
network access. Receipts contain only hashes and public process/file metadata.
The protected installed code manifest and the package pin these exact bytes.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

UNIT = 'intermarket-sbodesk.service'
UNIT_FILE = Path('/etc/systemd/system/intermarket-sbodesk.service')
DROPINS = Path('/etc/systemd/system/intermarket-sbodesk.service.d')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID',
              'InvocationID', 'Job', 'ExecStart', 'WorkingDirectory', 'User',
              'Group', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
COMMAND = ('/usr/bin/systemctl', 'show', UNIT, '--no-pager',
           '--property=' + ','.join(PROPERTIES))


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def token(info):
    return {key: getattr(info, key) for key in
            ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def manager_state():
    result = subprocess.run(COMMAND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=2, check=False,
                            env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})
    require(result.returncode == 0 and 0 < len(result.stdout) <= 65536,
            'Fixed service manager sample unavailable')
    fields = {}
    for line in result.stdout.decode('utf-8', errors='strict').splitlines():
        name, separator, value = line.partition('=')
        require(separator == '=' and name in PROPERTIES and name not in fields,
                'Fixed service manager sample shape differs')
        fields[name] = value
    require(set(fields) == set(PROPERTIES) and fields['Id'] == UNIT
            and fields['LoadState'] == 'loaded' and fields['ActiveState'] == 'active'
            and fields['SubState'] == 'running' and fields['ControlPID'] == '0'
            and fields['Job'] in ('', '0') and fields['NeedDaemonReload'] == 'no'
            and re.fullmatch('[1-9][0-9]{0,9}', fields['MainPID'])
            and int(fields['MainPID']) > 1
            and re.fullmatch('[0-9a-f]{32}', fields['InvocationID'])
            and int(fields['InvocationID'], 16) > 0
            and fields['FragmentPath'] == str(UNIT_FILE),
            'Fixed service is not the idle loaded running generation')
    paths = fields['DropInPaths'].split()
    require(len(paths) <= 32 and len(paths) == len(set(paths))
            and all(Path(path).parent == DROPINS and
                    re.fullmatch('[A-Za-z0-9_.-]+[.]conf', Path(path).name)
                    and not Path(path).name.startswith('.') for path in paths),
            'Fixed service drop-in paths differ')
    return fields, paths


def start_ticks(pid):
    # This public process identity does not read environment or private files.
    raw = (Path('/proc') / str(pid) / 'stat').read_bytes()
    require(0 < len(raw) <= 8192 and raw.split(b' ', 1)[0] == str(pid).encode(),
            'Original service process disappeared')
    tail = raw.rsplit(b')', 1)[1].split()
    require(len(tail) > 19 and tail[19].isdigit() and int(tail[19]) > 0,
            'Original process start identity unavailable')
    return int(tail[19])


def fragment(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= 65536, 'Fixed unit fragment type differs')
        raw = os.read(fd, 65537)
        after = os.fstat(fd)
        require(token(before) == token(after) and len(raw) == before.st_size,
                'Fixed unit fragment changed during read')
        require(token(os.lstat(path)) == token(before), 'Fixed fragment pathname replaced')
        return {'identity': token(before), 'sha256': sha(raw)}
    finally:
        os.close(fd)


def dropin_inventory():
    info = os.lstat(DROPINS)
    require(stat.S_ISDIR(info.st_mode), 'Fixed drop-in directory replaced')
    with os.scandir(DROPINS) as entries:
        names = sorted(entry.name for entry in entries if entry.name.endswith('.conf'))
    require(len(names) <= 32 and all(re.fullmatch('[A-Za-z0-9_.-]+[.]conf', n)
                                    and not n.startswith('.') for n in names),
            'Unexpected fixed drop-in inventory')
    return token(info), names


def sample_restart_precondition():
    """Return a stable current sample or fail closed; never submit a manager job."""
    before, paths = manager_state()
    pid = int(before['MainPID'])
    ticks = start_ticks(pid)
    directory, names = dropin_inventory()
    require(names == sorted(Path(path).name for path in paths),
            'Loaded selector and on-disk drop-in inventory differ')
    files = {str(UNIT_FILE): fragment(UNIT_FILE)}
    files.update({path: fragment(Path(path)) for path in sorted(paths)})
    after_directory, after_names = dropin_inventory()
    after, after_paths = manager_state()
    require(before == after and paths == after_paths and start_ticks(pid) == ticks
            and directory == after_directory and names == after_names,
            'Original service generation or selector changed while sampling')
    return {'unit': UNIT, 'pid': pid, 'start_ticks': ticks,
            'invocation_id': before['InvocationID'], 'manager_sha256': sha(canonical(before)),
            'dropin_directory': directory, 'fragments': files}

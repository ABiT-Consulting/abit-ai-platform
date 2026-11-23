"""Fixed root-owned standalone broker entrypoint; no application imports.

Install only a separately reviewed, hash-bound bundle. This source does not
install, enable, issue authority, or create a registration/key/gate.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import types

SERVICE = Path('/var/lib/abit/native-verification/service')
MODULE_NAMES = ('agentic_native_contract.py', 'agentic_native_broker.py')
ISSUER_MODULE_NAMES = ('agentic_native_contract.py', 'agentic_native_admission_contract.py',
                       'agentic_native_issuer.py', 'agentic_native_broker.py')
SOCKET = Path('/run/abit-native-verification/broker.sock')
KEY = Path('/etc/abit/native-verification-broker/key')


def require(value, code):
    if not value:
        raise ValueError(code)


def directory(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'NATIVE_SERVICE_PATH_INVALID')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022,
                    'NATIVE_SERVICE_ANCESTRY_CHANGED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def source_bytes(path, limit):
    path = Path(path)
    parent = directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and
                stat.S_IMODE(before.st_mode) == 0o400 and 0 < before.st_size <= limit,
                'NATIVE_SERVICE_SOURCE_METADATA_CHANGED')
        raw = bytearray()
        while len(raw) <= limit:
            part = os.read(fd, min(65536, limit+1-len(raw)))
            if not part:
                break
            raw.extend(part)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                                   value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) == before.st_size <= limit and identity(os.fstat(fd)) == identity(before) and
                identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == identity(before),
                'NATIVE_SERVICE_SOURCE_CHANGED')
        return bytes(raw)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def parse_registration(raw):
    def unique(items):
        value = {}
        for key, item in items:
            require(key not in value, 'NATIVE_REGISTRATION_DUPLICATE_KEY')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique)
    per_run = type(value) is dict and type(value.get('schema_version')) is int and value['schema_version'] == 2
    keys = {'schema_version', 'enabled', 'module_sha256', 'core_peer'} | ({'issuer'} if per_run else set())
    names = ISSUER_MODULE_NAMES if per_run else MODULE_NAMES
    require(type(value) is dict and set(value) == keys and
            type(value['schema_version']) is int and value['schema_version'] == (2 if per_run else 1) and
            value['enabled'] is True and type(value['module_sha256']) is dict and
            set(value['module_sha256']) == set(names), 'NATIVE_ROOT_REGISTRATION_REQUIRED')
    if per_run:
        issuer = value['issuer']
        require(type(issuer) is dict and set(issuer) == {'mode','subject_sha256','control_sha256'} and
                issuer['mode'] == 'per_invocation_v2', 'NATIVE_ISSUER_REGISTRATION_REQUIRED')
    for digest in value['module_sha256'].values():
        require(type(digest) is str and re.fullmatch(r'[a-f0-9]{64}', digest), 'NATIVE_ROOT_BUNDLE_UNBOUND')
    peer = value['core_peer']
    require(type(peer) is dict and set(peer) == {'pid', 'uid', 'gid', 'start_ticks', 'cmdline_sha256', 'boot_id'} and
            all(type(peer[key]) is int and peer[key] > 0 for key in ('pid', 'uid', 'gid', 'start_ticks')) and
            type(peer['cmdline_sha256']) is str and re.fullmatch(r'[a-f0-9]{64}', peer['cmdline_sha256']) and
            type(peer['boot_id']) is str and re.fullmatch(r'[a-f0-9-]{36}', peer['boot_id']),
            'NATIVE_CORE_PROCESS_PIN_REQUIRED')
    return value


def proc_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        raw = bytearray()
        while len(raw) <= limit:
            part = os.read(fd, min(65536, limit+1-len(raw)))
            if not part:
                break
            raw.extend(part)
        require(0 < len(raw) <= limit, 'NATIVE_CORE_KERNEL_METADATA_BOUNDS')
        return bytes(raw)
    finally:
        os.close(fd)


def process_proof(expected, pid, uid, gid):
    require((pid, uid, gid) == (expected['pid'], expected['uid'], expected['gid']),
            'NATIVE_CORE_KERNEL_PEER_CHANGED')
    base = Path('/proc') / str(pid)
    def start():
        value = proc_bytes(base/'stat', 8192).decode().rsplit(') ', 1)[1].split()
        require(value[0] not in {'Z', 'X'}, 'NATIVE_CORE_PROCESS_CEASED')
        return int(value[19])
    before = start()
    status = proc_bytes(base/'status', 65536).decode()
    numbers = {}
    for line in status.splitlines():
        key, sep, tail = line.partition(':')
        if key in {'Uid', 'Gid'} and sep:
            numbers[key] = tuple(int(item) for item in tail.split())
    command = hashlib.sha256(proc_bytes(base/'cmdline', 65536)).hexdigest()
    boot = proc_bytes('/proc/sys/kernel/random/boot_id', 64).decode().strip()
    require(before == expected['start_ticks'] == start() and
            numbers == {'Uid': (uid,)*4, 'Gid': (gid,)*4} and
            command == expected['cmdline_sha256'] and boot == expected['boot_id'],
            'NATIVE_CORE_PROCESS_IDENTITY_CHANGED')
    return dict(expected)


def load_modules(registration):
    # Verify the entire source set before executing either module. Each module
    # is compiled from the same bounded bytes that were hashed, without reopen.
    names = ISSUER_MODULE_NAMES if registration['schema_version'] == 2 else MODULE_NAMES
    sources = {name: source_bytes(SERVICE/name, 256*1024) for name in names}
    require(all(hashlib.sha256(raw).hexdigest() == registration['module_sha256'][name]
                for name, raw in sources.items()), 'NATIVE_ROOT_BUNDLE_BYTES_CHANGED')
    result = {}
    for filename in names:
        name = filename.removesuffix('.py')
        module = types.ModuleType(name)
        module.__file__, module.__package__ = str(SERVICE/filename), ''
        sys.modules[name] = module
        exec(compile(sources[filename], module.__file__, 'exec'), module.__dict__)
        result[name] = module
    return result


async def serve(registration, modules):
    peer = registration['core_peer']
    process_proof(peer, peer['pid'], peer['uid'], peer['gid'])
    broker = modules['agentic_native_broker']
    service = broker.RootNativeService(issuer_registration=registration.get('issuer'),
        peer_check=lambda: process_proof(peer, peer['pid'], peer['uid'], peer['gid']))
    server = broker.NativeBrokerServer(SOCKET, KEY, client_uid=peer['uid'], socket_gid=peer['gid'],
        verify_peer=lambda pid, uid, gid: process_proof(peer, pid, uid, gid), service=service)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    await server.start()
    try:
        await stopped.wait()
    finally:
        await server.close()


def main():
    require(os.geteuid() == 0 and Path(__file__) == SERVICE/'agentic_native_service.py' and len(sys.argv) == 1,
            'NATIVE_FIXED_ROOT_ENTRYPOINT_REQUIRED')
    source_bytes(Path(__file__), 65536)
    registration = parse_registration(source_bytes(SERVICE/'registration.json', 16384))
    asyncio.run(serve(registration, load_modules(registration)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('NATIVE_FIXED_SERVICE_HELD', file=sys.stderr)
        raise SystemExit(78) from None

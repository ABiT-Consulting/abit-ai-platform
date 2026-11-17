"""Server-scoped governance action intents and fixed infrastructure adapters.

No shell, arbitrary URL, model-provided root or approval assertion is accepted.
This gateway does not constrain tools reachable outside it; a relay without a
trusted tool filter must not be advertised as capability-isolated execution.
"""

from __future__ import annotations
import asyncio
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import time
from urllib.parse import urlsplit

ACTION_ID = re.compile(r'[A-Za-z0-9_.:@+-]{1,160}')
RESOURCE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}')
HEALTH_ENDPOINTS = {'abit-ai': 'http://127.0.0.1:8888/health', 'relay': 'http://127.0.0.1:8810/health'}
RPC = '/api/method/abit_ai_assistant.agentic_approval_actions.'
PROTECTED = {'active_log', 'audit_log', 'security_log', 'database', 'business_data', 'backup', 'unknown'}


class ApprovalRequired(ValueError):
    def __init__(self, action_id, action_fingerprint):
        self.action_id = action_id
        self.action_fingerprint = action_fingerprint
        super().__init__('Current ERP approval is required for this exact action')


class CapabilityUnavailable(ValueError):
    """No server-owned adapter/capability exists; never start a broad relay."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@contextmanager
def _connect(path=None):
    from open_webui.utils.agentic_governance import connect

    with connect(path) as db:
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS governance_action(
          action_id TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,request_hash TEXT NOT NULL,
          action_json TEXT NOT NULL,preflight_json TEXT NOT NULL,state TEXT NOT NULL,
          approval_json TEXT,result_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS governance_action_event(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,action_id TEXT NOT NULL,
          kind TEXT NOT NULL,body_json TEXT NOT NULL,created_at INTEGER NOT NULL);''')
        yield db


def _event(db, action, kind, body):
    db.execute(
        'INSERT INTO governance_action_event(action_id,kind,body_json,created_at) VALUES(?,?,?,?)',
        (action, kind, _json(body), int(time.time())),
    )


def _owned(db, invocation):
    row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation,)).fetchone()
    if (
        not row
        or row['state'] != 'running'
        or not row['lease_token']
        or not row['automation_run_id']
        or not row['work_packet']
        or not row['lease_expires_at']
        or row['lease_expires_at'] < int(time.time())
    ):
        raise ValueError('Action requires its currently active invocation and fresh task lease')
    lease = db.execute(
        'SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
        (row['erp_project'], row['erp_task']),
    ).fetchone()
    if not lease or tuple(lease) != (invocation, row['lease_fence']):
        raise ValueError('Action invocation lost its task fence')
    project = db.execute(
        'SELECT state FROM governance_project_control WHERE erp_project=?', (row['erp_project'],)
    ).fetchone()
    if not project or project['state'] != 'enabled':
        raise ValueError('Project infrastructure actions are paused, stopped or disabled')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_task_control'").fetchone():
        control = db.execute(
            'SELECT state FROM governance_task_control WHERE project=? AND task=?',
            (row['erp_project'], row['erp_task']),
        ).fetchone()
        if control and control['state'] != 'enabled':
            raise ValueError('Task infrastructure actions are paused or stopped')
    return dict(row)


def get_action(invocation_id, action_id, db_path=None):
    with _connect(db_path) as db:
        row = db.execute(
            'SELECT * FROM governance_action WHERE invocation_id=? AND action_id=?', (invocation_id, action_id)
        ).fetchone()
        if not row:
            return None
        value = dict(row)
        return {
            'action_id': value['action_id'],
            'invocation_id': invocation_id,
            'state': value['state'],
            'result': json.loads(value['result_json'] or 'null'),
            'created_at': value['created_at'],
            'updated_at': value['updated_at'],
        }


def _profile(config, project):
    profiles = (config.get('agentic_tool_gateway') or {}).get('projects') or {}
    value = profiles.get(project)
    if not isinstance(value, dict):
        raise CapabilityUnavailable('No trusted infrastructure gateway is configured for this project')
    if value.get('host_id') != socket.gethostname():
        raise ValueError('Configured gateway host identity does not match this server')
    return value


class TrustedAdapter:
    """Registered by server code only; never deserialized from API/model input."""

    def __init__(self, preflight, execute, *, capability, roles=('it_manager',), read_only=False, recover=None):
        if not callable(preflight) or not callable(execute):
            raise ValueError('Adapter handlers must be server callables')
        self.preflight = preflight
        self.execute = execute
        self.capability = capability
        self.roles = frozenset(roles)
        self.read_only = bool(read_only)
        self.recover = recover
        if recover is not None and not callable(recover):
            raise ValueError('Recovery reader must be a server callable')


def _open_dir(path):
    value = Path(path)
    if not value.is_absolute() or '..' in value.parts:
        raise ValueError('Only canonical server-owned absolute roots are allowed')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in value.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _root_for(path, roots):
    value = Path(path)
    if not value.is_absolute() or '..' in value.parts or '\\' in str(path):
        raise ValueError('Disposable path is not canonical')
    matches = []
    for root in roots:
        configured = Path(root)
        if configured.is_absolute() and '..' not in configured.parts:
            try:
                relative = value.relative_to(configured)
                if len(relative.parts) == 1:
                    matches.append(configured)
            except ValueError:
                pass
    if len(matches) != 1:
        raise ValueError('Cleanup needs a direct file in exactly one server-approved disposable directory')
    return matches[0], value.name


def _protected(path, classification, profile):
    value = str(path)
    if classification in PROTECTED or classification not in {'temporary', 'disposable_log'}:
        return True
    if value.startswith(('/etc/', '/var/lib/', '/home/')):
        return True
    if re.search(r'(?i)(^|[/_.-])(audit|security|secure|auth|backup|backups)([/_.-]|$)', value):
        return True
    if re.search(r'(?i)(\.db(?:-wal|-shm)?|\.sqlite(?:-wal|-shm)?|\.wal|\.bak|\.sql)$', value):
        return True
    return any(value == root or value.startswith(root.rstrip('/') + '/') for root in profile.get('protected_roots', []))


def _proc_writer(device, inode):
    """Fail closed when another process' descriptor inventory cannot be read."""
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
        except FileNotFoundError:
            continue
        except PermissionError:
            raise ValueError('Active-writer inspection is not authorized for every process') from None
        for descriptor in descriptors:
            try:
                info = descriptor.stat()
                if (info.st_dev, info.st_ino) != (device, inode):
                    continue
                lines = (process / 'fdinfo' / descriptor.name).read_text().splitlines()
                flags = next(int(line.split(':', 1)[1].strip(), 8) for line in lines if line.startswith('flags:'))
                if flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}:
                    return True
            except FileNotFoundError:
                continue
            except PermissionError:
                raise ValueError('An active descriptor cannot be inspected') from None
    return False


def _resource(profile, key, writer_probe):
    record = (profile.get('resources') or {}).get(key)
    if not isinstance(record, dict):
        raise ValueError('Unknown server-owned disposable resource ID')
    path = record.get('path', '')
    if _protected(path, record.get('classification'), profile):
        raise ValueError('Protected resources can never be quarantined as disposable')
    if type(record.get('retention_until')) is not int or record['retention_until'] > int(time.time()):
        raise ValueError('Disposable retention has not expired')
    root, name = _root_for(path, profile.get('disposable_roots', []))
    fd = _open_dir(root)
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(handle)
            expected = ('device', 'inode', 'uid', 'size', 'mtime_ns')
            actual = {
                'device': info.st_dev,
                'inode': info.st_ino,
                'uid': info.st_uid,
                'size': info.st_size,
                'mtime_ns': info.st_mtime_ns,
            }
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o022
                or info.st_uid != profile.get('expected_uid')
                or info.st_size > profile.get('max_cleanup_bytes', 2_000_000)
                or any(record.get(field) != actual[field] for field in expected)
            ):
                raise ValueError('Disposable file no longer matches its protected inventory identity')
            if writer_probe(info.st_dev, info.st_ino):
                raise ValueError('An active writer owns the disposable file')
            return {
                'id': key,
                'path': str(root / name),
                'classification': record['classification'],
                'retention_until': record['retention_until'],
                **actual,
            }
        finally:
            os.close(handle)
    finally:
        os.close(fd)


async def _inspect_preflight(action, scope, profile):
    if set(action['parameters']) - {'host_id', 'root_keys', 'services'}:
        raise ValueError('Inspection parameters cannot name commands, URLs or arbitrary paths')
    if action['parameters'].get('host_id', profile['host_id']) != profile['host_id']:
        raise ValueError('Requested host does not match server identity')
    roots = profile.get('roots') or {}
    keys = action['parameters'].get('root_keys', list(roots))
    services = action['parameters'].get('services', profile.get('services', []))
    if (
        not isinstance(keys, list)
        or len(keys) > 20
        or any(not isinstance(key, str) or key not in roots for key in keys)
        or not isinstance(services, list)
        or len(services) > 5
        or any(
            not isinstance(name, str) or name not in profile.get('services', []) or name not in HEALTH_ENDPOINTS
            for name in services
        )
    ):
        raise ValueError('Inspection exceeds server-approved roots or fixed services')
    return {'host_id': profile['host_id'], 'root_keys': keys, 'services': services, 'checked_at': int(time.time())}


async def _inspect(action, scope, preflight, profile):
    roots = {}
    for key in preflight['root_keys']:
        fd = _open_dir(profile['roots'][key])
        try:
            disk = os.fstatvfs(fd)
            info = os.fstat(fd)
            roots[key] = {
                'device': info.st_dev,
                'owner_uid': info.st_uid,
                'free_bytes': disk.f_bavail * disk.f_frsize,
                'total_bytes': disk.f_blocks * disk.f_frsize,
            }
        finally:
            os.close(fd)
    services = await _health(preflight['services'])
    return {
        'host_id': profile['host_id'],
        'roots': roots,
        'services': services,
        'observed_at': int(time.time()),
        'read_only': True,
    }


async def _health(names):
    if not names:
        return {}
    if not isinstance(names, list) or len(names) > 5 or any(name not in HEALTH_ENDPOINTS for name in names):
        raise ValueError('Health checks require fixed server service identities')
    import httpx

    services = {}
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        for name in names:
            try:
                response = await client.get(HEALTH_ENDPOINTS[name])
                try:
                    body = response.json() if len(response.content) <= 4096 else {}
                except ValueError:
                    body = {}
                flag = body.get('ready', body.get('status')) if isinstance(body, dict) else None
                healthy = response.status_code == 200 and (
                    flag is True or isinstance(flag, str) and flag.lower() in {'ok', 'ready', 'healthy'}
                )
                services[name] = {'http_status': response.status_code, 'healthy': healthy}
            except httpx.TransportError:
                services[name] = {'http_status': None, 'healthy': False}
    return services


def _change_preflight(profile):
    value = profile.get('change_preflight')
    if (
        not isinstance(value, dict)
        or value.get('host_id') != profile['host_id']
        or type(value.get('checked_at')) is not int
        or not 0 <= time.time() - value['checked_at'] <= 300
        or value.get('retention_policy_verified') is not True
    ):
        raise ValueError('A fresh server-attested backup/retention/service-impact preflight is required')
    for key, limit in [('backup_ref', 500), ('restore_plan', 6000), ('service_impact', 1000)]:
        if not isinstance(value.get(key), str) or not 0 < len(value[key]) <= limit:
            raise ValueError('Verified change preflight lacks ' + key)
    return {
        key: value[key]
        for key in (
            'host_id',
            'checked_at',
            'backup_ref',
            'restore_plan',
            'service_impact',
            'retention_policy_verified',
        )
    }


def cleanup_adapter(*, writer_probe=None):
    """Server may inject its privileged writer inventory; API cannot supply it."""
    probe = writer_probe or _proc_writer

    async def preflight(action, scope, profile):
        if set(action['parameters']) - {'host_id', 'resource_ids'}:
            raise ValueError('Cleanup parameters may select resource IDs only')
        if action['parameters'].get('host_id', profile['host_id']) != profile['host_id']:
            raise ValueError('Requested cleanup host differs')
        keys = action['parameters'].get('resource_ids')
        if (
            not isinstance(keys, list)
            or not 0 < len(keys) <= 20
            or any(not isinstance(key, str) or not RESOURCE_ID.fullmatch(key) for key in keys)
            or len(set(keys)) != len(keys)
        ):
            raise ValueError('Cleanup requires 1..20 unique server resource IDs')
        proof = _change_preflight(profile)
        services = await _health(profile.get('services', []))
        if any(not value['healthy'] for value in services.values()):
            raise ValueError('Affected services are not healthy before cleanup')
        quarantine = profile.get('quarantine_root')
        fd = _open_dir(quarantine)
        try:
            info = os.fstat(fd)
            if info.st_uid != profile.get('expected_uid') or info.st_mode & 0o077:
                raise ValueError('Quarantine root must be private and owned by the execution identity')
            disk = os.fstatvfs(fd)
            free = disk.f_bavail * disk.f_frsize
            if free < profile.get('minimum_free_bytes', 0):
                raise ValueError('Quarantine filesystem lacks its required safe capacity')
        finally:
            os.close(fd)
        return {
            'host_id': profile['host_id'],
            'checked_at': int(time.time()),
            'change_preflight': proof,
            'services_before': services,
            'free_bytes_before': free,
            'resources': [_resource(profile, key, probe) for key in keys],
        }

    async def execute(action, scope, before, profile):
        # Re-resolve every descriptor and writer immediately before any rename.
        if _change_preflight(profile) != before['change_preflight']:
            raise ValueError('Server change preflight changed before cleanup')
        current = [_resource(profile, item['id'], probe) for item in before['resources']]
        if current != before['resources']:
            raise ValueError('Cleanup inventory changed after preflight')
        root_fd = _open_dir(profile['quarantine_root'])
        name = 'action-' + _digest([scope['invocation_id'], action['action_id']])[:40]
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
            quarantine_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                manifest = {
                    'action_id': action['action_id'],
                    'invocation_id': scope['invocation_id'],
                    'resources': current,
                    'moved': [],
                    'state': 'prepared',
                    'change_preflight': before['change_preflight'],
                }
                _manifest(quarantine_fd, manifest)
                libc = ctypes.CDLL(None, use_errno=True)
                rename = getattr(libc, 'renameat2', None)
                if rename is None:
                    raise ValueError('Atomic no-replace quarantine is unavailable')
                for item in current:
                    source, name_in_source = _root_for(item['path'], profile['disposable_roots'])
                    source_fd = _open_dir(source)
                    try:
                        latest = _resource(profile, item['id'], probe)
                        info = os.stat(name_in_source, dir_fd=source_fd, follow_symlinks=False)
                        if latest != item or (info.st_dev, info.st_ino) != (item['device'], item['inode']):
                            raise ValueError('Disposable identity changed at the rename boundary')
                        if os.fstat(quarantine_fd).st_dev != item['device']:
                            raise ValueError('Quarantine must be on the same filesystem')
                        target = item['id'] + '.quarantined'
                        if rename(source_fd, os.fsencode(name_in_source), quarantine_fd, os.fsencode(target), 1) != 0:
                            code = ctypes.get_errno()
                            raise OSError(code, os.strerror(code))
                        os.fsync(source_fd)
                        os.fsync(quarantine_fd)
                        moved = {**item, 'quarantine_path': str(Path(profile['quarantine_root']) / name / target)}
                        manifest['moved'].append(moved)
                        _manifest(quarantine_fd, manifest)
                        if probe(item['device'], item['inode']):
                            restored = (
                                rename(quarantine_fd, os.fsencode(target), source_fd, os.fsencode(name_in_source), 1)
                                == 0
                            )
                            moved['restored'] = restored
                            manifest['state'] = (
                                'rolled_back_active_writer' if restored else 'recovery_required_active_writer'
                            )
                            os.fsync(source_fd)
                            os.fsync(quarantine_fd)
                            _manifest(quarantine_fd, manifest)
                            raise ValueError(
                                'Writer became active at quarantine boundary; original bytes retained and rollback recorded'
                            )
                    finally:
                        os.close(source_fd)
                manifest['services_after'] = await _health(profile.get('services', []))
                disk = os.fstatvfs(quarantine_fd)
                manifest['free_bytes_after'] = disk.f_bavail * disk.f_frsize
                manifest['state'] = 'quarantined'
                manifest['completed_at'] = int(time.time())
                _manifest(quarantine_fd, manifest)
                if any(not value['healthy'] for value in manifest['services_after'].values()):
                    raise ValueError('Postchange service health requires recovery review; bytes retained in quarantine')
                return {
                    'host_id': profile['host_id'],
                    'resources': manifest['moved'],
                    'manifest_path': str(Path(profile['quarantine_root']) / name / 'receipt.json'),
                    'change_preflight': before['change_preflight'],
                    'free_bytes_before': before['free_bytes_before'],
                    'free_bytes_after': manifest['free_bytes_after'],
                    'services_before': before['services_before'],
                    'services_after': manifest['services_after'],
                    'deleted': False,
                    'rollback': 'Restore exact quarantined inode through an approved recovery action',
                    'postcheck': 'Each source entry moved atomically; bytes retained in private quarantine',
                }
            finally:
                os.close(quarantine_fd)
        finally:
            os.close(root_fd)

    async def recover(action, scope, before, profile):
        directory = Path(profile['quarantine_root']) / (
            'action-' + _digest([scope['invocation_id'], action['action_id']])[:40]
        )
        try:
            fd = _open_dir(directory)
        except FileNotFoundError:
            return None
        try:
            source = os.open('receipt.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(source, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != profile['expected_uid'] or info.st_mode & 0o077:
                    raise ValueError('Quarantine receipt is not protected')
                content = handle.read(64_001)
            if len(content) > 64_000:
                raise ValueError('Quarantine receipt exceeds audit bound')
            manifest = json.loads(content)
            if (
                manifest.get('action_id') != action['action_id']
                or manifest.get('invocation_id') != scope['invocation_id']
            ):
                raise ValueError('Quarantine receipt identity changed')
            return {'partial_quarantine': manifest, 'manifest_path': str(directory / 'receipt.json'), 'deleted': False}
        finally:
            os.close(fd)

    return TrustedAdapter(preflight, execute, capability='execute_infrastructure', recover=recover)


def _manifest(directory, value):
    name = 'receipt-' + os.urandom(8).hex() + '.tmp'
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(_json(value).encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.rename(name, 'receipt.json', src_dir_fd=directory, dst_dir_fd=directory)
    os.fsync(directory)


async def _fresh(invocation, action, project_gate, config, bridge, profile, adapter, db_path):
    with _connect(db_path) as db:
        row = _owned(db, invocation)
    project, gate = await project_gate(row['erp_project'], config)
    if (
        not isinstance(gate, dict)
        or gate.get('enabled') is not True
        or gate.get('pm_task_automation_enabled') is not True
        or gate.get('assigned_user') != 'ai-projects@abitcons.com'
        or gate.get('project_status') not in {'Open', 'Active', 'In Progress'}
    ):
        raise ValueError('Project no longer authorizes infrastructure execution')
    stored = json.loads(row['work_packet'])
    current = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    fields = (
        'project_id',
        'task_id',
        'task_revision',
        'binding',
        'srs',
        'repository',
        'environment',
        'tool_scopes',
        'requirement_ids',
        'acceptance_tests',
        'dependencies',
        'instructions',
        'activity',
    )
    if any(stored.get(key) != current.get(key) for key in fields):
        raise ValueError('WorkPacket authority changed before tool execution')
    if row['target_role'] not in adapter.roles or adapter.capability not in current.get('tool_scopes', []):
        raise CapabilityUnavailable('The active ERP role has not granted this adapter capability')
    if action['operation'] not in profile.get('operations', []):
        raise CapabilityUnavailable('Operation is not enabled in the server gateway profile')
    if action['operation'] != 'inspect':
        classification = profile.get('environment_class')
        if classification not in {'development', 'test', 'staging', 'production'}:
            raise CapabilityUnavailable('Mutating adapters require explicit server-owned environment classification')
        if (
            re.search(r'(?i)(^|[^a-z])prod(?:uction)?([^a-z]|$)', str(current.get('environment', '')))
            and classification != 'production'
        ):
            raise ValueError('Server profile contradicts the production environment identity')
        if classification == 'production' and action.get('risk') == 'routine':
            raise ValueError('Production changes require privileged/sensitive risk and a current ERP approval')
    tool_action = stored.get('tool_action')
    if action['operation'] != 'inspect' and not isinstance(tool_action, dict):
        raise ValueError('A mutating action requires the immutable Task tool_action')
    if tool_action and any(action.get(key) != tool_action.get(key) for key in ('operation', 'risk', 'parameters')):
        raise ValueError('Action differs from the immutable Task operation and parameters')
    scope = {key: current.get(key) for key in ('project_id', 'task_id', 'task_revision', 'repository', 'environment')}
    scope.update(
        binding=current['binding'],
        operations=profile['operations'],
        invocation_id=invocation,
        registered_bindings={
            row['target_role']: {'agent_id': row['agent_id'], 'erp_user': current['binding']['erp_user']}
        },
    )
    return row, scope


async def _approval(bridge, row, action):
    from open_webui.utils.agentic_contracts.validator import action_fingerprint

    args = {
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'run_id': row['automation_run_id'],
        'invocation_id': row['invocation_id'],
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'action': action,
        'action_fingerprint': action_fingerprint(action),
    }
    response = await bridge.erp.request(
        'POST', RPC + 'get_action_approval', json=args, headers={'X-ABIT-Agentic-Bridge': bridge.secret}
    )
    return response.get('message'), args


async def execute_action(invocation_id, action, *, project_gate, config, bridge, adapters=None, db_path=None):
    from open_webui.utils.agentic_contracts.validator import authorize_action, schema_errors, action_fingerprint

    errors = schema_errors('action', action)
    if errors:
        raise ValueError('; '.join(errors)[:1200])
    ident = action['action_id']
    fingerprint = _digest([ident, action_fingerprint(action)])
    with _connect(db_path) as db:
        prior = db.execute(
            'SELECT invocation_id,request_hash FROM governance_action WHERE action_id=?', (ident,)
        ).fetchone()
        if prior:
            if tuple(prior) != (invocation_id, fingerprint):
                raise ValueError('Action idempotency key has different immutable content')
            return get_action(invocation_id, ident, db_path)
        row = _owned(db, invocation_id)
    profile = _profile(config, row['erp_project'])
    registry = {
        'inspect': TrustedAdapter(_inspect_preflight, _inspect, capability='inspect_infrastructure', read_only=True),
        'cleanup_disposable': cleanup_adapter(),
    }
    if adapters:
        if not isinstance(adapters, dict) or any(not isinstance(value, TrustedAdapter) for value in adapters.values()):
            raise ValueError('Only server-registered trusted adapters are accepted')
        registry.update(adapters)
    adapter = registry.get(action['operation'])
    if action['operation'] == 'inspect' and adapter is not None and not adapter.read_only:
        raise ValueError('Read-only inspection cannot be replaced with a mutating adapter')
    if adapter is None:
        raise CapabilityUnavailable('No trusted adapter is implemented for this operation')
    row, scope = await _fresh(invocation_id, action, project_gate, config, bridge, profile, adapter, db_path)
    errors = authorize_action(action, None, now=int(time.time()), scope=scope)
    needs_approval = 'Current ERP approval is required' in errors
    other_errors = [error for error in errors if error != 'Current ERP approval is required']
    if other_errors:
        raise ValueError('; '.join(other_errors)[:1200])
    approval = None
    approval_args = None
    if needs_approval:
        approval, approval_args = await _approval(bridge, row, action)
        errors = authorize_action(action, approval, now=int(time.time()), scope=scope)
        if 'Current ERP approval is required' in errors:
            raise ApprovalRequired(ident, action_fingerprint(action))
    if errors:
        raise ValueError('; '.join(errors)[:1200])
    before = await adapter.preflight(action, scope, profile)
    row, scope = await _fresh(invocation_id, action, project_gate, config, bridge, profile, adapter, db_path)
    with _connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        _owned(db, invocation_id)
        prior = db.execute(
            'SELECT invocation_id,request_hash FROM governance_action WHERE action_id=?', (ident,)
        ).fetchone()
        if prior:
            db.execute('COMMIT')
            if tuple(prior) != (invocation_id, fingerprint):
                raise ValueError('Concurrent action ID content conflict')
            return get_action(invocation_id, ident, db_path)
        now = int(time.time())
        db.execute(
            'INSERT INTO governance_action VALUES(?,?,?,?,?,?,NULL,NULL,?,?)',
            (ident, invocation_id, fingerprint, _json(action), _json(before), 'uncertain', now, now),
        )
        _event(db, ident, 'intent_fenced', {'invocation_id': invocation_id, 'fingerprint': fingerprint})
        db.execute('COMMIT')
    # A crash from this point never permits an automatic repeat, including the
    # gap between approval consumption and the first host operation.
    try:
        if needs_approval:
            args = {**approval_args, 'approval_id': approval['approval_id']}
            response = await bridge.erp.request(
                'POST', RPC + 'consume_action_approval', json=args, headers={'X-ABIT-Agentic-Bridge': bridge.secret}
            )
            consumed = response.get('message') or {}
            if (
                consumed.get('consumed') is not True
                or consumed.get('action_id') != ident
                or consumed.get('action_fingerprint') != approval_args['action_fingerprint']
                or consumed.get('approval_id') != approval['approval_id']
                or not consumed.get('receipt_id')
            ):
                raise ValueError('ERP did not confirm atomic approval consumption')
            with _connect(db_path) as db:
                db.execute('UPDATE governance_action SET approval_json=? WHERE action_id=?', (_json(consumed), ident))
                _event(db, ident, 'approval_consumed', {'receipt_id': consumed['receipt_id']})
        row, scope = await _fresh(invocation_id, action, project_gate, config, bridge, profile, adapter, db_path)
        result = await adapter.execute(action, scope, before, profile)
        if len(_json(result).encode()) > 64_000:
            raise ValueError('Adapter receipt exceeds the audit bound')
        with _connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(
                "UPDATE governance_action SET state='completed',result_json=?,updated_at=? WHERE action_id=? AND state='uncertain'",
                (_json(result), int(time.time()), ident),
            )
            _event(db, ident, 'completed', {'result_sha256': _digest(result)})
            db.execute('COMMIT')
    except BaseException:
        recovered = None
        if adapter.recover is not None:
            try:
                recovered = await adapter.recover(action, scope, before, profile)
            except Exception:
                pass  # Original uncertainty is preserved; never execute recovery writes.
        with _connect(db_path) as db:
            if recovered is not None and len(_json(recovered).encode()) <= 64_000:
                db.execute(
                    'UPDATE governance_action SET result_json=?,updated_at=? WHERE action_id=?',
                    (_json(recovered), int(time.time()), ident),
                )
            _event(
                db,
                ident,
                'outcome_uncertain',
                {'reason': 'Original adapter/approval outcome requires receipt reconciliation; no automatic replay'},
            )
        raise
    return get_action(invocation_id, ident, db_path)

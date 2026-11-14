"""Fixed gateway operations. No model-selected host, credential, command or root."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import tempfile
import shutil
import sys

from open_webui.utils.agentic_artifacts import artifact_directory, ensure_directory, local_bytes
from open_webui.utils.agentic_bridge import canonical, digest
from open_webui.utils.agentic_gateway_sessions import Operation
from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable, _open_dir

ROLES = frozenset({'solution_architect', 'project_manager', 'developer', 'qa', 'operations', 'it_manager'})
PLANNING_ROLES = frozenset({'solution_architect', 'project_manager'})
CAPABILITIES = {
    'solution_architect': ('read_srs', 'propose_tasks', 'submit_designs'),
    'project_manager': ('read_project', 'plan_dependencies', 'request_agent_invocation', 'propose_tasks'),
    'developer': ('read_project', 'execute_development', 'request_qa', 'request_it_task'),
    'qa': ('read_project', 'review_evidence', 'request_rework'),
    'operations': ('read_project', 'run_operational_work', 'request_qa', 'request_it_task'),
    'it_manager': ('inspect_infrastructure', 'propose_it_change', 'request_approval', 'execute_infrastructure'),
}
MAX_BYTES = 196_608  # base64 plus identities/receipt must fit the 300k edge envelope
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}')
COMMIT = re.compile(r'[a-f0-9]{40}')
PRIVATE = re.compile(r'(?i)(^|[_.-])(secret|secrets|credential|credentials|token|password)([_.-]|$)')


def _private_path_part(value):
    """Deny dotenv/shell environment variants at every workspace boundary."""
    name = value.casefold()
    return (
        name in {'.env', '.envrc', '.npmrc', '.pypirc', '.netrc'}
        or name.endswith('.env')
        or '.env.' in name
        or name.startswith('.envrc.')
        or PRIVATE.search(value)
    )


def exact(args, keys):
    if not isinstance(args, dict) or set(args) != set(keys):
        raise ValueError('Unsupported gateway arguments')


def _decode(value):
    if not isinstance(value, str) or len(value) > (MAX_BYTES + 2) // 3 * 4:
        raise ValueError('Gateway content exceeds its bounded size')
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError('Gateway content must be canonical base64') from None
    if len(data) > MAX_BYTES or base64.b64encode(data).decode() != value:
        raise ValueError('Gateway content exceeds its bounded size or is not canonical')
    return data


async def context(args, *, row, packet, config, **kwargs):
    exact(args, ())
    from open_webui.utils.agentic_gateway_contracts import model_contracts

    # This is the immutable packet whose current authority was just rechecked.
    examples = {
        'context.read': {},
        'artifact.read': {'name': 'evidence.txt', 'offset': 0},
        'artifact.write': {'name': 'evidence.txt', 'content_base64': '<actual base64 bytes>'},
        'repository.prepare': {},
        'repository.mkdir': {'path': '<approved relative directory>'},
        'repository.read': {'path': '<approved relative file>'},
        'repository.write': {
            'path': '<approved relative file>',
            'expected_sha256': '<prior sha256 or null for new>',
            'content_base64': '<actual base64 bytes>',
        },
        'repository.test': {'test_id': '<registered test id>'},
        'repository.deliver': {'test_call_ids': ['<actual successful test call_id>']},
        'repository.delivery_status': {},
        'repository.verify_delivery': {},
        'infrastructure.verify_original': {},
        'attachment.read': {'file_id': '<declared private File ID>', 'offset': 0},
        'attachment.present': {'file_id': '<declared private File ID>', 'page': 1, 'text_offset': 0},
    }
    available = registry(row['target_role'])
    catalog = {
        name: {'arguments': examples.get(name, examples['artifact.write']), 'read_only': operation.read_only}
        for name, operation in available.items()
    }
    profile = (config.get('agentic_repository_gateway') or {}).get('projects', {}).get(row['erp_project']) or {}
    repository = {
        key: profile.get(key)
        for key in ('repository', 'environment', 'allowed_paths', 'read_paths', 'test_paths', 'test_acceptance_ids')
    }
    repository['test_ids'] = sorted((profile.get('tests') or {}).keys())
    return {
        **model_contracts(row['target_role']),
        'invocation_id': row['invocation_id'],
        'work_packet': packet,
        'operations': catalog,
        'repository_profile': repository,
        'workflow': 'Planning roles may prepare/read the approved base through read_paths; repository tests, writes and delivery are not granted to them. Developer: prepare the approved base, change only allowed_paths, run registered tests and retain call IDs; repository.deliver returns the exact delivery/evidence/test_results for AGOV RESULT. QA: verify the implementation delivery, prepare its exact head, run independent tests, verify again, and copy only actual receipt evidence. ERP task/agent creation and approvals remain server-coordinated from validated final results.',
    }


async def artifact_read(args, *, row, config, **kwargs):
    if set(args) not in ({'name'}, {'name', 'offset'}):
        raise ValueError('Unsupported evidence read arguments')
    offset = args.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 2_000_000:
        raise ValueError('Invalid evidence byte offset')
    if not isinstance(args['name'], str) or not NAME.fullmatch(args['name']):
        raise ValueError('Invalid run-owned evidence filename')
    name, data = local_bytes(artifact_directory(row, config), 'artifact://' + args['name'])
    return {
        'uri': 'artifact://' + name,
        'sha256': hashlib.sha256(data).hexdigest(),
        'bytes': len(data),
        'offset': offset,
        'next_offset': offset + MAX_BYTES if offset + MAX_BYTES < len(data) else None,
        'content_base64': base64.b64encode(data[offset : offset + MAX_BYTES]).decode(),
    }


async def artifact_write(args, *, row, config, **kwargs):
    exact(args, ('name', 'content_base64'))
    return _store_artifact(args['name'], _decode(args['content_base64']), row=row, config=config)


def _store_artifact(name, data, *, row, config):
    """Server-created logs can use the publisher's full2MB artifact bound."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('Invalid run-owned evidence filename')
    from open_webui.utils.agentic_artifacts import EXTENSIONS

    if Path(name).suffix.lower() not in EXTENSIONS:
        raise ValueError('Unsupported evidence file type')
    from open_webui.utils.agentic_artifacts import validate_bytes

    validate_bytes(name, data)
    root = artifact_directory(row, config)
    ensure_directory(root)
    fd = _open_dir(root)
    try:
        try:
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
        except FileExistsError:
            _, prior = local_bytes(root, 'artifact://' + name)
            if prior != data:
                raise ValueError('Original evidence is immutable; choose a new evidence identity')
        else:
            try:
                with os.fdopen(out, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.fsync(fd)
        # Reuse the exact publication validator (SVG/external content included).
        _, actual = local_bytes(root, 'artifact://' + name)
        if actual != data:
            raise ValueError('Gateway evidence bytes did not verify')
    finally:
        os.close(fd)
    return {'uri': 'artifact://' + name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def _repository_view(row, profile):
    """Planning reads use separate server paths, never Developer write grants."""
    if row['target_role'] not in PLANNING_ROLES:
        if row['target_role'] not in {'developer', 'qa'}:
            raise CapabilityUnavailable('This role has no repository workspace capability')
        return profile
    paths = profile.get('read_paths')
    if not isinstance(paths, list) or not paths or len(paths) > 100 or any(not isinstance(p, str) for p in paths):
        raise CapabilityUnavailable('Planning repository reads require explicit bounded server read paths')
    if len(set(paths)) != len(paths):
        raise ValueError('Planning repository read paths must be unique')
    view = {**profile, 'allowed_paths': paths, 'test_paths': []}
    for path in paths:
        if path == '.':
            raise ValueError('Planning repository read paths must name explicit source paths')
        _path({'path': path}, view)
    return view


def _workspace(row, packet, config):
    profile = (config.get('agentic_repository_gateway') or {}).get('projects', {}).get(row['erp_project'])
    if (
        not isinstance(profile, dict)
        or profile.get('repository') != packet['repository']
        or profile.get('environment') != packet['environment']
    ):
        raise CapabilityUnavailable('No server-owned repository/environment profile is registered')
    base = Path(profile.get('workspace_root', ''))
    if not base.is_absolute() or not str(base).startswith('/srv/abit-dev-linux/') or '..' in base.parts:
        raise CapabilityUnavailable('Invalid server-owned repository gateway root')
    root = base / row['erp_task'] / row['invocation_id'] / 'repository'
    fd = _open_dir(root)
    os.close(fd)
    # This hash pins trusted operator-created Git configuration, not source files
    # that a developer is expected to edit. The model cannot write .git through us.
    git = root / '.git'
    git_fd = _open_dir(git)
    try:
        cfg = os.open('config', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=git_fd)
        try:
            metadata = os.fstat(cfg)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 32_768:
                raise ValueError('Repository configuration is not a bounded regular file')
            content = os.read(cfg, 32_769)
        finally:
            os.close(cfg)
    finally:
        os.close(git_fd)
    planning = row['target_role'] in PLANNING_ROLES
    expected = (profile.get('config_sha256_by_invocation') or {}).get(row['invocation_id'])
    original = None
    if expected is None or planning:
        from open_webui.utils.agentic_gateway_delivery import workspace_receipt
        from open_webui.utils.agentic_gateway_sessions import CURRENT_DB

        original = workspace_receipt(row, packet, config, db_path=CURRENT_DB.get(), include_source_manifest=planning)
        if original['workspace_root'] != str(root):
            raise ValueError('Prepared workspace root differs from the approved original run')
        expected = original['config_sha256']
    if expected != hashlib.sha256(content).hexdigest():
        raise CapabilityUnavailable('Repository configuration changed or has not been approved for this original run')
    view = _repository_view(row, profile)
    if planning:
        view = {**view, '_planning_source': original}
    return root, view


def _path(args, profile):
    value = args.get('path')
    if not isinstance(value, str) or len(value) > 400 or '\\' in value:
        raise ValueError('Invalid repository relative path')
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(p in {'', '.', '..', '.git', '.ssh', '.codex'} for p in path.parts)
    ):
        raise ValueError('Repository path escapes the original approved working copy')
    if any(_private_path_part(p) for p in path.parts):
        raise ValueError('Credential-bearing repository paths are outside the gateway')
    allowed = profile.get('allowed_paths')
    if not isinstance(allowed, list) or not any(
        value == x or value.startswith(x.rstrip('/') + '/') for x in allowed if isinstance(x, str) and x
    ):
        raise ValueError('Repository path is outside the approved change paths')
    return path


def _parent(root, path):
    fd = _open_dir(root)
    try:
        for part in path.parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name):
    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(f)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('Repository file is not a bounded regular file')
        data = os.read(f, MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('Repository file exceeds size limit')
        return data, info
    finally:
        os.close(f)


async def repository_read(args, *, row, packet, config, **kwargs):
    exact(args, ('path',))
    root, profile = _workspace(row, packet, config)
    path = _path(args, profile)
    fd = _parent(root, path)
    try:
        data, _ = _read(fd, path.name)
    finally:
        os.close(fd)
    result = {
        'path': str(path),
        'sha256': hashlib.sha256(data).hexdigest(),
        'content_base64': base64.b64encode(data).decode(),
    }
    if row['target_role'] in PLANNING_ROLES:
        source = profile.get('_planning_source') or {}
        expected = next((item for item in source.get('_source_manifest', []) if item['path'] == str(path)), None)
        if not expected or expected['sha256'] != result['sha256'] or expected['bytes'] != len(data):
            raise ValueError('Planning read bytes differ from the approved original repository snapshot')
        result.update(source_commit=source['commit'], repository_snapshot_sha256=source['snapshot_sha256'])
    return result


async def repository_write(args, *, row, packet, config, **kwargs):
    exact(args, ('path', 'expected_sha256', 'content_base64'))
    root, profile = _workspace(row, packet, config)
    path, data = _path(args, profile), _decode(args['content_base64'])
    expected = args['expected_sha256']
    if expected is not None and (not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected)):
        raise ValueError('A valid prior file fingerprint or null for a new file is required')
    fd = _parent(root, path)
    temporary = '.agov-' + hashlib.sha256(os.urandom(32)).hexdigest()
    try:
        try:
            prior, info = _read(fd, path.name)
        except FileNotFoundError:
            if expected is not None:
                raise ValueError('Expected repository file does not exist') from None
            info = None
        else:
            if expected != hashlib.sha256(prior).hexdigest() or info.st_nlink != 1:
                raise ValueError('Repository file precondition changed or has multiple hard links')
        f = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
        with os.fdopen(f, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # The shared gateway lock serializes our operations; an unrelated owner
        # change is detected again immediately before replacing the target.
        try:
            check, check_info = _read(fd, path.name)
        except FileNotFoundError:
            if info is not None:
                raise ValueError('Repository file disappeared before replacement') from None
        else:
            if info is None or check_info.st_ino != info.st_ino or check != prior:
                raise ValueError('Repository file changed before replacement')
        if info is None:
            # Atomic no-replace: never overwrite a concurrently created file.
            os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=fd)
        else:
            os.rename(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.close(fd)
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


async def repository_mkdir(args, *, row, packet, config, **kwargs):
    exact(args, ('path',))
    root, profile = _workspace(row, packet, config)
    path = _path(args, profile)
    fd = _open_dir(root)
    try:
        for part in path.parts:
            try:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode=0o750, dir_fd=fd)
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        os.fsync(fd)
    finally:
        os.close(fd)
    return {'path': str(path), 'created_or_reused': True}


async def bounded_process(argv, cwd, *, seconds=30, join_budget=None):
    """Fixed server argv only, no inherited credentials, bounded concurrent pipes."""
    env = {
        'PATH': '/usr/bin:/bin',
        'LANG': 'C.UTF-8',
        'HOME': '/nonexistent',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_TERMINAL_PROMPT': '0',
    }
    if join_budget is not None:
        launcher = "import os,sys;f=os.open(sys.argv[1],os.O_WRONLY);os.write(f,str(os.getpid()).encode());os.close(f);os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
        argv = [sys.executable, '-I', '-S', '-c', launcher, join_budget, *argv]
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def read(stream):
        value = bytearray()
        while True:
            chunk = await stream.read(16_384)
            if not chunk:
                return bytes(value)
            value.extend(chunk)
            if len(value) > 32_768:
                raise ValueError('Gateway command output exceeded its limit')

    try:
        stdout, stderr, code = await asyncio.wait_for(
            asyncio.gather(read(process.stdout), read(process.stderr), process.wait()), seconds
        )
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    return {
        'exit_code': code,
        'stdout': stdout.decode('utf-8', 'replace'),
        'stderr': stderr.decode('utf-8', 'replace'),
    }


def _test_snapshot(root, profile):
    """Copy only approved test inputs; Git/config/credentials are never mounted."""
    paths = profile.get('test_paths')
    if not isinstance(paths, list) or not paths or len(paths) > 100:
        raise CapabilityUnavailable('Repository tests require explicit sanitized input paths')
    target = Path(tempfile.mkdtemp(prefix='gateway-test-', dir=root.parent))
    manifest = []
    total = 0

    def copy(relative):
        nonlocal total
        path = _path({'path': relative}, {'allowed_paths': paths})
        parent = _parent(root, path)
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Test inputs cannot contain symlinks')
            if stat.S_ISDIR(info.st_mode):
                directory = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    children = os.listdir(directory)
                finally:
                    os.close(directory)
                if len(children) > 2000:
                    raise ValueError('Test input directory exceeds its bound')
                (target / path).mkdir(parents=True, exist_ok=True, mode=0o700)
                for child in sorted(children):
                    if child in {'.git', '.ssh', '.codex'} or _private_path_part(child):
                        continue
                    copy(str(path / child))
                return
            data, info = _read(parent, path.name)
            if info.st_nlink != 1:
                raise ValueError('Test inputs cannot contain hard-linked files')
        finally:
            os.close(parent)
        total += len(data)
        if len(manifest) >= 2000 or total > 64_000_000:
            raise ValueError('Sanitized test input snapshot exceeds its bound')
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open('xb') as output:
            output.write(data)
        destination.chmod(0o700 if info.st_mode & 0o111 else 0o600)
        manifest.append({'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()})

    try:
        for value in paths:
            copy(value)
        return target, sorted(manifest, key=lambda item: item['path'])
    except BaseException:
        shutil.rmtree(target)
        raise


async def repository_test(args, *, row, packet, config, gateway_call_id=None, **kwargs):
    exact(args, ('test_id',))
    root, profile = _workspace(row, packet, config)
    command = (profile.get('tests') or {}).get(args['test_id']) if isinstance(args['test_id'], str) else None
    if (
        not isinstance(command, list)
        or not command
        or len(command) > 30
        or any(not isinstance(x, str) or len(x) > 500 or '\x00' in x for x in command)
    ):
        raise CapabilityUnavailable('Only server-registered acceptance test commands may run')
    binary = Path('/usr/bin/bwrap')
    if not binary.is_file():
        raise CapabilityUnavailable('The approved repository test sandbox is not installed')
    # Tests may execute repository code, so an argv allowlist alone is insufficient.
    # Expose only OS runtime and a sanitized copy of approved original inputs.
    from open_webui.utils.agentic_gateway_delivery import snapshot_workspace

    before = snapshot_workspace(row, packet, config)['sha256']
    snapshot, manifest = _test_snapshot(root, profile)
    argv = [
        '/usr/bin/prlimit',
        '--cpu=25',
        '--as=1073741824',
        '--nofile=128',
        '--fsize=16777216',
        '--',
        str(binary),
        '--unshare-all',
        '--die-with-parent',
        '--new-session',
        '--clearenv',
        '--setenv',
        'PATH',
        '/usr/bin:/bin',
        '--setenv',
        'LANG',
        'C.UTF-8',
        '--setenv',
        'HOME',
        '/tmp',
        '--tmpfs',
        '/',
        '--proc',
        '/proc',
        '--dev',
        '/dev',
        '--tmpfs',
        '/tmp',
    ]
    for path in ('/usr', '/bin', '/lib', '/lib64'):
        if Path(path).exists():
            argv += ['--ro-bind', path, path]
    argv += ['--bind', str(snapshot), '/workspace', '--chdir', '/workspace', '--', *command]
    try:
        if snapshot_workspace(row, packet, config)['sha256'] != before:
            raise ValueError('Repository source changed while preparing its sanitized test inputs')
        from open_webui.utils.agentic_sandbox_budget import budget

        async with budget() as join:
            result = await bounded_process(argv, root, join_budget=join)
    finally:
        shutil.rmtree(snapshot)
    result['test_id'] = args['test_id']
    if snapshot_workspace(row, packet, config)['sha256'] != before:
        raise ValueError('Repository source changed during testing; these tests cannot approve newer code')
    result['repository_snapshot_sha256'] = before
    result['command'] = canonical(command)
    result['sandbox'] = 'bubblewrap-no-network-sanitized-copy'
    result['input_manifest_sha256'] = digest(manifest)
    result['input_file_count'] = len(manifest)
    for key in ('stdout', 'stderr'):
        result[key + '_sha256'] = hashlib.sha256(result[key].encode()).hexdigest()
    # The gateway, rather than a model narrative, supplies the actual test log.
    name = 'test-' + digest(gateway_call_id or args['test_id'])[:40] + '.txt'
    artifact = _store_artifact(name, canonical(result).encode(), row=row, config=config)
    result['output_preview_truncated'] = any(len(result[key]) > 4096 for key in ('stdout', 'stderr'))
    for key in ('stdout', 'stderr'):
        result[key] = result[key][:4096]
    result['evidence'] = artifact
    return result


def registry(role):
    """Capabilities include declarative intents; final coordinator APIs enforce them.

    The gateway cannot directly create Tasks, delegate agents or approve an
    action. Those intents still pass the existing signed result/ERP coordinator.
    """
    if role not in CAPABILITIES:
        raise CapabilityUnavailable('Unsupported registered gateway role')
    caps = CAPABILITIES[role]
    read = (
        'read_srs'
        if role == 'solution_architect'
        else 'inspect_infrastructure' if role == 'it_manager' else 'read_project'
    )
    evidence = {
        'solution_architect': 'submit_designs',
        'project_manager': 'plan_dependencies',
        'developer': 'execute_development',
        'qa': 'review_evidence',
        'operations': 'run_operational_work',
        'it_manager': 'propose_it_change',
    }[role]
    operations = {
        'context.read': Operation(read, frozenset({role}), context),
        'artifact.read': Operation(evidence, frozenset({role}), artifact_read),
        'artifact.write': Operation(evidence, frozenset({role}), artifact_write, False),
    }
    from open_webui.utils import agentic_gateway_attachments as attachments

    operations['attachment.read'] = Operation(
        read, frozenset({role}), attachments.read, requires_authority_callback=True
    )
    operations['attachment.present'] = Operation(
        read, frozenset({role}), attachments.present, requires_authority_callback=True
    )
    if role in {'solution_architect', 'project_manager', 'developer', 'qa'}:
        from open_webui.utils.agentic_gateway_delivery import prepare_workspace

        operations['repository.prepare'] = Operation(evidence, frozenset({role}), prepare_workspace, False)
        operations['repository.read'] = Operation(evidence, frozenset({role}), repository_read)
    if role in {'developer', 'qa'}:
        operations['repository.test'] = Operation(evidence, frozenset({role}), repository_test, False)
    if role == 'developer':
        operations['repository.mkdir'] = Operation(evidence, frozenset({'developer'}), repository_mkdir, False)
        operations['repository.write'] = Operation(evidence, frozenset({'developer'}), repository_write, False)
        from open_webui.utils.agentic_gateway_delivery import developer_deliver, reconcile_delivery

        operations['repository.deliver'] = Operation(evidence, frozenset({'developer'}), developer_deliver, False)
        operations['repository.delivery_status'] = Operation(evidence, frozenset({'developer'}), reconcile_delivery)
    if role == 'qa':
        from open_webui.utils.agentic_gateway_delivery import qa_verify_delivery

        operations['repository.verify_delivery'] = Operation(evidence, frozenset({'qa'}), qa_verify_delivery)
        from open_webui.utils.agentic_it_qa import verify_original

        operations['infrastructure.verify_original'] = Operation(
            'review_evidence',
            frozenset({'qa'}),
            verify_original,
            requires_authority_callback=True,
        )
    # These are draft intent records, never direct action/approval/Task authority.
    for capability in caps:
        if capability not in {v.capability for v in operations.values()}:
            operations['intent.' + capability] = Operation(capability, frozenset({role}), _intent, False)
    return operations


async def _intent(args, *, row, config, **kwargs):
    exact(args, ('name', 'content_base64'))
    return await artifact_write(args, row=row, config=config)

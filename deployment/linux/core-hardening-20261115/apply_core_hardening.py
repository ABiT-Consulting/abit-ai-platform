#!/usr/bin/env python3
"""Exact Core release preparation. UNFROZEN inputs deny all lifecycle actions."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import types

REPO = Path('/srv/abit-dev-linux/abit-ai-platform')
ROOT = Path('/srv/abit-dev-linux/abit-ai')
DATA = ROOT / 'data'
RUNTIME = ROOT / 'releases/20261031-live-0.11.4/runtime/open_webui'
S = ROOT / 'deployment/agentic-governance-full-20261113'
HERE = REPO / 'deployment/linux/core-hardening-20261115'
PLAN = HERE / 'frozen-release.json'
PLAN_SHA = 'UNFROZEN'
PARENT = Path('/var/lib/abit-ai-erp-rollouts/core-hardening-increments')
MAIN, QA = 'abit-ai.service', 'abit-ai-erp-review-qa.service'
TARGETS = frozenset({
    'utils/agentic_gateway_sessions.py', 'utils/agentic_gateway_operations.py',
    'utils/agentic_executor.py', 'routers/agentic_governance.py',
    'utils/agentic_result_provenance.py', 'utils/agentic_gateway_contracts.py',
    'utils/agentic_gateway_delivery.py', 'utils/agentic_orchestrator.py',
    'utils/agentic_delivery_continuation.py', 'utils/agentic_delivery_control.py',
    'utils/agentic_start_recovery.py',
})
NEW_FILES = frozenset({'utils/agentic_delivery_continuation.py', 'utils/agentic_delivery_control.py'})
NOKILL_BYTES = b'[Service]\nSendSIGKILL=no\n'
NOKILL = {unit: Path('/run/systemd/system') / (unit + '.d') /
          'zzzy-agov-core-no-force-kill.conf' for unit in (MAIN, QA)}
QA_FENCE = Path('/run/systemd/system/abit-ai-erp-review-qa.service.d/zzzz-agov-core-start-fence.conf')
QA_FENCE_BYTES = b'[Unit]\nRefuseManualStart=yes\nConditionPathExists=!/run/abit-ai-agentic-release.hold\n'
RELAY_HELPER = REPO / 'deployment/linux/relay-feed-hardening-20261115/apply_relay_feed.py'
RELAY_HELPER_SHA = 'e12ba354be54848fa7930d8423c7c66064dbf8bb364a7902d3ff778f59c2ed20'
MAINTENANCE = ROOT / 'deployment/agentic-governance-live-20261113/source/backend/open_webui/utils/agentic_maintenance.py'
MAINTENANCE_SHA = '7bacd6aae0ef7078e675f789f4d531dd10ec3393f60fb4a42d801c12512918b3'
PROCESS_NAMES = frozenset({'main', 'qa', 'node', 'chrome', 'watchdog', 'xvfb',
                           'egress', 'tunnel', 'mcp_auth', 'mcp_backend', 'mcp_tunnel'})

HELPERS = {
    'common': ('core-profile-prompt-gateway-increment-v4-architecture71-20261115/apply_profiles.template.py',
               'afb995efbdbda0479d1a72ad87e64e156bf7878167a81a3d4c9ed52bc864db44'),
    'history': ('core-profile-prompt-gateway-increment-v4-architecture71-20261115/history_guard.py',
                '1d1804e48c1a23fe6f86ee796e5756bd1c16a44376848e0a83788d79002b2052'),
    'lifecycle': ('core-8c-increment-proposal/apply_increment_v2.py',
                  'de71c225d025bfdfdcd3edb2e6b490ff3b4f01162e1dddc4e2414fc672e3a094'),
    'cas': ('core-typed-increment-proposal/apply_typed_increment.py',
            '42115b40228ecad434b86f4f19e736811387560a66f4078b49136ccdfe9952f1'),
    'base': ('release-proposal/apply_core_overlay.py',
             '64c611b03581593d27b5f954f97abd737cf05190dd273c41dd287b788493af73'),
    'finish': ('release-proposal/finish_release_hold.py',
               '28a155b2cc5d5646bb04d8678a02bd4770325ba8a8b67002a406db21e5d1c7fb'),
    'hold': ('release-proposal/prepare_release_hold.py',
             'd63d5c4bce65432def99529c378384007dc346c7de3d823ab815d0e3bd7c3740'),
}
SYSTEMD_DYNAMIC_ENV = frozenset({b'INVOCATION_ID', b'JOURNAL_STREAM', b'SYSTEMD_EXEC_PID'})
LIMIT = 2_000_000


def need(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def read(path, pin=None, private=False):
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_INPUT_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= LIMIT,
             'BOUNDED_SINGLE_LINK_INPUT_REQUIRED')
        need(not private or (before.st_uid == before.st_gid == 0
             and stat.S_IMODE(before.st_mode) == 0o600), 'ROOT_PRIVATE_INPUT_REQUIRED')
        raw = bytearray()
        while len(raw) <= LIMIT:
            part = os.read(fd, min(65536, LIMIT + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        need(len(raw) == before.st_size and all(getattr(before, key) == getattr(after, key)
             for key in ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
                         'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'INPUT_CHANGED_DURING_READ')
        need(pin is None or sha(raw) == pin, 'PINNED_INPUT_CHANGED')
        return bytes(raw)
    finally:
        os.close(fd)


def xattrs(path):
    return {key: os.getxattr(path, key, follow_symlinks=False).hex()
            for key in sorted(os.listxattr(path, follow_symlinks=False))}


def file_proof(path):
    raw = read(path)
    info = path.lstat()
    return {'sha256': sha(raw), 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode), 'size': len(raw),
            'mtime_ns': info.st_mtime_ns, 'xattrs': xattrs(path)}


def parent_proof(path):
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_PARENT_REQUIRED')
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode), 'DIRECTORY_PARENT_REQUIRED')
    return {'dev': info.st_dev, 'ino': info.st_ino, 'uid': info.st_uid,
            'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode), 'xattrs': xattrs(path)}


def module(name, path, pin):
    value = types.ModuleType('core_release_' + name)
    value.__file__ = str(path)
    sys.modules[value.__name__] = value
    exec(compile(read(path, pin), str(path), 'exec'), value.__dict__)
    return value


def exact_hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def validate_plan(value):
    need(value.get('schema_version') == 1 and value.get('state') == 'FROZEN_TESTED_CORE_INCREMENT'
         and re.fullmatch('[a-f0-9]{40}', value.get('source_commit', '')),
         'FROZEN_REVIEWED_SOURCE_REQUIRED')
    package = Path(value.get('package', ''))
    need(package.parent == REPO / '.git'
         and re.fullmatch('combined-core-hardening-20261115-[a-z0-9_]+', package.name),
         'EXACT_PRIVATE_PACKAGE_REQUIRED')
    need(set(value.get('target_sha256', {})) == TARGETS
         and all(exact_hash(v) for v in value['target_sha256'].values())
         and set(value.get('new_files', [])) == NEW_FILES, 'EXACT_ELEVEN_TARGET_SCOPE_REQUIRED')
    need(all(exact_hash(value.get(k)) for k in
         ('manifest_sha256', 'runtime_snapshot_sha256', 'tests_sha256', 'live_baseline_sha256')),
         'FROZEN_PACKAGE_HASHES_REQUIRED')
    need(type(value.get('test_count')) is int and value['test_count'] > 0,
         'EXACT_TEST_COUNT_REQUIRED')
    baseline = Path(value.get('live_baseline', ''))
    need(baseline.parent == REPO / '.git' and baseline.suffix == '.json',
         'EXACT_LIVE_BASELINE_REQUIRED')
    gate = value.get('initial_gate')
    need(isinstance(gate, dict) and set(gate) == {'state', 'epoch', 'active_admissions'}
         and gate['state'] in {'draining', 'sealed'} and type(gate['epoch']) is int
         and gate['epoch'] >= 20 and type(gate['active_admissions']) is int
         and gate['active_admissions'] == 0, 'EXACT_ZERO_WORK_BLOCKED_GATE_REQUIRED')
    return value


def validate_manifest(value, plan):
    files = value.get('files')
    need(value.get('source_commit') == plan['source_commit']
         and value.get('status') == 'OFFLINE_VERIFIED_PENDING_ROOT_RELEASE_GATES'
         and value.get('live_runtime') == str(RUNTIME)
         and isinstance(files, list) and len(files) == len(TARGETS)
         and {v.get('relative_path') for v in files} == TARGETS
         and value.get('replacement_count') == len(TARGETS)
         and value.get('new_file_count') == len(NEW_FILES), 'EXACT_RUNTIME_MANIFEST_REQUIRED')
    for item in files:
        name = item['relative_path']
        need(item.get('live_path') == str(RUNTIME / name)
             and item.get('candidate_sha256') == plan['target_sha256'][name]
             and item.get('replacement_required') is True, 'PINNED_TARGET_REQUIRED')
        new = name in NEW_FILES
        need(item.get('new_file') is new, 'EXACT_ABSENT_FILE_CLASSIFICATION_REQUIRED')
        if new:
            need(item.get('original_sha256') is None and item.get('live_metadata') is None,
                 'NEW_FILE_MUST_HAVE_PROVED_ABSENCE')
            meta = item.get('new_metadata', {})
        else:
            need(exact_hash(item.get('original_sha256')), 'EXACT_EXISTING_ORIGINAL_REQUIRED')
            meta = item.get('live_metadata', {})
        need(meta.get('uid') == 999 and meta.get('gid') == 988 and meta.get('mode') == 0o644
             and type(meta.get('mtime_ns')) is int and isinstance(meta.get('xattrs'), dict),
             'EXACT_SERVICE_SOURCE_METADATA_REQUIRED')
        parent = item.get('parent_metadata')
        need(isinstance(parent, dict) and set(parent) == {'dev', 'ino', 'uid', 'gid', 'mode', 'xattrs'}
             and parent['uid'] == 999 and parent['gid'] == 988, 'PINNED_SOURCE_PARENT_REQUIRED')
    return value


HISTORY_ROOT = Path('/var/lib/abit-ai-agentic-gateway/delivery-recovery-history')
HISTORY_AUTHORIZATION_SHA = 'e646b0506c34c59d82c327f69e289a7c9c11cc91c9a4728d163f402bda5351f7'


def validate_history_archive(baseline):
    value = baseline.get('delivery_history_archive')
    need(isinstance(value, dict) and set(value) ==
         {'schema_version', 'authorization_sha256', 'directory', 'parent_metadata', 'files'}
         and value['schema_version'] == 1
         and value['authorization_sha256'] == HISTORY_AUTHORIZATION_SHA
         and value['directory'] == str(HISTORY_ROOT), 'EXACT_REVIEWED_HISTORY_ARCHIVE_REQUIRED')
    parent = value['parent_metadata']
    need(isinstance(parent, dict)
         and set(parent) == {'dev', 'ino', 'uid', 'gid', 'mode', 'xattrs'}
         and parent['uid'] == 0 and parent['gid'] == 988 and parent['mode'] == 0o750,
         'ROOT_PROTECTED_HISTORY_PARENT_REQUIRED')
    files = value['files']
    index = str(HISTORY_ROOT / (HISTORY_AUTHORIZATION_SHA + '.history.json'))
    operator = str(HISTORY_ROOT / (HISTORY_AUTHORIZATION_SHA + '.bin'))
    need(isinstance(files, dict) and len(files) == 4 and index in files and operator in files,
         'EXACT_FOUR_HISTORY_INPUTS_REQUIRED')
    for name, proof in files.items():
        path = Path(name)
        is_index = name == index
        need(path.parent == HISTORY_ROOT
             and (is_index or re.fullmatch('[a-f0-9]{64}\\.bin', path.name)),
             'UNREVIEWED_HISTORY_INPUT_PATH')
        need(isinstance(proof, dict) and set(proof) ==
             {'sha256', 'uid', 'gid', 'mode', 'size', 'mtime_ns', 'xattrs'}
             and exact_hash(proof['sha256'])
             and proof['uid'] == 0 and proof['gid'] == 988 and proof['mode'] == 0o640
             and type(proof['size']) is int and 0 < proof['size'] <= (65536 if is_index else 262144)
             and type(proof['mtime_ns']) is int and proof['mtime_ns'] >= 0
             and isinstance(proof['xattrs'], dict)
             and (is_index or path.stem == proof['sha256']), 'EXACT_PROTECTED_HISTORY_FILE_REQUIRED')
        need(baseline.get('protected_configuration', {}).get(name) == proof,
             'HISTORY_INPUT_MUST_BE_IN_PRESERVED_CONFIGURATION')
    return value


def protected_history_archive(baseline):
    value = validate_history_archive(baseline)
    need(parent_proof(HISTORY_ROOT) == value['parent_metadata'], 'HISTORY_ARCHIVE_PARENT_CHANGED')
    for name, proof in value['files'].items():
        # file_proof uses canonical paths, O_NOFOLLOW, regular/single-link reads
        # and stable full metadata. The runtime owns semantic archive validation.
        need(file_proof(Path(name)) == proof, 'HISTORY_ARCHIVE_INPUT_CHANGED')
    return value


def validate_live_baseline(value):
    need(set(value.get('processes', {})) == PROCESS_NAMES
         and len({v['pid'] for v in value['processes'].values()}) == len(PROCESS_NAMES),
         'COMPLETE_DISTINCT_PRESERVED_PROCESS_SCOPE_REQUIRED')
    node = value['processes']['node']
    need(node['pid'] == 1273362 and node['start_ticks'] == '68729026'
         and value.get('detector_before') ==
             'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53',
         'COMPLETED_RELAY_DEPLOYMENT_MUST_BE_PRESERVED')
    need(len(value.get('core_files', {})) == 147
         and set(value.get('environments', {})) == {MAIN, QA}
         and set(value.get('static_units', {})) == {MAIN, QA},
         'COMPLETE_CORE_ENVIRONMENT_BASELINE_REQUIRED')
    validate_history_archive(value)
    return value


def validate_binding(value, plan, baseline):
    need(value.get('schema_version') == 1 and value.get('state') == 'REVIEWED_CORE_INCREMENT'
         and value.get('plan_sha256') == PLAN_SHA
         and value.get('source_commit') == plan['source_commit'], 'REVIEWED_ACTIVATION_BINDING_REQUIRED')
    for key in ('main', 'qa'):
        item = value.get(key)
        expected = baseline['processes'][key]
        need(isinstance(item, dict) and set(item) ==
             {'pid', 'process_start_ticks', 'entrypoint_sha256'}
             and type(item['pid']) is int and item['pid'] == expected['pid']
             and item['process_start_ticks'] == expected['start_ticks']
             and exact_hash(item['entrypoint_sha256']), 'EXACT_PROCESS_BINDING_REQUIRED')
    need(value.get('initial_gate') == plan['initial_gate'], 'EXACT_GATE_BINDING_REQUIRED')
    dest = Path(value.get('operation_directory', ''))
    need(dest.parent == PARENT and re.fullmatch('core-[a-f0-9]{12}', dest.name),
         'NEW_PRIVATE_OPERATION_REQUIRED')
    need(value.get('deployment_authorized') is True, 'CONCRETE_DEPLOYMENT_AUTHORIZATION_REQUIRED')
    return value


def validate_browser(value, main, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        age = (now - datetime.fromisoformat(value['observed_at'])).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('ACTUAL_AUTHENTICATED_BROWSER_TASK_PROOF_REQUIRED') from None
    need(value.get('source') == 'authenticated_browser_ui'
         and value.get('url') == 'https://abit.ai/api/tasks'
         and type(value.get('actual_task_count')) is int and value['actual_task_count'] == 0
         and type(value.get('main_pid')) is int and value['main_pid'] == main['pid']
         and str(value.get('main_process_start_ticks')) == main['process_start_ticks']
         and value.get('credential_extracted') is False and -5 <= age <= 120,
         'CURRENT_MAIN_AUTHENTICATED_TASK_MANAGER_NOT_PROVED_ZERO')


def source_cohort(baseline, manifest, installed=False):
    need((ROOT / 'current/runtime/open_webui').resolve() == RUNTIME, 'CURRENT_RELEASE_CHANGED')
    expected = dict(baseline['core_files'])
    if installed:
        for item in manifest['files']:
            meta = item['new_metadata'] if item['new_file'] else item['live_metadata']
            expected[str(RUNTIME / item['relative_path'])] = {
                **meta, 'sha256': item['candidate_sha256'], 'size': item['candidate_bytes']}
    paths = set((RUNTIME / 'utils').rglob('*.py'))
    paths |= set((RUNTIME / 'utils/agentic_contracts').rglob('*.json'))
    paths.add(RUNTIME / 'routers/agentic_governance.py')
    need(len(baseline['core_files']) == 147 and set(map(str, paths)) == set(expected)
         and len(paths) == 147 + (len(NEW_FILES) if installed else 0),
         'EXACT_FULL_RUNTIME_INVENTORY_REQUIRED')
    for name, pin in expected.items():
        need(file_proof(Path(name)) == pin, 'RUNTIME_BYTES_OR_METADATA_CHANGED')
    for item in manifest['files']:
        need(parent_proof((RUNTIME / item['relative_path']).parent) == item['parent_metadata'],
             'SOURCE_PARENT_CHANGED')
    return True


def environment_proof(pid):
    raw = (Path('/proc') / str(pid) / 'environ').read_bytes()
    need(len(raw) <= 262144, 'BOUNDED_PROCESS_ENVIRONMENT_REQUIRED')
    parts = [v.split(b'=', 1) for v in raw.split(b'\0') if v]
    need(all(len(v) == 2 for v in parts) and len({v[0] for v in parts}) == len(parts),
         'UNAMBIGUOUS_PROCESS_ENVIRONMENT_REQUIRED')
    value = dict(parts)
    need(value.get(b'PYTHONPYCACHEPREFIX', b'') == b'', 'UNREVIEWED_BYTECODE_CACHE_PREFIX')
    stable = sorted(k + b'=' + v for k, v in value.items() if k not in SYSTEMD_DYNAMIC_ENV)
    return {'stable_environment_sha256': sha(b'\0'.join(stable)),
            'bytecode_cache_prefix': '', 'variable_count': len(value),
            'dynamic_keys_excluded': sorted(k.decode() for k in SYSTEMD_DYNAMIC_ENV)}


def replacement_process(before, after):
    need(after['pid'] != before['pid'] and int(after['start_ticks']) > int(before['start_ticks'])
         and {k: v for k, v in before.items() if k not in {'pid', 'start_ticks'}}
             == {k: v for k, v in after.items() if k not in {'pid', 'start_ticks'}},
         'SERVICE_REPLACEMENT_IDENTITY_CHANGED')
    return after


def protected_runtime(relay, baseline):
    archive = protected_history_archive(baseline)
    for group in ('relay_code', 'protected_configuration'):
        for name, proof in baseline[group].items():
            if group == 'protected_configuration' and name in archive['files']:
                continue
            need(file_proof(Path(name)) == proof, 'PRESERVED_RELAY_OR_CONFIGURATION_CHANGED')
    for name, proof in baseline['processes'].items():
        if name not in {'main', 'qa'}:
            need(relay.process(proof['pid']) == proof, 'PRESERVED_PROCESS_CHANGED')
    for unit, expected in baseline['units'].items():
        if unit not in {MAIN, QA}:
            need(relay.service_state(unit) == expected, 'PRESERVED_UNIT_CHANGED')
    registry = Path('/srv/abit-dev-linux/relay-runtime/state/chat-thread-targets.json')
    need(file_proof(registry) == baseline['target_registry']
         and len(json.loads(read(registry))) == 16, 'EXACT_TARGET_REGISTRY_CHANGED')
    need(relay.health()['active_relay_turns'] == 0, 'RELAY_MUST_STAY_IDLE')
    return {'registry_sha256': baseline['target_registry']['sha256'],
            'relay_detector_sha256': baseline['detector_before']}


def static_unit_state(unit):
    fields = ('FragmentPath,DropInPaths,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,'
              'TimeoutStopUSec,Restart,RestartUSec,RefuseManualStart,User,Group,'
              'Environment,EnvironmentFiles,WorkingDirectory,'
              'NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,Delegate,DelegateSubgroup,AppArmorProfile')
    raw = subprocess.check_output(['/usr/bin/systemctl', 'show', unit, '--property=' + fields],
                                  timeout=5, text=True)
    # Only the digest, public drop-in paths and safety properties leave this function.
    value = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    variable = {'DropInPaths', 'SendSIGKILL', 'RefuseManualStart'}
    return {'fixed_properties_sha256': sha(canonical({k: v for k, v in value.items() if k not in variable})),
            'dropins': value.get('DropInPaths', '').split(),
            'SendSIGKILL': value.get('SendSIGKILL'),
            'RefuseManualStart': value.get('RefuseManualStart'),
            'KillSignal': value.get('KillSignal'), 'KillMode': value.get('KillMode')}


def check_units(before, guards=False, qa_fence=False, main_fence=None):
    for unit, expected in before.items():
        value = static_unit_state(unit)
        extras = ([str(NOKILL[unit])] if guards else [])
        if unit == QA and qa_fence:
            extras.append(str(QA_FENCE))
        if unit == MAIN and main_fence:
            extras.append(str(main_fence))
        need(value['fixed_properties_sha256'] == expected['fixed_properties_sha256']
             and sorted(value['dropins']) == sorted(expected['dropins'] + extras)
             and value['SendSIGKILL'] == ('no' if guards else expected['SendSIGKILL'])
             and value['RefuseManualStart'] == ('yes' if (unit == QA and qa_fence or unit == MAIN and main_fence) else expected['RefuseManualStart'])
             and (not (unit == QA and qa_fence) or value['dropins'][-1:] == [str(QA_FENCE)])
             and value['KillSignal'] == '15' and value['KillMode'] == 'control-group',
             'EXACT_UNIT_CONFIGURATION_OR_GRACEFUL_POLICY_CHANGED')


def checked_reload(record, name):
    record(name + '-intent.json', {'daemon_reload_attempts': 1})
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'daemon-reload'],
                                capture_output=True, timeout=15)
        ack = {'returncode': result.returncode, 'stdout_sha256': sha(result.stdout),
               'stderr_sha256': sha(result.stderr)}
    except subprocess.TimeoutExpired as error:
        ack = {'returncode': None, 'acknowledgement': 'unknown_timeout',
               'stdout_sha256': sha(error.stdout or b''), 'stderr_sha256': sha(error.stderr or b'')}
    record(name + '-ack.json', ack)
    # Callers must prove the exact loaded properties. No second reload is sent.


def create_root_guard(path, raw):
    parent = path.parent
    need(parent.parent == Path('/run/systemd/system') and parent.name in {MAIN + '.d', QA + '.d'},
         'EXACT_RUNTIME_UNIT_GUARD_PARENT_REQUIRED')
    if not parent.exists():
        parent.mkdir(mode=0o755)
    info = parent.lstat()
    need(parent.resolve() == parent and stat.S_ISDIR(info.st_mode)
         and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) & 0o022 == 0,
         'ROOT_PROTECTED_GUARD_PARENT_REQUIRED')
    need(not os.path.lexists(path), 'UNOWNED_RUNTIME_GUARD_PRESERVED')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fchmod(stream.fileno(), 0o644); os.fsync(stream.fileno())
    fsync_directory(parent)


def exact_root_guard(path, raw):
    info = path.lstat()
    need(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o644
         and read(path) == raw, 'EXACT_OWNED_RUNTIME_GUARD_REQUIRED')


def remove_root_guard(path, raw):
    exact_root_guard(path, raw)
    path.unlink()
    fsync_directory(path.parent)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare_temp(target, raw, metadata):
    temp = target.parent / ('.core-source-' + os.urandom(16).hex())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    complete = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush()
            os.fchown(stream.fileno(), metadata['uid'], metadata['gid'])
            os.fchmod(stream.fileno(), metadata['mode'])
            for key in os.listxattr(stream.fileno()):
                if key not in metadata['xattrs']:
                    os.removexattr(stream.fileno(), key)
            for key, value in metadata['xattrs'].items():
                os.setxattr(stream.fileno(), key, bytes.fromhex(value))
            os.utime(stream.fileno(), ns=(metadata['mtime_ns'], metadata['mtime_ns']))
            os.fsync(stream.fileno())
        complete = True
        return temp
    finally:
        if not complete:
            temp.unlink(missing_ok=True)


def atomic_existing_leaf(target, raw, expected_parent, original_proof, candidate_sha):
    need(target in {RUNTIME / name for name in TARGETS - NEW_FILES},
         'EXACT_EXISTING_TARGET_REQUIRED')
    need(parent_proof(target.parent) == expected_parent
         and file_proof(target) == original_proof and sha(raw) == candidate_sha,
         'EXISTING_TARGET_CAS_CHANGED')
    metadata = {key: original_proof[key] for key in ('uid', 'gid', 'mode', 'mtime_ns', 'xattrs')}
    temp = prepare_temp(target, raw, metadata)
    try:
        expected = {**metadata, 'sha256': candidate_sha, 'size': len(raw)}
        need(file_proof(temp) == expected and parent_proof(target.parent) == expected_parent
             and file_proof(target) == original_proof, 'EXISTING_TARGET_METADATA_OR_CAS_CHANGED')
        os.replace(temp, target)
        fsync_directory(target.parent)
        need(file_proof(target) == expected, 'EXISTING_TARGET_INSTALL_READBACK_CHANGED')
    finally:
        temp.unlink(missing_ok=True)


def atomic_new_leaf(target, raw, expected_parent, metadata, candidate_sha):
    need(target in {RUNTIME / name for name in NEW_FILES}, 'EXACT_NEW_HELPER_TARGET_REQUIRED')
    need(target.resolve() == target and parent_proof(target.parent) == expected_parent,
         'NEW_LEAF_PARENT_CHANGED')
    need(not os.path.lexists(target) and sha(raw) == candidate_sha, 'NEW_LEAF_ABSENCE_OR_HASH_CHANGED')
    temp = prepare_temp(target, raw, metadata)
    try:
        expected = {**metadata, 'size': len(raw), 'sha256': candidate_sha}
        need(file_proof(temp) == expected and parent_proof(target.parent) == expected_parent,
             'NEW_LEAF_METADATA_CHANGED')
        # link() is an atomic no-replace creation; an arriving target is never overwritten.
        os.link(temp, target, follow_symlinks=False)
        temp.unlink()
        fsync_directory(target.parent)
        need(file_proof(target) == expected, 'NEW_LEAF_INSTALL_READBACK_CHANGED')
    finally:
        temp.unlink(missing_ok=True)


def remove_new_leaf_for_reviewed_rollback(target, installed_proof, expected_parent):
    """Primitive only: a separate reviewed rollback binding must establish stopped services."""
    need(target in {RUNTIME / name for name in NEW_FILES}, 'EXACT_NEW_HELPER_ROLLBACK_REQUIRED')
    need(parent_proof(target.parent) == expected_parent and file_proof(target) == installed_proof,
         'ROLLBACK_NEW_LEAF_CHANGED')
    target.unlink()
    fsync_directory(target.parent)


def selected_caches(targets):
    found = []
    for target in targets:
        parent = target.parent / '__pycache__'
        if parent.exists() or parent.is_symlink():
            need(parent.resolve() == parent and parent.is_dir(), 'BYTECODE_PARENT_CHANGED')
            found.extend(sorted(parent.glob(target.stem + '.*.pyc')))
        legacy = target.with_suffix('.pyc')
        if legacy.exists() or legacy.is_symlink():
            found.append(legacy)
    need(len(found) == len(set(found)) and len(found) <= 128, 'BOUNDED_DISTINCT_BYTECODE_SCOPE_REQUIRED')
    for path in found:
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink(),
             'OWNED_BYTECODE_SCOPE_CHANGED')
    return found


def quarantine_bytecode(targets, destination, record):
    destination.mkdir(mode=0o700)
    rows = []
    for index, path in enumerate(selected_caches(targets)):
        info = path.lstat()
        need(info.st_dev == destination.stat().st_dev, 'BYTECODE_ARCHIVE_FILESYSTEM_CHANGED')
        proof = file_proof(path)
        target = destination / (str(index) + '.pyc')
        need(not os.path.lexists(target), 'BYTECODE_ARCHIVE_COLLISION')
        record('pycache-' + str(index) + '-intent.json',
               {'original_path': str(path), 'archive_path': str(target), 'original': proof})
        need(file_proof(path) == proof, 'BYTECODE_CHANGED_BEFORE_QUARANTINE')
        os.rename(path, target)
        fsync_directory(path.parent); fsync_directory(destination)
        need(file_proof(target) == proof, 'BYTECODE_ARCHIVE_READBACK_CHANGED')
        rows.append({'original_path': str(path), 'archive_path': str(target), 'proof': proof})
    need(selected_caches(targets) == [], 'SELECTED_BYTECODE_REMAINED')
    return rows


def once_service(record, label, action, unit, attempt, on_attempt=lambda: None):
    need(action in {'stop', 'start'} and unit in {MAIN, QA}, 'EXACT_SERVICE_ACTION_REQUIRED')
    record(label + '-intent.json', {'unit': unit, 'action': action, 'attempts': 1})
    # The durable create-only intent precedes the call. Unknown ACK never sends a second call.
    on_attempt()
    ack = attempt()
    record(label + '-ack.json', ack)
    return ack


def load_gate_pinned(hold):
    # Execute only the reviewed immutable bytes, rather than reopening an unchecked path.
    return module('maintenance', MAINTENANCE, MAINTENANCE_SHA)


def bind_gate_loader(hold):
    pinned = load_gate_pinned(hold)
    # All reviewed lifecycle helpers receive this exact hold object. Their nested
    # loads use immutable verified code, never SourceFileLoader or timestamp pyc.
    hold.load_gate = lambda: pinned
    return pinned


def gate_file_check():
    path = DATA / 'maintenance-admission/gate.sqlite'
    info = path.lstat()
    need(path.resolve() == path and stat.S_ISREG(info.st_mode)
         and info.st_nlink == 1 and info.st_uid == 999 and info.st_gid == 988
         and stat.S_IMODE(info.st_mode) == 0o600,
         'EXISTING_PRIVATE_GATE_REQUIRED_NO_CREATION')


def current_gate(hold):
    gate_file_check()
    with hold.as_service():
        gate_module = hold.load_gate()
        value = gate_module.status(gate_module.path(DATA))
    return {key: value[key] for key in ('state', 'epoch', 'active_admissions')}


def validate_gate_history(before, after, command_id, actor, expected_epoch):
    need(len(before['gate']) == len(after['gate']) == 1, 'EXACT_SINGLE_GATE_REQUIRED')
    old = before['gate'][0]
    expected = {**old, 'state': 'sealed', 'epoch': expected_epoch + 1,
                'command_id': command_id, 'actor': actor}
    need(old['state'] == 'draining' and old['epoch'] == expected_epoch
         and after['gate'] == [expected] and before['lease'] == after['lease'],
         'ONLY_EXACT_SEAL_GATE_TRANSITION_PERMITTED')
    previous = {row['seq']: row for row in before['audit']}
    now = {row['seq']: row for row in after['audit']}
    need(all(now.get(k) == v for k, v in previous.items()), 'OLD_GATE_AUDIT_CHANGED')
    added = [v for k, v in now.items() if k not in previous]
    need(len(added) == 1 and added[0]['event_id'] == command_id
         and added[0]['kind'] == 'operator_control'
         and json.loads(added[0]['payload']) ==
             {'action': 'seal', 'expected_epoch': expected_epoch, 'actor': actor},
         'EXACT_ONE_SEAL_AUDIT_REQUIRED')


def seal_once(hold, relay, expected, operation, record, on_attempt=lambda: None):
    need(current_gate(hold) == expected, 'INITIAL_GATE_CHANGED')
    if expected['state'] == 'sealed':
        return expected, 0
    need(expected['state'] == 'draining' and expected['active_admissions'] == 0,
         'NO_ACTIVE_WORK_BEFORE_SEAL')
    command = operation + '-seal'
    actor = 'reviewed-release-operator'
    before = relay.gate_history(hold)
    record('seal-intent.json', {'action': 'seal', 'expected_epoch': expected['epoch'],
                               'command_id': command, 'actor': actor, 'attempts': 1})
    on_attempt()
    try:
        with hold.as_service():
            gate = hold.load_gate()
            gate.control('seal', expected_epoch=expected['epoch'], command_id=command,
                         actor=actor, db_path=gate.path(DATA))
        ack = {'acknowledgement': 'returned'}
    except Exception as error:
        ack = {'acknowledgement': 'unknown', 'error_class': type(error).__name__}
    record('seal-call-ack.json', ack)
    # Reconcile the original transaction by readback only, even after a lost ACK.
    actual = current_gate(hold)
    need(actual == {'state': 'sealed', 'epoch': expected['epoch'] + 1, 'active_admissions': 0},
         'ORIGINAL_SEAL_UNCONFIRMED_NO_RETRY')
    validate_gate_history(before, relay.gate_history(hold), command, actor, expected['epoch'])
    record('seal-readback.json', actual)
    return actual, 1


def zero_current_work(hold, history, common, relay):
    idle = history.assert_idle(hold)
    with closing(hold.db('agentic-governance.sqlite')) as db:
        count = db.execute("SELECT count(*) FROM governance_invocation WHERE state IN ('running','uncertain','cancel_requested')").fetchone()[0]
    need(count == 0, 'CURRENT_GOVERNED_WORK_PRESENT')
    common.qa_idle()
    journals = relay.legacy_files()
    return {**idle, 'active_governance_invocations': count,
            'legacy_relay_journals_sha256': sha(canonical(journals))}


def history_without_gate(value):
    return {key: row for key, row in value['databases'].items()
            if key != 'maintenance-admission/gate.sqlite'}


def main(args):
    need(args.execute != args.preflight_only and os.geteuid() == 0
         and os.uname().nodename == 'abit-dev-linux' and exact_hash(args.controller_sha256)
         and sha(read(Path(__file__).resolve())) == args.controller_sha256,
         'EXACT_REVIEWED_ROOT_CONTROLLER_REQUIRED')
    need(exact_hash(PLAN_SHA), 'CORE_RELEASE_NOT_FROZEN')
    need(args.main_task_receipt and exact_hash(args.main_task_receipt_sha256),
         'ACTUAL_AUTHENTICATED_BROWSER_TASK_PROOF_REQUIRED')
    browser = json.loads(read(args.main_task_receipt, args.main_task_receipt_sha256, private=True))
    plan = validate_plan(json.loads(read(PLAN, PLAN_SHA)))
    package = Path(plan['package'])
    baseline = validate_live_baseline(json.loads(read(Path(plan['live_baseline']), plan['live_baseline_sha256'])))
    binding = validate_binding(json.loads(read(args.bindings, args.bindings_sha256, private=True)), plan, baseline)
    validate_browser(browser, binding['main'])
    manifest = validate_manifest(json.loads(read(package / 'preflight-manifest.json', plan['manifest_sha256'])), plan)
    tests = json.loads(read(package / 'backend-test-receipt.json', plan['tests_sha256']))
    need(tests.get('successful') is True and tests.get('tests') == plan['test_count']
         and all(tests.get(k) == 0 for k in ('failures', 'errors', 'skipped'))
         and tests.get('candidate_hashes_unchanged') is True
         and tests.get('all_runtime_fixture_hashes_unchanged') is True,
         'EXACT_ACTUAL_RUNTIME_OVERLAY_TESTS_REQUIRED')
    read(package / 'runtime-source-snapshot.json', plan['runtime_snapshot_sha256'])
    read(MAINTENANCE, MAINTENANCE_SHA)
    sys.dont_write_bytecode = True
    os.umask(0o077)
    helpers = {name: module(name, S / relative, pin) for name, (relative, pin) in HELPERS.items()}
    common, history, lifecycle, cas, base, finish, hold = (helpers[name] for name in
         ('common', 'history', 'lifecycle', 'cas', 'base', 'finish', 'hold'))
    relay = module('relay', RELAY_HELPER, RELAY_HELPER_SHA)
    bind_gate_loader(hold)
    lock = finish.private_lock('/run/lock/abit-ai-agentic-release.lock', 0)
    dispatch = None
    dest = None
    created = False
    stage = 'preflight'
    counts = {'Main_Stop_calls': 0, 'Main_Start_calls': 0, 'QA_Stop_calls': 0, 'QA_Start_calls': 0,
              'gate_seal_calls': 0, 'force_kills': 0, 'database_restores': 0,
              'ERP_actions': 0, 'model_requests': 0, 'unseal_calls': 0, 'relay_actions': 0}
    try:
        with hold.as_service():
            dispatch = finish.private_lock(DATA / 'erp-dot-dispatch.lock', os.geteuid())
        need(current_gate(hold) == plan['initial_gate'], 'ACTUAL_GATE_CHANGED')
        source_cohort(baseline, manifest)
        protected_runtime(relay, baseline)
        lifecycle.security()
        args.main_pid = binding['main']['pid']
        args.main_start_ticks = binding['main']['process_start_ticks']
        original = lifecycle.identity(hold, args)
        need(original == binding['main'], 'EXACT_MAIN_IDENTITY_CHANGED')
        qa_original = common.qa_identity(hold, binding)
        environments = {MAIN: environment_proof(original['pid']),
                        QA: environment_proof(qa_original['pid'])}
        need(environments == baseline['environments'], 'PROCESS_ENVIRONMENT_CHANGED')
        units = {unit: static_unit_state(unit) for unit in (MAIN, QA)}
        need(units == baseline['static_units'], 'ORIGINAL_UNIT_CONFIGURATION_CHANGED')
        check_units(units)
        hold.browser_tasks_proof(args, {MAIN: original})
        idle = zero_current_work(hold, history, common, relay)
        protected = history.snapshot(hold)
        unit_config = common.protected_files(cas)
        channel = common.channel_status()
        relay.capacity(hold, PARENT.parent)
        for item in manifest['files']:
            raw = read(package / 'candidate/open_webui' / item['relative_path'], item['candidate_sha256'])
            need(len(raw) == item['candidate_bytes'], 'CANDIDATE_LENGTH_CHANGED')
            compile(raw, str(RUNTIME / item['relative_path']), 'exec')
        need(not any(os.path.lexists(p) for p in [finish.MARKER, finish.DROPIN, QA_FENCE, *NOKILL.values()]),
             'UNOWNED_RUNTIME_GUARDS_PRESERVED')
        dest = Path(binding['operation_directory'])
        need(not os.path.lexists(dest), 'ORIGINAL_OPERATION_EXISTS_OBSERVE_ONLY')
        if args.preflight_only:
            print(json.dumps({'state': 'CORE_INCREMENT_READ_ONLY_PREFLIGHT_PASSED',
                'source_commit': plan['source_commit'], 'gate': plan['initial_gate'],
                'seal_needed': plan['initial_gate']['state'] == 'draining',
                'targets': len(TARGETS), 'new_helpers': len(NEW_FILES), 'runtime_writes': 0,
                'service_actions': 0, 'gate_mutations': 0, 'authenticated_browser_tasks': 0}))
            return
        parent = PARENT.lstat()
        need(PARENT.resolve() == PARENT and parent.st_uid == parent.st_gid == 0
             and stat.S_IMODE(parent.st_mode) == 0o700, 'PRESTAGED_PRIVATE_PARENT_REQUIRED')
        dest.mkdir(mode=0o700)
        created = True
        record = lambda name, value: history.private_new(dest / name, canonical(value))
        record('release-intent.json', {'plan_sha256': PLAN_SHA, 'controller_sha256': args.controller_sha256,
            'source_commit': plan['source_commit'], 'main': original, 'qa': qa_original,
            'initial_gate': plan['initial_gate'], 'history': protected, 'idle': idle,
            'environment_hashes': environments, 'unit_configuration': units,
            'first_flow_closed_claimed': False, 'database_restore_allowed': False})
        history.private_new(dest / 'frozen-release.json', read(PLAN, PLAN_SHA))
        history.private_new(dest / 'live-baseline.json',
                            read(Path(plan['live_baseline']), plan['live_baseline_sha256']))
        for index, (name, proof) in enumerate(unit_config.items()):
            history.private_new(dest / ('protected-' + str(index) + '.private'), read(Path(name), proof['sha256']))
        record('protected-file-map.json', unit_config)
        archive = protected_history_archive(baseline)
        for index, (name, proof) in enumerate(sorted(archive['files'].items())):
            history.private_new(dest / ('delivery-history-' + str(index) + '.private'),
                                read(Path(name), proof['sha256']))
        record('delivery-history-inputs.json', archive)
        for index, item in enumerate(manifest['files']):
            raw = read(package / 'candidate/open_webui' / item['relative_path'], item['candidate_sha256'])
            history.private_new(dest / (str(index) + '.after'), raw)
            if not item['new_file']:
                history.private_new(dest / (str(index) + '.before'),
                                    read(RUNTIME / item['relative_path'], item['original_sha256']))
            record(str(index) + '.metadata.json', item)
        record('rollback-plan.json', {'automatic': False, 'separate_fresh_binding_required': True,
            'restore_database_allowed': False, 'existing_source_backups': True,
            'remove_only_exact_created_helpers': sorted(NEW_FILES),
            'fresh_browser_idle_and_sealed_gates_required': True,
            'source_metadata': manifest['files']})
        validate_browser(json.loads(read(args.main_task_receipt, args.main_task_receipt_sha256, private=True)), original)
        need(history.snapshot(hold) == protected, 'HISTORY_CHANGED_BEFORE_SEAL')
        stage = 'supported_exact_seal'
        sealed, calls = seal_once(hold, relay, plan['initial_gate'], dest.name, record,
            on_attempt=lambda: counts.__setitem__('gate_seal_calls', 1))
        counts['gate_seal_calls'] = calls
        epoch = sealed['epoch']
        binding = {**binding, 'sealed_epoch': epoch}
        protected_sealed = history.snapshot(hold)
        need(history_without_gate(protected_sealed) == history_without_gate(protected),
             'BUSINESS_HISTORY_CHANGED_DURING_SEAL')
        record('sealed-history.json', protected_sealed)
        lifecycle.sealed(hold, epoch)
        stage = 'consistent_backups'
        record('consistent-database-backups.json', relay.backups(hold, history, dest))
        need(history.snapshot(hold, dest) == protected_sealed, 'HISTORY_CHANGED_DURING_BACKUPS')
        source_cohort(baseline, manifest); protected_runtime(relay, baseline)
        hold.browser_tasks_proof(args, {MAIN: original})
        need(zero_current_work(hold, history, common, relay) == idle, 'CURRENT_WORK_OR_RELAY_HISTORY_CHANGED')
        need(common.protected_files(cas) == unit_config, 'ORIGINAL_CONFIGURATION_CHANGED')
        stage = 'owned_runtime_guards'
        record('graceful-guards-intent.json', {'SendSIGKILL': 'no', 'units': [MAIN, QA]})
        for path in NOKILL.values():
            create_root_guard(path, NOKILL_BYTES)
        create_root_guard(QA_FENCE, QA_FENCE_BYTES)
        # Existing reviewed Main fence owns its normal create-only plan and marker.
        finish.install_start_fence(hold, dest, original)
        check_units(units, guards=True, qa_fence=True, main_fence=finish.DROPIN)
        for path in NOKILL.values():
            exact_root_guard(path, NOKILL_BYTES)
        exact_root_guard(QA_FENCE, QA_FENCE_BYTES)
        record('graceful-guards-readback.json', {'loaded': True, 'SendSIGKILL': 'no'})
        retained = hold.retained_inventory()['metadata_sha256']
        stage = 'one_Main_Stop'
        once_service(record, 'main-stop', 'stop', MAIN,
                     lambda: lifecycle.service_attempt('stop', MAIN, 45),
                     on_attempt=lambda: counts.__setitem__('Main_Stop_calls', 1))
        # Reuse exact stopped/cgroup/original-PID readback; no second Stop on a lost ACK.
        untouched = {name: {'pid': v['pid'], 'uid': v['uid'], 'start_ticks': v['start_ticks'],
                     'argv_sha256': v['argv_sha256'], 'cgroup': v['cgroup'][0]}
                     for name, v in baseline['processes'].items() if name not in {'main', 'qa'}}
        stop_binding = {**binding, 'untouched_processes': untouched}
        record('main-stop-readback.json', common.stopped(lifecycle, hold, finish, dest,
                                                        stop_binding, retained, untouched))
        need(common.qa_identity(hold, binding) == qa_original, 'QA_CHANGED_BEFORE_STOP')
        common.qa_idle()
        check_units(units, guards=True, qa_fence=True, main_fence=finish.DROPIN)
        stage = 'one_QA_Stop'
        once_service(record, 'qa-stop', 'stop', QA, lambda: common.qa_service_attempt('stop', 45),
                     on_attempt=lambda: counts.__setitem__('QA_Stop_calls', 1))
        record('qa-stop-readback.json', common.qa_stopped(hold, binding))
        lifecycle.sealed(hold, epoch)
        need(history.snapshot(hold) == protected_sealed, 'HISTORY_CHANGED_DURING_STOP')
        source_cohort(baseline, manifest)
        stage = 'exact_source_replacements'
        targets = [RUNTIME / item['relative_path'] for item in manifest['files']]
        record('retained-owned-bytecode.json',
               quarantine_bytecode(targets, dest / 'pycache-originals', record))
        for index, item in enumerate(manifest['files']):
            target = RUNTIME / item['relative_path']
            raw = read(package / 'candidate/open_webui' / item['relative_path'], item['candidate_sha256'])
            need(parent_proof(target.parent) == item['parent_metadata'], 'SOURCE_PARENT_CHANGED')
            record('source-' + str(index) + '-intent.json',
                   {'path': str(target), 'before': item['original_sha256'],
                    'after': item['candidate_sha256'], 'new_file': item['new_file']})
            if item['new_file']:
                atomic_new_leaf(target, raw, item['parent_metadata'], item['new_metadata'],
                                item['candidate_sha256'])
            else:
                need(file_proof(target) == baseline['core_files'][str(target)],
                     'EXISTING_TARGET_CAS_CHANGED')
                atomic_existing_leaf(target, raw, item['parent_metadata'],
                                     baseline['core_files'][str(target)], item['candidate_sha256'])
        source_cohort(baseline, manifest, installed=True)
        need(selected_caches(targets) == [], 'STALE_SELECTED_BYTECODE_MUST_REMAIN_QUARANTINED')
        need(history.snapshot(hold) == protected_sealed, 'HISTORY_CHANGED_BEFORE_START')
        protected_runtime(relay, baseline)
        lifecycle.security(); lifecycle.sealed(hold, epoch)
        check_units(units, guards=True, qa_fence=True, main_fence=finish.DROPIN)
        record('installed.json', {'exact_runtime_files': 149, 'targets': len(TARGETS),
                                  'new_helpers': len(NEW_FILES), 'bytecode_quarantined': True})
        # Release only the exact owned Main start fence; NoKill guards remain through startup.
        finish.root_file(finish.DROPIN, 0o644, finish.FENCE_BYTES)
        finish.root_file(finish.MARKER, 0o600, finish.marker_bytes(hold, dest, original))
        finish.DROPIN.unlink(); fsync_directory(finish.DROPIN.parent)
        checked_reload(record, 'main-fence-release')
        need(finish.fence_properties().get('RefuseManualStart') == 'no',
             'MAIN_FENCE_RELEASE_UNCONFIRMED')
        check_units(units, guards=True, qa_fence=True)
        finish.MARKER.unlink(); fsync_directory(finish.MARKER.parent)
        stage = 'one_Main_Start'
        once_service(record, 'main-start', 'start', MAIN,
                     lambda: lifecycle.service_attempt('start', MAIN, 45),
                     on_attempt=lambda: counts.__setitem__('Main_Start_calls', 1))
        deadline = time.monotonic() + 45
        while True:
            state = lifecycle.started_state(hold, original['pid'])
            if state:
                break
            need(time.monotonic() < deadline, 'MAIN_START_UNCONFIRMED_NO_SECOND_START')
            time.sleep(1)
        pid = int(state['MainPID'])
        birth = (Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1].split()[19]
        new_main = lifecycle.identity(hold, types.SimpleNamespace(main_pid=pid, main_start_ticks=birth))
        need(new_main['entrypoint_sha256'] == original['entrypoint_sha256']
             and environment_proof(pid) == environments[MAIN], 'MAIN_START_ENVIRONMENT_CHANGED')
        replacement_process(baseline['processes']['main'], relay.process(pid))
        record('main-start-readback.json', new_main)
        stage = 'QA_fence_release'
        remove_root_guard(QA_FENCE, QA_FENCE_BYTES)
        checked_reload(record, 'qa-fence-release')
        check_units(units, guards=True)
        stage = 'one_QA_Start'
        once_service(record, 'qa-start', 'start', QA, lambda: common.qa_service_attempt('start', 45),
                     on_attempt=lambda: counts.__setitem__('QA_Start_calls', 1))
        new_qa = common.qa_started(hold, binding)
        need(environment_proof(new_qa['pid']) == environments[QA], 'QA_START_ENVIRONMENT_CHANGED')
        replacement_process(baseline['processes']['qa'], relay.process(new_qa['pid']))
        record('qa-start-readback.json', new_qa)
        common.main_restarts_zero()
        stage = 'poststart_verification'
        lifecycle.sealed(hold, epoch); lifecycle.security()
        need(zero_current_work(hold, history, common, relay) == idle, 'CURRENT_WORK_OR_RELAY_HISTORY_CHANGED')
        source_cohort(baseline, manifest, installed=True); protected_runtime(relay, baseline)
        need(history.snapshot(hold) == protected_sealed, 'POSTSTART_HISTORY_CHANGED')
        deadline = time.monotonic() + 45
        while True:
            try:
                current_channel = common.channel_status()
                break
            except Exception:
                need(time.monotonic() < deadline, 'CHANNEL_RECONNECT_UNCONFIRMED_NO_SERVICE_ACTION')
                time.sleep(1)
        need(current_channel == channel, 'AUTHENTICATED_CHANNEL_IDENTITY_CHANGED')
        stage = 'restore_original_graceful_policy'
        for path in NOKILL.values():
            remove_root_guard(path, NOKILL_BYTES)
        checked_reload(record, 'restore-original-unit-policy')
        check_units(units)
        need(common.protected_files(cas) == unit_config, 'ORIGINAL_UNIT_CONFIGURATION_NOT_RESTORED')
        source_cohort(baseline, manifest, installed=True); protected_runtime(relay, baseline)
        lifecycle.sealed(hold, epoch)
        need(history.snapshot(hold) == protected_sealed, 'FINAL_HISTORY_CHANGED')
        record('completed-receipt.json', {'state': 'CORE_INCREMENT_INSTALLED_ADMISSION_SEALED',
            'source_commit': plan['source_commit'], 'plan_sha256': PLAN_SHA,
            'main': new_main, 'qa': new_qa, 'sealed_epoch': epoch,
            'original_unit_configuration_restored': True, 'environment_hashes_preserved': True,
            'historical_rows_preserved': True, 'first_flow_closed_claimed': False, **counts})
        print(json.dumps({'state': 'CORE_INCREMENT_INSTALLED_ADMISSION_SEALED',
                          'main_pid': pid, 'qa_pid': new_qa['pid'], 'sealed_epoch': epoch, **counts}))
    except BaseException as error:
        if created:
            try:
                history.private_new(dest / 'held.json', canonical({
                    'state': 'HELD_PRESERVE_ORIGINAL_NO_REPLAY', 'stage': stage,
                    'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                    'automatic_rollback': False, 'owned_guards_may_remain': True, **counts}))
            except BaseException:
                pass
        raise
    finally:
        if dispatch is not None:
            os.close(dispatch)
        os.close(lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller-sha256', required=True)
    parser.add_argument('--bindings', type=Path, required=True)
    parser.add_argument('--bindings-sha256', required=True)
    parser.add_argument('--main-task-receipt', type=Path, required=True)
    parser.add_argument('--main-task-receipt-sha256', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--execute', action='store_true')
    try:
        main(parser.parse_args())
    except Exception as error:
        print(json.dumps({'state': 'HELD_NO_AUTOMATIC_RETRY',
                          'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                          'instruction': 'Observe original intents and unit state; no replay, force kill or database restore.'}))
        raise SystemExit(65)

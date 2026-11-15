#!/usr/bin/env python3
"""Two existing contract files only; retain DRAINING24 and the submitted original.

Preparation is UNFROZEN. No recovery, selection, admission settlement, gate
transition, model request or native publication is available from this program.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import types

REPO = Path('/srv/abit-dev-linux/abit-ai-platform')
ROOT = Path('/srv/abit-dev-linux/abit-ai')
DATA = ROOT / 'data'
RUNTIME = ROOT / 'releases/20261031-live-0.11.4/runtime/open_webui'
HERE = REPO / 'deployment/linux/core-command-capacity-20261115'
PLAN = HERE / 'frozen-release.json'
PLAN_SHA = 'UNFROZEN'
PARENT = Path('/var/lib/abit-ai-erp-rollouts/core-command-capacity-increments')
MAIN, QA = 'abit-ai.service', 'abit-ai-erp-review-qa.service'
SUPPORT = REPO / 'deployment/linux/core-hardening-20261115/apply_core_hardening.py'
SUPPORT_SHA = 'f6477ec6b7173a7c7dd07a7423c037ea624ca759dc6383d2353f64bbcdd2a0b3'
PRESERVATION = HERE / 'preserved_original.py'
PRESERVATION_SHA = '171f91e8e1cadf967cec6ca8e8dd90af82e76f85ae95aa92a6847a045ec7bd4b'
EXTERNAL_TEMPLATE_SHA = 'abc0f6ccff456e12e74e56618cb2fe9252701bd6f59e09691a19e26f4246efd1'
TARGETS = {
    'utils/agentic_contracts/developer_delivery.py': {
        'kind': 'RuntimePython', 'sha256': '90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210'},
    'utils/agentic_contracts/schemas/governance_v1.schema.json': {
        'kind': 'RuntimeJSON', 'sha256': '5131fd838234f34173b4a7b30360d3c53d3d18f1fcdbc2afb079ba15db8d897c'},
}
PROCESS_NAMES = frozenset({'main', 'qa', 'node', 'chrome', 'watchdog', 'xvfb',
                           'egress', 'tunnel', 'mcp_auth', 'mcp_backend', 'mcp_tunnel'})
INITIAL_GATE = {'state': 'draining', 'epoch': 24, 'active_admissions': 1}


def need(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def exact_hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, 'DUPLICATE_RELEASE_JSON_KEY')
            value[key] = item
        return value
    def nonfinite(value):
        raise RuntimeError('NONFINITE_RELEASE_JSON_VALUE')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)


def pinned_module(name, path, pin):
    """Bootstrap only exact reviewed source, without timestamp bytecode loading."""
    need(exact_hash(pin) and path.is_absolute() and path.resolve() == path,
         'EXACT_SUPPORT_MODULE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 200000,
             'BOUNDED_SUPPORT_SOURCE_REQUIRED')
        raw = bytearray()
        while len(raw) <= 200000:
            part = os.read(fd, min(65536, 200001 - len(raw)))
            if not part:
                break
            raw.extend(part)
        raw = bytes(raw)
        after = os.fstat(fd)
        need(len(raw) == before.st_size and sha(raw) == pin and
             all(getattr(before, key) == getattr(after, key) for key in
                 ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
                  'st_size', 'st_mtime_ns', 'st_ctime_ns')),
             'SUPPORT_SOURCE_CHANGED')
    finally:
        os.close(fd)
    value = types.ModuleType('capacity_release_' + name)
    value.__file__ = str(path)
    sys.modules[value.__name__] = value
    exec(compile(raw, str(path), 'exec'), value.__dict__)
    return value


def exact_gate(value):
    need(isinstance(value, dict) and set(value) == set(INITIAL_GATE)
         and type(value.get('epoch')) is int and type(value.get('active_admissions')) is int
         and value == INITIAL_GATE, 'EXACT_DRAINING24_ONE_UNCHANGED_ADMISSION_REQUIRED')


def validate_browser(core, value, main, now=None):
    core.validate_browser(value, main, now=now)
    need(value.get('admin_authenticated') is True, 'ACTUAL_ADMINISTRATIVE_BROWSER_SESSION_REQUIRED')


def validate_plan(value):
    keys = {'schema_version', 'state', 'source_commit', 'package', 'manifest_sha256',
            'tests_sha256', 'test_count', 'live_baseline', 'live_baseline_sha256',
            'initial_gate', 'support_sha256', 'preservation_sha256', 'native_publication'}
    need(isinstance(value, dict) and set(value) == keys and type(value['schema_version']) is int
         and value['schema_version'] == 1 and value['state'] == 'FROZEN_CAPACITY_ONLY_RETAINED_ORIGINAL'
         and re.fullmatch('[a-f0-9]{40}', value['source_commit']), 'EXACT_FROZEN_CAPACITY_PLAN_REQUIRED')
    package, baseline = Path(value['package']), Path(value['live_baseline'])
    need(package.parent == REPO / '.git' and re.fullmatch('core-command-capacity-20261115-[a-z0-9_]+', package.name)
         and baseline.parent == REPO / '.git' and baseline.suffix == '.json', 'EXACT_PRIVATE_PACKAGE_REQUIRED')
    need(all(exact_hash(value[key]) for key in ('manifest_sha256', 'tests_sha256', 'live_baseline_sha256'))
         and type(value['test_count']) is int and value['test_count'] > 0, 'EXACT_OFFLINE_TEST_BINDING_REQUIRED')
    need(value['support_sha256'] == SUPPORT_SHA and value['preservation_sha256'] == PRESERVATION_SHA,
         'EXACT_REVIEWED_SUPPORT_REQUIRED')
    exact_gate(value['initial_gate'])
    native = value['native_publication']
    need(isinstance(native, dict) and set(native) == {'path', 'sha256', 'expected', 'channel_path',
         'channel_sha256', 'channel_expected'} and all(exact_hash(native[key]) for key in
         ('sha256', 'channel_sha256')), 'ACTUAL_NATIVE_POSITIVE_PUBLICATION_REQUIRED')
    return value


def validate_manifest(value, plan):
    need(isinstance(value, dict) and set(value) == {'schema_version', 'source_commit', 'runtime', 'files'}
         and type(value['schema_version']) is int and value['schema_version'] == 1
         and value['source_commit'] == plan['source_commit']
         and value['runtime'] == str(RUNTIME), 'EXACT_TWO_FILE_MANIFEST_REQUIRED')
    files = value['files']
    need(isinstance(files, list) and len(files) == 2 and
         {item.get('relative_path') for item in files} == set(TARGETS), 'ONLY_TWO_EXISTING_CONTRACT_FILES')
    for item in files:
        need(set(item) == {'relative_path', 'kind', 'candidate_sha256', 'candidate_bytes',
             'original', 'parent'}, 'CLOSED_EXISTING_FILE_MANIFEST_REQUIRED')
        expected = TARGETS[item['relative_path']]
        need(item['kind'] == expected['kind'] and item['candidate_sha256'] == expected['sha256']
             and type(item['candidate_bytes']) is int and 0 < item['candidate_bytes'] <= 200000,
             'EXACT_REVIEWED_CONTRACT_BYTES_REQUIRED')
        original = item['original']
        need(isinstance(original, dict) and set(original) ==
             {'sha256', 'uid', 'gid', 'mode', 'size', 'mtime_ns', 'xattrs'}
             and exact_hash(original['sha256']) and original['sha256'] != item['candidate_sha256']
             and (original['uid'], original['gid'], original['mode']) == (999, 988, 0o644)
             and type(original['mtime_ns']) is int and type(original['size']) is int
             and isinstance(original['xattrs'], dict), 'EXACT_EXISTING_SOURCE_METADATA_REQUIRED')
        parent = item['parent']
        need(isinstance(parent, dict) and set(parent) == {'dev', 'ino', 'uid', 'gid', 'mode', 'xattrs'}
             and parent['uid'] == 999 and parent['gid'] == 988, 'EXACT_SOURCE_PARENT_REQUIRED')
    return value


def validate_baseline(value, core):
    exact_gate(value['initial_gate'])
    need(isinstance(value, dict) and set(value['processes']) == PROCESS_NAMES
         and len({row['pid'] for row in value['processes'].values()}) == 11
         and set(value['environments']) == {MAIN, QA} and set(value['static_units']) == {MAIN, QA}
         and len(value['core_files']) == 147, 'COMPLETE_FRESH_PROCESS_AND_SOURCE_BASELINE_REQUIRED')
    need(value['initial_gate'] == INITIAL_GATE and exact_hash(value['preserved_state_sha256'])
         and isinstance(value.get('preserved_state'), dict)
         and sha(canonical(value['preserved_state'])) == value['preserved_state_sha256']
         and exact_hash(value['legacy_journals_sha256']), 'EXACT_RETAINED_ORIGINAL_BASELINE_REQUIRED')
    core.validate_history_archive(value)
    core.validate_format_capture(value)
    return value


def validate_binding(value, plan, baseline):
    exact_gate(value['initial_gate'])
    need(isinstance(value, dict) and set(value) == {'schema_version', 'state', 'plan_sha256',
         'source_commit', 'main', 'qa', 'initial_gate', 'operation_directory', 'deployment_authorized'}
         and type(value['schema_version']) is int and value['schema_version'] == 1
         and value['state'] == 'REVIEWED_CAPACITY_ONLY_PRESERVATION'
         and value['plan_sha256'] == PLAN_SHA and value['source_commit'] == plan['source_commit']
         and value['initial_gate'] == INITIAL_GATE and value['deployment_authorized'] is True,
         'EXACT_REVIEWED_CAPACITY_BINDING_REQUIRED')
    for key in ('main', 'qa'):
        expected, actual = baseline['processes'][key], value[key]
        need(isinstance(actual, dict) and set(actual) == {'pid', 'process_start_ticks', 'entrypoint_sha256'}
             and actual['pid'] == expected['pid'] and actual['process_start_ticks'] == expected['start_ticks']
             and exact_hash(actual['entrypoint_sha256']), 'EXACT_CURRENT_PROCESS_BINDING_REQUIRED')
    destination = Path(value['operation_directory'])
    need(destination.parent == PARENT and re.fullmatch('capacity-[a-f0-9]{12}', destination.name),
         'NEW_ROOT_PRIVATE_OPERATION_REQUIRED')
    return value


def source_cohort(core, baseline, manifest, installed_paths=()):
    installed_paths = set(installed_paths)
    need(installed_paths <= set(TARGETS), 'EXACT_INSTALLED_PATHS_REQUIRED')
    need((ROOT / 'current/runtime/open_webui').resolve() == RUNTIME, 'CURRENT_RELEASE_CHANGED')
    expected = dict(baseline['core_files'])
    for item in manifest['files']:
        target = str(RUNTIME / item['relative_path'])
        need(expected.get(target) == item['original'], 'BASELINE_ORIGINAL_SOURCE_CHANGED')
        if item['relative_path'] in installed_paths:
            expected[target] = {**item['original'], 'sha256': item['candidate_sha256'],
                                'size': item['candidate_bytes']}
    paths = set((RUNTIME / 'utils').rglob('*.py'))
    paths |= set((RUNTIME / 'utils/agentic_contracts').rglob('*.json'))
    paths.add(RUNTIME / 'routers/agentic_governance.py')
    need(len(paths) == 147 and set(map(str, paths)) == set(expected)
         and sum(path.suffix == '.py' for path in paths) == 144
         and sum(path.suffix == '.json' for path in paths) == 3, 'EXACT_UNEXPANDED_147_FILE_COHORT_REQUIRED')
    for name, proof in expected.items():
        need(core.file_proof(Path(name)) == proof, 'UNSELECTED_OR_SELECTED_SOURCE_DRIFT')
    for item in manifest['files']:
        need(core.parent_proof((RUNTIME / item['relative_path']).parent) == item['parent'],
             'SOURCE_PARENT_CHANGED')


NATIVE_FIXED = {
    'schema_version': 1, 'purpose': 'erp-f33-registered-command-contract-ui-publication',
    'ok': True, 'site': 'erp.abit.ai',
    'developer_delivery_sha256': TARGETS['utils/agentic_contracts/developer_delivery.py']['sha256'],
    'command_receipt_max_chars': 90091,
    'native_source_commit': 'fff8ef9f67bd5a28373a08c99f20fa6d9e68bb1d',
    'shared_contract_source_commit': '0bb85b29b94e70b106de42f71102389a24cc6de5',
    'site_config_sha256': '431407bae167c67007b4a7189393f70038d21d2591b91bb41a3647316ecd73b3',
    'common_config_sha256': 'a2e8bfd49945b2658a130067616ac1b462b05487e1613b51fcbd29e44dc2ebee',
}
CHANNEL_FIXED = {
    'schema_version': 1, 'ok': True,
    'state': 'ISOLATED_CHANNEL_SOURCE_REPLACEMENT_STARTED_AUTHENTICATED',
    'explicit_Start_calls': 1, 'prior_physical_explicit_Start_calls': 3,
    'physical_explicit_Start_calls_total': 4, 'old_Stop_calls': 1,
    'prior_old_Stop_calls': 2, 'physical_old_Stop_calls_total': 3,
}


def native_positive(core, plan):
    """Verify exact Root-retained publication and authenticated channel receipts."""
    binding = plan['native_publication']
    path, channel_path = Path(binding['path']), Path(binding['channel_path'])
    need(path.is_absolute() and channel_path.is_absolute()
         and str(path).startswith('/var/lib/abit-') and str(channel_path).startswith('/var/lib/abit-'),
         'ROOT_NATIVE_RECEIPT_PATH_REQUIRED')
    value = strict_json(core.read(path, binding['sha256'], private=True))
    expected = binding['expected']
    need(isinstance(expected, dict) and set(expected) == set(NATIVE_FIXED) | {'services'}
         and all(type(expected[key]) is type(item) and expected[key] == item
                 for key, item in NATIVE_FIXED.items()), 'EXACT_POSITIVE_NATIVE_CONTRACT_REQUIRED')
    need(all(type(value.get(key)) is type(item) and value[key] == item for key, item in expected.items()),
         'NATIVE_POSITIVE_PUBLICATION_DRIFT')
    need(value.get('original_f33') == {'invocation_id': 'agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35',
         'run_id': 'adf93fbf-9737-5eb4-9f5d-21e02dc63014', 'preserved': True}
         and value.get('channel_new_start_receipt_path') == str(channel_path)
         and value.get('channel_new_start_receipt_sha256') == binding['channel_sha256'],
         'ORIGINAL_OR_NATIVE_CHANNEL_PUBLICATION_NOT_BOUND')
    services = value.get('services')
    need(isinstance(services, dict) and set(services) == {'backend', 'governance', 'scheduler'}
         and expected['services'] == services
         and all(isinstance(role, str) and re.fullmatch('[a-z][a-z0-9_-]{0,39}', role) for role in services),
         'EXACT_THREE_PUBLISHED_NATIVE_SERVICES_REQUIRED')
    for service in services.values():
        need(isinstance(service, dict) and set(service) == {'id', 'image', 'running'}
             and service['running'] is True and re.fullmatch('[a-f0-9]{64}', service['id'])
             and re.fullmatch('sha256:[a-f0-9]{64}', service['image']),
             'ACTUAL_NATIVE_SERVICE_ID_AND_IMAGE_REQUIRED')
    channel = strict_json(core.read(channel_path, binding['channel_sha256'], private=True))
    fixed = binding['channel_expected']
    need(isinstance(fixed, dict) and set(fixed) == set(CHANNEL_FIXED)
         and all(type(fixed[key]) is type(item) and fixed[key] == item for key, item in CHANNEL_FIXED.items())
         and all(type(channel.get(key)) is type(item) and channel[key] == item for key, item in fixed.items()),
         'ACTUAL_AUTHENTICATED_NATIVE_CHANNEL_RECEIPT_REQUIRED')
    return {'publication_sha256': binding['sha256'], 'channel_sha256': binding['channel_sha256']}


def service_state(hold, unit):
    need(unit in {MAIN, QA}, 'EXACT_SERVICE_REQUIRED')
    return hold.services()[unit]


def stopped_state(state, original_present, cgroup_empty):
    return (state.get('ActiveState') == 'inactive' and state.get('MainPID') == '0'
            and original_present is False and cgroup_empty is True)


def wait_stopped(hold, unit, original, readback, deadline=60):
    end = time.monotonic() + deadline
    while True:
        state = service_state(hold, unit)
        group = Path('/sys/fs/cgroup/system.slice') / unit
        empty = not group.exists() or all(not path.read_text().strip() for path in group.rglob('cgroup.procs'))
        process = Path('/proc') / str(original['pid'])
        present = process.exists()
        if present:
            present = process.joinpath('stat').read_text().split(') ', 1)[1].split()[19] == original['process_start_ticks']
        readback()
        if stopped_state(state, present, empty):
            return {'state': state, 'original_process_gone': True, 'cgroup_empty': True,
                    'second_Stop_calls': 0}
        need(time.monotonic() < end, 'ORIGINAL_STOP_UNSETTLED_NO_REPLAY')
        time.sleep(1)


def ensure_service_identity(core, relay, lifecycle, common, hold, baseline, binding):
    original = lifecycle.identity(hold, types.SimpleNamespace(main_pid=binding['main']['pid'],
                                  main_start_ticks=binding['main']['process_start_ticks']))
    qa = common.qa_identity(hold, binding)
    need(original == binding['main'] and qa == binding['qa'], 'CURRENT_SERVICE_IDENTITY_CHANGED')
    for name, unit in (('main', MAIN), ('qa', QA)):
        need(relay.process(binding[name]['pid']) == baseline['processes'][name]
             and core.environment_proof(binding[name]['pid']) == baseline['environments'][unit],
             'CURRENT_CGROUP_ARGUMENT_OR_ENVIRONMENT_CHANGED')
    return original, qa


def storage_budget(core, relay, hold, baseline, manifest, package, destination):
    required = relay.capacity(hold, destination.parent)
    protected = set(baseline['protected_configuration']) | set(core.protected_history_archive(baseline)['files'])
    protected.add(core.protected_format_capture(baseline)['path'])
    extra = 2 * sum(Path(name).stat().st_size for name in protected)
    extra += sum(item['original']['size'] + item['candidate_bytes'] for item in manifest['files'])
    extra += sum(path.stat().st_size for path in core.selected_caches([RUNTIME / name for name in TARGETS]))
    # Small immutable manifests/receipts and private history snapshots are bounded too.
    extra += 2 * len(canonical(baseline)) + 2 * len(canonical(baseline.get('preserved_state', {})))
    extra += 2 * len(canonical(manifest)) + 67_108_864
    need(shutil.disk_usage(destination.parent).free >= required['backup_reserve_bytes']
         + required['minimum_remaining_bytes'] + extra, 'SOURCE_CACHE_AND_EVIDENCE_RESERVE_REQUIRED')
    return {**required, 'source_cache_protected_evidence_reserve_bytes': extra}


def main(args):
    need(args.execute != args.preflight_only and os.geteuid() == 0
         and os.uname().nodename == 'abit-dev-linux', 'EXACT_ROOT_REVIEW_MODE_REQUIRED')
    need(exact_hash(PLAN_SHA) and exact_hash(PRESERVATION_SHA), 'CAPACITY_RELEASE_NOT_FROZEN')
    core = pinned_module('core_guards', SUPPORT, SUPPORT_SHA)
    need(exact_hash(args.controller_sha256)
         and sha(core.read(Path(__file__).resolve())) == args.controller_sha256,
         'EXACT_CONTROLLER_BYTES_REQUIRED')
    need(args.main_task_receipt and exact_hash(args.main_task_receipt_sha256),
         'FRESH_AUTHENTICATED_BROWSER_PROOF_REQUIRED')
    preservation = pinned_module('preserved_original', PRESERVATION, PRESERVATION_SHA)
    plan = validate_plan(strict_json(core.read(PLAN, PLAN_SHA)))
    baseline = validate_baseline(strict_json(core.read(Path(plan['live_baseline']), plan['live_baseline_sha256'])), core)
    binding = validate_binding(strict_json(core.read(args.bindings, args.bindings_sha256, private=True)), plan, baseline)
    browser = strict_json(core.read(args.main_task_receipt, args.main_task_receipt_sha256, private=True))
    validate_browser(core, browser, binding['main'])
    package = Path(plan['package'])
    manifest = validate_manifest(strict_json(core.read(package / 'manifest.json', plan['manifest_sha256'])), plan)
    tests = strict_json(core.read(package / 'test-receipt.json', plan['tests_sha256']))
    need(tests.get('successful') is True and tests.get('tests') == plan['test_count']
         and all(tests.get(key) == 0 for key in ('failures', 'errors', 'skipped'))
         and tests.get('candidate_hashes') == {name: value['sha256'] for name, value in TARGETS.items()}
         and tests.get('candidate_hashes_unchanged') is True, 'EXACT_TWO_FILE_OFFLINE_TESTS_REQUIRED')
    validator = core.module('runtime_validation', core.RUNTIME_VALIDATION, core.RUNTIME_VALIDATION_SHA)
    for item in manifest['files']:
        raw = core.read(package / 'candidate/open_webui' / item['relative_path'], item['candidate_sha256'])
        need(len(raw) == item['candidate_bytes'], 'CANDIDATE_SIZE_CHANGED')
        validator.validate_runtime(item['relative_path'], raw, item['kind'])
    sys.dont_write_bytecode = True
    os.umask(0o077)
    helpers = {name: core.module(name, core.S / relative, pin)
               for name, (relative, pin) in core.HELPERS.items()}
    common, history, lifecycle, cas, finish, hold = (helpers[name] for name in
        ('common', 'history', 'lifecycle', 'cas', 'finish', 'hold'))
    relay = core.module('relay', core.RELAY_HELPER, core.RELAY_HELPER_SHA)
    core.bind_gate_loader(hold)
    release = finish.private_lock('/run/lock/abit-ai-agentic-release.lock', 0)
    dispatch, destination, record, before = None, None, None, None
    stage, created = 'preflight', False
    installed_paths = set()
    source_installation_started = False
    counters = {'Main_Stop_calls': 0, 'Main_Start_calls': 0, 'QA_Stop_calls': 0, 'QA_Start_calls': 0,
                'gate_changes': 0, 'force_kills': 0, 'database_restores': 0, 'model_requests': 0,
                'ERP_actions': 0, 'relay_actions': 0, 'original_recovery_calls': 0}
    qa_owners = {(binding['qa']['pid'], binding['qa']['process_start_ticks'])}
    qa_stop_ns = None
    try:
        with hold.as_service():
            dispatch = finish.private_lock(DATA / 'erp-dot-dispatch.lock', os.geteuid())
        source_cohort(core, baseline, manifest)
        core.protected_runtime(relay, baseline)
        lifecycle.security()
        original, qa_original = ensure_service_identity(core, relay, lifecycle, common, hold, baseline, binding)
        units = {unit: core.static_unit_state(unit) for unit in (MAIN, QA)}
        need(units == baseline['static_units'], 'ORIGINAL_UNIT_CONFIGURATION_CHANGED')
        core.check_units(units)
        before = preservation.capture(hold)
        preservation.preserve(baseline['preserved_state'], before, allowed_qa_owners=qa_owners,
            old_qa_owner=(qa_original['pid'], qa_original['process_start_ticks']),
            old_qa_stop_ns=time.time_ns())
        legacy = relay.legacy_files()
        need(sha(canonical(legacy)) == baseline['legacy_journals_sha256'], 'LEGACY_WORK_OR_JOURNAL_CHANGED')
        native = native_positive(core, plan)
        channel = common.channel_status()
        unit_config = common.protected_files(cas)
        destination = Path(binding['operation_directory'])
        capacity = storage_budget(core, relay, hold, baseline, manifest, package, destination)
        need(not any(os.path.lexists(path) for path in
             [finish.MARKER, finish.DROPIN, core.QA_FENCE, *core.NOKILL.values()]),
             'EXISTING_RUNTIME_GUARDS_PRESERVED')
        need(not os.path.lexists(destination), 'ORIGINAL_OPERATION_EXISTS_OBSERVE_ONLY')

        def readback():
            exact_gate(core.current_gate(hold))
            source_cohort(core, baseline, manifest, installed_paths)
            preservation.preserve(before, preservation.capture(hold), allowed_qa_owners=qa_owners,
                old_qa_owner=(qa_original['pid'], qa_original['process_start_ticks']), old_qa_stop_ns=qa_stop_ns if qa_stop_ns is not None else time.time_ns())
            need(relay.legacy_files() == legacy, 'LEGACY_CURRENT_WORK_CHANGED')
            core.protected_runtime(relay, baseline)
            need(native_positive(core, plan) == native, 'NATIVE_PUBLICATION_CHANGED')

        readback()
        if args.preflight_only:
            print(json.dumps({'state': 'CAPACITY_ONLY_READ_ONLY_PREFLIGHT_PASSED',
                'gate': INITIAL_GATE, 'targets': 2, 'new_files': 0, 'runtime_files': 147,
                'original_submission_preserved': True, 'existing_v1_selection_preserved': True,
                'browser_current_tasks': 0, 'capacity': capacity, **counters}))
            return
        parent = PARENT.lstat()
        need(PARENT.resolve() == PARENT and parent.st_uid == parent.st_gid == 0
             and stat.S_IMODE(parent.st_mode) == 0o700, 'PRESTAGED_PRIVATE_PARENT_REQUIRED')
        destination.mkdir(mode=0o700)
        created = True
        record = lambda name, value: history.private_new(destination / name, canonical(value))
        record('release-intent.json', {'plan_sha256': PLAN_SHA, 'controller_sha256': args.controller_sha256,
            'source_commit': plan['source_commit'], 'main': original, 'qa': qa_original,
            'initial_gate': INITIAL_GATE, 'original_state': before, 'capacity': capacity,
            'native_publication': native, 'zero_active_claimed': False, 'source_only_capacity': True})
        for name, path, pin in [('frozen-release.json', PLAN, PLAN_SHA),
            ('live-baseline.json', Path(plan['live_baseline']), plan['live_baseline_sha256']),
            ('manifest.json', package / 'manifest.json', plan['manifest_sha256'])]:
            history.private_new(destination / name, core.read(path, pin))
        for index, (name, proof) in enumerate(unit_config.items()):
            history.private_new(destination / ('configuration-' + str(index) + '.private'),
                                core.read(Path(name), proof['sha256']))
        record('configuration-map.json', unit_config)
        archive = core.protected_history_archive(baseline)
        for index, (name, proof) in enumerate(sorted(archive['files'].items())):
            history.private_new(destination / ('history-input-' + str(index) + '.private'), core.read(Path(name), proof['sha256']))
        record('history-input-map.json', archive)
        core.backup_format_capture(baseline, history, destination, record)
        for index, item in enumerate(manifest['files']):
            history.private_new(destination / (str(index) + '.before'), core.read(RUNTIME / item['relative_path'], item['original']['sha256']))
            history.private_new(destination / (str(index) + '.after'), core.read(package / 'candidate/open_webui' / item['relative_path'], item['candidate_sha256']))
        record('rollback-plan.json', {'automatic': False, 'separate_fresh_review_required': True,
            'database_restore': False, 'replay_original_service_intents': False, 'new_files': 0,
            'exact_source_and_metadata': manifest['files']})
        stage = 'eleven_consistent_backups_before_Stop'
        record('database-backups.json', relay.backups(hold, history, destination))
        readback()
        source_cohort(core, baseline, manifest)
        validate_browser(core, strict_json(core.read(args.main_task_receipt, args.main_task_receipt_sha256, private=True)), original)
        ensure_service_identity(core, relay, lifecycle, common, hold, baseline, binding)
        need(common.protected_files(cas) == unit_config, 'CONFIGURATION_CHANGED_BEFORE_STOP')
        stage = 'owned_graceful_guards'
        record('graceful-guards-intent.json', {'SendSIGKILL': 'no', 'units': [MAIN, QA]})
        for path in core.NOKILL.values():
            core.create_root_guard(path, core.NOKILL_BYTES)
        core.create_root_guard(core.QA_FENCE, core.QA_FENCE_BYTES)
        finish.install_start_fence(hold, destination, original)
        core.check_units(units, guards=True, qa_fence=True, main_fence=finish.DROPIN)
        readback()
        stage = 'one_Main_Stop'
        core.once_service(record, 'main-stop', 'stop', MAIN,
            lambda: lifecycle.service_attempt('stop', MAIN, 45),
            on_attempt=lambda: counters.__setitem__('Main_Stop_calls', 1))
        record('main-stop-readback.json', wait_stopped(hold, MAIN, original, readback))
        need(common.qa_identity(hold, binding) == qa_original, 'QA_CHANGED_BEFORE_STOP')
        core.check_units(units, guards=True, qa_fence=True, main_fence=finish.DROPIN)
        stage = 'one_QA_Stop'
        core.once_service(record, 'qa-stop', 'stop', QA,
            lambda: common.qa_service_attempt('stop', 45),
            on_attempt=lambda: counters.__setitem__('QA_Stop_calls', 1))
        qa_stop_ns = (destination / 'qa-stop-ack.json').stat().st_mtime_ns
        record('qa-stop-readback.json', wait_stopped(hold, QA, qa_original, readback))
        stage = 'exact_two_existing_file_install'
        source_cohort(core, baseline, manifest)
        targets = [RUNTIME / item['relative_path'] for item in manifest['files']]
        record('selected-bytecode.json', core.quarantine_bytecode(targets, destination / 'pycache-originals', record))
        for index, item in enumerate(manifest['files']):
            readback()
            target = RUNTIME / item['relative_path']
            need(item['relative_path'] in TARGETS, 'TWO_LEAF_SCOPE_REQUIRED_AT_WRITE')
            raw = core.read(package / 'candidate/open_webui' / item['relative_path'], item['candidate_sha256'])
            record('source-' + str(index) + '-intent.json', {'path': str(target), 'original': item['original'],
                                                         'candidate_sha256': item['candidate_sha256']})
            source_installation_started = True
            core.atomic_existing_leaf(target, raw, item['parent'], item['original'], item['candidate_sha256'])
            installed_paths.add(item['relative_path'])
        source_cohort(core, baseline, manifest, installed_paths)
        need(core.selected_caches(targets) == [], 'SELECTED_CACHE_REAPPEARED')
        readback()
        core.check_units(units, guards=True, qa_fence=True, main_fence=finish.DROPIN)
        lifecycle.security()
        stage = 'release_Main_fence'
        finish.root_file(finish.DROPIN, 0o644, finish.FENCE_BYTES)
        finish.root_file(finish.MARKER, 0o600, finish.marker_bytes(hold, destination, original))
        finish.DROPIN.unlink(); core.fsync_directory(finish.DROPIN.parent)
        core.checked_reload(record, 'main-fence-release')
        core.check_units(units, guards=True, qa_fence=True)
        finish.MARKER.unlink(); core.fsync_directory(finish.MARKER.parent)
        readback()
        stage = 'one_Main_Start'
        core.once_service(record, 'main-start', 'start', MAIN,
            lambda: lifecycle.service_attempt('start', MAIN, 45),
            on_attempt=lambda: counters.__setitem__('Main_Start_calls', 1))
        deadline = time.monotonic() + 45
        while True:
            state = lifecycle.started_state(hold, original['pid'])
            if state:
                break
            readback()
            need(time.monotonic() < deadline, 'ORIGINAL_MAIN_START_UNCONFIRMED_NO_REPLAY')
            time.sleep(1)
        pid = int(state['MainPID'])
        current = relay.process(pid)
        core.replacement_process(baseline['processes']['main'], current)
        need(core.environment_proof(pid) == baseline['environments'][MAIN], 'NEW_MAIN_ENVIRONMENT_CHANGED')
        new_main = lifecycle.identity(hold, types.SimpleNamespace(main_pid=pid, main_start_ticks=current['start_ticks']))
        need(new_main['entrypoint_sha256'] == original['entrypoint_sha256'], 'NEW_MAIN_ENTRYPOINT_CHANGED')
        record('main-start-readback.json', new_main)
        readback()
        stage = 'release_QA_fence'
        core.remove_root_guard(core.QA_FENCE, core.QA_FENCE_BYTES)
        core.checked_reload(record, 'qa-fence-release')
        core.check_units(units, guards=True)
        readback()
        stage = 'one_QA_Start'
        core.once_service(record, 'qa-start', 'start', QA,
            lambda: common.qa_service_attempt('start', 45),
            on_attempt=lambda: counters.__setitem__('QA_Start_calls', 1))
        new_qa = common.qa_started(hold, binding)
        qa_owners.add((new_qa['pid'], new_qa['process_start_ticks']))
        qa_current = relay.process(new_qa['pid'])
        core.replacement_process(baseline['processes']['qa'], qa_current)
        need(core.environment_proof(new_qa['pid']) == baseline['environments'][QA], 'NEW_QA_ENVIRONMENT_CHANGED')
        record('qa-start-readback.json', new_qa)
        stage = 'poststart_readback'
        readback()
        common.main_restarts_zero()
        source_cohort(core, baseline, manifest, installed_paths)
        core.check_units(units, guards=True)
        deadline = time.monotonic() + 45
        while True:
            try:
                actual_channel = common.channel_status()
                break
            except Exception:
                need(time.monotonic() < deadline, 'ORIGINAL_CHANNEL_RECONNECT_UNSETTLED_NO_REPLAY')
                time.sleep(1)
        need(actual_channel == channel, 'NATIVE_CHANNEL_IDENTITY_CHANGED')
        lifecycle.security()
        stage = 'restore_original_graceful_policy'
        for path in core.NOKILL.values():
            core.remove_root_guard(path, core.NOKILL_BYTES)
        core.checked_reload(record, 'restore-original-unit-policy')
        core.check_units(units)
        need(common.protected_files(cas) == unit_config, 'ORIGINAL_CONFIGURATION_NOT_RESTORED')
        readback()
        source_cohort(core, baseline, manifest, installed_paths)
        need(relay.process(new_main['pid']) == current
             and relay.process(new_qa['pid']) == qa_current
             and core.environment_proof(new_main['pid']) == baseline['environments'][MAIN]
             and core.environment_proof(new_qa['pid']) == baseline['environments'][QA],
             'NEW_MAIN_OR_QA_CHANGED_DURING_VERIFICATION')
        need(service_state(hold, MAIN)['NRestarts'] == '0'
             and service_state(hold, QA)['NRestarts'] == '0', 'AUTOMATIC_RESTART_OCCURRED')
        record('completed-receipt.json', {'state': 'TWO_CONTRACT_FILES_INSTALLED_ORIGINAL_RETAINED_DRAINING24',
            'source_commit': plan['source_commit'], 'plan_sha256': PLAN_SHA, 'main': new_main, 'qa': new_qa,
            'gate': INITIAL_GATE, 'runtime_files': 147, 'replacement_count': 2, 'new_files': 0,
            'existing_v1_selection_preserved': True, 'original_admission_preserved': True,
            'original_unit_configuration_restored': True, 'recovery_or_business_completion_claimed': False, **counters})
        print(json.dumps({'state': 'TWO_CONTRACT_FILES_INSTALLED_ORIGINAL_RETAINED_DRAINING24',
                          'gate': INITIAL_GATE, 'main_pid': new_main['pid'], 'qa_pid': new_qa['pid'], **counters}))
    except BaseException as error:
        if created:
            try:
                record('held.json', {'state': 'HELD_PRESERVE_ORIGINAL_NO_REPLAY', 'stage': stage,
                    'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                    'owned_guards_may_remain': True, 'source_installation_started': source_installation_started,
                    'installed_paths': sorted(installed_paths),
                    'automatic_rollback': False, **counters})
            except BaseException:
                pass
        raise
    finally:
        if dispatch is not None:
            os.close(dispatch)
        os.close(release)


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
        print(json.dumps({'state': 'HELD_NO_AUTOMATIC_REPLAY',
            'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
            'instructions': 'Observe original intents. No repeated service action, gate change, recovery or database restore.'}))
        raise SystemExit(65)

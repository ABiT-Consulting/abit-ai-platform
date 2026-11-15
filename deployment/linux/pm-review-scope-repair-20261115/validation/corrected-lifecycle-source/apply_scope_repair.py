#!/usr/bin/env python3
"""One existing PM-scope leaf, Main only. An UNFROZEN plan permits no action.

The separate preservation mode retains the historical unknown provider unchanged.
It does not weaken or invoke the full-hardening or native-publication controllers.
"""
import argparse
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
HERE = REPO / 'deployment/linux/pm-review-scope-repair-20261115'
PLAN = HERE / 'frozen-release.json'
PLAN_SHA = 'UNFROZEN'
PARENT = Path('/var/lib/abit-ai-erp-rollouts/pm-review-scope-repairs')
MAIN, QA = 'abit-ai.service', 'abit-ai-erp-review-qa.service'
TARGET = 'utils/agentic_relay_scope.py'
ORIGINAL_SHA = '50fc65add1abe1e5b50e1ccf3ae2bcafa33ab8ff26a39fca168ff7301be40055'
CANDIDATE_SHA = '1907f2010399336b0ae9053f66e0ecb3f1d9a560805f7ae07d0a3cc7d83f698f'
SUPPORT = REPO / 'deployment/linux/core-hardening-20261115/apply_core_hardening.py'
SUPPORT_SHA = 'f6477ec6b7173a7c7dd07a7423c037ea624ca759dc6383d2353f64bbcdd2a0b3'
CAPACITY_SUPPORT = REPO / 'deployment/linux/core-command-capacity-20261115/apply_command_capacity.py'
CAPACITY_SUPPORT_SHA = 'f9807a7f3277c9b6fa9b790ecf2b5064b9f4001c1549a8d3b8b5ad050cd13fb7'
PRESERVATION = HERE / 'preserved_scope.py'
PRESERVATION_SHA = '60f08968f147b227aa04226509ca4bcfb1f74c5a1d37d13d023dcad572c8afa8'
PROCESS_NAMES = frozenset({'main', 'qa', 'node', 'chrome', 'watchdog', 'xvfb',
    'egress', 'tunnel', 'mcp_auth', 'mcp_backend', 'mcp_tunnel'})


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
    need(isinstance(value, dict) and set(value) == {'state', 'epoch', 'active_admissions'}
         and value['state'] in {'draining', 'sealed'} and type(value['epoch']) is int
         and value['epoch'] >= 30 and type(value['active_admissions']) is int
         and value['active_admissions'] == 0, 'EXACT_BLOCKED_ZERO_ACTIVE_GATE_REQUIRED')


def validate_plan(value):
    keys = {'schema_version', 'state', 'source_commit', 'package', 'manifest_sha256',
        'tests_sha256', 'test_count', 'live_baseline', 'live_baseline_sha256', 'initial_gate',
        'support_sha256', 'capacity_support_sha256', 'preservation_sha256', 'native_publication'}
    need(isinstance(value, dict) and set(value) == keys and type(value['schema_version']) is int
         and value['schema_version'] == 1 and value['state'] == 'FROZEN_MAIN_ONLY_PM_SCOPE_REPAIR'
         and isinstance(value['source_commit'], str) and re.fullmatch('[a-f0-9]{40}', value['source_commit']),
         'EXACT_REVIEWED_ONE_LEAF_PLAN_REQUIRED')
    package, baseline = Path(value['package']), Path(value['live_baseline'])
    need(package.parent == REPO / '.git'
         and re.fullmatch('pm-review-scope-repair-20261115-[a-z0-9_]+', package.name)
         and baseline.parent == REPO / '.git' and baseline.suffix == '.json',
         'EXACT_PRIVATE_PACKAGE_REQUIRED')
    need(all(exact_hash(value[key]) for key in ('manifest_sha256', 'tests_sha256', 'live_baseline_sha256'))
         and type(value['test_count']) is int and value['test_count'] > 0,
         'EXACT_OFFLINE_VERIFICATION_REQUIRED')
    need(value['support_sha256'] == SUPPORT_SHA and value['capacity_support_sha256'] == CAPACITY_SUPPORT_SHA
         and value['preservation_sha256'] == PRESERVATION_SHA, 'EXACT_REVIEWED_GUARDS_REQUIRED')
    exact_gate(value['initial_gate'])
    native = value['native_publication']
    need(isinstance(native, dict) and set(native) == {'path', 'sha256', 'expected', 'channel_path',
         'channel_sha256', 'channel_expected'} and all(exact_hash(native[key]) for key in
         ('sha256', 'channel_sha256')), 'RETAINED_NATIVE_PUBLICATION_BINDING_REQUIRED')
    return value


def validate_manifest(value, plan):
    need(isinstance(value, dict) and set(value) == {'schema_version', 'source_commit', 'runtime', 'files'}
         and value['schema_version'] == 1 and value['source_commit'] == plan['source_commit']
         and value['runtime'] == str(RUNTIME) and isinstance(value['files'], list)
         and len(value['files']) == 1, 'EXACT_ONE_EXISTING_PYTHON_LEAF_REQUIRED')
    item = value['files'][0]
    need(set(item) == {'relative_path', 'kind', 'candidate_sha256', 'candidate_bytes', 'original', 'parent'}
         and item['relative_path'] == TARGET and item['kind'] == 'RuntimePython'
         and item['candidate_sha256'] == CANDIDATE_SHA and type(item['candidate_bytes']) is int
         and 0 < item['candidate_bytes'] <= 200000, 'ONLY_REVIEWED_PM_SCOPE_BYTES_ALLOWED')
    original = item['original']
    need(isinstance(original, dict) and set(original) ==
         {'sha256', 'uid', 'gid', 'mode', 'size', 'mtime_ns', 'xattrs'}
         and original['sha256'] == ORIGINAL_SHA
         and (original['uid'], original['gid'], original['mode']) == (999, 988, 0o644)
         and type(original['size']) is int and original['size'] > 0
         and type(original['mtime_ns']) is int and isinstance(original['xattrs'], dict),
         'EXACT_EXISTING_LIVE_LEAF_REQUIRED')
    parent = item['parent']
    need(isinstance(parent, dict) and set(parent) == {'dev', 'ino', 'uid', 'gid', 'mode', 'xattrs'}
         and parent['uid'] == 999 and parent['gid'] == 988 and isinstance(parent['xattrs'], dict),
         'EXACT_EXISTING_PARENT_REQUIRED')
    return value


def validate_product_tests(core, package, receipt, plan, baseline):
    """Re-derive 30 distinct passes from the retained 29-pass/1-error +1 pass.

    The failed first fixture attempt is mandatory evidence, never represented as
    a clean 30-case run. The controlled live-input proof is not fresh authority.
    """
    names = {
        'focused1-receipt.json': 'c93dee16a4dd69174fb884ef4c6d64c6042687479b4b866dcad3b21bfcf86ee1',
        'corrected-foreign-registration-receipt.json': 'a97fe8f0cdd87aa39e3d46cafe8a70e456d7013cf6fe9cc71a13d606cea4c08d',
        'candidate-live-input-proof.json': 'bd3429a357496fee278b9c66cbbd271f7968f0fa7c30d6f4acb012bd0757d7d9',
    }
    need(receipt == {'schema_version': 1, 'kind': 'DISTINCT_PASSES_ACROSS_RETAINED_29_PLUS_1',
         'candidate_sha256': CANDIDATE_SHA, 'distinct_passed': 30, 'initial_fixture_errors': 1,
         'clean_thirty_case_run_claimed': False, 'evidence': names}
         and plan['test_count'] == 30, 'EXACT_TRUTHFUL_SPLIT_TEST_EVIDENCE_REQUIRED')
    values = {name: strict_json(core.read(package / 'evidence' / name, pin))
              for name, pin in names.items()}
    first, corrected = (values[name] for name in ('focused1-receipt.json',
                                                'corrected-foreign-registration-receipt.json'))
    key = 'backend/open_webui/' + TARGET
    for value in (first, corrected):
        need(value['pins_unchanged'] is True and value['source_pins'][key] == CANDIDATE_SHA
             and value['executed_product_origins'][key] == CANDIDATE_SHA,
             'TESTED_PRODUCT_SOURCE_CHANGED')
    need(first['tests_run'] == 30 and first['unique_test_ids'] == 30
         and first['errors'] == 1 and first['failures'] == first['skips'] == 0
         and first['successful'] is False and len(first['passed_ids']) == 29,
         'RETAINED_FIRST_FIXTURE_ERROR_REQUIRED')
    missing = set(first['test_ids']) - set(first['passed_ids'])
    need(corrected['tests_run'] == corrected['unique_test_ids'] == 1
         and corrected['errors'] == corrected['failures'] == corrected['skips'] == 0
         and corrected['successful'] is True and set(corrected['test_ids']) == set(corrected['passed_ids']) == missing
         and len(set(first['passed_ids']) | set(corrected['passed_ids'])) == 30,
         'EXACT_CORRECTED_MISSING_CASE_REQUIRED')
    proof = values['candidate-live-input-proof.json']
    positive = proof['candidate_actual_packet_positive']
    need(positive['accepted_under_controlled_protocol_peer'] is True
         and positive['packet_unchanged'] is True and positive['real_network_calls'] == 0
         and proof['source_pins_unchanged'] is True
         and proof['source_pins'][str(REPO / key)] == CANDIDATE_SHA,
         'EXACT_CONTROLLED_LIVE_INPUT_PROOF_REQUIRED')
    for name, pin in proof['source_pins'].items():
        if name.startswith(str(RUNTIME) + '/'):
            need(baseline['core_files'].get(name, {}).get('sha256') == pin,
                 'TESTED_LIVE_DEPENDENCY_CHANGED')
    return {'distinct_passed': 30, 'initial_fixture_errors_retained': 1,
            'clean_thirty_case_run_claimed': False}


def validate_baseline(value, core, plan):
    exact_gate(value['initial_gate'])
    need(value['initial_gate'] == plan['initial_gate'] and set(value['processes']) == PROCESS_NAMES
         and len({row['pid'] for row in value['processes'].values()}) == 11
         and set(value['environments']) == {MAIN, QA} and set(value['static_units']) == {MAIN, QA}
         and len(value['core_files']) == 147 and value['core_files'][str(RUNTIME / TARGET)]['sha256'] == ORIGINAL_SHA,
         'FRESH_COMPLETE_147_FILE_11_PROCESS_BASELINE_REQUIRED')
    need(isinstance(value.get('preserved_state'), dict)
         and sha(canonical(value['preserved_state'])) == value.get('preserved_state_sha256')
         and exact_hash(value.get('legacy_journals_sha256')),
         'EXACT_PRIVATE_PRESERVATION_BASELINE_REQUIRED')
    core.validate_history_archive(value)
    core.validate_format_capture(value)
    return value


def validate_binding(value, plan, baseline):
    need(isinstance(value, dict) and set(value) == {'schema_version', 'state', 'plan_sha256',
         'source_commit', 'main', 'qa', 'initial_gate', 'operation_directory', 'deployment_authorized'}
         and type(value['schema_version']) is int and value['schema_version'] == 1
         and value['state'] == 'REVIEWED_MAIN_ONLY_PM_SCOPE_PRESERVATION'
         and value['plan_sha256'] == PLAN_SHA and value['source_commit'] == plan['source_commit']
         and value['initial_gate'] == plan['initial_gate'] and value['deployment_authorized'] is True,
         'EXACT_ROOT_REVIEWED_BINDING_REQUIRED')
    for name in ('main', 'qa'):
        item, prior = value[name], baseline['processes'][name]
        need(isinstance(item, dict) and set(item) == {'pid', 'process_start_ticks', 'entrypoint_sha256'}
             and type(item['pid']) is int and item['pid'] == prior['pid']
             and item['process_start_ticks'] == prior['start_ticks']
             and exact_hash(item['entrypoint_sha256']), 'EXACT_PROCESS_BIRTH_ENTRYPOINT_BINDING_REQUIRED')
    destination = Path(value['operation_directory'])
    need(destination.parent == PARENT and re.fullmatch('scope-[a-f0-9]{12}', destination.name),
         'NEW_SINGLE_USE_PRIVATE_OUTPUT_REQUIRED')
    return value


def source_cohort(core, baseline, manifest, installed=False):
    need((ROOT / 'current/runtime/open_webui').resolve() == RUNTIME, 'CURRENT_RELEASE_CHANGED')
    expected = dict(baseline['core_files'])
    item = manifest['files'][0]
    need(expected.get(str(RUNTIME / TARGET)) == item['original'], 'BASELINE_SELECTED_LEAF_CHANGED')
    if installed:
        expected[str(RUNTIME / TARGET)] = {**item['original'], 'sha256': CANDIDATE_SHA,
                                          'size': item['candidate_bytes']}
    paths = set((RUNTIME / 'utils').rglob('*.py'))
    paths |= set((RUNTIME / 'utils/agentic_contracts').rglob('*.json'))
    paths.add(RUNTIME / 'routers/agentic_governance.py')
    need(len(paths) == 147 and set(map(str, paths)) == set(expected)
         and sum(path.suffix == '.py' for path in paths) == 144
         and sum(path.suffix == '.json' for path in paths) == 3, 'EXACT_UNEXPANDED_RUNTIME_COHORT_REQUIRED')
    for name, proof in expected.items():
        need(core.file_proof(Path(name)) == proof, 'SELECTED_OR_UNSELECTED_SOURCE_CHANGED')
    need(core.parent_proof((RUNTIME / TARGET).parent) == item['parent'], 'SELECTED_PARENT_CHANGED')


def atomic_scope_leaf(core, target, raw, item):
    """Reviewed CAS body, scoped here because full14 TARGETS excludes this leaf."""
    need(target == RUNTIME / TARGET and item['relative_path'] == TARGET
         and item['candidate_sha256'] == CANDIDATE_SHA and item['original']['sha256'] == ORIGINAL_SHA,
         'ONLY_EXACT_PM_SCOPE_REPLACEMENT_ALLOWED')
    parent, original = item['parent'], item['original']
    need(core.parent_proof(target.parent) == parent and core.file_proof(target) == original
         and sha(raw) == CANDIDATE_SHA, 'SOURCE_CAS_BEFORE_TEMP_FAILED')
    metadata = {key: original[key] for key in ('uid', 'gid', 'mode', 'mtime_ns', 'xattrs')}
    temp = core.prepare_temp(target, raw, metadata)
    try:
        expected = {**metadata, 'sha256': CANDIDATE_SHA, 'size': len(raw)}
        need(core.file_proof(temp) == expected and core.parent_proof(target.parent) == parent
             and core.file_proof(target) == original, 'SOURCE_CAS_BEFORE_RENAME_FAILED')
        os.replace(temp, target)
        core.fsync_directory(target.parent)
        need(core.file_proof(target) == expected, 'INSTALLED_SOURCE_METADATA_OR_BYTES_CHANGED')
    finally:
        temp.unlink(missing_ok=True)


def check_units(core, units, *, guarded=False, fence=None):
    core.check_units({MAIN: units[MAIN]}, guards=guarded, main_fence=fence)
    core.check_units({QA: units[QA]})


def exact_qa(core, relay, common, hold, baseline, binding):
    need(common.qa_identity(hold, binding) == binding['qa']
         and relay.process(binding['qa']['pid']) == baseline['processes']['qa']
         and core.environment_proof(binding['qa']['pid']) == baseline['environments'][QA],
         'QA_MUST_REMAIN_EXACT_WITHOUT_RESTART')


def install_main_only(core, capacity, lifecycle, finish, hold, relay, common, *,
                      record, destination, item, candidate, baseline, binding,
                      units, original, readback, source_check, counters):
    """Single forward path; every ambiguous action holds, never repeats/rolls back.

    A lost systemctl ACK may be reconciled only by positive exact state readback.
    No second Stop/Start, QA lifecycle action, force kill or gate mutation exists.
    """
    record('main-graceful-guard-intent.json', {'unit': MAIN, 'SendSIGKILL': 'no'})
    core.create_root_guard(core.NOKILL[MAIN], core.NOKILL_BYTES)
    finish.install_start_fence(hold, destination, original)
    check_units(core, units, guarded=True, fence=finish.DROPIN)
    readback()
    current_main, _ = capacity.ensure_service_identity(core, relay, lifecycle, common, hold, baseline, binding)
    need(current_main == original, 'ORIGINAL_MAIN_CHANGED_IMMEDIATELY_BEFORE_STOP')
    common.main_restarts_zero()
    core.once_service(record, 'main-stop', 'stop', MAIN,
        lambda: lifecycle.service_attempt('stop', MAIN, 45),
        on_attempt=lambda: counters.__setitem__('Main_Stop_calls', 1))
    record('main-stop-readback.json', capacity.wait_stopped(hold, MAIN, original, readback))
    check_units(core, units, guarded=True, fence=finish.DROPIN)
    source_check(False)
    target = RUNTIME / TARGET
    record('selected-bytecode.json', core.quarantine_bytecode([target], destination / 'pycache-originals', record))
    readback()
    record('scope-source-intent.json', {'path': str(target), 'original': item['original'],
                                      'candidate_sha256': CANDIDATE_SHA})
    counters['source_replacements_started'] = 1
    atomic_scope_leaf(core, target, candidate, item)
    counters['source_replacements_completed'] = 1
    source_check(True)
    need(core.selected_caches([target]) == [], 'SELECTED_CACHE_REAPPEARED')
    readback()
    check_units(core, units, guarded=True, fence=finish.DROPIN)
    lifecycle.security()
    finish.root_file(finish.DROPIN, 0o644, finish.FENCE_BYTES)
    finish.root_file(finish.MARKER, 0o600, finish.marker_bytes(hold, destination, original))
    record('main-fence-release-intent.json', {'unit': MAIN, 'dropin': str(finish.DROPIN)})
    finish.DROPIN.unlink()
    core.fsync_directory(finish.DROPIN.parent)
    core.checked_reload(record, 'main-fence-release')
    check_units(core, units, guarded=True)
    finish.MARKER.unlink()
    core.fsync_directory(finish.MARKER.parent)
    readback()
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
    current = relay.process(int(state['MainPID']))
    core.replacement_process(baseline['processes']['main'], current)
    need(core.environment_proof(current['pid']) == baseline['environments'][MAIN],
         'REPLACEMENT_MAIN_ENVIRONMENT_CHANGED')
    new_main = lifecycle.identity(hold, types.SimpleNamespace(main_pid=current['pid'],
                                                main_start_ticks=current['start_ticks']))
    need(new_main['entrypoint_sha256'] == original['entrypoint_sha256'], 'MAIN_ENTRYPOINT_CHANGED')
    record('main-start-readback.json', new_main)
    common.main_restarts_zero()
    readback()
    source_check(True)
    check_units(core, units, guarded=True)
    return new_main, current


def storage_budget(core, relay, hold, baseline, item, destination):
    value = relay.capacity(hold, destination.parent)
    files = set(baseline['protected_configuration']) | set(core.protected_history_archive(baseline)['files'])
    files.add(core.protected_format_capture(baseline)['path'])
    extra = 2 * sum(Path(name).stat().st_size for name in files)
    extra += item['original']['size'] + item['candidate_bytes']
    extra += sum(path.stat().st_size for path in core.selected_caches([RUNTIME / TARGET]))
    extra += 2 * len(canonical(baseline)) + 67_108_864
    need(shutil.disk_usage(destination.parent).free >= value['backup_reserve_bytes']
         + value['minimum_remaining_bytes'] + extra, 'BACKUP_SOURCE_CACHE_AND_EVIDENCE_RESERVE_REQUIRED')
    return {**value, 'source_cache_protected_evidence_reserve_bytes': extra}


def main(args):
    need(args.execute != args.preflight_only and os.geteuid() == 0
         and os.uname().nodename == 'abit-dev-linux', 'EXACT_ROOT_REVIEW_MODE_REQUIRED')
    need(exact_hash(PLAN_SHA) and exact_hash(PRESERVATION_SHA), 'PM_SCOPE_RELEASE_NOT_FROZEN')
    sys.dont_write_bytecode = True
    core = pinned_module('scope_core_guards', SUPPORT, SUPPORT_SHA)
    capacity = pinned_module('scope_capacity_guards', CAPACITY_SUPPORT, CAPACITY_SUPPORT_SHA)
    preservation = pinned_module('scope_preservation', PRESERVATION, PRESERVATION_SHA)
    need(exact_hash(args.controller_sha256)
         and sha(core.read(Path(__file__).resolve())) == args.controller_sha256, 'EXACT_CONTROLLER_REQUIRED')
    plan = validate_plan(strict_json(core.read(PLAN, PLAN_SHA)))
    baseline = validate_baseline(strict_json(core.read(Path(plan['live_baseline']),
                                                      plan['live_baseline_sha256'], private=True)), core, plan)
    binding = validate_binding(strict_json(core.read(args.bindings, args.bindings_sha256, private=True)), plan, baseline)
    browser = strict_json(core.read(args.main_task_receipt, args.main_task_receipt_sha256, private=True))
    capacity.validate_browser(core, browser, binding['main'])
    package = Path(plan['package'])
    manifest = validate_manifest(strict_json(core.read(package / 'manifest.json', plan['manifest_sha256'])), plan)
    item = manifest['files'][0]
    candidate = core.read(package / 'candidate/open_webui' / TARGET, CANDIDATE_SHA)
    need(len(candidate) == item['candidate_bytes'], 'CANDIDATE_SIZE_CHANGED')
    compile(candidate, str(RUNTIME / TARGET), 'exec')
    tests = strict_json(core.read(package / 'test-receipt.json', plan['tests_sha256']))
    product_tests = validate_product_tests(core, package, tests, plan, baseline)
    os.umask(0o077)
    helpers = {name: core.module(name, core.S / relative, pin)
               for name, (relative, pin) in core.HELPERS.items()}
    common, history, lifecycle, cas, finish, hold = (helpers[name] for name in
        ('common', 'history', 'lifecycle', 'cas', 'finish', 'hold'))
    relay = core.module('relay', core.RELAY_HELPER, core.RELAY_HELPER_SHA)
    # Nested lifecycle gate loads, if introduced in the reviewed helpers, remain pinned.
    core.bind_gate_loader(hold)
    release = finish.private_lock('/run/lock/abit-ai-agentic-release.lock', 0)
    dispatch, record, destination = None, None, None
    created, stage = False, 'preflight'
    counters = {'Main_Stop_calls': 0, 'Main_Start_calls': 0, 'QA_Stop_calls': 0, 'QA_Start_calls': 0,
        'gate_changes': 0, 'force_kills': 0, 'database_restores': 0, 'model_requests': 0,
        'ERP_actions': 0, 'relay_actions': 0, 'original_recovery_calls': 0,
        'source_replacements_started': 0, 'source_replacements_completed': 0}
    try:
        with hold.as_service():
            dispatch = finish.private_lock(DATA / 'erp-dot-dispatch.lock', os.geteuid())
        source_cohort(core, baseline, manifest)
        core.protected_runtime(relay, baseline)
        lifecycle.security()
        original, qa = capacity.ensure_service_identity(core, relay, lifecycle, common, hold, baseline, binding)
        units = {unit: core.static_unit_state(unit) for unit in (MAIN, QA)}
        need(units == baseline['static_units'], 'ORIGINAL_UNIT_CONFIGURATION_CHANGED')
        check_units(core, units)
        before = preservation.capture(hold, plan['initial_gate'])
        qa_owner = (qa['pid'], qa['process_start_ticks'])
        preservation.preserve(baseline['preserved_state'], before, qa_owner=qa_owner)
        legacy = relay.legacy_files()
        need(sha(canonical(legacy)) == baseline['legacy_journals_sha256'], 'RELAY_JOURNAL_CHANGED')
        # Reuse the exact accepted native receipt validator; no new native policy.
        native = capacity.native_positive(core, plan)
        channel = common.channel_status()
        unit_config = common.protected_files(cas)
        destination = Path(binding['operation_directory'])
        budget = storage_budget(core, relay, hold, baseline, item, destination)
        need(not any(os.path.lexists(path) for path in
             [finish.MARKER, finish.DROPIN, core.QA_FENCE, *core.NOKILL.values()]),
             'EXISTING_RUNTIME_GUARDS_PRESERVED')
        need(not os.path.lexists(destination), 'ORIGINAL_OPERATION_EXISTS_OBSERVE_ONLY')

        def source_check(installed):
            source_cohort(core, baseline, manifest, installed)

        def readback():
            preservation.preserve(before, preservation.capture(hold, plan['initial_gate']), qa_owner=qa_owner)
            exact_qa(core, relay, common, hold, baseline, binding)
            core.protected_runtime(relay, baseline)
            need(relay.legacy_files() == legacy, 'RELAY_WORK_OR_JOURNAL_CHANGED')
            need(capacity.native_positive(core, plan) == native, 'RETAINED_NATIVE_PUBLICATION_CHANGED')

        readback()
        if args.preflight_only:
            print(json.dumps({'state': 'MAIN_ONLY_PM_SCOPE_PREFLIGHT_PASSED',
                'gate': plan['initial_gate'], 'targets': 1, 'new_files': 0, 'runtime_files': 147,
                'f33_provider_unknown_preserved': True, 'qa_pass_and_blocked_pm_preserved': True,
                'browser_current_tasks': 0, 'capacity': budget, **counters}))
            return
        parent = PARENT.lstat()
        need(PARENT.resolve() == PARENT and parent.st_uid == parent.st_gid == 0
             and stat.S_IMODE(parent.st_mode) == 0o700, 'PRESTAGED_ROOT_PRIVATE_PARENT_REQUIRED')
        destination.mkdir(mode=0o700)
        created = True
        record = lambda name, value: history.private_new(destination / name, canonical(value))
        record('release-intent.json', {'plan_sha256': PLAN_SHA, 'controller_sha256': args.controller_sha256,
            'source_commit': plan['source_commit'], 'main': original, 'qa': qa, 'product_tests': product_tests,
            'initial_gate': plan['initial_gate'], 'preserved_state': before, 'capacity': budget,
            'native_publication': native, 'active_admissions': 0, 'unknown_provider_reclassified': False})
        for name, path, pin in [('frozen-release.json', PLAN, PLAN_SHA),
            ('live-baseline.json', Path(plan['live_baseline']), plan['live_baseline_sha256']),
            ('manifest.json', package / 'manifest.json', plan['manifest_sha256']),
            ('bindings.json', args.bindings, args.bindings_sha256),
            ('browser-proof.json', args.main_task_receipt, args.main_task_receipt_sha256)]:
            history.private_new(destination / name, core.read(path, pin))
        for index, (name, proof) in enumerate(unit_config.items()):
            history.private_new(destination / ('configuration-' + str(index) + '.private'),
                                core.read(Path(name), proof['sha256']))
        record('configuration-map.json', unit_config)
        archive = core.protected_history_archive(baseline)
        for index, (name, proof) in enumerate(sorted(archive['files'].items())):
            history.private_new(destination / ('history-input-' + str(index) + '.private'),
                                core.read(Path(name), proof['sha256']))
        record('history-input-map.json', archive)
        core.backup_format_capture(baseline, history, destination, record)
        history.private_new(destination / 'scope.before', core.read(RUNTIME / TARGET, ORIGINAL_SHA))
        history.private_new(destination / 'scope.after', candidate)
        record('rollback-plan.json', {'automatic': False, 'separate_fresh_review_required': True,
            'database_restore': False, 'replay_original_service_intents': False, 'new_files': 0,
            'exact_source_and_metadata': item, 'cache_quarantine_retained': True})
        stage = 'eleven_consistent_backups_before_Stop'
        backup = relay.backups(hold, history, destination)
        need(backup['backup_count'] == 11 and backup['database_restores'] == 0, 'ELEVEN_VALIDATED_BACKUPS_REQUIRED')
        record('database-backups.json', backup)
        readback()
        source_check(False)
        capacity.validate_browser(core, strict_json(core.read(args.main_task_receipt,
            args.main_task_receipt_sha256, private=True)), original)
        capacity.ensure_service_identity(core, relay, lifecycle, common, hold, baseline, binding)
        need(common.protected_files(cas) == unit_config, 'CONFIGURATION_CHANGED_BEFORE_STOP')
        stage = 'one_Main_stop_one_leaf_one_Main_start'
        new_main, new_process = install_main_only(core, capacity, lifecycle, finish, hold, relay, common,
            record=record, destination=destination, item=item, candidate=candidate, baseline=baseline,
            binding=binding, units=units, original=original, readback=readback,
            source_check=source_check, counters=counters)
        stage = 'poststart_channel_readback'
        deadline = time.monotonic() + 45
        while True:
            try:
                actual_channel = common.channel_status()
                break
            except Exception:
                readback()
                need(time.monotonic() < deadline, 'NATIVE_CHANNEL_RECONNECT_UNCONFIRMED_NO_REPLAY')
                time.sleep(1)
        need(actual_channel == channel, 'NATIVE_CHANNEL_IDENTITY_CHANGED')
        lifecycle.security()
        stage = 'restore_only_Main_graceful_policy'
        record('restore-main-graceful-policy-intent.json', {'unit': MAIN})
        core.remove_root_guard(core.NOKILL[MAIN], core.NOKILL_BYTES)
        core.checked_reload(record, 'restore-main-unit-policy')
        check_units(core, units)
        need(common.protected_files(cas) == unit_config, 'ORIGINAL_CONFIGURATION_NOT_RESTORED')
        readback()
        source_check(True)
        need(relay.process(new_main['pid']) == new_process
             and core.environment_proof(new_main['pid']) == baseline['environments'][MAIN],
             'REPLACEMENT_MAIN_CHANGED_DURING_FINAL_VERIFICATION')
        common.main_restarts_zero()
        record('completed-receipt.json', {'state': 'ONE_PM_SCOPE_LEAF_INSTALLED_MAIN_ONLY_GATE_UNCHANGED',
            'source_commit': plan['source_commit'], 'plan_sha256': PLAN_SHA, 'main': new_main, 'qa': qa,
            'gate': plan['initial_gate'], 'runtime_files': 147, 'replacement_count': 1, 'new_files': 0,
            'f33_provider_unknown_preserved': True, 'qa_pass_and_blocked_pm_preserved': True,
            'original_unit_configuration_restored': True, 'first_flow_closed_claimed': False,
            'pm_submission_or_recovery_performed': False, **counters})
        print(json.dumps({'state': 'ONE_PM_SCOPE_LEAF_INSTALLED_MAIN_ONLY_GATE_UNCHANGED',
                          'gate': plan['initial_gate'], 'main_pid': new_main['pid'], **counters}))
    except BaseException as error:
        if created:
            try:
                record('held.json', {'state': 'HELD_PRESERVE_ORIGINAL_NO_REPLAY', 'stage': stage,
                    'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                    'owned_guards_may_remain': True, 'automatic_rollback': False, **counters})
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
            'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__}))
        raise SystemExit(65)

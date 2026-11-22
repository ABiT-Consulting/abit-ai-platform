"""Compose a fixed six-leaf release from the reviewed graceful Core operator."""
from pathlib import Path
import ast, hashlib, json, textwrap, difflib
ROOT = Path(__file__).resolve().parents[3]
E = ROOT / "docs/evidence/2026-11-22-native-verification-activation"
P = ROOT / "deployment/linux/native-verification-activation-20261122"
old = (E / "reference/release-core-graceful.py").read_text()
assert hashlib.sha256(old.encode()).hexdigest() == "d98a77389a7cf3300aedd4f336ab5557d3f28e042c469e425e18fb87fb234a8a"
s = old
s = s.replace("orchestration-recovery-20261122T1015", "native-verification-20261122T1615")
s = s.replace("zzzz-orchestration-recovery-20261122.conf", "zzzz-native-verification-20261122T1615.conf")
s = s.replace("ACTOR = OWNER + ':core-graceful'", "COMMAND_PREFIX = OWNER + ':native-verification-20261122T1615'\nACTOR = COMMAND_PREFIX + ':core-graceful'")
s = s.replace("OWNER + ':' + cycle['mode'] + ':core-", "COMMAND_PREFIX + ':' + cycle['mode'] + ':core-")
s = s.replace("ALLOWED = {'utils/agentic_transport_retry.py', 'utils/agentic_relay_scope.py', 'routers/agentic_governance.py'}", """ALLOWED = {'routers/agentic_governance.py', 'utils/agentic_contracts/validator.py',
           'utils/agentic_contracts/schemas/governance_v1.schema.json',
           'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
           'utils/agentic_native_execution.py'}
NEW_LEAVES = {'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
              'utils/agentic_native_execution.py'}
CONFIG_PATH = BASE / 'config/erp-assignment/resolver.json'
SOURCE_MANIFEST_SHA = '77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6'
PRESERVED_NAMES = {'preserved_0', 'preserved_1', 'preserved_2', 'preserved_3', 'qa', 'relay', 'watchdog'}
DATABASES = ('webui.db', 'agentic-governance.sqlite', 'abitdot-primary.sqlite',
             'aibit-chatgpt-relay.sqlite', 'abit-slack.sqlite', 'abit-telegram.sqlite',
             'codex-workspace-runs.sqlite', 'erp-dot-issues.sqlite',
             'erp-pending-review-qa.sqlite', 'erp-task-resolution.sqlite',
             'maintenance-admission/gate.sqlite')""")
replacement = {}
replacement["http"] = '''
def http(path, port=8888):
    require((port, path) in ((8888, '/health'), (8888, '/api/config'),
                            (8810, '/runtime-status')), 'Unsupported status path')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                     headers={'Connection': 'close'}, method='GET')
    maximum = 4096 if port == 8810 else 262144
    with opener.open(request, timeout=5) as response:
        require(response.status == 200, 'Status HTTP code differs')
        encoded = response.read(maximum + 1)
        require(len(encoded) <= maximum, 'Status body exceeded fixed bound')
        value = json.loads(encoded)
        require(type(value) is dict, 'Status body is not an object')
        return value
'''
replacement["relay_idle"] = '''
def relay_idle(m):
    expected = m['preserved_processes']['relay']
    require(process(expected['pid']) == expected, 'Relay process changed before status')
    value = http('/runtime-status', 8810)
    require(value.get('ok') is True and value.get('protocol') == 'v1_graceful_relay_drain'
            and value.get('pid') == expected['pid']
            and value.get('draining') is False and value.get('server_closed') is False,
            'Relay lifecycle status differs')
    counts = ('active_http_handlers', 'active_relay_turns', 'erp_active_turns', 'erp_inspections')
    require(all(type(value.get(k)) is int and value[k] == 0 for k in counts),
            'Relay has active accepted work; preserve it')
    require(process(expected['pid']) == expected, 'Relay changed during status')
    return {k: value[k] for k in ('protocol', 'pid', 'draining', 'server_closed', *counts)}
'''
replacement["source"] = '''
def source(m, mode):
    require(mode in ('before', 'after', 'partial'), 'Unsupported source phase')
    require((BASE / 'current/runtime/open_webui').resolve() == LIVE, 'Active runtime changed')
    baseline = m['baseline']
    require(len(baseline) == 378 and set(m['changes']) == ALLOWED
            and {k for k, v in baseline.items() if v['sha256'] is None} == NEW_LEAVES,
            'Unexpected complete source scope')
    expected_paths = set(baseline) - NEW_LEAVES
    actual_paths = {str(p.relative_to(LIVE)) for p in LIVE.rglob('*.py')}
    actual_paths.update(str(p.relative_to(LIVE)) for p in
                        (LIVE / 'utils/agentic_contracts/schemas').glob('*.json'))
    require(actual_paths - NEW_LEAVES == expected_paths
            and actual_paths <= set(baseline), 'Source inventory changed')
    for rel, row in baseline.items():
        path = LIVE / rel
        require(path.resolve() == path and not path.is_symlink(), 'Source path changed')
        expected = row['sha256']
        if rel in ALLOWED and mode == 'after':
            expected = m['changes'][rel]['after_sha256']
        permitted = {expected}
        if rel in ALLOWED and mode == 'partial':
            permitted.add(m['changes'][rel]['after_sha256'])
        actual = current_hash(path)
        require(actual in permitted, 'Installed source changed: ' + rel)
        if actual is not None:
            info = path.stat()
            require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                    (row['uid'], row['gid'], int(row['mode'], 8)),
                    'Source metadata changed: ' + rel)
    for rel, row in m['changes'].items():
        info = (LIVE / rel).parent.stat()
        require((info.st_dev, info.st_ino) == (row['parent_device'], row['parent_inode']),
                'Target parent changed')
    for row in m['configuration']:
        require(file_metadata(Path(row['path'])) == row, 'Runtime configuration changed')
    default_off(m)
'''
replacement["backup"] = '''
def backup(m):
    protected(STAGE, True)
    protected(STAGE / 'backup-receipt.json')
    require(filehash(STAGE / 'backup-receipt.json') == m['backup_receipt_sha256'],
            'Backup receipt changed')
    report = json.loads(raw(STAGE / 'backup-receipt.json'))
    require(report['status'] == 'PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED'
            and [r['relative_source'] for r in report['databases']] == list(DATABASES),
            'All eleven verified backups required')
    for row in report['databases']:
        path = STAGE / 'database' / row['relative_source'].replace('/', '--')
        protected(path)
        require(filehash(path) == row['backup_sha256'] and row['integrity'] == 'ok'
                and row['isolated_restore_integrity'] == 'ok'
                and row['isolated_restore_schema_and_counts_match'] is True,
                'Backup verification changed')
    recorded = {r['relative_source']: r for r in report['source']}
    require(set(recorded) == ALLOWED, 'Rollback source coverage changed')
    for rel, row in m['changes'].items():
        path = STAGE / 'source' / rel.replace('/', '--')
        record = recorded[rel]
        require(record['sha256'] == row['before_sha256'], 'Rollback preimage binding changed')
        if row['before_sha256'] is None:
            require(record.get('exists') is False and not os.path.lexists(path),
                    'New-leaf original absence proof changed')
        else:
            protected(path)
            require(filehash(path) == row['before_sha256'], 'Rollback bytes changed')
    require(report['unit_files'] == m['graceful']['unit_files'], 'Unit backup scope differs')
    for index, row in enumerate(report['unit_files']):
        path = STAGE / 'configuration' / ('unit--' + str(index))
        protected(path)
        require(filehash(path) == row['sha256'], 'Unit backup changed')
'''
replacement["base_manifest"] = '''
def base_manifest():
    require(MANIFEST_SHA != 'UNBOUND', 'Unbound release cannot execute')
    encoded = raw(PACKAGE / 'release-manifest.json')
    require(sha(encoded) == MANIFEST_SHA, 'Release manifest changed')
    m = json.loads(encoded)
    require(m['purpose'] == 'NATIVE_VERIFICATION_DEFAULT_OFF_CORE_INSTALL_V1'
            and m['runtime_root'] == str(LIVE)
            and m['source_package_sha256'] == SOURCE_MANIFEST_SHA,
            'Unexpected release scope')
    package_bytes = raw(PACKAGE / 'source-manifest.json')
    require(sha(package_bytes) == SOURCE_MANIFEST_SHA, 'Reviewed source package changed')
    product = json.loads(package_bytes)
    pinned = {r['path']: r for r in product['core_files']}
    require(set(m['changes']) == ALLOWED == set(pinned), 'Unexpected runtime targets')
    for rel, row in m['changes'].items():
        require(row['before_sha256'] == m['baseline'][rel]['sha256']
                == pinned[rel]['preimage_sha256']
                and row['after_sha256'] == pinned[rel]['postimage_sha256'],
                'Source pre/postimage binding changed')
        require(row['candidate'] == str(PACKAGE / 'payload/open_webui' / rel),
                'Candidate path changed')
        require(filehash(Path(row['candidate'])) == row['after_sha256'], 'Candidate changed')
    for row in m['evidence']:
        require(filehash(Path(row['path'])) == row['sha256'], 'Reviewed evidence changed')
    require(m['validation']['reviewed'] is True
            and m['validation']['api_verify_passed'] is True
            and m['validation']['api_integration_failed'] == 0,
            'Required review/API gates incomplete')
    return m, encoded
'''
replacement["validate_manifest"] = '''
def validate_manifest():
    m, encoded = base_manifest()
    require(m.get('live_activation_authorized') is True, 'Final activation binding required')
    require(set(m['preserved_processes']) == PRESERVED_NAMES,
            'Exact seven-process preservation set required')
    g = m.get('graceful', {})
    require(g.get('policy') == 'CORE_ONLY_GRACEFUL_V1'
            and g.get('cgroup') == CGROUP
            and g.get('temporary_dropin_sha256') == sha(OVERRIDE_BYTES),
            'Graceful release policy is unbound')
    require(g.get('maintenance_module_sha256') ==
            m['baseline']['utils/agentic_maintenance.py']['sha256'],
            'Maintenance CLI source binding differs')
    require(g.get('gate_before', {}).get('state') == 'open'
            and type(g['gate_before'].get('epoch')) is int,
            'Initial open epoch required')
    require(g.get('boot') == boot_identity(), 'Original boot evidence changed')
    identities = [m['core_before'], m['preserved_processes']['relay'],
                  m['preserved_processes']['preserved_2']]
    birth = g['boot']['btime'] + min(r['start_ticks'] for r in identities) / g['boot']['clock_ticks']
    require(g.get('original_process_birth_min') == birth, 'Process birth binding differs')
    require(set(g.get('historical', {})) == {'relay', 'dot'}, 'Historical cohorts missing')
    for summary in g['historical'].values():
        require(type(summary['count']) is int and 0 <= summary['count'] <= 64
                and sum(summary['states'].values()) == summary['count'],
                'Historical cohort exceeds fixed bounds')
        require(summary['count'] == 0 or summary['updated_max'] < birth,
                'Unsettled provider record is not older than the preserved cohort')
    require(g['unit_effective'] == ORIGINAL_EFFECTIVE, 'Original unit behavior differs')
    require(m['app_version'] == '0.11.4' and m['native_registration_enabled'] is False,
            'Default-off installation contract differs')
    unit_files(m)
    default_off(m)
    return m, encoded
'''
replacement["install_source"] = '''
def install_source(m, r, cycle):
    target_mode = 'after' if cycle['mode'] == 'apply' else 'before'
    if completed(cycle, 'install'):
        source(m, target_mode)
        return
    owner()
    held(m, cycle)
    preserve(m)
    stopped(m, cycle)
    check_prepared(m)
    work = ledger_snapshot(m, cycle['previous_identity'], strict=cycle['mode'] == 'apply',
                           idle=cycle['mode'] == 'apply', check_identity=False)
    source(m, 'partial')
    begin_intent(r, cycle, 'install', {'mode': cycle['mode'], 'post_stop_work': work,
                                     'no_database_restore': True})
    for rel, row in sorted(m['changes'].items()):
        owner()
        held(m, cycle)
        preserve(m)
        stopped(m, cycle)
        target = LIVE / rel
        wanted = row['after_sha256'] if cycle['mode'] == 'apply' else row['before_sha256']
        actual = current_hash(target)
        require(actual in (row['before_sha256'], row['after_sha256']),
                'Concurrent source change')
        if actual != wanted:
            if wanted is None:
                require(cycle['mode'] == 'rollback' and rel in NEW_LEAVES
                        and actual == row['after_sha256'],
                        'Only an owned new postimage can be removed')
                info = target.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                        (m['baseline'][rel]['uid'], m['baseline'][rel]['gid'],
                         int(m['baseline'][rel]['mode'], 8)), 'New source metadata changed')
                target.unlink()
                fsyncdir(target.parent)
            else:
                path = STAGE / ('graceful-candidate' if cycle['mode'] == 'apply' else 'source') / rel.replace('/', '--')
                protected(path)
                data = raw(path)
                require(sha(data) == wanted, 'Protected source bytes changed')
                if actual is None:
                    require(cycle['mode'] == 'apply' and rel in NEW_LEAVES,
                            'Unexpected absent existing source')
                    install_new(target, data, m['baseline'][rel])
                else:
                    install(target, data, m['baseline'][rel])
        require(current_hash(target) == wanted, 'Installed source readback failed')
    source(m, target_mode)
    stopped(m, cycle)
    finished(r, cycle, 'install', {'source_mode': target_mode, 'targets': sorted(ALLOWED),
                                  'databases_restored': False})
'''
replacement["core_from_start"] = '''
def core_from_start(m, cycle, override=True, receipt=None):
    value = service(m, override)
    intent = cycle['intents']['start']
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and int(value.get('MainPID') or 0) > 0, 'Owned Core start not active')
    current = process(int(value['MainPID']))
    require(current['start_ticks'] >= intent['requested_tick']
            and current != cycle['previous_identity'], 'Core predates the one-use start')
    generation = {'pid': current['pid'], 'start_ticks': current['start_ticks']}
    if 'start_process' in cycle:
        require(generation == cycle['start_process'], 'One-use start generation changed')
    else:
        cycle['start_process'] = generation
        if receipt is not None:
            update(receipt)
    if cycle.get('core_after'):
        require(current == cycle['core_after'], 'Observed new Core identity changed')
    if (current['uid'] != m['core_before']['uid']
            or current['cmdline_sha256'] != m['core_before']['cmdline_sha256']):
        # A Type=simple launcher may be visible before its same-PID exec.
        # Waiting does not accept it, signal it, or permit another start.
        raise StartIdentityPending('Awaiting exact same-generation runtime exec identity')
    return current
'''
replacement["consistent_snapshot"] = '''
def consistent_snapshot(m, mode, identity, override):
    owner()
    source(m, mode)
    backup(m)
    preserve(m)
    value = service(m, override)
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and int(value['MainPID']) == identity['pid']
            and process(identity['pid']) == identity, 'Core process changed')
    config_binding(identity)
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == m['app_version'], 'Core health/config failed')
    work = ledger_snapshot(m, identity)
    require(service(m, override) == value and process(identity['pid']) == identity,
            'Core changed during preflight')
    return work
'''
replacement["healthy"] = '''
def healthy(m, cycle, override):
    mode = 'after' if cycle['mode'] == 'apply' else 'before'
    source(m, mode)
    check_prepared(m)
    preserve(m)
    value = service(m, override)
    current = process(int(value['MainPID'])) if int(value.get('MainPID') or 0) else None
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and current == cycle.get('core_after'), 'Bound Core identity is not active')
    config_binding(current)
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == m['app_version'], 'Core health/config failed')
    return current
'''
# Apply function substitutions on exact syntactic boundaries.
tree = ast.parse(s)
lines = s.splitlines(keepends=True)
for node in sorted((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in replacement),
                   key=lambda n: n.lineno, reverse=True):
    lines[node.lineno - 1:node.end_lineno] = [textwrap.dedent(replacement[node.name]).strip() + "\n"]
s = ''.join(lines)
assert "result['relay_health'] = relay_idle()" in s
s = s.replace("result['relay_health'] = relay_idle()", "result['relay_runtime_status'] = relay_idle(m)")
s = s.replace("current = core_from_start(m, cycle)\n                if (http", "current = core_from_start(m, cycle, receipt=r)\n                if (http")
s = s.replace("except (OSError, TimeoutError, urllib.error.URLError):", "except (OSError, TimeoutError, urllib.error.URLError, StartIdentityPending):")
additional = '''
class StartIdentityPending(RuntimeError):
    """Only bounded observation may wait for the owned launcher to exec."""


def current_hash(path):
    return filehash(path) if os.path.lexists(path) else None


def boot_identity():
    values = dict(line.split(None, 1) for line in Path('/proc/stat').read_text().splitlines()
                  if line.startswith('btime '))
    return {'btime': int(values['btime']), 'clock_ticks': os.sysconf('SC_CLK_TCK'),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def default_off(m):
    require(m.get('native_registration_enabled') is False
            and m.get('native_configuration_path') == str(CONFIG_PATH),
            'Native default-off configuration binding required')
    info = CONFIG_PATH.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_size <= 2 * 1024 * 1024 and CONFIG_PATH.resolve() == CONFIG_PATH,
            'Native configuration file metadata differs')
    config = json.loads(raw(CONFIG_PATH))
    require(type(config) is dict, 'Core configuration is not an object')
    selected = config.get('agentic_native_verification')
    require(selected is None or
            (type(selected) is dict and selected.get('enabled') is False),
            'Native verification must remain explicitly disabled or absent')


def config_binding(identity):
    require(process(identity['pid']) == identity, 'Core changed before environment proof')
    encoded = raw_proc_environment(identity['pid'])
    prefix = b'ABIT_ERP_TASK_RESOLUTION_CONFIG='
    matches = [part[len(prefix):] for part in encoded.split(b'\\0') if part.startswith(prefix)]
    require(matches == [str(CONFIG_PATH).encode()], 'Actual Core integration configuration differs')
    require(process(identity['pid']) == identity, 'Core changed during environment proof')


def raw_proc_environment(pid):
    with open('/proc/' + str(pid) + '/environ', 'rb') as stream:
        value = stream.read(1024 * 1024 + 1)
    require(len(value) <= 1024 * 1024, 'Process environment exceeded bounded private read')
    return value


def install_new(path, data, row):
    """Link a fully fsynced owned inode into an absent target without replacement."""
    temp = path.with_name(path.name + '.native-new-' + str(os.getpid()) + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, int(row['mode'], 8))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), int(row['mode'], 8))
            os.fchown(stream.fileno(), row['uid'], row['gid'])
            os.fsync(stream.fileno())
        os.link(temp, path, follow_symlinks=False)
        fsyncdir(path.parent)
    finally:
        if temp.exists():
            temp.unlink()
            fsyncdir(temp.parent)
'''
s = s.replace("if __name__ == '__main__':", textwrap.dedent(additional).strip() + "\n\nif __name__ == '__main__':")
ast.parse(s)
(P / 'release_core.py').write_text(s)
(E / 'graceful-operator-delta.patch').write_text(''.join(difflib.unified_diff(
    old.splitlines(True), s.splitlines(True), fromfile='a/reviewed-release-core-graceful.py',
    tofile='b/release_core.py')))
left = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(old).body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
right = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(s).body
         if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
report = {'baseline_sha256': hashlib.sha256(old.encode()).hexdigest(),
          'candidate_sha256': hashlib.sha256(s.encode()).hexdigest(),
          'unchanged_functions': sorted(k for k in left if left[k] == right[k]),
          'changed_functions': sorted(k for k in left if left[k] != right[k]),
          'new_functions': sorted(set(right) - set(left)),
          'source_only': True}
(E / 'graceful-ast-comparison.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))

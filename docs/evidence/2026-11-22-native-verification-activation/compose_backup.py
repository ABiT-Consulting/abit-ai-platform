"""Build the fresh, fixed backup/preparation operator; no execution."""
from pathlib import Path
import ast, hashlib, json, textwrap
ROOT = Path(__file__).resolve().parents[3]
E = ROOT / 'docs/evidence/2026-11-22-native-verification-activation'
P = ROOT / 'deployment/linux/native-verification-activation-20261122'
old = (E / 'reference/backup-current-core.py').read_text()
release = (P / 'release_core.py').read_text()
assert hashlib.sha256(old.encode()).hexdigest() == 'a8d387a87ef503474bc6198b650a4a23bbeaf36fa3958bad5dc55972043e6209'
old_ast = ast.parse(old)
old_nodes = {n.name: n for n in old_ast.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
release_ast = ast.parse(release)
release_nodes = {n.name: n for n in release_ast.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
header = '''"""One create-only online backup and fresh Core release binding; no service control.

The eleven SQLite backups are individually consistent snapshots. They are never
restored automatically. Output contains metadata/hashes only; private copies stay
inside the new root-only stage. An incomplete stage is retained and never reused.
"""
from pathlib import Path
from contextlib import closing
import datetime, fcntl, hashlib, json, os, shutil, socket, sqlite3, stat, subprocess, sys, time, urllib.request
BASE = Path('/srv/abit-dev-linux/abit-ai')
LIVE = BASE / 'releases/20261031-live-0.11.4/runtime/open_webui'
PACKAGE = BASE / 'deployment/native-verification-20261122T1615'
STAGE = Path('/var/lib/abit-ai-native-verification-20261122T1615')
LEASE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
OWNER = 'chat-authorized-root-orchestration-recovery-20261122T1007'
PREPARATION_SHA = 'UNBOUND'
SOURCE_MANIFEST_SHA = '77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6'
CONFIG_PATH = BASE / 'config/erp-assignment/resolver.json'
CONFIG = ('config/runtime-environment.json', 'config/final-snapshot.json',
          'config/webui-secret-key', 'deployment/run-production.py',
          'config/erp-assignment/resolver.json')
DATABASES = ('webui.db', 'agentic-governance.sqlite', 'abitdot-primary.sqlite',
             'aibit-chatgpt-relay.sqlite', 'abit-slack.sqlite', 'abit-telegram.sqlite',
             'codex-workspace-runs.sqlite', 'erp-dot-issues.sqlite',
             'erp-pending-review-qa.sqlite', 'erp-task-resolution.sqlite',
             'maintenance-admission/gate.sqlite')
ALLOWED = {'routers/agentic_governance.py', 'utils/agentic_contracts/validator.py',
           'utils/agentic_contracts/schemas/governance_v1.schema.json',
           'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
           'utils/agentic_native_execution.py'}
NEW_LEAVES = {'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
              'utils/agentic_native_execution.py'}
UNIT_FILES = ['/etc/systemd/system/abit-ai.service',
              '/etc/systemd/system/abit-ai.service.d/40-agentic-gateway.conf',
              '/etc/systemd/system/abit-ai.service.d/erp-assignment.conf']
ORIGINAL_EFFECTIVE = {'TimeoutStopUSec': '1min 30s', 'SendSIGKILL': 'yes',
                      'KillMode': 'control-group', 'KillSignal': '15',
                      'Restart': 'on-failure', 'RestartUSec': '10s'}
OVERRIDE = Path('/run/systemd/system/abit-ai.service.d/zzzz-native-verification-20261122T1615.conf')
OVERRIDE_BYTES = b'[Service]\\nTimeoutStopSec=infinity\\nSendSIGKILL=no\\nKillMode=mixed\\nRestart=no\\n'
CGROUP = '/system.slice/abit-ai.service'
GLOBAL_LOCK = Path('/var/lock/abit-ai-governance-release.lock')
DEADLINE = time.monotonic() + 240
'''
old_keep = ['require','now','sha','regular','save','json_save','checkpoint','table_summary',
            'deadline','sql_deadline','progress','filehash','validate_owner']
new_keep = ['raw','process','NoRedirect','http','canonical','db_rows','gate','preserve',
            'relay_idle','ledger_snapshot','unit','file_metadata','boot_identity',
            'config_binding','raw_proc_environment','default_off','current_hash',
            'source','acquire_release_lock']
s = header + '\n\n'.join(ast.get_source_segment(old, old_nodes[n]) for n in old_keep) + '\n\n'
s += '\n\n'.join(ast.get_source_segment(release, release_nodes[n]) for n in new_keep) + '\n\n'
s += '''def owner():
    validate_owner()


def backup_databases(report):
    """Retained eleven-file online backup and isolated restore verification."""
'''
# Preserve the actual database backup loop from the prior operator.
main_node = old_nodes['main']
loop = next(n for n in ast.walk(main_node) if isinstance(n, ast.For)
            and isinstance(n.target, ast.Name) and n.target.id == 'name'
            and isinstance(n.iter, ast.Name) and n.iter.id == 'DATABASES')
body = textwrap.dedent(ast.get_source_segment(old, loop))
s += textwrap.indent(body, '    ') + '\n\n'
s += r'''
def read_preparation():
    require(PREPARATION_SHA != 'UNBOUND', 'Unbound preparation cannot execute')
    value = raw(PACKAGE / 'preparation.json')
    require(sha(value) == PREPARATION_SHA, 'Preparation binding changed')
    m = json.loads(value)
    require(m['purpose'] == 'NATIVE_VERIFICATION_DEFAULT_OFF_CORE_INSTALL_V1'
            and m['runtime_root'] == str(LIVE)
            and m['source_package_sha256'] == SOURCE_MANIFEST_SHA,
            'Wrong source preparation scope')
    product_bytes = raw(PACKAGE / 'source-manifest.json')
    require(sha(product_bytes) == SOURCE_MANIFEST_SHA, 'Reviewed source package changed')
    product = json.loads(product_bytes)
    expected = {r['path']: r for r in product['core_files']}
    require(set(m['changes']) == ALLOWED == set(expected), 'Unexpected six-file scope')
    for rel, row in m['changes'].items():
        require(row['before_sha256'] == expected[rel]['preimage_sha256']
                and row['after_sha256'] == expected[rel]['postimage_sha256']
                and row['candidate'] == str(PACKAGE / 'payload/open_webui' / rel)
                and filehash(Path(row['candidate'])) == row['after_sha256'],
                'Candidate source changed')
    for row in m['evidence']:
        require(filehash(Path(row['path'])) == row['sha256'], 'Source review evidence changed')
    return m


def fresh_snapshot(m, initial):
    validate_owner()
    require((BASE / 'current/runtime/open_webui').resolve() == LIVE, 'Active runtime changed')
    if initial:
        m['configuration'] = [file_metadata(BASE / rel) for rel in CONFIG]
        m['graceful']['unit_files'] = [file_metadata(Path(p)) for p in UNIT_FILES]
    source(m, 'before')
    require(not os.path.lexists(OVERRIDE), 'Previous temporary override is present')
    identity = m['core_before']
    require(process(identity['pid']) == identity, 'Current Core identity changed')
    config_binding(identity)
    preserve(m)
    value = unit()
    require(value.get('User') == 'mcpdev'
            and value.get('FragmentPath') == UNIT_FILES[0]
            and sorted(value.get('DropInPaths', '').split()) == sorted(UNIT_FILES[1:])
            and value.get('ControlGroup') == CGROUP
            and value.get('ActiveState') == 'active' and value.get('SubState') == 'running'
            and int(value.get('MainPID') or 0) == identity['pid']
            and all(value.get(k) == v for k, v in ORIGINAL_EFFECTIVE.items()),
            'Current effective Core service differs')
    for row in m['graceful']['unit_files']:
        require(file_metadata(Path(row['path'])) == row, 'Unit changed during preparation')
        require(row['uid'] == row['gid'] == 0 and int(row['mode'], 8) == 0o644,
                'Unit ownership/mode changed')
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == '0.11.4', 'Core health/config failed')
    before_gate = gate(m)
    require(before_gate['state'] == 'open', 'Preparation requires original open admission')
    work = ledger_snapshot(m, identity, strict=not initial)
    require(work['current_core_lease_count'] == 0
            and not work['governance'] and not work['codex'], 'Current Core work is active')
    safe_gate = {'state': before_gate['state'], 'epoch': before_gate['epoch'],
                 'command_id_sha256': sha(before_gate['command_id'].encode()),
                 'actor_sha256': sha(before_gate['actor'].encode())}
    if initial:
        m['graceful']['gate_before'] = safe_gate
        m['graceful']['historical'] = work['historical_queues']
        m['graceful']['boot'] = boot_identity()
        identities = [identity, m['preserved_processes']['relay'],
                      m['preserved_processes']['preserved_2']]
        boot = m['graceful']['boot']
        m['graceful']['original_process_birth_min'] = boot['btime'] + min(
            r['start_ticks'] for r in identities) / boot['clock_ticks']
    else:
        require(safe_gate == m['graceful']['gate_before']
                and boot_identity() == m['graceful']['boot'], 'Admission/boot changed')
    for summary in work['historical_queues'].values():
        require(summary['count'] == 0 or
                summary['updated_max'] < m['graceful']['original_process_birth_min'],
                'Current provider work is not an old preserved cohort')
    require(gate(m) == before_gate and process(identity['pid']) == identity,
            'Core/admission changed during preparation')
    preserve(m)
    return work


def capacity_preflight():
    total = 0
    for name in DATABASES:
        path = BASE / 'data' / name
        info = regular(path)
        require(path.resolve() == path and info.st_nlink == 1, 'Database alias changed')
        total += info.st_size
        wal = Path(str(path) + '-wal')
        if os.path.lexists(wal):
            meta = regular(wal)
            require(wal.resolve() == wal and meta.st_nlink == 1, 'Database WAL alias changed')
            total += meta.st_size
    needed = max(3 * 1024**3, 2 * total + 512 * 1024**2)
    require(shutil.disk_usage(STAGE.parent).free > needed, 'Backup reserve insufficient')
    return {'measured_source_and_wal_bytes': total, 'minimum_free_bytes': needed}


def backup_configuration(m, report):
    for rel in CONFIG:
        deadline()
        source_path = BASE / rel
        expected = next(row for row in m['configuration'] if row['path'] == str(source_path))
        require(file_metadata(source_path) == expected, 'Configuration preimage changed')
        encoded = raw(source_path)
        require(sha(encoded) == expected['sha256'], 'Configuration changed before copy')
        save(STAGE / 'configuration' / rel.replace('/', '--'), encoded)
        require(file_metadata(source_path) == expected, 'Configuration changed during backup')
        report['configuration'].append(expected)
    for index, row in enumerate(m['graceful']['unit_files']):
        deadline()
        source_path = Path(row['path'])
        require(file_metadata(source_path) == row, 'Unit preimage changed')
        encoded = raw(source_path)
        require(sha(encoded) == row['sha256'], 'Unit changed before backup')
        save(STAGE / 'configuration' / ('unit--' + str(index)), encoded)
        require(file_metadata(source_path) == row, 'Unit changed during backup')
    report['unit_files'] = m['graceful']['unit_files']


def backup_sources(m, report):
    for rel, row in sorted(m['changes'].items()):
        deadline()
        path = LIVE / rel
        expected = row['before_sha256']
        require(current_hash(path) == expected, 'Source changed before rollback backup')
        if expected is None:
            require(rel in NEW_LEAVES, 'Unexpected absent preimage')
            report['source'].append({'relative_source': rel, 'exists': False, 'sha256': None})
        else:
            info = regular(path)
            encoded = raw(path)
            require(sha(encoded) == expected, 'Source changed while copying preimage')
            save(STAGE / 'source' / rel.replace('/', '--'), encoded)
            require(current_hash(path) == expected, 'Source changed after rollback backup')
            report['source'].append({'relative_source': rel, 'exists': True,
                                    'sha256': expected, 'bytes': len(encoded),
                                    'uid': info.st_uid, 'gid': info.st_gid,
                                    'mode': oct(stat.S_IMODE(info.st_mode))})


def main():
    require(os.geteuid() == 0 and os.environ.get('SUDO_USER') == 'jimran@abitcons.com',
            'Existing jimran administrator route required')
    require(socket.gethostname() == 'abit-dev-linux', 'Wrong host')
    require(len(sys.argv) == 1, 'Preparation takes no runtime arguments')
    validate_owner()
    lock = acquire_release_lock(GLOBAL_LOCK)
    try:
        m = read_preparation()
        require(not os.path.lexists(STAGE), 'One-use stage exists; inspect original result')
        preflight = fresh_snapshot(m, initial=True)
        reserve = capacity_preflight()
        require(not os.path.lexists(STAGE), 'One-use stage appeared during preflight')
        STAGE.mkdir(mode=0o700)
        checkpoint(STAGE.parent)
        for name in ('database', 'restore-check', 'configuration', 'source'):
            (STAGE / name).mkdir(mode=0o700)
        report = {'schema_version': 1, 'started_utc': now(),
                  'purpose': 'NATIVE_VERIFICATION_CORE_PROTECTED_ONLINE_BACKUP',
                  'source_database_modified': False,
                  'cross_database_atomic_snapshot_claimed': False,
                  'automatic_database_restore_allowed': False,
                  'databases': [], 'configuration': [], 'source': [],
                  'preflight': preflight, 'capacity': reserve}
        json_save(STAGE / 'backup-intent.json', {'started_utc': now(), 'operator': OWNER,
                  'preparation_sha256': PREPARATION_SHA, 'databases': DATABASES})
        checkpoint(STAGE)
        try:
            backup_databases(report)
            backup_configuration(m, report)
            backup_sources(m, report)
            report['post_backup'] = fresh_snapshot(m, initial=False)
            report.update(status='PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED',
                          finished_utc=now(), sqlite_version=sqlite3.sqlite_version)
            json_save(STAGE / 'backup-receipt.json', report)
            m.update(created_utc=now(), backup_receipt_sha256=filehash(STAGE / 'backup-receipt.json'),
                     live_activation_authorized=False, native_registration_enabled=False)
            m['validation']['reviewed'] = False
            m['graceful']['observed_utc'] = now()
            json_save(STAGE / 'release-manifest.draft.json', m)
            for name in ('database', 'restore-check', 'configuration', 'source'):
                checkpoint(STAGE / name)
            checkpoint(STAGE)
            print(json.dumps({'status': report['status'], 'stage': str(STAGE),
                              'database_count': len(report['databases']),
                              'backup_receipt_sha256': filehash(STAGE / 'backup-receipt.json'),
                              'draft_manifest_sha256': filehash(STAGE / 'release-manifest.draft.json'),
                              'source_or_service_changed': False,
                              'native_registration_enabled': False}), flush=True)
        except BaseException as error:
            json_save(STAGE / 'backup-failed.json',
                      {'failed_utc': now(), 'error_type': type(error).__name__,
                       'completed_databases': len(report['databases']),
                       'partial_stage_retained': True})
            checkpoint(STAGE)
            raise
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
'''
ast.parse(s)
(P / 'prepare_release.py').write_text(s)
report = {'baseline_sha256': hashlib.sha256(old.encode()).hexdigest(),
          'candidate_sha256': hashlib.sha256(s.encode()).hexdigest(),
          'original_database_loop_ast_unchanged': True,
          'retained_backup_functions': old_keep,
          'retained_release_functions': new_keep,
          'source_only': True}
actual_tree = ast.parse(s)
actual = {n.name: n for n in actual_tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
actual_loop = next(n for n in ast.walk(actual['backup_databases']) if isinstance(n, ast.For)
                   and isinstance(n.target, ast.Name) and n.target.id == 'name'
                   and isinstance(n.iter, ast.Name) and n.iter.id == 'DATABASES')
assert ast.dump(actual_loop, include_attributes=False) == ast.dump(loop, include_attributes=False)
for name in old_keep:
    assert ast.dump(actual[name], include_attributes=False) == ast.dump(old_nodes[name], include_attributes=False)
for name in new_keep:
    assert ast.dump(actual[name], include_attributes=False) == ast.dump(release_nodes[name], include_attributes=False)
(E / 'backup-ast-comparison.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))

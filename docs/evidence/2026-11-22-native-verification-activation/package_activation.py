"""Freeze ordinary source inputs and a bound backup launcher; no privileged action."""
from pathlib import Path
import ast, datetime, hashlib, json, os, stat, subprocess

ROOT = Path(__file__).resolve().parents[3]
E = ROOT / 'docs/evidence/2026-11-22-native-verification-activation'
P = ROOT / 'deployment/linux/native-verification-activation-20261122'
PRODUCT = ROOT / 'deployment/native-verification-binding-20261122'
PACKAGE = Path('/srv/abit-dev-linux/abit-ai/deployment/native-verification-20261122T1615')
LIVE = Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
SOURCE_MANIFEST_SHA = '77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6'
hashfile = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
write = lambda p, v: p.write_text(json.dumps(v, indent=2, sort_keys=True) + '\n')
assert subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=ROOT, text=True).strip() == 'feat/native-verification-activation-20261122'
assert hashfile(PRODUCT / 'manifest.json') == SOURCE_MANIFEST_SHA
product = json.loads((PRODUCT / 'manifest.json').read_text())
normal = E / 'normal-20261122T163000202080Z.json'
optimized = E / 'optimized-20261122T163111362759Z.json'
for path in (normal, optimized):
    report = json.loads(path.read_text())
    assert report['tests'] == 56 and report['failures'] == report['errors'] == report['skips'] == 0
    assert report['source_drift'] is False
    assert hashfile(ROOT / report['log']) == report['log_sha256']
    for rel, digest in report['source_before'].items():
        assert hashfile(ROOT / rel) == digest == report['source_after'][rel]
process_report = json.loads((E / 'fresh-process-identities.json').read_text())
processes = process_report['processes']
for row in processes.values():
    p = Path('/proc') / str(row['pid'])
    fields = (p / 'stat').read_text().rsplit(')', 1)[1].split()
    current = {'pid': row['pid'], 'start_ticks': int(fields[19]), 'uid': p.stat().st_uid,
               'cmdline_sha256': hashfile(p / 'cmdline')}
    assert current == row
baseline = {}
existing = sorted(LIVE.rglob('*.py')) + sorted((LIVE / 'utils/agentic_contracts/schemas').glob('*.json'))
assert len(existing) == 375
for path in existing:
    assert path.resolve() == path and not path.is_symlink()
    info = path.stat()
    assert stat.S_ISREG(info.st_mode)
    rel = str(path.relative_to(LIVE))
    baseline[rel] = {'path': rel, 'sha256': hashfile(path), 'bytes': info.st_size,
                     'uid': info.st_uid, 'gid': info.st_gid, 'mode': oct(stat.S_IMODE(info.st_mode))}
changes = {}
for row in product['core_files']:
    rel = row['path']
    target = LIVE / rel
    if row['preimage_sha256'] is None:
        assert not os.path.lexists(target)
        baseline[rel] = {'path': rel, 'sha256': None, 'exists': False,
                         'uid': 999, 'gid': 988, 'mode': '0o660'}
    else:
        assert baseline[rel]['sha256'] == row['preimage_sha256']
    parent = target.parent.stat()
    changes[rel] = {'path': rel, 'before_sha256': row['preimage_sha256'],
                    'after_sha256': row['postimage_sha256'], 'bytes': row['bytes'],
                    'candidate': str(PACKAGE / 'payload/open_webui' / rel),
                    'parent_device': parent.st_dev, 'parent_inode': parent.st_ino}
    assert hashfile(PRODUCT / row['payload']) == row['postimage_sha256']
assert len(baseline) == 378
evidence_paths = [
    PRODUCT / 'manifest.json',
    ROOT / 'docs/evidence/2026-11-22-native-verification-binding/validation-final.json',
    ROOT / 'docs/evidence/2026-11-22-native-verification-binding/independent-core-native-review.json',
    ROOT / 'docs/evidence/2026-11-22-native-verification-binding/independent-root-broker-review.json',
    ROOT / 'docs/evidence/2026-11-22-native-verification-binding/independent-core-environment-delta-review.json',
    E / 'reference/corrected-root-source-review.json',
    E / 'reference/release-core-graceful.py',
    E / 'reference/backup-current-core.py',
    E / 'graceful-ast-comparison.json', E / 'backup-ast-comparison.json',
    E / 'fresh-process-identities.json', normal, optimized,
    E / 'test_graceful_lifecycle.py', E / 'test_native_activation.py',
    P / 'release_core.py', P / 'prepare_release.py', P / 'stage_broker.py',
]
m = {
    'schema_version': 1,
    'purpose': 'NATIVE_VERIFICATION_DEFAULT_OFF_CORE_INSTALL_V1',
    'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'source_commit': '6f376d916b88f2e3f4b2eb37ca3d1aae2cf80f7c',
    'source_package_sha256': SOURCE_MANIFEST_SHA,
    'runtime_root': str(LIVE), 'app_version': '0.11.4',
    'baseline': baseline, 'changes': changes, 'configuration': [],
    'core_before': processes['core'],
    'preserved_processes': {k: v for k, v in processes.items() if k != 'core'},
    'native_registration_enabled': False,
    'native_configuration_path': '/srv/abit-dev-linux/abit-ai/config/erp-assignment/resolver.json',
    'live_activation_authorized': False,
    'operator_helper': {'source': str(P / 'release_core.py'),
                        'source_sha256': hashfile(P / 'release_core.py'),
                        'binding_change': 'Only MANIFEST_SHA literal in a separately reviewed root-owned copy'},
    'graceful': {
        'policy': 'CORE_ONLY_GRACEFUL_V1', 'cgroup': '/system.slice/abit-ai.service',
        'temporary_dropin_sha256': hashlib.sha256(b'[Service]\nTimeoutStopSec=infinity\nSendSIGKILL=no\nKillMode=mixed\nRestart=no\n').hexdigest(),
        'maintenance_module_sha256': baseline['utils/agentic_maintenance.py']['sha256'],
        'unit_effective': {'TimeoutStopUSec': '1min 30s', 'SendSIGKILL': 'yes',
                           'KillMode': 'control-group', 'KillSignal': '15',
                           'Restart': 'on-failure', 'RestartUSec': '10s'},
        'unit_files': [], 'historical': {},
    },
    'validation': {'reviewed': False, 'api_verify_passed': True,
                    'api_integration_passed': 104, 'api_integration_failed': 0,
                    'native_binding_focused_passed': 45,
                    'activation_finite_unique_methods_passed': 56,
                    'activation_normal_passed': 56, 'activation_optimized_passed': 56},
    'evidence': [{'path': str(path), 'sha256': hashfile(path)} for path in evidence_paths],
    'limits': {'root_broker_started': False, 'native_authority_issued': False,
               'original_jobs_replayed': False, 'database_restore_allowed': False,
               'business_workflow_complete': False},
}
write(P / 'preparation.json', m)
preparation_sha = hashfile(P / 'preparation.json')
unbound = (P / 'prepare_release.py').read_text()
assert unbound.count("PREPARATION_SHA = 'UNBOUND'") == 1
bound = unbound.replace("PREPARATION_SHA = 'UNBOUND'",
                        "PREPARATION_SHA = '" + preparation_sha + "'")
ast.parse(bound)
(P / 'prepare_release.bound.py').write_text(bound)
write(E / 'backup-launcher-binding.json', {
    'schema_version': 1, 'source_sha256': hashfile(P / 'prepare_release.py'),
    'bound_sha256': hashfile(P / 'prepare_release.bound.py'),
    'preparation_sha256': preparation_sha,
    'only_source_change': 'PREPARATION_SHA literal',
    'root_install_path': '/var/lib/abit-ai-native-verification-20261122T1615-prepare.py',
    'root_install_mode': '0400', 'root_install_uid': 0, 'root_install_gid': 0,
    'argv': ['/usr/bin/sudo', '-n', '/usr/bin/python3.12', '-I', '-S', '-B',
             '/var/lib/abit-ai-native-verification-20261122T1615-prepare.py'],
    'root_action_performed': False,
})
mapping = []
def entry(source, relative):
    mapping.append({'source': str(source), 'target': str(PACKAGE / relative),
                    'sha256': hashfile(source), 'bytes': source.stat().st_size})
entry(PRODUCT / 'manifest.json', 'source-manifest.json')
for row in product['core_files']:
    entry(PRODUCT / row['payload'], 'payload/open_webui/' + row['path'])
for row in product['root_bundle_files']:
    entry(PRODUCT / 'root-bundle' / row['path'], 'root-bundle/' + row['path'])
for name in ('root-registration.template.json', 'abit-native-verification-broker.service'):
    entry(PRODUCT / name, name)
for name in ('release_core.py', 'prepare_release.py', 'prepare_release.bound.py', 'stage_broker.py', 'preparation.json'):
    entry(P / name, name)
write(P / 'operator-input-index.json', {'schema_version': 1,
    'state': 'ORDINARY_SOURCE_INPUTS_ONLY_ROOT_EXECUTION_NOT_PERFORMED',
    'package': str(PACKAGE), 'protected_stage': '/var/lib/abit-ai-native-verification-20261122T1615',
    'source_manifest_sha256': SOURCE_MANIFEST_SHA,
    'preparation_sha256': preparation_sha, 'files': mapping})
write(E / 'source-freeze.json', {'schema_version': 1, 'state': 'SOURCE_TESTED_PENDING_ROOT_OPERATOR_BINDING',
    'source_package_sha256': SOURCE_MANIFEST_SHA, 'preparation_sha256': preparation_sha,
    'operator_input_index_sha256': hashfile(P / 'operator-input-index.json'),
    'sources': {name: hashfile(P / name) for name in ('release_core.py','prepare_release.py','stage_broker.py')},
    'root_backup_launcher_sha256': hashfile(P / 'prepare_release.bound.py'),
    'normal_receipt': {'path': str(normal.relative_to(ROOT)), 'sha256': hashfile(normal)},
    'optimized_receipt': {'path': str(optimized.relative_to(ROOT)), 'sha256': hashfile(optimized)},
    'finite_tests_per_mode': 56, 'runtime_baseline_present_files': 375,
    'new_leaf_absence_preconditions': 3, 'preserved_non_core_processes': 7,
    'live_source_or_service_changes': False, 'root_backup_executed': False})
print(json.dumps({'preparation_sha256': preparation_sha,
                  'bound_backup_sha256': hashfile(P / 'prepare_release.bound.py'),
                  'operator_index_sha256': hashfile(P / 'operator-input-index.json'),
                  'operator_files': len(mapping),
                  'source_freeze_sha256': hashfile(E / 'source-freeze.json')}, indent=2))

"""Independent read-only final package binding verification. No live control."""
from pathlib import Path
import ast
import datetime
import hashlib
import json
import stat
import subprocess

WORK = Path('/srv/abit-dev-linux/abit-ai-core-graceful-activation-20261122')
PACKAGE = Path('/srv/abit-dev-linux/abit-ai/deployment/orchestration-recovery-20261122T1015')
GATEWAY = Path('/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122')
EVIDENCE = WORK / 'docs/evidence/2026-11-22-core-graceful-activation'
SOURCE = WORK / 'deployment/orchestration-recovery-20261122/release-core-graceful.py'
PIN = {
    'manifest': '006d84d979f57a0a93e27d2ee3ba357b4474e1ad288eaf2847bcd87e62f448e1',
    'bound': '4307136a592ace372c2e99af40bb22a0f44dcc3be1b7ea11a90069c354162bd4',
    'root_review': 'f8788c315a3f198a2c15a9a6b9970f63cbca009754bdbf0857b4bf02fd4db9a5',
    'draft': '0e864874ddfe2574cc2ce0c99114dc3230a3e526834e6208d20806baead2d086',
    'inputs': '88ae39ccfc60ac3d97e29198b08dd9702eaf8215301c600395eb850c76962dc9',
    'source': 'd98a77389a7cf3300aedd4f336ab5557d3f28e042c469e425e18fb87fb234a8a',
}
checks = []
def require(value, name):
    if not value:
        raise RuntimeError(name)
    checks.append(name)
def digest(data):
    return hashlib.sha256(data).hexdigest()
def content(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), 'regular-file:' + path.name)
    return path.read_bytes()
def pinned(path, expected, name):
    value = content(path)
    require(digest(value) == expected, name)
    return value

report = {'scope': 'Independent final manifest and bound-helper binding review',
          'reviewer': 'status_evidence_review; separate from manifest binder',
          'reviewed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'pins': PIN, 'source_commit': '3c8f05a7fbb4d43b5b1d7e34b28bdc8b141de87d',
          'live_service_actions_executed': False, 'protected_backup_or_business_data_exported': False,
          'provider_actions_executed': False, 'tests_rerun': False}
try:
    final_raw = pinned(PACKAGE / 'release-manifest.json', PIN['manifest'], 'final-manifest-pin')
    bound_raw = pinned(PACKAGE / 'release-core-graceful.bound.py', PIN['bound'], 'bound-helper-pin')
    review = json.loads(pinned(PACKAGE / 'root-graceful-source-review.json', PIN['root_review'], 'root-review-pin'))
    draft = json.loads(pinned(PACKAGE / 'release-manifest.draft.json', PIN['draft'], 'draft-pin'))
    inputs = json.loads(pinned(PACKAGE / 'graceful-manifest-inputs.json', PIN['inputs'], 'graceful-input-pin'))
    source_raw = pinned(SOURCE, PIN['source'], 'tested-source-pin')
    final = json.loads(final_raw)

    original_literal = b"MANIFEST_SHA = 'UNBOUND'"
    require(source_raw.count(original_literal) == 1, 'one-manifest-literal')
    expected_bound = source_raw.replace(original_literal,
        ("MANIFEST_SHA = '" + PIN['manifest'] + "'").encode(), 1)
    require(bound_raw == expected_bound, 'bound-helper-only-manifest-literal-changed')
    ast.parse(bound_raw)
    require(True, 'bound-helper-syntax-valid-without-execution')
    blob = subprocess.run(['git', '-C', str(WORK), 'show',
        report['source_commit'] + ':deployment/orchestration-recovery-20261122/release-core-graceful.py'],
        capture_output=True, check=True).stdout
    require(blob == source_raw, 'source-equals-committed-reviewed-blob')

    changed = {key for key in set(draft) | set(final) if draft.get(key) != final.get(key)}
    require(changed == {'authorization', 'draft_blocker', 'evidence', 'graceful',
                        'live_activation_authorized', 'operator_helper', 'validation'},
            'only-declared-finalization-fields-changed')
    require('draft_blocker' not in final and final['live_activation_authorized'] is True,
            'draft-blocker-removed-authorized-binding-explicit')
    require(final['graceful'] == inputs, 'graceful-inputs-exact')
    require(final['operator_helper']['source_commit'] == report['source_commit']
            and final['operator_helper']['source_sha256'] == PIN['source']
            and final['operator_helper']['source'] == str(SOURCE), 'operator-helper-binding-exact')
    require(final['authorization']['operator_owner'] ==
            'chat-authorized-root-orchestration-recovery-20261122T1007',
            'authorized-operator-session-exact')
    require(len(final['baseline']) == 373 and len(final['configuration']) == 5
            and len(final['preserved_processes']) == 6, 'source-config-process-scope-retained')
    require(final['evidence'][:len(draft['evidence'])] == draft['evidence'],
            'all-draft-evidence-retained-in-order')
    evidence = final['evidence']
    require(len(evidence) == 16 and len({row['path'] for row in evidence}) == 16,
            'exactly-sixteen-distinct-evidence-files')
    verified_evidence = []
    for row in evidence:
        path = Path(row['path'])
        require(path.is_relative_to(PACKAGE) or path.is_relative_to(WORK)
                or path.is_relative_to(GATEWAY), 'evidence-in-reviewed-workspaces:' + path.name)
        pinned(path, row['sha256'], 'evidence-hash:' + path.name)
        verified_evidence.append({'path': str(path), 'sha256': row['sha256']})
    require(review['decision'] == 'ACCEPTED_FOR_FINAL_MANIFEST_BINDING_AND_LIVE_PREFLIGHT'
            and review['source_sha256'] == PIN['source']
            and review['source_commit'] == report['source_commit'],
            'independent-root-review-reconciles-exact-source')

    allowed = {'utils/agentic_transport_retry.py', 'utils/agentic_relay_scope.py',
               'routers/agentic_governance.py'}
    require(set(final['changes']) == allowed, 'exact-three-runtime-targets')
    payloads = []
    for relative, row in sorted(final['changes'].items()):
        payload = PACKAGE / 'payload/open_webui' / relative
        require(row['candidate'] == str(payload), 'fixed-payload-path:' + relative)
        raw = pinned(payload, row['after_sha256'], 'payload-hash:' + relative)
        require(len(raw) == row['bytes'], 'payload-size:' + relative)
        require(row['before_sha256'] == final['baseline'][relative]['sha256'],
                'payload-preimage-bound:' + relative)
        require(content(GATEWAY / 'backend/open_webui' / relative) == raw,
                'payload-equals-source-tested-file:' + relative)
        payloads.append({'path': relative, 'sha256': row['after_sha256'], 'bytes': len(raw)})
    for row in inputs['unit_files']:
        path = Path(row['path'])
        raw = pinned(path, row['sha256'], 'unit-file-pin:' + path.name)
        info = path.lstat()
        require((info.st_uid, info.st_gid, oct(stat.S_IMODE(info.st_mode)), len(raw)) ==
                (row['uid'], row['gid'], row['mode'], row['bytes']),
                'unit-file-metadata:' + path.name)

    for key, value in draft['validation'].items():
        require(final['validation'][key] == value, 'previous-validation-retained:' + key)
    for key in ('graceful_normal_executions_passed', 'graceful_optimized_executions_passed',
                'graceful_unique_finite_methods_passed'):
        require(final['validation'][key] == 30, 'finite-validation-count:' + key)
    for label in ('normal', 'optimized'):
        row = json.loads((EVIDENCE / ('finite-final-' + label + '.json')).read_text())
        require(row['exit_code'] == 0 and row['tests'] == 30
                and row['source_sha256'] == row['source_after_sha256'] == PIN['source'],
                'finite-terminal-source-receipt:' + label)
        require(digest(content(EVIDENCE / ('finite-final-' + label + '.log'))) == row['log_sha256'],
                'finite-log-receipt:' + label)
    api_dir = GATEWAY / 'docs/evidence/2026-11-22-gateway-preflight'
    summary = json.loads((api_dir / 'validation-summary.json').read_text())
    require(summary['unique_final_passing_focused_methods'] == 53
            and summary['api_integration'] == {'failed': 0, 'passed': 104, 'registered_operations': 615},
            'runtime-focused-integration-counts-reconcile')
    require(summary['api_verify']['contract_tests'] == 18
            and summary['api_verify']['backend_developer_tests'] == 48
            and summary['api_verify']['frontend_tests'] == 37
            and summary['api_verify']['svelte_errors'] == summary['api_verify']['svelte_warnings'] == 0,
            'runtime-api-verify-counts-reconcile')
    for name in ('api-verify.json', 'api-verify-integration.json'):
        row = json.loads((api_dir / name).read_text())
        require(row['exit_code'] == 0 and row['source_drift_after'] == []
                and row['isolated_environment'] is True, 'runtime-terminal-api-receipt:' + name)
    require(inputs['historical']['relay']['full_rows_sha256'] ==
            'accbb7ef0d5a1226c6ad58906c4c08f4046ef23c70d7a8985f80a5a4dc49fc1d'
            and inputs['historical']['dot']['full_rows_sha256'] ==
            '06f10340c5d440c3758b387a2903fe799cb27516b75df74441ccc7714c45ef04',
            'historical-outcome-preservation-baselines-exact')

    report.update(decision='ACCEPTED_FOR_ROOT_PREFLIGHT_AND_ALREADY_AUTHORIZED_APPLY',
                  evidence_files=verified_evidence, payloads=payloads,
                  validation={'finite_normal': 30, 'finite_optimized': 30,
                              'runtime_focused_unique': 53, 'api_integration_passed': 104,
                              'api_integration_failed': 0, 'registered_operations': 615},
                  limitations=['This review does not itself perform live preflight, activation or business acceptance.',
                               'Protected database backups and live private configuration are revalidated by the root helper, not exported here.',
                               'Historical queued/prepared records remain unsettled; no global sealed-drain claim.'])
except BaseException as error:
    report.update(decision='BLOCKED', error_type=type(error).__name__, error=str(error))
report['checks_passed'] = checks
report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
target = EVIDENCE / 'bound-package-independent-review.json'
with target.open('x') as stream:
    stream.write(json.dumps(report, sort_keys=True, indent=2) + '\n')
print(json.dumps({'decision': report['decision'], 'receipt': str(target),
                  'receipt_sha256': digest(target.read_bytes()), 'checks_passed': len(checks),
                  'manifest_sha256': PIN['manifest'], 'bound_helper_sha256': PIN['bound']}, sort_keys=True))
raise SystemExit(0 if report['decision'].startswith('ACCEPTED') else 1)

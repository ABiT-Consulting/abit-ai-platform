"""Build only the owned, source-only three-path package; never touch live files."""
from pathlib import Path
import datetime
import hashlib
import json
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EVIDENCE = ROOT / 'docs/evidence/2026-11-22-restored-target-activation'
OLD = ROOT / 'deployment/original-restored-target-diagnostic-20261122'
LIVE = Path('/srv/abit-dev-linux/relay-runtime/service')
sha = lambda data: hashlib.sha256(data).hexdigest()
require = lambda ok: None if ok else (_ for _ in ()).throw(RuntimeError('Frozen package input differs'))
runtime = HERE / 'runtime'
require(not runtime.exists())
(runtime / 'preimages').mkdir(parents=True)
(runtime / 'payload').mkdir()
before_paths = {
    'aibit_relay.cjs': OLD / 'preimages/aibit_relay.cjs',
    'original_dot_target_probe.cjs': None,
    'erp_task_relay.cjs': EVIDENCE / 'reference/erp_task_relay.before.cjs',
}
preimages = {
    'aibit_relay.cjs': '5bf18e2aac4a26d422ea3951fe0b99411950161d63b47a19ebe47870b2d6b18e',
    'original_dot_target_probe.cjs': None,
    'erp_task_relay.cjs': '0355af93e5aefb29836cad7ca2088171f6cca205e9f880952702ec77ef33a336',
}
rows = []
for name, original in before_paths.items():
    if original:
        data = original.read_bytes()
        require(sha(data) == preimages[name])
        (runtime / 'preimages' / name).write_bytes(data)
    data = (ROOT / 'deployment/windows/aibit-relay' / name).read_bytes()
    (runtime / 'payload' / name).write_bytes(data)
    rows.append({'path': name, 'before_sha256': preimages[name], 'after_sha256': sha(data),
                 'after_bytes': len(data)})
forward, rollback = [], []
for row in rows:
    before = runtime / 'preimages' / row['path']
    after = runtime / 'payload' / row['path']
    old = str(before) if before.exists() else '/dev/null'
    first = subprocess.run(['diff', '-u', '--label', 'a/' + row['path'] if before.exists() else '/dev/null', '--label', 'b/' + row['path'],
                            old, str(after)], capture_output=True, check=False)
    require(first.returncode in (0, 1))
    forward.append(first.stdout)
    second = subprocess.run(['diff', '-u', '--label', 'a/' + row['path'], '--label', 'b/' + row['path'] if before.exists() else '/dev/null',
                             str(after), old], capture_output=True, check=False)
    require(second.returncode in (0, 1))
    rollback.insert(0, second.stdout)
(runtime / 'forward.patch').write_bytes(b''.join(forward))
(runtime / 'rollback.patch').write_bytes(b''.join(rollback))
dependencies = json.loads((EVIDENCE / 'runtime-dependency-hashes.json').read_text())['files']
unchanged = {str(LIVE / name): row['sha256'] for name, row in dependencies.items()
             if name not in before_paths}
for path, expected in {
    Path('/srv/abit-dev-linux/abit-ai/deployment/relay-watchdog.sh'): '16733cacf83186c3c630db4a25cab15db7c30895ce798a67ebc2f8f62bd170b2',
    LIVE / 'start-relay.sh': '0040069c1e74eff24e3e2d5c6fd3d412bab298a089bdefe81930c6004e148080',
    LIVE / 'ensure-project-tabs.cjs': 'f9aa621f310df494dd58edac098b1a0470ebcb9765a9edb5e5d379e7d24d4a97',
    Path('/etc/systemd/system/aibit-linux-relay-watchdog.service'): 'f937b84611249ea1ae26854a0e3f1547790bf1d6a1bfb38155c34ee1638bd0f7',
    Path('/etc/systemd/system/aibit-linux-relay-watchdog.service.d/20-abit-xvfb-private-tmp.conf'): '1b2c9df8d44ef56ff6370200caa3f114d89c34e3c3930db068a66c6aaed873f2',
}.items():
    require(sha(path.read_bytes()) == expected)
    unchanged[str(path)] = expected
manifest = {'schema': 2, 'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'base_commit': '26a374594f9d6f7b477d5f440fc36ae1b41a4390',
            'runtime_root': str(LIVE), 'runtime_files': rows,
            'unchanged_files': unchanged, 'forward_patch_sha256': sha((runtime/'forward.patch').read_bytes()),
            'rollback_patch_sha256': sha((runtime/'rollback.patch').read_bytes()),
            'purpose': 'Restored target diagnostic plus graceful relay/ERP drain; bootstrap separately bound',
            'live_activation': False, 'no_provider_execution': True}
(runtime / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
helper = HERE / 'release_relay.py'
text = helper.read_text()
require("SOURCE_MANIFEST_SHA = 'UNBOUND'" in text)
text = text.replace("SOURCE_MANIFEST_SHA = 'UNBOUND'",
                    "SOURCE_MANIFEST_SHA = '" + sha((runtime/'manifest.json').read_bytes()) + "'")
helper.write_text(text)
print(json.dumps({'manifest_sha256': sha((runtime/'manifest.json').read_bytes()), 'runtime_files': rows,
                  'unchanged_dependencies': len(unchanged), 'source_only': True}))

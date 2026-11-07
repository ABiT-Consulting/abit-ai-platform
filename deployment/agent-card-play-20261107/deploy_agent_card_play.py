"""Install the legacy card Play enhancement without restarting ABIT.AI."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
frontend = Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui/frontend').resolve()
asset_name = 'agent-card-play-20261107.js'
tag = f'\t\t<script src="/{asset_name}" defer crossorigin="use-credentials"></script>\n'
index = frontend / 'index.html'
digest = lambda data: hashlib.sha256(data).hexdigest()

assert subprocess.check_output(['id', '-un'], text=True).strip() == 'jimran@abitcons.com'
assert subprocess.check_output(['hostname'], text=True).strip() == 'abit-dev-linux'
subprocess.run(['node', '--check', str(base / asset_name)], check=True)
before = index.read_bytes()
before_text = before.decode('utf-8')
assert before_text.count('</head>') == 1
assert 'project-title-link-20261107.js' in before_text
asset = (base / asset_name).read_bytes()
assert asset and len(asset) < 100_000
with urllib.request.urlopen('http://127.0.0.1:8888/health', timeout=10) as response:
    assert json.load(response).get('status')
backup = base / 'index.before.html'
if not backup.exists():
    backup.write_bytes(before)
after = before_text if tag in before_text else before_text.replace('</head>', tag + '\t</head>', 1)
after_bytes = after.encode('utf-8')
assert index.read_bytes() == before, 'Concurrent frontend change; inspect before retrying'
target = frontend / asset_name
temp_asset = frontend / (asset_name + '.tmp')
temp_asset.write_bytes(asset)
os.chmod(temp_asset, 0o644)
os.replace(temp_asset, target)
temp_index = frontend / 'index.agent-card-play.tmp'
temp_index.write_bytes(after_bytes)
shutil.copystat(index, temp_index)
assert index.read_bytes() == before, 'Concurrent frontend change; inspect before retrying'
os.replace(temp_index, index)
try:
    with urllib.request.urlopen('http://127.0.0.1:8888/' + asset_name, timeout=10) as response:
        assert response.read() == asset, 'Served module differs from staged source'
    with urllib.request.urlopen('http://127.0.0.1:8888/automations', timeout=10) as response:
        assert tag.strip() in response.read().decode('utf-8'), 'Module not included in served page'
    with urllib.request.urlopen('http://127.0.0.1:8888/health', timeout=10) as response:
        assert json.load(response).get('status')
except BaseException:
    # Restore only this addition if nobody else has modified the shell meanwhile.
    if index.read_bytes() == after_bytes:
        temp_index.write_bytes(before)
        shutil.copystat(index, temp_index)
        os.replace(temp_index, index)
    raise
report = {
    'release': 'ABIT-AGENT-CARD-PLAY-20261107-01',
    'activated': True,
    'activated_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'asset': asset_name,
    'asset_sha256': digest(asset),
    'index_before_sha256': digest(before),
    'index_after_sha256': digest(after_bytes),
    'service_restart': False,
    'backend_changed': False,
    'live_agent_launched_during_deployment': False,
    'http_module_verified': True,
    'health': True,
}
(base / 'release-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))

import datetime, hashlib, json, os, pathlib, subprocess, time
root = pathlib.Path('/srv/abit-dev-linux/abit-ai-platform')
out = root / '.git/qa-workspace-ordering-root-validation'
owned = ['backend/open_webui/utils/agentic_gateway_operations.py',
         'backend/open_webui/utils/agentic_gateway_delivery.py',
         'scripts/check-api.mjs', 'package.json']
def pins():
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in owned}
before = pins()
assert before[owned[0]] == 'fa2c899f548e94d62ad0bf1a337bf769dcc557d26c6bfcc7aa5af097a0e0d4ec'
space = os.statvfs(root)
available = space.f_bavail * space.f_frsize
assert available > 1024**3 + 64*1024**2, 'API_VERIFY_DISK_RESERVE'
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
clock = time.monotonic()
log = out / 'api-verify.log'
with log.open('x') as stream:
    result = subprocess.run(['npm', 'run', 'api:verify'], cwd=root,
        stdout=stream, stderr=subprocess.STDOUT, timeout=480,
        env={**os.environ, 'ABIT_PYTHON': '/srv/abit-dev-linux/abit-ai/venv/bin/python',
             'PYTHONDONTWRITEBYTECODE': '1'})
after = pins()
record = {'schema_version': 1, 'command': ['npm', 'run', 'api:verify'],
    'started_utc': started, 'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'elapsed_seconds': time.monotonic()-clock, 'returncode': result.returncode,
    'source_before': before, 'source_after': after, 'source_unchanged': before == after,
    'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(), 'available_bytes_before': available,
    'supported_interpreter': '/srv/abit-dev-linux/abit-ai/venv/bin/python',
    'integration_run': False,
    'integration_reason': 'One context workflow guidance string only; routes, models, authentication, permissions, exports and registration unchanged.',
    'runtime_changed': False}
with (out / 'api-verify.json').open('x') as stream:
    stream.write(json.dumps(record, sort_keys=True, indent=2) + '\n')
print(json.dumps({k: record[k] for k in ['returncode', 'elapsed_seconds', 'source_unchanged', 'log_sha256']}), flush=True)
raise SystemExit(result.returncode if before == after else 65)

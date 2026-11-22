#!/usr/bin/env python3
"""Run one existing API gate with isolated environment and bounded resources."""
from pathlib import Path
import json
import hashlib
import datetime
import os
import signal
import subprocess
import sys
import time

ROOT = Path("/srv/abit-dev-linux/tmp/gateway-preflight-diagnostics-20261122/composed-api")
EVIDENCE = Path("/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122/docs/evidence/2026-11-22-gateway-preflight")
mode = sys.argv[1]
if mode not in {"api:verify", "api:verify:integration"}:
    raise SystemExit("Unsupported API gate")
name = mode.replace(":", "-")
manifest_path=Path('/srv/abit-dev-linux/tmp/gateway-preflight-diagnostics-20261122/composed-api-final-source-manifest.json')
assert hashlib.sha256(manifest_path.read_bytes()).hexdigest()=='0c319588b06fe3d2ce6f30247cc3f4a4078bc2157b19181954840aab651ad7ea'
manifest=json.loads(manifest_path.read_text())
package_manifest=Path('/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122/deployment/gateway-preflight-diagnostics-20261122/manifest.json')
assert hashlib.sha256(package_manifest.read_bytes()).hexdigest()=='554945a9863b64f5a28de4ecfcd30e574f047458ecb605bc958c5cc0d975dd5a'
replacements={'backend/open_webui/'+item['path']:item['after_sha256'] for item in json.loads(package_manifest.read_text())['runtime_files']}

def source_drift():
    drift=[]
    for item in manifest['files']:
        expected=replacements.get(item['snapshot'],item['sha256'])
        if hashlib.sha256((ROOT/item['snapshot']).read_bytes()).hexdigest()!=expected:drift.append(item['snapshot'])
    return drift
assert not source_drift(), 'Pinned source drift before gate'
with (EVIDENCE/(name+'-intent.json')).open('x') as f:
    json.dump({'gate':mode,'source_manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'One isolated composed API gate, no deployment or provider operation'},f,sort_keys=True)

limit_kib = 1280 * 1024
minimum_available_kib = 512 * 1024

def available():
    return int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemAvailable:")))

def descendants(pid):
    pending, seen = [pid], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            pending.extend(int(value) for value in
                           Path(f"/proc/{current}/task/{current}/children").read_text().split())
        except (FileNotFoundError, ProcessLookupError):
            pass
    return seen

def memory_kib(pid):
    total = 0
    for current in descendants(pid):
        try:
            total += int(next(line.split()[1] for line in
                              Path(f"/proc/{current}/status").read_text().splitlines()
                              if line.startswith("VmRSS:")))
        except (FileNotFoundError, ProcessLookupError, StopIteration):
            pass
    return total

start_available = available()
if start_available < 1536 * 1024:
    report = {"gate": mode, "status": "capacity-blocked-before-start",
              "mem_available_start_kib": start_available, "required_start_kib": 1536 * 1024}
    (EVIDENCE / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(75)

env = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "ABIT_PYTHON": "/srv/abit-dev-linux/abit-ai/venv/bin/python",
    "PYTHON_DOTENV_DISABLED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "CI": "1",
    "NO_COLOR": "1",
    "NODE_OPTIONS": "--max-old-space-size=512",
    "VITEST_MAX_THREADS": "1",
    "VITEST_MIN_THREADS": "1",
    "VITEST_MAX_FORKS": "1",
    "VITEST_MIN_FORKS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NPM_CONFIG_USERCONFIG": str(EVIDENCE / "empty-user.npmrc"),
    "NPM_CONFIG_GLOBALCONFIG": str(EVIDENCE / "empty-global.npmrc"),
    "NPM_CONFIG_CACHE": str(EVIDENCE / "npm-cache"),
}
os.nice(10)
started = time.monotonic()
peak_kib = 0
stopped = None
log_path = EVIDENCE / (name + ".log")
with log_path.open("w") as log:
    process = subprocess.Popen(["npm", "run", mode], cwd=ROOT, env=env,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    while process.poll() is None:
        peak_kib = max(peak_kib, memory_kib(process.pid))
        if peak_kib > limit_kib:
            stopped = "API check process tree exceeded 1280 MiB"
        elif available() < minimum_available_kib:
            stopped = "Host available memory fell below 512 MiB"
        elif time.monotonic() - started > 360:
            stopped = "API check exceeded 360 seconds"
        if stopped:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            break
        time.sleep(0.5)
    code = process.wait()
report = {
    "gate": mode, "exit_code": code, "stopped_by_guard": stopped,
    "seconds": round(time.monotonic() - started, 3),
    "peak_process_tree_rss_kib": peak_kib,
    "mem_available_start_kib": start_available,
    "isolated_environment": True,
    "source": str(ROOT), "log": str(log_path), "source_drift_after": source_drift(), "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
}
(EVIDENCE / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
print(log_path.read_text()[-12000:])
raise SystemExit(75 if stopped else code)

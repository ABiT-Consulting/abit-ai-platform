#!/usr/bin/env python3
"""Run only the restored-target diagnostic against pinned current relay modules."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deployment/original-restored-target-diagnostic-20261122"
EVIDENCE = ROOT / "docs/evidence/2026-11-22-restored-target-diagnostic"
CANONICAL = ROOT / "deployment/windows/aibit-relay"
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
stage = Path(tempfile.mkdtemp(prefix="original-target-probe-" + stamp + "-", dir="/srv/abit-dev-linux/tmp"))
runtime = stage / "source"
runtime.mkdir()
inputs = json.loads((PACKAGE / "inputs.json").read_text())
digest = lambda data: hashlib.sha256(data).hexdigest()
for record in inputs["inputs"]:
    copied = ROOT / record["copy"]
    data = copied.read_bytes()
    if digest(data) != record["sha256"]:
        raise SystemExit("Frozen input drift: " + record["name"])
    (runtime / record["name"]).write_bytes(data)
for name in ["aibit_relay.cjs", "original_dot_target_probe.cjs"]:
    data = (CANONICAL / name).read_bytes()
    (runtime / name).write_bytes(data)
    (PACKAGE / "payload" / name).write_bytes(data)
test = CANONICAL / "tests/original_dot_target_probe.test.cjs"
before = {p.name: digest(p.read_bytes()) for p in sorted(runtime.iterdir())}
node = shutil.which("node")
if not node:
    raise SystemExit("Existing Node executable is unavailable")
commands = [
    [node, "--check", str(runtime / "aibit_relay.cjs")],
    [node, "--check", str(runtime / "original_dot_target_probe.cjs")],
    [node, "--test", "--test-reporter=tap", *(["--test-name-pattern", sys.argv[1]] if len(sys.argv)>1 else []), str(test)],
]
env = {"PATH": os.defpath, "NODE_PATH": "/srv/abit-dev-linux/abit-ai-platform/node_modules",
       "RELAY_DIAGNOSTIC_SOURCE": str(runtime)}
started = time.monotonic()
records = []
log = EVIDENCE / ("focused-" + stamp + ".log")
with log.open("x") as stream:
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=55)
        stream.write("$ " + " ".join(command) + "\n" + completed.stdout + "\n")
        records.append({"command": command, "exit_code": completed.returncode})
        if completed.returncode:
            break
after = {p.name: digest(p.read_bytes()) for p in sorted(runtime.iterdir())}
text = log.read_text()
counts = {key: int(match.group(1)) if (match := re.search(r"^# " + key + r" (\d+)$", text, re.M)) else None
          for key in ["tests", "pass", "fail", "cancelled", "skipped", "todo"]}
receipt = {"schema": 1, "timestamp_utc": stamp, "stage": str(stage),
           "scope": "offline controlled DOM and Core-authority fixtures; no live browser or provider",
           "commands": records, "counts": counts, "elapsed_seconds": time.monotonic()-started,
           "test_sha256": digest(test.read_bytes()), "runtime_sources": before,
           "source_drift": [name for name in before if before[name] != after[name]],
           "log": str(log.relative_to(ROOT)), "log_sha256": digest(log.read_bytes())}
receipt_path = EVIDENCE / ("focused-" + stamp + ".json")
receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
if any(record["exit_code"] for record in records) or receipt["source_drift"]:
    raise SystemExit(1)

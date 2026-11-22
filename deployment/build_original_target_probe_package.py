#!/usr/bin/env python3
"""Build and round-trip only the exact two-path relay diagnostic delta."""
from pathlib import Path
import datetime
import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deployment/original-restored-target-diagnostic-20261122"
CANONICAL = ROOT / "deployment/windows/aibit-relay"
EVIDENCE = ROOT / "docs/evidence/2026-11-22-restored-target-diagnostic"
sha = lambda value: hashlib.sha256(value).hexdigest()
inputs = json.loads((PACKAGE / "inputs.json").read_text())
for item in inputs["inputs"]:
    assert sha((ROOT / item["copy"]).read_bytes()) == item["sha256"]
files, forward, rollback = [], [], []
for name in ["aibit_relay.cjs", "original_dot_target_probe.cjs"]:
    prior = PACKAGE / "preimages" / name
    before = prior.read_bytes() if prior.exists() else None
    after = (CANONICAL / name).read_bytes()
    assert after.endswith(b"\n")
    (PACKAGE / "payload" / name).write_bytes(after)
    files.append({"path":name,"before_sha256":sha(before) if before is not None else None,
                  "after_sha256":sha(after),"before_exists":before is not None,
                  "after_bytes":len(after)})
    old = before.decode().splitlines(keepends=True) if before is not None else []
    new = after.decode().splitlines(keepends=True)
    forward.extend(difflib.unified_diff(old,new,fromfile="a/"+name if before is not None else "/dev/null",
                                       tofile="b/"+name))
    rollback.extend(difflib.unified_diff(new,old,fromfile="a/"+name,
                                        tofile="b/"+name if before is not None else "/dev/null"))
(PACKAGE/"forward.patch").write_text("".join(forward))
(PACKAGE/"rollback.patch").write_text("".join(rollback))
scratch=Path(tempfile.mkdtemp(prefix="original-target-probe-roundtrip-",dir="/srv/abit-dev-linux/tmp"))
for item in files:
    if item["before_exists"]:
        shutil.copyfile(PACKAGE/"preimages"/item["path"],scratch/item["path"])
results=[]
for name in ["forward.patch","rollback.patch"]:
    completed=subprocess.run(["patch","--fuzz=0","--batch","--no-backup-if-mismatch","-p1","-i",str(PACKAGE/name)],
                             cwd=scratch,text=True,capture_output=True,timeout=10)
    results.append({"patch":name,"exit_code":completed.returncode,"stdout":completed.stdout,"stderr":completed.stderr})
    completed.check_returncode()
    for item in files:
        actual=scratch/item["path"]
        if name=="forward.patch":
            assert sha(actual.read_bytes())==item["after_sha256"]
        elif item["before_exists"]:
            assert sha(actual.read_bytes())==item["before_sha256"]
        else:
            assert not actual.exists()
manifest={"schema":1,"created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "runtime_root":"/srv/abit-dev-linux/relay-runtime/service",
          "base_git_commit":inputs["base_commit"],"runtime_files":files,
          "unchanged_prerequisites":[item for item in inputs["inputs"] if item["name"]!="aibit_relay.cjs"],
          "forward_patch_sha256":sha((PACKAGE/"forward.patch").read_bytes()),
          "rollback_patch_sha256":sha((PACKAGE/"rollback.patch").read_bytes()),
          "scope":"Explicit read-only restored-target diagnostic only; no new Send, target selection, selector/capture writes or Core changes",
          "live_activation":False}
(PACKAGE/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
receipt={"timestamp_utc":manifest["created_utc"],"scratch":str(scratch),"patches":results,
         "postimages_checked":len(files),"restored_preimage_conditions":len(files),
         "manifest_sha256":sha((PACKAGE/"manifest.json").read_bytes()),
         "live_files_modified":False}
(EVIDENCE/"patch-roundtrip.json").write_text(json.dumps(receipt,indent=2)+"\n")
print(json.dumps(receipt,indent=2))

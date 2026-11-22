#!/usr/bin/env python3
"""Copy the already verified current Core API harness, then add the narrow relay candidate."""
from pathlib import Path
import datetime
import hashlib
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
prior = Path("/srv/abit-dev-linux/tmp/gateway-preflight-diagnostics-20261122")
prior_source = prior / "composed-api"
prior_manifest = prior / "composed-api-final-source-manifest.json"
sha = lambda data: hashlib.sha256(data).hexdigest()
assert sha(prior_manifest.read_bytes()) == "0c319588b06fe3d2ce6f30247cc3f4a4078bc2157b19181954840aab651ad7ea"
core_package = Path("/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122/deployment/gateway-preflight-diagnostics-20261122/manifest.json")
assert sha(core_package.read_bytes()) == "554945a9863b64f5a28de4ecfcd30e574f047458ecb605bc958c5cc0d975dd5a"
replacements = {"backend/open_webui/"+item["path"]:item["after_sha256"]
                for item in json.loads(core_package.read_text())["runtime_files"]}
destination = Path("/srv/abit-dev-linux/tmp/original-restored-target-api-20261122")
assert not destination.exists(), "Fresh owned API directory required"
destination.mkdir(mode=0o700)
target = destination / "source"
target.mkdir(mode=0o700)
records = []
for item in json.loads(prior_manifest.read_text())["files"]:
    p = prior_source / item["snapshot"]
    assert p.is_file() and not p.is_symlink()
    data = p.read_bytes()
    expected = replacements.get(item["snapshot"], item["sha256"])
    assert sha(data) == expected, "Existing Core harness drift: " + item["snapshot"]
    d = target / item["snapshot"]
    d.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    d.write_bytes(data)
    records.append({"path":item["snapshot"],"sha256":expected,"source":str(p)})
package = ROOT / "deployment/original-restored-target-diagnostic-20261122"
inputs = json.loads((package/"inputs.json").read_text())
for item in inputs["inputs"]:
    if item["name"] == "aibit_relay.cjs":
        continue
    p = ROOT / item["copy"]
    data = p.read_bytes()
    assert sha(data) == item["sha256"]
    relative = "deployment/windows/aibit-relay/" + item["name"]
    d = target / relative
    d.parent.mkdir(parents=True,exist_ok=True)
    d.write_bytes(data)
    records.append({"path":relative,"sha256":sha(data),"source":str(p)})
for name in ["aibit_relay.cjs", "original_dot_target_probe.cjs"]:
    p = ROOT / "deployment/windows/aibit-relay" / name
    data = p.read_bytes()
    relative = "deployment/windows/aibit-relay/" + name
    (target / relative).write_bytes(data)
    records.append({"path":relative,"sha256":sha(data),"source":str(p)})
assert shutil.disk_usage(destination).free > 4*1024*1024*1024
subprocess.run(["cp","-a","--reflink=auto",str(prior_source/"node_modules"),str(target/"node_modules")],
               check=True,timeout=120)
subprocess.run(["git","init","--quiet",str(target)],check=True)
for item in records:
    assert sha(Path(item["source"]).read_bytes()) == item["sha256"]
manifest = {"created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source":str(target),"core_input_manifest_sha256":sha(prior_manifest.read_bytes()),
            "scope":"Existing accepted current Core API source; two-path private relay diagnostic; isolated state only",
            "files":records,"dependencies":"Independently copied existing dependencies; no network installation"}
(destination/"source-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
evidence=ROOT/"docs/evidence/2026-11-22-restored-target-diagnostic"
(evidence/"api-source-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
(evidence/"empty-user.npmrc").write_text("")
(evidence/"empty-global.npmrc").write_text("")
(evidence/".gitignore").write_text("npm-cache/\n")
print(json.dumps({"target":str(target),"files":len(records),
                  "manifest_sha256":sha((destination/"source-manifest.json").read_bytes())}))

"""Required static API gate for the captured current package; no application imports."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
EVIDENCE = HERE / "evidence"
sha = lambda raw: hashlib.sha256(raw).hexdigest()

def read(path):
    path = Path(path)
    assert path.is_file() and not path.is_symlink()
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    return raw

def save(name, raw):
    with (EVIDENCE / name).open("xb") as target:
        target.write(raw)

assert not (EVIDENCE / "api-static-complete-current-1.json").exists()
manifest_raw = read(HERE / "deployment-manifest.json")
assert sha(manifest_raw) == "6eb9e44f54af431b57f2bf40368aca0a0bf28aacca212d1892a64baa9286805e"
manifest = json.loads(manifest_raw)
full = json.loads(read(HERE / "api-complete-source-manifest.json"))
resource = json.loads(read(HERE / "api-source-resource-manifest-4.json"))
current = dict(full["all_source_pins"])
current[resource["source_path"]] = resource["sha256"]
assert len(current) == 373
for path, digest in current.items():
    assert sha(read(path)) == digest, path
replacements = {row["path"]: row for row in manifest["replacements"]}
inputs = {
    str(HERE / "deployment-manifest.json"): sha(manifest_raw),
    str(HERE / "api-complete-source-manifest.json"): sha(read(HERE / "api-complete-source-manifest.json")),
    str(HERE / "api-source-resource-manifest-4.json"): sha(read(HERE / "api-source-resource-manifest-4.json")),
    str(Path(__file__).resolve()): sha(read(__file__)),
}
checker = REPO / "scripts/check_api_contract.py"
policy = REPO / "docs/api-contract-policy.json"
inputs[str(checker)] = sha(read(checker))
inputs[str(policy)] = sha(read(policy))
assert inputs[str(checker)] == manifest["additional_source_pins"][str(checker)]
selected = {}
for source, digest in full["all_source_pins"].items():
    path = Path(source)
    if not path.is_relative_to(RUNTIME) or path.suffix != ".py":
        continue
    relative = str(path.relative_to(RUNTIME))
    replacement = replacements.get(relative)
    retained = Path(replacement["candidate_path"]) if replacement else HERE / "snapshot/open_webui" / relative
    expected = replacement["after_sha256"] if replacement else digest
    assert sha(read(retained)) == expected
    inputs[str(retained)] = expected
    selected[relative] = {"path": str(retained), "sha256": expected}
assert len(selected) == 364 and set(replacements).issubset(selected)
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TZ"}}
environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1")
with tempfile.TemporaryDirectory(prefix="core-current-static-", dir=REPO / ".git") as directory:
    root = Path(directory)
    for relative, item in selected.items():
        target = root / "backend/open_webui" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read(item["path"]))
    (root / "docs").mkdir()
    (root / "docs/api-contract-policy.json").write_bytes(read(policy))
    command = ["/srv/abit-dev-linux/abit-ai/venv/bin/python", "-I", "-B",
               str(checker), "--root", str(root)]
    result = subprocess.run(command, cwd=REPO, env=environment, capture_output=True, timeout=60)
save("api-static-complete-current-1.stdout", result.stdout)
save("api-static-complete-current-1.stderr", result.stderr)
same = all(sha(read(path)) == digest for path, digest in inputs.items())
current_same = all(sha(read(path)) == digest for path, digest in current.items())
assert sha(read(HERE / "deployment-manifest.json")) == sha(manifest_raw)
record = {
    "schema_version": 1,
    "state": "CURRENT_CAPTURED_SOURCE_STATIC_API_GATE_COMPLETED",
    "started_utc": started,
    "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "exit_code": result.returncode,
    "gate": "api:verify",
    "checker": str(checker),
    "checker_unmodified": True,
    "arguments": ["--root", "<temporary-current-source-root>"],
    "policy_path": str(policy),
    "python_source_files": len(selected),
    "candidate_overrides": sorted(replacements),
    "full_current_source_paths_bound": len(current),
    "current_source_pins_rechecked_before_and_after": current_same,
    "input_pins": inputs,
    "input_pins_unchanged": same,
    "selected_python_sources": selected,
    "stdout_sha256": sha(result.stdout),
    "stderr_sha256": sha(result.stderr),
    "source_only_no_application_imports": True,
    "temporary_source_tree_removed": not Path(directory).exists(),
    "frozen_deployment_manifest_unchanged": sha(manifest_raw),
    "integration_suite_repeated": False,
    "live_actions": 0,
}
save("api-static-complete-current-1.json", (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
print(json.dumps({key: value for key, value in record.items() if key not in {"input_pins", "selected_python_sources"}}, sort_keys=True))
print(result.stdout.decode() + result.stderr.decode(), end="")
raise SystemExit(0 if result.returncode == 0 and same and current_same else 1)

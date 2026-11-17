"""Run the full api:verify wrapper against current captured Python plus unchanged UI source."""
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
OVERLAY = HERE / "api-verification-current/candidate"
sha = lambda raw: hashlib.sha256(raw).hexdigest()

def read(path):
    path = Path(path)
    before = path.lstat()
    assert path.is_file() and not path.is_symlink()
    raw = path.read_bytes()
    after = path.lstat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    return raw

def save(name, raw):
    with (EVIDENCE / name).open("xb") as target:
        target.write(raw)

assert not (EVIDENCE / "api-verify-complete-current-1.json").exists()
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
    str(HERE / "api-verification-current/composition.json"): sha(read(HERE / "api-verification-current/composition.json")),
    str(Path(__file__).resolve()): sha(read(__file__)),
}
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
    selected["backend/open_webui/" + relative] = str(retained)
assert len(selected) == 364
for name in ["scripts/check_api_contract.py", "scripts/tests/test_api_contract.py", "docs/api-contract-policy.json"]:
    selected[name] = str(OVERLAY / name)
for name in ["scripts/check-api.mjs", "backend/tests/test_developer_api.py", "backend/tests/test_developer_ui.py",
             "package.json", "tsconfig.json", "tsconfig.developers.json", "vite.config.ts",
             "svelte.config.js", "postcss.config.js", "tailwind.config.js", ".svelte-kit/tsconfig.json"]:
    selected[name] = str(REPO / name)
for path in (REPO / "src").rglob("*"):
    assert not path.is_symlink(), "UNREVIEWED_SOURCE_SYMLINK"
    if path.is_file():
        selected[str(path.relative_to(REPO))] = str(path)
for path in selected.values():
    inputs[path] = sha(read(path))
package = json.loads(read(REPO / "package.json"))
assert package["scripts"]["api:verify"] == "node scripts/check-api.mjs --verify"
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TZ"}}
environment.update(
    ABIT_PYTHON="/srv/abit-dev-linux/abit-ai/venv/bin/python",
    PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1",
    APP_BUILD_HASH=manifest["head_reported_by_root"],
    npm_package_version=package["version"],
)
with tempfile.TemporaryDirectory(prefix="core-current-api-verify-", dir=REPO / ".git") as directory:
    root = Path(directory)
    for relative, source in selected.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read(source))
    (root / "static").mkdir()
    modules = root / "node_modules"
    modules.mkdir()
    # Keep caches local to this disposable fixture; reuse installed dependencies.
    for child in (REPO / "node_modules").iterdir():
        if child.name not in {".vite", ".cache"}:
            (modules / child.name).symlink_to(child, target_is_directory=child.is_dir())
    command = ["node", "scripts/check-api.mjs", "--verify"]
    result = subprocess.run(command, cwd=root, env=environment, capture_output=True, timeout=240)
save("api-verify-complete-current-1.stdout", result.stdout)
save("api-verify-complete-current-1.stderr", result.stderr)
same = all(sha(read(path)) == digest for path, digest in inputs.items())
current_same = all(sha(read(path)) == digest for path, digest in current.items())
record = {
    "schema_version": 1,
    "state": "CURRENT_CAPTURED_SOURCE_FULL_API_VERIFY_COMPLETED",
    "started_utc": started,
    "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "exit_code": result.returncode,
    "gate": "api:verify",
    "command": command,
    "exact_package_script_command": package["scripts"]["api:verify"],
    "wrapper_unmodified": True,
    "components": [
        "current source registration and visibility coverage",
        "API source guard unit tests including two Slack registration cases",
        "current developer API and HTML helper unit tests",
        "developer utility and session Vitest suites",
        "developer portal Svelte/TypeScript diagnostics",
    ],
    "current_python_source_files": 364,
    "candidate_overrides": sorted(replacements),
    "verification_overlay": "api-verification-current",
    "full_current_source_paths_bound": len(current),
    "current_source_pins_rechecked_before_and_after": current_same,
    "input_pins": inputs,
    "input_pins_unchanged": same,
    "fixture_source_file_count": len(selected),
    "stdout_sha256": sha(result.stdout),
    "stderr_sha256": sha(result.stderr),
    "no_production_application_or_data_import": True,
    "normal_application_lifespan_not_started": True,
    "temporary_source_tree_removed": not Path(directory).exists(),
    "frozen_deployment_manifest_unchanged": sha(manifest_raw),
    "integration_suite_repeated": False,
    "no_existing_checks_skipped": True,
    "live_actions": 0,
}
save("api-verify-complete-current-1.json", (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
print(json.dumps({key: value for key, value in record.items() if key != "input_pins"}, sort_keys=True))
print(result.stdout.decode()[-18000:] + result.stderr.decode()[-18000:], end="")
raise SystemExit(0 if result.returncode == 0 and same and current_same else 1)

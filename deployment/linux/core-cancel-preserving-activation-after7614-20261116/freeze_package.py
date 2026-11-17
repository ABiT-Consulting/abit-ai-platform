"""Freeze this source package after checking retained results; no service/application calls."""
from pathlib import Path
import ast
import datetime
import hashlib
import json
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
V2 = REPO / "deployment/linux/original-contract-rejection-hold-20261116-v2"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    path = Path(path)
    assert path.is_file() and not path.is_symlink(), str(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), str(path)
    return raw

def exact(path, digest):
    raw = read(path)
    assert sha(raw) == digest, str(path)
    return raw

def save(name, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with (HERE / name).open("xb") as f:
        f.write(raw)
    return {"path": name, "bytes": len(raw), "sha256": sha(raw)}

assert subprocess.check_output(["git", "branch", "--show-current"], text=True).strip() == "feature/agov-api-live-20261113"
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert head == "5b4f325a6082bb8f551a6500fe8a1f26e4e7edba"
assert subprocess.check_output(["id", "-un"], text=True).strip() == "jimran@abitcons.com"

validation = json.loads(exact(HERE / "evidence/validation-attempt-1.json",
                            "bf4170d5d9bb2ac4006c86bc7d6e7d54802fa82a318b7b0ef2a3527f95e196ea"))
assert validation["all_passed"] and validation["all_required_ran"] and validation["pins_unchanged"]
for path, digest in validation["input_pins"].items():
    exact(path, digest)
for item in validation["checks"]:
    assert item["exit_code"] == 0
    for suffix in ("stdout", "stderr"):
        exact(HERE / ("evidence/" + item["check"] + "-1." + suffix), item[suffix + "_sha256"])
integration = json.loads(exact(HERE / "evidence/api-integration-attempt-1.json",
                               "f03b28a5e188b2244666e06e70f2b6b3765dd66e479794437e62ff1c4d08e192"))
assert integration["exit_code"] == 0 and integration["pins_unchanged"] and not integration["app_against_live_db"]
for suffix in ("stdout", "stderr"):
    exact(HERE / ("evidence/api-integration-1." + suffix), integration[suffix + "_sha256"])
controller = json.loads(read(HERE / "evidence/controller-validation-attempt-1.json"))
assert controller["exit_code"] == 0 and controller["pins_unchanged"] and controller["temporary_fixture_only"]
for name, digest in controller["input_pins"].items():
    exact(HERE / name, digest)
for suffix in ("stdout", "stderr"):
    exact(HERE / ("evidence/controller-validation-1." + suffix), controller[suffix + "_sha256"])

composition = json.loads(read(HERE / "barrier-composition.json"))
for path, digest in {
    HERE / "source-before/canonical-agentic_governance.py": composition["canonical_before"],
    REPO / "backend/open_webui/routers/agentic_governance.py": composition["canonical_after"],
    HERE / "source-before/runtime-agentic_governance.py": composition["runtime_before"],
    HERE / "candidate/open_webui/routers/agentic_governance.py": composition["runtime_candidate"],
}.items():
    exact(path, digest)
import_line = b"    from open_webui.utils import agentic_maintenance as maintenance\n"
barrier = (
    b"            # Closed admission also holds startup reconciliation and original receipts.\n"
    b"            # Read the existing gate; never synthesize quiescence or settle ownership.\n"
    b"            if maintenance.status().get('state') != 'open':\n"
    b"                await asyncio.sleep(5)\n"
    b"                continue\n")
assert len(import_line) + len(barrier) == 367
for before, after in (
    (HERE / "source-before/canonical-agentic_governance.py", REPO / "backend/open_webui/routers/agentic_governance.py"),
    (HERE / "source-before/runtime-agentic_governance.py", HERE / "candidate/open_webui/routers/agentic_governance.py"),
):
    raw = read(after)
    assert raw.count(import_line) == raw.count(barrier) == 1
    assert raw.replace(import_line, b"", 1).replace(barrier, b"", 1) == read(before)

cohort = json.loads(exact(HERE / "current-runtime-cohort.json",
                        "96eeb3ea0e4c8dc29bd6a44248442f724abff1097f199ecdd2dba5f06272b3a7"))
files = {row["path"]: row["sha256"] for row in cohort["files"]}
assert len(files) == len(cohort["files"]) == 79
for path, digest in files.items():
    exact(path, digest)

expected = {
    "utils/agentic_executor.py": ("c91461e659d371d99af233aea3bb7c608ed42628b0060cf1b34916b33f580b38", "d14747f2eb00da233099e105b4a7000a053efd5027d027c6dec4af41671e6cd8"),
    "utils/agentic_result_store.py": ("5068586c80e31eeb5c1f8d42f44f2c11b55c0ceb7f32356962e715793703922a", "429633df024e05fbac044fbe8e36fb4843464004b3d17a00078c77baa99c11a7"),
    "utils/agentic_gateway_contracts.py": ("fa8e57cbbfd86288ad9dce00c7229fc4453a615b8a7c69714db8fe5b8e6cb6ca", "7dad9106e6a806e1e2e91ce595373f74e881616d2750e9bd4745c77977d7ada0"),
    "utils/agentic_blocker_review.py": ("17f72d771d81627c82725095de9e549b753d6bc6b55d1743d75a03c897adb3aa", "dd228d90bf28d39ef84a219bbc186b81391c69bee2db62411d9d42b20b50d200"),
    "routers/agentic_governance.py": (composition["runtime_before"], composition["runtime_candidate"]),
}
replacements = []
for rel, (before, after) in expected.items():
    candidate = (HERE if rel.startswith("routers/") else V2) / "candidate/open_webui" / rel
    raw = exact(candidate, after)
    assert files[str(RUNTIME / rel)] == before
    compile(raw, str(candidate), "exec")
    replacements.append({"path": rel, "candidate_path": str(candidate), "before_sha256": before,
                         "after_sha256": after, "bytes": len(raw)})

additional = dict(validation["input_pins"])
additional.update({
    str(HERE / "activate_once.py"): controller["input_pins"]["activate_once.py"],
    str(HERE / "activation_support.py"): controller["input_pins"]["activation_support.py"],
    str(HERE / "current-runtime-cohort.json"): "96eeb3ea0e4c8dc29bd6a44248442f724abff1097f199ecdd2dba5f06272b3a7",
    str(HERE / "runtime79-historical-root-read.json"): "1bd5005bc980a7713534962c3e1e924931f4e4bf2019198d595bedd0168a9604",
    str(V2 / "manifest.json"): "ba4a02927d66b1b5c9f8d134f7670a16b84b809595c764a30a63e3b27ec7894e",
    str(REPO / "deployment/linux/original-be395-status-readonly-20261116-v1/read_status.py"): "845bfdacd130e3b2c5047b3c4d60d95917747bcd4875cf4e3313a041da655ea5",
    str(RUNTIME / "main.py"): "353719427f5881b46d448143d99139b3c340fb7e2f07a326702196b6baa248e7",
    "/srv/abit-dev-linux/relay-runtime/service/aibit_relay.cjs": "f8c07bf55d56b6501ffe4d9d05169af7fa8e9e0d876b8695e128c8d58122b48e",
})
for path, digest in additional.items():
    exact(path, digest)
manifest = {
    "schema_version": 1,
    "state": "PREPARED_TESTED_SOURCE_NOT_ACTIVATED",
    "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "repository": str(REPO), "branch": "feature/agov-api-live-20261113", "observed_head": head,
    "controller_sha256": controller["input_pins"]["activate_once.py"],
    "support_sha256": controller["input_pins"]["activation_support.py"],
    "runtime_root": str(RUNTIME),
    "operation_namespace": "/var/lib/abit-ai-erp-rollouts/core-original-hold-cancel-preserving-20261116-v1",
    "current_runtime_files": files,
    "current_runtime_observed_utc": cohort["observed_utc"],
    "replacements": replacements,
    "additional_source_pins": additional,
    "original_binding": {"run_id": "5955703a-4a31-5a33-8265-63a5299bdace",
        "invocation_id": "agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0",
        "state": "cancel_requested", "task_control_state": "stopped", "task_fence": 2},
    "new_runtime_binding_included": False, "existing_admin_credential_supplied": False,
    "live_actions": 0,
}
record = save("deployment-manifest.json", manifest)
result = {"schema_version": 1, "state": "SOURCE_FREEZE_CHECKS_PASSED_NO_ACTIVATION",
          "deployment_manifest": record, "runtime_cohort_files": 79, "source_dependencies": len(additional),
          "router_inverse_bytes_exact": True, "candidate_leaves": 5,
          "retained_application_tests_repeated": False, "controller_tests": 11,
          "all_retained_log_hashes_checked": True, "all_current_source_pins_equal": True, "live_actions": 0}
save("evidence/freeze-validation.json", result)
print(json.dumps(result, sort_keys=True))

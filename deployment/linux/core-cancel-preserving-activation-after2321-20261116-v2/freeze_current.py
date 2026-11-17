"""Freeze current source and retained validation only; no application or service calls."""
from pathlib import Path
import ast
import datetime
import hashlib
import json
import os
import stat

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
OLD = HERE.parent / "core-cancel-preserving-activation-after7614-20261116"
V2 = HERE.parent / "original-contract-rejection-hold-20261116-v2"
EXPECTED_CONTROLLER = "e55353c64be8ef47dd561d080925a8e63355c204b591aad945b343c01fe13145"
EXPECTED_SUPPORT = "177ea12a77c2ce52439d55e02228c651dcfcbca0cfde42433765ee1e5107403c"
ROOT_REPORTED_HEAD = "6c153f9b0ed37007436103f6548b252bc1e54dec"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    path = Path(path)
    before = path.lstat()
    assert stat.S_ISREG(before.st_mode) and before.st_nlink == 1, str(path)
    raw = path.read_bytes()
    after = path.lstat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), str(path)
    return raw

def exact(path, digest):
    raw = read(path)
    assert sha(raw) == digest, str(path)
    return raw

def data(path):
    return json.loads(read(path))

def save(name, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with (HERE / name).open("xb") as output:
        output.write(raw)
    return {"path": name, "bytes": len(raw), "sha256": sha(raw)}

def functions(raw):
    lines = raw.splitlines(keepends=True)
    return {node.name: b"".join(lines[node.lineno - 1:node.end_lineno])
            for node in ast.parse(raw).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

def log_pair(stem, row):
    for suffix in ("stdout", "stderr"):
        exact(HERE / "evidence" / (stem + "." + suffix), row[suffix + "_sha256"])

assert not (HERE / "deployment-manifest.json").exists(), "NEVER_REPLACE_FROZEN_MANIFEST"
assert not Path("/var/lib/abit-ai-erp-rollouts/core-original-hold-cancel-preserving-after2321-20261116-v2").exists(), "NAMESPACE_ALREADY_CONSUMED"
assert os.getuid() != 0, "SOURCE_FREEZE_MUST_NOT_NEED_ROOT"

old_manifest = data(OLD / "manifest.json")
exact(OLD / "manifest.json", "689d7ef1a8627aaa5fa4e4ef7b7d48048f366557505c84cd598f8ed99866bfc5")
assert old_manifest["state"] == "HELD_STALE_RUNTIME_BASELINE_DO_NOT_EXECUTE"
assert len(old_manifest["owned_files"]) == 61
for path, item in old_manifest["owned_files"].items():
    raw = exact(REPO / path, item["sha256"])
    assert len(raw) == item["bytes"]
assert not (OLD / "deployment-manifest.json").exists()
assert data(OLD / "evidence/freeze-process.json")["exitCode"] == 1

# Preserve tested historical controller inputs instead of pretending they are current.
prestop_before = HERE / "source-before/before-prestop-refresh-activate_once.py"
prestop_test_before = HERE / "source-before/before-prestop-refresh-validate_activation.py"
resource_before = HERE / "source-before/before-source-resource-bound-activate_once.py"
resource_test_before = HERE / "source-before/before-source-resource-bound-validate_activation.py"
historical = {
    str(HERE / "activate_once.py"): {
        "27690df6f576e4e3f6f5ede937eec07634c1cd0f1a19a9e6194385610b410d67": prestop_before,
        "0cab796603a90e6f2982f56b016e4b7593f348f884ecef49b1f8a89ab3dbc03e": resource_before,
    },
    str(HERE / "validate_activation.py"): {
        "05c47d07054aba65babcc9b99869db61282ca4881debb42b52f0d69d2e0963b1": prestop_test_before,
        "a91d0a7f16d2e5f8f2e302951583e2f22a459329605128cb701dcb6dd2bc3fa8": resource_test_before,
    },
}

def validation_pin(path, digest):
    path = Path(path)
    if not path.is_absolute():
        path = REPO / path
    retained = historical.get(str(path), {}).get(digest)
    exact(retained or path, digest)

targeted = data(HERE / "evidence/targeted-validation-1.json")
assert targeted["all_passed"] and targeted["pins_unchanged"] and targeted["live_actions"] == 0
assert [row["check"] for row in targeted["checks"]] == [
    "startup-current", "candidate-current", "controller-current"]
for path, digest in targeted["input_pins"].items():
    validation_pin(path, digest)
for row in targeted["checks"]:
    assert row["exit_code"] == 0
    log_pair(row["check"] + "-1", row)
targeted_job = data(HERE / "evidence/targeted-validation-process-1.json")
assert targeted_job["id"] == "3ff8e598-1e41-438a-9452-6904e7c0d956"
assert targeted_job["running"] is False and targeted_job["exitCode"] == 0

controller_followups = [
    ("controller-prestop-attempt-2.json", "controller-prestop-process-2.json",
     "controller-prestop-2", "fb0bafad-3417-469f-9307-1f36fcaa8f67", 5),
    ("controller-resource-bound-attempt-3.json", "controller-resource-bound-process-3.json",
     "controller-resource-bound-3", "0156e6ff-074c-4b22-8a97-284a38044e85", 1),
]
for receipt, process, log, job_id, count in controller_followups:
    row = data(HERE / "evidence" / receipt)
    assert row["exit_code"] == 0 and row["pins_unchanged"] and row["live_actions"] == 0
    assert len(row["tests"]) == count and row["other_controller_functions_byte_exact"]
    for path, digest in row["input_pins"].items():
        validation_pin(HERE / path, digest)
    log_pair(log, row)
    job = data(HERE / "evidence" / process)
    assert job["id"] == job_id and job["running"] is False and job["exitCode"] == 0

before_funcs = functions(read(prestop_before))
prestop_funcs = functions(read(resource_before))
final_funcs = functions(exact(HERE / "activate_once.py", EXPECTED_CONTROLLER))
assert before_funcs.keys() == prestop_funcs.keys() == final_funcs.keys()
assert [name for name in before_funcs if before_funcs[name] != prestop_funcs[name]] == ["run"]
assert [name for name in prestop_funcs if prestop_funcs[name] != final_funcs[name]] == ["check_sources"]
assert final_funcs["run"] == prestop_funcs["run"]
old_bound = b'need(sha(read_bound(Path(path))) == digest, "ACTIVATION_DEPENDENCY_CHANGED")'
new_bound = b'need(sha(read_bound(Path(path), maximum=2097152)) == digest, "ACTIVATION_DEPENDENCY_CHANGED")'
assert read(HERE / "activate_once.py").replace(new_bound, old_bound, 1) == read(resource_before)
exact(HERE / "activation_support.py", EXPECTED_SUPPORT)

# Retain every failed attempt and verify its original completed job/log hashes.
api_jobs = {
    1: "12f88920-7058-4192-8705-ecb01c0cf4e4",
    2: "622e4e98-438f-418f-8fcb-abfdab8dd154",
    3: "4df9c5c4-2c70-4c75-a995-0060b758ed58",
    4: "7ae7bfa5-ffa9-48c2-8af2-247da203f3bf",
    5: "13174cb5-8768-48a8-af0c-56262921d0da",
    6: "3afe2cc6-27f4-4b77-81b8-293735b6cd65",
}
for number, job_id in api_jobs.items():
    receipt = data(HERE / f"evidence/api-current-attempt-{number}.json")
    assert receipt["pins_unchanged"] and receipt["live_actions"] == 0
    assert receipt["all_passed"] is (number == 6)
    for row in receipt["checks"]:
        log_pair(row["check"] + "-" + str(number), row)
        if "report_sha256" in row:
            exact(HERE / f"evidence/api-integration-current-{number}.report.json", row["report_sha256"])
    job = data(HERE / f"evidence/api-current-process-final-{number}.json")
    assert job["id"] == job_id and job["running"] is False
    assert job["exitCode"] == (0 if number == 6 else 1)
    for path, digest in receipt["input_pins"].items():
        validation_pin(path, digest)

api1 = data(HERE / "evidence/api-current-attempt-1.json")
assert api1["checks"][0]["check"] == "api-source-current" and api1["checks"][0]["exit_code"] == 0
assert b"606 declared HTTP operations; 4 justified hidden operations" in read(HERE / "evidence/api-source-current-1.stdout")
api = data(HERE / "evidence/api-current-attempt-6.json")
api_report = data(HERE / "evidence/api-integration-current-6.report.json")
assert api_report["passed"] == 91 and api_report["failed"] == 0
overlay = api_report["current_source_overlay"]
assert overlay["full_current_main_source_imported"] is True
assert overlay["production_database_or_config_access"] is False
assert overlay["network_connections_or_child_processes"] is False
assert overlay["application_lifespan_started"] is False
assert api_report["fixture_setup_platform_metadata_prepared_before_app_guard"] is True

cohort = data(HERE / "current-source-cohort.json")
full = data(HERE / "api-complete-source-manifest.json")
resource = data(HERE / "api-source-resource-manifest-4.json")
composition = data(HERE / "composition-proof.json")
primary = {row["path"]: row["sha256"] for row in cohort["files"]}
assert cohort["count"] == len(cohort["files"]) == len(primary) == 80
current = dict(full["all_source_pins"])
assert len(current) == 372 and full["python_import_closure"] == 303
current[resource["source_path"]] = resource["sha256"]
assert len(current) == 373
assert all(current[path] == digest for path, digest in primary.items())
for path, digest in current.items():
    exact(path, digest)

replacements = []
targets = set()
for row in composition["replacements"]:
    rel = row["path"]
    target = str(RUNTIME / rel)
    candidate = HERE / "candidate/open_webui" / rel
    baseline = HERE / "snapshot/open_webui" / rel
    targets.add(target)
    assert row["exact_inverse"] is True and primary[target] == row["before_sha256"]
    exact(baseline, row["before_sha256"])
    raw = exact(candidate, row["after_sha256"])
    assert len(raw) == row["after_bytes"]
    compile(raw, str(candidate), "exec")
    replacements.append({"path": rel, "candidate_path": str(candidate),
                         "before_sha256": row["before_sha256"],
                         "after_sha256": row["after_sha256"], "bytes": len(raw)})
assert len(replacements) == len(targets) == 5
assert composition["current_executor_PM_enrollment_and_serialization_preserved"]
assert composition["current_governance_maintenance_IT_lock_fixes_unchanged"]
assert composition["current_project_queue_and_orchestrator_retained_unchanged"]

additional = {path: digest for path, digest in current.items() if path not in primary}
# Snapshot/candidate/controller and fixture source actually used by the current API.
# The API harness's broader canonical-tree instrumentation pins are checked above;
# unchanged runtime and captured package source define activation dependencies.
for path, digest in api["input_pins"].items():
    if Path(path).is_relative_to(HERE):
        additional[path] = digest
for path, digest in targeted["input_pins"].items():
    if path.startswith("backend/"):
        additional[str(REPO / path)] = digest
for path in [
    HERE / "freeze_current.py", HERE / "binding.schema.json",
    REPO / "scripts/check_api_contract.py",
    REPO / "scripts/test_developer_module.py",
    REPO / "deployment/linux/original-be395-status-readonly-20261116-v1/read_status.py",
    Path("/srv/abit-dev-linux/relay-runtime/service/aibit_relay.cjs"),
    OLD / "manifest.json", V2 / "manifest.json",
]:
    additional[str(path)] = sha(read(path))
assert not targets.intersection(additional)
assert set(current).issubset(set(primary) | set(additional))
assert len(additional) > 500
for path, digest in additional.items():
    raw = exact(path, digest)
    assert 0 < len(raw) <= 2097152, str(path)
assert additional[str(HERE / "activate_once.py")] == EXPECTED_CONTROLLER
assert additional[str(HERE / "activation_support.py")] == EXPECTED_SUPPORT
assert additional[str(REPO / "deployment/linux/original-be395-status-readonly-20261116-v1/read_status.py")] == "845bfdacd130e3b2c5047b3c4d60d95917747bcd4875cf4e3313a041da655ea5"
assert additional["/srv/abit-dev-linux/relay-runtime/service/aibit_relay.cjs"] == "f8c07bf55d56b6501ffe4d9d05169af7fa8e9e0d876b8695e128c8d58122b48e"

manifest = {
    "schema_version": 2,
    "state": "PREPARED_TESTED_SOURCE_NOT_ACTIVATED",
    "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "repository": str(REPO), "branch": "feature/agov-api-live-20261113",
    "head_reported_by_root": ROOT_REPORTED_HEAD,
    "root_head_operation": "afe3ba58-e0a7-4926-af2e-fd944127755c",
    "git_mutations_by_source_preparer": 0,
    "controller_sha256": EXPECTED_CONTROLLER,
    "support_sha256": EXPECTED_SUPPORT,
    "runtime_root": str(RUNTIME),
    "operation_namespace": "/var/lib/abit-ai-erp-rollouts/core-original-hold-cancel-preserving-after2321-20261116-v2",
    "current_runtime_files": primary,
    "current_runtime_observed_utc": cohort["observed_utc"],
    "full_tested_current_source_paths": len(current),
    "original_python_import_closure": full["python_import_closure"],
    "captured_api_python_modules_including_migrations": overlay["captured_current_modules"],
    "replacements": replacements,
    "additional_source_pins": additional,
    "replacement_runtime_paths_excluded_from_additional_pins": True,
    "every_tested_current_dependency_covered": True,
    "source_collection_cross_file_atomic": False,
    "all_current_source_pins_rechecked_before_freeze": True,
    "validation": {
        "targeted_product_cases": 21,
        "startup_cases": 3, "candidate_cases": 18,
        "initial_controller_cases": 11,
        "prestop_followup_cases": 5,
        "resource_bound_followup_cases": 1,
        "unique_controller_cases": 14,
        "current_api_assertions": 91,
        "source_declared_http_operations": 606,
        "source_justified_hidden_operations": 4,
        "failed_api_attempts_retained": [1, 2, 3, 4, 5],
        "product_cases_not_repeated_for_harness_changes": True,
        "latest_controller_changed_functions_covered_by_scoped_followups": True,
    },
    "original_binding": {
        "run_id": "5955703a-4a31-5a33-8265-63a5299bdace",
        "invocation_id": "agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0",
        "state": "cancel_requested", "task_control_state": "stopped", "task_fence": 2,
        "dispatch_attempts": 1, "owner": "original", "automation_run_status": "running",
    },
    "new_runtime_binding_included": False,
    "existing_admin_credential_supplied": False,
    "activation_status": "HELD_PENDING_FRESH_REVIEWED_BINDING_AND_EXISTING_MAIN_ADMIN_AUTHORIZATION",
    "relay_health_policy": "existing_health_may_allocate_dedicated_relay_tab_and_check_login_no_send",
    "live_actions": 0,
}
raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
assert 0 < len(raw) <= 1048576, "DEPLOYMENT_MANIFEST_DEFAULT_BOUND"
record = save("deployment-manifest.json", manifest)
result = {
    "schema_version": 1,
    "state": "SOURCE_FREEZE_CHECKS_PASSED_NO_ACTIVATION",
    "deployment_manifest": record,
    "current_runtime_primary_cohort": 80,
    "full_tested_current_source_paths": len(current),
    "additional_source_pins": len(additional),
    "target_runtime_paths_in_additional": 0,
    "candidate_leaves": 5,
    "current_api_assertions_passed": 91,
    "product_test_cases": 21,
    "unique_controller_cases": 14,
    "controller_validation_sequence": [11, 5, 1],
    "all_retained_log_hashes_checked": True,
    "all_input_pins_checked_with_retained_amendment_preimages": True,
    "all_current_source_pins_equal": True,
    "old_package_frozen_held_and_unexecuted": True,
    "runtime_changes_performed": 0,
    "git_mutations": 0,
    "missing_admin_credential_and_fresh_binding": True,
}
save("evidence/freeze-validation.json", result)
print(json.dumps(result, sort_keys=True))

"""Read-only verification of source artifacts; never rerun the freeze writer or app tests."""
from pathlib import Path
import datetime
import hashlib
import json
import stat

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sha = lambda raw: hashlib.sha256(raw).hexdigest()

def read(path):
    path = Path(path)
    a = path.lstat()
    assert stat.S_ISREG(a.st_mode) and a.st_nlink == 1, str(path)
    raw = path.read_bytes()
    b = path.lstat()
    assert (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) == (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns), str(path)
    return raw

def exact(path, digest):
    raw = read(path)
    assert sha(raw) == digest, str(path)
    return raw

def data(path):
    return json.loads(read(path))

def job(path, identifier, code):
    value = data(path)
    if "content" in value:
        assert value["isError"] is False and len(value["content"]) == 1
        value = json.loads(value["content"][0]["text"])
    assert value["id"] == identifier and value["running"] is False and value["exitCode"] == code
    return value

assert not (HERE / "evidence/final-artifact-verification.json").exists()
manifest = json.loads(exact(HERE / "deployment-manifest.json", "6eb9e44f54af431b57f2bf40368aca0a0bf28aacca212d1892a64baa9286805e"))
freeze = json.loads(exact(HERE / "evidence/freeze-validation.json", "a9426d97e44287b587ea18831c04a0b3a3c9038cb6b7e83e2af297a06d269066"))
assert freeze["state"] == "SOURCE_FREEZE_CHECKS_PASSED_NO_ACTIVATION"
assert len(manifest["current_runtime_files"]) == 80 and len(manifest["additional_source_pins"]) == 726
for name in ("current_runtime_files", "additional_source_pins"):
    for path, digest in manifest[name].items():
        exact(path, digest)
runtime = Path(manifest["runtime_root"])
targets = {str(runtime / row["path"]) for row in manifest["replacements"]}
assert len(targets) == 5 and not targets.intersection(manifest["additional_source_pins"])
for row in manifest["replacements"]:
    exact(row["candidate_path"], row["after_sha256"])
assert manifest["controller_sha256"] == "e55353c64be8ef47dd561d080925a8e63355c204b591aad945b343c01fe13145"
exact(HERE / "activate_once.py", manifest["controller_sha256"])
exact(HERE / "activation_support.py", manifest["support_sha256"])

supplement = data(HERE / "verification-supplement.json")
assert supplement["state"] == "REQUIRED_CURRENT_API_GATES_PASSED_SOURCE_ONLY_NOT_ACTIVATED"
for path, expected in supplement["references"].items():
    raw = exact(HERE / path, expected["sha256"])
    assert len(raw) == expected["bytes"]
full = data(HERE / "evidence/api-verify-complete-current-1.json")
assert full["exit_code"] == 0 and full["input_pins_unchanged"] and full["current_source_pins_rechecked_before_and_after"]
assert full["wrapper_unmodified"] and full["no_existing_checks_skipped"] and not full["integration_suite_repeated"]
for path, digest in full["input_pins"].items():
    exact(path, digest)
job(HERE / "evidence/api-verify-complete-current-process-final-1.json", "a7db9def-8e92-4a06-a39e-0fb8670ff3b9", 0)
for suffix in ("stdout", "stderr"):
    exact(HERE / ("evidence/api-verify-complete-current-1." + suffix), full[suffix + "_sha256"])

api = data(HERE / "evidence/api-current-attempt-6.json")
assert api["all_passed"] and api["pins_unchanged"] and api["live_actions"] == 0
for path, digest in api["input_pins"].items():
    exact(path, digest)
job(HERE / "evidence/api-current-process-final-6.json", "3afe2cc6-27f4-4b77-81b8-293735b6cd65", 0)
report = data(HERE / "evidence/api-integration-current-6.report.json")
assert report["passed"] == 91 and report["failed"] == 0
assert report["current_source_overlay"]["full_current_main_source_imported"]
for row in api["checks"]:
    for suffix in ("stdout", "stderr"):
        exact(HERE / ("evidence/" + row["check"] + "-6." + suffix), row[suffix + "_sha256"])
    exact(HERE / "evidence/api-integration-current-6.report.json", row["report_sha256"])

overlay = data(HERE / "api-verification-current/composition.json")
for row in overlay["files"]:
    exact(HERE / "api-verification-current/source-before" / row["path"], row["before_sha256"])
    exact(REPO / row["path"], row["before_sha256"])
    exact(HERE / "api-verification-current/candidate" / row["path"], row["after_sha256"])
    exact(HERE / "api-verification-current/diff" / (row["path"] + ".diff"), row["diff_sha256"])
assert overlay["added_exact_hidden_operations"] == 8
assert overlay["unknown_router_and_undocumented_route_errors_preserved"]
job(HERE / "evidence/api-static-complete-current-process-final-1.json", "f80f05a2-386a-4bc6-bbfe-c88ca6c194c8", 1)
failed_static = data(HERE / "evidence/api-static-complete-current-1.json")
for suffix in ("stdout", "stderr"):
    exact(HERE / ("evidence/api-static-complete-current-1." + suffix), failed_static[suffix + "_sha256"])

ack = data(HERE / "evidence/freeze-acknowledgement-unavailable.json")
assert ack["original_process_id"] is None and ack["original_terminal_exit_code"] is None
assert ack["freeze_writer_rerun"] is False and not (HERE / "evidence/freeze-process-2.json").exists()
assert not Path(manifest["operation_namespace"]).exists()
assert not manifest["new_runtime_binding_included"] and not manifest["existing_admin_credential_supplied"]
old = HERE.parent / "core-cancel-preserving-activation-after7614-20261116"
old_manifest = json.loads(exact(old / "manifest.json", "689d7ef1a8627aaa5fa4e4ef7b7d48048f366557505c84cd598f8ed99866bfc5"))
assert old_manifest["state"] == "HELD_STALE_RUNTIME_BASELINE_DO_NOT_EXECUTE"
for path, item in old_manifest["owned_files"].items():
    assert len(exact(REPO / path, item["sha256"])) == item["bytes"]
assert not (old / "deployment-manifest.json").exists()

for path in HERE.rglob("*"):
    assert not path.is_symlink(), str(path)
    if path.is_file():
        assert path.suffix not in {".db", ".sqlite", ".pyc", ".pyo", ".pem", ".key"}, str(path)
        assert path.name not in {".env", ".webui_secret_key", "webui.db-wal", "webui.db-shm"}, str(path)

result = {
    "schema_version": 1,
    "state": "FROZEN_SOURCE_ARTIFACTS_AND_REQUIRED_CURRENT_GATES_VERIFIED",
    "observed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "verification_kind": "separate_read_only_artifact_verification",
    "deployment_manifest_sha256": "6eb9e44f54af431b57f2bf40368aca0a0bf28aacca212d1892a64baa9286805e",
    "freeze_validation_sha256": "a9426d97e44287b587ea18831c04a0b3a3c9038cb6b7e83e2af297a06d269066",
    "primary_source_pins_checked": 80,
    "additional_source_pins_checked": 726,
    "replacement_paths_in_additional": 0,
    "candidate_leaves": 5,
    "full_api_verify_input_pins_checked": len(full["input_pins"]),
    "api_integration_input_pins_checked": len(api["input_pins"]),
    "required_current_full_api_verify_passed": True,
    "required_current_isolated_api_integration_passed": True,
    "tests_or_freeze_writer_repeated": False,
    "original_freeze_process_acknowledgement_available": False,
    "original_freeze_process_exit_code": None,
    "old_package_remains_held": True,
    "operation_namespace_unused": True,
    "activation_runtime_binding_and_existing_admin_auth_still_required": True,
    "root_owned_health_and_review_docs_not_inventoried_here": True,
    "source_and_runtime_mutations": 0,
    "git_mutations": 0,
}
print(json.dumps(result, sort_keys=True))

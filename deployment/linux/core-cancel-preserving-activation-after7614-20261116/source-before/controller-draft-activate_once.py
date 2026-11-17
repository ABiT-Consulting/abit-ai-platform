"""One reviewed five-leaf Core cutover preserving the cancelled original.

SOURCE ONLY until Root supplies a fresh, reviewed binding and an existing
authorized Main administrator credential. No old controller is imported.
"""
from pathlib import Path
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
OUT = Path("/var/lib/abit-ai-erp-rollouts/core-original-hold-cancel-preserving-20261116-v1")
RUN = "5955703a-4a31-5a33-8265-63a5299bdace"
INVOCATION = "agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0"
PROTECTED_NAMES = {"watchdog", "Chrome", "egress", "Xvfb", "tunnel", "MCP", "auth", "Node"}
REPLACEMENTS = {
    "utils/agentic_executor.py", "utils/agentic_result_store.py",
    "utils/agentic_gateway_contracts.py", "utils/agentic_blocker_review.py",
    "routers/agentic_governance.py",
}
STATUS_READER = REPO / "deployment/linux/original-be395-status-readonly-20261116-v1/read_status.py"
STATUS_READER_SHA = "845bfdacd130e3b2c5047b3c4d60d95917747bcd4875cf4e3313a041da655ea5"
RELAY_SOURCE = Path("/srv/abit-dev-linux/relay-runtime/service/aibit_relay.cjs")
RELAY_SHA = "f8c07bf55d56b6501ffe4d9d05169af7fa8e9e0d876b8695e128c8d58122b48e"

def need(value, code):
    if not value:
        raise RuntimeError(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def read_bound(path, maximum=1048576):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and 0 < before.st_size <= maximum, "BOUNDED_REGULAR_FILE_REQUIRED")
        raw = b""
        while len(raw) <= maximum:
            part = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not part:
                break
            raw += part
        after = os.fstat(fd)
        need(len(raw) == before.st_size and
             (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
             "INPUT_CHANGED_DURING_READ")
        return raw
    finally:
        os.close(fd)

def load_inputs(args):
    need(os.geteuid() == 0, "AUTHORIZED_ROOT_OPERATOR_REQUIRED")
    need(sha(read_bound(Path(__file__))) == args.controller_sha256, "CONTROLLER_PIN_REQUIRED")
    raw = read_bound(HERE / "deployment-manifest.json")
    need(sha(raw) == args.manifest_sha256, "MANIFEST_PIN_REQUIRED")
    manifest = json.loads(raw)
    support_path = HERE / "activation_support.py"
    need(sha(read_bound(support_path)) == manifest["support_sha256"], "PRESERVATION_HELPER_CHANGED")
    spec = importlib.util.spec_from_file_location("cancel_preserving_activation_support", support_path)
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)  # New pinned stdlib-only helper; no app/old controller import.
    need(support.OUT == OUT and support.RUNTIME == RUNTIME, "EXACT_RUNTIME_NAMESPACE_REQUIRED")
    binding_raw = read_bound(Path(args.binding))
    need(sha(binding_raw) == args.binding_sha256, "CURRENT_BINDING_PIN_REQUIRED")
    binding = json.loads(binding_raw)
    need(binding.get("state") == "REVIEWED_CURRENT_BINDING"
         and type(binding.get("gate_epoch")) is int and binding["gate_epoch"] >= 0,
         "CURRENT_GATE_BINDING_REQUIRED")
    need(set(binding.get("services", {})) == {support.MAIN, support.QA}
         and set(binding.get("protected_processes", {})) == PROTECTED_NAMES,
         "EXACT_PROCESS_BINDING_REQUIRED")
    need(binding.get("configuration_sha256") and binding.get("database_identities"),
         "CURRENT_CONFIGURATION_AND_DATABASE_BINDING_REQUIRED")
    need(binding.get("relay_health_policy") ==
         "existing_health_may_allocate_dedicated_relay_tab_and_check_login_no_send",
         "RELAY_HEALTH_SIDE_EFFECT_SCOPE_REQUIRED")
    need(not OUT.exists(), "CONSUMED_CUTOVER_NAMESPACE_OBSERVE_ONLY")
    return support, manifest, binding

def supplied_admin_headers(fd):
    need(type(fd) is int and fd >= 3, "EXISTING_ADMIN_AUTHORIZATION_REQUIRED")
    # The caller supplies an existing credential through a private inherited FD.
    # No credential discovery, application signing key, token minting or browser cookie access.
    copy = os.dup(fd)
    try:
        data = b""
        while len(data) <= 16384:
            part = os.read(copy, min(4096, 16385 - len(data)))
            if not part:
                break
            data += part
    finally:
        os.close(copy)
    need(0 < len(data) <= 16384, "ADMIN_AUTH_INPUT_BOUND")
    value = json.loads(data)
    need(type(value) is dict and set(value) == {"Authorization"}, "EXISTING_BEARER_HEADER_REQUIRED")
    header = value["Authorization"]
    need(type(header) is str and re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]{20,8192}=*", header),
         "EXISTING_BEARER_HEADER_REQUIRED")
    return {"Authorization": header}

def main_tasks(c, headers):
    started = time.monotonic()
    value = c.http("/api/tasks", headers)
    need(value == {"tasks": []}, "GENUINE_ADMIN_MAIN_TASKS_NOT_EMPTY")
    return {"observed_utc": utc(), "monotonic": started, "task_count": 0,
            "normal_admin_authentication_succeeded": True}

def relay_readiness(c):
    need(sha(read_bound(RELAY_SOURCE)) == RELAY_SHA, "REVIEWED_RELAY_HEALTH_SOURCE_CHANGED")
    # Explicitly authorized existing health behavior: may allocate a dedicated
    # relay tab, probe responsiveness and check login state. It sends no message.
    value = c.http("/health", port=8810)
    need(value.get("ok") is True and value.get("authenticated") is True
         and value.get("target_ready") is True
         and type(value.get("active_relay_turns")) is int
         and value["active_relay_turns"] == 0, "RELAY_NOT_READY_OR_TURN_ACTIVE")
    return {"ready": bool(value.get("ready")), "authenticated": True, "target_ready": True,
            "active_relay_turns": 0, "browser_lifecycle_probe": True}

def service_json(c, command, label, maximum=1048576):
    result = subprocess.run(
        ["sudo", "-n", "-u", "#999", "-g", "#988", "/usr/bin/python3", "-I", "-S", "-B", *command],
        capture_output=True, timeout=60, close_fds=True)
    need(result.returncode == 0 and not result.stderr and 0 < len(result.stdout) <= maximum,
         label + "_UNCONFIRMED")
    return json.loads(result.stdout)

def state_snapshot(c):
    return service_json(c, [str(HERE / "activation_support.py"), "--state-snapshot"], "STATE_SNAPSHOT")

def original_snapshot(c):
    need(sha(read_bound(STATUS_READER)) == STATUS_READER_SHA, "REVIEWED_ORIGINAL_READER_CHANGED")
    value = service_json(c, [str(STATUS_READER), "--read-only"], "ORIGINAL_SNAPSHOT", 65536)
    need(value.get("complete") is True and value.get("run_id") == RUN
         and value.get("invocation_id") == INVOCATION
         and value.get("reader_uid") == 999 and value.get("reader_gid") == 988,
         "EXACT_ORIGINAL_READER_IDENTITY_REQUIRED")
    for item in value["snapshots"].values():
        need(item.get("query_only") == 1 and item.get("total_changes") == 0,
             "QUERY_ONLY_ORIGINAL_REQUIRED")
    return {name: item["projection"] for name, item in value["snapshots"].items()}

def validate_original(value):
    provider, governance, automation = (value[key] for key in ("provider", "governance", "automation"))
    need(provider["row_count"] == 1 and provider["identity_matches"] is True
         and provider["status"] == "delegated", "EXACT_ACCEPTED_ORIGINAL_REQUIRED")
    expected = {
        "envelope_json": (9209, "ec3b73c9292f7276d2054d20b801e389617803c5c8139e87582df27d9b7de231"),
        "response_text": (3250, "7836261f9501f33d64bef4b00c0de14a3119764be023eff4deb62ec24251068e"),
        "receipt": (64, "5a1edafc4cd1daee561a4e6517c37532376a3567b245bcb0573c466cfcf9fca7"),
        "scope_receipt_json": (1008, "7ff2bf770a4fe89b4754aad40fe7b84ca73327edc23f990dc8fb6795dcf2edc9"),
    }
    need(all(provider[key]["is_null"] is False and
             (provider[key]["bytes"], provider[key]["sha256"]) == pair for key, pair in expected.items()),
         "IMMUTABLE_ORIGINAL_EVIDENCE_CHANGED")
    row = governance["invocation"]
    need(row["row_count"] == 1 and row["identity_matches"] is True
         and row["state"] == "cancel_requested" and row["dispatch_attempts"] == 1
         and row["lease_fence"] == 2 and row["packet_version"] == 1
         and row["packet_hash"] == "3e8a02f987cb894296bd801f68b6da47d007774c265f471c7e73bbbcf4df550a",
         "CURRENT_CANCELLED_ORIGINAL_BINDING_CHANGED")
    lease = governance["task_lease"]
    need(lease["row_count"] == 1 and lease["owner_matches_original"] is True
         and lease["owner_present"] is True and lease["fence"] == 2,
         "ORIGINAL_TASK_FENCE_CHANGED")
    need(governance["task_control_states"] == [{"count": 1, "state": "stopped"}]
         and governance["results"]["local"]["row_count"] == 0
         and governance["results"]["final"]["row_count"] == 0
         and governance["gateway_session"]["row_count"] == 1
         and governance["gateway_session"]["state"] == "admitted",
         "ORIGINAL_CONTROL_OR_RESULT_CHANGED")
    calls = governance["gateway_call_states"]
    need(len(calls) == 1 and calls[0]["state"] == "completed" and calls[0]["count"] == 4,
         "ORIGINAL_GATEWAY_WORK_NOT_QUIESCENT")
    need(automation["row_count"] == 1 and automation["identity_matches"] is True
         and automation["status"] == "running" and automation["chat_is_null"] is True
         and automation["lease_is_null"] is True and automation["error"]["is_null"] is True,
         "ORIGINAL_AUTOMATION_CHANGED")
    return True

def check_processes(c, binding, *, old_services=True):
    for label, expected in binding["protected_processes"].items():
        need(c.identity(expected["pid"]) == expected, "PROTECTED_PROCESS_CHANGED")
    if old_services:
        for unit, expected in binding["services"].items():
            current = c.service(unit)
            need(current["MainPID"] == str(expected["identity"]["pid"])
                 and current["ActiveState"] == "active" and current["SubState"] == "running"
                 and current["NRestarts"] == "0"
                 and c.identity(expected["identity"]["pid"]) == expected["identity"],
                 "CURRENT_SERVICE_IDENTITY_CHANGED")
            need({key: current[key] for key in expected["policy"]} == expected["policy"],
                 "CURRENT_SERVICE_POLICY_CHANGED")

def check_sources(c, manifest, *, installed=False):
    replacements = manifest["replacements"]
    need({item["path"] for item in replacements} == REPLACEMENTS and len(replacements) == 5,
         "EXACT_FIVE_LEAVES_REQUIRED")
    before = manifest["current_runtime_files"]
    need(len(before) == 79, "EXACT_REVIEWED_COHORT_REQUIRED")
    after = dict(before)
    for item in replacements:
        target = str(RUNTIME / item["path"])
        need(before.get(target) == item["before_sha256"], "REPLACEMENT_BASELINE_NOT_IN_COHORT")
        raw = read_bound(Path(item["candidate_path"]))
        need(sha(raw) == item["after_sha256"], "TESTED_CANDIDATE_CHANGED")
        compile(raw, target, "exec")
        after[target] = item["after_sha256"]
    expected = after if installed else before
    need(all(sha(read_bound(Path(path))) == digest for path, digest in expected.items()),
         "REVIEWED_CURRENT_SOURCE_COHORT_CHANGED")
    for path, digest in manifest["additional_source_pins"].items():
        need(sha(read_bound(Path(path))) == digest, "ACTIVATION_DEPENDENCY_CHANGED")
    return expected

def quiescent(c, binding, headers, state, epoch):
    # Authenticate and establish genuine Main tasks[] before any browser health effect.
    tasks = main_tasks(c, headers)
    relay = relay_readiness(c)
    snapshot = state_snapshot(c)
    need(snapshot["gate"] == {"state": state, "epoch": epoch}, "MAINTENANCE_EPOCH_CHANGED")
    counts = snapshot["counts"]
    need(counts == {"running_automations": 1, "live_governed_runs": 0,
                    "dispatching_provider_rows": 0, "legacy_QA_work": 0},
         "OTHER_ACTIVE_WORK_PREVENTS_CUTOVER")
    need(snapshot["database_identities"] == binding["database_identities"],
         "DATA_IDENTITY_CHANGED")
    original = original_snapshot(c)
    validate_original(original)
    need(time.monotonic() - tasks["monotonic"] <= 120, "MAIN_TASK_PROOF_EXPIRED")
    return snapshot, original, tasks, relay

def gate(c, action, expected_epoch):
    command_id = "core-original-hold-cancel-" + action + "-20261116-v1"
    c.save("gate-" + action + ".intent.json", {"action": action, "command_id": command_id,
                                               "expected_epoch": expected_epoch})
    result = subprocess.run(
        ["sudo", "-n", "-u", "#999", "-g", "#988", "/usr/bin/python3", "-I", "-B",
         str(RUNTIME / "utils/agentic_maintenance.py"), action, "--data-dir", str(c.DATA),
         "--expected-epoch", str(expected_epoch), "--command-id", command_id,
         "--actor", "jimran@abitcons.com"], capture_output=True, timeout=20, close_fds=True)
    c.save("gate-" + action + ".ack.json", {"returncode": result.returncode,
           "stdout": result.stdout.decode(), "stderr_sha256": sha(result.stderr)})
    need(result.returncode == 0, "ORIGINAL_GATE_OUTCOME_UNKNOWN_NO_REPEAT")
    value = json.loads(result.stdout)
    need(value["epoch"] == expected_epoch + 1
         and value["state"] == {"close": "draining", "open": "open"}[action],
         "GATE_ACK_MISMATCH")
    return {"state": value["state"], "epoch": value["epoch"], "command_id": value["command_id"]}

def backup_bytecode(c, target, index):
    files = sorted((target.parent / "__pycache__").glob(target.stem + ".*.pyc"))
    need(len(files) <= 16, "GENERATED_BYTECODE_BOUND")
    for number, path in enumerate(files):
        raw, meta = read_bound(path, 4194304), c.metadata(path)
        c.save("old-bytecode/" + str(index) + "-" + str(number) + "-" + path.name, raw)
        need(read_bound(path, 4194304) == raw and c.metadata(path) == meta,
             "GENERATED_BYTECODE_CHANGED")
        path.unlink()

def preserved(c, binding, prior, original):
    now = state_snapshot(c)
    need(now["original_journals"] == prior["original_journals"]
         and now["database_identities"] == prior["database_identities"]
         and original_snapshot(c) == original, "ORIGINAL_JOURNALS_CHANGED_KEEP_FENCES")
    need(c.configuration() == binding["configuration_sha256"], "RUNTIME_CONFIGURATION_CHANGED")
    check_processes(c, binding, old_services=False)
    return now

def run(args):
    c, manifest, binding = load_inputs(args)
    check_sources(c, manifest)
    check_processes(c, binding)
    need(c.configuration() == binding["configuration_sha256"], "CURRENT_CONFIGURATION_CHANGED")
    headers = supplied_admin_headers(args.admin_auth_fd)
    before, original, tasks, relay = quiescent(c, binding, headers, "open", binding["gate_epoch"])
    if not args.execute:
        print(json.dumps({"state": "READONLY_PREFLIGHT_PASSED_NO_CUTOVER", "current_gate": before["gate"],
              "main_tasks": 0, "relay": relay, "original_state": "cancel_requested",
              "task_fence_preserved": 2, "source_files": 79, "replacements": 5}), flush=True)
        return
    lock = os.open(c.DATA / "erp-dot-dispatch.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(not OUT.exists(), "CONSUMED_CUTOVER_NAMESPACE_OBSERVE_ONLY")
        OUT.mkdir(mode=0o700)
        metadata = {item["path"]: c.metadata(RUNTIME / item["path"]) for item in manifest["replacements"]}
        c.save("intent.json", {"controller_sha256": args.controller_sha256,
               "manifest_sha256": args.manifest_sha256, "binding_sha256": args.binding_sha256,
               "services": binding["services"], "protected_processes": binding["protected_processes"],
               "configuration_sha256": binding["configuration_sha256"], "old_metadata": metadata,
               "original_projection_sha256": sha(canonical(original)),
               "original_journal_fingerprints": before["original_journals"],
               "cancel_requested_is_not_cancellation_acknowledgment": True,
               "no_completion_recovery_confirmation_release_or_model_send": True})
        gate(c, "close", binding["gate_epoch"])
        drained, drained_original, proof, relay = quiescent(
            c, binding, headers, "draining", binding["gate_epoch"] + 1)
        need(drained["original_journals"] == before["original_journals"] and drained_original == original,
             "DRAIN_CHANGED_ORIGINALS_NO_STOP")
        check_sources(c, manifest)
        check_processes(c, binding)
        need(c.configuration() == binding["configuration_sha256"], "DRAIN_CHANGED_CONFIGURATION")
        needed = 2 * sum(path.stat().st_size for path in list(c.DATA.glob("*.sqlite")) +
                        [c.DATA / "webui.db", c.DATA / "maintenance-admission/gate.sqlite"]) + 268435456
        need(shutil.disk_usage(OUT).free >= needed, "BACKUP_CAPACITY_REQUIRED")
        # Refresh real in-memory task proof immediately before the stop boundary.
        final_tasks = main_tasks(c, headers)
        need(time.monotonic() - final_tasks["monotonic"] <= 120, "MAIN_TASK_PROOF_EXPIRED")
        c.save("prestop-readiness.json", {"main_tasks": 0, "observed_utc": final_tasks["observed_utc"],
               "relay_readiness": relay, "original_projection_sha256": sha(canonical(original)),
               "counts": drained["counts"], "gate": drained["gate"]})
        for unit in (c.MAIN, c.QA):
            c.lifecycle(unit, "stop")
            value = c.service(unit)
            need(value["ActiveState"] == "inactive" and value["MainPID"] == "0",
                 "ORIGINAL_STOP_OUTCOME_UNKNOWN_NO_REPEAT")
        preserved(c, binding, before, original)
        c.backup()
        preserved(c, binding, before, original)
        (OUT / "old-bytecode").mkdir(mode=0o700)
        for index, item in enumerate(manifest["replacements"]):
            target = RUNTIME / item["path"]
            need(sha(read_bound(target)) == item["before_sha256"]
                 and c.metadata(target) == metadata[item["path"]], "SOURCE_CAS_REQUIRED")
            candidate = read_bound(Path(item["candidate_path"]))
            need(sha(candidate) == item["after_sha256"], "CANDIDATE_CAS_REQUIRED")
            c.install(target, candidate, str(index) + ".before")
            need(c.metadata(target) == metadata[item["path"]], "SOURCE_METADATA_CHANGED")
            backup_bytecode(c, target, index)
        after = check_sources(c, manifest, installed=True)
        c.save("runtime79-after.json", {"count": 79, "files": after})
        preserved(c, binding, before, original)
        for unit in (c.MAIN, c.QA):
            c.lifecycle(unit, "start")
        deadline = time.monotonic() + 150
        while True:
            try:
                services = {unit: c.service(unit) for unit in (c.MAIN, c.QA)}
                need(all(value["ActiveState"] == "active" and value["SubState"] == "running"
                         and value["NRestarts"] == "0" for value in services.values()), "WAIT_FOR_START")
                need(c.http("/health").get("status") is True, "WAIT_FOR_MAIN_HEALTH")
                need(c.http("/api/config").get("version") == "0.11.4", "EXPECTED_MAIN_VERSION")
                break
            except Exception:
                need(time.monotonic() < deadline, "ORIGINAL_START_OUTCOME_UNKNOWN_NO_REPEAT")
                time.sleep(1)
        check_sources(c, manifest, installed=True)
        held = preserved(c, binding, before, original)
        need(held["gate"] == {"state": "draining", "epoch": binding["gate_epoch"] + 1},
             "GATE_CHANGED_BEFORE_VERIFICATION")
        main_tasks(c, headers)
        identities = {unit: c.identity(int(value["MainPID"])) for unit, value in services.items()}
        need(all(value["uid"] == 999 and value != binding["services"][unit]["identity"]
                 for unit, value in identities.items()), "NEW_EXPECTED_SERVICE_PROCESSES_REQUIRED")
        c.save("startup-preservation.json", {"services": identities, "gate": held["gate"],
               "original_projection_sha256": sha(canonical(original)),
               "all_original_journals_preserved": True, "source_files": 79,
               "cancel_requested_and_task_fence_preserved": True})
        opened = gate(c, "open", binding["gate_epoch"] + 1)
        receipt = {"state": "CORE_FIVE_LEAF_PATCH_STARTED_AND_PRESERVED", "gate": opened,
                   "services": identities, "source_files": 79, "replaced_files": 5,
                   "original_state_during_verification": "cancel_requested",
                   "original_task_fence": 2, "original_journals_preserved_before_open": True,
                   "automatic_recovery_called_by_controller": False,
                   "cancellation_confirmation_or_ownership_release_called": False,
                   "model_messages_sent_by_controller": 0, "direct_erp_writes_by_controller": 0,
                   "relay_health_browser_lifecycle_probe_performed": True,
                   "normal_background_reconciliation_may_resume_after_open": True,
                   "changed_feature_and_authenticated_ui_verification_still_required": True}
        c.save("completed-receipt.json", receipt)
        print(json.dumps(receipt, sort_keys=True), flush=True)
    finally:
        headers.clear()
        os.close(lock)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--controller-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--admin-auth-fd", type=int)
    try:
        run(parser.parse_args())
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,120}", code):
            code = "UNCLASSIFIED_OPERATION_HELD"
        print(json.dumps({"state": "CUTOVER_HELD_OBSERVE_EXISTING_OUTCOME",
                          "error_code": code, "automatic_retry": False}), flush=True)
        raise SystemExit(65)

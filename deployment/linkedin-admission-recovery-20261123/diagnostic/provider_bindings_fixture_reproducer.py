"""Synthetic selector fixtures only; never opens production provider state."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from unittest import mock

EXPECTED = "313f45696aa3ee735c3a308fb1aaf93e87b05dec1d6c85fdba07c9040eb1f098"
if "source" not in globals():
    source = pathlib.Path(__file__).with_name("observe_provider_bindings.py").read_text()
assert hashlib.sha256(source.encode()).hexdigest() == EXPECTED
ns = {"__name__": "provider_selector_fixture_subject", "__file__": "fixture_only"}
exec(compile(source, "observe_provider_bindings.py", "exec"), ns)
checks = []
Refused = ns["Refused"]

def refused(action, expected=None):
    try:
        action()
    except (Refused, OSError) as exc:
        if expected is not None:
            assert isinstance(exc, Refused) and str(exc) == expected
        return
    raise AssertionError("Unsafe or unbounded operation accepted")

# Runtime entry point refuses ordinary callers/arguments before all reads.
with mock.patch.object(os, "getuid", return_value=0), mock.patch.object(os, "open") as opened:
    refused(ns["main"], "No arguments; exact service identity and host required")
    assert not opened.called
with mock.patch.object(sys, "argv", ["observe_provider_bindings.py", "--other-target"]), mock.patch.object(os, "open") as opened:
    refused(ns["main"], "No arguments; exact service identity and host required")
    assert not opened.called
checks.append("ordinary_identity_and_arguments_refused_before_access")

secret = "SYNTHETIC-PRIVATE-CANARY"
with tempfile.TemporaryDirectory(prefix="provider-bindings-fixture-") as temporary:
    root = pathlib.Path(temporary)
    registry, bindings = root / "chat-thread-targets.json", root / "original-dot-bindings-v1"
    ns.update(REGISTRY=str(registry), BINDINGS=str(bindings), UID=os.getuid(), GID=os.getgid())
    def put(path, value):
        path.write_bytes(ns["canonical"](value))
        path.chmod(0o600)
    def inventory():
        return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in root.rglob("*") if p.is_file() and not p.is_symlink()}
    def binding(account="a"*64, owner=None, job=None, extra=None):
        owner, job = owner or ns["OWNER"], job or ns["JOB"]
        identity = {"owner": owner, "account_id": account, "job_id": job,
                    "thread_id": "aibit:" + owner + ":dot_" + job,
                    "url": "https://chatgpt.com/dots/fixtureDOT~fixtureSEL",
                    "message_sha256": "b"*64, "scope_sha256": "c"*64}
        base = {"schema": 1, "identity": identity, "target_id": "A"*32, "origin": "admitted_original"}
        if extra:
            base.update(extra)
        stem = ns["digest"](ns["canonical"]([owner, account, job]))
        digest = ns["digest"](ns["canonical"](base))
        put(bindings / (stem + ".base.json"), base)
        root_row = {"schema": 1, "binding_sha256": digest, "root_id": "fixture_root_001",
                    "location": "https://chatgpt.com/c/fixture_conversation"}
        conversation = {"schema": 1, "binding_sha256": digest, "root_id": root_row["root_id"],
                        "url": root_row["location"]}
        terminal = {"schema": 1, "binding_sha256": digest, "root_id": root_row["root_id"],
                    "reply_id": "fixture_reply_001", "response_sha256": "d"*64}
        for kind, row in (("root", root_row), ("conversation", conversation), ("terminal", terminal)):
            put(bindings / (stem + "." + kind + ".json"), row)
        return stem, base

    absent_registry = ns["observe_registry"](ns["Budget"]())
    absent_bindings = ns["observe_bindings"](ns["Budget"]())
    assert not absent_registry["present"] and all(v is None for v in absent_registry["matching_targets"].values())
    assert not absent_bindings["present"] and not absent_bindings["account_binding_found"]
    checks.append("missing_files_report_absence_without_account_or_outcome_invention")

    put(registry, {ns["KEYS"]["local_chat"]: {"id": "B"*32, "touched": 123},
                   ns["KEYS"]["candidate_dot_thread"]: {"id": "C"*32, "touched": 456},
                   "f"*40: {"id": "D"*32, "touched": 789, "prompt": secret}})
    result = ns["observe_registry"](ns["Budget"]())
    assert set(result["matching_targets"]) == set(ns["KEYS"])
    assert result["matching_targets"]["candidate_dot_thread"] == {"id": "C"*32, "touched": 456}
    assert secret not in json.dumps(result) and "D"*32 not in json.dumps(result)
    checks.append("both_exact_registry_keys_only_and_unrelated_metadata_excluded")

    bindings.mkdir(mode=0o700)
    stem, base = binding()
    other_stem, _ = binding(owner="other-owner", extra={"prompt": secret})
    other_job_stem, _ = binding(account="e"*64, job="aibit_"+"f"*48, extra={"response": secret})
    before = inventory()
    calls = []
    real_read = ns["read_at"]
    def tracked_read(fd, name, *args, **kwargs):
        calls.append(name)
        return real_read(fd, name, *args, **kwargs)
    ns["read_at"] = tracked_read
    result = ns["observe_bindings"](ns["Budget"]())
    ns["read_at"] = real_read
    assert len(result["matching_bindings"]) == 1
    matched = result["matching_bindings"][0]
    assert matched["identity"]["account_id"] == "a"*64
    assert matched["selectors"]["conversation"]["url"] == "https://chatgpt.com/c/fixture_conversation"
    assert matched["selectors"]["terminal"]["response_sha256"] == "d"*64
    assert not matched["historical_send_verified"] and not matched["provider_outcome_verified"]
    assert not result["historical_binding_authenticated"]
    assert secret not in json.dumps(result) and "other-owner" not in json.dumps(result)
    assert all(other_stem+"."+k+".json" not in calls and other_job_stem+"."+k+".json" not in calls
               for k in ("root", "conversation", "terminal"))
    assert inventory() == before
    checks.append("owner_candidate_job_only_no_other_companions_and_zero_fixture_mutation")
    checks.append("stored_terminal_selector_never_claims_historical_send_or_settlement")

    second_stem, _ = binding(account="1"*64)
    multiple = ns["observe_bindings"](ns["Budget"]())
    assert len(multiple["matching_bindings"]) == 2
    assert not multiple["historical_binding_authenticated"]
    checks.append("multiple_account_candidates_retained_without_selecting_an_original")
    for p in bindings.glob(second_stem+".*.json"):
        p.unlink()

    # A target record with payload fields is refused rather than leaking the field.
    bad = dict(base, prompt=secret)
    put(bindings / (stem + ".base.json"), bad)
    refused(lambda: ns["observe_bindings"](ns["Budget"]()), "Selector schema differs")
    put(bindings / (stem + ".base.json"), base)
    checks.append("target_payload_fields_refused_and_never_emitted")

    companion = bindings / (stem+".terminal.json")
    saved = companion.read_bytes()
    row = json.loads(saved)
    row["binding_sha256"] = "0"*64
    put(companion, row)
    refused(lambda: ns["observe_bindings"](ns["Budget"]()), "Companion selector binding differs")
    companion.write_bytes(saved)
    checks.append("wrong_companion_binding_refused")

    orphan = bindings / ("9"*64 + ".root.json")
    put(orphan, {"schema": 1, "root_id": secret})
    refused(lambda: ns["observe_bindings"](ns["Budget"]()), "Orphan selector companion refused")
    orphan.unlink()
    checks.append("orphan_companion_refused_before_metadata_read")

    old_entry, old_base, old_match = ns["MAX_ENTRIES"], ns["MAX_BASES"], ns["MAX_MATCHES"]
    ns["MAX_ENTRIES"] = 1
    refused(lambda: ns["observe_bindings"](ns["Budget"]()), "Binding directory count exceeded")
    ns["MAX_ENTRIES"] = old_entry
    ns["MAX_BASES"] = 1
    refused(lambda: ns["observe_bindings"](ns["Budget"]()), "Base selector count exceeded")
    ns["MAX_BASES"] = old_base
    ns["MAX_MATCHES"] = 0
    refused(lambda: ns["observe_bindings"](ns["Budget"]()), "Matching selector count exceeded")
    ns["MAX_MATCHES"] = old_match
    checks.append("directory_base_and_match_count_bounds_enforced")

    over = root/"oversized"
    over.write_bytes(b"x"*(ns["MAX_FILE"]+1))
    over.chmod(0o600)
    refused(lambda: ns["read_path"](str(over), ns["MAX_FILE"], ns["Budget"](), private=True),
            "Per-file read bound exceeded")
    budget = ns["Budget"]()
    refused(lambda: budget.consume(ns["MAX_TOTAL"]+1), "Aggregate read bound exceeded")
    budget = ns["Budget"]()
    budget.deadline = -1
    refused(budget.check, "Observation deadline exceeded")
    checks.append("size_aggregate_and_deadline_bounds_enforced")

    leaf = root/"symlink"
    leaf.symlink_to(registry)
    refused(lambda: ns["read_path"](str(leaf), ns["MAX_FILE"], ns["Budget"](), private=True))
    parent_link = root/"directory-symlink"
    parent_link.symlink_to(bindings, target_is_directory=True)
    refused(lambda: ns["open_directory"](str(parent_link)))
    checks.append("file_and_parent_symlinks_refused")

    real_stat = os.stat
    baseline = inventory()
    def changed_stat(path, *args, **kwargs):
        value = real_stat(path, *args, **kwargs)
        if path == stem+".base.json":
            class Changed:
                pass
            altered = Changed()
            for field in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
                          "st_size", "st_mtime_ns", "st_ctime_ns"):
                setattr(altered, field, getattr(value, field))
            altered.st_mtime_ns += 1
            return altered
        return value
    with mock.patch.object(os, "stat", side_effect=changed_stat):
        refused(lambda: ns["observe_bindings"](ns["Budget"]()), "File changed during observation")
    assert inventory() == baseline
    checks.append("concurrent_file_drift_refused_without_mutation")

    refused(lambda: ns["parse"](b'{"same":1,"same":2}'), "Duplicate JSON key refused")
    refused(lambda: ns["parse"](b'{"value":NaN}'), "Nonfinite JSON number refused")
    checks.append("ambiguous_or_nonfinite_json_refused")

    def source_read(path, limit, budget, private=False):
        return b"", {"sha256": "0"*64}
    original = ns["read_path"]
    ns["read_path"] = source_read
    refused(lambda: ns["verify_sources"](ns["Budget"]()), "Installed selector source drift")
    ns["read_path"] = original
    checks.append("installed_contract_source_drift_refused")

    # Actual audit-hook denial in an isolated ordinary-user child. All destinations are fixtures.
    child = """
import json, os, socket, sys
subject = {"__name__": "audit_fixture_only"}
exec(compile(SOURCE, "reader_fixture", "exec"), subject)
sys.addaudithook(subject["audit_guard"])
denied = 0
for action in (
    lambda: os.open(DESTINATION, os.O_WRONLY|os.O_CREAT, 0o600),
    lambda: os.mkdir(DESTINATION),
    lambda: socket.socket(),
    lambda: os.system("true"),
):
    try: action()
    except subject["Refused"]: denied += 1
    else: raise AssertionError("Mutation or network action escaped")
print(json.dumps({"denied": denied, "created": os.path.exists(DESTINATION)}))
"""
    payload = "SOURCE="+repr(source)+"\nDESTINATION="+repr(str(root/"must-not-exist"))+"\n"+child
    process = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", payload],
                             text=True, capture_output=True, timeout=10)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"denied": 4, "created": False}
    checks.append("real_audit_hook_refuses_write_mkdir_socket_and_process_actions")

print(json.dumps({"source_sha256": EXPECTED, "tests_passed": len(checks), "checks": checks,
                  "fixture_only": True, "privileged_execution": False,
                  "production_provider_state_opened": False, "provider_api_called": False}, indent=2))

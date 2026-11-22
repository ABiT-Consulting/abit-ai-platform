"""One original-only recovery holder; it never changes the original barriers.

Only fixed protected inputs and the original holder's read-only continuity
primitives are reachable. Publication is durable and one-use. A failure keeps
the original Core close, nft seal and browser keeper for the frozen original
holder's separately reviewed finalization.
"""
from __future__ import annotations
import argparse
import base64
import copy
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

CODE = Path("/var/lib/abit-ai-native-overlay-recovery-holder-source-20261122")
STAGE = Path("/var/lib/abit-ai-native-overlay-recovery-holder-20261122")
WITNESS = Path("/var/lib/abit-ai-native-overlay-original-flock-witness-20261122")
OLD_CODE = Path("/var/lib/abit-ai-native-overlay-held-drain-source-20261122")
OLD_STAGE = Path("/var/lib/abit-ai-native-overlay-held-drain-20261122")
GLOBAL_LOCK = Path("/var/lock/abit-ai-governance-release.lock")
OLD_HOLDER_SHA = "214f79fa62136a29019178ee3241d1c6f2ea19855a2f25785d55b3595df0899c"
RECEIVER_SHA = "b18c9d5ef2c3b8bc245f8011a8ce5aae7f526d8568bfdeae3cd7778d57d02e26"
OWNER = "chat-authorized-root-orchestration-recovery-20261122T1007"
PURPOSE = "ORIGINAL_NATIVE_RECOVERY_HOLDER_V1"
MAX_HOLD_SECONDS = 900
SHA = re.compile(r"[a-f0-9]{64}\Z")
PHASES = {"prepared", "signing_may_have_started", "prepared_unpublished",
          "publication_may_have_started", "published", "recovery_terminal_verified"}
ROLES = {"backend", "agentic-governance-worker"}
ROLE_IDS = {"backend": "43de12d39ca2c84f41553605752b71afbfc6860abe00a6667978d08d83447a06",
 "agentic-governance-worker": "06564f0e2e1ea16c497e4f0e11111a9b493d75b9d989b737c9f6a712e91b9c11"}
ROLE_IMAGE = "sha256:6485ce07a88df2d5f7fefe36fd3b0b632c51f4d29e8eb0443eafdd1b8002ceb4"
LEAVES = {"agentic_native_verification.py", "agentic_approval_actions.py",
          "agentic_control.py", "srs_reconciliation.py"}
INPUT_FILES = {
    "native-recovery-manifest.json", "native-original-observation.json",
    "original-carrier-terminal.json", "definition950-before.json",
    "definition950-restored.json",
}
MANIFEST_KEYS = {"schema_version", "purpose", "site", "definition_id", "operator",
    "live_recovery_hold_authorized", "signer_sha256", "receiver_sha256",
    "original_manifest_sha256", "original_journal_sha256", "original_held_sha256",
    "original_flock_witness_sha256", "native", "inputs"}


class Held(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Held(code)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    need(type(value) is str and SHA.fullmatch(value), "EXACT_RECOVERY_DIGEST")
    return value


def unique(pairs):
    value = {}
    for name, item in pairs:
        need(name not in value, "DUPLICATE_RECOVERY_INPUT_KEY")
        value[name] = item
    return value


def decoded(raw):
    def nonfinite(_):
        raise Held("FINITE_RECOVERY_INPUT_REQUIRED")
    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise Held("BOUNDED_RECOVERY_JSON_REQUIRED") from None
    need(type(value) is dict, "RECOVERY_INPUT_OBJECT_REQUIRED")
    return value


def identity(info):
    return {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def original_module():
    # This initial fixed source read is identical in strength to the retained
    # holder's root_read and does not load imports or commands from inputs.
    path = OLD_CODE / "hold_native_overlay.py"
    for parent in (path.parent, *path.parent.parents):
        s = parent.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022
             and parent.resolve() == parent, "ORIGINAL_SOURCE_ROOT_ANCESTRY")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and identity(before)["mode"] == 0o400 and
             before.st_uid == before.st_gid == 0 and before.st_nlink == 1 and
             0 < before.st_size <= 256 * 1024, "ORIGINAL_SOURCE_METADATA")
        raw = bytearray()
        while len(raw) <= 256 * 1024:
            chunk = os.read(fd, min(65536, 256 * 1024 + 1 - len(raw)))
            if not chunk: break
            raw.extend(chunk)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns")
        after, named = os.fstat(fd), path.lstat()
        need(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k) ==
             getattr(named, k) for k in fields) and sha(raw) == OLD_HOLDER_SHA,
             "FROZEN_ORIGINAL_HOLDER_SOURCE_CHANGED")
    finally:
        os.close(fd)
    module = types.ModuleType("recovery_original_holder")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(bytes(raw), str(path), "exec"), module.__dict__)
    return module


def load_old(manifest_sha):
    old = original_module()
    a, receipt, m, raw = old.load_inputs(digest(manifest_sha))
    old.operator(a, m)
    need(a.GLOBAL_LOCK == GLOBAL_LOCK and old.STAGE == OLD_STAGE and old.CODE == OLD_CODE,
         "EXACT_ORIGINAL_RELEASE_SCOPE")
    return old, a, receipt, m, raw


def frozen_original(old, a, receipt, m, expected=None):
    r, c, raw = old.read_journal(a, receipt, m)
    held_raw = old.root_read(OLD_STAGE / "HELD.json", 0o600, 65536)
    held = old.held_record(a, receipt, r)
    publication = old.admission_state(a, m, r, c)
    need(publication["status"] == "published" and c["admission"]["phase"] == "published"
         and not any(name in c["intents"] for name in ("nft_unseal", "open"))
         and c.get("native_reconciled") is not True, "ORIGINAL_PUBLISHED_HOLD_STILL_REQUIRED")
    receipt.validate_binding(held["binding"])
    need(held["holder"] == r["original_holder"] and
         held["binding"]["holder_sha256"] == OLD_HOLDER_SHA and
         held["binding"]["operation_id"] == m["native"]["operation_id"],
         "ORIGINAL_HOLDER_BINDING_CHANGED")
    if expected is not None:
        need(sha(raw) == expected["original_journal_sha256"] and
             sha(held_raw) == expected["original_held_sha256"], "ORIGINAL_HELD_JOURNAL_CHANGED")
    return r, c, raw, held, held_raw


def boot_id():
    with open("/proc/sys/kernel/random/boot_id", "rb") as f:
        raw = f.read(129)
    value = raw.decode("ascii").strip()
    need(len(raw) <= 128 and re.fullmatch(r"[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}", value),
         "EXACT_HOST_BOOT_REQUIRED")
    return value


def proc_bytes(path, limit=8192):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(fd, limit + 1)
        need(0 < len(raw) <= limit, "BOUNDED_KERNEL_WITNESS")
        return raw
    finally:
        os.close(fd)


def lock_metadata(info):
    need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
         and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
         "EXACT_EXISTING_RELEASE_LOCK_METADATA")
    return identity(info)


def fd_lock_proof(pid, fd, named):
    need(type(pid) is int and pid > 1 and type(fd) is int and 0 <= fd <= 4096,
         "EXACT_FLOCK_PROCESS_DESCRIPTOR")
    link = Path("/proc") / str(pid) / "fd" / str(fd)
    info = link.stat()  # Intentional fixed process FD magic-link observation.
    need(lock_metadata(info) == named, "ORIGINAL_FLOCK_DESCRIPTOR_IDENTITY_CHANGED")
    raw = proc_bytes(Path("/proc") / str(pid) / "fdinfo" / str(fd))
    rows = re.findall(rb"(?m)^lock:\s+\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+(\d+)\s+"
                      rb"([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)\s+0\s+EOF\s*$", raw)
    need(len(rows) == 1, "EXACT_FLOCK_WRITE_OWNERSHIP_REQUIRED")
    owner, major, minor, inode = rows[0]
    need(int(owner) == pid and int(major, 16) == os.major(info.st_dev)
         and int(minor, 16) == os.minor(info.st_dev) and int(inode) == info.st_ino,
         "KERNEL_FLOCK_OWNER_OR_INODE_CHANGED")
    need(lock_metadata(link.stat()) == named and lock_metadata(GLOBAL_LOCK.lstat()) == named,
         "FLOCK_NAMED_ENTRY_CHANGED")
    return {"fd": fd, "file": named, "flock": "FLOCK_ADVISORY_WRITE",
            "owner_pid": pid, "fdinfo_sha256": sha(raw)}


def capture_original_lock(old, a, receipt, m):
    """No flock acquisition, mutation, or original-stage write in this observer."""
    old.operator(a, m)
    r, c, raw, held, held_raw = frozen_original(old, a, receipt, m)
    expected = r["original_holder"]
    need(a.process(expected["pid"]) == expected, "ORIGINAL_HOLDER_NOT_LIVE_FOR_CAPTURE")
    named = lock_metadata(GLOBAL_LOCK.lstat())
    path = Path("/proc") / str(expected["pid"]) / "fd"
    names = os.listdir(path)
    need(len(names) <= 256 and all(x.isdecimal() for x in names), "BOUNDED_ORIGINAL_HOLDER_FDS")
    found = []
    for text in names:
        try: candidate = (path / text).stat()
        except FileNotFoundError: continue
        if (candidate.st_dev, candidate.st_ino) == (named["device"], named["inode"]):
            found.append(fd_lock_proof(expected["pid"], int(text), named))
    need(len(found) == 1, "UNIQUE_ORIGINAL_RELEASE_FLOCK_REQUIRED")
    value = {"schema_version": 1, "purpose": "ACTUAL_ORIGINAL_NATIVE_HOLDER_FLOCK_WITNESS",
             "original_manifest_sha256": a.MANIFEST_SHA, "original_journal_sha256": sha(raw),
             "original_held_sha256": sha(held_raw), "original_holder": expected,
             "holder_source_sha256": OLD_HOLDER_SHA, "boot_id": boot_id(),
             "lock_path": str(GLOBAL_LOCK), "lock": found[0], "flock_acquired_by_observer": False,
             "original_stage_writes": False, "native_actions": 0}
    need(a.process(expected["pid"]) == expected and
         fd_lock_proof(expected["pid"], found[0]["fd"], named) == found[0],
         "ORIGINAL_HOLDER_CHANGED_DURING_CAPTURE")
    need(old.root_read(OLD_STAGE / "journal.json", 0o600) == raw and
         old.root_read(OLD_STAGE / "HELD.json", 0o600, 65536) == held_raw,
         "ORIGINAL_HOLD_CHANGED_DURING_CAPTURE")
    if WITNESS.exists():
        existing = old.root_read(WITNESS / "original-flock-witness.json", 0o600, 32768)
        need(decoded(existing) == value, "DIFFERENT_ORIGINAL_FLOCK_WITNESS_EXISTS")
    else:
        old.trusted_directory(WITNESS.parent)
        WITNESS.mkdir(mode=0o700); a.fsyncdir(WITNESS.parent)
        a.savejson(WITNESS / "original-flock-witness.json", value)
    proof_raw = old.root_read(WITNESS / "original-flock-witness.json", 0o600, 32768)
    need(decoded(proof_raw) == value and a.process(expected["pid"]) == expected and
         fd_lock_proof(expected["pid"], found[0]["fd"], named) == found[0],
         "ORIGINAL_CAPTURE_NOT_STABLE")
    return {"state": "ORIGINAL_FLOCK_WITNESSED_READ_ONLY", "witness_sha256": sha(proof_raw),
            "flock_acquired_by_observer": False, "native_actions": 0}


def load_inputs(manifest_sha):
    old = original_module()
    raw = old.root_read(CODE / "recovery-hold-manifest.json")
    need(sha(raw) == digest(manifest_sha), "RECOVERY_HOLD_MANIFEST_CHANGED")
    m = decoded(raw)
    need(set(m) == MANIFEST_KEYS and m["schema_version"] == 1 and type(m["schema_version"]) is int
         and m["purpose"] == PURPOSE and m["site"] == "erp.abit.ai" and
         m["definition_id"] == 950 and type(m["definition_id"]) is int and
         m["operator"] == {"owner": OWNER, "actor": OWNER + ":native-leaf-recovery-hold"} and
         m["live_recovery_hold_authorized"] is True, "EXACT_REVIEWED_RECOVERY_HOLD_SCOPE")
    need(sha(old.root_read(CODE / "hold_native_recovery.py")) == digest(m["signer_sha256"])
         and m["receiver_sha256"] == RECEIVER_SHA, "RECOVERY_SIGNER_OR_RECEIVER_CHANGED")
    for key in ("original_manifest_sha256", "original_journal_sha256",
                "original_held_sha256", "original_flock_witness_sha256"):
        digest(m[key])
    need(type(m["native"]) is dict and set(m["native"]) ==
         {"operation_id", "controller_sha256", "manifest_sha256"}, "EXACT_NEW_RECOVERY_NATIVE_SCOPE")
    for value in m["native"].values(): digest(value)
    need(type(m["inputs"]) is dict and set(m["inputs"]) == INPUT_FILES, "EXACT_RECOVERY_INPUT_SET")
    inputs = {}
    for name, checksum in m["inputs"].items():
        data = old.root_read(CODE / name, limit=2 * 1024 * 1024)
        need(sha(data) == digest(checksum), "RECOVERY_INPUT_CHANGED")
        inputs[name] = (decoded(data), data)
    a, held_receipt, original, original_raw = old.load_inputs(m["original_manifest_sha256"])
    old.operator(a, original)
    need(a.GLOBAL_LOCK == GLOBAL_LOCK, "SAME_FIXED_RELEASE_LOCK_REQUIRED")
    sys.modules["held_admission_receipt"] = held_receipt
    receiver_raw = old.root_read(CODE / "recovery_receipt.py", limit=65536)
    need(sha(receiver_raw) == RECEIVER_SHA, "FROZEN_RECOVERY_RECEIVER_CHANGED")
    receiver = types.ModuleType("fixed_native_recovery_receipt")
    receiver.__file__ = str(CODE / "recovery_receipt.py")
    sys.modules[receiver.__name__] = receiver
    exec(compile(receiver_raw, receiver.__file__, "exec"), receiver.__dict__)
    return old, a, held_receipt, receiver, m, raw, original, inputs


def witness(old, a, m, original, r):
    raw = old.root_read(WITNESS / "original-flock-witness.json", 0o600, 32768)
    need(sha(raw) == m["original_flock_witness_sha256"], "ORIGINAL_FLOCK_WITNESS_CHANGED")
    value = decoded(raw)
    need(value.get("schema_version") == 1 and value.get("purpose") ==
         "ACTUAL_ORIGINAL_NATIVE_HOLDER_FLOCK_WITNESS" and
         value.get("original_manifest_sha256") == m["original_manifest_sha256"] and
         value.get("original_journal_sha256") == m["original_journal_sha256"] and
         value.get("original_held_sha256") == m["original_held_sha256"] and
         value.get("original_holder") == r["original_holder"] and
         value.get("holder_source_sha256") == OLD_HOLDER_SHA and value.get("boot_id") == boot_id()
         and value.get("lock_path") == str(GLOBAL_LOCK) and
         value.get("flock_acquired_by_observer") is False and value.get("original_stage_writes") is False
         and value.get("native_actions") == 0, "ACTUAL_ORIGINAL_FLOCK_WITNESS_REQUIRED")
    need(value["lock"]["flock"] == "FLOCK_ADVISORY_WRITE" and
         value["lock"]["owner_pid"] == r["original_holder"]["pid"] and
         lock_metadata(GLOBAL_LOCK.lstat()) == value["lock"]["file"],
         "ORIGINAL_RELEASE_LOCK_INODE_CHANGED")
    return value


def acquire_same_lock(a, w):
    # Never O_CREAT: a missing/replaced original lock cannot be repaired here.
    before = lock_metadata(GLOBAL_LOCK.lstat())
    need(before == w["lock"]["file"], "ORIGINAL_RELEASE_LOCK_REPLACED")
    fd = os.open(GLOBAL_LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        need(lock_metadata(os.fstat(fd)) == before and lock_metadata(GLOBAL_LOCK.lstat()) == before,
             "ORIGINAL_LOCK_CHANGED_BEFORE_ACQUIRE")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proof = fd_lock_proof(os.getpid(), fd, before)
        return fd, proof
    except BaseException:
        os.close(fd)
        raise


def native_observation(value, original_binding, native_manifest):
    need(type(value.get("schema_version")) is int and value["schema_version"] == 1 and value.get("state") == "ORIGINAL_ONLY_OBSERVATION"
         and value.get("operation_id") == original_binding["operation_id"] and
         value.get("manifest_sha256") == original_binding["manifest_sha256"] and
         value.get("controller_sha256") == original_binding["controller_sha256"] and
         value.get("context_integrity_verified") is True, "EXACT_NATIVE_ORIGINAL_OBSERVATION_REQUIRED")
    context_hash = digest(value.get("context_sha256"))
    keeper = value.get("keeper")
    need(type(keeper) is dict and keeper.get("state") == "ORIGINAL_FDS_HELD"
         and keeper.get("current_verified") is True and keeper.get("operation_id") == value["operation_id"]
         and keeper.get("context_sha256") == context_hash and keeper.get("apply_authority_granted") is False
         and keeper.get("source_sha256") == native_manifest["native_fd_keeper_sha256"] and
         keeper.get("unit") == "abit-native-fd-keeper-20261122-" + value["operation_id"] + ".service"
         and type(keeper.get("invocation_id")) is str and
         re.fullmatch("[a-f0-9]{32}", keeper["invocation_id"]), "CURRENT_ORIGINAL_NATIVE_KEEPER_REQUIRED")
    digest(keeper.get("ready_sha256"))
    for key in ("pid", "start_ticks"):
        need(type(keeper.get(key)) is int and keeper[key] > 1, "EXACT_NATIVE_KEEPER_GENERATION")
    for key in ("source_or_config_actions", "service_actions", "journal_writes"):
        need(type(value.get(key)) is int and value[key] == 0, "READ_ONLY_NATIVE_OBSERVATION_REQUIRED")
    need(all(value.get(k) is False for k in ("admission_renewed", "replay_allowed",
                                           "stopped_root_rollback_authority")),
         "OBSERVATION_CANNOT_GRANT_ORIGINAL_APPLY_AUTHORITY")
    eligibility = value.get("recovery_eligibility")
    need(canonical(eligibility) == canonical({"native_mutations_by_observer": 0, "apply_resume_authority": False,
         "source_attribution_complete": True, "current_original_keeper_verified": True,
         "service_attribution_complete": True,
         "original_carrier_terminal_and_950_restored": "requires_external_verified_receipts",
         "service_changes_require_new_recovery_attestation": True}), "FULL_NATIVE_ATTRIBUTION_REQUIRED")
    roles = value.get("roles")
    need(type(roles) is dict and set(roles) == ROLES, "EXACT_TWO_NATIVE_ROLES")
    accepted = {"exact_original", "owned_postimage", "original_content_and_metadata_restored",
                "unprepared_exact_original", "archive_matches_candidate_bytes_standard_metadata",
                "archive_matches_original_bytes_standard_metadata"}
    for role, row in roles.items():
        need(type(row.get("leaves")) is dict and set(row["leaves"]) == LEAVES and
             type(row.get("running")) is bool and row.get("generation_attributed_to_original") is True
             and row.get("container_id") == ROLE_IDS[role] and
             row.get("image_id") == ROLE_IMAGE, "EXACT_FOUR_NATIVE_LEAVES_AND_ROLE")
        for name, leaf in row["leaves"].items():
            allowed = accepted | ({"missing", "original_absence", "archive_absent_original_helper"}
                                  if name == "agentic_native_verification.py" else set())
            need(type(leaf) is dict and leaf.get("state") in allowed and
                 type(leaf.get("source_actions")) is int and leaf["source_actions"] == 0,
                 "FOREIGN_NATIVE_SOURCE_CANNOT_AUTHORIZE_RECOVERY")
    pause = value.get("site_pause")
    need(type(pause) is dict and pause.get("state") in
         {"original_no_pause_intent", "original_bytes", "paused_candidate_bytes"} and
         type(pause.get("source_actions")) is int and pause["source_actions"] == 0,
         "ORIGINAL_SCHEDULER_CONFIGURATION_MUST_BE_ATTRIBUTED")
    services = value.get("services")
    need(type(services) is dict and set(services) == {"worker-stop", "backend-hup", "worker-start"},
         "EXACT_ORIGINAL_SERVICE_OBSERVATIONS")
    for row in services.values():
        need(all(type(row.get(k)) is bool for k in ("intent_present", "may_have_started",
             "wire_ack_present", "positive_receipt_present")) and
             type(row.get("new_calls")) is int and row["new_calls"] == 0, "ORIGINAL_SERVICE_ATTRIBUTION_REQUIRED")
        if row["may_have_started"]:
            need(type(row.get("current_original_outcome_proved")) is bool,
                 "ORIGINAL_SERVICE_OUTCOME_MUST_BE_EXPLICIT")
            if row["current_original_outcome_proved"]: digest(row.get("proof_sha256"))
            else: need(type(row.get("error_code")) is str and
                       re.fullmatch("[A-Z0-9_]{1,128}", row["error_code"]), "FIXED_ORIGINAL_UNCERTAINTY_CODE")
    journal = value.get("original_journal")
    need(type(journal) is dict and set(journal) == {"sha256", "records", "values_exported"} and
         journal["values_exported"] is False and type(journal["records"]) is dict and
         1 <= len(journal["records"]) <= 128 and sha(canonical(journal["records"])) == journal["sha256"],
         "EXACT_NATIVE_ORIGINAL_JOURNAL_DIGEST")
    for name, proof in journal["records"].items():
        need(type(name) is str and "/" not in name and "\\" not in name and
             type(proof) is dict and set(proof) == {"sha256", "bytes"} and
             type(proof["bytes"]) is int and 0 < proof["bytes"] <= 192 * 1024,
             "BOUNDED_NATIVE_JOURNAL_METADATA")
        digest(proof["sha256"])
    return {"original_context_sha256": context_hash, "keeper_ready_sha256": keeper["ready_sha256"],
            "original_observation_sha256": sha(canonical(value))}


def business(value):
    value = copy.deepcopy(value)
    for key in ("revision", "modifiedOn", "modifiedBy", "lastRelease"):
        value.pop(key, None)
    for env in value["environments"]:
        env.pop("currentRelease", None)
    return value


def carrier_proof(inputs, expected_release):
    """Parse actual full Azure GET receipts; never trust a declared success flag."""
    terminal, raw = inputs["original-carrier-terminal.json"]
    before, before_raw = inputs["definition950-before.json"]
    restored, restored_raw = inputs["definition950-restored.json"]
    need(type(expected_release) is int and expected_release > 0 and
         terminal.get("id") == expected_release and
         type(terminal.get("id")) is int and terminal.get("releaseDefinition", {}).get("id") == 950
         and type(terminal.get("environments")) is list and len(terminal["environments"]) == 1,
         "EXACT_ORIGINAL_AZURE_RELEASE_REQUIRED")
    env = terminal["environments"][0]
    need(env.get("releaseId") == expected_release and env.get("definitionEnvironmentId") == 967
         and type(env.get("id")) is int and env["id"] > 0, "EXACT_ORIGINAL_AZURE_ENVIRONMENT")
    terminal_states = {"succeeded", "failed", "canceled", "partiallySucceeded", "rejected"}
    need(env.get("status") in terminal_states and type(env.get("deploySteps")) is list
         and len(env["deploySteps"]) == 1, "ONE_TERMINAL_ORIGINAL_CARRIER_ATTEMPT")
    step = env["deploySteps"][0]
    need(type(step.get("id")) is int and step["id"] > 0 and
         type(step.get("attempt")) is int and step["attempt"] == 1 and
         step.get("status") in terminal_states and
         type(step.get("releaseDeployPhases")) is list and len(step["releaseDeployPhases"]) == 1,
         "ORIGINAL_CARRIER_ATTEMPT_NOT_TERMINAL_OR_REPLAYED")
    phase = step["releaseDeployPhases"][0]
    need(phase.get("status") in terminal_states and
         type(phase.get("deploymentJobs")) is list and len(phase["deploymentJobs"]) == 1,
         "ORIGINAL_CARRIER_PHASE_NOT_TERMINAL")
    job = phase["deploymentJobs"][0]
    need(type(job.get("job")) is dict and job["job"].get("status") in terminal_states and
         type(job.get("tasks")) is list and 1 <= len(job["tasks"]) <= 64 and
         all(type(task) is dict and task.get("status") in terminal_states | {"skipped", "notStarted"}
             for task in job["tasks"]), "ORIGINAL_CARRIER_JOB_NOT_TERMINAL")
    need(before.get("id") == restored.get("id") == 950 and
         type(before.get("revision")) is int and before["revision"] > 0 and
         type(restored.get("revision")) is int and restored["revision"] == before["revision"] + 2
         and terminal.get("releaseDefinitionRevision") == before["revision"] + 1 and
         type(before.get("environments")) is list and len(before["environments"]) == 1
         and before["environments"][0].get("id") == 967 and
         type(restored.get("environments")) is list and len(restored["environments"]) == 1
         and restored["environments"][0].get("id") == 967,
         "EXACT_DEFINITION950_RESTORATION_REVISIONS")
    need(canonical(business(before)) == canonical(business(restored)) and
         restored.get("lastRelease", {}).get("id") == expected_release and
         restored["environments"][0].get("currentRelease", {}).get("id") == expected_release,
         "FULL_DEFINITION950_BUSINESS_FIELDS_NOT_RESTORED")
    return {"release_id": expected_release, "environment_id": env["id"],
            "attempt_id": step["id"], "terminal_state": env["status"],
            "terminal_receipt_sha256": sha(raw), "definition_id": 950,
            "restored_revision": restored["revision"],
            "definition_restoration_sha256": sha(restored_raw), "business_fields_exact": True}


def initial_context(old, a, receipt, receiver, m, original, inputs):
    r, c, raw, held, held_raw = frozen_original(old, a, receipt, original, m)
    need(a.original_absent(r["original_holder"]), "ORIGINAL_HOLDER_NOT_POSITIVELY_CEASED")
    w = witness(old, a, m, original, r)
    original_native = decoded(old.root_read(OLD_CODE / "native-manifest.json"))
    obs = inputs["native-original-observation.json"][0]
    facts = native_observation(obs, held["binding"], original_native)
    native, native_raw = inputs["native-recovery-manifest.json"]
    need(sha(native_raw) == m["native"]["manifest_sha256"] and
         native.get("operation_id") == m["native"]["operation_id"] and
         native.get("controller_sha256") == m["native"]["controller_sha256"] and
         native.get("purpose") == "TEMPORARY_NATIVE_TWO_ROLE_LEAF_ROLLBACK_V1" and
         native.get("restore_only") is True and native.get("original_apply_resume_authority") is False and
         native.get("live_recovery_authorized") is True and
         native.get("original_operation_id") == held["binding"]["operation_id"] and
         native.get("original_manifest_sha256") == held["binding"]["manifest_sha256"] and
         native.get("native_fd_keeper_sha256") == original_native["native_fd_keeper_sha256"],
         "EXACT_DISTINCT_NATIVE_RESTORE_SCOPE")
    need(all(native.get(k) == v for k, v in facts.items()), "NATIVE_RECOVERY_OBSERVATION_CHANGED")
    need(type(obs.get("release_id")) is int and obs["release_id"] == native["original_release_id"],
         "ORIGINAL_NATIVE_RELEASE_BINDING")
    carrier = carrier_proof(inputs, obs["release_id"])
    return r, c, raw, held, w, facts, carrier


def own_update(a, r):
    temp = STAGE / ("journal-" + str(os.getpid()) + ".tmp")
    a.savejson(temp, r)
    os.replace(temp, STAGE / "journal.json")
    a.fsyncdir(STAGE)


def current_flock(a, fd, expected, holder):
    need(a.process(holder["pid"]) == holder and holder["pid"] == os.getpid(),
         "THIS_ORIGINAL_RECOVERY_HOLDER_REQUIRED")
    need(fd_lock_proof(os.getpid(), fd, expected["file"]) == expected, "RECOVERY_FLOCK_CHANGED")
    return expected


def continuity(old, a, receipt, original, r, c, m, context, fd, flock, self_process):
    old.operator(a, original)
    frozen_original(old, a, receipt, original, m)
    need(a.original_absent(r["original_holder"]), "ORIGINAL_HOLDER_REAPPEARED")
    witness(old, a, m, original, r)
    current_flock(a, fd, flock, self_process)
    b, g = a.barriers(original, r, c)
    observation = old.observations(a, receipt, original, r, c, b, g)
    receipt.validate_observation(context["binding"]["original_binding"], observation, now=time.time())
    current_flock(a, fd, flock, self_process)
    need(old.root_read(OLD_STAGE / "journal.json", 0o600) == context["original_journal_raw"],
         "ORIGINAL_JOURNAL_CHANGED_DURING_RECOVERY_HOLD")
    binding = context["binding"]
    result = {name: binding[name] for name in (
        "original_holder", "recovery_holder", "original_carrier",
        "original_holder_journal_sha256", "original_context_sha256", "keeper_ready_sha256",
        "original_observation_sha256", "continuity_sha256")}
    result.update(held_observation=observation, original_barriers_continuously_retained=True,
                  original_apply_resumed=False, barriers_recreated=False)
    return result


def publish_once(old, a, receipt, receiver, original, m, r, c, journal, context, fd, flock, holder):
    need(journal["phase"] == "prepared" and all(not os.path.lexists(STAGE/name) for name in
         ("recovery-admission.signed.json", "RECOVERY_HELD.json", "PUBLICATION_INTENT.json",
          "recovery-admission.txt")), "ORIGINAL_RECOVERY_SIGNATURE_ONLY")
    first = continuity(old, a, receipt, original, r, c, m, context, fd, flock, holder)
    final = continuity(old, a, receipt, original, r, c, m, context, fd, flock, holder)
    receiver.validate_observation(context["binding"], first, now=time.time())
    receiver.validate_observation(context["binding"], final, now=time.time())
    journal["phase"] = "signing_may_have_started"; own_update(a, journal)
    key = a.raw(old.KEY, 4096)
    encoded = receiver.make_receipt(context["binding"], first, final, key, now=time.time())
    receiver.verify(encoded, key, context["binding"], now=time.time())
    key = None
    signed = base64.b64decode(encoded, validate=True); text = encoded.encode() + b"\n"
    a.save(STAGE/"recovery-admission.signed.json", signed)
    held = {"schema_version":1, "purpose":"ACTUAL_ORIGINAL_ONLY_RECOVERY_HOLD",
            "manifest_sha256":journal["manifest_sha256"], "binding":context["binding"],
            "first":first, "final":final, "receipt_sha256":sha(signed),
            "original_apply_resumed":False, "original_barriers_modified":False}
    a.savejson(STAGE/"RECOVERY_HELD.json", held)
    journal.update(phase="prepared_unpublished", held_sha256=a.filehash(STAGE/"RECOVERY_HELD.json"),
                   receipt_sha256=sha(signed), text_sha256=sha(text))
    own_update(a, journal)
    journal["phase"] = "publication_may_have_started"; own_update(a, journal)
    a.savejson(STAGE/"PUBLICATION_INTENT.json", {
        "schema_version":1, "manifest_sha256":journal["manifest_sha256"],
        "operation_id":m["native"]["operation_id"], "held_sha256":journal["held_sha256"],
        "receipt_sha256":sha(signed), "text_sha256":sha(text)})
    a.save(STAGE/"recovery-admission.txt", text)  # Carrier-visible bytes LAST.
    journal["phase"] = "published"; own_update(a, journal)
    return held


def make_context(old, a, receipt, receiver, original, m, raw, initial, fd, flock):
    r, c, original_raw, held, w, facts, carrier = initial
    current = a.process(os.getpid())
    need(current["uid"] == 0 and current != r["original_holder"], "DISTINCT_ROOT_RECOVERY_HOLDER")
    current_flock(a, fd, flock, current)
    expiry = old.operator(a, original, MAX_HOLD_SECONDS + 120)
    cessation = {"schema_version": 1, "original_holder": r["original_holder"],
                 "original_source_sha256": OLD_HOLDER_SHA, "boot_id": boot_id(),
                 "positively_ceased": True, "original_flock_witness_sha256": m["original_flock_witness_sha256"]}
    need(a.original_absent(r["original_holder"]), "ORIGINAL_HOLDER_CESSATION_CHANGED")
    a.savejson(STAGE/"original-cessation.json", cessation)
    a.savejson(STAGE/"current-release-flock.json", flock)
    stable = {"schema_version":1, "original_manifest_sha256": m["original_manifest_sha256"],
              "original_journal_sha256": m["original_journal_sha256"],
              "original_held_sha256": m["original_held_sha256"],
              "original_flock_witness_sha256": m["original_flock_witness_sha256"],
              "original_native_observation": facts, "original_carrier": carrier,
              "original_core": held["binding"]["core"], "original_relay": held["binding"]["relay"],
              "release_lock_file": flock["file"], "original_barriers_recreated": False}
    a.savejson(STAGE/"continuity-binding.json", stable)
    binding = {**m["native"], "signer_sha256": m["signer_sha256"],
               "original_binding": held["binding"],
               "original_hold_receipt_sha256": held["receipt_sha256"],
               "original_holder_journal_sha256": m["original_journal_sha256"], **facts,
               "original_holder": {**r["original_holder"], "source_sha256": OLD_HOLDER_SHA,
                   "positively_ceased": True, "cessation_proof_sha256": a.filehash(STAGE/"original-cessation.json")},
               "recovery_holder": {**current, "source_sha256": m["signer_sha256"],
                   "same_release_flock_held": True, "flock_proof_sha256": a.filehash(STAGE/"current-release-flock.json")},
               "original_carrier": carrier, "holder_lease_expires_at": receipt.utc(expiry),
               "continuity_sha256": sha(canonical(stable))}
    receiver.validate_binding(binding)
    return {"binding":binding, "original_journal_raw":original_raw}, current


def check_native_terminal(value, binding):
    need(type(value) is dict and type(value.get("schema_version")) is int and value["schema_version"] == 1
         and value.get("state") == "ORIGINAL_TWO_ROLE_SOURCE_RESTORED_AND_VERIFIED"
         and value.get("operation_id") == binding["original_binding"]["operation_id"]
         and value.get("manifest_sha256") == binding["original_binding"]["manifest_sha256"]
         and value.get("context_sha256") == binding["original_context_sha256"]
         and value.get("keeper_ready_sha256") == binding["keeper_ready_sha256"]
         and value.get("recovery_operation_id") == binding["operation_id"]
         and value.get("recovery_manifest_sha256") == binding["manifest_sha256"]
         and value.get("original_apply_resumed") is False, "EXACT_ORIGINAL_NATIVE_ROLLBACK_TERMINAL")
    for key in ("all_original_leaves_and_metadata_restored", "scheduler_original_bytes_restored",
                "container_ids_and_images_preserved", "unrelated_app_bytes_and_metadata_preserved",
                "root_continuous_closure_still_required"):
        need(value.get(key) is True, "NATIVE_RECOVERY_PRESERVATION_INCOMPLETE")
    digest(value.get("source_readback_sha256")); digest(value.get("runtime_readback_sha256"))


def completion_proof(old, a, m, binding):
    """Root-staged exact native/actual carrier evidence; never settle from a label."""
    request_raw = old.root_read(STAGE/"recovery-reconciliation.json",0o600,65536)
    request = decoded(request_raw)
    keys = {"schema_version", "purpose", "recovery_operation_id", "recovery_manifest_sha256",
            "original_operation_id", "original_held_sha256", "files"}
    names = {"native-original-rollback.json", "native-recovery-completed.json",
             "recovery-carrier-terminal.json", "recovery-definition950-before.json",
             "recovery-definition950-restored.json"}
    need(set(request) == keys and type(request["schema_version"]) is int and request["schema_version"] == 1
         and request["purpose"] == "ROOT_REVIEWED_ORIGINAL_ONLY_RECOVERY_OUTCOME"
         and request["recovery_operation_id"] == binding["operation_id"]
         and request["recovery_manifest_sha256"] == binding["manifest_sha256"]
         and request["original_operation_id"] == binding["original_binding"]["operation_id"]
         and request["original_held_sha256"] == m["original_held_sha256"]
         and type(request["files"]) is dict and set(request["files"]) == names,
         "EXACT_RECOVERY_RECONCILIATION_REQUEST")
    inputs = {}
    for name, checksum in request["files"].items():
        raw = old.root_read(STAGE/name,0o600,2*1024*1024)
        need(sha(raw) == digest(checksum), "RECOVERY_TERMINAL_EVIDENCE_CHANGED")
        inputs[name] = (decoded(raw), raw)
    left, left_raw = inputs["native-original-rollback.json"]
    right, right_raw = inputs["native-recovery-completed.json"]
    need(left_raw == right_raw and canonical(left) == canonical(right),
         "BOTH_ORIGINAL_AND_NEW_NATIVE_TERMINALS_MUST_MATCH")
    check_native_terminal(left,binding)
    release = inputs["recovery-carrier-terminal.json"][0].get("id")
    need(type(release) is int and release > binding["original_carrier"]["release_id"],
         "DISTINCT_RECOVERY_CARRIER_REQUIRED")
    carrier = carrier_proof({
        "original-carrier-terminal.json": inputs["recovery-carrier-terminal.json"],
        "definition950-before.json": inputs["recovery-definition950-before.json"],
        "definition950-restored.json": inputs["recovery-definition950-restored.json"]},release)
    return {"request_sha256":sha(request_raw), "native_terminal_sha256":sha(left_raw),
            "recovery_carrier":carrier, "native_result":left,
            "original_barriers_must_remain_held":True}


def final_observation(old, a, receipt, original, r, c, m, binding):
    frozen_original(old,a,receipt,original,m)
    need(a.original_absent(r["original_holder"]), "ORIGINAL_HOLDER_CESSATION_CHANGED")
    b,g = a.barriers(original,r,c)
    observation = old.observations(a,receipt,original,r,c,b,g)
    receipt.validate_observation(binding["original_binding"],observation,now=time.time())
    return observation


def record_completion(old,a,receipt,original,r,c,m,journal,binding):
    proof = completion_proof(old,a,m,binding)
    final = final_observation(old,a,receipt,original,r,c,m,binding)
    # Proposal only: this helper never writes the original stage or opens intake.
    # Root must install these exact create-only records there before frozen
    # original --finalize can release its own browser/nft/Core barriers.
    proposed = {"schema_version":1, "purpose":"ROOT_REVIEWED_ORIGINAL_NATIVE_OUTCOME",
        "operation_id":binding["original_binding"]["operation_id"],
        "native_manifest_sha256":binding["original_binding"]["manifest_sha256"],
        "held_sha256":m["original_held_sha256"], "outcome":"ROLLED_BACK_VERIFIED",
        "native_result_sha256":proof["native_terminal_sha256"],
        "definition_restore_sha256":proof["recovery_carrier"]["definition_restoration_sha256"],
        "all_owned_mutations_terminal":True, "source_and_configuration_verified":True,
        "backend_health_verified":True, "worker_health_verified":True,
        "definition_950_restored":True, "unknown_native_action_remaining":False,
        "retry_performed":False, "reopen_original_intake":True}
    a.save_or_verify(STAGE/"original-holder-reconciliation.proposed.json",canonical(proposed)+b"\n")
    value = {"schema_version":1, "state":"RECOVERY_VERIFIED_ORIGINAL_BARRIERS_RETAINED",
             "manifest_sha256":journal["manifest_sha256"], "binding":binding,
             "proof":{k:v for k,v in proof.items() if k != "native_result"},
             "final_observation_sha256":sha(canonical(final)),
             "original_stage_writes":False, "original_barrier_mutations":0,
             "original_holder_finalize_still_required":True}
    a.save_or_verify(STAGE/"COMPLETED.json",canonical(value)+b"\n")
    journal["phase"]="recovery_terminal_verified";own_update(a,journal)
    return {"state":value["state"],"completed_sha256":a.filehash(STAGE/"COMPLETED.json"),
            "own_flock_may_release":True,"original_barriers_released":False}


def execute(old,a,receipt,receiver,m,raw,original,inputs,initial,fd,flock):
    need(not os.path.lexists(STAGE),"ONE_USE_RECOVERY_STAGE_EXISTS")
    old.operator(a,original,MAX_HOLD_SECONDS+120)
    old.trusted_directory(STAGE.parent)
    STAGE.mkdir(mode=0o700);a.fsyncdir(STAGE.parent)
    a.save(STAGE/"recovery-hold-manifest.json",raw)
    r,c,_,_,_,_,_=initial
    context,holder=make_context(old,a,receipt,receiver,original,m,raw,initial,fd,flock)
    journal={"schema_version":1,"manifest_sha256":sha(raw),"phase":"prepared",
             "original_holder":r["original_holder"],"recovery_holder":holder,
             "binding":context["binding"],"created_at":a.now()}
    own_update(a,journal)
    publish_once(old,a,receipt,receiver,original,m,r,c,journal,context,fd,flock,holder)
    print(json.dumps({"state":"HELD_FOR_ONE_ORIGINAL_RECOVERY",
          "held_sha256":a.filehash(STAGE/"RECOVERY_HELD.json"),
          "receipt_path":str(STAGE/"recovery-admission.txt"),"original_barriers_modified":False}),flush=True)
    end=time.monotonic()+MAX_HOLD_SECONDS
    while time.monotonic()<end:
        continuity(old,a,receipt,original,r,c,m,context,fd,flock,holder)
        if os.path.lexists(STAGE/"recovery-reconciliation.json"):
            return record_completion(old,a,receipt,original,r,c,m,journal,context["binding"])
        time.sleep(1)
    raise Held("RECOVERY_OUTCOME_UNCONFIRMED_ORIGINAL_BARRIERS_RETAINED")


def journal_read(old,receiver,manifest_sha):
    raw=old.root_read(STAGE/"journal.json",0o600,65536)
    value=decoded(raw)
    need(value.get("schema_version")==1 and value.get("manifest_sha256")==manifest_sha
         and value.get("phase") in PHASES and type(value.get("binding")) is dict,
         "ORIGINAL_RECOVERY_JOURNAL_REQUIRED")
    receiver.validate_binding(value["binding"])
    need(value.get("recovery_holder")=={k:v for k,v in value["binding"]["recovery_holder"].items()
         if k in {"pid","start_ticks","uid","cmdline_sha256"}},
         "ORIGINAL_RECOVERY_PROCESS_BINDING")
    return value,raw


def observe(old,a,receipt,receiver,m,raw,original):
    if not os.path.lexists(STAGE/"journal.json"):
        return {"state":"RECOVERY_STAGE_WITHOUT_JOURNAL" if STAGE.exists() else "NO_RECOVERY_STAGE",
                "writes_by_observer":False,"signature_or_launch_authority":False}
    journal,journal_raw=journal_read(old,receiver,sha(raw))
    files={}
    for name in ("recovery-admission.signed.json","RECOVERY_HELD.json","PUBLICATION_INTENT.json",
                 "recovery-admission.txt","COMPLETED.json","HOLDER_FAILURE.json"):
        files[name]=sha(old.root_read(STAGE/name,0o600,65536)) if os.path.lexists(STAGE/name) else None
    need(old.root_read(STAGE/"journal.json",0o600,65536)==journal_raw,
         "RECOVERY_JOURNAL_CHANGED_DURING_OBSERVATION")
    return {"state":"ORIGINAL_RECOVERY_OBSERVATION_ONLY","phase":journal["phase"],
            "journal_sha256":sha(journal_raw),"files":files,
            "recovery_holder_positively_ceased":a.original_absent(journal["recovery_holder"]),
            "publication_may_have_started":journal["phase"] in {"publication_may_have_started","published",
                 "recovery_terminal_verified"} or files["PUBLICATION_INTENT.json"] is not None,
            "writes_by_observer":False,"signature_or_launch_authority":False}


def main():
    p=argparse.ArgumentParser()
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture-original-lock",action="store_true")
    group.add_argument("--execute",action="store_true")
    group.add_argument("--observe",action="store_true")
    group.add_argument("--finalize",action="store_true")
    p.add_argument("--manifest-sha256",required=True)
    args=p.parse_args()
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
         "ISOLATED_OPERATOR_INTERPRETER")
    need(Path(__file__)==CODE/"hold_native_recovery.py","PROTECTED_RECOVERY_HOLDER_ENTRYPOINT")
    if args.capture_original_lock:
        old,a,receipt,original,_=load_old(args.manifest_sha256)
        result=capture_original_lock(old,a,receipt,original)
        print(json.dumps(result),flush=True)
        return
    old,a,receipt,receiver,m,raw,original,inputs=load_inputs(args.manifest_sha256)
    old.operator(a,original)
    if args.observe:
        print(json.dumps(observe(old,a,receipt,receiver,m,raw,original)),flush=True)
        return
    initial=initial_context(old,a,receipt,receiver,m,original,inputs)
    fd,flock=acquire_same_lock(a,initial[4])
    try:
        if args.execute:
            result=execute(old,a,receipt,receiver,m,raw,original,inputs,initial,fd,flock)
        else:
            journal,_=journal_read(old,receiver,sha(raw))
            need(a.original_absent(journal["recovery_holder"]),"RECOVERY_HOLDER_STILL_ALIVE")
            need(journal["phase"] in {"publication_may_have_started","published","recovery_terminal_verified"},
                 "NO_POSSIBLE_PUBLISHED_RECOVERY_TO_FINALIZE")
            result=record_completion(old,a,receipt,original,initial[0],initial[1],m,journal,journal["binding"])
        print(json.dumps(result),flush=True)
    except BaseException as error:
        if STAGE.exists() and not os.path.lexists(STAGE/"HOLDER_FAILURE.json"):
            a.savejson(STAGE/"HOLDER_FAILURE.json",{"schema_version":1,"error_type":type(error).__name__,
                "original_barriers_modified":False,"automatic_reopen_performed":False,
                "signature_renewed":False,"at":a.now()})
        raise
    finally:
        os.close(fd)  # Only this process's release flock; no original barrier action.


if __name__=="__main__":
    try:main()
    except BaseException as error:
        if isinstance(error,SystemExit):raise
        print(json.dumps({"state":"RECOVERY_HOLD_UNRESOLVED","error_type":type(error).__name__,
                          "automatic_reopen_performed":False}),flush=True)
        raise SystemExit(78)

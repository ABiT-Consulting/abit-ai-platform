#!/usr/bin/env python3
"""Read-only review of the approved seven-file package. Never activates a release.

The accountable operator has not supplied a supported scoped handoff. This
fallback deliberately has no backup, apply, restart, rollback or acknowledgement
input. Even a clean observation remains blocked. It does not import live code or
open business/maintenance databases. Output contains hashes/metadata, not content.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
COMMIT = "8c1bc2308fdf693659520103559863682333299b"
MANIFEST_SHA = "955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250"
BINDINGS_SHA = "841831d3d4521f6dcdac318b26d2378dc416f1ac3661ad9adb0441b725c12b9d"
ALLOWED = frozenset((
    "frontend/agent-activity.js", "models/automation_admission.py",
    "models/automations.py", "routers/automations.py",
    "utils/agentic_maintenance.py", "utils/automation_admission.py",
    "utils/automations.py",
))
MISSING_INPUTS = (
    "SCOPED_OPERATOR_HANDOFF_NOT_BOUND",
    "CLOSED_GATE_AND_ORIGINAL_WORK_RECEIPT_REQUIRED",
    "PRIVATE_BACKUP_RESTORE_AND_PRESERVATION_RECEIPTS_REQUIRED",
    "FRESH_RELAY_BROWSER_COHORT_REQUIRED",
    "ONCE_ONLY_ACTIVATION_CONTROLLER_NOT_BOUND",
)


class ReviewBlocked(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReviewBlocked(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, limit: int = 2 * 1024 * 1024) -> tuple[bytes, os.stat_result]:
    """No symlink following, special files, hard links, unlimited reads or writes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                    "REGULAR_SINGLE_LINK_FILE_REQUIRED")
            require(before.st_size <= limit, "READ_LIMIT_EXCEEDED")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(all(getattr(before, k) == getattr(after, k) for k in fields),
                "FILE_CHANGED_DURING_READ")
        require(len(data) <= limit, "READ_LIMIT_EXCEEDED")
        return data, after
    except ReviewBlocked:
        raise
    except OSError:
        raise ReviewBlocked("READ_UNAVAILABLE") from None


def file_info(path: Path) -> dict:
    data, s = read_regular(path)
    return {
        "sha256": sha(data), "uid": s.st_uid, "gid": s.st_gid,
        "mode": oct(stat.S_IMODE(s.st_mode)), "nlink": s.st_nlink,
        "device": s.st_dev, "inode": s.st_ino, "bytes": len(data),
    }


def directory_info(path: Path) -> dict:
    try:
        s = path.lstat()
        require(stat.S_ISDIR(s.st_mode), "REAL_DIRECTORY_REQUIRED")
        return {
            "parent_device": s.st_dev, "parent_inode": s.st_ino,
            "parent_uid": s.st_uid, "parent_gid": s.st_gid,
            "parent_mode": oct(stat.S_IMODE(s.st_mode)),
        }
    except OSError:
        raise ReviewBlocked("DIRECTORY_UNAVAILABLE") from None


def payload_manifest(package: Path) -> dict:
    raw, _ = read_regular(package / "manifest.json")
    require(sha(raw) == MANIFEST_SHA, "APPROVED_MANIFEST_HASH_CHANGED")
    manifest = json.loads(raw)
    rows = manifest.get("files", [])
    require(len(rows) == 7 and {row.get("path") for row in rows} == ALLOWED,
            "SEVEN_FILE_SCOPE_CHANGED")
    for row in rows:
        p = package / "candidate" / "open_webui" / row["path"]
        require(p.resolve() == p.absolute(), "CANDIDATE_SYMLINK_NOT_ALLOWED")
        info = file_info(p)
        require(info["sha256"] == row["after_sha256"]
                and info["bytes"] == row["bytes"], "CANDIDATE_HASH_CHANGED")
    return manifest


def bindings(path: Path) -> dict:
    raw, _ = read_regular(path)
    require(sha(raw) == BINDINGS_SHA, "OBSERVATION_BINDING_CHANGED")
    value = json.loads(raw)
    require(value["source_commit"] == COMMIT
            and value["approved_manifest_sha256"] == MANIFEST_SHA
            and value["operator_handoff_bound"] is False
            and set(value["files"]) == ALLOWED, "BINDING_SCOPE_CHANGED")
    return value


def check_live_files(root: Path, expected: dict) -> list[dict]:
    checks = []
    for rel, row in expected.items():
        try:
            target = root / rel
            require(target.parent.resolve() == target.parent.absolute(),
                    "TARGET_PARENT_SYMLINK_CHANGED")
            parent = directory_info(target.parent)
            require(all(parent[k] == row[k] for k in parent), "TARGET_PARENT_CHANGED")
            try:
                target.lstat()
                exists = True
            except FileNotFoundError:
                exists = False
            if row["sha256"] is None:
                require(not exists, "NEW_TARGET_ALREADY_EXISTS")
                reference = file_info(root / row["metadata_reference"])
                require(all(reference[k] == row[k] for k in ("uid", "gid", "mode")),
                        "NEW_MODULE_ADJACENT_METADATA_CHANGED")
            else:
                require(exists, "PREIMAGE_MISSING")
                actual = file_info(target)
                require(actual["sha256"] == row["sha256"], "PREIMAGE_HASH_CHANGED")
                require(all(actual[k] == row[k] for k in ("uid", "gid", "mode", "nlink")),
                        "PREIMAGE_METADATA_CHANGED")
            checks.append({"target": rel, "ok": True, "code": "PREIMAGE_AND_METADATA_MATCH"})
        except ReviewBlocked as exc:
            checks.append({"target": rel, "ok": False, "code": exc.code})
        except OSError:
            checks.append({"target": rel, "ok": False, "code": "READ_UNAVAILABLE"})
    return checks


def process(pid: int) -> dict:
    try:
        root = Path("/proc") / str(pid)
        first = (root / "stat").read_text().rsplit(")", 1)[1].split()
        result = {
            "pid": pid, "start_ticks": int(first[19]), "uid": root.stat().st_uid,
            "cmdline_sha256": sha((root / "cmdline").read_bytes()),
            "cgroup": (root / "cgroup").read_text().strip(),
        }
        second = (root / "stat").read_text().rsplit(")", 1)[1].split()
        require(first[19] == second[19], "PROCESS_CHANGED_DURING_READ")
        return result
    except (OSError, ValueError, IndexError):
        raise ReviewBlocked("PROCESS_IDENTITY_UNAVAILABLE") from None


def inspect_runtime(binding: dict) -> dict:
    observation = {"host": socket.gethostname(), "unit": {}, "processes": {},
                   "configuration": [], "owner": {}, "errors": []}
    def inspect(name, operation):
        try:
            return operation()
        except ReviewBlocked as exc:
            observation["errors"].append({"target": name, "code": exc.code})
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            observation["errors"].append({"target": name, "code": "OBSERVATION_UNAVAILABLE"})
        return None
    observation["runtime_resolved"] = inspect(
        "runtime", lambda: str(Path(binding["runtime_root"]).resolve(strict=True)))
    result = inspect("unit", lambda: subprocess.run(
        ["systemctl", "show", "abit-ai.service",
         *[arg for key in binding["unit"] for arg in ("-p", key)]],
        check=True, capture_output=True, text=True, timeout=15))
    if result is not None:
        observation["unit"] = dict(line.split("=", 1)
            for line in result.stdout.splitlines() if "=" in line)
    for name, expected in {"core": binding["core"], **binding["preserved_processes"]}.items():
        observation["processes"][name] = inspect(name, lambda e=expected: process(e["pid"]))
    for expected in binding["configuration"]:
        info = inspect(expected["path"], lambda e=expected: file_info(Path(e["path"])))
        observation["configuration"].append({"path": expected["path"], "info": info})
    raw = inspect("operator_record", lambda: read_regular(Path(binding["operator_record"]))[0])
    if raw is not None:
        value = inspect("operator_record_json", lambda: json.loads(raw))
        if isinstance(value, dict):
            # Deliberately omit unrelated session contents and any supplied handoff.
            observation["owner"] = {k: value.get(k) for k in (
                "owner", "state", "lease_expires_at", "updated_at")}
            observation["owner"]["record_sha256"] = sha(raw)
    def stage():
        try:
            Path(binding["proposed_stage"]).lstat()
            return "exists"
        except FileNotFoundError:
            return "absent"
    observation["stage"] = inspect("proposed_stage", stage)
    return observation


def evaluate(observation: dict, binding: dict, file_checks: list[dict], now: dt.datetime) -> dict:
    problems = [dict(row) for row in observation.get("errors", [])]
    def compare(ok, code, target):
        if not ok:
            problems.append({"target": target, "code": code})
    compare(observation.get("host") == binding["host"], "HOST_CHANGED", "host")
    compare(observation.get("runtime_resolved") == binding["runtime_resolved"],
            "RUNTIME_CHANGED", "runtime")
    for key, expected in binding["unit"].items():
        compare(observation.get("unit", {}).get(key) == expected, "UNIT_CHANGED", key)
    for name, expected in {"core": binding["core"], **binding["preserved_processes"]}.items():
        actual = observation.get("processes", {}).get(name) or {}
        compare(all(actual.get(k) == v for k, v in expected.items()),
                "PROCESS_IDENTITY_CHANGED", name)
    for expected in binding["configuration"]:
        actual = next((r["info"] for r in observation.get("configuration", [])
                       if r["path"] == expected["path"]), None) or {}
        compare(all(actual.get(k) == v for k, v in expected.items() if k != "path"),
                "CONFIGURATION_CHANGED", expected["path"])
    owner = observation.get("owner", {})
    compare(owner.get("owner") == binding["observed_owner"]
            and owner.get("state") == binding["observed_owner_state"],
            "OPERATOR_IDENTITY_CHANGED", "operator")
    try:
        expiry = dt.datetime.fromisoformat(owner["lease_expires_at"].replace("Z", "+00:00"))
        compare(expiry.tzinfo is not None and expiry > now,
                "OPERATOR_LEASE_EXPIRED", "operator")
    except (KeyError, TypeError, ValueError):
        compare(False, "OPERATOR_LEASE_UNAVAILABLE", "operator")
    compare(observation.get("stage") == "absent", "EXISTING_OR_UNOBSERVED_ONCE_ONLY_STAGE", "stage")
    problems.extend({"target": row["target"], "code": row["code"]}
                    for row in file_checks if not row["ok"])
    return {
        "status": "BLOCKED_OPERATOR_BINDING_REQUIRED",
        "source_commit": COMMIT, "approved_manifest_sha256": MANIFEST_SHA,
        "observed_at": now.isoformat(), "activation_authorized": False,
        "read_only_checks_passed": not problems, "drift_or_read_failures": problems,
        "required_operator_inputs": list(MISSING_INPUTS),
        "existing_owner": {k: owner.get(k) for k in (
            "owner", "state", "lease_expires_at", "updated_at", "record_sha256")},
        "target_checks": file_checks, "protected_gate_state_verified": False,
        "private_backups_verified": False, "cross_database_atomic_snapshot_claimed": False,
        "lifecycle_commands_executed": 0, "files_or_databases_written": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight"])
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent.parent
                        / "linkedin-admission-20261122")
    args = parser.parse_args(argv)
    try:
        binding = bindings(Path(__file__).resolve().with_name("bindings.json"))
        manifest = payload_manifest(args.package.resolve())
        require(manifest["runtime_root"] == binding["runtime_root"], "MANIFEST_RUNTIME_CHANGED")
        for row in manifest["files"]:
            require(row["expected_before_sha256"] == binding["files"][row["path"]]["sha256"],
                    "MANIFEST_PREIMAGE_BINDING_CHANGED")
        observation = inspect_runtime(binding)
        live = Path(binding["runtime_resolved"])
        checks = check_live_files(live, binding["files"])
        result = evaluate(observation, binding, checks, dt.datetime.now(dt.timezone.utc))
    except (ReviewBlocked, OSError, ValueError, KeyError, TypeError) as exc:
        result = {"status": "BLOCKED_PREFLIGHT_UNAVAILABLE", "activation_authorized": False,
                  "code": exc.code if isinstance(exc, ReviewBlocked) else "REVIEW_UNAVAILABLE",
                  "lifecycle_commands_executed": 0, "files_or_databases_written": 0}
    print(json.dumps(result, indent=2, sort_keys=True))
    # A clean read-only review still cannot authorize or activate a release.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

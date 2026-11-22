"""Isolated tests of the nonmutating preflight; no real service, data or session."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("release_preflight", HERE / "preflight.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def sample():
    binding = p.bindings(HERE / "bindings.json")
    observation = {
        "host": binding["host"], "runtime_resolved": binding["runtime_resolved"],
        "unit": copy.deepcopy(binding["unit"]),
        "processes": {"core": copy.deepcopy(binding["core"]),
                      **copy.deepcopy(binding["preserved_processes"])},
        "configuration": [{"path": row["path"], "info": {
            k: v for k, v in row.items() if k != "path"
        }} for row in binding["configuration"]],
        "owner": {"owner": binding["observed_owner"], "state": binding["observed_owner_state"],
                  "lease_expires_at": "2026-11-22T22:24:48.603297+00:00",
                  "updated_at": "2026-11-22T20:24:48.603297+00:00"},
        "stage": "absent", "errors": [],
    }
    return binding, observation


NOW = dt.datetime(2026, 11, 22, 21, 0, tzinfo=dt.timezone.utc)


class DecisionTests(unittest.TestCase):
    def result(self, change=None):
        binding, observation = sample()
        if change:
            change(observation)
        return p.evaluate(observation, binding, [], NOW)

    def test_complete_ordinary_observation_cannot_authorize(self):
        result = self.result()
        self.assertTrue(result["read_only_checks_passed"])
        self.assertFalse(result["activation_authorized"])
        self.assertIn("SCOPED_OPERATOR_HANDOFF_NOT_BOUND", result["required_operator_inputs"])
        self.assertEqual(result["lifecycle_commands_executed"], 0)

    def test_claimed_ack_is_not_authority(self):
        def change(observation):
            observation["owner"].update(
                acknowledged=True, handoff={"commit": p.COMMIT, "allow_restart": True})
        result = self.result(change)
        self.assertFalse(result["activation_authorized"])
        self.assertNotIn("acknowledged", json.dumps(result))
        self.assertIn("SCOPED_OPERATOR_HANDOFF_NOT_BOUND", result["required_operator_inputs"])

    def test_unknown_owner_never_implies_handoff(self):
        result = self.result(lambda o: o["owner"].update(owner="new-caller"))
        self.assertIn("OPERATOR_IDENTITY_CHANGED",
                      [r["code"] for r in result["drift_or_read_failures"]])

    def test_expired_owner_lease_is_not_cleared(self):
        result = self.result(lambda o: o["owner"].update(lease_expires_at="2026-11-22T20:00:00+00:00"))
        self.assertIn("OPERATOR_LEASE_EXPIRED",
                      [r["code"] for r in result["drift_or_read_failures"]])
        self.assertFalse(result["activation_authorized"])

    def test_unknown_owner_lease_is_blocked(self):
        result = self.result(lambda o: o["owner"].pop("lease_expires_at"))
        self.assertIn("OPERATOR_LEASE_UNAVAILABLE",
                      [r["code"] for r in result["drift_or_read_failures"]])

    def test_naive_lease_is_blocked(self):
        result = self.result(lambda o: o["owner"].update(lease_expires_at="2026-11-22T22:00:00"))
        self.assertFalse(result["read_only_checks_passed"])

    def test_pid_reuse_is_detected(self):
        result = self.result(lambda o: o["processes"]["core"].update(start_ticks=1))
        self.assertIn({"target": "core", "code": "PROCESS_IDENTITY_CHANGED"},
                      result["drift_or_read_failures"])

    def test_qa_drift_is_detected(self):
        result = self.result(lambda o: o["processes"].update(qa=None))
        self.assertIn({"target": "qa", "code": "PROCESS_IDENTITY_CHANGED"},
                      result["drift_or_read_failures"])

    def test_unit_drift_is_detected(self):
        result = self.result(lambda o: o["unit"].update(SendSIGKILL="no"))
        self.assertIn({"target": "SendSIGKILL", "code": "UNIT_CHANGED"},
                      result["drift_or_read_failures"])

    def test_launcher_drift_is_detected(self):
        result = self.result(lambda o: o["configuration"][0]["info"].update(sha256="0" * 64))
        self.assertIn("CONFIGURATION_CHANGED",
                      [r["code"] for r in result["drift_or_read_failures"]])

    def test_stage_exists_requires_observing_old_result(self):
        result = self.result(lambda o: o.update(stage="exists"))
        self.assertIn("EXISTING_OR_UNOBSERVED_ONCE_ONLY_STAGE",
                      [r["code"] for r in result["drift_or_read_failures"]])
        self.assertFalse(result["activation_authorized"])

    def test_stage_unknown_cannot_be_recreated(self):
        result = self.result(lambda o: o.update(stage=None))
        self.assertFalse(result["read_only_checks_passed"])
        self.assertFalse(result["activation_authorized"])

    def test_repeat_observation_has_no_execution_or_state_mutation(self):
        binding, observation = sample()
        before = copy.deepcopy(observation)
        first = p.evaluate(observation, binding, [], NOW)
        second = p.evaluate(observation, binding, [], NOW)
        self.assertEqual(first, second)
        self.assertEqual(before, observation)
        self.assertEqual(second["files_or_databases_written"], 0)
        self.assertFalse(second["activation_authorized"])

    def test_read_failure_cannot_be_clean_result(self):
        result = self.result(lambda o: o["errors"].append(
            {"target": "operator_record", "code": "READ_UNAVAILABLE"}))
        self.assertFalse(result["read_only_checks_passed"])

    def test_no_protected_or_global_snapshot_claim(self):
        result = self.result()
        self.assertFalse(result["protected_gate_state_verified"])
        self.assertFalse(result["private_backups_verified"])
        self.assertFalse(result["cross_database_atomic_snapshot_claimed"])


class FileChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "models").mkdir()
        self.existing = self.root / "models/automations.py"
        self.existing.write_bytes(b"retained source\n")
        self.existing.chmod(0o644)
        info = p.file_info(self.existing)
        self.rows = {"models/automations.py": {
            k: info[k] for k in ("sha256", "uid", "gid", "mode", "nlink")
        }}
        self.rows["models/automations.py"].update(p.directory_info(self.existing.parent))
        self.rows["models/automation_admission.py"] = {
            **self.rows["models/automations.py"], "sha256": None,
            "metadata_reference": "models/automations.py",
        }

    def tearDown(self):
        self.temp.cleanup()

    def codes(self):
        return [r["code"] for r in p.check_live_files(self.root, self.rows) if not r["ok"]]

    def test_actual_regular_files_pass_with_new_module_absent(self):
        self.assertEqual(self.codes(), [])

    def test_real_hash_drift_blocks(self):
        self.existing.write_bytes(b"unrelated source edit\n")
        self.assertIn("PREIMAGE_HASH_CHANGED", self.codes())

    def test_actual_mode_drift_blocks(self):
        self.existing.chmod(0o600)
        self.assertIn("PREIMAGE_METADATA_CHANGED", self.codes())
        self.assertIn("NEW_MODULE_ADJACENT_METADATA_CHANGED", self.codes())

    def test_per_file_uid_is_not_normalized(self):
        self.rows["models/automations.py"]["uid"] += 1
        self.assertIn("PREIMAGE_METADATA_CHANGED", self.codes())

    def test_target_parent_inode_drift_blocks(self):
        self.rows["models/automations.py"]["parent_inode"] += 1
        self.assertIn("TARGET_PARENT_CHANGED", self.codes())

    def test_existing_new_module_blocks_even_if_empty(self):
        (self.root / "models/automation_admission.py").write_bytes(b"")
        self.assertIn("NEW_TARGET_ALREADY_EXISTS", self.codes())

    def test_new_module_dangling_symlink_blocks(self):
        (self.root / "models/automation_admission.py").symlink_to(self.root / "absent")
        self.assertIn("NEW_TARGET_ALREADY_EXISTS", self.codes())

    def test_existing_file_symlink_blocks(self):
        self.existing.unlink()
        self.existing.symlink_to(self.root / "absent")
        self.assertIn("READ_UNAVAILABLE", self.codes())

    def test_hardlinked_file_blocks(self):
        os.link(self.existing, self.root / "alias")
        self.assertIn("REGULAR_SINGLE_LINK_FILE_REQUIRED", self.codes())

    def test_missing_existing_file_blocks(self):
        self.existing.unlink()
        self.assertIn("PREIMAGE_MISSING", self.codes())

    def test_read_bound_blocks_oversize(self):
        with self.assertRaisesRegex(p.ReviewBlocked, "READ_LIMIT_EXCEEDED"):
            p.read_regular(self.existing, limit=1)

    def test_readonly_checks_preserve_actual_bytes_and_metadata(self):
        before = {str(f): (f.read_bytes(), f.stat().st_mtime_ns, stat.S_IMODE(f.stat().st_mode))
                  for f in self.root.rglob("*") if f.is_file()}
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.codes(), [])
        after = {str(f): (f.read_bytes(), f.stat().st_mtime_ns, stat.S_IMODE(f.stat().st_mode))
                 for f in self.root.rglob("*") if f.is_file()}
        self.assertEqual(before, after)


class PackageChecks(unittest.TestCase):
    def test_actual_approved_payload_and_binding(self):
        manifest = p.payload_manifest(HERE.parent / "linkedin-admission-20261122")
        binding = p.bindings(HERE / "bindings.json")
        self.assertEqual(len(manifest["files"]), 7)
        self.assertEqual(binding["source_commit"], p.COMMIT)
        self.assertFalse(binding["operator_handoff_bound"])

    def test_modified_manifest_fails_before_candidate_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifest.json").write_text('{"files":[]}')
            with self.assertRaisesRegex(p.ReviewBlocked, "APPROVED_MANIFEST_HASH_CHANGED"):
                p.payload_manifest(root)

    def test_modified_binding_cannot_authorize(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bindings.json"
            data = json.loads((HERE / "bindings.json").read_text())
            data["operator_handoff_bound"] = True
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(p.ReviewBlocked, "OBSERVATION_BINDING_CHANGED"):
                p.bindings(path)

    def test_mutating_actions_and_ack_options_are_rejected(self):
        for action in ("apply", "backup", "restart", "rollback", "finalize", "open"):
            with self.subTest(action=action):
                result = subprocess.run([sys.executable, str(HERE / "preflight.py"), action],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid choice", result.stderr)
        result = subprocess.run([sys.executable, str(HERE / "preflight.py"), "preflight",
                                 "--ack", "trusted"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

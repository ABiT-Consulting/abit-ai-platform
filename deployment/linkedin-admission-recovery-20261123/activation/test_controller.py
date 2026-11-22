"""New activation logic only: real temp files/SQLite, deterministic service emulator.

No real service, private production data, privilege, process or network calls.
Production CLI remains UNBOUND and has no fixture/force/ack mode.
"""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("activation_controller", HERE / "controller.py")
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)
hspec = importlib.util.spec_from_file_location("service_sqlite_fixture", HERE / "service_sqlite.py")
helper = importlib.util.module_from_spec(hspec)
hspec.loader.exec_module(helper)
PRODUCT = HERE.parent.parent / "linkedin-admission-20261122"


class EmulatedService:
    def __init__(self, layout, binding):
        self.l, self.b = layout, binding
        self.commands = []
        self.identities = {r["pid"]: copy.deepcopy(r)
                           for r in [binding["core_before"], *binding["preserved_processes"].values()]}
        self.current = binding["core_before"]["pid"]
        self.state, self.substate, self.effective = "active", "running", "base"
        self.pending, self.descendant, self.idle, self.bad_asset = "", False, True, False
        self.fail_before, self.fail_after = None, None
        self.stop_stays_active, self.start_fails = False, False
        self.tick = 1000
        self.before_command = None

    def query(self, name, args=()):
        return helper.query(self.l.base / "data", name, args)

    def snapshot(self, name, destination, receipt_path, child_path, timeout):
        value, receipt = helper.snapshot_bytes(self.l.base / "data", name, seconds=timeout)
        c.save_once(destination, value)
        c.save_json(receipt_path, receipt)
        c.save_json(child_path, {"pid": os.getpid(), "synthetic_service": True})
        return receipt

    def show(self):
        settings = c.TEMP_EFFECTIVE if self.effective == "temp" else c.ORIGINAL_EFFECTIVE
        drops = self.b["unit"]["DropInPaths"].split()
        if self.effective == "temp":
            drops.append(str(self.l.override))
        return {**self.b["unit"], **settings, "MainPID": str(self.current or 0),
                "ActiveState": self.state, "SubState": self.substate, "Job": self.pending,
                "DropInPaths": " ".join(drops), "ControlGroup": "/system.slice/abit-ai.service"}

    def process(self, pid):
        if pid not in self.identities:
            raise FileNotFoundError(pid)
        return copy.deepcopy(self.identities[pid])

    def absent(self, identity):
        return identity is None or identity["pid"] not in self.identities or (
            self.identities[identity["pid"]]["start_ticks"] != identity["start_ticks"])

    def cgroup_empty(self):
        return self.current == 0 and not self.descendant

    def ticks(self):
        self.tick += 10
        return self.tick

    def command(self, command):
        if self.before_command:
            self.before_command(command)
        self.commands.append(command)
        if self.fail_before == command:
            self.fail_before = None
            raise TimeoutError("synthetic uncertain invocation")
        if command == "reload":
            self.effective = "temp" if self.l.override.exists() else "base"
        elif command == "stop":
            if not self.stop_stays_active:
                self.identities.pop(self.current, None)
                self.current, self.state, self.substate = 0, "inactive", "dead"
        elif command == "start":
            if self.start_fails:
                self.current, self.state, self.substate = 0, "failed", "failed"
            else:
                self.current = 100 + len(self.commands)
                self.tick += 10
                identity = {**self.b["core_before"], "pid": self.current, "start_ticks": self.tick}
                self.identities[self.current] = identity
                self.state, self.substate = "active", "running"
        else:
            raise AssertionError("unapproved service command")
        if self.fail_after == command:
            self.fail_after = None
            raise TimeoutError("synthetic result lost after actual state transition")
        return {"returncode": 0, "stdout_sha256": c.sha(b""), "stderr_sha256": c.sha(b"")}

    def health(self, frontend_sha):
        if self.bad_asset or c.digest_file(self.l.live / "frontend/agent-activity.js") != frontend_sha:
            raise c.Hold("served asset mismatch")
        if self.state != "active":
            raise c.Hold("unhealthy service")

    def relay_idle(self):
        c.need(self.idle, "relay not idle")


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / "app"
        self.live = self.base / "release/runtime/open_webui"
        self.live.mkdir(parents=True)
        self.current = self.base / "current"
        self.current.symlink_to(self.live, target_is_directory=True)
        self.stage = self.root / "private-stage"
        self.owner_file = self.root / "operator-session.json"
        self.override = self.root / "systemd/abit-ai.service.d/owned.conf"
        self.override.parent.mkdir(parents=True)
        self.layout = c.Layout(self.base, self.live, self.current, self.stage, PRODUCT,
                               self.override, self.owner_file)
        manifest = json.loads((PRODUCT / "manifest.json").read_text())
        self.rows = {r["path"]: r for r in manifest["files"]}
        self.preimage_bytes = {}
        files = {}
        for rel, row in self.rows.items():
            target = self.live / rel
            target.parent.mkdir(exist_ok=True)
            old = row["expected_before_sha256"]
            if old is not None:
                data = (PRODUCT / "baseline/open_webui" / rel).read_bytes()
                assert c.sha(data) == old
                target.write_bytes(data)
                target.chmod(0o644)
                self.preimage_bytes[rel] = data
            st = target.parent.stat()
            files[rel] = {"before_sha256": old, "uid": os.getuid(), "gid": os.getgid(), "mode": 0o644,
                          "parent_device": st.st_dev, "parent_inode": st.st_ino,
                          "parent_uid": st.st_uid, "parent_gid": st.st_gid,
                          "parent_mode": st.st_mode & 0o7777}
        owner = {"owner": "isolated-owner", "state": "isolated-hold", "phase": "reviewed",
                 "lease_expires_at": (c.now() + dt.timedelta(hours=1)).isoformat()}
        self.owner_file.write_bytes(c.encoded(owner))
        evidence = []
        for role in sorted(c.EVIDENCE_ROLES):
            path = self.root / (role + ".json")
            path.write_bytes(c.encoded({"synthetic_fixture_only": role}))
            path.chmod(0o600)
            evidence.append({"role": role, "path": str(path), "sha256": c.digest_file(path)})
        configurations = []
        for name in ("runtime-environment.json", "final-snapshot.json", "webui-secret-key",
                     "run-production.py", "abit-ai.service", "existing-dropin.conf"):
            path = self.root / name
            path.write_bytes(("synthetic " + name).encode())
            path.chmod(0o600)
            configurations.append({"path": str(path), **c.metadata(path), "sha256": c.digest_file(path)})
        self.core = {"pid": 10, "start_ticks": 100, "uid": os.getuid(), "cmdline_sha256": c.sha(b"launcher"),
                     "cgroup": "0::/system.slice/abit-ai.service/controller"}
        preserved = {name: {"pid": pid, "start_ticks": pid * 10, "uid": os.getuid(),
                           "cmdline_sha256": c.sha(name.encode()), "cgroup": "isolated/" + name}
                     for name, pid in (("qa", 20), ("relay", 21), ("relay_watchdog", 22), ("browser", 23))}
        self.gate = {"state": "draining", "epoch": 7, "command_id": "isolated-close", "actor": "isolated-owner"}
        payload = {"action": "close", "expected_epoch": 6, "actor": "isolated-owner"}
        data_dir = self.base / "data"
        (data_dir / "maintenance-admission").mkdir(parents=True)
        for name in c.DATABASES:
            path = data_dir / name
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE business(id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO business VALUES(1,'preserve this original work')")
                if name == "maintenance-admission/gate.sqlite":
                    db.execute("CREATE TABLE gate(id INTEGER PRIMARY KEY,state,epoch,command_id,actor)")
                    db.execute("INSERT INTO gate VALUES(1,?,?,?,?)", tuple(self.gate.values()))
                    db.execute("CREATE TABLE audit(event_id PRIMARY KEY,kind,payload)")
                    db.execute("INSERT INTO audit VALUES(?,?,?)", ("isolated-close", "operator_control", json.dumps(payload)))
                    db.execute("CREATE TABLE lease(id PRIMARY KEY,kind,reference,parent_id,epoch,pid,process_start,state)")
                    db.execute("INSERT INTO lease VALUES('old','automation','marketing',NULL,1,99999,'1','unresolved')")
                elif name == "aibit-chatgpt-relay.sqlite":
                    db.execute("CREATE TABLE jobs(job_id PRIMARY KEY,status,payload)")
                    db.execute("INSERT INTO jobs VALUES('retained-relay','queued','original')")
                    db.execute("INSERT INTO jobs VALUES('unknown-relay','unknown','original')")
                elif name == "abitdot-primary.sqlite":
                    db.execute("CREATE TABLE dot_primary_runs(id PRIMARY KEY,status,payload)")
                    db.execute("INSERT INTO dot_primary_runs VALUES('retained-dot','prepared','original')")
                    db.execute("INSERT INTO dot_primary_runs VALUES('unknown-dot','unknown','original')")
                elif name == "agentic-governance.sqlite":
                    db.execute("CREATE TABLE governance_invocation(id PRIMARY KEY,state)")
                elif name == "codex-workspace-runs.sqlite":
                    db.execute("CREATE TABLE runs(id PRIMARY KEY,state)")
        override_st = self.override.parent.stat()
        self.binding = {
            "product_commit": c.COMMIT, "product_manifest_sha256": c.MANIFEST_SHA,
            "operator_session_sha256": c.digest_file(self.owner_file), "operator_owner": "isolated-owner",
            "operator_state": "isolated-hold", "operator_phase": "reviewed",
            "execution_expires_at": (c.now() + dt.timedelta(hours=1)).isoformat(),
            "private_gid": os.getgid(), "files": files, "configuration": configurations,
            "evidence": evidence, "core_before": self.core, "preserved_processes": preserved,
            "closed_gate": self.gate, "gate_control_payload_sha256": c.sha(c.canonical(payload)),
            "override_parent": {k: v for k, v in zip(("device", "inode", "uid", "gid", "mode"),
                (override_st.st_dev, override_st.st_ino, override_st.st_uid, override_st.st_gid,
                 override_st.st_mode & 0o7777))},
            "unit": {"User": "mcpdev", "Group": "mcpdev", "WorkingDirectory": str(self.base),
                     "FragmentPath": str(self.root / "abit-ai.service"),
                     "DropInPaths": str(self.root / "existing-dropin.conf")},
            "work": {},
        }
        self.service = EmulatedService(self.layout, self.binding)
        self.binding["work"] = {
            "lease_cohort_sha256": helper.query(data_dir, "lease_cohort")["sha256"],
            "relay": helper.query(data_dir, "relay_cohort"),
            "dot": helper.query(data_dir, "dot_cohort"),
        }
        self.controller = c.Controller(self.layout, self.binding, c.encoded(self.binding),
                                       self.service, os.getuid(), 0)

    def close(self):
        self.temp.cleanup()

    def sql(self, name, sql, args=()):
        with sqlite3.connect(self.base / "data" / name) as db:
            db.execute(sql, args)

    def business_snapshot(self):
        result = {}
        for name in c.DATABASES:
            with sqlite3.connect(self.base / "data" / name) as db:
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                result[name] = {table: list(db.execute('SELECT * FROM "' + table + '" ORDER BY rowid'))
                                for table in tables}
        return result


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.x = self.f.controller
        self.before = self.f.business_snapshot()

    def backup(self):
        self.x.backup()

    def test_backup_is_consistent_private_and_restorable(self):
        self.backup()
        receipt = self.x.prepared()
        self.assertEqual(len(receipt["databases"]), 11)
        self.assertFalse(receipt["cross_database_atomic_snapshot_claimed"])
        self.assertEqual(self.f.stage.stat().st_mode & 0o777, 0o700)
        for row in receipt["databases"].values():
            self.assertEqual(row["integrity"], "ok")
            self.assertTrue(row["isolated_restore_schema_and_counts_match"])
        self.assertEqual(self.before, self.f.business_snapshot())
        self.assertEqual(self.f.service.commands, [])

    def test_full_activation_preserves_unknown_work_and_seven_file_metadata(self):
        self.backup()
        result = self.x.apply()
        self.assertEqual(result["status"], "DEPLOYED_ADMISSION_HELD")
        self.assertTrue(result["authenticated_ui_verification_required"])
        self.assertFalse(result["original_work_reconciled"])
        self.assertFalse(result["marketing_agent_executed"])
        self.assertEqual(self.f.service.commands, ["reload", "stop", "start", "reload"])
        self.assertEqual(self.before, self.f.business_snapshot())
        self.assertFalse(self.f.override.exists())
        for rel, row in self.f.rows.items():
            self.assertEqual(c.digest_file(self.f.live / rel), row["after_sha256"])
            self.assertEqual((self.f.live / rel).stat().st_mode & 0o777, 0o644)

    def test_explicit_code_rollback_restores_only_original_source(self):
        self.backup()
        self.x.apply()
        result = self.x.rollback()
        self.assertEqual(result["status"], "ROLLED_BACK_ADMISSION_HELD")
        self.assertFalse(result["database_restore_performed"])
        for rel, row in self.f.rows.items():
            path = self.f.live / rel
            if row["expected_before_sha256"] is None:
                self.assertFalse(path.exists())
            else:
                self.assertEqual(path.read_bytes(), self.f.preimage_bytes[rel])
        self.assertEqual(self.before, self.f.business_snapshot())
        self.assertEqual(self.f.service.commands.count("stop"), 2)
        self.assertEqual(self.f.service.commands.count("start"), 2)

    def test_repeated_apply_and_rollback_are_not_new_cycles(self):
        self.backup()
        self.x.apply()
        commands = list(self.f.service.commands)
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.x.finalize()
        self.assertEqual(commands, self.f.service.commands)
        self.x.rollback()
        commands = list(self.f.service.commands)
        with self.assertRaises(c.Hold):
            self.x.rollback()
        self.x.finalize()
        self.assertEqual(commands, self.f.service.commands)

    def test_existing_backup_stage_is_never_reused(self):
        self.backup()
        with self.assertRaises(c.Hold):
            self.x.backup()
        self.assertEqual(self.f.service.commands, [])

    def test_owner_drift_between_backup_and_stop_blocks_without_commands(self):
        self.backup()
        self.f.owner_file.write_text("{}")
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_closed_gate_epoch_drift_blocks_without_commands(self):
        self.backup()
        self.f.sql("maintenance-admission/gate.sqlite", "UPDATE gate SET epoch=8")
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_current_core_owned_lease_blocks_even_with_historical_cohort(self):
        self.f.sql("maintenance-admission/gate.sqlite",
                   "INSERT INTO lease VALUES('new','automation','other',NULL,7,10,'100','active')")
        with self.assertRaises(c.Hold):
            self.backup()
        self.assertFalse(self.f.stage.exists())
        self.assertEqual(self.f.service.commands, [])

    def test_provider_cohort_drift_between_backup_and_stop_blocks(self):
        self.backup()
        self.f.sql("aibit-chatgpt-relay.sqlite", "UPDATE jobs SET status='running' WHERE job_id='retained-relay'")
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_core_worker_drift_after_reload_blocks_stop(self):
        self.backup()
        original = self.f.service.command
        def command(name):
            result = original(name)
            if name == "reload":
                self.f.sql("maintenance-admission/gate.sqlite",
                    "INSERT INTO lease VALUES('new','automation','other',NULL,7,10,'100','active')")
            return result
        self.f.service.command = command
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, ["reload"])

    def test_same_pid_different_start_is_not_stopped(self):
        self.backup()
        self.f.service.identities[10]["start_ticks"] += 1
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_distinct_target_uid_is_checked_without_normalization(self):
        self.backup()
        self.x.b["files"]["frontend/agent-activity.js"]["uid"] += 1
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_parent_inode_drift_blocks_before_stop(self):
        self.backup()
        self.x.b["files"]["utils/automations.py"]["parent_inode"] += 1
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_unknown_source_and_unjournaled_candidate_block(self):
        self.backup()
        target = self.f.live / "frontend/agent-activity.js"
        target.write_bytes((PRODUCT / "candidate/open_webui/frontend/agent-activity.js").read_bytes())
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_tampered_backup_prevents_restart(self):
        self.backup()
        path = self.f.stage / "database/webui.db"
        path.write_bytes(b"damaged")
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_failed_backup_retains_evidence_and_blocks_apply(self):
        self.f.sql("webui.db", "CREATE TABLE large_test(x)")
        real = c.verify_private_snapshot
        calls = []
        def fail(*args):
            calls.append(args[0].name)
            if len(calls) == 2:
                raise c.Hold("injected backup failure")
            return real(*args)
        with patch.object(c, "verify_private_snapshot", fail):
            with self.assertRaises(c.Hold):
                self.backup()
        self.assertIsNotNone(self.x.load("backup-failed"))
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_uncertain_stop_is_observed_without_second_stop(self):
        self.backup()
        self.f.service.fail_after = "stop"
        with self.assertRaises(TimeoutError):
            self.x.apply()
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.x.finalize()
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.assertEqual(self.f.service.commands.count("start"), 1)

    def test_uncertain_stop_still_active_blocks_rollback_without_reissue(self):
        self.backup()
        self.f.service.stop_stays_active = True
        with self.assertRaises(c.Hold):
            self.x.apply()
        with self.assertRaises(c.Hold):
            self.x.rollback()
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.assertEqual(self.f.service.commands.count("start"), 0)

    def test_uncertain_stopped_apply_can_rollback_without_repeating_stop(self):
        self.backup()
        self.f.service.fail_after = "stop"
        with self.assertRaises(TimeoutError):
            self.x.apply()
        self.x.rollback()
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.assertEqual(self.f.service.commands.count("start"), 1)
        self.assertEqual(self.before, self.f.business_snapshot())

    def test_cgroup_descendant_prevents_source_write(self):
        self.backup()
        self.f.service.descendant = True
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands.count("start"), 0)
        for rel, data in self.f.preimage_bytes.items():
            self.assertEqual((self.f.live / rel).read_bytes(), data)

    def test_uncertain_start_is_observed_without_second_start(self):
        self.backup()
        self.f.service.fail_after = "start"
        with self.assertRaises(TimeoutError):
            self.x.apply()
        self.x.finalize()
        self.assertEqual(self.f.service.commands.count("start"), 1)
        self.assertEqual(self.f.service.commands.count("stop"), 1)

    def test_unknown_start_without_actual_transition_is_not_retried(self):
        self.backup()
        self.f.service.fail_before = "start"
        with self.assertRaises(TimeoutError):
            self.x.apply()
        with self.assertRaises(c.Hold):
            self.x.finalize()
        self.assertEqual(self.f.service.commands.count("start"), 1)
        self.assertEqual(self.f.service.commands.count("stop"), 1)

    def test_pending_original_start_refuses_rollback(self):
        self.backup()
        self.f.service.fail_before = "start"
        with self.assertRaises(TimeoutError):
            self.x.apply()
        self.f.service.pending = "42"
        with self.assertRaises(c.Hold):
            self.x.rollback()
        self.assertEqual(self.f.service.commands.count("start"), 1)

    def test_failed_settled_start_can_rollback_source_without_replay(self):
        self.backup()
        self.f.service.start_fails = True
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.f.service.start_fails = False
        self.x.rollback()
        self.assertEqual(self.f.service.commands.count("start"), 2)
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.assertEqual(self.before, self.f.business_snapshot())

    def test_crash_after_replace_reconciles_exact_bytes_without_second_stop(self):
        self.backup()
        real_replace = os.replace
        hit = []
        def replace(source, destination, *args, **kwargs):
            result = real_replace(source, destination, *args, **kwargs)
            if not hit:
                hit.append(destination)
                raise RuntimeError("synthetic crash after atomic replace")
            return result
        with patch.object(c.os, "replace", replace):
            with self.assertRaises(RuntimeError):
                self.x.apply()
        self.x.finalize()
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.assertEqual(self.f.service.commands.count("start"), 1)
        self.x.source("after")

    def test_crash_after_new_module_link_is_reconciled_from_owned_inode(self):
        self.backup()
        real_unlink = os.unlink
        hit = []
        def unlink(path, *args, **kwargs):
            if path == ".abit-linkedin-apply-automation_admission.py" and not hit:
                hit.append(path)
                raise RuntimeError("synthetic crash after no-replace link")
            return real_unlink(path, *args, **kwargs)
        with patch.object(c.os, "unlink", unlink):
            with self.assertRaises(RuntimeError):
                self.x.apply()
        self.x.finalize()
        self.assertEqual((self.f.live / "models/automation_admission.py").stat().st_nlink, 1)
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.x.source("after")

    def test_partial_source_can_rollback_without_starting_candidate(self):
        self.backup()
        real_replace = os.replace
        hit = []
        def replace(source, destination, *args, **kwargs):
            result = real_replace(source, destination, *args, **kwargs)
            if not hit:
                hit.append(destination)
                raise RuntimeError("synthetic partial install")
            return result
        with patch.object(c.os, "replace", replace):
            with self.assertRaises(RuntimeError):
                self.x.apply()
        self.x.rollback()
        self.x.source("before")
        self.assertEqual(self.f.service.commands.count("stop"), 1)
        self.assertEqual(self.f.service.commands.count("start"), 1)

    def test_rollback_never_overwrites_drifted_candidate_or_deletes_new_unknown(self):
        self.backup()
        self.x.apply()
        self.f.live.joinpath("models/automation_admission.py").write_bytes(b"unrelated replacement")
        before = list(self.f.service.commands)
        with self.assertRaises(c.Hold):
            self.x.rollback()
        self.assertEqual(before, self.f.service.commands)
        self.assertEqual(self.f.live.joinpath("models/automation_admission.py").read_bytes(), b"unrelated replacement")

    def test_served_old_asset_prevents_success_and_does_not_restart_again(self):
        self.backup()
        self.f.service.bad_asset = True
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertIsNone(self.x.load("apply-complete"))
        with self.assertRaises(c.Hold):
            self.x.finalize()
        self.assertEqual(self.f.service.commands.count("start"), 1)

    def test_readonly_observe_does_not_issue_lifecycle_commands(self):
        self.backup()
        result = self.x.observe()
        self.assertTrue(result["no_lifecycle_command_issued"])
        self.assertEqual(self.f.service.commands, [])

    def test_real_same_uid_child_preserves_wal_visibility_and_auxiliary_ownership(self):
        path = self.f.base / "data/webui.db"
        # Keep a real WAL writer open so a noncheckpointed row must be read from WAL.
        writer = sqlite3.connect(path)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO business VALUES(2,'uncheckpointed WAL row')")
        writer.commit()
        code = (
            "import importlib.util,json,os,sys;from pathlib import Path;"
            "s=importlib.util.spec_from_file_location('h',sys.argv[1]);"
            "h=importlib.util.module_from_spec(s);s.loader.exec_module(h);"
            "v,r=h.snapshot_bytes(Path(sys.argv[2]),'webui.db');"
            "Path(sys.argv[3]).write_bytes(v);"
            "print(json.dumps({'uid':os.geteuid(),'gid':os.getegid(),'receipt':r}))"
        )
        output = self.f.root / "isolated-child-snapshot.sqlite"
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                                 str(HERE / "service_sqlite.py"), str(self.f.base / "data"), str(output)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["uid"], os.geteuid())
        self.assertEqual(observed["gid"], os.getegid())
        with sqlite3.connect(output) as snapshot:
            self.assertEqual(snapshot.execute("SELECT value FROM business WHERE id=2").fetchone()[0],
                             "uncheckpointed WAL row")
        for suffix in ("", "-wal", "-shm"):
            self.assertEqual(Path(str(path) + suffix).stat().st_uid, os.geteuid())
            self.assertEqual(Path(str(path) + suffix).stat().st_gid, os.getegid())

    def test_wal_header_without_auxiliaries_creates_them_under_actual_child_uid(self):
        path = self.f.base / "data/webui.db"
        with sqlite3.connect(path) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO business VALUES(2,'checkpointed')")
        writer.close()
        # Normal final-writer close leaves a WAL-mode header with no auxiliaries.
        self.assertFalse(Path(str(path) + "-wal").exists())
        self.assertFalse(Path(str(path) + "-shm").exists())
        code = (
            "import importlib.util,json,os,sys;from pathlib import Path;"
            "s=importlib.util.spec_from_file_location('h',sys.argv[1]);"
            "h=importlib.util.module_from_spec(s);s.loader.exec_module(h);"
            "h.snapshot_bytes(Path(sys.argv[2]),'webui.db');"
            "p=Path(sys.argv[2])/'webui.db';"
            "print(json.dumps({'uid':os.geteuid(),'gid':os.getegid(),"
            "'aux':[{'path':str(p)+x,'uid':Path(str(p)+x).stat().st_uid,"
            "'gid':Path(str(p)+x).stat().st_gid} for x in ['-wal','-shm']]}))"
        )
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                                 str(HERE / "service_sqlite.py"), str(self.f.base / "data")],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["uid"], os.geteuid())
        for row in observed["aux"]:
            self.assertEqual(row["uid"], observed["uid"])
            self.assertEqual(row["gid"], observed["gid"])

    def test_snapshot_growth_bound_and_memory_reserve_fail_closed(self):
        with patch.object(helper, "MAX_SNAPSHOT_BYTES", 1):
            with self.assertRaises(RuntimeError):
                helper.snapshot_bytes(self.f.base / "data", "webui.db")
        self.assertFalse(self.f.stage.exists())

    def test_missing_real_backup_and_receipt_entry_cannot_hide_behind_name_list(self):
        self.backup()
        path = self.x.artifact("backup-complete")
        receipt = json.loads(path.read_text())
        receipt["databases"].pop("webui.db")
        path.write_bytes(c.encoded(receipt))
        (self.f.stage / "database/webui.db").unlink()
        (self.f.stage / "restore-check/webui.db").unlink()
        self.assertEqual(len(receipt["database_names"]), 11)
        with self.assertRaises(c.Hold):
            self.x.prepared()
        with self.assertRaises(c.Hold):
            self.x.apply()
        self.assertEqual(self.f.service.commands, [])

    def test_preopened_output_fd_streams_exact_bytes_with_real_space_checks(self):
        path = self.f.root / "bounded-output"
        data = b"a" * (2 * 1024 * 1024 + 37)
        with path.open("xb") as output:
            helper.stream_snapshot(output.fileno(), data, helper.time.monotonic() + 5)
        self.assertEqual(path.read_bytes(), data)

    def test_output_reserve_drop_retains_only_completed_private_chunk(self):
        path = self.f.root / "partial-output"
        data = b"a" * (2 * 1024 * 1024 + 37)
        calls = []
        original = helper.os.fstatvfs
        def space(fd):
            actual = original(fd)
            calls.append(fd)
            if len(calls) == 1:
                return actual
            fields = list(actual)
            fields[4] = 0
            return os.statvfs_result(fields)
        with path.open("xb") as output, patch.object(helper.os, "fstatvfs", space):
            with self.assertRaises(RuntimeError):
                helper.stream_snapshot(output.fileno(), data, helper.time.monotonic() + 5)
        self.assertEqual(path.read_bytes(), data[:1024 * 1024])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.before, self.f.business_snapshot())



class ProductionBoundaryTests(unittest.TestCase):

    def test_service_helper_hash_and_existing_identity_route_are_exact(self):
        service = c.Service()
        self.assertEqual(c.sha(service.helper_source()), c.SQLITE_HELPER_SHA)
        self.assertEqual(service.helper_argv("query", "gate"),
                         ["/usr/bin/sudo", "-n", "-u", "mcpdev", "--", "/usr/bin/python3",
                          "-I", "-S", "-B", "-", "query", "gate"])

    def test_unbound_cli_refuses_every_action_before_private_access(self):
        self.assertEqual(c.EXECUTION_BINDING_SHA, "UNBOUND")
        for action in ("backup", "apply", "observe", "finalize", "rollback"):
            result = subprocess.run([sys.executable, str(HERE / "controller.py"), action],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            value = json.loads(result.stdout)
            self.assertFalse(value["production_binding_set"])
            self.assertTrue(value["no_gate_reopen"])
            self.assertEqual(value["error_type"], "Hold")

    def test_production_helper_rejects_wrong_uid_before_live_access(self):
        if os.geteuid() == 999 and os.getegid() == 988:
            self.skipTest("fixture host unexpectedly has production service numeric identity")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(HERE / "service_sqlite.py"),
                                 "query", "gate"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "SERVICE_READ_FAILED")

    def test_production_has_no_force_ack_test_or_gate_action(self):
        for args in (["open"], ["close"], ["apply", "--force"], ["apply", "--ack", "yes"],
                     ["apply", "--test"], ["execute-agent"]):
            result = subprocess.run([sys.executable, str(HERE / "controller.py"), *args],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

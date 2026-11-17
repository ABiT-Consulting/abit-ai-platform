"""Offline controller tests: temporary files/SQLite only; no app or old-controller imports."""
from pathlib import Path
from contextlib import ExitStack, redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent

def module(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

C = module("offline_cancel_controller", "activate_once.py")
S = module("offline_cancel_support", "activation_support.py")

def original():
    provider = {"row_count": 1, "identity_matches": True, "status": "delegated"}
    for key, pair in {
        "envelope_json": (9209, "ec3b73c9292f7276d2054d20b801e389617803c5c8139e87582df27d9b7de231"),
        "response_text": (3250, "7836261f9501f33d64bef4b00c0de14a3119764be023eff4deb62ec24251068e"),
        "receipt": (64, "5a1edafc4cd1daee561a4e6517c37532376a3567b245bcb0573c466cfcf9fca7"),
        "scope_receipt_json": (1008, "7ff2bf770a4fe89b4754aad40fe7b84ca73327edc23f990dc8fb6795dcf2edc9"),
    }.items():
        provider[key] = {"is_null": False, "bytes": pair[0], "sha256": pair[1]}
    governance = {
        "invocation": {"row_count": 1, "identity_matches": True, "state": "cancel_requested",
                       "dispatch_attempts": 1, "lease_fence": 2, "packet_version": 1,
                       "packet_hash": "3e8a02f987cb894296bd801f68b6da47d007774c265f471c7e73bbbcf4df550a"},
        "task_lease": {"row_count": 1, "owner_matches_original": True, "owner_present": True, "fence": 2},
        "task_control_states": [{"count": 1, "state": "stopped"}],
        "results": {"local": {"row_count": 0}, "final": {"row_count": 0}},
        "gateway_session": {"row_count": 1, "state": "admitted"},
        "gateway_call_states": [{"count": 4, "state": "completed"}],
    }
    automation = {"row_count": 1, "identity_matches": True, "status": "running",
                  "chat_is_null": True, "lease_is_null": True, "error": {"is_null": True}}
    return {"provider": provider, "governance": governance, "automation": automation}

def service_fixture():
    binding = {"services": {}}
    services, identities = {}, {}
    for index, unit in enumerate(("main-fixture.service", "qa-fixture.service"), 10):
        identity = {"pid": index, "uid": 999, "start": "10",
                    "argv_sha256": "argv-" + unit, "cgroup_sha256": "cgroup-" + unit}
        policy = {"Restart": "on-failure", "KillMode": "mixed", "SendSIGKILL": "no"}
        binding["services"][unit] = {"identity": identity, "policy": policy}
        identities[index + 100] = {**identity, "pid": index + 100, "start": "20"}
        services[unit] = {**policy, "MainPID": str(index + 100), "ActiveState": "active",
                          "SubState": "running", "NRestarts": "0"}
    support = types.SimpleNamespace(MAIN="main-fixture.service", QA="qa-fixture.service",
                                    identity=lambda pid: identities[pid])
    return support, binding, services, identities

class OfflineActivation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="abit-cancel-controller-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_existing_private_header_only_and_bounded_pipe_input(self):
        fixture = {"Authorization": "Bearer offline-fixture-token-never-authority"}
        for kind in ("private-file", "pipe"):
            with self.subTest(kind=kind):
                if kind == "private-file":
                    p = self.root / "header.json"
                    p.write_text(json.dumps(fixture))
                    p.chmod(0o600)
                    fd = os.open(p, os.O_RDONLY)
                else:
                    fd, writer = os.pipe()
                    os.write(writer, json.dumps(fixture).encode())
                    os.close(writer)
                try:
                    self.assertEqual(C.supplied_admin_headers(fd), fixture)
                finally:
                    os.close(fd)
        p = self.root / "header.json"
        p.chmod(0o644)
        fd = os.open(p, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(RuntimeError, "PRIVATE_AUTH_FILE_REQUIRED"):
                C.supplied_admin_headers(fd)
        finally:
            os.close(fd)
        fd, writer = os.pipe()
        try:
            with patch.object(C.select, "select", return_value=([], [], [])):
                with self.assertRaisesRegex(RuntimeError, "COMPLETE_AUTH_INPUT_REQUIRED"):
                    C.supplied_admin_headers(fd)
        finally:
            os.close(fd)
            os.close(writer)
        with self.assertRaisesRegex(RuntimeError, "EXISTING_ADMIN_AUTHORIZATION_REQUIRED"):
            C.supplied_admin_headers(None)

    def test_failed_admin_auth_precedes_every_relay_or_database_probe(self):
        support = types.SimpleNamespace(http=Mock(side_effect=RuntimeError("AUTH_FIXTURE_DENIED")))
        with patch.object(C, "relay_readiness") as relay, patch.object(C, "state_snapshot") as state:
            with self.assertRaisesRegex(RuntimeError, "AUTH_FIXTURE_DENIED"):
                C.quiescent(support, {}, {"Authorization": "offline-fixture"}, "open", 11)
            relay.assert_not_called()
            state.assert_not_called()
        support.http.assert_called_once_with("/api/tasks", {"Authorization": "offline-fixture"})

    def test_relay_ready_is_required_and_no_send_endpoint_exists(self):
        fixture = b"offline-relay-source"
        support = types.SimpleNamespace(http=Mock(return_value={
            "ok": True, "ready": False, "authenticated": True,
            "target_ready": True, "active_relay_turns": 0}))
        with patch.object(C, "read_bound", return_value=fixture), patch.object(C, "RELAY_SHA", C.sha(fixture)):
            with self.assertRaisesRegex(RuntimeError, "RELAY_NOT_READY_OR_TURN_ACTIVE"):
                C.relay_readiness(support)
            support.http.return_value["ready"] = True
            self.assertTrue(C.relay_readiness(support)["ready"])
        self.assertEqual(support.http.call_args_list[0].args, ("/health",))
        self.assertEqual(support.http.call_args_list[0].kwargs, {"port": 8810})

    def test_cancel_state_owner_fence_provider_and_results_are_bound(self):
        self.assertTrue(C.validate_original(original()))
        mutations = [
            ("governance", "invocation", "state", "uncertain"),
            ("governance", "invocation", "lease_fence", 3),
            ("governance", "invocation", "dispatch_attempts", 2),
            ("governance", "task_lease", "owner_present", False),
            ("governance", "task_lease", "owner_matches_original", False),
            ("governance", "task_lease", "fence", 3),
            ("provider", "response_text", "sha256", "changed"),
            ("automation", "error", "is_null", False),
        ]
        for first, second, field, value in mutations:
            changed = original()
            changed[first][second][field] = value
            with self.subTest(mutation=(first, second, field)):
                with self.assertRaises(RuntimeError):
                    C.validate_original(changed)
        changed = original()
        changed["governance"]["results"]["local"]["row_count"] = 1
        with self.assertRaisesRegex(RuntimeError, "ORIGINAL_CONTROL_OR_RESULT_CHANGED"):
            C.validate_original(changed)

    def test_restarted_process_requires_same_authority_command_cgroup_and_policy(self):
        support, binding, services, identities = service_fixture()
        self.assertEqual(len(C.replacement_services(support, binding, services)), 2)
        for field, value in (("uid", 0), ("argv_sha256", "other"), ("cgroup_sha256", "other"),
                             ("pid", 10)):
            changed = copy.deepcopy(identities)
            changed[110][field] = value
            if field == "pid":
                changed[110]["start"] = "10"
            support.identity = lambda pid, changed=changed: changed[pid]
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, "NEW_EXPECTED_SERVICE_PROCESSES_REQUIRED"):
                    C.replacement_services(support, binding, services)
        support.identity = lambda pid: identities[pid]
        changed_services = copy.deepcopy(services)
        changed_services[support.MAIN]["SendSIGKILL"] = "yes"
        with self.assertRaisesRegex(RuntimeError, "RESTARTED_SERVICE_POLICY_CHANGED"):
            C.replacement_services(support, binding, changed_services)

    def test_entire79_file_cohort_checked_and_extra_leaf_change_is_held(self):
        runtime = self.root / "runtime"
        candidates = self.root / "candidates"
        before, replacements = {}, []
        for index, rel in enumerate(sorted(C.REPLACEMENTS)):
            source, target = candidates / rel, runtime / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"VALUE = 1\n")
            source.write_bytes(b"VALUE = 2\n")
            before[str(target)] = C.sha(target.read_bytes())
            replacements.append({"path": rel, "before_sha256": before[str(target)],
                                 "after_sha256": C.sha(source.read_bytes()), "candidate_path": str(source)})
        for index in range(74):
            target = runtime / ("unchanged-" + str(index) + ".txt")
            target.write_text("unchanged fixture " + str(index))
            before[str(target)] = C.sha(target.read_bytes())
        manifest = {"replacements": replacements, "current_runtime_files": before, "additional_source_pins": {}}
        with patch.object(C, "RUNTIME", runtime):
            self.assertEqual(C.check_sources(None, manifest), before)
            (runtime / "unchanged-73.txt").write_text("unexpected")
            with self.assertRaisesRegex(RuntimeError, "REVIEWED_CURRENT_SOURCE_COHORT_CHANGED"):
                C.check_sources(None, manifest)

    def test_consistent_private_backups_before_and_after_stop_preserve_source(self):
        data, out = self.root / "data", self.root / "evidence"
        data.mkdir()
        out.mkdir()
        sources = {}
        for index, name in enumerate(S.DATABASE_NAMES):
            p = data / name
            p.parent.mkdir(exist_ok=True)
            with sqlite3.connect(p) as db:
                db.execute("CREATE TABLE fixture(value INTEGER)")
                db.execute("INSERT INTO fixture VALUES(?)", (index,))
            sources[name] = p.read_bytes()
        with patch.object(S, "DATA", data), patch.object(S, "OUT", out):
            S.backup("prestop")
            S.backup("stopped")
            for phase in ("prestop", "stopped"):
                retained = json.loads((out / ("database-backups-" + phase + ".json")).read_text())
                self.assertEqual(len(retained), 11)
                for index, name in enumerate(S.DATABASE_NAMES):
                    path = out / ("databases-" + phase) / Path(name).name
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                        self.assertEqual(db.execute("SELECT value FROM fixture").fetchall(), [(index,)])
                    self.assertEqual((data / name).read_bytes(), sources[name])
            with self.assertRaises(FileExistsError):
                S.backup("prestop")

    def test_database_inventory_drift_is_held_before_backup_creation(self):
        data, out = self.root / "data", self.root / "evidence"
        data.mkdir()
        out.mkdir()
        (data / "unexpected.sqlite").touch()
        with patch.object(S, "DATA", data), patch.object(S, "OUT", out):
            with self.assertRaisesRegex(RuntimeError, "REVIEWED_DATABASE_SET_CHANGED"):
                S.backup("prestop")
        self.assertEqual(list(out.iterdir()), [])

    def workflow(self, failure=None):
        events = []
        root = self.root
        data, runtime, candidates, out = (root / part for part in ("data", "runtime", "candidates", "out"))
        data.mkdir()
        (data / "erp-dot-dispatch.lock").touch()
        (data / "fixture.sqlite").write_bytes(b"fixture-only")
        basic, binding, active_services, identities = service_fixture()
        binding.update(gate_epoch=11, protected_processes={}, configuration_sha256={"fixture": "digest"},
                       database_identities={"fixture": "identity"})
        manifest = {"replacements": []}
        for rel in sorted(C.REPLACEMENTS):
            target, candidate = runtime / rel, candidates / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"VALUE = 1\n")
            candidate.write_bytes(b"VALUE = 2\n")
            manifest["replacements"].append({"path": rel, "before_sha256": C.sha(target.read_bytes()),
                     "after_sha256": C.sha(candidate.read_bytes()), "candidate_path": str(candidate)})
        current = copy.deepcopy(active_services)
        saved = {}
        class Support:
            MAIN, QA = basic.MAIN, basic.QA
            DATA, DATABASE_NAMES = data, ("fixture.sqlite",)
            identity = staticmethod(basic.identity)
            configuration = staticmethod(lambda: binding["configuration_sha256"])
            metadata = staticmethod(lambda path: {"uid": 999, "gid": 988, "mode": 0o644, "xattrs": {}})
            @staticmethod
            def save(name, value):
                if name in saved:
                    raise AssertionError("fixture refuses duplicate evidence name")
                saved[name] = copy.deepcopy(value)
                events.append("save:" + name)
            @staticmethod
            def backup(phase):
                events.append("backup:" + phase)
                if failure == "prestop-backup" and phase == "prestop":
                    raise RuntimeError("BACKUP_NOT_CONSISTENT")
            @staticmethod
            def lifecycle(unit, action):
                events.append(action + ":" + unit)
                if failure == "main-stop" and unit == basic.MAIN and action == "stop":
                    raise RuntimeError("ORIGINAL_SERVICE_OUTCOME_UNCONFIRMED_NO_REPEAT")
                if action == "stop":
                    current[unit] = {**current[unit], "ActiveState": "inactive", "MainPID": "0"}
                else:
                    current[unit] = copy.deepcopy(active_services[unit])
            @staticmethod
            def service(unit):
                return copy.deepcopy(current[unit])
            @staticmethod
            def http(path, headers=None):
                events.append("http:" + path)
                return {"/api/tasks": {"tasks": []}, "/health": {"status": True}, "/api/config": {"version": "0.11.4"}}[path]
            @staticmethod
            def install(target, raw, backup):
                events.append("install:" + target.name)
                Support.save(backup, target.read_bytes())
                target.write_bytes(raw)
        def quiescent(c, b, headers, state, epoch):
            events.append("quiescent:" + state)
            snapshot = {"gate": {"state": state, "epoch": epoch}, "counts": {"fixture": 0},
                        "original_journals": {"fixture": "same"}}
            return snapshot, original(), {}, {"ready": True, "active_relay_turns": 0}
        def preserved(c, b, prior, o):
            events.append("preserved")
            return {"gate": {"state": "draining", "epoch": 12}, "original_journals": prior["original_journals"]}
        def gate(c, action, expected_epoch):
            events.append("gate:" + action)
            return {"state": "draining" if action == "close" else "open", "epoch": expected_epoch + 1}
        args = types.SimpleNamespace(execute=True, admin_auth_fd=99, controller_sha256="fixture",
                                     manifest_sha256="fixture", binding_sha256="fixture")
        with ExitStack() as stack:
            for name, value in {
                "OUT": out, "RUNTIME": runtime, "load_inputs": lambda a: (Support, manifest, binding),
                "check_sources": lambda *a, **k: {"fixture": "same"},
                "check_processes": lambda *a, **k: None,
                "supplied_admin_headers": lambda fd: {"Authorization": "offline-fixture"},
                "quiescent": quiescent, "preserved": preserved, "gate": gate,
            }.items():
                stack.enter_context(patch.object(C, name, value))
            output = io.StringIO()
            with redirect_stdout(output):
                if failure:
                    with self.assertRaises(RuntimeError):
                        C.run(args)
                else:
                    C.run(args)
        return events, saved, output.getvalue()

    def test_single_cutover_order_backs_up_before_stop_and_opens_only_after_preservation(self):
        events, saved, output = self.workflow()
        self.assertLess(events.index("backup:prestop"), events.index("stop:main-fixture.service"))
        self.assertLess(events.index("stop:qa-fixture.service"), events.index("backup:stopped"))
        self.assertLess(events.index("backup:stopped"), next(i for i, item in enumerate(events) if item.startswith("install:")))
        self.assertLess(events.index("save:startup-preservation.json"), events.index("gate:open"))
        self.assertEqual(sum(item.startswith("install:") for item in events), 5)
        self.assertEqual([item for item in events if item.startswith(("stop:", "start:"))],
                         ["stop:main-fixture.service", "stop:qa-fixture.service",
                          "start:main-fixture.service", "start:qa-fixture.service"])
        self.assertTrue(saved["completed-receipt.json"]["original_journals_preserved_before_open"])
        self.assertFalse(saved["completed-receipt.json"]["cancellation_confirmation_or_ownership_release_called"])
        self.assertEqual(json.loads(output)["original_task_fence"], 2)

    def test_failed_prestop_backup_never_stops_or_installs(self):
        events, saved, output = self.workflow("prestop-backup")
        self.assertFalse(any(item.startswith(("stop:", "start:", "install:")) for item in events))
        self.assertNotIn("gate:open", events)
        self.assertNotIn("completed-receipt.json", saved)

    def test_unknown_original_stop_is_not_retried_and_no_replacement_occurs(self):
        events, saved, output = self.workflow("main-stop")
        self.assertEqual(events.count("stop:main-fixture.service"), 1)
        self.assertFalse(any(item.startswith(("start:", "install:")) for item in events))
        self.assertNotIn("backup:stopped", events)
        self.assertNotIn("gate:open", events)
        self.assertNotIn("completed-receipt.json", saved)

if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Focused admission projection checks using the actual frozen maintenance gate.

All databases, policies, leases and receipts are isolated temporary fixtures.
These tests do not import the application or use live DATA_DIR / credentials.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
HELPER_SOURCE = ROOT / "backend/open_webui/utils/automation_admission.py"
GATE_SOURCE = ROOT / "deployment/linkedin-admission-20261122/baseline/open_webui/utils/agentic_maintenance.py"


def load(source):
    spec = importlib.util.spec_from_file_location("isolated_" + uuid4().hex, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.name == "posix", "Actual maintenance gate requires Linux process and POSIX owner checks")
class AdmissionProjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="automation-admission-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.gate = self.root / "maintenance-admission/gate.sqlite"
        self.m = load(GATE_SOURCE)
        self.helper = load(HELPER_SOURCE)
        with self.m._db(self.gate):
            pass
        self.webui = self.root / "webui.db"
        with sqlite3.connect(self.webui) as db:
            db.executescript("""
                CREATE TABLE automation(id TEXT PRIMARY KEY,user_id TEXT NOT NULL);
                CREATE TABLE automation_run(id TEXT PRIMARY KEY,automation_id TEXT,chat_id TEXT,status TEXT,created_at INTEGER);
                CREATE TABLE chat(id TEXT PRIMARY KEY,user_id TEXT,chat TEXT);
            """)
            db.execute("INSERT INTO automation VALUES(?,?)", ("agent-one", "owner-one"))
            db.execute("INSERT INTO automation VALUES(?,?)", ("agent-two", "owner-two"))

    def status(self, agent="agent-one"):
        return self.helper.admission_status(agent, maintenance=self.m, db_path=self.gate)

    def blocked(self):
        return self.helper.blocked_automation_references(maintenance=self.m, db_path=self.gate)

    def snapshot(self):
        return {
            str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.rglob("*") if p.is_file()
        }

    def original(self, reference="agent-one", state="unresolved", run_id=None, ident="lease-fixture"):
        with self.m._db(self.gate) as db:
            db.execute(
                "INSERT INTO lease VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (ident, "automation", reference, "never-export-this-token-hash", 0,
                 os.getpid(), self.m._start(), 100, state, None),
            )
            payload = {"lease": ident, "kind": "automation", "reference": reference,
                       "epoch": 0, "parent": None, "original_receipt": None}
            if run_id is not None:
                payload["run_id"] = run_id
            self.m._audit(db, "admitted", payload, "fixture-admitted-" + ident)
        return payload

    def stored_run(self, run_id="run-original", automation="agent-one", chat_id="chat-original",
                   chat_owner="owner-one", chat_automation="agent-one", long=False):
        with sqlite3.connect(self.webui) as db:
            db.execute("INSERT INTO automation_run VALUES(?,?,?,?,?)",
                       (run_id, automation, chat_id, "success", 101))
            if chat_id is not None:
                db.execute("INSERT INTO chat VALUES(?,?,?)", (
                    chat_id, chat_owner,
                    json.dumps({"meta": {"automation_id": chat_automation},
                                "messages": ["x" * 25000] if long else []}),
                ))

    def test_open_ready_is_readonly(self):
        before = self.snapshot()
        self.assertEqual(self.status()["code"], "ready")
        self.assertEqual(self.blocked(), set())
        self.assertEqual(before, self.snapshot())

    def test_unresolved_original_is_blocked_without_guessing_latest_run(self):
        self.original()
        self.stored_run(run_id="unrelated-latest-success")
        before = self.snapshot()
        result = self.status()
        self.assertEqual(result["code"], "original_unresolved")
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["lease_id"], "lease-fixture")
        self.assertIsNone(result["original_run_id"])
        self.assertIsNone(result["original_chat_url"])
        self.assertEqual(self.blocked(), {"agent-one"})
        self.assertNotIn("never-export-this-token-hash", json.dumps(result))
        self.assertEqual(before, self.snapshot())

    def test_active_lease_is_waiting_without_claiming_worker_running(self):
        self.original(state="active")
        result = self.status()
        self.assertEqual(result["state"], "waiting")
        self.assertEqual(result["code"], "original_active")
        self.assertIn("not established", result["reason"])

    def test_blocked_references_only_include_automation_active_and_unresolved(self):
        self.original(reference="agent-one", state="active", ident="lease-one")
        self.original(reference="agent-two", state="unresolved", ident="lease-two")
        self.original(reference="finished-agent", state="finished", ident="lease-done")
        lease = self.m.acquire("erp", "TASK-fixture", db_path=self.gate)
        self.assertEqual(self.blocked(), {"agent-one", "agent-two"})
        self.assertIsNotNone(lease)

    def test_closed_gate_precedes_original_but_preserves_lease(self):
        self.original()
        self.m.control("close", expected_epoch=0, command_id="close-fixture", actor="operator-fixture", db_path=self.gate)
        result = self.status()
        self.assertEqual(result["code"], "maintenance_closed")
        self.assertEqual(result["gate_state"], "draining")
        self.assertEqual(result["operator_owner"], "operator-fixture")
        self.assertEqual(result["operator_action"], "close")
        self.assertEqual(result["lease_id"], "lease-fixture")

    def test_restricted_scope_precedes_closed_gate(self):
        self.m.control("close", expected_epoch=0, command_id="close", actor="operator", db_path=self.gate)
        self.m.control("seal", expected_epoch=1, command_id="seal", actor="operator", db_path=self.gate)
        self.m.configure_scope(
            "restrict-project", expected_epoch=2, command_id="scope", actor="operator",
            project_id="PROJ-fixture", db_path=self.gate,
        )
        result = self.status()
        self.assertEqual(result["code"], "scope_restricted")
        self.assertEqual(result["gate_state"], "sealed")
        self.assertIn("PROJ-fixture", result["reason"])

    def test_missing_gate_denies_without_creating_schema(self):
        missing = self.root / "missing/gate.sqlite"
        result = self.helper.admission_status("agent-one", maintenance=self.m, db_path=missing)
        self.assertEqual(result["code"], "admission_unavailable")
        with self.assertRaises(self.m.MaintenanceDenied):
            self.helper.blocked_automation_references(maintenance=self.m, db_path=missing)
        self.assertFalse(missing.parent.exists())

    def test_private_mode_failure_denies_without_mutation(self):
        self.gate.chmod(0o644)
        before = self.snapshot()
        self.assertEqual(self.status()["code"], "admission_unavailable")
        with self.assertRaises(self.m.MaintenanceDenied):
            self.blocked()
        self.assertEqual(before, self.snapshot())

    def test_invalid_gate_state_is_not_ready_or_empty_exclusion(self):
        with sqlite3.connect(self.gate) as db:
            db.execute("UPDATE gate SET state='invalid'")
        self.assertEqual(self.status()["code"], "admission_unavailable")
        with self.assertRaises(self.m.MaintenanceDenied):
            self.blocked()

    def test_missing_runtime_module_is_unavailable(self):
        with mock.patch.object(self.helper.importlib, "import_module", side_effect=ModuleNotFoundError("fixture")):
            self.assertEqual(self.helper.admission_status("agent-one")["code"], "admission_unavailable")
            with self.assertRaises(self.helper.MaintenanceDenied):
                self.helper.blocked_automation_references()

    def test_exact_immutable_binding_returns_owned_original_and_long_chat(self):
        self.original(run_id="run-original")
        self.stored_run(long=True)
        before = self.snapshot()
        result = self.status()
        self.assertEqual(result["original_run_id"], "run-original")
        self.assertEqual(result["original_chat_url"], "/c/chat-original")
        self.assertEqual(before, self.snapshot())

    def test_wrong_automation_binding_never_returns_run_or_chat(self):
        self.original(run_id="run-other")
        self.stored_run(run_id="run-other", automation="agent-two")
        result = self.status()
        self.assertIsNone(result["original_run_id"])
        self.assertIsNone(result["original_chat_url"])
        self.assertEqual(result["code"], "original_unresolved")

    def test_cross_owner_or_cross_agent_chat_has_no_output_link(self):
        self.original(run_id="run-original")
        self.stored_run(chat_owner="owner-two", chat_automation="agent-two")
        result = self.status()
        self.assertEqual(result["original_run_id"], "run-original")
        self.assertIsNone(result["original_chat_url"])

    def test_nonexistent_chat_does_not_fabricate_link(self):
        self.original(run_id="run-original")
        self.stored_run()
        with sqlite3.connect(self.webui) as db:
            db.execute("DELETE FROM chat")
        result = self.status()
        self.assertEqual(result["original_run_id"], "run-original")
        self.assertIsNone(result["original_chat_url"])

    def test_ambiguous_admission_audit_never_chooses_one(self):
        payload = self.original(run_id="run-original")
        self.stored_run()
        with self.m._db(self.gate) as db:
            self.m._audit(db, "admitted", {**payload, "run_id": "run-different"}, "other-admission")
        result = self.status()
        self.assertIsNone(result["original_run_id"])
        self.assertEqual(result["code"], "original_unresolved")

    def test_audit_without_immutable_guards_does_not_establish_binding(self):
        self.original(run_id="run-original")
        self.stored_run()
        with sqlite3.connect(self.gate) as db:
            db.execute("DROP TRIGGER audit_no_update")
        result = self.status()
        self.assertIsNone(result["original_run_id"])

    def test_denial_remains_primary_when_later_gate_is_ready(self):
        result = self.helper.denial_status(
            "agent-one", self.m.MaintenanceDenied("Maintenance admission is closed; original work is preserved"),
            maintenance=self.m, db_path=self.gate,
        )
        self.assertEqual(result["gate_state"], "open")
        self.assertEqual(result["code"], "maintenance_closed")
        self.assertEqual(result["state"], "waiting")
        self.assertTrue(result["denial_observed"])

    def test_original_denial_does_not_become_ready_after_lease_settlement(self):
        result = self.helper.denial_status(
            "agent-one", self.m.MaintenanceDenied("An original admission remains active or unresolved"),
            maintenance=self.m, db_path=self.gate,
        )
        self.assertEqual(result["code"], "original_retained")
        self.assertEqual(result["state"], "blocked")
        self.assertIsNone(result["original_run_id"])

    def test_changed_original_identity_has_safe_specific_code(self):
        result = self.helper.denial_status(
            "agent-one", self.m.MaintenanceDenied("Scheduled original run identity differs"),
            maintenance=self.m, db_path=self.gate,
        )
        self.assertEqual(result["code"], "original_identity_changed")
        self.assertEqual(result["state"], "blocked")

    def test_unknown_denial_never_echoes_secret_or_returns_ready(self):
        result = self.helper.denial_status(
            "agent-one", RuntimeError("token=do-not-export"), maintenance=self.m, db_path=self.gate,
        )
        self.assertEqual(result["code"], "admission_unavailable")
        self.assertEqual(result["state"], "blocked")
        self.assertNotIn("do-not-export", json.dumps(result))

    def test_matching_text_from_nonmaintenance_error_is_not_classified_as_known_denial(self):
        result = self.helper.denial_status(
            "agent-one", RuntimeError("Maintenance admission is closed; original work is preserved"),
            maintenance=self.m, db_path=self.gate,
        )
        self.assertEqual(result["code"], "admission_unavailable")

    def test_invalid_input_does_not_open_another_path(self):
        self.assertEqual(self.status("../other")["code"], "admission_unavailable")

    def test_invalid_stored_reference_denies_scheduler_instead_of_empty_result(self):
        self.original(reference="bad\nreference")
        with self.assertRaises(self.m.MaintenanceDenied):
            self.blocked()


if __name__ == "__main__":
    unittest.main(verbosity=2)

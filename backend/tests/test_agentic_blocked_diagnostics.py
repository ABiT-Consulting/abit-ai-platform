"""Registered command denials remain diagnostics and cannot become QA acceptance."""
from copy import deepcopy
import unittest
from test_agentic_executor_flow import ExecutorFlow

class BlockedDiagnostics(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ExecutorFlow.asyncSetUp
    artifact_root = ExecutorFlow.artifact_root

    def fixture(self):
        packet = self.erp.packet("developer")
        result = {
            "schema_version": 1, "invocation_id": "agov-blocked-diagnostic-fixture",
            "actor": packet["binding"], "project_id": packet["project_id"],
            "task_id": packet["task_id"], "task_revision": packet["task_revision"],
            "work_packet_digest": self.bridge_module.packet_digest(packet),
            "outcome": "blocked", "summary": "Registered command denied before execution.",
            "implementation_invocation_id": None, "implementation_agent_id": None,
            "evidence": [{"id": "DIAGNOSTIC-01", "kind": "checkpoint",
                "uri": "artifact://command-denial.json", "sha256": "a" * 64,
                "requirement_ids": packet["requirement_ids"]}],
            "test_results": [{"test_id": "registered-command", "status": "blocked",
                "observed": "Measured capacity is insufficient.", "evidence_ids": ["DIAGNOSTIC-01"]}],
            "defects": [],
        }
        return packet, result

    def errors(self, packet, result, commands=frozenset({"registered-command"})):
        return self.validator.validate_result(result, packet, registered_blocked_tests=commands)

    async def test_blocked_registered_command_keeps_original_result(self):
        p, r = self.fixture(); before = deepcopy(r)
        self.assertEqual([], self.errors(p, r)); self.assertEqual(before, r)

    async def test_default_and_unregistered_commands_remain_rejected(self):
        p, r = self.fixture()
        self.assertIn("Result includes an unknown acceptance test", self.errors(p, r, frozenset()))
        r["test_results"][0]["test_id"] = "unregistered"
        self.assertIn("Result includes an unknown acceptance test", self.errors(p, r))

    async def test_registered_command_success_or_failure_cannot_count_as_acceptance(self):
        for status in ("pass", "fail", "not_run"):
            p, r = self.fixture(); r["test_results"][0]["status"] = status
            self.assertTrue(self.errors(p, r))

    async def test_readiness_or_verdict_cannot_use_operational_command_ids(self):
        for outcome in ("continue", "ready_for_review", "pass", "fail"):
            p, r = self.fixture(); r["outcome"] = outcome
            self.assertTrue(self.errors(p, r))

    async def test_evidence_and_scope_checks_are_preserved(self):
        p, r = self.fixture(); r["evidence"] = []
        self.assertTrue(self.errors(p, r))
        p, r = self.fixture(); r["project_id"] = "PROJ-OTHER"
        self.assertTrue(self.errors(p, r))

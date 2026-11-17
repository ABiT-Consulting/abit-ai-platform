"""Actual reconciliation-loop checks without importing the application or live data."""
from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path("/srv/abit-dev-linux/abit-ai-platform")
ROUTER = ROOT / "backend/open_webui/routers/agentic_governance.py"
CANDIDATE = Path(__file__).resolve().parent / "candidate/open_webui/routers/agentic_governance.py"


def actual_loop_source(path):
    source = path.read_text()
    nodes = [item for item in ast.parse(source).body
             if isinstance(item, ast.AsyncFunctionDef) and item.name == "reconciliation_loop"]
    if len(nodes) != 1:
        raise AssertionError("One actual reconciliation loop is required")
    return ast.get_source_segment(source, nodes[0])


class StartupMaintenance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "original-fixture.sqlite"
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE governance_agent(erp_project TEXT);
                INSERT INTO governance_agent VALUES('PROJ-0196');
                CREATE TABLE governance_invocation(
                    invocation_id TEXT PRIMARY KEY,automation_run_id TEXT,state TEXT,
                    lease_fence INTEGER,lease_token TEXT,packet_hash TEXT);
                INSERT INTO governance_invocation VALUES(
                    'original-cancel-fixture','original-run-fixture','cancel_requested',
                    2,'private-fixture-token','packet-fixture');
                CREATE TABLE governance_task_lease(
                    erp_project TEXT,erp_task TEXT,invocation_id TEXT,fence INTEGER);
                INSERT INTO governance_task_lease VALUES(
                    'PROJ-0196','TASK-2026-06222','original-cancel-fixture',2);
                CREATE TABLE original_provider(status TEXT,reply_sha TEXT);
                INSERT INTO original_provider VALUES('delegated','unchanged-reply-fixture');
                CREATE TABLE original_automation(status TEXT,chat_id TEXT);
                INSERT INTO original_automation VALUES('running',NULL);
                CREATE TABLE original_admission(state TEXT,reference TEXT);
                INSERT INTO original_admission VALUES('unresolved','original-cancel-fixture');
            """)
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(agentic_execution_tasks=set()))
        self.calls = []
        self.sleeps = []
        self.configuration = Mock(side_effect=lambda: self.calls.append("config") or {})
        self.request = AsyncMock(side_effect=lambda *a, **k: self.calls.append("recover_outbox"))
        self.close = AsyncMock(side_effect=lambda: self.calls.append("close_bridge"))
        self.bridge = Mock(return_value=types.SimpleNamespace(
            erp=types.SimpleNamespace(request=self.request), secret="private-fixture-bridge", close=self.close))
        self.recoverable = Mock(side_effect=lambda: self.calls.append("recoverable") or {"queued_for_reconciliation": []})
        self.pending = Mock(side_effect=lambda: self.calls.append("pending_events") or [])
        self.recover = AsyncMock(side_effect=lambda *a, **k: self.calls.append("recover_original_project"))
        self.scan = AsyncMock(side_effect=lambda *a, **k: self.calls.append("scan_project"))
        self.connect = Mock(side_effect=self.connection)
        self.spawn = Mock()
        self.log = Mock()

    @contextmanager
    def connection(self):
        self.calls.append("ledger_read")
        with sqlite3.connect(self.database) as db:
            yield db

    def original_snapshot(self):
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            return {name: db.execute("SELECT * FROM " + name).fetchall() for name in (
                "governance_invocation", "governance_task_lease", "original_provider",
                "original_automation", "original_admission")}

    async def run_loop(self, status, *, resume_after_wait=False):
        canonical = actual_loop_source(ROUTER)
        self.assertEqual(canonical, actual_loop_source(CANDIDATE))
        top, utils = types.ModuleType("open_webui"), types.ModuleType("open_webui.utils")
        ledger = types.ModuleType("open_webui.utils.agentic_governance")
        ledger.recoverable, ledger.pending_events, ledger.connect = self.recoverable, self.pending, self.connect
        maintenance = types.ModuleType("open_webui.utils.agentic_maintenance")
        maintenance.status = status
        bridge = types.ModuleType("open_webui.utils.agentic_bridge")
        bridge.ERPBridge, bridge.RPC = self.bridge, "/fixture/rpc/"
        orchestrator = types.ModuleType("open_webui.utils.agentic_orchestrator")
        orchestrator.scan_project = self.scan
        top.utils = utils
        for module in (ledger, maintenance, bridge, orchestrator):
            setattr(utils, module.__name__.rsplit(".", 1)[-1], module)
        async def sleep(seconds):
            self.sleeps.append(seconds)
            if resume_after_wait and seconds == 5 and len(self.sleeps) == 1:
                return
            raise asyncio.CancelledError()
        namespace = {
            "asyncio": types.SimpleNamespace(CancelledError=asyncio.CancelledError,
                                             sleep=sleep, gather=asyncio.gather),
            "log": self.log, "_config": self.configuration, "_spawn": self.spawn,
            "_deliver": AsyncMock(), "_process_event": AsyncMock(), "_recover_project": self.recover,
            "_gate": AsyncMock(), "RECONCILE_SECONDS": 1800,
        }
        exec(compile(ast.parse(canonical), "<actual-reconciliation-loop>", "exec"), namespace)
        modules = {module.__name__: module for module in (top, utils, ledger, maintenance, bridge, orchestrator)}
        with patch.dict(sys.modules, modules):
            with self.assertRaises(asyncio.CancelledError):
                await namespace["reconciliation_loop"](self.app)

    async def test_nonopen_gate_preserves_cancelled_original_and_every_boundary(self):
        original = self.original_snapshot()
        for state in ("draining", "sealed", "uninitialised", "unknown"):
            with self.subTest(state=state):
                self.sleeps.clear()
                await self.run_loop(Mock(return_value={"state": state}))
                self.assertEqual(self.original_snapshot(), original)
                self.assertEqual(self.sleeps, [5])
                self.assertEqual(self.calls, [])
                self.configuration.assert_not_called()
                self.bridge.assert_not_called()
                self.recoverable.assert_not_called()
                self.pending.assert_not_called()
                self.connect.assert_not_called()
                self.recover.assert_not_awaited()
                self.scan.assert_not_awaited()
                self.spawn.assert_not_called()

    async def test_existing_open_gate_resumes_unchanged_reconciliation_sequence(self):
        original = self.original_snapshot()
        status = Mock(side_effect=[{"state": "draining"}, {"state": "open"}])
        await self.run_loop(status, resume_after_wait=True)
        self.assertEqual(self.sleeps, [5, 1800])
        self.assertEqual(status.call_count, 2)
        self.assertEqual(self.calls, [
            "config", "recover_outbox", "close_bridge", "recoverable", "pending_events",
            "ledger_read", "recover_original_project", "scan_project"])
        self.request.assert_awaited_once_with(
            "POST", "/fixture/rpc/recover_outbox", json={}, headers={"X-ABIT-Agentic-Bridge": "private-fixture-bridge"})
        self.recover.assert_awaited_once_with(self.app, "PROJ-0196")
        self.scan.assert_awaited_once()
        self.assertEqual(self.original_snapshot(), original)
        self.spawn.assert_not_called()

    async def test_gate_read_failure_is_held_before_configuration_or_original_recovery(self):
        original = self.original_snapshot()
        await self.run_loop(Mock(side_effect=OSError("fixture unavailable gate")))
        self.assertEqual(self.sleeps, [1800])
        self.assertEqual(self.original_snapshot(), original)
        self.assertEqual(self.calls, [])
        self.configuration.assert_not_called()
        self.bridge.assert_not_called()
        self.recover.assert_not_awaited()
        self.log.exception.assert_called_once_with("Governance recovery preserved existing requests")


if __name__ == "__main__":
    unittest.main(verbosity=2)

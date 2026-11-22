"""Accepted originals bypass transport replacement and retain normal recovery.

The real Retry helper and router function run against the existing synthetic
ledger fixture. Only downstream recovery/model/ERP boundaries are controlled;
no application startup, provider connection or live data is involved.
"""
import ast
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_agentic_controls_events import P, T
from test_transport_retry import TransportRetry


ROUTER = Path(__file__).resolve().parents[1] / "open_webui/routers/agentic_governance.py"
EVIDENCE = ("receipt", "response_text", "scope_receipt_json")


class AcceptedOriginalRetry(TransportRetry):
    def set_evidence(self, fields, *, empty=False):
        values = {name: ("" if empty else "synthetic accepted " + name)
                  if name in fields else None for name in EVIDENCE}
        with self.primary.ledger(self.path.parent / "abitdot-primary.sqlite") as db:
            db.execute(
                "UPDATE dot_primary_runs SET receipt=?,response_text=?,scope_receipt_json=?",
                tuple(values[name] for name in EVIDENCE),
            )

    def snapshot(self, ident):
        with self.primary.ledger(self.path.parent / "abitdot-primary.sqlite") as db:
            provider = dict(db.execute("SELECT * FROM dot_primary_runs").fetchone())
        return self.g.find(ident, self.path, include_private=True), provider

    def assert_preserved(self, ident, before):
        self.assertEqual(self.snapshot(ident), before)
        self.assertEqual(before[0]["dispatch_attempts"], 1)
        self.assertEqual(before[0]["automation_run_id"],
                         self.bridge_module.stable_run_id(ident))
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)
        with self.g.connect(self.path) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_transport_retry'"
            ).fetchone()
            if exists:
                self.assertEqual(db.execute(
                    "SELECT count(*) FROM governance_transport_retry").fetchone()[0], 0)
        self.assertEqual(self.native.posts, 0)
        self.assertEqual(self.native.reads, 0)
        self.adapter.reject_transport.assert_not_awaited()
        self.observer.assert_not_awaited()
        self.assertEqual(self.actual_sends, 0)

    async def route_retry(self, ident):
        recovery = self.load("agentic_recovery")
        original_recovery = AsyncMock(return_value=None)
        observe = AsyncMock(side_effect=AssertionError("No transport observation"))
        publication = AsyncMock()
        rearm = Mock()
        deliver = Mock(side_effect=AssertionError("No replacement dispatch"))
        spawn = Mock()
        advance = AsyncMock()
        log = Mock()
        self.native.close = AsyncMock()
        modules = {}
        boundaries = {
            "open_webui.utils.agentic_executor": {
                "DotAdapter": Mock(return_value=self.adapter),
                "reconcile_publication": publication,
            },
            "open_webui.utils.agentic_result_store": {"get_result": Mock(return_value=None)},
            "open_webui.utils.agentic_it_execution": {
                "resume": AsyncMock(), "recover_completed": AsyncMock(),
            },
            "open_webui.utils.agentic_unsubmitted": {"rearm": rearm},
            "open_webui.models.automations": {
                "Automations": SimpleNamespace(get_by_id=AsyncMock(return_value=self.automation)),
            },
        }
        for name, values in boundaries.items():
            module = ModuleType(name)
            module.__dict__.update(values)
            modules[name] = module
        namespace = {
            "asyncio": asyncio, "_config": lambda: {}, "_gate": self.gate, "_spawn": spawn,
            "_deliver": deliver, "_advance": advance, "log": log,
        }
        nodes = [item for item in ast.parse(ROUTER.read_text()).body
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and item.name in {"_recover_project", "_reconciliation_wakeup"}]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTER), "exec"), namespace)
        app = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.dict(sys.modules, modules),
            patch.object(self.g, "path", return_value=self.path),
            patch.object(self.bridge_module, "ERPBridge", return_value=self.native),
            patch.object(recovery, "recover_original", original_recovery),
            patch.object(recovery, "observe_original", observe),
        ):
            result = await namespace["_recover_project"](
                app, P, T, explicit=True, retry_event_id=self.retry_event)
        self.assertEqual(result, [])
        original_recovery.assert_awaited_once_with(
            ident, config={}, project_gate=self.gate, bridge=self.native, app=app)
        observe.assert_not_awaited()
        publication.assert_not_awaited()
        rearm.assert_not_called()
        deliver.assert_not_called()
        spawn.assert_not_called()
        advance.assert_not_awaited()
        log.exception.assert_not_called()
        self.native.close.assert_awaited_once()

    async def accepted_case(self, fields):
        ident, _, _ = await self.setup_original()
        self.set_evidence(fields)
        before = self.snapshot(ident)
        self.native.packet = AsyncMock(wraps=self.native.packet)
        with (
            patch.object(self.primary, "deliver", AsyncMock()) as send,
            patch.object(self.relay, "send_via_existing_relay", AsyncMock()) as relay_send,
        ):
            await self.route_retry(ident)
            self.assertIsNone(await self.retry(ident))
        send.assert_not_awaited()
        relay_send.assert_not_awaited()
        self.native.packet.assert_not_awaited()
        self.assert_preserved(ident, before)

    async def test_receipt_routes_to_original_recovery(self):
        await self.accepted_case(("receipt",))

    async def test_response_routes_to_original_recovery(self):
        await self.accepted_case(("response_text",))

    async def test_scope_receipt_routes_to_original_recovery(self):
        await self.accepted_case(("scope_receipt_json",))

    async def test_all_acceptance_evidence_routes_to_original_recovery(self):
        await self.accepted_case(EVIDENCE)

    async def test_empty_acceptance_marker_still_preserves_original(self):
        ident, _, _ = await self.setup_original()
        for field in EVIDENCE:
            with self.subTest(field=field):
                self.set_evidence((field,), empty=True)
                before = self.snapshot(ident)
                self.assertIsNone(await self.retry(ident))
                self.assert_preserved(ident, before)

    async def test_identity_mismatch_is_not_hidden_by_acceptance_evidence(self):
        import json

        ident, envelope, _ = await self.setup_original()
        self.set_evidence(EVIDENCE)
        automation = deepcopy(self.automation)
        roster = self.g.roster(P, self.path)
        cases = (
            ("missing roster", "roster", None, None),
            ("registered owner", "roster", "owner_id", "foreign-owner"),
            ("automation id", "automation", "id", "foreign-automation"),
            ("managed flag", "meta", "governance_managed", False),
            ("project", "meta", "agentic_project", "PROJ-OTHER"),
            ("role", "meta", "agentic_role", "qa"),
            ("ERP role owner", "meta", "erp_role_user", "foreign@example.invalid"),
            ("envelope owner", "envelope", "owner_id", "foreign-owner"),
            ("envelope run", "envelope", "run_id", "foreign-run"),
            ("envelope automation", "envelope", "automation_id", "foreign-automation"),
        )
        for name, target, key, value in cases:
            with self.subTest(identity=name):
                self.automation = deepcopy(automation)
                current_roster, current_envelope = deepcopy(roster), deepcopy(envelope)
                if target == "roster":
                    if key is None:
                        current_roster = []
                    else:
                        for member in current_roster:
                            if member["agent_id"] == automation.id:
                                member[key] = value
                elif target == "automation":
                    setattr(self.automation, key, value)
                elif target == "meta":
                    self.automation.meta[key] = value
                else:
                    current_envelope[key] = value
                with self.primary.ledger(self.path.parent / "abitdot-primary.sqlite") as db:
                    db.execute("UPDATE dot_primary_runs SET envelope_json=?",
                               (json.dumps(current_envelope),))
                before = self.snapshot(ident)
                with patch.object(self.g, "roster", return_value=current_roster):
                    with self.assertRaisesRegex(ValueError, "ownership"):
                        await self.retry(ident)
                self.assert_preserved(ident, before)

    async def test_accepted_original_still_requires_current_native_retry(self):
        ident, _, _ = await self.setup_original()
        self.set_evidence(EVIDENCE)
        with self.g._transaction(self.path) as db:
            db.execute("UPDATE governance_native_control SET action='resume'")
        before = self.snapshot(ident)
        with self.assertRaisesRegex(ValueError, "superseded"):
            await self.retry(ident)
        self.assert_preserved(ident, before)

    async def test_task_pause_keeps_accepted_original_fenced(self):
        ident, _, _ = await self.setup_original()
        self.set_evidence(EVIDENCE)
        self.controls.task_control(P, T, "pause", actor="human@example.invalid", db_path=self.path)
        before = self.snapshot(ident)
        with self.assertRaises(ValueError):
            await self.retry(ident)
        self.assert_preserved(ident, before)

    async def test_project_revocation_keeps_accepted_original_fenced(self):
        ident, _, _ = await self.setup_original()
        self.set_evidence(EVIDENCE)
        before = self.snapshot(ident)
        self.allowed = False
        with self.assertRaisesRegex(ValueError, "Project revoked"):
            await self.retry(ident)
        self.assertEqual(self.snapshot(ident), before)
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)
        self.observer.assert_not_awaited()
        self.adapter.reject_transport.assert_not_awaited()
        self.assertEqual(self.native.posts, 0)

    async def test_unknown_observation_cannot_create_replacement_generation(self):
        ident, _, _ = await self.setup_original()
        before = self.snapshot(ident)
        self.observer.return_value = (None, None)
        self.assertIsNone(await self.retry(ident))
        self.observer.assert_awaited_once()
        self.observer.reset_mock()
        self.assert_preserved(ident, before)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        AcceptedOriginalRetry(name) for name in AcceptedOriginalRetry.__dict__
        if name.startswith("test_")
    )

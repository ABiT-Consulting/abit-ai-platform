"""Closing PM admission uses the complete registered Project roster, without extra grants.

Real temporary ledger/executor/PM factory and strict validators are used. Native,
provider and HTTP peers stay controlled; no live packet or business payload is used.
"""
from copy import deepcopy
import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_agentic_pm_review as pm_fixture
from test_agentic_relay_scope import RelayScope, Session


class PMRelayScope(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.base = pm_fixture.PMReview(methodName="runTest")
        await self.base.asyncSetUp()
        self.addCleanup(self.base.doCleanups)
        self.f = self.base.f
        original_packet = self.f.erp.packet
        def closing_packet(role):
            value = original_packet(role)
            if role == "project_manager" and value.get("qa_review"):
                value["tool_scopes"] = ["read_project"]
                value["content_sha256"] = self.f.bridge_module.packet_digest(value)
            return value
        self.f.erp.packet = closing_packet
        await self.base.approved()
        queued = await self.base.ensure()
        self.row = self.f.ledger.find(queued["invocation_id"], self.f.db, include_private=True)
        self.packet = json.loads(self.row["work_packet"])
        self.module = importlib.import_module("open_webui.utils.agentic_relay_scope")
        self.relay = importlib.import_module("open_webui.utils.abitdot_primary_relay")
        self.adapter = self.f.executor.DotAdapter(types.SimpleNamespace())
        self.calls = []
        self.session_options = []
        self.actual_sends = 0
        self.status = 200
        self.raw = None
        self.mutate = None
        self.account = AsyncMock(return_value="a" * 64)
        self.credentials = Mock(return_value=("fixture-only-auth", "fixture-user"))
        router = types.ModuleType("open_webui.routers.chatgpt_relay")
        router.MESSAGE_URL = "http://127.0.0.1:8810"
        router.MESSAGE_TOKEN_FILE = "/fixture/token"
        router._credentials = self.credentials
        self.patch(patch.dict(sys.modules, {"open_webui.routers.chatgpt_relay": router}))
        self.patch(patch.dict(os.environ, {
            "ABIT_DOT_PRIMARY_ENABLED": "true",
            "ABIT_CHATGPT_DOT_OWNER_ID": "verified-owner",
            "ABIT_CHATGPT_DOT_URL": "https://chatgpt.com/dots/dot-fixture-000001",
            "ABIT_DOT_PRIMARY_RELAY_URL": "http://127.0.0.1:8810",
        }))
        old_token = os.environ.pop("ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE", None)
        if old_token is not None:
            self.addCleanup(lambda: os.environ.__setitem__("ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE", old_token))
        import aiohttp
        self.patch(patch.object(aiohttp, "ClientSession", lambda **kw: Session(self, **kw)))
        self.patch(patch.object(self.relay, "relay_account_for_owner", self.account))
        # DotAdapter uses the default ledger path. Redirect only that path to the
        # real fixture database; keep roster SQL and all identity checks intact.
        roster = self.f.ledger.roster
        self.roster_projects = []
        def fixture_roster(project, db_path=None):
            self.roster_projects.append(project)
            return roster(project, self.f.db)
        self.patch(patch.object(self.f.ledger, "roster", side_effect=fixture_roster))

    def patch(self, item):
        value = item.start()
        self.addCleanup(item.stop)
        return value

    def proof(self, body):
        return RelayScope.proof(self, body)

    def changed_qa(self, **fields):
        packet = deepcopy(self.packet)
        actor = packet["qa_review"]["result_contract"]["actor"]
        actor.update(fields)
        if "agent_id" in fields:
            packet["qa_review"]["qa_agent_id"] = fields["agent_id"]
        packet["qa_review"]["qa_result_sha256"] = self.f.bridge_module.digest(
            packet["qa_review"]["result_contract"]
        )
        packet["content_sha256"] = self.f.bridge_module.packet_digest(packet)
        return packet

    async def denied(self, packet=None, *, row=None, owner=None):
        before = len(self.calls)
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.module.require_relay_scope(
                row or self.row, packet or self.packet,
                owner or "verified-owner", db_path=self.f.db,
            )
        self.assertEqual(before, len(self.calls))
        self.assertEqual(0, self.actual_sends)

    async def test_actual_closing_pm_adapter_preflight_accepts_same_project_qa_without_grant_expansion(self):
        before = deepcopy(self.packet)
        source = self.f.modules["agentic_result_store"].get_result(
            self.packet["qa_review"]["qa_invocation_id"], self.f.db
        )
        self.assertEqual("pass", source["result"]["outcome"])
        self.assertEqual("QA APPROVED", source["receipt"]["status"])
        proof = await self.adapter.preflight(self.row, self.packet)
        self.assertEqual(before, self.packet)
        self.assertEqual("project_manager", proof["scope"]["role"])
        self.assertEqual(self.packet["binding"]["agent_id"], proof["scope"]["agent_id"])
        self.assertEqual(["read_project"], proof["scope"]["tool_scopes"])
        self.assertEqual(["read_project"], proof["enforced_tool_scopes"])
        self.assertEqual([], proof["external_write_allowlist"])
        self.assertEqual([pm_fixture.PROJECT, pm_fixture.PROJECT], self.roster_projects)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(0, self.actual_sends)
        saved = self.f.ledger.find(self.row["invocation_id"], self.f.db, include_private=True)
        self.assertIsNone(saved["automation_run_id"])
        self.assertIsNone(saved["submitted_at"])
        self.assertEqual(0, saved["dispatch_attempts"])

    async def test_missing_current_project_qa_is_not_filled_by_foreign_project_registration(self):
        with self.f.ledger.connect(self.f.db) as db:
            # Preserve the referenced agent ID while removing its registration
            # from this Project. A registration elsewhere cannot supply authority.
            db.execute(
                "UPDATE governance_agent SET erp_project=? WHERE erp_project=? AND role='qa'",
                ("PROJ-9001", pm_fixture.PROJECT),
            )
        await self.denied()
        self.assertEqual([pm_fixture.PROJECT], self.roster_projects)
        self.account.assert_not_awaited()
        self.credentials.assert_not_called()

    async def test_foreign_nested_qa_binding_denied_even_with_consistent_rehashed_packet(self):
        self.f.ledger.enroll("PROJ-9001", "foreign-owner", pm_fixture.ROLE_USERS, self.f.db)
        await self.denied(self.changed_qa(
            project_id="PROJ-9001",
            agent_id=self.f.ledger.agent_id("PROJ-9001", "qa"),
        ))
        self.account.assert_not_awaited()

    async def test_unregistered_nested_qa_agent_denied(self):
        await self.denied(self.changed_qa(agent_id="unregistered-qa-agent"))
        self.account.assert_not_awaited()

    async def test_nested_qa_erp_user_must_match_current_registration(self):
        await self.denied(self.changed_qa(erp_user="foreign-qa@example.test"))
        self.account.assert_not_awaited()

    async def test_current_target_owner_role_and_erp_binding_are_still_required(self):
        for mutation in ("owner", "role", "erp"):
            with self.subTest(mutation=mutation):
                if mutation == "owner":
                    await self.denied(owner="different-owner")
                elif mutation == "role":
                    await self.denied(row={**self.row, "target_role": "qa"})
                else:
                    packet = deepcopy(self.packet)
                    packet["binding"]["erp_user"] = "foreign-pm@example.test"
                    packet["content_sha256"] = self.f.bridge_module.packet_digest(packet)
                    await self.denied(packet)
        self.account.assert_not_awaited()
        self.credentials.assert_not_called()

    async def test_next_preflight_reloads_roster_and_rejects_revoked_qa(self):
        first = await self.adapter.preflight(self.row, self.packet)
        saved = deepcopy(first)
        with self.f.ledger.connect(self.f.db) as db:
            db.execute(
                "UPDATE governance_agent SET erp_user=? WHERE erp_project=? AND role='qa'",
                ("changed-qa@example.test", pm_fixture.PROJECT),
            )
        await self.denied()
        self.assertEqual(saved, first)
        self.assertEqual(1, len(self.calls))

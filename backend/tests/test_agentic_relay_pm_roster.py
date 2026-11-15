"""Closing PM scope checks use the authoritative project roster; no model sends."""

from copy import deepcopy
import importlib
import unittest
import sys
import types
from unittest.mock import AsyncMock, patch

import test_agentic_gateway_pm_review as pm_fixture
import test_agentic_relay_scope as scope_fixture


class ClosingReviewRoster(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = pm_fixture.PMReviewEvidence('runTest')
        await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.module = importlib.import_module('open_webui.utils.agentic_relay_scope')
        self.row, self.packet = self.fixture.original, deepcopy(self.fixture.packet)
        self.status, self.raw, self.mutate = 200, None, None
        self.calls, self.session_options, self.actual_sends = [], [], 0
        self.proof = scope_fixture.RelayScope.proof.__get__(self)
        import aiohttp
        relay = importlib.import_module('open_webui.utils.abitdot_primary_relay')
        primary = importlib.import_module('open_webui.utils.abitdot_primary')
        router = types.ModuleType('open_webui.routers.chatgpt_relay')
        router.MESSAGE_URL = 'http://127.0.0.1:8810'
        router.MESSAGE_TOKEN_FILE = '/fixed/fixture/token'
        router._credentials = lambda path: ('fixture-token-not-a-real-secret', 'fixture-user')
        router_patch = patch.dict(sys.modules, {'open_webui.routers.chatgpt_relay': router})
        router_patch.start()
        self.addCleanup(router_patch.stop)
        for target, name, value in (
            (aiohttp, 'ClientSession', lambda **kw: scope_fixture.Session(self, **kw)),
            (relay, 'relay_account_for_owner', AsyncMock(return_value='a' * 64)),
            (primary, 'primary_dot_for_owner', lambda owner: 'controlled-dot'),
            (router, '_credentials', lambda path: ('fixture-token-not-a-real-secret', 'fixture-user')),
        ):
            item = patch.object(target, name, value)
            item.start()
            self.addCleanup(item.stop)

    async def check(self):
        return await self.module.require_relay_scope(
            self.row, self.packet, 'verified-owner', db_path=self.f.db
        )

    async def test_exact_independent_qa_review_passes_with_current_project_roster(self):
        validator = importlib.import_module('open_webui.utils.agentic_contracts.validator')
        pm = self.packet['binding']
        self.assertIn('Agent is not registered for this project', validator.validate_work_packet(
            self.packet, registered_bindings={'project_manager': pm}
        ))
        proof = await self.check()
        self.assertEqual(self.packet['qa_review']['qa_agent_id'],
                         self.packet['qa_review']['result_contract']['actor']['agent_id'])
        self.assertEqual(self.row['invocation_id'], proof['scope']['invocation_id'])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(0, self.actual_sends)
        self.assertIsNone(self.f.ledger.find(self.row['invocation_id'], self.f.db)['automation_run_id'])

    async def test_missing_companion_qa_registration_denies_before_scope_http(self):
        roster = self.f.ledger.roster(self.row['erp_project'], self.f.db)
        with patch.object(self.f.ledger, 'roster', return_value=[r for r in roster if r['role'] != 'qa']):
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check()
        self.assertEqual([], self.calls)
        self.assertEqual(0, self.actual_sends)

    async def test_foreign_companion_qa_registration_denies_before_scope_http(self):
        roster = deepcopy(self.f.ledger.roster(self.row['erp_project'], self.f.db))
        for row in roster:
            if row['role'] == 'qa':
                row['agent_id'] = 'agent-from-another-project'
        with patch.object(self.f.ledger, 'roster', return_value=roster):
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check()
        self.assertEqual([], self.calls)
        self.assertEqual(0, self.actual_sends)

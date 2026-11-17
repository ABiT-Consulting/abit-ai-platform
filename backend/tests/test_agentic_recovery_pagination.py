"""Actionable recovery uses real SQLite identities, never a public history window."""
import ast
import importlib
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import test_agentic_ledger_fences as fixture

g, P, T, GATE = fixture.g, fixture.P, fixture.T, fixture.GATE


class RecoveryPagination(unittest.IsolatedAsyncioTestCase):
    packet = fixture.DurableLedger.packet
    enqueue = fixture.DurableLedger.enqueue

    def setUp(self):
        fixture.DurableLedger.setUp(self)
        modules = patch.dict(sys.modules, {'open_webui.utils.agentic_governance': g})
        modules.start(); self.addCleanup(modules.stop)
        selected = patch.object(g, 'path', return_value=self.db)
        selected.start(); self.addCleanup(selected.stop)
        bridge_module = importlib.import_module('open_webui.utils.agentic_bridge')
        self.bridge = types.SimpleNamespace(close=AsyncMock())
        self.original = AsyncMock(return_value=None)
        self.spawn = Mock(side_effect=AssertionError('Recovery audit cannot start a model'))
        observations = types.ModuleType('open_webui.utils.agentic_execution_observations')
        observations.seed_uncertain = Mock()
        observations.deliver_pending = AsyncMock(return_value=[])
        # Stage is retained for real ledger fixture transitions; only remote
        # visibility delivery is controlled in this selector/route test.
        real_observations = importlib.import_module('open_webui.utils.agentic_execution_observations')
        observations.stage = real_observations.stage
        dependencies = {
            'agentic_result_store': {'get_result': Mock(return_value=None)},
            'agentic_executor': {'reconcile_publication': AsyncMock()},
            'agentic_recovery': {'recover_original': self.original, 'resume_qa_preflight': AsyncMock()},
            'agentic_it_execution': {'resume': AsyncMock(), 'recover_completed': AsyncMock()},
            'agentic_unsubmitted': {'rearm': Mock()},
            'agentic_controls': {'task_control': Mock(return_value={'state': 'enabled'})},
        }
        stubs = {'open_webui.utils.agentic_execution_observations': observations}
        for name, values in dependencies.items():
            module = types.ModuleType('open_webui.utils.' + name)
            module.__dict__.update(values); stubs[module.__name__] = module
        overrides = patch.dict(sys.modules, stubs)
        overrides.start(); self.addCleanup(overrides.stop)
        factory = patch.object(bridge_module, 'ERPBridge', return_value=self.bridge)
        factory.start(); self.addCleanup(factory.stop)
        router = fixture.ROOT.parent / 'routers/agentic_governance.py'
        node = next(node for node in ast.parse(router.read_bytes()).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == '_recover_project')
        namespace = {'_config': lambda: {}, '_gate': AsyncMock(return_value=({}, GATE)),
                     '_spawn': self.spawn, '_advance': AsyncMock(), '_deliver': AsyncMock(),
                     'log': logging.getLogger('recovery-pagination-offline')}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(router), 'exec'), namespace)
        self.recover = namespace['_recover_project']

    def add(self, index, *, state='uncertain', task=None):
        ident = 'invocation-recovery-' + str(index).zfill(5)
        task = task or 'TASK-recovery-' + str(index).zfill(5)
        self.enqueue(ident, task=task, work_packet=self.packet(task=task))
        if state != 'queued':
            row = g.claim_invocation(ident, 'run-' + ident, gate=GATE, db_path=self.db)
            if state == 'uncertain':
                g.mark_submitted(ident, row['automation_run_id'], row['lease_token'], gate=GATE, db_path=self.db)
            g.finish_execution(ident, row['automation_run_id'], row['lease_token'],
                               state, db_path=self.db)
        return g.find(ident, self.db, include_private=True)

    def leases(self):
        with g.connect(self.db) as db:
            return [tuple(row) for row in db.execute('SELECT * FROM governance_task_lease ORDER BY erp_task')]

    async def test_old_uncertain_original_is_recovered_behind_more_than_100_terminal_rows(self):
        old = self.add(0)
        for index in range(1, 126):
            self.add(index, state='failed')
        with g.connect(self.db) as db:
            db.execute('UPDATE governance_invocation SET created_at=1 WHERE invocation_id=?',
                       (old['invocation_id'],))
        self.assertNotIn(old['invocation_id'], [row['invocation_id'] for row in g.list_invocations(P, self.db, 100)])
        before = self.leases()
        await self.recover(object(), P)
        self.original.assert_awaited_once()
        self.assertEqual(self.original.await_args.args[0], old['invocation_id'])
        self.assertEqual(self.leases(), before)
        self.assertEqual(g.find(old['invocation_id'], self.db, include_private=True), old | {'created_at': 1})
        self.spawn.assert_not_called()

    async def test_explicit_old_task_scope_is_applied_before_the_history_bound(self):
        old = self.add(0, task=T)
        for index in range(1, 126):
            self.add(index)
        with g.connect(self.db) as db:
            db.execute('UPDATE governance_invocation SET created_at=1 WHERE invocation_id=?',
                       (old['invocation_id'],))
        before = self.leases()
        await self.recover(object(), P, T, explicit=True)
        self.original.assert_awaited_once()
        self.assertEqual(self.original.await_args.args[0], old['invocation_id'])
        self.assertEqual(self.leases(), before)
        pages = list(g._recovery_invocation_pages(P, self.db, task=T, explicit=True, page_size=1))
        self.assertEqual([[row['invocation_id'] for row in page] for page in pages], [[old['invocation_id']]])
        self.spawn.assert_not_called()

    async def test_more_than_100_actionable_rows_are_paged_once_under_a_fixed_watermark(self):
        originals = [self.add(index) for index in range(205)]
        expected = [row['invocation_id'] for row in originals]
        pages = list(g._recovery_invocation_pages(P, self.db, page_size=37))
        self.assertEqual([len(page) for page in pages], [37, 37, 37, 37, 37, 20])
        self.assertEqual([row['invocation_id'] for page in pages for row in page], expected)
        before = self.leases()
        later = []
        async def read(ident, **kwargs):
            if not later:
                later.append(self.add(1000))
            return None
        self.original.side_effect = read
        await self.recover(object(), P)
        visited = [call.args[0] for call in self.original.await_args_list]
        self.assertEqual(visited, expected)
        self.assertEqual(len(set(visited)), 205)
        self.assertNotIn(later[0]['invocation_id'], visited)
        after = self.leases()
        self.assertEqual([row for row in after if row[1] != later[0]['erp_task']], before)
        self.assertEqual(g.find(originals[-1]['invocation_id'], self.db, include_private=True), originals[-1])
        self.spawn.assert_not_called()

    async def test_selector_does_not_mutate_or_replay_terminal_or_fresh_running_history(self):
        queued = self.add(0, state='queued')
        ended = self.add(1, state='failed')
        # Claim this never-submitted original; healthy running work is observed
        # by its owner, not treated as failed just because history is scanned.
        running = g.claim_invocation(queued['invocation_id'], 'fresh-running', gate=GATE, db_path=self.db)
        before = self.leases()
        self.assertEqual(list(g._recovery_invocation_pages(P, self.db)), [])
        self.assertEqual(self.leases(), before)
        self.assertEqual(g.find(ended['invocation_id'], self.db, include_private=True), ended)
        self.assertEqual(g.find(running['invocation_id'], self.db, include_private=True), running)


if __name__ == '__main__':
    unittest.main()

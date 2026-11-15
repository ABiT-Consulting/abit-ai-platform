"""Actual canonical recovery route with the real isolated QA proof/ledger fixture."""
import ast
import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_qa_preflight_retry import QAPreflightRetry

ROUTER = Path(__file__).resolve().parents[1] / 'open_webui/routers/agentic_governance.py'


class QAPreflightRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = QAPreflightRetry('test_newer_ack_pending_review_one_successor_preserves_original_and_duplicate')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.pending = []
        self.addCleanup(self.close_pending)
        self.deliver = AsyncMock()
        self.publication = AsyncMock(return_value=None)
        self.preflight = AsyncMock(wraps=self.f.recovery.resume_qa_preflight)
        self.f.bridge.close = AsyncMock()
        for item in (
            patch.object(self.f.g, 'path', return_value=self.f.path),
            patch.object(self.f.bridge_module, 'ERPBridge', return_value=self.f.bridge),
            patch.object(self.f.recovery, 'resume_qa_preflight', self.preflight),
        ):
            item.start()
            self.addCleanup(item.stop)
        modules = {}
        for name, values in {
            'agentic_executor': {'reconcile_publication': self.publication},
            'agentic_it_execution': {'resume': AsyncMock(), 'recover_completed': AsyncMock()},
        }.items():
            module = types.ModuleType('open_webui.utils.' + name)
            module.__dict__.update(values)
            modules[module.__name__] = module
        controlled = patch.dict(sys.modules, modules)
        controlled.start()
        self.addCleanup(controlled.stop)
        node = next(node for node in ast.parse(ROUTER.read_bytes()).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == '_recover_project')
        namespace = {
            '_config': lambda: self.f.config, '_gate': self.f.gate,
            '_spawn': lambda app, coroutine: self.pending.append(coroutine),
            '_deliver': self.deliver, '_advance': AsyncMock(), 'log': Mock(),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROUTER), 'exec'), namespace)
        self.recover = namespace['_recover_project']
        self.app = object()

    def close_pending(self):
        for coroutine in self.pending:
            if coroutine.cr_frame is not None:
                coroutine.close()

    async def test_explicit_route_uses_real_proof_and_schedules_only_the_new_qa_unit(self):
        f = self.f
        reconciled = await self.recover(self.app, f.qa['erp_project'], f.qa['erp_task'], explicit=True)
        self.assertEqual(reconciled, [f.qa_id])
        self.preflight.assert_awaited_once()
        self.assertEqual(self.preflight.await_args.args, (f.qa_id,))
        self.assertEqual(len(self.pending), 1)
        await asyncio.gather(*self.pending)
        with f.g.connect(f.path) as db:
            row = db.execute("SELECT * FROM governance_invocation WHERE source_invocation_id=?",
                             (f.qa_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['target_role'], 'qa')
            self.assertEqual(row['state'], 'queued')
            self.assertIsNone(row['automation_run_id'])
            self.assertIsNone(row['submitted_at'])
            self.assertEqual(row['dispatch_attempts'], 0)
            self.deliver.assert_awaited_once_with(self.app, row['invocation_id'])
        self.assertEqual(f.history(), f.immutable)
        self.assertEqual(f.g.find(f.qa_id, f.path)['state'], 'handed_off')
        # The source QA result is already published, so no publication replay occurs.
        self.assertNotIn(f.qa_id, [call.args[0] for call in self.publication.await_args_list])

    async def test_passive_reconciliation_cannot_create_a_qa_preflight_successor(self):
        f = self.f
        reconciled = await self.recover(self.app, f.qa['erp_project'], f.qa['erp_task'])
        self.assertEqual(reconciled, [])
        self.preflight.assert_not_awaited()
        self.assertEqual(self.pending, [])
        self.assertEqual(f.history(), f.immutable)
        self.assertEqual(f.g.find(f.qa_id, f.path)['state'], 'execution_finished')
        with f.g.connect(f.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()

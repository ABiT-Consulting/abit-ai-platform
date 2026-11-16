"""Execute the actual scheduler helper without importing the application."""
import ast, asyncio, types, unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

SOURCE=Path(__file__).parents[1]/'runtime/utils/automations.py'
class Denied(Exception):pass
class Claims(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree=ast.parse(SOURCE.read_bytes())
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_spawn_claimed_automation')
        self.execute=AsyncMock();self.finish=AsyncMock();self.spawn=Mock()
        self.ns={'_agov_maintenance':types.SimpleNamespace(MaintenanceDenied=Denied,spawn_automation=self.spawn),
            'AutomationRuns':types.SimpleNamespace(finish_claim=self.finish),
            'execute_scheduled_automation':self.execute,'log':types.SimpleNamespace(warning=Mock())}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(SOURCE),'exec'),self.ns)
        self.call=self.ns['_spawn_claimed_automation'];self.agent=types.SimpleNamespace(id='agent-fixture')
    async def test_denied_admission_finishes_only_unstarted_claim(self):
        self.spawn.side_effect=Denied('existing unresolved original')
        await self.call('app',self.agent,'new-claim')
        self.execute.assert_not_called()
        self.finish.assert_awaited_once_with('new-claim','error',error='Maintenance admission denied before execution; original work retained')
    async def test_admitted_factory_keeps_original_identity_and_is_not_awaited_by_scheduler(self):
        await self.call('app',self.agent,'allowed-claim')
        self.finish.assert_not_called();self.execute.assert_not_called()
        factory,agent=self.spawn.call_args.args
        self.assertEqual(agent,self.agent.id);await factory()
        self.execute.assert_awaited_once_with('app',self.agent,'allowed-claim')
    async def test_unknown_error_is_not_falsely_classified_as_denied(self):
        self.spawn.side_effect=RuntimeError('unknown worker outcome')
        with self.assertRaisesRegex(RuntimeError,'unknown worker outcome'):await self.call('app',self.agent,'claim')
        self.finish.assert_not_called();self.execute.assert_not_called()
    async def test_denied_item_does_not_leave_later_batch_item_running_without_a_worker(self):
        self.spawn.side_effect=[Denied('old unresolved original'),None]
        await self.call('app',self.agent,'denied-first')
        await self.call('app',self.agent,'allowed-second')
        self.assertEqual(self.spawn.call_count,2)
        self.finish.assert_awaited_once();self.assertEqual(self.finish.await_args.args,('denied-first','error'))
        await self.spawn.call_args.args[0]()
        self.execute.assert_awaited_once_with('app',self.agent,'allowed-second')
if __name__=='__main__':unittest.main(verbosity=2)

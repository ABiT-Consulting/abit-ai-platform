"""Same-run Codex -> Dot -> independent QA, using the real governance executor.

Only provider/ERP boundaries are fixtures; receipts, validation, artifact bytes,
task leases, publication and original-run recovery are the production helpers.
"""
import importlib
import json
import unittest
from unittest.mock import AsyncMock, patch

import test_agentic_executor_flow as flow


class CodexFlow(flow.ExecutorFlow):
    async def primary(self, outcome):
        codex = importlib.import_module('open_webui.utils.agentic_codex')
        engine = importlib.import_module('open_webui.utils.agentic_execution_engine')
        gateway = importlib.import_module('open_webui.utils.agentic_gateway_sessions')
        # Initialize actual operation-journal schema; provider admission is the fixture boundary.
        with gateway.connect(self.db):
            pass
        async def run(row, prompt, config, tools):
            engine.change(row, {'starting'}, 'running', self.db, pid=999999)
            return outcome
        adapter = codex.CodexAdapter(None, self.project_gate, self.config, self.db, runner=run)
        adapter.preflight = AsyncMock(return_value={'scope': {}})
        adapter.scope_check = AsyncMock(return_value={'scope': {}})
        adapter.prepare = self.adapter.prepare
        adapter.record = self.adapter.record
        # This fixture verifies the real Result/artifact/ERP path. Real PR/test
        # gateway enforcement and epoch transfer have their own integration suite.
        adapter.requires_governed_gateway = False
        self.addCleanup(patch.stopall)
        patch.object(gateway, 'admit', AsyncMock()).start()
        return adapter, engine

    async def test_quota_handoff_preserves_logical_run_then_independent_qa_and_erp_readback(self):
        row, _, _ = await self.enqueue()
        primary, engine = await self.primary({'exit_code': 1, 'reason':'quota','reply':'','events':[
            {'type':'turn.failed','error':{'code':'usage_limit_reached'}}]})
        # Checkpoint contains verified original identity plus the operation journal.
        self.config['agentic_repository_gateway'] = {'projects': {flow.PROJECT: {
            'repository': self.erp.packet('developer')['repository'], 'environment':'development',
            'workspace_root':str(self.root/'unused-workspace')}}}
        with patch.object(importlib.import_module('open_webui.utils.agentic_executor').DotAdapter,
                          'submit', side_effect=self.adapter.submit):
            receipt = await self.executor.execute(row['invocation_id'], project_gate=self.project_gate,
                config=self.config, adapter=primary, db_path=self.db, bridge=self.bridge)
        self.assertEqual(receipt['status'], 'Pending Review')
        execution = engine.get(row['invocation_id'], self.db, private=True)
        original = self.ledger.find(row['invocation_id'], self.db, include_private=True)
        self.assertEqual(execution['run_id'], original['automation_run_id'])
        self.assertEqual(execution['executor'], 'dot')
        self.assertEqual(execution['epoch'], 2)
        self.assertEqual(execution['handoff_reason'], 'quota')
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(execution['checkpoint']['task_revision'], original['task_revision'])
        # Re-dispatch is a no-op. The fallback is the same run, never another start.
        self.assertIsNone(await self.executor.execute(row['invocation_id'], project_gate=self.project_gate,
            config=self.config, adapter=primary, db_path=self.db, bridge=self.bridge))
        qa, _, _ = await self.handoff(row['invocation_id'], 'qa', 'codex-independent-qa-001')
        qa_receipt = await self.execute(qa['invocation_id'])
        self.assertEqual(qa_receipt['status'], 'QA APPROVED')
        self.assertEqual(self.erp.task['status'], 'QA APPROVED')
        self.assertEqual(self.erp.publish_calls, ['run.started','run.ready','run.started','qa.passed'])
        self.assertNotEqual(qa['agent_id'], row['agent_id'])
        self.assertEqual(len(self.adapter.calls), 2)

    async def test_auth_network_or_failed_test_never_dispatch_fallback(self):
        row, _, _ = await self.enqueue()
        primary, engine = await self.primary({'exit_code': 1, 'reason':None,'reply':'','events':[
            {'type':'turn.failed','error':{'code':'authentication_error'}}]})
        self.config['agentic_repository_gateway'] = {'projects': {flow.PROJECT: {
            'repository': self.erp.packet('developer')['repository'], 'environment':'development',
            'workspace_root':str(self.root/'unused-workspace')}}}
        fallback = AsyncMock()
        with patch.object(importlib.import_module('open_webui.utils.agentic_executor').DotAdapter, 'submit', fallback):
            result = await self.executor.execute(row['invocation_id'], project_gate=self.project_gate,
                config=self.config, adapter=primary, db_path=self.db, bridge=self.bridge)
        self.assertIsNone(result)
        fallback.assert_not_awaited()
        self.assertEqual(engine.get(row['invocation_id'],self.db)['state'],'held')
        self.assertEqual(self.ledger.find(row['invocation_id'],self.db)['state'],'uncertain')

    async def test_saved_codex_reply_recovers_same_run_without_another_provider_call(self):
        row, packet, _ = await self.enqueue()
        # Generate the real fixture artifacts/result once, then use it as the
        # controlled external Codex response.
        original_submit = self.adapter.submit
        run = self.bridge_module.stable_run_id(row['invocation_id'])
        record = await original_submit(row, packet, run, None, self.bridge, before_submit=AsyncMock())
        self.adapter.calls.clear()
        primary, engine = await self.primary({'exit_code':0,'reason':None,'reply':record['response_text'],'events':[]})
        with patch.object(self.executor,'complete_original',side_effect=TimeoutError('fixture lost local acknowledgment')):
            await self.executor.execute(row['invocation_id'], project_gate=self.project_gate,
                config=self.config,adapter=primary,db_path=self.db,bridge=self.bridge)
        state=engine.get(row['invocation_id'],self.db,private=True)
        self.assertEqual(state['state'],'reply_saved')
        original=self.ledger.find(row['invocation_id'],self.db,include_private=True)
        automation=type('Automation',(),{'id':row['agent_id'],'user_id':'verified-owner','meta':{
            'agentic_project':flow.PROJECT,'agentic_role':'developer',
            'erp_role_user':flow.ROLE_USERS['developer'],'governance_managed':True}})()
        reader=AsyncMock(side_effect=AssertionError('No Dot receipt read is needed for a saved Codex reply'))
        recovered=await self.modules['agentic_recovery'].recover_original(
            row['invocation_id'],config=self.config,project_gate=self.project_gate,bridge=self.bridge,
            db_path=self.db,adapter=primary,automation=automation,reader=reader)
        self.assertEqual(recovered['status'],'Pending Review')
        self.assertEqual(self.ledger.find(row['invocation_id'],self.db)['automation_run_id'],original['automation_run_id'])
        self.assertEqual(self.adapter.calls,[])
        reader.assert_not_awaited()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(CodexFlow(name) for name in CodexFlow.__dict__ if name.startswith('test_'))


if __name__=='__main__':
    unittest.main()

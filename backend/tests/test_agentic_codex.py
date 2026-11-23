"""Non-production Codex execution, real SQLite fencing and local MCP transport."""
from copy import deepcopy
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T
import test_agentic_gateway_sessions as gateway_tests


class Execution(gateway_tests.Gateway):
    # Reuse setup/helpers only; avoid inheriting the whole gateway regression suite.
    scope = gateway_tests.Gateway.scope
    kwargs = gateway_tests.Gateway.kwargs
    admitted = gateway_tests.Gateway.admitted

    def setUp(self):
        gateway_tests.Gateway.setUp(self)
        self.engine = self.load('agentic_execution_engine')
        self.codex = self.load('agentic_codex')
        self.router = self.load('agentic_execution_router')

    def start(self):
        self.engine.begin(self.row, self.path)
        self.engine.change(self.row, {'starting'}, 'running', self.path, pid=999999)

    def checkpoint(self):
        packet = self.packet()
        return {'checkpoint_id': 'verified-fixture-checkpoint', 'run_id': self.row['automation_run_id'],
                'task_revision': packet['task_revision'], 'work_packet_digest': packet['content_sha256'],
                'remaining_work': ['REQ-ONE.acceptance'], 'evidence': []}

    async def test_quota_classification_rejects_test_output_rate_auth_network_and_ordinary_errors(self):
        for code in self.codex.QUOTA_CODES:
            self.assertTrue(self.codex.quota_failure({'type': 'turn.failed', 'error': {'code': code}}))
            self.assertTrue(self.codex.quota_failure({'type': 'error', 'codex_error_info': {'type': code}}))
        self.assertTrue(self.codex.quota_failure({'type': 'error', 'message': "You've hit your usage limit. Upgrade"}))
        for value in (
            {'type': 'item.completed', 'item': {'output': 'insufficient_quota'}},
            {'type': 'turn.failed', 'error': {'code': 'rate_limit_exceeded'}},
            {'type': 'error', 'code': 'unauthorized'}, {'type': 'error', 'message': 'HTTP 429'},
            {'type': 'turn.failed', 'error': {'message': 'network failure'}},
            {'type': 'error', 'message': 'test quota_exceeded failed'},
            {'type': 'error', 'message': "You've hit your usage limit", 'code': 'authentication_error'},
        ):
            self.assertFalse(self.codex.quota_failure(value), value)

    async def test_codex_first_only_for_development_and_policy_never_changes_existing_owner(self):
        row = self.enqueue()
        self.assertIsInstance(self.router.select_adapter(None, row, {}, self.gate, self.path), self.codex.CodexAdapter)
        self.engine.set_policy(P, 'chatgpt_dot_first', [], 'manager', self.path)
        self.assertNotIsInstance(self.router.select_adapter(None, row, {}, self.gate, self.path), self.codex.CodexAdapter)
        for role in ('qa', 'project_manager', 'operations', 'solution_architect'):
            self.assertNotIsInstance(self.router.select_adapter(None, {**row, 'target_role': role}, {}, self.gate, self.path),
                                     self.codex.CodexAdapter)
        await self.admitted()
        self.start()
        self.assertIsInstance(self.router.select_adapter(None, self.row, {}, self.gate, self.path), self.codex.CodexAdapter)

    async def test_policy_validates_explicit_availability_conditions(self):
        self.assertEqual(self.engine.policy(P, self.path)['fallback_on'], ['quota'])
        for fallback in (['network_error'], ['authentication'], ['quota', 'quota'], ['test_failure']):
            with self.assertRaises(ValueError):
                self.engine.set_policy(P, 'codex_first', fallback, 'manager', self.path)
        self.engine.set_policy(P, 'codex_first', ['quota', 'cli_unavailable'], 'manager', self.path)
        self.assertIn('cli_unavailable', self.engine.policy(P, self.path)['fallback_on'])

    async def test_single_original_engine_cannot_be_claimed_twice(self):
        await self.admitted()
        self.start()
        with self.assertRaises(ValueError):
            self.engine.begin(self.row, self.path)
        original = self.engine.get(self.row['invocation_id'], self.path, private=True)
        self.assertEqual(original['run_id'], self.row['automation_run_id'])
        self.assertEqual(original['epoch'], 1)

    async def test_handoff_rejects_running_process_wrong_checkpoint_stale_fence_and_uncertain_tool(self):
        await self.admitted()
        self.start()
        with self.assertRaises(ValueError):
            self.engine.handoff(self.row, self.checkpoint(), 'quota', self.path)
        self.engine.change(self.row, {'running'}, 'exited', self.path, exit_code=1)
        for reason in ('network', 'test_failure', 'auth'):
            with self.assertRaises(ValueError):
                self.engine.handoff(self.row, self.checkpoint(), reason, self.path)
        with self.assertRaises(ValueError):
            self.engine.handoff(self.row, {**self.checkpoint(), 'run_id': 'wrong'}, 'quota', self.path)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_task_lease SET fence=fence+1')
        with self.assertRaises(ValueError):
            self.engine.handoff(self.row, self.checkpoint(), 'quota', self.path)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_task_lease SET fence=fence-1')
            db.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,NULL,?,?)',
                       (self.body['session_id'], 'unresolved-call', 'a'*64, 'repository.write', 'uncertain', 1, 1))
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            self.engine.handoff(self.row, self.checkpoint(), 'quota', self.path)

    async def test_verified_partial_work_and_receipts_survive_fenced_transfer(self):
        await self.admitted()
        self.start()
        self.body['client_session_hash'] = self.codex.client_hash(self.row['invocation_id'])
        completed = await self.m.call(self.body, **self.kwargs())
        self.assertEqual(completed['state'], 'completed')
        original = self.g.find(self.row['invocation_id'], self.path, include_private=True)
        self.engine.change(self.row, {'running'}, 'exited', self.path, exit_code=1)
        progress = self.checkpoint()
        progress['evidence'] = [completed]
        self.engine.handoff(self.row, progress, 'quota', self.path)
        # Old local MCP client loses even read/cached access after transfer.
        with self.assertRaisesRegex(ValueError, 'fenced'):
            await self.m.call(self.body, **self.kwargs())
        value = deepcopy(self.value)
        fresh = await self.m.proof(value['scope'], **self.kwargs())
        value['preflight_proof'] = fresh
        value['submission_proof'] = fresh
        await self.m.admit(value, **self.kwargs())
        self.body['client_session_hash'] = 'd'*64
        reread = await self.m.call(self.body, **self.kwargs())
        self.assertTrue(reread['replayed'])
        self.assertEqual(reread['result'], completed['result'])
        self.assertEqual(self.calls, 1)  # No repeated completed action.
        after = self.g.find(self.row['invocation_id'], self.path, include_private=True)
        for name in ('automation_run_id', 'work_packet', 'lease_token', 'lease_fence', 'submitted_at'):
            self.assertEqual(original[name], after[name])
        state = self.engine.get(self.row['invocation_id'], self.path, private=True)
        self.assertEqual(state['checkpoint'], progress)
        self.assertEqual((state['executor'], state['epoch']), ('dot', 2))
        with self.assertRaises(ValueError):
            self.engine.handoff(self.row, progress, 'quota', self.path)

    async def test_relay_cannot_admit_over_active_codex(self):
        await self.admitted()
        self.start()
        value = deepcopy(self.value)
        fresh = await self.m.proof(value['scope'], **self.kwargs())
        value['submission_proof'] = fresh
        with self.assertRaisesRegex(ValueError, 'immutable'):
            await self.m.admit(value, **self.kwargs())

    async def test_command_disables_inherited_tools_and_keeps_only_local_mcp(self):
        argv = self.codex.command({}, '/tmp/fixture', '/tmp/fixture/gateway.sock')
        self.assertIn('--ignore-user-config', argv)
        self.assertIn('read-only', argv)
        self.assertIn('features.shell_tool=false', argv)
        self.assertIn('features.apps=false', argv)
        self.assertIn('features.plugins=false', argv)
        self.assertIn('mcp_servers.abit.tools.agentic_action.approval_mode="approve"',argv)
        self.assertIn('mcp_servers.abit.required=true',argv)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)
        self.assertNotIn('--full-auto', argv)

    async def test_local_scope_uses_registry_without_contacting_relay(self):
        row = self.enqueue()
        scope = self.codex.local_scope(row, self.packet(), self.config, self.path)
        self.assertEqual(scope, self.scope(row))

    async def test_private_checkpoint_and_reply_are_not_exported(self):
        await self.admitted()
        self.start()
        self.engine.change(self.row, {'running'}, 'exited', self.path, exit_code=0)
        self.engine.change(self.row, {'exited'}, 'reply_saved', self.path,
                           record_json=json.dumps({'response_text': 'private reply'}),
                           checkpoint_json=json.dumps(self.checkpoint()))
        public = self.engine.get(self.row['invocation_id'], self.path)
        self.assertEqual(public['checkpoint_id'], self.checkpoint()['checkpoint_id'])
        for key in ('record', 'checkpoint', 'pid', 'packet_hash'):
            self.assertNotIn(key, public)

    async def test_actual_stdio_mcp_transport_and_capability_check(self):
        await self.admitted()
        self.start()
        tools = self.codex.LocalTools(self.row, self.packet(), types.SimpleNamespace(user_id='owner-fixture'),
                                     self.bridge, self.config, self.gate, self.scope(self.row), self.path)
        tools.registry = self.registry
        with tempfile.TemporaryDirectory() as directory:
            async with tools.serve(directory) as path:
                env = {**os.environ, 'ABIT_CODEX_SOCKET': str(path), 'ABIT_CODEX_CAPABILITY': tools.capability}
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(self.codex.Path(self.codex.__file__).with_name('agentic_codex_mcp.py')),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=env)
                request = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                    'name':'agentic_action','arguments':{'call_id':'stdio-real-call','operation':'read.fixture','arguments':{}}}}
                out, _ = await process.communicate((json.dumps(request)+'\n').encode())
                response = json.loads(out)
                result = json.loads(response['result']['content'][0]['text'])
                self.assertEqual(result['state'], 'completed')
                with self.assertRaises(ValueError):
                    await tools.call({'capability': 'wrong','call_id':'bad','operation':'read.fixture','arguments':{}})

    async def test_runner_joins_process_and_tools_before_exposing_quota(self):
        await self.admitted()
        self.engine.begin(self.row, self.path)
        tools = self.codex.LocalTools(self.row, self.packet(), None, self.bridge, self.config, self.gate,
                                     self.scope(self.row), self.path)
        # Real subprocess; only the external CLI provider is a controlled fixture.
        script = "import sys;sys.stdin.read();print('{\"type\":\"turn.failed\",\"error\":{\"code\":\"usage_limit_reached\"}}');sys.exit(1)"
        with patch.object(self.codex, 'command', return_value=[sys.executable, '-c', script]):
            outcome = await self.codex.run_cli(self.row, 'fixture goal', {}, tools)
        self.assertEqual(outcome['reason'], 'quota')
        self.assertTrue(tools.closing)
        self.assertFalse(tools.active)
        state = self.engine.get(self.row['invocation_id'], self.path, private=True)
        with self.assertRaises(ProcessLookupError):
            os.kill(state['pid'], 0)

    async def test_real_subprocess_test_error_and_cancellation_never_become_quota(self):
        await self.admitted()
        self.engine.begin(self.row, self.path)
        tools = self.codex.LocalTools(self.row, self.packet(), None, self.bridge, self.config, self.gate,
                                     self.scope(self.row), self.path)
        script = "import sys;sys.stdin.read();print('{\"type\":\"turn.failed\",\"error\":{\"message\":\"tests failed\"}}');sys.exit(1)"
        with patch.object(self.codex, 'command', return_value=[sys.executable, '-c', script]):
            outcome = await self.codex.run_cli(self.row, 'fixture goal', {}, tools)
        self.assertIsNone(outcome['reason'])
        self.assertEqual(outcome['exit_code'], 1)


    async def test_checkpoint_verifies_partial_source_and_reuses_only_matching_test_receipts(self):
        await self.admitted()
        self.start()
        profile = {'repository':self.packet()['repository'],'environment':'development',
                   'workspace_root':str(Path(self.temp.name)/'workspaces'),
                   'allowed_paths':['src'],'test_acceptance_ids':{'unit':['REQ-ONE.acceptance']}}
        self.config['agentic_repository_gateway']={'projects':{P:profile}}
        root=Path(profile['workspace_root'])/T/self.row['invocation_id']/'repository'
        (root/'src').mkdir(parents=True)
        (root/'src/app.py').write_text('partial implementation preserved\n')
        delivery=self.load('agentic_gateway_delivery')
        with patch.object(self.ops,'_workspace',return_value=(root,profile)):
            actual=delivery.snapshot_workspace(self.row,self.packet(),self.config)
            observed={'exit_code':0,'test_id':'unit','repository_snapshot_sha256':actual['sha256']}
            with self.m.connect(self.path) as db:
                db.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',
                           (self.body['session_id'],'already-tested-source','a'*64,'repository.test',
                            'completed',json.dumps(observed),1,1))
            progress=self.codex.save_checkpoint(self.row,self.packet(),self.config,self.path)
            self.assertEqual(progress['source_snapshot']['sha256'],actual['sha256'])
            self.assertEqual(progress['completed_acceptance_observations'],['REQ-ONE.acceptance'])
            self.assertFalse(any(item.get('test_id')=='REQ-ONE.acceptance' for item in progress['remaining_work']))
            self.assertEqual(progress['evidence'][0]['call_id'],'already-tested-source')
            self.engine.change(self.row,{'running'},'exited',self.path,exit_code=1)
            self.engine.handoff(self.row,progress,'quota',self.path)
            self.assertEqual((root/'src/app.py').read_text(),'partial implementation preserved\n')
            # Later source changes invalidate acceptance reuse without deleting the old evidence.
            (root/'src/app.py').write_text('new source requires retesting\n')
            fresh=self.codex.checkpoint(self.row,self.packet(),self.config,self.path)
            self.assertEqual(fresh['completed_acceptance_observations'],[])
            self.assertTrue(any(item.get('test_id')=='REQ-ONE.acceptance' for item in fresh['remaining_work']))

    async def test_auth_login_failure_is_not_availability_and_stops_before_execution(self):
        process=type('Process',(),{'pid':999999,'returncode':1,'wait':AsyncMock(return_value=1)})()
        with patch.object(self.codex.asyncio,'create_subprocess_exec',AsyncMock(return_value=process)):
            with self.assertRaisesRegex(ValueError,'not signed in'):
                await self.codex.verify_login({})
        self.assertIsNone(self.engine.get('unclaimed-fixture',self.path))


    async def test_cancelled_cli_is_killed_joined_and_cannot_trigger_handoff(self):
        await self.admitted()
        self.engine.begin(self.row,self.path)
        tools=self.codex.LocalTools(self.row,self.packet(),None,self.bridge,self.config,self.gate,
                                   self.scope(self.row),self.path)
        script="import sys,time;sys.stdin.read();time.sleep(30)"
        with patch.object(self.codex,'command',return_value=[sys.executable,'-c',script]):
            execution=asyncio.create_task(self.codex.run_cli(self.row,'fixture',{},tools))
            for _ in range(100):
                state=self.engine.get(self.row['invocation_id'],self.path,private=True)
                if state['pid']:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(state['pid'])
            execution.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await execution
        with self.assertRaises(ProcessLookupError):
            os.kill(state['pid'],0)
        self.assertTrue(tools.closing)
        self.assertFalse(tools.active)
        self.assertEqual(self.engine.get(self.row['invocation_id'],self.path)['executor'],'codex')

    async def test_later_auth_failure_cannot_reuse_an_earlier_quota_event(self):
        await self.admitted()
        self.engine.begin(self.row,self.path)
        tools=self.codex.LocalTools(self.row,self.packet(),None,self.bridge,self.config,self.gate,
                                   self.scope(self.row),self.path)
        events=[{'type':'error','code':'usage_limit_reached'},
                {'type':'turn.failed','error':{'code':'authentication_error'}}]
        script="import sys;sys.stdin.read();print("+repr('\n'.join(json.dumps(e) for e in events))+");sys.exit(1)"
        with patch.object(self.codex,'command',return_value=[sys.executable,'-c',script]):
            result=await self.codex.run_cli(self.row,'fixture',{},tools)
        self.assertIsNone(result['reason'])

    async def test_checkpoint_history_is_immutable_and_id_collision_is_rejected(self):
        await self.admitted()
        self.start()
        first=self.checkpoint()
        self.engine.change(self.row,{'running'},'running',self.path,checkpoint_json=json.dumps(first))
        with self.assertRaises(ValueError):
            self.engine.change(self.row,{'running'},'running',self.path,
                               checkpoint_json=json.dumps({**first,'remaining_work':[]}))
        with self.g.connect(self.path) as db:
            with self.assertRaises(Exception):
                db.execute('DELETE FROM governance_executor_checkpoint')
        self.assertEqual(self.engine.get(self.row['invocation_id'],self.path,private=True)['checkpoint'],first)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(Execution(name) for name in Execution.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()

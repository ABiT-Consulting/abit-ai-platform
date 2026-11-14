"""Real host receipt/SQLite reconciliation; native authority remains a test peer."""

from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T


class OriginalPhysicalRecovery(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway=self.load('agentic_tool_gateway');self.changes=self.load('agentic_it_changes')
        self.physical=self.load('agentic_it_physical');self.runtime_module=self.load('agentic_it_physical_runtime')
        self.it=self.load('agentic_it_execution');self.root=Path(self.temp.name);self.output=self.root/'host-once'
        self.command=(sys.executable,'-c','from pathlib import Path;import sys;Path(sys.argv[1]).open("ab").write(b"physical-once\\n")',str(self.output))
        original_packet=self.packet
        self.current=original_packet(T,'it_manager');self.current['tool_action']={'operation':'restart_service','risk':'privileged',
            'request_key':'physical-original','parameters':{'resource_id':'owned-resource'}}
        self.current['tool_scopes'].append('execute_infrastructure')
        self.plan={'schema_version':1,'kind':'it_change_plan','operation':'restart_service','resource_id':'owned-resource',
            'scope':{key:self.current[key] for key in ('project_id','task_id','repository','environment')},
            'task_revision':self.current['task_revision'],'host_id':socket.gethostname(),'policy_digest':'a'*64,
            'target':{},'baseline':{},'fixed_argv':list(self.command),'safety':{},'state':'prepared','executable':True}
        self.plan['scope']['actor']=self.current['binding'];self.plan['plan_digest']=self.changes.plan_authority_digest(self.plan)
        self.current['tool_action']['parameters']['plan_digest']=self.plan['plan_digest']
        self.current['content_sha256']=self.bridge_module.packet_digest(self.current)
        self.packet=lambda *args,**kwargs:deepcopy(self.current)
        self.row=self.running('original-physical-invocation',T,'it_manager')
        self.action=self.it.make_action(self.row,self.current)
        fingerprint=self.runtime_module.action_fingerprint(self.action)
        self.consumed={'consumed':True,'action_id':self.action['action_id'],'invocation_id':self.row['invocation_id'],
            'run_id':self.row['automation_run_id'],'approval_id':'original-human-approval','receipt_id':'original-native-consumption',
            'action_fingerprint':fingerprint,'project_id':P,'task_id':T}
        with self.gateway._connect(self.path) as db:
            db.execute('INSERT INTO governance_action VALUES(?,?,?,?,?,?,?,?,?,?)',
                (self.action['action_id'],self.row['invocation_id'],self.gateway._digest([self.action['action_id'],fingerprint]),
                 self.gateway._json(self.action),self.gateway._json(self.plan),'uncertain',self.gateway._json(self.consumed),None,1,1))
        self.handler=self.physical.PhysicalHandler('restart_service','owned-resource',P,self.current['repository'],self.current['environment'],
            socket.gethostname(),os.geteuid(),lambda plan:[self.command],lambda plan:self.output.read_bytes()==b'physical-once\n',physical_identity='original-runtime-test-file')
        self.executor=self.physical.DurablePhysicalExecutor(self.root/'host-journal',[self.handler],
            run_lookup=lambda inv:self.runtime_module.original_run(inv,self.path))
        self.runtime=self.runtime_module.PhysicalRuntime(self.changes.ChangeReaders(),self.executor)
        self.calls=[];self.match=True
        async def request(method,path,**kwargs):
            self.calls.append((method,path))
            self.assertEqual('get_action_approval',path.rsplit('.',1)[-1])
            return {'message':{'schema_version':1,'approved_by':'authorized-human@example.invalid','approval_id':self.consumed['approval_id'],'action_fingerprint':fingerprint,
                'used_by_action_id':self.action['action_id'] if self.match else 'another-original','project_id':P,'task_id':T,
                'status':'revoked' if not self.allowed else 'approved','approved_at':1,'expires_at':9999999999}}
        self.bridge=type('Bridge',(),{})();self.bridge.secret='synthetic-only';self.bridge.erp=type('ERP',(),{})();self.bridge.erp.request=request
        self.bridge.packet=AsyncMock(side_effect=lambda *args:deepcopy(self.current))

    async def execute_original(self):
        return await self.executor.execute_once(self.plan,self.action['action_id'],self.row['invocation_id'],self.command)

    async def test_real_original_receipt_recovers_under_stop_and_replay_has_no_second_host_write(self):
        receipt=await self.execute_original();self.allowed=False
        recovered=await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path)
        self.assertEqual('completed',recovered['state']);self.assertFalse(recovered['result']['new_host_execution'])
        self.assertEqual(receipt['receipt_mac'],recovered['result']['original_receipt']['receipt_mac'])
        self.assertEqual(b'physical-once\n',self.output.read_bytes())
        await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path)
        self.assertEqual(1,len(self.calls));self.assertEqual(b'physical-once\n',self.output.read_bytes())

    async def test_same_current_host_state_without_original_authenticated_receipt_never_settles(self):
        self.output.write_bytes(b'physical-once\n');self.allowed=False
        self.assertIsNone(await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path))
        self.assertEqual('uncertain',self.gateway.get_action(self.row['invocation_id'],self.action['action_id'],self.path)['state'])

    async def test_other_native_consumption_and_changed_run_proof_cannot_settle(self):
        await self.execute_original();self.match=False
        with self.assertRaisesRegex(ValueError,'consumption'):await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path)
        self.match=True
        with self.g.connect(self.path) as db:db.execute('UPDATE governance_invocation SET automation_run_id=? WHERE invocation_id=?',('different-run',self.row['invocation_id']))
        with self.assertRaisesRegex(ValueError,'consumed ERP'):await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path)

    async def test_stop_denies_new_execution_but_not_original_receipt_observation(self):
        await self.execute_original();self.allowed=False
        adapter=self.runtime.adapters(bridge=self.bridge,project_gate=self.gate,config={'agentic_tool_gateway':{'projects':{P:{'operations':['restart_service'],'host_id':socket.gethostname()}}}},db_path=self.path)['restart_service']
        with patch.object(self.changes,'prepare_change',AsyncMock(return_value=self.plan)):
            with self.assertRaisesRegex(ValueError,'revoked'):
                await adapter.execute(self.action,{'invocation_id':self.row['invocation_id']},self.plan,{})
        result=await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path)
        self.assertEqual('completed',result['state']);self.assertEqual(b'physical-once\n',self.output.read_bytes())

    async def test_serialized_runtime_is_rejected_and_unregistered_historical_actions_are_untouched(self):
        with self.assertRaisesRegex(ValueError,'internal trusted'):
            self.it.ITAdapter(None,self.gate,{},self.path,physical_runtime={})
        with self.gateway._connect(self.path) as db:
            before={**self.plan,'kind':'historical-unregistered-adapter'}
            db.execute('UPDATE governance_action SET preflight_json=? WHERE action_id=?',(self.gateway._json(before),self.action['action_id']))
        self.assertIsNone(await self.runtime.reconcile_original(self.row['invocation_id'],self.action['action_id'],bridge=self.bridge,db_path=self.path))
        self.assertFalse(self.output.exists());self.assertFalse(self.calls)

    async def test_actual_gateway_through_unix_broker_consumes_once_and_publishes_real_host_receipt(self):
        import secrets
        broker=self.load('agentic_it_broker')
        key=self.root/'transport-key';key.write_bytes(secrets.token_bytes(32));key.chmod(0o600)
        with self.gateway._connect(self.path) as db:db.execute('DELETE FROM governance_action')
        self.root.chmod(0o700)
        native_request=self.bridge.erp.request
        consumed=False
        async def request(method,path,**kwargs):
            nonlocal consumed
            if path.endswith('consume_action_approval'):
                consumed=True;self.calls.append((method,path));return {'message':deepcopy(self.consumed)}
            result=await native_request(method,path,**kwargs)
            if not consumed:result['message']['used_by_action_id']=None
            return result
        self.bridge.erp.request=request
        async def prepare(packet):return deepcopy(self.plan)
        async def execute(payload,grant):
            self.assertEqual(self.consumed['receipt_id'],grant['receipt_id'])
            return await self.executor.execute_once(payload['plan'],payload['action_id'],payload['invocation_id'],tuple(payload['plan']['fixed_argv']))
        async def observe(payload):return await self.executor.observe_original(payload['action_id'],payload['invocation_id'],payload['plan_digest'])
        server=await broker.BrokerServer(self.root/'it.sock',key,client_uid=os.geteuid(),key_owner=os.geteuid(),
            prepare=prepare,launch_original=execute,observe_original=observe).start()
        self.addAsyncCleanup(server.close)
        config={'agentic_it_physical_broker':{'enabled':True,'socket_path':str(self.root/'it.sock'),'key_file':str(key),
                   'resources':[{'operation':'restart_service','resource_id':'owned-resource'}]},
                'agentic_tool_gateway':{'projects':{P:{'host_id':socket.gethostname(),'operations':['restart_service'],'environment_class':'test'}}}}
        # The unit test has its own same-UID peer. The production factory remains
        # fixed to Root; actual Root/Core UID hardening is a separate canary.
        original=broker.BrokerClient.__init__
        def same_uid(client,*args,**kwargs):kwargs['server_uid']=os.geteuid();original(client,*args,**kwargs)
        with patch.object(broker.BrokerClient,'__init__',same_uid):
            runtime=broker.configured_runtime(config,bridge=self.bridge,project_gate=self.gate,db_path=self.path)
        adapters=runtime.adapters(bridge=self.bridge,project_gate=self.gate,config=config,db_path=self.path)
        first=await self.gateway.execute_action(self.row['invocation_id'],self.action,config=config,project_gate=self.gate,
            bridge=self.bridge,adapters=adapters,db_path=self.path)
        second=await self.gateway.execute_action(self.row['invocation_id'],self.action,config=config,project_gate=self.gate,
            bridge=self.bridge,adapters=adapters,db_path=self.path)
        self.assertEqual('completed',first['state']);self.assertEqual(first,second)
        self.assertEqual(1,sum(path.endswith('consume_action_approval') for _,path in self.calls))
        self.assertEqual(b'physical-once\n',self.output.read_bytes())

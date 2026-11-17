"""Actual child fanout ledger plus a CAS/lost-ACK native binding boundary."""

from copy import deepcopy
import json
import types

from test_agentic_controls_events import Fixture, P, T, SECOND


class FreshITChildBinding(Fixture):
    def setUp(self):
        super().setUp()
        self.module=self.load('agentic_it_plan_binding');self.changes=self.load('agentic_it_changes')
        self.orchestrator=self.load('agentic_orchestrator')
        self.original=self.packet(SECOND,'it_manager');self.original['task_revision']='a'*64
        self.original['parent_task']=T
        self.original['tool_action']={'operation':'restart_service','risk':'privileged','request_key':'child-physical-request','parameters':{'resource_id':'owned-resource'}}
        self.original['content_sha256']=self.bridge_module.packet_digest(self.original)
        self.current=deepcopy(self.original);self.receipt={'exists':False};self.posts=0;self.preparations=0;self.lose_ack=False
        self.plan={'schema_version':1,'kind':'it_change_plan','operation':'restart_service','resource_id':'owned-resource',
            'task_revision':self.original['task_revision'],'scope':{'project_id':P,'task_id':SECOND},
            'state':'prepared','executable':True,'target':{},'baseline':{},'fixed_argv':[], 'safety':{}}
        self.plan['plan_digest']=self.changes.plan_authority_digest(self.plan)
        async def prepare(packet,**kwargs):self.preparations+=1;return deepcopy(self.plan)
        self.runtime=types.SimpleNamespace(prepare_binding=prepare)
        async def request(method,path,**kwargs):
            if method=='GET':return {'message':deepcopy(self.receipt)}
            self.assertEqual('bind_it_plan',path.rsplit('.',1)[-1]);self.posts+=1
            self.assertEqual(self.original['task_revision'],kwargs['json']['expected_task_revision'])
            self.current['tool_action']['parameters']['plan_digest']=self.plan['plan_digest']
            self.current['task_revision']='b'*64;self.current['source_modified']='native-bound-modified'
            self.current['content_sha256']=self.bridge_module.packet_digest(self.current)
            self.receipt={'exists':True,'durable':True,'plan_digest':self.plan['plan_digest'],
                'original_task_revision':self.original['task_revision'],'bound_task_revision':self.current['task_revision'],
                'modified':self.current['source_modified']}
            if self.lose_ack:raise TimeoutError('original native CAS committed, response lost')
            return {'message':deepcopy(self.receipt)}
        async def packet(*args):return deepcopy(self.current)
        self.bridge=types.SimpleNamespace(erp=types.SimpleNamespace(request=request),packet=packet,secret='synthetic-only')

    async def bind(self,packet=None):
        return await self.module.bind_child_plan(packet or self.original,bridge=self.bridge,config={},project_gate=self.gate,db_path=self.path,runtime=self.runtime)

    async def test_bind_before_single_original_fanout_key_and_duplicate_scan_has_no_second_post(self):
        source=self.running('developer-original-parent',T,'developer')
        self.g.finish_execution(source['invocation_id'],source['automation_run_id'],source['lease_token'],db_path=self.path)
        fresh=await self.bind()
        first,created=self.orchestrator._fanout(source,fresh,[SECOND,T],self.gate_value,self.path,purpose='Released action child acceptance work')
        self.assertTrue(created);self.assertEqual('b'*64,first['task_revision'])
        again=await self.bind(fresh)
        second,created=self.orchestrator._fanout(source,again,[SECOND,T],self.gate_value,self.path,purpose='Released action child acceptance work')
        self.assertFalse(created);self.assertEqual(first['invocation_id'],second['invocation_id'])
        self.assertEqual((1,1),(self.posts,self.preparations))
        with self.g.connect(self.path) as db:self.assertEqual(1,db.execute('SELECT count(*) FROM governance_invocation WHERE erp_task=?',(SECOND,)).fetchone()[0])

    async def test_unknown_cas_ack_is_get_only_recovered_before_any_enqueue(self):
        self.lose_ack=True
        fresh=await self.bind();self.assertEqual(self.plan['plan_digest'],fresh['tool_action']['parameters']['plan_digest'])
        self.assertEqual(1,self.posts)

    async def test_existing_digest_without_trusted_native_receipt_is_never_adopted(self):
        packet=deepcopy(self.original);packet['tool_action']['parameters']['plan_digest']='c'*64
        with self.assertRaisesRegex(ValueError,'original server plan'):await self.bind(packet)
        self.assertEqual((0,0),(self.posts,self.preparations))

    async def test_unresolved_ack_or_other_business_change_never_enqueues(self):
        original=self.bridge.erp.request
        async def fail(method,path,**kwargs):
            if method=='POST':raise TimeoutError('no positive commit receipt')
            return await original(method,path,**kwargs)
        self.bridge.erp.request=fail
        with self.assertRaisesRegex(ValueError,'unresolved'):await self.bind()
        with self.g.connect(self.path) as db:self.assertEqual(0,db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0])

    async def test_after_cas_changed_acceptance_denies_dispatch(self):
        original=self.bridge.erp.request
        async def changed(method,path,**kwargs):
            result=await original(method,path,**kwargs)
            if method=='POST':self.current['objective']='Changed while binding'
            return result
        self.bridge.erp.request=changed
        with self.assertRaisesRegex(ValueError,'changed during'):await self.bind()
        self.assertEqual(1,self.posts)

    async def test_any_original_core_invocation_history_prevents_automatic_replanning(self):
        self.g.enqueue(invocation_id='old-unsubmitted-it-original',project=P,task=SECOND,from_role='project_manager',target_role='it_manager',
            task_revision=self.original['task_revision'],context='Old original must be reconciled',work_packet=self.original,
            actor='server-fixture',gate=self.gate_value,db_path=self.path)
        with self.assertRaisesRegex(ValueError,'Existing IT invocation history'):await self.bind()
        self.assertEqual((0,0),(self.posts,self.preparations))

"""Actual Core SQLite/artifacts/handoffs with a strict controlled native peer.

Native authorization/File readers have their separate ERP repository tests.
This peer provides only fixed protocol receipts and never contacts a real ERP.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import types
from unittest.mock import AsyncMock,patch
from test_agentic_controls_events import Fixture,P,T

class CoreOperations(Fixture):
    def setUp(self):
        super().setUp()
        self.ops=self.load('agentic_operations');self.qa=self.load('agentic_operations_qa')
        self.row=self.running('operations-original-controlled',T,'operations');self.packet_ops=self.packet(T,'operations')
        self.root=Path(self.temp.name)/'artifacts';artifacts=self.load('agentic_artifacts')
        selector=patch.object(artifacts,'artifact_directory',side_effect=lambda row,config:self.root/row['invocation_id'])
        selector.start();self.addCleanup(selector.stop)
        packet=self.packet_ops
        self.snapshot={'project_id':P,'task_id':T,'task_revision':packet['task_revision'],'repository':packet['repository'],'environment':packet['environment'],
            'srs_revision':packet['srs']['revision'],'srs_sha256':packet['srs']['sha256'],'project_status':'Open',
            'governance_enabled':True,'pm_task_automation_enabled':True,
            'bindings':[{'role':role,'agent_id':self.g.agent_id(P,role),'erp_user':user,'verification_state':'Verified','is_enabled':1} for role,user in self.users.items()]}
        self.calls=[];self.receipt=None;self.lose_ack=False;self.current=True;self.qa_proof=None
        async def request(method,path,**kwargs):
            operation=path.rsplit('.',1)[-1];args=kwargs.get('json') if method=='POST' else kwargs.get('params')
            self.calls.append((method,operation,deepcopy(args)))
            if method=='POST' and operation=='record_controls_audit':
                if self.receipt is None:
                    body={'schema_version':1,'policy_id':self.ops.POLICY,'project_id':P,'task_id':T,'task_revision':packet['task_revision'],
                        'run_id':self.row['automation_run_id'],'invocation_id':self.row['invocation_id'],'agent_id':self.row['agent_id'],
                        'erp_user':packet['binding']['erp_user'],'request_id':args['request_id'],'policy_sha256':'b'*64,
                        'snapshot':deepcopy(self.snapshot),'snapshot_sha256':self.bridge_module.digest(self.snapshot),'checked_at':int(time.time()),
                        'business_document_mutation':False,'external_communication':False,'host_execution':False}
                    self.receipt={'exists':True,'durable':True,'record':'controlled-native-original','content_sha256':self.bridge_module.digest(body),**body}
                if self.lose_ack:self.lose_ack=False;raise TimeoutError('Synthetic committed original; ACK lost')
                return {'message':deepcopy(self.receipt)}
            if method=='GET' and operation=='get_controls_audit_receipt':return {'message':deepcopy(self.receipt)}
            if method=='GET' and operation=='get_controls_audit_current':
                if not self.current:raise ValueError('Controlled native current controls differ')
                return {'message':{'verified':True,'record':self.receipt['record'],'content_sha256':self.receipt['content_sha256'],'snapshot_sha256':self.receipt['snapshot_sha256']}}
            if method=='GET' and operation=='get_controls_audit_for_qa':
                if not self.current:raise ValueError('Controlled native current QA authority differs')
                return {'message':deepcopy(self.qa_proof)}
            raise AssertionError('Unexpected native operation')
        self.bridge=types.SimpleNamespace(secret='synthetic-only',erp=types.SimpleNamespace(request=request))
        self.config={};self.authority=AsyncMock()

    async def execute(self,args=None):
        return await self.ops.record_controls_audit({} if args is None else args,row=self.row,packet=self.packet_ops,config=self.config,bridge=self.bridge,
            db_path=self.path,gateway_call_id='operations-original-gateway-call',check_authority=self.authority)

    async def test_actual_receipt_bytes_and_readiness_require_original_connector(self):
        result=await self.execute()
        self.assertTrue(await self.ops.assert_operations_ready(self.row,self.packet_ops,{'outcome':'ready_for_review','evidence':[result['evidence']]},self.config,self.path,bridge=self.bridge))
        self.assertEqual(1,sum(method=='POST' for method,_,_ in self.calls));self.assertEqual(2,self.authority.await_count)
        with self.g.connect(self.path) as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM governance_operations_execution').fetchone()[0])
            with self.assertRaises(Exception):db.execute('DELETE FROM governance_operations_execution')

    async def test_unknown_post_ack_reconciles_exact_original_get_without_second_post(self):
        self.lose_ack=True;result=await self.execute();self.assertTrue(result['completed'])
        self.assertEqual([('POST','record_controls_audit'),('GET','get_controls_audit_receipt')],[(method,name) for method,name,_ in self.calls])

    async def test_model_selected_target_or_model_only_ready_has_no_authority(self):
        with self.assertRaisesRegex(ValueError,'actual scoped'):await self.execute({'command':'arbitrary command'})
        with self.assertRaisesRegex(ValueError,'actual named connector'):
            await self.ops.assert_operations_ready(self.row,self.packet_ops,{'outcome':'ready_for_review','evidence':[]},self.config,self.path,bridge=self.bridge)
        self.assertFalse(self.calls)

    async def test_current_native_rejection_and_mutated_evidence_close_readiness(self):
        result=await self.execute();contract={'outcome':'ready_for_review','evidence':[result['evidence']]};self.current=False
        with self.assertRaisesRegex(ValueError,'current controls differ'):
            await self.ops.assert_operations_ready(self.row,self.packet_ops,contract,self.config,self.path,bridge=self.bridge)
        self.current=True;path=self.root/self.row['invocation_id']/result['evidence']['uri'].removeprefix('artifact://');path.write_bytes(b'mutated actual evidence')
        with self.assertRaisesRegex(ValueError,'actual evidence bytes changed'):
            await self.ops.assert_operations_ready(self.row,self.packet_ops,contract,self.config,self.path,bridge=self.bridge)

    async def handoff(self,result):
        source=self.row
        contract={'outcome':'ready_for_review','evidence':[result['evidence']]}
        event={'event_id':'operational-original-ready','run_id':source['automation_run_id'],'task_id':T,'invocation_id':source['invocation_id'],'result_contract':contract}
        receipt={'event_id':event['event_id'],'run_id':source['automation_run_id'],'task_id':T,'modified':'native-ready','status':'Pending Review','durable':True}
        store=self.load('agentic_result_store');store.save_result(source['invocation_id'],source['automation_run_id'],contract,event,self.path)
        store.publication_receipt(source['invocation_id'],receipt,self.path)
        self.g.finish_execution(source['invocation_id'],source['automation_run_id'],source['lease_token'],db_path=self.path)
        packet=self.packet(T,'qa');packet['implementation_run_id']=source['automation_run_id'];packet['content_sha256']=self.bridge_module.packet_digest(packet)
        self.g.handoff(source_invocation_id=source['invocation_id'],source_run_id=source['automation_run_id'],source_lease_token=source['lease_token'],
            invocation_id='qa-operational-original',project=P,task=T,from_role='operations',target_role='qa',task_revision=packet['task_revision'],context='Independent current operational read',
            work_packet=packet,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        row=self.g.claim_invocation('qa-operational-original','qa-operational-original-run',gate=self.gate_value,db_path=self.path)
        self.g.mark_submitted(row['invocation_id'],row['automation_run_id'],row['lease_token'],gate=self.gate_value,db_path=self.path)
        row=self.g.find(row['invocation_id'],self.path,include_private=True)
        self.qa_proof={'schema_version':1,'policy_id':self.ops.POLICY,'verified':True,'project_id':P,'task_id':T,'task_revision':packet['task_revision'],
            'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],'qa_agent_id':row['agent_id'],'qa_erp_user':packet['binding']['erp_user'],
            'implementation_run_id':source['automation_run_id'],'implementation_invocation_id':source['invocation_id'],'implementation_agent_id':source['agent_id'],
            'implementation_erp_user':self.packet_ops['binding']['erp_user'],'ready_event_id':event['event_id'],'ready_result_sha256':self.bridge_module.digest(event),
            'ready_receipt_sha256':self.bridge_module.digest(receipt),'source_file_sha256':result['evidence']['sha256'],
            'operational_record':self.receipt['record'],'operational_content_sha256':self.receipt['content_sha256'],
            'current_snapshot':self.receipt['snapshot'],'current_snapshot_sha256':self.receipt['snapshot_sha256'],
            'business_document_mutation':False,'external_communication':False,'host_execution':False}
        return row,packet

    async def test_actual_core_ops_and_independent_qa_use_real_acceptance_hooks(self):
        delivery=self.load('agentic_gateway_delivery');result=await self.execute()
        await delivery.assert_gateway_result(self.row,self.packet_ops,{'outcome':'ready_for_review','evidence':[result['evidence']]},self.config,self.path,bridge=self.bridge)
        row,packet=await self.handoff(result)
        proof=await self.qa.verify_original({},row=row,packet=packet,config=self.config,bridge=self.bridge,db_path=self.path,gateway_call_id='qa-original-read',check_authority=AsyncMock())
        await delivery.assert_gateway_result(row,packet,{'outcome':'pass','evidence':[proof['evidence']],
            'test_results':[{'status':'pass','evidence_ids':[proof['evidence']['id']]}]},self.config,self.path,bridge=self.bridge)
        self.assertNotEqual(proof['evidence']['id'],result['evidence']['id']);self.assertEqual(1,sum(method=='POST' for method,_,_ in self.calls))

    async def test_wrong_native_source_or_implementer_only_evidence_cannot_pass(self):
        result=await self.execute();row,packet=await self.handoff(result)
        with self.assertRaisesRegex(ValueError,'own actual typed'):
            await self.qa.assert_operations_qa_result(row,packet,{'outcome':'pass','evidence':[result['evidence']]},self.config,self.path,bridge=self.bridge)
        self.qa_proof['implementation_invocation_id']='model-selected-source'
        with self.assertRaisesRegex(ValueError,'provenance differs'):
            await self.qa.verify_original({},row=row,packet=packet,config=self.config,bridge=self.bridge,db_path=self.path,gateway_call_id='qa-denied-read',check_authority=AsyncMock())

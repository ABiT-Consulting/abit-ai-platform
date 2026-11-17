"""Real scoped registry/session, QA/start journals and PM artifact publication.

Native/provider boundaries are controlled peers. No real key/account/provider or
production record is accessed; ordinary planning authority is never granted.
"""
from copy import deepcopy
import base64
import hashlib
import importlib
import json
import unittest
# Keep protocol exception identities outside the fixture's sys.modules restore.
# Otherwise httpx's cached map can retain removed httpcore exception classes.
import httpcore
import httpx
from unittest.mock import AsyncMock, patch
import test_agentic_pm_review as fixture

P,T=fixture.PROJECT,fixture.TASK

class PMReviewEvidence(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.base=fixture.PMReview(methodName='runTest');await self.base.asyncSetUp();self.addCleanup(self.base.doCleanups)
        self.f=self.base.f;self.bridge=self.f.bridge;self.b=self.f.bridge_module
        packet=self.f.erp.packet
        def only_read(role):
            value=packet(role)
            if role=='project_manager' and value.get('qa_review'):
                value['tool_scopes']=['read_project'];value['content_sha256']=self.b.packet_digest(value)
            return value
        self.f.erp.packet=only_read
        self.native_start=True;self.native_changed=False;request=self.f.erp.request
        async def native(method,path,**kwargs):
            if method=='GET' and path.endswith('get_result_receipt'):
                event_id=kwargs['params']['event_id']
                event=next((item for item in self.base.pm_events if item['event_id']==event_id),None)
                if event:
                    if not self.native_start:return {'message':{'exists':False}}
                    receipt=deepcopy(self.f.erp.receipts[event_id]);receipt.update(exists=True,content_sha256=self.b.digest(event))
                    if self.native_changed:receipt['artifact_target']={'doctype':'AI Governance Record','name':'AGOV-other-invocation'}
                    return {'message':receipt}
            return await request(method,path,**kwargs)
        self.f.erp.request=native
        self.sessions=importlib.import_module('open_webui.utils.agentic_gateway_sessions')
        self.ops=importlib.import_module('open_webui.utils.agentic_gateway_operations')
        self.handler=importlib.import_module('open_webui.utils.agentic_gateway_pm_review')
        key=patch.object(self.sessions,'edge_secret',return_value='controlled-only-edge-key-value-000000000001');key.start();self.addCleanup(key.stop)
        self.f.config['agentic_action_gateway']={'owners':{'verified-owner':{'account_id':'a'*64,'dot_id':'controlled-dot','mcp_connection_hash':'b'*64}}}
        self.bridge.gateway_boundary=AsyncMock(side_effect=lambda scope:{'schema_version':1,'project_id':scope['project_id'],'task_id':scope['task_id'],
            'role':scope['role'],'agent_id':scope['agent_id'],'boundary':self.sessions.BOUNDARY,'managed_resource_guards':True,
            'pre_filesystem_file_guard':True,'ordinary_owner_plugins_fenced':False})
        await self.base.approved();self.queued=await self.base.ensure()
        self.original=self.f.ledger.find(self.queued['invocation_id'],self.f.db,include_private=True)
        self.packet=json.loads(self.original['work_packet']);self.assertEqual(['read_project'],self.packet['tool_scopes'])
        run=self.b.stable_run_id(self.original['invocation_id'])
        from open_webui.utils.abitdot_primary import dot_primary_key
        job='aibit_'+hashlib.sha256(('primary:'+dot_primary_key('verified-owner',self.original['agent_id'],run)).encode()).hexdigest()[:48]
        self.scope={'owner':'verified-owner','account_id':'a'*64,'dot_id':'controlled-dot','project_id':P,'task_id':T,'role':'project_manager',
            'agent_id':self.original['agent_id'],'erp_user':self.packet['binding']['erp_user'],'repository':self.packet['repository'],
            'environment':self.packet['environment'],'tool_scopes':['read_project'],'work_packet_digest':self.b.packet_digest(self.packet),
            'invocation_id':self.original['invocation_id'],'run_id':run,'job_id':job}
        self.registry=self.ops.registry('project_manager')
        self.arguments={'name':'pm-qa-review.txt','content_base64':base64.b64encode(b'Independent review of the exact QA approval and evidence.\n').decode()}
        self.kwargs=dict(bridge=self.bridge,project_gate=self.f.project_gate,config=self.f.config,registry=self.registry,db_path=self.f.db)
        self.initial=await self.sessions.proof(self.scope,**self.kwargs)
        self.body=None;self.callback=None;self.observed=None;self.callback_error=None
        async def submit(row,packet,run_id,automation,bridge,*,before_submit):
            final=await self.sessions.proof(self.scope,**self.kwargs)
            await before_submit()
            envelope={'protocol_version':1,'scope':self.scope,'preflight_proof':self.initial,'submission_proof':final}
            await self.sessions.admit(envelope,**self.kwargs)
            self.body={'connection_hash':'b'*64,'client_session_hash':'c'*64,'session_id':final['gateway_session_id'],
                'call_id':'pm-review-original-evidence','operation':'pm_review.write','arguments':deepcopy(self.arguments)}
            self.current=row;self.current_packet=packet
            try:value=await self.callback()
            except BaseException as error:self.callback_error=error;raise
            if value is None:return None
            evidence={'id':'PM-REVIEW-OWN','kind':'test_log','uri':value['uri'],'sha256':value['sha256'],'requirement_ids':packet['requirement_ids']}
            result={'schema_version':1,'invocation_id':row['invocation_id'],'actor':packet['binding'],'project_id':P,'task_id':T,
                'task_revision':packet['task_revision'],'work_packet_digest':self.b.packet_digest(packet),'outcome':'reviewed',
                'summary':'Read the original QA approval and recorded own scoped review evidence.',
                'implementation_invocation_id':packet['qa_review']['implementation_invocation_id'],
                'implementation_agent_id':packet['qa_review']['implementation_agent_id'],'qa_review_ack':self.b.digest(packet['qa_review']),
                'test_results':[{'test_id':'PM-QA-REVIEW','status':'pass','observed':'Original QA approval read back; current evidence retained.',
                    'evidence_ids':['PM-REVIEW-OWN']}],'evidence':[evidence],'defects':[]}
            self.f.returned_results[row['invocation_id']]=deepcopy(result)
            return {'response_text':'AGOV RESULT: '+json.dumps(result)}
        self.f.adapter.submit=submit

    async def run_original(self,callback):
        self.callback=callback
        receipt=await self.f.execute(self.original['invocation_id'])
        if self.callback_error:raise self.callback_error
        return receipt

    def own_file(self):return self.f.artifact_root(self.original)/self.arguments['name']

    async def call(self,**changes):return await self.sessions.call({**self.body,**changes},**self.kwargs)

    async def direct(self,*,row=None,packet=None,args=None):
        return await self.handler.write(self.arguments if args is None else args,row=row or self.current,packet=packet or self.current_packet,
            config=self.f.config,bridge=self.bridge,db_path=self.f.db,check_authority=AsyncMock())

    async def test_actual_read_project_only_gateway_bytes_publish_and_close_without_feature_mutation(self):
        before=deepcopy(self.f.erp.task);task_files={k for k,v in self.f.erp.files.items() if v['attached_to_doctype']=='Task'}
        async def operation():
            response=await self.call();self.assertEqual('completed',response['state'])
            duplicate=await self.call();self.assertTrue(duplicate['replayed']);self.assertEqual(response['result'],duplicate['result'])
            self.observed=response['result'];return response['result']
        receipt=await self.run_original(operation)
        self.assertIsNotNone(receipt);self.assertTrue(self.base.pm.finish_review(self.original['invocation_id'],receipt,self.f.db))
        self.assertTrue(self.base.pm.review_complete(P,T,self.f.db));self.assertEqual(before,self.f.erp.task)
        self.assertEqual(task_files,{k for k,v in self.f.erp.files.items() if v['attached_to_doctype']=='Task'})
        self.assertEqual(hashlib.sha256(self.own_file().read_bytes()).hexdigest(),self.observed['sha256'])
        self.assertEqual(1,self.base.evidence_posts);self.assertEqual(0,self.base.generic_pm_uploads)
        with self.f.ledger.connect(self.f.db) as db:
            self.assertEqual(1,db.execute("SELECT count(*) FROM governance_gateway_call WHERE operation='pm_review.write'").fetchone()[0])

    async def test_no_general_artifact_repository_or_task_planning_grant(self):
        async def operation():
            for index,name in enumerate(['artifact.write','repository.prepare','intent.propose_tasks','intent.request_agent_invocation']):
                with self.assertRaisesRegex(ValueError,'not been granted'):
                    await self.call(operation=name,call_id='denied-general-operation-'+str(index))
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())
        self.assertEqual('read_project',self.registry['pm_review.write'].capability)
        self.assertTrue(self.registry['pm_review.write'].requires_authority_callback)
        self.assertNotIn('pm_review.write',self.ops.registry('developer'))

    async def test_forged_review_other_role_other_invocation_and_extra_arguments_denied(self):
        async def operation():
            forged=deepcopy(self.current_packet);forged['qa_review']['qa_result_sha256']='f'*64
            for row,packet,args in [
                (self.current,forged,None),({**self.current,'target_role':'developer'},self.current_packet,None),
                ({**self.current,'invocation_id':'unregistered-other-invocation'},self.current_packet,None),
                (self.current,self.current_packet,{**self.arguments,'task_id':'other-task'})]:
                with self.assertRaises((ValueError,KeyError)):await self.direct(row=row,packet=packet,args=args)
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())

    async def test_missing_or_changed_native_original_start_denied_before_bytes(self):
        async def operation():
            self.native_start=False
            with self.assertRaisesRegex(ValueError,'Native PM review start journal'):await self.direct()
            self.native_start=True;self.native_changed=True
            with self.assertRaisesRegex(ValueError,'artifact|receipt|target'):await self.direct()
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())

    async def test_missing_registered_review_turn_is_not_replaced_by_packet_flag(self):
        async def operation():
            with self.f.ledger.connect(self.f.db) as db:
                db.execute('DELETE FROM governance_pm_review_turn WHERE review_invocation_id=?',(self.current['invocation_id'],))
            with self.assertRaisesRegex(ValueError,'registered coordination turn'):await self.direct()
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())

    async def test_project_stop_denies_new_review_bytes(self):
        async def operation():
            self.f.ledger.project_control(P,'stop',actor='human',policy_revision='current-stop',db_path=self.f.db)
            with self.assertRaisesRegex(ValueError,'paused, stopped|cancel|active'):await self.direct()
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())

    async def test_saved_original_result_closes_evidence_before_bytes(self):
        async def operation():
            store=self.f.modules['agentic_result_store']
            store.save_local_result(self.current['invocation_id'],self.current['automation_run_id'],{'summary':'Known controlled original reply'},self.f.db)
            with self.assertRaisesRegex(ValueError,'closes after the original model result'):await self.direct()
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())

    async def test_unverified_qa_source_cannot_use_a_valid_looking_packet(self):
        async def operation():
            with self.f.ledger.connect(self.f.db) as db:
                db.execute("UPDATE governance_invocation SET state='execution_finished' WHERE invocation_id=?",(self.current_packet['qa_review']['qa_invocation_id'],))
            with self.assertRaisesRegex(ValueError,'verified independent QA'):await self.direct()
            return None
        await self.run_original(operation);self.assertFalse(self.own_file().exists())

"""Actual temp ledger, source continuation chain, loopback Git journal protocol.
No live service, ERP, external GitHub or model send is performed.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import base64
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
ROOT=BACKEND/'open_webui/utils';TESTS=BACKEND/'tests';sys.path.insert(0,str(TESTS))
import test_agentic_gateway_delivery as fixture_delivery


class OriginalDeliveryContinuation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f=fixture_delivery.Delivery('test_real_files_git_objects_and_draft_PR_complete_once_with_exact_evidence')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.utils.__path__.insert(0,str(HERE))
        def candidate_load(f,name):
            path=HERE/(name+'.py')
            if not path.is_file():path=ROOT/(name+'.py')
            full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,path);module=importlib.util.module_from_spec(spec);sys.modules[full]=module;setattr(f.utils,name,module);spec.loader.exec_module(module);return module
        self.f.load=types.MethodType(candidate_load,self.f)
        await self.f.asyncSetUp();self.m=self.f.m;self.g=self.f.g;self.path=self.f.path;self.b=self.f.bridge_module
        self.store=self.f.load('agentic_result_store');self.orchestrator=self.f.load('agentic_orchestrator')
        self.g.mark_submitted(self.f.row['invocation_id'],self.f.row['automation_run_id'],self.f.row['lease_token'],gate=self.f.gate_value,db_path=self.path)
        self.original=self.g.find(self.f.row['invocation_id'],self.path,include_private=True);self.f.row=self.original;self.original_packet=deepcopy(self.f.packet_value)
        self.raw_result_hashes=[]

    async def prepare_original(self):
        self.f.remote.drop='/git/commits';held=await self.f.deliver();self.assertEqual(held['state'],'pending_reconciliation')
        self.assertEqual(self.f.remote.posts().count('/git/commits'),1);before=self.f.remote.posts().copy();status=await self.f.reconcile();self.assertEqual(status['state'],'reconciled');self.assertEqual(self.f.remote.posts(),before)
        with self.m._db(self.path) as db:
            row=db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',(self.original['invocation_id'],)).fetchone();self.original_input=json.loads(row['input_json']);self.assertEqual(row['state'],'prepared')
            self.original_steps=[dict(x) for x in db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? ORDER BY step_key',(self.original['invocation_id'],))]
        return status

    async def continue_original(self,row,packet,*,outcome='continue',kind='continue'):
        result={'schema_version':1,'invocation_id':row['invocation_id'],'actor':deepcopy(packet['binding']),'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],'work_packet_digest':packet['content_sha256'],'outcome':outcome,'summary':'Finish the exact original prepared Git delivery without replaying prior mutations.','test_results':[],'evidence':[],'implementation_invocation_id':None,'implementation_agent_id':None,'defects':[]}
        event={'schema_version':1,'event_id':'agov-result-'+self.b.digest([row['invocation_id'],result])[:48],'type':'run.checkpoint' if outcome=='continue' else 'run.blocked','project_id':row['erp_project'],'task_id':row['erp_task'],'run_id':row['automation_run_id'],'invocation_id':row['invocation_id'],'role':'developer','agent_id':row['agent_id'],'work_packet_digest':packet['content_sha256'],'task_revision':packet['task_revision'],'result_contract':result,'artifacts':[],'acceptance_passed':False}
        self.store.save_result(row['invocation_id'],row['automation_run_id'],result,event,self.path);self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'execution_finished',db_path=self.path)
        self.store.publication_receipt(row['invocation_id'],{'event_id':event['event_id'],'task_id':row['erp_task'],'run_id':row['automation_run_id'],'modified':'fixture-checkpoint-'+row['invocation_id'],'status':'Working' if outcome=='continue' else 'Blocked','durable':True},self.path)
        source=self.g.find(row['invocation_id'],self.path,include_private=True)
        self.last_checkpoint=source
        successor,created=self.orchestrator._resume(source,packet,self.f.gate_value,self.path,[],kind,delivery_config=self.f.config if kind=='delivery' else None);self.assertTrue(created)
        run=self.b.stable_run_id(successor['invocation_id']);current=self.g.claim_invocation(successor['invocation_id'],run,gate=self.f.gate_value,db_path=self.path);self.g.mark_submitted(current['invocation_id'],run,current['lease_token'],gate=self.f.gate_value,db_path=self.path)
        return self.g.find(current['invocation_id'],self.path,include_private=True)

    async def current_chain(self):
        await self.prepare_original()
        first=await self.continue_original(self.original,self.original_packet)
        self.current=await self.continue_original(first,self.original_packet)
        self.current_packet=deepcopy(self.original_packet)
        self.f.row=self.current;self.f.packet_value=self.current_packet
        return self.current

    async def authority(self):
        self.authority_checks=getattr(self,'authority_checks',0)+1
        if getattr(self,'revoke_next',False):raise ValueError('Controlled current authority revoked')
        fresh=self.g.find(self.current['invocation_id'],self.path,include_private=True)
        if not fresh or fresh['state'] not in {'running','uncertain'}:raise ValueError('Current invocation is not active')
        with self.g.connect(self.path)as db:
            self.g._gate(db,fresh['erp_project'],self.f.gate_value);self.g._task_gate(db,fresh['erp_project'],fresh['erp_task'])
        return fresh,json.loads(fresh['work_packet'])

    async def status(self):
        return await self.m.reconcile_delivery({},row=self.current,packet=self.current_packet,config=self.f.config,db_path=self.path,check_authority=self.authority)

    async def resume(self,args=None):
        return await self.m.developer_deliver(args if args is not None else {'test_call_ids':['fixture-test-call-001']},row=self.current,packet=self.current_packet,config=self.f.config,db_path=self.path,check_authority=self.authority)

    def contract(self,ready):
        row=self.current;packet=self.current_packet
        return {'schema_version':1,'invocation_id':row['invocation_id'],'actor':deepcopy(packet['binding']),'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],'work_packet_digest':packet['content_sha256'],'outcome':'ready_for_review','summary':'The exact original tested implementation and draft PR are now verified.','test_results':deepcopy(ready['test_results']),'evidence':deepcopy(ready['evidence']),'delivery':deepcopy(ready['delivery']),'implementation_invocation_id':None,'implementation_agent_id':None,'defects':[]}

    async def test_two_published_continues_resume_same_journals_and_copy_exact_evidence(self):
        await self.current_chain();owner=self.m.original_delivery(self.current,self.current_packet,self.f.config,self.path)
        self.assertEqual(owner['row']['invocation_id'],self.original['invocation_id']);self.assertEqual(len(owner['chain']),2)
        posts=self.f.remote.posts().copy();status=await self.status();self.assertEqual(self.f.remote.posts(),posts)
        self.assertEqual(status['next_operation'],'repository.deliver');self.assertEqual(status['arguments'],{'test_call_ids':['fixture-test-call-001']})
        ready=await self.resume(status['arguments']);self.assertEqual(ready['state'],'ready_for_review');self.assertEqual(ready['invocation_id'],self.current['invocation_id']);self.assertEqual(ready['original_invocation_id'],self.original['invocation_id'])
        self.assertEqual(self.f.remote.posts().count('/git/commits'),1);self.assertEqual(self.f.remote.posts().count('/git/refs'),1);self.assertEqual(self.f.remote.posts().count('/pulls'),1)
        for item in ready['evidence']:
            _,old=self.m.local_bytes(self.m.artifact_directory(self.original,self.f.config),item['uri']);_,new=self.m.local_bytes(self.m.artifact_directory(self.current,self.f.config),item['uri']);self.assertEqual(new,old);self.assertEqual(hashlib.sha256(new).hexdigest(),item['sha256'])
        before=self.f.remote.posts().copy();self.assertEqual(await self.resume(),ready);self.assertEqual(self.f.remote.posts(),before)
        with self.m._db(self.path) as db:
            self.assertEqual({r[0] for r in db.execute('SELECT invocation_id FROM governance_developer_delivery')},{self.original['invocation_id'],self.current['invocation_id']});self.assertEqual(db.execute('SELECT count(*) FROM governance_developer_delivery').fetchone()[0],2)
            imported=db.execute('SELECT input_json,result_json FROM governance_developer_delivery WHERE invocation_id=?',(self.current['invocation_id'],)).fetchone()
            self.assertEqual(json.loads(imported['input_json'])['delivery_continuation']['origin_invocation_id'],self.original['invocation_id'])
            self.assertEqual(json.loads(imported['result_json']),ready)
            frozen=db.execute('SELECT input_json FROM governance_developer_delivery WHERE invocation_id=?',(self.original['invocation_id'],)).fetchone()[0];self.assertEqual(json.loads(frozen),self.original_input)
            for step in self.original_steps:
                current=dict(db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?',(self.original['invocation_id'],step['step_key'])).fetchone());self.assertEqual(current,step)

            self.assertEqual({r[0]for r in db.execute('SELECT DISTINCT invocation_id FROM governance_delivery_step')},{self.original['invocation_id']})
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE invocation_id=? AND kind='delivery_continuation'",(self.current['invocation_id'],)).fetchone()[0],1)
        contract=self.contract(ready)
        from open_webui.utils.agentic_contracts.validator import validate_result
        self.assertEqual(validate_result(contract,self.current_packet),[])
        await self.m.assert_gateway_result(self.current,self.current_packet,contract,self.f.config,self.path)
        forged=deepcopy(contract);forged['delivery']['head_commit']='f'*40
        with self.assertRaises(ValueError):await self.m.assert_gateway_result(self.current,self.current_packet,forged,self.f.config,self.path)
        # A continuation can finish its Developer delivery; it cannot borrow
        # Developer tests as an independent QA pass.
        qa,qa_packet=self.f.qa_context(ready['delivery'])
        facts=await self.m.qa_verify_delivery({},row=qa,packet=qa_packet,config=self.f.config,db_path=self.path)
        self.assertFalse(facts['tests_complete'])
        from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest
        with self.assertRaises(ValueError):await self.m.assert_gateway_result(qa,qa_packet,{'outcome':'pass','reviewed_delivery_sha256':delivery_digest(ready['delivery']),'test_results':[],'evidence':[]},self.f.config,self.path)


    async def test_lost_pr_ack_reconciles_get_only_and_never_recreates_commit_or_pr(self):
        await self.current_chain();self.f.remote.drop='/pulls';held=await self.resume();self.assertEqual(held['state'],'pending_reconciliation');self.assertEqual(held['next_operation'],'repository.delivery_status')
        before=self.f.remote.posts().copy();self.assertEqual(before.count('/pulls'),1);status=await self.status();self.assertEqual(self.f.remote.posts(),before);self.assertEqual(status['state'],'reconciled');self.assertEqual(status['next_operation'],'repository.deliver')
        ready=await self.resume(status['arguments']);self.assertEqual(ready['state'],'ready_for_review');self.assertEqual(self.f.remote.posts(),before)
        self.assertEqual(before.count('/git/commits'),1);self.assertEqual(before.count('/git/refs'),1);self.assertEqual(len(self.f.remote.pulls),1)

    async def test_current_authority_is_required_and_rechecked_before_next_remote_post(self):
        await self.current_chain();before=self.f.remote.posts().copy()
        with self.assertRaises(self.m.CapabilityUnavailable):await self.m.developer_deliver({'test_call_ids':['fixture-test-call-001']},row=self.current,packet=self.current_packet,config=self.f.config,db_path=self.path)
        calls=0
        async def revoke_before_post():
            nonlocal calls
            calls+=1
            if calls>1:raise ValueError('Authority revoked before first missing POST')
            return await self.authority()
        with self.assertRaises(ValueError):await self.m.developer_deliver({'test_call_ids':['fixture-test-call-001']},row=self.current,packet=self.current_packet,config=self.f.config,db_path=self.path,check_authority=revoke_before_post)
        self.assertGreaterEqual(calls,2);self.assertEqual(self.f.remote.posts(),before);self.assertEqual(self.f.remote.pulls,{})

    async def test_zero_defect_blocked_delivery_checkpoint_creates_one_audited_successor(self):
        await self.prepare_original();first=await self.continue_original(self.original,self.original_packet)
        self.current=await self.continue_original(first,self.original_packet,outcome='blocked',kind='delivery');self.current_packet=deepcopy(self.original_packet);self.f.row=self.current;self.f.packet_value=self.current_packet
        again,created=self.orchestrator._resume(self.last_checkpoint,self.current_packet,self.f.gate_value,self.path,[],'delivery',delivery_config=self.f.config);self.assertFalse(created);self.assertEqual(again['invocation_id'],self.current['invocation_id'])
        with self.g.connect(self.path)as db:self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE kind='delivery_checkpoint_resumed'").fetchone()[0],1)
        owner=self.m.original_delivery(self.current,self.current_packet,self.f.config,self.path);self.assertEqual(owner['row']['invocation_id'],self.original['invocation_id']);ready=await self.resume();self.assertEqual(ready['state'],'ready_for_review');self.assertEqual(self.f.remote.posts().count('/git/commits'),1)

    async def test_published_qa_fail_starts_fresh_developer_and_never_borrows_old_delivery(self):
        await self.prepare_original();ready=await self.f.deliver();self.assertEqual(ready['state'],'ready_for_review')
        self.current=self.original;self.current_packet=deepcopy(self.original_packet)
        def publish(row,packet,result,kind,status):
            event={'schema_version':1,'event_id':'agov-result-'+self.b.digest([row['invocation_id'],result])[:48],'type':kind,'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],'run_id':row['automation_run_id'],'invocation_id':row['invocation_id'],'role':row['target_role'],'agent_id':row['agent_id'],'work_packet_digest':packet['content_sha256'],'result_contract':result,'artifacts':[],'acceptance_passed':False}
            self.store.save_result(row['invocation_id'],row['automation_run_id'],result,event,self.path);self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'execution_finished',db_path=self.path)
            self.store.publication_receipt(row['invocation_id'],{'event_id':event['event_id'],'task_id':row['erp_task'],'run_id':row['automation_run_id'],'modified':'fixture-published-'+row['invocation_id'],'status':status,'durable':True},self.path)
        publish(self.original,self.original_packet,self.contract(ready),'run.ready','Pending Review')
        qa_packet=self.f.packet(role='qa');qa_packet['implementation_run_id']=self.original['automation_run_id'];qa_packet['implementation_delivery']=ready['delivery'];qa_packet['content_sha256']=self.b.packet_digest(qa_packet)
        qa_id='invocation-qa-rejected-fixture';self.g.handoff(source_invocation_id=self.original['invocation_id'],source_run_id=self.original['automation_run_id'],source_lease_token=self.original['lease_token'],invocation_id=qa_id,project=self.original['erp_project'],task=self.original['erp_task'],from_role='developer',target_role='qa',task_revision=qa_packet['task_revision'],context='Independent QA found a concrete defect',work_packet=qa_packet,gate=self.f.gate_value,actor='server-fixture',db_path=self.path)
        run=self.b.stable_run_id(qa_id);qa=self.g.claim_invocation(qa_id,run,gate=self.f.gate_value,db_path=self.path);self.g.mark_submitted(qa_id,run,qa['lease_token'],gate=self.f.gate_value,db_path=self.path);qa=self.g.find(qa_id,self.path,include_private=True)
        failure={'schema_version':1,'invocation_id':qa_id,'actor':qa_packet['binding'],'project_id':qa['erp_project'],'task_id':qa['erp_task'],'task_revision':qa_packet['task_revision'],'work_packet_digest':qa_packet['content_sha256'],'outcome':'fail','summary':'A concrete independent defect requires a new implementation.','test_results':[],'evidence':[],'implementation_invocation_id':self.original['invocation_id'],'implementation_agent_id':self.original['agent_id'],'defects':[{'id':'DEFECT-CONTROLLED'}]}
        content=b'Controlled independent QA defect report for the correction-boundary fixture\n'
        artifact=await self.f.ops.artifact_write({'name':'qa-correction-defect.txt','content_base64':base64.b64encode(content).decode()},row=qa,config=self.f.config)
        failure['evidence']=[{'id':'EV-QA-DEFECT','kind':'test_log','uri':artifact['uri'],'sha256':artifact['sha256'],'requirement_ids':['REQ-ONE']}]
        failure['test_results']=[{'test_id':qa_packet['acceptance_tests'][0]['id'],'status':'fail','observed':'The controlled case reported a concrete defect.','evidence_ids':['EV-QA-DEFECT']}]
        failure['defects']=[{'id':'DEFECT-CONTROLLED','requirement_ids':['REQ-ONE'],'reproduction':'Inspect the controlled fixture case.','expected':'The specified acceptance behavior.','observed':'The controlled fixture reports a mismatch.','evidence_ids':['EV-QA-DEFECT']}]
        from open_webui.utils.agentic_contracts.validator import validate_result
        self.assertEqual(validate_result(failure,qa_packet),[])
        publish(qa,qa_packet,failure,'qa.rejected','QA REJECTED')
        corrected_packet=deepcopy(self.original_packet);ident='invocation-new-correction-fixture';self.g.handoff(source_invocation_id=qa_id,source_run_id=run,source_lease_token=qa['lease_token'],invocation_id=ident,project=qa['erp_project'],task=qa['erp_task'],from_role='qa',target_role='developer',task_revision=corrected_packet['task_revision'],context='Correct the exact independently reported defect',work_packet=corrected_packet,gate=self.f.gate_value,actor='server-fixture',db_path=self.path)
        current_run=self.b.stable_run_id(ident);new=self.g.claim_invocation(ident,current_run,gate=self.f.gate_value,db_path=self.path);self.g.mark_submitted(ident,current_run,new['lease_token'],gate=self.f.gate_value,db_path=self.path);self.current=self.g.find(ident,self.path,include_private=True);self.current_packet=corrected_packet
        self.assertIsNone(self.m.original_delivery(self.current,self.current_packet,self.f.config,self.path));before=self.f.remote.posts().copy()
        with self.assertRaises(ValueError):await self.resume({'test_call_ids':['fixture-test-call-001']})
        self.assertEqual(self.f.remote.posts(),before)
        with self.m._db(self.path)as db:self.assertIsNone(db.execute('SELECT 1 FROM governance_developer_delivery WHERE invocation_id=?',(ident,)).fetchone())
        # A fresh QA-correction turn must also be able to deliver its own new
        # tested source while the old completed journal remains unchanged.
        with self.m._db(self.path) as db:
            original_saved=dict(db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                                           (self.original['invocation_id'],)).fetchone())
        self.f.row,self.f.packet_value=self.current,self.current_packet
        (self.f.repo/'src/app.py').write_bytes(b'print("independent QA correction")\n')
        with self.f.sessions.connect(self.path) as db:
            db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',
                       ('fixture-qa-correction-session',ident,'{}','{}','a'*64,'b'*64,'admitted',1))
            db.commit()
        await self.f.seed_test('fixture-qa-correction-test')
        with self.f.sessions.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET session_id='fixture-qa-correction-session' WHERE call_id='fixture-qa-correction-test'")
            db.commit()
        corrected=await self.resume({'test_call_ids':['fixture-qa-correction-test']})
        self.assertEqual(corrected['state'],'ready_for_review');self.assertEqual(corrected['invocation_id'],ident)
        self.assertNotIn('delivery_provenance',corrected)
        self.assertEqual(await self.resume({'test_call_ids':['fixture-qa-correction-test']}),corrected)
        with self.m._db(self.path) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                                            (self.original['invocation_id'],)).fetchone()),original_saved)
            self.assertEqual(db.execute('SELECT count(*) FROM governance_developer_delivery').fetchone()[0],2)

    async def test_changed_test_source_and_model_selected_original_cannot_resume(self):
        await self.current_chain();before=self.f.remote.posts().copy()
        with self.assertRaises(ValueError):await self.resume({'test_call_ids':['not-original-test']})
        with self.assertRaises(ValueError):await self.resume({'test_call_ids':['fixture-test-call-001'],'original_invocation_id':self.original['invocation_id']})
        (self.f.repo/'src/app.py').write_bytes(b'Changed after the original tested snapshot\n')
        with self.assertRaises(ValueError):await self.resume()
        self.assertEqual(self.f.remote.posts(),before);self.assertEqual(self.f.remote.pulls,{})

    async def test_changed_server_scope_or_unpublished_checkpoint_denies_before_remote_mutation(self):
        await self.current_chain();before=self.f.remote.posts().copy();original_row=deepcopy(self.current);original_packet=deepcopy(self.current_packet)
        for field,value in [('repository','https://github.com/ABiT-Consulting/unrelated'),('srs',{'revision':'foreign','sha256':'f'*64}),('objective','Unrelated implementation work'),('tool_scopes',['new-scope'])]:
            changed=deepcopy(original_packet);changed[field]=value;changed['content_sha256']=self.b.packet_digest(changed)
            with self.g.connect(self.path)as db:db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',(self.b.canonical(changed),self.b.digest(changed),self.current['invocation_id']))
            row=self.g.find(self.current['invocation_id'],self.path,include_private=True)
            with self.subTest(field=field),self.assertRaises(ValueError):self.m.original_delivery(row,changed,self.f.config,self.path)
        with self.g.connect(self.path)as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',(self.b.canonical(original_packet),self.b.digest(original_packet),self.current['invocation_id']))
            db.execute("UPDATE governance_result SET phase='uncertain' WHERE invocation_id=?",(self.original['invocation_id'],))
        with self.assertRaises(ValueError):self.m.original_delivery(original_row,original_packet,self.f.config,self.path)
        for field in ('erp_task','erp_project','agent_id','target_role'):
            foreign={**original_row,field:'qa' if field=='target_role' else 'foreign'}
            with self.subTest(row_field=field),self.assertRaises(ValueError):await self.m.developer_deliver({'test_call_ids':['fixture-test-call-001']},row=foreign,packet=original_packet,config=self.f.config,db_path=self.path,check_authority=self.authority)
        self.assertEqual(self.f.remote.posts(),before);self.assertEqual(self.f.remote.pulls,{})

if __name__=='__main__':unittest.main()

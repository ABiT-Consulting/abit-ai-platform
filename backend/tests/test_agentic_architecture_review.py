"""Read-only phase derivation with real temporary ledger and published SA chain."""
from __future__ import annotations
import ast
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
TESTS=BACKEND/'tests';ROOT=BACKEND/'open_webui/utils'
sys.path.insert(0,str(TESTS))
from test_agentic_controls_events import Fixture,P,T


def plan_fixture():
    tree=ast.parse((TESTS/'test_agentic_planning.py').read_bytes())
    value=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PLAN' for t in n.targets))
    return json.loads(ast.literal_eval(value.args[0]))


class ArchitectureReview(Fixture):
    def candidate(self,name):
        path=HERE/(name+'.py')
        if not path.is_file():path=ROOT/(name+'.py')
        full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,path);module=importlib.util.module_from_spec(spec);sys.modules[full]=module;setattr(self.utils,name,module);spec.loader.exec_module(module);return module

    def setUp(self):
        super().setUp();self.phase=self.candidate('agentic_architecture_review');self.store=self.load('agentic_result_store');self.b=self.bridge_module
        self.sa='agov-architecture-source-fixture';self.pm='agov-architecture-review-fixture';self.raw_sha='a'*64
        sa_packet=self.packet(role='solution_architect');sa_packet['srs']={'revision':'fixture-srs-v1','sha256':'1'*64};sa_packet['requirement_ids']=['REQ-UI-01','REQ-API-02'];sa_packet['acceptance_tests']=[{'id':'TEST-UI-02','requirement_ids':['REQ-UI-01'],'method':'browser','expected':'Controlled UI acceptance.'},{'id':'TEST-API-01','requirement_ids':['REQ-API-02'],'method':'integration','expected':'Controlled API acceptance.'}]
        epoch=self.b.digest({'project_id':P,'tasks':[]});sa_packet['project_backlog']={'project_id':P,'complete':True,'page_size':50,'total_count':0,'tasks':[],'context_excluded_task_ids':[],'snapshot_sha256':epoch,'context_sha256':epoch};sa_packet['content_sha256']=self.b.packet_digest(sa_packet)
        self.g.enqueue(invocation_id=self.sa,project=P,task=T,from_role='project_manager',target_role='solution_architect',task_revision=sa_packet['task_revision'],context='Controlled original architect',work_packet=sa_packet,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        run=self.b.stable_run_id(self.sa);owned=self.g.claim_invocation(self.sa,run,gate=self.gate_value,db_path=self.path);self.g.mark_submitted(self.sa,run,owned['lease_token'],gate=self.gate_value,db_path=self.path)
        plan=plan_fixture();meta=plan['metadata'];meta.update(project_id=P,run_id=self.sa,source_url='https://erp.abit.ai/desk/project/'+P,source_activity_revision=sa_packet['source_modified'],project_backlog_context_sha256=epoch,project_backlog_snapshot_sha256=epoch)
        meta['agent_binding']={'project_id':P,'role':'solution_architect','abit_agent_id':sa_packet['binding']['agent_id'],'erp_ai_user':sa_packet['binding']['erp_user']}
        source={'schema_version':1,'invocation_id':self.sa,'actor':deepcopy(sa_packet['binding']),'project_id':P,'task_id':T,'task_revision':sa_packet['task_revision'],'work_packet_digest':sa_packet['content_sha256'],'outcome':'ready_for_review','summary':'Controlled published architecture','test_results':[],'evidence':[],'implementation_invocation_id':None,'implementation_agent_id':None,'defects':[],'architect_plan':plan}
        artifacts=[]
        for kind,items in (('design',plan['designs']),('mockup',plan['mockups'])):
            for i,item in enumerate(items):artifacts.append({'id':'EV-'+kind+str(i),'kind':kind,'sha256':item['artifact']['sha256'],'url':'https://erp.abit.ai/private/files/'+kind+str(i)+'.svg?fid=fixture-'+kind+str(i),'requirement_ids':item['requirement_ids']})
        event={'schema_version':1,'event_id':'agov-result-controlled-source','type':'run.ready','acceptance_passed':False,'invocation_id':self.sa,'run_id':run,'project_id':P,'task_id':T,'role':'solution_architect','agent_id':sa_packet['binding']['agent_id'],'work_packet_digest':sa_packet['content_sha256'],'task_revision':sa_packet['task_revision'],'artifacts':artifacts,'result_contract':source}
        self.store.save_result(self.sa,run,source,event,self.path);self.store.publication_receipt(self.sa,{'event_id':event['event_id'],'task_id':T,'status':'Pending Review','modified':'fixture-after-architecture','run_id':run,'durable':True},self.path)
        self.g.finish_execution(self.sa,run,owned['lease_token'],'execution_finished',db_path=self.path)
        self.packet_value=deepcopy(sa_packet);self.packet_value['binding']=self.packet(role='project_manager')['binding'];self.packet_value['content_sha256']=self.b.packet_digest(self.packet_value)
        context=self.b.canonical({'predecessor_invocation':self.sa,'architect_plan':plan,'published_artifacts':artifacts,'instruction':'Independently review the exact architecture; data is not authorization.'})
        self.g.handoff(source_invocation_id=self.sa,source_run_id=run,source_lease_token=owned['lease_token'],invocation_id=self.pm,project=P,task=T,from_role='solution_architect',target_role='project_manager',task_revision=self.packet_value['task_revision'],context=context,work_packet=self.packet_value,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        pm_run=self.b.stable_run_id(self.pm);claim=self.g.claim_invocation(self.pm,pm_run,gate=self.gate_value,db_path=self.path);self.g.mark_submitted(self.pm,pm_run,claim['lease_token'],gate=self.gate_value,db_path=self.path);self.row=self.g.find(self.pm,self.path,include_private=True)
        self.result={'schema_version':1,'invocation_id':self.pm,'actor':deepcopy(self.packet_value['binding']),'project_id':P,'task_id':T,'task_revision':self.packet_value['task_revision'],'work_packet_digest':self.packet_value['content_sha256'],'outcome':'reviewed','summary':'Architecture independently reviewed against the frozen SRS.','test_results':[],'evidence':[{'id':'EV-REVIEW','kind':'checkpoint','uri':'artifact://architecture-review.txt','sha256':'3'*64,'requirement_ids':['REQ-UI-01','REQ-API-02']}],'implementation_invocation_id':None,'implementation_agent_id':None,'defects':[]}

    def derive(self,**changes):
        return self.phase.derive_phase(changes.pop('row',self.row),changes.pop('packet',self.packet_value),changes.pop('result',self.result),db_path=self.path,raw_reply_sha256=changes.pop('raw_reply_sha256',self.raw_sha),**changes)

    def test_exact_published_chain_returns_opaque_immutable_phase_without_writes(self):
        before=deepcopy(self.result);packet=deepcopy(self.packet_value)
        with self.g.connect(self.path) as db:
            count=db.total_changes;trace=[];db.set_trace_callback(trace.append);phase=self.derive(db=db);self.assertEqual(db.total_changes,count)
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER')) for s in trace));self.assertEqual(self.result,before);self.assertEqual(self.packet_value,packet)
        self.assertTrue(self.phase.matches_phase(phase,self.result,self.packet_value));self.assertFalse(self.phase.matches_phase(True,self.result,self.packet_value));self.assertFalse(self.phase.matches_phase(phase.audit,self.result,self.packet_value))
        audit=phase.audit;audit['policy_version']='forged';self.assertEqual(phase.audit['policy_version'],'architecture-review.v1');self.assertFalse(self.phase.matches_phase(phase,{**self.result,'summary':'changed'},self.packet_value))
        with self.assertRaises(Exception):phase._result_sha256='0'*64
        self.assertEqual(phase.audit['published_source_result_sha256'],self.store.get_result(self.sa,self.path)['result_sha256'])

    def test_caller_row_packet_result_identity_and_raw_hash_cannot_supply_authority(self):
        for field in ('automation_run_id','source_invocation_id','agent_id','lease_fence','submitted_at','context_hash'):
            with self.subTest(field=field),self.assertRaises(ValueError):self.derive(row={**self.row,field:'wrong'})
        with self.assertRaises(ValueError):self.derive(packet={**self.packet_value,'objective':'changed'})
        for field in ('invocation_id','actor','project_id','task_id','task_revision','work_packet_digest'):
            with self.subTest(field=field),self.assertRaises(ValueError):self.derive(result={**self.result,field:'wrong'})
        with self.assertRaises(ValueError):self.derive(raw_reply_sha256='missing')

    def test_missing_nonpublished_or_changed_source_result_event_receipt_denies(self):
        with self.g.connect(self.path) as db:
            for field,value in (('phase','pending'),('result_sha256','0'*64),('run_id','foreign-run'),('receipt_json','null'),('event_json','{}')):
                with self.subTest(field=field):
                    db.execute('SAVEPOINT mutation');db.execute('UPDATE governance_result SET '+field+'=? WHERE invocation_id=?',(value,self.sa))
                    with self.assertRaises(ValueError):self.derive(db=db)
                    db.execute('ROLLBACK TO mutation');db.execute('RELEASE mutation')
            saved=self.store.get_result(self.sa,self.path)
            for key,value in (('durable',False),('run_id','foreign-run'),('status','Completed')):
                with self.subTest(receipt=key):
                    changed={**saved['receipt'],key:value};db.execute('SAVEPOINT mutation');db.execute('UPDATE governance_result SET receipt_json=? WHERE invocation_id=?',(self.b.canonical(changed),self.sa))
                    with self.assertRaises(ValueError):self.derive(db=db)
                    db.execute('ROLLBACK TO mutation');db.execute('RELEASE mutation')
            db.execute('DELETE FROM governance_result WHERE invocation_id=?',(self.sa,))
            with self.assertRaises(ValueError):self.derive(db=db)

    def test_context_plan_artifacts_srs_and_backlog_cannot_be_replaced(self):
        with self.g.connect(self.path) as db:
            context=json.loads(self.row['context'])
            for key,value in (('predecessor_invocation','foreign-source'),('architect_plan',{}),('published_artifacts',[])):
                with self.subTest(key=key):
                    changed={**context,key:value};raw=self.b.canonical(changed);digest=self.g.work_digest(P,T,'project_manager',raw);db.execute('SAVEPOINT mutation');db.execute('UPDATE governance_invocation SET context=?,context_hash=? WHERE invocation_id=?',(raw,digest,self.pm));fresh=dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(self.pm,)).fetchone())
                    with self.assertRaises(ValueError):self.derive(db=db,row=fresh)
                    db.execute('ROLLBACK TO mutation');db.execute('RELEASE mutation')
            for key,value in (('srs',{'revision':'foreign','sha256':'f'*64}),('project_backlog',{**self.packet_value['project_backlog'],'context_sha256':'f'*64})):
                changed=deepcopy(self.packet_value);changed[key]=value;changed['content_sha256']=self.b.packet_digest(changed);db.execute('SAVEPOINT mutation');db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',(self.b.canonical(changed),self.b.digest(changed),self.pm));fresh=dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(self.pm,)).fetchone())
                with self.subTest(key=key),self.assertRaises(ValueError):self.derive(db=db,row=fresh,packet=changed)
                db.execute('ROLLBACK TO mutation');db.execute('RELEASE mutation')

    def test_review_cannot_claim_qa_or_implementation_materialization(self):
        for key,value in (('test_results',[{'test_id':'TEST-UI-02','status':'pass'}]),('defects',[{'id':'defect'}]),('implementation_invocation_id','foreign'),('implementation_agent_id','foreign'),('qa_review_ack','a'*64),('reviewed_delivery_sha256','a'*64),('delivery',{}),('architect_plan',{}),('action_requests',[]),('dependency_verifications',[]),('evidence',[]),('evidence',[{**self.result['evidence'][0],'uri':'https://erp.abit.ai/private/files/foreign.txt'}]),('evidence',[{**self.result['evidence'][0],'uri':'artifact://../foreign.txt'}])):
            with self.subTest(key=key),self.assertRaises(ValueError):self.derive(result={**self.result,key:value})
        qa=deepcopy(self.packet_value);qa['qa_review']={'untrusted':True};self.assertIsNone(self.derive(packet=qa))
        self.assertIsNone(self.derive(result={**self.result,'outcome':'ready_for_review'}))
        self.assertIsNone(self.derive(row={**self.row,'target_role':'qa'}))
        validator=importlib.import_module('open_webui.utils.agentic_contracts.validator');errors=validator.validate_result(self.result,self.packet_value);self.assertIn('Only the registered PM may acknowledge a bound QA coordination review',errors);self.assertFalse(any(error.startswith('work_packet.') for error in errors))

    def test_cancel_requested_retains_local_classification_without_write_authority(self):
        initial=self.derive().audit;self.g.request_cancel(self.pm,'manager@example.invalid',self.path);fresh=self.g.find(self.pm,self.path,include_private=True);self.assertEqual(fresh['state'],'cancel_requested');phase=self.derive(row=fresh);self.assertEqual(phase.audit,initial)
        self.assertEqual(self.store.get_result(self.pm,self.path),None);self.assertEqual(self.g.find(self.pm,self.path)['state'],'cancel_requested')

    def test_preview_is_source_bound_before_submission_and_excludes_atomic_and_qa(self):
        self.assertTrue(self.phase.architecture_context(self.row,self.packet_value,db_path=self.path))
        for field in ('qa_review','atomic_adoption'):
            changed={**self.packet_value,field:{'untrusted':True}};self.assertFalse(self.phase.architecture_context(self.row,changed,db_path=self.path))
        self.assertFalse(self.phase.architecture_context({**self.row,'source_invocation_id':'foreign'},self.packet_value,db_path=self.path))
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_invocation SET state='queued',automation_run_id=NULL,submitted_at=NULL WHERE invocation_id=?",(self.pm,));queued=self.g.find(self.pm,self.path,include_private=True)
            self.assertTrue(self.phase.architecture_context(queued,self.packet_value,db_path=self.path))
            with self.assertRaises(ValueError):self.derive(row=queued,db=db)
            db.execute('UPDATE governance_invocation SET state=?,automation_run_id=?,submitted_at=? WHERE invocation_id=?',(self.row['state'],self.row['automation_run_id'],self.row['submitted_at'],self.pm))
            saved=self.store.get_result(self.sa,self.path);event={**saved['event'],'acceptance_passed':True};db.execute('UPDATE governance_result SET event_json=? WHERE invocation_id=?',(self.b.canonical(event),self.sa))
            self.assertFalse(self.phase.architecture_context(self.row,self.packet_value,db_path=self.path))
            with self.assertRaises(ValueError):self.derive(db=db)

    def test_saved_audit_is_rederived_and_changed_proof_cannot_be_reused(self):
        initial=self.derive();audit=initial.audit
        with self.g.connect(self.path) as db:
            self.g._event(db,P,self.pm,self.phase.AUDIT_KIND,'server-phase-policy',audit)
            phase=self.phase.verify_saved_phase(self.row,self.packet_value,self.result,db=db);self.assertEqual(phase.audit,audit)
            with self.assertRaises(__import__('sqlite3').IntegrityError):db.execute('UPDATE governance_event SET payload=? WHERE invocation_id=? AND kind=?',(self.b.canonical({**audit,'policy_sha256':'f'*64}),self.pm,self.phase.AUDIT_KIND))
            self.g._event(db,P,self.pm,self.phase.AUDIT_KIND,'server-phase-policy',{**audit,'policy_sha256':'f'*64})
            with self.assertRaises(ValueError):self.phase.verify_saved_phase(self.row,self.packet_value,self.result,db=db)

if __name__=='__main__':unittest.main()

"""Real temporary ledger and immutable published QA preflight boundary; no network.
The controlled ERP bridge returns a fresh packet only. No model/Git/Native action.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
TESTS=BACKEND/'tests';ROOT=BACKEND/'open_webui/utils';sys.path.insert(0,str(TESTS))
from test_agentic_controls_events import Fixture,P,T

class QAPreflightRetry(Fixture):
    def setUp(self):
        super().setUp()
        self.recovery=self.load_candidate('agentic_recovery')
        self.delivery=self.load('agentic_gateway_delivery');self.sessions=self.load('agentic_gateway_sessions')
        self.store=self.load('agentic_result_store');self.start=self.load('agentic_start_recovery')
        with self.delivery._db(self.path):pass
        with self.sessions.connect(self.path):pass
        self.tool=self.load('agentic_tool_gateway')
        with self.tool._connect(self.path):pass
        self.profile={'repository':self.packet()['repository'],'environment':'development','allowed_paths':['src'],'read_paths':['src'],'test_paths':['tests'],'base_branch':'main','base_commit':'a'*40,'workspace_root':'/srv/abit-dev-linux/qa-preflight-synthetic-'+Path(self.temp.name).name,'tests':{'unit':['python3','-m','unittest']},'test_acceptance_ids':{'unit':['REQ-ONE.acceptance']},'git_author':{'name':'Synthetic Developer','email':'fixture@example.invalid'}}
        self.config={'agentic_repository_gateway':{'projects':{P:self.profile}}}
        self.dev_id='fixture-developer-preflight';self.qa_id='fixture-qa-preflight'
        self.dev_packet=self.packet(role='developer');self.dev=self.claim(self.dev_id,self.dev_packet)
        self.delivery_contract={'repository':self.dev_packet['repository'],'base_branch':'main','base_commit':'a'*40,'head_commit':'b'*40,'pull_request_url':self.dev_packet['repository']+'/pull/24','pull_request_number':24,'file_manifest_sha256':'c'*64,'test_runs':[]}
        dev_result=self.result(self.dev,self.dev_packet,'ready_for_review',delivery=self.delivery_contract)
        self.publish(self.dev,self.dev_packet,dev_result,'run.ready','Pending Review')
        self.qa_packet=self.packet(role='qa');self.qa_packet['implementation_run_id']=self.dev['automation_run_id'];self.qa_packet['implementation_delivery']=deepcopy(self.delivery_contract)
        self.qa_packet['project_backlog']={'complete':True,'context_sha256':'d'*64,'tasks':[{'task_id':T,'status':'Pending Review','source_modified':'modified-open'}]}
        self.qa_packet['content_sha256']=self.bridge_module.packet_digest(self.qa_packet)
        self.g.handoff(source_invocation_id=self.dev_id,source_run_id=self.dev['automation_run_id'],source_lease_token=self.dev['lease_token'],invocation_id=self.qa_id,project=P,task=T,from_role='developer',target_role='qa',task_revision=self.qa_packet['task_revision'],context='Independent QA of exact published implementation',work_packet=self.qa_packet,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        self.qa=self.claim(self.qa_id,self.qa_packet,queued=True)
        result=self.result(self.qa,self.qa_packet,'blocked',implementation_invocation_id=self.dev_id,implementation_agent_id=self.dev['agent_id'])
        self.publish(self.qa,self.qa_packet,result,'run.blocked','Blocked')
        self.qa=self.g.find(self.qa_id,self.path,include_private=True)
        self.fresh=deepcopy(self.qa_packet);self.fresh['source_modified']='modified-pending-review';self.fresh['project_backlog']['tasks'][0]['source_modified']=self.fresh['source_modified'];self.fresh['content_sha256']=self.bridge_module.packet_digest(self.fresh)
        self.bridge=types.SimpleNamespace(packet=AsyncMock(side_effect=lambda *a:deepcopy(self.fresh)))
        self.seed_calls()
        with self.g.connect(self.path)as db:self.intents._schema(db);db.commit()
        self.retry()
        self.immutable=self.history()

    def load_candidate(self,name):
        path=HERE/(name+'.py')
        if not path.exists():
            exported=BACKEND.parent/'docs/evidence/2026-11-15-qa-preflight-retry-v1/runtime/utils'/(name+'.py')
            path=exported if exported.is_file()else ROOT/(name+'.py')
        full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,path);m=importlib.util.module_from_spec(spec);sys.modules[full]=m;setattr(self.utils,name,m);spec.loader.exec_module(m);return m

    def claim(self,ident,packet,queued=False):
        if not queued:self.g.enqueue(invocation_id=ident,project=P,task=T,from_role='project_manager',target_role=packet['binding']['role'],task_revision=packet['task_revision'],context='Controlled published predecessor',work_packet=packet,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        run=self.bridge_module.stable_run_id(ident);row=self.g.claim_invocation(ident,run,gate=self.gate_value,db_path=self.path);self.assertIsNotNone(row)
        event={'schema_version':1,'event_id':'agov-start-'+self.bridge_module.digest(ident)[:48],'type':'run.started','project_id':P,'task_id':T,'task_revision':packet['task_revision'],'run_id':run,'invocation_id':ident,'role':packet['binding']['role'],'agent_id':row['agent_id'],'summary':'Controlled original start.','expected_modified':packet['source_modified'],'expected_project_revision':packet['project_revision'],'work_packet_digest':packet['content_sha256'],'artifacts':[]}
        self.start.record_start_intent(row,event,self.path);self.start.record_start_receipt(ident,{'event_id':event['event_id'],'task_id':T,'run_id':run,'modified':'controlled-started','status':'QA Reviewing'if row['target_role']=='qa'else'Working','durable':True},self.path)
        self.g.mark_submitted(ident,run,row['lease_token'],gate=self.gate_value,db_path=self.path);return self.g.find(ident,self.path,include_private=True)

    def result(self,row,packet,outcome,**extra):
        return {'schema_version':1,'invocation_id':row['invocation_id'],'actor':deepcopy(packet['binding']),'project_id':P,'task_id':T,'task_revision':packet['task_revision'],'work_packet_digest':packet['content_sha256'],'outcome':outcome,'summary':'Controlled exact published checkpoint.','test_results':[],'evidence':[],'defects':[],'implementation_invocation_id':None,'implementation_agent_id':None,**extra}

    def publish(self,row,packet,result,kind,status):
        event={'schema_version':1,'event_id':'agov-result-'+self.bridge_module.digest([row['invocation_id'],result])[:48],'type':kind,'project_id':P,'task_id':T,'task_revision':packet['task_revision'],'run_id':row['automation_run_id'],'invocation_id':row['invocation_id'],'role':row['target_role'],'agent_id':row['agent_id'],'work_packet_digest':packet['content_sha256'],'result_contract':result,'artifacts':[],'acceptance_passed':False}
        self.store.save_result(row['invocation_id'],row['automation_run_id'],result,event,self.path)
        self.assertTrue(self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'execution_finished',qa_verdict='blocked'if row['target_role']=='qa'else None,db_path=self.path))
        self.store.publication_receipt(row['invocation_id'],{'event_id':event['event_id'],'task_id':T,'run_id':row['automation_run_id'],'modified':'controlled-published','status':status,'durable':True},self.path)

    def seed_calls(self):
        packet=self.qa_packet;scope={'project_id':P,'task_id':T,'role':'qa','agent_id':self.qa['agent_id'],'invocation_id':self.qa_id,'run_id':self.qa['automation_run_id'],'work_packet_digest':packet['content_sha256'],'erp_user':self.users['qa'],'repository':packet['repository'],'environment':packet['environment'],'tool_scopes':sorted(packet['tool_scopes']),'owner':'owner-fixture'}
        view={k:self.profile.get(k)for k in ('repository','environment','allowed_paths','read_paths','test_paths','test_acceptance_ids')};view['test_ids']=['unit']
        context={'invocation_id':self.qa_id,'work_packet':packet,'repository_profile':view}
        c=self.bridge_module.canonical;d=self.bridge_module.digest
        with self.sessions.connect(self.path)as db:
            db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',('qa-preflight-session',self.qa_id,c(scope),c({'protocol_version':'fixture','scope':scope,'preflight_proof':{},'submission_proof':{}}),'a'*64,'b'*64,'admitted',1))
            for call,op,state,result in [('context-original','context.read','completed',c(context)),('verify-original','repository.verify_delivery','uncertain',None)]:db.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',('qa-preflight-session',call,d({'operation':op,'arguments':{}}),op,state,result,1,1))
            db.commit()

    def retry(self):
        with self.g._transaction(self.path)as db:
            db.execute("INSERT INTO governance_native_control VALUES(?,?,?,?,?,?,?)",(P,'',15,'human-native-retry15','retry','enabled','acknowledged'))
            with patch.object(self.g.time,'time',return_value=200):self.g._event(db,P,None,'native_control_applied','human@example.invalid',{'command_id':'human-native-retry15','control_sequence':15,'task_id':'','action':'retry','state':'enabled'})
            db.execute('UPDATE governance_result SET updated_at=100 WHERE invocation_id=?',(self.qa_id,))

    def history(self):
        with self.g.connect(self.path)as db:
            return {table:[dict(r)for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]for table in ['governance_start','governance_result','governance_gateway_session','governance_gateway_call','governance_delivery_step','governance_developer_delivery','governance_qa_delivery','governance_repository_workspace','governance_action']}

    async def resume(self):return await self.recovery.resume_qa_preflight(self.qa_id,bridge=self.bridge,project_gate=self.gate,config=self.config,db_path=self.path)

    async def test_newer_ack_pending_review_one_successor_preserves_original_and_duplicate(self):
        successor,created=await self.resume();self.assertTrue(created);self.assertEqual(successor['target_role'],'qa');self.assertEqual(successor['state'],'queued');self.assertIsNone(successor['automation_run_id']);self.assertIsNone(successor['submitted_at']);self.assertEqual(successor['dispatch_attempts'],0)
        current=self.g.find(self.qa_id,self.path,include_private=True);self.assertEqual(current['state'],'handed_off')
        for key in ['automation_run_id','submitted_at','dispatch_attempts','packet_hash','work_packet','lease_token','lease_fence']:self.assertEqual(current[key],self.qa[key])
        self.assertEqual(self.history(),self.immutable);self.assertIsNone(await self.resume())
        with self.g.connect(self.path)as db:
            audit=db.execute("SELECT payload FROM governance_event WHERE kind='qa_preflight_resumed'").fetchall();self.assertEqual(len(audit),1);proof=json.loads(audit[0][0]);self.assertEqual(proof['successor'],successor['invocation_id']);self.assertEqual(proof['implementation_invocation_id'],self.dev_id);self.assertEqual(proof['original_calls'][1]['call_id'],'verify-original');self.assertEqual(db.execute('SELECT count(*)FROM governance_invocation').fetchone()[0],3)
            lease=db.execute('SELECT invocation_id FROM governance_task_lease WHERE erp_project=?AND erp_task=?',(P,T)).fetchone();self.assertEqual(lease[0],successor['invocation_id'])

    async def test_unpublished_or_stale_control_no_continuation(self):
        with self.g._transaction(self.path)as db:db.execute("UPDATE governance_result SET phase='pending'WHERE invocation_id=?",(self.qa_id,))
        self.assertIsNone(await self.resume());self.bridge.packet.assert_not_awaited()
        with self.g._transaction(self.path)as db:db.execute("UPDATE governance_result SET phase='published'WHERE invocation_id=?",(self.qa_id,));db.execute('UPDATE governance_result SET updated_at=201 WHERE invocation_id=?',(self.qa_id,))
        self.assertIsNone(await self.resume());self.assertEqual(self.g.find(self.qa_id,self.path)['state'],'execution_finished')

    async def test_gate_checkbox_task_pause_and_changed_business_deny(self):
        self.allowed=False
        with self.assertRaises(ValueError):await self.resume()
        self.allowed=True;self.gate_value['pm_task_automation_enabled']=False
        with self.assertRaises(ValueError):await self.resume()
        self.gate_value['pm_task_automation_enabled']=True
        self.controls.task_control(P,T,'pause',actor='human@example.invalid',db_path=self.path)
        with self.assertRaises(ValueError):await self.resume()
        self.controls.task_control(P,T,'resume',actor='human@example.invalid',db_path=self.path)
        self.fresh['srs']['sha256']='f'*64
        with self.assertRaises(ValueError):await self.resume()
        self.assertEqual(self.history(),self.immutable)

    async def test_empty_checkpoint_required_forged_result_receipt_or_implementation_denied(self):
        for field,value in [('test_results',[{'test_id':'independent-work'}]),('evidence',[{'uri':'artifact://existing'}]),('defects',[{'id':'DEFECT-ONE'}]),('implementation_invocation_id','foreign-developer')]:
            with self.subTest(field=field):
                with self.g.connect(self.path)as db:old=dict(db.execute('SELECT *FROM governance_result WHERE invocation_id=?',(self.qa_id,)).fetchone())
                result=json.loads(old['result_json']);result[field]=value;event=json.loads(old['event_json']);event['result_contract']=result;event['event_id']='agov-result-'+self.bridge_module.digest([self.qa_id,result])[:48];receipt=json.loads(old['receipt_json']);receipt['event_id']=event['event_id']
                with self.g._transaction(self.path)as db:db.execute('UPDATE governance_result SET result_json=?,result_sha256=?,event_json=?,receipt_json=?WHERE invocation_id=?',(self.bridge_module.canonical(result),self.bridge_module.digest(result),self.bridge_module.canonical(event),self.bridge_module.canonical(receipt),self.qa_id))
                with self.assertRaises(ValueError):await self.resume()
                with self.g._transaction(self.path)as db:db.execute('UPDATE governance_result SET result_json=?,result_sha256=?,event_json=?,receipt_json=?WHERE invocation_id=?',(old['result_json'],old['result_sha256'],old['event_json'],old['receipt_json'],self.qa_id))
        with self.g._transaction(self.path)as db:db.execute("UPDATE governance_result SET receipt_json=json_set(receipt_json,'$.durable',json('false'))WHERE invocation_id=?",(self.qa_id,))
        with self.assertRaises(ValueError):await self.resume()

    async def test_workspace_work_or_changed_call_scope_context_profile_denied(self):
        for table in ['governance_repository_workspace','governance_qa_delivery','governance_developer_delivery','governance_action']:
            with self.subTest(table=table):
                # Actual table schemas, minimally shaped existing original work rows.
                with self.g.connect(self.path)as db:columns=[dict(r)for r in db.execute('PRAGMA table_info('+table+')')]
                values={r['name']:('existing'if r['type']=='TEXT'else 1)for r in columns};values['invocation_id']=self.qa_id
                with self.g._transaction(self.path)as db:db.execute('INSERT INTO '+table+'('+','.join(values)+')VALUES('+','.join('?'for _ in values)+')',tuple(values.values()))
                with self.assertRaises(ValueError):await self.resume()
                with self.g._transaction(self.path)as db:db.execute('DELETE FROM '+table+' WHERE invocation_id=?',(self.qa_id,))
        with patch.object(self.delivery.os.path,'lexists',return_value=True):
            with self.assertRaises(ValueError):await self.resume()
        for mutation in ["UPDATE governance_gateway_call SET request_hash='forged'WHERE call_id='verify-original'","UPDATE governance_gateway_call SET state='completed'WHERE call_id='verify-original'","UPDATE governance_gateway_session SET scope_json=json_set(scope_json,'$.erp_user','other@example.invalid')"]:
            with self.g.connect(self.path)as db:oldcalls=[tuple(r)for r in db.execute('SELECT *FROM governance_gateway_call')];oldscope=db.execute('SELECT scope_json FROM governance_gateway_session').fetchone()[0]
            with self.g._transaction(self.path)as db:db.execute(mutation)
            with self.assertRaises(ValueError):await self.resume()
            with self.g._transaction(self.path)as db:
                db.execute('DELETE FROM governance_gateway_call');db.executemany('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',oldcalls);db.execute('UPDATE governance_gateway_session SET scope_json=?',(oldscope,))
        self.config['agentic_repository_gateway']['projects'][P]['allowed_paths']=['changed']
        with self.assertRaises(ValueError):await self.resume()

    async def test_wrong_ready_status_control_namespace_and_consumed_task_deny(self):
        self.fresh['project_backlog']['tasks'][0]['status']='Blocked'
        with self.assertRaises(ValueError):await self.resume()
        self.fresh['project_backlog']['tasks'][0]['status']='Pending Review'
        with self.g._transaction(self.path)as db:db.execute("UPDATE governance_native_control SET task='another-task'")
        self.assertIsNone(await self.resume())
        with self.g._transaction(self.path)as db:
            db.execute("UPDATE governance_native_control SET task=''");self.g._event(db,P,None,'qa_preflight_resumed','server-fixture',{'task_id':T})
        with self.assertRaisesRegex(ValueError,'consumed'):await self.resume()

    async def test_atomic_audit_failure_rolls_back_handoff_successor_and_fence(self):
        with self.g.connect(self.path)as db:
            rows=[dict(r)for r in db.execute('SELECT *FROM governance_invocation ORDER BY invocation_id')];leases=[dict(r)for r in db.execute('SELECT *FROM governance_task_lease')]
            db.execute("CREATE TRIGGER fail_preflight_audit BEFORE INSERT ON governance_event WHEN NEW.kind='qa_preflight_resumed'BEGIN SELECT RAISE(ABORT,'controlled audit failure');END");db.commit()
        with self.assertRaisesRegex(Exception,'controlled audit failure'):await self.resume()
        with self.g.connect(self.path)as db:
            self.assertEqual(rows,[dict(r)for r in db.execute('SELECT *FROM governance_invocation ORDER BY invocation_id')]);self.assertEqual(leases,[dict(r)for r in db.execute('SELECT *FROM governance_task_lease')]);self.assertEqual(db.execute("SELECT count(*)FROM governance_event WHERE kind='qa_preflight_resumed'").fetchone()[0],0)
        self.assertEqual(self.history(),self.immutable)

if __name__=='__main__':unittest.main()

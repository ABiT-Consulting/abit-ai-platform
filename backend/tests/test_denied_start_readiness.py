"""Same-original denied-start amendment: real temporary ledger, controlled ERP.
No live ERP, service, DB, provider, relay, host or deployment actions.
"""
from __future__ import annotations
from copy import deepcopy
import importlib.util
import json
import sqlite3
import subprocess
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
ROOT=BACKEND/'open_webui/utils';sys.path.insert(0,str(BACKEND/'tests'))
from test_agentic_controls_events import Fixture,P,T


class ReadinessBridge:
    def __init__(self,f):
        self.f=f;self.erp=self;self.secret='controlled-fixture-only';self.calls=[];self.exists=False;self.receipt=None;self.native_sha=None;self.status='Open';self.modified='fixture-human-open';self.lost_ack=False;self.changed=None
    async def packet(self,*args):
        value=deepcopy(self.f.packet_value);value['source_modified']=self.modified
        for item in value['project_backlog']['tasks']:
            if item['task_id']==T:item['status']=self.status;item['source_modified']=self.modified
        value['project_backlog']['snapshot_sha256']=self.f.b.digest({'project_id':P,'tasks':value['project_backlog']['tasks']})
        if self.changed:self.changed(value)
        self.f.seal_packet(value)
        return value
    async def get(self,doctype,name):
        assert (doctype,name)==('Task',T)
        self.calls.append(('GET_TASK',name));return {'doctype':'Task','name':T,'project':P,'status':self.status,'modified':self.modified,'custom_agentic_managed':1,'custom_agov_dispatch_released':1,'custom_agov_active_run':'old-original-run'}
    async def request(self,method,path,**kwargs):
        self.calls.append((method,path,deepcopy(kwargs)))
        assert method=='GET' and path.endswith('get_result_receipt')
        return {'message':{'exists':False} if not self.exists else {'exists':True,**deepcopy(self.receipt),'content_sha256':self.native_sha}}
    async def publish(self,event):
        self.calls.append(('POST',deepcopy(event)))
        assert self.status=='Open'
        assert event['expected_modified']==self.modified
        assert event['work_packet_digest']==(await self.packet())['content_sha256']
        self.native_sha=self.f.b.digest(event);self.status='Working';self.modified='fixture-native-started';self.exists=True
        self.receipt={key:event[key] for key in ('event_id','task_id','run_id')};self.receipt.update(modified=self.modified,status='Working',durable=True,duplicate=False)
        if self.lost_ack:self.lost_ack=False;raise TimeoutError('Controlled native ACK lost after commit')
        return deepcopy(self.receipt)


class DeniedReadiness(Fixture):
    def candidate(self,name):
        path=HERE/(name+'.py')
        if not path.is_file():path=ROOT/(name+'.py')
        full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,path);module=importlib.util.module_from_spec(spec);sys.modules[full]=module;setattr(self.utils,name,module);spec.loader.exec_module(module);return module
    def setUp(self):
        super().setUp();self.b=self.bridge_module;self.start=self.candidate('agentic_start_recovery');self.sessions=self.load('agentic_gateway_sessions');self.store=self.candidate('agentic_result_store')
        with self.sessions.connect(self.path):pass
        self.provider_path=self.path.parent/'abitdot-primary.sqlite'
        with sqlite3.connect(self.provider_path) as db:
            db.execute('CREATE TABLE dot_primary_runs(original_run_id TEXT)')
        with self.g.connect(self.path)as db:self.store._schema(db)
        self.m=self.load('agentic_maintenance');self.gate_db=self.m.path(self.path.parent)
        self.m.control('close',expected_epoch=0,command_id='fixture-close',actor='fixture',db_path=self.gate_db)
        self.m.control('seal',expected_epoch=1,command_id='fixture-seal',actor='fixture',db_path=self.gate_db)
        self.m.configure_scope('restrict-project',expected_epoch=2,command_id='fixture-scope',actor='fixture',project_id=P,db_path=self.gate_db)
        self.m.control('open',expected_epoch=2,command_id='fixture-open',actor='fixture',db_path=self.gate_db)
        self.m.control('close',expected_epoch=3,command_id='fixture-restart-close',actor='fixture',db_path=self.gate_db)
        self.m.control('open',expected_epoch=4,command_id='fixture-restart-open',actor='fixture',db_path=self.gate_db)
        self.ident='invocation-denied-readiness-fixture';self.packet_value=self.packet();self.packet_value['source_modified']='fixture-before-blocked'
        tasks=[{'task_id':T,'owner_context_sha256':'b'*64,'status':'Blocked','source_modified':'fixture-before-blocked','subject':'same original'}, {'task_id':'TASK-FIXTURE-OTHER','status':'Open','source_modified':'other-unchanged','subject':'other original'}]
        self.packet_value['project_backlog']={'complete':True,'context_sha256':'9'*64,'tasks':tasks,'snapshot_sha256':self.b.digest({'project_id':P,'tasks':tasks})}
        self.seal_packet(self.packet_value)
        self.g.enqueue(invocation_id=self.ident,project=P,task=T,from_role='project_manager',target_role='developer',task_revision=self.packet_value['task_revision'],context='Finish the same exact prepared delivery',work_packet=self.packet_value,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        run=self.b.stable_run_id(self.ident);self.row=self.g.claim_invocation(self.ident,run,gate=self.gate_value,db_path=self.path)
        self.event={'schema_version':1,'event_id':'agov-start-'+self.b.digest(self.ident)[:48],'type':'run.started','project_id':P,'task_id':T,'task_revision':self.packet_value['task_revision'],'run_id':run,'invocation_id':self.ident,'role':'developer','agent_id':self.row['agent_id'],'summary':'Original governed execution claimed.','expected_modified':self.packet_value['source_modified'],'expected_project_revision':self.packet_value['project_revision'],'work_packet_digest':self.b.packet_digest(self.packet_value),'artifacts':[],'previous_run_id':'old-original-run'}
        self.start.record_start_intent(self.row,self.event,self.path);self.g.finish_execution(self.ident,run,self.row['lease_token'],'uncertain',reason=self.start.DENIED_REASON,db_path=self.path);self.row=self.g.find(self.ident,self.path,include_private=True)
        with self.g.connect(self.path)as db:self.old_entry=dict(db.execute('SELECT * FROM governance_start WHERE invocation_id=?',(self.ident,)).fetchone())
        self.bridge=ReadinessBridge(self)

        captured=self.old_entry['created_at']
        self.proof={'schema_version':1,'purpose':self.start.READINESS_POLICY,'invocation_id':self.ident,
                    'run_id':run,'event_id':self.event['event_id'],'event_sha256':self.old_entry['event_sha256'],
                    'denial':{'status':409,'error_type':'GovernanceValidationError','message':'Task is not ready for a new execution','reason_sha256':self.b.digest(self.start.DENIED_REASON),'control_sequence':9,'control_task':''},'core':{'no_model_proof_sha256':'8'*64},
                    'native':{'complete':True,'source_sha256':self.start.NATIVE_SOURCE,'policy_sha256':self.start.NATIVE_POLICY,'run_id':run,'event_id':self.event['event_id'],'event_sha256':self.old_entry['event_sha256'],'active_run_is_target':False,'all_run_records_count':0,'counts':dict.fromkeys(('run_started','run_context','result','inbox','result_receipt'),0),'raw_proof_sha256':'7'*64,'captured_at':captured}}
        with self.g.connect(self.path) as db:
            self.intents._schema(db)
            db.execute("INSERT INTO governance_native_control VALUES(?,?,10,'signed-retry-10','retry','enabled','acknowledged')",(P,''))
            with patch.object(self.g.time,'time',return_value=captured+1):
                self.g._event(db,P,None,'native_control_applied','human-fixture',{'command_id':'signed-retry-10','control_sequence':10,'action':'retry'})
        self.proof_patch=patch.object(self.start,'_read_readiness_proof',side_effect=lambda row,entry:(deepcopy(self.proof),self.b.digest(self.proof)))
        self.proof_patch.start();self.addCleanup(self.proof_patch.stop)

    def seal_packet(self,value):
        value.pop('content_sha256',None);value.pop('packet_id',None)
        value['packet_id']='erp-packet-'+self.b.digest(value)[:48]
        value['content_sha256']=self.b.packet_digest(value)
        return value

    async def confirm(self):
        return await self.start.get_confirmed_start(self.row,self.bridge,self.gate,{},self.path)

    def posts(self):return [x[1] for x in self.bridge.calls if x[0]=='POST']

    def audits(self,kind):
        with self.g.connect(self.path) as db:
            return [json.loads(x['payload']) for x in db.execute('SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',(self.ident,kind))]

    def unchanged_original(self):
        current=self.g.find(self.ident,self.path,include_private=True)
        for key in ('automation_run_id','lease_token','lease_fence','packet_hash','work_packet','dispatch_attempts','submitted_at'):
            self.assertEqual(current[key],self.row[key],key)
        with self.g.connect(self.path) as db:
            entry=dict(db.execute('SELECT * FROM governance_start WHERE invocation_id=?',(self.ident,)).fetchone())
        for key in set(self.old_entry)-{'receipt_json','updated_at'}:self.assertEqual(entry[key],self.old_entry[key],key)
        self.assertEqual(len(self.g.list_invocations(P,self.path)),1)

    def result_and_event(self):
        result={'schema_version':1,'invocation_id':self.ident,'actor':deepcopy(self.packet_value['binding']),
                'project_id':P,'task_id':T,'task_revision':self.packet_value['task_revision'],
                'work_packet_digest':self.b.packet_digest(self.packet_value),'outcome':'continue',
                'summary':'Exact original delivery checkpoint; no new implementation.', 'tests':[],
                'evidence':[],'defects':[],'implementation_invocation_id':None,'implementation_agent_id':None}
        event={**self.event,'type':'run.progress','event_id':'agov-result-fixture-original',
               'result_contract':deepcopy(result),'summary':result['summary']}
        return result,event

    async def test_human_readiness_posts_once_and_preserves_core_original(self):
        receipt=await self.confirm()
        self.assertEqual(receipt,self.bridge.receipt);self.assertEqual(len(self.posts()),1)
        post=self.posts()[0]
        changed={k for k in set(self.event)|set(post) if self.event.get(k)!=post.get(k)}
        self.assertEqual(changed,{'expected_modified','work_packet_digest'})
        self.assertEqual(post['event_id'],self.event['event_id'])
        self.assertEqual(len(self.audits(self.start.READINESS_KIND)),1)
        self.assertEqual(len(self.audits('original_start_readiness_receipt')),1)
        self.assertEqual(self.start.native_packet_digest(self.row,self.packet_value,db_path=self.path),post['work_packet_digest'])
        self.unchanged_original()
        claimed=self.start.reclaim_same(self.ident,receipt,gate=self.gate_value,db_path=self.path)
        self.assertEqual(claimed['state'],'running');self.unchanged_original()
        self.assertIsNone(self.start.reclaim_same(self.ident,receipt,gate=self.gate_value,db_path=self.path))
        self.assertEqual(len(self.posts()),1)

    async def test_dead_maintenance_owner_reclaims_same_run_at_open5(self):
        old=self.m._OWNED[(str(self.gate_db),'governed',self.ident)]
        dead=subprocess.Popen([sys.executable,'-c','pass']);dead.wait(timeout=5)
        with self.m._db(self.gate_db) as db:
            db.execute('UPDATE lease SET pid=?,process_start=? WHERE id=?',(dead.pid,'dead-owner-birth',old.id))
            before=dict(db.execute('SELECT * FROM lease WHERE id=?',(old.id,)).fetchone())
        self.m._OWNED.clear()
        receipt=await self.confirm()
        recovered=self.start.reclaim_same(self.ident,receipt,gate=self.gate_value,db_path=self.path)
        self.assertEqual(recovered['state'],'running');self.assertEqual(self.m.status(self.gate_db)['epoch'],5)
        new=self.m.require_active('governed',self.ident,db_path=self.gate_db)
        self.assertNotEqual(new['lease_id'],old.id)
        with self.m._db(self.gate_db,write=False) as db:
            prior=dict(db.execute('SELECT * FROM lease WHERE id=?',(old.id,)).fetchone())
            replacement=dict(db.execute('SELECT * FROM lease WHERE id=?',(new['lease_id'],)).fetchone())
        self.assertEqual(prior['state'],'finished');self.assertEqual(replacement['parent_id'],old.id)
        self.assertEqual(replacement['pid'],os.getpid());self.assertEqual(replacement['state'],'active')
        for key in ('id','pid','process_start','created_ns','token_sha256','epoch','reference'):
            self.assertEqual(prior[key],before[key])
        self.unchanged_original();self.assertEqual(len(self.posts()),1)

    async def test_lost_ack_reconciles_amended_hash_get_only(self):
        self.bridge.lost_ack=True
        with self.assertRaises(TimeoutError):await self.confirm()
        self.assertEqual(len(self.posts()),1);self.assertEqual(len(self.audits(self.start.READINESS_KIND)),1)
        with self.assertRaisesRegex(ValueError,'positive start receipt'):
            self.start.native_packet_digest(self.row,self.packet_value,db_path=self.path)
        receipt=await self.confirm();self.assertEqual(receipt,self.bridge.receipt)
        self.assertEqual(await self.confirm(),receipt)
        self.assertEqual(len(self.posts()),1);self.assertEqual(len(self.audits('original_start_readiness_receipt')),1)
        self.unchanged_original()

    async def test_unknown_amended_ack_never_posts_twice_or_changes_epoch(self):
        self.bridge.lost_ack=True
        with self.assertRaises(TimeoutError):await self.confirm()
        self.bridge.exists=False
        self.assertIsNone(await self.confirm())
        self.bridge.modified='another-human-save'
        self.assertIsNone(await self.confirm())
        self.assertEqual(len(self.posts()),1);self.assertEqual(len(self.audits(self.start.READINESS_KIND)),1)
        self.unchanged_original()

    async def test_missing_or_invalid_native_denial_proof_and_control_are_inert(self):
        with patch.object(self.start,'_read_readiness_proof',return_value=None):self.assertIsNone(await self.confirm())
        for mutate in (lambda p:p['native'].__setitem__('complete',False),
                       lambda p:p['native']['counts'].__setitem__('inbox',1),
                       lambda p:p['native'].__setitem__('all_run_records_count',1),
                       lambda p:p['native'].__setitem__('event_sha256','0'*64),
                       lambda p:p['denial'].__setitem__('status',503),
                       lambda p:p['denial'].__setitem__('reason_sha256','0'*64),
                       lambda p:p['native'].__setitem__('captured_at',self.old_entry['created_at']-1)):
            saved=deepcopy(self.proof);mutate(self.proof)
            with self.assertRaises(ValueError):await self.confirm()
            self.proof=saved
        with self.g.connect(self.path) as db:db.execute("UPDATE governance_native_control SET phase='applied_pending_ack'")
        self.assertIsNone(await self.confirm())
        self.assertEqual(self.posts(),[]);self.assertEqual(self.audits(self.start.READINESS_KIND),[]);self.unchanged_original()

    async def test_changed_context_or_non_open_task_denies_before_amendment(self):
        def other_backlog(p):p['project_backlog']['tasks'][1]['subject']='foreign edit'
        changes=[lambda p:p['srs'].__setitem__('revision','changed'),lambda p:p['activity'].append({'content':'new comment'}),
                 lambda p:p['binding'].__setitem__('agent_id','foreign'),lambda p:p.__setitem__('instructions','changed'),other_backlog,
                 lambda p:p['project_backlog'].__setitem__('context_sha256','0'*64)]
        for change in changes:
            self.bridge.changed=change
            with self.assertRaises(ValueError):await self.confirm()
        self.bridge.changed=None
        for status in ('Blocked','Working','QA REJECTED'):
            self.bridge.status=status
            self.assertIsNone(await self.confirm())
        self.assertEqual(self.posts(),[]);self.assertEqual(self.audits(self.start.READINESS_KIND),[]);self.unchanged_original()

    async def test_draining_startup_and_no_new_retry_leave_original_inert(self):
        self.m.control('close',expected_epoch=5,command_id='fixture-draining-startup',actor='fixture',db_path=self.gate_db)
        self.assertIsNone(await self.confirm())
        self.assertEqual(self.posts(),[]);self.assertEqual(self.audits(self.start.READINESS_KIND),[])
        self.assertEqual(self.g.find(self.ident,self.path,include_private=True)['reason'],self.start.DENIED_REASON)
        self.m.control('open',expected_epoch=6,command_id='fixture-open-after-startup',actor='fixture',db_path=self.gate_db)
        with self.g.connect(self.path) as db:db.execute('UPDATE governance_native_control SET sequence=9')
        self.assertIsNone(await self.confirm())
        self.assertEqual(self.posts(),[]);self.assertEqual(self.audits(self.start.READINESS_KIND),[])
        self.assertEqual(self.g.find(self.ident,self.path,include_private=True)['reason'],self.start.DENIED_REASON)
        self.unchanged_original()

    async def test_missing_provider_ledger_denies_without_creating_storage(self):
        self.provider_path.unlink()
        with self.assertRaisesRegex(ValueError,'provider ledger is unavailable'):
            await self.confirm()
        self.assertFalse(self.provider_path.exists())
        self.assertEqual(self.posts(),[])
        self.assertEqual(self.audits(self.start.READINESS_KIND),[])
        self.unchanged_original()

    async def test_submitted_session_provider_result_and_revoked_scope_deny(self):
        for column,value in [('submitted_at',123),('dispatch_attempts',2),('reason','unknown native ACK')]:
            with self.g.connect(self.path) as db:db.execute('UPDATE governance_invocation SET '+column+'=? WHERE invocation_id=?',(value,self.ident))
            observed=self.g.find(self.ident,self.path,include_private=True)
            try:
                with self.assertRaises(ValueError) if column!='submitted_at' else _NoRaise():
                    out=await self.start.get_confirmed_start(observed,self.bridge,self.gate,{},self.path)
                    self.assertIsNone(out)
            finally:
                with self.g.connect(self.path) as db:db.execute('UPDATE governance_invocation SET '+column+'=? WHERE invocation_id=?',(self.row[column],self.ident))
        with self.g.connect(self.path) as db:
            db.execute("INSERT INTO governance_gateway_session VALUES(?,?, '{}','{}','x',NULL,'CLOSED',1)",('session-fixture',self.ident))
        with self.assertRaisesRegex(ValueError,'gateway session'):await self.confirm()
        with self.g.connect(self.path) as db:db.execute('DELETE FROM governance_gateway_session')
        with sqlite3.connect(self.provider_path) as db:
            db.execute('INSERT INTO dot_primary_runs VALUES(?)',(self.row['automation_run_id'],))
        with self.assertRaisesRegex(ValueError,'provider receipt'):await self.confirm()
        with sqlite3.connect(self.provider_path) as db:
            db.execute('DELETE FROM dot_primary_runs')
        self.store.save_local_result(self.ident,self.row['automation_run_id'],{'fixture':'already returned'},self.path)
        self.assertIsNone(await self.confirm())
        with self.g.connect(self.path) as db:db.execute('DELETE FROM governance_local_result')
        self.allowed=False
        with self.assertRaisesRegex(ValueError,'revoked'):await self.confirm()
        self.allowed=True
        self.gate_value['pm_task_automation_enabled']=False
        with self.assertRaises(ValueError):await self.confirm()
        self.assertEqual(self.posts(),[]);self.assertEqual(self.audits(self.start.READINESS_KIND),[]);self.unchanged_original()

    async def test_result_projection_is_only_transport_digests_and_atomic_once(self):
        await self.confirm();result,event=self.result_and_event();original_result=deepcopy(result);original_event=deepcopy(event)
        with self.g.connect(self.path) as db:
            with self.assertRaisesRegex(ValueError,'unchanged validated local result'):
                self.start.project_native_result(self.row,self.packet_value,result,event,db=db)
        self.store.save_local_result(self.ident,self.row['automation_run_id'],result,self.path)
        self.assertTrue(self.store.save_result(self.ident,self.row['automation_run_id'],result,event,self.path))
        self.assertFalse(self.store.save_result(self.ident,self.row['automation_run_id'],result,event,self.path))
        with self.g.connect(self.path) as db:
            saved=dict(db.execute('SELECT * FROM governance_result WHERE invocation_id=?',(self.ident,)).fetchone())
        projected=json.loads(saved['event_json']);expected=deepcopy(event)
        expected['work_packet_digest']=self.posts()[0]['work_packet_digest'];expected['result_contract']['work_packet_digest']=expected['work_packet_digest']
        self.assertEqual(projected,expected);self.assertEqual(json.loads(saved['result_json']),result)
        self.assertEqual(result,original_result);self.assertEqual(event,original_event)
        audits=self.audits('readiness_native_result');self.assertEqual(len(audits),1)
        self.assertEqual(audits[0]['pointers'],['/work_packet_digest','/result_contract/work_packet_digest'])
        self.assertEqual(audits[0]['result_sha256'],self.b.digest(result))
        changed=deepcopy(result);changed['summary']='forged'
        with self.assertRaises(ValueError):self.store.save_result(self.ident,self.row['automation_run_id'],changed,{**event,'result_contract':changed},self.path)
        self.unchanged_original()

    async def test_result_audit_failure_rolls_back_callback_and_modified_receipt_denies(self):
        await self.confirm();result,event=self.result_and_event()
        self.store.save_local_result(self.ident,self.row['automation_run_id'],result,self.path)
        with self.g.connect(self.path) as db:
            db.execute("CREATE TRIGGER deny_readiness_audit BEFORE INSERT ON governance_event WHEN NEW.kind='readiness_native_result' BEGIN SELECT RAISE(ABORT,'controlled audit failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'controlled audit failure'):
            self.store.save_result(self.ident,self.row['automation_run_id'],result,event,self.path)
        with self.g.connect(self.path) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM governance_result WHERE invocation_id=?',(self.ident,)).fetchone())
            self.assertEqual(self.audits('readiness_native_result'),[])
            db.execute('DROP TRIGGER deny_readiness_audit')
            with self.assertRaisesRegex(sqlite3.IntegrityError,'append-only'):
                db.execute("UPDATE governance_event SET payload='{}' WHERE invocation_id=? AND kind='original_start_readiness_receipt'",(self.ident,))
            self.g._event(db,P,self.ident,'original_start_readiness_receipt','counterfeit-fixture',{})
        with self.assertRaisesRegex(ValueError,'atomic positive receipt audit'):
            self.store.save_result(self.ident,self.row['automation_run_id'],result,event,self.path)
        self.assertEqual(self.audits('readiness_native_result'),[]);self.unchanged_original()


class _NoRaise:
    def __enter__(self):return self
    def __exit__(self,*args):return False


if __name__=='__main__':unittest.main()

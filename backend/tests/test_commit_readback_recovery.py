"""Finite original Git message/readback and one authorized successor checks.
Real temporary SQLite, loopback Git fixture only. No live/GitHub/ERP/model calls.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import time
import sqlite3
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
sys.path.insert(0,str(BACKEND/'tests'))
import test_original_delivery_continuation as original


class CommitReadbackRecovery(unittest.IsolatedAsyncioTestCase):
    # Reuse only fixture operations, without inheriting its unrelated test suite.
    continue_original=original.OriginalDeliveryContinuation.continue_original
    authority=original.OriginalDeliveryContinuation.authority
    status=original.OriginalDeliveryContinuation.status
    resume=original.OriginalDeliveryContinuation.resume
    contract=original.OriginalDeliveryContinuation.contract
    async def asyncSetUp(self):
        self.proposal=patch.object(original,'HERE',HERE)
        self.proposal.start();self.addCleanup(self.proposal.stop)
        prior=original.fixture_delivery.Delivery.packet
        def packet_with_backlog(f,*args,**kwargs):
            value=prior(f,*args,**kwargs)
            tasks=[{'task_id':value['task_id'],'status':'Open','source_modified':value['source_modified'],'subject':'Exact original implementation'}]
            value['project_backlog']={'complete':True,'context_sha256':'9'*64,'tasks':tasks,'snapshot_sha256':f.bridge_module.digest({'project_id':value['project_id'],'tasks':tasks})}
            value['content_sha256']=f.bridge_module.packet_digest(value)
            return value
        self.packet_patch=patch.object(original.fixture_delivery.Delivery,'packet',packet_with_backlog)
        self.packet_patch.start();self.addCleanup(self.packet_patch.stop)
        prior_seed=original.fixture_delivery.Delivery.seed_test
        async def seed_two_files(f,*args,**kwargs):
            (f.repo/'src/test_app.py').write_bytes(b'assert 1 + 1 == 2\n')
            return await prior_seed(f,*args,**kwargs)
        self.seed_patch=patch.object(original.fixture_delivery.Delivery,'seed_test',seed_two_files)
        self.seed_patch.start();self.addCleanup(self.seed_patch.stop)
        await original.OriginalDeliveryContinuation.asyncSetUp(self)

    async def prepare_original(self):
        self.f.remote.drop='/git/commits'
        held=await self.f.deliver();self.assertEqual(held['state'],'pending_reconciliation')
        self.assertEqual(self.f.remote.posts().count('/git/commits'),1)
        with self.m._db(self.path)as db:
            step=db.execute("SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key='commit'",(self.original['invocation_id'],)).fetchone()
            body=json.loads(step['request_json'])['body'];predicted=self.m._commit_hash(body)
        self.commit_body=deepcopy(body);self.commit_sha=predicted
        self.assertTrue(body['message'].endswith('\n'))
        self.f.remote.commits[predicted]['message']=body['message'][:-1]
        posts=self.f.remote.posts().copy();status=await self.f.reconcile()
        self.assertEqual(status['state'],'reconciled');self.assertEqual(self.f.remote.posts(),posts)
        with self.m._db(self.path)as db:
            row=db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',(self.original['invocation_id'],)).fetchone()
            self.original_input=json.loads(row['input_json'])
            self.original_steps=[dict(x)for x in db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? ORDER BY step_key',(self.original['invocation_id'],))]
        self.assertEqual(len(self.original_steps),4)
        return status

    def assert_original_journals(self):
        with self.m._db(self.path)as db:
            saved=json.loads(db.execute('SELECT input_json FROM governance_developer_delivery WHERE invocation_id=?',(self.original['invocation_id'],)).fetchone()[0])
            self.assertEqual(saved,self.original_input)
            for old in self.original_steps:
                self.assertEqual(dict(db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?',(old['invocation_id'],old['step_key'])).fetchone()),old)
            self.assertEqual({x[0]for x in db.execute('SELECT DISTINCT invocation_id FROM governance_delivery_step')},{self.original['invocation_id']})

    async def test_single_terminal_lf_cached_readback_finishes_without_recreating_original_objects(self):
        await self.prepare_original();posts=self.f.remote.posts().copy()
        ready=await self.f.deliver();self.assertEqual(ready['state'],'ready_for_review')
        self.assertEqual(self.f.remote.posts().count('/git/commits'),1)
        self.assertEqual(self.f.remote.posts().count('/git/blobs'),2)
        self.assertEqual(self.f.remote.posts().count('/git/trees'),1)
        self.assertEqual(self.f.remote.posts().count('/git/refs'),1)
        self.assertEqual(self.f.remote.posts().count('/pulls'),1)
        self.assertEqual(self.f.remote.posts()[:len(posts)],posts)
        self.assert_original_journals()

    async def test_message_tree_parent_and_commit_identity_drift_still_deny(self):
        await self.prepare_original();remote=self.f.remote.commits[self.commit_sha];saved=deepcopy(remote)
        async def request(method,path,*args):
            self.assertEqual((method,path),('GET','/git/commits/'+self.commit_sha));return deepcopy(remote)
        client=type('ReadOnlyClient',(),{'request':staticmethod(request)})()
        for mutate in (lambda v:v.__setitem__('message',self.commit_body['message'].replace('Snapshot:','snapshot:')),
                       lambda v:v.__setitem__('message',self.commit_body['message'].rstrip('\n')+' '),
                       lambda v:v.__setitem__('message',self.commit_body['message'].replace('\n','\r\n')),
                       lambda v:v.__setitem__('message',self.commit_body['message']+'\n'),
                       lambda v:v['tree'].__setitem__('sha','f'*40),
                       lambda v:v.__setitem__('parents',[{'sha':'f'*40}]),
                       lambda v:v.__setitem__('sha','f'*40)):
            remote.clear();remote.update(deepcopy(saved));mutate(remote)
            with self.assertRaises(self.m.RemoteUnknown):await self.m._commit_result(client,{'sha':self.commit_sha},self.commit_body)
        remote.clear();remote.update(saved)
        with self.assertRaises(self.m.RemoteUnknown):await self.m._commit_result(client,{'sha':'f'*40},self.commit_body)
        self.assert_original_journals()

    async def blocked_after_consumed_recovery(self):
        await self.prepare_original()
        first=await self.continue_original(self.original,self.original_packet)
        self.current=await self.continue_original(first,self.original_packet,outcome='blocked',kind='delivery')
        self.current_packet=deepcopy(self.original_packet);self.f.row=self.current;self.f.packet_value=self.current_packet
        result={'schema_version':1,'invocation_id':self.current['invocation_id'],'actor':deepcopy(self.current_packet['binding']),
                'project_id':self.current['erp_project'],'task_id':self.current['erp_task'],'task_revision':self.current_packet['task_revision'],
                'work_packet_digest':self.b.packet_digest(self.current_packet),'outcome':'blocked','summary':'Original commit readback is blocked only by single terminal LF presentation.',
                'test_results':[],'evidence':[],'implementation_invocation_id':None,'implementation_agent_id':None,'defects':[]}
        event={'schema_version':1,'event_id':'agov-result-'+self.b.digest([self.current['invocation_id'],result])[:48],
               'type':'run.blocked','project_id':self.current['erp_project'],'task_id':self.current['erp_task'],
               'task_revision':self.current['task_revision'],'run_id':self.current['automation_run_id'],'invocation_id':self.current['invocation_id'],
               'role':'developer','agent_id':self.current['agent_id'],'work_packet_digest':self.b.packet_digest(self.current_packet),
               'result_contract':result,'artifacts':[],'acceptance_passed':False}
        self.store.save_result(self.current['invocation_id'],self.current['automation_run_id'],result,event,self.path)
        self.g.finish_execution(self.current['invocation_id'],self.current['automation_run_id'],self.current['lease_token'],'execution_finished',db_path=self.path)
        self.store.publication_receipt(self.current['invocation_id'],{'event_id':event['event_id'],'task_id':self.current['erp_task'],'run_id':self.current['automation_run_id'],'modified':'fixture-blocked-after-readback','status':'Blocked','durable':True},self.path)
        self.blocked=self.g.find(self.current['invocation_id'],self.path,include_private=True)
        self.blocked_result=result
        with self.g.connect(self.path)as db:
            saved=db.execute('SELECT * FROM governance_result WHERE invocation_id=?',(self.blocked['invocation_id'],)).fetchone()
            commit=next(x for x in self.original_steps if x['step_key']=='commit')
            self.proof={'schema_version':1,'purpose':self.m.DELIVERY_RECOVERY_POLICY,
                        'blocked_invocation_id':self.blocked['invocation_id'],'blocked_run_id':self.blocked['automation_run_id'],
                        'blocked_result_sha256':saved['result_sha256'],'native_block_receipt_sha256':self.b.digest(json.loads(saved['receipt_json'])),'original_invocation_id':self.original['invocation_id'],
                        'original_input_sha256':self.b.digest(self.original_input),'delivery_source_sha256':hashlib.sha256(Path(self.m.__file__).read_bytes()).hexdigest(),
                        'completed_steps':sorted(x['step_key']for x in self.original_steps),
                        'commit_request_sha256':self.b.digest(json.loads(commit['request_json'])),'commit_sha':self.commit_sha,
                        'commit_GET_proof_sha256':'8'*64,'minimum_control_sequence':10,'control_task':''}
            self.blocked_updated_at=saved['updated_at']
            self.f.intents._schema(db)
            db.execute("INSERT INTO governance_native_control VALUES(?,?,11,'signed-retry-11','retry','enabled','acknowledged')",(self.blocked['erp_project'],''))
            with patch.object(self.g.time,'time',return_value=self.blocked_updated_at+1):
                self.g._event(db,self.blocked['erp_project'],None,'native_control_applied','human-fixture',{'command_id':'signed-retry-11','control_sequence':11,'action':'retry'})
        self.authorized_patch=patch.object(self.m,'_read_delivery_recovery_authorization',side_effect=lambda row:(deepcopy(self.proof),self.b.digest(self.proof)))
        self.authorized_patch.start();self.addCleanup(self.authorized_patch.stop)
        self.fresh=deepcopy(self.current_packet);self.fresh['source_modified']='fixture-human-open-after-blocked'
        self.fresh['project_backlog']['tasks'][0]['status']='Open';self.fresh['project_backlog']['tasks'][0]['source_modified']=self.fresh['source_modified']
        self.fresh['project_backlog']['snapshot_sha256']=self.b.digest({'project_id':self.blocked['erp_project'],'tasks':self.fresh['project_backlog']['tasks']})
        self.fresh['content_sha256']=self.b.packet_digest(self.fresh)

    def resolve_successor(self):
        return self.orchestrator._resume(self.blocked,self.fresh,self.f.gate_value,self.path,[],'delivery',delivery_config=self.f.config)

    def authorization_events(self):
        with self.g.connect(self.path)as db:return [dict(x)for x in db.execute("SELECT * FROM governance_event WHERE kind='delivery_checkpoint_authorized'")]

    async def test_default_off_stale_control_wrong_source_result_and_scope_cannot_resume(self):
        await self.blocked_after_consumed_recovery();posts=self.f.remote.posts().copy()
        with patch.object(self.m,'_read_delivery_recovery_authorization',return_value=None):
            with self.assertRaises(ValueError):self.resolve_successor()
        for key,value in [('delivery_source_sha256','0'*64),('native_block_receipt_sha256','0'*64),('control_task','TASK-OTHER'),('blocked_result_sha256','0'*64),('blocked_run_id','wrong-original'),('original_input_sha256','0'*64),('commit_request_sha256','0'*64),('commit_sha','f'*40)]:
            old=self.proof[key];self.proof[key]=value
            with self.assertRaises(ValueError):self.resolve_successor()
            self.proof[key]=old
        for query,args in [("UPDATE governance_native_control SET sequence=10",()),("UPDATE governance_native_control SET phase='applied_pending_ack'",())]:
            with self.g.connect(self.path)as db:db.execute(query,args)
            with self.assertRaises(ValueError):self.resolve_successor()
            with self.g.connect(self.path)as db:db.execute("UPDATE governance_native_control SET sequence=11,phase='acknowledged'")
        self.fresh['project_backlog']['tasks'][0]['status']='Blocked'
        with self.assertRaises(ValueError):self.resolve_successor()
        self.fresh['project_backlog']['tasks'][0]['status']='Open'
        before=deepcopy(self.fresh)
        for mutate in (lambda p:p['srs'].__setitem__('sha256','0'*64),lambda p:p['binding'].__setitem__('agent_id','foreign'),lambda p:p.__setitem__('task_revision','changed')):
            mutate(self.fresh)
            with self.assertRaises(ValueError):self.resolve_successor()
            self.fresh=deepcopy(before)
        self.assertEqual(self.authorization_events(),[]);self.assertEqual(self.f.remote.posts(),posts)
        self.assertEqual(self.g.find(self.blocked['invocation_id'],self.path,include_private=True)['state'],'execution_finished')
        self.assert_original_journals()

    async def test_newer_retry_creates_one_deterministic_successor_and_atomic_audit(self):
        await self.blocked_after_consumed_recovery();before=self.f.remote.posts().copy()
        successor,created=self.resolve_successor();self.assertTrue(created)
        again,created=self.resolve_successor();self.assertFalse(created);self.assertEqual(again['invocation_id'],successor['invocation_id'])
        self.assertEqual(successor['state'],'queued');self.assertEqual(successor['dispatch_attempts'],0);self.assertIsNone(successor['automation_run_id']);self.assertIsNone(successor['submitted_at'])
        self.assertEqual(self.g.find(successor['invocation_id'],self.path,include_private=True)['source_invocation_id'],self.blocked['invocation_id'])
        audits=self.authorization_events();self.assertEqual(len(audits),1);value=json.loads(audits[0]['payload'])
        self.assertEqual(value['blocked_invocation_id'],self.blocked['invocation_id']);self.assertEqual(value['original_delivery'],self.original['invocation_id']);self.assertEqual(value['commit_sha'],self.commit_sha);self.assertEqual(value['control']['sequence'],11)
        self.assertEqual(self.f.remote.posts(),before);self.assert_original_journals()
        run=self.b.stable_run_id(successor['invocation_id']);self.current=self.g.claim_invocation(successor['invocation_id'],run,gate=self.f.gate_value,db_path=self.path);self.g.mark_submitted(self.current['invocation_id'],run,self.current['lease_token'],gate=self.f.gate_value,db_path=self.path)
        self.current=self.g.find(self.current['invocation_id'],self.path,include_private=True);self.current_packet=deepcopy(self.fresh);self.f.row=self.current;self.f.packet_value=self.current_packet
        self.f.remote.drop='/pulls';held=await self.resume();self.assertEqual(held['state'],'pending_reconciliation')
        posts=self.f.remote.posts().copy();status=await self.status();self.assertEqual(self.f.remote.posts(),posts)
        ready=await self.resume(status['arguments']);self.assertEqual(ready['state'],'ready_for_review');self.assertEqual(self.f.remote.posts(),posts)
        self.assertEqual(posts.count('/git/commits'),1);self.assertEqual(posts.count('/git/refs'),1);self.assertEqual(posts.count('/pulls'),1);self.assert_original_journals()

    async def test_authorization_audit_failure_rolls_back_handoff_and_successor(self):
        await self.blocked_after_consumed_recovery()
        with self.g.connect(self.path)as db:
            count=db.execute('SELECT count(*)FROM governance_invocation').fetchone()[0]
            db.execute("CREATE TRIGGER deny_delivery_authorization BEFORE INSERT ON governance_event WHEN NEW.kind='delivery_checkpoint_authorized'BEGIN SELECT RAISE(ABORT,'controlled authorization failure');END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'controlled authorization failure'):self.resolve_successor()
        with self.g.connect(self.path)as db:self.assertEqual(db.execute('SELECT count(*)FROM governance_invocation').fetchone()[0],count)
        self.assertEqual(self.g.find(self.blocked['invocation_id'],self.path,include_private=True)['state'],'execution_finished');self.assertEqual(self.authorization_events(),[]);self.assert_original_journals()


if __name__=='__main__':unittest.main()


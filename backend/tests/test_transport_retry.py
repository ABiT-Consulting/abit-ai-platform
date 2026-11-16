"""Explicit failed outgoing retry uses a new generation, never replays an unknown."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import test_original_dot_rejection as negative_tests
from test_agentic_controls_events import P,T


class NativeBridge:
    def __init__(self,f):
        self.f=f;self.erp=self;self.secret='fixture-only';self.posts=0
        self.cleared=False;self.receipt=None;self.lose_ack=False;self.reads=0

    async def packet(self,*args):
        p=self.f.packet();p['activity']=[{'id':'new-human-checkpoint','text':'Existing source-only auth patch: 542 tests passed.'}]
        p['source_modified']='after-transport-rejection' if self.cleared else 'modified-started'
        p['task_revision']='fresh-retry-revision' if self.cleared else 'with-human-checkpoint'
        p['content_sha256']=self.f.bridge_module.packet_digest(p)
        return p

    async def publish(self,event):
        self.posts+=1;self.cleared=True;r=event['transport_rejection']
        self.receipt={k:event[k]for k in ('event_id','task_id','run_id')}
        self.receipt.update(durable=True,duplicate=False,status='Open',modified='after-transport-rejection',
            not_submitted=True,proof_sha256=r['proof_sha256'],
            transport_rejection_sha256=self.f.bridge_module.digest(r),current_task_revision='fresh-retry-revision')
        if self.lose_ack:raise TimeoutError('Original ACK lost after Native commit')
        return deepcopy(self.receipt)

    async def request(self,method,path,**kwargs):
        assert method=='GET' and path.endswith('get_result_receipt')
        self.reads+=1
        return {'message':{'exists':True,**deepcopy(self.receipt),'content_sha256':'fixture-event-sha'}
                if self.receipt else {'exists':False}}


class TransportRetry(negative_tests.OriginalDotRejection):
    def packet(self,task=T,role='project_manager',project=P):
        return super().packet(task,role,project)

    def enqueue(self,ident='invocation-fixture-0001',task=T,role='project_manager',project=P):
        packet=self.packet(task,role,project)
        self.g.enqueue(invocation_id=ident,project=project,task=task,from_role='erp',
            target_role=role,task_revision=packet['task_revision'],context='Fixture adopted Task review',
            work_packet=packet,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        return self.g.find(ident,self.path,include_private=True)

    async def setup_original(self):
        locks=self.load('erp_status_write');locks.LOCK_ROOT=self.path.parent/'task-locks'
        _,envelope,proof=await self.original()
        ident=envelope['governance_scope']['scope']['invocation_id']
        run=self.bridge_module.stable_run_id(ident)
        row=self.g.claim_invocation(ident,run,gate=self.gate_value,db_path=self.path)
        self.start=self.load('agentic_start_recovery')
        packet=json.loads(row['work_packet'])
        event={'schema_version':1,'event_id':'agov-start-'+self.bridge_module.digest(ident)[:48],
            'type':'run.started','project_id':P,'task_id':T,'task_revision':packet['task_revision'],
            'run_id':run,'invocation_id':ident,'role':row['target_role'],'agent_id':row['agent_id'],
            'work_packet_digest':self.bridge_module.packet_digest(packet),'expected_modified':packet['source_modified'],
            'expected_project_revision':packet['project_revision'],'artifacts':[]}
        self.start.record_start_intent(row,event,self.path)
        receipt={k:event[k]for k in ('event_id','task_id','run_id')}
        receipt.update(durable=True,status='Working',modified='modified-started',duplicate=False)
        self.start.record_start_receipt(ident,receipt,self.path)
        self.g.mark_submitted(ident,run,row['lease_token'],gate=self.gate_value,db_path=self.path)
        self.g.finish_execution(ident,run,row['lease_token'],'uncertain',db_path=self.path)
        with self.primary.ledger(self.path.parent/'abitdot-primary.sqlite')as d:self.primary.claim(d,envelope)
        sessions=self.load('agentic_gateway_sessions')
        with sessions.connect(self.path)as d:
            d.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',
                ('gateway-fixture-session-001',ident,json.dumps(envelope['governance_scope']['scope']),
                 '{}','fixture-connection',None,'admitted',1))
        control=await self.event(kind='project.control',action='retry',task=T,identity='explicit-transport-retry-0001')
        self.retry_event=control['event_id'];self.transport=self.load('agentic_transport_retry')
        self.native=NativeBridge(self)
        self.adapter=SimpleNamespace(reject_transport=AsyncMock())
        self.automation=SimpleNamespace(id=row['agent_id'],user_id='owner-fixture',meta={
            'governance_managed':True,'agentic_project':P,'agentic_role':row['target_role'],
            'erp_role_user':self.users[row['target_role']]})
        self.observer=AsyncMock(return_value=({'status':'rejected_before_submit','original_rejection':proof},proof))
        return ident,envelope,proof

    async def retry(self,ident,event_id=None):
        return await self.transport.retry_rejected_transport(ident,event_id or self.retry_event,
            automation=self.automation,adapter=self.adapter,bridge=self.native,
            project_gate=self.gate,config={},db_path=self.path,observer=self.observer)

    async def test_explicit_retry_preserves_original_and_queues_fresh_generation_once(self):
        ident,envelope,_=await self.setup_original()
        new=await self.retry(ident)
        old=self.g.find(ident,self.path,include_private=True);fresh=self.g.find(new,self.path,include_private=True)
        self.assertEqual(old['state'],'failed');self.assertEqual(old['automation_run_id'],envelope['run_id'])
        self.assertEqual(fresh['state'],'queued');self.assertIsNone(fresh['submitted_at'])
        self.assertNotEqual(self.bridge_module.stable_run_id(new),old['automation_run_id'])
        self.assertEqual(json.loads(fresh['work_packet'])['activity'][0]['id'],'new-human-checkpoint')
        self.assertEqual(fresh['task_revision'],'fresh-retry-revision')
        self.assertEqual(await self.retry(ident),new)
        self.assertEqual(self.native.posts,1);self.observer.assert_awaited_once()
        self.adapter.reject_transport.assert_awaited_once()
        self.assertEqual(len(self.g.list_invocations(P,self.path)),2)
        with self.primary.ledger(self.path.parent/'abitdot-primary.sqlite')as d:
            saved=d.execute('SELECT * FROM dot_primary_runs').fetchone()
            self.assertEqual(json.loads(saved['envelope_json']),envelope)
            self.assertEqual(saved['status'],'unavailable')

    async def test_client_binding_or_any_tool_call_blocks_retry(self):
        ident,_,_=await self.setup_original()
        with self.g._transaction(self.path)as d:
            d.execute('UPDATE governance_gateway_session SET client_session_hash=?',('accepted-client',))
        with self.assertRaisesRegex(ValueError,'gateway accepted'):await self.retry(ident)
        self.assertEqual(self.native.posts,0);self.adapter.reject_transport.assert_not_awaited()
        self.assertEqual(self.g.find(ident,self.path)['state'],'uncertain')
        with self.g._transaction(self.path)as d:
            d.execute('UPDATE governance_gateway_session SET client_session_hash=NULL')
            d.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',
                ('gateway-fixture-session-001','existing-call','hash','context.read','completed','{}',1,1))
        with self.assertRaisesRegex(ValueError,'gateway accepted'):await self.retry(ident)
        self.assertEqual(self.native.posts,0)

    async def test_resume_or_superseded_retry_cannot_create_generation(self):
        ident,_,_=await self.setup_original()
        with self.g._transaction(self.path)as d:
            d.execute("UPDATE governance_native_control SET action='resume'")
        with self.assertRaisesRegex(ValueError,'superseded'):await self.retry(ident)
        self.observer.assert_not_awaited();self.assertEqual(self.native.posts,0)

    async def test_unknown_native_ack_is_get_only_and_new_generation_waits(self):
        ident,_,_=await self.setup_original();self.native.lose_ack=True
        with self.assertRaises(TimeoutError):await self.retry(ident)
        self.assertEqual(self.g.find(ident,self.path)['state'],'uncertain')
        self.assertEqual(len(self.g.list_invocations(P,self.path)),1)
        new=await self.retry(ident)
        self.assertTrue(new);self.assertEqual(self.native.posts,1);self.assertEqual(self.native.reads,1)
        self.assertEqual(len(self.g.list_invocations(P,self.path)),2)

    async def test_original_fence_change_or_task_pause_preserves_failed_transport_hold(self):
        ident,_,_=await self.setup_original()
        with self.g._transaction(self.path)as d:
            d.execute('UPDATE governance_task_lease SET fence=fence+1')
        with self.assertRaisesRegex(ValueError,'uncertain stable'):await self.retry(ident)
        self.assertEqual(self.native.posts,0)
        self.controls.task_control(P,T,'pause',actor='human@example.invalid',db_path=self.path)
        with self.assertRaises(ValueError):await self.retry(ident)
        self.assertEqual(self.native.posts,0)

    async def test_maintenance_settlement_requires_native_ack_and_preserves_other_unknown(self):
        ident,_,_=await self.setup_original()
        maintenance=self.load('agentic_maintenance');gate=maintenance.path(self.path.parent)
        other=maintenance.acquire('governed','unrelated-fixture-transport-0001',
            db_path=gate,governance_project=P)
        other.unresolved()
        with maintenance._db(gate,write=False)as d:
            before=dict(d.execute('SELECT * FROM lease WHERE id=?',(other.id,)).fetchone())
        with self.assertRaisesRegex(ValueError,'durable Native cleanup'):
            self.transport.settle_rejected_admission(ident,self.path)
        await self.retry(ident)
        with maintenance._db(gate,write=False)as d:
            after=dict(d.execute('SELECT * FROM lease WHERE id=?',(other.id,)).fetchone())
            old=d.execute('SELECT state FROM lease WHERE reference=?',(ident,)).fetchone()[0]
        self.assertEqual(old,'finished');self.assertEqual(before,after)



def load_tests(loader,tests,pattern):
    return unittest.TestSuite(TransportRetry(n)for n in (
        'test_explicit_retry_preserves_original_and_queues_fresh_generation_once',
        'test_client_binding_or_any_tool_call_blocks_retry',
        'test_resume_or_superseded_retry_cannot_create_generation',
        'test_unknown_native_ack_is_get_only_and_new_generation_waits',
        'test_original_fence_change_or_task_pause_preserves_failed_transport_hold',
        'test_maintenance_settlement_requires_native_ack_and_preserves_other_unknown'))

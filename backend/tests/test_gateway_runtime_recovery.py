import asyncio,ast,base64,copy,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from test_agentic_gateway_sessions import Gateway
from test_intermarket_gateway_prerequisites import PrerequisiteRetry,RepositoryFiles
from test_agentic_controls_events import P,T
ROOT=Path(__file__).parents[1]/'open_webui/utils'

class FastReceipts(Gateway):
 async def test_pending_and_completed_receipts_do_not_wait_for_original_lock_or_repeat_work(self):
  await self.admitted();started=asyncio.Event();release=asyncio.Event()
  async def original(*a,**k):
   self.calls+=1;started.set();await release.wait();return {'actual':True}
  self.registry['read.fixture']=self.m.Operation('inspect_infrastructure',frozenset({'developer'}),original)
  job=asyncio.create_task(self.m.call(self.body,**self.kwargs()))
  try:
   await started.wait()
   pending=await asyncio.wait_for(self.m.call(self.body,**self.kwargs()),.5)
   self.assertEqual(pending['state'],'uncertain');self.assertTrue(pending['replayed']);self.assertEqual(self.calls,1)
   for changed in ({**self.body,'client_session_hash':'d'*64},{**self.body,'arguments':{'changed':True}}):
    with self.assertRaises(ValueError):await asyncio.wait_for(self.m.call(changed,**self.kwargs()),.5)
  finally:release.set()
  completed=await job;self.assertEqual(completed['state'],'completed')
  self.assertEqual((await self.m.call(self.body,**self.kwargs()))['result'],{'actual':True});self.assertEqual(self.calls,1)
 async def test_HTTP_pending_response_retains_one_original_job_and_its_bridge(self):
  await self.admitted();body={**self.body,'operation':'repository.test'};release=asyncio.Event();closed=AsyncMock()
  async def work(*a,**k):await release.wait();return {'call_id':body['call_id'],'state':'completed','result':{'exit_code':0},'replayed':False}
  route=ROOT.parent/'routers/agentic_governance.py';tree=ast.parse(route.read_text());node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='gateway_call');node.decorator_list=[]
  retained=set()
  def spawn(app,coro):
   task=asyncio.create_task(coro);retained.add(task);task.add_done_callback(retained.discard);return task
  ns={'Request':object,'asyncio':asyncio,'json':json,'HTTPException':HTTPException,'_config':lambda:self.config,'_gate':self.gate,'_spawn':spawn,'_erp_failure':lambda e,c:str(e)}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-gateway-route>','exec'),ns)
  class Request:
   headers=self.m.edge_headers(body,self.m.edge_secret())
   app=SimpleNamespace(state=SimpleNamespace())
   async def stream(self):yield json.dumps(body).encode()
  wait=asyncio.wait
  async def bounded(tasks,timeout):return await wait(tasks,timeout=.01)
  bridge=SimpleNamespace(close=closed)
  connection=self.m.connect
  with patch.object(self.m,'connect',side_effect=lambda *a,**k:connection(self.path)),patch.object(self.bridge_module,'ERPBridge',return_value=bridge),patch.object(self.m,'call',side_effect=work),patch.object(asyncio,'wait',side_effect=bounded):
   result=await ns['gateway_call'](Request())
   self.assertEqual(result['state'],'uncertain');self.assertEqual(len(retained),1);closed.assert_not_awaited()
   release.set();await asyncio.gather(*retained);closed.assert_awaited_once()

class ReadOnlyReferences(RepositoryFiles):
 async def test_explicit_reference_can_be_read_but_cannot_be_written_patched_or_made_directory(self):
  self.profile['read_only_paths']=['small.py'];p=self.root/'small.py';p.write_bytes(b'original')
  result=await self.ops.repository_read({'path':'small.py'},row=self.row,packet={},config={})
  self.assertEqual(base64.b64decode(result['content_base64']),b'original')
  import hashlib
  sha=hashlib.sha256(b'original').hexdigest()
  for method,args in [(self.ops.repository_write,{'path':'small.py','expected_sha256':sha,'content_base64':base64.b64encode(b'changed').decode()}),(self.ops.repository_patch,{'path':'small.py','expected_sha256':sha,'edits':[{'old':'original','new':'changed'}]}),(self.ops.repository_mkdir,{'path':'small.py'})]:
   with self.assertRaises(self.ops.CapabilityUnavailable):await method(args,row=self.row,packet={},config={})
  self.assertEqual(p.read_bytes(),b'original')

class CompletedCorrection(PrerequisiteRetry):
 async def setup_rejected(self):
  pm=await self.original();old=self.g.find(pm,self.path,include_private=True)
  with self.g._transaction(self.path) as db:
   saved=db.execute('SELECT result_json FROM governance_result WHERE invocation_id=?',(pm,)).fetchone()
   result=json.loads(saved[0]);result['outcome']='ready_for_review'
   db.execute('UPDATE governance_result SET result_json=?,result_sha256=? WHERE invocation_id=?',(json.dumps(result),self.bridge_module.digest(result),pm))
   db.execute("UPDATE governance_invocation SET state='handed_off' WHERE invocation_id=?",(pm,));self.g._release(db,old)
  packet=self.packet(role='developer');ident='synthetic-rejected-developer-0001'
  self.g.enqueue(invocation_id=ident,project=P,task=T,from_role='project_manager',target_role='developer',task_revision=packet['task_revision'],context='Synthetic prior PM decision',work_packet=packet,actor='manager@example.invalid',gate=self.gate_value,db_path=self.path)
  row=self.g.claim_invocation(ident,self.bridge_module.stable_run_id(ident),gate=self.gate_value,db_path=self.path);self.g.mark_submitted(ident,row['automation_run_id'],row['lease_token'],gate=self.gate_value,db_path=self.path)
  with self.g.connect(self.path) as db:db.execute('UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',(pm,ident))
  self.helper=self.load('agentic_prerequisite_retry')
  with self.g._transaction(self.path) as db:
   db.execute("UPDATE governance_invocation SET state='uncertain',reason=? WHERE invocation_id=?",('ValueError: '+self.helper.UNSAFE_TEMPLATE,ident))
   db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',('rejected-session-0001',ident,'{}','{}','connection','conversation','admitted',1))
   for i,op in enumerate(['context.read','repository.prepare','repository.test','repository.read']):
    db.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',('rejected-session-0001','original-call-'+str(i),'hash',op,'uncertain' if op=='repository.read' else 'completed','{}',1,1))
  self.fresh=copy.deepcopy(packet);self.fresh['content_sha256']=self.bridge_module.packet_digest(self.fresh)
  self.native_task.update(custom_agov_active_run=row['automation_run_id'],custom_agov_invocation_id=ident)
  self.proof=patch.object(self.helper,'_verified_rejected_reply',return_value={'reply_sha256':'a'*64,'provider_receipt':'synthetic-receipt','validation_error':self.helper.UNSAFE_TEMPLATE});self.proof.start();self.addCleanup(self.proof.stop)
  event=await self.event(kind='project.control',action='retry',task=T,identity='explicit-completed-correction-0001');self.retry_id=event['event_id'];self.rejected=ident;self.pm=pm
 async def correction(self):
  return await self.helper.resume_completed_protocol_rejection(self.rejected,self.retry_id,config=self.config,bridge=self.native,project_gate=self.gate,db_path=self.path)
 async def test_current_human_retry_creates_one_new_developer_under_retained_PM_boundary(self):
  await self.setup_rejected()
  values=await asyncio.gather(self.correction(),self.correction());self.assertEqual(values[0],values[1])
  row=self.g.find(values[0],self.path,include_private=True);self.assertEqual(row['state'],'queued');self.assertEqual(row['source_invocation_id'],self.pm);self.assertEqual(row['target_role'],'developer')
  self.assertEqual(self.g.find(self.rejected,self.path)['state'],'handed_off');self.dispatch.assert_not_awaited()
 async def test_unresolved_original_test_cannot_be_replaced(self):
  await self.setup_rejected()
  with self.g.connect(self.path) as db:db.execute("UPDATE governance_gateway_call SET state='uncertain' WHERE session_id='rejected-session-0001' AND operation='repository.test'")
  with self.assertRaisesRegex(ValueError,'remain unresolved'):await self.correction()
  self.assertEqual(self.g.find(self.rejected,self.path)['state'],'uncertain')
 async def test_unknown_provider_and_scope_change_preserve_original_fence(self):
  await self.setup_rejected()
  with patch.object(self.helper,'_verified_rejected_reply',side_effect=ValueError('Unknown provider')):
   with self.assertRaisesRegex(ValueError,'Unknown provider'):await self.correction()
  self.fresh['acceptance_tests'][0]['expected']='Changed business scope'
  with self.assertRaisesRegex(ValueError,'business scope'):await self.correction()
  self.assertEqual(self.g.find(self.rejected,self.path)['state'],'uncertain')
if __name__=='__main__':unittest.main()

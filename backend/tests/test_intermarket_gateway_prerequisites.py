"""Real SQLite and file tests; no production app, ERP, model or GitHub."""
import asyncio,base64,copy,hashlib,importlib.util,json,os,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from test_agentic_controls_events import Fixture,P,T
ROOT=Path(__file__).parents[1]/'open_webui/utils'
def direct(name):
 spec=importlib.util.spec_from_file_location('isolated_'+name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class Resources(unittest.TestCase):
 def test_defaults_and_hard_caps(self):
  m=direct('agentic_test_resources');self.assertEqual(m.limits({}),m.DEFAULT)
  for change in ({'memory_mb':999999},{'cpu_seconds':True},{'processes':9999},{'unknown':1}):
   with self.assertRaises(ValueError):m.limits({'test_resources':{**m.DEFAULT,**change}})
 def test_sandbox_has_no_network_or_host_home(self):
  m=direct('agentic_test_resources');a=m.sandbox_argv('/tmp/synthetic-workspace',['python3','-V'],m.DEFAULT)
  self.assertIn('--unshare-all',a);self.assertIn('--clearenv',a);self.assertNotIn('/home',a);self.assertNotIn('--share-net',a)
class PrerequisiteRetry(Fixture):
 def packet(self,task=T,role='project_manager',project=P):
  p=super().packet(task,role,project);p['atomic_adoption']={'adoption_id':'synthetic-adoption','executor_role':'developer','task_revision':p['task_revision'],'requirement_ids':p['requirement_ids']}
  p['content_sha256']=self.bridge_module.packet_digest(p);return p
 async def original(self):
  packet=self.packet();ident='pm-original-registration-block-0001'
  self.g.enqueue(invocation_id=ident,project=P,task=T,from_role='erp',target_role='project_manager',task_revision=packet['task_revision'],context='Synthetic review',work_packet=packet,actor='manager@example.invalid',gate=self.gate_value,db_path=self.path)
  row=self.g.claim_invocation(ident,'run-'+ident,gate=self.gate_value,db_path=self.path)
  self.g.mark_submitted(ident,row['automation_run_id'],row['lease_token'],gate=self.gate_value,db_path=self.path)
  self.g.finish_execution(ident,row['automation_run_id'],row['lease_token'],db_path=self.path)
  self.store=self.load('agentic_result_store');sessions=self.load('agentic_gateway_sessions')
  result={'schema_version':1,'project_id':P,'task_id':T,'invocation_id':ident,'work_packet_digest':self.bridge_module.packet_digest(packet),'actor':packet['binding'],'outcome':'blocked','summary':'No registered repository profile','defects':[],'evidence':[],'test_results':[]}
  event={'event_id':'event-original-result-0001','task_id':T,'run_id':row['automation_run_id']}
  with self.g.connect(self.path) as db:
   self.store._schema(db);db.execute('INSERT INTO governance_result VALUES(?,?,?,?,?,?,?,?,?)',(ident,row['automation_run_id'],self.bridge_module.digest(result),json.dumps(result),json.dumps(event),'published',json.dumps({'event_id':event['event_id'],'task_id':T,'status':'Blocked','modified':'fixture-modified'}),1,1))
  with sessions.connect(self.path) as db:
   db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',('synthetic-session',ident,'{}','{}','connection',None,'admitted',1))
   observed={'invocation_id':ident,'work_packet':packet,'repository_registration':{'ready':False,'kind':'unconfigured'}}
   db.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',('synthetic-session','actual-context-read','hash','context.read','completed',json.dumps(observed),1,1))
  event=await self.event(kind='project.control',action='retry',task=T,identity='human-registration-retry-0001')
  self.retry_id=event['event_id'];self.row=self.g.find(ident,self.path,include_private=True)
  self.fresh=copy.deepcopy(packet);self.fresh['activity']=[{'id':'fresh-human-checkpoint'}];self.fresh['content_sha256']=self.bridge_module.packet_digest(self.fresh)
  self.native_task={'project':P,'status':'Open','custom_agov_active_run':row['automation_run_id'],'custom_agov_invocation_id':ident,'custom_agentic_managed':1,'is_group':0}
  self.native=SimpleNamespace(erp=SimpleNamespace(get=AsyncMock(side_effect=lambda *a:copy.deepcopy(self.native_task))),packet=AsyncMock(side_effect=lambda *a:copy.deepcopy(self.fresh)))
  profile={'repository':packet['repository'],'environment':packet['environment'],'workspace_root':'/srv/abit-dev-linux/synthetic','allowed_paths':['src'],'read_paths':['src'],'test_paths':['src'],'tests':{'real':['python3','-V']},'test_acceptance_ids':{},'base_branch':'review/synthetic','base_commit':'a'*40,'git_author':{'name':'Synthetic','email':'synthetic@example.invalid'},'operator_verification':{'commit':'a'*40,'runtime_manifest_sha256':'b'*64,'sandbox_receipt_sha256':'c'*64}}
  self.config={'agentic_repository_gateway':{'projects':{P:profile}}};self.retry=self.load('agentic_prerequisite_retry');return ident
 async def call(self,ident):
  return await self.retry.resume_registered_repository(ident,self.retry_id,config=self.config,bridge=self.native,project_gate=self.gate,db_path=self.path)
 async def test_retry_keeps_original_result_and_creates_one_generation(self):
  ident=await self.original();before=self.store.get_result(ident,self.path);new=await self.call(ident)
  self.assertEqual(self.g.find(ident,self.path)['state'],'handed_off');row=self.g.find(new,self.path,include_private=True)
  self.assertEqual(row['state'],'queued');self.assertEqual(row['dispatch_attempts'],0);self.assertIsNone(row['submitted_at'])
  self.assertEqual(json.loads(row['work_packet'])['activity'],self.fresh['activity']);self.assertEqual(self.store.get_result(ident,self.path),before)
  self.assertEqual(await self.call(ident),new);self.assertEqual(len(self.g.list_invocations(P,self.path)),2)
 async def test_concurrent_recovery_uses_same_generation(self):
  ident=await self.original();a,b=await asyncio.gather(self.call(ident),self.call(ident));self.assertEqual(a,b);self.assertEqual(len(self.g.list_invocations(P,self.path)),2)
 async def test_only_current_human_retry_is_authority(self):
  ident=await self.original()
  with self.g.connect(self.path) as db:db.execute("UPDATE governance_native_control SET action='resume'")
  with self.assertRaises(ValueError):await self.call(ident)
  self.assertEqual(self.g.find(ident,self.path)['state'],'execution_finished')
 async def test_actual_tool_observation_is_required(self):
  ident=await self.original()
  with self.g.connect(self.path) as db:db.execute("UPDATE governance_gateway_call SET result_json='{}'")
  with self.assertRaisesRegex(ValueError,'did not observe'):await self.call(ident)
 async def test_changed_scope_and_active_run_are_preserved(self):
  ident=await self.original();self.fresh['objective']='Unauthorized changed objective'
  with self.assertRaisesRegex(ValueError,'business scope'):await self.call(ident)
  self.fresh['objective']=json.loads(self.row['work_packet'])['objective'];self.native_task['custom_agov_active_run']='other-run'
  with self.assertRaisesRegex(ValueError,'same enrolled'):await self.call(ident)
 async def test_unverified_runtime_cannot_queue(self):
  ident=await self.original();del self.config['agentic_repository_gateway']['projects'][P]['operator_verification']
  with self.assertRaisesRegex(ValueError,'preflight'):await self.call(ident)
  self.assertEqual(len(self.g.list_invocations(P,self.path)),1)
 async def test_existing_child_repair_cannot_be_bypassed(self):
  ident=await self.original()
  with self.g.connect(self.path) as db:
   db.execute('CREATE TABLE governance_repair_publication(source TEXT,issue TEXT)');db.execute('INSERT INTO governance_repair_publication VALUES(?,?)',(ident,'original-issue'))
  with self.assertRaisesRegex(ValueError,'repair/dependency'):await self.call(ident)
class RepositoryFiles(Fixture):
 def setUp(self):
  super().setUp();self.ops=self.load('agentic_gateway_operations');self.root=Path(self.temp.name)/'source';self.root.mkdir()
  self.profile={'allowed_paths':['large.cs','small.py']};self.w=patch.object(self.ops,'_workspace',return_value=(self.root,self.profile));self.w.start();self.addCleanup(self.w.stop);self.row={'target_role':'developer'}
 async def test_large_source_pages_keep_complete_digest(self):
  raw=b'x'*700000;(self.root/'large.cs').write_bytes(raw);parts=[];offset=0
  while True:
   result=await self.ops.repository_read({'path':'large.cs','offset':offset},row=self.row,packet={},config={});parts.append(base64.b64decode(result['content_base64']))
   self.assertLessEqual(len(parts[-1]),self.ops.MAX_BYTES);self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
   if result['next_offset'] is None:break
   offset=result['next_offset']
  self.assertEqual(b''.join(parts),raw)
 async def test_exact_large_patch_preserves_executable_mode(self):
  raw=('x'*300000+'UNIQUE ORIGINAL'+'y'*300000).encode();p=self.root/'large.cs';p.write_bytes(raw);p.chmod(0o750)
  result=await self.ops.repository_patch({'path':'large.cs','expected_sha256':hashlib.sha256(raw).hexdigest(),'edits':[{'old':'UNIQUE ORIGINAL','new':'VERIFIED REPLACEMENT'}]},row=self.row,packet={},config={})
  self.assertIn(b'VERIFIED REPLACEMENT',p.read_bytes());self.assertEqual(p.stat().st_mode&0o777,0o750);self.assertEqual(result['sha256'],hashlib.sha256(p.read_bytes()).hexdigest())
 async def test_stale_ambiguous_outside_and_symlink_patches_deny(self):
  p=self.root/'small.py';p.write_text('same same');sha=hashlib.sha256(p.read_bytes()).hexdigest()
  for args in [{'path':'small.py','expected_sha256':'0'*64,'edits':[{'old':'same','new':'new'}]},{'path':'small.py','expected_sha256':sha,'edits':[{'old':'same','new':'new'}]},{'path':'../outside','expected_sha256':sha,'edits':[{'old':'x','new':'y'}]}]:
   with self.assertRaises(ValueError):await self.ops.repository_patch(args,row=self.row,packet={},config={})
  self.assertEqual(p.read_bytes(),b'same same');p.unlink();p.symlink_to(self.root/'large.cs')
  with self.assertRaises((OSError,ValueError)):await self.ops.repository_patch({'path':'small.py','expected_sha256':sha,'edits':[{'old':'x','new':'y'}]},row=self.row,packet={},config={})
 def test_only_developer_can_patch(self):
  self.assertIn('repository.patch',self.ops.registry('developer'))
  for role in ['project_manager','solution_architect','qa']:self.assertNotIn('repository.patch',self.ops.registry(role))
if __name__=='__main__':unittest.main(verbosity=2)

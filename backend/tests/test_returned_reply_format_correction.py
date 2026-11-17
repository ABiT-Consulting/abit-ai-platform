"""Returned original formatting only: real temporary ledger, no external actions."""
from copy import deepcopy
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import ast,hashlib,importlib.util,json,os,sqlite3,subprocess,sys,time,types,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path(os.environ['AGOV_FORMAT_BACKEND_FIXTURE'])
FALLBACK=BACKEND/'open_webui/utils'
sys.path.insert(0,str(BACKEND/'tests'))
from test_agentic_controls_events import Fixture,EventBridge,P,T
import test_agentic_relay_scope as relay_fixture

class ReturnedFormat(Fixture):
 def candidate(self,name):
  p=HERE/(name+'.py')
  if not p.is_file():p=FALLBACK/(name+'.py')
  full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,p);m=importlib.util.module_from_spec(spec);sys.modules[full]=m;setattr(self.utils,name,m);spec.loader.exec_module(m);return m
 def setUp(self):
  super().setUp();self.utils.__path__.insert(0,str(FALLBACK));self.utils.__path__.insert(0,str(HERE))
  self.m=self.candidate('agentic_recovery');self.start=self.candidate('agentic_start_recovery');self.store=self.candidate('agentic_result_store');self.executor=self.candidate('agentic_executor');self.scope=self.candidate('agentic_relay_scope');self.primary=self.candidate('abitdot_primary');self.sessions=self.candidate('agentic_gateway_sessions');self.b=self.bridge_module
  with self.sessions.connect(self.path):pass
  with self.g.connect(self.path)as db:self.store._schema(db);self.intents._schema(db)
  self.ident='agov-format-original-fixture';self.packet_value=self.packet();self.packet_value['objective']='Preserve quotes "exactly" and backslashes C:\\tmp.';self.packet_value['content_sha256']=self.b.packet_digest(self.packet_value)
  self.g.enqueue(invocation_id=self.ident,project=P,task=T,from_role='project_manager',target_role='developer',task_revision=self.packet_value['task_revision'],context='Original delivery acceptance only',work_packet=self.packet_value,gate=self.gate_value,actor='server-fixture',db_path=self.path)
  run=self.b.stable_run_id(self.ident);self.row=self.g.claim_invocation(self.ident,run,gate=self.gate_value,db_path=self.path)
  self.event={'schema_version':1,'event_id':'agov-start-'+self.b.digest(self.ident)[:48],'type':'run.started','project_id':P,'task_id':T,'task_revision':self.packet_value['task_revision'],'run_id':run,'invocation_id':self.ident,'role':'developer','agent_id':self.row['agent_id'],'expected_modified':self.packet_value['source_modified'],'expected_project_revision':self.packet_value['project_revision'],'work_packet_digest':self.b.packet_digest(self.packet_value),'artifacts':[]}
  self.start.record_start_intent(self.row,self.event,self.path)
  self.start_receipt={k:self.event[k]for k in('event_id','task_id','run_id')};self.start_receipt.update(modified='native-original-working',status='Working',durable=True)
  self.start.record_start_receipt(self.ident,self.start_receipt,self.path)
  self.g.mark_submitted(self.ident,run,self.row['lease_token'],gate=self.gate_value,db_path=self.path)
  self.g.finish_execution(self.ident,run,self.row['lease_token'],'uncertain',reason='Returned original JSON is malformed',db_path=self.path)
  self.row=self.g.find(self.ident,self.path,include_private=True)
  owner='owner-fixture';dot='11111111-2222-4333-8444-555555555555~d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1';account='a'*64
  scope=self.scope.scope_for_packet(self.row,self.packet_value,owner,account,dot,run)
  self.mutate=None;preflight=relay_fixture.RelayScope.proof(self,{'scope':scope,'scope_digest':self.b.digest(scope)});final=deepcopy(preflight);final['proof_id']='submission-fixture-proof-002'
  automation={'id':self.row['agent_id'],'user_id':owner,'name':'Original controlled Developer','data':{'prompt':'Original controlled prompt'},'meta':{'governance_managed':True,'erp_task':T,'erp_role':'developer','governance_project':P,'governance_packet_digest':scope['work_packet_digest'],'governance_scope_digest':self.b.digest(scope)}}
  self.envelope=self.primary.build_project_envelope(owner=owner,automation=automation,run_id=run,dot_id=dot)
  self.envelope['governance_scope']={'protocol_version':1,'scope':scope,'preflight_proof':preflight,'submission_proof':final}
  admitted_receipt=relay_fixture.RelayScope.provider(self,self.envelope)['scope_receipt']
  self.admission=deepcopy(self.envelope['governance_scope'])
  self.assertEqual(self.sessions.receipt(self.admission),admitted_receipt)
  self.assertEqual(self.scope.scope_receipt(self.envelope,account,admitted_receipt),admitted_receipt)
  self.automation=types.SimpleNamespace(id=self.row['agent_id'],user_id=owner,meta={'agentic_project':P,'agentic_role':'developer','erp_role_user':self.packet_value['binding']['erp_user'],'governance_managed':True})
  self.provider_path=self.path.parent/'abitdot-primary.sqlite'
  with sqlite3.connect(self.provider_path)as db:
   db.execute('CREATE TABLE dot_primary_runs(id TEXT,owner TEXT,automation_id TEXT,original_run_id TEXT,status TEXT,envelope_json TEXT,response_text TEXT,receipt TEXT)')
   db.execute('INSERT INTO dot_primary_runs VALUES(?,?,?,?,?,?,NULL,NULL)',('provider-fixture',owner,self.row['agent_id'],run,'unknown',self.b.canonical(self.envelope)))
   db.execute('CREATE TABLE original_messages(id TEXT,raw TEXT)');db.execute('INSERT INTO original_messages VALUES(?,?)',('original-visible','AGOV RESULT: {"unparseable": original visible quotes'))
  with self.sessions.connect(self.path)as db:
   db.execute("INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,NULL,'ADMITTED',?)",('gateway-fixture-session-001',self.ident,self.b.canonical(scope),self.b.canonical(self.admission),'c'*64,int(time.time())))
   db.execute("INSERT INTO governance_gateway_call VALUES(?,?,?,'repository.delivery_status','completed',?,?,?)",('gateway-fixture-session-001','status-original','d'*64,'{"state":"ready_for_review"}',int(time.time()),int(time.time())))
  self.result={'schema_version':1,'invocation_id':self.ident,'actor':deepcopy(self.packet_value['binding']),'project_id':P,'task_id':T,'task_revision':self.packet_value['task_revision'],'work_packet_digest':self.b.packet_digest(self.packet_value),'outcome':'continue','summary':'Escaped "quotes" and path C:\\tmp stay exact.','test_results':[],'evidence':[],'implementation_invocation_id':None,'implementation_agent_id':None,'defects':[]}
  self.raw='```text\nAGOV RESULT: '+json.dumps(self.result,ensure_ascii=False)+'\n```'
  with self.g.connect(self.path)as db:entry=dict(db.execute('SELECT *FROM governance_start WHERE invocation_id=?',(self.ident,)).fetchone())
  captured=int(time.time())
  self.proof={'schema_version':2,'purpose':self.m.FORMAT_POLICY,'invocation_id':self.ident,'run_id':run,'project_id':P,'task_id':T,'agent_id':self.row['agent_id'],'packet_sha256':self.row['packet_hash'],'native_start_event_sha256':entry['event_sha256'],'native_start_receipt_sha256':self.b.digest(json.loads(entry['receipt_json'])),'provider_envelope_sha256':hashlib.sha256(self.b.canonical(self.envelope).encode()).hexdigest(),'scope_sha256':self.b.digest(self.envelope['governance_scope']),'gateway_admission_sha256':self.b.digest(self.admission),'owner_id':owner,'account_id':account,'dot_id':dot,'conversation_url':'https://chatgpt.com/dots/'+dot,'original_root_id':'c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~CalpicoMessage~Sentinel_a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1','original_reply_id':'c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~CalpicoMessage~Sentinel_b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2','correction_root_id':'c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~CalpicoMessage~Sentinel_11111111111111111111111111111111','correction_reply_id':'c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1~CalpicoMessage~Sentinel_22222222222222222222222222222222','original_visible_reply_sha256':'1'*64,'corrected_reply_utf8':self.raw,'corrected_reply_sha256':hashlib.sha256(self.raw.encode()).hexdigest(),'correction_intent_sha256':'2'*64,'capture_release_proof_sha256':'3'*64,'relay_idle_proof_sha256':'4'*64,'pause_control_sequence':12,'captured_at':captured}
  self.file_patch=patch.object(self.m,'_format_file',side_effect=lambda row:(deepcopy(self.proof),self.b.digest(self.proof)));self.file_patch.start();self.addCleanup(self.file_patch.stop)
  self.bridge=types.SimpleNamespace(packet=AsyncMock(side_effect=self.fresh_packet),
   erp=types.SimpleNamespace(get=AsyncMock(return_value={'name':T,'project':P,'status':'Open'})))
  self.before=self.preserved()
 async def asyncSetUp(self):
  await self.install_retry(sequence=13)

 async def fresh_packet(self,*args):
  p=deepcopy(self.packet_value);p['source_modified']='native-original-working';p['content_sha256']=self.b.packet_digest(p);return p
 async def read(self):return await self.m.read_format_correction(self.row,self.automation,config={},project_gate=self.gate,bridge=self.bridge,db_path=self.path)
 def audits(self):
  with self.g.connect(self.path)as db:return [json.loads(x[0])for x in db.execute('SELECT payload FROM governance_event WHERE invocation_id=?AND kind=?',(self.ident,self.m.FORMAT_AUDIT))]
 def preserved(self):
  with sqlite3.connect(self.provider_path)as db:provider={t:db.execute('SELECT *FROM '+t).fetchall()for t in('dot_primary_runs','original_messages')}
  with self.g.connect(self.path)as db:
   start=[dict(x)for x in db.execute('SELECT *FROM governance_start')];row=dict(db.execute('SELECT *FROM governance_invocation WHERE invocation_id=?',(self.ident,)).fetchone())
  return {'provider':provider,'start':start,'identity':{k:row[k]for k in('invocation_id','work_packet','packet_hash','automation_run_id','lease_token','lease_fence','dispatch_attempts','submitted_at')}}
 def assert_preserved(self):self.assertEqual(self.preserved(),self.before)
 async def test_default_off_and_protected_held_never_fallback_to_observe(self):
  with patch.object(self.m,'_format_file',return_value=None):self.assertIsNone(await self.read())
  with self.g.connect(self.path)as db:db.execute('UPDATE governance_native_control SET sequence=12')
  self.assertTrue((await self.read()) is False);self.assertEqual(self.audits(),[])
  fallback=AsyncMock(side_effect=AssertionError('No fallback observation after protected hold'))
  self.assertIsNone(await self.m.recover_original(self.ident,config={},project_gate=self.gate,bridge=self.bridge,db_path=self.path,reader=fallback,automation=self.automation,adapter=object()))
  fallback.assert_not_called();self.assert_preserved()
 async def test_actual_corrected_bytes_decode_unchanged_and_audit_once(self):
  record=await self.read();self.assertEqual(record['response_text'],self.raw)
  parsed=self.executor.decode_result(record['response_text'],self.packet_value,self.ident)
  self.assertEqual(parsed,self.result);self.assertEqual(await self.read(),record);self.assertEqual(len(self.audits()),1)
  self.assertEqual(self.audits()[0]['corrected_reply_sha256'],hashlib.sha256(self.raw.encode()).hexdigest());self.assert_preserved()
  # This submitted-result readback never acquires/transfers a process admission.
  maint=sys.modules['open_webui.utils.agentic_maintenance'];key=maint.path(self.path.parent)
  with maint._db(key)as db:
   original=db.execute("SELECT *FROM lease WHERE kind='governed'AND reference=?",(self.ident,)).fetchone();db.execute('UPDATE lease SET pid=?,process_start=?WHERE id=?',(999999999,'gone-original-process',original['id']))
  self.assertEqual((await self.read())['response_text'],self.raw);self.assertEqual(len(self.audits()),1);self.assert_preserved()
 async def test_proof_identity_hash_chat_native_start_and_admission_drift_deny(self):
  for key,value in [('run_id','different'),('packet_sha256','0'*64),('native_start_event_sha256','0'*64),('native_start_receipt_sha256','0'*64),('provider_envelope_sha256','0'*64),('gateway_admission_sha256','0'*64),('account_id','b'*64),('correction_reply_id','other-dot~CalpicoMessage~reply'),('correction_reply_id','33333333333333333333333333333333~33333333333333333333333333333333~CalpicoMessage~Sentinel_44444444444444444444444444444444'),('correction_reply_id',self.proof['original_reply_id']),('corrected_reply_sha256','0'*64),('corrected_reply_utf8',self.raw+' changed')]:
   old=self.proof[key];self.proof[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):await self.read()
   self.proof[key]=old
  self.assertEqual(self.audits(),[]);self.assert_preserved()
 async def test_closed_project_task_maintenance_stop_context_and_unsettled_calls_deny(self):
  self.allowed=False
  with self.assertRaises(ValueError):await self.read()
  self.allowed=True
  with self.g.connect(self.path)as db:db.execute("UPDATE governance_project_control SET state='paused'WHERE erp_project=?",(P,))
  self.assertTrue((await self.read()) is False)
  with self.g.connect(self.path)as db:
   db.execute("UPDATE governance_project_control SET state='enabled'WHERE erp_project=?",(P,));db.execute("INSERT INTO governance_task_control VALUES(?,?,'paused','fixture',0)",(P,T))
  self.assertTrue((await self.read()) is False)
  with self.g.connect(self.path)as db:db.execute("UPDATE governance_task_control SET state='enabled'")
  maint=sys.modules['open_webui.utils.agentic_maintenance'];gate=maint.path(self.path.parent);status=maint.status(gate)
  maint.control('close',expected_epoch=status['epoch'],command_id='fixture-close',actor='fixture',db_path=gate);self.assertTrue((await self.read()) is False)
  maint.control('open',expected_epoch=status['epoch']+1,command_id='fixture-open',actor='fixture',db_path=gate)
  changed=await self.fresh_packet();changed['srs']['sha256']='f'*64;self.bridge.packet=AsyncMock(return_value=changed)
  with self.assertRaisesRegex(ValueError,'ERP inputs changed'):await self.read()
  self.bridge.packet=AsyncMock(side_effect=self.fresh_packet)
  with self.g.connect(self.path)as db:db.execute("UPDATE governance_gateway_call SET state='uncertain'")
  with self.assertRaisesRegex(ValueError,'not all settled'):await self.read()
  with self.g.connect(self.path)as db:db.execute("UPDATE governance_gateway_call SET state='completed'")
  self.g.request_cancel(self.ident,'human-fixture',self.path)
  with self.assertRaisesRegex(ValueError,'owned submitted original'):await self.read()
  self.assertEqual(self.audits(),[]);self.assert_preserved()
 async def test_preexisting_result_and_audit_failure_deny_without_mutating_original(self):
  self.store.save_local_result(self.ident,self.row['automation_run_id'],self.result,self.path)
  with self.assertRaisesRegex(ValueError,'already has result'):await self.read()
  with self.g.connect(self.path)as db:
   db.execute('DELETE FROM governance_local_result');db.execute("CREATE TRIGGER reject_format_audit BEFORE INSERT ON governance_event WHEN NEW.kind='returned_reply_format_selected'BEGIN SELECT RAISE(ABORT,'controlled format audit failure');END")
  with self.assertRaisesRegex(sqlite3.IntegrityError,'controlled format audit failure'):await self.read()
  self.assertEqual(self.audits(),[]);self.assert_preserved()
 async def test_selected_corrected_raw_is_immutable_and_business_validation_stays_strict(self):
  await self.read();self.proof['corrected_reply_utf8']=self.raw.replace('Escaped','Changed');self.proof['corrected_reply_sha256']=hashlib.sha256(self.proof['corrected_reply_utf8'].encode()).hexdigest()
  with self.assertRaisesRegex(ValueError,'selection is immutable'):await self.read()
  self.assertEqual(len(self.audits()),1);self.assert_preserved()
  bad=deepcopy(self.result);bad['actor']['agent_id']='foreign'
  with self.assertRaises(ValueError):self.executor.decode_result('AGOV RESULT: '+json.dumps(bad),self.packet_value,self.ident)
  bad=deepcopy(self.result);bad['outcome']='ready_for_review'
  with self.assertRaises(ValueError):self.executor.decode_result('AGOV RESULT: '+json.dumps(bad),self.packet_value,self.ident)

 async def test_actual_fenced_prompt_current_feed_and_core_keep_escaped_json_bytes(self):
  # Execute only the actual pure prompt assignment and provenance-view helper.
  tree=ast.parse(Path(self.executor.__file__).read_bytes());klass=next(x for x in tree.body if isinstance(x,ast.ClassDef)and x.name=='DotAdapter');submit=next(x for x in klass.body if isinstance(x,ast.AsyncFunctionDef)and x.name=='submit');prompt=next(x for x in submit.body if isinstance(x,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='prompt'for t in x.targets))
  ns={'root':Path(self.temp.name)/'evidence','row':self.row,'packet':self.packet_value,'packet_digest':self.b.packet_digest,'json':json,'implementation':{}}
  exec(compile(ast.Module(body=[prompt],type_ignores=[]),'<actual-prompt-assignment>','exec'),ns)
  self.assertIn('fenced plain-text block',ns['prompt']);self.assertIn('preserving all JSON escapes exactly',ns['prompt'])
  policy=next(k.value.value for x in ast.walk(submit)if isinstance(x,ast.Call)for k in x.keywords if k.arg=='policy'and isinstance(k.value,ast.Constant))
  context_source=(FALLBACK/'erp_dot_execution.py').read_bytes();node=next(x for x in ast.parse(context_source).body if isinstance(x,ast.FunctionDef)and x.name=='_governed_packet_context');ctx={'time':time};exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-governed-context>','exec'),ctx)
  context,_,_=ctx['_governed_packet_context']({'name':T,'project':P,'status':'Working'},'developer',self.automation.user_id,self.row['agent_id'],self.row['automation_run_id'],self.envelope['dot_id'],self.packet_value,self.envelope['governance_scope']['preflight_proof'],lambda:None)
  envelope=deepcopy(self.envelope);envelope['task']['original_prompt']=policy+'\n\n'+ns['prompt']+'\n\nVerified ERP context:\n'+json.dumps(context)
  source=(FALLBACK/'abitdot_primary_relay.py').read_bytes();function=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef)and x.name=='verified_dot_payload');assignments=[x for x in function.body if isinstance(x,ast.Assign)and any(isinstance(t,ast.Name)and t.id in('model_context','message')for t in x.targets)];self.assertEqual(len(assignments),2)
  view={'envelope':envelope,'governed':True,'json':json};exec(compile(ast.Module(body=assignments,type_ignores=[]),'<actual-relay-message>','exec'),view)
  reader=BACKEND.parent/'deployment/windows/aibit-relay/aibit_dot_feed.cjs'
  self.assertEqual(hashlib.sha256(reader.read_bytes()).hexdigest(),'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53')
  script=r"""const fs=require('node:fs'),assert=require('node:assert/strict');const f=JSON.parse(fs.readFileSync(0,'utf8'));let sends=0;global.fetch=()=>{sends++;throw Error('No network');};const feed=require(process.argv[1]);const row=(id,text,self)=>({classList:{contains:k=>k==='self'&&self},innerText:text,textContent:text,getAttribute:k=>k==='data-message-id'?id:null,querySelector:()=>({innerText:text,textContent:text})});global.document={querySelectorAll:()=>[row('original-root',f.expected,true),row('original-reply',f.response,false)]};const x=feed.dotFeedSnapshotDOM(f.expected,()=>{throw Error('No Markdown rewrite');},'');assert.equal(x.complete,true);assert.equal(x.response,f.response);assert.equal(sends,0);console.log(JSON.stringify({response:x.response,complete:x.complete,sends}));"""
  run=subprocess.run(['node','-e',script,str(reader)],input=json.dumps({'expected':view['message'],'response':self.raw}),text=True,capture_output=True,timeout=10);self.assertEqual(run.returncode,0,run.stderr)
  observed=json.loads(run.stdout);self.assertTrue(observed['complete']);self.assertEqual(observed['response'],self.raw);self.assertEqual(observed['sends'],0)
  self.assertEqual(self.executor.decode_result(observed['response'],self.packet_value,self.ident),self.result);self.assertEqual(self.audits(),[]);self.assert_preserved()


 async def install_retry(self,*,sequence=14,task='',actor='human-fixture',signed=True):
  native=EventBridge(self);native.secret='synthetic-format-control-signing-secret-0001'
  intent={'project_id':P,'task_id':task,'command_id':'format-command-'+str(sequence),
          'control_sequence':sequence,'action':'retry','actor':actor,'retained_veto':False}
  native.native[task]=deepcopy(intent)
  value={'schema_version':1,'event_id':'format-event-'+str(sequence),'project':P,'task':task,
         'type':'project.control','revision':'project-revision-1','source_modified':'2026-11-15 01:00:00',
         'payload':{key:intent[key] for key in ('task_id','command_id','control_sequence','action','actor')}}
  value['payload'].update(expected_revision='project-revision-1',target_role='developer' if task else 'project_manager')
  with patch.object(self.g.time,'time',return_value=self.proof['captured_at']+3):
   if signed:
    headers=self.b.signing_headers(value,native.secret);self.b.verify_event(value,headers,native.secret)
    self.g.receive_event(value['event_id'],P,value,actor='signed-erp',db_path=self.path)
   self.assertTrue(await self.intents.apply_current(value,native,db_path=self.path))
  self.bound_event,self.bound_intent=deepcopy(value),deepcopy(intent)
  self.proof['retry_control']={'event_id':value['event_id'],'task_id':task,'command_id':intent['command_id'],
       'control_sequence':sequence,'payload_sha256':self.b.digest(value)}
  self.creation_proof(intent)
  return value,intent

 def creation_proof(self,intent,*,created=None,zone='UTC'):
  created=self.proof['captured_at']+1 if created is None else created
  key='AGOV-'+self.b.digest(['control_intent',[P,intent['command_id']]])[:48]
  record={'doctype':'AI Governance Record','name':key,'record_key':key,'kind':'control_intent',
          'project':P,'task':intent['task_id'],'run_id':f"{intent['control_sequence']:020d}",
          'content_sha256':self.b.digest(intent),'payload_json':self.b.canonical(intent),'owner':intent['actor'],
          'creation':datetime.fromtimestamp(created,timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None).isoformat(' '),
          'modified':'retained native metadata','modified_by':intent['actor'],'docstatus':0,'idx':0,'state':'Recorded'}
  raw=json.dumps({'data':record},ensure_ascii=False)
  timezone_raw=json.dumps({'message':{'doctype':'System Settings','time_zone':zone,'name':'System Settings'}})
  self.proof['native_creation']={'schema_version':1,'purpose':'native-control-creation.v1',
       'record_response_utf8':raw,'record_response_sha256':hashlib.sha256(raw.encode()).hexdigest(),
       'timezone_response_utf8':timezone_raw,'timezone_response_sha256':hashlib.sha256(timezone_raw.encode()).hexdigest(),
       'observed_at_utc':datetime.fromtimestamp(self.proof['captured_at']+4,timezone.utc).isoformat(),
       'provenance':'Independent native control-intent and site-timezone readbacks; no write.'}

 async def test_unsigned_retry_cursor_cannot_authorize_corrected_bytes(self):
  await self.install_retry(signed=False)
  self.assertTrue((await self.read()) is False)
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_signed_registered_agent_retry_cannot_authorize_corrected_bytes(self):
  await self.install_retry(actor=self.users['developer'])
  self.assertTrue((await self.read()) is False)
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_foreign_task_application_cannot_authorize_project_retry_cursor(self):
  value,intent=await self.install_retry(task='TASK-foreign-fixture')
  with self.g.connect(self.path)as db:
   db.execute("UPDATE governance_native_control SET sequence=?,command_id=? WHERE project=? AND task=''",
              (intent['control_sequence'],intent['command_id'],P))
  self.assertTrue((await self.read()) is False)
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_exact_task_retry_is_bound_and_a_later_cursor_cannot_replace_it(self):
  await self.install_retry(task=T)
  record=await self.read();self.assertEqual(record['response_text'],self.raw)
  self.assertEqual(self.audits()[0]['control_task'],T)
  old=deepcopy(self.proof)
  await self.install_retry(sequence=15,task=T)
  self.proof=old
  self.assertTrue((await self.read()) is False)
  self.assertEqual(len(self.audits()),1);self.assert_preserved()

 def change_native_record(self,mutate):
  evidence=self.proof['native_creation'];value=json.loads(evidence['record_response_utf8'])
  mutate(value['data'])
  raw=json.dumps(value,ensure_ascii=False);evidence['record_response_utf8']=raw
  evidence['record_response_sha256']=hashlib.sha256(raw.encode()).hexdigest()

 def change_timezone(self,value):
  evidence=self.proof['native_creation'];raw=json.dumps({'message':{'doctype':'System Settings','time_zone':value}})
  evidence['timezone_response_utf8']=raw;evidence['timezone_response_sha256']=hashlib.sha256(raw.encode()).hexdigest()

 async def test_native_creation_raw_identity_payload_hash_and_duplicate_keys_are_verified(self):
  original=deepcopy(self.proof)
  cases=[lambda r:r.update(name='AGOV-foreign'),lambda r:r.update(kind='control'),
         lambda r:r.update(project='PROJ-foreign'),lambda r:r.update(task='TASK-foreign'),
         lambda r:r.update(owner=self.users['developer']),lambda r:r.update(run_id='00000000000000000099'),
         lambda r:r.update(content_sha256='0'*64),
         lambda r:r.update(payload_json=r['payload_json'].replace('human-fixture','another-human'))]
  for mutate in cases:
   self.proof=deepcopy(original);self.change_native_record(mutate)
   with self.subTest(mutation=mutate),self.assertRaises(ValueError):await self.read()
  self.proof=deepcopy(original)
  evidence=self.proof['native_creation'];raw=evidence['record_response_utf8'].replace('"kind": "control_intent"','"kind": "control_intent", "kind": "control_intent"')
  self.assertNotEqual(raw,evidence['record_response_utf8']);evidence['record_response_utf8']=raw;evidence['record_response_sha256']=hashlib.sha256(raw.encode()).hexdigest()
  with self.assertRaisesRegex(ValueError,'Duplicate'):await self.read()
  self.proof=deepcopy(original);evidence=self.proof['native_creation']
  raw=json.dumps({'data':json.loads(evidence['record_response_utf8'])['data'],'message':{}})
  evidence['record_response_utf8']=raw;evidence['record_response_sha256']=hashlib.sha256(raw.encode()).hexdigest()
  with self.assertRaisesRegex(ValueError,'envelope'):await self.read()
  self.proof=deepcopy(original);self.proof['native_creation']['record_response_sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'bytes changed'):await self.read()
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_delayed_creation_capture_second_and_future_creation_are_denied(self):
  for created in (self.proof['captured_at']-10,self.proof['captured_at']+.999999,self.proof['captured_at']+10):
   self.creation_proof(self.bound_intent,created=created)
   with self.subTest(created=created),self.assertRaisesRegex(ValueError,'not created after'):await self.read()
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_native_site_timezone_is_used_and_ambiguous_or_inconsistent_times_are_denied(self):
  self.creation_proof(self.bound_intent,zone='Asia/Karachi')
  self.assertEqual((await self.read())['response_text'],self.raw)
  original=deepcopy(self.proof)
  # Offset must agree with retained native site timezone, not just parse as UTC.
  self.change_native_record(lambda r:r.update(creation=r['creation']+'+00:00'))
  with self.assertRaisesRegex(ValueError,'inconsistent'):await self.read()
  for stamp in ('2026-11-01 01:30:00','2026-03-08 02:30:00'):
   self.proof=deepcopy(original);self.change_timezone('America/New_York')
   self.change_native_record(lambda r:r.update(creation=stamp))
   with self.subTest(stamp=stamp),self.assertRaisesRegex(ValueError,'ambiguous'):await self.read()
  self.proof=deepcopy(original);self.change_timezone('Not/AZone')
  with self.assertRaisesRegex(ValueError,'unknown'):await self.read()
  self.assertEqual(len(self.audits()),1);self.assert_preserved()

 async def test_old_unbound_v1_and_missing_creation_records_deny_without_replacement(self):
  original=deepcopy(self.proof)
  self.proof.update(schema_version=1,purpose='returned-reply-format.v1')
  self.proof.pop('retry_control');self.proof.pop('native_creation')
  with self.assertRaisesRegex(ValueError,'binding changed'):await self.read()
  self.proof=deepcopy(original);self.proof.pop('native_creation')
  with self.assertRaisesRegex(ValueError,'binding changed'):await self.read()
  self.proof=deepcopy(original);self.proof['native_creation']['unreviewed']=True
  with self.assertRaisesRegex(ValueError,'creation evidence'):await self.read()
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_task_application_cannot_satisfy_an_explicit_project_binding(self):
  value,intent=await self.install_retry(task=T)
  self.proof['retry_control']['task_id']=''
  with self.g.connect(self.path)as db:
   db.execute("UPDATE governance_native_control SET sequence=?,command_id=? WHERE project=? AND task=''",
              (intent['control_sequence'],intent['command_id'],P))
  self.assertTrue((await self.read()) is False)
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_signed_ingress_application_hash_chain_actor_scope_and_cursor_are_rederived(self):
  original=self.m._format_provider(self.row,self.automation)
  cases=('inbox_key','payload_hash','unsigned_ingress','application_actor','application_scope','application_hash','previous_link','cursor_state')
  for case in cases:
   db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
   with self.g.connect(self.path)as source:source.backup(db)
   try:
    if case in {'unsigned_ingress','application_actor','application_scope','application_hash','previous_link'}:
     # Corrupt only a private copied database; original audit triggers stay on.
     for item in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='governance_event'").fetchall():
      db.execute('DROP TRIGGER "'+item[0].replace('"','""')+'"')
     kind='erp_event_received' if case=='unsigned_ingress' else 'native_control_applied'
     audit=dict(db.execute('SELECT *FROM governance_event WHERE kind=? ORDER BY seq DESC LIMIT 1',(kind,)).fetchone())
     if case=='unsigned_ingress':audit['actor']='unverified-erp'
     if case=='application_actor':audit['actor']='another-human'
     if case=='application_scope':
      payload=json.loads(audit['payload']);payload['task_id']=T;audit['payload']=self.g._json(payload)
     if case=='previous_link':audit['previous_hash']='0'*64
     audit['event_hash']=self.g._digest([audit[k] for k in ('event_id','erp_project','invocation_id','kind','actor','payload','created_at','previous_hash')])
     if case=='application_hash':audit['event_hash']='0'*64
     db.execute('UPDATE governance_event SET actor=?,payload=?,previous_hash=?,event_hash=? WHERE seq=?',
                (audit['actor'],audit['payload'],audit['previous_hash'],audit['event_hash'],audit['seq']))
    elif case=='inbox_key':db.execute("UPDATE governance_inbox SET event_id='different-inbox-key'")
    elif case=='payload_hash':db.execute("UPDATE governance_inbox SET payload_hash=?",('0'*64,))
    else:db.execute("UPDATE governance_native_control SET applied_state='paused'")
    before=db.total_changes
    with self.subTest(case=case):self.assertIsNone(self.m._format_authority(db,self.row,self.proof,self.packet_value,original))
    self.assertEqual(db.total_changes,before)
   finally:db.close()
  self.assertEqual(self.audits(),[]);self.assert_preserved()

 async def test_stored_admission_is_original_scope_and_not_a_receipt_shaped_substitute(self):
  receipt=self.sessions.receipt(self.admission)
  with self.sessions.connect(self.path)as db:
   db.execute('UPDATE governance_gateway_session SET admission_json=? WHERE invocation_id=?',
              (self.b.canonical(receipt),self.ident))
  with self.assertRaisesRegex(ValueError,'admission differs'):await self.read()
  self.assertEqual(self.audits(),[]);self.assert_preserved()



 async def test_maintenance_closing_during_packet_read_holds_before_selection_and_completion(self):
  maint=sys.modules['open_webui.utils.agentic_maintenance'];key=maint.path(self.path.parent)
  async def close_during_read(*args):
   current=maint.status(key)
   if current['state']=='open':
    maint.control('close',expected_epoch=current['epoch'],command_id='format-race-close',actor='fixture-human',db_path=key)
   return await self.fresh_packet(*args)
  self.bridge.packet=AsyncMock(side_effect=close_during_read)
  fallback=AsyncMock(return_value={'unexpected':'fallback'})
  completed=AsyncMock(return_value={'unexpected':'completion'})
  with patch.object(self.executor,'complete_original',completed):
   value=await self.m.recover_original(self.ident,config={},project_gate=self.gate,bridge=self.bridge,
       db_path=self.path,reader=fallback,automation=self.automation,adapter=object())
  self.assertIsNone(value)
  fallback.assert_not_called();completed.assert_not_called()
  self.assertEqual(self.audits(),[]);self.assert_preserved()

if __name__=='__main__':unittest.main()

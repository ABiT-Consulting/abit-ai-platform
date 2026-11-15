"""Bounded query-only proof of the published QA and same-Task PM status."""
import ast,contextlib,hashlib,importlib.util,json,os,re,sqlite3,stat,sys,time,types,uuid
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
sys.dont_write_bytecode=True
R=Path('/srv/abit-dev-linux/abit-ai-platform')
D=Path('/srv/abit-dev-linux/abit-ai/data')
L=Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
Q='agov-parent-bd0c8d3ad7437b0619f6c73ee16bd95e1a7e6df747d1fea8'
U='3df6d316-75cb-563b-b972-73fe4cdea33a'
F='agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35'
FU='adf93fbf-9737-5eb4-9f5d-21e02dc63014'
PROJECT='PROJ-0198';TASK='TASK-2026-06200'
sha=lambda x:hashlib.sha256(x.encode()if isinstance(x,str)else x).hexdigest()
can=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
dig=lambda x:sha(can(x))
out={'schema_version':1,'purpose':'published-qa-and-closing-pm-read-only-proof','observed_utc':datetime.now(timezone.utc).isoformat(),'invocation_id':Q,'run_id':U,'endpoint_calls':0,'model_submissions':0,'database_writes':0,'observer_repeated':False}
pins=json.loads((R/'.git/new-qa-publication-proof-20261115/source-pins.json').read_text())
for p,h in pins.items():assert sha(Path(p).read_bytes())==h
out['source_pins']=pins
loader_path=R/'scripts/prepare_delivery_history.py'
assert sha(loader_path.read_bytes())=='776c4d110bda1e6800c0dfec1eb433b9f54a360c796acc51cefcaeeb7ce7caa9'
spec=importlib.util.spec_from_file_location('readonly_selector',loader_path);loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
bridge=loader.selected(L/'utils/agentic_bridge.py',{'canonical','digest','packet_digest','stable_run_id'})
def module(name,ns):
 m=types.ModuleType(name)
 for k,v in ns.items():
  if not k.startswith('__'):setattr(m,k,v)
 sys.modules[name]=m;return m
for name in ('open_webui','open_webui.utils'):
 m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m
primary=loader.selected(L/'utils/abitdot_primary.py',{'dot_primary_key'});module('open_webui.utils.abitdot_primary',primary)
class CapabilityUnavailable(Exception):pass
scope=loader.selected(L/'utils/agentic_relay_scope.py',{'_unavailable','_proof','handoff_scope','scope_receipt'},{'PROTOCOL_VERSION','MAX_PROOF_BYTES','MAX_PROOF_SECONDS','PROOF_ID','SESSION_ID','SCOPE_ID','SCOPE_FIELDS','ENFORCEMENT_MODE','ENFORCEMENT_BOUNDARY','EXCLUDED_SURFACES','PROOF_FIELDS','BLOCKER'},{**bridge,'time':time,'CapabilityUnavailable':CapabilityUnavailable})
module('open_webui.utils.agentic_relay_scope',scope)
class RelayPreflightError(Exception):pass
relay=loader.selected(L/'utils/abitdot_primary_relay.py',{'receipt_from_relay'},{'MAX_REPLY'},{'RelayPreflightError':RelayPreflightError,'urlsplit':urlsplit})
parser=loader.selected(L/'utils/erp_dot_workflow.py',{'parse_contract_json'})
pm=loader.selected(L/'utils/agentic_pm_review.py',{'receipt_identity','review_id','_verified_source'},{'STABLE_RECEIPT'},{**bridge})
contracts=L/'utils/agentic_contracts';sys.path.insert(0,str(contracts))
spec=importlib.util.spec_from_file_location('readonly_pure_contract_validator',contracts/'validator.py');validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
delivery_contract=loader.selected(contracts/'developer_delivery.py',{'delivery_digest'})
cfgraw=Path('/srv/abit-dev-linux/abit-ai/config/erp-assignment/resolver.json').read_bytes()
assert sha(cfgraw)=='59bc74711535c060b3dc9cecde33a7941308ff249b739c892fcdcf73866db110'
cfg=json.loads(cfgraw);out['configuration_sha256']=sha(cfgraw)
ALLOWED={'agentic-governance.sqlite','abitdot-primary.sqlite','webui.db','maintenance-admission/gate.sqlite'}
opened=[]
def ro(name):
 assert name in ALLOWED
 p=D/name;assert p.is_file()
 db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
 db.execute('PRAGMA query_only=ON');db.execute('BEGIN');opened.append((name,db));return db
def query(db,sql,args=(),limit=401):
 rows=db.execute(sql,args).fetchmany(limit);assert len(rows)<limit
 return [dict(x)for x in rows]
def opaque(v):return {'present':bool(v),'bytes':len(v.encode())if isinstance(v,str)else None,'sha256':sha(v)if isinstance(v,str)else None}
def safe_reason(v):
 if not v:return {'present':False}
 known={'An original ERP/Dot request is active or unresolved','An existing ERP automation owns this task','Execution lease expired; reconcile original run without replay','Pre-submission retry limit reached'}
 return {**opaque(v),'known_literal':v if v in known else None}
def proof(name,fn):
 try:
  value=fn();out[name]={'verified':True,**(value if isinstance(value,dict)else {})}
 except Exception as e:out[name]={'verified':False,'error_type':type(e).__name__,'error_sha256':sha(str(e))}
def local_bytes(root,uri):
 assert root==D/'agentic-acceptance-evidence/20261114'/TASK/Q
 assert isinstance(uri,str)and uri.startswith('artifact://')
 name=uri[len('artifact://'):];assert re.fullmatch(r'test-[a-f0-9]{40}\.txt',name)
 p=root/name;s=p.lstat();assert stat.S_ISREG(s.st_mode)and s.st_nlink==1 and s.st_uid==999 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size<=2_000_000
 with p.open('rb')as f:
  b=f.read(2_000_001);after=os.fstat(f.fileno())
 assert len(b)<=2_000_000 and (s.st_ino,s.st_dev,s.st_size,s.st_mtime_ns)==(after.st_ino,after.st_dev,after.st_size,after.st_mtime_ns)
 return name,b
stage='opening'
try:
 assert os.geteuid()==0
 os.setegid(988);os.seteuid(999)
 g=ro('agentic-governance.sqlite');p=ro('abitdot-primary.sqlite');w=ro('webui.db');gate=ro('maintenance-admission/gate.sqlite')
 invs=query(g,'SELECT * FROM governance_invocation WHERE erp_project=? AND erp_task=? ORDER BY created_at,invocation_id',(PROJECT,TASK),101)
 byid={x['invocation_id']:x for x in invs};q=byid[Q];f=byid[F]
 assert q['automation_run_id']==U and q['target_role']=='qa'
 packet=json.loads(q['work_packet'])
 saved=query(g,'SELECT * FROM governance_result WHERE invocation_id=?',(Q,),3);local=query(g,'SELECT * FROM governance_local_result WHERE invocation_id=?',(Q,),3)
 assert len(saved)==len(local)==1
 saved=saved[0];local=local[0];result=json.loads(saved['result_json']);event=json.loads(saved['event_json']);ack=json.loads(saved['receipt_json'])
 provider=query(p,'SELECT * FROM dot_primary_runs WHERE original_run_id IN (?,?) ORDER BY original_run_id',(U,FU),4);ps={x['original_run_id']:x for x in provider};pq=ps[U];pf=ps[FU];envelope=json.loads(pq['envelope_json'])
 registered={x['role']:{'agent_id':x['agent_id'],'erp_user':x['erp_user']}for x in query(g,'SELECT role,agent_id,erp_user FROM governance_agent WHERE erp_project=?',(PROJECT,),10)}
 out['invocations']=[{**{k:x.get(k)for k in ('invocation_id','source_invocation_id','target_role','state','automation_run_id','dispatch_attempts','submitted_at','lease_fence','qa_verdict','created_at','updated_at')},'reason':safe_reason(x.get('reason')),'packet_hash':x.get('packet_hash'),'has_qa_review':bool(json.loads(x.get('work_packet')or'{}').get('qa_review'))}for x in invs]
 out['qa_provider']={'status':pq['status'],'envelope':opaque(pq['envelope_json']),'response':opaque(pq.get('response_text')),'receipt':opaque(pq.get('receipt')),'scope_receipt':opaque(pq.get('scope_receipt_json')),'conversation_url_sha256':sha(pq['conversation_url'])if pq.get('conversation_url')else None,'updated_at':pq['updated_at']}
 out['original_f33_provider']={'status':pf['status'],'envelope':opaque(pf['envelope_json']),'response':opaque(pf.get('response_text')),'receipt':opaque(pf.get('receipt')),'scope_receipt':opaque(pf.get('scope_receipt_json')),'reason':safe_reason(pf.get('reason')),'updated_at':pf['updated_at']}
 out['qa_publication']={'phase':saved['phase'],'canonical_result_sha256':saved['result_sha256'],'raw_result_sha256':sha(saved['result_json']),'local_raw_sha256':sha(local['result_json']),'outcome':result.get('outcome'),'native_ack':{k:ack.get(k)for k in ('event_id','task_id','run_id','durable','status','modified')},'native_ack_raw_sha256':sha(saved['receipt_json']),'native_ack_canonical_sha256':dig(ack),'event_type':event.get('type'),'event_sha256':dig(event),'created_at':saved['created_at'],'updated_at':saved['updated_at'],'acceptance_passed':event.get('acceptance_passed')}
 def identity_proof():
  assert saved['phase']=='published'and saved['run_id']==local['run_id']==U and saved['result_sha256']==dig(result)
  assert local['result_json']==saved['result_json']and event['result_contract']==result
  assert event['type']=='qa.passed'and event['acceptance_passed']is True and result['outcome']=='pass'
  assert result['invocation_id']==event['invocation_id']==Q and event['run_id']==U and bridge['stable_run_id'](Q)==U
  assert result['actor']==packet['binding']and result['work_packet_digest']==packet['content_sha256']==bridge['packet_digest'](packet)
  assert q['packet_hash']==dig(packet)and packet['project_id']==result['project_id']==PROJECT and packet['task_id']==result['task_id']==TASK
  assert ack['event_id']==event['event_id']=='agov-result-'+dig([Q,result])[:48]and ack['run_id']==U and ack['task_id']==TASK and ack['status']=='QA APPROVED'and ack['durable']is True
  errors=validator.validate_result(result,packet,registered_bindings=registered)
  assert not errors,can(errors)
  return {'strict_live_contract_error_count':len(errors),'result_and_native_ack_bind_exact_original':True}
 proof('canonical_identity',identity_proof)
 def provider_proof():
  assert pq['status']=='delegated'and sha(pq['envelope_json'])=='86cfbcc63cf01facc545cb4573cffafc9005acc499ff51e140c3306f2f1560d4'
  account=envelope['governance_scope']['scope']['account_id'];scoped=json.loads(pq['scope_receipt_json'])
  checked=scope['scope_receipt'](envelope,account,scoped);assert checked==scoped
  receipt=relay['receipt_from_relay'](envelope,{'dot_handoff':True,'account_id':account,'response':pq['response_text'],'conversation_url':pq['conversation_url'],'scope_receipt':scoped},account)
  assert receipt['status']=='accepted'and receipt['receipt_id']==pq['receipt']
  lines=[x[len('AGOV RESULT:'):].strip()for x in pq['response_text'].splitlines()if x.startswith('AGOV RESULT:')]
  assert len(lines)==1 and len(lines[0].encode())<=200000
  raw_result=parser['parse_contract_json'](lines[0]);assert raw_result==result
  return {'exact_provider_receipt_rederived':True,'reply_contract_equals_published_result':True,'original_reply_bytes':len(pq['response_text'].encode()),'original_reply_sha256':sha(pq['response_text'])}
 proof('provider_reply_receipt',provider_proof)
 proof('verified_qa_pm_source',lambda: {'review_sha256':dig(pm['_verified_source'](g,Q)[1]),'expected_pm_invocation_id':pm['review_id'](PROJECT,TASK,pm['_verified_source'](g,Q)[1])})
 sessions=query(g,'SELECT * FROM governance_gateway_session WHERE invocation_id=?',(Q,),3);assert len(sessions)==1
 calls=query(g,'SELECT * FROM governance_gateway_call WHERE session_id=? ORDER BY created_at,call_id',(sessions[0]['session_id'],),21)
 out['gateway_calls']=[{'call_id_sha256':sha(x['call_id']),'operation':x['operation'],'state':x['state'],'request_hash':x['request_hash'],'result_sha256':sha(x['result_json'])if x['result_json']else None,'created_at':x['created_at'],'updated_at':x['updated_at']}for x in calls]
 @contextlib.contextmanager
 def read_db(_=None):yield g
 profile=cfg['agentic_repository_gateway']['projects'][PROJECT]
 workspace=query(g,'SELECT * FROM governance_repository_workspace WHERE invocation_id=?',(Q,),3);qa_delivery=query(g,'SELECT * FROM governance_qa_delivery WHERE invocation_id=?',(Q,),3);assert len(workspace)==len(qa_delivery)==1
 snapshot={'sha256':workspace[0]['snapshot_sha256']}
 tests=loader.selected(L/'utils/agentic_gateway_delivery.py',{'_tests','_assert_observations'},namespace={'_db':read_db,'local_bytes':local_bytes,'artifact_directory':lambda row,config:D/'agentic-acceptance-evidence/20261114'/TASK/Q,**bridge})
 def tests_proof():
  testcalls=[x for x in calls if x['operation']=='repository.test']
  assert len(testcalls)==1 and all(x['state']=='completed'for x in calls)
  tested=tests['_tests']([x['call_id']for x in testcalls],q,packet,profile,snapshot,cfg,None)
  tests['_assert_observations'](result,tested)
  delivery=packet['implementation_delivery'];dd=delivery_contract['delivery_digest'](delivery)
  assert result['reviewed_delivery_sha256']==qa_delivery[0]['delivery_sha256']==dd
  assert qa_delivery[0]['snapshot_sha256']==snapshot['sha256'] and qa_delivery[0]['packet_sha256']==packet['content_sha256']
  verifying=[json.loads(x['result_json'])for x in calls if x['operation']=='repository.verify_delivery']
  assert verifying[-1]['tests_complete']is True and verifying[-1]['reviewed_delivery_sha256']==dd and verifying[-1]['repository_snapshot_sha256']==snapshot['sha256']
  tests['_assert_observations'](result,verifying[-1])
  src=byid[result['implementation_invocation_id']];ss=query(g,'SELECT * FROM governance_result WHERE invocation_id=?',(src['invocation_id'],),3);assert len(ss)==1 and ss[0]['phase']=='published'
  implementation=json.loads(ss[0]['result_json']);assert implementation['delivery']==delivery and implementation['outcome']=='ready_for_review'and src['automation_run_id']==packet['implementation_run_id']
  logs=[]
  for c in testcalls:
   actual=json.loads(c['result_json']);name,b=local_bytes(D/'agentic-acceptance-evidence/20261114'/TASK/Q,actual['evidence']['uri']);log=json.loads(b)
   logs.append({'artifact_name':name,'sha256':sha(b),'bytes':len(b),'exit_code':actual['exit_code'],'sandbox':actual['sandbox'],'registered_test_id':actual['test_id'],'command_sha256':sha(actual['command']),'stdout_sha256':actual['stdout_sha256'],'stderr_sha256':actual['stderr_sha256'],'reported_pass_counts':[int(x)for x in re.findall(r'(?m)Ran (\\d+) tests?',log.get('stdout','')+'\\n'+log.get('stderr',''))],'snapshot_sha256':actual['repository_snapshot_sha256']})
  return {'original_registered_test_calls':len(testcalls),'acceptance_tests':len(tested['test_results']),'all_pass':all(x['status']=='pass'for x in tested['test_results']),'logs':logs,'delivery_sha256':dd,'head_commit':delivery['head_commit'],'base_commit':delivery['base_commit'],'pull_request_url':delivery['pull_request_url'],'implementation_invocation_id':src['invocation_id'],'implementation_run_id':src['automation_run_id'],'second_verify_tests_complete':True,'no_fresh_remote_readback':True}
 proof('test_and_delivery_identity',tests_proof)
 for table,where,args in [('governance_pm_review','project=? AND task=?',(PROJECT,TASK)),('governance_pm_review_turn','qa_invocation_id=?',(Q,)),('governance_verification','qa_invocation_id=?',(Q,)),('governance_task_lease','erp_project=? AND erp_task=?',(PROJECT,TASK))]:
  exists=bool(g.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())
  vals=query(g,'SELECT * FROM '+table+' WHERE '+where,args,101)if exists else []
  out[table]={'present':exists,'rows':[{k:v for k,v in x.items()if k not in {'actor'}}for x in vals]}
 selected_ids=[Q,F]+[x['invocation_id']for x in invs if x['target_role']=='project_manager'];marks=','.join('?'for _ in selected_ids)
 events=query(g,'SELECT * FROM governance_event WHERE invocation_id IN ('+marks+') ORDER BY seq',selected_ids,401);evs=[]
 for x in events:
  prior=g.execute('SELECT event_hash FROM governance_event WHERE erp_project=? AND seq<? ORDER BY seq DESC LIMIT 1',(PROJECT,x['seq'])).fetchone()
  ok=x['event_hash']==dig([x['event_id'],x['erp_project'],x['invocation_id'],x['kind'],x['actor'],x['payload'],x['created_at'],x['previous_hash']])and x['previous_hash']==(prior[0]if prior else'')
  value=json.loads(x['payload']);evs.append({'seq':x['seq'],'invocation_id':x['invocation_id'],'kind':x['kind'],'created_at':x['created_at'],'event_hash':x['event_hash'],'payload_sha256':sha(x['payload']),'payload_keys':sorted(value)if isinstance(value,dict)else None,'hash_and_predecessor_valid':ok})
 out['selected_audits']=evs
 out['gate']=[dict(x)for x in gate.execute('SELECT state,epoch FROM gate')]
 out['active_admissions']=[{k:x[k]for k in ('kind','reference','state','epoch','pid','process_start')}for x in query(gate,"SELECT * FROM lease WHERE state IN('active','unresolved') ORDER BY reference",(),21)]
 runs=[x['automation_run_id']for x in invs if x.get('automation_run_id')]
 records=query(w,'SELECT * FROM automation_run WHERE id IN ('+','.join('?'for _ in runs)+')',runs,101)
 out['automation_runs']=[{**{k:x.get(k)for k in ('id','automation_id','status','started_at','finished_at','created_at','updated_at')},'result':opaque(x.get('result')),'error':safe_reason(x.get('error'))}for x in records]
 # Evaluate only the pinned already-live historical f33 recognizer. It cannot mutate provider history.
 actual_connect=sqlite3.connect
 def only_ro(database,*args,**kw):
  assert kw.get('uri')is True and str(database).endswith('?mode=ro')
  assert str(database)in {'file:'+str(D/n)+'?mode=ro'for n in ('agentic-governance.sqlite','webui.db')}
  c=actual_connect(database,*args,**kw);c.execute('PRAGMA query_only=ON');opened.append(('historical_recognizer',c));return c
 own=loader.selected(L/'utils/agentic_ownership.py',{'_digest','_published_delivery'},namespace={'Path':Path,'sqlite3':types.SimpleNamespace(connect=only_ro,Error=sqlite3.Error),'uuid':uuid})
 proof('already_live_f33_published_recognition',lambda:{'recognized':own['_published_delivery'](D,TASK,FU,json.loads(pf['envelope_json']),pf['owner'],pf['automation_id']),'provider_unknown_unchanged':pf['status']=='unknown','no_canonical_policy_adoption':True})
 out['snapshot_rows_not_cross_database_atomic']=True
 out['query_only_connections']=[]
 for name,c in opened:
  try:out['query_only_connections'].append({'name':name,'query_only':c.execute('PRAGMA query_only').fetchone()[0],'total_changes':c.total_changes})
  except sqlite3.ProgrammingError:out['query_only_connections'].append({'name':name,'closed_by_read_only_helper':True})
 for path,pin in pins.items():assert sha(Path(path).read_bytes())==pin
 out['source_pins_unchanged']=True
except Exception as e:
 out['reader_error']={'stage':stage,'type':type(e).__name__,'message_sha256':sha(str(e))}
finally:
 for name,c in opened:
  try:c.close()
  except Exception:pass
 if os.geteuid()!=0:os.seteuid(0);os.setegid(0)
 out['finished_utc']=datetime.now(timezone.utc).isoformat()
 print(json.dumps(out,sort_keys=True))

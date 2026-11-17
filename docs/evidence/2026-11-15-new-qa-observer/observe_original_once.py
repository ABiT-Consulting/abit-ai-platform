"""One-shot observation preparation for the exact submitted QA; Root review required."""
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from types import ModuleType
import hashlib, http.client, importlib.util, json, os, re, signal, sqlite3, stat, sys, time
sys.dont_write_bytecode = True

REPO = Path('/srv/abit-dev-linux/abit-ai-platform')
LIVE = Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
SERVICE = Path('/srv/abit-dev-linux/relay-runtime/service')
DATA = Path('/srv/abit-dev-linux/abit-ai/data')
DEST = Path('/var/lib/abit-ai-erp-rollouts/new-qa-original-observe-3df6d316-20261115')
INV = 'agov-parent-bd0c8d3ad7437b0619f6c73ee16bd95e1a7e6df747d1fea8'
RUN = '3df6d316-75cb-563b-b972-73fe4cdea33a'
ENVELOPE_SHA = '86cfbcc63cf01facc545cb4573cffafc9005acc499ff51e140c3306f2f1560d4'
PACKET_SHA = 'a2f55eb7ac46d95666a181ccda77d4f12470e14ca3d832f6fa1c799a0b508c8a'
PACKET_DIGEST = 'd146068f9e743ba6fec435452f3f01d20d6623f3961da0ef691730bdff6f7f82'
MAIN = (1189079, '71364635', 'c6baac9b17d8e12e9d8a535cb09e7bdf375f9d383432121340ed62d8fb0805e5')
NODE = (1824372, '69069325', 'daa409a71e910a962e855daf8b780d479e53d3413eb1cf57abda964cf13a58bb')
PINS = {
 REPO/'scripts/prepare_delivery_history.py':'776c4d110bda1e6800c0dfec1eb433b9f54a360c796acc51cefcaeeb7ce7caa9',
 LIVE/'utils/agentic_bridge.py':'a0220b323e62fa27c14b00cdda61f5af4439805c0963c94b4b301091bd327f3b',
 LIVE/'utils/abitdot_primary.py':'3848123b6dbc7eb3aa46b64e867a0847a1e5f792ebe6947bdb9b8b5cc3b387a8',
 LIVE/'utils/abitdot_primary_relay.py':'5e58239fe1f1cd5242460a6b4c8b86a959608fafd41528b671a340dad16c83aa',
 LIVE/'utils/agentic_relay_scope.py':'50fc65add1abe1e5b50e1ccf3ae2bcafa33ab8ff26a39fca168ff7301be40055',
 LIVE/'utils/agentic_recovery.py':'72ecafaf0d5449156525c750b24015bd5ac4cf8536b5f7486abc29633a3ea893',
 LIVE/'routers/agentic_governance.py':'9c08767360232ef9e358148c22222ac4b8ebd8d03c18332ff1a8dfebb09c89d7',
 LIVE/'utils/agentic_gateway_sessions.py':'2c7250179de04419cc751a08bc1ffe2ee4e33c9179acd3e73d5db16a8e575794',
 LIVE/'utils/agentic_tool_gateway.py':'463b9550ccdc1ce8edc2b85759981c8ca316cb8dba1aab3a2acfc206d33ba065',
 SERVICE/'aibit_relay.cjs':'b3cd84de552c487e4751986929cd0e1cb70abb0af60d366459ac791df739627d',
 SERVICE/'aibit_dot_feed.cjs':'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53',
 SERVICE/'aibit_dot_observation.cjs':'e53f5fe838ce44a9b3408e5744e2b12591e37e2220dcb3573f1e07ca53982e27',
 SERVICE/'aibit_dot_handoff.cjs':'4d6c26fb17ee1d59c7d0974c29db6d024325e988eb9cc7c96037dae86a024464',
 SERVICE/'chat_thread_tabs.cjs':'1057d64935a8d51f1695893911f8d342701958f7c3f694027f5d17d5d2555dcc',
 SERVICE/'agentic-gateway/relay.cjs':'b096564620080dc9ecb7a661a71b0c7403b342ca52059060d6aeb66b2deca7c7',
 SERVICE/'agentic-gateway/wire.cjs':'14b1be3703e17d2fcb9c070ceb55ef34d6cb24272dd875cc7ecb1820cdf24887',
}
EXPECTED_CALLS = [
 ('context.read','17fc0563cff47052778506e83e23943fd338dc185009582053a62bb36d6a1830','cc1c41b83d9ddab908257e3b53257675535512929be95143d47216139d841b39'),
 ('repository.prepare','63dce0cd477ad7a1823c479559c03c48e2a58e1fa98b3a6d4235a652332fe569','b2fa8fc55db025a35382194ed165402a8574ddfe606770e4d262f6fe562bd96d'),
 ('repository.verify_delivery','fb7690fdcb7f118f2f6966aa9aa5dc31d82fe3701178ed980238e3863edb5c59','04af65b4f2fd150114480ad1be5f68aa47fd6dc89bae770da6cf9aa114bcbc0b'),
 ('repository.test','cd98b979e3595f38a566547c560d4121e36a7266b9c482d1bd600bf389b040b3','702baea173a8e75e85ca5a0072d03387f7e349636d926c6be11d6b15d30a22f4'),
 ('repository.verify_delivery','fb7690fdcb7f118f2f6966aa9aa5dc31d82fe3701178ed980238e3863edb5c59','554484dc730f51b62943c356f46dfce791a19647b934c2bf366bae9e5ad48424'),
]
sha=lambda raw:hashlib.sha256(raw.encode() if isinstance(raw,str) else raw).hexdigest()
canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
class Denied(RuntimeError): pass
def need(value,code):
 if not value:raise Denied(code)
def identity(value):
 pid,birth,argv=value;root=Path('/proc')/str(pid)
 fields=(root/'stat').read_text().rsplit(')',1)[1].split()
 need(fields[0] not in {'Z','X'} and fields[19]==birth and sha((root/'cmdline').read_bytes())==argv,'PROCESS_IDENTITY_CHANGED')
 return {'pid':pid,'birth':birth,'argv_sha256':argv}
def pin_sources():
 for path,digest in PINS.items():
  need(sha(path.read_bytes())==digest,'SOURCE_PIN_CHANGED:'+path.name)
 need(Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui').resolve()==LIVE,'RUNTIME_TARGET_CHANGED')
def rows(name,queries):
 need(name in {'agentic-governance.sqlite','abitdot-primary.sqlite','aibit-chatgpt-relay.sqlite','maintenance-admission/gate.sqlite'},'DATABASE_SCOPE')
 os.setegid(988);os.seteuid(999);db=None
 try:
  path=DATA/name
  need(path.is_file(),'EXISTING_DATABASE_REQUIRED')
  db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
  db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
  result={key:[dict(r) for r in db.execute(sql,args)] for key,(sql,args) in queries.items()}
  need(db.total_changes==0,'QUERY_ONLY_REQUIRED')
  return result
 finally:
  if db is not None:db.close()
  os.seteuid(0);os.setegid(0)
def snapshot():
 g=rows('agentic-governance.sqlite',{
  'inv':('SELECT * FROM governance_invocation WHERE invocation_id=? LIMIT 2',(INV,)),
  'session':('SELECT * FROM governance_gateway_session WHERE invocation_id=? LIMIT 2',(INV,)),
  'calls':('SELECT c.* FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id WHERE s.invocation_id=? ORDER BY c.created_at,c.call_id LIMIT 6',(INV,)),
  'start':('SELECT * FROM governance_start WHERE invocation_id=? LIMIT 2',(INV,)),
  'result':('SELECT invocation_id FROM governance_result WHERE invocation_id=? LIMIT 2',(INV,)),
  'local':('SELECT invocation_id FROM governance_local_result WHERE invocation_id=? LIMIT 2',(INV,))})
 need(len(g['inv'])==len(g['session'])==len(g['start'])==1 and not g['result'] and not g['local'],'ORIGINAL_ALREADY_ADVANCED_OR_AMBIGUOUS')
 row=g['inv'][0]
 need(row['state']=='running' and row['automation_run_id']==RUN and row['dispatch_attempts']==2
      and row['submitted_at']==1794761332 and row['lease_fence']==9 and row['packet_hash']==PACKET_SHA
      and row['erp_project']=='PROJ-0198' and row['erp_task']=='TASK-2026-06200' and row['target_role']=='qa','QA_IDENTITY_CHANGED')
 provider=rows('abitdot-primary.sqlite',{'original':('SELECT * FROM dot_primary_runs WHERE original_run_id=? LIMIT 2',(RUN,))})['original']
 need(len(provider)==1,'PROVIDER_AMBIGUOUS')
 original=provider[0]
 need(original['status']=='dispatching' and not original.get('response_text') and not original.get('receipt')
      and not original.get('scope_receipt_json') and sha(original['envelope_json'])==ENVELOPE_SHA,'ORIGINAL_PROVIDER_CHANGED')
 envelope=json.loads(original['envelope_json']);packet=json.loads(row['work_packet'])
 admission=envelope['governance_scope'];session=g['session'][0]
 need(envelope['run_id']==RUN and envelope['automation_id']==row['agent_id']
      and admission['scope']['invocation_id']==INV and admission['scope']['run_id']==RUN
      and admission['scope']['role']=='qa' and admission['scope']['work_packet_digest']==PACKET_DIGEST
      and packet['content_sha256']==PACKET_DIGEST and session['state']=='admitted'
      and json.loads(session['admission_json'])==admission and json.loads(session['scope_json'])==admission['scope'],'SAVED_ADMISSION_CHANGED')
 calls=[(c['operation'],c['request_hash'],sha(c['result_json']) if c['result_json'] else None) for c in g['calls']]
 need(calls==EXPECTED_CALLS and all(c['state']=='completed' for c in g['calls']),'ORIGINAL_GATEWAY_WORK_CHANGED')
 start=json.loads(g['start'][0]['receipt_json'] or '{}')
 need(start.get('durable') is True and start.get('run_id')==RUN and start.get('task_id')=='TASK-2026-06200','ORIGINAL_START_NOT_DURABLE')
 gate=rows('maintenance-admission/gate.sqlite',{
  'gate':('SELECT state,epoch FROM gate WHERE id=1',()),
  'active':("SELECT kind,reference,state,epoch,pid,process_start FROM lease WHERE state IN ('active','unresolved') ORDER BY reference LIMIT 3",())})
 need(gate['gate']==[{'state':'open','epoch':30}] and gate['active']==[
  {'kind':'governed','reference':INV,'state':'active','epoch':30,'pid':MAIN[0],'process_start':MAIN[1]}],'ORIGINAL_ADMISSION_OR_GATE_CHANGED')
 safe={'invocation_id':INV,'run_id':RUN,'state':row['state'],'dispatch_attempts':2,
  'packet_sha256':PACKET_SHA,'work_packet_digest':PACKET_DIGEST,'envelope_sha256':ENVELOPE_SHA,
  'gateway_session_sha256':sha(canonical(session)),'gateway_calls_sha256':sha(canonical(g['calls'])),
  'start_row_sha256':sha(canonical(g['start'][0])),'provider_state':'dispatching','reply_present':False,
  'local_and_canonical_results':0,'gate':gate['gate'][0],'original_admission':gate['active'][0]}
 return row,envelope,safe
def env(pid):
 return dict(part.decode().split('=',1) for part in (Path('/proc')/str(pid)/'environ').read_bytes().split(b'\0') if b'=' in part)
def private(name,raw):
 need(DEST.is_dir() and '/' not in name and name not in {'.','..'},'PRIVATE_OUTPUT_SCOPE')
 fd=os.open(DEST/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
def save(name,value):
 raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();private(name,raw);return sha(raw)
def module(name,values):
 m=ModuleType(name)
 for key,value in values.items():
  if not key.startswith('__'):setattr(m,key,value)
 sys.modules[name]=m;return m
def ui_summary(result):
 observed=result.get('observed') if isinstance(result,dict) else None
 if not isinstance(observed,dict):return {'present':False}
 def count(name):
  value=observed.get(name);return len(value) if isinstance(value,list) else None
 def number(name):
  value=observed.get(name);return value if type(value) is int and 0<=value<=1000000 else None
 buttons=observed.get('buttons');buttons=buttons if isinstance(buttons,list) else []
 fixed={'stop':{'stop generating','stop streaming'},'continue':{'continue generating','continue response'},'send':{'send','send prompt'}}
 signals={key:False for key in fixed}
 for item in buttons[:60]:
  if not isinstance(item,dict):continue
  for field in ('label','text'):
   label=item.get(field)
   if not isinstance(label,str):continue
   for key,values in fixed.items():
    if label.strip().lower() in values and item.get('disabled') is False:signals[key]=True
 text_values=[]
 for name in ('assistant_tail','body_tail'):
  value=observed.get(name)
  if isinstance(value,str):text_values.append(value)
  elif isinstance(value,list):text_values.extend(x for x in value[:2] if isinstance(x,str))
 return {'present':True,'composer_length':number('composer_length'),
  'composer_matches_original':observed.get('composer_matches') is True,
  'original_user_visible':observed.get('original_user_visible') is True,
  'user_count':number('users'),'assistant_count':number('assistants'),
  'feed_count':count('feed'),'original_marker_ancestor_count':count('marker_ancestors'),
  'result_marker_ancestor_count':count('result_ancestors'),'dialog_count':count('dialogs'),
  'recognized_enabled_buttons':signals,
  'formal_result_marker_visible':any('AGOV RESULT:' in s for s in text_values),
  'text_not_exported':True,'diagnostic_only_not_terminal_or_acceptance_proof':True}
def deadline(signum,frame):raise TimeoutError('ONE_OBSERVATION_DEADLINE')
def main():
 need(os.geteuid()==0 and os.getegid()==0,'ROOT_REQUIRED')
 os.umask(0o077);pin_sources();identity(MAIN);identity(NODE)
 need(not DEST.exists() and not DEST.is_symlink(),'OBSERVATION_ALREADY_HAS_EVIDENCE_NO_REPLAY')
 loader_path=REPO/'scripts/prepare_delivery_history.py'
 spec=importlib.util.spec_from_file_location('reviewed_pure_source_selector',loader_path)
 loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
 bridge=loader.selected(LIVE/'utils/agentic_bridge.py',{'canonical','digest','packet_digest','stable_run_id'})
 primary=loader.selected(LIVE/'utils/abitdot_primary.py',{'dot_primary_key'})
 for name in ('open_webui','open_webui.utils'):
  m=ModuleType(name);m.__path__=[];sys.modules[name]=m
 module('open_webui.utils.abitdot_primary',primary)
 class CapabilityUnavailable(Exception):pass
 scope=loader.selected(LIVE/'utils/agentic_relay_scope.py',{'_unavailable','_proof','handoff_scope','scope_receipt'},
  {'PROTOCOL_VERSION','MAX_PROOF_BYTES','MAX_PROOF_SECONDS','PROOF_ID','SESSION_ID','SCOPE_ID','SCOPE_FIELDS','ENFORCEMENT_MODE','ENFORCEMENT_BOUNDARY','EXCLUDED_SURFACES','PROOF_FIELDS','BLOCKER'},
  {**bridge,'time':time,'CapabilityUnavailable':CapabilityUnavailable})
 module('open_webui.utils.agentic_relay_scope',scope)
 class RelayPreflightError(Exception):pass
 relay=loader.selected(LIVE/'utils/abitdot_primary_relay.py',{'verified_dot_payload','receipt_from_relay','validated_local_relay_url'},
  {'ACCOUNT_ID','DOT_JOB_ID','MAX_MESSAGE','MAX_REPLY','ALLOWED_RELAY_HOSTS'},
  {'RelayPreflightError':RelayPreflightError,'urlsplit':urlsplit})
 row,envelope,before=snapshot()
 need(envelope['idempotency_key']==primary['dot_primary_key'](envelope['owner_id'],row['agent_id'],RUN)
      and bridge['stable_run_id'](INV)==RUN,'ORIGINAL_IDEMPOTENCY_CHANGED')
 accounts=rows('aibit-chatgpt-relay.sqlite',{'accounts':('SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 2',(envelope['owner_id'],))})['accounts']
 need(accounts and len({x['account'] for x in accounts})==1,'ORIGINAL_SYNC_ACCOUNT_AMBIGUOUS')
 account=accounts[0]['account'];payload=relay['verified_dot_payload'](envelope,account,admission=False)
 need(payload['governance_scope']==envelope['governance_scope'],'ORIGINAL_ADMISSION_SUBSTITUTED')
 node=env(NODE[0])
 need(node.get('ABIT_CHATGPT_DOT_OWNER_ID')==envelope['owner_id']
      and node.get('ABIT_CHATGPT_DOT_URL')=='https://chatgpt.com/dots/'+envelope['dot_id'],'RELAY_ORIGINAL_OWNER_CHANGED')
 config_path=Path('/srv/abit-dev-linux/abit-ai/config/runtime-environment.json')
 config_raw,_=loader.read_file(config_path)
 need(sha(config_raw)=='ef68f172f355d14c9e4e355bc7f8c7f59541a209f917662162b224232a6ce09c','RUNTIME_CONFIGURATION_CHANGED')
 config=json.loads(config_raw)
 need(relay['validated_local_relay_url'](config['ABIT_DOT_PRIMARY_RELAY_URL'])=='http://127.0.0.1:8810'
      and config['ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE']=='/srv/abit-dev-linux/relay-runtime/secrets/relay.token','LOCAL_RELAY_CONFIGURATION_CHANGED')
 registry=Path(node.get('ABIT_ERP_CHATGPT_THREAD_TARGET_FILE') or str(Path(node['ABIT_ERP_CHATGPT_TARGET_FILE']).parent/'chat-thread-targets.json'))
 need(registry==Path('/srv/abit-dev-linux/relay-runtime/state/chat-thread-targets.json'),'REGISTRY_SCOPE_CHANGED')
 registry_raw,_=loader.read_file(registry);mapping=json.loads(registry_raw)
 thread_key=sha('aibit:'+envelope['owner_id']+':dot_'+payload['job_id'])[:40]
 need(thread_key in mapping and re.fullmatch(r'[a-fA-F0-9]{16,64}',str(mapping[thread_key]['id'])),'EXISTING_ORIGINAL_TARGET_MISSING')
 token_raw,_=loader.read_file(Path(config['ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE']),16384)
 token=token_raw.decode().strip();need(token,'EXISTING_CREDENTIAL_MISSING')
 request=canonical(payload).encode();need(len(request)<=1048576,'REQUEST_BOUND')
 # Create-only private operation identity; no automatic rerun after an attempted observation.
 need(DEST.parent.resolve()==DEST.parent and DEST.parent.stat().st_uid==0,'PRIVATE_PARENT_REQUIRED')
 DEST.mkdir(mode=0o700);DEST.chmod(0o700)
 private('observer-source.py',Path(__file__).read_bytes());private('registry-before.private.json',registry_raw)
 save('before.private.json',before)
 summary={'schema_version':1,'purpose':'one-original-qa-read-only-observation.v1','invocation_id':INV,'run_id':RUN,
  'started_at_utc':datetime.now(timezone.utc).isoformat(),'health_get_attempts':0,'observe_post_attempts':0,
  'model_submissions':0,'fresh_admissions':0,'database_writes':0,'result_persistence':False,
  'request_sha256':sha(request),'envelope_sha256':ENVELOPE_SHA,'source_pins':{str(k):v for k,v in PINS.items()},
  'main':identity(MAIN),'relay_node':identity(NODE),'max_response_bytes':1048576,
  'observe_wall_deadline_seconds':35,'observe_socket_timeout_seconds':35}
 conn=None;captured=bytearray();body_complete=False;result={};verified=False
 prior_handler=signal.getsignal(signal.SIGALRM)
 signal.signal(signal.SIGALRM,deadline)
 try:
  _,_,fresh=snapshot();need(fresh==before,'ORIGINAL_CHANGED_BEFORE_OBSERVATION')
  pin_sources();identity(MAIN);identity(NODE)
  save('intent.json',{**summary,'method':'POST','route':'http://127.0.0.1:8810/dot/observe','attempts_authorized':1,
   'no_redirect_no_retry':True,'existing_target_mapping_sha256':sha(canonical(mapping[thread_key])),
   'existing_target_registry_may_prune_stale_entry':True,'no_send_no_admission_no_result_write':True})
  signal.setitimer(signal.ITIMER_REAL,35)
  summary['observe_post_attempts']=1
  conn=http.client.HTTPConnection('127.0.0.1',8810,timeout=35)
  conn.request('POST','/dot/observe',body=request,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
  response=conn.getresponse();summary['http_status']=response.status
  while True:
   chunk=response.read(65536)
   if not chunk:body_complete=True;break
   remaining=1048576-len(captured)
   captured.extend(chunk[:remaining]);need(len(chunk)<=remaining,'RESPONSE_BOUND')
  result=json.loads(bytes(captured))
  if response.status==200 and isinstance(result,dict):
   exact=(result.get('read_only') is True and result.get('owner')==envelope['owner_id']
    and result.get('job_id')==payload['job_id'] and bool(result.get('root_message_id')) and bool(result.get('reply_message_id')))
   receipt=relay['receipt_from_relay'](envelope,result,account) if exact else None
   verified=bool(receipt and receipt.get('status')=='accepted')
   if verified:save('verified-original-receipt.private.json',receipt)
 except BaseException as error:
  summary['error_type']=type(error).__name__
  if isinstance(error,Denied):summary['error_code']=str(error)
 finally:
  signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,prior_handler)
  if conn is not None:conn.close()
 if summary['observe_post_attempts']:
  private('response.private.bin',bytes(captured))
  summary.update(response_sha256=sha(bytes(captured)),response_bytes=len(captured),response_body_complete=body_complete)
 if isinstance(result,dict):
  summary.update(read_only=result.get('read_only') is True,dot_handoff=result.get('dot_handoff') is True,
   response_present=isinstance(result.get('response'),str) and bool(result['response'].strip()),
   root_identity_present=bool(result.get('root_message_id')),reply_identity_present=bool(result.get('reply_message_id')),
   root_identity_sha256=sha(result['root_message_id']) if isinstance(result.get('root_message_id'),str) else None,
   reply_identity_sha256=sha(result['reply_message_id']) if isinstance(result.get('reply_message_id'),str) else None,
   ui=ui_summary(result))
 try:
  pin_sources();identity(MAIN);identity(NODE)
  registry_after,_=loader.read_file(registry);private('registry-after.private.json',registry_after)
  summary['registry_unchanged']=registry_after==registry_raw
  summary['source_and_process_pins_unchanged']=True
 except BaseException as error:
  summary['source_and_process_pins_unchanged']=False;summary['postcheck_error_type']=type(error).__name__
  if isinstance(error,Denied):summary['postcheck_error_code']=str(error)
 try:
  _,_,after=snapshot();save('after.private.json',after);summary['original_snapshot_unchanged']=after==before
 except BaseException as error:
  summary['original_snapshot_unchanged']=False;summary['post_observation_original_may_have_progressed']=True
  summary['post_original_check_error_type']=type(error).__name__
 summary['original_receipt_valid_under_pinned_checker']=verified
 summary['verified_original_receipt_and_stable_source']=verified and summary.get('source_and_process_pins_unchanged') is True
 summary['finished_at_utc']=datetime.now(timezone.utc).isoformat()
 digest=save('observation-receipt.json',summary)
 print(json.dumps({**summary,'observation_receipt_sha256':digest,'private_evidence_directory':str(DEST)},sort_keys=True))
 return 0 if summary.get('source_and_process_pins_unchanged') is True and body_complete else 2

if __name__=='__main__':
 try:raise SystemExit(main())
 except Denied as error:print(json.dumps({'held':True,'error_type':'Denied','error_code':str(error),'observe_post_not_automatically_repeated':True}));raise SystemExit(2)

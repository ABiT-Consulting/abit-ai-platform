"""Actual-source, no-network reproduction of closing-PM registration-map rejection."""
import ast,asyncio,hashlib,importlib.util,json,os,sqlite3,sys,types
from pathlib import Path
from datetime import datetime,timezone
sys.dont_write_bytecode=True
R=Path('/srv/abit-dev-linux/abit-ai-platform');D=Path('/srv/abit-dev-linux/abit-ai/data');L=Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
P='agov-pm-review-9ac3cfd3cc1969734e889cec5e0e4c978dd19ab86738fa5c'
sha=lambda b:hashlib.sha256(b.encode()if isinstance(b,str)else b).hexdigest()
canon=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
pins=json.loads((R/'.git/new-qa-publication-proof-20261115/source-pins.json').read_text())
out={'schema_version':1,'purpose':'closing-pm-actual-source-registration-map-reproduction','observed_utc':datetime.now(timezone.utc).isoformat(),'invocation_id':P,'endpoint_calls':0,'model_submissions':0,'database_writes':0,'production_behavior_changed':False}
for p,h in pins.items():assert sha(Path(p).read_bytes())==h
loaderpath=R/'scripts/prepare_delivery_history.py';assert sha(loaderpath.read_bytes())=='776c4d110bda1e6800c0dfec1eb433b9f54a360c796acc51cefcaeeb7ce7caa9'
spec=importlib.util.spec_from_file_location('pure_selector_pm',loaderpath);loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
bridge=loader.selected(L/'utils/agentic_bridge.py',{'canonical','digest','packet_digest','stable_run_id'})
contracts=L/'utils/agentic_contracts';sys.path.insert(0,str(contracts))
spec=importlib.util.spec_from_file_location('pm_pure_validator',contracts/'validator.py');validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
def mod(name,values=None):
 m=types.ModuleType(name)
 if values:
  for k,v in values.items():setattr(m,k,v)
 sys.modules[name]=m;return m
counters={'roster_reads':0,'validator_calls':0,'primary_dot_lookup':0,'account_lookup':0,'credential_read':0,'http_client':0}
def forbidden(kind):
 def fn(*a,**k):counters[kind]+=1;raise AssertionError('NO_EXTERNAL_EFFECTS')
 return fn
class CapabilityUnavailable(Exception):pass
db=None
try:
 assert os.geteuid()==0;os.setegid(988);os.seteuid(999)
 path=D/'agentic-governance.sqlite';assert path.is_file()
 db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
 rows=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(P,)).fetchall();assert len(rows)==1
 row=dict(rows[0]);packet=json.loads(row['work_packet'])
 assert row['target_role']=='project_manager'and row['state']=='blocked'and row['automation_run_id']is None and row['dispatch_attempts']==0 and row['submitted_at']is None
 assert sha(row['work_packet'])=='1fd522a6dd7dbd489f8fe2ef6c4e7b92e0ab6419af553acc23e256ee6efb3f89'
 roster=[dict(x)for x in db.execute('SELECT * FROM governance_agent WHERE erp_project=?',(row['erp_project'],)).fetchmany(20)];assert 1<=len(roster)<20
 registered=next(x for x in roster if x['agent_id']==row['agent_id']and x['role']==row['target_role'])
 restricted={row['target_role']:{'agent_id':registered['agent_id'],'erp_user':registered['erp_user']}}
 complete={x['role']:{'agent_id':x['agent_id'],'erp_user':x['erp_user']}for x in roster};assert len(complete)==len(roster)
 restricted_errors=validator.validate_work_packet(packet,registered_bindings=restricted)
 complete_errors=validator.validate_work_packet(packet,registered_bindings=complete)
 assert restricted_errors==['Agent is not registered for this project']
 assert complete_errors==[]
 out['saved_packet']={'sha256':sha(row['work_packet']),'content_sha256':packet['content_sha256'],'closing_review':bool(packet['qa_review']),'registered_target_equal':packet['binding']['agent_id']==restricted['project_manager']['agent_id'],'nested_qa_registered':packet['qa_review']['result_contract']['actor']['agent_id']==complete['qa']['agent_id'],'nested_qa_erp_binding_equal':packet['qa_review']['result_contract']['actor']['erp_user']==complete['qa']['erp_user'],'invocation_state':row['state'],'dispatch_attempts':row['dispatch_attempts'],'run_present':False}
 out['exact_live_registration_map']={'roles':sorted(restricted),'errors':restricted_errors}
 out['diagnostic_complete_same_project_roster']={'roles':sorted(complete),'errors':complete_errors,'does_not_modify_live_authority':True}
 # Only the actual preflight function is evaluated; all non-pure imports are deny-by-default facades.
 for name in ('open_webui','open_webui.utils','open_webui.utils.agentic_contracts','open_webui.routers'):
  m=mod(name);m.__path__=[]
 def roster_read(project,db_path=None):
  counters['roster_reads']+=1;assert project==row['erp_project'];return roster
 def validate(value,*,registered_bindings=None):
  counters['validator_calls']+=1
  assert value is packet and registered_bindings==restricted
  return validator.validate_work_packet(value,registered_bindings=registered_bindings)
 mod('open_webui.utils.agentic_governance',{'roster':roster_read})
 mod('open_webui.utils.agentic_contracts.validator',{'validate_work_packet':validate})
 mod('open_webui.utils.abitdot_primary',{'primary_dot_for_owner':forbidden('primary_dot_lookup')})
 mod('open_webui.utils.abitdot_primary_relay',{'relay_account_for_owner':forbidden('account_lookup'),'validated_local_relay_url':lambda x:(_ for _ in ()).throw(AssertionError('NO_URL_SELECTION')),'ACCOUNT_ID':None})
 mod('open_webui.routers.chatgpt_relay',{'MESSAGE_URL':None,'MESSAGE_TOKEN_FILE':None,'_credentials':forbidden('credential_read')})
 mod('aiohttp',{'ClientSession':forbidden('http_client'),'ClientTimeout':lambda *a,**k:None})
 source=L/'utils/agentic_relay_scope.py'
 scope=loader.selected(source,{'_unavailable','require_relay_scope'},{'BLOCKER'},{'asyncio':asyncio,'CapabilityUnavailable':CapabilityUnavailable,**bridge})
 trace=[]
 def tracer(frame,event,arg):
  if frame.f_code.co_name=='require_relay_scope':
   if event=='exception':trace.append({'line':frame.f_lineno,'event':'exception','type':arg[0].__name__,'message_sha256':sha(str(arg[1]))})
   elif event=='return':trace.append({'line':frame.f_lineno,'event':'return','value_present':arg is not None})
  return tracer
 sys.settrace(tracer)
 try:asyncio.run(scope['require_relay_scope'](row,packet,registered['owner_id']))
 except CapabilityUnavailable as e:
  assert str(e)==row['reason'];out['actual_scope_preflight']={'denied':True,'error':str(e),'trace':trace}
 else:raise AssertionError('EXPECTED_PREFLIGHT_DENIAL')
 finally:sys.settrace(None)
 assert counters=={'roster_reads':1,'validator_calls':1,'primary_dot_lookup':0,'account_lookup':0,'credential_read':0,'http_client':0}
 out['effect_counters']=counters
 out['query_only']=db.execute('PRAGMA query_only').fetchone()[0];out['total_changes']=db.total_changes
 for p,h in pins.items():assert sha(Path(p).read_bytes())==h
 out['source_pins']=pins;out['source_pins_unchanged']=True
except Exception as e:out['reader_error']={'type':type(e).__name__,'message_sha256':sha(str(e))}
finally:
 sys.settrace(None)
 if db:db.close()
 if os.geteuid()!=0:os.seteuid(0);os.setegid(0)
 out['finished_utc']=datetime.now(timezone.utc).isoformat();print(json.dumps(out,sort_keys=True))

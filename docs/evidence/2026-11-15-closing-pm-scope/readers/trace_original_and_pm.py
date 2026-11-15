"""Bounded query-only correction of a diagnostic facade and PM preflight input proof."""
import ast,hashlib,importlib.util,json,os,sqlite3,sys,types,uuid
from pathlib import Path
from datetime import datetime,timezone
sys.dont_write_bytecode=True
R=Path('/srv/abit-dev-linux/abit-ai-platform')
D=Path('/srv/abit-dev-linux/abit-ai/data')
L=Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
O=L/'utils/agentic_ownership.py'
G=L/'utils/agentic_relay_scope.py'
P='agov-pm-review-9ac3cfd3cc1969734e889cec5e0e4c978dd19ab86738fa5c'
F='agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35'
RUN='adf93fbf-9737-5eb4-9f5d-21e02dc63014'
TASK='TASK-2026-06200'
sha=lambda x:hashlib.sha256(x.encode()if isinstance(x,str)else x).hexdigest()
out={'schema_version':1,'purpose':'corrected-pure-f33-recognizer-and-pm-input-readback','observed_utc':datetime.now(timezone.utc).isoformat(),'network_calls':0,'database_writes':0,'prior_harness_limitation':'read_published_qa.py SQLite facade omitted Row; its recognized:false is not runtime predicate evidence'}
pins={str(O):'e7b55be765892bd70cdd155d8ef72e7578bae8756ab5373008eb55c63b521f0d',str(G):'50fc65add1abe1e5b50e1ccf3ae2bcafa33ab8ff26a39fca168ff7301be40055',str(R/'scripts/prepare_delivery_history.py'):'776c4d110bda1e6800c0dfec1eb433b9f54a360c796acc51cefcaeeb7ce7caa9'}
for p,h in pins.items():assert sha(Path(p).read_bytes())==h
sp=importlib.util.spec_from_file_location('readonly_selector_corrected',R/'scripts/prepare_delivery_history.py');loader=importlib.util.module_from_spec(sp);sp.loader.exec_module(loader)
opened=[]
actual=sqlite3.connect
def ro(path,*args,**kw):
 assert kw.get('uri')is True and str(path)in {'file:'+str(D/n)+'?mode=ro'for n in ('agentic-governance.sqlite','webui.db','abitdot-primary.sqlite')}
 c=actual(path,*args,**kw);c.execute('PRAGMA query_only=ON');opened.append(c);return c
fn=next(n for n in ast.parse(O.read_text()).body if isinstance(n,ast.FunctionDef)and n.name=='_published_delivery')
returns={n.lineno for n in ast.walk(fn)if isinstance(n,ast.Return)}
trace_events=[]
def trace(frame,event,arg):
 if frame.f_code.co_name!='_published_delivery':return trace
 if event=='exception':
  trace_events.append({'event':'exception','line':frame.f_lineno,'type':arg[0].__name__,'message_sha256':sha(str(arg[1]))})
 elif event=='line'and frame.f_lineno in returns:
  trace_events.append({'event':'return_statement','line':frame.f_lineno})
 elif event=='return':trace_events.append({'event':'return','line':frame.f_lineno,'value':arg if type(arg)is bool else None})
 return trace
try:
 assert os.geteuid()==0
 os.setegid(988);os.seteuid(999)
 db=ro('file:'+str(D/'abitdot-primary.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('BEGIN')
 rows=db.execute('SELECT * FROM dot_primary_runs WHERE original_run_id=?',(RUN,)).fetchall();assert len(rows)==1
 p=dict(rows[0]);envelope=json.loads(p['envelope_json'])
 assert sha(p['envelope_json'])=='8486720f452aecc63b0bad28686592e4cc0ee6fbaac3b97647d63075076ee815'
 for label,facade in [('prior_incomplete_facade',types.SimpleNamespace(connect=ro,Error=sqlite3.Error)),('corrected_read_only_facade',types.SimpleNamespace(connect=ro,Error=sqlite3.Error,Row=sqlite3.Row))]:
  own=loader.selected(O,{'_digest','_published_delivery'},namespace={'Path':Path,'sqlite3':facade,'uuid':uuid})
  trace_events.clear();sys.settrace(trace)
  try:recognized=own['_published_delivery'](D,TASK,RUN,envelope,p['owner'],p['automation_id'])
  finally:sys.settrace(None)
  out[label]={'recognized':recognized,'trace':trace_events.copy()}
 out['provider']={'status':p['status'],'response_present':bool(p.get('response_text')),'receipt_present':bool(p.get('receipt')),'scope_receipt_present':bool(p.get('scope_receipt_json')),'envelope_sha256':sha(p['envelope_json']),'reason_sha256':sha(p.get('reason')or'')}
 g=ro('file:'+str(D/'agentic-governance.sqlite')+'?mode=ro',uri=True);g.row_factory=sqlite3.Row;g.execute('BEGIN')
 row=g.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(P,)).fetchone();assert row
 row=dict(row);packet=json.loads(row['work_packet'])
 declared=next(n.value for n in ast.parse(G.read_text()).body if isinstance(n,ast.Assign)and any(isinstance(x,ast.Name)and x.id=='BLOCKER'for x in n.targets))
 blocker=ast.literal_eval(declared)
 out['pm']={k:row[k]for k in ('invocation_id','state','dispatch_attempts','automation_run_id','submitted_at','created_at','updated_at')}
 out['pm'].update(reason_sha256=sha(row['reason']or''),reason_bytes=len((row['reason']or'').encode()),equals_exact_scope_unavailable_literal=row['reason']==blocker,reason=blocker if row['reason']==blocker else None,packet_digest=packet.get('content_sha256'),packet_sha256=sha(row['work_packet']),has_qa_review=bool(packet.get('qa_review')),tool_scopes=packet['tool_scopes'])
 roster=g.execute('SELECT * FROM governance_agent WHERE erp_project=? AND role=?',(row['erp_project'],row['target_role'])).fetchall();assert len(roster)==1
 registered=dict(roster[0])
 out['pm']['binding_registration_equal']=packet['binding']['agent_id']==registered['agent_id']and packet['binding']['erp_user']==registered['erp_user']and row['agent_id']==registered['agent_id']
 out['pm']['registered_owner_equal_qa']=registered['owner_id']==p['owner']
 # No app imports: exact pure contract module and its pure local dependencies only.
 contracts=L/'utils/agentic_contracts';sys.path.insert(0,str(contracts))
 spec=importlib.util.spec_from_file_location('readonly_pm_contract',contracts/'validator.py');validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
 errors=validator.validate_work_packet(packet,registered_bindings={row['target_role']:{'agent_id':registered['agent_id'],'erp_user':registered['erp_user']}})
 out['pm']['work_packet_errors']=[{'path':x.get('path'),'code':x.get('code'),'message_sha256':sha(str(x.get('message')))}if isinstance(x,dict)else{'type':type(x).__name__,'sha256':sha(str(x))}for x in errors]
 out['pm']['work_packet_error_count']=len(errors)
 f=g.execute('SELECT state,dispatch_attempts,automation_run_id FROM governance_invocation WHERE invocation_id=?',(F,)).fetchone()
 out['f33_invocation']=dict(f)
 out['query_only_connections']=[]
 for c in opened:
  try:out['query_only_connections'].append({'query_only':c.execute('PRAGMA query_only').fetchone()[0],'total_changes':c.total_changes})
  except sqlite3.ProgrammingError:out['query_only_connections'].append({'closed_by_pure_helper':True})
 for path,pin in pins.items():assert sha(Path(path).read_bytes())==pin
 out['source_pins']=pins;out['source_pins_unchanged']=True
except Exception as e:out['reader_error']={'type':type(e).__name__,'message_sha256':sha(str(e))}
finally:
 sys.settrace(None)
 for c in opened:
  try:c.close()
  except Exception:pass
 if os.geteuid()!=0:os.seteuid(0);os.setegid(0)
 out['finished_utc']=datetime.now(timezone.utc).isoformat();print(json.dumps(out,sort_keys=True))

"""Import the one-leaf candidate with exact live pure dependencies, using read-only original facts."""
import ast,asyncio,contextlib,hashlib,importlib.util,json,os,re,sqlite3,sys,time,types
from pathlib import Path
from datetime import datetime,timezone
sys.dont_write_bytecode=True
R=Path('/srv/abit-dev-linux/abit-ai-platform');D=Path('/srv/abit-dev-linux/abit-ai/data');L=Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
P='agov-pm-review-9ac3cfd3cc1969734e889cec5e0e4c978dd19ab86738fa5c'
sha=lambda b:hashlib.sha256(b.encode()if isinstance(b,str)else b).hexdigest()
pins={
 str(R/'backend/open_webui/utils/agentic_relay_scope.py'):'1907f2010399336b0ae9053f66e0ecb3f1d9a560805f7ae07d0a3cc7d83f698f',
 str(L/'utils/agentic_relay_scope.py'):'50fc65add1abe1e5b50e1ccf3ae2bcafa33ab8ff26a39fca168ff7301be40055',
 str(L/'utils/agentic_bridge.py'):'a0220b323e62fa27c14b00cdda61f5af4439805c0963c94b4b301091bd327f3b',
 str(L/'utils/agentic_tool_gateway.py'):'463b9550ccdc1ce8edc2b85759981c8ca316cb8dba1aab3a2acfc206d33ba065',
 str(L/'utils/agentic_governance.py'):'a86e819585256aafb904abb7eeb2263a252d0f872f05eb55078b2184f7e26787',
 str(L/'utils/agentic_contracts/validator.py'):'b124192191935e773f2e955aa64b3ed5d8f052f4466a4da417920605ad424af5',
 str(L/'utils/agentic_contracts/schemas/governance_v1.schema.json'):'5131fd838234f34173b4a7b30360d3c53d3d18f1fcdbc2afb079ba15db8d897c',
 str(L/'utils/agentic_contracts/developer_delivery.py'):'90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210',
 str(L/'utils/agentic_contracts/dependency_verification.py'):'a5ec8a03cad36200545b715908c4f623667677101e81f03cc38d06b054639d59',
 str(L/'utils/agentic_unsubmitted.py'):'066f8e861532cef27242a2d32b66f1bc3449aa0a6de608195373c0b3a9f4e488',
 str(R/'scripts/prepare_delivery_history.py'):'776c4d110bda1e6800c0dfec1eb433b9f54a360c796acc51cefcaeeb7ce7caa9',
}
out={'schema_version':1,'purpose':'candidate-live-import-and-saved-pm-packet-proof','observed_utc':datetime.now(timezone.utc).isoformat(),'network_calls':0,'database_writes':0,'model_submissions':0,'fresh_native_read_performed':False,'eligibility_is_not_authority_to_rearm':True}
for p,h in pins.items():assert sha(Path(p).read_bytes())==h
def load(name,path):
 sp=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(sp);sys.modules[name]=m;sp.loader.exec_module(m);return m
def mod(name,values=None):
 m=types.ModuleType(name)
 for k,v in (values or {}).items():setattr(m,k,v)
 sys.modules[name]=m;return m
for n in ('open_webui','open_webui.utils','open_webui.utils.agentic_contracts','open_webui.routers'):
 m=mod(n);m.__path__=[]
def deny_network(event,args):
 if event in {'socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr','subprocess.Popen','os.system','os.posix_spawn'}:raise AssertionError('NO_NETWORK_OR_CHILD_PROCESS')
sys.addaudithook(deny_network)
# Full files, not edited AST: their inspected top-level code only imports stdlib,
# defines helpers/classes and compiles constant regexes.
bridge=load('open_webui.utils.agentic_bridge',L/'utils/agentic_bridge.py')
gateway=load('open_webui.utils.agentic_tool_gateway',L/'utils/agentic_tool_gateway.py')
candidate=load('open_webui.utils.agentic_relay_scope',R/'backend/open_webui/utils/agentic_relay_scope.py')
selector=load('readonly_selector_candidate',R/'scripts/prepare_delivery_history.py')
contracts=L/'utils/agentic_contracts';sys.path.insert(0,str(contracts))
validator=load('open_webui.utils.agentic_contracts.validator',contracts/'validator.py')
db=None;gate=None
try:
 assert os.geteuid()==0;os.setegid(988);os.seteuid(999)
 def ro(name):
  p=D/name;assert p.is_file();c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
 db=ro('agentic-governance.sqlite')
 @contextlib.contextmanager
 def connection(_=None):yield db
 pure_roster=selector.selected(L/'utils/agentic_governance.py',{'roster'},{'PROJECT_ID'},{'connect':connection})
 mod('open_webui.utils.agentic_governance',{'roster':pure_roster['roster']})
 row=dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(P,)).fetchone());packet=json.loads(row['work_packet'])
 assert sha(row['work_packet'])=='1fd522a6dd7dbd489f8fe2ef6c4e7b92e0ab6419af553acc23e256ee6efb3f89'
 owner=next(x['owner_id']for x in pure_roster['roster'](row['erp_project'])if x['agent_id']==row['agent_id'])
 def dot(user):assert user==owner;return 'dot-readonly-protocol-fixture'
 async def account(user):assert user==owner;return 'a'*64
 mod('open_webui.utils.abitdot_primary',{'primary_dot_for_owner':dot,'dot_primary_key':lambda owner,automation,run:sha(json.dumps([owner,automation,run],separators=(',',':')))})
 mod('open_webui.utils.abitdot_primary_relay',{'relay_account_for_owner':account,'validated_local_relay_url':lambda value:value,'ACCOUNT_ID':re.compile('[0-9a-f]{64}')})
 mod('open_webui.routers.chatgpt_relay',{'MESSAGE_URL':'http://127.0.0.1:8810','MESSAGE_TOKEN_FILE':Path('/fixture-only-token'),'_credentials':lambda p:('fixture-only-token',None)})
 os.environ['ABIT_DOT_PRIMARY_RELAY_URL']='http://127.0.0.1:8810';os.environ.pop('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE',None)
 calls=[]
 class Response:
  status=200
  def __init__(self,body):
   self.content=self;scope=body['scope'];now=int(time.time())
   self.raw=json.dumps({'protocol_version':1,'scope':scope,'scope_digest':body['scope_digest'],'enforced':True,'read_only':True,'default_deny':True,'enforcement_mode':candidate.ENFORCEMENT_MODE,'enforcement_boundary':candidate.ENFORCEMENT_BOUNDARY,'excluded_surfaces':candidate.EXCLUDED_SURFACES,'gateway_session_id':'fixture-protocol-session','enforced_tool_scopes':scope['tool_scopes'],'external_write_allowlist':[],'proof_id':'fixture-proof','issued_at':now,'expires_at':now+30}).encode()
  async def read(self,limit):return self.raw[:limit]
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
 class Client:
  def __init__(self,**kw):assert kw['trust_env']is False
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  def post(self,url,**kw):
   assert url=='http://127.0.0.1:8810/dot/scope'and kw['allow_redirects']is False
   calls.append(kw['json']);return Response(kw['json'])
 mod('aiohttp',{'ClientSession':Client,'ClientTimeout':lambda **kw:kw})
 before=bridge.digest(packet)
 result=asyncio.run(candidate.require_relay_scope(row,packet,owner))
 assert len(calls)==1 and result['scope']['role']=='project_manager'and result['scope']['agent_id']==row['agent_id']and result['scope']['tool_scopes']==['read_project']and result['external_write_allowlist']==[]
 assert bridge.digest(packet)==before and result['scope']['work_packet_digest']==packet['content_sha256']
 out['candidate_actual_packet_positive']={'accepted_under_controlled_protocol_peer':True,'role':result['scope']['role'],'tool_scopes':result['scope']['tool_scopes'],'external_write_allowlist':result['external_write_allowlist'],'packet_unchanged':True,'real_network_calls':0,'controlled_protocol_calls':len(calls),'account_dot_and_http_are_fixtures_not_live_attestation':True}
 tables=('governance_local_result','governance_result','governance_gateway_session')
 counts={t:db.execute('SELECT COUNT(*) FROM '+t+' WHERE invocation_id=?',(P,)).fetchone()[0]for t in tables}
 project=db.execute('SELECT state FROM governance_project_control WHERE erp_project=?',(row['erp_project'],)).fetchone()
 task=db.execute('SELECT state FROM governance_task_control WHERE project=? AND task=?',(row['erp_project'],row['erp_task'])).fetchone()
 lease=db.execute('SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',(row['erp_project'],row['erp_task'])).fetchone()
 gate=ro('maintenance-admission/gate.sqlite')
 gaterow=gate.execute('SELECT state,epoch FROM gate').fetchone()
 active=gate.execute("SELECT COUNT(*) FROM lease WHERE state IN ('active','unresolved')").fetchone()[0]
 out['local_same_original_rearm_facts']={'state':row['state'],'submitted_at':row['submitted_at'],'automation_run_id':row['automation_run_id'],'dispatch_attempts':row['dispatch_attempts'],'packet_sha256':bridge.digest(packet),'stored_packet_hash_equal':bridge.digest(packet)==row['packet_hash'],'project_control':project[0]if project else None,'task_control':task[0]if task else 'default-enabled','task_lease_invocation':lease[0]if lease else None,'task_fence':lease[1]if lease else None,'stored_rows':counts,'maintenance_gate':dict(gaterow),'active_or_unresolved_admissions':active,'fresh_native_packet_identity_and_project_gate_not_observed':True,'no_rearm_or_model_dispatch_performed':True}
 out['query_only_connections']=[{'query_only':c.execute('PRAGMA query_only').fetchone()[0],'total_changes':c.total_changes}for c in (db,gate)]
 for p,h in pins.items():assert sha(Path(p).read_bytes())==h
 out['source_pins']=pins;out['source_pins_unchanged']=True
 out['imported_full_module_origins']={m.__name__:str(Path(m.__file__))for m in (bridge,gateway,candidate,validator)}
except Exception as e:out['reader_error']={'type':type(e).__name__,'message_sha256':sha(str(e))}
finally:
 for c in (gate,db):
  if c:c.close()
 if os.geteuid()!=0:os.seteuid(0);os.setegid(0)
 out['finished_utc']=datetime.now(timezone.utc).isoformat();print(json.dumps(out,sort_keys=True))

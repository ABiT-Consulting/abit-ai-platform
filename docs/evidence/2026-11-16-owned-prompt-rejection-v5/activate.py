"""Four existing relay leaves; preserve the known unresolved original without replay.

The operator's retained observation is NOT a rejection/completion receipt. Closing
its one HTTP transport may leave execution uncertain. Existing original fences
and all unrelated unknown journals remain intact. This controller leaves the
existing admission gate DRAINING for the following reviewed Core source reload.
"""
import argparse,hashlib,json,os,signal,sqlite3,stat,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/srv/abit-dev-linux/abit-ai');DATA=ROOT/'data'
RUNTIME=ROOT/'releases/20261031-live-0.11.4/runtime/open_webui'
SOURCE=Path(__file__).resolve().parent
LIVE=Path('/srv/abit-dev-linux/relay-runtime/service')
OUT=Path('/var/lib/abit-ai-erp-rollouts/owned-prompt-rejection-v5-20261116')
RUN='4f399cc6-f323-5d98-8155-c48c3ca692fe'
INV='agov-adoption-f804fe47d15981100bc37ded27e5d0dbba29880315bab1d1'
OLD={'aibit_relay.cjs':'9262cbb007439ad9924468526fd4226444b232416dd7a42f2a3f44e5e14a08bd',
 'aibit_dot_observation.cjs':'7de919af2dfe72a356b0b7f600175846d8de674e4236a352486705cc5d41cda9',
 'original_dot_capture.cjs':'232b84be06762a66eaf3abb68afd38d6345b0066b2dffd865ce5c1a626d6fe2d',
 'aibit_dot_feed.cjs':'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53'}
FIXED={'Main':1817640,'QA':1817642,'watchdog':1073226,'Chrome':1074156,
 'egress':1073233,'Xvfb':1073566,'tunnel':1075923,'MCP':2995736,'auth':675402}
can=lambda v:json.dumps(v,sort_keys=True,separators=(',',':')).encode()
sha=lambda b:hashlib.sha256(b).hexdigest()
def need(v,c):
 if not v:raise RuntimeError(c)
def read(p):
 st=p.lstat();need(stat.S_ISREG(st.st_mode)and st.st_nlink==1 and st.st_size<=2_000_000,'BOUNDED_REGULAR_FILE_REQUIRED');return p.read_bytes()
def save(n,v):
 b=v if isinstance(v,bytes)else can(v);fd=os.open(OUT/n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(b);f.flush();os.fsync(f.fileno())
def identity(pid):
 p=Path('/proc')/str(pid);s=p.joinpath('stat').read_text().split(') ',1)[1].split();need(s[0]!='Z','LIVE_PROCESS_REQUIRED')
 return{'pid':pid,'start':s[19],'uid':p.stat().st_uid,'cmd_sha256':sha(p.joinpath('cmdline').read_bytes()),'cgroup_sha256':sha(p.joinpath('cgroup').read_bytes())}
def rows(name,sql,args=()):
 with sqlite3.connect((DATA/name).as_uri()+'?mode=ro',uri=True,timeout=10)as c:
  c.execute('PRAGMA query_only=ON');c.row_factory=sqlite3.Row;return[dict(r)for r in c.execute(sql,args)]
def health():return json.load(urllib.request.urlopen('http://127.0.0.1:8810/health',timeout=8))
def validate_manifest(v):
 need(isinstance(v,dict)and set(v)==set(OLD),'ONLY_FOUR_EXISTING_RELAY_LEAVES_ALLOWED')
 for n,p in v.items():need(isinstance(p,str)and len(p)==64 and all(c in '0123456789abcdef'for c in p)and p!=OLD[n],'EXACT_TESTED_NEW_FILE_HASH_REQUIRED')
 return v
def original():
 pp=rows('abitdot-primary.sqlite','SELECT * FROM dot_primary_runs WHERE original_run_id=?',(RUN,));gg=rows('agentic-governance.sqlite','SELECT * FROM governance_invocation WHERE invocation_id=?',(INV,))
 need(len(pp)==len(gg)==1,'EXACT_ORIGINAL_REQUIRED');p,g=pp[0],gg[0]
 need(p['status']=='dispatching'and p['receipt']is None and p['response_text']is None and g['state']=='running'and g['automation_run_id']==RUN and g['erp_task']=='TASK-2026-06203'and g['erp_project']=='PROJ-0196'and g['target_role']=='project_manager'and g['dispatch_attempts']==1 and g['lease_fence']==1 and g['submitted_at']==1794824572,'EXACT_UNRESOLVED_ORIGINAL_CHANGED')
 need(rows('webui.db',"SELECT id FROM automation_run WHERE status='running'")==[{'id':RUN}]and rows('abitdot-primary.sqlite',"SELECT original_run_id FROM dot_primary_runs WHERE status='dispatching'")==[{'original_run_id':RUN}],'OTHER_ACTIVE_EXECUTION_PRESENT')
 return p,g
def preserved():
 qs=[('abitdot-primary.sqlite',"SELECT * FROM dot_primary_runs WHERE original_run_id!=? ORDER BY id",(RUN,)),
 ('agentic-governance.sqlite',"SELECT * FROM governance_invocation WHERE invocation_id!=? ORDER BY invocation_id",(INV,)),
 ('maintenance-admission/gate.sqlite',"SELECT * FROM lease WHERE state='unresolved' ORDER BY id",())]
 return{name:{'rows':len(v),'sha256':sha(can(v))}for name,q,a in qs for v in[rows(name,q,a)]}
def observation(raw,p,g):
 v=json.loads(raw);e=json.loads(p['envelope_json']);s=e['governance_scope']['scope'];r=v.get('scope_receipt')or{}
 need(v.get('ok')is True and v.get('read_only')is True and v.get('owner')==p['owner']and v.get('account_id')==s['account_id']and v.get('job_id')==s['job_id']and v.get('conversation_url')=='https://chatgpt.com/dots/'+p['dot_id']and v.get('observed',{}).get('generating')is False,'EXACT_OWNED_READONLY_OBSERVATION_REQUIRED')
 admitted=rows('agentic-governance.sqlite','SELECT admission_json FROM governance_gateway_session WHERE invocation_id=?',(INV,))
 need(len(admitted)==1 and json.loads(admitted[0]['admission_json'])==e['governance_scope'] and s['run_id']==RUN and s['invocation_id']==INV and s['work_packet_digest']==json.loads(g['work_packet'])['content_sha256'],'ORIGINAL_ADMISSION_BINDING_REQUIRED')
 return{'record_sha256':sha(raw),'run_id':RUN,'invocation_id':INV,'root_message_id':v.get('root_message_id'),'reply_message_id':v.get('reply_message_id'),'provider_outcome':'UNRESOLVED_NOT_REJECTION_OR_COMPLETION','model_sends':0}
def socket_pair(node,main):
 def owned(pid):
  result=set()
  for f in(Path('/proc')/str(pid)/'fd').iterdir():
   try:s=os.readlink(f)
   except FileNotFoundError:continue
   if s.startswith('socket:['):result.add(s[8:-1])
  return result
 ni,mi=owned(node),owned(main);server=[];client=[]
 for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
  c=line.split();local,remote,state,inode=c[1],c[2],c[3],c[9]
  if state!='01':continue
  if local=='0100007F:226A'and remote.startswith('0100007F:')and inode in ni:server.append((local,remote,inode))
  if remote=='0100007F:226A'and local.startswith('0100007F:')and inode in mi:client.append((local,remote,inode))
 need(len(server)==len(client)==1 and server[0][0]==client[0][1]and server[0][1]==client[0][0],'SOLE_OWNED_ORIGINAL_HTTP_SOCKET_REQUIRED')
 return{'server_inode':server[0][2],'client_inode':client[0][2],'client_port':int(client[0][0].split(':')[1],16),'server_port':8810}
def gate(epoch):
 command='owned-prompt-v5-known-unresolved-close-20261116';save('gate-close.intent.json',{'expected_epoch':epoch,'command_id':command,'attempts':1})
 r=subprocess.run(['sudo','-n','-u','#999','-g','#988','/usr/bin/python3','-I','-B',str(RUNTIME/'utils/agentic_maintenance.py'),'close','--data-dir',str(DATA),'--expected-epoch',str(epoch),'--command-id',command,'--actor','jimran@abitcons.com'],capture_output=True,timeout=20)
 save('gate-close.ack.json',{'returncode':r.returncode,'stdout':r.stdout.decode(),'stderr_sha256':sha(r.stderr)});need(r.returncode==0,'GATE_CLOSE_UNKNOWN_RECONCILE_ONLY');v=json.loads(r.stdout);need(v['state']=='draining'and v['epoch']==epoch+1,'EXACT_DRAINING_GATE_REQUIRED');return v

def backup():
 dest=OUT/'databases';dest.mkdir(mode=0o700);items=[]
 for p in sorted(list(DATA.glob('*.sqlite'))+[DATA/'webui.db',DATA/'maintenance-admission/gate.sqlite']):
  q=dest/p.name
  with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)as a,sqlite3.connect(q)as b:a.backup(b);need(b.execute('PRAGMA quick_check').fetchone()[0]=='ok','CONSISTENT_BACKUP_REQUIRED')
  q.chmod(0o600);items.append({'database':str(p),'sha256':sha(q.read_bytes())})
 save('database-backups.json',items)
def main(a):
 need(os.geteuid()==0,'ROOT_REQUIRED');need(sha(read(Path(__file__)))==a.controller_sha256,'REVIEWED_CONTROLLER_HASH_REQUIRED')
 raw=read(SOURCE/'activation-manifest.json');need(sha(raw)==a.manifest_sha256,'EXACT_MANIFEST_REQUIRED');m=validate_manifest(json.loads(raw))
 for n,pin in m.items():
  need(sha(read(LIVE/n))==OLD[n]and sha(read(SOURCE/n))==pin,'EXACT_OLD_AND_NEW_LEAF_SOURCE_REQUIRED');subprocess.run(['/usr/local/bin/node','--check',str(SOURCE/n)],capture_output=True,check=True,timeout=10)
 p,g=original();old=identity(a.node_pid);need(old['uid']==999 and old['start']==a.node_start,'EXACT_OLD_NODE_BIRTH_REQUIRED')
 fixed={k:identity(pid)for k,pid in FIXED.items()};need(fixed['Main']['start']=='74495928'and fixed['QA']['start']=='74495933','ORIGINAL_MAIN_QA_REQUIRED')
 proofraw=read(a.observation);st=a.observation.lstat();need(st.st_uid==st.st_gid==0 and stat.S_IMODE(st.st_mode)==0o600 and sha(proofraw)==a.observation_sha256 and 0<=time.time()-st.st_mtime<=900,'ROOT_PRIVATE_RETAINED_OBSERVATION_REQUIRED');proof=observation(proofraw,p,g)
 h=health();need(h.get('authenticated')is True and h.get('target_ready')is True and h.get('active_relay_turns')==1,'SOLE_KNOWN_ORIGINAL_HANDLER_REQUIRED');pair=socket_pair(old['pid'],fixed['Main']['pid']);kept=preserved()
 expected=rows('maintenance-admission/gate.sqlite','SELECT state,epoch FROM gate');need(expected==[{'state':'open','epoch':a.expected_epoch}],'EXACT_EXISTING_OPEN_GATE_REQUIRED')
 if a.preflight_only:print(json.dumps({'state':'NODE_V5_READONLY_PREFLIGHT','old_Node':old,'held_original':proof,'socket':pair,'preserved':kept,'source_writes':0,'process_actions':0,'model_sends':0}));return
 need(not OUT.exists(),'EXISTING_OPERATION_OBSERVE_ONLY');OUT.mkdir(mode=0o700);save('intent.json',{'old_Node':old,'protected':fixed,'held_original':proof,'socket':pair,'preserved':kept,'manifest':m,'controller_sha256':a.controller_sha256,'model_sends':0,'Core_actions':0,'maximum_Node_TERM_calls':1,'maximum_owned_HTTP_socket_closes':1,'database_restores':0})
 save('retained-observation.private.json',proofraw);closed=gate(a.expected_epoch);backup();save('original-provider.private.json',p);save('original-Core.private.json',g)
 need(original()[0]==p and preserved()==kept and socket_pair(old['pid'],fixed['Main']['pid'])==pair and identity(old['pid'])==old and {k:identity(v['pid'])for k,v in fixed.items()}==fixed,'ORIGINAL_OR_PROTECTED_STATE_CHANGED_BEFORE_INSTALL')
 for n,pin in m.items():
  path=LIVE/n;st=path.lstat();save(n+'.before',read(path));temp=path.with_name('.'+n+'.owned-prompt-v5-new');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb')as f:f.write(read(SOURCE/n));f.flush();os.fchown(f.fileno(),st.st_uid,st.st_gid);os.fchmod(f.fileno(),stat.S_IMODE(st.st_mode));os.fsync(f.fileno())
  need(sha(read(path))==OLD[n],'LIVE_LEAF_CAS_CHANGED');os.replace(temp,path);need(sha(read(path))==pin,'LEAF_INSTALL_READBACK_FAILED')
 need(identity(old['pid'])==old and socket_pair(old['pid'],fixed['Main']['pid'])==pair,'OLD_NODE_SOCKET_CHANGED_BEFORE_TERM')
 pidfd=os.pidfd_open(old['pid']);need(identity(old['pid'])==old,'OLD_NODE_BIRTH_CHANGED');save('Node-TERM.intent.json',{'old':old,'signal':'SIGTERM','maximum_calls':1});signal.pidfd_send_signal(pidfd,signal.SIGTERM);os.close(pidfd)
 # Existing Node SIGTERM drains its HTTP server. Close only this one transport;
 # execution outcome remains unresolved and the original is never retried here.
 need(identity(old['pid'])==old and socket_pair(old['pid'],fixed['Main']['pid'])==pair,'EXACT_ORIGINAL_SOCKET_CHANGED_NO_CLOSE')
 save('original-HTTP-close.intent.json',{'socket':pair,'Node':old,'Main':fixed['Main'],'maximum_calls':1,'purpose':'release only the known held original response transport; no cancellation or new Send'})
 result=subprocess.run(['/usr/bin/ss','-K','state','established','(','src','127.0.0.1','and','sport','=',':8810','and','dst','127.0.0.1','and','dport','=',':'+str(pair['client_port']),')'],capture_output=True,timeout=10)
 save('original-HTTP-close.ack.json',{'returncode':result.returncode,'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr)});need(result.returncode==0,'ORIGINAL_TRANSPORT_CLOSE_UNKNOWN_NO_REPEAT')
 deadline=time.monotonic()+45
 while Path('/proc',str(old['pid'])).exists()and time.monotonic()<deadline:time.sleep(.2)
 need(not Path('/proc',str(old['pid'])).exists(),'OLD_NODE_DID_NOT_DRAIN_NO_KILL_OR_REPEAT');new=None;deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  candidates=[]
  for q in Path('/proc').iterdir():
   if not q.name.isdecimal():continue
   try:
    v=identity(int(q.name))
    if v['cmd_sha256']==old['cmd_sha256']and v['uid']==999:candidates.append(v)
   except(FileNotFoundError,PermissionError,ProcessLookupError):pass
  if len(candidates)==1:
   try:
    h=health()
    if h.get('authenticated')is True and h.get('target_ready')is True and h.get('active_relay_turns')==0:new=candidates[0];break
   except Exception:pass
  time.sleep(.5)
 need(new and preserved()==kept and {k:identity(v['pid'])for k,v in fixed.items()}==fixed and all(sha(read(LIVE/n))==pin for n,pin in m.items()),'NEW_NODE_OR_PROTECTED_READBACK_FAILED')
 gp=rows('agentic-governance.sqlite','SELECT automation_run_id,dispatch_attempts,lease_fence,submitted_at,state FROM governance_invocation WHERE invocation_id=?',(INV,))[0]
 pp=rows('abitdot-primary.sqlite','SELECT status,receipt,response_text,envelope_json FROM dot_primary_runs WHERE original_run_id=?',(RUN,))[0]
 need(gp['automation_run_id']==RUN and gp['dispatch_attempts']==1 and gp['lease_fence']==1 and gp['submitted_at']==g['submitted_at']and gp['state']in{'running','uncertain'}and pp['status']in{'dispatching','unknown'}and pp['receipt']is None and pp['response_text']is None and pp['envelope_json']==p['envelope_json'],'ORIGINAL_FENCE_CHANGED_NO_REPLAY')
 receipt={'state':'NODE_V5_SOURCE_LIVE_ORIGINAL_HELD','Node':new,'Node_TERM_calls':1,'owned_HTTP_socket_closes':1,'Core_actions':0,'QA_actions':0,'forced_kills':0,'model_sends':0,'ERP_writes':0,'database_restores':0,'gate_state':'draining','gate_epoch':closed['epoch'],'unknown_originals_preserved':True,'original_run':RUN,'original_invocation':INV,'original_status':{'Core':gp['state'],'provider':pp['status']},'source_hashes':m}
 save('completed-receipt.json',receipt);print(json.dumps(receipt),flush=True)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--controller-sha256',required=True);parser.add_argument('--manifest-sha256',required=True);parser.add_argument('--observation',type=Path,required=True);parser.add_argument('--observation-sha256',required=True);parser.add_argument('--node-pid',type=int,required=True);parser.add_argument('--node-start',required=True);parser.add_argument('--expected-epoch',type=int,required=True);parser.add_argument('--preflight-only',action='store_true');main(parser.parse_args())

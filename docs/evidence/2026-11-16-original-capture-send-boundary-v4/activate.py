"""Replace only Node after verifying the sole original turn is terminal and idle.

The original response handler may still be awaiting its Send acknowledgement.
Its genuine read-only reply is archived; no original run is reset or resent.
"""
import fcntl,hashlib,json,os,signal,sqlite3,stat,subprocess,sys,time,types,urllib.request
from pathlib import Path
ROOT=Path('/srv/abit-dev-linux/abit-ai');DATA=ROOT/'data'
RUNTIME=ROOT/'releases/20261031-live-0.11.4/runtime'
SOURCE=Path(__file__).resolve().parent
LIVE=Path('/srv/abit-dev-linux/relay-runtime/service/aibit_relay.cjs')
OUT=Path('/var/lib/abit-ai-erp-rollouts/original-capture-send-boundary-v4-20261116')
RUN='f6c5dbc0-af9d-508b-8671-1b8f3db9a4da'
INV='agov-parent-41dbde40fddbf343015d396438629d4092512e9fbbd11866'
OLD='91f7bef336e93c8f431dd7711c59cccbda40b61e63cab5b69550ab78fd3f4d55'
NEW='9262cbb007439ad9924468526fd4226444b232416dd7a42f2a3f44e5e14a08bd'
can=lambda v:json.dumps(v,sort_keys=True,separators=(',',':')).encode()
sha=lambda b:hashlib.sha256(b).hexdigest()
def need(v,c):
 if not v:raise RuntimeError(c)
def save(n,v):
 b=v if isinstance(v,bytes)else can(v);fd=os.open(OUT/n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(b);f.flush();os.fsync(f.fileno())
def identity(pid):
 p=Path('/proc')/str(pid);return {'pid':pid,'start':p.joinpath('stat').read_text().split(') ',1)[1].split()[19],'cmd_sha256':sha(p.joinpath('cmdline').read_bytes()),'cgroup_sha256':sha(p.joinpath('cgroup').read_bytes())}
def health():return json.load(urllib.request.urlopen('http://127.0.0.1:8810/health',timeout=8))
def rows(name,sql,args=()):
 with sqlite3.connect((DATA/name).as_uri()+'?mode=ro',uri=True)as c:
  c.row_factory=sqlite3.Row;return [dict(r)for r in c.execute(sql,args)]
def gate(action,epoch):
 p=RUNTIME/'open_webui/utils/agentic_maintenance.py';command='capture-v4-original-terminal-'+action+'-20261116';save('gate-'+action+'.intent.json',{'expected_epoch':epoch,'command_id':command,'attempts':1})
 r=subprocess.run(['sudo','-n','-u','#999','-g','#988','/usr/bin/python3','-I','-B',str(p),action,'--data-dir',str(DATA),'--expected-epoch',str(epoch),'--command-id',command,'--actor','jimran@abitcons.com'],capture_output=True,timeout=20);save('gate-'+action+'.ack.json',{'returncode':r.returncode,'stdout':r.stdout.decode(),'stderr_sha256':sha(r.stderr)});need(r.returncode==0,'GATE_OUTCOME_RECONCILE_ONLY');return json.loads(r.stdout)
def original():
 p=rows('abitdot-primary.sqlite','SELECT * FROM dot_primary_runs WHERE original_run_id=?',(RUN,));g=rows('agentic-governance.sqlite','SELECT * FROM governance_invocation WHERE invocation_id=?',(INV,));need(len(p)==len(g)==1,'EXACT_ORIGINAL_REQUIRED');p,g=p[0],g[0]
 need(p['status']=='dispatching'and p['receipt']is None and p['response_text']is None and g['state']=='running'and g['automation_run_id']==RUN and g['lease_fence']==4 and g['dispatch_attempts']==1 and g['submitted_at']==1794787971,'EXACT_RUNNING_ORIGINAL_CHANGED')
 need(rows('webui.db',"SELECT id FROM automation_run WHERE status='running'")==[{'id':RUN}],'OTHER_RUNNING_AUTOMATION_PRESENT')
 need(rows('abitdot-primary.sqlite',"SELECT original_run_id FROM dot_primary_runs WHERE status='dispatching'")==[{'original_run_id':RUN}],'OTHER_PROVIDER_DISPATCH_PRESENT');return p,g
def backup():
 directory=OUT/'databases';directory.mkdir(mode=0o700);items=[]
 for path in sorted(list(DATA.glob('*.sqlite'))+[DATA/'webui.db',DATA/'maintenance-admission/gate.sqlite']):
  dest=directory/path.name
  with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)as source,sqlite3.connect(dest)as target:
   source.backup(target);need(target.execute('PRAGMA quick_check').fetchone()[0]=='ok','CONSISTENT_DATABASE_BACKUP_REQUIRED')
  dest.chmod(0o600);items.append({'database':str(path),'backup_sha256':sha(dest.read_bytes())})
 save('database-backups.json',items)
def observe(p):
 for name,path in [('open_webui',RUNTIME/'open_webui'),('open_webui.utils',RUNTIME/'open_webui/utils')]:
  m=types.ModuleType(name);m.__path__=[str(path)];sys.modules[name]=m
 from open_webui.utils.abitdot_primary_relay import verified_dot_payload,receipt_from_relay
 e=json.loads(p['envelope_json']);a=rows('aibit-chatgpt-relay.sqlite','SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 2',(p['owner'],));need(a and all(x['account']==a[0]['account']for x in a),'CURRENT_OWNER_ACCOUNT_CHANGED');account=a[0]['account'];b=verified_dot_payload(e,account,admission=False)
 save('observe-once.intent.json',{'run_id':RUN,'read_only':True,'attempts':1,'model_sends':0,'envelope_sha256':sha(p['envelope_json'].encode())});token=Path('/srv/abit-dev-linux/relay-runtime/secrets/relay.token').read_text().strip();req=urllib.request.Request('http://127.0.0.1:8810/dot/observe',data=json.dumps(b).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=40)as response:need(response.status==200,'ORIGINAL_READ_UNAVAILABLE');raw=response.read(300001)
 need(len(raw)<=300000,'BOUNDED_ORIGINAL_REPLY_REQUIRED');v=json.loads(raw);receipt=receipt_from_relay(e,v,account);need(v.get('read_only')is True and v.get('dot_handoff')is True and v.get('observed',{}).get('generating')is False and receipt['status']=='accepted'and v.get('root_message_id')and v.get('reply_message_id'),'ORIGINAL_NOT_VERIFIED_TERMINAL_AND_IDLE')
 lines=[s[len('AGOV RESULT:'):].strip()for s in v['response'].splitlines()if s.startswith('AGOV RESULT:')];need(len(lines)==1,'ONE_ORIGINAL_RESULT_REQUIRED');r=json.loads(lines[0]);scope=e['governance_scope']['scope'];need(r['invocation_id']==INV and r['project_id']=='PROJ-0198'and r['task_id']=='TASK-2026-06199'and r['work_packet_digest']==scope['work_packet_digest'],'ORIGINAL_RESULT_IDENTITY_CHANGED');save('terminal-original.private.json',raw);save('verified-observation-receipt.private.json',receipt);return {'run_id':RUN,'response_sha256':sha(v['response'].encode()),'response_record_sha256':sha(raw),'root_message_id':v['root_message_id'],'reply_message_id':v['reply_message_id'],'read_only':True,'verified_terminal':True,'idle':True,'observed_at':int(time.time())}
def main():
 need(os.geteuid()==0 and not OUT.exists(),'ROOT_NEW_OPERATION_REQUIRED');old=identity(1134768);need(old['start']=='74016893','EXACT_OLD_NODE_CHANGED');fixed={k:identity(v)for k,v in {'Main':4132708,'QA':314775,'watchdog':2996798,'Chrome':2998209,'egress':2997073,'Xvfb':2997675,'tunnel':3002922,'MCP':2995736,'auth':675402}.items()};need(sha(LIVE.read_bytes())==OLD and sha((SOURCE/'aibit_relay.cjs').read_bytes())==NEW,'EXACT_NODE_SOURCES_REQUIRED');subprocess.run(['/usr/local/bin/node','--check',str(SOURCE/'aibit_relay.cjs')],check=True,capture_output=True,timeout=10)
 p,g=original();need(health()['active_relay_turns']==1,'SOLE_ORIGINAL_HANDLER_REQUIRED');OUT.mkdir(mode=0o700);save('intent.json',{'old_Node':old,'protected':fixed,'original_run':RUN,'original_invocation':INV,'model_sends':0,'Main_actions':0,'QA_actions':0,'unknown_admissions_preserved':True});proof=observe(p);save('terminal-original-proof.json',proof)
 closed=gate('close',44);need(closed['state']=='draining'and closed['epoch']==45,'EXACT_DRAINING45_REQUIRED');current_p,current_g=original();need(current_p==p,'ORIGINAL_PROVIDER_CHANGED_BEFORE_RESTART');backup();need(health()['active_relay_turns']==1 and time.time()-proof['observed_at']<=60,'SOLE_ORIGINAL_HANDLER_OR_FRESH_IDLE_PROOF_CHANGED')
 save('old-provider.private.json',p);save('old-Core.private.json',g);save('Node.before',LIVE.read_bytes());st=LIVE.lstat();tmp=LIVE.with_name('.'+LIVE.name+'.capture-v4-new');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write((SOURCE/'aibit_relay.cjs').read_bytes());f.flush();os.fchown(f.fileno(),st.st_uid,st.st_gid);os.fchmod(f.fileno(),stat.S_IMODE(st.st_mode));os.fsync(f.fileno())
 need(sha(LIVE.read_bytes())==OLD,'LIVE_NODE_CAS_CHANGED');os.replace(tmp,LIVE);need(sha(LIVE.read_bytes())==NEW and identity(1134768)==old,'OLD_NODE_IDENTITY_OR_NEW_SOURCE_CHANGED');save('Node-TERM.intent.json',{'old':old,'signal':'SIGTERM','attempts':1,'terminal_original_proof':proof,'forced_kills':0});os.kill(1134768,signal.SIGTERM)
 for _ in range(100):
  if not Path('/proc/1134768').exists():break
  time.sleep(.1)
 need(not Path('/proc/1134768').exists(),'OLD_NODE_NOT_EXITED_NO_KILL_OR_REPEAT');new=None
 for _ in range(45):
  candidates=[]
  for q in Path('/proc').iterdir():
   if not q.name.isdecimal():continue
   try:
    if sha(q.joinpath('cmdline').read_bytes())==old['cmd_sha256']:candidates.append(identity(int(q.name)))
   except(FileNotFoundError,PermissionError,ProcessLookupError):pass
  if len(candidates)==1:
   try:
    h=health()
    if h['authenticated']is True and h['target_ready']is True and h['active_relay_turns']==0:new=candidates[0];break
   except Exception:pass
  time.sleep(1)
 need(new is not None,'NEW_NODE_NOT_VERIFIED_OBSERVE_ONLY');need({k:identity(v['pid'])for k,v in fixed.items()}==fixed,'PROTECTED_PROCESS_CHANGED')
 for _ in range(60):
  gp=rows('agentic-governance.sqlite','SELECT state,automation_run_id,submitted_at,lease_fence,dispatch_attempts FROM governance_invocation WHERE invocation_id=?',(INV,))[0];pp=rows('abitdot-primary.sqlite','SELECT status,receipt,response_text,envelope_json FROM dot_primary_runs WHERE original_run_id=?',(RUN,))[0]
  if gp['state']=='uncertain'and pp['status']=='unknown':break
  time.sleep(1)
 need(gp['state']=='uncertain'and gp['automation_run_id']==RUN and gp['lease_fence']==4 and gp['dispatch_attempts']==1 and gp['submitted_at']==g['submitted_at']and pp['status']=='unknown'and pp['receipt']is None and pp['response_text']is None and pp['envelope_json']==p['envelope_json'],'ORIGINAL_HANDLER_NOT_FENCED_OBSERVE_ONLY');save('original-fenced-readback.json',{'Core':gp,'provider_status':pp['status'],'provider_envelope_preserved':True,'raw_reply_retained':proof})
 lock=os.open(DATA/'erp-dot-dispatch.lock',os.O_RDWR|os.O_NOFOLLOW);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  opened=gate('open',45);need(opened['state']=='open'and opened['epoch']==46,'NORMAL_OPEN46_UNCONFIRMED');v={'state':'NODE_CAPTURE_SEND_BOUNDARY_V4_LIVE','Node':new,'old_Node_TERM_calls':1,'forced_kills':0,'Main_actions':0,'QA_actions':0,'protected_processes_unchanged':True,'original_run_unchanged':RUN,'original_reply_verified_and_retained':proof,'provider_unknown_preserved_for_normal_receipt_recovery':True,'model_sends':0,'Core_gate':'OPEN46'};save('completed-receipt.json',v);print(json.dumps(v),flush=True)
 finally:os.close(lock)
if __name__=='__main__':main()

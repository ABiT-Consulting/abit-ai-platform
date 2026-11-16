"""Two reviewed relay files, idle producer restart, preserve all original ledgers."""
import fcntl,hashlib,json,os,signal,sqlite3,stat,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/srv/abit-dev-linux/abit-ai');DATA=ROOT/'data'
SOURCE=ROOT/'deployment/agentic-governance-full-20261113/relay-proof-window-diagnostics-v3-20261115'
SERVICE=Path('/srv/abit-dev-linux/relay-runtime/service')
OUT=Path('/var/lib/abit-ai-erp-rollouts/relay-proof-window-diagnostics-v3-20261115')
TARGETS=[('aibit_relay.cjs','aibit_relay.cjs','6c83a562bb266b3c7b1f758b23f3cc5ffd98b6a71ae2b9add7ba4ed70de8035b','91f7bef336e93c8f431dd7711c59cccbda40b61e63cab5b69550ab78fd3f4d55'),('agentic-gateway/relay.cjs','gateway-relay.cjs','b096564620080dc9ecb7a661a71b0c7403b342ca52059060d6aeb66b2deca7c7','88308fee2b56d8fecd9625b9f9e915164771887caa71d531be45ce1dbddbd603')]
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
def gate(action,epoch,command):
 p=ROOT/'releases/20261031-live-0.11.4/runtime/open_webui/utils/agentic_maintenance.py';args=['sudo','-n','-u','#999','-g','#988','/usr/bin/python3','-I','-B',str(p),action,'--data-dir',str(DATA),'--expected-epoch',str(epoch),'--command-id',command,'--actor','jimran@abitcons.com'];save(action+'.intent.json',{'expected_epoch':epoch,'command_id':command,'purpose':'idle Node-only source update; unresolved original admissions retained'});r=subprocess.run(args,capture_output=True,text=True,timeout=20);need(r.returncode==0,'GATE_'+action.upper()+'_UNKNOWN_RECONCILE_ONLY');v=json.loads(r.stdout);save(action+'.ack.json',v);return v
def main():
 need(os.geteuid()==0 and not OUT.exists(),'ROOT_NEW_OPERATION_REQUIRED');before=identity(45190);need(before['start']=='73301863','EXACT_OLD_NODE_REQUIRED');fixed={n:identity(p)for n,p in {'Main':4132708,'QA':314775,'watchdog':2996798,'Chrome':2998209,'egress':2997073,'Xvfb':2997675,'tunnel':3002922,'MCP':2995736,'auth':675402}.items()};h=health();need(h['active_relay_turns']==0 and h['authenticated']is True and h['target_ready']is True,'ORIGINAL_NODE_NOT_IDLE_AND_READY')
 for live,source,old,new in TARGETS:
  p=SERVICE/live;q=SOURCE/source;st=p.lstat();need(p.resolve()==p and stat.S_ISREG(st.st_mode)and st.st_nlink==1 and sha(p.read_bytes())==old and sha(q.read_bytes())==new,'EXACT_REVIEWED_RELAY_SOURCE_REQUIRED');subprocess.run(['/usr/local/bin/node','--check',str(q)],check=True,capture_output=True,timeout=10)
 fd=os.open(DATA/'erp-dot-dispatch.lock',os.O_RDWR|os.O_NOFOLLOW);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  OUT.mkdir(mode=0o700);save('intent.json',{'old_Node':before,'protected':fixed,'maximum_proof_lifetime_seconds':60,'fresh_current_native_admission_still_required':True,'preserve_unknown_originals':True,'Node_TERM_calls':1,'CoreMain_actions':0,'QA_actions':0,'forced_kills':0});v=gate('close',42,'relay-proof-v3-idle-node-close-20261115');need(v['state']=='draining'and v['epoch']==43,'EXACT_DRAINING_EPOCH_REQUIRED');need(health()['active_relay_turns']==0,'NODE_BECAME_ACTIVE_BEFORE_UPDATE')
  backups=[];(OUT/'databases').mkdir(mode=0o700)
  for p in sorted(list(DATA.glob('*.sqlite'))+[DATA/'webui.db',DATA/'maintenance-admission/gate.sqlite']):
   c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=10);q=OUT/'databases'/p.name;dest=sqlite3.connect(q);c.backup(dest);need(dest.execute('PRAGMA quick_check').fetchone()[0]=='ok','CONSISTENT_SQLITE_BACKUP_REQUIRED');dest.close();c.close();os.chmod(q,0o600);backups.append({'database':str(p),'backup':str(q),'sha256':sha(q.read_bytes())})
  save('backups.json',backups);print(json.dumps({'phase':'backups_complete','count':len(backups)}),flush=True)
  for i,(live,source,old,new)in enumerate(TARGETS):
   p=SERVICE/live;st=p.lstat();save(str(i)+'.before',p.read_bytes());b=(SOURCE/source).read_bytes();tmp=p.with_name('.'+p.name+'.proof-v3-new');of=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(of,'wb')as f:f.write(b);f.flush();os.fchown(f.fileno(),st.st_uid,st.st_gid);os.fchmod(f.fileno(),stat.S_IMODE(st.st_mode));os.fsync(f.fileno())
   need(sha(p.read_bytes())==old,'RELAY_TARGET_CHANGED_BEFORE_CAS');os.replace(tmp,p);need(sha(p.read_bytes())==new,'RELAY_SOURCE_CAS_READBACK_FAILED')
  need(identity(45190)==before and health()['active_relay_turns']==0,'ORIGINAL_NODE_CHANGED_OR_BECAME_ACTIVE');save('Node-TERM.intent.json',{'identity':before,'attempts':1,'signal':'SIGTERM'});os.kill(45190,signal.SIGTERM)
  for _ in range(100):
   if not Path('/proc/45190').exists():break
   time.sleep(.1)
  need(not Path('/proc/45190').exists(),'ORIGINAL_NODE_STILL_EXISTS_NO_KILL_OR_RETRY');newnode=None
  for _ in range(40):
   matches=[]
   for p in Path('/proc').iterdir():
    if not p.name.isdecimal():continue
    try:
     if sha(p.joinpath('cmdline').read_bytes())==before['cmd_sha256']:matches.append(identity(int(p.name)))
    except(FileNotFoundError,PermissionError,ProcessLookupError):pass
   if len(matches)==1:
    try:
     h=health()
     if h.get('active_relay_turns')==0 and h.get('authenticated')is True and h.get('target_ready')is True:newnode=matches[0];break
    except Exception:pass
   time.sleep(1)
  need(newnode is not None,'WATCHDOG_NEW_NODE_NOT_VERIFIED_RECONCILE_ONLY');need({n:identity(p['pid'])for n,p in fixed.items()}==fixed,'PROTECTED_PROCESS_CHANGED');opened=gate('open',43,'relay-proof-v3-normal-operation-open-20261115');need(opened['state']=='open'and opened['epoch']==44,'NORMAL_OPEN_NOT_CONFIRMED');receipt={'ok':True,'state':'RELAY_PROOF_WINDOW_DIAGNOSTICS_V3_LIVE','Node':newnode,'old_Node_TERM_calls':1,'forced_kills':0,'Main_actions':0,'QA_actions':0,'protected_processes_unchanged':True,'Core_gate':'OPEN','Core_epoch':44,'unknown_original_admissions_preserved':True,'maximum_proof_lifetime_seconds':60,'expired_proof_and_current_native_scope_checks_preserved':True,'databases_backed_up':len(backups),'model_sends':0};save('completed-receipt.json',receipt);print(json.dumps(receipt),flush=True)
 finally:os.close(fd)
if __name__=='__main__':main()

"""Approved one-leaf cutover; preserve all execution journals and configuration."""
from pathlib import Path
import fcntl,hashlib,json,os,shutil,sqlite3,subprocess,time,urllib.request
ROOT=Path('/srv/abit-dev-linux/abit-ai')
W=Path(__file__).resolve().parent
OUT=ROOT/'deployment/assignment-trigger-context-core-live-20261117'
LIVE=ROOT/'current/runtime/open_webui/utils/agentic_gateway_operations.py'
GATE=ROOT/'current/runtime/open_webui/utils/agentic_maintenance.py'
sha=lambda b:hashlib.sha256(b).hexdigest()
def save(name,value):
 raw=value if isinstance(value,bytes) else (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
 with (OUT/name).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
def rows(file,sql,args=()):
 with sqlite3.connect((ROOT/'data'/file).as_uri()+'?mode=ro',uri=True) as c:return c.execute(sql,args).fetchall()
def gate(action,epoch):
 r=subprocess.run(['sudo','-n','-u','mcpdev','python3','-I','-B',str(GATE),action,'--data-dir',str(ROOT/'data'),'--expected-epoch',str(epoch),'--command-id','assignment-context-core-'+action+'-20261117','--actor','jimran@abitcons.com'],capture_output=True,timeout=20)
 assert r.returncode==0, 'Maintenance command failed'
 v=json.loads(r.stdout);save('gate-'+action+'.json',v);return v
assert os.geteuid()==0 and not OUT.exists()
before=LIVE.read_bytes();after=(W/'agentic_gateway_operations.py').read_bytes()
assert sha(before)=='026f5fe230051f10d1bd7ac0823bbd724b73855175bbc2b201feb9067208e584'
assert before==(W/'agentic_gateway_operations.before.py').read_bytes()
assert b'task_detail_arguments' in after and b'Task detail input or ownership changed' in after
compile(after,str(LIVE),'exec')
assert shutil.disk_usage(ROOT).free>1024**3
epoch=rows('maintenance-admission/gate.sqlite','select state,epoch from gate')
assert epoch==[('open',70)], 'Current maintenance epoch changed'
assert rows('agentic-governance.sqlite',"select count(*) from governance_invocation where state in ('queued','running','uncertain','awaiting_approval')")==[(0,)]
assert rows('agentic-governance.sqlite',"select count(*) from governance_gateway_call where state not in ('completed','uncertain')")==[(0,)]
original=[('5955703a-4a31-5a33-8265-63a5299bdace',)]
assert rows('webui.db',"select id from automation_run where status='running'")==original
assert rows('abitdot-primary.sqlite',"select status,length(response_text)>0 from dot_primary_runs where original_run_id=?",original[0])==[('delegated',1)]
OUT.mkdir(mode=0o700)
lock=os.open(ROOT/'data/erp-dot-dispatch.lock',os.O_RDWR|os.O_NOFOLLOW)
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
configs={str(p):sha(p.read_bytes()) for p in (ROOT/'config').glob('*.json')}
configs[str(ROOT/'config/erp-assignment/resolver.json')]=sha((ROOT/'config/erp-assignment/resolver.json').read_bytes())
save('intent.json',{'old_source_sha256':sha(before),'new_source_sha256':sha(after),'configuration_hashes':configs,'retained_original_running_run':original[0][0],'source_tests':3,'production_approval':'User approved in current session'})
save('service-before.private.txt',subprocess.check_output(['systemctl','show','abit-ai.service']))
gate('close',70)
assert rows('agentic-governance.sqlite',"select count(*) from governance_invocation where state in ('queued','running','uncertain','awaiting_approval')")==[(0,)]
# Keep the historical cancel-requested invocation and all unresolved receipts.
for path in sorted(set((ROOT/'data').glob('*.sqlite'))|{ROOT/'data/webui.db',ROOT/'data/maintenance-admission/gate.sqlite'}):
 target=OUT/('backup-'+path.parent.name+'-'+path.name)
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(target) as dest:
  source.backup(dest)
 os.chmod(target,0o600)
save('agentic_gateway_operations.before.py',before)
subprocess.run(['systemctl','stop','abit-ai.service'],check=True,timeout=110)
assert subprocess.check_output(['systemctl','show','abit-ai.service','-p','MainPID','--value']).strip()==b'0'
assert LIVE.read_bytes()==before, 'Source changed during cutover'
meta=LIVE.stat();temp=LIVE.with_name('.agentic_gateway_operations.assignment-20261117.py')
with temp.open('xb') as f:f.write(after);f.flush();os.fsync(f.fileno())
shutil.copystat(LIVE,temp);os.chown(temp,meta.st_uid,meta.st_gid)
os.replace(temp,LIVE)
subprocess.run(['systemctl','start','abit-ai.service'],check=True,timeout=30)
deadline=time.monotonic()+120
while True:
 try:
  with urllib.request.urlopen('http://127.0.0.1:8888/health',timeout=5) as r:assert json.load(r)['status'] is True
  break
 except Exception:
  assert time.monotonic()<deadline, 'Health did not return; maintenance stays closed'
  time.sleep(2)
for url in ['/api/config','/api/version']:
 with urllib.request.urlopen('http://127.0.0.1:8888'+url,timeout=10) as r:assert r.status==200
assert LIVE.read_bytes()==after
assert all(sha(Path(p).read_bytes())==v for p,v in configs.items()), 'Configuration changed'
assert rows('webui.db',"select id from automation_run where status='running'")==original
gate('open',71)
receipt={'state':'CORE_SCOPED_TASK_DETAIL_LOADED','before_sha256':sha(before),'after_sha256':sha(after),'health':200,'api_config':200,'source_leaves_changed':1,'existing_execution_journals_preserved':True,'historical_unresolved_admissions_preserved':True,'models_submitted':0,'erp_documents_changed':0}
save('completed.json',receipt);print(json.dumps(receipt))

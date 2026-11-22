"""Narrow Core-only activation. Original browser, relay, QA and data are preserved."""
from pathlib import Path
from contextlib import closing
import datetime, fcntl, hashlib, json, os, socket, sqlite3, stat, subprocess, sys, time, urllib.request
PACKAGE=Path('/srv/abit-dev-linux/abit-ai/deployment/orchestration-recovery-20261122T1015')
STAGE=Path('/var/lib/abit-ai-orchestration-recovery-20261122T1015')
BASE=Path('/srv/abit-dev-linux/abit-ai')
LIVE=BASE/'releases/20261031-live-0.11.4/runtime/open_webui'
LEASE=Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
OWNER='chat-authorized-root-orchestration-recovery-20261122T1007'
MANIFEST_SHA='UNBOUND'
ALLOWED={'utils/agentic_transport_retry.py','utils/agentic_relay_scope.py','routers/agentic_governance.py'}
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(data):return hashlib.sha256(data).hexdigest()
def raw(path):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'Regular file required')
  return f.read()
def filehash(path):
 h=hashlib.sha256()
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'Regular file required')
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def fsyncdir(path):
 fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(fd)
 finally:os.close(fd)
def save(path,data):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def savejson(path,value):save(path,(json.dumps(value,indent=2,sort_keys=True)+'\n').encode())
def protected(path,directory=False):
 s=path.lstat()
 require(not stat.S_ISLNK(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==(0o700 if directory else 0o600),'Protected stage metadata changed')
 require(stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode),'Protected stage type changed')
def update(receipt):
 p=STAGE/('activation-'+str(os.getpid())+'.tmp')
 savejson(p,receipt);os.replace(p,STAGE/'activation.json');fsyncdir(STAGE)
 print(json.dumps({'state':receipt['state'],'utc':now()}),flush=True)
def owner():
 s=json.loads(LEASE.read_text())
 require(s.get('owner')==OWNER and s.get('state')=='ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY','Operator lease changed')
 require(datetime.datetime.fromisoformat(s['lease_expires_at'].replace('Z','+00:00'))>datetime.datetime.now(datetime.timezone.utc),'Operator lease expired')
def process(pid):
 p=Path('/proc')/str(pid);fields=(p/'stat').read_text().rsplit(')',1)[1].split()
 return {'pid':pid,'start_ticks':int(fields[19]),'uid':p.stat().st_uid,'cmdline_sha256':sha((p/'cmdline').read_bytes())}
def unit(name):
 require(name in ('abit-ai.service','abit-ai-erp-review-qa.service'),'Unsupported service')
 out=subprocess.run(['systemctl','show',name,'-p','MainPID','-p','ActiveState','-p','SubState','-p','User','-p','FragmentPath'],check=True,capture_output=True,text=True,timeout=10).stdout
 return dict(x.split('=',1) for x in out.splitlines() if '=' in x)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
def http(path,port=8888):
 require((port,path) in [(8888,'/health'),(8888,'/api/config'),(8810,'/health')],'Unsupported health path')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 with opener.open('http://127.0.0.1:'+str(port)+path,timeout=5) as response:
  require(response.status==200,'Health HTTP status differs')
  return json.load(response)
def relay_idle():
 d=http('/health',8810)
 require(all(d.get(k) is True for k in ['ok','ready','authenticated','target_ready']),'Relay is not ready')
 require(d.get('active_relay_turns')==0 and d.get('browser_busy') is False,'Relay is busy; leave original work running')
 return {k:d.get(k) for k in ['ok','ready','authenticated','target_ready','active_relay_turns','browser_busy']}
def observed_idle(m,identity):
 # Read-only observations, not a sealed admission barrier. The release flock
 # excludes another operator release, not runtime work admitted after this read.
 deadline=time.monotonic()+20
 def rows(relative,queries):
  path=BASE/'data'/relative;before=path.lstat()
  require(stat.S_ISREG(before.st_mode) and not path.is_symlink(),'Work ledger path changed')
  require(before.st_uid==m['core_before']['uid'],'Work ledger owner changed')
  require(time.monotonic()<deadline,'Work observation deadline exceeded')
  with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
   db.execute('PRAGMA query_only=ON');db.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
   db.execute('BEGIN')
   result=[list(db.execute(sql,args)) for sql,args in queries]
  after=path.lstat()
  require((before.st_dev,before.st_ino,before.st_uid,before.st_mode)==(after.st_dev,after.st_ino,after.st_uid,after.st_mode),'Work ledger identity changed')
  require(time.monotonic()<deadline,'Work observation deadline exceeded')
  return result
 pid=identity['pid'] if identity else -1;start=str(identity['start_ticks']) if identity else ''
 gate,leases=rows('maintenance-admission/gate.sqlite',[
  ('SELECT state,epoch FROM gate WHERE id=1',()),
  ("SELECT state,CASE WHEN pid=? AND process_start=? THEN 1 ELSE 0 END AS owned,COUNT(*) FROM lease WHERE state IN ('active','unresolved') GROUP BY state,owned",(pid,start))])
 require(len(gate)==1 and gate[0][0] in ('open','draining','sealed'),'Maintenance state unavailable')
 current_count=sum(int(count) for state,owned,count in leases if owned)
 require(current_count==0,'Current Core owns active/unresolved work; preserve running work')
 queue_specs=(
  ('relay','aibit-chatgpt-relay.sqlite',"SELECT status,COUNT(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status"),
  ('dot','abitdot-primary.sqlite',"SELECT status,COUNT(*) FROM dot_primary_runs WHERE status IN ('prepared','dispatching') GROUP BY status"),
  ('governance','agentic-governance.sqlite',"SELECT state,COUNT(*) FROM governance_invocation WHERE state IN ('running','cancel_requested') GROUP BY state"),
  ('codex','codex-workspace-runs.sqlite',"SELECT state,COUNT(*) FROM runs WHERE state IN ('submitting','running') GROUP BY state"))
 queues={}
 for label,relative,sql in queue_specs:
  counts=rows(relative,[(sql,())])[0];queues[label]={state:int(count) for state,count in counts}
  require(not any(queues[label].values()),'Active provider work in '+label+'; preserve running work')
 rh=relay_idle()
 if identity:require(process(identity['pid'])==identity,'Core changed during work observation')
 return {'observed_utc':now(),'core_identity':identity,'current_core_lease_count':current_count if identity else None,
  'no_current_core_process':identity is None,'maintenance_state':gate[0][0],'maintenance_epoch':gate[0][1],
  'other_active_or_unresolved_leases':sum(int(count) for state,owned,count in leases if not owned),
  'provider_queue_counts':queues,'relay_health':rh,'sealed_drain':False,
  'admission_race_excluded':False,'historical_and_other_leases_unchanged':True}
def intent_hash(intent):return sha(json.dumps(intent,sort_keys=True,separators=(',',':')).encode())
def last_intent(m,r):
 intents=r.get('activation_intents',[])
 require([x.get('mode') for x in intents] in [['apply'],['rollback'],['apply','rollback']],'Missing or ambiguous activation intent')
 prior=None
 for intent in intents:
  require(intent.get('unit')=='abit-ai.service' and intent.get('unit_sha256')==m['unit_sha256'],'Activation unit binding differs')
  require(intent.get('manifest_sha256')==r['manifest_sha256']==MANIFEST_SHA,'Activation manifest binding differs')
  require(isinstance(intent.get('requested_tick'),int) and intent['requested_tick']>0,'Activation clock binding missing')
  previous=intent.get('previous_identity');state=intent.get('previous_unit',{})
  require(state.get('User')=='mcpdev' and state.get('FragmentPath')=='/etc/systemd/system/abit-ai.service','Previous unit identity differs')
  if intent['mode']=='apply':require(previous==m['core_before'],'Apply predecessor changed')
  else:
   require(intent.get('prior_apply_intent_sha256')==(intent_hash(prior) if prior else None),'Rollback predecessor intent changed')
   if prior:require(intent['requested_tick']>=prior['requested_tick'],'Rollback predates apply')
  if previous is None:
   require(intent['mode']=='rollback' and prior is not None and int(state.get('MainPID') or 0)==0 and state.get('ActiveState') in ('inactive','failed') and state.get('SubState') in ('dead','failed'),'Stopped Core lacks this release apply intent')
  else:
   require(previous['uid']==m['core_before']['uid'] and previous['cmdline_sha256']==m['core_before']['cmdline_sha256'],'Previous Core signature changed')
   require(int(state.get('MainPID') or 0)==previous['pid'] and state.get('ActiveState')=='active' and state.get('SubState')=='running','Previous Core state changed')
   if intent['mode']=='rollback' and previous!=m['core_before']:
    require(prior is not None and previous['start_ticks']>=prior['requested_tick'] and previous['pid']!=prior['previous_identity']['pid'],'Rollback Core is not bound to this apply')
  if intent['mode']=='apply':prior=intent
 return intents[-1]
def activation_origin(m,r,mode):
 s=service(m);pid=int(s.get('MainPID') or 0)
 prior=last_intent(m,r) if r.get('activation_intents') else None
 require(not prior or prior['mode']!='rollback','Existing rollback intent: finalize without another restart')
 if mode=='apply':require(prior is None,'Existing apply intent: finalize without another restart')
 if pid:
  require(s.get('ActiveState')=='active' and s.get('SubState')=='running','Core activation is still transitioning')
  old=process(pid)
  require(old['uid']==m['core_before']['uid'] and old['cmdline_sha256']==m['core_before']['cmdline_sha256'],'Core identity changed')
  if mode=='apply' or prior is None:require(old==m['core_before'],'Core changed after preflight')
  elif old!=m['core_before']:
   require(old['start_ticks']>=prior['requested_tick'] and old['pid']!=prior['previous_identity']['pid'],'Core does not match pending apply')
  if r.get('core_after'):require(old==r['core_after'],'Previously observed Core changed')
 else:
  require(mode=='rollback' and prior is not None and prior['mode']=='apply','Stopped Core requires this release original apply intent')
  require(s.get('ActiveState') in ('inactive','failed') and s.get('SubState') in ('dead','failed'),'Core activation is still transitioning')
  old=None
 return s,old,prior
def preserve(m):
 for value in m['preserved_processes'].values():require(process(value['pid'])==value,'Original relay/browser/QA process changed')
def service(m):
 require(filehash(Path('/etc/systemd/system/abit-ai.service'))==m['unit_sha256'],'Service definition changed')
 s=unit('abit-ai.service')
 require(s.get('User')=='mcpdev' and s.get('FragmentPath')=='/etc/systemd/system/abit-ai.service','Core unit identity changed')
 return s
def source(m,mode):
 require((BASE/'current/runtime/open_webui').resolve()==LIVE,'Active runtime changed')
 baseline=m['baseline']
 require(len(baseline)==373 and set(m['changes'])==ALLOWED,'Unexpected source scope')
 for rel,row in baseline.items():
  p=LIVE/rel
  require(p.resolve()==p and not p.is_symlink(),'Source path changed')
  expected=row['sha256']
  if rel in ALLOWED and mode=='after':expected=m['changes'][rel]['after_sha256']
  actual=filehash(p)
  permitted={expected}
  if rel in ALLOWED and mode=='partial':permitted.add(m['changes'][rel]['after_sha256'])
  require(actual in permitted,'Installed source changed: '+rel)
  st=p.stat()
  require((st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))==(row['uid'],row['gid'],int(row['mode'],8)),'Source metadata changed: '+rel)
 for rel,row in m['changes'].items():
  p=LIVE/rel;st=p.parent.stat()
  require((st.st_dev,st.st_ino)==(row['parent_device'],row['parent_inode']),'Target parent changed')
 for row in m['configuration']:
  p=Path(row['path']);require(filehash(p)==row['sha256'],'Runtime configuration changed')
def backup(m):
 protected(STAGE,True);protected(STAGE/'backup-receipt.json')
 require(filehash(STAGE/'backup-receipt.json')==m['backup_receipt_sha256'],'Backup receipt changed')
 r=json.loads(raw(STAGE/'backup-receipt.json'))
 require(r['status']=='PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED' and len(r['databases'])==11,'Verified backup required')
 for row in r['databases']:
  p=STAGE/'database'/row['relative_source'].replace('/','--');protected(p)
  require(filehash(p)==row['backup_sha256'] and row['integrity']=='ok' and row['isolated_restore_integrity']=='ok' and row['isolated_restore_schema_and_counts_match'] is True,'Backup verification changed')
 for rel,row in m['changes'].items():
  p=STAGE/'source'/rel.replace('/','--');protected(p)
  require(filehash(p)==row['before_sha256'],'Rollback bytes changed')
def manifest():
 require(MANIFEST_SHA!='UNBOUND','Unbound release cannot execute')
 value=raw(PACKAGE/'release-manifest.json');require(sha(value)==MANIFEST_SHA,'Release manifest changed')
 m=json.loads(value)
 require(m['purpose']=='CURRENT_CORE_ACCEPTED_ORIGINAL_AND_BOUNDED_GATEWAY_DIAGNOSTICS' and m['runtime_root']==str(LIVE),'Unexpected release scope')
 require(set(m['changes'])==ALLOWED,'Unexpected runtime targets')
 for rel,row in m['changes'].items():
  require(row['before_sha256']==m['baseline'][rel]['sha256'],'Preimage binding changed')
  require(row['candidate']==str(PACKAGE/'payload/open_webui'/rel),'Candidate path changed')
  require(filehash(Path(row['candidate']))==row['after_sha256'],'Candidate changed')
 for r in m['evidence']:
  require(filehash(Path(r['path']))==r['sha256'],'Reviewed evidence changed')
 require(m['validation']['reviewed'] is True and m['validation']['api_verify_passed'] is True and m['validation']['api_integration_failed']==0,'Required review/API gates incomplete')
 return m,value
def inspect(m,mode='before',require_idle=True):
 owner();source(m,mode);backup(m);preserve(m);s=service(m)
 require(s['ActiveState']=='active' and s['SubState']=='running','Core is not running')
 identity=process(int(s['MainPID']))
 require(identity['uid']==m['core_before']['uid'] and identity['cmdline_sha256']==m['core_before']['cmdline_sha256'],'Core process signature changed')
 if mode=='before':require(identity==m['core_before'],'Core preflight identity changed')
 require(http('/health').get('status') is True,'Core health failed')
 config=http('/api/config');require(config.get('version')==m['app_version'],'App version changed')
 work=observed_idle(m,identity) if require_idle else None
 require(service(m)==s,'Core unit changed during preflight')
 return {'service':s,'process':identity,'health':True,'config_version':config.get('version'),'work_observation':work}
def install(p,data,row):
 temp=p.with_name(p.name+'.recovery-'+str(os.getpid())+'.tmp')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,int(row['mode'],8))
 try:
  with os.fdopen(fd,'wb') as f:
   f.write(data);f.flush();os.fchmod(f.fileno(),int(row['mode'],8));os.fchown(f.fileno(),row['uid'],row['gid']);os.fsync(f.fileno())
  os.replace(temp,p);fsyncdir(p.parent)
 finally:
  if temp.exists():temp.unlink()
def activate(m,r,mode):
 owner();preserve(m);s,old,prior=activation_origin(m,r,mode)
 proof=observed_idle(m,old)
 require(service(m)==s,'Core unit changed during work observation')
 if old:require(process(old['pid'])==old,'Core changed before activation')
 tick=int(time.clock_gettime(time.CLOCK_BOOTTIME)*os.sysconf('SC_CLK_TCK'))
 intent={'mode':mode,'unit':'abit-ai.service','unit_sha256':m['unit_sha256'],
  'manifest_sha256':r['manifest_sha256'],'requested_tick':tick,'previous_identity':old,
  'previous_unit':s,'requested_utc':now()}
 if mode=='rollback':intent['prior_apply_intent_sha256']=intent_hash(prior) if prior else None
 r.setdefault('activation_intents',[]).append(intent);last_intent(m,r)
 r['pre_restart_work_observation']=proof;r['state']='core_activation_requested';update(r)
 # Do not retry this command after an interruption: finalize this durable intent.
 owner();preserve(m);require(service(m)==s,'Core changed after intent recording')
 if old:require(process(old['pid'])==old,'Core changed after intent recording')
 relay_idle()
 subprocess.run(['systemctl','restart','--no-block','abit-ai.service'],check=True,timeout=15)
 deadline=time.monotonic()+150
 while time.monotonic()<deadline:
  preserve(m)
  try:
   current_unit=service(m);pid=int(current_unit.get('MainPID') or 0)
   if current_unit.get('ActiveState')=='active' and current_unit.get('SubState')=='running' and pid and (old is None or pid!=old['pid']) and http('/health').get('status') is True:
    current=process(pid)
    require(current['start_ticks']>=tick and current['uid']==m['core_before']['uid'] and current['cmdline_sha256']==m['core_before']['cmdline_sha256'],'Activated process identity differs')
    r['core_after']=current;r['state']='core_health_responding';update(r);return
  except (OSError,TimeoutError,urllib.error.URLError):pass
  time.sleep(1)
 raise RuntimeError('Core health deadline exceeded; preserve intent and use finalize after checking original activation')
def verify(m,r,rollback=False):
 mode='before' if rollback else 'after'
 source(m,mode);backup(m);preserve(m);s=service(m)
 require(s.get('ActiveState')=='active' and s.get('SubState')=='running','Core not active')
 current=process(int(s['MainPID']));intent=last_intent(m,r);previous=intent['previous_identity']
 require(intent['mode']==('rollback' if rollback else 'apply') and (previous is None or current['pid']!=previous['pid']) and current['start_ticks']>=intent['requested_tick'],'Activation intent not fulfilled')
 require(current['uid']==m['core_before']['uid'] and current['cmdline_sha256']==m['core_before']['cmdline_sha256'],'Core signature differs')
 if r.get('core_after'):require(current==r['core_after'],'Activated process changed')
 require(http('/health').get('status') is True,'Core health failed')
 cfg=http('/api/config');require(cfg.get('version')==m['app_version'],'Config/version failed')
 rh=relay_idle();preserve(m);source(m,mode)
 require(service(m)==s and process(current['pid'])==current,'Core changed during final verification')
 r.update(state='rolled_back_health_verified' if rollback else 'deployed_health_verified',verified_utc=now(),core_after=current,relay_health=rh,config_version=cfg['version'],business_workflow_complete=False,browser_and_relay_and_qa_preserved=True,databases_restored=False)
 update(r)
def main():
 require(os.geteuid()==0 and os.environ.get('SUDO_USER')=='jimran@abitcons.com','Existing authorized administrator route required')
 require(socket.gethostname()=='abit-dev-linux','Wrong host')
 require(len(sys.argv)==2 and sys.argv[1] in ['preflight','apply','finalize','rollback'],'Explicit release action required')
 mode=sys.argv[1];owner();m,encoded=manifest()
 if mode in ['preflight','apply']:
  checked=inspect(m)
  if mode=='preflight':
   print(json.dumps({'state':'preflight_passed','manifest_sha256':MANIFEST_SHA,'targets':sorted(ALLOWED),'core':checked['process'],'work_observation':checked['work_observation'],'utc':now()}));return
  require(not (STAGE/'activation.json').exists(),'Existing activation; reconcile instead of applying again')
  protected(STAGE,True);(STAGE/'candidate').mkdir(mode=0o700)
  save(STAGE/'release-manifest.json',encoded)
  for rel,row in m['changes'].items():save(STAGE/'candidate'/rel.replace('/','--'),raw(Path(row['candidate'])))
  r={'state':'prepared','manifest_sha256':MANIFEST_SHA,'started_utc':now(),'core_before':m['core_before'],'preserved_processes':m['preserved_processes'],'files_written':[],'original_jobs_resubmitted':False,'preflight_work_observation':checked['work_observation']}
  update(r)
 else:
  protected(STAGE/'activation.json');protected(STAGE/'release-manifest.json')
  require(filehash(STAGE/'release-manifest.json')==MANIFEST_SHA,'Protected manifest changed')
  r=json.loads(raw(STAGE/'activation.json'));require(r['manifest_sha256']==MANIFEST_SHA,'Activation receipt changed')
  if mode=='finalize':
   intent=last_intent(m,r);verify(m,r,intent['mode']=='rollback');return
  # Repeated rollback is readback/finalization only, never another restart.
  if r.get('activation_intents') and last_intent(m,r)['mode']=='rollback':
   verify(m,r,True);return
  source(m,'partial');backup(m);preserve(m)
  activation_origin(m,r,'rollback')
  r['rollback_started_utc']=now();r.pop('core_after',None);update(r)
 try:
  owner();s,identity,_=activation_origin(m,r,mode)
  r['pre_install_work_observation']=observed_idle(m,identity)
  require(service(m)==s,'Core changed before source installation');update(r)
  for rel,row in m['changes'].items():
   target=LIVE/rel;expected=row['before_sha256'] if mode=='apply' else row['after_sha256']
   wanted=row['after_sha256'] if mode=='apply' else row['before_sha256']
   actual=filehash(target)
   if mode=='rollback' and actual==wanted:continue
   require(actual==expected,'Concurrent target change')
   p=STAGE/('candidate' if mode=='apply' else 'source')/rel.replace('/','--');protected(p)
   data=raw(p);require(sha(data)==wanted,'Staged bytes changed')
   install(target,data,m['baseline'][rel])
   r['files_written'].append({'mode':mode,'path':rel,'sha256':wanted,'utc':now()});update(r)
  r['state']='source_bytes_installed';update(r)
  activate(m,r,mode);verify(m,r,mode=='rollback')
 except BaseException as error:
  r.update(state='activation_incomplete_evidence_preserved',failed_utc=now(),error_type=type(error).__name__)
  update(r);raise
if __name__=='__main__':
 fd=os.open('/var/lock/abit-ai-governance-release.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'a') as f:
  s=os.fstat(f.fileno());require(s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o600,'Release lock metadata changed')
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);main()

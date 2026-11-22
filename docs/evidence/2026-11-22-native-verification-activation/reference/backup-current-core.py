"""One bounded, protected SQLite backup for the current ABiT Core release.

This does not modify business databases, stop services, settle executions, or
claim a cross-database atomic snapshot. The backups are for recovery inspection;
source rollback must never restore them automatically.
"""
from pathlib import Path
import datetime, fcntl, hashlib, json, os, shutil, socket, sqlite3, stat, time
BASE=Path('/srv/abit-dev-linux/abit-ai')
PACKAGE=BASE/'deployment/orchestration-recovery-20261122T1015'
STAGE=Path('/var/lib/abit-ai-orchestration-recovery-20261122T1015')
LEASE=Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
OWNER='chat-authorized-root-orchestration-recovery-20261122T1007'
DATABASES=('webui.db','agentic-governance.sqlite','abitdot-primary.sqlite',
 'aibit-chatgpt-relay.sqlite','abit-slack.sqlite','abit-telegram.sqlite',
 'codex-workspace-runs.sqlite','erp-dot-issues.sqlite','erp-pending-review-qa.sqlite',
 'erp-task-resolution.sqlite','maintenance-admission/gate.sqlite')
CONFIG=('config/runtime-environment.json','config/final-snapshot.json',
 'config/webui-secret-key','deployment/run-production.py')
TARGETS=('utils/agentic_transport_retry.py','utils/agentic_relay_scope.py',
 'routers/agentic_governance.py')
DEADLINE=time.monotonic()+240
def require(ok,msg):
 if not ok: raise RuntimeError(msg)
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def regular(p):
 s=p.lstat()
 require(stat.S_ISREG(s.st_mode) and not stat.S_ISLNK(s.st_mode),'Regular source required')
 return s
def save(p,raw):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:
  f.write(raw); f.flush(); os.fsync(f.fileno())
def json_save(p,obj): save(p,(json.dumps(obj,sort_keys=True,indent=2)+'\n').encode())
def checkpoint(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try: os.fsync(fd)
 finally: os.close(fd)
def table_summary(db):
 names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
 rows=[]
 for name in names:
  quoted='"'+name.replace('"','""')+'"'
  rows.append((name,db.execute('SELECT count(*) FROM '+quoted).fetchone()[0]))
 schema=list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name"))
 return {'table_count':len(names),'counts_sha256':sha(json.dumps(rows).encode()),'schema_sha256':sha(json.dumps(schema).encode())}
def deadline():
 require(time.monotonic()<DEADLINE,'Backup time bound exceeded; preserve partial stage')
def sql_deadline():return 1 if time.monotonic()>=DEADLINE else 0
def progress(status,remaining,total):deadline()
def filehash(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):
   deadline();h.update(block)
 return h.hexdigest()
def validate_owner():
 s=json.loads(LEASE.read_text())
 require(s['owner']==OWNER and s['state']=='ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY','Operator lease changed')
 expiry=datetime.datetime.fromisoformat(s['lease_expires_at'].replace('Z','+00:00'))
 require(expiry>datetime.datetime.now(datetime.timezone.utc),'Operator lease expired')
def main():
 require(os.geteuid()==0 and os.environ.get('SUDO_USER')=='jimran@abitcons.com','Existing jimran administrator route required')
 require(socket.gethostname()=='abit-dev-linux','Wrong host')
 require((BASE/'current/runtime').resolve()==BASE/'releases/20261031-live-0.11.4/runtime','Runtime changed')
 validate_owner()
 require(shutil.disk_usage(STAGE.parent).free>3*1024**3,'Insufficient backup space')
 require(not STAGE.exists(),'One-use stage already exists; inspect its existing result')
 STAGE.mkdir(mode=0o700)
 for name in ['database','restore-check','configuration','source']:(STAGE/name).mkdir(mode=0o700)
 report={'started_utc':now(),'purpose':'CORE_SOURCE_RELEASE_PROTECTED_ONLINE_BACKUP',
  'source_database_modified':False,'cross_database_atomic_snapshot_claimed':False,
  'automatic_database_restore_allowed':False,'databases':[],'configuration':[],'source':[]}
 json_save(STAGE/'backup-intent.json',{'started_utc':now(),'operator':OWNER,'databases':DATABASES})
 try:
  for name in DATABASES:
   deadline()
   src=BASE/'data'/name; srcmeta=regular(src)
   destination=STAGE/'database'/name.replace('/','--')
   fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
   with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True,timeout=5) as source:
    source.execute('PRAGMA query_only=ON')
    started=now()
    with sqlite3.connect(destination,timeout=5) as backup:
     backup.set_progress_handler(sql_deadline,1000)
     source.backup(backup,pages=256,progress=progress,sleep=0.025)
     require(backup.execute('PRAGMA quick_check').fetchall()==[('ok',)],'Backup integrity failed')
     summary=table_summary(backup)
    source_version=source.execute('PRAGMA data_version').fetchone()[0]
   os.chmod(destination,0o600)
   regular(destination)
   with open(destination,'rb') as f:os.fsync(f.fileno())
   # Exercise the same SQLite engine restoring the backup to a new isolated file.
   restored=STAGE/'restore-check'/destination.name
   fd=os.open(restored,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
   with sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True) as backup:
    backup.execute('PRAGMA query_only=ON')
    with sqlite3.connect(restored) as restore:
     restore.set_progress_handler(sql_deadline,1000)
     backup.backup(restore,pages=256,progress=progress,sleep=0.025)
     require(restore.execute('PRAGMA quick_check').fetchall()==[('ok',)],'Isolated restored copy integrity failed')
     require(table_summary(restore)==summary,'Isolated restored copy differs')
   row={'relative_source':name,'snapshot_started_utc':started,'snapshot_finished_utc':now(),
    'source_uid':srcmeta.st_uid,'source_gid':srcmeta.st_gid,'source_mode':oct(stat.S_IMODE(srcmeta.st_mode)),
    'backup_bytes':destination.stat().st_size,'backup_sha256':filehash(destination),
    'integrity':'ok','isolated_restore_integrity':'ok','isolated_restore_schema_and_counts_match':True,
    'source_data_version_observed':source_version,**summary}
   report['databases'].append(row)
   restored.unlink()
  for name in CONFIG:
   deadline()
   src=BASE/name;s=regular(src);raw=src.read_bytes();dst=STAGE/'configuration'/name.replace('/','--')
   save(dst,raw);require(sha(src.read_bytes())==sha(raw),'Configuration changed during backup')
   report['configuration'].append({'relative_source':name,'sha256':sha(raw),'bytes':len(raw),'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode))})
  unit=Path('/etc/systemd/system/abit-ai.service');raw=unit.read_bytes();regular(unit)
  save(STAGE/'configuration/abit-ai.service',raw)
  report['configuration'].append({'relative_source':'/etc/systemd/system/abit-ai.service','sha256':sha(raw),'bytes':len(raw)})
  for name in TARGETS:
   deadline()
   src=BASE/'current/runtime/open_webui'/name;s=regular(src);raw=src.read_bytes()
   save(STAGE/'source'/name.replace('/','--'),raw)
   report['source'].append({'relative_source':name,'sha256':sha(raw),'bytes':len(raw),'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode))})
  validate_owner()
  report.update(status='PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED',finished_utc=now(),sqlite_version=sqlite3.sqlite_version)
  json_save(STAGE/'backup-receipt.json',report)
  for d in [STAGE/'database',STAGE/'restore-check',STAGE/'configuration',STAGE/'source',STAGE]:checkpoint(d)
  print(json.dumps({'status':report['status'],'stage':str(STAGE),'database_count':len(report['databases']),'backup_bytes':sum(r['backup_bytes'] for r in report['databases']),'receipt_sha256':sha((STAGE/'backup-receipt.json').read_bytes()),'cross_database_atomic_snapshot_claimed':False}),flush=True)
 except BaseException as error:
  json_save(STAGE/'backup-failed.json',{'failed_utc':now(),'error_type':type(error).__name__,'completed_databases':len(report['databases']),'partial_stage_retained':True})
  raise
if __name__=='__main__':
 fd=os.open('/var/lock/abit-ai-governance-release.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'a') as f:
  s=os.fstat(f.fileno());require(s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o600,'Release lock metadata changed')
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);main()

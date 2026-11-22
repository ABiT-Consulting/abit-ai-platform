"""Fixed create-only installation of three already reviewed source leaves."""
from pathlib import Path
import datetime,hashlib,json,os,stat,sys
BASE=Path("/srv/abit-dev-linux/abit-ai-native-overlay-recovery-holder-20261122")
OLD=Path("/var/lib/abit-ai-native-overlay-held-drain-source-20261122")
NEW=Path("/var/lib/abit-ai-native-overlay-recovery-holder-source-20261122")
COMMIT="11530ff2c01382bfb35743bf682a25a757b3a314"
FILES={
 "deployment/native-overlay-held-drain-20261122/hold_native_overlay.py":(OLD/"hold_native_overlay.py","214f79fa62136a29019178ee3241d1c6f2ea19855a2f25785d55b3595df0899c"),
 "deployment/native-overlay-recovery-holder-20261122/hold_native_recovery.py":(NEW/"hold_native_recovery.py","5e01ac517e7e7c4f961361608ee94444aa5b02f35aef734608d9675a472e1a6a"),
 "deployment/native-overlay-recovery-holder-20261122/recovery_receipt.py":(NEW/"recovery_receipt.py","b18c9d5ef2c3b8bc245f8011a8ce5aae7f526d8568bfdeae3cd7778d57d02e26")}
DEPS={"reviewed/release_relay.py":"4cbf14905acd67d0acabded257b9090443aa023ca1038d424c0baea83c764481",
 "relay_bootstrap.py":"b6292e09d4313d6fecca0e63440b5d3f557a0b976a795bbdef93fb42024bd282",
 "relay_runtime_guard.py":"cc3fd5538ed797f89770751e80bbc5b53b95c67b1fa5374db0cbb76218c2afde",
 "browser_lock_keeper.cjs":"f718f8b1a64846a876ff73bfa4e908285c12137160e7ea08a9269efaec4b7879",
 "held_admission_receipt.py":"7c4262dc3d1e9299a067023d0fb623aea33ae3c71ed18a3385b4e14176403237"}
ABSENT=(Path("/var/lib/abit-ai-native-overlay-held-drain-20261122"),
 Path("/var/lib/abit-ai-native-overlay-recovery-holder-20261122"),
 Path("/var/lib/abit-ai-native-overlay-original-flock-witness-20261122"))
def need(value,code):
 if not value:raise RuntimeError(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def ident(x):
 return {k:getattr(x,k)for k in ("st_dev","st_ino","st_mode","st_uid","st_gid","st_nlink","st_size","st_mtime_ns","st_ctime_ns")}
def directory(p,mode=None):
 need(p.is_absolute()and p.resolve()==p,"EXACT_ROOT_DIRECTORY_PATH")
 for q in (p,*p.parents):
  x=q.lstat();need(stat.S_ISDIR(x.st_mode)and x.st_uid==x.st_gid==0 and not x.st_mode&0o022,"ROOT_ANCESTRY_REQUIRED")
 x=p.lstat()
 if mode is not None:need(stat.S_IMODE(x.st_mode)==mode,"EXACT_ROOT_DIRECTORY_MODE")
 return ident(x)
def read(p,expected,root=False):
 need(p.resolve()==p,"SOURCE_ALIAS_DENIED")
 if root:directory(p.parent)
 fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  x=os.fstat(fd);need(stat.S_ISREG(x.st_mode)and x.st_nlink==1 and 0<x.st_size<=512*1024,"BOUNDED_REGULAR_SOURCE_REQUIRED")
  if root:need(x.st_uid==x.st_gid==0 and stat.S_IMODE(x.st_mode)==0o400,"ROOT0400_SOURCE_REQUIRED")
  chunks=[];total=0
  while True:
   block=os.read(fd,min(65536,512*1024+1-total))
   if not block:break
   total+=len(block);need(total<=512*1024,"SOURCE_TOO_LARGE");chunks.append(block)
  raw=b"".join(chunks)
  need(len(raw)==x.st_size and ident(x)==ident(os.fstat(fd))==ident(p.lstat()),"SOURCE_CHANGED_DURING_READ")
  need(sha(raw)==expected,"EXACT_REVIEWED_SOURCE_HASH_REQUIRED")
  return raw,ident(x)
 finally:os.close(fd)
def syncdir(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:os.fsync(fd)
 finally:os.close(fd)
def create(p,raw):
 directory(p.parent,0o700)
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o400)
 try:
  os.fchmod(fd,0o400);os.fchown(fd,0,0)
  offset=0
  while offset<len(raw):
   size=os.write(fd,raw[offset:]);need(size>0,"SOURCE_WRITE_STOPPED");offset+=size
  os.fsync(fd)
 finally:os.close(fd)
 syncdir(p.parent)
def main():
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0,"ROOT_REQUIRED")
 need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,"ISOLATED_FIXED_INTERPRETER")
 directory(OLD,0o700);directory(OLD/"reviewed",0o700);directory(NEW.parent)
 need(set(os.listdir(OLD))=={"reviewed","relay_bootstrap.py","relay_runtime_guard.py","browser_lock_keeper.cjs","held_admission_receipt.py"},"EXACT_EXISTING_FIVE_DEPENDENCY_SCOPE")
 need(set(os.listdir(OLD/"reviewed"))=={"release_relay.py"},"EXACT_EXISTING_REVIEWED_DEPENDENCY")
 need(not os.path.lexists(NEW)and all(not os.path.lexists(p)for p in ABSENT),"CREATE_ONLY_CODE_AND_NO_OPERATION_REQUIRED")
 need(all(not os.path.lexists(p)for p,_ in FILES.values()),"REFUSE_SOURCE_OVERWRITE")
 payload={rel:read(BASE/rel,pin)[0]for rel,(_,pin)in FILES.items()}
 before={rel:read(OLD/rel,pin,root=True)[1]for rel,pin in DEPS.items()}
 NEW.mkdir(mode=0o700);syncdir(NEW.parent);directory(NEW,0o700)
 output={}
 for rel,(p,pin)in FILES.items():
  create(p,payload[rel]);raw,metadata=read(p,pin,root=True)
  output[str(p)]={"sha256":sha(raw),"bytes":len(raw),"metadata":metadata}
 for rel,pin in DEPS.items():need(read(OLD/rel,pin,root=True)[1]==before[rel],"EXISTING_DEPENDENCY_CHANGED")
 need(set(os.listdir(NEW))=={"hold_native_recovery.py","recovery_receipt.py"},"EXACT_NEW_CODE_FILE_SET")
 need(set(os.listdir(OLD))=={"reviewed","relay_bootstrap.py","relay_runtime_guard.py","browser_lock_keeper.cjs","held_admission_receipt.py","hold_native_overlay.py"},"EXACT_OLD_CODE_ADDITION")
 need(all(not os.path.lexists(p)for p in ABSENT),"UNEXPECTED_OPERATION_SCOPE_CREATED")
 print(json.dumps({"schema_version":1,"state":"EXACT_REVIEWED_CODE_CREATED_ONLY","at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
  "operator_session_identity":"jimran@abitcons.com","effective_install_uid":os.geteuid(),"source_commit":COMMIT,
  "files":output,"existing_five_dependency_metadata_preserved":True,"new_code_directory":directory(NEW,0o700),
  "root_directory_fsync_completed":True,"overwrite_performed":False,"payload_code_executed":False,
  "manifest_writes":0,"operation_stage_created":False,"witness_created":False,"signatures_issued":0,
  "barrier_actions":0,"service_actions":0},sort_keys=True))
if __name__=="__main__":main()

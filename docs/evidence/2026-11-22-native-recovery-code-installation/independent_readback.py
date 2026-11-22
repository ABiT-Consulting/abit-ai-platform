"""Independent fixed-code readback; no source import, mutation or service action."""
import datetime,hashlib,json,os,stat
from pathlib import Path
OLD=Path("/var/lib/abit-ai-native-overlay-held-drain-source-20261122")
NEW=Path("/var/lib/abit-ai-native-overlay-recovery-holder-source-20261122")
PINS={
 OLD/"hold_native_overlay.py":"214f79fa62136a29019178ee3241d1c6f2ea19855a2f25785d55b3595df0899c",
 NEW/"hold_native_recovery.py":"5e01ac517e7e7c4f961361608ee94444aa5b02f35aef734608d9675a472e1a6a",
 NEW/"recovery_receipt.py":"b18c9d5ef2c3b8bc245f8011a8ce5aae7f526d8568bfdeae3cd7778d57d02e26",
 OLD/"reviewed/release_relay.py":"4cbf14905acd67d0acabded257b9090443aa023ca1038d424c0baea83c764481",
 OLD/"relay_bootstrap.py":"b6292e09d4313d6fecca0e63440b5d3f557a0b976a795bbdef93fb42024bd282",
 OLD/"relay_runtime_guard.py":"cc3fd5538ed797f89770751e80bbc5b53b95c67b1fa5374db0cbb76218c2afde",
 OLD/"browser_lock_keeper.cjs":"f718f8b1a64846a876ff73bfa4e908285c12137160e7ea08a9269efaec4b7879",
 OLD/"held_admission_receipt.py":"7c4262dc3d1e9299a067023d0fb623aea33ae3c71ed18a3385b4e14176403237"}
def check(v,c):
 if not v:raise RuntimeError(c)
fields=("st_dev","st_ino","st_mode","st_uid","st_gid","st_nlink","st_size","st_mtime_ns","st_ctime_ns")
results={}
check(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0,"ROOT_READBACK_REQUIRED")
for path,pin in PINS.items():
 check(path.resolve()==path,"EXACT_FIXED_PATH_REQUIRED")
 for parent in (path.parent,*path.parent.parents):
  s=parent.lstat();check(stat.S_ISDIR(s.st_mode)and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,"ROOT_ANCESTRY")
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 try:
  before=os.fstat(fd)
  check(stat.S_ISREG(before.st_mode)and before.st_uid==before.st_gid==0 and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o400 and 0<before.st_size<=512*1024,"FIXED_ROOT0400_REGULAR_FILE")
  with os.fdopen(os.dup(fd),"rb")as stream:raw=stream.read(512*1024+1)
  check(len(raw)==before.st_size and hashlib.sha256(raw).hexdigest()==pin,"EXACT_CODE_BYTES")
  after=os.fstat(fd);named=path.lstat()
  check(all(getattr(before,k)==getattr(after,k)==getattr(named,k)for k in fields),"NAMED_SOURCE_CHANGED")
  results[str(path)]={"sha256":pin,"bytes":len(raw),"uid":0,"gid":0,"mode":"0400","nlink":1,"device":before.st_dev,"inode":before.st_ino}
 finally:os.close(fd)
for path in (OLD,OLD/"reviewed",NEW):check(stat.S_IMODE(path.lstat().st_mode)==0o700,"ROOT0700_CODE_DIRECTORY")
check(set(os.listdir(NEW))=={"hold_native_recovery.py","recovery_receipt.py"},"NO_ADDITIONAL_RECOVERY_INPUTS")
absent=("/var/lib/abit-ai-native-overlay-held-drain-20261122","/var/lib/abit-ai-native-overlay-recovery-holder-20261122","/var/lib/abit-ai-native-overlay-original-flock-witness-20261122")
check(all(not os.path.lexists(p)for p in absent),"NO_OPERATION_OR_WITNESS")
print(json.dumps({"schema_version":1,"state":"INDEPENDENT_FIXED_CODE_READBACK_PASSED","at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"files":results,"root0700_directories":True,"operation_and_witness_paths_absent":True,"source_imports":0,"writes":0,"service_actions":0,"barrier_actions":0},sort_keys=True))

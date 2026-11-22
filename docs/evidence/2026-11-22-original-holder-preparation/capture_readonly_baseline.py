"""Read only current fixed Core/relay identities; emit metadata and hashes only."""
from pathlib import Path
import copy,datetime,hashlib,json,os,stat,sys,types
OLD=Path("/var/lib/abit-ai-native-overlay-held-drain-source-20261122")
PRIOR=Path("/srv/abit-dev-linux/abit-ai-native-overlay-recovery-holder-20261122/docs/evidence/2026-11-22-original-holder-preparation/inputs/prior-baseline.json")
OLD_SHA="214f79fa62136a29019178ee3241d1c6f2ea19855a2f25785d55b3595df0899c"
PRIOR_SHA="1cf74f44b62b6229d6787f0ba630001e9577942da63dc554026af5c5cc0bd5d7"
def need(v,c):
 if not v:raise RuntimeError(c)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def root_source(path,pin):
 need(path.resolve()==path,"EXACT_CODE_PATH")
 for p in (path.parent,*path.parent.parents):
  q=p.lstat();need(stat.S_ISDIR(q.st_mode)and q.st_uid==q.st_gid==0 and not q.st_mode&0o022,"ROOT_SOURCE_ANCESTRY")
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 try:
  s=os.fstat(fd);need(stat.S_ISREG(s.st_mode)and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==0o400 and s.st_nlink==1 and 0<s.st_size<=256*1024,"EXACT_ROOT0400_SOURCE")
  data=os.read(fd,256*1024+1)
  attrs=("st_dev","st_ino","st_mode","st_uid","st_gid","st_nlink","st_size","st_mtime_ns","st_ctime_ns")
  after=os.fstat(fd);named=path.lstat()
  need(len(data)==s.st_size and digest(data)==pin and all(getattr(s,k)==getattr(after,k)==getattr(named,k)for k in attrs),"FIXED_CODE_CHANGED")
  return data
 finally:os.close(fd)
need(os.getresuid()==(0,0,0)and os.getresgid()==(0,0,0),"ROOT_READ_ONLY_OBSERVER")
raw=root_source(OLD/"hold_native_overlay.py",OLD_SHA)
old=types.ModuleType("fixed_original_holder_observer");old.__file__=str(OLD/"hold_native_overlay.py");sys.modules[old.__name__]=old;exec(compile(raw,old.__file__,"exec"),old.__dict__)
a=old.load_module("reviewed/release_relay.py","fixed_original_holder_snapshot_api")
b=old.load_module("relay_bootstrap.py","fixed_original_holder_bootstrap_snapshot")
a.PACKAGE=OLD;a.STAGE=old.STAGE
for name,pin in old.DEPENDENCIES.items():need(digest(old.root_read(OLD/name))==pin,"DEPENDENCY_CHANGED")
previous=PRIOR.read_bytes();need(digest(previous)==PRIOR_SHA,"PRIOR_BOUND_SCOPE_CHANGED")
prior=json.loads(previous);m=copy.deepcopy(prior)
need(not os.path.lexists(old.STAGE)and not os.path.lexists("/var/lib/abit-ai-native-overlay-recovery-holder-20261122")and not os.path.lexists("/var/lib/abit-ai-native-overlay-original-flock-witness-20261122"),"NO_HOLD_OPERATION_OR_WITNESS_REQUIRED")
expiry=old.operator(a,m)
a.preserve(m);need(a.process(m["relay_before"]["pid"])==m["relay_before"],"EXACT_RELAY_GENERATION")
a.units(m);old.source_check(a,m)
key=old.KEY.lstat();need(stat.S_ISREG(key.st_mode)and key.st_nlink==1 and 0<key.st_size<=4096 and old.KEY.resolve()==old.KEY,"EXISTING_FIXED_SIGNING_KEY_METADATA")
key_metadata={"path":str(old.KEY),"device":key.st_dev,"inode":key.st_ino,"uid":key.st_uid,"gid":key.st_gid,"mode":oct(stat.S_IMODE(key.st_mode)),"bytes":key.st_size,"mtime_ns":key.st_mtime_ns,"contents_read":False,"digest_recorded":False}
m["preserved_processes"]={name:a.process(v["pid"])for name,v in m["preserved_processes"].items()}
m["relay_before"]=a.process(m["relay_before"]["pid"])
m["gate_before"]=a.gate(m);need(m["gate_before"]["state"]=="open","FRESH_OPEN_GATE_REQUIRED")
m.update(a.ledger_snapshot(m,verify=False))
m["private_preservation"]=a.private_preservation(m,verify=False)
m["browser_inventory"]=a.browser_snapshot(m,verify=False)
m["hermes_transport"]=b.source_timeout(m["relay_before"]["pid"])
other,owned=b.split_ruleset(b.read_ruleset());need(not owned,"ORIGINAL_BARRIER_ALREADY_PRESENT")
m["nft_before_sha256"]=b.digest(other)
runtime=old.runtime_status(a,m);work=old.work_counts(a,m)
# Every preservation result is independently observed again, with no command or write.
a.preserve(m);need(a.process(m["relay_before"]["pid"])==m["relay_before"],"RELAY_CHANGED_DURING_SNAPSHOT")
a.units(m);old.source_check(a,m);need(a.gate(m)==m["gate_before"],"GATE_CHANGED_DURING_SNAPSHOT")
need(a.ledger_snapshot(m,verify=False)=={"historical_queues":m["historical_queues"],"original_governance":m["original_governance"]},"LEDGERS_CHANGED_DURING_SNAPSHOT")
need(a.private_preservation(m,verify=False)==m["private_preservation"],"PRIVATE_PRESERVATION_CHANGED")
need(a.browser_snapshot(m,verify=False)==m["browser_inventory"],"BROWSER_INVENTORY_CHANGED")
need(b.source_timeout(m["relay_before"]["pid"])==m["hermes_transport"],"LOCAL_TRANSPORT_CHANGED")
after_other,after_owned=b.split_ruleset(b.read_ruleset());need(not after_owned and b.digest(after_other)==m["nft_before_sha256"],"FIREWALL_CHANGED_DURING_SNAPSHOT")
need(old.runtime_status(a,m)==runtime and old.work_counts(a,m)==work,"CURRENT_WORK_CHANGED_DURING_SNAPSHOT")
current_key=old.KEY.lstat();need((key.st_dev,key.st_ino,key.st_mode,key.st_uid,key.st_gid,key.st_nlink,key.st_size,key.st_mtime_ns,key.st_ctime_ns)==(current_key.st_dev,current_key.st_ino,current_key.st_mode,current_key.st_uid,current_key.st_gid,current_key.st_nlink,current_key.st_size,current_key.st_mtime_ns,current_key.st_ctime_ns),"KEY_METADATA_CHANGED")
old.operator(a,m)
m["observed_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat();m["live_hold_authorized"]=False;m["holder_sha256"]=OLD_SHA
changed=[name for name in prior if name not in {"observed_at","live_hold_authorized"}and prior[name]!=m[name]]
print(json.dumps({"schema_version":1,"state":"READ_ONLY_ORIGINAL_HOLDER_BASELINE_DRAFT","at":m["observed_at"],"prior_baseline_sha256":PRIOR_SHA,"holder_sha256":OLD_SHA,"manifest_without_native_binding":m,"changed_prior_fields":changed,"current_zero_work":work,"relay_runtime_counts":runtime,"signing_key_metadata":key_metadata,"operator_lease_expires_at":datetime.datetime.fromtimestamp(expiry,datetime.timezone.utc).isoformat(),"double_observation_stable":True,"native_live_preflight_performed":False,"current_native_apply_carrier":None,"closed_epoch":None,"holder_process":None,"shared_lock_keeper":None,"original_flock_witness":None,"signature":None,"root_writes":0,"barrier_actions":0,"service_actions":0,"browser_navigation_or_login":False,"raw_private_values_exported":False},sort_keys=True))

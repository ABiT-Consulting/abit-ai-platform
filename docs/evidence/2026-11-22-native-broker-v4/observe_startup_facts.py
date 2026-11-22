"""Read-only native broker startup facts; no key bodies or private config export."""
import datetime,hashlib,json,os,stat,subprocess
from pathlib import Path
assert os.getresuid()==(0,0,0) and os.environ.get("SUDO_USER")=="jimran@abitcons.com"
sha=lambda b:hashlib.sha256(b).hexdigest()
def properties(unit):
 r=subprocess.run(["/usr/bin/systemctl","show",unit,"--property=LoadState,ActiveState,SubState,UnitFileState,MainPID,FragmentPath"],capture_output=True,text=True,timeout=10,check=True)
 return dict(line.split("=",1)for line in r.stdout.splitlines()if "="in line)
def metadata(path):
 p=Path(path)
 try:s=p.lstat()
 except FileNotFoundError:return {"exists":False}
 return {"exists":True,"uid":s.st_uid,"gid":s.st_gid,"mode":oct(stat.S_IMODE(s.st_mode)),"bytes":s.st_size,"nlink":s.st_nlink,"kind":"directory"if stat.S_ISDIR(s.st_mode)else"socket"if stat.S_ISSOCK(s.st_mode)else"regular"if stat.S_ISREG(s.st_mode)else"other","symlink":stat.S_ISLNK(s.st_mode)}
def process(pid):
 p=Path("/proc")/str(pid)
 before=(p/"stat").read_text().rsplit(") ",1)[1].split()
 status={}
 for line in (p/"status").read_text().splitlines():
  k,sep,v=line.partition(":")
  if k in {"Uid","Gid"}:status[k]=[int(x)for x in v.split()]
 cmd=sha((p/"cmdline").read_bytes())
 after=(p/"stat").read_text().rsplit(") ",1)[1].split()
 assert before[19]==after[19] and before[0]not in {"Z","X"}
 return {"pid":pid,"start_ticks":int(before[19]),"uids":status["Uid"],"gids":status["Gid"],"cmdline_sha256":cmd,"boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
core=properties("abit-ai.service");peer=process(int(core["MainPID"]))
assert peer["pid"]==3481989 and peer["start_ticks"]==131898701
config_path=Path("/srv/abit-dev-linux/abit-ai/config/erp-assignment/resolver.json")
raw=config_path.read_bytes();config=json.loads(raw)
selected=config.get("agentic_native_verification")
safe=None if selected is None else {k:selected.get(k)for k in ("enabled","socket_path","key_file","projects")}
profile=((config.get("agentic_tool_gateway")or{}).get("projects")or{}).get("PROJ-0202")
profile_safe=None if not isinstance(profile,dict)else {k:profile.get(k)for k in ("host_id","environment_class","operations")}
root=Path("/var/lib/abit/native-verification/service")
registration_raw=(root/"registration.json").read_bytes();registration=json.loads(registration_raw)
sources={name:sha((root/name).read_bytes())for name in ("agentic_native_service.py","agentic_native_broker.py","agentic_native_contract.py")}
paths=[
 "/var/lib/abit/native-verification","/var/lib/abit/native-verification/gates","/var/lib/abit/native-verification/attempts",
 "/etc/abit/native-verification-broker","/etc/abit/native-verification-broker/key",
 "/run/abit-native-verification","/run/abit-native-verification/broker.sock",
 "/srv/abit-dev-linux/abit-ai/config/erp-assignment/native-verification-key"]
assert process(peer["pid"])==peer and properties("abit-ai.service")==core
print(json.dumps({"schema_version":1,"observed_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"core_unit":core,"core_peer":peer,"broker_unit":properties("abit-native-verification-broker.service"),"staged_source_sha256":sources,"registration_sha256":sha(registration_raw),"registration":registration,"configuration":{"path":str(config_path),"sha256":sha(raw),"native_lane":safe,"project_0202_gateway":profile_safe},"required_path_metadata":{p:metadata(p)for p in paths},"key_contents_read":False,"config_other_values_exported":False,"service_actions":0,"configuration_changes":0,"gate_or_authority_changes":0},sort_keys=True))

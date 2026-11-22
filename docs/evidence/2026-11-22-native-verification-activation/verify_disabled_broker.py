"""Read-only, fixed-target acceptance of the separately staged disabled broker."""
from pathlib import Path
import base64
import datetime
import hashlib
import json
import subprocess

HERE = Path(__file__).resolve().parent
INNER = r'''
from pathlib import Path
import base64,hashlib,json,os,stat,subprocess,types
p=Path("/srv/abit-dev-linux/abit-ai-native-verification-activation-20261122/deployment/linux/native-verification-activation-20261122/stage_broker.py")
data=p.read_bytes()
if hashlib.sha256(data).hexdigest()!="0a294f562ba944ab78ad513b176627a7c060afca23e7ae01cb2db1889f29a25a":
 raise RuntimeError("REVIEWED_READONLY_SOURCE_CHANGED")
a=types.ModuleType("disabled_broker_acceptance")
a.__file__=str(p)
exec(compile(data,str(p),"exec"),a.__dict__)
payload=a.inputs()
for target,(value,mode) in payload.items():
 a.owned_file(target,value,mode)
a.inactive()
a.default_off()
raw=a.read_regular(a.JOURNAL)
a.owned_file(a.JOURNAL,raw,0o600)
if a.digest(raw)!="a4c41ecb2e6151eaa34b472ea377ec1c011abb6c3d7b3b0456dea0d7fffa168e":
 raise RuntimeError("ORIGINAL_STAGING_RECEIPT_CHANGED")
# Preserve exact producer bytes and report its trailing terminator separately.
try:
 receipt=json.loads(raw)
 terminator="valid_json_whitespace"
except json.JSONDecodeError:
 if not raw.endswith(b"\\n"):
  raise
 receipt=json.loads(raw[:-2])
 terminator="literal_backslash_n_after_json"
if receipt.get("phase")!="staged_disabled":
 raise RuntimeError("STAGING_NOT_TERMINAL")
for key in ("service_started","daemon_reloaded","service_enabled","core_configuration_modified",
            "keys_created","runtime_socket_created","authority_issued","per_task_gate_created"):
 if receipt.get(key) is not False:
  raise RuntimeError("DEFAULT_OFF_PRODUCER_CLAIM_CHANGED")
absent={}
for target in (Path("/etc/abit/native-verification-broker/key"),
               Path("/run/abit-native-verification/broker.sock"),
               Path("/var/lib/abit/native-verification/gates")):
 absent[str(target)]=not os.path.lexists(target)
 if not absent[str(target)]:
  raise RuntimeError("UNEXPECTED_BROKER_AUTHORITY_OR_SOCKET")
status=subprocess.run(["/usr/bin/systemctl","show","abit-native-verification-broker.service",
                      "-p","ActiveState","-p","SubState","-p","MainPID","-p","UnitFileState"],
                      capture_output=True,text=True,timeout=10,check=False)
if status.returncode not in (0,4):
 raise RuntimeError("STATUS_READ_FAILED")
selected=dict(line.split("=",1) for line in status.stdout.splitlines() if "=" in line)
print(json.dumps({"source_manifest_sha256":a.SOURCE_MANIFEST_SHA,
 "staging_receipt_sha256":a.digest(raw),"staging_receipt_base64":base64.b64encode(raw).decode(),
 "receipt_terminator":terminator,"five_files":[{"path":str(target),"sha256":a.digest(value),
 "mode":oct(mode),"uid":0,"gid":0} for target,(value,mode) in sorted(payload.items())],
 "service":selected,"core_route_enabled":False,"fixed_paths_absent":absent,
 "service_actions_by_verifier":0,"writes_by_root_reader":0}))
'''
def main():
    before=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    child=subprocess.run(["sudo","-n","/usr/bin/python3","-I","-S","-B","-c",INNER],
                         capture_output=True,text=True,timeout=45,check=False)
    if child.returncode:
        raise RuntimeError("READ_ONLY_BROKER_ACCEPTANCE_FAILED:"+str(child.returncode)+":"+child.stderr[:500])
    result=json.loads(child.stdout)
    receipt=base64.b64decode(result.pop("staging_receipt_base64"),validate=True)
    evidence=HERE/"actual-activation"
    evidence.mkdir(exist_ok=True)
    targets={
      "original-default-off-broker-staging.json":receipt,
      "independent-disabled-broker-verification.json":
        (json.dumps({"schema_version":1,"captured_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
         "verifier_sha256":before,"result":result},sort_keys=True,indent=2)+"\n").encode()
    }
    for name,body in targets.items():
        path=evidence/name
        with path.open("xb") as stream: stream.write(body)
    if before!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise RuntimeError("VERIFIER_SOURCE_DRIFT")
    print(json.dumps({"passed":True,"files":5,"service":result["service"],"receipt_terminator":result["receipt_terminator"],
      "artifacts":{name:hashlib.sha256(body).hexdigest() for name,body in targets.items()}}))
if __name__=="__main__":
    main()

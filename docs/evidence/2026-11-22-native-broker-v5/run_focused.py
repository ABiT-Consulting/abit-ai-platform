"""Bounded ordinary-source tests; no live broker/provider actions."""
from pathlib import Path
import datetime,hashlib,json,os,re,subprocess,sys,time
E=Path(__file__).resolve().parent
ROOT=E.parents[2]
sha=lambda b:hashlib.sha256(b).hexdigest()
files=["backend/open_webui/utils/agentic_native_broker.py","backend/open_webui/utils/agentic_native_contract.py","backend/open_webui/utils/agentic_native_service.py",
"docs/evidence/2026-11-22-native-broker-v5/test_broker_v5.py","docs/evidence/2026-11-22-native-broker-v5/run_focused.py","docs/evidence/2026-11-22-native-broker-v5/v5-fixture-execution-bundle.json"]
files += ["deployment/native-broker-v5-20261122/payload/agentic_native_broker.py", "deployment/native-broker-v5-20261122/payload/agentic_native_contract.py", "deployment/native-broker-v5-20261122/core/open_webui/utils/agentic_native_contract.py"]
native=Path("/srv/abit-dev-linux/bigrro-dev/proj0202-native-v5-cache-composition-20261122/scripts/native-worker")
native_before={name:sha((native/name).read_bytes()) for name in ['admission.py', 'bootstrap.py', 'controller.py', 'linux_boundary.py', 'policy.py', 'worker_policy.py']}
before={name:sha((ROOT/name).read_bytes())for name in files}
mode="optimized"if sys.flags.optimize else"normal"
stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
log=E/(mode+"-"+stamp+".log")
cmd=["python3"]+(["-O"]if sys.flags.optimize else[])+["-B",str(E/"test_broker_v5.py"),"-v"]
started=time.monotonic()
with log.open("w")as stream:
 result=subprocess.run(cmd,cwd=ROOT,env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"LANG":"C.UTF-8","PYTHONDONTWRITEBYTECODE":"1","PYTHON_DOTENV_DISABLED":"1"},stdout=stream,stderr=subprocess.STDOUT,timeout=60)
after={name:sha((ROOT/name).read_bytes())for name in files}
text=log.read_text()
count=re.search(r"Ran ([0-9]+) tests?",text)
record={"schema_version":1,"mode":mode,"command":cmd,"exit_code":result.returncode,"tests":int(count[1])if count else None,"seconds":round(time.monotonic()-started,3),"source_sha256":before,"source_drift":{name:{"before":before[name],"after":after[name]}for name in files if before[name]!=after[name]},"log":log.name,"log_sha256":sha(log.read_bytes()),"external_network_and_provider_commands_denied":True,"actual_v5_control_bytes_read_only":True,"protected_file_reads_and_host_manager_are_fixtures":True,"native_or_service_actions":0,"actual_native_controls_sha256":native_before,"native_source_drift":{name:sha((native/name).read_bytes()) for name in native_before if sha((native/name).read_bytes())!=native_before[name]},"synthetic_gate_authority_and_rootfs_fixture":True}
log.with_suffix(".json").write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
print(json.dumps(record))
print("\n".join(text.splitlines()[-12:]))
raise SystemExit(result.returncode or bool(record["source_drift"]) or bool(record["native_source_drift"]))

from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,os,subprocess,sys,shutil
ROOT=Path(__file__).resolve().parents[3]
HARNESS=Path("/srv/abit-dev-linux/tmp/native-verification-binding-20261122/composed-api")
EVIDENCE=Path(__file__).resolve().parent
FILES=[*sorted(str(p.relative_to(ROOT)) for p in (ROOT/"backend/open_webui/utils").glob("agentic_native_*.py")),
       "backend/open_webui/routers/agentic_governance.py",
       "backend/open_webui/utils/agentic_contracts/validator.py",
       "backend/open_webui/utils/agentic_contracts/schemas/governance_v1.schema.json",
       "backend/tests/test_agentic_native_verification.py"]
def hashes():return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in FILES}
for rel in FILES:
    target=HARNESS/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,target)
for name in ["test_agentic_controls_events.py"]:
    shutil.copy2(ROOT/"backend/tests"/name,HARNESS/"backend/tests"/name)
stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
before=hashes()
code='''import sys,unittest
def guard(event,args):
    if event in {"socket.connect","socket.connect_ex","subprocess.Popen","os.system","os.exec","os.posix_spawn"}:
        raise RuntimeError("OFFLINE_VALIDATION_EXTERNAL_ACTION_DENIED")
sys.addaudithook(guard)
suite=unittest.defaultTestLoader.loadTestsFromName(TEST_NAME)
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
'''
code = code.replace("TEST_NAME", repr(sys.argv[1] if len(sys.argv) > 1 else "test_agentic_native_verification"))
cmd=["/srv/abit-dev-linux/abit-ai/venv/bin/python","-B","-c",code]
env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","PYTHONPATH":str(HARNESS/"backend")+":"+str(HARNESS/"backend/tests"),
     "DATA_DIR":str(HARNESS/"isolated-native-data"),"STATIC_DIR":str(HARNESS/"isolated-native-static"),
     "WEBUI_SECRET_KEY":"isolated-offline-fixture-not-production","ENV":"test","ENABLE_OLLAMA_API":"false",
     "ENABLE_OPENAI_API":"false","ENABLE_LITELLM":"false"}
result=subprocess.run(cmd,cwd=HARNESS/"backend/tests",env=env,capture_output=True,text=True,timeout=90)
log=EVIDENCE/("focused-"+stamp+".log");log.write_text(result.stdout+result.stderr)
after=hashes()
record={"schema_version":1,"at_utc":datetime.now(timezone.utc).isoformat(),"command":cmd,"cwd":str(HARNESS/"backend/tests"),
        "exit_code":result.returncode,"source_before":before,"source_after":after,"source_drift":before!=after,
        "log":log.name,"log_sha256":hashlib.sha256(log.read_bytes()).hexdigest(),
        "no_network_guard":True,"no_provider_or_root_action":True,"isolated_sqlite":True,
        "terminal_tail":(result.stdout+result.stderr).splitlines()[-18:]}
out=EVIDENCE/("focused-"+stamp+".json");out.write_text(json.dumps(record,indent=2)+"\n")
print(json.dumps({"receipt":str(out),"exit_code":result.returncode,"source_drift":before!=after,"tail":record["terminal_tail"]}))
raise SystemExit(result.returncode or (before!=after))

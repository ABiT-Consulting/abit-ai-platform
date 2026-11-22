from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,sys,time
E=Path(__file__).resolve().parent
ROOT=E.parents[2]
paths=[ROOT/"deployment/native-overlay-original-finalize-20261122/finalize_original.py",E/"test_finalize_original.py"]
def hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
mode=sys.argv[1] if len(sys.argv)>1 else "normal"
if mode not in ("normal","optimized"):raise SystemExit("FIXED_MODE")
stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
before=hashes();argv=[sys.executable,"-B"]+(["-O"] if mode=="optimized" else [])+[str(E/"test_finalize_original.py")]
start=time.monotonic()
result=subprocess.run(argv,cwd=ROOT,env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","PYTHONDONTWRITEBYTECODE":"1"},capture_output=True,timeout=60)
log=E/(mode+"-"+stamp+".log");log.write_bytes(result.stdout+result.stderr)
text=log.read_text();import re
match=re.search(r"Ran (\d+) tests? in",text)
record={"mode":mode,"argv":argv,"exit_code":result.returncode,"tests_run":int(match.group(1)) if match else None,
        "passed":result.returncode==0,"seconds":round(time.monotonic()-start,3),"source_hashes":before,
        "source_drift":before!=hashes(),"log":log.name,"log_sha256":hashlib.sha256(log.read_bytes()).hexdigest(),
        "live_actions":False,"kernel_firewall_or_service_execution":False}
log.with_suffix(".json").write_text(json.dumps(record,indent=2)+"\n")
print(json.dumps(record));print("\n".join(text.splitlines()[-80:]))
raise SystemExit(result.returncode or record["source_drift"])

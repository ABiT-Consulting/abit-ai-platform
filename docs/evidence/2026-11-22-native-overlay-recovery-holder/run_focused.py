"""Bounded ordinary recovery-holder tests. No service, native, or network action."""
from pathlib import Path
import ast,datetime,hashlib,json,os,re,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[3]
HERE=ROOT/"deployment/native-overlay-recovery-holder-20261122"
E=Path(__file__).resolve().parent
STAMP=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
def hashes():
 return {p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in sorted(HERE.glob("*.py"))}
before=hashes()
for p in HERE.glob("*.py"):ast.parse(p.read_text())
failed=False
for name,cmd in (("normal",["python3","-B","test_hold_native_recovery.py"]),("optimized",["python3","-O","-B","test_hold_native_recovery.py"])):
 start=time.monotonic()
 env={k:os.environ[k]for k in ("PATH","LANG")if k in os.environ}
 env.update(PYTHONDONTWRITEBYTECODE="1",PYTHON_DOTENV_DISABLED="1",OFFLINE_MODE="True")
 out=subprocess.run(cmd,cwd=HERE,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=45)
 log=E/(name+"-focused-"+STAMP+".log")
 with log.open("xb")as f:f.write(out.stdout)
 text=out.stdout.decode("utf-8","replace")
 count=re.search(r"Ran (\d+) tests? in",text)
 row={"schema_version":1,"command":cmd,"exit_code":out.returncode,"tests":int(count[1])if count else None,
      "source_hashes":before,"source_drift":before!=hashes(),"seconds":round(time.monotonic()-start,3),
      "log":log.name,"log_sha256":hashlib.sha256(out.stdout).hexdigest(),
      "network_guard":True,"ordinary_kernel_flock_fixture":True,"native_or_service_actions":False,
      "root_systemd_native_implementation_executed":False}
 with log.with_suffix(".json").open("x")as f:json.dump(row,f,sort_keys=True,indent=2);f.write("\n")
 print(json.dumps(row,sort_keys=True))
 if out.returncode:print(text[-10000:]);failed=True
 if row["source_drift"]:failed=True
raise SystemExit(1 if failed else 0)

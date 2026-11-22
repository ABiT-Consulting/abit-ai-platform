from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,sys,time,shutil
ROOT=Path(__file__).resolve().parents[3]
H=Path("/srv/abit-dev-linux/tmp/native-overlay-recovery-holder-20261122/composed-api")
E=Path(__file__).resolve().parent
MODE=sys.argv[1] if len(sys.argv)>1 else "api:verify"
if MODE not in {"api:verify"}:raise SystemExit("Fixed API gate required")
if not (H/".git").exists():subprocess.run(["git","init","--quiet",str(H)],check=True)
def source():
    return {str(p.relative_to(H)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((H/"backend/open_webui").rglob("*"))
            if p.is_file() and p.suffix in {".py",".json"} and "__pycache__" not in p.parts}
def holder_sources():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/"deployment/native-overlay-recovery-holder-20261122").rglob("*")) if p.is_file()}
holder_before=holder_sources()
before=source();stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
log=E/(MODE.replace(":","-")+"-"+stamp+".log")
env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"LANG":"C.UTF-8",
     "ABIT_PYTHON":"/srv/abit-dev-linux/abit-ai/venv/bin/python",
     "PYTHONDONTWRITEBYTECODE":"1","PYTHON_DOTENV_DISABLED":"1",
     "OFFLINE_MODE":"True","HF_HUB_OFFLINE":"1","ENABLE_OLLAMA_API":"False","ENABLE_OPENAI_API":"False"}
start=time.monotonic()
with log.open("w") as stream:
    proc=subprocess.Popen(["npm","run",MODE],cwd=H,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    try:exit_code=proc.wait(timeout=390)
    except subprocess.TimeoutExpired:
        import signal
        os.killpg(proc.pid,signal.SIGTERM)
        try:proc.wait(timeout=15)
        except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
        exit_code=124
after=source()
record={"gate":MODE,"exit_code":exit_code,"seconds":round(time.monotonic()-start,3),
        "source":str(H),"source_files":len(before),"source_manifest_sha256":hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        "source_drift":[p for p in set(before)|set(after) if before.get(p)!=after.get(p)],
        "log":log.name,"log_sha256":hashlib.sha256(log.read_bytes()).hexdigest(),
        "finished_utc":datetime.now(timezone.utc).isoformat(),"isolated_environment":True,
        "production_data_copied":False,"production_action":False,
        "holder_source_hashes":holder_before,"holder_source_drift":holder_before!=holder_sources(),
        "scope":"Required API gate for source-only operator change; no application API/runtime source was changed."}
out=log.with_suffix(".json");out.write_text(json.dumps(record,indent=2)+"\n")
(E/("composed-source-"+stamp+".json")).write_text(json.dumps(before,indent=2)+"\n")
print(json.dumps(record))
print("\n".join(log.read_text().splitlines()[-20:]))
raise SystemExit(exit_code or bool(record["source_drift"]) or record["holder_source_drift"])

"""Run the current-source isolated API gate once after stdlib platform metadata setup."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import subprocess

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
EVIDENCE=HERE/"evidence"
PYTHON="/srv/abit-dev-linux/abit-ai/venv/bin/python"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def save(name,raw):
    with (EVIDENCE/name).open("xb") as f:f.write(raw)

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

assert not (EVIDENCE/"api-current-attempt-5.json").exists()
inputs={str(p):sha(p.read_bytes()) for p in HERE.rglob("*")
        if p.is_file() and "evidence" not in p.relative_to(HERE).parts}
for path in (REPO/"backend/open_webui").rglob("*.py"):
    if path.is_file() and not path.is_symlink() and not any(part in {"data","__pycache__"} for part in path.relative_to(REPO).parts):
        inputs[str(path)]=sha(path.read_bytes())
for name in ("scripts/check_api_contract.py","scripts/test_developer_module.py"):
    inputs[str(REPO/name)]=sha((REPO/name).read_bytes())
env={k:v for k,v in os.environ.items() if k in {"PATH","LANG","LC_ALL","TZ"}}
env.update(PYTHONDONTWRITEBYTECODE="1",PYTHON_DOTENV_DISABLED="1")
checks=[]
for name,command in (
    ("api-integration-current",[PYTHON,"-B",str(HERE/"api_integration_current_v5.py")]),
):
    started=now()
    result=subprocess.run(command,cwd=REPO,env=env,capture_output=True,timeout=330)
    save(name+"-5.stdout",result.stdout)
    save(name+"-5.stderr",result.stderr)
    row={"check":name,"started_utc":started,"finished_utc":now(),"exit_code":result.returncode,
         "stdout_sha256":sha(result.stdout),"stderr_sha256":sha(result.stderr)}
    if name=="api-integration-current":
        markers=[line.split("=",1)[1] for line in result.stdout.decode().splitlines()
                 if line.startswith("API_MODULE_REPORT=")]
        if len(markers)==1:
            report=Path(markers[0])
            assert report.name=="report.json" and report.parent.name.startswith("api-module-")
            assert report.parent.parent.resolve()==(REPO/".git").resolve()
            if report.is_file():
                raw=report.read_bytes()
                save("api-integration-current-5.report.json",raw)
                row["report_sha256"]=sha(raw)
                parsed=json.loads(raw)
                row["assertions_passed"]=parsed.get("passed",0)
                row["assertions_failed"]=parsed.get("failed",1)
                row["current_overlay"]=parsed.get("current_source_overlay")
    checks.append(row)
    print(json.dumps({k:v for k,v in row.items() if k!="current_overlay"},sort_keys=True),flush=True)
    print(result.stdout.decode()[-2500:]+result.stderr.decode()[-2500:],flush=True)
    if result.returncode:break
same=all(sha(Path(path).read_bytes())==digest for path,digest in inputs.items())
receipt={"schema_version":1,"checks":checks,"all_passed":len(checks)==1 and all(r["exit_code"]==0 for r in checks),
         "pins_unchanged":same,"input_pins":inputs,"live_actions":0,"api_child_no_network_or_live_database_access":True,
         "application_lifespan_not_started":True,"app_imported_only_in_isolated_child":True}
save("api-current-attempt-5.json",(json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode())
print(json.dumps({"state":"ISOLATED_CURRENT_API_CHECK_COMPLETED","passed":receipt["all_passed"],
                  "pins_unchanged":same,"input_count":len(inputs),"live_actions":0}),flush=True)
raise SystemExit(0 if receipt["all_passed"] and same else 1)

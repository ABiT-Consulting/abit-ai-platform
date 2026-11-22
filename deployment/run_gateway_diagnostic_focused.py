from pathlib import Path
import datetime,hashlib,json,os,subprocess,sys,time
WORK=Path('/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122')
HARNESS=Path('/srv/abit-dev-linux/tmp/gateway-preflight-diagnostics-20261122/composed-api')
EVIDENCE=WORK/'docs/evidence/2026-11-22-gateway-preflight'
EVIDENCE.mkdir(parents=True,exist_ok=True)
label=sys.argv[1]
modules=sys.argv[2:]
assert label.replace('-','').isalnum() and modules and all(x.startswith('test_') and '/'not in x for x in modules)
manifest=json.loads((WORK/'deployment/gateway-preflight-diagnostics-20261122/manifest.json').read_text())
sha=lambda b:hashlib.sha256(b).hexdigest()
sources=[]
for f in manifest['runtime_files']:
 p=HARNESS/'backend/open_webui'/f['path'];actual=sha(p.read_bytes());assert actual==f['after_sha256']
 sources.append({'path':f['path'],'sha256':actual})
command=['/srv/abit-dev-linux/abit-ai/venv/bin/python','-B','-m','unittest','-v',*modules]
env={'PATH':'/usr/local/bin:/usr/bin:/bin','PYTHON_DOTENV_DISABLED':'1','PYTHONDONTWRITEBYTECODE':'1','NO_COLOR':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'}
log=EVIDENCE/(label+'.log');assert not log.exists()
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
with log.open('x')as output:
 result=subprocess.run(command,cwd=HARNESS/'backend/tests',env=env,stdout=output,stderr=subprocess.STDOUT,timeout=300)
after=[f['path']for f in manifest['runtime_files']if sha((HARNESS/'backend/open_webui'/f['path']).read_bytes())!=f['after_sha256']]
text=log.read_text()
report={'command':command,'cwd':str(HARNESS/'backend/tests'),'started_utc':started,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':result.returncode,'runtime_sources':sources,'source_drift_after':after,'log':str(log),'log_sha256':sha(log.read_bytes()),'test_source_hashes':{name:sha((HARNESS/'backend/tests'/(name+'.py')).read_bytes())for name in sorted({item.split('.')[0] for item in modules})}}
(EVIDENCE/(label+'.json')).write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps(report))
print(text[-16000:])
raise SystemExit(result.returncode)

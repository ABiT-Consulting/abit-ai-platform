import datetime,hashlib,json,os,pathlib,subprocess,sys,time
root=pathlib.Path('/srv/abit-dev-linux/abit-ai-platform')
out=root/'.git/native7549-projection-validation'
owned=[
 'deployment/linux/core-command-capacity-20261115/project_native7549.py',
 'deployment/linux/core-command-capacity-20261115/test_native7549_projection.py',
 'deployment/linux/core-command-capacity-20261115/fixtures/native7549-inputs.json',
 'deployment/linux/core-command-capacity-20261115/apply_command_capacity.py',
 'deployment/linux/core-command-capacity-20261115/preserved_original.py',
 'backend/open_webui/utils/agentic_contracts/developer_delivery.py',
 'backend/open_webui/utils/agentic_contracts/schemas/governance_v1.schema.json',
]
pins=lambda:{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in owned}
before=pins();started=datetime.datetime.now(datetime.timezone.utc).isoformat();clock=time.monotonic()
space=os.statvfs(root);available=space.f_bavail*space.f_frsize
assert available>1024**3+64*1024**2,'API_VERIFY_DISK_RESERVE'
log=out/'api-verify-venv.log'
with log.open('x') as stream:
    result=subprocess.run(['npm','run','api:verify'],cwd=root,stdout=stream,stderr=subprocess.STDOUT,timeout=480,env={**os.environ,'ABIT_PYTHON':'/srv/abit-dev-linux/abit-ai/venv/bin/python','PYTHONDONTWRITEBYTECODE':'1'})
after=pins()
record={'schema_version':1,'command':['npm','run','api:verify'],'started_utc':started,
 'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-clock,
 'returncode':result.returncode,'source_before':before,'source_after':after,'source_unchanged':before==after,
 'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'available_bytes_before':available,
 'supported_interpreter_override':{'ABIT_PYTHON':'/srv/abit-dev-linux/abit-ai/venv/bin/python','PYTHONDONTWRITEBYTECODE':'1'},'integration_not_run':'No backend route/model/auth/export/runtime source was changed by this deployment-tool increment.'}
(out/'api-verify-venv.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
print(json.dumps({k:record[k] for k in ['returncode','elapsed_seconds','source_unchanged','log_sha256']}))
raise SystemExit(result.returncode)

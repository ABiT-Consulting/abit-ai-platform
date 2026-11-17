from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, subprocess, sys
ROOT=Path(__file__).resolve().parents[2]
E=Path(__file__).resolve().parent
mode=sys.argv[1]
assert mode in ('api-verify','api-integration')
manifest=E/'source-manifest.json';bound=json.loads(manifest.read_text())
def checks():
    return all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==pin['sha256'] for name,pin in bound['pins'].items())
assert checks(), 'Frozen source drifted before API job'
argv=['npm','run','api:verify' if mode=='api-verify' else 'api:verify:integration']
env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL'}}
env.update(ABIT_PYTHON='/srv/abit-dev-linux/abit-ai/venv/bin/python',PYTHONDONTWRITEBYTECODE='1',PYTHON_DOTENV_DISABLED='1')
started=datetime.now(timezone.utc).isoformat()
log=E/(mode+'.log')
with log.open('xb') as stream:
    run=subprocess.run(argv,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=420)
after=checks()
receipt={'schema_version':1,'started_at_utc':started,'finished_at_utc':datetime.now(timezone.utc).isoformat(),
 'command':argv,'head_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
 'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'pinned_sources_unchanged_before':True,
 'pinned_sources_unchanged_after':after,'exit_code':run.returncode,
 'log':{'path':str(log.relative_to(ROOT)),'sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'bytes':log.stat().st_size},
 'scope':'Required repository API gate. Integration retains OS-only child environment, fresh data/static fixtures, disabled providers, ASGI without lifespan, external socket denial and cleanup. No runtime/governance action.'}
p=E/(mode+'-receipt.json');p.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps({'receipt':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
 'exit_code':run.returncode,'source_pins_unchanged':after}))
raise SystemExit(run.returncode if run.returncode else 0 if after else 2)

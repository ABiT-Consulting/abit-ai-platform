"""Reuse the accepted isolated Core API inputs; copy no live data or credentials."""
from pathlib import Path
import datetime
import hashlib
import json
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EVIDENCE = ROOT / 'docs/evidence/2026-11-22-restored-target-activation'
PRIOR = Path('/srv/abit-dev-linux/tmp/original-restored-target-api-20261122')
DESTINATION = Path('/srv/abit-dev-linux/tmp/restored-target-activation-api-20261122')
sha = lambda data: hashlib.sha256(data).hexdigest()
manifest_bytes = (PRIOR/'source-manifest.json').read_bytes()
assert sha(manifest_bytes) == '99b8fb92f18688761e34e08dca708931ad8b2a5b10299c62410ad25e614af5ab'
assert not DESTINATION.exists(), 'Fresh owned isolated API directory required'
DESTINATION.mkdir(mode=0o700)
target = DESTINATION/'source'
target.mkdir(mode=0o700)
prior = json.loads(manifest_bytes)
records = {}
for row in prior['files']:
    source = PRIOR/'source'/row['path']
    assert source.is_file() and not source.is_symlink()
    data = source.read_bytes()
    assert sha(data) == row['sha256'], 'Accepted input drift: '+row['path']
    destination = target/row['path']
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination.write_bytes(data)
    records[row['path']] = {'path':row['path'],'sha256':sha(data),'input':str(source)}
for row in json.loads((HERE/'runtime/manifest.json').read_text())['runtime_files']:
    relative = 'deployment/windows/aibit-relay/'+row['path']
    source = HERE/'runtime/payload'/row['path']
    data = source.read_bytes()
    assert sha(data) == row['after_sha256']
    (target/relative).write_bytes(data)
    records[relative] = {'path':relative,'sha256':sha(data),'input':str(source)}
relative='docs/api-module.md'
source=ROOT/relative
(target/relative).parent.mkdir(parents=True,exist_ok=True)
(target/relative).write_bytes(source.read_bytes())
records[relative]={'path':relative,'sha256':sha(source.read_bytes()),'input':str(source)}
assert shutil.disk_usage(DESTINATION).free > 4*1024**3
subprocess.run(['cp','-a','--reflink=auto',str(PRIOR/'source/node_modules'),str(target/'node_modules')],
               check=True,timeout=120)
subprocess.run(['git','init','--quiet',str(target)],check=True)
for row in records.values():
    assert sha((target/row['path']).read_bytes()) == row['sha256']
manifest={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'source':str(target),'prior_source_manifest_sha256':sha(manifest_bytes),
          'runtime_manifest_sha256':sha((HERE/'runtime/manifest.json').read_bytes()),
          'files':list(records.values()),'scope':'Current accepted Core plus three-file relay lifecycle/diagnostic; isolated API gate'}
encoded=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
(DESTINATION/'source-manifest.json').write_bytes(encoded)
(EVIDENCE/'api-source-manifest.json').write_bytes(encoded)
(EVIDENCE/'empty-user.npmrc').write_text('')
(EVIDENCE/'empty-global.npmrc').write_text('')
(EVIDENCE/'.gitignore').write_text('npm-cache/\n')
runner=(ROOT/'deployment/run_original_target_probe_api.py').read_text()
runner=runner.replace('/srv/abit-dev-linux/tmp/original-restored-target-api-20261122',
                      str(DESTINATION))
runner=runner.replace('/srv/abit-dev-linux/abit-ai-original-restored-target-diagnostic-20261122/docs/evidence/2026-11-22-restored-target-diagnostic',
                      str(EVIDENCE))
runner=runner.replace('99b8fb92f18688761e34e08dca708931ad8b2a5b10299c62410ad25e614af5ab',sha(encoded))
runner=runner.replace('two-file private relay diagnostic source','three-file relay lifecycle and restored-target diagnostic')
(HERE/'run_api.py').write_text(runner)
print(json.dumps({'source':str(target),'files':len(records),'manifest_sha256':sha(encoded)}))

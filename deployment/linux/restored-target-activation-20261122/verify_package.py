"""Exact package reconstruction and affected original-diagnostic regressions."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
E=ROOT/'docs/evidence/2026-11-22-restored-target-activation'
PACKAGE=HERE/'runtime'
sha=lambda data:hashlib.sha256(data).hexdigest()
manifest=json.loads((PACKAGE/'manifest.json').read_text())
stage=Path(tempfile.mkdtemp(prefix='restored-target-activation-package-',dir='/srv/abit-dev-linux/tmp'))
runtime=stage/'source'
runtime.mkdir()
for row in json.loads((ROOT/'deployment/original-restored-target-diagnostic-20261122/inputs.json').read_text())['inputs']:
    data=(ROOT/row['copy']).read_bytes()
    if sha(data)!=row['sha256']:raise RuntimeError('Frozen diagnostic input drift')
    (runtime/row['name']).write_bytes(data)
for row in manifest['runtime_files']:
    data=(PACKAGE/'payload'/row['path']).read_bytes()
    if sha(data)!=row['after_sha256']:raise RuntimeError('Composed payload drift')
    (runtime/row['path']).write_bytes(data)
test=ROOT/'deployment/windows/aibit-relay/tests/original_dot_target_probe.test.cjs'
env={'PATH':'/usr/local/bin:/usr/bin:/bin','NODE_PATH':'/srv/abit-dev-linux/abit-ai-platform/node_modules',
     'RELAY_DIAGNOSTIC_SOURCE':str(runtime)}
commands=[['/usr/local/bin/node','--check',str(runtime/row['path'])] for row in manifest['runtime_files']]
commands.append(['/usr/local/bin/node','--test','--test-reporter=tap',str(test)])
stamp=str(int(time.time()))
log=E/('diagnostic-composition-'+stamp+'.log')
results=[]
with log.open('x') as stream:
    for command in commands:
        r=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=50)
        stream.write('$ '+' '.join(command)+'\n'+r.stdout+r.stderr+'\n')
        results.append({'command':command,'exit_code':r.returncode})
        if r.returncode:break
text=log.read_text()
counts={key:int(match.group(1)) if (match:=re.search(r'^# '+key+r' (\d+)$',text,re.M)) else None
        for key in ['tests','pass','fail','cancelled','skipped']}
receipt={'commands':results,'counts':counts,'stage':str(stage),'test_sha256':sha(test.read_bytes()),
         'manifest_sha256':sha((PACKAGE/'manifest.json').read_bytes()),
         'runtime_sources':{p.name:sha(p.read_bytes()) for p in sorted(runtime.iterdir())},
         'log':str(log.relative_to(ROOT)),'no_live_actions':True}
(E/('diagnostic-composition-'+stamp+'.json')).write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps(receipt))
if any(row['exit_code'] for row in results):
    print(text[-12000:])
    raise SystemExit(1)
reconstruction=stage/'reconstruction'
reconstruction.mkdir()
for row in manifest['runtime_files']:
    if row['before_sha256'] is not None:
        data=(PACKAGE/'preimages'/row['path']).read_bytes()
        if sha(data)!=row['before_sha256']:raise RuntimeError('Packaged preimage drift')
        (reconstruction/row['path']).write_bytes(data)
roundtrip=[]
for patch_name,field in [('forward.patch','after_sha256'),('rollback.patch','before_sha256')]:
    data=(PACKAGE/patch_name).read_bytes()
    if sha(data)!=manifest[patch_name.replace('.patch','_patch_sha256')]:raise RuntimeError('Patch identity differs')
    r=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(PACKAGE/patch_name)],
                     cwd=reconstruction,capture_output=True,text=True,timeout=10)
    if r.returncode:raise RuntimeError('Exact scratch patch application failed')
    observations=[]
    for row in manifest['runtime_files']:
        path=reconstruction/row['path']
        actual=sha(path.read_bytes()) if path.exists() else None
        if actual!=row[field]:raise RuntimeError('Scratch postimage differs')
        observations.append({'path':row['path'],'sha256':actual})
    roundtrip.append({'patch':patch_name,'exit_code':r.returncode,'observations':observations})
result={'manifest_sha256':sha((PACKAGE/'manifest.json').read_bytes()),'scope':'disposable scratch reconstruction only',
        'source':str(reconstruction),'roundtrip':roundtrip}
(E/'patch-roundtrip.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps(result))

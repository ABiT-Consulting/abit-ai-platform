from pathlib import Path
import datetime,hashlib,json,os,shutil,stat,subprocess
os.umask(0o077)
root=Path('/srv/abit-dev-linux/tmp/gateway-preflight-diagnostics-20261122')
assert not root.exists(), 'Owned validation root already exists'
root.mkdir(mode=0o700)
target=root/'composed-api'
assert not target.exists(),'Create once'
target.mkdir(mode=0o700)
live=Path('/srv/abit-dev-linux/abit-ai/current').resolve()/ 'runtime/open_webui'
assert str(live)=='/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui'
harness=Path('/srv/abit-dev-linux/tmp/core-priority-recovery-api-20261121T2010')
prior=Path('/srv/abit-dev-linux/tmp/intermarket-pr23-pr25-release-preparation-20261122')
sha=lambda b:hashlib.sha256(b).hexdigest()
assert sha((live/'utils/agentic_transport_retry.py').read_bytes())=='8ac1970ddd09e60e42fb93620ade69a67cb4ca1bb8076d951cb0ea01366fdf9f'
assert sha((live/'routers/agentic_governance.py').read_bytes())=='85dbbe0a6d5ead08796b830eba212541016f1f790630ff6ddbcf8829bf6edefc'
candidate=(prior/'core-minimal/agentic_transport_retry.candidate.py').read_bytes()
assert sha(candidate)=='bf813b34e602017086db6859574e60d7c3e094c6ffb83442a42e75ab78dcbfc3'
manifest=[]
def copy(p,d,kind):
 assert p.is_file() and not p.is_symlink()
 b=p.read_bytes();assert len(b)<=5*1024*1024
 d.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 d.write_bytes(b);d.chmod(0o600)
 manifest.append({'source':str(p),'snapshot':str(d.relative_to(target)),'sha256':sha(b),'bytes':len(b),'kind':kind})
sources=[p for p in sorted(live.rglob('*.py')) if not any(x in p.relative_to(live).parts for x in ['static','frontend','__pycache__'])]
assert len(sources)==373
for p in sources:copy(p,target/'backend/open_webui'/p.relative_to(live),'installed-python')
for relative in ['alembic.ini','migrations/script.py.mako','utils/agentic_contracts/provenance.json','utils/agentic_contracts/schemas/governance_v1.schema.json','utils/agentic_contracts/schemas/solution_architect_plan.schema.json','CHANGELOG.md']:
 copy(live/relative,target/'backend/open_webui'/relative,'installed-package-resource')
for name in ['scripts','src']:
 for p in sorted((harness/name).rglob('*')):
  if p.is_file() and '__pycache__' not in p.parts:copy(p,target/p.relative_to(harness),'existing-API-harness')
for p in sorted((harness/'backend/tests').glob('test_developer*.py')):copy(p,target/p.relative_to(harness),'existing-API-harness')
copy(harness/'backend/tests/fixtures/slack_ui_before_api_contract.py.txt',target/'backend/tests/fixtures/slack_ui_before_api_contract.py.txt','existing-API-harness')
for relative in ['AGENTS.md','package.json','package-lock.json','svelte.config.js','tsconfig.json','tsconfig.developers.json','vite.config.ts','vite.developers.config.ts','tailwind.config.js','postcss.config.js','backend/requirements.txt','docs/api-contract-policy.json']:
 copy(harness/relative,target/relative,'existing-API-harness')
work=Path('/srv/abit-dev-linux/abit-ai-gateway-preflight-diagnostics-20261122')
package=work/'deployment/gateway-preflight-diagnostics-20261122'
release_manifest=json.loads((package/'manifest.json').read_text())
for item in release_manifest['runtime_files']:
 source=package/'runtime/open_webui'/item['path']
 assert sha(source.read_bytes())==item['after_sha256']
 (target/'backend/open_webui'/item['path']).write_bytes(source.read_bytes())
# Copy the existing affected fixture closure without importing its modules.
import ast
pending={'test_agentic_controls_events','test_agentic_relay_scope','test_agentic_gateway_preflight_diagnostics','test_transport_retry_accepted'}
seen=set()
while pending:
 name=pending.pop()
 if name in seen:continue
 seen.add(name)
 source=work/'backend/tests'/(name+'.py')
 copy(source,target/'backend/tests'/(name+'.py'),'focused-test-fixture')
 for node in ast.walk(ast.parse(source.read_text())):
  if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('test_'):
   pending.add(node.module)
  elif isinstance(node,ast.Import):
   pending.update(alias.name for alias in node.names if alias.name.startswith('test_'))

# Installed source capture is read twice; source-only bytes and package resources only.
drift=[x['source'] for x in manifest if sha(Path(x['source']).read_bytes())!=x['sha256']]
assert not drift
# Independent dependency copy prevents cache writes into other active worktrees.
deps=(harness/'node_modules').resolve(strict=True)
assert shutil.disk_usage(target).free>4*1024*1024*1024
subprocess.run(['cp','-a','--reflink=auto',str(deps),str(target/'node_modules')],check=True,timeout=90)
subprocess.run(['git','init','--quiet',str(target)],check=True,timeout=10)
tooling={k:sha((target/k).read_bytes()) for k in ['scripts/check-api.mjs','scripts/check_api_contract.py','scripts/test_developer_module.py','docs/api-contract-policy.json','package.json','package-lock.json']}
report={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'target':str(target),'live_source':str(live),'harness_source':str(harness),'installed_python_count':len(sources),'files':manifest,'candidate_runtime_delta':[x['path'] for x in release_manifest['runtime_files']],'candidate_sha256':sha(candidate),'source_unchanged_after_capture':not drift,'tooling':tooling,'scope':'Actual installed Python source plus reviewed PR25 guard and proof-only gateway diagnostics; existing reviewed API harness; isolated synthetic state; no application lifespan/provider/SAP/live data','dependencies':'Existing installed node modules copied without network install'}
p=root/'composed-api-source-manifest.json';p.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps({'manifest':str(p),'sha256':sha(p.read_bytes()),'source_count':len(sources),'total_manifest_files':len(manifest),'tooling':tooling,'target':str(target)}))

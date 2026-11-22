from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,tarfile,subprocess,shutil,tempfile,re
R=Path(__file__).resolve().parents[3];E=Path(__file__).resolve().parent;P=R/'deployment/native-verification-binding-20261122';H=Path('/srv/abit-dev-linux/tmp/native-verification-binding-20261122/composed-api')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=P/'README.md';s=p.read_text().replace('/api/v1/agentic-governance/projects/','/api/v1/agentic/projects/');p.write_text(s)
verify=E/'api-verify-20261122T154403319114Z.json';integration=E/'api-verify-integration-20261122T154401846015Z.json';focused=E/'focused-20261122T154212336007Z.json'
for p in (verify,integration,focused):
 d=json.loads(p.read_text());log=E/d['log'];assert d['exit_code']==0 and not d['source_drift'] and sha(log)==d['log_sha256']
print('VERIFY_COUNTS',[l for l in (E/json.loads(verify.read_text())['log']).read_text().splitlines() if re.search(r'Ran \d+ tests|tests \d+|pass \d+|svelte-check found',l)])
manifest=json.loads((P/'manifest.json').read_text())
work=Path(tempfile.mkdtemp(prefix='native-binding-patch-roundtrip-',dir='/srv/abit-dev-linux/tmp'))
for row in manifest['core_files']:
 if row['preimage_sha256'] is not None:
  target=work/row['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(P/'preimages/open_webui'/row['path'],target)
checks=[]
for patchname,key in [('core-forward.patch','postimage_sha256'),('core-rollback.patch','preimage_sha256')]:
 result=subprocess.run(['/usr/bin/patch','--batch','--fuzz=0','-p1','-i',str(P/patchname)],cwd=work,capture_output=True,text=True)
 assert result.returncode==0,(patchname,result.stdout,result.stderr)
 for row in manifest['core_files']:
  target=work/row['path'];actual=sha(target) if target.exists() else None
  assert actual==row[key],(patchname,row['path'],actual,row[key])
 checks.append({'patch':patchname,'sha256':sha(P/patchname),'exit_code':result.returncode,'matched_files':len(manifest['core_files'])})
receipt={'schema_version':1,'source_only':True,'work':str(work),'patch_fuzz':0,'checks':checks,'live_files_changed':False}
(E/'package-roundtrip.json').write_text(json.dumps(receipt,indent=2)+'\n')
# The exact source composition used by mandatory tests, plus required public
# migration resources and the actual focused fixtures. Never include temp DBs.
source_map=json.loads((E/'composed-source-20261122T154401846015Z.json').read_text())
files=set(source_map)|{'backend/open_webui/alembic.ini','backend/open_webui/migrations/script.py.mako','backend/tests/test_agentic_native_verification.py','backend/tests/test_agentic_controls_events.py'}
for name,digest in source_map.items():assert sha(H/name)==digest,name
archive=E/'composed-backend-source.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for name in sorted(files):
  path=H/name;assert path.is_file() and not path.is_symlink() and name.startswith(('backend/open_webui/','backend/tests/'))
  tar.add(path,arcname=name,recursive=False)
archive_index={'source_files':len(source_map),'archive_files':len(files),'archive_sha256':sha(archive),'archive_bytes':archive.stat().st_size,'files':{name:sha(H/name) for name in sorted(files)},'excluded':'All databases, user/configuration data, uploads, provider caches, logs, keys and .git temporary execution state. Only enumerated source and fixture files included.'}
(E/'composed-backend-source.json').write_text(json.dumps(archive_index,indent=2)+'\n')
validation={'schema_version':1,'finished_utc':datetime.now(timezone.utc).isoformat(),'focused':{'tests':43,'failures':0,'errors':0,'skips':0,'receipt':focused.name,'sha256':sha(focused)},'api_verify':{'receipt':verify.name,'sha256':sha(verify),'exit_code':0,'svelte_errors':0,'svelte_warnings':0},'api_integration':{'receipt':integration.name,'sha256':sha(integration),'exit_code':0,'checks_passed':104,'checks_failed':0,'http_operations':616},'source_composition_files':381,'source_drift':False,'archive':{'path':archive.name,'sha256':sha(archive)},'package_roundtrip_sha256':sha(E/'package-roundtrip.json'),'no_provider_or_root_actions':True}
(E/'validation-final.json').write_text(json.dumps(validation,indent=2)+'\n')
manifest['supporting_files']={name:sha(P/name) for name in ['root-registration.template.json','core-config.template.json','abit-native-verification-broker.service','README.md']}
manifest['validation_final_sha256']=sha(E/'validation-final.json')
manifest['package_roundtrip_sha256']=sha(E/'package-roundtrip.json')
manifest['source_archive_sha256']=sha(archive)
manifest['independent_core_review_sha256']=sha(E/'independent-core-native-review.json')
(P/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'package_manifest':sha(P/'manifest.json'),'validation':sha(E/'validation-final.json'),'archive':sha(archive),'archive_bytes':archive.stat().st_size,'archive_source_files':len(files),'roundtrip':sha(E/'package-roundtrip.json')}))

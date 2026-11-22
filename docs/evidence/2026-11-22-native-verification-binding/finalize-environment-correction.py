from pathlib import Path
import hashlib,json,tarfile,subprocess,tempfile,shutil,datetime
R=Path(__file__).resolve().parents[3];E=Path(__file__).resolve().parent;P=R/'deployment/native-verification-binding-20261122';H=Path('/srv/abit-dev-linux/tmp/native-verification-binding-20261122/composed-api')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
# Keep prior committed artifacts available in9c37; all original logs also remain.
subprocess.run(['/usr/bin/python3','-B',str(E/'package-source.py')],cwd=R,check=True)
p=P/'README.md';s=p.read_text().replace('environment `native-verification`','environment `ABIT-DEV-LINUX`');s=s.replace('in a registered native-verification environment','in the exact registered ABIT-DEV-LINUX environment');p.write_text(s)
p=P/'integration-closure.json';d=json.loads(p.read_text());d['environment']='ABIT-DEV-LINUX';d['root_gate']['source_sha256']='44c1f5c7118be8235427da695a51a3066962207d8772ff9a318c60d641f63b3b';d['actual_environment_scope_preserved']=True;p.write_text(json.dumps(d,indent=2)+'\n')
focused=E/'focused-20261122T160533787865Z.json';verify=E/'api-verify-20261122T160555971964Z.json';integration=E/'api-verify-integration-20261122T160553922882Z.json'
for p in [focused,verify,integration]:
 d=json.loads(p.read_text());assert d['exit_code']==0 and not d['source_drift'] and sha(E/d['log'])==d['log_sha256']
for name,digest in json.loads(focused.read_text())['source_after'].items():assert sha(R/name)==digest,name
source_map=json.loads((E/'composed-source-20261122T160553922882Z.json').read_text());files=set(source_map)|{'backend/open_webui/alembic.ini','backend/open_webui/migrations/script.py.mako','backend/tests/test_agentic_native_verification.py','backend/tests/test_agentic_controls_events.py'}
for name,digest in source_map.items():assert sha(H/name)==digest,name
archive=E/'composed-backend-source.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for name in sorted(files):
  path=H/name;assert path.is_file() and not path.is_symlink();tar.add(path,arcname=name,recursive=False)
(E/'composed-backend-source.json').write_text(json.dumps({'source_files':len(source_map),'archive_files':len(files),'archive_sha256':sha(archive),'archive_bytes':archive.stat().st_size,'files':{name:sha(H/name) for name in sorted(files)},'excluded':'All DBs, production configuration, user data, credentials, uploads, caches and temporary execution state.'},indent=2)+'\n')
m=json.loads((P/'manifest.json').read_text());work=Path(tempfile.mkdtemp(prefix='native-binding-environment-roundtrip-',dir='/srv/abit-dev-linux/tmp'))
for row in m['core_files']:
 if row['preimage_sha256'] is not None:
  dest=work/row['path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(P/'preimages/open_webui'/row['path'],dest)
checks=[]
for patch,key in [('core-forward.patch','postimage_sha256'),('core-rollback.patch','preimage_sha256')]:
 q=subprocess.run(['/usr/bin/patch','--batch','--fuzz=0','-p1','-i',str(P/patch)],cwd=work,capture_output=True,text=True);assert q.returncode==0,(q.stdout,q.stderr)
 for row in m['core_files']:
  path=work/row['path'];assert (sha(path) if path.exists() else None)==row[key],row['path']
 checks.append({'patch':patch,'sha256':sha(P/patch),'exit_code':0,'matched_files':6})
(E/'package-roundtrip-environment-corrected.json').write_text(json.dumps({'schema_version':1,'source_only':True,'work':str(work),'patch_fuzz':0,'checks':checks,'live_files_changed':False},indent=2)+'\n')
v={'schema_version':1,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'focused':{'tests':45,'failures':0,'errors':0,'skips':0,'receipt':focused.name,'sha256':sha(focused)},'api_verify':{'receipt':verify.name,'sha256':sha(verify),'exit_code':0,'source_checks':18,'backend_contract_checks':48,'frontend_checks':37,'svelte_errors':0,'svelte_warnings':0},'api_integration':{'receipt':integration.name,'sha256':sha(integration),'exit_code':0,'checks_passed':104,'checks_failed':0,'http_operations':616},'source_composition_files':381,'source_drift':False,'archive':{'path':archive.name,'sha256':sha(archive)},'package_roundtrip_sha256':sha(E/'package-roundtrip-environment-corrected.json'),'no_provider_or_root_actions':True,'actual_environment':'ABIT-DEV-LINUX','supersedes_commit_for_activation':'9c37afc970d54ce98938f0f48a8231347acaef0f'}
(E/'validation-final.json').write_text(json.dumps(v,indent=2)+'\n')
m['state']='SOURCE_TESTED_INDEPENDENTLY_REVIEWED_DEFAULT_OFF';m['actual_project_environment']='ABIT-DEV-LINUX';m['supersedes_commit_for_activation']='9c37afc970d54ce98938f0f48a8231347acaef0f';m['supporting_files']={name:sha(P/name) for name in ['root-registration.template.json','core-config.template.json','abit-native-verification-broker.service','README.md','integration-closure.json']};m['validation_final_sha256']=sha(E/'validation-final.json');m['package_roundtrip_sha256']=sha(E/'package-roundtrip-environment-corrected.json');m['source_archive_sha256']=sha(archive)
for key,name in [('independent_core_review_sha256','independent-core-native-review.json'),('independent_root_broker_review_sha256','independent-root-broker-review.json'),('independent_environment_delta_review_sha256','independent-core-environment-delta-review.json')]:m[key]=sha(E/name)
(P/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
p=E/'README.md';s=p.read_text();s=s.replace('0964f23c8e2194a0487c03955f90ed2a4fc0e5605472d53f4551bfbcec037302',sha(P/'manifest.json'));s=s.replace('44 distinct focused methods: the final 43-method run plus one added actual-ledger queued→claim→start→approval→submit transition. The original 43 test source bytes remain unchanged as a prefix. That extra case proves','45 focused methods in one terminal run, including the actual-ledger queued→claim→start→approval→submit transition and the exact current Project environment regression. The transition case proves');s=s.replace('See `package-roundtrip.json`.','See `package-roundtrip-environment-corrected.json`.');s+='\n## Actual Project environment correction\n\nThe earlier9c37 source candidate required an artificial environment value. Root’s authenticated Project read showed both current fields are `ABIT-DEV-LINUX`; the one exact contract comparison was corrected, with no Project change or broader allowlist. The old artificial value, wrong case and production value are rejected. The exact fixed profile, repository, IT role, development/test gateway and native isolation requirements remain. The prior source candidate is retained and superseded for activation. `independent-core-environment-delta-review.json` separately accepts the byte-exact correction and45-method receipt.\n';p.write_text(s)
tracked=['backend/open_webui/routers/agentic_governance.py','backend/open_webui/utils/agentic_contracts/validator.py','backend/open_webui/utils/agentic_contracts/schemas/governance_v1.schema.json','backend/tests/test_agentic_native_verification.py']+[str(p.relative_to(R)) for p in (R/'backend/open_webui/utils').glob('agentic_native_*.py')]
files=sorted(set([R/name for name in tracked]+[p for root in [E,P] for p in root.rglob('*') if p.is_file() and p.name!='evidence-index.json']))
(E/'evidence-index.json').write_text(json.dumps({'schema_version':1,'branch':'feat/native-verification-binding-20261122','remote':'abit-origin: ABiT-Consulting/abit-ai-platform','intentional_source_archive':True,'no_production_data_or_credentials':True,'files':[{'path':str(p.relative_to(R)),'sha256':sha(p),'bytes':p.stat().st_size} for p in files]},indent=2)+'\n')
print(json.dumps({'manifest_sha256':sha(P/'manifest.json'),'validation_sha256':sha(E/'validation-final.json'),'roundtrip_sha256':sha(E/'package-roundtrip-environment-corrected.json'),'index_sha256':sha(E/'evidence-index.json'),'source_files':len(files),'archive_sha256':sha(archive)}))

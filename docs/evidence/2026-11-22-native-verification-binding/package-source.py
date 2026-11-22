from pathlib import Path
import hashlib,json,shutil,difflib,datetime
R=Path(__file__).resolve().parents[3]
E=Path(__file__).resolve().parent
P=R/'deployment/native-verification-binding-20261122'
LIVE=Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
files=['routers/agentic_governance.py','utils/agentic_contracts/validator.py','utils/agentic_contracts/schemas/governance_v1.schema.json','utils/agentic_native_contract.py','utils/agentic_native_verification.py','utils/agentic_native_execution.py']
expected={'routers/agentic_governance.py':'460edce0e13c756bb86d9668e86becee8c7b2889c3dcb98ad406d2732d6355f6','utils/agentic_contracts/validator.py':'318d0b0985b50b445ffcf2d1981ac08c6b0baa27d6159a511550bc81c02a1e8e','utils/agentic_contracts/schemas/governance_v1.schema.json':'c0db8dd1529bd828fa01ccc3692131257b7074841eb760f28fb9690d81a2ca43'}
sha=lambda raw:hashlib.sha256(raw).hexdigest()
records=[];forward=[];reverse=[]
for rel in files:
 target=LIVE/rel;before=target.read_bytes() if target.exists() else None
 if (sha(before) if before is not None else None)!=expected.get(rel):raise SystemExit('Live preimage drift '+rel)
 after=(R/'backend/open_webui'/rel).read_bytes();out=P/'core/open_webui'/rel;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(after)
 if before is not None:
  out=P/'preimages/open_webui'/rel;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(before)
 records.append({'path':rel,'preimage_sha256':sha(before) if before is not None else None,'postimage_sha256':sha(after),'bytes':len(after),'source':'backend/open_webui/'+rel,'payload':'core/open_webui/'+rel})
 a=before.decode().splitlines(keepends=True) if before is not None else []
 b=after.decode().splitlines(keepends=True)
 forward.extend(difflib.unified_diff(a,b,fromfile='a/'+rel if before is not None else '/dev/null',tofile='b/'+rel))
 reverse.extend(difflib.unified_diff(b,a,fromfile='a/'+rel,tofile='b/'+rel if before is not None else '/dev/null'))
(P/'core-forward.patch').write_text(''.join(forward));(P/'core-rollback.patch').write_text(''.join(reverse))
rootfiles=[]
for name in ['agentic_native_contract.py','agentic_native_broker.py','agentic_native_service.py']:
 raw=(R/'backend/open_webui/utils'/name).read_bytes();out=P/'root-bundle'/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(raw)
 rootfiles.append({'path':name,'sha256':sha(raw),'bytes':len(raw),'target':'/var/lib/abit/native-verification/service/'+name,'mode':'0400','uid':0,'gid':0})
registration={'schema_version':1,'enabled':False,'module_sha256':{x['path']:x['sha256'] for x in rootfiles if x['path']!='agentic_native_service.py'},'core_peer':{'pid':0,'uid':0,'gid':0,'start_ticks':0,'cmdline_sha256':'PENDING_DENY','boot_id':'PENDING_DENY'}}
(P/'root-registration.template.json').write_text(json.dumps(registration,indent=2)+'\n')
(P/'core-config.template.json').write_text(json.dumps({'agentic_native_verification':{'enabled':False,'socket_path':'/run/abit-native-verification/broker.sock','key_file':'PENDING_DENY','projects':[]}},indent=2)+'\n')
manifest={'schema_version':1,'kind':'native_verification_source_candidate','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'SOURCE_TESTED_REVIEW_AND_LIVE_ACCEPTANCE_SEPARATE','core_base_commit':'d393c7653ccb032d8b393f66ab56d9b45726eb10','core_live_preimages_checked':True,'core_runtime':str(LIVE),'core_files':records,'core_forward_patch_sha256':sha((P/'core-forward.patch').read_bytes()),'core_rollback_patch_sha256':sha((P/'core-rollback.patch').read_bytes()),'root_bundle_files':rootfiles,'root_registration_default_enabled':False,'core_registration_default_enabled':False,'root_issued_native_authority':False,'core_or_root_service_deployed_by_source_task':False,'native_controller_sha256':'773bf0adea5ef2c885adbd0124050a302066c1a41513c65c063d94625c03894b','native_controller_revision_receipt_sha256':'ca240b6abc3969d4fa3872d4f1dd2a51d4d2020656be679b5023fde93c33b714','original_jobs_modified':False}
(P/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'package':str(P),'manifest_sha256':sha((P/'manifest.json').read_bytes()),'core_files':len(records),'root_files':len(rootfiles)}))

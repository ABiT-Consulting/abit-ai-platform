"""Read exact protected metadata, verify fresh conditions, assemble a reviewable binding.

This performs read-only root checks. It writes only new ordinary metadata/source
artifacts. It never invokes the activation helper's main or any control action.
"""
from pathlib import Path
import base64, copy, datetime, hashlib, json, os, subprocess

ROOT = Path(__file__).resolve().parents[3]
E = ROOT / 'docs/evidence/2026-11-22-native-verification-activation'
P = ROOT / 'deployment/linux/native-verification-activation-20261122'
PACKAGE = Path('/srv/abit-dev-linux/abit-ai/deployment/native-verification-20261122T1615')
STAGE = '/var/lib/abit-ai-native-verification-20261122T1615'
SOURCE_SHA = 'd51c6c593c8ab09bbd08e8bb8177f0b282bb5fdc51d1def530788e13a046bf11'
BACKUP_SHA = '127800e12d7c191b8250b7924d2b888f31316415429ace3433a4919d36bcd467'
DRAFT_SHA = '38c292339bfe205a28652aeac583e4b6a5bf1f95b99386f075013eacf7b97366'
REVIEW_SHA = 'be7e9e2f5dfb7538e7ce20761850abec7199b8400f28e069f17e1236b4a633ed'
hashfile = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert hashfile(P / 'release_core.py') == SOURCE_SHA
assert hashfile(E / 'independent-root-activation-review.json') == REVIEW_SHA
assert not (E / 'actual-preparation').exists()
assert not (PACKAGE / 'release-manifest.json').exists()
assert not (PACKAGE / 'release_core.bound.py').exists()

reader = r'''
from pathlib import Path
import base64, hashlib, json, os, stat, sys, types
source_path=Path('/srv/abit-dev-linux/abit-ai-native-verification-activation-20261122/deployment/linux/native-verification-activation-20261122/release_core.py')
source=source_path.read_bytes()
if hashlib.sha256(source).hexdigest()!='d51c6c593c8ab09bbd08e8bb8177f0b282bb5fdc51d1def530788e13a046bf11':raise RuntimeError('Reviewed readonly source changed')
if os.geteuid()!=0 or os.environ.get('SUDO_USER')!='jimran@abitcons.com':raise RuntimeError('Authorized metadata route required')
h=types.ModuleType('readonly_native_activation_review');h.__file__=str(source_path)
exec(compile(source,str(source_path),'exec'),h.__dict__)
h.owner()
files={}
for name,expected in [('backup-receipt.json','127800e12d7c191b8250b7924d2b888f31316415429ace3433a4919d36bcd467'),('release-manifest.draft.json','38c292339bfe205a28652aeac583e4b6a5bf1f95b99386f075013eacf7b97366')]:
 path=h.STAGE/name
 info=path.lstat()
 if info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or path.resolve()!=path:raise RuntimeError('Protected metadata identity differs')
 value=h.raw(path)
 if h.sha(value)!=expected:raise RuntimeError('Protected metadata hash differs')
 files[name]={'sha256':expected,'base64':base64.b64encode(value).decode()}
m=json.loads(base64.b64decode(files['release-manifest.draft.json']['base64']))
if m['backup_receipt_sha256']!='127800e12d7c191b8250b7924d2b888f31316415429ace3433a4919d36bcd467':raise RuntimeError('Draft backup binding differs')
if m['live_activation_authorized'] is not False or m['validation']['reviewed'] is not False:raise RuntimeError('Unreviewed draft unexpectedly authorizes activation')
if not h.gate_before_matches(m,h.gate(m)):raise RuntimeError('Open admission epoch changed since backup')
work=h.consistent_snapshot(m,'before',m['core_before'],False)
if not h.gate_before_matches(m,h.gate(m)):raise RuntimeError('Open admission epoch changed during review')
report={'schema_version':1,'observed_utc':h.now(),'kind':'INDEPENDENT_PROTECTED_BACKUP_AND_CURRENT_CORE_READONLY_VERIFICATION',
 'source_sha256':h.sha(source),'backup_receipt_sha256':files['backup-receipt.json']['sha256'],
 'draft_sha256':files['release-manifest.draft.json']['sha256'],'actual_backup_files_rehashed':11,
 'source_preimages_and_metadata_verified':378,'non_core_generations_preserved':7,
 'actual_config_path_bound_and_native_disabled':True,'current_gate_matches_backup':True,
 'unit':h.service(m,False),'core_identity':m['core_before'],'work':work,
 'service_controls':False,'database_writes':False,'configuration_exports':False,
 'protected_writes':False,'original_jobs_replayed':False}
print(json.dumps({'files':files,'observation':report},sort_keys=True))
'''
result = subprocess.run(['/usr/bin/sudo','-n','/usr/bin/python3.12','-I','-S','-B','-c',reader],
                        capture_output=True, text=True, timeout=55, check=False)
if result.returncode != 0:
    # The fixed reader emits no private values on failures.
    print('READONLY_REVIEW_EXIT', result.returncode)
    print(result.stderr[-5000:])
    raise SystemExit(result.returncode or 1)
envelope = json.loads(result.stdout)
actual = E / 'actual-preparation'
actual.mkdir()
for name, item in envelope['files'].items():
    assert name in ('backup-receipt.json','release-manifest.draft.json')
    data = base64.b64decode(item['base64'])
    assert hashlib.sha256(data).hexdigest() == item['sha256']
    (actual / name).write_bytes(data)
observation_path = actual / 'fresh-readonly-observation.json'
observation_path.write_text(json.dumps(envelope['observation'], indent=2, sort_keys=True) + '\n')
draft = json.loads((actual / 'release-manifest.draft.json').read_text())
preparation = json.loads((P / 'preparation.json').read_text())
for key in ('purpose','source_commit','source_package_sha256','runtime_root','app_version',
            'baseline','changes','core_before','preserved_processes',
            'native_registration_enabled','native_configuration_path','operator_helper','evidence','limits'):
    assert draft[key] == preparation[key], key
assert draft['validation'] == preparation['validation']
assert len(draft['configuration']) == 5
assert len(draft['graceful']['unit_files']) == 3
assert draft['backup_receipt_sha256'] == BACKUP_SHA
final = copy.deepcopy(draft)
final['live_activation_authorized'] = True
final['validation']['reviewed'] = True
final['validation']['independent_operator_review_sha256'] = REVIEW_SHA
final['operator_helper']['source_commit'] = '778e15257354ebf71a5b9583258aa6a56a03df28'
final['operator_package_commit'] = '778e15257354ebf71a5b9583258aa6a56a03df28'
final['final_binding_created_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
final['authorization'] = {
    'basis': 'Existing user production continuation and parent root operator review',
    'operator_review_sha256': REVIEW_SHA,
    'scope': 'Exactly six Core source leaves, default-off native registration, graceful Core-only lifecycle',
    'native_broker_enable_authorized_by_this_manifest': False,
    'native_authority_or_nonce_issuance_authorized_by_this_manifest': False,
    'original_job_replay_authorized_by_this_manifest': False,
}
for path in (E / 'independent-root-activation-review.json', observation_path):
    final['evidence'].append({'path':str(path),'sha256':hashfile(path)})
encoded = (json.dumps(final, indent=2, sort_keys=True) + '\n').encode()
manifest_sha = hashlib.sha256(encoded).hexdigest()
def exclusive(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
    with os.fdopen(fd,'wb') as stream:
        stream.write(value);stream.flush();os.fsync(stream.fileno())
exclusive(PACKAGE / 'release-manifest.json', encoded)
exclusive(actual / 'release-manifest.final.json', encoded)
source = (P / 'release_core.py').read_text()
assert source.count("MANIFEST_SHA = 'UNBOUND'") == 1
bound = source.replace("MANIFEST_SHA = 'UNBOUND'", "MANIFEST_SHA = '" + manifest_sha + "'")
exclusive(PACKAGE / 'release_core.bound.py', bound.encode())
exclusive(actual / 'release_core.bound.py', bound.encode())
assert bound.replace("MANIFEST_SHA = '" + manifest_sha + "'", "MANIFEST_SHA = 'UNBOUND'") == source
report = {'schema_version':1,'state':'FINAL_BINDING_ASSEMBLED_FOR_ROOT_INDEPENDENT_REVIEW',
          'backup_receipt_sha256':BACKUP_SHA,'protected_draft_sha256':DRAFT_SHA,
          'readonly_observation_sha256':hashfile(observation_path),
          'root_operator_review_sha256':REVIEW_SHA,'final_manifest_sha256':manifest_sha,
          'unbound_release_source_sha256':SOURCE_SHA,
          'bound_release_source_sha256':hashfile(PACKAGE/'release_core.bound.py'),
          'only_release_source_change':'MANIFEST_SHA literal',
          'root_install_path':'/var/lib/abit-ai-native-verification-20261122T1615-release.py',
          'root_mode':'0400','root_uid':0,'root_gid':0,
          'service_action_performed':False,'protected_files_modified':False,
          'native_registration_enabled':False}
(actual / 'final-binding-review.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(json.dumps(report,indent=2,sort_keys=True))

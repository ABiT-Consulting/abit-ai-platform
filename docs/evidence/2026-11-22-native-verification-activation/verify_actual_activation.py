"""Read-only post-activation verification; never invokes a service control."""
from pathlib import Path
import base64, hashlib, json, subprocess

ROOT = Path(__file__).resolve().parents[3]
E = ROOT / 'docs/evidence/2026-11-22-native-verification-activation'
OUT = E / 'actual-activation'
assert not OUT.exists()
# Read only the previously recorded original invocation identifier.
context = json.loads(Path('/srv/abit-dev-linux/tmp/orchestration-native-route-readiness-20261122/current-06455-bounded-20261122T1440.json').read_text())
def find_identifier(value):
    if isinstance(value, dict):
        if isinstance(value.get('invocation_id'), str):
            return value['invocation_id']
        for item in value.values():
            found = find_identifier(item)
            if found: return found
    elif isinstance(value, list):
        for item in value:
            found = find_identifier(item)
            if found: return found
    return None
invocation = find_identifier(context)
assert isinstance(invocation, str) and 1 <= len(invocation) <= 160
reader = r'''
from pathlib import Path
from contextlib import closing
import base64,hashlib,json,os,socket,sqlite3,stat,sys,time,types,urllib.error,urllib.request
HELPER=Path('/var/lib/abit-ai-native-verification-20261122T1615-release.py')
STAGE=Path('/var/lib/abit-ai-native-verification-20261122T1615')
EXPECTED_MANIFEST='d98cd51865d15f37089440ff26f5a2c25fbdacd5fad8b6321e1d40540b656b4f'
EXPECTED_BACKUP='127800e12d7c191b8250b7924d2b888f31316415429ace3433a4919d36bcd467'
EXPECTED_SOURCE='3bbafd2731cf06f13c76f8b13afd474cc06d8fcef4112ceec221de60af09e4ca'
if os.geteuid()!=0 or os.environ.get('SUDO_USER')!='jimran@abitcons.com':raise RuntimeError('Existing authorized read-only administrator required')
info=HELPER.lstat()
if info.st_uid!=0 or info.st_gid!=0 or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o400 or info.st_nlink!=1 or HELPER.resolve()!=HELPER:raise RuntimeError('Protected helper identity differs')
encoded=HELPER.read_bytes()
if hashlib.sha256(encoded).hexdigest()!=EXPECTED_SOURCE:raise RuntimeError('Protected helper changed')
h=types.ModuleType('readonly_final_native_verification');h.__file__=str(HELPER)
exec(compile(encoded,str(HELPER),'exec'),h.__dict__)
h.owner()
manifest_bytes=h.raw(STAGE/'graceful-release-manifest.json')
if h.sha(manifest_bytes)!=EXPECTED_MANIFEST:raise RuntimeError('Protected manifest changed')
m=json.loads(manifest_bytes)
if m['backup_receipt_sha256']!=EXPECTED_BACKUP:raise RuntimeError('Backup identity differs')
h.protected(STAGE/'graceful-activation.json')
journal_bytes=h.raw(STAGE/'graceful-activation.json');journal=json.loads(journal_bytes)
if journal.get('state')!='deployed_health_verified' or journal.get('manifest_sha256')!=EXPECTED_MANIFEST:raise RuntimeError('Original activation is not verified')
if [c['mode'] for c in journal['cycles']]!=['apply']:raise RuntimeError('Unexpected release lifecycle')
cycle=journal['cycles'][0]
if any(not h.completed(cycle,key) for key in ('override_on','close','stop','install','start','override_off','open')):raise RuntimeError('Original lifecycle evidence incomplete')
m2,ordinary=h.validate_manifest()
if ordinary!=manifest_bytes or m2!=m:raise RuntimeError('Ordinary/protected final manifest mismatch')
core=h.healthy(m,cycle,False)
if h.core_from_start(m,cycle,False)!=core:raise RuntimeError('Final start generation differs')
if h.override_exists():raise RuntimeError('Owned temporary override remains')
open_gate=h.control_receipt(m,cycle['intents']['open'])
h.backup(m)
work=h.ledger_snapshot(m,core)
# Count-only comparison; no row values or SQL schema contents are exported.
deadline=time.monotonic()+40
def inventory(path):
 before=path.lstat()
 if not stat.S_ISREG(before.st_mode) or path.resolve()!=path:raise RuntimeError('Database alias changed')
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
  db.execute('PRAGMA query_only=ON');db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  db.execute('BEGIN')
  if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise RuntimeError('Read-only database integrity failed')
  names=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
  counts={name:db.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in names}
  schema=list(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name'))
 after=path.lstat()
 if (before.st_dev,before.st_ino,before.st_uid,before.st_mode)!=(after.st_dev,after.st_ino,after.st_uid,after.st_mode):raise RuntimeError('Database identity changed during count read')
 return counts,h.sha(json.dumps(schema).encode())
comparisons=[]
for relative in h.DATABASES:
 backup_path=STAGE/'database'/relative.replace('/','--')
 before,before_schema=inventory(backup_path)
 after,after_schema=inventory(h.BASE/'data'/relative)
 changed={name:{'backup':before.get(name),'live':after.get(name)}
          for name in sorted(set(before)|set(after)) if before.get(name)!=after.get(name)}
 item={'relative_database':relative,'backup_table_count':len(before),'live_table_count':len(after),
       'row_counts_equal':before==after,'schema_equal':before_schema==after_schema,
       'backup_counts_sha256':h.sha(json.dumps(sorted(before.items())).encode()),
       'live_counts_sha256':h.sha(json.dumps(sorted(after.items())).encode()),
       'changed_table_counts':changed,'quick_check':'ok'}
 if relative=='webui.db':
  item['selected_existing_user_data_counts']={name:{'backup':before[name],'live':after.get(name)}
      for name in ('user','chat','folder','file','model','prompt','config','group','project') if name in before}
 comparisons.append(item)
# The fixed status endpoint never dispatches or settles a task.
original=sys.argv[1]
path='/api/v1/agentic/projects/PROJ-0202/native-verification/'+original
if any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:@+-' for c in original):raise RuntimeError('Original invocation identifier shape differs')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),h.NoRedirect())
def route(method,url_path,token=None):
 headers={'Connection':'close'}
 if token is not None:headers['X-ABIT-ERP-Control']=token
 request=urllib.request.Request('http://127.0.0.1:8888'+url_path,method=method,headers=headers)
 try:
  response=opener.open(request,timeout=5)
 except urllib.error.HTTPError as error:
  response=error
 with response:
  data=response.read(4097)
  if len(data)>4096:raise RuntimeError('Bounded API response exceeded')
  value=json.loads(data) if data else {}
  result={'method':method,'status':response.status,'body_sha256':h.sha(data)}
  if response.status==200:
   expected={'invocation_id','available','phase','binding_sha256','gate_sha256','receipt','automatic_replay_permitted','independent_qa_approved'}
   if type(value)is not dict or set(value)!=expected:raise RuntimeError('Status response shape differs')
   if value['invocation_id']!=original or value['available']is not False or value['phase']is not None or value['receipt']is not None or value['automatic_replay_permitted']is not False or value['independent_qa_approved']is not False:raise RuntimeError('Unexpected existing native status')
   result['bounded_status']={k:value[k] for k in ('available','phase','automatic_replay_permitted','independent_qa_approved')}
  return result
api=[route('GET',path),route('POST',path)]
if api[0]['status']!=401 or api[1]['status']!=405:raise RuntimeError('New status auth/method boundary differs')
environment=h.raw_proc_environment(core['pid'])
prefix=b'ABIT_ERP_CONTROL_TOKEN_FILE='
configured=[x[len(prefix):].decode() for x in environment.split(b'\0') if x.startswith(prefix)]
token_path=h.BASE/'config/erp-assignment/control-token'
if configured not in ([],[str(token_path)]):raise RuntimeError('Existing control credential path is outside fixed route')
token=h.raw(token_path).decode().strip()
if len(token)<32:raise RuntimeError('Existing control credential unavailable')
api.extend([route('GET',path,token),route('GET',path.replace('/PROJ-0202/','/PROJ-0181/'),token)])
if [x['status'] for x in api]!=[401,405,200,404]:raise RuntimeError('Status route boundary differs')
token=None;environment=None
final_work=h.ledger_snapshot(m,core)
h.healthy(m,cycle,False);h.control_receipt(m,cycle['intents']['open'])
report={'schema_version':1,'observed_utc':h.now(),'verdict':'CORE_DEPLOYMENT_LIVE_VERIFIED_NATIVE_ROUTE_DEFAULT_OFF',
 'manifest_sha256':EXPECTED_MANIFEST,'protected_helper_sha256':EXPECTED_SOURCE,
 'backup_receipt_sha256':EXPECTED_BACKUP,'original_journal_sha256':h.sha(journal_bytes),
 'original_activation_state':journal['state'],'original_verified_utc':journal.get('verified_utc'),
 'six_live_files':[{'path':rel,'sha256':h.filehash(h.LIVE/rel),'expected_sha256':row['after_sha256']} for rel,row in sorted(m['changes'].items())],
 'complete_source_and_absence_contract_entries':len(m['baseline']),
 'core_before':m['core_before'],'core_after':core,'preserved_processes':m['preserved_processes'],
 'health':h.http('/health'),'config_version':h.http('/api/config').get('version'),
 'unit':h.service(m,False),'temporary_override_absent':True,
 'admission':{'state':open_gate['state'],'epoch':open_gate['epoch'],'exact_original_command_verified':True},
 'native_configuration_path':str(h.CONFIG_PATH),'native_registration_enabled':False,
 'work_before_status_gets':work,'work_after_status_gets':final_work,
 'database_count_comparisons':comparisons,'status_api_checks':api,
 'reviewer_service_actions':False,'reviewer_database_writes':False,'original_jobs_replayed':False,
 'worker_execution_or_end_to_end_task_completion_claimed':False,
 'limits':['Counts and integrity checks do not claim equality of every live business row.',
           'No browser login, new chat/provider dispatch, native broker enable or worker execution was performed.']}
print(json.dumps({'report':report,'original_journal_base64':base64.b64encode(journal_bytes).decode()},sort_keys=True))
'''
result = subprocess.run(['/usr/bin/sudo','-n','/usr/bin/python3.12','-I','-S','-B','-c',reader,invocation],
                        capture_output=True,text=True,timeout=60,check=False)
if result.returncode:
    print('READONLY_VERIFY_EXIT',result.returncode)
    print(result.stderr[-6000:])
    raise SystemExit(result.returncode or 1)
value=json.loads(result.stdout)
OUT.mkdir()
journal=base64.b64decode(value['original_journal_base64'])
assert hashlib.sha256(journal).hexdigest()==value['report']['original_journal_sha256']
(OUT/'original-graceful-activation.json').write_bytes(journal)
report_path=OUT/'independent-live-verification.json'
report_path.write_text(json.dumps(value['report'],indent=2,sort_keys=True)+'\n')
print(json.dumps({'verdict':value['report']['verdict'],'report_path':str(report_path),
 'report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),
 'original_journal_sha256':hashlib.sha256(journal).hexdigest(),
 'core_after':value['report']['core_after'],'admission':value['report']['admission'],
 'status_api_http_codes':[x['status'] for x in value['report']['status_api_checks']],
 'database_changes':{x['relative_database']:x['changed_table_counts'] for x in value['report']['database_count_comparisons'] if not x['row_counts_equal']}},indent=2,sort_keys=True))

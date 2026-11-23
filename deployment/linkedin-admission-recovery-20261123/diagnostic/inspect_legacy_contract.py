from pathlib import Path
import os,json,sqlite3,hashlib,ast,re,resource,signal,socket
from datetime import datetime,timezone
if os.geteuid()!=999 or os.getegid()!=988 or socket.gethostname()!='abit-dev-linux':raise SystemExit('IDENTITY_DENIED')
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
signal.alarm(20)
p=Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui/utils/abitdot_failover.py')
b=p.read_bytes()
if hashlib.sha256(b).hexdigest()!='e9f2b7d6a8abf72366753848cf4ce3b6c56700f363a9fc1920e2577dce287e90':raise SystemExit('SOURCE_DRIFT')
tree=ast.parse(b)
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='prompt_for_dot')
scope={'json':json}
exec(compile(ast.Module(body=[node],type_ignores=[]),'verified_prompt_formatter','exec'),scope)
owner='03519eef-e01d-4c45-8e07-266b35d0ac91'
agent='d4a6e561-cb2a-41d9-93b5-2888624cf8d6'
event='b73e264f-43ba-4596-a6b6-ca97bf8e33a1'
database=Path('/srv/abit-dev-linux/abit-ai/data/aibit-chatgpt-relay.sqlite')
s=database.lstat()
if database.resolve()!=database or s.st_uid!=999 or s.st_gid!=988 or s.st_nlink!=1:raise SystemExit('DATABASE_IDENTITY_DENIED')
db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=2,isolation_level=None)
db.row_factory=sqlite3.Row
db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,2*1024*1024)
db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION}
db.set_authorizer(lambda action,*_:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
r=db.execute('SELECT id,owner,automation_id,chat_id,job_id,status,context_json FROM abitdot_relay_events WHERE id=? AND owner=? AND automation_id=?',(event,owner,agent)).fetchone()
if r is None:raise SystemExit('EXACT_EVENT_MISSING')
body=json.loads(r['context_json']);message=scope['prompt_for_dot'](dict(r))
job=body.get('job') or {};context=body.get('context') or {}
metadata=[]
for label,part in [('job',job),('chat_meta',(context.get('chat') or {}).get('meta') or {}),('folder_meta',(context.get('folder') or {}).get('meta') or {})]:
 for key in ['account','account_id','chatgpt_account_id','aibit_account']:
  value=part.get(key)
  if value is not None:metadata.append({'location':label+'.'+key,'valid_account_shape':isinstance(value,str) and bool(re.fullmatch('[a-f0-9]{64}',value)),'sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()})
bindings=db.execute('SELECT owner,account,chat_id,project_id FROM bindings WHERE owner=? AND chat_id=?',(owner,r['chat_id'])).fetchmany(3)
result={'at':datetime.now(timezone.utc).isoformat(),'event_id':event,'status':r['status'],'saved_job_id':r['job_id'],'context_sha256':hashlib.sha256(r['context_json'].encode()).hexdigest(),'formatter_source_sha256':hashlib.sha256(b).hexdigest(),'reconstructed_message_sha256':hashlib.sha256(message.encode()).hexdigest(),'message_characters':len(message),'message_bytes':len(message.encode()),'contains_expected_idempotency_marker':bool(re.search(r'"idempotency_key"\s*:\s*"[a-f0-9]{64}"',message)),'contains_expected_run_marker':bool(re.search(r'"run_id"\s*:\s*"[A-Za-z0-9_-]{1,160}"',message)),'structured_outer_label_present':'Original project, instructions, prompt, prior chat and exact run idempotency key (JSON):' in message,'stored_account_locations':metadata,'current_chat_binding_count':len(bindings),'current_chat_binding_is_original_account_proof':False,'stored_origin_message_id':context.get('origin_message_id'),'formatter_historical_equivalence_verified':False,'provider_called':False,'total_changes':db.total_changes,'read_only':True}
db.close();signal.alarm(0)
print(json.dumps(result,sort_keys=True,indent=2))

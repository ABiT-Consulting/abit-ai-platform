"""Pure retained original-QA receipt verification; no endpoint or database access."""
import ast, hashlib, json, re, sys, time, types, os, stat
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, uuid5
from collections import Counter
from urllib.parse import parse_qsl, unquote, urlsplit
from jsonschema import Draft202012Validator, FormatChecker
from datetime import datetime, timezone

ROOT=Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui')
OBS=Path('/var/lib/abit-ai-erp-rollouts/qa-original-observe-20261115')
COLD=Path('/var/lib/abit-ai-erp-rollouts/qa-original-cold-reader-20261115')
INV='agov-step-28b6ec836fbb90da84623ee37fc3cc65ce1037c6ec522b5c'
RUN='3d016aab-b569-528f-9837-e46945ebcf51'
PINS={
'utils/agentic_bridge.py':'a0220b323e62fa27c14b00cdda61f5af4439805c0963c94b4b301091bd327f3b',
'utils/abitdot_primary.py':'3848123b6dbc7eb3aa46b64e867a0847a1e5f792ebe6947bdb9b8b5cc3b387a8',
'utils/agentic_relay_scope.py':'50fc65add1abe1e5b50e1ccf3ae2bcafa33ab8ff26a39fca168ff7301be40055',
'utils/abitdot_primary_relay.py':'5e58239fe1f1cd5242460a6b4c8b86a959608fafd41528b671a340dad16c83aa',
'utils/agentic_contracts/validator.py':'b124192191935e773f2e955aa64b3ed5d8f052f4466a4da417920605ad424af5',
'utils/agentic_contracts/developer_delivery.py':'90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210',
}
INPUTS={
OBS/'intent.json':'59081acd6235fc653972f9b88a989b67b35dcb6de55253d4b28b27593a1d9152',
OBS/'actual-observation.private.json':'02c7d8b8958325b75b9a506456b8012fd05e3b8d986a2577dd9bad8f06450ee7',
OBS/'actual-normalized-receipt.private.json':'633447bb00a9b2825763c441e41b5a55fde54975660045f1837a977707c15f53',
COLD/'attempt/before.private.json':'de15ef486058c80fd549867e2052ebbf9b5371e8c56a961f939566cd260c1607',
}
refs=[]
def read_pinned(p,want):
 b=p.read_bytes();assert hashlib.sha256(b).hexdigest()==want,'PIN_CHANGED'
 st=p.lstat();assert stat.S_ISREG(st.st_mode),'REGULAR_FILE_REQUIRED'
 refs.append({'path':str(p),'sha256':want,'bytes':len(b),'uid':st.st_uid,'gid':st.st_gid,'mode':oct(stat.S_IMODE(st.st_mode))})
 return b
def audit(event,args):
 if event.startswith(('socket.','subprocess.','sqlite3.')) or event in {'os.system','os.fork','os.posix_spawn'}:
  raise RuntimeError('NETWORK_PROCESS_DATABASE_DENIED')
 if event=='open':
  _,mode,flags=args
  if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
   raise RuntimeError('WRITE_DENIED')
 if event in {'os.mkdir','os.remove','os.rename','os.rmdir','os.chmod','os.chown','os.link','os.symlink','os.truncate','os.utime'}:
  raise RuntimeError('WRITE_DENIED')
sys.addaudithook(audit)
for name in ('open_webui','open_webui.utils'):
 m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m
class CapabilityUnavailable(Exception): pass
seed=dict(globals())
def extract(rel,name,names=None,constants=True,classes=()):
 p=ROOT/rel;b=read_pinned(p,PINS[rel]);tree=ast.parse(b,str(p));nodes=[]
 for node in tree.body:
  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name!='main' and (names is None or node.name in names):nodes.append(node)
  elif isinstance(node,ast.ClassDef) and node.name in classes:nodes.append(node)
  elif constants and isinstance(node,(ast.Assign,ast.AnnAssign)):
   targets=node.targets if isinstance(node,ast.Assign) else [node.target]
   if all(not isinstance(target,ast.Name) or target.id!='log' for target in targets):nodes.append(node)
 m=types.ModuleType(name);m.__dict__.update(seed);m.__name__=name;m.__file__=str(p);m.__package__=name.rpartition('.')[0]
 sys.modules[name]=m
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(p),'exec'),m.__dict__)
 return m
bridge=extract('utils/agentic_bridge.py','open_webui.utils.agentic_bridge',{'canonical','digest','packet_digest','stable_run_id'},False)
seed.update({k:getattr(bridge,k)for k in ('canonical','digest','packet_digest','stable_run_id')})
primary=extract('utils/abitdot_primary.py','open_webui.utils.abitdot_primary',{'dot_primary_key'},False)
scope=extract('utils/agentic_relay_scope.py','open_webui.utils.agentic_relay_scope',{'_unavailable','_proof','handoff_scope','scope_receipt','scope_for_packet'})
relay=extract('utils/abitdot_primary_relay.py','open_webui.utils.abitdot_primary_relay',{'verified_dot_payload','receipt_from_relay'},True,{'RelayPreflightError'})
delivery=extract('utils/agentic_contracts/developer_delivery.py','developer_delivery')
validator=extract('utils/agentic_contracts/validator.py','retained_qa_validator')
schema=validator.SCHEMA_PATH;sb=schema.read_bytes();refs.append({'path':str(schema),'sha256':hashlib.sha256(sb).hexdigest(),'bytes':len(sb)})
values={p:json.loads(read_pinned(p,w))for p,w in INPUTS.items()}
intent=values[OBS/'intent.json'];raw=values[OBS/'actual-observation.private.json'];normal=values[OBS/'actual-normalized-receipt.private.json'];before=values[COLD/'attempt/before.private.json']
row=before['QA'];assert row['invocation_id']==INV and row['automation_run_id']==RUN
providers=[v for v in before['providers'] if v['original_run_id']==RUN];assert len(providers)==1
original=providers[0];envelope=json.loads(original['envelope_json']);packet=json.loads(row['work_packet'])
account=envelope['governance_scope']['scope']['account_id']
payload=relay.verified_dot_payload(envelope,account,admission=False)
computed=relay.receipt_from_relay(envelope,raw,account)
expected_scope=scope.scope_for_packet(row,packet,envelope['owner_id'],account,envelope['dot_id'],RUN)
lines=[v[len('AGOV RESULT:'):].strip()for v in raw['response'].splitlines()if v.startswith('AGOV RESULT:')]
assert len(lines)==1
result=json.loads(lines[0])
schema_errors=validator.schema_errors('result',result)
validation={'schema_error_count':len(schema_errors),'semantic_validation_completed':False}
try:
 errors=validator.validate_result(result,packet)
 validation.update(semantic_validation_completed=True,strict_error_count=len(errors),strict_errors_sha256=hashlib.sha256(json.dumps(errors,sort_keys=True).encode()).hexdigest())
except Exception as e:
 validation.update(error_type=type(e).__name__,error_message_sha256=hashlib.sha256(str(e).encode()).hexdigest())
projection={
'schema_version':1,'observed_utc':datetime.now(timezone.utc).isoformat(),
'purpose':'Pure rederivation of already-retained original QA observation; no endpoint, DB or app import',
'original':{'invocation_id':INV,'run_id':RUN,'project_id':row['erp_project'],'task_id':row['erp_task'],
 'packet_sha256':row['packet_hash'],'provider_envelope_sha256':hashlib.sha256(original['envelope_json'].encode()).hexdigest(),
 'retained_provider_status':original['status'],'observed_raw_reply_sha256':hashlib.sha256(raw['response'].encode()).hexdigest()},
'checks':{
 'computed_receipt_accepted':computed.get('status')=='accepted',
 'computed_normalized_receipt_exact_equal':computed==normal,
 'scope_exact_original_packet_binding':envelope['governance_scope']['scope']==expected_scope,
 'intent_envelope_digest_matches':intent['envelope_sha256']==hashlib.sha256(original['envelope_json'].encode()).hexdigest(),
 'intent_and_observation_job_equal':intent['job_id']==payload['job_id']==raw['job_id'],
 'owner_exact':raw['owner']==envelope['owner_id'],'account_exact':raw['account_id']==account,
 'read_only':raw.get('read_only')is True,'root_and_reply_present':bool(raw.get('root_message_id')and raw.get('reply_message_id')),
 'single_result_marker':len(lines)==1,'invocation_exact':result.get('invocation_id')==INV,
 'actor_exact':result.get('actor')==packet['binding'],'scope_receipt_exact_equal':raw['scope_receipt']==normal['scope_receipt'],
 'immutable_context_packet_exact':next(json.loads(v['result_json'])for v in before['QA_calls']if v['operation']=='context.read')['work_packet']==packet,
},
'receipt':{'receipt_id':computed.get('receipt_id'),'status':computed.get('status'),'response_utf8_bytes':len(raw['response'].encode())},
'contract':{'outcome':result['outcome'],'test_results':len(result['test_results']),'evidence':len(result['evidence']),'defects':len(result['defects']),**validation},
'limits':['Uses retained pre-restart provider and immutable packet snapshot; no current database re-read.',
 'Strict validator uses its default deterministic project registration policy, not a new registration/ERP authority read.',
 'Relay acceptance and a valid blocked result are not Core persistence, Native publication, QA pass or closing PM completion.'],
'references':refs,
'actions':{'endpoint_calls':0,'DB_reads':0,'DB_writes':0,'model_sends':0,'production_file_writes':0,'runtime_app_imports':0}
}
print(json.dumps(projection,sort_keys=True))

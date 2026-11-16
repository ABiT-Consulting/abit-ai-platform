"""Explicit operator recovery of two positively preflight-denied offline tests.

This does not re-admit an old AI session or rewrite its uncertain receipts.
The exact original profile/source and logical call IDs are retained. Results
are operator recovery evidence, not an automated QA or deployment verdict.
"""
from pathlib import Path
import asyncio,fcntl,hashlib,json,os,sqlite3,sys,time,types
ROOT=Path('/srv/abit-dev-linux/abit-ai')
OUT=ROOT/'deployment/original-test-recovery-06203-20261117-final'
INVOCATION='agov-contract-retry-667b933bd3e3ea992679e23bb15432e6f9b32ac7f42ccb87'
SESSION='agws_6ed88d52fda28e0ff5e4024c37f53667d594ec85b92abaaa'
PROFILE='e094f5706fc0f9ffcc7e3c342fb6645e8f067dda4643d383fab9e4957882010a'
SNAPSHOT='306a96004265bc553fe4cc6e26d6e4f95b25eca995c5df9dfdb85c5ee9e4d8b5'
TESTS=[('dev-security-667b933bd3e3ea99','intermarket_security'),('dev-typecheck-667b933bd3e3ea99','frontend_typecheck')]
sha=lambda raw:hashlib.sha256(raw).hexdigest()
def write(name,value):
 raw=value if isinstance(value,bytes) else (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
 with (OUT/name).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
def memory():
 fields={}
 for line in Path('/proc/meminfo').read_text().splitlines():
  if line.startswith(('MemAvailable:','MemTotal:','SwapTotal:','SwapFree:')):
   a=line.split();fields[a[0][:-1]+'_bytes']=int(a[1])*1024
 relative=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
 current=Path('/sys/fs/cgroup')/relative.lstrip('/')
 groups=[]
 while current!=Path('/sys/fs'):
  if current.exists():
   values={}
   for name in ('memory.max','memory.high','memory.current','memory.swap.max','memory.events','pids.max','pids.current'):
    p=current/name
    if p.is_file():values[name]=p.read_text().strip()
   if values:groups.append({'path':str(current),**values})
  if current==Path('/sys/fs/cgroup'):break
  current=current.parent
 return {**fields,'cgroups':groups}
def original_calls():
 with sqlite3.connect((ROOT/'data/agentic-governance.sqlite').as_uri()+'?mode=ro',uri=True) as db:
  return db.execute('select call_id,request_hash,operation,state,result_json,created_at,updated_at from governance_gateway_call where session_id=? and call_id in (?,?) order by call_id',(SESSION,*[t[0] for t in TESTS])).fetchall()
async def main():
 need=lambda v,m: v or (_ for _ in ()).throw(RuntimeError(m))
 need(os.getuid()==999,'Run only as the isolated service identity')
 previous=ROOT/'deployment/original-test-recovery-06203-20261117'
 for call_id,_ in TESTS:
  denied=json.loads((previous/(call_id+'.result.json')).read_text())
  need(denied['exit_code']==1 and denied['stdout']=='' and denied['stderr']=='bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n','Prior recovery was not a proven sandbox startup denial')
 need(Path('/proc/self/attr/current').read_text().strip().startswith('chatgpt'),'Use the same approved namespace profile as the application service')
 lock=os.open(OUT/'recovery.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 data=json.loads((OUT/'input.private.json').read_text())
 config=data['config'];row=data['row'];packet=data['packet']
 need(config['agentic_artifact_roots']['PROJ-0196']==str(OUT/'artifacts'),'Recovery evidence must use this attempt directory')
 need(row['invocation_id']==INVOCATION and row['erp_task']=='TASK-2026-06203','Original scope mismatch')
 need(sha(json.dumps(config['agentic_repository_gateway']['projects']['PROJ-0196'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())==PROFILE,'Frozen operator profile changed')
 runtime=ROOT/'current/runtime/open_webui'
 for name,path in [('open_webui',runtime),('open_webui.utils',runtime/'utils')]:
  mod=types.ModuleType(name);mod.__path__=[str(path)];sys.modules[name]=mod
 env=types.ModuleType('open_webui.env');env.DATA_DIR=ROOT/'data';sys.modules['open_webui.env']=env
 from open_webui.utils import agentic_gateway_operations as ops
 from open_webui.utils.agentic_gateway_delivery import workspace_receipt,snapshot_workspace
 from open_webui.utils.agentic_gateway_sessions import CURRENT_DB
 token=CURRENT_DB.set(ROOT/'data/agentic-governance.sqlite')
 try:
  need(ops.artifact_directory(row,config).is_relative_to(OUT/'artifacts'),'Resolved artifact root escaped recovery directory')
  provenance=workspace_receipt(row,packet,config,db_path=ROOT/'data/agentic-governance.sqlite')
  need(provenance['commit']=='82ef12862e0986845560e5c9158e3b6f2f043c77','Pinned baseline changed')
  need(snapshot_workspace(row,packet,config)['sha256']==SNAPSHOT,'Original workspace changed')
  calls=original_calls();need(len(calls)==2 and all(x[2]=='repository.test' and x[3]=='uncertain' and x[4] is None for x in calls),'Original denied-call state changed')
  for call_id,test_id in TESTS:
   result_path=OUT/(call_id+'.result.json')
   if result_path.exists():
    result=json.loads(result_path.read_text());print(json.dumps({'call_id':call_id,'state':'retained','exit_code':result['exit_code']}),flush=True);continue
   need(not (OUT/(call_id+'.intent.json')).exists(),'Prior execution intent has no completion; inspect it, never replay')
   artifact_name='test-'+ops.digest(call_id)[:40]+'.txt'
   need(not (ops.artifact_directory(row,config)/artifact_name).exists(),'An immutable evidence identity already exists; reconcile without replay')
   sample=memory();need(sample['MemAvailable_bytes']>=5*1024**3,'Original 4 GiB budget plus 1 GiB reserve not available')
   write(call_id+'.intent.json',{'call_id':call_id,'test_id':test_id,'authority':'current explicit human instruction to resolve and resume original tests','original_invocation':INVOCATION,'original_source_sha256':SNAPSHOT,'original_profile_sha256':PROFILE,'memory':sample,'original_memory_budget_bytes':4*1024**3,'reserve_bytes':1024**3,'original_gateway_receipts_unchanged':calls,'started_at':int(time.time()),'maximum_executions':1})
   print(json.dumps({'call_id':call_id,'test_id':test_id,'state':'starting','memory_available_bytes':sample['MemAvailable_bytes'],'budget_bytes':4*1024**3}),flush=True)
   result=await ops.repository_test({'test_id':test_id},row=row,packet=packet,config=config,gateway_call_id=call_id)
   need(snapshot_workspace(row,packet,config)['sha256']==SNAPSHOT,'Workspace changed during recovery')
   need(original_calls()==calls,'Original gateway journal changed during recovery')
   result.update(recovery_kind='explicit_operator_original_test_resume',original_call_id=call_id,
                 original_invocation=INVOCATION,original_gateway_receipt_state='uncertain_preserved',
                 measured_after=memory(),finished_at=int(time.time()),not_a_qa_or_deployment_verdict=True)
   write(call_id+'.result.json',result)
   print(json.dumps({'call_id':call_id,'test_id':test_id,'state':'completed','exit_code':result['exit_code'],'source_snapshot':result['repository_snapshot_sha256'],'stdout':result['stdout'],'stderr':result['stderr']}),flush=True)
  completed_tests=[{'call_id':call,'test_id':test,'exit_code':json.loads((OUT/(call+'.result.json')).read_text())['exit_code']} for call,test in TESTS]
  write('completed.json',{'state':'TESTS_PASSED' if all(t['exit_code']==0 for t in completed_tests) else 'TESTS_FINISHED_WITH_FAILURES','tests':completed_tests,'original_gateway_rows_preserved':True,'project_config_unchanged':True,'original_source_snapshot':SNAPSHOT,'qa_or_runtime_acceptance_claimed':False})
 finally:CURRENT_DB.reset(token)
if __name__=='__main__':
 try:asyncio.run(main())
 except BaseException as error:
  print(json.dumps({'state':'HELD_PRESERVE_ORIGINAL_INTENT','error_type':type(error).__name__,'message':str(error)[:500],'automatic_replay':False}),flush=True)
  raise

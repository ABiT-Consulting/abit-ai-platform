from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,stat,subprocess
D=Path('/srv/abit-dev-linux/abit-ai/data');L=Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui').resolve();Q='agov-parent-bd0c8d3ad7437b0619f6c73ee16bd95e1a7e6df747d1fea8';U='3df6d316-75cb-563b-b972-73fe4cdea33a'
def sha(x):return hashlib.sha256(x.encode()if isinstance(x,str)else x).hexdigest()
def can(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def ro(name):
 c=sqlite3.connect((D/name).as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
def path_meta(p):
 try:s=p.lstat()
 except FileNotFoundError:return {'exists':False}
 return {'exists':True,'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'symlink':stat.S_ISLNK(s.st_mode),'directory':stat.S_ISDIR(s.st_mode),'regular':stat.S_ISREG(s.st_mode),'nlink':s.st_nlink}
out={'schema_version':1,'observed_utc':datetime.now(timezone.utc).isoformat(),'invocation_id':Q,'run_id':U,'api_calls':0,'database_writes':0,'handler_executed':False}
state=subprocess.check_output(['/usr/bin/systemctl','show','abit-ai.service','--property=MainPID,ActiveState,SubState,NRestarts,InvocationID'],text=True,timeout=3);service=dict(x.split('=',1)for x in state.splitlines()if'='in x);pid=int(service['MainPID']);proc=Path('/proc')/str(pid);birth=proc.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]
out['main']={**service,'birth':birth,'command_sha256':sha(proc.joinpath('cmdline').read_bytes())}
env=dict(x.decode().split('=',1)for x in proc.joinpath('environ').read_bytes().split(b'\0')if b'='in x);cp=Path(env['ABIT_ERP_TASK_RESOLUTION_CONFIG']);assert str(cp)=='/srv/abit-dev-linux/abit-ai/config/erp-assignment/resolver.json';raw=cp.read_bytes();config=json.loads(raw);out['configuration_sha256']=sha(raw)
c=ro('agentic-governance.sqlite')
try:
 r=dict(c.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(Q,)).fetchone());assert r['automation_run_id']==U and r['erp_project']=='PROJ-0198'and r['erp_task']=='TASK-2026-06200'and r['target_role']=='qa';packet=json.loads(r['work_packet'])
 out['qa']={k:r[k]for k in ['state','dispatch_attempts','automation_run_id','submitted_at','updated_at','packet_hash','lease_fence']};out['packet_digest']=packet['content_sha256']
 out['implementation_delivery']={k:v for k,v in packet.get('implementation_delivery',{}).items()if k in ['repository','pull_request_url','head_commit','base_commit','branch','base_branch']}
 calls=[dict(x)for x in c.execute('SELECT c.* FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id WHERE s.invocation_id=? ORDER BY c.created_at,c.call_id',(Q,))]
 out['gateway_calls']=[{'call_id_sha256':sha(x['call_id']),'operation':x['operation'],'state':x['state'],'created_at':x['created_at'],'updated_at':x['updated_at'],'request_hash':x['request_hash'],'matches_empty_verify_request':x['request_hash']==sha(can({'operation':'repository.verify_delivery','arguments':{}})),'result_present':bool(x['result_json']),'result_sha256':sha(x['result_json'])if x['result_json'] else None}for x in calls]
 for t in ['governance_qa_delivery','governance_repository_workspace','governance_result','governance_local_result']:
  present=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name=?",(t,)).fetchone());vals=[dict(x)for x in c.execute('SELECT * FROM '+t+' WHERE invocation_id=?',(Q,))]if present else []
  items=[]
  for x in vals:
   v={k:x[k]for k in ['invocation_id','state','phase','packet_sha256','delivery_sha256','snapshot_sha256','config_sha256','created_at','updated_at']if k in x}
   for k in ['scope_json','facts_json','manifest_json','result_json','receipt_json']:
    if k in x:
     v[k+'_present']=bool(x[k]);v[k+'_sha256']=sha(x[k])if x[k]else None
     if x[k]and k in ['scope_json','facts_json','result_json']:
      z=json.loads(x[k]);v[k+'_safe']={a:b for a,b in z.items()if a in ['commit','role','project_id','task_id','invocation_id','repository','environment','head_commit','base_commit','pull_request_url','verified','read_only','observed_at','tests_complete','outcome']}
   items.append(v)
  out[t]={'table_present':present,'rows':items}
 out['gateway_qa_test_call_count']=sum(x['operation']=='repository.test'for x in calls)
 assert c.total_changes==0
finally:c.close()
profile=config['agentic_repository_gateway']['projects']['PROJ-0198'];assert profile['repository']==packet['repository']and profile['environment']==packet['environment']
base=Path(profile['workspace_root']);assert base.is_absolute()and str(base).startswith('/srv/abit-dev-linux/')and'..'not in base.parts
work=base/r['erp_task']/Q/'repository';art=Path(config.get('agentic_artifact_roots',{}).get('PROJ-0198','/srv/abit-dev-linux/abit-ai/data/governance-artifacts/PROJ-0198'))/r['erp_task']/Q
out['server_selected_profile']={'sha256':sha(can(profile)),'workspace_path':str(work),'artifact_path':str(art),'has_operator_config_pin_for_qa':Q in profile.get('config_sha256_by_invocation',{}),'registered_test_ids':sorted((profile.get('tests')or{})),'profile_matches_packet':True}
out['workspace']={'root':path_meta(work),'git_directory':path_meta(work/'.git'),'git_config':path_meta(work/'.git/config')}
out['artifact_directory']=path_meta(art)
if out['artifact_directory']['exists']and out['artifact_directory']['directory']and not out['artifact_directory']['symlink']:
 entries=list(art.iterdir());assert len(entries)<=100;out['artifact_entries']=[{'name':p.name,'metadata':path_meta(p)}for p in sorted(entries)]
else:out['artifact_entries']=[]
children=proc.joinpath('task',str(pid),'children').read_text().split();assert len(children)<=32;out['main_direct_children']=[]
for cid in children:
 p=Path('/proc')/cid
 try:s=p.joinpath('stat').read_text().rsplit(')',1)[1].split();out['main_direct_children'].append({'pid':int(cid),'birth':s[19],'state':s[0],'command_sha256':sha(p.joinpath('cmdline').read_bytes())})
 except FileNotFoundError:out['main_direct_children'].append({'pid':int(cid),'disappeared_during_read':True})
out['worker_linkage_limit']='verify_delivery itself launches no subprocess; main-child metadata is not attribution of unrelated workers.'
c=ro('abitdot-primary.sqlite')
try:
 vals=[dict(x)for x in c.execute('SELECT status,response_text,receipt,envelope_json FROM dot_primary_runs WHERE original_run_id=?',(U,))];assert len(vals)==1
 out['provider']=[{'status':x['status'],'response_present':bool(x['response_text']),'receipt_present':bool(x['receipt']),'envelope_sha256':sha(x['envelope_json'])}for x in vals];assert c.total_changes==0
finally:c.close()
c=ro('maintenance-admission/gate.sqlite')
try:out['gate']=[dict(x)for x in c.execute('SELECT state,epoch FROM gate')];out['active_admission_count']=c.execute("SELECT count(*)FROM lease WHERE state IN('active','unresolved')").fetchone()[0];assert c.total_changes==0
finally:c.close()
out['source_sha256']={str(L/p):sha((L/p).read_bytes())for p in ['utils/agentic_gateway_delivery.py','utils/agentic_gateway_operations.py','utils/agentic_gateway_sessions.py','utils/agentic_tool_gateway.py','utils/agentic_artifacts.py','routers/agentic_governance.py']}
assert proc.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]==birth
out['finished_utc']=datetime.now(timezone.utc).isoformat()
print(json.dumps(out,sort_keys=True))

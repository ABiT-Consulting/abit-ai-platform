"""Independent read-only QA for exact completed inspection/quarantine actions.

This module neither executes nor recovers a mutating adapter. Retained-inode
checks prove current quarantine bytes and identity; old receipts lack a before
content hash, so they do not prove historical byte equality.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from open_webui.utils import agentic_governance as ledger
from open_webui.utils import agentic_tool_gateway as gateway
from open_webui.utils.agentic_bridge import canonical,digest,packet_digest,same_business_context
from open_webui.utils.agentic_contracts.validator import action_fingerprint
from open_webui.utils.agentic_it_qa import _owned
from open_webui.utils.agentic_result_store import get_result

RPC='/api/method/abit_ai_assistant.agentic_it_plan.get_it_routine_implementation_receipt'
OPERATIONS={'inspect','cleanup_disposable'}

def require(value,message):
    if not value:raise ValueError(message)


def _source(row,packet,path):
    from open_webui.utils.agentic_executor import implementation_source
    require(row['target_role']=='qa','Routine infrastructure QA requires its current QA role')
    source=implementation_source(row,packet,path)
    if source['target_role']!='it_manager':return None
    original=json.loads(source['work_packet']);template=original.get('tool_action') or {}
    if template.get('operation') not in OPERATIONS:return None
    require(source['agent_id']!=row['agent_id'] and original['binding']['erp_user']!=packet['binding']['erp_user'],
            'Routine IT implementer cannot accept its own work')
    published=get_result(source['invocation_id'],path)
    require(published and published['phase']=='published' and published['result'].get('outcome')=='ready_for_review',
            'Routine IT source requires its actual immutable ready publication')
    with gateway._connect(path) as db:
        records=db.execute("SELECT * FROM governance_action WHERE invocation_id=?",(source['invocation_id'],)).fetchall()
    require(len(records)==1 and records[0]['state']=='completed','Routine IT source requires one exact completed original action')
    record=dict(records[0]);action=json.loads(record['action_json']);before=json.loads(record['preflight_json'])
    consumed=json.loads(record['approval_json'] or 'null');actual=gateway.get_action(source['invocation_id'],record['action_id'],path)
    require(action.get('actor')==original['binding']
            and all(action.get(k)==original.get(k) for k in ('project_id','task_id','task_revision','repository','environment'))
            and all(action.get(k)==template.get(k) for k in ('operation','risk','parameters'))
            and record['request_hash']==digest([record['action_id'],action_fingerprint(action)])
            and record['action_id']=='agov-action-'+digest([source['invocation_id'],template])[:48],
            'Routine IT original action scope or deterministic identity differs')
    if action['operation']=='inspect':
        require(action['risk']=='routine' and consumed is None and actual['result'].get('read_only') is True,
                'Inspection QA requires an original read-only action without mutation authority')
    else:
        require(isinstance(consumed,dict) and consumed.get('consumed') is True
                and consumed.get('run_id')==source['automation_run_id'] and consumed.get('invocation_id')==source['invocation_id']
                and consumed.get('action_id')==record['action_id'] and consumed.get('receipt_id')
                and consumed.get('action_fingerprint')==action_fingerprint(action) and actual['result'].get('deleted') is False,
                'Quarantine QA requires its exact original consumed approval and retained-byte receipt')
    return source,published,record,action,before,consumed,actual


async def _native(row,packet,bridge,path):
    require(bridge is not None,'Routine IT QA requires the existing governed ERP bridge')
    _owned(row,packet,path);source=_source(row,packet,path)
    require(source is not None,'Routine QA requires its authoritative inspect/quarantine predecessor')
    predecessor,published,record,action,before,consumed,actual=source
    response=await bridge.erp.request('GET',RPC,params={'project_id':row['erp_project'],'task_id':row['erp_task'],
        'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],'qa_agent_id':row['agent_id'],
        'expected_task_revision':packet['task_revision']},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    proof=response.get('message') or {}
    expected={'schema_version':1,'kind':'it_routine_implementation','project_id':row['erp_project'],'task_id':row['erp_task'],
        'task_revision':packet['task_revision'],'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],
        'qa_agent_id':row['agent_id'],'qa_erp_user':packet['binding']['erp_user'],
        'implementation_run_id':predecessor['automation_run_id'],'implementation_invocation_id':predecessor['invocation_id'],
        'implementation_agent_id':predecessor['agent_id'],'implementation_erp_user':json.loads(predecessor['work_packet'])['binding']['erp_user'],
        'operation':action['operation'],'action_id':record['action_id'],'action_fingerprint':action_fingerprint(action),
        'action_receipt_sha256':digest(actual),'consumed_approval_receipt_id':consumed['receipt_id'] if consumed else None,
        'ready_event_id':published['event']['event_id'],'ready_result_sha256':digest(published['event'])}
    require(all(proof.get(k)==v for k,v in expected.items()),'Native routine IT source proof differs from original Core receipts')
    stable={k:published['receipt'][k] for k in ('event_id','run_id','task_id','modified','status','durable')}
    evidence=[item for item in published['result'].get('evidence',[]) if item.get('id')=='action-receipt']
    require(proof.get('ready_receipt_sha256')==digest(stable) and len(evidence)==1
            and proof.get('action_file_sha256')==evidence[0]['sha256'],'Original routine IT publication/private File differs')
    current=await bridge.packet(row['erp_project'],row['erp_task'],'qa',row['agent_id'])
    require(same_business_context(packet,current,('source_modified','project_revision','content_sha256','packet_id')),
            'Routine QA business context changed before current readback')
    return proof,source


def _private(info,uid,*,directory=False):
    return info.st_uid==uid and not info.st_mode&0o077 and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1)


def _retained_quarantine(source,profile):
    """Descriptor-only observation; no rename, cleanup, deletion or restoration."""
    predecessor,published,record,action,before,consumed,actual=source
    require((profile.get('routine_qa_policy') or {}).get('cleanup_disposable')=='retained_inode_v1',
            'Retained quarantine QA requires its explicit server-approved verification policy')
    keys=action['parameters'].get('resource_ids');resources=actual['result'].get('resources')
    require(isinstance(keys,list) and keys and len(keys)<=20 and len(keys)==len(set(keys))
            and isinstance(resources,list) and len(resources)==len(keys)
            and {item.get('id') for item in resources}==set(keys),'Original quarantine inventory is not exact')
    root=gateway._open_dir(profile['quarantine_root']);directory=None
    try:
        root_info=os.fstat(root);require(_private(root_info,profile['expected_uid'],directory=True),'Quarantine root is not private')
        name='action-'+digest([predecessor['invocation_id'],record['action_id']])[:40]
        directory=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        require(_private(os.fstat(directory),profile['expected_uid'],directory=True),'Original quarantine directory is not protected')
        handle=os.open('receipt.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(handle,'rb') as stream:
            require(_private(os.fstat(stream.fileno()),profile['expected_uid']),'Original quarantine manifest is not protected')
            raw=stream.read(64001)
        require(len(raw)<=64000,'Quarantine manifest exceeds read bound');manifest=json.loads(raw)
        require(manifest.get('action_id')==record['action_id'] and manifest.get('invocation_id')==predecessor['invocation_id']
                and manifest.get('state')=='quarantined' and manifest.get('moved')==resources
                and manifest.get('resources')==before.get('resources')
                and manifest.get('change_preflight')==before.get('change_preflight')==actual['result'].get('change_preflight'),
                'Original quarantine manifest/protected action receipt differs')
        disk=os.fstatvfs(directory);free=disk.f_bavail*disk.f_frsize
        minimum=profile.get('minimum_free_bytes')
        require(type(minimum) is int and minimum>=0 and free>=minimum,'Current retained quarantine capacity is below its reviewed minimum')
        observed=[]
        for item in resources:
            registered=(profile.get('resources') or {}).get(item['id'])
            require(isinstance(registered,dict) and not gateway._protected(registered.get('path',''),registered.get('classification'),profile)
                    and type(registered.get('retention_until')) is int and registered['retention_until']<=int(time.time())
                    and all(registered.get(k)==item.get(k) for k in ('path','classification','retention_until','device','inode','uid','size','mtime_ns')),
                    'Quarantined resource is protected or differs from server inventory')
            source_root,source_name=gateway._root_for(registered['path'],profile.get('disposable_roots',[]))
            source_fd=gateway._open_dir(source_root)
            try:
                try:os.stat(source_name,dir_fd=source_fd,follow_symlinks=False)
                except FileNotFoundError:pass
                else:raise ValueError('Original source path is occupied; quarantine acceptance requires reconciliation')
            finally:os.close(source_fd)
            target=item['id']+'.quarantined';expected_path=str(Path(profile['quarantine_root'])/name/target)
            require(item.get('quarantine_path')==expected_path,'Original quarantine path differs from server-derived scope')
            descriptor=os.open(target,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            try:
                info=os.fstat(descriptor)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not info.st_mode&0o022
                        and info.st_dev==root_info.st_dev and info.st_size<=profile.get('max_cleanup_bytes',2000000)
                        and all(item.get(k)==v for k,v in {'device':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,
                            'size':info.st_size,'mtime_ns':info.st_mtime_ns}.items()),'Retained quarantine inode/geometry changed')
                before_info=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
                hasher=hashlib.sha256();remaining=info.st_size
                while remaining:
                    block=os.read(descriptor,min(remaining,65536));require(block,'Retained quarantine bytes became truncated')
                    remaining-=len(block);hasher.update(block)
                final=os.fstat(descriptor)
                require(before_info==(final.st_dev,final.st_ino,final.st_size,final.st_mtime_ns,final.st_ctime_ns),
                        'Retained quarantine changed during independent read')
                observed.append({'id':item['id'],'device':info.st_dev,'inode':info.st_ino,'size':info.st_size,'mtime_ns':info.st_mtime_ns,
                    'current_sha256':hasher.hexdigest(),'original_sha256_available':False,'source_absent':True})
            finally:os.close(descriptor)
        return {'policy':'retained_inode_v1','resources':observed,'deleted':False,'host_id':profile['host_id'],
                'minimum_free_bytes':minimum,'capacity_sufficient':True,'manifest_sha256':hashlib.sha256(raw).hexdigest()}
    finally:
        if directory is not None:os.close(directory)
        os.close(root)


async def _observe(source,profile):
    predecessor,published,record,action,before,consumed,actual=source
    require(action['operation'] in profile.get('operations',[]),'Routine operation is no longer registered')
    if action['operation']=='inspect':
        policy=(profile.get('routine_qa_policy') or {}).get('inspect')
        require(isinstance(policy,dict) and policy.get('mode')=='inspection_v1' and type(policy.get('require_healthy_services')) is bool,
                'Inspection QA requires explicit server-owned acceptance thresholds')
        current=await gateway._inspect_preflight(action,{},profile)
        require(all(current.get(k)==before.get(k) for k in ('host_id','root_keys','services')),
                'Inspection original roots/services differ from current server profile')
        result=await gateway._inspect(action,{},current,profile)
        thresholds=policy.get('minimum_free_bytes')
        require(isinstance(thresholds,dict) and set(thresholds)==set(result['roots']), 'Every inspected root needs its exact capacity threshold')
        original=actual['result'];roots={}
        require(original.get('host_id')==result['host_id'] and set(original.get('roots',{}))==set(result['roots'])
                and set(original.get('services',{}))==set(result['services']),'Inspection original identity differs')
        for key,observed in result['roots'].items():
            require(type(thresholds[key]) is int and thresholds[key]>=0 and observed['free_bytes']>=thresholds[key]
                    and all(observed[k]==original['roots'][key].get(k) for k in ('device','owner_uid','total_bytes')),
                    'Inspection filesystem identity/geometry or current capacity differs')
            roots[key]={k:observed[k] for k in ('device','owner_uid','total_bytes')}
            roots[key].update(minimum_free_bytes=thresholds[key],capacity_sufficient=True)
        if policy['require_healthy_services']:
            require(all(item['healthy'] for item in result['services'].values()),'Inspected service is not currently healthy')
        return {'policy':'inspection_v1','host_id':result['host_id'],'roots':roots,'services':result['services'],'read_only':True}
    observation=_retained_quarantine(source,profile)
    observation['services']=await gateway._health(profile.get('services',[]))
    require(all(item['healthy'] for item in observation['services'].values()),'Quarantine affected service is not currently healthy')
    return observation


async def _readback(row,packet,config,bridge,path,check_authority=None):
    if check_authority:await check_authority()
    proof,source=await _native(row,packet,bridge,path)
    profile=gateway._profile(config,row['erp_project']);profile_sha=digest(profile)
    observation=await _observe(source,profile)
    require(profile_sha==digest(gateway._profile(config,row['erp_project'])),'Routine QA server profile changed during readback')
    fresh,_=await _native(row,packet,bridge,path);require(fresh==proof,'Routine native source authority changed during readback')
    if check_authority:await check_authority()
    return proof,observation,profile_sha


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_it_routine_qa(
        invocation_id TEXT NOT NULL,call_id TEXT NOT NULL,packet_sha256 TEXT NOT NULL,proof_json TEXT NOT NULL,
        observation_json TEXT NOT NULL,profile_sha256 TEXT NOT NULL,artifact_json TEXT NOT NULL,PRIMARY KEY(invocation_id,call_id))''')
    for action in ('UPDATE','DELETE'):
        db.execute('CREATE TRIGGER IF NOT EXISTS routine_qa_no_'+action.lower()+' BEFORE '+action+
                   " ON governance_it_routine_qa BEGIN SELECT RAISE(ABORT,'Routine QA is immutable'); END")


async def verify_original(args,*,row,packet,config,bridge,db_path=None,gateway_call_id=None,check_authority=None,**kwargs):
    require(args=={} and gateway_call_id and callable(check_authority),'Routine QA uses only its actual scoped gateway with no selected target')
    proof,observed,profile_sha=await _readback(row,packet,config,bridge,db_path,check_authority)
    from open_webui.utils.agentic_artifacts import artifact_directory,ensure_directory,local_bytes
    identity='routine-qa-'+digest([row['invocation_id'],gateway_call_id])[:32]
    raw=canonical({'native_implementation':proof,'current_readback':observed,'profile_sha256':profile_sha}).encode()
    require(len(raw)<=32000,'Routine QA evidence exceeds its bound')
    artifact={'id':identity,'kind':'test_log','uri':'artifact://'+identity+'.json','sha256':hashlib.sha256(raw).hexdigest(),'requirement_ids':packet['requirement_ids']}
    root=artifact_directory(row,config);ensure_directory(root)
    try:fd=os.open(root/(identity+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
    except FileExistsError:
        _,old=local_bytes(root,artifact['uri']);require(old==raw,'Original routine QA evidence bytes differ')
    else:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o640)
    with ledger.connect(db_path) as db:
        _table(db)
        require(db.execute('SELECT count(*) FROM governance_it_routine_qa WHERE invocation_id=?',(row['invocation_id'],)).fetchone()[0]<10,
                'Routine QA exceeded its bounded observation count')
        db.execute('INSERT INTO governance_it_routine_qa VALUES(?,?,?,?,?,?,?)',(row['invocation_id'],gateway_call_id,packet_digest(packet),
            canonical(proof),canonical(observed),profile_sha,canonical(artifact)))
    return {'verified':True,'executed':False,'observation':observed,'evidence':artifact}


async def assert_routine_qa_result(row,packet,result,config,db_path=None,*,bridge=None):
    if _source(row,packet,db_path) is None:return False
    require(result.get('outcome')=='pass','Routine acceptance requires independent QA pass')
    with ledger.connect(db_path) as db:
        _table(db);records=db.execute('SELECT * FROM governance_it_routine_qa WHERE invocation_id=?',(row['invocation_id'],)).fetchall()
    evidence={item.get('id'):item for item in result.get('evidence',[])}
    matches=[record for record in records if record['packet_sha256']==packet_digest(packet)
        and evidence.get(json.loads(record['artifact_json'])['id'])==json.loads(record['artifact_json'])]
    require(len(matches)==1,'Routine QA requires its own exact original gateway readback evidence')
    chosen=matches[0];artifact=json.loads(chosen['artifact_json']);proof=json.loads(chosen['proof_json']);observation=json.loads(chosen['observation_json'])
    tests=result.get('test_results') or []
    require(tests and all(test.get('status')=='pass' and artifact['id'] in test.get('evidence_ids',[]) for test in tests),
            'Every routine acceptance test requires its own independent evidence')
    from open_webui.utils.agentic_artifacts import artifact_directory,local_bytes
    _,raw=local_bytes(artifact_directory(row,config),artifact['uri'])
    require(hashlib.sha256(raw).hexdigest()==artifact['sha256'] and json.loads(raw)=={
        'native_implementation':proof,'current_readback':observation,'profile_sha256':chosen['profile_sha256']},'Routine QA evidence bytes changed')
    fresh,current,profile_sha=await _readback(row,packet,config,bridge,db_path)
    require(fresh==proof and current==observation and profile_sha==chosen['profile_sha256'],'Routine current readback changed after independent QA')
    return True

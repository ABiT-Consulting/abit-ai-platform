"""Independent QA of an authoritative physical IT predecessor, with own bytes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical,digest,packet_digest,same_business_context
from open_webui.utils.agentic_result_store import get_result
from open_webui.utils import agentic_tool_gateway as gateway
from open_webui.utils.agentic_it_broker import BrokerClient
from open_webui.utils.agentic_it_changes import OPERATIONS,plan_authority_digest
from open_webui.utils.agentic_contracts.validator import action_fingerprint

RPC='/api/method/abit_ai_assistant.agentic_it_plan.get_it_implementation_receipt'


def require(value,message):
    if not value:raise ValueError(message)


def _source(row,packet,path):
    require(row['target_role']=='qa' and row['erp_project']==packet['project_id'] and row['erp_task']==packet['task_id'],
            'Independent physical QA scope differs')
    from open_webui.utils.agentic_executor import implementation_source
    source=implementation_source(row,packet,path)
    if source['target_role']!='it_manager':return None
    original=json.loads(source['work_packet'])
    require(source['agent_id']!=row['agent_id'] and original['binding']['erp_user']!=packet['binding']['erp_user'],
            'IT implementer cannot perform its own independent QA')
    published=get_result(source['invocation_id'],path)
    require(published and published['phase']=='published' and published['result'].get('outcome')=='ready_for_review',
            'IT predecessor has no authoritative actual ready publication')
    with gateway._connect(path) as db:
        actions=db.execute("SELECT * FROM governance_action WHERE invocation_id=? AND state='completed'",(source['invocation_id'],)).fetchall()
    require(len(actions)==1,'Published IT predecessor requires one exact completed physical action')
    action_row=dict(actions[0]);action=json.loads(action_row['action_json']);plan=json.loads(action_row['preflight_json'])
    consumed=json.loads(action_row['approval_json'] or 'null');actual=json.loads(action_row['result_json'] or 'null')
    template=original.get('tool_action') or {}
    require(action.get('operation') in OPERATIONS and action.get('actor')==original['binding']
            and all(action.get(key)==original.get(key) for key in ('project_id','task_id','task_revision','repository','environment'))
            and all(action.get(key)==template.get(key) for key in ('operation','risk','parameters'))
            and action_row['request_hash']==gateway._digest([action_row['action_id'],action_fingerprint(action)])
            and plan.get('plan_digest')==plan_authority_digest(plan),'Original IT physical action authority differs')
    require(plan.get('kind')=='it_change_plan' and action.get('parameters',{}).get('plan_digest')==plan.get('plan_digest')
            and isinstance(consumed,dict) and consumed.get('consumed') is True
            and consumed.get('run_id')==source['automation_run_id'] and consumed.get('invocation_id')==source['invocation_id']
            and consumed.get('action_id')==action_row['action_id'] and consumed.get('receipt_id')
            and consumed.get('action_fingerprint')==action_fingerprint(action)
            and isinstance(actual,dict) and actual.get('original_receipt',{}).get('receipt_mac'),
            'IT predecessor lacks its exact consumed authority and authenticated host completion')
    return source,published,action_row,plan,consumed,actual


def _owned(row,packet,path):
    with ledger.connect(path) as db:
        fresh=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
        require(fresh and fresh['state'] in {'running','uncertain'} and fresh['lease_token']==row['lease_token']
                and fresh['automation_run_id']==row['automation_run_id'] and fresh['lease_expires_at']>int(time.time()),
                'Independent QA lost its original active lease')
        lease=db.execute('SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
                         (row['erp_project'],row['erp_task'])).fetchone()
        require(lease and tuple(lease)==(row['invocation_id'],row['lease_fence']),'Independent QA task fence differs')
        ledger._task_gate(db,row['erp_project'],row['erp_task'])
        require(packet_digest(json.loads(fresh['work_packet']))==packet_digest(packet),'Independent QA immutable packet differs')


async def _native(row,packet,bridge,path):
    require(bridge is not None,'Physical QA requires the existing governed ERP bridge')
    _owned(row,packet,path)
    expected=_source(row,packet,path)
    require(expected is not None,'Physical QA operation requires an authoritative IT source')
    source,published,action_row,plan,consumed,actual=expected
    response=await bridge.erp.request('GET',RPC,params={'project_id':row['erp_project'],'task_id':row['erp_task'],
        'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],'qa_agent_id':row['agent_id'],
        'expected_task_revision':packet['task_revision']},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    proof=response.get('message') or {}
    expected_fields={'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],
        'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],'qa_agent_id':row['agent_id'],
        'qa_erp_user':packet['binding']['erp_user'],'implementation_run_id':source['automation_run_id'],
        'implementation_invocation_id':source['invocation_id'],'implementation_agent_id':source['agent_id'],
        'implementation_erp_user':json.loads(source['work_packet'])['binding']['erp_user'],'action_id':action_row['action_id'],
        'plan_digest':plan['plan_digest'],'action_fingerprint':consumed['action_fingerprint'],
        'consumed_approval_receipt_id':consumed['receipt_id'],'ready_event_id':published['event']['event_id'],
        'ready_result_sha256':digest(published['event']),'host_receipt_sha256':digest(actual['original_receipt'])}
    require(isinstance(proof,dict) and all(proof.get(key)==value for key,value in expected_fields.items()),
            'Native authoritative IT predecessor proof differs from original Core receipts')
    stable={key:published['receipt'][key] for key in ('event_id','run_id','task_id','modified','status','durable')}
    artifacts=[item for item in published['result'].get('evidence',[]) if item.get('id')=='action-receipt']
    require(proof.get('ready_receipt_sha256')==digest(stable) and len(artifacts)==1
            and artifacts[0].get('sha256')==proof.get('action_file_sha256'),
            'Native original IT publication/File receipt differs')
    return proof,expected


async def _readback(row,packet,config,bridge,path,check_authority=None):
    if check_authority:await check_authority()
    proof,source=await _native(row,packet,bridge,path)
    selected=config.get('agentic_it_physical_broker') or {}
    require(isinstance(selected,dict) and selected.get('enabled') is True,'Physical QA broker is not registered')
    payload={'action_id':proof['action_id'],'invocation_id':proof['implementation_invocation_id'],
        'run_id':proof['implementation_run_id'],'plan_digest':proof['plan_digest'],'project_id':row['erp_project'],'task_id':row['erp_task'],
        'qa_invocation_id':row['invocation_id'],'qa_run_id':row['automation_run_id'],'qa_agent_id':row['agent_id'],
        'native_implementation_receipt_sha256':digest(proof)}
    async def authorize(operation,requested):
        require(operation=='verify' and requested==payload,'QA broker accepts only its derived original tuple')
        fresh,_=await _native(row,packet,bridge,path)
        require(fresh==proof,'Native IT/QA authority changed during host read admission')
        current=await bridge.packet(row['erp_project'],row['erp_task'],'qa',row['agent_id'])
        require(same_business_context(packet,current,('source_modified','project_revision','content_sha256','packet_id')),
                'QA business scope changed before current host readback')
        return {'authorized':True,'mode':'verify','role':'qa',**payload,'request_payload_sha256':digest(requested)}
    client=BrokerClient(selected['socket_path'],selected['key_file'],authorize=authorize)
    observed=await client.request('verify',payload)
    require(isinstance(observed,dict) and observed.get('kind')=='independent_qa_current_readback'
            and observed.get('verified') is True and observed.get('executed') is False
            and all(observed.get(key)==value for key,value in payload.items()) and observed.get('receipt_mac')
            and observed.get('host_receipt_sha256')==proof['host_receipt_sha256']
            and type(observed.get('checked_at')) is int and 0<=int(time.time())-observed['checked_at']<=30,
            'Independent QA host readback is not the exact current original resource')
    fresh,_=await _native(row,packet,bridge,path)
    require(fresh==proof,'Native QA authority changed before recording independent evidence')
    if check_authority:await check_authority()
    return proof,observed


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_it_qa_verification(
        invocation_id TEXT NOT NULL, call_id TEXT NOT NULL, packet_sha256 TEXT NOT NULL,
        proof_json TEXT NOT NULL, observation_json TEXT NOT NULL, artifact_json TEXT NOT NULL,
        PRIMARY KEY(invocation_id,call_id))''')
    for action in ('UPDATE','DELETE'):
        db.execute('CREATE TRIGGER IF NOT EXISTS it_qa_no_'+action.lower()+
                   ' BEFORE '+action+" ON governance_it_qa_verification BEGIN SELECT RAISE(ABORT,'Independent IT QA evidence is immutable'); END")


async def verify_original(args,*,row,packet,config,bridge,db_path=None,gateway_call_id=None,check_authority=None,**kwargs):
    require(args=={} and row['target_role']=='qa' and gateway_call_id,'Independent physical QA uses no caller-selected target')
    proof,observed=await _readback(row,packet,config,bridge,db_path,check_authority)
    from open_webui.utils.agentic_artifacts import artifact_directory,ensure_directory,local_bytes
    ident='infra-qa-'+digest([row['invocation_id'],gateway_call_id])[:32];name=ident+'.json'
    content=canonical({'native_implementation':proof,'independent_observation':observed}).encode()
    require(len(content)<=32000,'Independent physical QA evidence exceeds its fixed bound')
    artifact={'id':ident,'kind':'test_log','uri':'artifact://'+name,'sha256':hashlib.sha256(content).hexdigest(),'requirement_ids':packet['requirement_ids']}
    root=artifact_directory(row,config);ensure_directory(root)
    try:
        descriptor=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
    except FileExistsError:
        _,prior=local_bytes(root,'artifact://'+name)
        require(prior==content,'Original independent QA evidence bytes differ; never overwrite')
    else:
        with os.fdopen(descriptor,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o640)
    with ledger.connect(db_path) as db:
        _table(db)
        require(db.execute('SELECT count(*) FROM governance_it_qa_verification WHERE invocation_id=?',(row['invocation_id'],)).fetchone()[0]<10,
                'Independent physical QA reached its bounded observation limit')
        db.execute('INSERT INTO governance_it_qa_verification VALUES(?,?,?,?,?,?)',
            (row['invocation_id'],gateway_call_id,packet_digest(packet),canonical(proof),canonical(observed),canonical(artifact)))
    return {'verified':True,'executed':False,'implementation_invocation_id':proof['implementation_invocation_id'],
        'implementation_run_id':proof['implementation_run_id'],'observation':observed,'evidence':artifact}


async def assert_it_qa_result(row,packet,result,config,db_path=None,*,bridge=None):
    """True only for an authoritative IT source; other executors retain their gates."""
    source=_source(row,packet,db_path)
    if source is None:return False
    require(result.get('outcome')=='pass','Physical QA acceptance requires a pass result')
    with ledger.connect(db_path) as db:
        _table(db)
        saved=db.execute('SELECT * FROM governance_it_qa_verification WHERE invocation_id=?',(row['invocation_id'],)).fetchall()
    evidence={item.get('id'):item for item in result.get('evidence',[])}
    matches=[item for item in saved if item['packet_sha256']==packet_digest(packet)
             and evidence.get(json.loads(item['artifact_json'])['id'])==json.loads(item['artifact_json'])]
    require(len(matches)==1,'Physical QA pass requires its own exact actual gateway readback artifact')
    chosen=matches[0];artifact=json.loads(chosen['artifact_json']);proof=json.loads(chosen['proof_json']);observed=json.loads(chosen['observation_json'])
    tests=result.get('test_results') or []
    require(tests and all(test.get('status')=='pass' and artifact['id'] in test.get('evidence_ids',[]) for test in tests),
            'Every physical acceptance test requires its own independent readback evidence')
    from open_webui.utils.agentic_artifacts import artifact_directory,local_bytes
    _,content=local_bytes(artifact_directory(row,config),artifact['uri'])
    require(hashlib.sha256(content).hexdigest()==artifact['sha256'] and json.loads(content)=={'native_implementation':proof,'independent_observation':observed},
            'Independent QA evidence bytes changed after host verification')
    fresh,current=await _readback(row,packet,config,bridge,db_path)
    stable=lambda value:{key:item for key,item in value.items() if key not in {'checked_at','receipt_mac'}}
    require(fresh==proof and stable(current)==stable(observed),'Host state changed after independent QA observation')
    return True

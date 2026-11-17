"""OFFLINE increment: fixed ERP controls-audit connector, real receipt only."""

from __future__ import annotations

import hashlib
import json
import os

from open_webui.utils import agentic_governance as ledger
from open_webui.utils import agentic_tool_gateway as gateway
from open_webui.utils.agentic_bridge import canonical,digest,packet_digest

RPC='/api/method/abit_ai_assistant.agentic_operations.'
POLICY='erp_project_controls_audit_v1'


def require(value,message):
    if not value:raise ValueError(message)


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_operations_execution(
        invocation_id TEXT NOT NULL, request_id TEXT NOT NULL, packet_sha256 TEXT NOT NULL,
        receipt_json TEXT NOT NULL, artifact_json TEXT NOT NULL, PRIMARY KEY(invocation_id,request_id))''')
    for action in ('UPDATE','DELETE'):
        db.execute('CREATE TRIGGER IF NOT EXISTS operations_no_'+action.lower()+' BEFORE '+action+
                   " ON governance_operations_execution BEGIN SELECT RAISE(ABORT,'Operational receipts are immutable'); END")


def _validate(receipt,row,packet,request_id):
    require(isinstance(receipt,dict) and receipt.get('exists') is True and receipt.get('durable') is True
            and receipt.get('record') and receipt.get('policy_id')==POLICY,'Actual native operational receipt is unavailable')
    body={key:value for key,value in receipt.items() if key not in {'exists','durable','record','content_sha256'}}
    expected={'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],
        'run_id':row['automation_run_id'],'invocation_id':row['invocation_id'],'agent_id':row['agent_id'],
        'erp_user':packet['binding']['erp_user'],'request_id':request_id}
    require(all(body.get(key)==value for key,value in expected.items()) and receipt.get('content_sha256')==digest(body),
            'Operational receipt scope or immutable native content differs')
    snapshot=body.get('snapshot') or {}
    require(body.get('snapshot_sha256')==digest(snapshot)
            and all(snapshot.get(key)==packet.get(key) for key in ('project_id','task_id','task_revision','repository','environment'))
            and snapshot.get('srs_revision')==packet['srs']['revision'] and snapshot.get('srs_sha256')==packet['srs']['sha256']
            and snapshot.get('governance_enabled') is True and snapshot.get('pm_task_automation_enabled') is True
            and snapshot.get('project_status') in {'Open','Active','In Progress'}
            and body.get('business_document_mutation') is False and body.get('external_communication') is False
            and body.get('host_execution') is False,'Native controls-audit criteria do not match this original operational task')
    bindings=snapshot.get('bindings')
    require(isinstance(bindings,list) and len(bindings)==6 and len({item.get('agent_id') for item in bindings})==6
            and {item.get('role') for item in bindings}=={'solution_architect','project_manager','developer','qa','operations','it_manager'}
            and all(item.get('verification_state')=='Verified' and item.get('is_enabled')==1 for item in bindings)
            and any(item.get('role')=='operations' and item.get('agent_id')==row['agent_id']
                    and item.get('erp_user')==packet['binding']['erp_user'] for item in bindings),
            'Operational receipt has no actual six-agent/current Operations identity proof')
    return body


def _artifact(receipt,request_id,*,row,packet,config,path):
    from open_webui.utils.agentic_artifacts import artifact_directory,ensure_directory,local_bytes
    root=artifact_directory(row,config);ensure_directory(root)
    name='ops-audit-'+digest(request_id)[:40]+'.json';content=canonical({'native_operational_receipt':receipt}).encode()
    require(len(content)<=32000,'Operational evidence exceeds the fixed artifact bound')
    artifact={'id':'operations-receipt','kind':'test_log','uri':'artifact://'+name,'sha256':hashlib.sha256(content).hexdigest(),
              'requirement_ids':packet['requirement_ids']}
    try:descriptor=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
    except FileExistsError:
        _,prior=local_bytes(root,'artifact://'+name);require(prior==content,'Original operational evidence bytes changed')
    else:
        with os.fdopen(descriptor,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o640)
    with ledger.connect(path) as db:
        _table(db)
        prior=db.execute('SELECT * FROM governance_operations_execution WHERE invocation_id=? AND request_id=?',(row['invocation_id'],request_id)).fetchone()
        values=(row['invocation_id'],request_id,packet_digest(packet),canonical(receipt),canonical(artifact))
        if prior:require(tuple(prior)==values,'Original operational journal differs')
        else:db.execute('INSERT INTO governance_operations_execution VALUES(?,?,?,?,?)',values)
    return artifact


async def record_controls_audit(args,*,row,packet,config,bridge,db_path=None,gateway_call_id=None,check_authority=None,**kwargs):
    require(args=={} and row['target_role']=='operations' and gateway_call_id and callable(check_authority),
            'Fixed operational connector requires its actual scoped gateway authority')
    await check_authority()
    with gateway._connect(db_path) as db:current=gateway._owned(db,row['invocation_id'])
    require(current['automation_run_id']==row['automation_run_id'] and current['lease_token']==row['lease_token'],
            'Operational connector lost its original execution ownership')
    request_id='agov-ops-'+digest([row['invocation_id'],gateway_call_id])[:48]
    body={'project_id':row['erp_project'],'task_id':row['erp_task'],'run_id':row['automation_run_id'],
          'invocation_id':row['invocation_id'],'agent_id':row['agent_id'],'expected_task_revision':packet['task_revision'],'request_id':request_id}
    headers={'X-ABIT-Agentic-Bridge':bridge.secret}
    try:
        response=await bridge.erp.request('POST',RPC+'record_controls_audit',json=body,headers=headers)
        receipt=response.get('message')
    except Exception:
        # No second POST after an unknown acknowledgement.
        query={key:value for key,value in body.items() if key!='expected_task_revision'}
        response=await bridge.erp.request('GET',RPC+'get_controls_audit_receipt',params=query,headers=headers)
        receipt=response.get('message')
    _validate(receipt,row,packet,request_id)
    await check_authority()
    artifact=_artifact(receipt,request_id,row=row,packet=packet,config=config,path=db_path)
    return {'completed':True,'policy_id':POLICY,'business_document_mutation':False,'external_communication':False,
            'host_execution':False,'native_record':receipt['record'],'snapshot':receipt['snapshot'],'evidence':artifact}


async def assert_operations_ready(row,packet,result,config,db_path=None,*,bridge):
    require(row['target_role']=='operations' and result.get('outcome')=='ready_for_review',
            'Typed operational readiness requires its actual Operations implementation')
    evidence={item.get('id'):item for item in result.get('evidence',[])}
    with ledger.connect(db_path) as db:
        _table(db)
        saved=db.execute('SELECT * FROM governance_operations_execution WHERE invocation_id=?',(row['invocation_id'],)).fetchall()
    matched=[item for item in saved if item['packet_sha256']==packet_digest(packet)
             and json.loads(item['artifact_json'])==evidence.get('operations-receipt')]
    require(len(matched)==1,'Operational readiness requires one actual named connector receipt; unsupported work remains blocked')
    original=matched[0];receipt=json.loads(original['receipt_json']);_validate(receipt,row,packet,original['request_id'])
    query={'project_id':row['erp_project'],'task_id':row['erp_task'],'run_id':row['automation_run_id'],
           'invocation_id':row['invocation_id'],'agent_id':row['agent_id'],'request_id':original['request_id']}
    current=(await bridge.erp.request('GET',RPC+'get_controls_audit_receipt',params=query,headers={'X-ABIT-Agentic-Bridge':bridge.secret})).get('message')
    require(current==receipt,'Original operational receipt changed before publication')
    readback=(await bridge.erp.request('GET',RPC+'get_controls_audit_current',params={**query,'expected_task_revision':packet['task_revision']},
        headers={'X-ABIT-Agentic-Bridge':bridge.secret})).get('message') or {}
    require(readback.get('verified') is True and readback.get('record')==receipt['record']
            and readback.get('content_sha256')==receipt['content_sha256']
            and readback.get('snapshot_sha256')==receipt['snapshot_sha256'],
            'Current native operational controls no longer verify the original receipt')
    from open_webui.utils.agentic_artifacts import artifact_directory,local_bytes
    artifact=json.loads(original['artifact_json']);_,raw=local_bytes(artifact_directory(row,config),artifact['uri'])
    require(hashlib.sha256(raw).hexdigest()==artifact['sha256'] and json.loads(raw)=={'native_operational_receipt':receipt},
            'Original operational actual evidence bytes changed')
    return True

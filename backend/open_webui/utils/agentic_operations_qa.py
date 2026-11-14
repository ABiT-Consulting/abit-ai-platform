"""OFFLINE increment: actual independent QA for the named native audit only."""

from __future__ import annotations

import hashlib
import json
import os

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical,digest,packet_digest
from open_webui.utils.agentic_result_store import get_result
from open_webui.utils.agentic_it_qa import _owned
from open_webui.utils.agentic_operations import POLICY,_validate,_table as operations_table

RPC='/api/method/abit_ai_assistant.agentic_operations.get_controls_audit_for_qa'


def require(value,message):
    if not value:raise ValueError(message)


async def _proof(row,packet,bridge,path,check_authority=None):
    require(bridge is not None,'Independent operational QA requires the existing governed ERP bridge')
    if check_authority:await check_authority()
    _owned(row,packet,path)
    from open_webui.utils.agentic_executor import implementation_source
    source=implementation_source(row,packet,path)
    require(source['target_role']=='operations','Typed operational QA requires an authoritative Operations predecessor')
    source_packet=json.loads(source['work_packet'])
    require(source['agent_id']!=row['agent_id'] and source_packet['binding']['erp_user']!=packet['binding']['erp_user'],
            'Operations implementer cannot perform its own QA')
    published=get_result(source['invocation_id'],path)
    require(published and published['phase']=='published' and published['result'].get('outcome')=='ready_for_review',
            'Operations source has no immutable actual ready publication')
    response=await bridge.erp.request('GET',RPC,params={'project_id':row['erp_project'],'task_id':row['erp_task'],
        'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],'qa_agent_id':row['agent_id'],
        'expected_task_revision':packet['task_revision']},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    proof=response.get('message') or {}
    expected={'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],
        'qa_run_id':row['automation_run_id'],'qa_invocation_id':row['invocation_id'],'qa_agent_id':row['agent_id'],
        'qa_erp_user':packet['binding']['erp_user'],'implementation_run_id':source['automation_run_id'],
        'implementation_invocation_id':source['invocation_id'],'implementation_agent_id':source['agent_id'],
        'implementation_erp_user':source_packet['binding']['erp_user'],'ready_event_id':published['event']['event_id'],
        'ready_result_sha256':digest(published['event'])}
    require(proof.get('schema_version')==1 and proof.get('policy_id')==POLICY and proof.get('verified') is True
            and all(proof.get(key)==value for key,value in expected.items()),'Native operational QA provenance differs')
    stable={key:published['receipt'][key] for key in ('event_id','run_id','task_id','modified','status','durable')}
    refs=[item for item in published['result'].get('evidence',[]) if item.get('id')=='operations-receipt']
    require(proof.get('ready_receipt_sha256')==digest(stable) and len(refs)==1
            and proof.get('source_file_sha256')==refs[0]['sha256'],'Original operational publication/private bytes differ')
    with ledger.connect(path) as db:
        operations_table(db)
        records=db.execute('SELECT * FROM governance_operations_execution WHERE invocation_id=?',(source['invocation_id'],)).fetchall()
    matches=[]
    for record in records:
        receipt=json.loads(record['receipt_json'])
        if receipt.get('record')==proof.get('operational_record') and receipt.get('content_sha256')==proof.get('operational_content_sha256'):
            matches.append((record,receipt))
    require(len(matches)==1,'Operational QA has no exact original server-issued connector execution')
    record,receipt=matches[0];_validate(receipt,source,source_packet,record['request_id'])
    require(record['packet_sha256']==packet_digest(source_packet)
            and proof.get('current_snapshot')==receipt['snapshot'] and proof.get('current_snapshot_sha256')==receipt['snapshot_sha256']
            and all(proof.get(key) is False for key in ('business_document_mutation','external_communication','host_execution')),
            'Independent current native operational readback differs from the original actual audit')
    if check_authority:await check_authority()
    return proof


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_operations_qa(
        invocation_id TEXT NOT NULL,call_id TEXT NOT NULL,packet_sha256 TEXT NOT NULL,proof_json TEXT NOT NULL,
        artifact_json TEXT NOT NULL,PRIMARY KEY(invocation_id,call_id))''')
    for action in ('UPDATE','DELETE'):
        db.execute('CREATE TRIGGER IF NOT EXISTS operations_qa_no_'+action.lower()+' BEFORE '+action+
                   " ON governance_operations_qa BEGIN SELECT RAISE(ABORT,'Independent operational QA is immutable'); END")


async def verify_original(args,*,row,packet,config,bridge,db_path=None,gateway_call_id=None,check_authority=None,**kwargs):
    require(args=={} and row['target_role']=='qa' and gateway_call_id and callable(check_authority),
            'Independent operational readback requires its actual scoped QA gateway')
    proof=await _proof(row,packet,bridge,db_path,check_authority)
    from open_webui.utils.agentic_artifacts import artifact_directory,ensure_directory,local_bytes
    ident='operations-qa-'+digest([row['invocation_id'],gateway_call_id])[:32];name=ident+'.json'
    raw=canonical({'native_independent_operational_readback':proof}).encode();require(len(raw)<=32000,'Operational QA artifact exceeds bound')
    artifact={'id':ident,'kind':'test_log','uri':'artifact://'+name,'sha256':hashlib.sha256(raw).hexdigest(),'requirement_ids':packet['requirement_ids']}
    root=artifact_directory(row,config);ensure_directory(root)
    try:descriptor=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
    except FileExistsError:
        _,old=local_bytes(root,artifact['uri']);require(old==raw,'Original operational QA bytes differ')
    else:
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o640)
    with ledger.connect(db_path) as db:
        _table(db)
        require(db.execute('SELECT count(*) FROM governance_operations_qa WHERE invocation_id=?',(row['invocation_id'],)).fetchone()[0]<10,
                'Operational QA reached its bounded independent read count')
        db.execute('INSERT INTO governance_operations_qa VALUES(?,?,?,?,?)',(row['invocation_id'],gateway_call_id,packet_digest(packet),canonical(proof),canonical(artifact)))
    return {'verified':True,'policy_id':POLICY,'business_document_mutation':False,'external_communication':False,'host_execution':False,
            'current_snapshot':proof['current_snapshot'],'evidence':artifact}


async def assert_operations_qa_result(row,packet,result,config,db_path=None,*,bridge=None):
    from open_webui.utils.agentic_executor import implementation_source
    source=implementation_source(row,packet,db_path)
    if source['target_role']!='operations':return False
    require(result.get('outcome')=='pass','Operational acceptance requires its actual independent QA pass')
    with ledger.connect(db_path) as db:
        _table(db);rows=db.execute('SELECT * FROM governance_operations_qa WHERE invocation_id=?',(row['invocation_id'],)).fetchall()
    evidence={item.get('id'):item for item in result.get('evidence',[])}
    matches=[item for item in rows if item['packet_sha256']==packet_digest(packet)
             and evidence.get(json.loads(item['artifact_json'])['id'])==json.loads(item['artifact_json'])]
    require(len(matches)==1,'Operations QA pass requires its own actual typed current-readback artifact')
    chosen=matches[0];artifact=json.loads(chosen['artifact_json']);original=json.loads(chosen['proof_json'])
    tests=result.get('test_results') or []
    require(tests and all(test.get('status')=='pass' and artifact['id'] in test.get('evidence_ids',[]) for test in tests),
            'Every operational acceptance check requires actual independent evidence')
    from open_webui.utils.agentic_artifacts import artifact_directory,local_bytes
    _,raw=local_bytes(artifact_directory(row,config),artifact['uri'])
    require(hashlib.sha256(raw).hexdigest()==artifact['sha256'] and json.loads(raw)=={'native_independent_operational_readback':original},
            'Original independent operational evidence bytes changed')
    fresh=await _proof(row,packet,bridge,db_path)
    stable=lambda value:{key:item for key,item in value.items() if key!='checked_at'}
    require(stable(fresh)==stable(original),'Operational native scope/readback changed after independent QA')
    return True

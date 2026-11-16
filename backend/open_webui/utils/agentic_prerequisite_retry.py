"""One human-authorized PM review after a verified repository prerequisite repair.
Keeps the original published Blocked result immutable; never replays its request.
"""
from __future__ import annotations
import json
import time
from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest
from open_webui.utils.agentic_project_profiles import resolve_repository_profile, _template_policy
from open_webui.utils.agentic_result_store import get_result
from open_webui.utils.agentic_transport_retry import _authority

KIND = 'repository_prerequisite_retry_queued'
BUSINESS_FIELDS = ('project_id','task_id','task_revision','repository','environment','srs',
    'instructions','objective','requirement_ids','acceptance_tests','atomic_adoption')

def _prior(db, row, command):
    found = db.execute(
        "SELECT invocation_id,payload FROM governance_event WHERE erp_project=? AND kind=? "
        "AND json_extract(payload,'$.source_invocation')=? AND json_extract(payload,'$.command_id')=?",
        (row['erp_project'], KIND, row['invocation_id'], command)).fetchall()
    if len(found) > 1:
        raise ValueError('Ambiguous original repository prerequisite retry')
    return found[0]['invocation_id'] if found else None

def _blocked_registration(db, row, packet):
    # This is an actual completed tool receipt, never a model-only assertion.
    sessions = [r[0] for r in db.execute(
        'SELECT session_id FROM governance_gateway_session WHERE invocation_id=?', (row['invocation_id'],))]
    for session in sessions:
        for call in db.execute("SELECT result_json FROM governance_gateway_call WHERE session_id=? "
                               "AND operation='context.read' AND state='completed'", (session,)):
            value = json.loads(call['result_json'])
            if (value.get('invocation_id') == row['invocation_id']
                and value.get('work_packet',{}).get('content_sha256') == packet['content_sha256']
                and value.get('repository_registration',{}).get('ready') is False
                and value.get('repository_registration',{}).get('kind') == 'unconfigured'):
                return
    raise ValueError('Original PM did not observe an unconfigured repository gateway')

async def resume_registered_repository(invocation, event_id, *, config, bridge, project_gate, db_path=None):
    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row['target_role'] != 'project_manager':
        return None
    saved = get_result(invocation, db_path)
    if not saved or saved['phase'] != 'published' or saved['result'].get('outcome') != 'blocked':
        return None
    original = json.loads(row['work_packet'])
    if (saved.get('result_sha256') != digest(saved['result'])
        or saved['result'].get('actor') != original.get('binding')
        or saved['result'].get('project_id') != row['erp_project']
        or saved['result'].get('task_id') != row['erp_task']
        or not isinstance(saved.get('receipt'), dict)
        or saved['receipt'].get('task_id') != row['erp_task']
        or saved['receipt'].get('event_id') != saved.get('event',{}).get('event_id')):
        raise ValueError('Original published PM result identity or receipt changed')
    if (not original.get('atomic_adoption') or saved['run_id'] != row['automation_run_id']
        or saved['result'].get('invocation_id') != invocation
        or saved['result'].get('work_packet_digest') != packet_digest(original)):
        raise ValueError('Repository retry requires the exact published atomic PM review')
    _, gate = await project_gate(row['erp_project'], config)
    with ledger._transaction(db_path) as db:
        authority = _authority(db, row, event_id);ledger._gate(db,row['erp_project'],gate)
        prior = _prior(db,row,authority['command_id'])
        if prior:
            return prior
        if not ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'execution_finished'}):
            return None
        _blocked_registration(db,row,original)
        for table in ('governance_repair_publication','governance_lifecycle_wait'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                if db.execute('SELECT 1 FROM '+table+' WHERE source=? LIMIT 1',(invocation,)).fetchone():
                    raise ValueError('Existing repair/dependency work must be reconciled before PM re-review')
    task = await bridge.erp.get('Task', row['erp_task'])
    if (task.get('project') != row['erp_project'] or task.get('status') != 'Open'
        or task.get('custom_agov_active_run') != row['automation_run_id']
        or task.get('custom_agov_invocation_id') != invocation
        or not task.get('custom_agentic_managed') or task.get('is_group')):
        raise ValueError('Explicit PM repository re-review requires the same enrolled Open atomic Task')
    fresh = await bridge.packet(row['erp_project'],row['erp_task'],'project_manager',row['agent_id'])
    if any(fresh.get(k) != original.get(k) for k in BUSINESS_FIELDS):
        raise ValueError('Task business scope changed; a new explicit adoption review is required')
    if fresh.get('binding') != original.get('binding'):
        raise ValueError('PM Project-role authority changed before prerequisite retry')
    profile, registration = resolve_repository_profile(
        config,row['erp_project'],fresh['repository'],fresh['environment'])
    _template_policy(profile)
    if registration.get('ready') is not True:
        raise ValueError('Repository registration is still unavailable')
    verification = profile.get('operator_verification')
    if (not isinstance(verification,dict) or set(verification) != {'commit','runtime_manifest_sha256','sandbox_receipt_sha256'}
        or verification['commit'] != profile.get('base_commit')
        or any(not isinstance(verification[k],str) or len(verification[k]) != 64
               for k in ('runtime_manifest_sha256','sandbox_receipt_sha256'))):
        raise ValueError('Actual operator sandbox/runtime preflight is required before PM retry')
    profile_sha = digest(profile)
    ident = 'agov-repository-retry-' + digest([invocation,authority['command_id']])[:48]
    context = canonical({
        'instruction': 'Perform one fresh PM review after the operator repaired the exact repository gateway prerequisite. Fetch context.read and repository.prepare, reconcile all existing checkpoints and source changes, then delegate this exact adopted Task to its accountable Developer when planning-ready. This does not claim business acceptance. If blocked, provide 1..5 concrete defects or action_requests from the actual observations; an empty issue list cannot create repair children.',
        'source_invocation':invocation, 'human_retry_command':authority['command_id'],
        'repository_profile_sha256':profile_sha,'base_branch':profile['base_branch'],
        'base_commit':profile['base_commit'],'operator_verification':verification,
        'prior_result_retained':'The exact prior Blocked result and artifacts remain immutable. No original request is replayed.'})
    with ledger._transaction(db_path) as db:
        authority = _authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate)
        prior = _prior(db,row,authority['command_id'])
        if prior:
            return prior
        source = ledger._owned(db,invocation,row['automation_run_id'],row['lease_token'],{'execution_finished'})
        if not source:
            raise ValueError('Original PM ownership changed before explicit prerequisite retry')
        _blocked_registration(db,row,original)
        db.execute("UPDATE governance_invocation SET state='handed_off',updated_at=? WHERE invocation_id=?",
                   (int(time.time()),invocation))
        ledger._release(db,source)
        new,created=ledger._enqueue(db,invocation_id=ident,project=row['erp_project'],task=row['erp_task'],
            from_role='erp',target_role='project_manager',task_revision=fresh['task_revision'],
            context=context,work_packet=fresh,actor=authority['actor'],gate=gate)
        db.execute('UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',(invocation,ident))
        ledger._event(db,row['erp_project'],ident,KIND,authority['actor'],
            {'source_invocation':invocation,'source_run':row['automation_run_id'],
             'command_id':authority['command_id'],'control_sequence':authority['sequence'],
             'profile_sha256':profile_sha,'work_packet_digest':packet_digest(fresh),
             'operator_verification':verification,'created':created})
        return new['invocation_id']

# A completed, authenticated reply may fail protocol validation. It is not an
# unknown model request and must never be resent to repair its formatting.
REJECTED_KIND='completed_protocol_retry_queued'
UNSAFE_TEMPLATE='Infrastructure templates cannot carry credentials, authority fields, commands or URLs'

def _verified_rejected_reply(row,packet,data_directory):
    from pathlib import Path
    import sqlite3
    from open_webui.utils.abitdot_primary import verify_saved_scope_receipt
    from open_webui.utils.agentic_executor import _decode_original_result
    from open_webui.utils.agentic_result_provenance import parse_original_reply, reply_sha256
    with sqlite3.connect((Path(data_directory)/'abitdot-primary.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        found=db.execute('SELECT * FROM dot_primary_runs WHERE original_run_id=? AND automation_id=?',
                         (row['automation_run_id'],row['agent_id'])).fetchall()
    if len(found)!=1:
        raise ValueError('Exactly one completed original provider receipt is required')
    provider=dict(found[0])
    if provider['status']!='delegated' or not provider.get('response_text'):
        raise ValueError('Unknown or running provider work cannot be retried')
    verify_saved_scope_receipt(provider)
    envelope=json.loads(provider['envelope_json'])
    scope=envelope.get('governance_scope',{}).get('scope',{})
    if any(scope.get(k)!=v for k,v in {
        'invocation_id':row['invocation_id'],'run_id':row['automation_run_id'],
        'project_id':row['erp_project'],'task_id':row['erp_task'],'role':'developer',
        'agent_id':row['agent_id'],'work_packet_digest':packet_digest(packet)}.items()):
        raise ValueError('Completed provider receipt differs from this exact original job')
    result=parse_original_reply(provider['response_text'])
    if (result.get('outcome')!='blocked' or result.get('defects') or result.get('evidence')
            or result.get('actor')!=packet['binding']
            or result.get('invocation_id')!=row['invocation_id']):
        raise ValueError('Only this exact rejected blocked diagnostic may request correction')
    try:
        _decode_original_result(provider['response_text'],packet,row['invocation_id'])
    except ValueError as error:
        if str(error)!=UNSAFE_TEMPLATE:
            raise ValueError('Original result has another unresolved protocol error') from None
    else:
        raise ValueError('A valid result must be published through its original path')
    return {'reply_sha256':reply_sha256(provider['response_text']),
            'provider_receipt':provider['receipt'],'validation_error':UNSAFE_TEMPLATE}

def _rejected_state(db,row,packet):
    current=ledger._owned(db,row['invocation_id'],row['automation_run_id'],row['lease_token'],{'uncertain'})
    if not current or current['reason']!='ValueError: '+UNSAFE_TEMPLATE:
        raise ValueError('Exact completed protocol-rejection fence is required')
    if db.execute('SELECT 1 FROM governance_result WHERE invocation_id=?',(row['invocation_id'],)).fetchone():
        raise ValueError('An existing result must be reconciled, not replaced')
    sessions=db.execute('SELECT session_id FROM governance_gateway_session WHERE invocation_id=?',
                        (row['invocation_id'],)).fetchall()
    if len(sessions)!=1:raise ValueError('Exactly one original gateway session is required')
    calls=db.execute('SELECT operation,state FROM governance_gateway_call WHERE session_id=?',(sessions[0][0],)).fetchall()
    allowed={'context.read','repository.prepare','repository.read','repository.test'}
    if (not calls or any(c['operation'] not in allowed
            or (c['state']!='completed' and c['operation']!='repository.read') for c in calls)):
        raise ValueError('Original actions or tests remain unresolved; no new model run is authorized')
    pm=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(row['source_invocation_id'],)).fetchone()
    if (not pm or pm['target_role']!='project_manager' or pm['state']!='handed_off'
            or (pm['erp_project'],pm['erp_task'])!=(row['erp_project'],row['erp_task'])):
        raise ValueError('The original published PM delegation must still be retained')
    saved=db.execute('SELECT * FROM governance_result WHERE invocation_id=?',(pm['invocation_id'],)).fetchone()
    if (not saved or saved['phase']!='published'
            or json.loads(saved['result_json']).get('outcome')!='ready_for_review'
            or digest(json.loads(saved['result_json']))!=saved['result_sha256']):
        raise ValueError('The original PM planning-ready receipt is required')
    for table,column in [('governance_action','invocation_id'),('governance_developer_delivery','invocation_id'),
                         ('governance_repair_publication','source'),('governance_lifecycle_wait','source')]:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            if db.execute('SELECT 1 FROM '+table+' WHERE '+column+'=? LIMIT 1',(row['invocation_id'],)).fetchone():
                raise ValueError('Original delivery, infrastructure or dependency work needs its own reconciliation')
    return dict(pm)

async def resume_completed_protocol_rejection(invocation,event_id,*,config,bridge,project_gate,db_path=None):
    row=ledger.find(invocation,db_path,include_private=True)
    if (not row or row['target_role']!='developer' or row.get('reason')!='ValueError: '+UNSAFE_TEMPLATE):
        return None
    packet=json.loads(row['work_packet'])
    _,gate=await project_gate(row['erp_project'],config)
    with ledger._transaction(db_path) as db:
        authority=_authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate)
        previous=db.execute("SELECT invocation_id FROM governance_event WHERE kind=? AND "
                            "json_extract(payload,'$.source_invocation')=? AND json_extract(payload,'$.command_id')=?",
                            (REJECTED_KIND,invocation,authority['command_id'])).fetchall()
        if previous:
            if len(previous)!=1:raise ValueError('Ambiguous original protocol correction')
            return previous[0][0]
        pm=_rejected_state(db,row,packet)
    from open_webui.env import DATA_DIR
    proof=_verified_rejected_reply(row,packet,__import__('pathlib').Path(db_path).parent if db_path else DATA_DIR)
    task=await bridge.erp.get('Task',row['erp_task'])
    if (task.get('project')!=row['erp_project'] or task.get('status')!='Open'
            or task.get('custom_agov_active_run')!=row['automation_run_id']
            or task.get('custom_agov_invocation_id')!=invocation
            or not task.get('custom_agentic_managed') or task.get('is_group')):
        raise ValueError('Correction requires the same enrolled Open Task and original ownership')
    fresh=await bridge.packet(row['erp_project'],row['erp_task'],'developer',row['agent_id'])
    if any(fresh.get(k)!=packet.get(k) for k in BUSINESS_FIELDS) or fresh.get('binding')!=packet.get('binding'):
        raise ValueError('Task business scope or accountable Developer changed')
    profile,registration=resolve_repository_profile(config,row['erp_project'],fresh['repository'],fresh['environment'])
    _template_policy(profile)
    if not registration.get('ready') or not profile.get('operator_verification'):
        raise ValueError('Verified repository runtime is required before correction')
    context=canonical({'instruction':'Continue this exact adopted Task under its retained PM delegation after the operator repaired long-test receipt handling and reference read scopes. The prior Developer reply is terminal and retained but was rejected because action_request parameters included identity fields. Do not replay that model request or original tool jobs. Use the new registered context guidance. For a long test, keep its exact call_id and read the original uncertain/completed receipt; do not report lock contention as an ERP business edit. Infrastructure parameters omit all actor/project/task/run/invocation IDs, credentials, commands and URLs. Finish independent available work and report concrete repair issues or typed approval requests. Historical test evidence below is original provenance only and is not a new execution or deployment/QA acceptance.',
        'rejected_source_invocation':invocation,'original_PM_invocation':pm['invocation_id'],
        'human_retry_command':authority['command_id'],'rejection_proof':proof})
    ident='agov-contract-retry-'+digest([invocation,authority['command_id']])[:48]
    with ledger._transaction(db_path) as db:
        authority=_authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate)
        old=db.execute("SELECT invocation_id FROM governance_event WHERE kind=? AND json_extract(payload,'$.source_invocation')=? AND json_extract(payload,'$.command_id')=?",
                       (REJECTED_KIND,invocation,authority['command_id'])).fetchone()
        if old:return old[0]
        _rejected_state(db,row,packet)
        db.execute("UPDATE governance_invocation SET state='handed_off',updated_at=? WHERE invocation_id=?",
                   (int(time.time()),invocation));ledger._release(db,row)
        new,created=ledger._enqueue(db,invocation_id=ident,project=row['erp_project'],task=row['erp_task'],
            from_role='project_manager',target_role='developer',task_revision=fresh['task_revision'],
            context=context,work_packet=fresh,
            actor=authority['actor'],gate=gate)
        db.execute('UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',(pm['invocation_id'],ident))
        ledger._event(db,row['erp_project'],ident,REJECTED_KIND,authority['actor'],
            {'source_invocation':invocation,'original_PM_invocation':pm['invocation_id'],
             'command_id':authority['command_id'],'control_sequence':authority['sequence'],
             'rejection_proof':proof,'created':created})
        return new['invocation_id']

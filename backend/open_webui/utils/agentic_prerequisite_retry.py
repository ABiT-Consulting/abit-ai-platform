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

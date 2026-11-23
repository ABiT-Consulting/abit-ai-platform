"""PM blocker triage derived only from exact durable same-Task predecessors."""
import json
from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical,digest,packet_digest,stable_run_id
from open_webui.utils.agentic_result_store import get_result

FIELDS=('task_revision','srs','repository','environment','requirement_ids')

TRIAGE_PURPOSE = 'Diagnose the published blocker and coordinate role-scoped repairs'


def _verified_transport_retry(review, initial_id, path):
    if not review['invocation_id'].startswith('agov-transport-retry-'):
        return False
    from open_webui.utils.agentic_transport_retry import blocker_retry_source

    with ledger.connect(path) as db:
        return blocker_retry_source(db, review, phase='queued') == initial_id


def origin(row,path=None, *, automated_only=False):
    if row.get('target_role')!='project_manager':return None
    original=json.loads(row.get('work_packet') or '{}')
    review = row
    cursor=row.get('source_invocation_id');seen=set()
    for _ in range(20):
        if not cursor:return None
        if cursor in seen:raise ValueError('Blocker review predecessor cycle')
        seen.add(cursor);source=ledger.find(cursor,path,include_private=True);saved=get_result(cursor,path)
        if not source or not saved or saved['phase']!='published':return None
        packet=json.loads(source.get('work_packet') or '{}')
        if ((source['erp_project'],source['erp_task'])!=(row['erp_project'],row['erp_task'])
            or source['state']!='handed_off' or saved['run_id']!=source['automation_run_id']
            or any(packet.get(k)!=original.get(k) for k in FIELDS)):
            return None
        result=saved['result']
        if source['target_role']!='project_manager':
            if (result.get('outcome') not in {'blocked','fail'} or saved['receipt'].get('status')!='Blocked'
                or saved['receipt'].get('durable') is not True
                or saved['event'].get('type')!='run.blocked'
                or result.get('actor')!=packet.get('binding')):return None
            if automated_only:
                initial_id = 'agov-step-' + digest([source['invocation_id'], 'project_manager', TRIAGE_PURPOSE])[:48]
                if review['invocation_id'] != initial_id and not _verified_transport_retry(review, initial_id, path):
                    return None
            return source,saved
        if result.get('outcome')!='continue':return None
        review = source
        cursor=source.get('source_invocation_id')
    raise ValueError('Blocker review exceeds bounded checkpoint history')

def require_triage_outcome(row, result, path=None, *, event=None):
    if row.get('target_role') != 'project_manager':
        return
    outcome = result.get('outcome')
    if outcome == 'blocked' and (event is None or event.get('type') == 'run.blocked'):
        return
    if (
        outcome == 'continue'
        and (event is None or event.get('type') == 'run.checkpoint')
        and not (result.get('defects') or result.get('action_requests'))
    ):
        return
    if origin(row, path, automated_only=True):
        raise ValueError('Automated blocker triage must publish a blocker or a coordination checkpoint')


def guidance(row,path=None):
    proof=origin(row,path)
    if not proof:return {}
    source,saved=proof
    return {'blocker_review':{
        'source_invocation_id':source['invocation_id'],'source_run_id':source['automation_run_id'],
        'source_result':saved['result'],'published_artifacts':saved['event'].get('artifacts',[]),
        'instruction':'Act as PM: diagnose this exact blocked result against current Task/backlog evidence. Reuse already-created repair children via defects[].existing_task_id; never duplicate or reset active/uncertain work. For new code repairs set defects[].executor_role=developer; for supported operational audits use operations. Required privileged work uses existing action_requests and exact IT templates; approvals remain mandatory. Return blocked with concrete bounded repair defects/action_requests and real evidence. Completion and QA approval are not implied. Do not return implementation readiness or a QA verdict from this triage turn.',
    }}

async def context(row,result,bridge,path=None):
    children=[]
    async for item in bridge.erp.rows('Task',['name','subject','status','parent_task','custom_agov_executor_role','custom_agov_source_run'],
                                     [['parent_task','=',row['erp_task']]]):
        children.append(item)
        if len(children)>25:raise ValueError('PM blocker review needs bounded child task context')
    saved=get_result(row['invocation_id'],path)
    value={'instruction':'PM blocker triage: determine root cause, reuse existing child Tasks and delegate only concrete uncovered work to the correct registered agent. Existing active or uncertain runs must be reconciled, never repeated. Return a blocked result with evidence and bounded defects/action_requests. Use existing_task_id for an existing repair child and executor_role developer or operations for a new repair; IT work uses action_requests. This is coordination, not implementation/QA acceptance.',
        'source_invocation_id':row['invocation_id'],'source_run_id':row['automation_run_id'],
        'source_role':row['target_role'],'source_result':result,
        'published_artifacts':saved['event'].get('artifacts',[]) if saved else [],
        'existing_children':sorted(children,key=lambda item:item['name'])}
    text=canonical(value)
    if len(text)>50000:raise ValueError('PM blocker triage context exceeds its bound')
    return text

async def pending(project,bridge,gate,dispatch,path=None):
    """Backfill known published blocked runs, including legacy direct-repair waits."""
    from open_webui.utils.agentic_orchestrator import _handoff,_task_allowed
    with ledger.connect(path) as db:
        rows=[dict(v) for v in db.execute("""SELECT i.* FROM governance_invocation i
            JOIN governance_result r ON r.invocation_id=i.invocation_id
            WHERE i.erp_project=? AND i.state='execution_finished' AND i.target_role!='project_manager'
            AND r.phase='published' AND json_extract(r.event_json,'$.type')='run.blocked'
            ORDER BY i.created_at LIMIT 25""",(project,))]
    for row in rows:
        if not _task_allowed(project,row['erp_task'],path):continue
        saved=get_result(row['invocation_id'],path)
        task=await bridge.erp.get('Task',row['erp_task'])
        if task.get('status')!='Blocked' or task.get('custom_agov_active_run')!=row['automation_run_id']:continue
        await _handoff(row,'project_manager',TRIAGE_PURPOSE,bridge,gate,dispatch,path)


def failure_proof(row,result,path=None):
    # A Developer's failed command is a blocker, never an independent QA verdict.
    if (row.get('target_role')!='developer' or result.get('outcome')!='fail'
        or not result.get('defects') or result.get('implementation_invocation_id') is not None
        or result.get('implementation_agent_id') is not None):
        return None
    evidence={(item.get('uri'),item.get('sha256')) for item in result.get('evidence',[]) if item.get('kind')=='test_log'}
    with ledger.connect(path) as db:
        calls=db.execute("SELECT c.call_id,c.result_json FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id WHERE s.invocation_id=? AND c.operation='repository.test' AND c.state='completed'",(row['invocation_id'],)).fetchall()
    failed=[]
    for call in calls:
        receipt=json.loads(call['result_json']);artifact=receipt.get('evidence') or {}
        if (type(receipt.get('exit_code')) is int and receipt['exit_code']!=0
            and (artifact.get('uri'),artifact.get('sha256')) in evidence):
            failed.append({'call_id':call['call_id'],'test_id':receipt['test_id'],'exit_code':receipt['exit_code'],'sha256':artifact['sha256']})
    if not failed:return None
    return {'policy':'verified-developer-failure-is-blocker.v1','invocation_id':row['invocation_id'],'run_id':row['automation_run_id'],'failed_tests':sorted(failed,key=lambda item:item['call_id'])}

def record_failure(row,proof,path=None):
    with ledger._transaction(path) as db:
        # The diagnostic is evidence, not terminal authority. Recheck its exact
        # original ownership in the same transaction that appends the audit.
        current = ledger._owned(
            db, row['invocation_id'], row['automation_run_id'], row['lease_token'],
            {'running', 'uncertain', 'cancel_requested'})
        keys = ('agent_id', 'erp_project', 'erp_task', 'target_role', 'task_revision',
                'work_packet', 'packet_hash', 'lease_fence')
        if (not current or any(current[key] != row[key] for key in keys)
                or current['target_role'] != 'developer' or current['submitted_at'] is None
                or current['automation_run_id'] != stable_run_id(row['invocation_id'])
                or proof.get('policy') != 'verified-developer-failure-is-blocker.v1'
                or proof.get('invocation_id') != row['invocation_id']
                or proof.get('run_id') != row['automation_run_id']):
            raise ValueError('Failure diagnostic requires the exact owned submitted original')
        packet = json.loads(current['work_packet'])
        if (digest(packet) != current['packet_hash']
                or packet.get('content_sha256') != packet_digest(packet)):
            raise ValueError('Failure diagnostic original packet changed')
        before=db.execute("SELECT payload FROM governance_event WHERE invocation_id=? AND kind='implementation_failure_diagnostic'",(row['invocation_id'],)).fetchall()
        if before and (len(before)!=1 or before[0][0]!=canonical(proof)):raise ValueError('Original failure diagnostic proof changed')
        if not before:ledger._event(db,row['erp_project'],row['invocation_id'],'implementation_failure_diagnostic','governance-lifecycle-coordinator',proof)

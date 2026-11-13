"""Materialize an immutable architect plan through the governed ERP Task API.

The PM owns the original plan publication. A durable intent is fenced before
any remote write. Unknown acknowledgments are resolved by the original ERP
receipt key; neither a model nor an unconfirmed Task creation is replayed.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
from urllib.parse import parse_qsl, urlsplit

TASK = re.compile(r'TASK-[A-Za-z0-9-]{1,60}')
MAX_TASKS = 50
PRIORITY = {'P0': 'Urgent', 'P1': 'High', 'P2': 'Medium', 'P3': 'Low'}


class BacklogUncertain(RuntimeError):
    """Original ERP creation still needs receipt reconciliation; do not resend."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@contextmanager
def _connect(db_path=None):
    from open_webui.utils.agentic_governance import connect

    with connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript('''
          CREATE TABLE IF NOT EXISTS governance_backlog_plan(
            plan_hash TEXT PRIMARY KEY, architect_invocation TEXT NOT NULL,
            pm_invocation TEXT NOT NULL, project TEXT NOT NULL, anchor_task TEXT NOT NULL,
            source_hash TEXT NOT NULL, pm_packet_hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS governance_backlog_task(
            plan_hash TEXT NOT NULL, task_key TEXT NOT NULL, request_hash TEXT NOT NULL,
            request_json TEXT NOT NULL, phase TEXT NOT NULL,
            erp_task TEXT, PRIMARY KEY(plan_hash,task_key),
            UNIQUE(plan_hash,erp_task));
        ''')
        yield db


def _complete_backlog(packet):
    snapshot=packet.get('project_backlog')
    if not isinstance(snapshot,dict) or snapshot.get('complete') is not True or snapshot.get('project_id')!=packet.get('project_id'):
        raise ValueError('Planning requires its complete authorized Project backlog snapshot')
    tasks=snapshot.get('tasks');total=snapshot.get('total_count')
    if not isinstance(tasks,list) or not isinstance(total,int) or isinstance(total,bool) or not 0<=total<=500 or len(tasks)!=total:
        raise ValueError('Incomplete or excessive Project backlog snapshot')
    ids=[row.get('task_id') for row in tasks if isinstance(row,dict)]
    if len(ids)!=len(tasks) or len(ids)!=len(set(ids)) or any(not isinstance(v,str) or not TASK.fullmatch(v) for v in ids):
        raise ValueError('Backlog Task identities are missing, duplicated or invalid')
    if snapshot.get('snapshot_sha256')!=_digest({'project_id':packet['project_id'],'tasks':tasks}):
        raise ValueError('Complete Project backlog failed its content digest')
    excluded=snapshot.get('context_excluded_task_ids') or []
    by_id={row['task_id']:row for row in tasks}
    if not isinstance(excluded,list) or len(excluded)!=len(set(excluded)) or not set(excluded)<=set(ids):
        raise ValueError('Backlog context exclusions are invalid')
    if any(not by_id[key].get('managed') or not by_id[key].get('source_run_id') for key in excluded):
        raise ValueError('Only source-provenance managed Tasks may be excluded from planning context')
    semantic=[{k:row[k] for k in ('task_id','task_revision','owner_context_sha256')} for row in tasks if row['task_id'] not in excluded]
    if snapshot.get('context_sha256')!=_digest({'project_id':packet['project_id'],'tasks':semantic}):
        raise ValueError('Project backlog semantic digest differs')
    return snapshot

async def complete_project_backlog(bridge,project,source_task=''):
    """Collect every bounded page under the same server capture digest."""
    offset=0;rows=[];base=None
    for _ in range(11):
        params={'project_id':project,'offset':offset,'page_size':50}
        if source_task:params['source_task_id']=source_task
        if base:params['expected_snapshot_sha256']=base['snapshot_sha256']
        response=await bridge.erp.request('GET','/api/method/abit_ai_assistant.agentic_control.get_project_backlog',params=params,headers={'X-ABIT-Agentic-Bridge':bridge.secret})
        page=response.get('message') or {}
        if not isinstance(page,dict) or page.get('capture_complete') is not True or page.get('project_id')!=project or page.get('offset')!=offset or not isinstance(page.get('tasks'),list):
            raise ValueError('ERP did not return a complete scoped backlog capture')
        if base and any(page.get(k)!=base.get(k) for k in ('snapshot_sha256','context_sha256','total_count','context_excluded_task_ids')):
            raise ValueError('ERP backlog capture changed between pages')
        if base is None:base={k:v for k,v in page.items() if k not in {'tasks','offset','next_offset','page_complete','capture_complete'}}
        if len(page['tasks'])>50 or len(rows)+len(page['tasks'])>500:raise ValueError('ERP backlog page exceeds its bound')
        rows.extend(page['tasks']);next_offset=page.get('next_offset')
        if next_offset is None:
            if page.get('page_complete') is not True:raise ValueError('ERP backlog ended without a complete-page acknowledgement')
            snapshot={**base,'complete':True,'tasks':rows}
            _complete_backlog({'project_id':project,'project_backlog':snapshot})
            return snapshot
        if not page['tasks'] or next_offset!=offset+len(page['tasks']):raise ValueError('ERP backlog pagination did not progress exactly')
        offset=next_offset
    raise ValueError('Project backlog requires explicit bounded decomposition')

async def _fresh_planning_backlog(bridge,pm,packet):
    original=_complete_backlog(packet)
    fresh=await complete_project_backlog(bridge,pm['erp_project'],pm['erp_task'])
    if fresh['context_sha256']!=original['context_sha256']:
        raise ValueError('Project backlog business/ownership context changed after PM review')
    return fresh

async def _existing_reference(bridge,item,pm,snapshot):
    ident=item['existing_task_id'];expected=item['existing_task_revision']
    row=next((r for r in snapshot['tasks'] if r['task_id']==ident),None)
    if not row or ident==pm['erp_task'] or row['task_revision']!=expected:
        raise ValueError('Existing Task mapping is absent, self-referential or stale')
    if item['parent_task_key'] is not None:raise ValueError('Existing Task cannot be reparented by a plan')
    if row.get('requirement_ids') and not set(item['requirement_ids'])<=set(row['requirement_ids']):
        raise ValueError('Existing Task mapping expands its recorded SRS requirements')
    if row.get('executor_role') and row['executor_role']!=item['role']:
        raise ValueError('Existing Task accountable role differs from the reviewed reference')
    response=await bridge.erp.request('GET','/api/method/abit_ai_assistant.agentic_control.get_project_task_snapshot',params={'project_id':pm['erp_project'],'task_id':ident,'expected_task_revision':expected,**({'human_user':item['human_user']} if item.get('human_user') else {})},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    actual=response.get('message') or {}
    if actual.get('task_id')!=ident or actual.get('task_revision')!=expected or actual.get('owner_context_sha256')!=row['owner_context_sha256']:
        raise ValueError('Existing Task input/ownership readback differs; no adoption or reassignment is authorized')
    if item.get('human_user') and actual.get('status') not in {'Completed','QA APPROVED'}:
        proof=actual.get('human_owner_receipt') or {}
        if proof.get('assignment_verified') is not True or (proof.get('project_id'),proof.get('task_id'),proof.get('human_user'))!=(pm['erp_project'],ident,item['human_user']):
            raise ValueError('Existing human Task has a different or ambiguous owner')
    return ident

def _reconcile_existing_work(plan,snapshot,pm_packet):
    """Use traceable identities only; ambiguous same-requirement work is held."""
    referenced={item['existing_task_id']:item for item in plan['tasks'] if item.get('existing_task_id')}
    excluded=set(snapshot.get('context_excluded_task_ids') or [])|{pm_packet['task_id']}
    normalize=lambda value:' '.join(str(value).split()).casefold()
    for item in plan['tasks']:
        if item.get('existing_task_id'):continue
        requirements=set(item['requirement_ids'])
        for row in snapshot['tasks']:
            if row['task_id'] in excluded:continue
            overlap=requirements&set(row.get('requirement_ids') or [])
            if not overlap:continue
            ref=referenced.get(row['task_id'])
            active_same_srs=row.get('status') not in {'Completed','QA APPROVED','Cancelled'} and row.get('srs_revision')==pm_packet['srs']['revision']
            if active_same_srs and not ref:
                raise ValueError('Existing requirement work must be reconciled by exact Task ID/revision before creating overlapping work: '+row['task_id'])
            if ref:
                same_acceptance=normalize('\n'.join(item['acceptance_criteria']))==normalize(row.get('acceptance_tests'))
                same_description=normalize(item['description'])==normalize(row.get('description'))
                if same_acceptance or same_description:
                    raise ValueError('New Task duplicates exact existing acceptance/description: '+row['task_id'])
                if active_same_srs and ref['task_key'] not in item['depends_on']:
                    raise ValueError('Further decomposition requires an explicit dependency on existing Task: '+row['task_id'])


def _current(db, supplied, role, state):
    ident = supplied.get('invocation_id')
    row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('Original planning invocation does not exist')
    value = dict(row)
    for key in ('erp_project', 'erp_task', 'agent_id', 'automation_run_id'):
        if not value.get(key) or value[key] != supplied.get(key):
            raise ValueError('Planning invocation identity changed: ' + key)
    if value['target_role'] != role or value['state'] != state:
        raise ValueError('Planning source no longer owns the required finished role state')
    return value


def _saved(db, invocation):
    row = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (invocation,)).fetchone()
    if not row or row['phase'] != 'published':
        raise ValueError('Planning requires the original published result')
    result = json.loads(row['result_json'])
    if 'result_sha256' in row.keys() and row['result_sha256'] != _digest(result):
        raise ValueError('Saved original planning result failed its immutable hash')
    return dict(row), result, json.loads(row['event_json'])


def _pm_predecessor_chain(db, architect, pm, pm_packet):
    """Allow only bounded, published same-Task PM checkpoints before this review."""
    source = pm.get('source_invocation_id')
    seen = {pm['invocation_id']}
    count = 0
    immutable = (
        'task_revision',
        'srs',
        'repository',
        'environment',
        'project_revision',
        'objective',
        'requirement_ids',
        'acceptance_tests',
        'instructions',
        'binding',
    )
    while source != architect['invocation_id']:
        if not source or source in seen or count >= 20:
            raise ValueError('PM must trace a bounded acyclic continuation to the exact architect successor')
        seen.add(source)
        count += 1
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (source,)).fetchone()
        if not row:
            raise ValueError('PM predecessor is missing')
        prior = dict(row)
        if (
            prior['target_role'] != 'project_manager'
            or prior['state'] != 'handed_off'
            or (prior['erp_project'], prior['erp_task']) != (pm['erp_project'], pm['erp_task'])
            or not prior.get('work_packet')
        ):
            raise ValueError('PM continuation requires a finished PM on the same project Task')
        packet = json.loads(prior['work_packet'])
        if any(packet.get(key) != pm_packet.get(key) for key in immutable):
            raise ValueError('PM continuation business inputs changed before plan review')
        saved, result, event = _saved(db, source)
        if (
            saved['run_id'] != prior['automation_run_id']
            or result.get('invocation_id') != source
            or result.get('actor') != packet.get('binding')
            or result.get('work_packet_digest') != _digest({k: v for k, v in packet.items() if k != 'content_sha256'})
        ):
            raise ValueError('PM continuation result does not bind its original run and WorkPacket')
        if result.get('outcome') not in {'continue', 'blocked'} and not (
            result.get('outcome') == 'ready_for_review' and result.get('action_requests')
        ):
            raise ValueError('PM predecessor did not publish an allowed dependency checkpoint')
        source = prior.get('source_invocation_id')


def _sources(db, architect_row, architect_result, pm_row, pm_packet):
    architect = _current(db, architect_row, 'solution_architect', 'handed_off')
    pm = _current(db, pm_row, 'project_manager', 'execution_finished')
    if (architect['erp_project'], architect['erp_task']) != (pm['erp_project'], pm['erp_task']):
        raise ValueError('PM must be the exact architect successor on the same project Task')
    _pm_predecessor_chain(db, architect, pm, pm_packet)
    pm_backlog=_complete_backlog(pm_packet)
    if not architect.get('work_packet'):raise ValueError('Original architect backlog packet is missing')
    arch_packet=json.loads(architect['work_packet']);arch_backlog=_complete_backlog(arch_packet)
    if arch_backlog['context_sha256']!=pm_backlog['context_sha256']:raise ValueError('PM did not review the original complete backlog context')
    lease = db.execute(
        'SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
        (pm['erp_project'], pm['erp_task']),
    ).fetchone()
    if not lease or tuple(lease) != (pm['invocation_id'], pm['lease_fence']):
        raise ValueError('PM task lease was canceled or transferred')
    control = db.execute(
        'SELECT state FROM governance_project_control WHERE erp_project=?', (pm['erp_project'],)
    ).fetchone()
    if not control or control['state'] != 'enabled':
        raise ValueError('Project backlog publication is paused, stopped or disabled')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_task_control'").fetchone():
        task_control = db.execute(
            'SELECT state FROM governance_task_control WHERE project=? AND task=?', (pm['erp_project'], pm['erp_task'])
        ).fetchone()
        if task_control and task_control['state'] != 'enabled':
            raise ValueError('Task backlog publication is paused or stopped')
    arch_saved, original, arch_event = _saved(db, architect['invocation_id'])
    actor = original.get('actor') or {}
    if (
        original != architect_result
        or original.get('outcome') != 'ready_for_review'
        or original.get('invocation_id') != architect['invocation_id']
        or actor.get('project_id') != architect['erp_project']
        or actor.get('role') != 'solution_architect'
        or actor.get('agent_id') != architect['agent_id']
    ):
        raise ValueError('Architect result changed or was not ready for review')
    pm_saved, reviewed, pm_event = _saved(db, pm['invocation_id'])
    if (
        reviewed.get('outcome') != 'ready_for_review'
        or reviewed.get('invocation_id') != pm['invocation_id']
        or reviewed.get('actor') != pm_packet.get('binding')
    ):
        raise ValueError('PM has not published its exact reviewed plan outcome')
    for current, saved in ((architect, arch_saved), (pm, pm_saved)):
        if saved['run_id'] != current['automation_run_id']:
            raise ValueError('Saved planning result belongs to another execution')
    binding = pm_packet.get('binding') or {}
    if (
        pm_packet.get('project_id') != pm['erp_project']
        or pm_packet.get('task_id') != pm['erp_task']
        or pm_packet.get('task_revision') != pm['task_revision']
        or binding.get('role') != 'project_manager'
        or binding.get('agent_id') != pm['agent_id']
    ):
        raise ValueError('PM packet identity or revision differs from its invocation')
    if pm.get('work_packet') and json.loads(pm['work_packet']) != pm_packet:
        raise ValueError('PM WorkPacket changed since invocation')
    expected = {key: value for key, value in pm_packet.items() if key != 'content_sha256'}
    if reviewed.get('work_packet_digest') != _digest(expected):
        raise ValueError('PM review does not bind the immutable WorkPacket')
    return architect, pm, arch_event


def _private_ref(value):
    if not isinstance(value, dict):
        raise ValueError('Published artifact is not a verified ERP reference')
    url = value.get('url') or ''
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != 'https'
        or parsed.netloc != 'erp.abit.ai'
        or parsed.fragment
        or not re.fullmatch(r'/private/files/[A-Za-z0-9_.-]+', parsed.path)
        or len(query) != 1
        or query[0][0] != 'fid'
        or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}', query[0][1])
    ):
        raise ValueError('Architect artifacts must have verified private ERP File IDs')
    return {'kind': value['kind'], 'url': url, 'sha256': value['sha256']}


def _plan(architect, architect_result, pm_packet, arch_event, verified_artifacts):
    from open_webui.utils.agentic_contracts.validate_architect_plan import validate_plan

    plan = architect_result.get('architect_plan')
    errors = validate_plan(plan)
    if errors:
        raise ValueError('Invalid architect plan: ' + '; '.join(errors)[:1500])
    metadata = plan['metadata']
    source_backlog=_complete_backlog(json.loads(architect['work_packet']))
    pm_backlog=_complete_backlog(pm_packet)
    if metadata.get('project_backlog_context_sha256')!=pm_backlog['context_sha256'] or metadata.get('project_backlog_snapshot_sha256')!=source_backlog['snapshot_sha256']:
        raise ValueError('Architect plan does not bind its complete Project backlog snapshot')
    binding = metadata['agent_binding']
    srs = pm_packet.get('srs') or {}
    if (
        metadata['project_id'] != architect['erp_project']
        or metadata['run_id'] != architect['invocation_id']
        or metadata['srs_version'] != srs.get('revision')
        or metadata['srs_sha256'] != srs.get('sha256')
        or binding['project_id'] != architect['erp_project']
        or binding['role'] != 'solution_architect'
        or binding['abit_agent_id'] != architect['agent_id']
        or binding['erp_ai_user'] != (architect_result.get('actor') or {}).get('erp_user')
    ):
        raise ValueError('Architect plan SRS or binding no longer matches the PM source')
    if {v['id'] for v in plan['requirements']} != set(pm_packet.get('requirement_ids') or []):
        raise ValueError('Plan must cover the reviewed SRS requirements exactly')
    if not 0 < len(plan['tasks']) <= MAX_TASKS:
        raise ValueError('One plan publication supports at most 50 atomic tasks')
    published = arch_event.get('artifacts') or []
    if verified_artifacts is not None and verified_artifacts != published:
        raise ValueError('Artifact publication differs from the original saved ERP result')
    evidence = architect_result.get('evidence') or []
    references = {}
    for item in plan['designs'] + plan['mockups']:
        kind = 'mockup' if item in plan['mockups'] else 'design'
        matches = [
            e
            for e in evidence
            if e['kind'] == kind and e['uri'] == item['artifact']['uri'] and e['sha256'] == item['artifact']['sha256']
        ]
        if len(matches) != 1:
            raise ValueError('Every design/mockup needs its exact original evidence URI and hash')
        match = matches[0]
        files = [
            p
            for p in published
            if p.get('id') == match['id'] and p.get('sha256') == match['sha256'] and p.get('kind') == kind
        ]
        if len(files) != 1:
            raise ValueError('Architecture/mockup evidence lacks a unique verified ERP File')
        references[item['id']] = _private_ref(files[0])
    return plan, references


def _ordered(plan):
    tasks = {item['task_key']: item for item in plan['tasks']}
    # Native ERP adds parent -> child execution dependencies automatically.
    # Check that graph separately from parent-before-child creation ordering.
    native = {key: set(item['depends_on']) for key, item in tasks.items()}
    for key, item in tasks.items():
        if item['parent_task_key']:
            native[item['parent_task_key']].add(key)

    def visit(key, trail, done):
        if key in trail:
            raise ValueError('Plan creates a native ERP parent/child dependency cycle')
        if key in done:
            return
        for other in native[key]:
            visit(other, trail | {key}, done)
        done.add(key)

    done = set()
    for key in tasks:
        visit(key, set(), done)
    result = []
    pending = set(tasks)
    roots = sum(item['parent_task_key'] is None for item in tasks.values())
    if roots > 20:
        raise ValueError('Plan anchor supports at most 20 direct Tasks')
    while pending:
        ready = sorted(
            key
            for key in pending
            if not (
                set(tasks[key]['depends_on'])
                | ({tasks[key]['parent_task_key']} if tasks[key]['parent_task_key'] else set())
            )
            & pending
        )
        if not ready:
            raise ValueError('Plan Task graph is cyclic')
        for key in ready:
            if any(ref not in tasks for ref in tasks[key]['depends_on']):
                raise ValueError('Plan has an unknown Task dependency')
            result.append(tasks[key])
            pending.remove(key)
    return result


def _proposal(item, plan, pm, pm_packet, references, actual):
    tests = {value['id']: value for value in plan['tests']}
    description = (
        item['description']
        + '\n\nAlgorithm:\n'
        + '\n'.join(
            f"{n}. Input: {step['input']}\nAction: {step['action']}\nOutput: {step['output']}\nVerify: {step['verification']}"
            for n, step in enumerate(item['steps'], 1)
        )
    )
    description += '\n\nSRS requirements: ' + ', '.join(item['requirement_ids'])
    acceptance = (
        '\n'.join(item['acceptance_criteria'])
        + '\n\nIndependent QA:\n'
        + '\n'.join(
            f"{key} ({tests[key]['method']}): {tests[key]['expected_result']} Evidence: {tests[key]['evidence_required']}"
            for key in item['qa_test_ids']
        )
    )
    if len(description) > 6000 or len(acceptance) > 6000:
        raise ValueError('Atomic Task description/acceptance exceeds the ERP contract; decompose the plan')
    if item['description'].strip().casefold() == str(pm_packet.get('objective') or '').strip().casefold() or item[
        'subject'
    ].strip().casefold() in {
        str(pm_packet.get('objective') or '').strip().casefold(),
        'resolve the parent task',
        'fix everything',
        'complete the project',
    }:
        raise ValueError('Generic copies of parent work are not atomic Tasks')
    key = 'agov-task-' + _digest([pm['erp_project'], plan, item['task_key']])[:48]
    return {
        'project_id': pm['erp_project'],
        'source_role': 'project_manager',
        'source_agent_id': pm['agent_id'],
        'source_run_id': pm['automation_run_id'],
        'source_invocation_id': pm['invocation_id'],
        'target_role': item['role'],
        **({'human_user':item['human_user']} if item.get('human_user') else {}),
        'idempotency_key': key,
        'srs_revision': pm_packet['srs']['revision'],
        'defer_dispatch': True,
        'plan_digest': _digest(plan),
        'is_group': any(t['parent_task_key'] == item['task_key'] for t in plan['tasks']),
        'requirement_id': item['requirement_ids'][0],
        'requirement_ids': item['requirement_ids'],
        'subject': item['subject'],
        'description': description,
        'acceptance_tests': acceptance,
        'reason': f"Reviewed SRS {pm_packet['srs']['revision']} plan Task {item['task_key']}",
        'priority': PRIORITY[item['priority']],
        'estimate_hours': item['estimate_hours'],
        'dependencies': [actual[dep] for dep in item['depends_on']],
        'parent_task': actual[item['parent_task_key']] if item['parent_task_key'] else pm['erp_task'],
        'artifacts': [references[key] for key in item['artifact_ids']],
    }


async def _own_child_dependencies(bridge, task_id, plan_hash, db_path, trail):
    if not plan_hash:
        return set()
    with _connect(db_path) as db:
        rows = [
            dict(row) for row in db.execute('SELECT * FROM governance_backlog_task WHERE plan_hash=?', (plan_hash,))
        ]
    children = set()
    for row in rows:
        child = json.loads(row['request_json'])
        if child.get('parent_task') != task_id or row['phase'] == 'prepared':
            continue
        if row['request_hash'] != _digest(child):
            raise BacklogUncertain('Child creation intent changed during native dependency readback')
        child_id = row['erp_task']
        if not child_id:
            response = await bridge.erp.request(
                'GET',
                '/api/method/abit_ai_assistant.agentic_control.get_task_request_receipt',
                params={'project_id': child['project_id'], 'idempotency_key': child['idempotency_key']},
                headers={'X-ABIT-Agentic-Bridge': bridge.secret},
            )
            receipt = response.get('message') or {}
            if receipt.get('exists') is not True:
                continue
            if receipt.get('content_sha256') != row['request_hash']:
                raise BacklogUncertain('Native child dependency has a different original receipt')
            child_id = receipt.get('task_id')
        await _readback(bridge, child_id, child, plan_hash=plan_hash, db_path=db_path, trail=trail)
        children.add(child_id)
    return children


async def _readback(bridge, task_id, packet, *, plan_hash=None, db_path=None, trail=()):
    if not isinstance(task_id, str) or not TASK.fullmatch(task_id):
        raise BacklogUncertain('ERP did not return a valid original Task ID')
    if task_id in trail or len(trail) > 4:
        raise BacklogUncertain('Native Task hierarchy readback is cyclic or exceeds its bound')
    trail = (*trail, task_id)
    task = await bridge.erp.get('Task', task_id)
    own_children = await _own_child_dependencies(bridge, task_id, plan_hash, db_path, trail)
    actual_dependencies = [row['task'] for row in task.get('depends_on', [])]
    if len(actual_dependencies) != len(set(actual_dependencies)):
        raise BacklogUncertain('Duplicate native Task dependencies require reconciliation')
    requirements = task.get('custom_agov_requirements')
    if isinstance(requirements, str):
        requirements = json.loads(requirements)
    if not requirements:
        requirements = [task.get('custom_agov_requirement')]
    if (
        task.get('project') != packet['project_id']
        or task.get('parent_task') != packet['parent_task']
        or task.get('custom_agov_source_run') != packet['source_run_id']
        or set(requirements) != set(packet['requirement_ids'])
        or task.get('subject') != packet['subject']
        or task.get('description') != packet['description']
        or task.get('custom_agov_acceptance') != packet['acceptance_tests']
        or sorted(set(actual_dependencies) - own_children) != sorted(packet['dependencies'])
    ):
        raise BacklogUncertain('Created Task did not pass exact scope/origin/acceptance readback')
    return task_id


async def materialize_backlog(
    bridge, architect_row, architect_result, pm_row, pm_packet, db_path=None, *, verified_artifacts=None
):
    """Return task-key → verified ERP Task ID under the original PM publication."""
    with _connect(db_path) as db:
        architect, pm, event = _sources(db, architect_row, architect_result, pm_row, pm_packet)
        plan, references = _plan(architect, architect_result, pm_packet, event, verified_artifacts)
        ordered = _ordered(plan)
        plan_hash = _digest(plan)
        # Validate every bounded payload before any business write. Placeholder
        # IDs are discarded; real parent/dependencies are filled after readback.
        preview = {item['task_key']: 'TASK-preview-' + str(n) for n, item in enumerate(ordered)}
        for item in ordered:
            _proposal(item, plan, pm, pm_packet, references, preview)
        expected = (
            architect['invocation_id'],
            pm['invocation_id'],
            pm['erp_project'],
            pm['erp_task'],
            _digest(architect_result),
            _digest(pm_packet),
        )
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute(
            'SELECT architect_invocation,pm_invocation,project,anchor_task,source_hash,pm_packet_hash FROM governance_backlog_plan WHERE plan_hash=?',
            (plan_hash,),
        ).fetchone()
        if prior and tuple(prior) != expected:
            db.execute('ROLLBACK')
            raise ValueError('The original reviewed PM publication owns this plan')
        db.execute('INSERT OR IGNORE INTO governance_backlog_plan VALUES(?,?,?,?,?,?,?)', (plan_hash, *expected))
        db.execute('COMMIT')
    fresh=await _fresh_planning_backlog(bridge,pm,pm_packet)
    _reconcile_existing_work(plan,fresh,pm_packet)
    # Validate EVERY read-only mapping before the first new Task can be written.
    for item in ordered:
        if item.get('existing_task_id'):await _existing_reference(bridge,item,pm,fresh)
    actual = {}
    for item in ordered:
        if item.get('existing_task_id'):
            ident=await _existing_reference(bridge,item,pm,fresh)
            existing=next(row for row in fresh['tasks'] if row['task_id']==ident)
            reference={'project_id':pm['erp_project'],'source_run_id':pm['automation_run_id'],'source_invocation_id':pm['invocation_id'],'plan_digest':plan_hash,'target_role':item['role'],'existing_task_id':ident,'existing_task_revision':item['existing_task_revision'],'owner_context_sha256':existing['owner_context_sha256'],'external_dependency':not existing.get('managed') or existing.get('executor_role')=='human','reference_only':True,**({'human_user':item['human_user']} if item.get('human_user') else {})}
            with _connect(db_path) as db:
                db.execute('BEGIN IMMEDIATE');_sources(db,architect_row,architect_result,pm_row,pm_packet)
                old=db.execute('SELECT * FROM governance_backlog_task WHERE plan_hash=? AND task_key=?',(plan_hash,item['task_key'])).fetchone()
                if old and (old['request_hash']!=_digest(reference) or old['erp_task']!=ident):raise ValueError('Existing Task reference intent changed')
                db.execute("INSERT OR IGNORE INTO governance_backlog_task VALUES(?,?,?,?,?,?)",(plan_hash,item['task_key'],_digest(reference),_json(reference),'referenced',ident));db.execute('COMMIT')
            actual[item['task_key']]=ident
            continue
        fresh=await _fresh_planning_backlog(bridge,pm,pm_packet)
        packet = _proposal(item, plan, pm, pm_packet, references, actual)
        with _connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            _sources(db, architect_row, architect_result, pm_row, pm_packet)
            row = db.execute(
                'SELECT * FROM governance_backlog_task WHERE plan_hash=? AND task_key=?', (plan_hash, item['task_key'])
            ).fetchone()
            if row and row['request_hash'] != _digest(packet):
                db.execute('ROLLBACK')
                raise ValueError('Original Task publication parameters are immutable')
            if not row:
                db.execute(
                    'INSERT INTO governance_backlog_task VALUES(?,?,?,?,?,NULL)',
                    (plan_hash, item['task_key'], _digest(packet), _json(packet), 'prepared'),
                )
                row = db.execute(
                    'SELECT * FROM governance_backlog_task WHERE plan_hash=? AND task_key=?',
                    (plan_hash, item['task_key']),
                ).fetchone()
            previous = dict(row)
            claimed = (
                db.execute(
                    "UPDATE governance_backlog_task SET phase='uncertain' WHERE plan_hash=? AND task_key=? AND phase='prepared'",
                    (plan_hash, item['task_key']),
                ).rowcount
                == 1
            )
            db.execute('COMMIT')
        if previous['phase'] == 'confirmed':
            task_id = previous['erp_task']
        elif not claimed:
            response = await bridge.erp.request(
                'GET',
                '/api/method/abit_ai_assistant.agentic_control.get_task_request_receipt',
                params={'project_id': pm['erp_project'], 'idempotency_key': packet['idempotency_key']},
                headers={'X-ABIT-Agentic-Bridge': bridge.secret},
            )
            receipt = response.get('message') or {}
            if receipt.get('exists') is not True or receipt.get('content_sha256') != _digest(packet):
                raise BacklogUncertain('Original Task creation has no confirmed matching receipt; no write repeated')
            task_id = receipt.get('task_id')
        else:
            # No automatic retry even if an exception occurs before acknowledgment.
            receipt = await bridge.create_task(packet)
            task_id = receipt.get('task_id') if isinstance(receipt, dict) else None
        task_id = await _readback(bridge, task_id, packet, plan_hash=plan_hash, db_path=db_path)
        with _connect(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                'SELECT request_hash,erp_task FROM governance_backlog_task WHERE plan_hash=? AND task_key=?',
                (plan_hash, item['task_key']),
            ).fetchone()
            if not row or row['request_hash'] != _digest(packet) or row['erp_task'] not in (None, task_id):
                db.execute('ROLLBACK')
                raise BacklogUncertain('Original Task publication changed during readback')
            db.execute(
                "UPDATE governance_backlog_task SET phase='confirmed',erp_task=? WHERE plan_hash=? AND task_key=?",
                (task_id, plan_hash, item['task_key']),
            )
            db.execute('COMMIT')
        actual[item['task_key']] = task_id
    return actual

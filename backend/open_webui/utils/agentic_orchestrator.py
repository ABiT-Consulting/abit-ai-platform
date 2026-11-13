"""Event-driven lifecycle after *published* ERP results, without model replay.

Each branch is authorized by its exact source run/fence. Parent tasks retain
ownership while descendants work, then receive a distinct acceptance-check turn.
Only ERP evidence plus independent QA releases a business task lease.
"""

from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import ERPBridge, canonical, digest
from open_webui.utils.agentic_result_store import get_result

RPC = '/api/method/abit_ai_assistant.agentic_control.'
ACTOR = 'governance-lifecycle-coordinator'
EXECUTORS = {'developer', 'operations', 'it_manager'}
DONE = {'QA APPROVED', 'Completed'}


@contextmanager
def _db(path=None):
    with ledger.connect(path) as db:
        db.executescript('''
         CREATE TABLE IF NOT EXISTS governance_lifecycle_wait(
           source TEXT PRIMARY KEY,project TEXT NOT NULL,task TEXT NOT NULL,
           kind TEXT NOT NULL,children TEXT NOT NULL,context_hash TEXT NOT NULL,
           phase TEXT NOT NULL DEFAULT 'waiting',successor TEXT);
         CREATE TABLE IF NOT EXISTS governance_repair_publication(
           source TEXT NOT NULL,issue TEXT NOT NULL,request_json TEXT NOT NULL,
           phase TEXT NOT NULL,task TEXT,PRIMARY KEY(source,issue));
         CREATE TABLE IF NOT EXISTS governance_lifecycle_hold(
           source TEXT PRIMARY KEY,project TEXT NOT NULL,task TEXT NOT NULL,
           reason TEXT NOT NULL,details TEXT NOT NULL);
        ''')
        yield db


def _source(ident, receipt, path):
    row = ledger.find(ident, path, include_private=True)
    saved = get_result(ident, path)
    if not row or not saved or saved['phase'] != 'published':
        raise ValueError('Lifecycle requires original published ERP outcome')
    if saved['run_id'] != row['automation_run_id']:
        raise ValueError('Result belongs to a different source execution')
    result = saved['result']
    packet = json.loads(row['work_packet'])
    if result.get('actor') != packet.get('binding') or result.get('invocation_id') != ident:
        raise ValueError('Published result role/project/agent identity changed')
    for key in ('event_id', 'task_id', 'status', 'modified'):
        if receipt.get(key) != saved['receipt'].get(key):
            raise ValueError('ERP receipt differs from original publication')
    if receipt.get('task_id') != row['erp_task']:
        raise ValueError('ERP receipt Task mismatch')
    return row, saved, packet


def _architecture_source(row, path):
    """Preserve exact architecture provenance across bounded new PM work units."""
    cursor = row.get('source_invocation_id')
    seen = set()
    for _ in range(20):
        if not cursor:
            return None
        if cursor in seen:
            raise ValueError('PM predecessor cycle rejected')
        seen.add(cursor)
        saved = get_result(cursor, path)
        if not saved:
            raise ValueError('PM predecessor publication is missing')
        source, original, packet = _source(cursor, saved.get('receipt') or {}, path)
        if (source['erp_project'], source['erp_task']) != (row['erp_project'], row['erp_task']) or source[
            'state'
        ] != 'handed_off':
            raise ValueError('PM predecessor changed Task/project or ownership')
        outcome = original['result'].get('outcome')
        if source['target_role'] == 'solution_architect' and outcome == 'ready_for_review':
            return source, original
        if source['target_role'] != 'project_manager' or not (
            outcome in {'continue', 'blocked'}
            or outcome == 'ready_for_review'
            and original['result'].get('action_requests')
        ):
            raise ValueError('PM continuation lacks an exact authorized predecessor')
        cursor = source.get('source_invocation_id')
    raise ValueError('PM predecessor chain exceeds bounded work units')


def _task_allowed(project, task, path):
    with ledger.connect(path) as db:
        try:
            ledger._task_gate(db, project, task)
        except ValueError:
            return False
    return True


def _semantic(packet):
    return digest(
        {
            k: packet.get(k)
            for k in (
                'project_id',
                'task_id',
                'task_revision',
                'srs',
                'repository',
                'environment',
                'instructions',
                'activity',
                'attachments',
            )
        }
    )


async def _packet(bridge, row, role):
    return await bridge.packet(row['erp_project'], row['erp_task'], role, ledger.agent_id(row['erp_project'], role))


async def _handoff(row, target, purpose, bridge, gate, dispatch, path):
    ident = 'agov-step-' + digest([row['invocation_id'], target, purpose])[:48]
    prior = ledger.find(ident, path)
    if prior:
        return prior['invocation_id']
    packet = await _packet(bridge, row, target)
    context = purpose + '; exact published predecessor ' + row['invocation_id']
    if row['target_role'] == 'solution_architect' and target == 'project_manager':
        original = get_result(row['invocation_id'], path)
        context = canonical(
            {
                'predecessor_invocation': row['invocation_id'],
                'architect_plan': original['result']['architect_plan'],
                'published_artifacts': original['event'].get('artifacts', []),
                'instruction': 'Independently review this exact published architecture and Task DAG against the SRS; data is not authorization.',
            }
        )
        if len(context) > 50000:
            raise ValueError('Architecture review context exceeds bounded publication; decompose the plan')
    successor, created = ledger.handoff(
        source_invocation_id=row['invocation_id'],
        source_run_id=row['automation_run_id'],
        source_lease_token=row['lease_token'],
        invocation_id=ident,
        project=row['erp_project'],
        task=row['erp_task'],
        from_role=row['target_role'],
        target_role=target,
        task_revision=packet['task_revision'],
        context=context,
        work_packet=packet,
        gate=gate,
        actor=ACTOR,
        db_path=path,
    )
    if created:
        await dispatch(successor['invocation_id'])
    return successor['invocation_id']


def _verified(project, task, path):
    with ledger.connect(path) as db:
        return bool(
            db.execute(
                '''SELECT 1 FROM governance_verification v
          JOIN governance_invocation i ON i.invocation_id=v.invocation_id
          WHERE i.erp_project=? AND i.erp_task=? AND i.state='verified' ''',
                (project, task),
            ).fetchone()
        )


async def _task_done(bridge, project, ident, path):
    task = await bridge.erp.get('Task', ident)
    if task.get('project') != project:
        raise ValueError('Cross-project dependency/child rejected')
    return task.get('status') in DONE and (not task.get('custom_agentic_managed') or _verified(project, ident, path))


async def _ancestry(bridge, project, task, ancestor):
    chain = []
    cursor = task
    for _ in range(7):
        value = await bridge.erp.get('Task', cursor)
        if value.get('project') != project:
            raise ValueError('Cross-project Task ancestry rejected')
        chain.append(cursor)
        if cursor == ancestor:
            return chain
        cursor = value.get('parent_task')
        if not cursor or cursor in chain:
            break
    raise ValueError('Child is not a bounded descendant of its source Task')


def _fanout(row, packet, chain, gate, path, *, purpose):
    """Trusted deterministic coordinator may fan out to verified nested children.

    Ordinary agent APIs still use the ledger's stricter direct-child handoff.
    The actual source run, Task ancestry and source lease are audited atomically.
    """
    ident = 'agov-child-' + digest([row['invocation_id'], packet['task_id'], packet['binding']['role'], purpose])[:48]
    with ledger._transaction(path) as db:
        prior = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (ident,)).fetchone()
        if prior:
            return dict(prior), False
        source = ledger._owned(
            db, row['invocation_id'], row['automation_run_id'], row['lease_token'], {'execution_finished'}
        )
        if source:
            ledger._task_gate(db, source['erp_project'], source['erp_task'])
        if (
            not source
            or source['erp_project'] != packet['project_id']
            or chain[-1] != source['erp_task']
            or chain[0] != packet['task_id']
        ):
            raise ValueError('Source child-fanout authority changed')
        result, created = ledger._enqueue(
            db,
            invocation_id=ident,
            project=packet['project_id'],
            task=packet['task_id'],
            from_role='project_manager',
            target_role=packet['binding']['role'],
            task_revision=packet['task_revision'],
            context=purpose + '; actual source ' + row['invocation_id'],
            work_packet=packet,
            actor=ACTOR,
            gate=gate,
        )
        db.execute(
            'UPDATE governance_invocation SET parent_invocation_id=? WHERE invocation_id=?',
            (row['invocation_id'], ident),
        )
        ledger._event(
            db,
            row['erp_project'],
            ident,
            'verified_descendant_dispatch',
            ACTOR,
            {
                'source': row['invocation_id'],
                'source_role': row['target_role'],
                'ancestor_chain': chain,
                'packet_hash': digest(packet),
            },
        )
        return result, created


def _wait(row, kind, children, packet, path):
    children = sorted(children)
    fingerprint = _semantic(packet)
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute(
            'SELECT kind,children,context_hash FROM governance_lifecycle_wait WHERE source=?', (row['invocation_id'],)
        ).fetchone()
        if previous and tuple(previous) != (kind, canonical(children), fingerprint):
            db.execute('ROLLBACK')
            raise ValueError('Original parent wait scope is immutable')
        db.execute(
            'INSERT OR IGNORE INTO governance_lifecycle_wait(source,project,task,kind,children,context_hash) VALUES(?,?,?,?,?,?)',
            (row['invocation_id'], row['erp_project'], row['erp_task'], kind, canonical(children), fingerprint),
        )
        db.execute('COMMIT')


def _resume(row, packet, gate, path, children, kind):
    """New unit of acceptance work after independently verified descendants.

    No original request is resubmitted. The old parent fence transfers only in
    the same transaction that claims this new, deterministic continuation.
    """
    with _db(path):
        pass
    target = packet['binding']['role']
    ident = 'agov-parent-' + digest([row['invocation_id'], kind, children, target])[:48]
    context = (
        'Continue remaining authorized work from the exact published checkpoint; this is a distinct new work unit, never replay the previous execution.'
        if kind == 'continue'
        else 'Required descendants passed independent QA; verify THIS parent acceptance, do not infer completion from children.'
    )
    architecture = _architecture_source(row, path) if target == 'project_manager' else None
    if architecture:
        arch, saved = architecture
        context = canonical(
            {
                'instruction': context,
                'predecessor_invocation': row['invocation_id'],
                'architect_invocation': arch['invocation_id'],
                'architect_plan': saved['result']['architect_plan'],
                'published_artifacts': saved['event'].get('artifacts', []),
            }
        )
        if len(context) > 50000:
            raise ValueError('Resumed PM review context exceeds bounded publication; decompose the plan')
    with ledger._transaction(path) as db:
        prior = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (ident,)).fetchone()
        if prior:
            return dict(prior), False
        source = ledger._owned(
            db, row['invocation_id'], row['automation_run_id'], row['lease_token'], {'execution_finished'}
        )
        if not source:
            raise ValueError('Parent run was stopped or superseded while waiting')
        for task in children:
            if not db.execute(
                '''SELECT 1 FROM governance_verification v JOIN governance_invocation i
                ON i.invocation_id=v.invocation_id WHERE i.erp_project=? AND i.erp_task=? AND i.state='verified' ''',
                (row['erp_project'], task),
            ).fetchone():
                raise ValueError('Parent cannot resume before child independent verification')
        ledger._gate(db, row['erp_project'], gate)
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        db.execute("UPDATE governance_invocation SET state='handed_off' WHERE invocation_id=?", (row['invocation_id'],))
        ledger._release(db, source)
        successor, created = ledger._enqueue(
            db,
            invocation_id=ident,
            project=row['erp_project'],
            task=row['erp_task'],
            from_role='erp' if target == 'project_manager' else 'project_manager',
            target_role=target,
            task_revision=packet['task_revision'],
            context=context,
            work_packet=packet,
            actor=ACTOR,
            gate=gate,
        )
        db.execute(
            'UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',
            (row['invocation_id'], ident),
        )
        db.execute(
            "UPDATE governance_lifecycle_wait SET phase='resumed',successor=? WHERE source=?",
            (ident, row['invocation_id']),
        )
        ledger._event(
            db,
            row['erp_project'],
            ident,
            'parent_acceptance_resumed',
            ACTOR,
            {
                'original_parent': row['invocation_id'],
                'actual_source_role': row['target_role'],
                'verified_children': children,
                'kind': kind,
            },
        )
        return successor, created


async def _release_plan(bridge, row, actual, plan, path):
    plan_hash = digest(plan)
    response = await bridge.erp.request(
        'POST',
        RPC + 'release_backlog',
        json={
            'project_id': row['erp_project'],
            'source_run_id': row['automation_run_id'],
            'task_ids': sorted(actual.values()),
            'plan_digest': plan_hash,
        },
        headers={'X-ABIT-Agentic-Bridge': bridge.secret},
    )
    receipt = response.get('message') or {}
    if (
        receipt.get('released') is not True
        or receipt.get('plan_digest') != plan_hash
        or set(receipt.get('task_ids', [])) != set(actual.values())
    ):
        raise ValueError('ERP did not atomically release the complete reviewed backlog')


async def _repair_children(row, result, packet, bridge, path, gate):
    with ledger._transaction(path) as db:
        ledger._gate(db, row['erp_project'], gate)
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        if not ledger._owned(
            db, row['invocation_id'], row['automation_run_id'], row['lease_token'], {'execution_finished'}
        ):
            raise ValueError('Original blocker no longer owns its Task lease')
    actions = result.get('action_requests') or []
    defects = result.get('defects') or []
    if actions:
        if row['target_role'] not in {'developer', 'operations', 'project_manager', 'solution_architect'}:
            raise ValueError('This role cannot delegate infrastructure actions directly')
        defects = [
            *defects,
            *[
                {
                    'id': a['request_key'],
                    'requirement_ids': packet['requirement_ids'],
                    'reproduction': a['reason'],
                    'observed': 'A bounded infrastructure action is required before parent acceptance can continue.',
                    'expected': '\n'.join(a['acceptance_criteria']),
                    'tool_action': {k: a[k] for k in ('operation', 'risk', 'parameters', 'request_key')},
                }
                for a in actions
            ],
        ]
    if not 0 < len(defects) <= 5:
        raise ValueError('Blocked work requires 1..5 concrete repair issues')
    for issue in defects:
        if len(issue['reproduction'] + '\nObserved: ' + issue['observed']) > 6000 or len(issue['expected']) > 6000:
            raise ValueError('Repair must be decomposed into a bounded atomic issue')
    with ledger.connect(path) as db:
        blocks = db.execute(
            "SELECT count(*) FROM governance_result r JOIN governance_invocation i ON i.invocation_id=r.invocation_id WHERE i.erp_project=? AND i.erp_task=? AND json_extract(r.result_json,'$.outcome')='blocked'",
            (row['erp_project'], row['erp_task']),
        ).fetchone()[0]
    if blocks > 3:
        raise ValueError('Repeated parent blocker requires explicit PM intervention')
    parent = await bridge.erp.get('Task', row['erp_task'])
    if int(parent.get('custom_agov_repair_depth') or 0) >= 2:
        raise ValueError('Repair recursion limit reached')
    actual = []
    for issue in defects:
        if not set(issue['requirement_ids']) <= set(packet['requirement_ids']):
            raise ValueError('Repair expands source requirements')
        key = (
            'agov-repair-'
            + digest([row['erp_project'], row['erp_task'], row['invocation_id'], packet['task_revision'], issue])[:48]
        )
        proposal = {
            'project_id': row['erp_project'],
            'source_role': row['target_role'],
            'source_agent_id': row['agent_id'],
            'source_run_id': row['automation_run_id'],
            'source_invocation_id': row['invocation_id'],
            'target_role': 'it_manager' if issue.get('tool_action') else 'developer',
            'idempotency_key': key,
            'srs_revision': packet['srs']['revision'],
            'requirement_ids': issue['requirement_ids'],
            'requirement_id': issue['requirement_ids'][0],
            'parent_task': row['erp_task'],
            'dependencies': [],
            'subject': 'Repair ' + issue['id'],
            'description': issue['reproduction'] + '\nObserved: ' + issue['observed'],
            'acceptance_tests': issue['expected'],
            'reason': 'Concrete blocker from ' + row['invocation_id'],
            'priority': 'High',
            'estimate_hours': 1,
            'repair': not bool(issue.get('tool_action')),
            'artifacts': [],
        }
        if issue.get('tool_action'):
            proposal['tool_action'] = issue['tool_action']
        with _db(path) as db:
            db.execute('BEGIN IMMEDIATE')
            ledger._gate(db, row['erp_project'], gate)
            ledger._task_gate(db, row['erp_project'], row['erp_task'])
            if not ledger._owned(
                db, row['invocation_id'], row['automation_run_id'], row['lease_token'], {'execution_finished'}
            ):
                raise ValueError('Blocker ownership changed before child creation')
            old = db.execute(
                'SELECT * FROM governance_repair_publication WHERE source=? AND issue=?', (row['invocation_id'], key)
            ).fetchone()
            if old and old['request_json'] != canonical(proposal):
                db.execute('ROLLBACK')
                raise ValueError('Repair request changed')
            claimed = old is None
            if claimed:
                db.execute(
                    "INSERT INTO governance_repair_publication VALUES(?,?,?,'uncertain',NULL)",
                    (row['invocation_id'], key, canonical(proposal)),
                )
            db.execute('COMMIT')
        if old and old['phase'] == 'confirmed':
            task_id = old['task']
        elif claimed:
            response = await bridge.create_task(proposal)
            task_id = response.get('task_id')
        else:
            response = await bridge.erp.request(
                'GET',
                RPC + 'get_task_request_receipt',
                params={'project_id': row['erp_project'], 'idempotency_key': key},
                headers={'X-ABIT-Agentic-Bridge': bridge.secret},
            )
            value = response.get('message') or {}
            if value.get('exists') is not True or value.get('content_sha256') != digest(proposal):
                raise ValueError('Original repair creation remains uncertain; do not resend')
            task_id = value.get('task_id')
        task = await bridge.erp.get('Task', task_id)
        if (
            task.get('project') != row['erp_project']
            or task.get('parent_task') != row['erp_task']
            or task.get('custom_agov_source_run') != row['automation_run_id']
        ):
            raise ValueError('Repair Task origin readback failed')
        with _db(path) as db:
            db.execute(
                "UPDATE governance_repair_publication SET phase='confirmed',task=? WHERE source=? AND issue=?",
                (task_id, row['invocation_id'], key),
            )
        actual.append(task_id)
    return actual


async def advance(invocation_id, receipt, *, config, project_gate, dispatch, db_path=None, bridge=None):
    row, saved, original = _source(invocation_id, receipt, db_path)
    result = saved['result']
    role = row['target_role']
    outcome = result['outcome']
    owned = bridge is None
    bridge = bridge or ERPBridge(config)
    try:
        _, gate = await project_gate(row['erp_project'], config)
        with ledger.connect(db_path) as db:
            ledger._gate(db, row['erp_project'], gate)
        if row['state'] not in {'execution_finished', 'handed_off', 'verified'}:
            return
        if not _task_allowed(row['erp_project'], row['erp_task'], db_path):
            return
        if result.get('action_requests'):
            if row['state'] != 'execution_finished':
                return
            actual = await _repair_children(row, result, original, bridge, db_path, gate)
            packet = await _packet(bridge, row, role)
            _wait(row, 'action', actual, packet, db_path)
        elif role == 'solution_architect' and outcome == 'ready_for_review':
            if row['state'] == 'execution_finished':
                await _handoff(
                    row,
                    'project_manager',
                    'Review exact published architect plan ' + digest(result.get('architect_plan')),
                    bridge,
                    gate,
                    dispatch,
                    db_path,
                )
        elif role == 'project_manager' and outcome == 'ready_for_review' and row['state'] == 'execution_finished':
            architecture = _architecture_source(row, db_path)
            adoption = original.get('atomic_adoption')
            if not architecture and adoption:
                if row['state'] != 'execution_finished':
                    return
                if (
                    adoption.get('task_revision') != original['task_revision']
                    or adoption.get('requirement_ids') != original['requirement_ids']
                    or adoption.get('executor_role') not in EXECUTORS
                    or not adoption.get('adoption_id')
                ):
                    raise ValueError('Atomic PM delegation differs from the original human adoption')
                current = await _packet(bridge, row, 'project_manager')
                if current.get('atomic_adoption') != adoption or current['task_revision'] != original['task_revision']:
                    raise ValueError('Human-adopted Task changed before PM delegation')
                await _handoff(
                    row,
                    adoption['executor_role'],
                    'Execute the exact human-adopted atomic Task after its published PM review',
                    bridge,
                    gate,
                    dispatch,
                    db_path,
                )
            else:
                if not architecture:
                    raise ValueError('PM publication requires its exact architect predecessor or human atomic adoption')
                source, arch = architecture
                from open_webui.utils.agentic_planning import materialize_backlog

                actual = await materialize_backlog(bridge, source, arch['result'], row, original, db_path)
                await _release_plan(bridge, row, actual, arch['result']['architect_plan'], db_path)
                packet = await _packet(bridge, row, 'project_manager')
                _wait(row, 'plan', actual.values(), packet, db_path)
        elif role in EXECUTORS and outcome == 'ready_for_review':
            if row['state'] == 'execution_finished':
                await _handoff(
                    row,
                    'qa',
                    'Independently test the exact implementation ' + row['automation_run_id'],
                    bridge,
                    gate,
                    dispatch,
                    db_path,
                )
        elif role == 'qa' and outcome == 'pass':
            source = ledger.find(
                result.get('implementation_invocation_id') or row.get('source_invocation_id') or '',
                db_path,
                include_private=True,
            )
            if (
                not source
                or source['erp_project'] != row['erp_project']
                or source['erp_task'] != row['erp_task']
                or source['agent_id'] != result.get('implementation_agent_id')
            ):
                raise ValueError('QA does not identify its exact original implementation')
            if receipt['status'] != 'QA APPROVED':
                raise ValueError('ERP has not independently approved the task')
            ledger.verify_completion(
                source['invocation_id'],
                invocation_id,
                digest(result['evidence']),
                digest(receipt),
                ACTOR,
                db_path=db_path,
            )
        elif role == 'qa' and outcome == 'fail':
            with ledger.connect(db_path) as db:
                count = db.execute(
                    "SELECT count(*) FROM governance_invocation WHERE erp_project=? AND erp_task=? AND target_role='qa' AND qa_verdict='fail'",
                    (row['erp_project'], row['erp_task']),
                ).fetchone()[0]
            if count > 3:
                raise ValueError('Same-task QA rework limit reached; explicit PM intervention required')
            source = ledger.find(
                result.get('implementation_invocation_id') or row.get('source_invocation_id') or '',
                db_path,
                include_private=True,
            )
            if (
                not source
                or source['erp_project'] != row['erp_project']
                or source['erp_task'] != row['erp_task']
                or source['target_role'] not in EXECUTORS
            ):
                raise ValueError('QA rejection requires its exact original executor')
            if result.get('implementation_agent_id') and result['implementation_agent_id'] != source['agent_id']:
                raise ValueError('QA rejection implementation identity differs')
            if source['target_role'] == 'it_manager':
                details = canonical(
                    {
                        'implementation_invocation_id': source['invocation_id'],
                        'defects': result.get('defects', []),
                        'next': 'PM must scope a new repair/action and obtain fresh required approval; never replay the original privileged action',
                    }
                )
                with _db(db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    existing = db.execute(
                        'SELECT details FROM governance_lifecycle_hold WHERE source=?', (invocation_id,)
                    ).fetchone()
                    if existing and existing[0] != details:
                        db.execute('ROLLBACK')
                        raise ValueError('Original IT intervention scope changed')
                    if not existing:
                        db.execute(
                            'INSERT INTO governance_lifecycle_hold VALUES(?,?,?,?,?)',
                            (
                                invocation_id,
                                row['erp_project'],
                                row['erp_task'],
                                'it_qa_intervention_required',
                                details,
                            ),
                        )
                        ledger._event(
                            db,
                            row['erp_project'],
                            invocation_id,
                            'it_qa_intervention_required',
                            ACTOR,
                            json.loads(details),
                        )
                    db.execute('COMMIT')
            elif row['state'] == 'execution_finished':
                await _handoff(
                    row,
                    source['target_role'],
                    'Correct the original independent QA defects: ' + canonical(result.get('defects', [])),
                    bridge,
                    gate,
                    dispatch,
                    db_path,
                )
        elif outcome == 'continue':
            if role not in {'solution_architect', 'project_manager', 'developer', 'operations'}:
                raise ValueError('This specialist cannot replay a structured action as model continuation')
            if row['state'] != 'execution_finished':
                return
            with ledger.connect(db_path) as db:
                count = db.execute(
                    "SELECT count(*) FROM governance_result r JOIN governance_invocation i ON i.invocation_id=r.invocation_id WHERE i.erp_project=? AND i.erp_task=? AND json_extract(r.result_json,'$.outcome')='continue'",
                    (row['erp_project'], row['erp_task']),
                ).fetchone()[0]
            budget = min(20, max(1, int(config.get('agentic_max_continuations', 10))))
            if count > budget:
                raise ValueError('Task continuation budget exhausted; PM checkpoint required')
            packet = await _packet(bridge, row, role)
            for field in ('task_revision', 'srs', 'repository', 'environment', 'instructions', 'activity'):
                if packet.get(field) != original.get(field):
                    raise ValueError('Task scope changed before continuation')
            successor, created = _resume(row, packet, gate, db_path, [], 'continue')
            if created:
                await dispatch(successor['invocation_id'])
        elif outcome == 'blocked':
            if row['state'] != 'execution_finished':
                return
            actual = await _repair_children(row, result, original, bridge, db_path, gate)
            packet = await _packet(bridge, row, role)
            _wait(row, 'repair', actual, packet, db_path)
        await scan_project(
            row['erp_project'],
            config=config,
            project_gate=project_gate,
            dispatch=dispatch,
            db_path=db_path,
            bridge=bridge,
        )
    finally:
        if owned:
            await bridge.close()


async def scan_project(project, *, config, project_gate, dispatch, db_path=None, bridge=None):
    """Normal events/QA completion wake dependency-ready work; no new timer."""
    owned = bridge is None
    bridge = bridge or ERPBridge(config)
    try:
        _, gate = await project_gate(project, config)
        with ledger.connect(db_path) as db:
            ledger._gate(db, project, gate)
        with _db(db_path) as db:
            waiting = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM governance_lifecycle_wait WHERE project=? AND phase='waiting'", (project,)
                )
            ]
        for wait in waiting:
            source = ledger.find(wait['source'], db_path, include_private=True)
            if (
                not source
                or source['state'] != 'execution_finished'
                or not _task_allowed(project, source['erp_task'], db_path)
            ):
                continue
            children = json.loads(wait['children'])
            all_done = True
            candidates = [(ident, await bridge.erp.get('Task', ident)) for ident in children]
            priorities = {'Urgent': 0, 'High': 1, 'Medium': 2, 'Low': 3}
            candidates.sort(
                key=lambda pair: (
                    priorities.get(pair[1].get('priority'), 2),
                    str(pair[1].get('exp_end_date') or '9999'),
                    pair[0],
                )
            )
            for ident, task in candidates:
                if task.get('project') != project:
                    raise ValueError('Waiting task moved to another project')
                if await _task_done(bridge, project, ident, db_path):
                    continue
                if not _task_allowed(project, ident, db_path):
                    all_done = False
                    continue
                all_done = False
                with ledger.connect(db_path) as db:
                    active = db.execute(
                        "SELECT 1 FROM governance_task_lease WHERE erp_project=? AND erp_task=? AND invocation_id IS NOT NULL",
                        (project, ident),
                    ).fetchone()
                with ledger.connect(db_path) as db:
                    inflight = db.execute(
                        "SELECT count(*) FROM governance_invocation WHERE erp_project=? AND state IN ('queued','running','uncertain','cancel_requested','awaiting_approval')",
                        (project,),
                    ).fetchone()[0]
                maximum = min(20, max(1, int(config.get('agentic_max_project_inflight', 6))))
                if inflight >= maximum:
                    continue
                if (
                    active
                    or task.get('status') not in {'Open', 'Overdue'}
                    or not task.get('custom_agov_dispatch_released', True)
                ):
                    continue
                dependencies = [r['task'] for r in task.get('depends_on', [])]
                if not all([await _task_done(bridge, project, dep, db_path) for dep in dependencies]):
                    continue
                if task.get('is_group'):
                    nested = [
                        r async for r in bridge.erp.rows('Task', ['name', 'status'], [['parent_task', '=', ident]])
                    ]
                    if not nested or not all([await _task_done(bridge, project, r['name'], db_path) for r in nested]):
                        continue
                target = 'developer'
                if wait['kind'] in {'action', 'repair'}:
                    with _db(db_path) as db:
                        proposal = db.execute(
                            "SELECT request_json FROM governance_repair_publication WHERE source=? AND task=? AND phase='confirmed'",
                            (wait['source'], ident),
                        ).fetchone()
                    if not proposal:
                        raise ValueError('Repair/action child has no original confirmed role-scoped request')
                    target = json.loads(proposal[0])['target_role']
                if wait['kind'] == 'plan':
                    with ledger.connect(db_path) as db:
                        proposal = db.execute(
                            'SELECT request_json FROM governance_backlog_task WHERE erp_task=?', (ident,)
                        ).fetchone()
                    if not proposal:
                        raise ValueError('Released Task has no original published plan request')
                    target = json.loads(proposal[0])['target_role']
                if target not in EXECUTORS:
                    raise ValueError('Unsupported specialist requires a governed executable capability')
                packet = await bridge.packet(project, ident, target, ledger.agent_id(project, target))
                chain = await _ancestry(bridge, project, ident, source['erp_task'])
                row, created = _fanout(
                    source, packet, chain, gate, db_path, purpose='Released ' + wait['kind'] + ' child acceptance work'
                )
                if created:
                    await dispatch(row['invocation_id'])
            if all_done:
                target = 'developer' if wait['kind'] == 'plan' else source['target_role']
                packet = await _packet(bridge, source, target)
                # Bind the parent business inputs/SRS/repository/environment;
                # role capabilities legitimately differ for final acceptance.
                old = json.loads(source['work_packet'])
                for field in ('task_revision', 'srs', 'repository', 'environment', 'instructions'):
                    if packet.get(field) != old.get(field):
                        raise ValueError('Parent scope changed while descendants executed')
                if _semantic(await _packet(bridge, source, source['target_role'])) != wait['context_hash']:
                    raise ValueError('Parent activity changed during child execution')
                successor, created = _resume(source, packet, gate, db_path, children, wait['kind'])
                if created:
                    await dispatch(successor['invocation_id'])
    finally:
        if owned:
            await bridge.close()

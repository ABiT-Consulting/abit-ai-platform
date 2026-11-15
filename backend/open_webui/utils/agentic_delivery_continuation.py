"""Bounded recovery of one original Developer delivery by its server continuation.

No request selects a predecessor. Remote step identities and test observations
remain owned by the original invocation; only a currently authorized successor
may finish missing publication steps and import verified evidence bytes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest, stable_run_id

POLICY = {
    'version': 'developer-delivery-continuation.v1',
    'maximum_edges': 20,
    'source_role': 'developer',
    'edge_kinds': ['continue', 'delivery'],
    'fresh_work_boundaries': ['repair', 'action'],
    'original_tests_reexecuted': False,
    'remote_steps_replayed': False,
    'evidence_import': 'immutable-original-bytes',
}
POLICY_SHA256 = digest(POLICY)
AUDIT_KIND = 'delivery_continuation'
KEY = 'delivery_continuation'
IDENTITY = ('invocation_id', 'erp_project', 'erp_task', 'target_role', 'agent_id',
            'automation_run_id', 'task_revision', 'packet_hash', 'source_invocation_id',
            'context_hash', 'lease_fence', 'submitted_at')
MANAGED = {'packet_id', 'content_sha256', 'source_modified', 'attachments', 'project_backlog'}
HISTORICAL_STATES = {'running', 'uncertain', 'cancel_requested', 'execution_finished',
                     'handed_off', 'verified'}


def need(value, message):
    if not value:
        raise ValueError(message)


def one(db, query, args):
    value = db.execute(query, args).fetchone()
    return dict(value) if value is not None else None


def frozen_packet(row):
    packet = json.loads(row.get('work_packet') or 'null')
    need(isinstance(packet, dict) and row.get('target_role') == 'developer'
         and row.get('packet_hash') == digest(packet)
         and packet.get('content_sha256') == packet_digest(packet)
         and row.get('automation_run_id') == stable_run_id(row['invocation_id'])
         and type(row.get('submitted_at')) is int and row['submitted_at'] > 0,
         'Delivery continuation requires each original submitted Developer packet')
    need(packet.get('binding') == {
        'project_id': row['erp_project'], 'role': 'developer',
        'agent_id': row['agent_id'], 'erp_user': packet.get('binding', {}).get('erp_user')}
         and all(packet.get(k) == row[v] for k, v in
                 [('project_id', 'erp_project'), ('task_id', 'erp_task'), ('task_revision', 'task_revision')])
         and row.get('context_hash') == ledger.work_digest(
             row['erp_project'], row['erp_task'], 'developer', row.get('context') or ''),
         'Delivery continuation packet or original context identity changed')
    return packet


def _core_publication(db, row, packet, result, event):
    """Verify an exact audited native frame without changing stored Core bytes."""
    need(isinstance(event.get('result_contract'), dict),
         'Delivery continuation requires the original result contract')
    original = deepcopy(event)
    original['work_packet_digest'] = packet_digest(packet)
    original['result_contract']['work_packet_digest'] = packet_digest(packet)
    from open_webui.utils.agentic_start_recovery import project_native_result
    projected, expected = project_native_result(row, packet, result, original, db=db)
    need(projected == event, 'Delivery continuation native projection changed')
    audits = db.execute(
        "SELECT * FROM governance_event WHERE invocation_id=? AND kind='readiness_native_result'",
        (row['invocation_id'],)).fetchall()
    if expected is None:
        need(not audits, 'Original readiness result projection cannot be discarded')
    else:
        need(len(audits) == 1, 'Delivery continuation requires one atomic readiness result audit')
        audit = dict(audits[0])
        need(audit['erp_project'] == row['erp_project']
             and audit['actor'] == 'server-readiness-policy'
             and json.loads(audit['payload']) == expected
             and audit['event_hash'] == ledger._digest([
                 audit['event_id'], audit['erp_project'], audit['invocation_id'], audit['kind'],
                 audit['actor'], audit['payload'], audit['created_at'], audit['previous_hash']]),
             'Delivery continuation readiness result audit changed')
    return original


def published(db, row, *, allow_ready=False):
    saved = one(db, 'SELECT * FROM governance_result WHERE invocation_id=?', (row['invocation_id'],))
    need(saved is not None and saved['phase'] == 'published' and saved['run_id'] == row['automation_run_id'],
         'Delivery continuation requires the exact published predecessor')
    result, event, receipt = (json.loads(saved.get(k) or 'null')
                              for k in ('result_json', 'event_json', 'receipt_json'))
    packet = frozen_packet(row)
    need(isinstance(result, dict) and isinstance(event, dict) and isinstance(receipt, dict),
         'Delivery continuation published result or native receipt is missing')
    core_event = _core_publication(db, row, packet, result, event)
    outcomes = {'continue': ('run.checkpoint', 'Working'), 'blocked': ('run.blocked', 'Blocked')}
    if allow_ready:
        outcomes['ready_for_review'] = ('run.ready', 'Pending Review')
    need(isinstance(result, dict) and isinstance(event, dict) and isinstance(receipt, dict)
         and result.get('outcome') in outcomes
         and event.get('type') == outcomes[result['outcome']][0]
         and saved['result_sha256'] == digest(result)
         and result.get('invocation_id') == row['invocation_id']
         and result.get('actor') == packet['binding']
         and result.get('project_id') == row['erp_project']
         and result.get('task_id') == row['erp_task']
         and result.get('task_revision') == row['task_revision']
         and result.get('work_packet_digest') == packet_digest(packet)
         and core_event.get('result_contract') == result
         and event.get('invocation_id') == row['invocation_id']
         and event.get('run_id') == row['automation_run_id']
         and event.get('project_id') == row['erp_project']
         and event.get('task_id') == row['erp_task']
         and event.get('role') == 'developer'
         and event.get('agent_id') == row['agent_id']
         and core_event.get('work_packet_digest') == packet_digest(packet)
         and event.get('task_revision') == row['task_revision']
         and event.get('acceptance_passed') is False
         and receipt.get('durable') is True and bool(receipt.get('modified'))
         and receipt.get('status') == outcomes[result['outcome']][1]
         and all(receipt.get(k) == event.get(k) for k in ('event_id', 'task_id', 'run_id'))
         and bool(event.get('event_id')),
         'Delivery continuation published result or native receipt changed')
    return result, event, receipt


def scope_equal(left, right):
    need({k: v for k, v in left.items() if k not in MANAGED}
         == {k: v for k, v in right.items() if k not in MANAGED},
         'Delivery continuation changed repository, SRS, acceptance, role or business scope')
    before, after = left.get('project_backlog'), right.get('project_backlog')
    if before is None and after is None:
        return
    need(isinstance(before, dict) and isinstance(after, dict)
         and before.get('complete') is True and after.get('complete') is True
         and {k: v for k, v in before.items() if k not in {'tasks', 'snapshot_sha256'}}
         == {k: v for k, v in after.items() if k not in {'tasks', 'snapshot_sha256'}},
         'Delivery continuation Project backlog business context changed')


def _edge(db, child, parent):
    rows = db.execute(
        "SELECT * FROM governance_event WHERE invocation_id=? AND kind='parent_acceptance_resumed'",
        (child['invocation_id'],)).fetchall()
    need(len(rows) == 1, 'Delivery continuation requires one server lifecycle edge')
    audit = dict(rows[0])
    proof = json.loads(audit['payload'])
    kind = proof.get('kind')
    children = proof.get('independently_verified_children')
    external = proof.get('externally_completed_dependencies')
    need(audit['erp_project'] == child['erp_project']
         and audit['actor'] == 'governance-lifecycle-coordinator'
         and audit['event_hash'] == ledger._digest([
             audit['event_id'], audit['erp_project'], audit['invocation_id'], audit['kind'],
             audit['actor'], audit['payload'], audit['created_at'], audit['previous_hash']])
         and proof.get('original_parent') == parent['invocation_id']
         and proof.get('actual_source_role') == 'developer'
         and kind in POLICY['edge_kinds'] and isinstance(children, list)
         and external == [] and len(children) <= 50 and len(set(children)) == len(children),
         'Delivery continuation lifecycle proof changed')
    result, event, receipt = published(db, parent)
    need(children == [] and not result.get('defects') and not result.get('action_requests'),
         'Delivery continuation cannot borrow implementation after independent defect or action work')
    if kind == 'continue':
        need(result.get('outcome') == 'continue' and event.get('type') == 'run.checkpoint',
             'Delivery continuation is not a published continue checkpoint')
    else:
        need(result.get('outcome') == 'blocked' and event.get('type') == 'run.blocked',
             'Delivery-only continuation requires its exact published platform blocker')
    need(child['invocation_id'] == 'agov-parent-' + digest(
        [parent['invocation_id'], kind, sorted(children), 'developer'])[:48],
         'Delivery continuation identity is not its deterministic original lifecycle successor')
    return {
        'kind': kind, 'child_invocation_id': child['invocation_id'],
        'child_packet_sha256': child['packet_hash'],
        'parent_invocation_id': parent['invocation_id'],
        'parent_run_id': parent['automation_run_id'],
        'parent_packet_sha256': parent['packet_hash'],
        'result_sha256': digest(result), 'event_sha256': digest(event),
        'receipt_sha256': digest(receipt), 'lifecycle_event_hash': audit['event_hash'],
    }, (parent, result, event)


def _fresh_work_boundary(db, child, parent):
    """A proved repair/action starts new tested work; it never imports old delivery."""
    rows = db.execute(
        "SELECT * FROM governance_event WHERE invocation_id=? AND kind='parent_acceptance_resumed'",
        (child['invocation_id'],)).fetchall()
    if len(rows) != 1:
        return False
    event = dict(rows[0])
    proof = json.loads(event['payload'])
    kind = proof.get('kind')
    if kind not in {'repair', 'action'}:
        return False
    children = proof.get('independently_verified_children')
    need(parent['state'] == 'handed_off'
         and event['erp_project'] == child['erp_project']
         and event['actor'] == 'governance-lifecycle-coordinator'
         and proof.get('original_parent') == parent['invocation_id']
         and proof.get('actual_source_role') == 'developer'
         and isinstance(children, list) and 1 <= len(children) <= 5
         and len(set(children)) == len(children)
         and proof.get('externally_completed_dependencies') == []
         and child['invocation_id'] == 'agov-parent-' + digest(
             [parent['invocation_id'], kind, sorted(children), 'developer'])[:48]
         and event['event_hash'] == ledger._digest([event['event_id'], event['erp_project'],
             event['invocation_id'], event['kind'], event['actor'], event['payload'],
             event['created_at'], event['previous_hash']]),
         'Fresh repair/action work lacks its exact server lifecycle edge')
    result, _, _ = published(db, parent, allow_ready=True)
    need((kind == 'repair' and result.get('outcome') == 'blocked'
          and bool(result.get('defects')) and not result.get('action_requests'))
         or (kind == 'action' and bool(result.get('action_requests'))),
         'Fresh repair/action work has no exact published source issue')
    parent_packet, child_packet = frozen_packet(parent), frozen_packet(child)
    need(all(child_packet.get(key) == parent_packet.get(key)
             for key in ('task_revision', 'srs', 'repository', 'environment', 'instructions')),
         'Fresh repair/action parent business scope changed')
    wait = one(db, 'SELECT * FROM governance_lifecycle_wait WHERE source=?', (parent['invocation_id'],))
    from open_webui.utils.agentic_orchestrator import _semantic
    need(wait is not None and wait['project'] == child['erp_project'] and wait['task'] == child['erp_task']
         and wait['kind'] == kind and wait['phase'] == 'resumed'
         and wait['successor'] == child['invocation_id']
         and json.loads(wait['children']) == sorted(children)
         and wait['context_hash'] == _semantic(child_packet),
         'Fresh repair/action work lacks its exact resumed dependency wait')
    for task in children:
        publications = db.execute(
            "SELECT * FROM governance_repair_publication WHERE source=? AND task=? AND phase='confirmed'",
            (parent['invocation_id'], task)).fetchall()
        need(len(publications) == 1, 'Repair/action child has no exact confirmed source request')
        publication = dict(publications[0])
        request = json.loads(publication['request_json'])
        need(all(request.get(key) == value for key, value in {
                 'project_id': parent['erp_project'], 'source_role': 'developer',
                 'source_agent_id': parent['agent_id'], 'source_run_id': parent['automation_run_id'],
                 'source_invocation_id': parent['invocation_id'], 'parent_task': parent['erp_task'],
                 'idempotency_key': publication['issue']}.items())
             and request.get('target_role') in {'developer', 'it_manager'}
             and db.execute(
                 """SELECT 1 FROM governance_verification v JOIN governance_invocation i
                 ON i.invocation_id=v.invocation_id WHERE i.erp_project=? AND i.erp_task=?
                 AND i.target_role=? AND i.state='verified'""",
                 (child['erp_project'], task, request['target_role'])).fetchone(),
             'Repair/action child is not the exact independently verified source request')
    return True


def derive(row, packet, *, db, require_active=True):
    """Return a trusted lineage or None for ordinary, non-inherited delivery."""
    current = one(db, 'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],))
    need(current is not None, 'Delivery invocation is unavailable')
    records, edges, publications, nodes, seen = [], [], [], [current], set()
    inherited_marker = False
    cursor = current
    # Discover whether this is inherited delivery before enforcing its special
    # checkpoint policy. Ordinary dependency/repair work with no delivery yet
    # must still create its own workspace, tests and original journal.
    while True:
        need(cursor['invocation_id'] not in seen and len(nodes) - 1 <= POLICY['maximum_edges'],
             'Delivery continuation lineage is cyclic or exceeds its bound')
        seen.add(cursor['invocation_id'])
        found = one(db, 'SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                    (cursor['invocation_id'],))
        if found:
            value = json.loads(found['input_json'])
            if KEY in value:
                inherited_marker = True
            else:
                records.append((cursor, found, value))
        inherited_marker = inherited_marker or bool(db.execute(
            'SELECT 1 FROM governance_event WHERE invocation_id=? AND kind=?',
            (cursor['invocation_id'], AUDIT_KIND)).fetchone())
        parent_id = cursor.get('source_invocation_id')
        if not parent_id:
            break
        parent = one(db, 'SELECT * FROM governance_invocation WHERE invocation_id=?', (parent_id,))
        need(parent is not None, 'Delivery continuation predecessor is missing')
        if (parent['target_role'] != 'developer'
                or any(parent[k] != current[k] for k in ('erp_project', 'erp_task'))):
            break
        if _fresh_work_boundary(db, cursor, parent):
            break
        nodes.append(parent)
        cursor = parent
    need(len(records) <= 1, 'Delivery continuation has ambiguous independent original deliveries')
    if not records or records[0][0]['invocation_id'] == current['invocation_id']:
        need(not inherited_marker, 'Delivery continuation original or immutable binding was discarded')
        return None
    for child, parent in zip(nodes, nodes[1:]):
        need(parent['state'] == 'handed_off'
             and all(parent[k] == current[k] for k in ('erp_project', 'erp_task', 'agent_id', 'target_role')),
             'Delivery continuation crosses an owner, Task, Project or unfinished predecessor')
        edge, publication = _edge(db, child, parent)
        scope_equal(frozen_packet(parent), frozen_packet(child))
        edges.append(edge)
        publications.append(publication)
    need(current['state'] in HISTORICAL_STATES
         and all(current.get(k) == row.get(k) for k in IDENTITY)
         and current.get('lease_token') == row.get('lease_token') and bool(current.get('lease_token')),
         'Delivery continuation current invocation identity changed')
    current_packet = frozen_packet(current)
    need(current_packet == packet, 'Delivery continuation packet is not the current server original')
    if require_active:
        from open_webui.utils.agentic_tool_gateway import _owned
        _owned(db, current['invocation_id'])
    registered = one(db, "SELECT * FROM governance_agent WHERE erp_project=? AND role='developer'",
                     (current['erp_project'],))
    need(registered is not None and registered['agent_id'] == current['agent_id']
         and registered['erp_user'] == packet['binding']['erp_user'],
         'Delivery continuation is not the current registered Developer')
    origin, delivery, original = records[0]
    origin_packet = frozen_packet(origin)
    scope_equal(origin_packet, packet)
    from open_webui.utils.agentic_contracts.validator import validate_work_packet
    bindings = {r['role']: {'agent_id': r['agent_id'], 'erp_user': r['erp_user']}
                for r in db.execute('SELECT * FROM governance_agent WHERE erp_project=?', (current['erp_project'],))}
    for value in [frozen_packet(node) for node in nodes]:
        need(not validate_work_packet(value, registered_bindings=bindings),
             'Delivery continuation packet failed the existing registered contract')
    need(delivery['project_id'] == current['erp_project'] and delivery['task_id'] == current['erp_task']
         and delivery['state'] in {'prepared', 'pending_reconciliation', 'completed'}
         and original.get('packet_sha256') == packet_digest(origin_packet)
         and original.get('branch') is not None and isinstance(original.get('tested'), dict)
         and not original['tested'].get('dependency_verifications')
         and not origin_packet.get('dependency_acceptance_policy'),
         'Delivery continuation requires one frozen repository-only original delivery')
    for edge in edges:
        if edge['kind'] != 'delivery':
            continue
        audits = db.execute(
            "SELECT * FROM governance_event WHERE erp_project=? AND kind='delivery_checkpoint_resumed' "
            "AND json_extract(payload,'$.original_delivery')=?", (current['erp_project'], origin['invocation_id'])).fetchall()
        need(len(audits) == 1, 'Delivery-only successor requires one immutable original recovery audit')
        audit = dict(audits[0])
        need(audit['invocation_id'] == edge['child_invocation_id']
             and audit['actor'] == 'governance-lifecycle-coordinator'
             and json.loads(audit['payload']) == {'blocked_source': edge['parent_invocation_id'],
                 'original_delivery': origin['invocation_id'], 'input_sha256': digest(original)}
             and audit['event_hash'] == ledger._digest([audit['event_id'], audit['erp_project'],
                 audit['invocation_id'], audit['kind'], audit['actor'], audit['payload'],
                 audit['created_at'], audit['previous_hash']]),
             'Delivery-only successor does not match its immutable original audit')
    # Current authority is rechecked separately. An authorized same-invocation
    # recovery must not rewrite a receipt merely because a lease was refreshed.
    identity = {k: current.get(k) for k in IDENTITY if k != 'lease_fence'}
    core = {
        'policy_version': POLICY['version'], 'policy_sha256': POLICY_SHA256,
        'invocation_id': current['invocation_id'], 'run_id': current['automation_run_id'],
        'row_sha256': digest(identity), 'packet_sha256': digest(packet),
        'origin_invocation_id': origin['invocation_id'], 'origin_run_id': origin['automation_run_id'],
        'origin_packet_sha256': digest(origin_packet), 'origin_delivery_input_sha256': digest(original),
        'lineage': edges, 'original_tests_reexecuted': False,
    }
    return {'current': current, 'packet': packet, 'origin': origin, 'origin_packet': origin_packet,
            'delivery': delivery, 'input': original, 'publications': publications, 'nodes': nodes, 'core': core}


def attachments(context, config):
    """Only exact old attachments and ancestor-published, byte-verified Files."""
    from open_webui.utils.agentic_artifacts import artifact_directory, local_bytes
    originals = context['origin_packet'].get('attachments', [])
    need(isinstance(originals, list), 'Invalid original attachment list')
    prior = {item['id']: item for item in originals}
    need(len(prior) == len(originals), 'Duplicate original attachment identity')
    publications = context['publications']
    for index, node in enumerate(context['nodes']):
        values = frozen_packet(node).get('attachments', [])
        need(isinstance(values, list), 'Invalid continuation attachment list')
        now = {item['id']: item for item in values}
        need(len(now) == len(values) and all(now.get(key) == value for key, value in prior.items()),
             'Original delivery attachment was removed or changed')
        added = set(now) - set(prior)
        approved = {}
        for owner, result, event in publications[index:]:
            refs = {ref['id']: ref for ref in result.get('evidence', [])}
            for artifact in event.get('artifacts', []):
                url = urlsplit(artifact.get('url') or '')
                query = parse_qs(url.query)
                if (url.scheme != 'https' or url.netloc != 'erp.abit.ai'
                        or not re.fullmatch(r'/private/files/[A-Za-z0-9_.-]+', url.path)
                        or url.fragment or set(query) != {'fid'} or len(query['fid']) != 1):
                    continue
                fid = query['fid'][0]
                if fid not in added:
                    continue
                reference = refs.get(artifact.get('id'))
                need(reference and reference.get('sha256') == artifact.get('sha256')
                     and reference.get('kind') == artifact.get('kind'),
                     'Published continuation attachment changed')
                _, data = local_bytes(artifact_directory(owner, config), reference['uri'])
                need(hashlib.sha256(data).hexdigest() == artifact['sha256'],
                     'Published continuation attachment bytes changed')
                value = hashlib.md5(data, usedforsecurity=False).hexdigest()
                need(fid not in approved or approved[fid] == value, 'Ambiguous published File identity')
                approved[fid] = value
        for key in added:
            private = now[key].get('is_private')
            need((private is True or type(private) is int and private == 1)
                 and approved.get(key) == now[key].get('content_hash'),
                 'New continuation attachment lacks exact published File and byte provenance')


def test_calls(db, context):
    """Recover original call IDs from immutable observed test evidence, never guess latest."""
    wanted = {run['log_evidence_id'] for run in context['input']['tested'].get('test_runs', [])}
    need(bool(wanted), 'Original delivery has no command test provenance')
    found, provenance = {}, []
    for row in db.execute(
        """SELECT c.*,s.invocation_id FROM governance_gateway_call c
        JOIN governance_gateway_session s ON s.session_id=c.session_id
        WHERE s.invocation_id=? AND c.operation='repository.test' AND c.state='completed'""",
        (context['origin']['invocation_id'],)):
        evidence_id = 'TEST-' + digest(row['call_id'])[:32]
        if evidence_id not in wanted:
            continue
        need(evidence_id not in found, 'Original test call identity is ambiguous')
        found[evidence_id] = row['call_id']
        provenance.append({'call_id': row['call_id'], 'session_id': row['session_id'],
                           'request_hash': row['request_hash'],
                           'result_sha256': digest(json.loads(row['result_json']))})
    need(set(found) == wanted, 'Original delivery command receipt is missing')
    return sorted(found.values()), sorted(provenance, key=lambda item: item['call_id'])


def copied_evidence(context, actual):
    result = deepcopy(actual)
    mapping = [{'id': ref['id'], 'origin_invocation_id': context['origin']['invocation_id'],
                'origin_uri': ref['uri'], 'current_invocation_id': context['current']['invocation_id'],
                'current_uri': ref['uri'], 'sha256': ref['sha256']} for ref in result['evidence']]
    result['invocation_id'] = context['current']['invocation_id']
    result['original_invocation_id'] = context['origin']['invocation_id']
    result['delivery_provenance'] = {
        'policy_version': POLICY['version'], 'origin_invocation_id': context['origin']['invocation_id'],
        'origin_run_id': context['origin']['automation_run_id'], 'original_tests_reexecuted': False}
    return result, mapping


def completed_receipt(db, context):
    """Stored aliases require their atomic audit and exact rederived identity."""
    own = one(db, 'SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
              (context['current']['invocation_id'],))
    audits = db.execute('SELECT * FROM governance_event WHERE invocation_id=? AND kind=?',
                        (context['current']['invocation_id'], AUDIT_KIND)).fetchall()
    if not own:
        need(not audits, 'Delivery continuation audit has no atomic receipt')
        return None
    need(own['state'] == 'completed' and len(audits) == 1
         and own['project_id'] == context['current']['erp_project']
         and own['task_id'] == context['current']['erp_task'],
         'Delivery continuation receipt and audit are incomplete or ambiguous')
    event = dict(audits[0])
    need(event['erp_project'] == context['current']['erp_project']
         and event['actor'] == 'server-delivery-continuation'
         and event['event_hash'] == ledger._digest([event['event_id'], event['erp_project'],
             event['invocation_id'], event['kind'], event['actor'], event['payload'],
             event['created_at'], event['previous_hash']]),
         'Delivery continuation audit identity or hash changed')
    frozen, actual, audit = json.loads(own['input_json']), json.loads(own['result_json']), json.loads(event['payload'])
    need(frozen.get(KEY) == context['core']
         and frozen.get('packet_sha256') == packet_digest(context['packet'])
         and frozen.get('profile_sha256') == context['input']['profile_sha256']
         and frozen.get('snapshot_sha256') == context['input']['snapshot_sha256']
         and audit.get('binding') == context['core']
         and audit.get('result_sha256') == digest(actual),
         'Delivery continuation receipt or original binding changed')
    return frozen, actual, audit


def record_receipt(db, context, actual, mapping, tests):
    """Caller holds its Task lock; preserve result and provenance in one transaction."""
    frozen = {
        KEY: context['core'], 'packet_sha256': packet_digest(context['packet']),
        'profile_sha256': context['input']['profile_sha256'],
        'snapshot_sha256': context['input']['snapshot_sha256'],
    }
    audit = {'binding': context['core'], 'evidence_map': mapping, 'original_test_calls': tests,
             'result_sha256': digest(actual), 'original_tests_reexecuted': False}
    prior = completed_receipt(db, context)
    if prior:
        need(prior == (frozen, actual, audit), 'Original continued delivery receipt is immutable')
        return actual
    import time
    db.execute('INSERT INTO governance_developer_delivery VALUES(?,?,?,?,?,?,?)',
               (context['current']['invocation_id'], context['current']['erp_project'],
                context['current']['erp_task'], canonical(frozen), 'completed', canonical(actual), int(time.time())))
    ledger._event(db, context['current']['erp_project'], context['current']['invocation_id'], AUDIT_KIND,
                  'server-delivery-continuation', audit)
    return actual

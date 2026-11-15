"""One additional delivery unit from an exact newer signed human Task control.

This policy never resets the generic delivery marker or a submitted execution.
The historical proof is immutable; current operation authority remains separate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re

from open_webui.utils import agentic_governance as ledger
from open_webui.utils import agentic_delivery_continuation as continuation
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest

POLICY = 'developer-delivery-human-recovery.v1'
KIND = 'delivery_control_recovery'
EDGE = 'delivery_control'
ACTOR = 'server-delivery-control-policy'
CONTROL_RPC = '/api/method/abit_ai_assistant.agentic_control_fence.'
READY_KEYS = ('name', 'project', 'status', 'modified', 'custom_agov_active_run',
              'custom_agentic_managed', 'custom_agov_dispatch_released')


def need(value, message):
    continuation.need(value, message)


def audit_valid(audit, project, actor):
    return (audit['erp_project'] == project and audit['actor'] == actor
            and audit['event_hash'] == ledger._digest([
                audit[key] for key in ('event_id', 'erp_project', 'invocation_id', 'kind',
                                        'actor', 'payload', 'created_at', 'previous_hash')]))


def stamp(value):
    need(isinstance(value, str) and len(value) <= 40
         and re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?', value),
         'Delivery recovery requires exact native Task timestamps')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def successor_id(source):
    return 'agov-parent-' + digest([source['invocation_id'], EDGE, [], 'developer'])[:48]


def control_proof(db, source, receipt, event_id, published_at):
    """Reconstruct the signed historical control; never infer authority from polling."""
    inbox = continuation.one(db, 'SELECT * FROM governance_inbox WHERE event_id=?', (event_id,))
    need(inbox is not None, 'Delivery recovery requires its original signed inbox event')
    event = json.loads(inbox['payload'])
    payload = event.get('payload') or {}
    actor, sequence = payload.get('actor'), payload.get('control_sequence')
    need(event.get('schema_version') == 1 and event.get('event_id') == event_id
         and event.get('type') == 'project.control'
         and event.get('project') == source['erp_project']
         and event.get('task') == payload.get('task_id') == source['erp_task']
         and payload.get('action') in {'retry', 'resume'}
         and type(sequence) is int and sequence > 0
         and isinstance(payload.get('command_id'), str) and bool(payload['command_id'])
         and isinstance(actor, str) and 0 < len(actor) <= 254
         and actor not in {'Guest', 'signed-erp-event'}
         and not db.execute('SELECT 1 FROM governance_agent WHERE lower(erp_user)=lower(?)', (actor,)).fetchone()
         and event.get('revision') == source['task_revision']
         and inbox['erp_project'] == source['erp_project']
         and inbox['payload_hash'] == digest(event)
         and inbox['received_at'] > published_at,
         'Delivery recovery requires a newer signed human Task Retry or Resume')
    before, after = stamp(receipt['modified']), stamp(event.get('source_modified'))
    need(before.tzinfo == after.tzinfo and after > before,
         'Delivery recovery control predates the exact blocked Task publication')
    ingresses = db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='erp_event_received' "
        "AND json_extract(payload,'$.event_id')=?", (source['erp_project'], event_id)).fetchall()
    need(len(ingresses) == 1, 'Delivery recovery signed ingress is missing or ambiguous')
    ingress = dict(ingresses[0])
    need(audit_valid(ingress, source['erp_project'], 'signed-erp')
         and json.loads(ingress['payload']) == {'event_id': event_id, 'payload_hash': inbox['payload_hash']},
         'Delivery recovery event was not received through signed ERP transport')
    applications = db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='native_control_applied' "
        "AND json_extract(payload,'$.command_id')=?",
        (source['erp_project'], payload['command_id'])).fetchall()
    need(len(applications) == 1, 'Delivery recovery requires one exact native control application')
    applied = dict(applications[0])
    expected = {'task_id': source['erp_task'], 'command_id': payload['command_id'],
                'control_sequence': sequence, 'action': payload['action'],
                'state': 'enabled', 'upstream_cancel_confirmed': False}
    need(audit_valid(applied, source['erp_project'], actor)
         and json.loads(applied['payload']) == expected
         and applied['seq'] > ingress['seq'] and applied['created_at'] > published_at,
         'Delivery recovery native control audit differs or predates publication')
    return {
        'event_id': event_id, 'event_sha256': digest(event),
        'ingress_event_hash': ingress['event_hash'], 'application_event_hash': applied['event_hash'],
        'project_id': source['erp_project'], 'task_id': source['erp_task'],
        'command_id': payload['command_id'], 'control_sequence': sequence,
        'action': payload['action'], 'actor': actor, 'source_modified': event['source_modified'],
        'task_revision': event['revision'],
    }


def current_control(db, proof):
    inbox = continuation.one(db, 'SELECT state FROM governance_inbox WHERE event_id=?', (proof['event_id'],))
    row = continuation.one(db, 'SELECT * FROM governance_native_control WHERE project=? AND task=?',
                           (proof['project_id'], proof['task_id']))
    need(inbox and inbox['state'] == 'pending'
         and row == {'project': proof['project_id'], 'task': proof['task_id'],
                     'sequence': proof['control_sequence'], 'command_id': proof['command_id'],
                     'action': proof['action'], 'applied_state': 'enabled', 'phase': 'acknowledged'},
         'Delivery recovery control is old, consumed, unacknowledged or superseded')


def expected_ack(proof):
    return {'schema_version': 1,
            'event_id': 'agov-control-applied-' + digest([
                proof['project_id'], proof['task_id'], proof['command_id'], proof['control_sequence']])[:40],
            **{key: proof[key] for key in ('project_id', 'task_id', 'command_id', 'control_sequence', 'action')},
            'applied_state': 'enabled'}


async def native_control(bridge, proof):
    """GET-only confirmation of the exact acknowledged human command and vetoes."""
    for task in ('', proof['task_id']):
        response = await bridge.erp.request('GET', CONTROL_RPC + 'get_control_intent',
            params={'project_id': proof['project_id'], 'task_id': task},
            headers={'X-ABIT-Agentic-Bridge': bridge.secret})
        value = response.get('message') or {}
        need(value.get('project_id') == proof['project_id'] and value.get('task_id') == task
             and value.get('execution_veto') is False, 'Native control currently vetoes delivery recovery')
        if task:
            intent = value.get('intent') or {}
            need(all(intent.get(key) == proof[key] for key in
                     ('project_id', 'task_id', 'command_id', 'control_sequence', 'action', 'actor')),
                 'Native Task control changed before delivery recovery')
    ack = expected_ack(proof)
    response = await bridge.erp.request('GET', CONTROL_RPC + 'get_control_application_receipt',
        params={key: proof[key] for key in ('project_id', 'task_id', 'command_id', 'control_sequence')},
        headers={'X-ABIT-Agentic-Bridge': bridge.secret})
    value = response.get('message') or {}
    need(value.get('exists') is True and value.get('durable') is True
         and value.get('superseded') is not True
         and all(value.get(key) == expected for key, expected in ack.items()),
         'Native delivery recovery control acknowledgment is unavailable or changed')
    return digest({**ack, 'exists': True, 'durable': True})


def original_context(db, source):
    packet = continuation.frozen_packet(source)
    result, event, receipt = continuation.published(db, source)
    need(result['outcome'] == 'blocked' and not result.get('defects') and not result.get('action_requests'),
         'Human delivery recovery requires a published zero-issue delivery blocker')
    context = continuation.derive(source, packet, db=db, require_active=False)
    need(context is not None and context['origin']['invocation_id'] != source['invocation_id']
         and len(context['core']['lineage']) < continuation.POLICY['maximum_edges']
         and not any(edge['kind'] == EDGE for edge in context['core']['lineage']),
         'Human delivery recovery requires the original bounded delivery segment')
    markers = db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='delivery_checkpoint_resumed' "
        "AND json_extract(payload,'$.original_delivery')=?",
        (source['erp_project'], context['origin']['invocation_id'])).fetchall()
    need(len(markers) == 1 and any(edge['kind'] == 'delivery' for edge in context['core']['lineage']),
         'Human delivery recovery requires the consumed generic recovery marker')
    marker = dict(markers[0])
    return context, result, event, receipt, marker


def source_binding(source, context, result, event, receipt, marker):
    identity = {key: source.get(key) for key in continuation.IDENTITY}
    return {
        'source_invocation_id': source['invocation_id'], 'source_run_id': source['automation_run_id'],
        'source_identity_sha256': digest(identity),
        'source_lease_token_sha256': __import__('hashlib').sha256(source['lease_token'].encode()).hexdigest(),
        'source_packet_sha256': source['packet_hash'], 'source_result_sha256': digest(result),
        'source_event_sha256': digest(event), 'source_receipt_sha256': digest(receipt),
        'source_lineage_sha256': digest(context['core']),
        'original_delivery': context['origin']['invocation_id'],
        'original_input_sha256': digest(context['input']),
        'prior_delivery_marker_event_hash': marker['event_hash'],
    }


def completed_steps(db, origin):
    rows = [dict(row) for row in db.execute(
        'SELECT * FROM governance_delivery_step WHERE invocation_id=? ORDER BY step_key LIMIT 503', (origin,))]
    keys = {row['step_key'] for row in rows}
    need(2 < len(rows) <= 502 and {'tree', 'commit'} <= keys
         and all(row['state'] == 'completed' and row['request_json'] and row['result_json'] for row in rows),
         'Human delivery recovery cannot consume an unknown original remote outcome')
    return [{'step_key': row['step_key'], 'sha256': digest(row)} for row in rows]


def retained_steps(db, origin, expected):
    need(isinstance(expected, list) and 2 < len(expected) <= 502
         and len({item['step_key'] for item in expected}) == len(expected)
         and {'tree', 'commit'} <= {item['step_key'] for item in expected},
         'Human delivery recovery original journal proof is incomplete')
    for item in expected:
        row = continuation.one(db,
            'SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?',
            (origin, item['step_key']))
        need(row and row['state'] == 'completed' and digest(row) == item['sha256'],
             'Human delivery recovery original completed step changed')


def ready_packet(source, packet, fresh, task, proof, receipt, db):
    """Accept only the actual human Open save, without modifying native state."""
    need(all(task.get(key) == value for key, value in {
             'name': source['erp_task'], 'project': source['erp_project'], 'status': 'Open',
             'custom_agov_active_run': source['automation_run_id']}.items())
         and all(task.get(key) is True or type(task.get(key)) is int and task[key] == 1
                 for key in ('custom_agentic_managed', 'custom_agov_dispatch_released'))
         and str(task.get('modified')) == fresh.get('source_modified') == proof['source_modified']
         and fresh.get('task_revision') == proof['task_revision'] == source['task_revision'],
         'Human delivery recovery requires the current Open Task and original native ownership')
    need(stamp(str(task['modified'])) > stamp(receipt['modified']),
         'Human delivery recovery requires a later actual native readiness save')
    continuation.scope_equal(packet, fresh)
    from open_webui.utils.agentic_contracts.validator import validate_work_packet
    bindings = {row['role']: {'agent_id': row['agent_id'], 'erp_user': row['erp_user']}
                for row in db.execute('SELECT * FROM governance_agent WHERE erp_project=?', (source['erp_project'],))}
    need(not validate_work_packet(fresh, registered_bindings=bindings)
         and fresh.get('content_sha256') == packet_digest(fresh)
         and fresh.get('packet_id') == 'erp-packet-' + digest({
             key: value for key, value in fresh.items() if key not in {'packet_id', 'content_sha256'}})[:48],
         'Human delivery recovery requires the exact fresh registered native packet')
    old, new = packet.get('project_backlog'), fresh.get('project_backlog')
    need(isinstance(old, dict) and isinstance(new, dict),
         'Human delivery recovery requires the complete original backlog')
    normalized = deepcopy(new)
    prior = [item for item in old['tasks'] if item['task_id'] == source['erp_task']]
    current = [item for item in normalized['tasks'] if item['task_id'] == source['erp_task']]
    need(len(prior) == len(current) == 1 and current[0]['status'] == 'Open'
         and current[0]['source_modified'] == proof['source_modified'],
         'Human delivery recovery current Task backlog readback differs')
    current[0]['status'], current[0]['source_modified'] = prior[0]['status'], prior[0]['source_modified']
    normalized['snapshot_sha256'] = old['snapshot_sha256']
    need(normalized == old, 'Human delivery recovery cannot hide another backlog change')


def prepare(db, source, fresh, task, event_id, native_ack_sha256):
    context, result, event, receipt, marker = original_context(db, source)
    saved = continuation.one(db, 'SELECT updated_at FROM governance_result WHERE invocation_id=?', (source['invocation_id'],))
    proof = control_proof(db, source, receipt, event_id, saved['updated_at'])
    current_control(db, proof)
    ready_packet(source, context['packet'], fresh, task, proof, receipt, db)
    need(native_ack_sha256 == digest({**expected_ack(proof), 'exists': True, 'durable': True}),
         'Human delivery recovery requires its exact observed native control receipt')
    need(context['delivery']['state'] in {'prepared', 'pending_reconciliation'},
         'Human delivery recovery cannot replace a completed original delivery')
    _, tests = continuation.test_calls(db, context)
    return {
        'policy': POLICY, 'invocation_id': successor_id(source),
        **source_binding(source, context, result, event, receipt, marker),
        'blocked_publication_at': saved['updated_at'], 'control': proof,
        'native_control_ack_sha256': native_ack_sha256,
        'native_readiness': {key: task.get(key) for key in READY_KEYS},
        'successor_packet_sha256': digest(fresh),
        'original_steps': completed_steps(db, context['origin']['invocation_id']),
        'original_test_calls_sha256': digest(tests),
    }, context


def verify_edge(db, child, parent):
    """Revalidate historical authorization without requiring its cursor to stay latest."""
    rows = db.execute('SELECT * FROM governance_event WHERE invocation_id=? AND kind=?',
                      (child['invocation_id'], KIND)).fetchall()
    need(len(rows) == 1, 'Human delivery continuation requires one atomic authorization')
    audit = dict(rows[0])
    value = json.loads(audit['payload'])
    need(audit_valid(audit, parent['erp_project'], ACTOR)
         and value.get('policy') == POLICY
         and value.get('invocation_id') == child['invocation_id'] == successor_id(parent)
         and child['source_invocation_id'] == parent['invocation_id']
         and value.get('successor_packet_sha256') == child['packet_hash'],
         'Human delivery continuation authorization identity changed')
    context, result, event, receipt, marker = original_context(db, parent)
    binding = source_binding(parent, context, result, event, receipt, marker)
    need(all(value.get(key) == item for key, item in binding.items()),
         'Human delivery continuation source or original input changed')
    proof = control_proof(db, parent, receipt, value['control']['event_id'], value['blocked_publication_at'])
    need(value['control'] == proof
         and value['native_control_ack_sha256'] == digest({**expected_ack(proof), 'exists': True, 'durable': True}),
         'Human delivery continuation signed control proof changed')
    resolution = value.get('operator_resolution') or {}
    need(resolution.get('policy') == 'single-lf-delivery-recovery.v1'
         and resolution.get('blocked_invocation_id') == parent['invocation_id']
         and resolution.get('blocked_run_id') == parent['automation_run_id']
         and resolution.get('blocked_result_sha256') == digest(result)
         and resolution.get('original_delivery') == value['original_delivery']
         and resolution.get('original_input_sha256') == value['original_input_sha256']
         and resolution.get('control') == proof
         and all(re.fullmatch(r'[a-f0-9]{64}', str(resolution.get(key, '')))
                 for key in ('authorization_sha256', 'delivery_source_sha256', 'commit_request_sha256')),
         'Human delivery continuation operator resolution changed')
    commit = continuation.one(db, "SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key='commit'",
                              (value['original_delivery'],))
    need(commit and digest(json.loads(commit['request_json'])) == resolution['commit_request_sha256']
         and json.loads(commit['result_json']).get('sha') == resolution.get('commit_sha'),
         'Human delivery continuation original resolved commit changed')
    fresh = json.loads(child['work_packet'])
    need(digest(fresh) == child['packet_hash'], 'Human delivery continuation packet changed')
    ready_packet(parent, context['packet'], fresh, value['native_readiness'], proof, receipt, db)
    retained_steps(db, value['original_delivery'], value['original_steps'])
    _, tests = continuation.test_calls(db, context)
    need(value['original_test_calls_sha256'] == digest(tests),
         'Human delivery continuation original test receipt changed')
    uses = db.execute(
        "SELECT event_id FROM governance_event WHERE kind=? AND (json_extract(payload,'$.original_delivery')=? "
        "OR json_extract(payload,'$.control.command_id')=?)",
        (KIND, value['original_delivery'], proof['command_id'])).fetchall()
    need(len(uses) == 1 and uses[0]['event_id'] == audit['event_id'],
         'Human delivery continuation control or original was consumed more than once')
    return audit['event_hash']


# This reader recognizes one already-created legacy unit. It cannot authorize a
# transition, reserve a gateway call, import evidence, or change any ledger row.
LEGACY_POLICY = 'single-lf-delivery-recovery.v1'
LEGACY_RECEIPT = 'original-delivery-continuation.v1'
HISTORY_PURPOSE = 'preserved-delivery-history.v1'
HISTORY_ROOT = '/var/lib/abit-ai-agentic-gateway/delivery-recovery-history'
LEGACY_PRODUCERS = {
    'agentic_gateway_delivery.py': '18646bd51b98add2af5612132b2c9e2da9f77251606d1f22096edaf02dce27ff',
    'agentic_orchestrator.py': '60e6003d41adadfd8601945487bff3bf5ef5f984d4504da1825eb05a496e0c4d',
}
LEGACY_SOURCE_COMMIT = 'a60145155c277a566380c5ca02e8eb41be57c6f4'


def _history_file(name, limit=262144):
    import os
    import stat
    from open_webui.utils.agentic_tool_gateway import _open_dir
    need(re.fullmatch(r'[a-f0-9]{64}\.(?:bin|history\.json)', name),
         'Historical delivery evidence name is invalid')
    directory = _open_dir(HISTORY_ROOT)
    try:
        info = os.fstat(directory)
        need(info.st_uid == 0 and info.st_gid == 988 and stat.S_IMODE(info.st_mode) == 0o750,
             'Historical delivery evidence directory is not protected')
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_uid == 0 and info.st_gid == 988
                 and stat.S_IMODE(info.st_mode) == 0o640 and info.st_size <= limit,
                 'Historical delivery evidence object is not protected')
            raw = stream.read(limit + 1)
    finally:
        os.close(directory)
    need(len(raw) <= limit, 'Historical delivery evidence exceeds its bound')
    return raw


def _history_object(sha):
    import hashlib
    need(isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{64}', sha),
         'Historical delivery evidence digest is invalid')
    raw = _history_file(sha + '.bin')
    need(hashlib.sha256(raw).hexdigest() == sha, 'Historical delivery evidence bytes changed')
    return raw


def _legacy_objects(authorization_sha256):
    import hashlib
    need(isinstance(authorization_sha256, str) and re.fullmatch(r'[a-f0-9]{64}', authorization_sha256),
         'Historical delivery authorization digest is invalid')
    raw = _history_file(authorization_sha256 + '.history.json', 65536)
    index = json.loads(raw)
    need(isinstance(index, dict) and set(index) == {
        'schema_version', 'purpose', 'authorization_sha256', 'native_ack_sha256',
        'deployment_sha256', 'producer_sha256', 'bindings', 'retained_at', 'provenance'}
         and index['schema_version'] == 1 and index['purpose'] == HISTORY_PURPOSE
         and index['authorization_sha256'] == authorization_sha256
         and index['producer_sha256'] == LEGACY_PRODUCERS
         and isinstance(index['retained_at'], str)
         and index['provenance'] == 'Retrospective preservation of existing evidence; no new approval or transition.',
         'Historical delivery evidence index is unsupported')
    objects = {key: json.loads(_history_object(index[key])) for key in
               ('authorization_sha256', 'native_ack_sha256', 'deployment_sha256')}
    return index, objects, hashlib.sha256(raw).hexdigest()


def legacy_evidence_context(db, child):
    """Pure bounded discovery used by the reader and offline evidence preparer."""
    node, nodes, edges, publications, seen = child, [], [], [], set()
    while True:
        need(node['invocation_id'] not in seen and len(nodes) <= continuation.POLICY['maximum_edges'],
             'Historical delivery lineage is cyclic or exceeds its bound')
        seen.add(node['invocation_id'])
        nodes.append(node)
        packet = continuation.frozen_packet(node)
        own = continuation.one(db, 'SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                               (node['invocation_id'],))
        if own:
            frozen = json.loads(own['input_json'])
            need(node['invocation_id'] != child['invocation_id'] and continuation.KEY not in frozen
                 and own['state'] == 'completed' and own['project_id'] == child['erp_project']
                 and own['task_id'] == child['erp_task'],
                 'Historical delivery requires its completed independent original')
            return {'current': child, 'packet': continuation.frozen_packet(child),
                    'origin': node, 'origin_packet': packet, 'delivery': own, 'input': frozen,
                    'nodes': nodes, 'publications': publications, 'edges': edges}
        parent = continuation.one(db, 'SELECT * FROM governance_invocation WHERE invocation_id=?',
                                  (node.get('source_invocation_id'),))
        need(parent and parent['state'] == 'handed_off'
             and all(parent[k] == child[k] for k in ('erp_project', 'erp_task', 'target_role', 'agent_id')),
             'Historical delivery crosses an unfinished or foreign predecessor')
        edge, publication = continuation._edge(db, node, parent)
        need(edge['kind'] in {'continue', 'delivery'},
             'Historical delivery cannot cross a control, repair or action boundary')
        continuation.scope_equal(continuation.frozen_packet(parent), packet)
        edges.append(edge)
        publications.append(publication)
        node = parent


def _legacy_event(db, invocation, kind, actor):
    rows = db.execute('SELECT * FROM governance_event WHERE invocation_id=? AND kind=?',
                      (invocation, kind)).fetchall()
    need(len(rows) == 1, 'Historical delivery audit is missing or ambiguous')
    audit = dict(rows[0])
    need(audit_valid(audit, audit['erp_project'], actor), 'Historical delivery audit hash or actor changed')
    return audit


def _legacy_control(db, parent, authorization, operator, native):
    command = authorization['control']
    need(isinstance(command, dict) and set(command) == {'actor', 'command_id', 'sequence'},
         'Historical control identity is unsupported')
    rows = db.execute(
        "SELECT * FROM governance_inbox WHERE erp_project=? AND json_extract(payload,'$.payload.command_id')=?",
        (parent['erp_project'], command.get('command_id'))).fetchall()
    need(len(rows) == 1, 'Historical delivery signed command is missing or ambiguous')
    inbox = dict(rows[0])
    event = json.loads(inbox['payload'])
    payload = event.get('payload') or {}
    task = operator['control_task']
    actor = command.get('actor')
    need(event.get('schema_version') == 1 and event.get('event_id') == inbox['event_id']
         and event.get('type') == 'project.control'
         and event.get('project') == parent['erp_project'] and event.get('task') == task
         and task in {'', parent['erp_task']} and payload.get('task_id') == task
         and payload.get('project_id', parent['erp_project']) == parent['erp_project']
         and payload.get('command_id') == command.get('command_id')
         and payload.get('control_sequence') == command.get('sequence')
         and type(command.get('sequence')) is int and type(operator.get('minimum_control_sequence')) is int
         and operator['minimum_control_sequence'] >= 10
         and command['sequence'] > operator['minimum_control_sequence']
         and payload.get('action') == 'retry' and payload.get('actor') == actor
         and isinstance(actor, str) and actor not in {'', 'Guest', 'signed-erp-event'}
         and not db.execute('SELECT 1 FROM governance_agent WHERE lower(erp_user)=lower(?)', (actor,)).fetchone()
         and inbox['payload_hash'] == digest(event),
         'Historical delivery signed Project/Task sequence-floor authority changed')
    ingress_rows = db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='erp_event_received' "
        "AND json_extract(payload,'$.event_id')=?", (parent['erp_project'], event['event_id'])).fetchall()
    apply_rows = db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='native_control_applied' "
        "AND json_extract(payload,'$.command_id')=?", (parent['erp_project'], command['command_id'])).fetchall()
    need(len(ingress_rows) == len(apply_rows) == 1, 'Historical signed/native control proof is ambiguous')
    ingress, applied = dict(ingress_rows[0]), dict(apply_rows[0])
    need(audit_valid(ingress, parent['erp_project'], 'signed-erp')
         and json.loads(ingress['payload']) == {'event_id': event['event_id'], 'payload_hash': inbox['payload_hash']}
         and audit_valid(applied, parent['erp_project'], actor)
         and json.loads(applied['payload']) == {
             'task_id': task, 'command_id': command['command_id'], 'control_sequence': command['sequence'],
             'action': 'retry', 'state': 'enabled', 'upstream_cancel_confirmed': False}
         and applied['seq'] > ingress['seq'],
         'Historical signed/native control audit changed')
    expected = {'schema_version': 1, 'event_id': 'agov-control-applied-' + digest([
        parent['erp_project'], task, command['command_id'], command['sequence']])[:40],
        'project_id': parent['erp_project'], 'task_id': task, 'command_id': command['command_id'],
        'control_sequence': command['sequence'], 'action': 'retry', 'applied_state': 'enabled'}
    need(native == {'message': {**expected, 'exists': True, 'durable': True}},
         'Historical native control acknowledgment changed')
    saved = continuation.one(db, 'SELECT updated_at FROM governance_result WHERE invocation_id=?',
                             (parent['invocation_id'],))
    need(saved and inbox['received_at'] > saved['updated_at']
         and applied['created_at'] > saved['updated_at'],
         'Historical native control was not applied after the blocked publication')
    return {'event_id': event['event_id'], 'inbox_sha256': inbox['payload_hash'],
            'ingress_event_hash': ingress['event_hash'], 'application_event_hash': applied['event_hash'],
            'scope': task, 'sequence': command['sequence'], 'command_id': command['command_id'],
            'actor_sha256': __import__('hashlib').sha256(actor.encode()).hexdigest(),
            'received_at': inbox['received_at'],
            'applied_at': applied['created_at'], 'applied_seq': applied['seq']}


def _legacy_call(db, child, packet, actual, calls, bound_call=None):
    query = ("SELECT c.* FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id "
             "WHERE s.invocation_id=? AND c.operation='repository.deliver' AND c.state='completed'")
    args = [child['invocation_id']]
    if bound_call is not None:
        need(isinstance(bound_call, dict) and set(bound_call) == {
                 'session_id', 'call_id', 'call_sha256', 'session_sha256', 'gateway_audits'}
             and all(re.fullmatch(r'[A-Za-z0-9_.:@+-]{8,160}', str(bound_call.get(key, '')))
                     for key in ('session_id', 'call_id')),
             'Historical original call binding is invalid')
        query += ' AND c.session_id=? AND c.call_id=?'
        args.extend((bound_call['session_id'], bound_call['call_id']))
    rows = db.execute(query + ' LIMIT 2', args).fetchall()
    need(len(rows) == 1, 'Historical completed delivery call is missing or ambiguous')
    call = dict(rows[0])
    session = continuation.one(db, 'SELECT * FROM governance_gateway_session WHERE session_id=?', (call['session_id'],))
    scope = json.loads(session['scope_json'])
    admission = json.loads(session['admission_json'])
    expected = {'project_id': child['erp_project'], 'task_id': child['erp_task'], 'role': 'developer',
                'agent_id': child['agent_id'], 'invocation_id': child['invocation_id'],
                'run_id': child['automation_run_id'], 'work_packet_digest': packet_digest(packet),
                'repository': packet['repository'], 'environment': packet['environment'],
                'erp_user': packet['binding']['erp_user'], 'tool_scopes': sorted(packet['tool_scopes'])}
    need(session['invocation_id'] == child['invocation_id']
         and all(scope.get(key) == value for key, value in expected.items())
         and admission.get('scope') == scope
         and all(re.fullmatch(r'[a-f0-9]{64}', str(session.get(key, '')))
                 for key in ('connection_hash', 'client_session_hash'))
         and call['request_hash'] == digest({'operation': 'repository.deliver', 'arguments': {'test_call_ids': calls}})
         and json.loads(call['result_json']) == actual,
         'Historical delivery session, call arguments or response changed')
    expected_audits = {
        'intent_fenced': {'call_id': call['call_id'], 'operation': 'repository.deliver', 'request_hash': call['request_hash']},
        'completed': {'call_id': call['call_id'], 'result_sha256': digest(actual)}}
    audits = {}
    for kind, payload in expected_audits.items():
        values = db.execute(
            "SELECT * FROM governance_gateway_audit WHERE session_id=? AND kind=? AND json_extract(body_json,'$.call_id')=?",
            (call['session_id'], kind, call['call_id'])).fetchall()
        need(len(values) == 1 and json.loads(values[0]['body_json']) == payload,
             'Historical gateway intent or completion audit changed')
        audits[kind] = digest(dict(values[0]))
    identity = {key: session[key] for key in
                ('session_id', 'invocation_id', 'scope_json', 'admission_json', 'connection_hash', 'client_session_hash')}
    proof = {'session_id': call['session_id'], 'call_id': call['call_id'],
             'session_sha256': digest(identity), 'call_sha256': digest(call), 'gateway_audits': audits}
    need(bound_call is None or proof == bound_call, 'Historical original completed call changed')
    return proof


def legacy_facts(db, context, operator, native, deployment, *, bound_call=None):
    """Reconstruct only an existing legacy edge and its completed receipt."""
    child, origin = context['current'], context['origin']
    parent = context['nodes'][1]
    result, event, receipt = continuation.published(db, parent)
    need(result['outcome'] == 'blocked' and not result.get('defects') and not result.get('action_requests'),
         'Historical recovery parent is not the original zero-issue blocker')
    markers = [dict(row) for row in db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='delivery_checkpoint_resumed' "
        "AND json_extract(payload,'$.original_delivery')=? ORDER BY seq",
        (child['erp_project'], origin['invocation_id']))]
    need(len(markers) == 2 and markers[0]['invocation_id'] == parent['invocation_id']
         and markers[1]['invocation_id'] == child['invocation_id'],
         'Historical delivery requires exactly its generic and authorized markers')
    delivery_edges = [edge for edge in context['edges'] if edge['kind'] == 'delivery']
    need(len(delivery_edges) == 2 and delivery_edges[0]['child_invocation_id'] == child['invocation_id']
         and delivery_edges[1]['child_invocation_id'] == parent['invocation_id'],
         'Historical delivery cannot admit another exceptional edge')
    for marker, edge in zip(markers, reversed(delivery_edges)):
        need(audit_valid(marker, child['erp_project'], 'governance-lifecycle-coordinator')
             and json.loads(marker['payload']) == {
                 'blocked_source': edge['parent_invocation_id'], 'original_delivery': origin['invocation_id'],
                 'input_sha256': digest(context['input'])},
             'Historical delivery marker or original input changed')
    lifecycle = _legacy_event(db, child['invocation_id'], 'parent_acceptance_resumed', 'governance-lifecycle-coordinator')
    authorization = _legacy_event(db, child['invocation_id'], 'delivery_checkpoint_authorized', 'governance-lifecycle-coordinator')
    binding = _legacy_event(db, child['invocation_id'], 'delivery_continuation', 'delivery-coordinator')
    resolution = json.loads(authorization['payload'])
    need(all(item['erp_project'] == child['erp_project'] for item in (lifecycle, authorization, binding))
         and isinstance(resolution, dict) and set(resolution) == {
             'policy', 'authorization_sha256', 'blocked_invocation_id', 'blocked_run_id',
             'blocked_result_sha256', 'original_delivery', 'original_input_sha256', 'commit_sha',
             'commit_request_sha256', 'delivery_source_sha256', 'control'},
         'Historical authorization scope or fields changed')
    need(markers[1]['previous_hash'] == lifecycle['event_hash']
         and authorization['previous_hash'] == markers[1]['event_hash']
         and lifecycle['created_at'] == markers[1]['created_at'] == authorization['created_at']
         and resolution.get('policy') == LEGACY_POLICY
         and resolution.get('blocked_invocation_id') == parent['invocation_id']
         and resolution.get('blocked_run_id') == parent['automation_run_id']
         and resolution.get('blocked_result_sha256') == digest(result)
         and resolution.get('original_delivery') == origin['invocation_id']
         and resolution.get('original_input_sha256') == digest(context['input'])
         and resolution.get('delivery_source_sha256') == LEGACY_PRODUCERS['agentic_gateway_delivery.py'],
         'Historical delivery authorization or its producer changed')
    expected_operator = {
        'schema_version': 1, 'purpose': LEGACY_POLICY, 'blocked_invocation_id': parent['invocation_id'],
        'blocked_run_id': parent['automation_run_id'], 'blocked_result_sha256': digest(result),
        'native_block_receipt_sha256': digest(receipt), 'original_invocation_id': origin['invocation_id'],
        'original_input_sha256': digest(context['input']), 'delivery_source_sha256': resolution['delivery_source_sha256'],
        'commit_sha': resolution.get('commit_sha'), 'commit_request_sha256': resolution.get('commit_request_sha256')}
    need(all(operator.get(key) == value for key, value in expected_operator.items()),
         'Preserved operator bytes belong to different historical work')
    control = _legacy_control(db, parent, resolution, operator, native)
    need(control['applied_seq'] < lifecycle['seq'] and control['applied_at'] <= lifecycle['created_at'],
         'Historical recovery predates its acknowledged command')
    need(set(deployment) == {'schema_version', 'purpose', 'source_commit', 'producer_sha256',
                            'records', 'observed_at', 'provenance'}
         and deployment['schema_version'] == 1 and deployment['purpose'] == 'preserved-legacy-deployment.v1'
         and deployment['source_commit'] == LEGACY_SOURCE_COMMIT
         and deployment['producer_sha256'] == LEGACY_PRODUCERS
         and set(deployment['records']) == {'complete71-after.json', 'completed-receipt.json', 'intent.json'}
         and deployment['provenance'] == 'Read-only projection of retained pre-execution deployment records.',
         'Historical deployment evidence is unsupported')
    for value in deployment['records'].values():
        need(set(value) == {'sha256', 'mtime_ns'} and re.fullmatch(r'[a-f0-9]{64}', value['sha256'])
             and type(value['mtime_ns']) is int and 0 < value['mtime_ns'] < control['received_at'] * 1_000_000_000,
             'Historical producer was not retained before the command')
    # The native start is historical; lease refresh or later state changes do
    # not change its original request/receipt or turn it into current authority.
    start = continuation.one(db, 'SELECT * FROM governance_start WHERE invocation_id=?', (child['invocation_id'],))
    need(start and start['run_id'] == child['automation_run_id'] and start['packet_hash'] == child['packet_hash'],
         'Historical delivery original native start is missing')
    from open_webui.utils.agentic_start_recovery import _identity, _receipt
    _identity(child, json.loads(start['event_json']))
    need(start['event_sha256'] == digest(json.loads(start['event_json'])), 'Historical native start changed')
    _receipt(start, json.loads(start['receipt_json'] or 'null'))
    steps = [dict(row) for row in db.execute(
        'SELECT * FROM governance_delivery_step WHERE invocation_id=? ORDER BY step_key', (origin['invocation_id'],))]
    keys = [row['step_key'] for row in steps]
    prefix = [key for key in keys if key not in {'reference', 'pull_request'}]
    need(len(prefix) == 4 and prefix == operator.get('completed_steps')
         and sum(key.startswith('blob:') for key in prefix) == 2 and {'commit', 'tree'} <= set(prefix)
         and set(keys) == set(prefix) | {'reference', 'pull_request'}
         and all(row['state'] == 'completed' for row in steps),
         'Historical original completed step prefix or publication changed')
    commit = next(row for row in steps if row['step_key'] == 'commit')
    request, response = json.loads(commit['request_json']), json.loads(commit['result_json'])
    from open_webui.utils.agentic_gateway_delivery import _commit_hash
    body = request['body']
    need(request['path'] == '/git/commits' and digest(request) == resolution['commit_request_sha256']
         and _commit_hash(body) == response.get('sha') == resolution['commit_sha']
         and body['message'].endswith('\n') and not body['message'].endswith('\n\n')
         and response.get('message') == body['message'][:-1]
         and response.get('tree', {}).get('sha') == body['tree']
         and [value.get('sha') for value in response.get('parents', [])] == body['parents']
         and re.fullmatch(r'[a-f0-9]{64}', str(operator.get('commit_GET_proof_sha256', ''))),
         'Historical original commit or single-LF proof changed')
    packet = context['packet']
    own = [value for value in (packet.get('project_backlog') or {}).get('tasks', [])
           if value.get('task_id') == child['erp_task']]
    need(len(own) == 1 and own[0].get('status') == 'Open'
         and own[0].get('source_modified') == packet.get('source_modified')
         and stamp(packet['source_modified']) > stamp(receipt['modified']),
         'Historical successor lacks its original Open packet')
    actual = json.loads(context['delivery']['result_json'])
    need(actual.get('state') == 'ready_for_review' and actual.get('invocation_id') == origin['invocation_id']
         and actual.get('repository_snapshot_sha256') == context['input']['snapshot_sha256'],
         'Historical completed original result changed')
    chain = [{'invocation_id': row['invocation_id'], 'packet_sha256': packet_digest(continuation.frozen_packet(row)),
              'result_sha256': digest(value), 'outcome': value['outcome']}
             for row, value, _ in context['publications']]
    expected_binding = {'policy': LEGACY_RECEIPT, 'invocation_id': child['invocation_id'],
        'packet_sha256': packet_digest(packet), 'original_invocation_id': origin['invocation_id'],
        'original_packet_sha256': packet_digest(context['origin_packet']),
        'original_input_sha256': digest(context['input']), 'original_result_sha256': digest(actual),
        'snapshot_sha256': context['input']['snapshot_sha256'], 'checkpoints': chain}
    need(json.loads(binding['payload']) == expected_binding and binding['seq'] > authorization['seq'],
         'Historical completed delivery binding changed')
    calls, tests = continuation.test_calls(db, context)
    continued = {**actual, 'invocation_id': child['invocation_id'], 'original_invocation_id': origin['invocation_id']}
    call = _legacy_call(db, child, packet, continued, calls, bound_call)
    facts = {
        'child_invocation_id': child['invocation_id'], 'parent_invocation_id': parent['invocation_id'],
        'original_invocation_id': origin['invocation_id'], 'child_packet_sha256': child['packet_hash'],
        'parent_packet_sha256': parent['packet_hash'], 'original_input_sha256': digest(context['input']),
        'original_result_sha256': digest(actual), 'source_result_sha256': digest(result),
        'source_receipt_sha256': digest(receipt), 'generic_marker_event_hash': markers[0]['event_hash'],
        'legacy_marker_event_hash': markers[1]['event_hash'], 'lifecycle_event_hash': lifecycle['event_hash'],
        'authorization_event_hash': authorization['event_hash'], 'continuation_event_hash': binding['event_hash'],
        'original_steps': [{'step_key': row['step_key'], 'sha256': digest(row)} for row in steps],
        'original_test_calls_sha256': digest(tests), 'native_start_sha256': digest({
            key: value for key, value in start.items() if key != 'updated_at'}),
        'control': control, 'completed_gateway': call}
    return facts, continued


def legacy_marker_proof(db, context):
    """Recognize only a preserved existing legacy edge; never create one."""
    origin = context['origin']['invocation_id']
    rows = db.execute(
        "SELECT * FROM governance_event WHERE erp_project=? AND kind='delivery_checkpoint_authorized' "
        "AND json_extract(payload,'$.original_delivery')=?", (context['current']['erp_project'], origin)).fetchall()
    need(len(rows) == 1, 'Historical original delivery authorization is missing or ambiguous')
    authorization = dict(rows[0])
    child = continuation.one(db, 'SELECT * FROM governance_invocation WHERE invocation_id=?',
                             (authorization['invocation_id'],))
    need(child and child['invocation_id'] in {node['invocation_id'] for node in context['nodes']},
         'Historical authorization is outside this exact existing lineage')
    original = legacy_evidence_context(db, child)
    need(original['origin']['invocation_id'] == origin, 'Historical original delivery changed')
    resolution = json.loads(authorization['payload'])
    index, objects, index_sha = _legacy_objects(resolution.get('authorization_sha256'))
    facts, actual = legacy_facts(db, original, objects['authorization_sha256'],
                                objects['native_ack_sha256'], objects['deployment_sha256'],
                                bound_call=index['bindings'].get('completed_gateway'))
    need(index['bindings'] == facts, 'Preserved historical delivery evidence differs from its original rows')
    return {'policy': LEGACY_RECEIPT, 'invocation_id': child['invocation_id'],
            'authorization_event_hash': authorization['event_hash'], 'history_sha256': index_sha,
            'bindings_sha256': digest(facts)}, actual


def historical_receipt(db, context):
    """A direct read of the old audit+completed call; no canonical row is made."""
    historical = context.get('historical')
    if not historical or historical['invocation_id'] != context['current']['invocation_id']:
        return None
    proof, actual = legacy_marker_proof(db, context)
    need(proof == historical, 'Historical delivery binding changed')
    return actual

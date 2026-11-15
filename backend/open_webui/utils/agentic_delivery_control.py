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

"""Read-only preservation contract for this two-file capacity release.

This recognizes an existing submitted, uncertain original. It neither settles
its dead-owner admission nor supplies authority to read, publish or retry it.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re

INVOCATION = 'agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35'
RUN = 'adf93fbf-9737-5eb4-9f5d-21e02dc63014'
PROJECT, TASK = 'PROJ-0198', 'TASK-2026-06200'
ADMISSION = '2b8b4caec4908a78832c9c2123dd08b718fab92d5742f210'
ORIGINAL_OWNER = (1362365, '68780389')
GATE = {'state': 'draining', 'epoch': 24, 'active_admissions': 1}
PACKET_SHA = 'efb84b465348836bebc9cac33a9366cd0fd4dc52875ea73f7a1e72aed8df5fc5'
SELECTION_HASH = 'bdce5c2cff7f19cb8b8050850eed1e48595432f359e098537608617ffb4e1337'
CAPTURE_SHA = '5b0af71eddfd8b470f8d791576512c6d3e286de065c76522b751e120ae9605e9'
CORRECTED_SHA = 'b7c29aa3e2d7fc11a504b508770b272eea784a0a7a0d46a7dd43b73f24ef38ad'
CONTROL = 'erp-command-3bf7c491-f438-47d4-9aa2-c053021a913a'
QA_REFERENCE = 'TASK-2026-05624:5e944318-a000-4f44-8650-31ea6ea55847'
QA_RECEIPT_SHA = '123c6e8d2ae56d2b10431f23d0f21daea94d85454c3872fe981f56d728a2ba37'
TABLES = ('governance_invocation', 'governance_task_lease', 'governance_start',
          'governance_local_result', 'governance_result', 'governance_action',
          'governance_developer_delivery', 'governance_delivery_step',
          'governance_gateway_session', 'governance_gateway_call',
          'governance_native_control', 'governance_project_control', 'governance_task_control')


def need(value, code):
    if not value:
        raise RuntimeError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def rows(db, name):
    need(name in {*TABLES, 'governance_event', 'dot_primary_runs', 'review',
                  'gate', 'lease', 'audit'}, 'FIXED_PRESERVATION_TABLE_REQUIRED')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
        return None
    result = [dict(row) for row in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid LIMIT 10001')]
    need(len(result) <= 10000 and len(canonical(result)) <= 20_971_520,
         'PRESERVATION_HISTORY_BOUND_EXCEEDED')
    return result


def original_owner_gone():
    try:
        current = (Path('/proc') / str(ORIGINAL_OWNER[0]) / 'stat').read_text()
    except FileNotFoundError:
        return True
    return current.split(') ', 1)[1].split()[19] != ORIGINAL_OWNER[1]


def validate_facts(facts, *, owner_gone):
    original = facts['original']
    need(original == {'invocation_id': INVOCATION, 'run_id': RUN, 'project': PROJECT,
         'task': TASK, 'role': 'developer', 'state': 'uncertain', 'dispatch_attempts': 1,
         'submitted_at': 1794734924, 'lease_fence': 5, 'packet_hash': PACKET_SHA},
         'EXACT_SUBMITTED_ORIGINAL_REQUIRED')
    need(owner_gone is True, 'ORIGINAL_ADMISSION_OWNER_STILL_LIVE')
    need(facts['gate'] == GATE, 'EXACT_DRAINING24_ONE_ORIGINAL_REQUIRED')
    admission = facts['active_admission']
    need(admission['id'] == ADMISSION and admission['kind'] == 'governed'
         and admission['reference'] == INVOCATION and admission['epoch'] == 21
         and admission['pid'] == ORIGINAL_OWNER[0] and admission['process_start'] == ORIGINAL_OWNER[1]
         and admission['state'] in {'active', 'unresolved'}, 'EXACT_OLD_ADMISSION_REQUIRED')
    need(facts['task_lease'] == {'erp_project': PROJECT, 'erp_task': TASK,
         'invocation_id': INVOCATION, 'fence': 5}, 'ORIGINAL_TASK_FENCE_CHANGED')
    need(facts['original_start_valid'] is True and facts['packet_hash_valid'] is True,
         'ORIGINAL_START_OR_PACKET_CHANGED')
    need(facts['result_or_action_count'] == 0 and facts['gateway_calls'] ==
         [['context.read', 'completed'], ['repository.deliver', 'completed'],
          ['repository.delivery_status', 'completed']], 'ORIGINAL_RESULTS_OR_CALLS_CHANGED')
    need(facts['provider_unresolved'] is True and facts['automation_success_original'] is True,
         'ORIGINAL_PROVIDER_OR_AUTOMATION_CHANGED')
    need(facts['project_enabled'] is True and facts['task_enabled'] is True
         and facts['native_control'] == {'project': PROJECT, 'task': '', 'sequence': 14,
             'command_id': CONTROL, 'action': 'retry', 'applied_state': 'enabled', 'phase': 'acknowledged'},
         'EXACT_RETAINED_CONTROL_CHANGED')
    need(facts['selection'] == {'count': 1, 'seq': 150, 'event_hash': SELECTION_HASH,
         'hash_valid': True, 'previous_link_valid': True, 'policy': 'returned-reply-format.v1',
         'protected_file_sha256': CAPTURE_SHA, 'corrected_reply_sha256': CORRECTED_SHA,
         'control_sequence': 14, 'control_command': CONTROL}, 'EXISTING_V1_SELECTION_CHANGED')
    need(facts['qa_non_done'] == 0 and facts['qa_terminal_receipt_sha256'] == QA_RECEIPT_SHA,
         'QA_IS_NOT_THE_EXACT_IDLE_HISTORICAL_STATE')
    return True


def capture(hold):
    """Each database uses an explicit query-only read transaction via service euid."""
    with closing(hold.db('agentic-governance.sqlite')) as db:
        db.execute('BEGIN')
        table_values = {name: rows(db, name) for name in TABLES}
        current = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',
                             (INVOCATION,)).fetchone()
        need(current is not None, 'ORIGINAL_INVOCATION_MISSING')
        current = dict(current)
        packet = json.loads(current['work_packet'])
        start = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (INVOCATION,)).fetchone()
        need(start is not None and start['receipt_json'], 'ORIGINAL_NATIVE_START_MISSING')
        event, receipt = json.loads(start['event_json']), json.loads(start['receipt_json'])
        start_valid = (start['run_id'] == RUN and start['packet_hash'] == current['packet_hash']
            and start['lease_fence'] == current['lease_fence']
            and start['lease_token_sha256'] == hashlib.sha256(current['lease_token'].encode()).hexdigest()
            and hashlib.sha256(start['event_json'].encode()).hexdigest() == start['event_sha256']
            and event['invocation_id'] == INVOCATION and event['run_id'] == RUN
            and event['type'] == 'run.started' and event['work_packet_digest'] == packet['content_sha256']
            and receipt['durable'] is True and receipt['status'] == 'Working'
            and receipt['run_id'] == RUN and receipt['event_id'] == event['event_id'] and receipt['task_id'] == TASK)
        results = sum(sum(row['invocation_id'] == INVOCATION for row in table_values[name] or [])
                      for name in ('governance_local_result', 'governance_result', 'governance_action'))
        calls = db.execute('SELECT c.operation,c.state FROM governance_gateway_call c '
            'JOIN governance_gateway_session s ON s.session_id=c.session_id '
            'WHERE s.invocation_id=? ORDER BY c.operation', (INVOCATION,)).fetchall()
        lease = db.execute('SELECT * FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
                           (PROJECT, TASK)).fetchone()
        project = db.execute('SELECT state FROM governance_project_control WHERE erp_project=?', (PROJECT,)).fetchone()
        task = db.execute('SELECT state FROM governance_task_control WHERE project=? AND task=?', (PROJECT, TASK)).fetchone()
        control = db.execute("SELECT * FROM governance_native_control WHERE project=? AND task=''", (PROJECT,)).fetchone()
        selected = db.execute("SELECT * FROM governance_event WHERE invocation_id=? AND kind='returned_reply_format_selected' ORDER BY seq LIMIT 2", (INVOCATION,)).fetchall()
        need(len(selected) == 1, 'EXACT_ONE_EXISTING_SELECTION_REQUIRED')
        selected = selected[0]
        previous = db.execute('SELECT event_hash FROM governance_event WHERE erp_project=? AND seq<? ORDER BY seq DESC LIMIT 1', (PROJECT, selected['seq'])).fetchone()
        value = json.loads(selected['payload'])
        selection = {'count': 1, 'seq': selected['seq'], 'event_hash': selected['event_hash'],
            'hash_valid': selected['event_hash'] == digest([selected[k] for k in
                ('event_id', 'erp_project', 'invocation_id', 'kind', 'actor', 'payload', 'created_at', 'previous_hash')]),
            'previous_link_valid': selected['previous_hash'] == (previous['event_hash'] if previous else ''),
            **{key: value.get(key) for key in ('policy', 'protected_file_sha256',
                'corrected_reply_sha256', 'control_sequence', 'control_command')}}
        audit = rows(db, 'governance_event')
    with closing(hold.db('abitdot-primary.sqlite')) as db:
        db.execute('BEGIN')
        provider = rows(db, 'dot_primary_runs')
        original = [row for row in provider if row['original_run_id'] == RUN]
        unresolved = (len(original) == 1 and original[0]['status'] in {'unknown', 'unavailable', 'dispatching'}
                      and not original[0]['response_text'] and not original[0]['receipt'])
    with closing(hold.db('webui.db')) as db:
        db.execute('BEGIN')
        automation = db.execute('SELECT * FROM automation_run WHERE id=?', (RUN,)).fetchone()
        need(automation is not None, 'ORIGINAL_AUTOMATION_RUN_MISSING')
        automation = dict(automation)
    with closing(hold.db('erp-pending-review-qa.sqlite')) as db:
        db.execute('BEGIN')
        qa = rows(db, 'review')
        terminal = [dict(row) for row in db.execute('SELECT task,run,binding,phase,status FROM review WHERE task=? AND run=?', tuple(QA_REFERENCE.split(':', 1)))]
        qa_receipt = hashlib.sha256(json.dumps(terminal, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
        need(len(terminal) == 1 and terminal[0]['phase'] == 'done' and terminal[0]['status'] == 'blocked',
             'EXACT_COMPLETED_QA_RECEIPT_REQUIRED')
    with closing(hold.db('maintenance-admission/gate.sqlite')) as db:
        db.execute('BEGIN')
        gates, leases, maintenance_audit = (rows(db, name) for name in ('gate', 'lease', 'audit'))
        need(len(gates) == 1, 'EXACT_ONE_GATE_REQUIRED')
        active = [row for row in leases if row['state'] in {'active', 'unresolved'}]
        need(len(active) == 1, 'EXACT_ONE_UNCHANGED_ORIGINAL_ADMISSION_REQUIRED')
    facts = {'original': {'invocation_id': current['invocation_id'], 'run_id': current['automation_run_id'],
        'project': current['erp_project'], 'task': current['erp_task'], 'role': current['target_role'],
        **{key: current[key] for key in ('state', 'dispatch_attempts', 'submitted_at', 'lease_fence', 'packet_hash')}},
        'gate': {'state': gates[0]['state'], 'epoch': gates[0]['epoch'], 'active_admissions': len(active)},
        'active_admission': active[0], 'task_lease': dict(lease) if lease else {},
        'original_start_valid': bool(start_valid), 'packet_hash_valid': digest(packet) == current['packet_hash'],
        'result_or_action_count': results, 'gateway_calls': [list(row) for row in calls],
        'provider_unresolved': bool(unresolved),
        'automation_success_original': (automation['automation_id'] == current['agent_id']
            and automation['status'] == 'success' and automation['chat_id'] == '6a7b943a-4ebd-5684-a2d8-d612350a9b71'),
        'project_enabled': bool(project and project['state'] == 'enabled'),
        'task_enabled': task is None or task['state'] == 'enabled',
        'native_control': dict(control) if control else {}, 'selection': selection,
        'qa_non_done': sum(row['phase'] != 'done' for row in qa), 'qa_terminal_receipt_sha256': qa_receipt}
    validate_facts(facts, owner_gone=original_owner_gone())
    return {'facts': facts, 'table_hashes': {name: digest(value) for name, value in table_values.items()},
        'audit': audit, 'provider_sha256': digest(provider), 'qa_sha256': digest(qa),
        'automation_sha256': digest(automation), 'gate': gates, 'leases': leases,
        'maintenance_audit': maintenance_audit}


def preserve(before, after, *, allowed_qa_owners=(), old_qa_owner=None, old_qa_stop_ns=None):
    """Only the already verified terminal QA receipt may add finished bookkeeping."""
    for key in ('facts', 'table_hashes', 'audit', 'provider_sha256', 'qa_sha256', 'automation_sha256', 'gate'):
        need(before[key] == after[key], 'ORIGINAL_BUSINESS_OR_GATE_HISTORY_CHANGED')
    old = {row['id']: row for row in before['leases']}
    new = {row['id']: row for row in after['leases']}
    need(set(old) <= set(new) and all(new[key] == value for key, value in old.items()),
         'ORIGINAL_ADMISSION_CHANGED_OR_REMOVED')
    prior = before['maintenance_audit']
    need(after['maintenance_audit'][:len(prior)] == prior, 'ORIGINAL_MAINTENANCE_AUDIT_CHANGED')
    extra = [row for key, row in new.items() if key not in old]
    need(len(extra) <= 64, 'TERMINAL_QA_BOOKKEEPING_BOUND')
    new_audits = after['maintenance_audit'][len(prior):]
    need(len(new_audits) == len(extra) * 2, 'UNRELATED_MAINTENANCE_AUDIT_ADDED')
    for row in extra:
        owner = (row['pid'], row['process_start'])
        need(row['kind'] == 'qa' and row['reference'] == QA_REFERENCE and row['state'] == 'finished'
             and row['epoch'] == 24 and row['parent_id'] is None and row['receipt_sha256'] == QA_RECEIPT_SHA
             and owner in set(allowed_qa_owners), 'ONLY_KNOWN_FINISHED_QA_BOOKKEEPING_PERMITTED')
        if owner == old_qa_owner:
            need(old_qa_stop_ns is not None and row['created_ns'] <= old_qa_stop_ns,
                 'OLD_QA_BOOKKEEPING_AFTER_STOP')
        events = [event for event in new_audits if json.loads(event['payload']).get('lease') == row['id']]
        need(len(events) == 2 and [e['kind'] for e in events] ==
             ['admitted_original_recovery', 'original_receipt_settled'], 'EXACT_QA_RECEIPT_AUDIT_PAIR_REQUIRED')
        need(json.loads(events[0]['payload']) == {'lease': row['id'], 'kind': 'qa', 'reference': QA_REFERENCE,
             'epoch': 24, 'parent': None, 'original_receipt': QA_RECEIPT_SHA}
             and json.loads(events[1]['payload']) == {'lease': row['id'], 'kind': 'qa',
                 'reference': QA_REFERENCE, 'receipt_sha256': QA_RECEIPT_SHA},
             'QA_BOOKKEEPING_RECEIPT_CHANGED')
    return len(extra)

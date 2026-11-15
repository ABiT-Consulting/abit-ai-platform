"""Query-only, immutable-history contract for the one-leaf PM scope repair.

This is not a provider settlement policy. The raw f33 unknown row remains unknown;
the independently published QA and unsubmitted PM must remain unchanged.
"""
from contextlib import closing
import hashlib
import json
import re

PROJECT, TASK = 'PROJ-0198', 'TASK-2026-06200'
F33 = 'agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35'
F33_RUN = 'adf93fbf-9737-5eb4-9f5d-21e02dc63014'
QA = 'agov-parent-bd0c8d3ad7437b0619f6c73ee16bd95e1a7e6df747d1fea8'
QA_RUN = '3df6d316-75cb-563b-b972-73fe4cdea33a'
PM = 'agov-pm-review-9ac3cfd3cc1969734e889cec5e0e4c978dd19ab86738fa5c'
PM_PACKET = '1fd522a6dd7dbd489f8fe2ef6c4e7b92e0ab6419af553acc23e256ee6efb3f89'
QA_RESULT = '1050b2ef3a6544795ad5ab160c447b2cb40269231e96f43582622f08677481fa'
QA_ACK = '3e40cd808a0366f68b5b1b79c86ca95569938bf672278c70df5fd8a4d7b09b6c'
QA_REPLY = '7f9c69e755799c4e195a859615cffe5f163f91646ffea3c4e85db3d0bbc168a6'
QA_ENVELOPE = '86cfbcc63cf01facc545cb4573cffafc9005acc499ff51e140c3306f2f1560d4'
F33_ENVELOPE = '8486720f452aecc63b0bad28686592e4cc0ee6fbaac3b97647d63075076ee815'
SELECTION_HASH = 'bdce5c2cff7f19cb8b8050850eed1e48595432f359e098537608617ffb4e1337'
CAPTURE_SHA = '5b0af71eddfd8b470f8d791576512c6d3e286de065c76522b751e120ae9605e9'
CORRECTED_SHA = 'b7c29aa3e2d7fc11a504b508770b272eea784a0a7a0d46a7dd43b73f24ef38ad'
CONTROL = 'erp-command-3bf7c491-f438-47d4-9aa2-c053021a913a'
# Existing passive, already terminal background-QA receipt, not the governed QA.
PASSIVE_QA = 'TASK-2026-05624:5e944318-a000-4f44-8650-31ea6ea55847'
PASSIVE_RECEIPT = '123c6e8d2ae56d2b10431f23d0f21daea94d85454c3872fe981f56d728a2ba37'
MAX_ROWS, MAX_BYTES = 20000, 32 * 1024 * 1024


def need(value, code):
    if not value:
        raise RuntimeError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def text_sha(value):
    need(isinstance(value, str), 'EXPECTED_STORED_TEXT')
    return hashlib.sha256(value.encode()).hexdigest()


def table(db, name):
    need(isinstance(name, str) and re.fullmatch('[a-z][a-z0-9_]{0,100}', name),
         'SAFE_STORED_TABLE_NAME_REQUIRED')
    result = [dict(row) for row in db.execute('SELECT * FROM "' + name + '" ORDER BY rowid LIMIT 20001')]
    need(len(result) <= MAX_ROWS and len(canonical(result)) <= MAX_BYTES,
         'BOUNDED_HISTORY_REQUIRED')
    return result


def all_tables(db):
    rows = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    need(0 < len(rows) <= 128, 'BOUNDED_EXISTING_SCHEMA_REQUIRED')
    values = {row['name']: table(db, row['name']) for row in rows}
    return values, digest([dict(row) for row in rows])


def one(values, key, identity):
    found = [row for row in values if row.get(key) == identity]
    need(len(found) == 1, 'EXACT_ONE_EXISTING_RECORD_REQUIRED')
    return found[0]


def gate_facts(gates, leases, expected):
    need(isinstance(expected, dict) and set(expected) == {'state', 'epoch', 'active_admissions'}
         and expected['state'] in {'draining', 'sealed'}
         and type(expected['epoch']) is int and expected['epoch'] >= 30
         and type(expected['active_admissions']) is int and expected['active_admissions'] == 0,
         'REVIEWED_BLOCKED_ZERO_ACTIVE_GATE_REQUIRED')
    need(len(gates) == 1, 'ONE_EXISTING_GATE_REQUIRED')
    value = {key: gates[0][key] for key in ('state', 'epoch')}
    value['active_admissions'] = sum(row['state'] in {'active', 'unresolved'} for row in leases)
    need(value == expected, 'EXACT_GATE_OR_ACTIVE_WORK_CHANGED')
    return value


def validate_facts(facts):
    need(facts['f33'] == {'invocation_id': F33, 'run_id': F33_RUN, 'role': 'developer',
         'state': 'verified', 'dispatch_attempts': 1, 'result_published': True,
         'provider_status': 'unknown', 'provider_response_is_null': True,
         'provider_receipt_is_null': True, 'provider_scope_receipt_is_null': True,
         'provider_envelope_sha256': F33_ENVELOPE},
         'ORIGINAL_UNKNOWN_PROVIDER_HISTORY_MUST_REMAIN_EXACT')
    need(facts['qa'] == {'invocation_id': QA, 'run_id': QA_RUN, 'role': 'qa',
         'state': 'verified', 'qa_verdict': 'pass', 'result_sha256': QA_RESULT,
         'native_ack_sha256': QA_ACK, 'provider_status': 'delegated',
         'reply_sha256': QA_REPLY, 'envelope_sha256': QA_ENVELOPE,
         'result_native_identity_valid': True}, 'EXACT_PUBLISHED_QA_PASS_REQUIRED')
    need(facts['pm'] == {'invocation_id': PM, 'source_invocation_id': QA,
         'role': 'project_manager', 'state': 'blocked', 'run_id': None,
         'submitted_at': None, 'dispatch_attempts': 0, 'packet_hash': PM_PACKET,
         'stored_packet_hash_valid': True, 'session_count': 0, 'result_count': 0,
         'local_result_count': 0}, 'EXACT_UNSUBMITTED_PM_MUST_REMAIN_BLOCKED')
    need(facts['selection'] == {'count': 1, 'event_hash': SELECTION_HASH,
         'event_hash_valid': True, 'policy': 'returned-reply-format.v1',
         'protected_file_sha256': CAPTURE_SHA, 'corrected_reply_sha256': CORRECTED_SHA,
         'control_sequence': 14, 'control_command': CONTROL},
         'IMMUTABLE_ORIGINAL_V1_SELECTION_REQUIRED')
    need(facts['active_governance_count'] == 0 and facts['active_automation_count'] == 0
         and facts['other_unresolved_provider_count'] == 0 and facts['qa_non_done'] == 0,
         'NO_OTHER_CURRENT_OR_UNRESOLVED_WORK_REQUIRED')


def capture(hold, expected_gate):
    """No application imports; existing DBs use mode=ro, query_only and BEGIN.

    Database snapshots are individually consistent, not cross-database atomic.
    Repeated lock-held comparisons detect any change. Opening SQLite can create
    its own WAL/SHM sidecars as the service user; no schema/write SQL is executed.
    """
    with closing(hold.db('agentic-governance.sqlite')) as db:
        need(db.execute('PRAGMA query_only').fetchone()[0] == 1, 'QUERY_ONLY_REQUIRED')
        db.execute('BEGIN')
        values, schema = all_tables(db)
        invs = values['governance_invocation']
        f, q, p = (one(invs, 'invocation_id', value) for value in (F33, QA, PM))
        need(all(row['erp_project'] == PROJECT and row['erp_task'] == TASK for row in (f, q, p)),
             'SAME_PROJECT_TASK_REQUIRED')
        saved = values['governance_result']
        fr, qr = (one(saved, 'invocation_id', value) for value in (F33, QA))
        qlocal = one(values['governance_local_result'], 'invocation_id', QA)
        result, event, ack = (json.loads(qr[key]) for key in ('result_json', 'event_json', 'receipt_json'))
        qpacket, ppacket = json.loads(q['work_packet']), json.loads(p['work_packet'])
        identity_valid = (qr['phase'] == 'published' and qr['run_id'] == qlocal['run_id'] == QA_RUN
            and qr['result_json'] == qlocal['result_json'] and digest(result) == qr['result_sha256'] == QA_RESULT
            and result['invocation_id'] == event['invocation_id'] == QA
            and result['outcome'] == 'pass' and result['project_id'] == PROJECT and result['task_id'] == TASK
            and result['actor'] == qpacket['binding']
            and result['work_packet_digest'] == qpacket['content_sha256']
            and event['result_contract'] == result and event['type'] == 'qa.passed'
            and event['run_id'] == QA_RUN and event['acceptance_passed'] is True
            and ack['durable'] is True and ack['status'] == 'QA APPROVED'
            and ack['event_id'] == event['event_id'] and ack['task_id'] == TASK and ack['run_id'] == QA_RUN
            and text_sha(qr['receipt_json']) == QA_ACK and digest(qpacket) == q['packet_hash'])
        fresult, fack = json.loads(fr['result_json']), json.loads(fr['receipt_json'])
        fpublished = (fr['phase'] == 'published' and fr['run_id'] == F33_RUN
            and fresult['invocation_id'] == F33 and fresult['outcome'] == 'ready_for_review'
            and fr['result_sha256'] == digest(fresult) and fack['durable'] is True
            and fack['task_id'] == TASK and fack['run_id'] == F33_RUN)
        selected = [row for row in values['governance_event']
                    if row['invocation_id'] == F33 and row['kind'] == 'returned_reply_format_selected']
        need(len(selected) == 1, 'EXACT_ONE_V1_SELECTION_REQUIRED')
        selection = selected[0]
        body = json.loads(selection['payload'])
        selected_proof = {'count': 1, 'event_hash': selection['event_hash'],
            'event_hash_valid': selection['event_hash'] == digest([selection[key] for key in
                ('event_id', 'erp_project', 'invocation_id', 'kind', 'actor', 'payload', 'created_at', 'previous_hash')]),
            **{key: body.get(key) for key in ('policy', 'protected_file_sha256', 'corrected_reply_sha256',
                                            'control_sequence', 'control_command')}}
        facts = {
            'f33': {'invocation_id': f['invocation_id'], 'run_id': f['automation_run_id'],
                'role': f['target_role'], 'state': f['state'], 'dispatch_attempts': f['dispatch_attempts'],
                'result_published': bool(fpublished)},
            'qa': {'invocation_id': q['invocation_id'], 'run_id': q['automation_run_id'],
                'role': q['target_role'], 'state': q['state'], 'qa_verdict': q['qa_verdict'],
                'result_sha256': qr['result_sha256'], 'native_ack_sha256': text_sha(qr['receipt_json']),
                'result_native_identity_valid': bool(identity_valid)},
            'pm': {'invocation_id': p['invocation_id'], 'source_invocation_id': p['source_invocation_id'],
                'role': p['target_role'], 'state': p['state'], 'run_id': p['automation_run_id'],
                'submitted_at': p['submitted_at'], 'dispatch_attempts': p['dispatch_attempts'],
                'packet_hash': p['packet_hash'], 'stored_packet_hash_valid': digest(ppacket) == p['packet_hash'],
                **{label: sum(row['invocation_id'] == PM for row in values[name]) for label, name in
                    [('session_count', 'governance_gateway_session'), ('result_count', 'governance_result'),
                     ('local_result_count', 'governance_local_result')]}},
            'selection': selected_proof,
            'active_governance_count': sum(row['state'] in {'running', 'uncertain', 'cancel_requested'} for row in invs)}
        governance_hashes = {name: digest(rows) for name, rows in values.items()}
    with closing(hold.db('abitdot-primary.sqlite')) as db:
        need(db.execute('PRAGMA query_only').fetchone()[0] == 1, 'QUERY_ONLY_REQUIRED')
        db.execute('BEGIN')
        provider, provider_schema = all_tables(db)
        rows = provider['dot_primary_runs']
        pf, pq = (one(rows, 'original_run_id', value) for value in (F33_RUN, QA_RUN))
        facts['f33'].update(provider_status=pf['status'], provider_response_is_null=pf['response_text'] is None,
            provider_receipt_is_null=pf['receipt'] is None, provider_scope_receipt_is_null=pf['scope_receipt_json'] is None,
            provider_envelope_sha256=text_sha(pf['envelope_json']))
        facts['qa'].update(provider_status=pq['status'], reply_sha256=text_sha(pq['response_text']),
                           envelope_sha256=text_sha(pq['envelope_json']))
        facts['other_unresolved_provider_count'] = sum(row['original_run_id'] != F33_RUN
            and row['status'] in {'prepared', 'dispatching', 'unknown'} for row in rows)
        need(pq['receipt'] and pq['scope_receipt_json'], 'EXISTING_QA_PROVIDER_RECEIPT_REQUIRED')
    with closing(hold.db('webui.db')) as db:
        need(db.execute('PRAGMA query_only').fetchone()[0] == 1, 'QUERY_ONLY_REQUIRED')
        db.execute('BEGIN')
        automation = table(db, 'automation_run')
        for run in (F33_RUN, QA_RUN):
            need(one(automation, 'id', run)['status'] == 'success', 'ORIGINAL_SUCCESSFUL_AUTOMATION_REQUIRED')
        facts['active_automation_count'] = sum(row['status'] in {'running', 'pending', 'dispatching'} for row in automation)
    with closing(hold.db('erp-pending-review-qa.sqlite')) as db:
        need(db.execute('PRAGMA query_only').fetchone()[0] == 1, 'QUERY_ONLY_REQUIRED')
        db.execute('BEGIN')
        reviews, review_schema = all_tables(db)
        facts['qa_non_done'] = sum(row['phase'] != 'done' for row in reviews['review'])
    with closing(hold.db('maintenance-admission/gate.sqlite')) as db:
        need(db.execute('PRAGMA query_only').fetchone()[0] == 1, 'QUERY_ONLY_REQUIRED')
        db.execute('BEGIN')
        gates, leases, audit = (table(db, name) for name in ('gate', 'lease', 'audit'))
    gate = gate_facts(gates, leases, expected_gate)
    validate_facts(facts)
    return {'facts': facts, 'gate_facts': gate, 'governance_schema_sha256': schema,
        'governance_table_hashes': governance_hashes, 'provider_schema_sha256': provider_schema,
        'provider_tables_sha256': digest(provider), 'automation_sha256': digest(automation),
        'review_schema_sha256': review_schema, 'review_tables_sha256': digest(reviews),
        'gate': gates, 'leases': leases, 'maintenance_audit': audit}


def preserve(before, after, *, qa_owner):
    """Only the one existing passive terminal-QA pair may append, at most64 times."""
    immutable = set(before) - {'leases', 'maintenance_audit'}
    need(set(after) == set(before) and all(before[key] == after[key] for key in immutable),
         'ORIGINAL_BUSINESS_PROVIDER_OR_GATE_HISTORY_CHANGED')
    old = {row['id']: row for row in before['leases']}
    new = {row['id']: row for row in after['leases']}
    need(len(old) == len(before['leases']) and len(new) == len(after['leases'])
         and set(old) <= set(new) and all(new[key] == value for key, value in old.items()),
         'ORIGINAL_ADMISSION_CHANGED_OR_REMOVED')
    prior = before['maintenance_audit']
    need(after['maintenance_audit'][:len(prior)] == prior, 'ORIGINAL_ADMISSION_AUDIT_CHANGED')
    extra = [row for key, row in new.items() if key not in old]
    events = after['maintenance_audit'][len(prior):]
    need(len(extra) <= 64 and len(events) == 2 * len(extra), 'ONLY_BOUNDED_PASSIVE_QA_PAIRS_ALLOWED')
    for row in extra:
        need(row['kind'] == 'qa' and row['reference'] == PASSIVE_QA and row['state'] == 'finished'
             and row['epoch'] == before['gate_facts']['epoch'] and row['parent_id'] is None
             and row['receipt_sha256'] == PASSIVE_RECEIPT
             and (row['pid'], row['process_start']) == qa_owner,
             'EXACT_UNCHANGED_QA_OWNER_AND_TERMINAL_RECEIPT_REQUIRED')
        pair = [event for event in events if json.loads(event['payload']).get('lease') == row['id']]
        need(len(pair) == 2 and [e['kind'] for e in pair] ==
             ['admitted_original_recovery', 'original_receipt_settled'], 'EXACT_PASSIVE_QA_AUDIT_PAIR_REQUIRED')
        need(json.loads(pair[0]['payload']) == {'lease': row['id'], 'kind': 'qa', 'reference': PASSIVE_QA,
             'epoch': before['gate_facts']['epoch'], 'parent': None, 'original_receipt': PASSIVE_RECEIPT}
             and json.loads(pair[1]['payload']) == {'lease': row['id'], 'kind': 'qa',
                 'reference': PASSIVE_QA, 'receipt_sha256': PASSIVE_RECEIPT},
             'PASSIVE_QA_AUDIT_IDENTITY_CHANGED')
    return len(extra)

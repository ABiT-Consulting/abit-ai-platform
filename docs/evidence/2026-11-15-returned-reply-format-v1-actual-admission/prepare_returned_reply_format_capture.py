"""Prepare a proposed Root capture from exact retained actual bytes; never stage it.

All database connections are mode=ro/query_only. No ERP, browser, provider, Git,
admission, service or model calls are made. Normal completion remains mandatory.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import uuid

HERE = Path(__file__).resolve().parent
DATA = Path('/srv/abit-dev-linux/abit-ai/data')
OUT = Path('/var/lib/abit-ai-erp-rollouts/governed-returned-reply-format-correction-v1-20261115')
INV = 'agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35'
RUN = 'adf93fbf-9737-5eb4-9f5d-21e02dc63014'
ORIGIN = 'agov-child-972b2a1794c982c7c3aefab1314314e4b5c660f6cba9c32e'
POLICY = 'returned-reply-format.v1'
REPLY_SHA = 'b7c29aa3e2d7fc11a504b508770b272eea784a0a7a0d46a7dd43b73f24ef38ad'
META_SHA = 'f0f71b163e4d9e904e644e456b287857d513eb56492e3f955d6486e4020a6d58'
INTENT_SHA = '7d260df9d1d5c8d8c546c8a356e887db615e9dec2a2df1ecfd1993fa932d9929'
PARENT_SHA = '3648865c420ab1aefb87f40dae228414ef2407a69434af51fe4b5d626ddb57eb'
RELEASE_SHA = 'aa59829769f372b6b2bd11063c4d64ca3606e12194d54389a7bc5961240f9f5a'
OBS_SHA = '881b0fced1df3e8d8ec649f53c3f5cb7076f2c2a454cf940834bbe02ad24fd6f'
COMMAND_SHA = '7563887192862206e23b5ad0f2eb08362f1936a7c752c9494ceee9b93708d527'

def sha(value):
    return hashlib.sha256(value).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def digest(value):
    return sha(canonical(value).encode())

def retained(path, expected):
    raw = path.read_bytes()
    assert sha(raw) == expected, 'Retained input hash changed: ' + path.name
    return raw

def read_db(name):
    db = sqlite3.connect('file:' + str(DATA / name) + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db

def write_proposal(path, value):
    assert path.parent == HERE and path.name.endswith('.json')
    raw = (canonical(value) + '\n').encode()
    if path.exists():
        assert path.read_bytes() == raw, 'Existing proposed evidence is immutable'
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    return {'path': str(path), 'sha256': sha(raw), 'bytes': len(raw)}

def main():
    inputs = HERE / 'private-actual-correction'
    reply = retained(inputs / 'abit-f33-corrected-code-reply-20261115.txt', REPLY_SHA)
    assert len(reply) == 6767
    text = reply.decode('utf-8', errors='strict')  # No strip, replacement or normalization.
    metadata = json.loads(retained(inputs / 'abit-f33-corrected-code-metadata-20261115.json', META_SHA))
    intent = json.loads(retained(OUT / 'format-data-v1/intent.json', INTENT_SHA))
    parent = json.loads(retained(OUT / 'format-message-v1/intent.json', PARENT_SHA))
    release = json.loads(retained(OUT / 'release-f33-capture-v1/completed-receipt.json', RELEASE_SHA))
    previous = HERE.parent / 'governed-github-commit-readback-recovery-v1-proposal-20261115'
    observed = json.loads(retained(previous / 'returnedf33-current-feed-readonly-observe.private.json', OBS_SHA))
    assert release['ok'] is True and release['run_id'] == RUN
    assert release['original_provider_start_delivery_history_preserved'] is True
    assert release['health']['active_relay_turns'] == 0
    assert release['health']['authenticated'] is True and release['health']['target_ready'] is True
    assert all(release[k] == 0 for k in ('DB_writes', 'Native_Start_calls', 'ChatGPT_Stop_calls', 'model_sends', 'gate_changes'))
    assert intent['invocation_id'] == INV and intent['run_id'] == RUN
    assert intent['parent_intent_sha256'] == PARENT_SHA
    assert intent['pause_sequence'] == 12 and intent['max_send_attempts'] == 1
    assert intent['additional_model_messages_including_parent'] == 2
    assert intent['new_business_runs_NativeStart_tools_tests_authorized'] == 0
    assert intent['Root_semantic_result_reconstruction'] is False
    assert intent['canonical_command_sha256'] == COMMAND_SHA
    assert metadata['additional_operator_format_data_messages'] == 2
    assert metadata['new_business_run_or_tool_work_requested'] == 0
    assert type(metadata['captured_at']) is int and metadata['captured_at'] > 0
    old_lines = [line for line in observed['observed']['body_tail'].splitlines() if line.startswith('AGOV RESULT:')]
    assert len(old_lines) == 1
    visible_sha = sha(old_lines[0].encode())
    assert visible_sha == intent['original_visible_reply_sha256']
    assert metadata['original_root_id'] == observed['root_message_id']
    assert metadata['original_reply_id'] == observed['observed']['feed'][-1]['id']
    shape = re.compile(r'([a-f0-9]{32}~[a-f0-9]{32})~CalpicoMessage~Sentinel_[a-f0-9]{32}')
    ids = [metadata[k] for k in ('original_root_id','original_reply_id','correction_root_id','correction_reply_id')]
    matched = [shape.fullmatch(value) for value in ids]
    assert len(set(ids)) == 4 and all(matched)
    assert len({value.group(1) for value in matched}) == 1
    assert metadata['correction_root_id'].endswith('Sentinel_4abf34dfe2ec8191afeb4f7d35dc9578')
    assert metadata['correction_reply_id'].endswith('Sentinel_0b4ebf7af360819186056de344089818')
    lines = [line[len('AGOV RESULT:'):].strip() for line in text.splitlines() if line.startswith('AGOV RESULT:')]
    assert len(lines) == 1
    result = json.loads(lines[0])  # Syntax and exact identity only; no result rewriting.
    db = read_db('agentic-governance.sqlite')
    provider = read_db('abitdot-primary.sqlite')
    try:
        row = dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (INV,)).fetchone())
        assert row['state'] == 'uncertain' and row['automation_run_id'] == RUN
        assert row['submitted_at'] == 1794734924 and row['dispatch_attempts'] == 1
        assert RUN == str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://abit.ai/agentic/run/' + INV))
        assert row['erp_project'] == 'PROJ-0198' and row['erp_task'] == 'TASK-2026-06200'
        assert row['target_role'] == 'developer'
        lease = db.execute('SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?', (row['erp_project'],row['erp_task'])).fetchone()
        assert tuple(lease) == (INV, row['lease_fence'])
        packet = json.loads(row['work_packet'])
        assert digest(packet) == row['packet_hash']
        for table in ('governance_local_result','governance_result','governance_action'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                assert db.execute('SELECT 1 FROM ' + table + ' WHERE invocation_id=?', (INV,)).fetchone() is None
        project = db.execute('SELECT state FROM governance_project_control WHERE erp_project=?', (row['erp_project'],)).fetchone()
        assert project['state'] == 'paused'
        pause = db.execute("SELECT * FROM governance_native_control WHERE project=? AND sequence=12 AND phase='acknowledged' AND action='pause'", (row['erp_project'],)).fetchall()
        assert len(pause) == 1
        start = dict(db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (INV,)).fetchone())
        event, receipt = json.loads(start['event_json']), json.loads(start['receipt_json'])
        assert start['run_id'] == RUN and event['run_id'] == RUN and receipt['run_id'] == RUN
        assert receipt['durable'] is True and receipt['task_id'] == row['erp_task']
        sessions = db.execute('SELECT * FROM governance_gateway_session WHERE invocation_id=?', (INV,)).fetchall()
        assert len(sessions) == 1 and sessions[0]['admission_json']
        admission = json.loads(sessions[0]['admission_json'])
        assert set(admission) == {'protocol_version','scope','preflight_proof','submission_proof'}
        assert admission['scope']['invocation_id'] == INV and admission['scope']['run_id'] == RUN
        calls = db.execute('SELECT c.operation,c.state FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id WHERE s.invocation_id=? ORDER BY c.created_at', (INV,)).fetchall()
        assert [tuple(x) for x in calls] == [('context.read','completed'),('repository.delivery_status','completed'),('repository.deliver','completed')]
        originals = provider.execute('SELECT * FROM dot_primary_runs WHERE automation_id=? AND original_run_id=?', (row['agent_id'],RUN)).fetchall()
        assert len(originals) == 1
        original = dict(originals[0])
        assert original['status'] in {'unknown','unavailable','dispatching'} and not original['response_text'] and not original['receipt']
        envelope = json.loads(original['envelope_json'])
        assert admission == envelope['governance_scope']
        scope = envelope['governance_scope']['scope']
        assert scope['invocation_id'] == INV and scope['run_id'] == RUN
        assert metadata['conversation_url'] == 'https://chatgpt.com/dots/' + envelope['dot_id']
        assert all(result.get(k) == value for k,value in {'invocation_id':INV,'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],'work_packet_digest':packet['content_sha256'],'actor':packet['binding']}.items())
        assert result['schema_version'] == 1 and result['outcome'] == 'ready_for_review'
        delivery = dict(db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?', (ORIGIN,)).fetchone())
        assert delivery['state'] == 'completed'
        actual = json.loads(delivery['result_json'])
        assert all(result[k] == actual[k] for k in ('delivery','test_results','evidence'))
        command = actual['delivery']['test_runs'][0]['command']
        assert len(command.encode()) == 4420 and sha(command.encode()) == COMMAND_SHA
        proof = {'schema_version':1,'purpose':POLICY,'invocation_id':INV,'run_id':RUN,
                 'project_id':row['erp_project'],'task_id':row['erp_task'],'agent_id':row['agent_id'],
                 'packet_sha256':row['packet_hash'],'native_start_event_sha256':start['event_sha256'],
                 'native_start_receipt_sha256':digest(receipt),
                 'provider_envelope_sha256':sha(original['envelope_json'].encode()),
                 'scope_sha256':digest(envelope['governance_scope']),
                 'gateway_admission_sha256':digest(admission),'owner_id':envelope['owner_id'],
                 'account_id':scope['account_id'],'dot_id':envelope['dot_id'],
                 'conversation_url':metadata['conversation_url'],
                 **{k:metadata[k] for k in ('original_root_id','original_reply_id','correction_root_id','correction_reply_id','captured_at')},
                 'original_visible_reply_sha256':visible_sha,'corrected_reply_utf8':text,
                 'corrected_reply_sha256':REPLY_SHA,'correction_intent_sha256':INTENT_SHA,
                 'capture_release_proof_sha256':RELEASE_SHA,'relay_idle_proof_sha256':RELEASE_SHA,
                 'pause_control_sequence':12}
        contract = json.loads((HERE / 'format-capture-contract.json').read_text())
        assert set(proof) == set(contract['exact_keys'])
        assert db.total_changes == provider.total_changes == 0
        prepared = write_proposal(HERE / 'returned-reply-format-capture.proposed.private.json', proof)
        evidence = {'schema_version':1,'purpose':'actual-returned-format-capture-preparation',
                    'state':'PROPOSED_ONLY_PENDING_ROOT_REVIEW_STAGING_AND_FRESH_CONTROLS',
                    'proposed':prepared,'actual_reply_sha256':REPLY_SHA,'actual_reply_bytes':len(reply),
                    'actual_metadata_sha256':META_SHA,'visible_original_hash_kind':'DOM-only, not raw-model assertion',
                    'visible_original_reply_sha256':visible_sha,'format_data_intent_sha256':INTENT_SHA,
                    'parent_format_intent_sha256':PARENT_SHA,'additional_format_data_messages':2,
                    'original_envelope_preserved':True,'original_run_attempt':1,
                    'original_delivery_evidence_test_results_exact':True,'exact_recorded_command_sha256':COMMAND_SHA,
                    'gateway_calls_all3_completed':True,'Core_DB_changes':db.total_changes,
                    'provider_DB_changes':provider.total_changes,'protected_staging':False,
                    'network_browser_model_Git_calls':0,'ordinary_completion_validation_still_required':True}
        report = write_proposal(HERE / 'actual-correction-preparation-proof.private.json', evidence)
        print(json.dumps({'proposed':prepared,'preparation_proof':report,'Root_staging_performed':False},sort_keys=True))
    finally:
        db.rollback(); db.close(); provider.rollback(); provider.close()

if __name__ == '__main__':
    main()

"""Verify a failed outgoing original; this module never sends or rearms work."""

import hashlib
import json
import time

DIALOG = 'The message you submitted was too long, please edit it and resubmit.'
FAILED_LABEL = 'Failed to send. Click to retry'


def verify_rejection(envelope, proof):
    """A visible outgoing root can be a failed send, not an accepted model turn.

    The authenticated existing relay supplies this proof. Caller-provided ERP
    fields, generic message counts and a global error dialog are insufficient.
    """
    from open_webui.utils.agentic_relay_scope import scope_receipt

    try:
        scope = envelope['governance_scope']['scope']
        if (
            not isinstance(proof, dict)
            or proof.get('ok') is not True
            or proof.get('read_only') is not True
            or proof.get('owner') != envelope['owner_id']
            or proof.get('account_id') != scope['account_id']
            or proof.get('job_id') != scope['job_id']
            or proof.get('dot_handoff') is not False
            or proof.get('response') != ''
            or proof.get('reply_message_id') is not None
            or proof.get('conversation_url') != 'https://chatgpt.com/dots/' + envelope['dot_id']
        ):
            raise ValueError()
        receipt = scope_receipt(envelope, scope['account_id'], proof.get('scope_receipt'))
        rejection = proof['rejection']
        if (rejection.get('code') != 'CHATGPT_MESSAGE_TOO_LONG'
            or rejection.get('not_submitted') is not True or rejection.get('dialog') != DIALOG):
            raise ValueError()
        if proof.get('protocol') == 'v4_outgoing_rejection':
            root = proof.get('root_message_id')
            failure = proof.get('root_failure')
            if (not isinstance(root, str) or not root or not isinstance(failure, dict)
                or failure != {'root_id': root, 'original_envelope_matched': True,
                               'failed_to_send': True, 'failed_send_label': FAILED_LABEL,
                               'generating': False}):
                raise ValueError()
        else:
            if (proof.get('root_message_id') is not None
                or proof.get('rejection_capture') != {
                    'send_attempted': True, 'baseline_empty': True,
                    'baseline_rejection_absent': True, 'accepted_observed': False}
                or any(rejection.get(k) is not False for k in
                       ('original_user_visible', 'feed_present', 'generating'))
                or type(rejection.get('users')) is not int or rejection['users'] != 0
                or type(rejection.get('assistants')) is not int or rejection['assistants'] != 0):
                raise ValueError()
        # Only protocol evidence is retained; broad observer DOM/body diagnostics
        # never enter the provider error or its immutable rejection audit.
        keep = ('protocol', 'ok', 'read_only', 'owner', 'account_id', 'job_id',
                'dot_handoff', 'response', 'conversation_url', 'root_message_id',
                'reply_message_id', 'rejection', 'root_failure', 'rejection_capture')
        result = {k: proof[k] for k in keep if k in proof}
        result['scope_receipt'] = receipt
        return result
    except Exception:
        raise ValueError('Original outgoing rejection does not verify for this exact job') from None


def record_rejection(db, envelope, proof):
    """CAS only this original to unavailable, preserving its envelope and proof.

    This is a negative provider receipt, not permission to retry. Existing task,
    project, lease and Native Retry gates remain necessary before another send.
    """
    checked = verify_rejection(envelope, proof)
    raw = json.dumps(checked, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    sha = hashlib.sha256(raw.encode()).hexdigest()
    key = envelope['idempotency_key']
    row = db.execute('SELECT * FROM dot_primary_runs WHERE id=?', (key,)).fetchone()
    if (not row or json.loads(row['envelope_json']) != envelope
        or row['receipt'] is not None or row['response_text'] is not None
        or row['scope_receipt_json'] is not None
        or row['status'] not in {'dispatching', 'unknown', 'unavailable'}):
        raise ValueError('Original provider is not an unresolved outgoing request')
    db.execute('''CREATE TABLE IF NOT EXISTS dot_primary_rejections(
        run_key TEXT NOT NULL, proof_sha256 TEXT NOT NULL, proof_json TEXT NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY(run_key,proof_sha256))''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS dot_rejections_no_update
        BEFORE UPDATE ON dot_primary_rejections BEGIN
        SELECT RAISE(ABORT,'Original rejection evidence is immutable'); END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS dot_rejections_no_delete
        BEFORE DELETE ON dot_primary_rejections BEGIN
        SELECT RAISE(ABORT,'Original rejection evidence is immutable'); END''')
    now = int(time.time())
    db.execute('INSERT OR IGNORE INTO dot_primary_rejections VALUES(?,?,?,?)', (key,sha,raw,now))
    db.execute("UPDATE dot_primary_runs SET status='unavailable',reason=?,updated_at=? "
               "WHERE id=? AND status IN ('dispatching','unknown','unavailable') AND receipt IS NULL "
               "AND response_text IS NULL AND scope_receipt_json IS NULL",
               ('CHATGPT_MESSAGE_TOO_LONG: ' + DIALOG,now,key))
    return {'state':'unavailable','receipt_id':None,'reason':'CHATGPT_MESSAGE_TOO_LONG: ' + DIALOG,
            'original_rejection_sha256':sha, 'retry_authorized':False}

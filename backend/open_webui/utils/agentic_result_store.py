"""Immutable result/outbox journal, separate from model execution and ERP status."""

from __future__ import annotations
import json
import time
from pathlib import Path
from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest
from open_webui.utils.agentic_governance import connect


def _db(path=None):
    return connect(path)


def _schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS governance_result(
        invocation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, result_sha256 TEXT NOT NULL,
        result_json TEXT NOT NULL, event_json TEXT NOT NULL, phase TEXT NOT NULL,
        receipt_json TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    );''')
    db.execute('''CREATE TABLE IF NOT EXISTS governance_local_result(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,result_json TEXT NOT NULL,
        created_at INTEGER NOT NULL)''')


CONTRACT_REJECTION_KIND = 'result_contract_rejected'
CONTRACT_REJECTION_REASON = (
    'Original Developer result contract rejected; original task and admission ownership held'
)


def hold_contract_rejection(row, packet, record, automation, error, db_path=None):
    """Audit only an exact accepted original reply; never invent a valid Result.

    The provider journal is read-only. Governance keeps the task lease and any
    maintenance admission, including while gateway actions remain unresolved.
    AutomationRun stays uncompleted so no success or generic error-state bypass
    is introduced. An authorized format-correction view is not the raw original.
    """
    import sqlite3
    from open_webui.env import DATA_DIR
    from open_webui.utils.abitdot_primary import dot_primary_key, verify_saved_scope_receipt
    from open_webui.utils.agentic_bridge import packet_digest, stable_run_id
    from open_webui.utils.agentic_relay_scope import scope_for_packet
    from open_webui.utils.agentic_result_provenance import reply_sha256

    if error.stage not in {'parse', 'contract'} or row['target_role'] != 'developer':
        raise ValueError('Only an original Developer contract rejection can be held')
    provider_path = Path(DATA_DIR) / 'abitdot-primary.sqlite'
    with sqlite3.connect(provider_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3) as provider:
        provider.row_factory = sqlite3.Row
        provider.execute('PRAGMA query_only=ON')
        rows = provider.execute(
            'SELECT * FROM dot_primary_runs WHERE owner=? AND automation_id=? AND original_run_id=?',
            (automation.user_id, row['agent_id'], row['automation_run_id']),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError('Contract rejection requires one saved original provider reply')
    original = dict(rows[0])
    exact_fields = ('id', 'original_run_id', 'status', 'receipt', 'response_text',
                    'scope_receipt_json', 'conversation_url')
    if (original['status'] != 'delegated' or not original['response_text']
            or any(record.get(key) != original[key] for key in exact_fields)
            or record.get('format_correction_provenance') is not None):
        raise ValueError('Contract rejection requires the unchanged accepted original reply')
    envelope = json.loads(original['envelope_json'])
    meta = envelope.get('task', {}).get('meta') or {}
    scope = envelope.get('governance_scope', {}).get('scope', {})
    expected_key = dot_primary_key(automation.user_id, row['agent_id'], row['automation_run_id'])
    if (original['id'] != expected_key or automation.id != row['agent_id']
            or envelope.get('idempotency_key') != expected_key
            or envelope.get('owner_id') != automation.user_id
            or envelope.get('automation_id') != row['agent_id']
            or envelope.get('run_id') != row['automation_run_id']
            or envelope.get('dot_id') != original['dot_id']
            or meta.get('governance_managed') is not True
            or meta.get('erp_task') != row['erp_task']
            or meta.get('erp_role') != row['target_role']
            or scope != scope_for_packet(row, packet, automation.user_id,
                                        scope.get('account_id'), original['dot_id'], row['automation_run_id'])):
        raise ValueError('Contract rejection provider scope differs from its original')
    verify_saved_scope_receipt(original)
    audit = {
        'policy': 'agov.original-contract-rejection-hold.v1',
        'invocation_id': row['invocation_id'], 'run_id': row['automation_run_id'],
        'packet_sha256': row['packet_hash'], 'lease_fence': row['lease_fence'],
        'provider_key_sha256': reply_sha256(original['id']),
        'raw_reply_sha256': reply_sha256(original['response_text']),
        'envelope_sha256': reply_sha256(original['envelope_json']),
        'provider_receipt_sha256': reply_sha256(original['receipt']),
        'scope_receipt_sha256': reply_sha256(original['scope_receipt_json']),
        'stage': error.stage, 'error_sha256': reply_sha256(str(error)),
        'disposition': 'hold_original_ownership',
    }
    with ledger._transaction(db_path) as db:
        current = ledger._owned(db, row['invocation_id'], row['automation_run_id'],
                                row['lease_token'], {'running', 'uncertain', 'cancel_requested'})
        keys = ('agent_id', 'erp_project', 'erp_task', 'target_role', 'task_revision',
                'packet_hash', 'work_packet', 'lease_fence')
        if (not current or any(current[key] != row[key] for key in keys)
                or current['submitted_at'] is None
                or current['automation_run_id'] != stable_run_id(row['invocation_id'])
                or json.loads(current['work_packet']) != packet
                or current['packet_hash'] != digest(packet)
                or packet.get('content_sha256') != packet_digest(packet)):
            raise ValueError('Contract rejection requires the exact owned submitted original')
        owner = db.execute(
            'SELECT owner_id,erp_user FROM governance_agent WHERE agent_id=? AND erp_project=? AND role=?',
            (row['agent_id'], row['erp_project'], row['target_role']),
        ).fetchone()
        if not owner or tuple(owner) != (automation.user_id, packet['binding']['erp_user']):
            raise ValueError('Contract rejection original specialist binding changed')
        for table in ('governance_local_result', 'governance_result'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                if db.execute('SELECT 1 FROM ' + table + ' WHERE invocation_id=?',
                              (row['invocation_id'],)).fetchone():
                    raise ValueError('Contract rejection cannot replace a saved result')
        sessions = db.execute(
            'SELECT scope_json,admission_json FROM governance_gateway_session WHERE invocation_id=?',
            (row['invocation_id'],),
        ).fetchall()
        if (len(sessions) != 1 or json.loads(sessions[0]['scope_json']) != scope
                or json.loads(sessions[0]['admission_json']) != envelope['governance_scope']):
            raise ValueError('Contract rejection requires the exact original gateway admission')
        prior = db.execute(
            'SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
            (row['invocation_id'], CONTRACT_REJECTION_KIND),
        ).fetchall()
        if prior:
            if len(prior) != 1 or json.loads(prior[0]['payload']) != audit:
                raise ValueError('Original contract rejection evidence is immutable')
            return False
        ledger._event(db, row['erp_project'], row['invocation_id'], CONTRACT_REJECTION_KIND,
                      'original-result-validator', audit)
        if current['state'] != 'cancel_requested':
            db.execute(
                "UPDATE governance_invocation SET state='uncertain',reason=?,updated_at=? WHERE invocation_id=?",
                (CONTRACT_REJECTION_REASON, int(time.time()), row['invocation_id']),
            )
    return True


def assert_original_reply(invocation_id, run_id, raw_reply, db_path=None):
    """Do not overwrite a recorded original reply with a different retry read."""
    from open_webui.utils.agentic_result_provenance import AUDIT_KIND, reply_sha256

    with _db(db_path) as db:
        rows = db.execute(
            'SELECT payload FROM governance_event WHERE invocation_id=? AND kind IN (?,?)',
            (invocation_id, AUDIT_KIND, 'architecture_review_phase'),
        ).fetchall()
    if len(rows) > 1:
        raise ValueError('Original result provenance audit is ambiguous')
    if rows:
        prior = json.loads(rows[0][0])
        if prior.get('run_id') != run_id or prior.get('raw_reply_sha256') != reply_sha256(raw_reply):
            raise ValueError('Original raw reply is immutable')


def save_local_result(invocation_id, run_id, result, db_path=None, *, provenance=None, raw_reply=None,
                      architecture_review=None):
    """Atomically preserve the validated canonical contract and provenance audit.

    Raw text remains in the original AutomationRun/relay record; only its digest
    is exposed in the hash-chained event. No capability or raw reply is exported.
    """
    with _db(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
        if (result.get('actor') or {}).get('role') == 'project_manager':
            from open_webui.utils.agentic_blocker_review import require_triage_outcome

            owner = db.execute(
                'SELECT * FROM governance_invocation WHERE invocation_id=? AND automation_run_id=?',
                (invocation_id, run_id),
            ).fetchone()
            if owner:
                require_triage_outcome(dict(owner), result, db_path)
        prior = db.execute(
            'SELECT run_id,result_json FROM governance_local_result WHERE invocation_id=?', (invocation_id,)
        ).fetchone()
        value = (run_id, canonical(result))
        if prior and tuple(prior) != value:
            raise ValueError('Original local result is immutable')
        from open_webui.utils.agentic_result_provenance import (
            AUDIT_KIND, parse_original_reply, project_provenance, reply_sha256,
        )

        audits = db.execute(
            'SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
            (invocation_id, AUDIT_KIND),
        ).fetchall()
        if len(audits) > 1:
            raise ValueError('Original result provenance audit is ambiguous')
        if provenance is not None:
            if not isinstance(raw_reply, str):
                raise ValueError('Original raw reply is required for provenance audit')
            current = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',
                                 (invocation_id,)).fetchone()
            if not current or current['automation_run_id'] != run_id:
                raise ValueError('Original provenance invocation changed')
            current = dict(current)
            frozen = json.loads(current['work_packet'])
            projected, expected = project_provenance(
                parse_original_reply(raw_reply), row=current, packet=frozen,
                raw_reply_sha256=reply_sha256(raw_reply),
            )
            if projected != result or expected != provenance:
                raise ValueError('Canonical provenance proof changed before storage')
            if audits and json.loads(audits[0][0]) != provenance:
                raise ValueError('Original result provenance policy or reply changed')
            if bool(prior) != bool(audits):
                raise ValueError('Original canonical result and provenance audit are not atomic')
        elif audits:
            raise ValueError('Original result provenance cannot be discarded')
        from open_webui.utils.agentic_architecture_review import (
            AUDIT_KIND as PHASE_KIND, derive_phase, matches_phase, phase_audit,
        )
        phase_audits = db.execute(
            'SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
            (invocation_id, PHASE_KIND),
        ).fetchall()
        if len(phase_audits) > 1:
            raise ValueError('Original architecture-review audit is ambiguous')
        verified_phase = None
        if architecture_review is not None:
            if provenance is not None or not isinstance(raw_reply, str):
                raise ValueError('Architecture review requires only its exact original raw reply')
            current = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',
                                 (invocation_id,)).fetchone()
            if not current or current['automation_run_id'] != run_id:
                raise ValueError('Original architecture-review invocation changed')
            current = dict(current)
            frozen = json.loads(current['work_packet'])
            verified_phase = derive_phase(
                current, frozen, parse_original_reply(raw_reply), db=db,
                raw_reply_sha256=reply_sha256(raw_reply),
            )
            if (verified_phase is None or not matches_phase(architecture_review, result, frozen)
                    or not matches_phase(verified_phase, result, frozen)
                    or phase_audit(verified_phase) != phase_audit(architecture_review)):
                raise ValueError('Architecture-review phase changed before storage')
            if phase_audits and json.loads(phase_audits[0][0]) != phase_audit(verified_phase):
                raise ValueError('Original architecture-review policy or reply changed')
            if bool(prior) != bool(phase_audits):
                raise ValueError('Architecture-review result and phase audit are not atomic')
        elif phase_audits:
            raise ValueError('Original architecture-review phase cannot be discarded')
        db.execute(
            'INSERT OR IGNORE INTO governance_local_result VALUES(?,?,?,?)', (invocation_id, *value, int(time.time()))
        )
        if provenance is not None and not audits:
            ledger._event(db, current['erp_project'], invocation_id, AUDIT_KIND,
                          'server-provenance-policy', provenance)
        if verified_phase is not None and not phase_audits:
            ledger._event(db, current['erp_project'], invocation_id, PHASE_KIND,
                          'server-architecture-review', phase_audit(verified_phase))
        db.execute('COMMIT')


def save_result(invocation_id, run_id, result, event, db_path=None):
    with _db(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
        from open_webui.utils.agentic_start_recovery import project_native_result
        owner=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(invocation_id,)).fetchone()
        projection=None
        if owner:
            owner=dict(owner)
            event,projection=project_native_result(owner,json.loads(owner['work_packet']),result,event,db=db)
        if projection:
            audits=db.execute("SELECT payload FROM governance_event WHERE invocation_id=? AND kind='readiness_native_result'",(invocation_id,)).fetchall()
            if audits and (len(audits)!=1 or json.loads(audits[0]['payload'])!=projection):
                raise ValueError('Original readiness result projection is immutable')
            if not audits:ledger._event(db,owner['erp_project'],invocation_id,'readiness_native_result','server-readiness-policy',projection)
        current = db.execute(
            'SELECT result_sha256,event_json FROM governance_result WHERE invocation_id=?', (invocation_id,)
        ).fetchone()
        value = digest(result)
        if current:
            if tuple(current) != (value, canonical(event)):
                db.execute('ROLLBACK')
                raise ValueError('Original saved result is immutable')
            db.execute('COMMIT')
            return False
        db.execute(
            'INSERT INTO governance_result VALUES(?,?,?,?,?,?,NULL,?,?)',
            (
                invocation_id,
                run_id,
                value,
                canonical(result),
                canonical(event),
                'pending',
                int(time.time()),
                int(time.time()),
            ),
        )
        db.execute('COMMIT')
        return True


def get_result(invocation_id, db_path=None):
    with _db(db_path) as db:
        _schema(db)
        db.row_factory = __import__('sqlite3').Row
        row = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ('result_json', 'event_json', 'receipt_json'):
            result[key.removesuffix('_json')] = json.loads(result.pop(key) or 'null')
        return result


def publication_attempt(invocation_id, db_path=None):
    with _db(db_path) as db:
        _schema(db)
        pm_result = db.execute(
            '''SELECT i.*, r.result_json, r.event_json FROM governance_result r
               JOIN governance_invocation i ON i.invocation_id=r.invocation_id
               WHERE i.invocation_id=? AND r.phase='pending' AND i.target_role='project_manager' ''',
            (invocation_id,),
        ).fetchone()
        if pm_result:
            from open_webui.utils.agentic_blocker_review import require_triage_outcome

            row = dict(pm_result)
            require_triage_outcome(row, json.loads(row['result_json']), db_path, event=json.loads(row['event_json']))
        return (
            db.execute(
                "UPDATE governance_result SET phase='uncertain',updated_at=? WHERE invocation_id=? AND phase='pending'",
                (int(time.time()), invocation_id),
            ).rowcount
            == 1
        )


def publication_receipt(invocation_id, receipt, db_path=None, *, native_content_sha256=None):
    with _db(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(
            'SELECT receipt_json,event_json,run_id,phase FROM governance_result WHERE invocation_id=?', (invocation_id,)
        ).fetchone()
        if not row:
            raise ValueError('No saved original result')
        from open_webui.utils.agentic_publication_receipts import semantic_receipt

        event = json.loads(row[1])
        if event.get('invocation_id') != invocation_id or event.get('run_id') != row[2]:
            raise ValueError('Saved publication original identity differs')
        semantic = semantic_receipt(event, receipt)
        if native_content_sha256 is not None and native_content_sha256 != digest(event):
            raise ValueError('Native ERP inbox digest differs from original event')
        if row[0] and semantic_receipt(event, json.loads(row[0])) != semantic:
            raise ValueError('Original ERP receipt is immutable')
        # Keep the first raw receipt and its original verified-publication clock.
        # A repeated transport response is not new execution progress.
        if row[3] != 'published':
            db.execute(
                "UPDATE governance_result SET phase='published',receipt_json=?,updated_at=? WHERE invocation_id=?",
                (row[0] or canonical(receipt), int(time.time()), invocation_id),
            )
        elif not row[0]:
            raise ValueError('Published original result has no durable receipt')
        proof = {
            'schema_version': 1, 'event_id': event['event_id'], 'run_id': row[2],
            'event_sha256': digest(event), 'receipt_sha256': digest(receipt),
            'semantic_receipt_sha256': digest(semantic),
            'native_content_sha256': native_content_sha256,
        }
        prior = db.execute(
            "SELECT payload FROM governance_event WHERE invocation_id=? AND kind='publication_receipt_verified'",
            (invocation_id,)).fetchall()
        if not any(json.loads(value[0]) == proof for value in prior):
            ledger._event(db, event['project_id'], invocation_id, 'publication_receipt_verified',
                          'server-original-publication', proof)
        db.execute('COMMIT')
    from open_webui.utils import agentic_maintenance as maintenance

    admission_path = maintenance.path(Path(db_path).parent if db_path else Path(ledger.path()).parent)
    if maintenance.has_lease('governed', invocation_id, db_path=admission_path):
        proof = maintenance.governed_receipt(invocation_id, db_path or ledger.path())
        maintenance.settle_reference('governed', invocation_id, proof, db_path=admission_path)

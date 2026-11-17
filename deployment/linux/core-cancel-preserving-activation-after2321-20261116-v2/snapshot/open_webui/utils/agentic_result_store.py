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
        return (
            db.execute(
                "UPDATE governance_result SET phase='uncertain',updated_at=? WHERE invocation_id=? AND phase='pending'",
                (int(time.time()), invocation_id),
            ).rowcount
            == 1
        )


def publication_receipt(invocation_id, receipt, db_path=None):
    with _db(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(
            'SELECT receipt_json,event_json FROM governance_result WHERE invocation_id=?', (invocation_id,)
        ).fetchone()
        if not row:
            raise ValueError('No saved original result')
        event = json.loads(row[1])
        if (
            receipt.get('event_id') != event['event_id']
            or receipt.get('task_id') != event['task_id']
            or not receipt.get('modified')
            or not receipt.get('status')
        ):
            raise ValueError('ERP readback does not match original result')
        if row[0] and row[0] != canonical(receipt):
            raise ValueError('Original ERP receipt is immutable')
        db.execute(
            "UPDATE governance_result SET phase='published',receipt_json=?,updated_at=? WHERE invocation_id=?",
            (canonical(receipt), int(time.time()), invocation_id),
        )
        db.execute('COMMIT')
    from open_webui.utils import agentic_maintenance as maintenance

    admission_path = maintenance.path(Path(db_path).parent if db_path else Path(ledger.path()).parent)
    if maintenance.has_lease('governed', invocation_id, db_path=admission_path):
        proof = maintenance.governed_receipt(invocation_id, db_path or ledger.path())
        maintenance.settle_reference('governed', invocation_id, proof, db_path=admission_path)

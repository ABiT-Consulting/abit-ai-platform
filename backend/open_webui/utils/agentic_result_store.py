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


def save_local_result(invocation_id, run_id, result, db_path=None):
    """Preserve the returned contract before any new ERP mutation is permitted."""
    with _db(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute(
            'SELECT run_id,result_json FROM governance_local_result WHERE invocation_id=?', (invocation_id,)
        ).fetchone()
        value = (run_id, canonical(result))
        if prior and tuple(prior) != value:
            raise ValueError('Original local result is immutable')
        db.execute(
            'INSERT OR IGNORE INTO governance_local_result VALUES(?,?,?,?)', (invocation_id, *value, int(time.time()))
        )
        db.execute('COMMIT')


def save_result(invocation_id, run_id, result, event, db_path=None):
    with _db(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
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

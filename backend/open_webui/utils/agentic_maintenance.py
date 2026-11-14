"""Cross-process maintenance admission; never cancels or replays an execution.

The gate and lease insertion share one SQLite transaction. An unresolved lease
never expires: only an original, server-read receipt can settle it. Operators
close/open using an epoch CAS; an unlocked count is never a drain decision.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

_CURRENT = ContextVar('abit_maintenance_admission', default=None)
_BATCH = ContextVar('abit_maintenance_batch', default=None)
_UNSUBMITTED = ContextVar('abit_maintenance_unsubmitted', default=None)
_DOT_ORIGINAL = ContextVar('abit_maintenance_dot_original', default=None)
_AUTHORITY = object()
_OWNED = {}


class MaintenanceDenied(RuntimeError):
    pass


def path(data_dir=None):
    if data_dir is None:
        from open_webui.env import DATA_DIR

        data_dir = DATA_DIR
    return Path(data_dir) / 'maintenance-admission' / 'gate.sqlite'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def _protected(target, create):
    target = Path(target)
    parent = target.parent
    if create:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.resolve() != parent or not parent.is_dir():
        raise MaintenanceDenied('Maintenance state requires its protected real directory')
    info = parent.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise MaintenanceDenied('Maintenance state directory owner/mode differs')
    if create:
        fd = os.open(target, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        os.close(fd)
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise MaintenanceDenied('Maintenance gate requires a private owned regular file')
    return target


def _schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS gate(id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL,
        epoch INTEGER NOT NULL,command_id TEXT NOT NULL,actor TEXT NOT NULL);
      INSERT OR IGNORE INTO gate VALUES(1,'open',0,'bootstrap','service');
      CREATE TABLE IF NOT EXISTS lease(id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference TEXT NOT NULL,
        token_sha256 TEXT NOT NULL,epoch INTEGER NOT NULL,pid INTEGER NOT NULL,process_start TEXT NOT NULL,
        created_ns INTEGER NOT NULL,state TEXT NOT NULL,parent_id TEXT,receipt_sha256 TEXT);
      CREATE UNIQUE INDEX IF NOT EXISTS one_live_admission ON lease(kind,reference)
        WHERE state IN ('active','unresolved');
      CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,payload TEXT NOT NULL,created_ns INTEGER NOT NULL);
      CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
        BEGIN SELECT RAISE(ABORT,'Maintenance audit is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
        BEGIN SELECT RAISE(ABORT,'Maintenance audit is immutable'); END;
    ''')


@contextmanager
def _db(db_path=None, *, write=True):
    target = _protected(db_path or path(), write)
    db = sqlite3.connect(
        target.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=15, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    try:
        if write:
            _schema(db)
            db.execute('BEGIN IMMEDIATE')
        else:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
        yield db
        if write:
            db.execute('COMMIT')
    except BaseException:
        if write and db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()


def _audit(db, kind, value, event_id=None):
    db.execute(
        'INSERT INTO audit(event_id,kind,payload,created_ns) VALUES(?,?,?,?)',
        (event_id or secrets.token_hex(24), kind, json.dumps(value, sort_keys=True), time.time_ns()),
    )


def _start():
    return Path('/proc/self/stat').read_text().split(') ', 1)[1].split()[19]


def _snapshot(db):
    gate = dict(db.execute('SELECT * FROM gate WHERE id=1').fetchone())
    counts = {
        r[0]: r[1]
        for r in db.execute("SELECT state,count(*) FROM lease WHERE state IN ('active','unresolved') GROUP BY state")
    }
    return {
        **gate,
        'active_admissions': sum(counts.values()),
        'lease_states': counts,
        'drained': gate['state'] == 'sealed' and not counts,
    }


def status(db_path=None):
    target = Path(db_path or path())
    if not target.exists():
        return {'state': 'uninitialised', 'epoch': None, 'active_admissions': None, 'drained': False}
    with _db(target, write=False) as db:
        return _snapshot(db)


def has_lease(kind, reference, *, db_path=None):
    target = Path(db_path or path())
    if not target.exists():
        return False
    with _db(target, write=False) as db:
        return bool(
            db.execute(
                "SELECT 1 FROM lease WHERE kind=? AND reference=? AND state IN ('active','unresolved')",
                (kind, reference),
            ).fetchone()
        )


def require_active(kind, reference, *, db_path=None):
    """Broker-side check for a bound job, without handing its lease token out."""
    target = Path(db_path or path())
    if not target.exists():
        raise MaintenanceDenied('No original maintenance admission exists')
    with _db(target, write=False) as db:
        gate = db.execute('SELECT state FROM gate WHERE id=1').fetchone()
        row = db.execute(
            "SELECT id,epoch,pid,process_start FROM lease WHERE kind=? AND reference=? AND state='active'",
            (kind, reference),
        ).fetchone()
        if not row or gate[0] == 'sealed':
            raise MaintenanceDenied('The exact original admission is not active')
        try:
            start = Path('/proc/' + str(row['pid']) + '/stat').read_text().split(') ', 1)[1].split()[19]
        except OSError:
            raise MaintenanceDenied('Original admission process is unavailable; preserve its receipt') from None
        if start != row['process_start']:
            raise MaintenanceDenied('Original admission process identity changed')
        return {'lease_id': row['id'], 'kind': kind, 'reference': reference, 'epoch': row['epoch']}


def control(action, *, expected_epoch, command_id, actor, db_path=None):
    if action not in {'close', 'seal', 'open'} or not actor or not command_id or len(command_id) > 160:
        raise ValueError('Exact maintenance action, command and operator are required')
    with _db(db_path) as db:
        original = db.execute('SELECT payload FROM audit WHERE event_id=?', (command_id,)).fetchone()
        body = {'action': action, 'expected_epoch': expected_epoch, 'actor': actor}
        if original:
            if json.loads(original[0]) != body:
                raise MaintenanceDenied('Maintenance command identity changed')
            return _snapshot(db)
        gate = db.execute('SELECT * FROM gate WHERE id=1').fetchone()
        if type(expected_epoch) is not int or expected_epoch != gate['epoch']:
            raise MaintenanceDenied('Maintenance epoch changed; preserve current admission state')
        if action == 'close' and gate['state'] != 'open':
            raise MaintenanceDenied('Close requires the current open admission epoch')
        if action == 'seal' and (
            gate['state'] != 'draining'
            or db.execute("SELECT 1 FROM lease WHERE state IN ('active','unresolved') LIMIT 1").fetchone()
        ):
            raise MaintenanceDenied('Seal requires closed admission and zero unresolved leases in this transaction')
        db.execute(
            'UPDATE gate SET state=?,epoch=epoch+1,command_id=?,actor=? WHERE id=1',
            ({'close': 'draining', 'seal': 'sealed', 'open': 'open'}[action], command_id, actor),
        )
        _audit(db, 'operator_control', body, command_id)
        return _snapshot(db)


class Receipt:
    def __init__(self, kind, reference, digest, authority=None):
        if authority is not _AUTHORITY:
            raise MaintenanceDenied('A server-read original receipt is required')
        self.kind, self.reference, self.digest = kind, reference, digest


def _proof(kind, reference, value):
    return Receipt(kind, reference, _digest(value), _AUTHORITY)


class Admission:
    def __init__(self, ident, token, kind, reference, database, recovering=False):
        self.id, self.token, self.kind, self.reference, self.database = ident, token, kind, reference, Path(database)
        self.recovering = recovering

    def finish(self, receipt):
        if not isinstance(receipt, Receipt) or (receipt.kind, receipt.reference) != (self.kind, self.reference):
            raise MaintenanceDenied('Original receipt scope differs from admission')
        with _db(self.database) as db:
            row = db.execute('SELECT * FROM lease WHERE id=?', (self.id,)).fetchone()
            if not row or not secrets.compare_digest(
                row['token_sha256'], hashlib.sha256(self.token.encode()).hexdigest()
            ):
                raise MaintenanceDenied('Maintenance lease identity changed')
            if row['state'] == 'finished':
                if row['receipt_sha256'] != receipt.digest:
                    raise MaintenanceDenied('Original settlement receipt changed')
                return
            db.execute("UPDATE lease SET state='finished',receipt_sha256=? WHERE id=?", (receipt.digest, self.id))
            _audit(
                db,
                'original_receipt_settled',
                {'lease': self.id, 'kind': self.kind, 'reference': self.reference, 'receipt_sha256': receipt.digest},
            )
        cached = _OWNED.get((str(self.database), self.kind, self.reference))
        if cached and cached.id == self.id:
            _OWNED.pop((str(self.database), self.kind, self.reference), None)

    def unresolved(self):
        with _db(self.database) as db:
            db.execute("UPDATE lease SET state='unresolved' WHERE id=? AND state='active'", (self.id,))


def acquire(kind, reference, *, db_path=None, parent=None, recovery_receipt=None):
    database = Path(db_path or path())
    if (
        kind not in {'automation', 'governed', 'erp', 'qa', 'batch'}
        or not isinstance(reference, str)
        or not reference
        or len(reference) > 200
    ):
        raise ValueError('Bounded maintenance admission scope required')
    with _db(database) as db:
        gate = db.execute('SELECT * FROM gate WHERE id=1').fetchone()
        transferred = False
        if parent is not None:
            row = db.execute('SELECT * FROM lease WHERE id=?', (parent.id,)).fetchone()
            transferred = bool(
                row
                and row['kind'] == 'batch'
                and row['state'] == 'active'
                and row['pid'] == os.getpid()
                and row['process_start'] == _start()
                and secrets.compare_digest(row['token_sha256'], hashlib.sha256(parent.token.encode()).hexdigest())
            )
            if not transferred:
                raise MaintenanceDenied('Original scheduler batch admission changed')
        recovering = isinstance(recovery_receipt, Receipt) and (recovery_receipt.kind, recovery_receipt.reference) == (
            kind,
            reference,
        )
        if gate['state'] == 'sealed' or gate['state'] != 'open' and not transferred and not recovering:
            raise MaintenanceDenied('Maintenance admission is closed; original work is preserved')
        existing = db.execute(
            "SELECT * FROM lease WHERE kind=? AND reference=? AND state IN ('active','unresolved')", (kind, reference)
        ).fetchone()
        if existing:
            if not recovering or existing['state'] != 'unresolved':
                raise MaintenanceDenied('An original admission remains active or unresolved')
            db.execute(
                "UPDATE lease SET state='finished',receipt_sha256=? WHERE id=?",
                (recovery_receipt.digest, existing['id']),
            )
            _audit(
                db,
                'original_receipt_settled',
                {
                    'lease': existing['id'],
                    'kind': kind,
                    'reference': reference,
                    'receipt_sha256': recovery_receipt.digest,
                },
            )
        ident, token = secrets.token_hex(24), secrets.token_hex(32)
        db.execute(
            'INSERT INTO lease VALUES(?,?,?,?,?,?,?,?,?,?,NULL)',
            (
                ident,
                kind,
                reference,
                hashlib.sha256(token.encode()).hexdigest(),
                gate['epoch'],
                os.getpid(),
                _start(),
                time.time_ns(),
                'active',
                parent.id if transferred else None,
            ),
        )
        _audit(
            db,
            'admitted_original_recovery' if recovering else 'admitted',
            {
                'lease': ident,
                'kind': kind,
                'reference': reference,
                'epoch': gate['epoch'],
                'parent': parent.id if transferred else None,
                'original_receipt': recovery_receipt.digest if recovering else None,
            },
        )
    lease = Admission(ident, token, kind, reference, database, recovering)
    _OWNED[(str(database), kind, reference)] = lease
    return lease


def continue_owned(kind, reference, *, db_path=None):
    """Normal open-gate polling keeps the same in-process original lease."""
    database = Path(db_path or path())
    lease = _OWNED.get((str(database), kind, reference))
    if not lease:
        raise MaintenanceDenied('Original admission token is unavailable; receipt recovery required')
    with _db(database) as db:
        gate = db.execute('SELECT state FROM gate WHERE id=1').fetchone()
        row = db.execute('SELECT * FROM lease WHERE id=?', (lease.id,)).fetchone()
        if (
            gate[0] != 'open'
            or not row
            or row['state'] != 'unresolved'
            or row['pid'] != os.getpid()
            or row['process_start'] != _start()
            or not secrets.compare_digest(row['token_sha256'], hashlib.sha256(lease.token.encode()).hexdigest())
        ):
            raise MaintenanceDenied('Original continuation is not admitted; preserve its receipt and identity')
        db.execute("UPDATE lease SET state='active' WHERE id=?", (lease.id,))
        _audit(db, 'continued_same_admission', {'lease': lease.id, 'kind': kind, 'reference': reference})
    lease.recovering = False
    return lease


def _root(database):
    return Path(database).parent.parent


def _readonly(database):
    db = sqlite3.connect(Path(database).as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def automation_receipt(automation_id, *, db_path=None, run_id=None, unsubmitted=None):
    database = Path(db_path or path())
    current = _CURRENT.get()
    if not current or current.kind != 'automation' or current.reference != automation_id:
        return None
    with _readonly(_root(database) / 'webui.db') as db:
        row = db.execute(
            'SELECT id,automation_id,status,chat_id,created_at FROM automation_run WHERE automation_id=? '
            + ('AND id=? ' if run_id else '')
            + 'ORDER BY created_at DESC,id DESC LIMIT 1',
            (automation_id, run_id) if run_id else (automation_id,),
        ).fetchone()
    if not row:
        return None
    with _db(database, write=False) as db:
        lease = db.execute(
            'SELECT min(l.created_ns,coalesce(p.created_ns,l.created_ns)) FROM lease l LEFT JOIN lease p ON p.id=l.parent_id WHERE l.id=?',
            (current.id,),
        ).fetchone()
    if not lease or row['created_at'] < lease[0]:
        return None
    if unsubmitted in {'owner_not_found', 'owner_not_authorized', 'chat_not_created', 'dot_context_rejected'}:
        if row['status'] not in {'error', 'deferred'}:
            raise MaintenanceDenied('Pre-submission receipt is not a committed rejection')
        return _proof('automation', automation_id, {'run': dict(row), 'never_submitted_branch': unsubmitted})
    if row['status'] == 'success' and row['chat_id']:
        return _proof('automation', automation_id, dict(row))
    dot = _root(database) / 'abitdot-primary.sqlite'
    if not dot.exists():
        return None
    linked = _DOT_ORIGINAL.get()
    if linked is not None and linked.get('automation_id') != automation_id:
        raise MaintenanceDenied('Original Dot server receipt belongs to another automation')
    original_run = linked['run_id'] if linked is not None else row['id']
    with _readonly(dot) as db:
        rows = db.execute(
            'SELECT id,owner,automation_id,original_run_id,dot_id,status,envelope_json,receipt,response_text,reason '
            'FROM dot_primary_runs WHERE automation_id=? AND original_run_id=?',
            (automation_id, original_run),
        ).fetchall()
    if len(rows) != 1:
        return None
    original = dict(rows[0])
    envelope = json.loads(original['envelope_json'])
    identity = {
        'owner_id': original['owner'],
        'automation_id': automation_id,
        'run_id': original_run,
        'dot_id': original['dot_id'],
        'idempotency_key': original['id'],
    }
    if (
        any(envelope.get(k) != v for k, v in identity.items())
        or original['id'] != _digest([original['owner'], automation_id, original_run])
        or linked is not None
        and any(linked.get(k) != v for k, v in identity.items())
    ):
        raise MaintenanceDenied('Exact original Dot receipt identity differs')
    never_sent = (original['status'], original['reason']) in {
        ('prepared', 'verified_pre_submission_busy'),
        ('unavailable', 'verified_pre_submission_rejection'),
    }
    completed_reply = (
        original['status'] == 'delegated'
        and isinstance(original['receipt'], str)
        and len(original['receipt']) >= 8
        and bool(original['response_text'])
    )
    if never_sent or completed_reply:
        return _proof(
            'automation',
            automation_id,
            {'run': dict(row), 'dot_state': original['status'], 'original_sha256': _digest(original)},
        )
    return None


def governed_receipt(invocation, ledger_path):
    with _readonly(ledger_path) as db:
        row = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (invocation,)).fetchone()
    if not row or row['phase'] != 'published' or not row['receipt_json']:
        return None
    event, receipt = json.loads(row['event_json']), json.loads(row['receipt_json'])
    if (
        event.get('invocation_id') != invocation
        or row['run_id'] != event.get('run_id')
        or receipt.get('durable') is not True
        or not receipt.get('modified')
        or not receipt.get('status')
        or any(receipt.get(k) != event.get(k) for k in ('event_id', 'task_id', 'run_id'))
    ):
        raise MaintenanceDenied('Original governed publication receipt differs')
    return _proof('governed', invocation, {'event_sha256': _digest(event), 'receipt': receipt})


def unsubmitted_receipt(invocation, ledger_path):
    """Read the immutable owned-run rejection event, never a caller boolean."""
    with _readonly(ledger_path) as db:
        row = db.execute(
            'SELECT automation_run_id,submitted_at,state,dispatch_attempts FROM governance_invocation WHERE invocation_id=?',
            (invocation,),
        ).fetchone()
        event = db.execute(
            "SELECT event_id,payload,event_hash FROM governance_event WHERE invocation_id=? AND kind IN ('pre_submission_queued','pre_submission_blocked') ORDER BY seq DESC LIMIT 1",
            (invocation,),
        ).fetchone()
    if not row or not event or row['submitted_at'] is not None or row['state'] not in {'queued', 'blocked'}:
        raise MaintenanceDenied('Exact original pre-submission rejection is unavailable')
    body = json.loads(event['payload'])
    if (
        body.get('proof') not in {'not_called', 'relay_busy_before_submission', 'rejected_before_submission'}
        or body.get('attempt') != row['dispatch_attempts']
    ):
        raise MaintenanceDenied('Original rejection journal differs from its owned attempt')
    return _proof(
        'governed',
        invocation,
        {'run_id': row['automation_run_id'], 'event_id': event['event_id'], 'event_hash': event['event_hash']},
    )


def settle_reference(kind, reference, receipt, *, db_path=None):
    if not isinstance(receipt, Receipt) or (receipt.kind, receipt.reference) != (kind, reference):
        raise MaintenanceDenied('Exact original settlement receipt required')
    with _db(db_path) as db:
        rows = db.execute(
            "SELECT id FROM lease WHERE kind=? AND reference=? AND state IN ('active','unresolved')", (kind, reference)
        ).fetchall()
        for row in rows:
            db.execute("UPDATE lease SET state='finished',receipt_sha256=? WHERE id=?", (receipt.digest, row['id']))
            _audit(
                db,
                'original_receipt_settled',
                {'lease': row['id'], 'kind': kind, 'reference': reference, 'receipt_sha256': receipt.digest},
            )
    return len(rows)


@contextmanager
def automation_batch(*, db_path=None):
    lease = acquire('batch', secrets.token_hex(24), db_path=db_path)
    token = _BATCH.set(lease)
    try:
        yield lease
    finally:
        _BATCH.reset(token)
        with _db(lease.database, write=False) as db:
            children = [r[0] for r in db.execute('SELECT id FROM lease WHERE parent_id=? ORDER BY id', (lease.id,))]
        lease.finish(_proof('batch', lease.reference, {'transferred_children': children}))


async def _run(lease, factory):
    token = _CURRENT.set(lease)
    try:
        return await factory()
    finally:
        _CURRENT.reset(token)
        lease.unresolved()  # finished receipts remain finished; errors stay fenced


def spawn_automation(factory, automation_id):
    parent = _BATCH.get()
    lease = acquire('automation', automation_id, parent=parent, db_path=parent.database if parent else None)
    try:
        return asyncio.create_task(_run(lease, factory))
    except BaseException:
        lease.unresolved()
        raise


def guard_automation(fn):
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs).arguments
        automation = bound['automation']
        if (automation.meta or {}).get('governance_managed') is True:
            return await fn(*args, **kwargs)
        current = _CURRENT.get()
        if current and current.kind == 'automation' and current.reference == automation.id:
            return await fn(*args, **kwargs)
        lease = acquire('automation', automation.id)
        return await _run(lease, lambda: fn(*args, **kwargs))

    return wrapped


def record_automation_receipt(fn):
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapped(*args, **kwargs):
        result = await fn(*args, **kwargs)
        bound = signature.bind(*args, **kwargs).arguments
        current = _CURRENT.get()
        if current:
            proof = automation_receipt(
                bound['automation_id'],
                db_path=current.database,
                run_id=bound.get('scheduled_run_id'),
                unsubmitted=_UNSUBMITTED.get(),
            )
            if proof:
                current.finish(proof)
        return result

    return wrapped


async def record_pre_submission(reason, record, *args, **kwargs):
    """Only fixed server branches known to precede provider submission call this."""
    if reason not in {'owner_not_found', 'owner_not_authorized', 'chat_not_created', 'dot_context_rejected'}:
        raise MaintenanceDenied('Unsupported pre-submission receipt branch')
    token = _UNSUBMITTED.set(reason)
    try:
        return await record(*args, **kwargs)
    finally:
        _UNSUBMITTED.reset(token)


async def record_dot_outcome(envelope, record, *args, **kwargs):
    """Bind a manual run's server-created Dot ID to its committed run row."""
    keys = ('owner_id', 'automation_id', 'run_id', 'dot_id', 'idempotency_key')
    if not isinstance(envelope, dict) or any(not isinstance(envelope.get(k), str) or not envelope[k] for k in keys):
        raise MaintenanceDenied('Exact server-created original Dot identity is required')
    token = _DOT_ORIGINAL.set({k: envelope[k] for k in keys})
    try:
        return await record(*args, **kwargs)
    finally:
        _DOT_ORIGINAL.reset(token)


def _qa_proof(store, task, run):
    with _readonly(store.path) as db:
        rows = db.execute(
            'SELECT task,run,binding,phase,status FROM review WHERE task=? AND run=?', (task, run)
        ).fetchall()
    reference = task + ':' + run
    if not rows:
        return _proof('qa', reference, {'no_review_claim': True})
    if any(row['phase'] not in {'publish', 'done'} or row['status'] not in {'pass', 'fail', 'blocked'} for row in rows):
        return None
    return _proof('qa', reference, [dict(row) for row in rows])


def qa_claim(fn):
    @wraps(fn)
    def wrapped(store, task, run, binding):
        lease = _CURRENT.get()
        if not lease or lease.kind != 'qa' or lease.reference != task + ':' + run or lease.recovering:
            raise MaintenanceDenied(
                'QA claim requires its admitted exact new original; receipt recovery cannot start tests'
            )
        return fn(store, task, run, binding)

    return wrapped


def legacy_qa(fn):
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapped(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        task = values.get('task_id') or values['todo']['reference_name']
        store = values['store']
        database = path(Path(store.path).parent)
        if 'todo' in values:
            original = await values['erp'].get('Task', task)
            policy = values.get('policy') or {}
            run = fn.__globals__['assigned_review_id'](original, values['todo']['name'], policy.get('attempt'))
        else:
            candidate = await fn.__globals__['latest_run'](values['resolver'], task, values['owner_id'])
            if not candidate:
                return None
            run = candidate['run']
        proof = _qa_proof(store, task, run)
        # Only a persisted publish/done receipt may enter while closed. An
        # absent claim is not permission to start a new QA turn during drain.
        with _readonly(store.path) as db:
            known = db.execute(
                "SELECT 1 FROM review WHERE task=? AND run=? AND phase IN ('publish','done') LIMIT 1", (task, run)
            ).fetchone()
        try:
            lease = acquire('qa', task + ':' + run, db_path=database, recovery_receipt=proof if known else None)
        except MaintenanceDenied:
            lease = continue_owned('qa', task + ':' + run, db_path=database)
        token = _CURRENT.set(lease)
        try:
            result = await fn(*args, **kwargs)
            proof = _qa_proof(store, task, run)
            if proof:
                lease.finish(proof)
            return result
        finally:
            _CURRENT.reset(token)
            lease.unresolved()

    return wrapped


def legacy_erp(fn):
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapped(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        resolver = values.get('resolver') or values.get('self')
        task = values.get('task') or values.get('task_id') or values.get('run')
        if isinstance(task, dict):
            task = task.get('task') or task.get('name')
        if not isinstance(task, str):
            raise MaintenanceDenied('Exact ERP intake Task scope required')
        database = path(Path(resolver.store.path).parent)
        current = _CURRENT.get()
        if current and current.kind == 'erp' and current.reference == task:
            if current.recovering and fn.__name__ != 'advance':
                raise MaintenanceDenied('Original terminal ERP receipt cannot reopen or dispatch another run')
            return await fn(*args, **kwargs)
        proof = _erp_terminal_receipt(resolver.store, task) if fn.__name__ == 'advance' else None
        if proof and isinstance(values.get('run'), dict):
            persisted = resolver.store.get(task)
            if _digest(persisted) != _digest(values['run']):
                raise MaintenanceDenied('Terminal ERP recovery differs from the exact persisted original')
        try:
            lease = acquire('erp', task, db_path=database, recovery_receipt=proof)
        except MaintenanceDenied:
            lease = continue_owned('erp', task, db_path=database)
        token = _CURRENT.set(lease)
        try:
            result = await fn(*args, **kwargs)
            value = resolver.store.get(task)
            if value and value.get('synced') is True:
                lease.finish(_proof('erp', task, {'original_state_sha256': _digest(value)}))
            elif not value or not value.get('attempts'):
                # The pinned standard-agent intake only arms its persistent
                # Automation. Actual execution has its own admitted child.
                lease.finish(
                    _proof(
                        'erp',
                        task,
                        {
                            'intake_returned': fn.__name__,
                            'revision': values.get('revision'),
                            'no_external_attempt': True,
                        },
                    )
                )
            return result
        finally:
            _CURRENT.reset(token)
            lease.unresolved()

    return wrapped


def _erp_terminal_receipt(store, task):
    """Only the trusted stored terminal original may enter for publication."""
    if not Path(store.path).exists():
        return None
    with _readonly(store.path) as db:
        row = db.execute('SELECT state FROM erp_runs WHERE task=?', (task,)).fetchone()
    if not row:
        return None
    value = json.loads(row['state'])
    attempts = value.get('attempts')
    if (
        value.get('task') != task
        or value.get('status') not in {'Completed', 'Pending Review', 'Failed', 'Cancelled'}
        or not isinstance(attempts, list)
        or len(attempts) > 3
        or any(not a.get('finished') or a.get('phase') not in {'verified', 'stopped'} for a in attempts)
    ):
        return None
    return _proof('erp', task, {'original_terminal_state_sha256': _digest(value)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'close', 'seal', 'open'])
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--expected-epoch', type=int)
    parser.add_argument('--command-id')
    parser.add_argument('--actor')
    args = parser.parse_args()
    database = path(args.data_dir)
    result = (
        status(database)
        if args.action == 'status'
        else control(
            args.action,
            expected_epoch=args.expected_epoch,
            command_id=args.command_id,
            actor=args.actor,
            db_path=database,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

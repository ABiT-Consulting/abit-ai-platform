"""Cross-stack ownership checks sharing the existing ERP status submission lock."""

from contextlib import asynccontextmanager
import json
import hashlib
import uuid
from pathlib import Path
import sqlite3


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _published_delivery(root, task, run, envelope, owner, automation):
    """Recognize a settled governed Developer result; never change provider state."""
    governance, webui = root / 'agentic-governance.sqlite', root / 'webui.db'
    if not governance.is_file() or not webui.is_file():
        return False
    db = None
    app = None
    try:
        db = sqlite3.connect('file:' + str(governance) + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = db.execute('SELECT * FROM governance_invocation WHERE automation_run_id=?', (run,)).fetchall()
        if len(rows) != 1:
            return False
        row = dict(rows[0])
        # Claim attempts include never-submitted preflight retries (ledger cap3).
        if (row['state'] not in {'handed_off', 'verified'} or row['target_role'] != 'developer'
                or row['erp_task'] != task or row['agent_id'] != automation
                or row['submitted_at'] is None or type(row['dispatch_attempts']) is not int
                or not 1 <= row['dispatch_attempts'] <= 3
                or run != str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://abit.ai/agentic/run/' + row['invocation_id']))):
            return False
        packet = json.loads(row['work_packet'])
        binding = packet['binding']
        meta = envelope.get('task', {}).get('meta') or {}
        key = hashlib.sha256(json.dumps([owner, automation, run], separators=(',', ':')).encode()).hexdigest()
        if (envelope.get('owner_id') != owner or envelope.get('automation_id') != automation
                or envelope.get('run_id') != run or envelope.get('idempotency_key') != key
                or meta.get('governance_managed') is not True or meta.get('erp_task') != task
                or meta.get('governance_project') != row['erp_project'] or meta.get('erp_role') != 'developer'
                or packet.get('project_id') != row['erp_project'] or packet.get('task_id') != task
                or packet.get('task_revision') != row['task_revision']
                or binding.get('project_id') != row['erp_project'] or binding.get('role') != 'developer'
                or binding.get('agent_id') != automation or _digest(packet) != row['packet_hash']
                or packet.get('content_sha256') != _digest({k: v for k, v in packet.items() if k != 'content_sha256'})
                or meta.get('governance_packet_digest') != packet['content_sha256']):
            return False
        saved = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
        if not saved or saved['phase'] != 'published' or saved['run_id'] != run:
            return False
        result, event, receipt = (json.loads(saved[name] or 'null')
                                  for name in ('result_json', 'event_json', 'receipt_json'))
        if (not isinstance(receipt, dict) or _digest(result) != saved['result_sha256']
                or result.get('outcome') != 'ready_for_review' or result.get('actor') != binding
                or any(result.get(k) != v for k, v in {'invocation_id': row['invocation_id'],
                    'project_id': row['erp_project'], 'task_id': task, 'task_revision': row['task_revision'],
                    'work_packet_digest': packet['content_sha256']}.items())
                or event.get('type') != 'run.ready' or event.get('result_contract') != result
                or any(event.get(k) != v for k, v in {'invocation_id': row['invocation_id'], 'run_id': run,
                    'project_id': row['erp_project'], 'task_id': task, 'task_revision': row['task_revision'],
                    'agent_id': automation, 'role': 'developer', 'work_packet_digest': packet['content_sha256']}.items())
                or event.get('event_id') != 'agov-result-' + _digest([row['invocation_id'], result])[:48]
                or receipt.get('durable') is not True or receipt.get('status') != 'Pending Review'
                or not receipt.get('modified') or any(receipt.get(k) != v for k, v in {
                    'event_id': event['event_id'], 'run_id': run, 'task_id': task}.items())):
            return False
        unsettled = db.execute("""SELECT 1 FROM governance_gateway_call c
            JOIN governance_gateway_session s ON s.session_id=c.session_id
            WHERE s.invocation_id=? AND c.state!='completed' LIMIT 1""", (row['invocation_id'],)).fetchone()
        if unsettled:
            return False
        app = sqlite3.connect('file:' + str(webui) + '?mode=ro', uri=True)
        app.row_factory = sqlite3.Row
        app.execute('PRAGMA query_only=ON')
        app.execute('BEGIN')
        completed = app.execute("""SELECT r.status,r.automation_id,a.user_id,a.meta
            FROM automation_run r JOIN automation a ON a.id=r.automation_id WHERE r.id=?""", (run,)).fetchall()
        if len(completed) != 1:
            return False
        completed = dict(completed[0])
        registration = json.loads(completed['meta'] or '{}')
        return (completed['status'] == 'success' and completed['automation_id'] == automation
                and completed['user_id'] == owner and registration.get('governance_managed') is True
                and registration.get('agentic_project') == row['erp_project']
                and registration.get('agentic_role') == 'developer'
                and registration.get('erp_role_user') == binding.get('erp_user'))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return False  # Missing or inconsistent proof keeps the old ownership veto.
    finally:
        for connection in (app, db):
            if connection is not None:
                connection.rollback()
                connection.close()


def external_execution(task, data_dir, *, own_run=None):
    root = Path(data_dir)
    webui = root / 'webui.db'
    if webui.is_file():
        with sqlite3.connect('file:' + str(webui) + '?mode=ro', uri=True) as db:
            for run, meta in db.execute(
                "SELECT r.id,a.meta FROM automation_run r JOIN automation a ON a.id=r.automation_id WHERE r.status='running'"
            ):
                value = json.loads(meta or '{}')
                # Optional SQLAlchemy JSON metadata may be the JSON literal null.
                # It has no ERP Task binding, just like a SQL NULL. Other invalid
                # shapes keep the ownership check closed with a useful error.
                if value is None:
                    value = {}
                if not isinstance(value, dict):
                    raise ValueError('Active automation metadata is invalid; reconcile the original automation')
                if run != own_run and value.get('erp_task') == task:
                    return 'An existing ERP automation owns this task'
    dot = root / 'abitdot-primary.sqlite'
    if dot.is_file():
        with sqlite3.connect('file:' + str(dot) + '?mode=ro', uri=True) as db:
            for run, env, status, owner, automation in db.execute(
                "SELECT original_run_id,envelope_json,status,owner,automation_id FROM dot_primary_runs WHERE status IN ('prepared','dispatching','unknown')"
            ):
                value = json.loads(env)
                if run != own_run and (value.get('task', {}).get('meta') or {}).get('erp_task') == task:
                    # A durable published delivery can release cross-stack ownership
                    # while a retained provider UNKNOWN record stays immutable.
                    if status == 'unknown' and _published_delivery(root, task, run, value, owner, automation):
                        continue
                    return 'An original ERP/Dot request is active or unresolved'
    managed = root / 'erp-task-resolution.sqlite'
    if managed.is_file():
        with sqlite3.connect('file:' + str(managed) + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT state FROM erp_runs WHERE task=?', (task,)).fetchone()
            if row:
                state = json.loads(row[0])
                if not state.get('synced') and state.get('attempts'):
                    return 'An existing managed ERP attempt requires original-run reconciliation'
    return None


@asynccontextmanager
async def submission_guard(row, db_path=None):
    from open_webui.env import DATA_DIR
    from open_webui.utils.erp_status_write import task_write_lock

    async with task_write_lock(row['erp_task']):
        reason = external_execution(
            row['erp_task'], Path(db_path).parent if db_path else DATA_DIR, own_run=row.get('automation_run_id')
        )
        if reason:
            raise ValueError(reason)
        yield

"""Read-only logical history guard; completed rows are evidence, not activity.

Only hashes/counts leave this helper. Optional archives retain selected full
rows privately for code-only rollback review; no database restore is provided.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import stat

TABLES = {
    'agentic-governance.sqlite': None,
    'maintenance-admission/gate.sqlite': None,
    'abitdot-primary.sqlite': ('dot_primary_runs',),
    'aibit-chatgpt-relay.sqlite': ('jobs',),
    'erp-task-resolution.sqlite': ('erp_runs',),
    'erp-pending-review-qa.sqlite': ('review',),
    'webui.db': ('automation_run',),
}
TERMINAL = {'completed', 'failed', 'blocked', 'cancelled', 'handed_off', 'verified'}
ROW_LIMIT = 100_000
TABLE_BYTES = 64 * 1024 * 1024


def need(value, code):
    if not value:
        raise RuntimeError(code)


def canonical(value):
    def binary(row):
        if isinstance(row, bytes):
            return {'sqlite_blob_hex': row.hex()}
        raise TypeError('Unsupported SQLite value')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False, default=binary).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def private_new(path, raw):
    info = path.parent.lstat()
    need(path.parent.resolve() == path.parent and stat.S_ISDIR(info.st_mode)
         and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
         'ROOT_PRIVATE_ARCHIVE_DIRECTORY_REQUIRED')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(hold, archive=None):
    """Hash every governed/gate row and fixed legacy execution journals.

    A transaction spans each individual database. Calling twice around the
    private archive proves cross-database stability while the gate is sealed.
    No requirement that any historical table is empty is introduced.
    """
    result = {}
    for number, (name, selected) in enumerate(TABLES.items()):
        with closing(hold.db(name)) as db:
            db.execute('BEGIN')
            schema = [tuple(row) for row in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
            available = {row[1] for row in schema if row[0] == 'table'}
            names = sorted(available) if selected is None else list(selected)
            need(set(names) <= available, 'REQUIRED_HISTORY_TABLE_MISSING')
            tables, full = {}, {}
            for table in names:
                need(re.fullmatch('[A-Za-z0-9_]+', table), 'STATIC_SQLITE_TABLE_IDENTITY_REQUIRED')
                rows = db.execute('SELECT * FROM "' + table + '" LIMIT ?', (ROW_LIMIT + 1,)).fetchall()
                need(len(rows) <= ROW_LIMIT, 'HISTORY_ROW_BOUND_REQUIRES_REVIEW')
                columns = [row[1] for row in db.execute('PRAGMA table_info("' + table + '")')]
                encoded = sorted(canonical(list(row)) for row in rows)
                need(sum(map(len, encoded)) <= TABLE_BYTES, 'HISTORY_BYTE_BOUND_REQUIRES_REVIEW')
                raw = b'[' + b','.join(encoded) + b']'
                tables[table] = {'rows': len(rows), 'columns': columns, 'sha256': sha(raw)}
                if archive is not None:
                    full[table] = json.loads(raw)
            result[name] = {'schema_sha256': sha(canonical(schema)), 'tables': tables}
            if archive is not None:
                private_new(archive / ('history-' + str(number) + '.private.json'),
                            canonical({'database': name, 'schema': schema, 'tables': full}))
    return {'schema_version': 1, 'databases': result, 'sha256': sha(canonical(result)),
            'historical_rows_preserved': True, 'total_counts_required_zero': False}


def assert_idle(hold):
    """Only current work is an idle precondition; old evidence stays intact."""
    with closing(hold.db('webui.db')) as db:
        automation = db.execute("SELECT count(*) FROM automation_run WHERE status='running'").fetchone()[0]
    with closing(hold.db('erp-pending-review-qa.sqlite')) as db:
        reviews = db.execute("SELECT count(*) FROM review WHERE phase IN ('testing','publish')").fetchone()[0]
    retained = hold.retained_inventory()
    need(automation == reviews == retained['current_original_links'] == 0,
         'CURRENT_LEGACY_EXECUTION_OR_PUBLICATION_NOT_IDLE')
    return {'running_automations': automation, 'testing_or_publishing_legacy_qa': reviews,
            'current_original_provider_links': retained['current_original_links'],
            'historical_unknown_metadata_sha256': retained['metadata_sha256']}


def assert_closed_first_flow(hold, proof):
    """Future Root evidence must identify the actual closed PM and original runs."""
    need(proof.get('schema_version') == 1 and proof.get('purpose') == 'first-real-governed-flow-settled'
         and proof.get('state') == 'CLOSED_VERIFIED' and proof.get('callback_dedup_verified') is True
         and isinstance(proof.get('project_id'), str)
         and isinstance(proof.get('invocation_ids'), list) and 1 <= len(proof['invocation_ids']) <= 100
         and len(set(proof['invocation_ids'])) == len(proof['invocation_ids'])
         and all(isinstance(v, str) and len(v) <= 160 for v in proof['invocation_ids'])
         and all(re.fullmatch('[a-f0-9]{64}', str(proof.get(k, ''))) for k in
                 ('native_closing_receipt_sha256', 'verified_artifact_sha256')),
         'ACTUAL_CLOSED_FIRST_FLOW_PROOF_REQUIRED')
    with closing(hold.db('agentic-governance.sqlite')) as db:
        db.execute('BEGIN')
        rows = []
        for ident in proof['invocation_ids']:
            row = db.execute('SELECT invocation_id,erp_project,target_role,state,automation_run_id '
                             'FROM governance_invocation WHERE invocation_id=?', (ident,)).fetchone()
            need(row and row[1] == proof['project_id'] and row[3] in TERMINAL and row[4],
                 'FIRST_FLOW_ORIGINAL_INVOCATION_NOT_SETTLED')
            rows.append(tuple(row))
        pm = next((r for r in rows if r[0] == proof.get('closing_pm_invocation_id')), None)
        need(pm and pm[2] == 'project_manager' and pm[3] == 'completed'
             and pm[4] == proof.get('closing_pm_run_id'), 'ACTUAL_CLOSING_PM_ORIGINAL_NOT_COMPLETED')
        total = db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0]
        sessions = db.execute('SELECT count(*) FROM governance_gateway_session').fetchone()[0]
        need(total > 0 and sessions > 0, 'FIRST_REAL_FLOW_HISTORY_MUST_EXIST')
    return {'project_id': proof['project_id'], 'closed_invocations': len(rows),
            'originals_sha256': sha(canonical(rows)), 'historical_invocations': total,
            'historical_gateway_sessions': sessions}

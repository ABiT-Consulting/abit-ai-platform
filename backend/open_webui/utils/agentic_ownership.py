"""Cross-stack ownership checks sharing the existing ERP status submission lock."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3


def external_execution(task, data_dir, *, own_run=None):
    root = Path(data_dir)
    webui = root / 'webui.db'
    if webui.is_file():
        with sqlite3.connect('file:' + str(webui) + '?mode=ro', uri=True) as db:
            for run, meta in db.execute(
                "SELECT r.id,a.meta FROM automation_run r JOIN automation a ON a.id=r.automation_id WHERE r.status='running'"
            ):
                value = json.loads(meta or '{}')
                if run != own_run and value.get('erp_task') == task:
                    return 'An existing ERP automation owns this task'
    dot = root / 'abitdot-primary.sqlite'
    if dot.is_file():
        with sqlite3.connect('file:' + str(dot) + '?mode=ro', uri=True) as db:
            for run, env in db.execute(
                "SELECT original_run_id,envelope_json FROM dot_primary_runs WHERE status IN ('prepared','dispatching','unknown')"
            ):
                value = json.loads(env)
                if run != own_run and (value.get('task', {}).get('meta') or {}).get('erp_task') == task:
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

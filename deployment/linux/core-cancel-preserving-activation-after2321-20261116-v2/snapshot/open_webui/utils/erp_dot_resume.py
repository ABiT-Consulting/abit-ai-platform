"""Resume explicitly held, proven pre-submission runs under their original IDs.

The protected maintenance manifest is written only during a drained deployment.
No request/API parameter can supply it. Submitted or uncertain runs never resume
through this path. It creates no AutomationRun and consumes no extra attempt.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import time

ROOT = Path('/srv/abit-dev-linux/abit-ai')
MANIFEST = ROOT / 'config/erp-assignment/dot-maintenance-resume.json'
log = logging.getLogger(__name__)


def claim_held(record):
    with sqlite3.connect('file:' + str(ROOT / 'data/webui.db') + '?mode=rw', uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM automation WHERE id=?', (record['automation_id'],)).fetchone()
        if not row:
            return False
        meta = json.loads(row['meta'] or '{}') or {}
        if (row['user_id'] != record['owner'] or meta.get('erp_task') != record['task']
                or meta.get('erp_task_revision') != record['input_revision']
                or meta.get('erp_standard_agent') is not True or meta.get('erp_operator_stop')):
            return False
        latest = db.execute('SELECT id FROM automation_run WHERE automation_id=? ORDER BY created_at DESC LIMIT 1',
                            (record['automation_id'],)).fetchone()
        if not latest or latest['id'] != record['run_id']:
            return False
        if db.execute("SELECT 1 FROM automation_run WHERE automation_id=? AND status='running'",
                      (record['automation_id'],)).fetchone():
            return False
        with sqlite3.connect('file:' + str(ROOT / 'data/abitdot-primary.sqlite') + '?mode=ro', uri=True) as dot:
            # Any original receipt, even a prepared one, requires its own recovery
            # path. This maintenance resume is exclusively for never-submitted work.
            if dot.execute('SELECT 1 FROM dot_primary_runs WHERE owner=? AND automation_id=? AND original_run_id=?',
                           (record['owner'], record['automation_id'], record['run_id'])).fetchone():
                return False
        changed = db.execute("""UPDATE automation_run SET status='running',error=NULL,lease_until=?
            WHERE id=? AND automation_id=? AND status='deferred' AND error=? AND chat_id IS NULL""",
            (time.time_ns() + 600_000_000_000, record['run_id'], record['automation_id'], record['hold_error'])).rowcount
        return changed == 1


async def start_held_runs(app):
    from open_webui.utils.erp_dot_workflow import enabled, task_enabled
    if not enabled():
        return
    if not MANIFEST.is_file():
        return
    fd = os.open(MANIFEST, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o022 or info.st_size > 1_000_000):
            raise RuntimeError('ERP maintenance resume manifest is not protected')
        data = json.load(handle)
    if data.get('version') != 1 or not isinstance(data.get('records'), list):
        raise RuntimeError('Invalid protected ERP maintenance resume manifest')
    records = [r for r in data['records'] if task_enabled(r['task'])]

    async def resume_serially():
        from open_webui.models.automations import Automations
        from open_webui.utils.automations import execute_scheduled_automation
        for record in records:
            claimed = await asyncio.to_thread(claim_held, record)
            if claimed:
                automation = await Automations.get_by_id(record['automation_id'])
                if automation is None:
                    continue
                log.info('Resuming held ERP run %s without a new run or submission', record['run_id'])
                await execute_scheduled_automation(app, automation, record['run_id'])
            elif record.get('wait_for_qa') is not True:
                continue
            if record.get('wait_for_qa') is True:
                # The operator's verification task gets its independent QA turn
                # before the parked developer backlog consumes the shared slot.
                from open_webui.utils.erp_task_resolution import ERPClient
                config = json.loads((ROOT / 'config/erp-assignment/resolver.json').read_text())
                erp = ERPClient(config['erp_token_file'])
                try:
                    for _ in range(180):
                        try:
                            task = await erp.get('Task', record['task'])
                            if task.get('status') in {'QA APPROVED', 'Completed', 'Blocked', 'QA REJECTED', 'Cancelled'}:
                                break
                        except Exception:
                            log.warning('Waiting for existing ERP verification task state; no submission repeated')
                        await asyncio.sleep(10)
                finally:
                    await erp.client.aclose()
            # Let the existing independent QA scanner acquire the dispatch slot.
            await asyncio.sleep(2)
    app.state.erp_dot_resume_task = asyncio.create_task(resume_serially())

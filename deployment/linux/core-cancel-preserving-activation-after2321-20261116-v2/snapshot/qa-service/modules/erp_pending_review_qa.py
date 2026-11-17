"""Reconcile approved, independently tested ERP tasks after Pending Review."""
from open_webui.utils import agentic_maintenance as _agov_maintenance

from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from open_webui.internal.db import get_async_db_context
from open_webui.models.automations import Automation, AutomationRun
from open_webui.models.users import Users
from open_webui.utils.erp_task_delivery import DEFAULT_DELIVERY_TASKS, DEV_DELIVERY_FIELD
from open_webui.utils.erp_task_resolution import AGENT_URL, DETAIL, ERP, EXECUTION_ID, agent_url, task_audit, task_revision

log = logging.getLogger(__name__)
TASK_ID = re.compile(r'TASK-[A-Za-z0-9-]{1,64}')
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 3


class ReviewStore:
    def __init__(self, path):
        self.path = path
        fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError('QA evidence state must be a private regular file')
        finally:
            os.close(fd)
        with closing(self.open()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS review (
                task TEXT NOT NULL, run TEXT NOT NULL, binding TEXT NOT NULL,
                phase TEXT NOT NULL, status TEXT, summary TEXT,
                image1 BLOB, image2 BLOB, image3 BLOB, PRIMARY KEY (task, run)
            )""")

            columns = {row[1] for row in db.execute('PRAGMA table_info(review)')}
            if 'issues' not in columns:
                db.execute('ALTER TABLE review ADD COLUMN issues TEXT')

    def open(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA secure_delete=ON')
        return db

    def get(self, task, run):
        with closing(self.open()) as db:
            row = db.execute("""SELECT binding,phase,status,summary,issues,image1,image2,image3
                FROM review WHERE task=? AND run=?""", (task, run)).fetchone()
        if row:
            binding, phase, status, summary, issues, *screenshots = row
            return binding, phase, {'status': status, 'summary': summary, 'issues': json.loads(issues or '[]'),
                                    'screenshots': [image for image in screenshots if image is not None]}
        return None

    @_agov_maintenance.qa_claim
    def claim(self, task, run, binding):
        with closing(self.open()) as db, db:
            return bool(db.execute(
                "INSERT OR IGNORE INTO review(task,run,binding,phase) VALUES(?,?,?,'testing')",
                (task, run, binding),
            ).rowcount)

    def finish_test(self, task, run, outcome):
        images = [*outcome['screenshots'], None, None, None][:MAX_IMAGES]
        with closing(self.open()) as db, db:
            db.execute("""UPDATE review SET phase='publish',status=?,summary=?,issues=?,image1=?,image2=?,image3=?
                WHERE task=? AND run=? AND phase='testing'""",
                (outcome['status'], outcome['summary'], json.dumps(outcome.get('issues', [])), *images, task, run))

    def settle(self, task, run):
        with closing(self.open()) as db, db:
            db.execute("""UPDATE review SET phase='done',summary=NULL,image1=NULL,image2=NULL,image3=NULL
                WHERE task=? AND run=?""", (task, run))


async def latest_run(resolver, task_id, owner_id):
    agent_id = str(uuid5(NAMESPACE_URL, f'{ERP}/standard-agent/{task_id}'))
    legacy = resolver.store.get(task_id)
    if legacy and legacy.get('linked') and legacy.get('synced'):
        agent_id = legacy['agent']
    async with get_async_db_context() as db:
        agent = await db.get(Automation, agent_id)
        if agent is None or agent.user_id != owner_id:
            return None
        meta = agent.meta or {}
        if (meta.get('erp_standard_agent') is not True or meta.get('erp_standard_armed') is not True
                or meta.get('erp_managed') is True or meta.get('erp_task') != task_id
                or meta.get('erp_url') != f'{ERP}/desk/task/{task_id}'):
            return None
        result = await db.scalar(
            select(AutomationRun).where(AutomationRun.automation_id == agent_id)
            .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc()).limit(1)
        )
        synced = meta.get('erp_status_sync') or {}
        if (result is None or result.status != 'success' or result.id == meta.get('erp_ignore_run_id')
                or synced.get('run_id') != result.id or synced.get('status') != 'success'
                or not meta.get('erp_task_revision') or type(meta.get('erp_generation')) is not int):
            return None
        return {'agent': agent_id, 'run': result.id, 'created_at': result.created_at, 'chat_id': result.chat_id,
                'generation': meta['erp_generation'], 'revision': meta['erp_task_revision']}


def matches_task(task, candidate):
    if task.get('custom_agentic_managed'):
        return False
    audit = task_audit(task)
    return (
        task.get('status') == 'Pending Review' and not task.get('is_group') and not task.get('is_template')
        and task.get(AGENT_URL) == agent_url(candidate['agent'])
        and task.get(EXECUTION_ID) in (None, '', candidate['agent'])
        and audit.get('run_id') == candidate['run'] and audit.get('status') == 'Pending Review'
        and audit.get('generation') == candidate['generation']
        and task_revision(task) == candidate['revision'] == audit.get('task_revision')
        and isinstance(task.get('modified'), str) and bool(task['modified'])
    )


def requires_delivery(task, config):
    configured = config.get('delivery_tasks', {})
    return (not isinstance(configured, dict) or task['name'] in configured
            or task['name'] in DEFAULT_DELIVERY_TASKS
            or task.get(DEV_DELIVERY_FIELD) not in (None, False, 0, '', '0'))


def normalize_outcome(outcome):
    if not isinstance(outcome, dict) or outcome.get('status') not in ('pass', 'fail', 'blocked'):
        return {'status': 'blocked', 'summary': 'QA returned no valid verdict.', 'screenshots': []}
    status = outcome['status']
    summary = outcome.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        status, summary = 'blocked', 'QA returned no test summary.'
    images = outcome.get('screenshots')
    screenshots = [image for image in images[:MAX_IMAGES]
                   if isinstance(image, bytes) and 0 < len(image) <= MAX_IMAGE_BYTES
                   and image.startswith(b'\x89PNG\r\n\x1a\n')] if isinstance(images, list) else []
    if status == 'pass' and not screenshots:
        status = 'blocked'
        summary = 'QA reported a pass but produced no valid test screenshot. ' + summary
    return {'status': status, 'summary': summary.strip()[:4500], 'screenshots': screenshots,
            'issues': outcome.get('issues', [])}


@_agov_maintenance.legacy_qa
async def review_one(resolver, config, store, task_id, owner_id, policy, cutoff, run_review, publish_review):
    candidate = await latest_run(resolver, task_id, owner_id)
    if not candidate or candidate['created_at'] < cutoff * 1_000_000_000:
        return
    run_id = candidate['run']
    record = store.get(task_id, run_id)
    if record and record[1] == 'done':
        return
    erp = resolver.erp
    task = await erp.get('Task', task_id)
    if not matches_task(task, candidate) or not await resolver.authorized(task):
        if record:
            store.settle(task_id, run_id)
        return
    project = await erp.get('Project', task['project']) if task.get('project') else None
    binding = hashlib.sha256(json.dumps(
        [candidate, task['modified'], (project or {}).get('modified'), policy],
        sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    if record:
        if record[0] != binding:
            store.settle(task_id, run_id)
            return
        if record[1] == 'testing':
            # The previous process may have reached the development server. Do
            # not repeat an interrupted acceptance test with unknown side effects.
            store.finish_test(task_id, run_id, normalize_outcome({
                'status': 'blocked', 'summary': 'QA was interrupted before a verified result could be saved.',
            }))
    else:
        if not store.claim(task_id, run_id, binding):
            return
        outcome = normalize_outcome(await run_review(task, project, run_id, policy))
        store.finish_test(task_id, run_id, outcome)

    _, _, outcome = store.get(task_id, run_id)
    fresh = await erp.get('Task', task_id)
    if (not matches_task(fresh, candidate) or fresh['modified'] != task['modified']
            or not await resolver.authorized(fresh) or await latest_run(resolver, task_id, owner_id) != candidate):
        store.settle(task_id, run_id)
        return
    if outcome['status'] == 'pass' and requires_delivery(fresh, config):
        outcome = {**outcome, 'status': 'blocked',
                   'summary': 'QA passed, but the task still requires independent DEV delivery proof. '
                              + outcome['summary']}
    uploaded = await publish_review(erp, task_id, run_id, outcome)
    if (outcome['status'] == 'pass' and type(uploaded) is int
            and uploaded == len(set(outcome['screenshots'])) and uploaded >= 1):
        fresh = await erp.get('Task', task_id)
        if (matches_task(fresh, candidate) and fresh['modified'] == task['modified']
                and not requires_delivery(fresh, config) and await resolver.authorized(fresh)
                and await latest_run(resolver, task_id, owner_id) == candidate):
            current_project = await erp.get('Project', fresh['project']) if fresh.get('project') else None
            if (current_project or {}).get('modified') == (project or {}).get('modified'):
                audit = {**task_audit(fresh), 'status': 'Completed',
                         'qa': {'run_id': run_id, 'status': 'pass', 'screenshots': uploaded}}
                # ERPClient.save sends the original modified timestamp and verifies
                # the write. A concurrent human update wins over this transition.
                await erp.save(fresh, {'status': 'Completed', DETAIL: json.dumps(audit)})
    store.settle(task_id, run_id)


async def reconcile_pending_review(resolver, config):
    settings = config.get('pending_review_qa')
    if not isinstance(settings, dict) or not isinstance(settings.get('tasks'), dict):
        return
    cutoff = settings.get('cutoff')
    if type(cutoff) is not int or cutoff <= 0 or not settings['tasks']:
        return
    owner = await Users.get_user_by_email(resolver.app.owner)
    if not owner or owner.role != 'admin':
        return
    from open_webui.utils.erp_review_evidence import publish_review
    from open_webui.utils.erp_review_worker import run_review

    path = Path(resolver.store.path).with_name('erp-pending-review-qa.sqlite')
    store = ReviewStore(path)
    for task_id, policy in settings['tasks'].items():
        if not TASK_ID.fullmatch(task_id) or not isinstance(policy, dict):
            continue
        try:
            await review_one(resolver, config, store, task_id, owner.id, policy, cutoff,
                             run_review, publish_review)
        except Exception as exc:
            log.warning('ERP QA for %s will reconcile: %s', task_id, type(exc).__name__)

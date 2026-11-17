"""Conflict-safe Task writes and bounded, credential-free ERP rejection details."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import fcntl
from functools import wraps
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import stat
import time

import httpx

LOCK_ROOT = Path('/srv/abit-dev-linux/abit-ai/data/erp-task-write-locks')
_held = ContextVar('erp_task_write_locks', default={})


def error_details(response):
    try:
        body = response.json()
    except (ValueError, UnicodeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    kind = body.get('exc_type') or str(body.get('exception') or '').split(':', 1)[0].split('.')[-1]
    kind = kind if isinstance(kind, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', kind) else 'ERPError'
    messages = []
    raw = body.get('_server_messages')
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
        for row in rows[:8] if isinstance(rows, list) else []:
            row = json.loads(row) if isinstance(row, str) else row
            if isinstance(row, dict) and isinstance(row.get('message'), str):
                messages.append(row['message'])
    except (ValueError, TypeError):
        pass
    if not messages and isinstance(body.get('message'), str):
        messages.append(body['message'])
    exception = body.get('exception')
    if (not messages and isinstance(exception, str) and '\n' not in exception
            and re.match(r'^(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*:', exception)):
        messages.append(exception.split(':', 1)[-1])
    # Do not include exc/tracebacks, request bodies, response headers or arbitrary
    # HTML pages. Frappe error logs can contain credentials in local variables.
    text = ' '.join(messages)[:6000]
    if re.search(r'(?i)traceback|\b(headers|cookies)\s*=|traceback with variables', text):
        text = 'ERP server diagnostics omitted; validation type retained.'
    text = unescape(re.sub(r'<[^>]*>', ' ', text))
    auth = response.request.headers.get('Authorization', '')
    for secret in [auth, auth.partition(' ')[2]]:
        if len(secret) >= 8:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?i)\b(password|secret|api[_ -]?key|authorization|cookie|token)\s*[:=]\s*[^\s,;]+',
                  r'\1=[redacted]', text)
    return kind, ' '.join(text.split())[:1800]


class ERPResponseError(httpx.HTTPStatusError):
    def __init__(self, response):
        self.erp_type, detail = error_details(response)
        self.timestamp_conflict = (response.status_code in {409, 417}
            and (self.erp_type == 'TimestampMismatchError'
                 or 'Document has been modified after you have opened it' in detail))
        message = f'ERP HTTP {response.status_code} {self.erp_type}'
        if detail:
            message += ': ' + detail
        super().__init__(message, request=response.request, response=response)


class ERPStateChanged(RuntimeError):
    """A concurrent business/ownership edit must be preserved, not rebased."""


@asynccontextmanager
async def task_write_lock(name):
    owner = asyncio.current_task()
    if _held.get().get(name) is owner:
        yield
        return
    LOCK_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LOCK_ROOT / (hashlib.sha256(name.encode()).hexdigest() + '.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ERPStateChanged('ERP Task lock is not a regular file')
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ERPStateChanged('ERP Task status writer is busy; original execution was preserved')
                await asyncio.sleep(0.05)
        token = _held.set({**_held.get(), name: owner})
        try:
            yield
        finally:
            _held.reset(token)
    finally:
        os.close(fd)


def equivalent(field, left, right):
    if field == 'custom_ai_relay_execution_detail':
        try:
            return json.loads(left or '{}') == json.loads(right or '{}')
        except (ValueError, TypeError):
            pass
    return left == right


def validate_rebase(original, fresh, changes):
    from open_webui.utils.erp_task_resolution import TASK_INPUTS, AGENT_URL, EXECUTION_ID
    for field in {*TASK_INPUTS, AGENT_URL, EXECUTION_ID, 'parent_task'}:
        if (not equivalent(field, original.get(field), fresh.get(field))
                and not (field in changes and equivalent(field, fresh.get(field), changes[field]))):
            raise ERPStateChanged(f"ERP Task {original['name']} changed {field}; preserved the newer task")
    for field, desired in changes.items():
        if (not equivalent(field, fresh.get(field), original.get(field))
                and not equivalent(field, fresh.get(field), desired)):
            raise ERPStateChanged(f"ERP Task {original['name']} has a newer {field}; preserved the newer status")


async def save_task(client, task, changes):
    async with task_write_lock(task['name']):
        for attempt in range(3):
            fresh = await client.get('Task', task['name'])
            validate_rebase(task, fresh, changes)
            if all(equivalent(k, fresh.get(k), v) for k, v in changes.items()):
                return
            try:
                await client.request('POST', '/api/method/frappe.client.save',
                                     json={'doc': {**fresh, **changes, 'doctype': 'Task'}})
            except ERPResponseError as error:
                # A timestamp rejection is a confirmed negative acknowledgment.
                # Never retry a timeout, a lost reply or an arbitrary 417.
                if not error.timestamp_conflict or attempt == 2:
                    raise
                continue
            saved = await client.get('Task', task['name'])
            if any(not equivalent(k, saved.get(k), v) for k, v in changes.items()):
                raise ERPStateChanged(f"ERP Task {task['name']} save did not pass exact status readback")
            return


def serialize_status_sync(function):
    @wraps(function)
    async def serialized(erp, task_id, *args, **kwargs):
        async with task_write_lock(task_id):
            return await function(erp, task_id, *args, **kwargs)
    return serialized

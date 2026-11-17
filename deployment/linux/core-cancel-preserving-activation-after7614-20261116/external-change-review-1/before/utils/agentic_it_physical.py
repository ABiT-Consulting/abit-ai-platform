"""Concrete fixed-command host executor with immutable original-operation evidence.

Server code registers typed resource handlers. Model/JSON input never supplies a
handler, command, privilege or root. Unknown outcomes are observe-only forever
under their original action/invocation/plan identity.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import time

from open_webui.utils.agentic_it_changes import (
    ChangeOutcomeUncertain, TrustedChangeExecutor, canonical, digest, plan_authority_digest,
)

IDENTITY = re.compile(r'[A-Za-z0-9_.:@+-]{1,160}')
SHA = re.compile(r'[a-f0-9]{64}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class PhysicalHandler:
    """A reviewed server callback derives/rechecks its exact physical commands."""

    operation: str
    resource_id: str
    project_id: str
    repository: str
    environment: str
    host_id: str
    effective_uid: int
    prepare_commands: object
    verify_postcondition: object
    maximum_seconds: int = 120
    physical_identity: str = ''

    def __post_init__(self):
        require(self.operation in {'install_official_package', 'restart_service', 'extend_capacity'}, 'Unsupported physical operation')
        require(IDENTITY.fullmatch(self.resource_id) and callable(self.prepare_commands)
                and callable(self.verify_postcondition), 'Typed physical resource and postcondition callbacks required')
        require(type(self.effective_uid) is int and self.effective_uid >= 0, 'Exact host executor identity required')
        require(type(self.maximum_seconds) is int and 1 <= self.maximum_seconds <= 300, 'Bounded physical deadline required')
        require(isinstance(self.physical_identity, str) and 1 <= len(self.physical_identity) <= 300
                and '\x00' not in self.physical_identity, 'Exact underlying physical resource identity required')


class DurablePhysicalExecutor(TrustedChangeExecutor):
    """Real subprocess execution; an existing durable intent is never dispatched again.

    This runs as the explicitly registered host operator. Production registration
    requires its reviewed fixed helper/profile; no sudo or privilege is inferred.
    """

    def __init__(self, state_root, handlers, *, run_lookup):
        require(isinstance(handlers, (tuple, list)) and handlers and all(isinstance(x, PhysicalHandler) for x in handlers),
                'Only internal typed physical handlers may be registered')
        self.handlers = {(item.operation, item.resource_id): item for item in handlers}
        require(len(self.handlers) == len(handlers), 'Ambiguous physical resource registration')
        require(callable(run_lookup), 'Original server run lookup required')
        self.run_lookup = run_lookup
        self.root = Path(state_root)
        require(self.root.is_absolute() and '..' not in self.root.parts and self.root.resolve() == self.root,
                'Canonical protected physical state root required')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'Physical state owner/mode differs')
        self.database = self.root / 'original-operations.sqlite'
        key_file = self.root / 'receipt-authentication.key'
        if not key_file.exists():
            require(not self.database.exists(), 'Original physical receipt authentication key is missing; never rotate implicitly')
            key = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(key, 'wb') as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
        key = os.open(key_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(key)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 32, 'Physical authentication key is not private')
            self._key = os.read(key, 33)
        finally:
            os.close(key)
        fd = os.open(self.database, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        os.close(fd)
        with self._db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS original_operation(
                action_id TEXT PRIMARY KEY, invocation_id TEXT NOT NULL UNIQUE,
                plan_digest TEXT NOT NULL, request_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL,
                state TEXT NOT NULL, receipt_json TEXT, receipt_sha256 TEXT, created_at INTEGER NOT NULL,
                operation TEXT NOT NULL, resource_id TEXT NOT NULL, run_id TEXT NOT NULL, physical_identity TEXT NOT NULL);
              CREATE UNIQUE INDEX IF NOT EXISTS one_unsettled_physical_resource
                ON original_operation(physical_identity) WHERE state='uncertain';
              CREATE TABLE IF NOT EXISTS original_operation_event(
                event_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, created_at INTEGER NOT NULL);
              CREATE TRIGGER IF NOT EXISTS original_event_no_update BEFORE UPDATE ON original_operation_event
                BEGIN SELECT RAISE(ABORT,'Physical operation events are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS original_event_no_delete BEFORE DELETE ON original_operation_event
                BEGIN SELECT RAISE(ABORT,'Physical operation events are immutable'); END;
            ''')
        super().__init__(self._execute_once, self._observe_original)

    def supports(self, operation, resource_id, scope, profile):
        handler = self.handlers.get((operation, resource_id))
        authorized = bool(handler and handler.host_id == profile.get('host_id') == socket.gethostname()
            and handler.effective_uid == profile.get('expected_uid') == os.geteuid()
            and all(scope.get(key) == getattr(handler, key) for key in ('project_id', 'repository', 'environment')))
        if not authorized:
            return False
        with self._db(False) as db:
            return not db.execute("SELECT 1 FROM original_operation WHERE physical_identity=? AND state='uncertain'",
                                  (handler.physical_identity,)).fetchone()

    def _receipt_mac(self, receipt):
        body = {key: value for key, value in receipt.items() if key != 'receipt_mac'}
        return hmac.new(self._key, b'abit-it-host-receipt-v1\0' + canonical(body).encode(), hashlib.sha256).hexdigest()

    async def _original_run(self, invocation):
        value = self.run_lookup(invocation)
        if hasattr(value, '__await__'):
            value = await value
        require(isinstance(value, str) and IDENTITY.fullmatch(value), 'Exact original physical run identity unavailable')
        return value

    @contextmanager
    def _db(self, write=True):
        require(self.root.resolve() == self.root and self.root.stat().st_uid == os.geteuid()
                and stat.S_IMODE(self.root.stat().st_mode) == 0o700, 'Physical state root changed')
        info = self.database.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'Physical journal is not private')
        db = sqlite3.connect(self.database.as_uri() + ('?mode=rw' if write else '?mode=ro'), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if not write:
                db.execute('PRAGMA query_only=ON')
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _identity(self, action, invocation, plan):
        require(all(isinstance(value, str) and IDENTITY.fullmatch(value) for value in (action, invocation)), 'Exact physical execution IDs required')
        require(isinstance(plan, str) and SHA.fullmatch(plan), 'Exact physical plan digest required')

    async def _observe_original(self, action, invocation, plan):
        self._identity(action, invocation, plan)
        with self._db(False) as db:
            row = db.execute('SELECT * FROM original_operation WHERE action_id=? OR invocation_id=?', (action, invocation)).fetchall()
        if not row:
            return None
        require(len(row) == 1 and (row[0]['action_id'], row[0]['invocation_id'], row[0]['plan_digest']) == (action, invocation, plan),
                'Original physical receipt owns different immutable authority')
        row = row[0]
        require(row['run_id'] == await self._original_run(invocation), 'Original physical run identity changed')
        if row['state'] != 'completed':
            return {'state': 'uncertain', 'action_id': action, 'invocation_id': invocation,
                    'plan_digest': plan, 'run_id': row['run_id'], 'automatic_replay_permitted': False}
        receipt = json.loads(row['receipt_json'])
        require(digest(receipt) == row['receipt_sha256'] and receipt['action_id'] == action
                and receipt['invocation_id'] == invocation and receipt['plan_digest'] == plan
                and receipt.get('run_id') == await self._original_run(invocation)
                and receipt.get('plan_sha256') == digest(json.loads(row['plan_json']))
                and hmac.compare_digest(str(receipt.get('receipt_mac')), self._receipt_mac(receipt)),
                'Immutable physical receipt failed readback')
        return receipt

    async def original_plan(self,action,invocation,plan_digest):
        receipt=await self._observe_original(action,invocation,plan_digest)
        require(receipt and receipt.get('state')=='completed','Original completed authenticated physical plan is missing')
        with self._db(False) as db:
            row=db.execute('SELECT plan_json FROM original_operation WHERE action_id=? AND invocation_id=?',(action,invocation)).fetchone()
        plan=json.loads(row['plan_json'])
        require(digest(plan)==receipt['plan_sha256'] and plan_authority_digest(plan)==plan_digest,'Original physical plan bytes differ')
        return plan,receipt

    async def _execute_once(self, plan, action, invocation, argv):
        self._identity(action, invocation, plan.get('plan_digest'))
        original_run = await self._original_run(invocation)
        require(plan_authority_digest(plan) == plan['plan_digest'] and plan.get('executable') is True
                and plan.get('state') == 'prepared', 'Physical execution requires the exact validated prepared plan')
        handler = self.handlers.get((plan.get('operation'), plan.get('resource_id')))
        require(handler is not None, 'No trusted physical resource handler is registered')
        scope = plan.get('scope') or {}
        require(handler.host_id == socket.gethostname() == plan.get('host_id')
                and handler.effective_uid == os.geteuid(), 'Physical host/operator identity differs')
        require(all(scope.get(key) == getattr(handler, key) for key in ('project_id', 'repository', 'environment')),
                'Physical resource scope differs from its reviewed owner')
        require(tuple(plan.get('fixed_argv', ())) == tuple(argv), 'Physical command differs from the server plan')
        prior = await self._observe_original(action, invocation, plan['plan_digest'])
        if prior:
            if prior['state'] == 'completed':
                return prior
            raise ChangeOutcomeUncertain('Original physical host intent remains uncertain; observe only')
        commands = handler.prepare_commands(deepcopy(plan))
        if hasattr(commands, '__await__'):
            commands = await commands
        require(isinstance(commands, (list, tuple)) and 1 <= len(commands) <= 3, 'Bounded registered physical commands required')
        require(all(isinstance(command, tuple) and 1 <= len(command) <= 20
                    and all(isinstance(arg, str) and 0 < len(arg) <= 1024 and '\x00' not in arg for arg in command)
                    and command[0].startswith('/') for command in commands), 'Physical commands must be derived by trusted server code')
        request_hash = digest([action, invocation, original_run, plan['plan_digest'], commands])
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT 1 FROM original_operation WHERE action_id=? OR invocation_id=?', (action, invocation)).fetchone()
            if existing:
                raise ChangeOutcomeUncertain('Concurrent original physical intent exists; observe only')
            resource = db.execute("SELECT 1 FROM original_operation WHERE physical_identity=? AND state='uncertain'",
                                  (handler.physical_identity,)).fetchone()
            if resource:
                raise ChangeOutcomeUncertain('Physical resource has an unsettled original operation; observe only')
            now = int(time.time())
            db.execute('INSERT INTO original_operation VALUES(?,?,?,?,?,\'uncertain\',NULL,NULL,?,?,?,?,?)',
                       (action, invocation, plan['plan_digest'], request_hash, canonical(plan), now, handler.operation,
                        handler.resource_id, original_run, handler.physical_identity))
            db.execute('INSERT INTO original_operation_event VALUES(?,?,?,?,?)',
                       (digest([action, 'intent']), action, 'intent_before_host_dispatch', canonical({'request_sha256': request_hash}), now))
        # All paths after this durable fence, including cancellation/timeout, are
        # observe-only. A matching postcondition alone never attributes execution.
        rechecked = handler.prepare_commands(deepcopy(plan))
        if hasattr(rechecked, '__await__'):
            rechecked = await rechecked
        require(rechecked == commands, 'Physical target/commands changed immediately before dispatch')
        results = []
        for index, command in enumerate(commands):
            result = await self._command(command, handler.maximum_seconds)
            results.append(result)
            with self._db() as db:
                db.execute('INSERT INTO original_operation_event VALUES(?,?,?,?,?)',
                           (digest([action, 'step', index]), action, 'fixed_step_returned', canonical(result), int(time.time())))
            if result['exit_code'] != 0:
                raise ChangeOutcomeUncertain('Original fixed host command did not confirm success; no automatic repeat')
        observed = handler.verify_postcondition(deepcopy(plan))
        if hasattr(observed, '__await__'):
            observed = await observed
        postcondition = {'verified': True, 'observed': None} if observed is True else observed
        if not (isinstance(postcondition, dict) and set(postcondition) == {'verified', 'observed'}
                and postcondition['verified'] is True and len(canonical(postcondition).encode()) <= 16384):
            raise ChangeOutcomeUncertain('Original host command returned but exact physical postcondition is unverified')
        receipt = {'state': 'completed', 'action_id': action, 'invocation_id': invocation,
                   'run_id': original_run,
                   'plan_digest': plan['plan_digest'], 'receipt_id': 'host-' + digest([action, invocation, plan['plan_digest']])[:48],
                   'finished_at': int(time.time()), 'request_sha256': request_hash, 'plan_sha256':digest(plan), 'fixed_steps': results}
        receipt['postcondition'] = postcondition
        receipt['receipt_mac'] = self._receipt_mac(receipt)
        with self._db() as db:
            db.execute('UPDATE original_operation SET state=\'completed\',receipt_json=?,receipt_sha256=? WHERE action_id=? AND state=\'uncertain\'',
                       (canonical(receipt), digest(receipt), action))
            db.execute('INSERT INTO original_operation_event VALUES(?,?,?,?,?)',
                       (digest([action, 'completed']), action, 'completed', canonical({'receipt_sha256': digest(receipt)}), int(time.time())))
        return await self._observe_original(action, invocation, plan['plan_digest'])

    async def _command(self, argv, timeout):
        process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'DEBIAN_FRONTEND': 'noninteractive'})
        async def consume(stream):
            value, size = hashlib.sha256(), 0
            while chunk := await stream.read(65536):
                size += len(chunk)
                require(size <= 32_000_000, 'Physical command output exceeded its reviewed bound')
                value.update(chunk)
            return {'bytes': size, 'sha256': value.hexdigest()}
        try:
            out, err, code = await asyncio.wait_for(asyncio.gather(consume(process.stdout), consume(process.stderr), process.wait()), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        return {'exit_code': code, 'stdout': out, 'stderr': err, 'argv_sha256': digest(argv)}

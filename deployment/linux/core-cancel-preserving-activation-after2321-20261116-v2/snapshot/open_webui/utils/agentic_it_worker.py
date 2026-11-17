"""Protected independent original-worker journal and fixed systemd launcher.

Only a Root-reviewed installation registers its fixed worker command/profile.
An uncertain StartUnit acknowledgement is never submitted again. A fresh worker
reads the authenticated original request and rechecks every physical identity.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from open_webui.utils.agentic_it_changes import (canonical, digest, plan_authority_digest, ChangeOutcomeUncertain)
from open_webui.utils.agentic_it_broker import call

ID = re.compile(r'[A-Za-z0-9_.:@+-]{1,160}')


def require(value, message):
    if not value:
        raise ValueError(message)


class OriginalWorkerJournal:
    def __init__(self, root, *, owner_uid=0):
        self.root, self.owner = Path(root), owner_uid
        require(self.root.is_absolute() and self.root.resolve() == self.root, 'Canonical original worker root required')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.root/'worker-launches.sqlite'
        self._private(self.root, directory=True)
        key = self.root/'worker-request.key'
        if not key.exists():
            require(not self.database.exists(), 'Worker key missing; original authority must never rotate implicitly')
            self._write(key, secrets.token_bytes(32))
        self._private(key)
        self.key = key.read_bytes()
        require(len(self.key) == 32, 'Invalid private worker key')
        if not self.database.exists():
            self._write(self.database, b'')
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS worker_launch(
                action_id TEXT PRIMARY KEY, invocation_id TEXT UNIQUE NOT NULL,
                run_id TEXT NOT NULL, plan_digest TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                request_file TEXT NOT NULL UNIQUE, unit TEXT NOT NULL UNIQUE, accepted_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS worker_launch_event(
                event_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL);
              CREATE TRIGGER IF NOT EXISTS worker_launch_no_update BEFORE UPDATE ON worker_launch
                BEGIN SELECT RAISE(ABORT,'Original worker launches are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS worker_launch_no_delete BEFORE DELETE ON worker_launch
                BEGIN SELECT RAISE(ABORT,'Original worker launches are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS worker_event_no_update BEFORE UPDATE ON worker_launch_event
                BEGIN SELECT RAISE(ABORT,'Original worker events are immutable'); END;
              CREATE TRIGGER IF NOT EXISTS worker_event_no_delete BEFORE DELETE ON worker_launch_event
                BEGIN SELECT RAISE(ABORT,'Original worker events are immutable'); END;''')

    def _private(self, path, directory=False):
        info = path.lstat()
        require(info.st_uid == self.owner and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
                and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                'Private original worker path owner/mode differs')

    def _write(self, path, raw):
        descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())

    @contextmanager
    def db(self):
        self._private(self.root, True);self._private(self.database)
        db=sqlite3.connect(self.database,timeout=5);db.row_factory=sqlite3.Row
        try:
            yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def event(self, action, kind, body):
        raw=canonical(body)
        with self.db() as db:
            db.execute('INSERT INTO worker_launch_event VALUES(?,?,?,?)',
                ('event-'+digest([action,kind,body]),action,kind,raw))

    def original_run(self, invocation):
        with self.db() as db:
            row=db.execute('SELECT run_id FROM worker_launch WHERE invocation_id=?',(invocation,)).fetchone()
        require(row is not None, 'Original independent worker run is missing')
        return row['run_id']

    def _mac(self, body):
        return hmac.new(self.key,b'abit-it-worker-request-v1\0'+canonical(body).encode(),hashlib.sha256).hexdigest()

    def accept(self, payload, grant, profile_sha256):
        for key in ('action_id','invocation_id','run_id'):
            require(isinstance(payload.get(key),str) and ID.fullmatch(payload[key]), 'Exact original worker IDs required')
        require(payload['plan'].get('plan_digest')==plan_authority_digest(payload['plan'])
                and grant.get('consumed') is True and grant.get('receipt_id')
                and all(grant.get(key)==payload[key] for key in ('action_id','invocation_id','run_id'))
                and grant.get('plan_digest')==payload['plan']['plan_digest'], 'Consumed original worker authority differs')
        authority = {'payload':payload,'grant':grant,'profile_sha256':profile_sha256}
        fingerprint = digest(authority)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT * FROM worker_launch WHERE action_id=? OR invocation_id=?',
                (payload['action_id'],payload['invocation_id'])).fetchall()
            if prior:
                require(len(prior)==1 and prior[0]['action_id']==payload['action_id']
                        and prior[0]['invocation_id']==payload['invocation_id'] and prior[0]['request_sha256']==fingerprint,
                        'Original worker key owns different authority')
                return dict(prior[0]),False
            ident=digest([payload['action_id'],payload['invocation_id'],payload['plan']['plan_digest']])[:48]
            file=self.root/('original-'+ident+'.json')
            # A crash after file creation and before transaction commit must hold;
            # an unowned file is never reinterpreted as permission to dispatch.
            body={**authority,'accepted_at':int(time.time()),'execute_before':int(time.time())+30}
            self._write(file,canonical({'body':body,'mac':self._mac(body)}).encode())
            row={'action_id':payload['action_id'],'invocation_id':payload['invocation_id'],'run_id':payload['run_id'],
                 'plan_digest':payload['plan']['plan_digest'],'request_sha256':fingerprint,'request_file':str(file),
                 'unit':'abit-ai-it-original-'+ident+'.service','accepted_at':body['accepted_at']}
            db.execute('INSERT INTO worker_launch VALUES(:action_id,:invocation_id,:run_id,:plan_digest,:request_sha256,:request_file,:unit,:accepted_at)',row)
            return row,True

    def read_request(self, file, profile_sha256):
        file=Path(file)
        require(file.parent==self.root and file.resolve()==file and file.name.startswith('original-'), 'Fixed original worker file required')
        self._private(file);require(file.stat().st_size<=128000,'Original worker request exceeds bound')
        envelope=json.loads(file.read_bytes());body=envelope['body']
        require(hmac.compare_digest(str(envelope['mac']),self._mac(body)) and body['profile_sha256']==profile_sha256,
                'Original worker request authentication/profile differs')
        payload=body['payload'];grant=body['grant']
        with self.db() as db:
            row=db.execute('SELECT * FROM worker_launch WHERE action_id=? AND invocation_id=?',
                           (payload['action_id'],payload['invocation_id'])).fetchone()
        require(row and row['request_file']==str(file) and row['request_sha256']==digest({key:body[key] for key in ('payload','grant','profile_sha256')})
                and row['run_id']==payload['run_id'] and row['plan_digest']==payload['plan']['plan_digest'],
                'Worker request has no exact durable original launch record')
        require(int(time.time())<body['execute_before'], 'Original worker admission expired; no automatic replay')
        return payload,grant


class SystemdOriginalLauncher:
    def __init__(self, journal, executor, *, profile_sha256, worker_argv, recheck_plan, writable_paths, launch_unit=None):
        require(isinstance(journal,OriginalWorkerJournal) and callable(recheck_plan)
                and callable(getattr(executor,'observe_original',None)), 'Typed original worker dependencies required')
        require(re.fullmatch('[a-f0-9]{64}',str(profile_sha256)), 'Pinned worker resource profile required')
        require(isinstance(worker_argv,tuple) and len(worker_argv)==4 and worker_argv[0]=='/usr/bin/python3'
                and worker_argv[2]=='worker' and all(isinstance(worker_argv[index],str)
                and worker_argv[index].startswith('/') and '..' not in Path(worker_argv[index]).parts for index in (1,3)),
                'Server-fixed absolute worker entrypoint required')
        require(writable_paths and all(isinstance(path,str) and path.startswith('/') and '..' not in Path(path).parts for path in writable_paths),
                'Exact worker writable resource paths required')
        self.journal,self.executor,self.profile_sha=journal,executor,profile_sha256
        self.worker_argv,self.recheck,self.paths=worker_argv,recheck_plan,tuple(writable_paths)
        self.launch_unit=launch_unit or self._systemd

    async def _systemd(self, row):
        # An independent fixed root unit outlives Core/broker and has no restart
        # policy. No shell, environment assignment or model-selected flag exists.
        argv=('/usr/bin/systemd-run','--quiet','--unit='+row['unit'],'--service-type=exec',
            '--property=User=root','--property=Group=root','--property=NoNewPrivileges=yes',
            '--property=ProtectSystem=strict','--property=ProtectHome=yes','--property=PrivateTmp=yes',
            '--property=PrivateNetwork=yes','--property=RestrictAddressFamilies=AF_UNIX',
            '--property=MemoryMax=536870912','--property=TasksMax=64','--property=RuntimeMaxSec=330',
            '--property=TimeoutStopSec=15','--property=Restart=no','--property=UMask=0077',
            '--property=CapabilityBoundingSet=','--property=ReadWritePaths='+' '.join(self.paths),
            '--',*self.worker_argv,row['request_file'])
        process=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
        try:code=await asyncio.wait_for(process.wait(),10)
        except BaseException:
            # Killing this bounded CLI never cancels an accepted systemd worker.
            process.kill();await process.wait();raise
        require(code==0,'Original StartUnit acknowledgement is unknown or rejected')
        return {'unit':row['unit'],'start_acknowledged':True}

    async def launch(self, payload, grant):
        # Current safety/capacity is re-read even when an existing approval's
        # authority digest remains stable under normal free-space movement.
        fresh=await call(self.recheck,payload['plan'])
        require(fresh.get('executable') is True and fresh.get('state')=='prepared'
                and fresh.get('plan_digest')==payload['plan']['plan_digest'], 'Physical safety changed before worker admission')
        row,created=self.journal.accept(payload,grant,self.profile_sha)
        if created:
            try:
                receipt=await call(self.launch_unit,row)
                self.journal.event(row['action_id'],'start_acknowledged',receipt)
            except BaseException:
                self.journal.event(row['action_id'],'start_acknowledgement_unknown',{'automatic_replay_permitted':False})
                raise
        result=await self.executor.observe_original(row['action_id'],row['invocation_id'],row['plan_digest'])
        return result or {'state':'uncertain','action_id':row['action_id'],'invocation_id':row['invocation_id'],
            'run_id':row['run_id'],'plan_digest':row['plan_digest'],'automatic_replay_permitted':False}

    async def observe(self,payload):
        require(self.journal.original_run(payload['invocation_id'])==payload['run_id'],'Original worker run differs')
        return await self.executor.observe_original(payload['action_id'],payload['invocation_id'],payload['plan_digest'])

    async def run_original(self,request_file):
        payload,grant=self.journal.read_request(request_file,self.profile_sha)
        fresh=await call(self.recheck,payload['plan'])
        require(fresh.get('executable') is True and fresh.get('plan_digest')==payload['plan']['plan_digest'],
                'Worker current physical safety differs from original approval')
        result=await self.executor.execute_once(payload['plan'],payload['action_id'],payload['invocation_id'],tuple(payload['plan']['fixed_argv']))
        require(result.get('state')=='completed' and result.get('run_id')==payload['run_id'],'Original worker did not complete its exact run')
        return result

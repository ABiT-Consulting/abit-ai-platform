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

    def phase_event(self, action, key, value):
        # Fixed event key makes each checkpoint and each consumption single-use.
        with self.db() as db:
            db.execute('INSERT INTO worker_launch_event VALUES(?,?,?,?)',
                       (digest([action,key]),action,key,canonical(value)))

    async def phase_exchange(self, q, request):
        from open_webui.utils.agentic_intermarket_release_registry import release_for
        release = release_for(q['plan']['resource_id'])
        action = q['original']['action_id']
        challenge = {'phase_request':q, 'request':request, 'expires_at':int(time.time()) + 25}
        release.check_challenge(challenge, q['plan'], q['original'])
        self.phase_event(action, 'ask-' + request['phase'] + '-' + request['op'] + '-' + request['checkpoint'], challenge)
        key = digest(challenge)
        while time.time() < challenge['expires_at']:
            with self.db() as db:
                row = db.execute('SELECT payload_json FROM worker_launch_event WHERE event_id=?',
                                 (digest([action,'reply-' + key]),)).fetchone()
            if row:
                reply = json.loads(row[0])
                if request['op'] == 'authorize':
                    release.check_decision(reply, request)
                self.phase_event(action, 'used-' + key, {'reply_sha256':digest(reply)})
                return reply
            await asyncio.sleep(0.05)
        raise ChangeOutcomeUncertain('Original phase response unavailable; preserve reservation')

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
        paths = self.paths
        if row.get('intermarket_release') is True:
            from open_webui.utils.agentic_intermarket_release_registry import activation_parent
            paths = (str(self.journal.root), str(self.executor.root), activation_parent(row['release_resource_id']),
                     '/etc/systemd/system/intermarket-sbodesk.service.d')
            require(set(paths) <= set(self.paths), 'Exact release writable directories must be registered')
            for path in paths:
                directory = Path(path)
                info = directory.lstat()
                require(directory.resolve() == directory and info.st_uid == 0 and stat.S_ISDIR(info.st_mode)
                        and not info.st_mode & 0o022, 'Protected release mount point required')
        argv=('/usr/bin/systemd-run','--quiet','--unit='+row['unit'],'--service-type=exec',
            '--property=User=root','--property=Group=root','--property=NoNewPrivileges=yes',
            '--property=ProtectSystem=strict','--property=ProtectHome=yes','--property=PrivateTmp=yes',
            '--property=PrivateNetwork=yes','--property=RestrictAddressFamilies=AF_UNIX',
            '--property=MemoryMax=536870912','--property=TasksMax=64','--property=RuntimeMaxSec=330',
            '--property=TimeoutStopSec=15','--property=Restart=no','--property=UMask=0077',
            '--property=CapabilityBoundingSet=','--property=ReadWritePaths='+' '.join(paths),
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
                receipt=await call(self.launch_unit,dict(row,intermarket_release=payload['plan']['operation']=='deploy_release',
                    release_resource_id=payload['plan']['resource_id']))
                self.journal.event(row['action_id'],'start_acknowledged',receipt)
            except BaseException:
                self.journal.event(row['action_id'],'start_acknowledgement_unknown',{'automatic_replay_permitted':False})
                raise
        result=await self.executor.observe_original(row['action_id'],row['invocation_id'],row['plan_digest'])
        return result or {'state':'uncertain','action_id':row['action_id'],'invocation_id':row['invocation_id'],
            'run_id':row['run_id'],'plan_digest':row['plan_digest'],'automatic_replay_permitted':False}

    async def release_session(self, payload, grant, exchange):
        from open_webui.utils.agentic_intermarket_release_registry import release_for
        release = release_for(payload['plan']['resource_id'])
        action = payload['action_id']
        # Bind the relay to a single accepted Core connection. No reconnect or
        # retry can take over a detached or uncertain original.
        self.journal.phase_event(action, 'original-core-session', {'original':digest(payload)})
        result = await self.launch(payload, grant)
        cursor = 0
        while result.get('state') == 'uncertain':
            with self.journal.db() as db:
                row = db.execute("SELECT rowid,payload_json FROM worker_launch_event WHERE action_id=? "
                                 "AND rowid>? AND kind LIKE 'ask-%' ORDER BY rowid LIMIT 1",(action,cursor)).fetchone()
                stopped = db.execute('SELECT 1 FROM worker_launch_event WHERE event_id=?',
                                     (digest([action,'parent-observe-only']),)).fetchone()
            if stopped:
                return await self.observe({**payload,'plan_digest':payload['plan']['plan_digest']}) or result
            if row:
                cursor = row[0]
                challenge = json.loads(row[1])
                release.check_challenge(challenge, payload['plan'], release.original_authority(grant))
                self.executor.check_release_reservation(payload, challenge)
                proof = await exchange(challenge)
                request = challenge['request']
                release.check_decision(proof, request)
                if request['op'] == 'observe':
                    proof = await release.host_observation(challenge)
                    if request['phase'] == 'preflight':
                        self.journal.phase_event(action, 'original-host-preimage', proof['host_preimage_sha256'])
                    elif release.RESOURCE == 'intermarket-pr241-auth-guard' and request['checkpoint'] == 'final-restart-resources':
                        with self.journal.db() as db:
                            prior = db.execute('SELECT payload_json FROM worker_launch_event WHERE event_id=?',
                                (digest([action,'original-restart-preimage-' + request['phase']]),)).fetchone()
                        require(prior is not None, 'Original heavy restart observation is missing')
                        release.check_final_restart_observation(proof, challenge, heavy=json.loads(prior[0]))
                    else:
                        with self.journal.db() as db:
                            prior = db.execute('SELECT payload_json FROM worker_launch_event WHERE event_id=?',
                                               (digest([action,'original-host-preimage']),)).fetchone()
                        require(prior and json.loads(prior[0]) == proof['host_preimage_sha256'],
                                'Host preimage differs from original preflight')
                    if release.RESOURCE == 'intermarket-pr241-auth-guard' and request['checkpoint'] == 'restart-preimage':
                        self.journal.phase_event(action, 'original-restart-preimage-' + request['phase'], proof)
                self.journal.phase_event(action, 'reply-' + digest(challenge), proof)
            await asyncio.sleep(0.05)
            result = await self.observe({**payload,'plan_digest':payload['plan']['plan_digest']}) or {'state':'uncertain'}
        return result

    async def observe(self,payload):
        require(self.journal.original_run(payload['invocation_id'])==payload['run_id'],'Original worker run differs')
        return await self.executor.observe_original(payload['action_id'],payload['invocation_id'],payload['plan_digest'])

    async def run_original(self,request_file):
        payload,grant=self.journal.read_request(request_file,self.profile_sha)
        fresh=await call(self.recheck,payload['plan'])
        require(fresh.get('executable') is True and fresh.get('plan_digest')==payload['plan']['plan_digest'],
                'Worker current physical safety differs from original approval')
        if payload['plan']['operation'] == 'deploy_release':
            try:
                return await self.executor.execute_release_parent(self.journal, request_file, self.profile_sha)
            except BaseException:
                self.journal.phase_event(payload['action_id'], 'parent-observe-only', {'automatic_replay_permitted':False})
                raise
        result=await self.executor.execute_once(payload['plan'],payload['action_id'],payload['invocation_id'],tuple(payload['plan']['fixed_argv']))
        require(result.get('state')=='completed' and result.get('run_id')==payload['run_id'],'Original worker did not complete its exact run')
        return result

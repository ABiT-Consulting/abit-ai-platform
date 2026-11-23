"""Real journal/MAC/once subprocess evidence with a typed test launcher only."""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

from test_agentic_controls_events import Fixture


class IndependentOriginalWorker(Fixture):
    def setUp(self):
        super().setUp()
        self.changes=self.load('agentic_it_changes');self.physical=self.load('agentic_it_physical')
        self.worker=self.load('agentic_it_worker');self.root=Path(self.temp.name)
        self.output=self.root/'physical-once.txt';self.starts=[]
        self.command=(sys.executable,'-c','from pathlib import Path;import sys;p=Path(sys.argv[1]);p.open("ab").write(b"once\\n")',str(self.output))
        self.handler=self.physical.PhysicalHandler('restart_service','owned-resource','PROJ-it-fixture','https://github.com/ABiT-Consulting/abit-ai-platform','test',socket.gethostname(),os.geteuid(),
            lambda plan:[self.command],lambda plan:self.output.read_bytes()==b'once\n',physical_identity='owned-worker-test-file')
        self.journal=self.worker.OriginalWorkerJournal(self.root/'worker',owner_uid=os.geteuid())
        self.executor=self.physical.DurablePhysicalExecutor(self.root/'physical',[self.handler],run_lookup=self.journal.original_run)
        self.plan={'schema_version':1,'operation':'restart_service','resource_id':'owned-resource',
            'scope':{'project_id':self.handler.project_id,'repository':self.handler.repository,'environment':'test'},
            'host_id':socket.gethostname(),'policy_digest':'a'*64,'target':{},'baseline':{},'fixed_argv':list(self.command),
            'safety':{},'state':'prepared','executable':True}
        self.plan['plan_digest']=self.changes.plan_authority_digest(self.plan)
        self.payload={'action_id':'action-original-worker','invocation_id':'invocation-original-worker','run_id':'run-original-worker','plan':self.plan}
        self.grant={'consumed':True,'receipt_id':'original-consumed-approval',**{key:self.payload[key] for key in ('action_id','invocation_id','run_id')},'plan_digest':self.plan['plan_digest']}
        async def launch(row):
            self.starts.append(row['unit'])
            await self.launcher.run_original(row['request_file'])
            return {'unit':row['unit'],'start_acknowledged':True}
        self.launcher=self.worker.SystemdOriginalLauncher(self.journal,self.executor,profile_sha256='b'*64,
            worker_argv=('/usr/bin/python3','/fixed/root-owned-worker.py','worker','/fixed/root-owned-profile.json'),
            writable_paths=(str(self.root),),recheck_plan=lambda plan:deepcopy(plan),launch_unit=launch)

    async def test_original_phase_mailbox_is_once_bound_to_nonce_and_consumption(self):
        release = self.load('agentic_intermarket_release')
        q = dict(plan=self.plan, original={'action_id':'phase-action'}, phase='publish', previous={})
        frame = dict(op='authorize',phase='publish',checkpoint='switch-selector',
                     request_sha256=self.changes.digest(q),nonce='a'*64)
        waiting = asyncio.create_task(self.journal.phase_exchange(q, frame))
        await asyncio.sleep(0)
        with self.journal.db() as db:
            row = db.execute("SELECT payload_json FROM worker_launch_event WHERE kind='ask-publish-authorize-switch-selector'").fetchone()
        challenge = json.loads(row[0])
        release.check_challenge(challenge,self.plan,q['original'])
        proof = dict(authorized=True,phase='publish',checkpoint='switch-selector',challenge_sha256=self.changes.digest(frame),
                     expires_at=int(time.time())+5)
        self.journal.phase_event('phase-action','reply-'+self.changes.digest(challenge),proof)
        self.assertEqual(await asyncio.wait_for(waiting, 2), proof)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            await self.journal.phase_exchange(q,dict(frame,nonce='b'*64))
        with self.assertRaises(ValueError):
            release.check_decision(proof,dict(frame,checkpoint='reload-manager'))
        with self.assertRaises(ValueError):
            release.check_decision(dict(proof,expires_at=int(time.time())-1),frame)

    async def test_independent_original_launch_and_real_subprocess_are_both_once(self):
        first=await self.launcher.launch(self.payload,self.grant)
        self.assertEqual('completed',first['state']);self.assertTrue(first['receipt_mac'])
        self.assertEqual(first,await self.launcher.launch(self.payload,self.grant))
        self.assertEqual(['abit-ai-it-original-'+self.changes.digest([self.payload['action_id'],self.payload['invocation_id'],self.plan['plan_digest']])[:48]+'.service'],self.starts)
        self.assertEqual(b'once\n',self.output.read_bytes())

    async def test_network_ack_loss_reconciles_completed_original_without_startunit_again(self):
        original=self.launcher.launch_unit
        async def lost(row):await original(row);raise TimeoutError('synthetic lost StartUnit acknowledgement')
        self.launcher.launch_unit=lost
        with self.assertRaises(TimeoutError):await self.launcher.launch(self.payload,self.grant)
        read={key:self.payload[key] for key in ('action_id','invocation_id','run_id')};read['plan_digest']=self.plan['plan_digest']
        receipt=await self.launcher.observe(read)
        self.assertEqual('completed',receipt['state']);self.assertEqual(b'once\n',self.output.read_bytes())
        self.assertEqual(receipt,await self.launcher.launch(self.payload,self.grant));self.assertEqual(1,len(self.starts))

    async def test_uncertain_startunit_before_worker_never_launches_again(self):
        async def lost(row):self.starts.append(row['unit']);raise TimeoutError('unknown original StartUnit')
        self.launcher.launch_unit=lost
        with self.assertRaises(TimeoutError):await self.launcher.launch(self.payload,self.grant)
        original=await self.launcher.launch(self.payload,self.grant)
        self.assertEqual('uncertain',original['state']);self.assertFalse(self.output.exists());self.assertEqual(1,len(self.starts))

    async def test_different_tuple_or_tampered_authenticated_request_denied_before_host(self):
        row,created=self.journal.accept(self.payload,self.grant,'b'*64);self.assertTrue(created)
        changed=deepcopy(self.payload);changed['run_id']='other-run'
        with self.assertRaises(ValueError):self.journal.accept(changed,{**self.grant,'run_id':'other-run'},'b'*64)
        path=Path(row['request_file']);body=json.loads(path.read_bytes());body['body']['payload']['plan']['fixed_argv']=['/bin/false'];path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError,'authentication'):await self.launcher.run_original(path)
        self.assertFalse(self.output.exists());self.assertFalse(self.starts)

    async def test_expired_admission_or_fresh_capacity_failure_does_not_execute(self):
        row,_=self.journal.accept(self.payload,self.grant,'b'*64)
        with patch.object(self.worker.time,'time',return_value=time.time()+31),self.assertRaisesRegex(ValueError,'expired'):
            await self.launcher.run_original(row['request_file'])
        self.launcher.recheck=lambda plan:{**plan,'executable':False}
        with self.assertRaisesRegex(ValueError,'safety changed'):await self.launcher.launch(self.payload,self.grant)
        self.assertFalse(self.output.exists());self.assertFalse(self.starts)

    async def test_original_journal_is_immutable_and_missing_key_never_rotates(self):
        row,_=self.journal.accept(self.payload,self.grant,'b'*64)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError),self.journal.db() as db:db.execute('DELETE FROM worker_launch')
        (self.journal.root/'worker-request.key').unlink()
        with self.assertRaisesRegex(ValueError,'never rotate'):self.worker.OriginalWorkerJournal(self.journal.root,owner_uid=os.geteuid())

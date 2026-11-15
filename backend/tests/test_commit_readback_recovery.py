"""Finite original Git message/readback and one authorized successor checks.
Real temporary SQLite and in-memory Git; sockets and DNS are denied.
The three incoming authorization scenarios are superseded by the stricter actual
signed-event/router tests in test_delivery_control_recovery.py. Historical
runtime evidence remains unchanged; generic _resume must stay one-use.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import time
import sqlite3
import socket
import test_delivery_continuation_guards as guards
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
sys.path.insert(0,str(BACKEND/'tests'))
import test_original_delivery_continuation as original

# Capture the unwrapped fixture before per-case patches in a combined suite.
# This case owns exactly two files and must not compose another case's seeder.
_ORIGINAL_SEED_TEST = original.fixture_delivery.Delivery.seed_test


class CommitReadbackRecovery(unittest.IsolatedAsyncioTestCase):
    # Reuse only fixture operations, without inheriting its unrelated test suite.
    continue_original=original.OriginalDeliveryContinuation.continue_original
    authority=original.OriginalDeliveryContinuation.authority
    status=original.OriginalDeliveryContinuation.status
    resume=original.OriginalDeliveryContinuation.resume
    contract=original.OriginalDeliveryContinuation.contract
    async def asyncSetUp(self):
        self.proposal=patch.object(original,'HERE',HERE)
        self.proposal.start();self.addCleanup(self.proposal.stop)
        for name in ('connect', 'connect_ex'):
            guard=patch.object(socket.socket,name,side_effect=AssertionError('Network disabled in commit regression'))
            guard.start();self.addCleanup(guard.stop)
        guard=patch.object(socket,'getaddrinfo',side_effect=AssertionError('DNS disabled in commit regression'))
        guard.start();self.addCleanup(guard.stop)
        memory=patch.object(original.fixture_delivery,'GitHubFixture',guards.MemoryGit)
        memory.start();self.addCleanup(memory.stop)
        prior_seed=_ORIGINAL_SEED_TEST
        async def seed_two_files(f,*args,**kwargs):
            (f.repo/'src/test_app.py').write_bytes(b'assert 1 + 1 == 2\n')
            return await prior_seed(f,*args,**kwargs)
        self.seed_patch=patch.object(original.fixture_delivery.Delivery,'seed_test',seed_two_files)
        self.seed_patch.start();self.addCleanup(self.seed_patch.stop)
        await original.OriginalDeliveryContinuation.asyncSetUp(self)
        memory_client=patch.object(self.m,'github_client',side_effect=lambda repository:guards.Client(self.f.remote))
        memory_client.start();self.addCleanup(memory_client.stop)

    async def prepare_original(self):
        self.f.remote.drop='/git/commits'
        held=await self.f.deliver();self.assertEqual(held['state'],'pending_reconciliation')
        self.assertEqual(self.f.remote.posts().count('/git/commits'),1)
        with self.m._db(self.path)as db:
            step=db.execute("SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key='commit'",(self.original['invocation_id'],)).fetchone()
            body=json.loads(step['request_json'])['body'];predicted=self.m._commit_hash(body)
        self.commit_body=deepcopy(body);self.commit_sha=predicted
        self.assertTrue(body['message'].endswith('\n'))
        self.f.remote.commits[predicted]['message']=body['message'][:-1]
        posts=self.f.remote.posts().copy();status=await self.f.reconcile()
        self.assertEqual(status['state'],'reconciled');self.assertEqual(self.f.remote.posts(),posts)
        with self.m._db(self.path)as db:
            row=db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',(self.original['invocation_id'],)).fetchone()
            self.original_input=json.loads(row['input_json'])
            self.original_steps=[dict(x)for x in db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? ORDER BY step_key',(self.original['invocation_id'],))]
        self.assertEqual(len(self.original_steps),4)
        return status

    def assert_original_journals(self):
        with self.m._db(self.path)as db:
            saved=json.loads(db.execute('SELECT input_json FROM governance_developer_delivery WHERE invocation_id=?',(self.original['invocation_id'],)).fetchone()[0])
            self.assertEqual(saved,self.original_input)
            for old in self.original_steps:
                self.assertEqual(dict(db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?',(old['invocation_id'],old['step_key'])).fetchone()),old)
            self.assertEqual({x[0]for x in db.execute('SELECT DISTINCT invocation_id FROM governance_delivery_step')},{self.original['invocation_id']})

    async def test_single_terminal_lf_cached_readback_finishes_without_recreating_original_objects(self):
        await self.prepare_original();posts=self.f.remote.posts().copy()
        ready=await self.f.deliver();self.assertEqual(ready['state'],'ready_for_review')
        self.assertEqual(self.f.remote.posts().count('/git/commits'),1)
        self.assertEqual(self.f.remote.posts().count('/git/blobs'),2)
        self.assertEqual(self.f.remote.posts().count('/git/trees'),1)
        self.assertEqual(self.f.remote.posts().count('/git/refs'),1)
        self.assertEqual(self.f.remote.posts().count('/pulls'),1)
        self.assertEqual(self.f.remote.posts()[:len(posts)],posts)
        self.assert_original_journals()

    async def test_message_tree_parent_and_commit_identity_drift_still_deny(self):
        await self.prepare_original();remote=self.f.remote.commits[self.commit_sha];saved=deepcopy(remote)
        async def request(method,path,*args):
            self.assertEqual((method,path),('GET','/git/commits/'+self.commit_sha));return deepcopy(remote)
        client=type('ReadOnlyClient',(),{'request':staticmethod(request)})()
        for mutate in (lambda v:v.__setitem__('message',self.commit_body['message'].replace('Snapshot:','snapshot:')),
                       lambda v:v.__setitem__('message',self.commit_body['message'].rstrip('\n')+' '),
                       lambda v:v.__setitem__('message',self.commit_body['message'].replace('\n','\r\n')),
                       lambda v:v.__setitem__('message',self.commit_body['message']+'\n'),
                       lambda v:v['tree'].__setitem__('sha','f'*40),
                       lambda v:v.__setitem__('parents',[{'sha':'f'*40}]),
                       lambda v:v.__setitem__('sha','f'*40)):
            remote.clear();remote.update(deepcopy(saved));mutate(remote)
            with self.assertRaises(self.m.RemoteUnknown):await self.m._commit_result(client,{'sha':self.commit_sha},self.commit_body)
        remote.clear();remote.update(saved)
        with self.assertRaises(self.m.RemoteUnknown):await self.m._commit_result(client,{'sha':'f'*40},self.commit_body)
        self.assert_original_journals()








if __name__=='__main__':unittest.main()


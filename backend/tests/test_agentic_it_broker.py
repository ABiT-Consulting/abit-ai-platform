"""Actual AF_UNIX peer/frame/challenge tests; no privileged service is installed."""

import asyncio
from copy import deepcopy
import os
from pathlib import Path
import secrets
import time
from unittest.mock import patch

from test_agentic_controls_events import Fixture


class BrokerTransport(Fixture):
    async def asyncSetUp(self):
        self.module=self.load('agentic_it_broker')
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.key=self.root/'transport.key';self.key.write_bytes(secrets.token_bytes(32));self.key.chmod(0o600)
        self.path=self.root/'broker.sock';self.calls=[];self.authorizations=[]
        async def prepare(packet):self.calls.append(('prepare',deepcopy(packet)));return {'read_only':True}
        async def launch(payload,grant):self.calls.append(('execute',deepcopy(payload)));return {'state':'uncertain'}
        async def observe(payload):self.calls.append(('observe',deepcopy(payload)));return {'state':'completed','receipt':'original-only'}
        async def authorize(operation,payload):
            self.authorizations.append((operation,deepcopy(payload)))
            return {'authorized':True,'mode':operation,'request_payload_sha256':self.module.digest(payload),
                **{key:value for key,value in payload.items() if key!='plan'},'consumed':True,'receipt_id':'native-original-consumption',
                'plan_digest':payload.get('plan',{}).get('plan_digest',payload.get('plan_digest'))}
        self.authorize=authorize
        self.server=await self.module.BrokerServer(self.path,self.key,client_uid=os.geteuid(),key_owner=os.geteuid(),
            prepare=prepare,launch_original=launch,observe_original=observe).start()
        self.client=self.module.BrokerClient(self.path,self.key,authorize=authorize,server_uid=os.geteuid())

    async def asyncTearDown(self):
        await self.server.close()

    async def test_real_unix_read_preparation_requires_exact_fresh_core_challenge(self):
        self.assertEqual({'read_only':True},await self.client.request('prepare',{'packet':{'task_id':'owned'}}))
        self.assertEqual([('prepare',{'task_id':'owned'})],self.calls)
        self.assertEqual(1,len(self.authorizations))

    async def test_peer_uid_and_wrong_authentication_key_deny_before_any_callback(self):
        wrong=self.module.BrokerClient(self.path,self.key,authorize=self.authorize,server_uid=os.geteuid()+1)
        with self.assertRaisesRegex(ValueError,'peer identity'):await wrong.request('prepare',{'packet':{}})
        other=self.root/'wrong.key';other.write_bytes(secrets.token_bytes(32));other.chmod(0o600)
        wrong=self.module.BrokerClient(self.path,other,authorize=self.authorize,server_uid=os.geteuid())
        with self.assertRaises(ValueError):await wrong.request('prepare',{'packet':{}})
        self.assertFalse(self.calls);self.assertFalse(self.authorizations)

    async def test_missing_or_forged_consumed_grant_cannot_launch_worker(self):
        async def no_approval(operation,payload):return {'authorized':True,'mode':operation,'request_payload_sha256':self.module.digest(payload)}
        self.client.authorize=no_approval
        payload={'action_id':'original-action','invocation_id':'original-invocation','run_id':'original-run','plan':{'plan_digest':'a'*64}}
        with self.assertRaises(ValueError):await self.client.request('execute',payload)
        self.assertFalse(self.calls)

    async def test_observe_is_distinct_read_only_original_tuple(self):
        payload={'action_id':'original-action','invocation_id':'original-invocation','run_id':'original-run','plan_digest':'a'*64}
        self.assertEqual('completed',(await self.client.request('observe',payload))['state'])
        self.assertEqual([('observe',payload)],self.calls)

    async def test_unregistered_body_command_or_oversized_frame_has_zero_operations(self):
        with self.assertRaises(ValueError):await self.client.request('execute',{'command':'do not execute'})
        with self.assertRaisesRegex(ValueError,'fixed bound'):await self.client.request('prepare',{'packet':{'value':'x'*140000}})
        self.assertFalse(self.calls)

    async def test_socket_generation_never_replaces_existing_instance(self):
        other=self.module.BrokerServer(self.path,self.key,client_uid=os.geteuid(),key_owner=os.geteuid(),
            prepare=lambda _:None,launch_original=lambda *_:None,observe_original=lambda _:None)
        with self.assertRaisesRegex(ValueError,'already exists'):await other.start()
        self.assertEqual({'read_only':True},await self.client.request('prepare',{'packet':{}}))

    async def test_client_loss_does_not_cancel_an_accepted_independent_launch(self):
        admitted=asyncio.Event();finish=asyncio.Event();settled=asyncio.Event()
        async def launch(payload,grant):admitted.set();await finish.wait();settled.set();return {'state':'completed'}
        self.server.launch=launch
        task=asyncio.create_task(self.client.request('execute',{'action_id':'a','invocation_id':'i','run_id':'r','plan':{'plan_digest':'a'*64}}))
        await asyncio.wait_for(admitted.wait(),2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        finish.set();await asyncio.wait_for(settled.wait(),2)
        self.assertTrue(settled.is_set())

    async def test_default_registration_off_and_serialized_runtime_is_not_authority(self):
        self.assertIsNone(self.module.configured_runtime({},bridge=None,project_gate=None))
        self.assertIsNone(self.module.configured_runtime({'agentic_it_physical_broker':{'enabled':False}},bridge=None,project_gate=None))
        with self.assertRaises(ValueError):self.module.configured_runtime({'agentic_it_physical_broker':{'enabled':True,'command':'no'}},bridge=None,project_gate=None)

"""Actual independent host read/socket/journal/artifact; controlled native peer."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import time
import types
from unittest.mock import AsyncMock,patch

from test_agentic_controls_events import Fixture,P,T


class IndependentPhysicalQA(Fixture):
    async def asyncSetUp(self):
        self.gateway=self.load('agentic_tool_gateway');self.changes=self.load('agentic_it_changes')
        self.physical=self.load('agentic_it_physical');self.broker=self.load('agentic_it_broker')
        self.resources_module=self.load('agentic_it_broker_resources');self.qa=self.load('agentic_it_qa')
        self.store=self.load('agentic_result_store');self.root=Path(self.temp.name);self.root.chmod(0o700)
        artifacts=self.load('agentic_artifacts')
        artifact_patch=patch.object(artifacts,'artifact_directory',side_effect=lambda row,config:self.root/'qa-artifacts'/row['invocation_id'])
        artifact_patch.start();self.addCleanup(artifact_patch.stop)
        self.output=self.root/'actual-owned-host-file'
        command=(sys.executable,'-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(b"one verified physical mutation\\n")',str(self.output))
        async def post(plan):
            raw=self.output.read_bytes()
            return {'verified':raw==b'one verified physical mutation\n','observed':{'content_sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'checked_at':int(time.time())}}
        handler=self.physical.PhysicalHandler('restart_service','owned-fixture',P,self.packet()['repository'],'development',socket.gethostname(),os.geteuid(),
            lambda plan:[command],post,physical_identity='owned-it-qa-fixture-file')
        it_packet=self.packet(T,'it_manager')
        self.plan={'schema_version':1,'kind':'it_change_plan','operation':'restart_service','resource_id':'owned-fixture',
            'scope':{key:it_packet[key] for key in ('project_id','task_id','repository','environment')},'host_id':socket.gethostname(),
            'policy_digest':'a'*64,'target':{},'baseline':{},'fixed_argv':list(command),'safety':{},'state':'prepared','executable':True}
        self.plan['scope']['actor']=it_packet['binding'];self.plan['plan_digest']=self.changes.plan_authority_digest(self.plan)
        it_packet['tool_action']={'operation':'restart_service','risk':'privileged','request_key':'physical-it-source','parameters':{'resource_id':'owned-fixture','plan_digest':self.plan['plan_digest']}}
        it_packet['content_sha256']=self.bridge_module.packet_digest(it_packet)
        original_packet=self.packet;self.packet=lambda *args,**kwargs:deepcopy(it_packet)
        self.source=self.running('it-source-original',T,'it_manager');self.packet=original_packet
        self.executor=self.physical.DurablePhysicalExecutor(self.root/'host',[handler],run_lookup=lambda invocation:self.source['automation_run_id'])
        self.host=await self.executor.execute_once(self.plan,'action-it-source',self.source['invocation_id'],command)
        action={key:it_packet[key] for key in ('project_id','task_id','task_revision','repository','environment')}
        action.update(schema_version=1,action_id='action-it-source',requested_at=int(time.time()),actor=it_packet['binding'],
                      **{key:it_packet['tool_action'][key] for key in ('operation','risk','parameters')})
        fingerprint=self.qa.action_fingerprint(action)
        consumed={'consumed':True,'action_id':action['action_id'],'run_id':self.source['automation_run_id'],
            'invocation_id':self.source['invocation_id'],'receipt_id':'native-consumed-original','action_fingerprint':fingerprint}
        actual={'original_receipt':self.host}
        with self.gateway._connect(self.path) as db:
            db.execute('INSERT INTO governance_action VALUES(?,?,?,?,?,?,?,?,?,?)',(action['action_id'],self.source['invocation_id'],self.gateway._digest([action['action_id'],fingerprint]),json.dumps(action),json.dumps(self.plan),'completed',json.dumps(consumed),json.dumps(actual),1,1))
        source_action=self.gateway.get_action(self.source['invocation_id'],action['action_id'],self.path)
        action_sha=hashlib.sha256(self.bridge_module.canonical(source_action).encode()).hexdigest()
        result={'outcome':'ready_for_review','evidence':[{'id':'action-receipt','sha256':action_sha}]}
        event={'event_id':'original-it-source-ready-event','invocation_id':self.source['invocation_id'],'run_id':self.source['automation_run_id'],'task_id':T,'result_contract':result}
        publication={'event_id':event['event_id'],'run_id':event['run_id'],'task_id':T,'modified':'native-ready','status':'Pending Review','durable':True}
        self.store.save_result(self.source['invocation_id'],event['run_id'],result,event,self.path)
        self.store.publication_receipt(self.source['invocation_id'],publication,self.path)
        self.g.finish_execution(self.source['invocation_id'],self.source['automation_run_id'],self.source['lease_token'],db_path=self.path)
        self.packet_qa=self.packet(T,'qa');self.packet_qa['implementation_run_id']=self.source['automation_run_id']
        self.packet_qa['content_sha256']=self.bridge_module.packet_digest(self.packet_qa)
        self.g.handoff(source_invocation_id=self.source['invocation_id'],source_run_id=self.source['automation_run_id'],source_lease_token=self.source['lease_token'],
            invocation_id='qa-independent-original',project=P,task=T,from_role='it_manager',target_role='qa',task_revision=self.packet_qa['task_revision'],
            context='Independent current physical acceptance',work_packet=self.packet_qa,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        row=self.g.claim_invocation('qa-independent-original','qa-run-original',gate=self.gate_value,db_path=self.path)
        self.g.mark_submitted(row['invocation_id'],row['automation_run_id'],row['lease_token'],gate=self.gate_value,db_path=self.path)
        self.row=self.g.find(row['invocation_id'],self.path,include_private=True)
        self.proof={'schema_version':1,'project_id':P,'task_id':T,'task_revision':self.packet_qa['task_revision'],
            'qa_run_id':self.row['automation_run_id'],'qa_invocation_id':self.row['invocation_id'],'qa_agent_id':self.row['agent_id'],'qa_erp_user':self.users['qa'],
            'implementation_run_id':self.source['automation_run_id'],'implementation_invocation_id':self.source['invocation_id'],
            'implementation_agent_id':self.source['agent_id'],'implementation_erp_user':self.users['it_manager'],
            'action_id':action['action_id'],'plan_digest':self.plan['plan_digest'],'action_fingerprint':consumed['action_fingerprint'],
            'consumed_approval_receipt_id':consumed['receipt_id'],'ready_event_id':event['event_id'],'ready_result_sha256':self.bridge_module.digest(event),
            'ready_receipt_sha256':self.bridge_module.digest(publication),'action_file_id':'file-native-original','action_file_sha256':action_sha,
            'host_receipt_sha256':self.bridge_module.digest(self.host)}
        self.native_calls=0
        async def request(method,path,**kwargs):self.native_calls+=1;return {'message':deepcopy(self.proof)}
        self.bridge=types.SimpleNamespace(secret='synthetic-only',erp=types.SimpleNamespace(request=request),
            packet=AsyncMock(side_effect=lambda *args:deepcopy(self.packet_qa)))
        key=self.root/'key';key.write_bytes(secrets.token_bytes(32));key.chmod(0o600)
        resource=object.__new__(self.resources_module.RegisteredBrokerResources);resource.executor=self.executor
        self.server=await self.broker.BrokerServer(self.root/'broker.sock',key,client_uid=os.geteuid(),key_owner=os.geteuid(),
            prepare=lambda _:None,launch_original=lambda *_:None,observe_original=lambda _:None,verify_original=resource.verify_original).start()
        self.config={'agentic_it_physical_broker':{'enabled':True,'socket_path':str(self.root/'broker.sock'),'key_file':str(key)},
                     'agentic_artifact_roots':{P:str(self.root/'qa-artifacts')}}
        original=self.broker.BrokerClient.__init__
        def same_uid(client,*args,**kwargs):kwargs['server_uid']=os.geteuid();original(client,*args,**kwargs)
        self.patch=patch.object(self.broker.BrokerClient,'__init__',same_uid);self.patch.start();self.addCleanup(self.patch.stop)

    async def asyncTearDown(self):await self.server.close()

    async def verify(self):
        return await self.qa.verify_original({},row=self.row,packet=self.packet_qa,config=self.config,bridge=self.bridge,db_path=self.path,gateway_call_id='own-qa-call',check_authority=AsyncMock())

    async def test_independent_current_host_read_has_own_artifact_and_exact_final_gate(self):
        observed=await self.verify();artifact=observed['evidence']
        self.assertTrue(observed['verified']);self.assertFalse(observed['executed'])
        result={'outcome':'pass','evidence':[artifact],'test_results':[{'status':'pass','evidence_ids':[artifact['id']]}]}
        self.assertTrue(await self.qa.assert_it_qa_result(self.row,self.packet_qa,result,self.config,self.path,bridge=self.bridge))
        with self.executor._db(False) as db:self.assertEqual(1,db.execute('SELECT count(*) FROM original_operation').fetchone()[0])
        self.assertGreaterEqual(self.native_calls,6)

    async def test_model_only_claim_or_old_implementer_artifact_cannot_pass(self):
        with self.assertRaisesRegex(ValueError,'own exact actual'):
            await self.qa.assert_it_qa_result(self.row,self.packet_qa,{'outcome':'pass','evidence':[]},self.config,self.path,bridge=self.bridge)

    async def test_native_other_source_or_self_qa_proof_denies_before_host_evidence(self):
        self.proof['implementation_run_id']='another-native-run'
        with self.assertRaisesRegex(ValueError,'authoritative IT predecessor proof'):await self.verify()
        with self.g.connect(self.path) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='governance_it_qa_verification'").fetchone())

    async def test_current_host_changed_after_qa_read_cannot_publish_pass(self):
        observed=await self.verify();artifact=observed['evidence'];self.output.write_bytes(b'another mutation')
        result={'outcome':'pass','evidence':[artifact],'test_results':[{'status':'pass','evidence_ids':[artifact['id']]}]}
        with self.assertRaises(ValueError):await self.qa.assert_it_qa_result(self.row,self.packet_qa,result,self.config,self.path,bridge=self.bridge)

    async def test_control_stop_or_missing_existing_bridge_never_grants_new_read(self):
        self.g.project_control(P,'stop',actor='human-fixture',db_path=self.path)
        with self.assertRaises(ValueError):await self.verify()
        with self.assertRaisesRegex(ValueError,'existing governed'):
            await self.qa._native(self.row,self.packet_qa,None,self.path)

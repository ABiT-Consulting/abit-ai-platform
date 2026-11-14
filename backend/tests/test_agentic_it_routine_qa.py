"""Actual temp inspection/quarantine, SQLite handoff, private QA bytes.

Native HTTP is a controlled exact proof peer. Cleanup fixture injects a trusted
no-writer reader only for its owned temporary file; it is not live host proof.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time
import types
from unittest.mock import AsyncMock,patch
SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE/'tests'))
from test_agentic_controls_events import Fixture,P,T
MODULES=SOURCE/'open_webui/utils'

class RoutineQA(Fixture):
    async def asyncSetUp(self):
        self.gateway=self.load('agentic_tool_gateway');self.store=self.load('agentic_result_store')
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.disposable=self.root/'temporary';self.disposable.mkdir(mode=0o700)
        self.quarantine=self.root/'quarantine';self.quarantine.mkdir(mode=0o700)
        self.profile={'host_id':socket.gethostname(),'environment_class':'test','expected_uid':os.geteuid(),
            'roots':{'owned':str(self.root)},'services':[],'operations':['inspect','cleanup_disposable'],
            'disposable_roots':[str(self.disposable)],'quarantine_root':str(self.quarantine),'minimum_free_bytes':0,
            'routine_qa_policy':{'inspect':{'mode':'inspection_v1','minimum_free_bytes':{'owned':0},'require_healthy_services':True},
                                 'cleanup_disposable':'retained_inode_v1'}}
        self.operation='cleanup_disposable' if 'quarantine' in self._testMethodName else 'inspect'
        parameters={'root_keys':['owned'],'services':[]} if self.operation=='inspect' else {'resource_ids':['temporary-one']}
        if self.operation=='cleanup_disposable':
            self.original=self.disposable/'temporary-one.tmp';self.original.write_bytes(b'owned disposable current content\n');self.original.chmod(0o600)
            info=self.original.stat();self.profile['resources']={'temporary-one':{'path':str(self.original),'classification':'temporary','retention_until':int(time.time())-60,
                'device':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,'size':info.st_size,'mtime_ns':info.st_mtime_ns}}
            self.profile['change_preflight']={'host_id':socket.gethostname(),'checked_at':int(time.time()),'retention_policy_verified':True,
                'backup_ref':'owned isolated fixture backup receipt', 'service_impact':'no services in owned fixture',
                'restore_plan':'owned exact inode retained restore'}
        self.packet_it=self.packet(T,'it_manager');self.packet_it['tool_action']={'operation':self.operation,'risk':'routine' if self.operation=='inspect' else 'privileged',
            'request_key':'routine-one','parameters':parameters};self.packet_it['content_sha256']=self.bridge_module.packet_digest(self.packet_it)
        previous=self.packet;self.packet=lambda *args,**kwargs:deepcopy(self.packet_it);self.source=self.running('it-routine-original',T,'it_manager');self.packet=previous
        self.action={key:self.packet_it[key] for key in ('project_id','task_id','task_revision','repository','environment')}
        self.action.update(schema_version=1,action_id='agov-action-'+self.bridge_module.digest([self.source['invocation_id'],self.packet_it['tool_action']])[:48],
            requested_at=int(time.time()),actor=self.packet_it['binding'],**{key:self.packet_it['tool_action'][key] for key in ('operation','risk','parameters')})
        self.scope={'invocation_id':self.source['invocation_id']}
        if self.operation=='inspect':
            self.before=await self.gateway._inspect_preflight(self.action,self.scope,self.profile)
            actual=await self.gateway._inspect(self.action,self.scope,self.before,self.profile);self.consumed=None
        else:
            adapter=self.gateway.cleanup_adapter(writer_probe=lambda device,inode:False)
            self.before=await adapter.preflight(self.action,self.scope,self.profile)
            actual=await adapter.execute(self.action,self.scope,self.before,self.profile)
            self.consumed={'consumed':True,'action_id':self.action['action_id'],'run_id':self.source['automation_run_id'],
                'invocation_id':self.source['invocation_id'],'receipt_id':'native-original-consumption','action_fingerprint':self.gateway._digest({k:self.action[k] for k in
                    ('project_id','task_id','task_revision','actor','repository','environment','operation','risk','parameters')})}
        fingerprint=self.gateway._digest({k:self.action[k] for k in ('project_id','task_id','task_revision','actor','repository','environment','operation','risk','parameters')})
        with self.gateway._connect(self.path) as db:
            db.execute('INSERT INTO governance_action VALUES(?,?,?,?,?,?,?,?,?,?)',(self.action['action_id'],self.source['invocation_id'],self.bridge_module.digest([self.action['action_id'],fingerprint]),
                json.dumps(self.action),json.dumps(self.before),'completed',json.dumps(self.consumed),json.dumps(actual),1,1))
        self.actual=self.gateway.get_action(self.source['invocation_id'],self.action['action_id'],self.path)
        sha=hashlib.sha256(self.bridge_module.canonical(self.actual).encode()).hexdigest()
        result={'outcome':'ready_for_review','evidence':[{'id':'action-receipt','sha256':sha}]}
        event={'event_id':'routine-ready-original','invocation_id':self.source['invocation_id'],'run_id':self.source['automation_run_id'],'task_id':T,'result_contract':result}
        receipt={'event_id':event['event_id'],'run_id':event['run_id'],'task_id':T,'modified':'native-ready','status':'Pending Review','durable':True}
        self.store.save_result(self.source['invocation_id'],event['run_id'],result,event,self.path);self.store.publication_receipt(self.source['invocation_id'],receipt,self.path)
        self.g.finish_execution(self.source['invocation_id'],self.source['automation_run_id'],self.source['lease_token'],db_path=self.path)
        self.packet_qa=self.packet(T,'qa');self.packet_qa['implementation_run_id']=self.source['automation_run_id'];self.packet_qa['content_sha256']=self.bridge_module.packet_digest(self.packet_qa)
        self.g.handoff(source_invocation_id=self.source['invocation_id'],source_run_id=self.source['automation_run_id'],source_lease_token=self.source['lease_token'],
            invocation_id='qa-routine-original',project=P,task=T,from_role='it_manager',target_role='qa',task_revision=self.packet_qa['task_revision'],
            context='Read original retained resource',work_packet=self.packet_qa,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        row=self.g.claim_invocation('qa-routine-original','qa-routine-run',gate=self.gate_value,db_path=self.path)
        self.g.mark_submitted(row['invocation_id'],row['automation_run_id'],row['lease_token'],gate=self.gate_value,db_path=self.path)
        self.row=self.g.find(row['invocation_id'],self.path,include_private=True)
        self.proof={'schema_version':1,'kind':'it_routine_implementation','project_id':P,'task_id':T,'task_revision':self.packet_qa['task_revision'],
            'qa_run_id':self.row['automation_run_id'],'qa_invocation_id':self.row['invocation_id'],'qa_agent_id':self.row['agent_id'],'qa_erp_user':self.users['qa'],
            'implementation_run_id':self.source['automation_run_id'],'implementation_invocation_id':self.source['invocation_id'],
            'implementation_agent_id':self.source['agent_id'],'implementation_erp_user':self.users['it_manager'],'operation':self.operation,
            'action_id':self.action['action_id'],'action_fingerprint':fingerprint,'action_receipt_sha256':self.bridge_module.digest(self.actual),
            'consumed_approval_receipt_id':self.consumed['receipt_id'] if self.consumed else None,
            'ready_event_id':event['event_id'],'ready_result_sha256':self.bridge_module.digest(event),'ready_receipt_sha256':self.bridge_module.digest(receipt),
            'action_file_id':'native-private-original','action_file_sha256':sha}
        self.requests=[]
        async def request(method,path,**kwargs):self.requests.append((method,path));return {'message':deepcopy(self.proof)}
        self.bridge=types.SimpleNamespace(secret='synthetic-only',erp=types.SimpleNamespace(request=request),packet=AsyncMock(side_effect=lambda *args:deepcopy(self.packet_qa)))
        full='open_webui.utils.agentic_it_routine_qa';spec=importlib.util.spec_from_file_location(full,MODULES/'agentic_it_routine_qa.py')
        self.module=importlib.util.module_from_spec(spec);sys.modules[full]=self.module;spec.loader.exec_module(self.module)
        artifacts=self.load('agentic_artifacts');paths=patch.object(artifacts,'artifact_directory',side_effect=lambda row,config:self.root/'artifacts'/row['invocation_id'])
        paths.start();self.addCleanup(paths.stop);self.authority=AsyncMock();self.config={'agentic_tool_gateway':{'projects':{P:self.profile}}}

    async def verify(self):
        return await self.module.verify_original({},row=self.row,packet=self.packet_qa,config=self.config,bridge=self.bridge,db_path=self.path,
            gateway_call_id='own-independent-call',check_authority=self.authority)

    async def accept(self,result):
        contract={'outcome':'pass','evidence':[result['evidence']],'test_results':[{'status':'pass','evidence_ids':[result['evidence']['id']]}]}
        return await self.module.assert_routine_qa_result(self.row,self.packet_qa,contract,self.config,self.path,bridge=self.bridge)

    async def test_inspection_actual_fresh_statvfs_own_bytes_and_immutable_receipt(self):
        result=await self.verify();self.assertTrue(await self.accept(result));self.assertTrue(result['observation']['read_only'])
        self.assertEqual({'GET'},{method for method,path in self.requests});self.assertEqual(2,self.authority.await_count)
        with self.g.connect(self.path) as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM governance_action').fetchone()[0])
            with self.assertRaises(Exception):db.execute('DELETE FROM governance_it_routine_qa')

    async def test_inspection_missing_policy_or_below_minimum_denied_without_adapter_execution(self):
        self.profile['routine_qa_policy']['inspect']['minimum_free_bytes']['owned']=2**80
        with self.assertRaisesRegex(ValueError,'current capacity'):await self.verify()
        self.profile['routine_qa_policy']={}
        with self.assertRaisesRegex(ValueError,'explicit server-owned'):await self.verify()

    async def test_inspection_original_native_proof_change_and_model_only_pass_denied(self):
        with self.assertRaisesRegex(ValueError,'own exact original'):
            await self.module.assert_routine_qa_result(self.row,self.packet_qa,{'outcome':'pass','evidence':[]},self.config,self.path,bridge=self.bridge)
        self.proof['implementation_invocation_id']='another-implementation'
        with self.assertRaisesRegex(ValueError,'Native routine IT source proof differs'):await self.verify()

    async def test_inspection_project_stop_prevents_new_qa_readback(self):
        self.g.project_control(P,'stop',actor='human',policy_revision='current-stop',db_path=self.path)
        with self.assertRaises(ValueError):await self.verify()
        self.assertFalse(self.requests)

    async def test_quarantine_actual_inode_retained_current_hash_and_no_mutating_call(self):
        with patch.object(self.gateway,'cleanup_adapter',side_effect=AssertionError('QA must never execute cleanup')):
            result=await self.verify();self.assertTrue(await self.accept(result))
        observed=result['observation']['resources'][0]
        self.assertFalse(self.original.exists());self.assertTrue(observed['source_absent']);self.assertFalse(observed['original_sha256_available'])
        self.assertEqual(hashlib.sha256(b'owned disposable current content\n').hexdigest(),observed['current_sha256'])
        self.assertFalse(result['observation']['deleted'])

    async def test_quarantine_changed_retained_bytes_or_source_reappearance_denied(self):
        target=Path(self.actual['result']['resources'][0]['quarantine_path']);target.write_bytes(b'changed retained bytes')
        with self.assertRaisesRegex(ValueError,'inode/geometry changed'):await self.verify()

    async def test_quarantine_protected_log_or_symlink_receipt_denied(self):
        self.profile['resources']['temporary-one']['classification']='audit_log'
        with self.assertRaisesRegex(ValueError,'protected or differs'):await self.verify()
        self.profile['resources']['temporary-one']['classification']='temporary'
        receipt=Path(self.actual['result']['manifest_path']);raw=receipt.read_bytes();receipt.unlink();other=self.root/'not-authority';other.write_bytes(raw);receipt.symlink_to(other)
        with self.assertRaises(OSError):await self.verify()

    async def test_quarantine_post_observation_change_and_forged_consumption_denied(self):
        result=await self.verify();self.proof['consumed_approval_receipt_id']='model-selected-approval'
        with self.assertRaisesRegex(ValueError,'Native routine IT source proof differs'):await self.accept(result)


    async def test_inspection_actual_specialist_registry_and_acceptance_hook(self):
        def load(name):
            full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,MODULES/(name+'.py'))
            module=importlib.util.module_from_spec(spec);sys.modules[full]=module;setattr(self.utils,name,module);spec.loader.exec_module(module);return module
        facade=load('agentic_specialist_qa');delivery=load('agentic_gateway_delivery')
        observed=await facade.verify_infrastructure({},row=self.row,packet=self.packet_qa,config=self.config,bridge=self.bridge,db_path=self.path,
            gateway_call_id='actual-specialist-original',check_authority=self.authority)
        await delivery.assert_gateway_result(self.row,self.packet_qa,{'outcome':'pass','evidence':[observed['evidence']],
            'test_results':[{'status':'pass','evidence_ids':[observed['evidence']['id']]}]},self.config,self.path,bridge=self.bridge)
        self.assertEqual({'GET'},{method for method,path in self.requests})

if __name__=='__main__':
    import unittest
    unittest.main()

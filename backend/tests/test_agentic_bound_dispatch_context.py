"""Actual governed DotAdapter/dispatch + ERPClient/reverse channel, no live IO.

Owner-folder lookup and localhost relay acceptance are controlled boundaries;
ERP reads, scope validation, prompt construction and durable Dot dedup are real.
"""
import ast
import asyncio
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock,patch
from urllib.parse import parse_qs,urlsplit

import httpx
import test_agentic_relay_scope as scoped
import test_agentic_governed_task_read as actual

ROOT=Path(__file__).resolve().parents[1]/'open_webui/utils'


def client_module():
    """Use the actual ERPClient class and pure revision/audit implementation."""
    source=(ROOT/'erp_task_resolution.py').read_bytes();tree=ast.parse(source)
    names={'ERP','ASSIGNEE','AGENT_URL','DETAIL','RELAY_STATUS','TASK_INPUTS'}
    nodes=[n for n in tree.body if
           isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id in names for t in n.targets)
           or isinstance(n,ast.FunctionDef)and n.name in {'task_revision','task_audit'}
           or isinstance(n,ast.ClassDef)and n.name=='ERPClient']
    module=types.ModuleType('open_webui.utils.erp_task_resolution')
    module.__dict__.update(actual.module.__dict__)
    module.__dict__.update(hashlib=hashlib,json=json)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/'erp_task_resolution.py'),'exec'),module.__dict__)
    return module


class NativePeer:
    def __init__(self,task,parent,project):
        self.task,self.parent,self.project=task,parent,project
        self.sent=[];self.connection=None
    async def send_text(self,raw):
        frame=json.loads(raw);self.sent.append(frame);parts=urlsplit(frame['path'])
        if parts.path=='/api/resource/Task/'+self.task['name']:body={'data':deepcopy(self.task)}
        elif parts.path=='/api/resource/Task/'+self.parent['name']:body={'data':deepcopy(self.parent)}
        elif parts.path=='/api/resource/Project/'+self.project['name']:body={'data':deepcopy(self.project)}
        elif parts.path=='/api/method/frappe.client.get_value':
            query=parse_qs(parts.query)
            assert query=={'doctype':['Task'],'filters':[self.task['name']],'fieldname':['_assign']}
            body={'message':{'_assign':json.dumps(['ai-developer@abitcons.com'])}}
        elif parts.path=='/api/resource/ToDo':
            query=parse_qs(parts.query);filters=json.loads(query['filters'][0])
            assert ['reference_name','=',self.task['name']]in filters
            body={'data':[{'name':'TODO-EXACT','reference_name':self.task['name']}]}
        else:raise AssertionError('Unexpected native operation: '+parts.path)
        self.connection.pending[frame['id']].set_result({'type':'response','id':frame['id'],'status':200,
            'headers':{'content-type':'application/json'},'body_base64':base64.b64encode(json.dumps(body).encode()).decode()})


class BoundDispatchContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=scoped.RelayScope();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;self.packet=f.packet()
        self.packet.update(activity=[{'id':'ACTIVITY-ORIGINAL','text':'Original activity data'}],
                           attachments=[{'id':'FILE-ORIGINAL','name':'original-spec.txt','is_private':True,'content_hash':'d'*64}],
                           dependencies=['TASK-DEPENDENCY-ORIGINAL'])
        self.packet['content_sha256']=f.bridge_module.packet_digest(self.packet)
        with patch.object(f,'packet',return_value=deepcopy(self.packet)):self.row=f.enqueue()
        self.run_id=f.bridge_module.stable_run_id(self.row['invocation_id'])
        self.task={'name':self.packet['task_id'],'project':self.packet['project_id'],'status':'Working',
                   'subject':'Exact governed fixture','description':'Original Task input','parent_task':'TASK-PARENT',
                   'custom_agentic_managed':1}
        self.parent={'name':'TASK-PARENT','project':self.packet['project_id'],'custom_chatgpt_project_id':'g-p-'+'a'*32}
        self.project={'name':self.packet['project_id'],'custom_chatgpt_chat_url':'https://chatgpt.com/g/g-p-'+'a'*32}
        self.folder={'id':'folder-original','user_id':'owner-fixture','name':'Original owner folder',
                     'meta':{'chatgpt_project_id':'g-p-'+'a'*32,'chatgpt_project_url':self.project['custom_chatgpt_chat_url']}}
        self.client_module=client_module()
        self.module_patch=patch.dict(sys.modules,{'open_webui.utils.erp_task_resolution':self.client_module})
        self.module_patch.start();self.addCleanup(self.module_patch.stop)
        self.execution=f.load('erp_dot_execution');self.execution.STATE_DIR=Path(f.temp.name)
        self.executor=f.load('agentic_executor');self.artifacts=f.load('agentic_artifacts')
        self.channel=actual.channel.ReverseERPChannel(actual.channel.ChannelProfile(enabled=True,bridge_id='controlled-bound-dispatch'),
                                                     'controlled-test-only-no-live-key-00000000')
        self.peer=NativePeer(self.task,self.parent,self.project);connection=actual.channel._Connection(self.peer);self.peer.connection=connection;self.channel._connection=connection
        with patch.object(actual.runtime,'configured_transport',return_value=self.channel.transport()):self.client=actual.runtime.governed_client('/never-read-secret')
        self.sent=[]
        self.folder_lookup=AsyncMock(return_value=self.folder)
        self.saved_reply=AsyncMock(return_value='chat-original')
        self.no_legacy_context=AsyncMock(side_effect=AssertionError('Governed flow reached legacy task_context'))
        for item in (
            patch.object(self.execution,'synced_task_project',self.folder_lookup),
            patch.object(self.execution,'save_dot_reply',self.saved_reply),
            patch.object(self.artifacts,'artifact_directory',lambda row,config:Path(f.temp.name)/'artifacts'/row['invocation_id']),
        ):
            item.start();self.addCleanup(item.stop)
    async def asyncTearDown(self):
        await self.client.client.aclose()
    async def preflight(self):
        return await self.f.check(self.row,self.packet,run_id=self.run_id)
    async def sender(self,envelope,**kwargs):
        payload=self.f.relay.verified_dot_payload(envelope,'a'*64);self.sent.append((deepcopy(envelope),payload))
        provider=self.f.provider(envelope)if'governance_scope'in envelope else {
            'dot_handoff':True,'account_id':'a'*64,'response':'Original legacy controlled reply',
            'conversation_url':'https://chatgpt.com/c/legacy-controlled'}
        return self.f.relay.receipt_from_relay(envelope,provider,'a'*64)
    async def test_actual_adapter_dispatch_preserves_packet_uses_allowed_channel_reads_and_sends_original_once(self):
        proof=await self.preflight();row={**self.row,'_relay_scope_preflight':proof}
        final=deepcopy(proof);final['proof_id']='submission-controlled-final'
        guard=AsyncMock(return_value=final)
        automation=types.SimpleNamespace(id=row['agent_id'],user_id='owner-fixture')
        bridge=types.SimpleNamespace(erp=self.client,config={})
        adapter=self.executor.DotAdapter(None)
        with patch.object(self.execution,'task_context',self.no_legacy_context),patch.object(self.f.relay,'send_via_existing_relay',self.sender):
            result=await adapter.submit(row,self.packet,self.run_id,automation,bridge,before_submit=guard)
            repeated=await adapter.submit(row,self.packet,self.run_id,automation,bridge,before_submit=guard)
        self.assertEqual(result['status'],'delegated');self.assertEqual(repeated['status'],'delegated')
        self.assertEqual(len(self.sent),1);self.no_legacy_context.assert_not_awaited()
        envelope,payload=self.sent[0];prompt=envelope['task']['original_prompt']
        self.assertEqual(prompt.count('GOVERNANCE WORK PACKET\n'),1)
        marker='GOVERNANCE WORK PACKET\n';packet_text=prompt.split(marker,1)[1].split('\n\nVerified ERP context:',1)[0]
        self.assertEqual(json.loads(packet_text),self.packet)
        for token in ('ACTIVITY-ORIGINAL','FILE-ORIGINAL','DEPENDENCY-ORIGINAL'):self.assertEqual(prompt.count(token),1)
        context=json.loads(prompt.split('\n\nVerified ERP context:',1)[1]);self.assertEqual(context['work_packet_digest'],self.f.bridge_module.packet_digest(self.packet))
        self.assertEqual(context['packet_sections']['attachments'],{'included':True,'items':1})
        self.assertEqual(payload['governance_scope']['submission_proof'],final)
        paths=[urlsplit(frame['path']).path for frame in self.peer.sent]
        self.assertIn('/api/method/frappe.client.get_value',paths);self.assertIn('/api/resource/ToDo',paths)
        self.assertNotIn('/api/method/frappe.desk.form.load.get_docinfo',paths)
        self.assertTrue(all('/Custom'not in path for path in paths));self.assertTrue(all(frame['method']=='GET'for frame in self.peer.sent))
        self.assertEqual(self.channel.status()['inflight'],0)
        with self.f.primary.ledger(Path(self.f.temp.name)/'abitdot-primary.sqlite')as db:
            rows=[dict(r)for r in db.execute('SELECT * FROM dot_primary_runs')]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['original_run_id'],self.run_id)
        self.f.primary.verify_saved_scope_receipt(rows[0]);self.saved_reply.assert_awaited()
    async def test_mismatched_packet_role_agent_or_proof_denies_before_durable_send(self):
        proof=await self.preflight();guard=AsyncMock(return_value=proof)
        variants=[]
        for key,value in [('objective','Changed business input'),('project_id','PROJ-FOREIGN'),('task_id','TASK-FOREIGN')]:
            packet=deepcopy(self.packet);packet[key]=value;variants.append(packet)
        for key,value in [('agent_id','foreign-agent'),('role','qa')]:
            packet=deepcopy(self.packet);packet['binding'][key]=value;variants.append(packet)
        with patch.object(self.execution,'task_context',self.no_legacy_context),patch.object(self.f.relay,'send_via_existing_relay',self.sender):
            for packet in variants:
                with self.subTest(packet=packet.get('objective')):
                    with self.assertRaisesRegex(ValueError,'approved work packet'):
                        await self.execution.dispatch_task_dot(self.client,self.task,'owner-fixture',self.row['agent_id'],self.run_id,
                            'Original packet already in prompt','developer',before_submit=guard,policy='Governed',relay_scope_preflight=proof,governed_packet=packet)
        self.assertEqual(self.sent,[]);guard.assert_not_awaited();self.no_legacy_context.assert_not_awaited()
        self.assertFalse((Path(self.f.temp.name)/'abitdot-primary.sqlite').exists())
    async def test_legacy_default_still_calls_original_context_and_preserves_legacy_handoff(self):
        context=AsyncMock(return_value={'latest_run':{'run_id':'legacy-original'},'activity':[{'content':'legacy context'}]})
        self.task['custom_agentic_managed']=0;task=deepcopy(self.task)
        with patch.object(self.execution,'task_context',context),patch.object(self.f.relay,'send_via_existing_relay',self.sender):
            result=await self.execution.dispatch_task_dot(self.client,task,'owner-fixture',self.row['agent_id'],self.run_id,'Legacy original','qa')
        context.assert_awaited_once_with(self.client,task,'qa');self.assertEqual(result['status'],'delegated');self.assertEqual(len(self.sent),1)
        envelope,payload=self.sent[0];self.assertNotIn('governance_scope',envelope);self.assertNotIn('governance_scope',payload)
        self.assertIn('legacy context',envelope['task']['original_prompt']);self.assertIsNone(result['scope_receipt_json'])

if __name__=='__main__':unittest.main()

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
import test_agentic_planning as plan_fixture

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
            # Prompt phase preview uses the same server ledger as this fixture.
            patch.object(f.g, 'path', return_value=f.path),
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
        embedded, end = json.JSONDecoder().raw_decode(packet_text)
        self.assertEqual(embedded, self.packet)
        from open_webui.utils.agentic_gateway_contracts import developer_result_guidance
        self.assertEqual(packet_text[end:].strip(), developer_result_guidance('developer'))
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
    def prepare_identity_packet(self, role, project):
        task = 'TASK-guidance-' + project + '-' + role.replace('_', '-')
        invocation = 'invocation-guidance-' + project + '-' + role
        packet = self.f.packet(task=task, role=role, project=project)
        for key in ('activity', 'attachments', 'dependencies'):
            packet[key] = deepcopy(self.packet[key])
        packet['srs'] = {'revision': 'fixture-srs-' + project, 'sha256': hashlib.sha256(project.encode()).hexdigest()}
        packet['project_backlog'] = plan_fixture.backlog(project)
        self.run_id = self.f.bridge_module.stable_run_id(invocation)
        packet['instructions'] = ('Original source data: https://erp.abit.ai/desk/task/' + task
                                  + '; transport run ' + self.run_id)
        if role == 'solution_architect':
            packet['requirement_ids'] = [item['id'] for item in plan_fixture.PLAN['requirements']]
            packet['acceptance_tests'] = [
                {'id': item['id'], 'requirement_ids': item['requirement_ids'],
                 'method': 'browser' if item['method'] == 'e2e' else item['method'], 'expected': item['expected_result']}
                for item in plan_fixture.PLAN['tests']]
        packet['content_sha256'] = self.f.bridge_module.packet_digest(packet)
        self.f.g.enqueue(invocation_id=invocation, project=project, task=task,
                         from_role='erp' if role == 'project_manager' else 'project_manager',
                         target_role=role, task_revision=packet['task_revision'], context='Original fixture context',
                         work_packet=packet, gate=self.f.gate_value, actor='server-fixture', db_path=self.f.path)
        self.packet = packet
        self.row = self.f.g.find(invocation, self.f.path, include_private=True)
        self.task.update(name=task, project=project)
        self.parent['project'] = project
        self.project['name'] = project

    async def capture_identity_dispatch(self):
        before = deepcopy(self.packet)
        stored = self.row['work_packet']
        proof = await self.preflight()
        row = {**self.row, '_relay_scope_preflight': proof}
        automation = types.SimpleNamespace(id=row['agent_id'], user_id='owner-fixture')
        bridge = types.SimpleNamespace(erp=self.client, config={})
        count = len(self.sent)
        with patch.object(self.execution, 'task_context', self.no_legacy_context), patch.object(
                self.f.relay, 'send_via_existing_relay', self.sender):
            result = await self.executor.DotAdapter(None).submit(
                row, self.packet, self.run_id, automation, bridge, before_submit=AsyncMock(return_value=proof))
        self.assertEqual(result['status'], 'delegated')
        self.assertEqual(len(self.sent), count + 1)
        envelope, payload = self.sent[-1]
        prompt = envelope['task']['original_prompt']
        self.assertEqual(prompt.count('GOVERNANCE WORK PACKET\n'), 1)
        embedded, _ = json.JSONDecoder().raw_decode(prompt.split('GOVERNANCE WORK PACKET\n', 1)[1])
        self.assertEqual(embedded, before)
        self.assertEqual(self.packet, before)
        saved = self.f.g.find(self.row['invocation_id'], self.f.path, include_private=True)
        self.assertEqual(saved['work_packet'], stored)
        self.assertEqual(saved['packet_hash'], self.row['packet_hash'])
        context = json.loads(prompt.split('\n\nVerified ERP context:', 1)[1])
        self.assertEqual(context['work_packet_digest'], before['content_sha256'])
        self.assertEqual(context['latest_run']['run_id'], self.run_id)
        scope = payload['governance_scope']['scope']
        self.assertEqual(scope['run_id'], self.run_id)
        self.assertEqual(scope['invocation_id'], self.row['invocation_id'])
        self.no_legacy_context.assert_not_awaited()
        return prompt

    def guided_architect_result(self, metadata):
        plan = deepcopy(plan_fixture.PLAN)
        plan['metadata'].update(metadata)
        plan['metadata']['project_backlog_context_sha256'] = self.packet['project_backlog']['context_sha256']
        plan['metadata']['project_backlog_snapshot_sha256'] = self.packet['project_backlog']['snapshot_sha256']
        for task in plan['tasks']:
            task['idempotency_key'] = self.packet['project_id'] + '-' + task['task_key']
        evidence = []
        published = []
        for kind, items in (('design', plan['designs']), ('mockup', plan['mockups'])):
            for item in items:
                evidence.append({'id': item['id'], 'kind': kind, 'uri': item['artifact']['uri'],
                                 'sha256': item['artifact']['sha256'], 'requirement_ids': item['requirement_ids']})
                published.append({'id': item['id'], 'kind': kind, 'sha256': item['artifact']['sha256'],
                                  'url': 'https://erp.abit.ai/private/files/' + item['id'] + '.svg?fid=fixture-' + item['id']})
        result = {'schema_version': 1, 'invocation_id': self.row['invocation_id'], 'actor': deepcopy(self.packet['binding']),
                  'project_id': self.packet['project_id'], 'task_id': self.packet['task_id'],
                  'task_revision': self.packet['task_revision'], 'work_packet_digest': self.packet['content_sha256'],
                  'outcome': 'ready_for_review', 'summary': 'Controlled architecture fixture', 'test_results': [],
                  'evidence': evidence, 'implementation_invocation_id': None, 'implementation_agent_id': None,
                  'defects': [], 'architect_plan': plan}
        pm_packet = deepcopy(self.packet)
        pm_packet['binding'].update(role='project_manager', agent_id=self.f.g.agent_id(self.packet['project_id'], 'project_manager'))
        pm_packet['content_sha256'] = self.f.bridge_module.packet_digest(pm_packet)
        return result, pm_packet, {'artifacts': published}

    async def test_architect_guidance_binds_each_project_and_keeps_outer_run_and_source_packet(self):
        from open_webui.utils.agentic_contracts.validator import validate_result

        identities = []
        for project in ('PROJ-0197', 'PROJ-0091'):
            with self.subTest(project=project):
                self.prepare_identity_packet('solution_architect', project)
                prompt = await self.capture_identity_dispatch()
                marker = 'ARCHITECT PLAN METADATA IDENTITY: '
                self.assertEqual(prompt.count(marker), 1)
                metadata = json.loads(prompt.split(marker, 1)[1].split('\n', 1)[0])
                self.assertEqual(metadata, {
                    'project_id': project, 'srs_version': self.packet['srs']['revision'],
                    'srs_sha256': self.packet['srs']['sha256'], 'source_url': 'https://erp.abit.ai/desk/project/' + project,
                    'run_id': self.row['invocation_id'], 'agent_binding': {
                        'project_id': project, 'role': 'solution_architect', 'abit_agent_id': self.row['agent_id'],
                        'erp_ai_user': self.packet['binding']['erp_user']}})
                self.assertNotEqual(metadata['run_id'], self.run_id)
                self.assertIn('OUTER AUTOMATION RUN (transport only): ' + self.run_id, prompt)
                result, pm_packet, event = self.guided_architect_result(metadata)
                self.assertEqual(plan_fixture.architect_contract.validate_plan(result['architect_plan']), [])
                self.assertEqual(validate_result(result, self.packet), [])
                checked, references = plan_fixture.planning._plan(self.row, result, pm_packet, event, None)
                self.assertIs(checked, result['architect_plan'])
                self.assertEqual(set(references), {'DES-ARCH-01', 'MOCK-UI-01'})
                identities.append(metadata)
        self.assertEqual(len(identities), 2)
        self.assertNotEqual(identities[0]['run_id'], identities[1]['run_id'])
        self.assertNotEqual(identities[0]['agent_binding']['abit_agent_id'], identities[1]['agent_binding']['abit_agent_id'])
        self.assertNotEqual(identities[0]['srs_sha256'], identities[1]['srs_sha256'])
        self.assertEqual(len(self.sent), 2)

    async def test_actual_guidance_matches_unchanged_rejection_of_task_url_outer_run_and_foreign_identity(self):
        from open_webui.utils.agentic_contracts.validator import validate_result

        self.prepare_identity_packet('solution_architect', 'PROJ-0197')
        prompt = await self.capture_identity_dispatch()
        self.assertEqual(prompt.count('ARCHITECT PLAN METADATA IDENTITY: '), 1)
        metadata = json.loads(prompt.split('ARCHITECT PLAN METADATA IDENTITY: ', 1)[1].split('\n', 1)[0])
        result, pm_packet, event = self.guided_architect_result(metadata)
        variants = [
            ('source_url', 'https://erp.abit.ai/desk/task/' + self.packet['task_id']),
            ('run_id', self.run_id), ('project_id', 'PROJ-0091'),
            ('srs_version', 'another-SRS'), ('srs_sha256', 'e' * 64),
            ('agent_binding', {**metadata['agent_binding'], 'abit_agent_id': self.f.g.agent_id('PROJ-0091', 'solution_architect')}),
            ('agent_binding', {**metadata['agent_binding'], 'erp_ai_user': 'ai-qa@abitcons.com'}),
            ('agent_binding', {**metadata['agent_binding'], 'role': 'project_manager'}),
        ]
        for key, value in variants:
            with self.subTest(field=key, value=value):
                wrong = deepcopy(result)
                wrong['architect_plan']['metadata'][key] = value
                errors = validate_result(wrong, self.packet)
                self.assertTrue(errors)
                if key == 'source_url':
                    self.assertIn('Architect plan: source SRS URL does not match ERP project', errors)
                elif key == 'run_id':
                    self.assertEqual(plan_fixture.architect_contract.validate_plan(wrong['architect_plan']), [])
                    self.assertIn('Architect plan identity or SRS revision does not match the invocation', errors)
                with self.assertRaises(ValueError):
                    plan_fixture.planning._plan(self.row, wrong, pm_packet, event, None)
        self.assertEqual(validate_result(result, self.packet), [])


    async def test_developer_dispatch_and_context_share_role_specific_failure_guidance(self):
        self.prepare_identity_packet('developer', 'PROJ-0197')
        prompt = await self.capture_identity_dispatch()
        from open_webui.utils.agentic_gateway_contracts import developer_result_guidance
        guidance = developer_result_guidance('developer')
        self.assertEqual(prompt.count(guidance), 1)
        self.assertIn('Only independent QA may return a verification verdict', guidance)
        self.assertIn('previously captured Developer fail as an implementation blocker', guidance)
        self.assertIn('return outcome blocked', guidance)
        self.assertIn('test_results entries with status fail', guidance)
        self.assertNotIn('Outcome continue|ready_for_review|pass|fail|blocked|reviewed', prompt)
        self.assertEqual(len(self.sent), 1)
        operations = self.f.load('agentic_gateway_operations')
        before = deepcopy(self.packet)
        response = await operations.context({}, row=self.row, packet=self.packet, config={}, db_path=self.f.path)
        self.assertEqual(response['result_contract_guidance'].count(guidance), 1)
        self.assertEqual(response['work_packet'], before)
        self.assertEqual(self.packet, before)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(developer_result_guidance('qa'), '')
        self.assertEqual(developer_result_guidance('project_manager'), '')

    async def test_planning_pm_prompt_exposes_exact_result_mode_without_changing_packet(self):
        self.prepare_identity_packet('project_manager', 'PROJ-0197')
        prompt = await self.capture_identity_dispatch()
        self.assertEqual(prompt.count('PLANNING PM RESULT CONTRACT:'), 1)
        from open_webui.utils.agentic_gateway_contracts import planning_pm_result_guidance
        guidance = planning_pm_result_guidance('project_manager', self.packet)
        self.assertIn(guidance, prompt)
        self.assertIn('ready_for_review', guidance)
        self.assertIn('test_results may be empty', guidance)
        self.assertIn('Do not return task_plan or architect_plan', guidance)
        self.assertNotIn('DEDICATED PM QA COORDINATION:', prompt)
        self.assertEqual(len(self.sent), 1)

    async def test_context_read_exposes_same_packet_scoped_planning_pm_guidance(self):
        self.prepare_identity_packet('project_manager', 'PROJ-0197')
        before = deepcopy(self.packet)
        operations = self.f.load('agentic_gateway_operations')
        response = await operations.context({}, row=self.row, packet=self.packet, config={}, db_path=self.f.path)
        self.assertIn('PLANNING PM RESULT CONTRACT:', response['result_contract_guidance'])
        from open_webui.utils.agentic_gateway_contracts import model_contracts, planning_pm_result_guidance
        guidance = planning_pm_result_guidance('project_manager', self.packet)
        self.assertEqual(response['result_contract_guidance'].count(guidance), 1)
        self.assertEqual(response['result_contract_guidance'],
                         model_contracts('project_manager', packet=self.packet)['result_contract_guidance'])
        self.assertEqual(response['work_packet'], before)
        self.assertEqual(self.packet, before)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.peer.sent, [])

    async def test_actual_architecture_phase_keeps_shared_guidance_and_exact_packet(self):
        self.prepare_identity_packet('solution_architect', 'PROJ-0197')
        source_id, source_run = self.row['invocation_id'], self.run_id
        metadata = {
            'project_id': self.packet['project_id'], 'srs_version': self.packet['srs']['revision'],
            'srs_sha256': self.packet['srs']['sha256'],
            'source_url': 'https://erp.abit.ai/desk/project/' + self.packet['project_id'],
            'run_id': source_id, 'agent_binding': {
                'project_id': self.packet['project_id'], 'role': 'solution_architect',
                'abit_agent_id': self.row['agent_id'], 'erp_ai_user': self.packet['binding']['erp_user']},
        }
        result, pm_packet, event = self.guided_architect_result(metadata)
        owned = self.f.g.claim_invocation(source_id, source_run, gate=self.f.gate_value, db_path=self.f.path)
        self.f.g.mark_submitted(source_id, source_run, owned['lease_token'],
                                gate=self.f.gate_value, db_path=self.f.path)
        event.update(schema_version=1, event_id='controlled-published-architect', type='run.ready',
                     acceptance_passed=False, invocation_id=source_id, run_id=source_run,
                     project_id=self.packet['project_id'], task_id=self.packet['task_id'],
                     role='solution_architect', agent_id=self.row['agent_id'],
                     work_packet_digest=self.packet['content_sha256'],
                     task_revision=self.packet['task_revision'], result_contract=result)
        store = self.f.load('agentic_result_store')
        store.save_result(source_id, source_run, result, event, self.f.path)
        store.publication_receipt(source_id, {
            'event_id': event['event_id'], 'task_id': self.packet['task_id'],
            'status': 'Pending Review', 'modified': 'controlled-published-receipt',
            'run_id': source_run, 'durable': True}, self.f.path)
        self.f.g.finish_execution(source_id, source_run, owned['lease_token'],
                                  'execution_finished', db_path=self.f.path)
        context = self.f.bridge_module.canonical({
            'predecessor_invocation': source_id, 'architect_plan': result['architect_plan'],
            'published_artifacts': event['artifacts'], 'instruction': 'Review exact published fixture.'})
        invocation = 'invocation-guidance-published-architecture-pm'
        self.f.g.handoff(source_invocation_id=source_id, source_run_id=source_run,
                         source_lease_token=owned['lease_token'], invocation_id=invocation,
                         project=self.packet['project_id'], task=self.packet['task_id'],
                         from_role='solution_architect', target_role='project_manager',
                         task_revision=pm_packet['task_revision'], context=context, work_packet=pm_packet,
                         gate=self.f.gate_value, actor='server-fixture', db_path=self.f.path)
        self.packet = pm_packet
        self.row = self.f.g.find(invocation, self.f.path, include_private=True)
        self.run_id = self.f.bridge_module.stable_run_id(invocation)
        phase = self.f.load('agentic_architecture_review')
        self.assertTrue(phase.architecture_context(self.row, self.packet, db_path=self.f.path))
        prompt = await self.capture_identity_dispatch()
        operations = self.f.load('agentic_gateway_operations')
        response = await operations.context({}, row=self.row, packet=self.packet, config={}, db_path=self.f.path)
        from open_webui.utils.agentic_gateway_contracts import planning_pm_result_guidance
        guidance = planning_pm_result_guidance('project_manager', self.packet)
        self.assertEqual(prompt.count(guidance), 1)
        self.assertEqual(response['result_contract_guidance'].count(guidance), 1)
        self.assertIn('PM ARCHITECTURE REVIEW PHASE: architecture-review.v1.', prompt)
        self.assertIn('architecture-review.v1', response['pm_phase_guidance'])
        for text in (prompt, response['pm_phase_guidance']):
            self.assertIn('test_results=[]', text)
            self.assertIn('defects=[]', text)
        self.assertNotIn('PM DELEGATION PHASE:', prompt)
        self.assertNotIn('DEDICATED PM QA COORDINATION:', prompt)
        self.assertNotIn('pm_review.write', response['operations'])
        self.assertEqual(response['work_packet'], self.packet)
        self.assertEqual(store.get_result(source_id, self.f.path)['result'], result)
        self.assertEqual(len(self.sent), 1)

    async def test_planning_pm_evidence_with_no_test_claims_passes_existing_strict_validator(self):
        from open_webui.utils.agentic_contracts.validator import validate_result
        self.prepare_identity_packet('project_manager', 'PROJ-0197')
        before = deepcopy(self.packet)
        review = Path(self.f.temp.name) / 'planning-review.txt'
        review.write_bytes(b'Controlled review of the frozen planning source and acceptance scope.\n')
        result = {
            'schema_version': 1, 'invocation_id': self.row['invocation_id'],
            'actor': deepcopy(self.packet['binding']), 'project_id': self.packet['project_id'],
            'task_id': self.packet['task_id'], 'task_revision': self.packet['task_revision'],
            'work_packet_digest': self.packet['content_sha256'], 'outcome': 'ready_for_review',
            'summary': 'Reviewed the controlled planning fixture; no acceptance tests executed.',
            'test_results': [], 'implementation_invocation_id': None, 'implementation_agent_id': None,
            'defects': [], 'evidence': [{'id': 'PM-REVIEW', 'kind': 'checkpoint',
                'uri': 'artifact://planning-review.txt', 'sha256': hashlib.sha256(review.read_bytes()).hexdigest(),
                'requirement_ids': self.packet['requirement_ids']}],
        }
        self.assertEqual(validate_result(result, self.packet), [])
        self.assertEqual(self.executor.decode_result('AGOV RESULT: ' + json.dumps(result),
                         self.packet, self.row['invocation_id']), result)
        wrong = deepcopy(result);wrong['outcome'] = 'reviewed'
        errors = validate_result(wrong, self.packet)
        self.assertIn('Only the registered PM may acknowledge a bound QA coordination review', errors)
        self.assertIn('PM acknowledgement requires complete observed review tests and real evidence', errors)
        with self.assertRaisesRegex(ValueError, 'bound QA coordination review'):
            self.executor.decode_result('AGOV RESULT: ' + json.dumps(wrong), self.packet, self.row['invocation_id'])
        missing = deepcopy(result);missing['evidence'] = []
        self.assertIn('Ready for review requires concrete evidence references', validate_result(missing, self.packet))
        wrong = deepcopy(result);wrong['architect_plan'] = deepcopy(plan_fixture.PLAN)
        self.assertIn('Only the Solution Architect may submit an architecture plan', validate_result(wrong, self.packet))
        for key, value in [('task_plan', {}), ('qa_review_ack', 'a' * 64)]:
            wrong = deepcopy(result);wrong[key] = value
            self.assertTrue(validate_result(wrong, self.packet))
        wrong = deepcopy(result);wrong['test_results'] = [{
            'test_id': 'UNOBSERVED-FOREIGN-TEST', 'status': 'pass',
            'observed': 'Unsupported test claim', 'evidence_ids': ['PM-REVIEW']}]
        self.assertTrue(validate_result(wrong, self.packet))
        self.assertEqual(self.packet, before)
        self.assertEqual(self.sent, [])

    async def test_non_architect_roles_receive_no_architect_metadata_identity(self):
        for role in ('project_manager', 'developer', 'qa', 'operations', 'it_manager'):
            with self.subTest(role=role):
                self.prepare_identity_packet(role, 'PROJ-0197')
                predecessor = {'invocation_id': 'fixture-prior-implementation',
                               'agent_id': self.f.g.agent_id('PROJ-0197', 'developer')}
                with patch.object(self.executor, 'implementation_source', return_value=predecessor) as source:
                    prompt = await self.capture_identity_dispatch()
                self.assertNotIn('ARCHITECT PLAN METADATA IDENTITY:', prompt)
                self.assertNotIn('OUTER AUTOMATION RUN (transport only):', prompt)
                self.assertNotIn('ARCHITECT BACKLOG CONTRACT:', prompt)
                if role == 'qa':
                    source.assert_called_once()
                    self.assertIn('fixture-prior-implementation', prompt)
                else:
                    source.assert_not_called()
        self.assertEqual(len(self.sent), 5)

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

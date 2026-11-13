"""Real ledger/gateway IT flow with fake ERP and only temporary-file inspection."""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import time
import types
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T


class ApprovalBridge:
    def __init__(self, fixture):
        self.f = fixture
        self.erp = self
        self.secret = 'fixture-only'
        self.calls = []
        self.drafts = {}
        self.checkpoints = {}
        self.approval = None
        self.lose_draft = False
        self.lose_checkpoint = False
        self.absent_checkpoint = False

    async def packet(self, *args, **kwargs):
        return self.f.packet(role='it_manager')

    async def request(self, method, path, **kwargs):
        operation = path.rsplit('.', 1)[-1]
        self.calls.append((method, operation, deepcopy(kwargs)))
        args = kwargs.get('json', {})
        if operation == 'request_action_approval':
            key = args['approval_id']
            prior = self.drafts.setdefault(key, deepcopy(args))
            if prior != args:
                raise AssertionError('Approval draft changed under original key')
            if self.lose_draft:
                self.lose_draft = False
                raise TimeoutError('Draft committed; response lost')
            return {'message': {'approval_id': key}}
        if operation == 'get_result_receipt':
            key = kwargs['params']['event_id']
            value = self.checkpoints.get(key)
            return {
                'message': (
                    {'exists': False}
                    if self.absent_checkpoint or not value
                    else {'exists': True, 'receipt': deepcopy(value)}
                )
            }
        if operation == 'get_action_approval':
            return {'message': deepcopy(self.approval)}
        if operation == 'consume_action_approval':
            return {
                'message': {
                    'consumed': True,
                    'approval_id': args['approval_id'],
                    'action_id': args['action']['action_id'],
                    'action_fingerprint': args['action_fingerprint'],
                    'receipt_id': 'approval-consumption-fixture',
                }
            }
        raise AssertionError((method, path))

    async def publish(self, event):
        self.calls.append(('POST', 'publish', deepcopy(event)))
        receipt = {key: event[key] for key in ('event_id', 'run_id', 'task_id')}
        receipt['modified'] = 'modified-checkpoint'
        self.checkpoints[event['event_id']] = receipt
        if self.lose_checkpoint:
            self.lose_checkpoint = False
            raise TimeoutError('Checkpoint committed; response lost')
        return receipt

    def count(self, operation):
        return sum(call[1] == operation for call in self.calls)


class ITExecution(Fixture):
    def setUp(self):
        super().setUp()
        self.template = {'request_key': 'fixture-inspect', 'operation': 'inspect', 'risk': 'routine', 'parameters': {}}
        self.gateway = self.load('agentic_tool_gateway')
        self.artifacts = self.load('agentic_artifacts')
        self.changes=self.load('agentic_it_changes')
        self.it = self.load('agentic_it_execution')
        self.contracts = __import__('open_webui.utils.agentic_contracts.validator', fromlist=['validator'])
        self.root = Path(self.temp.name)
        self.artifact_root = self.root / 'evidence'
        root_patch = patch.object(self.artifacts, 'artifact_directory', return_value=self.artifact_root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.config = {
            'agentic_tool_gateway': {
                'projects': {
                    P: {
                        'host_id': socket.gethostname(),
                        'roots': {'fixture': str(self.root)},
                        'services': [],
                        'operations': ['inspect', 'restart_service', 'install_official_package'],
                        'environment_class': 'development',
                    }
                }
            }
        }
        self.bridge = ApprovalBridge(self)
        self.host_calls = 0
        self.completed = []

        async def complete(row, packet, record, adapter, automation, bridge, receipt, gate, config, db_path):
            self.completed.append((deepcopy(row), deepcopy(packet), deepcopy(record)))
            self.g.finish_execution(row['invocation_id'], row['automation_run_id'], row['lease_token'], db_path=db_path)
            return {'completed_original': row['automation_run_id']}

        executor = types.ModuleType('open_webui.utils.agentic_executor')
        executor.complete_original = complete
        executor.DotAdapter = types.SimpleNamespace(
            submit=AsyncMock(side_effect=AssertionError('IT may never call Dot'))
        )
        automations = types.ModuleType('open_webui.models.automations')
        automations.Automations = types.SimpleNamespace(
            get_by_id=AsyncMock(return_value=types.SimpleNamespace(id='fixture-automation'))
        )
        self.runtime_patch = patch.dict(
            sys.modules, {'open_webui.utils.agentic_executor': executor, 'open_webui.models.automations': automations}
        )
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def packet(self, task=T, role='developer', project=P):
        value = super().packet(task, role, project)
        if role == 'it_manager':
            value['tool_action'] = deepcopy(self.template)
            value['tool_scopes'] = ['inspect_infrastructure', 'execute_infrastructure']
            value['content_sha256'] = self.bridge_module.packet_digest(value)
        return value

    def adapter(self):
        return self.it.ITAdapter(None, self.gate, self.config, self.path, self.registry())

    def registry(self):
        async def before(*args):
            return {'fixture_preflight': True}

        async def execute(*args):
            self.host_calls += 1
            return {'fixture_registered_handler_completed': True}

        return {'restart_service': self.gateway.TrustedAdapter(before, execute, capability='execute_infrastructure')}

    def privileged(self):
        self.template = {
            'request_key': 'fixture-restart',
            'operation': 'restart_service',
            'risk': 'privileged',
            'parameters': {},
        }

    def state(self, row):
        return self.g.find(row['invocation_id'], self.path, include_private=True)

    def pending(self, row):
        with self.g.connect(self.path) as db:
            return dict(
                db.execute(
                    'SELECT * FROM governance_action_wait WHERE invocation_id=?', (row['invocation_id'],)
                ).fetchone()
            )

    async def wait(self):
        self.privileged()
        row = self.running(role='it_manager')
        adapter = self.adapter()
        packet = self.packet(role='it_manager')
        try:
            await adapter.submit(row, packet, row['automation_run_id'], None, self.bridge, before_submit=AsyncMock())
        except self.gateway.ApprovalRequired as error:
            await self.it.suspend(
                row['invocation_id'], packet, adapter, self.bridge, {'modified': 'modified-started'}, error, self.path
            )
        else:
            raise AssertionError('Privileged fixture requires human approval')
        return row, adapter

    async def resume(self, row):
        return await self.it.resume(
            row['invocation_id'],
            config=self.config,
            project_gate=self.gate,
            bridge=self.bridge,
            app=None,
            db_path=self.path,
            adapters=self.registry(),
        )

    def approve(self, row, **override):
        action = self.it.make_action(row, self.packet(role='it_manager'))
        now = int(time.time())
        self.bridge.approval = {
            'schema_version': 1,
            'approval_id': self.pending(row)['approval_id'],
            'project_id': P,
            'task_id': T,
            'action_fingerprint': self.contracts.action_fingerprint(action),
            'approved_by': 'human@example.invalid',
            'approved_at': now - 1,
            'expires_at': now + 600,
            'status': 'approved',
            'used_by_action_id': None,
            **override,
        }

    async def test_preflight_rejects_missing_action_and_unimplemented_operation(self):
        row = self.enqueue(role='it_manager')
        packet = self.packet(role='it_manager')
        del packet['tool_action']
        with self.assertRaises(self.gateway.CapabilityUnavailable):
            await self.adapter().preflight(row, packet)
        packet = self.packet(role='it_manager')
        packet['tool_action']['operation'] = 'deploy_release'
        self.config['agentic_tool_gateway']['projects'][P]['operations'].append('deploy_release')
        with self.assertRaises(self.gateway.CapabilityUnavailable):
            await self.adapter().preflight(row, packet)
        self.assertEqual(self.state(row)['state'], 'queued')
        self.assertEqual(self.host_calls, 0)

    async def test_readonly_inspect_records_real_receipt_without_dot_or_model(self):
        row = self.running(role='it_manager')
        packet = self.packet(role='it_manager')
        before = AsyncMock()
        adapter = self.adapter()
        await adapter.preflight(row, packet)
        record = await adapter.submit(row, packet, row['automation_run_id'], None, self.bridge, before_submit=before)
        before.assert_awaited_once()
        self.assertEqual(record['model_requests'], 0)
        self.assertEqual(record['transport'], 'trusted-tool-gateway')
        result = json.loads(record['response_text'].removeprefix('AGOV RESULT: '))
        receipt = json.loads((self.artifact_root / 'trusted-action-receipt.json').read_text())
        self.assertGreater(receipt['result']['roots']['fixture']['total_bytes'], 0)
        self.assertTrue(receipt['result']['read_only'])
        self.assertEqual(result['outcome'], 'ready_for_review')
        self.assertEqual(result['test_results'][0]['status'], 'skipped')
        self.assertEqual(os.stat(self.artifact_root / 'trusted-action-receipt.json').st_mode & 0o777, 0o640)
        self.assertEqual(self.it.make_record(row, packet, receipt, self.config), record)
        receipt['result']['changed'] = True
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            self.it.make_record(row, packet, receipt, self.config)

    async def test_privileged_wait_and_approval_resume_same_run_action_once(self):
        row, adapter = await self.wait()
        action_id = adapter.action['action_id']
        run = row['automation_run_id']
        self.assertEqual(self.state(row)['state'], 'awaiting_approval')
        self.assertIsNone(self.gateway.get_action(row['invocation_id'], action_id, self.path))
        self.assertIsNone(await self.resume(row))
        self.assertEqual(self.state(row)['automation_run_id'], run)
        self.assertEqual(self.host_calls, 0)
        self.approve(row)
        result = await self.resume(row)
        self.assertEqual(result, {'completed_original': run})
        self.assertEqual(self.host_calls, 1)
        self.assertEqual(self.gateway.get_action(row['invocation_id'], action_id, self.path)['state'], 'completed')
        self.assertIsNone(await self.resume(row))
        self.assertEqual(self.host_calls, 1)
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)

    async def test_lost_draft_response_repairs_only_original_draft_and_checkpoint(self):
        self.bridge.lose_draft = True
        with self.assertRaises(TimeoutError):
            await self.wait()
        row = self.g.list_invocations(P, self.path)[0]
        row = self.state(row)
        pending = self.pending(row)
        self.assertEqual(row['state'], 'awaiting_approval')
        self.assertEqual(pending['checkpoint_phase'], 'pending')
        original = json.loads(pending['request_json'])
        self.assertTrue(await self.it.reconcile_wait(row['invocation_id'], self.bridge, self.path))
        self.assertEqual(self.pending(row)['checkpoint_phase'], 'published')
        self.assertEqual(len(self.bridge.drafts), 1)
        self.assertEqual(next(iter(self.bridge.drafts.values())), original)
        self.assertEqual(self.host_calls, 0)

    async def test_lost_checkpoint_response_uses_receipt_get_without_repeat_post(self):
        self.bridge.lose_checkpoint = True
        with self.assertRaises(TimeoutError):
            await self.wait()
        row = self.state(self.g.list_invocations(P, self.path)[0])
        self.assertEqual(self.pending(row)['checkpoint_phase'], 'uncertain')
        self.bridge.absent_checkpoint = True
        self.assertFalse(await self.it.reconcile_wait(row['invocation_id'], self.bridge, self.path))
        self.bridge.absent_checkpoint = False
        self.assertTrue(await self.it.reconcile_wait(row['invocation_id'], self.bridge, self.path))
        self.assertEqual(self.bridge.count('publish'), 1)
        self.assertEqual(self.bridge.count('get_result_receipt'), 2)
        self.assertEqual(self.host_calls, 0)

    async def test_expired_denied_or_self_approval_keeps_original_wait_without_host_intent(self):
        row, adapter = await self.wait()
        for override in (
            {'expires_at': int(time.time()) - 1},
            {'status': 'revoked'},
            {'approved_by': 'ai-operations@abitcons.com'},
        ):
            self.approve(row, **override)
            self.assertIsNone(await self.resume(row))
            self.assertEqual(self.state(row)['state'], 'awaiting_approval')
            self.assertEqual(self.state(row)['automation_run_id'], row['automation_run_id'])
            self.assertIsNone(self.gateway.get_action(row['invocation_id'], adapter.action['action_id'], self.path))
            self.assertEqual(self.host_calls, 0)

    async def test_expired_draft_renews_id_with_fresh_human_decision_without_action_replay(self):
        row, adapter = await self.wait()
        pending = self.pending(row)
        args = json.loads(pending['request_json'])
        old = args['approval_id']
        args['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.g._transaction(self.path) as db:
            db.execute(
                'UPDATE governance_action_wait SET request_json=? WHERE invocation_id=?',
                (json.dumps(args), row['invocation_id']),
            )
        self.assertIsNone(await self.resume(row))
        renewed = self.pending(row)
        self.assertNotEqual(renewed['approval_id'], old)
        self.assertEqual(renewed['renewals'], 1)
        self.assertEqual(self.host_calls, 0)
        self.approve(row)
        await self.resume(row)
        self.assertEqual(self.host_calls, 1)
        self.assertEqual(self.completed[0][0]['automation_run_id'], row['automation_run_id'])

    async def test_paused_task_and_pm_checkbox_off_block_before_host_execution(self):
        row, _ = await self.wait()
        self.approve(row)
        self.controls.task_control(P, T, 'pause', actor='human@example.invalid', db_path=self.path)
        with self.assertRaises(ValueError):
            await self.resume(row)
        self.assertEqual(self.host_calls, 0)
        self.controls.task_control(P, T, 'resume', actor='human@example.invalid', db_path=self.path)
        self.gate_value['pm_task_automation_enabled'] = False
        self.assertIsNone(await self.resume(row))
        self.assertEqual(self.host_calls, 0)
        self.assertEqual(self.state(row)['state'], 'awaiting_approval')

    def unavailable(self):
        self.template={'request_key':'fixture-change','operation':'restart_service','risk':'privileged','parameters':{'resource_id':'fixture-service'}}
        return self.it.ITAdapter(None,self.gate,self.config,self.path)

    async def prepared(self):
        adapter=self.unavailable();row=self.running(role='it_manager');packet=self.packet(role='it_manager')
        record=await adapter.submit(row,packet,row['automation_run_id'],None,self.bridge,before_submit=AsyncMock())
        return row,packet,adapter,record

    async def test_default_unavailable_preparation_becomes_typed_blocked_checkpoint_without_approval_or_host_intent(self):
        row,packet,adapter,record=await self.prepared()
        result=json.loads(record['response_text'].removeprefix('AGOV RESULT: '))
        self.assertEqual(result['outcome'],'blocked');self.assertEqual(record['model_requests'],0)
        self.assertEqual(self.contracts.validate_result(result,packet),[])
        self.assertGreaterEqual(len(result['defects']),1);self.assertLessEqual(len(result['defects']),5)
        self.assertEqual(result['evidence'][0]['kind'],'checkpoint');self.assertTrue(all(test['status']=='skipped' for test in result['test_results']))
        self.assertIsNone(self.gateway.get_action(row['invocation_id'],adapter.action['action_id'],self.path))
        self.assertEqual(self.bridge.count('request_action_approval'),0);self.assertEqual(self.bridge.count('consume_action_approval'),0);self.assertEqual(self.host_calls,0)
        _,content=self.artifacts.local_bytes(self.artifact_root,result['evidence'][0]['uri']);plan=json.loads(content)
        self.assertEqual(plan['state'],'prepared_unavailable');self.assertFalse(plan['executable']);self.assertEqual(plan['execution_state'],'not_executed')
        self.assertEqual(self.bridge_module.digest(plan),result['evidence'][0]['sha256'])
        repeated=await adapter.submit(row,packet,row['automation_run_id'],None,self.bridge,before_submit=AsyncMock())
        self.assertEqual(record,repeated)

    async def test_many_missing_preconditions_are_grouped_without_losing_codes_or_leaking_profile_extras(self):
        adapter=self.unavailable();profile=self.config['agentic_tool_gateway']['projects'][P];profile['expected_uid']=os.getuid()
        secret='DO-NOT-EXPORT-this-server-secret'
        profile.update(secret_token=secret,change_resources={'restart_service':{'fixture-service':{'enabled':True,'exclusive_to_project':True,'project_id':P,'host_id':socket.gethostname(),'repository':self.packet(role='it_manager')['repository'],'environment':'development','minimum_free_bytes':0,'unit':'fixture.service','unit_sha256':'1'*64,'password':secret}}})
        readers=self.changes.ChangeReaders(safety=lambda *a:{'host_id':socket.gethostname(),'project_id':P,'environment':'development','checked_at':int(time.time())})
        with patch.object(self.changes,'LinuxReadOnlyReaders',return_value=readers):adapter=self.it.ITAdapter(None,self.gate,self.config,self.path)
        row=self.running(role='it_manager');packet=self.packet(role='it_manager')
        record=await adapter.submit(row,packet,row['automation_run_id'],None,self.bridge,before_submit=AsyncMock())
        result=json.loads(record['response_text'].removeprefix('AGOV RESULT: '));_,content=self.artifacts.local_bytes(self.artifact_root,result['evidence'][0]['uri']);plan=json.loads(content)
        self.assertGreater(len(plan['prerequisites']),5);self.assertLessEqual(len(result['defects']),5)
        observed='\n'.join(defect['observed'] for defect in result['defects'])
        for item in plan['prerequisites']:self.assertIn(item['code'],observed)
        self.assertNotIn(secret,content.decode());self.assertNotIn('password',content.decode());self.assertNotIn('secret_token',content.decode())
        self.assertEqual(self.contracts.validate_result(result,packet),[])

    async def test_prepared_plan_recovery_uses_exact_original_bytes_without_preflight_or_dispatch(self):
        row,packet,adapter,record=await self.prepared()
        self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'uncertain',db_path=self.path)
        with patch.object(self.it,'execute_action',AsyncMock(side_effect=AssertionError('No action replay'))),patch.object(self.changes,'prepare_change',AsyncMock(side_effect=AssertionError('No host readers/re-preparation'))):
            value=await self.it.recover_completed(row['invocation_id'],config=self.config,project_gate=self.gate,bridge=self.bridge,db_path=self.path,adapter=adapter,automation=types.SimpleNamespace(id='fixture-automation'))
        self.assertEqual(value,{'completed_original':row['automation_run_id']});self.assertEqual(self.completed[-1][2],record)
        self.assertEqual(len(self.g.list_invocations(P,self.path)),1);self.assertEqual(self.host_calls,0)
        self.assertEqual(self.bridge.count('consume_action_approval'),0)

    async def test_forged_crossrun_or_modified_checkpoint_cannot_recover_unknown_work(self):
        row,packet,adapter,record=await self.prepared();self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'uncertain',db_path=self.path)
        filename=json.loads(record['response_text'].removeprefix('AGOV RESULT: '))['evidence'][0]['uri'].removeprefix('artifact://');file=self.artifact_root/filename;original=file.read_bytes()
        file.write_bytes(b'{"state":"prepared"}')
        self.assertIsNone(await self.it.recover_completed(row['invocation_id'],config=self.config,project_gate=self.gate,bridge=self.bridge,db_path=self.path))
        file.write_bytes(original)
        with self.g._transaction(self.path) as db:db.execute("UPDATE governance_prepared_it SET run_id='forged-other-run'")
        self.assertIsNone(await self.it.recover_completed(row['invocation_id'],config=self.config,project_gate=self.gate,bridge=self.bridge,db_path=self.path))
        with self.g._transaction(self.path) as db:db.execute('DELETE FROM governance_prepared_it')
        self.assertIsNone(await self.it.recover_completed(row['invocation_id'],config=self.config,project_gate=self.gate,bridge=self.bridge,db_path=self.path))
        self.assertEqual(self.state(row)['state'],'uncertain');self.assertEqual(self.completed,[])

    async def test_unavailable_plan_cannot_reclassify_any_existing_unknown_host_intent(self):
        adapter=self.unavailable();row=self.running(role='it_manager');packet=self.packet(role='it_manager');action=self.it.make_action(row,packet)
        self.gateway.get_action(row['invocation_id'],action['action_id'],self.path)
        with self.g._transaction(self.path) as db:
            db.execute("INSERT INTO governance_action VALUES(?,?,?,?,?,'uncertain',NULL,NULL,?,?)",(action['action_id'],row['invocation_id'],self.gateway._digest([action['action_id'],self.contracts.action_fingerprint(action)]),json.dumps(action),'{}',int(time.time()),int(time.time())))
        with patch.object(self.changes,'prepare_change',AsyncMock(side_effect=AssertionError('Never preflight unknown action'))),self.assertRaisesRegex(RuntimeError,'uncertain'):
            await adapter.submit(row,packet,row['automation_run_id'],None,self.bridge,before_submit=AsyncMock())
        self.assertEqual(self.host_calls,0);self.assertIsNone(self.it._prepared_record(row,packet,self.config,self.path))

    async def test_unknown_plan_metadata_file_urls_and_changed_artifact_bytes_are_rejected(self):
        row,packet,adapter,record=await self.prepared();action=adapter.action
        result=json.loads(record['response_text'].removeprefix('AGOV RESULT: '));_,raw=self.artifacts.local_bytes(self.artifact_root,result['evidence'][0]['uri']);plan=json.loads(raw)
        for change in ({'server_profile':{'password':'never-export'}},{'target':{'official_source':{'origin':'file:///private/credentials'}}},{'target':'unstructured'}):
            bad={**deepcopy(plan),**change}
            with self.assertRaises(ValueError):self.it._validate_prepared(bad,packet,action)
        error=self.changes.PreparedChangeUnavailable(plan);error.artifact['content']+=b' '
        with self.assertRaisesRegex(ValueError,'exact bytes'):self.it._save_prepared(row,packet,action,error,self.config,self.path)
        self.assertEqual(self.it._prepared_record(row,packet,self.config,self.path),record)

    async def test_post_intent_host_uncertainty_never_becomes_a_prepared_blocker(self):
        row,_=await self.wait();self.approve(row)
        async def before(*args):return {'controlled_preflight':True}
        async def unknown(*args):
            self.host_calls+=1
            raise self.changes.ChangeOutcomeUncertain('Original host receipt unavailable')
        registry={'restart_service':self.gateway.TrustedAdapter(before,unknown,capability='execute_infrastructure')}
        with patch.object(self,'registry',return_value=registry),self.assertRaises(self.changes.ChangeOutcomeUncertain):await self.resume(row)
        self.assertEqual(self.state(row)['state'],'uncertain');self.assertEqual(self.host_calls,1);self.assertEqual(self.completed,[])
        self.assertIsNone(await self.it.recover_completed(row['invocation_id'],config=self.config,project_gate=self.gate,bridge=self.bridge,db_path=self.path))
        self.assertEqual(self.host_calls,1)

    async def test_registry_cannot_be_supplied_as_json_and_unknown_errors_are_not_blocked_results(self):
        with self.assertRaisesRegex(ValueError,'trusted server objects'):self.it.ITAdapter(None,self.gate,self.config,self.path,{'restart_service':{'execute':'shell'}})
        self.privileged();row=self.running(role='it_manager');packet=self.packet(role='it_manager')
        async def fails(*args):raise RuntimeError('Unknown original host transport outcome')
        adapter=self.it.ITAdapter(None,self.gate,self.config,self.path,{'restart_service':self.gateway.TrustedAdapter(fails,AsyncMock(),capability='execute_infrastructure')})
        with self.assertRaisesRegex(RuntimeError,'Unknown original host'):await adapter.submit(row,packet,row['automation_run_id'],None,self.bridge,before_submit=AsyncMock())
        self.assertEqual(self.completed,[]);self.assertEqual(self.host_calls,0)

    async def test_completed_gateway_receipt_recovery_never_executes_host_again(self):
        row = self.running(role='it_manager')
        record = await self.adapter().submit(
            row, self.packet(role='it_manager'), row['automation_run_id'], None, self.bridge, before_submit=AsyncMock()
        )
        self.g.finish_execution(
            row['invocation_id'], row['automation_run_id'], row['lease_token'], 'uncertain', db_path=self.path
        )
        with patch.object(self.it, 'execute_action', AsyncMock(side_effect=AssertionError('No host replay'))):
            result = await self.it.recover_completed(
                row['invocation_id'], config=self.config, project_gate=self.gate, bridge=self.bridge, db_path=self.path
            )
        self.assertEqual(result, {'completed_original': row['automation_run_id']})
        self.assertEqual(self.completed[0][2], record)

    async def test_unknown_gateway_result_is_not_recovered_or_reexecuted(self):
        row, _ = await self.wait()
        with self.g._transaction(self.path) as db:
            db.execute(
                "UPDATE governance_invocation SET state='uncertain' WHERE invocation_id=?", (row['invocation_id'],)
            )
        with patch.object(self.it, 'execute_action', AsyncMock(side_effect=AssertionError('No host replay'))):
            self.assertIsNone(
                await self.it.recover_completed(
                    row['invocation_id'],
                    config=self.config,
                    project_gate=self.gate,
                    bridge=self.bridge,
                    db_path=self.path,
                )
            )
        self.assertEqual(self.host_calls, 0)
        self.assertEqual(self.completed, [])


class PreparedOutboxIntegration(__import__('unittest').IsolatedAsyncioTestCase):
    async def test_unavailable_it_run_publishes_blocked_and_reconciles_original_ack_with_bounded_deduplicated_repair_children(self):
        import importlib
        import test_agentic_executor_flow as flow_module
        users=patch.dict(flow_module.ROLE_USERS,{'it_manager':'ai-operations@abitcons.com'})
        users.start();self.addCleanup(users.stop)
        flow=flow_module.ExecutorFlow(methodName='runTest');await flow.asyncSetUp();self.addCleanup(flow.doCleanups)
        template={'request_key':'service-fixture','operation':'restart_service','risk':'privileged','parameters':{'resource_id':'fixture-service'}}
        def context(packet):
            if packet['binding']['role']=='it_manager':packet.update(tool_action=deepcopy(template),tool_scopes=['inspect_infrastructure','execute_infrastructure'])
            return packet
        flow.erp.mutate_packet=context
        project=flow_module.PROJECT;task=flow_module.TASK
        flow.config['agentic_tool_gateway']={'projects':{project:{'host_id':socket.gethostname(),'environment_class':'development','operations':['restart_service']}}}
        it=importlib.import_module('open_webui.utils.agentic_it_execution')
        class Adapter(it.ITAdapter):
            async def prepare(self,row,packet,run_id):return await flow.adapter.prepare(row,packet,run_id)
            async def record(self,automation,run_id,record):return await flow.adapter.record(automation,run_id,record)
        adapter=Adapter(None,flow.project_gate,flow.config,flow.db)
        row,packet,_=await flow.enqueue('it-prepared-original-001','it_manager')
        flow.erp.lose_ack_for.add('run.blocked')
        with patch.object(it,'execute_action',AsyncMock(side_effect=AssertionError('Unavailable preparation never enters action gateway'))):
            self.assertIsNone(await flow.executor.execute(row['invocation_id'],project_gate=flow.project_gate,config=flow.config,adapter=adapter,db_path=flow.db,bridge=flow.bridge))
        self.assertEqual(flow.erp.task['status'],'Blocked');self.assertEqual(flow.adapter.calls,[])
        saved=flow.modules['agentic_result_store'].get_result(row['invocation_id'],flow.db)
        self.assertEqual(saved['phase'],'uncertain');self.assertEqual(saved['event']['type'],'run.blocked');self.assertEqual(saved['result']['evidence'][0]['kind'],'checkpoint')
        self.assertGreater(len(flow.erp.uploads[0]['bytes']),0);self.assertEqual(len(flow.erp.uploads),1)
        receipt=await flow.executor.reconcile_publication(row['invocation_id'],flow.bridge,flow.db)
        self.assertEqual(receipt['status'],'Blocked');self.assertEqual(flow.erp.publish_calls,['run.started','run.blocked']);self.assertEqual(len(flow.erp.receipt_reads),1)
        source=flow.ledger.find(row['invocation_id'],flow.db,include_private=True)
        gateway=importlib.import_module('open_webui.utils.agentic_tool_gateway')
        self.assertIsNone(gateway.get_action(source['invocation_id'],it.make_action(source,packet)['action_id'],flow.db))
        orchestrator=importlib.import_module('open_webui.utils.agentic_orchestrator')
        children={};requests=[];get_original=flow.erp.get
        async def create(body):
            ident='TASK-preparation-repair-'+str(len(requests)+1);requests.append(deepcopy(body));children[ident]={'name':ident,'project':project,'parent_task':task,'custom_agov_source_run':source['automation_run_id']};return {'task_id':ident}
        async def get(doctype,ident):return deepcopy(children[ident]) if doctype=='Task' and ident in children else await get_original(doctype,ident)
        flow.bridge.create_task=create;flow.erp.get=get;_,gate=await flow.project_gate(project,flow.config)
        first=await orchestrator._repair_children(source,saved['result'],packet,flow.bridge,flow.db,gate)
        second=await orchestrator._repair_children(source,saved['result'],packet,flow.bridge,flow.db,gate)
        self.assertEqual(first,second);self.assertGreaterEqual(len(requests),1);self.assertLessEqual(len(requests),5)
        self.assertTrue(all(request['target_role']=='developer' and request['repair'] and request['parent_task']==task for request in requests))
        self.assertEqual(flow.erp.task['status'],'Blocked');self.assertEqual(len(flow.ledger.list_invocations(project,flow.db)),1)

"""Signed human Task delivery recovery through the real event/router boundary.

Temporary ledgers/workspaces, actual HMAC/inbox/control application and in-memory
Git protocol. No external network, ERP, model or deployment actions.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import ast
import asyncio
import hashlib
import json
import os
import sqlite3
import time
import unittest
from unittest.mock import AsyncMock, patch

import test_delivery_continuation_guards as guards
from test_agentic_controls_events import EventBridge

ROUTER = Path(os.environ.get('AGOV_CONTROL_ROUTER_FIXTURE',
    Path(__file__).resolve().parents[1] / 'open_webui/routers/agentic_governance.py'))


def seal(bridge, packet):
    packet.pop('packet_id', None)
    packet.pop('content_sha256', None)
    packet['packet_id'] = 'erp-packet-' + bridge.digest(packet)[:48]
    packet['content_sha256'] = bridge.packet_digest(packet)
    return packet


class NativeControlBridge(EventBridge):
    def __init__(self, fixture, owner):
        super().__init__(fixture)
        self.owner = owner
        self.reads = []
        self.after_read = None
    async def get(self, doctype, name):
        assert (doctype, name) == ('Task', self.owner.source['erp_task'])
        self.reads.append((doctype, name))
        if self.after_read:
            self.after_read(len(self.reads))
        return deepcopy(self.owner.native_task)
    async def packet(self, project, task, role, agent, *args):
        assert (project, task, role, agent) == (
            self.owner.source['erp_project'], self.owner.source['erp_task'], 'developer', self.owner.source['agent_id'])
        return deepcopy(self.owner.fresh)


class DeliveryControlRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c = guards.ContinuationGuards('test_existing_commit_readback_missing_one_final_LF_keeps_exact_original_commit')
        self.addCleanup(self.c.doCleanups)
        await self.c.asyncSetUp()
        self.f = self.c.f
        self.g, self.b, self.m = self.c.g, self.c.b, self.c.m
        self.path, self.config = self.c.path, self.f.config
        self.policy = self.f.load('agentic_delivery_control')
        self.events = self.f.load('agentic_events')
        self.orchestrator = self.c.orchestrator
        default_path = patch.object(self.g, 'path', return_value=self.path)
        default_path.start()
        self.addCleanup(default_path.stop)
        packet = deepcopy(self.c.original_packet)
        packet['source_modified'] = '2026-11-15 00:00:00.000000'
        item = {
            'task_id': packet['task_id'], 'task_revision': '2' * 64,
            'source_modified': packet['source_modified'], 'subject': 'Exact original implementation',
            'description': 'Controlled delivery recovery fixture', 'status': 'Open',
            'parent_task': '', 'is_group': False, 'dependencies': [], 'assignees': [],
            'owner_context_sha256': '3' * 64, 'managed': True, 'dispatch_released': True,
            'executor_role': 'developer', 'srs_revision': packet['srs']['revision'],
            'requirement_ids': packet['requirement_ids'], 'acceptance_tests': '[]',
            'source_run_id': '', 'priority': 'Medium', 'due_date': '',
        }
        context = [{key: item[key] for key in ('task_id', 'task_revision', 'owner_context_sha256')}]
        packet['project_backlog'] = {
            'project_id': packet['project_id'], 'complete': True, 'total_count': 1, 'page_size': 50,
            'context_excluded_task_ids': [], 'tasks': [item],
            'snapshot_sha256': self.b.digest({'project_id': packet['project_id'], 'tasks': [item]}),
            'context_sha256': self.b.digest({'project_id': packet['project_id'], 'tasks': context})}
        seal(self.b, packet)
        # This fixture has not created a delivery journal yet.
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                       (self.b.canonical(packet), self.b.digest(packet), self.c.original['invocation_id']))
        self.c.original = self.g.find(self.c.original['invocation_id'], self.path, include_private=True)
        self.c.original_packet = deepcopy(packet)
        self.f.row, self.f.packet_value = self.c.original, deepcopy(packet)
        self.f.remote.normalize_message = True
        await self.c.current_chain()
        child = await self.c.continue_original(self.c.current, packet, outcome='blocked', kind='delivery')
        self.c.current = child
        self.c.current_packet = deepcopy(packet)
        self.f.row, self.f.packet_value = child, deepcopy(packet)
        self.source = child
        self.blocked_time = datetime.now().replace(microsecond=0)
        self.control_time = self.blocked_time + timedelta(seconds=2)
        self.result = {
            'schema_version': 1, 'invocation_id': child['invocation_id'], 'actor': deepcopy(packet['binding']),
            'project_id': child['erp_project'], 'task_id': child['erp_task'], 'task_revision': packet['task_revision'],
            'work_packet_digest': packet['content_sha256'], 'outcome': 'blocked',
            'summary': 'Exact original delivery stopped on the reviewed commit presentation.',
            'test_results': [], 'evidence': [], 'defects': [],
            'implementation_invocation_id': None, 'implementation_agent_id': None}
        event = {
            'schema_version': 1, 'event_id': 'agov-result-' + self.b.digest([child['invocation_id'], self.result])[:48],
            'type': 'run.blocked', 'project_id': child['erp_project'], 'task_id': child['erp_task'],
            'task_revision': packet['task_revision'], 'run_id': child['automation_run_id'],
            'invocation_id': child['invocation_id'], 'role': 'developer', 'agent_id': child['agent_id'],
            'work_packet_digest': packet['content_sha256'], 'result_contract': self.result,
            'artifacts': [], 'acceptance_passed': False}
        self.receipt = {'event_id': event['event_id'], 'task_id': child['erp_task'], 'run_id': child['automation_run_id'],
                        'modified': self.blocked_time.isoformat(' '), 'status': 'Blocked', 'durable': True}
        self.c.store.save_result(child['invocation_id'], child['automation_run_id'], self.result, event, self.path)
        self.g.finish_execution(child['invocation_id'], child['automation_run_id'], child['lease_token'],
                                'execution_finished', db_path=self.path)
        self.c.store.publication_receipt(child['invocation_id'], self.receipt, self.path)
        self.source = self.g.find(child['invocation_id'], self.path, include_private=True)
        with self.g.connect(self.path) as db:
            self.published_at = db.execute('SELECT updated_at FROM governance_result WHERE invocation_id=?',
                                           (child['invocation_id'],)).fetchone()[0]
        self.fresh = deepcopy(packet)
        self.fresh['source_modified'] = self.control_time.isoformat(' ')
        self.fresh['project_backlog']['tasks'][0]['source_modified'] = self.fresh['source_modified']
        self.fresh['project_backlog']['snapshot_sha256'] = self.b.digest({
            'project_id': packet['project_id'], 'tasks': self.fresh['project_backlog']['tasks']})
        seal(self.b, self.fresh)
        self.native_task = {
            'name': child['erp_task'], 'project': child['erp_project'], 'status': 'Open',
            'modified': self.fresh['source_modified'], 'custom_agov_active_run': child['automation_run_id'],
            'custom_agentic_managed': 1, 'custom_agov_dispatch_released': 1}
        self.bridge = NativeControlBridge(self.f, self)
        self.bridge.secret = 'synthetic-signed-control-fixture-secret-0001'
        self.proof = self.operator_proof()
        root_file = patch.object(self.m, '_read_delivery_recovery_authorization',
                                 side_effect=lambda row: (deepcopy(self.proof), self.b.digest(self.proof)) if self.proof else None)
        root_file.start()
        self.addCleanup(root_file.stop)
        bridge_factory = patch.object(self.f.bridge_module, 'ERPBridge', side_effect=lambda config: self.bridge)
        bridge_factory.start()
        self.addCleanup(bridge_factory.stop)
        self.delivered, self.spawned = [], []
        async def deliver(app, ident):
            self.delivered.append(ident)
        def spawn(app, coro):
            job = asyncio.create_task(coro)
            self.spawned.append(job)
            return job
        self.ordinary = AsyncMock(return_value=[])
        self.scan_patch = patch.object(self.orchestrator, 'scan_project', AsyncMock())
        self.scan_patch.start()
        self.addCleanup(self.scan_patch.stop)
        namespace = {'_config': lambda: self.config, '_gate': self.f.gate, '_provision_project': AsyncMock(),
                     '_recover_project': self.ordinary, '_spawn': spawn, '_deliver': deliver}
        node = next(item for item in ast.parse(ROUTER.read_text()).body
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == '_process_event')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROUTER), 'exec'), namespace)
        self.router_event = namespace['_process_event']
        with self.g.connect(self.path) as db:
            self.original_result = tuple(db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (self.source['invocation_id'],)).fetchone())
            self.initial_invocations = db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0]
        self.initial_remote_posts = self.f.remote.posts().copy()

    def operator_proof(self):
        commit = next(item for item in self.c.original_steps if item['step_key'] == 'commit')
        return {'schema_version': 1, 'purpose': self.m.DELIVERY_RECOVERY_POLICY,
                'blocked_invocation_id': self.source['invocation_id'], 'blocked_run_id': self.source['automation_run_id'],
                'blocked_result_sha256': self.b.digest(self.result), 'native_block_receipt_sha256': self.b.digest(self.receipt),
                'original_invocation_id': self.c.original['invocation_id'],
                'original_input_sha256': self.b.digest(self.c.original_input),
                'delivery_source_sha256': hashlib.sha256(Path(self.m.__file__).read_bytes()).hexdigest(),
                'completed_steps': sorted(item['step_key'] for item in self.c.original_steps),
                'commit_request_sha256': self.b.digest(json.loads(commit['request_json'])),
                'commit_sha': json.loads(commit['result_json'])['sha'], 'commit_GET_proof_sha256': '8' * 64,
                'minimum_control_sequence': 0, 'control_task': self.source['erp_task']}

    def make_event(self, *, action='retry', sequence=1, task=True, kind='project.control'):
        scope = self.source['erp_task'] if task else ''
        ident = 'event-delivery-control-' + str(sequence) + '-' + action
        payload = {'project_id': self.source['erp_project'], 'task_id': scope,
                   'command_id': 'command-delivery-control-' + str(sequence), 'control_sequence': sequence,
                   'action': action, 'actor': 'human@example.invalid', 'retained_veto': False}
        self.bridge.native[scope] = deepcopy(payload)
        self.bridge.sequence = sequence
        return {'schema_version': 1, 'event_id': ident, 'project': self.source['erp_project'], 'task': scope,
                'type': kind, 'revision': self.source['task_revision'],
                'source_modified': self.fresh['source_modified'], 'payload': payload}

    async def event(self, event=None, *, receive=True, ingress='signed-erp'):
        value = event or self.make_event()
        with patch.object(self.g.time, 'time', return_value=self.published_at + 2):
            if receive:
                headers = self.b.signing_headers(value, self.bridge.secret)
                self.b.verify_event(value, headers, self.bridge.secret)
                self.g.receive_event(value['event_id'], value['project'], value, actor=ingress, db_path=self.path)
            await self.router_event(object(), value['event_id'], value)
            if self.spawned:
                await asyncio.gather(*self.spawned)
                self.spawned.clear()
        return value

    def audits(self):
        with self.g.connect(self.path) as db:
            return [dict(row) for row in db.execute('SELECT * FROM governance_event WHERE kind=?', (self.policy.KIND,))]

    def assert_inert(self):
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0], self.initial_invocations)
            self.assertEqual(tuple(db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (self.source['invocation_id'],)).fetchone()), self.original_result)
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE kind='delivery_checkpoint_resumed'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT invocation_id FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
                                       (self.source['erp_project'], self.source['erp_task'])).fetchone()[0], self.source['invocation_id'])
        self.assertEqual(self.audits(), [])
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.f.remote.posts(), self.initial_remote_posts)

    async def claim_child(self):
        ident = self.delivered[-1]
        run = self.b.stable_run_id(ident)
        row = self.g.claim_invocation(ident, run, gate=self.f.gate_value, db_path=self.path)
        self.assertIsNotNone(row)
        self.assertTrue(self.g.mark_submitted(ident, run, row['lease_token'], gate=self.f.gate_value, db_path=self.path))
        self.c.current = self.g.find(ident, self.path, include_private=True)
        self.c.current_packet = deepcopy(self.fresh)
        self.f.row, self.f.packet_value = self.c.current, self.c.current_packet
        return self.c.current

    async def test_signed_task_retry_creates_one_audited_unit_then_finishes_original_delivery(self):
        event = await self.event()
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(len(self.audits()), 1)
        self.assertEqual(self.f.remote.posts(), self.initial_remote_posts)
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE kind='delivery_checkpoint_resumed'").fetchone()[0], 1)
            self.assertEqual(tuple(db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (self.source['invocation_id'],)).fetchone()), self.original_result)
        await self.event(event, receive=False)
        self.assertEqual(len(self.delivered), 1)
        row = await self.claim_child()
        gateway = await self.c.open_gateway()
        self.f.remote.drop = '/pulls'
        pending = await self.c.gateway_call(gateway, 'repository.deliver', {'test_call_ids': ['fixture-test-call-001']}, 'controlled-delivery-call')
        self.assertEqual(pending['result']['state'], 'pending_reconciliation')
        posts = self.f.remote.posts().copy()
        status = await self.c.gateway_call(gateway, 'repository.delivery_status', {}, 'controlled-status-call')
        self.assertEqual(self.f.remote.posts(), posts)
        ready = await self.c.gateway_call(gateway, 'repository.deliver', status['result']['arguments'], 'controlled-finish-call')
        self.assertEqual(ready['result']['state'], 'ready_for_review')
        self.assertEqual(self.f.remote.posts(), posts)
        self.assertEqual(posts.count('/git/commits'), 1)
        self.assertEqual(posts.count('/git/refs'), 1)
        self.assertEqual(posts.count('/pulls'), 1)
        await self.m.assert_gateway_result(row, self.c.current_packet, self.c.contract(ready['result']), self.config, self.path)
        from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest
        qa, qa_packet = self.f.qa_context(ready['result']['delivery'])
        facts = await self.m.qa_verify_delivery({}, row=qa, packet=qa_packet, config=self.config, db_path=self.path)
        self.assertFalse(facts['tests_complete'])
        qa_result = {'outcome': 'pass', 'reviewed_delivery_sha256': delivery_digest(ready['result']['delivery']),
                     'test_results': [], 'evidence': []}
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa, qa_packet, qa_result, self.config, self.path)
        await self.f.seed_qa_test(qa, qa_packet)
        facts = await self.m.qa_verify_delivery({}, row=qa, packet=qa_packet, config=self.config, db_path=self.path)
        qa_result.update(test_results=facts['test_results'], evidence=facts['evidence'])
        await self.m.assert_gateway_result(qa, qa_packet, qa_result, self.config, self.path)
        with self.m._db(self.path) as db:
            for old in self.c.original_steps:
                self.assertEqual(dict(db.execute('SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?',
                                               (old['invocation_id'], old['step_key'])).fetchone()), old)

    async def test_root_resolution_drift_cannot_replace_the_exact_signed_task_authority(self):
        original = deepcopy(self.proof)
        cases = [('delivery_source_sha256', '0' * 64), ('native_block_receipt_sha256', '0' * 64),
                 ('control_task', ''), ('blocked_result_sha256', '0' * 64),
                 ('blocked_run_id', 'foreign-original'), ('original_input_sha256', '0' * 64),
                 ('commit_request_sha256', '0' * 64), ('commit_sha', 'f' * 40),
                 ('commit_GET_proof_sha256', 'unknown'), ('minimum_control_sequence', 999)]
        for sequence, (key, value) in enumerate(cases, 1):
            with self.subTest(field=key):
                self.proof = {**deepcopy(original), key: value}
                with self.assertRaises(ValueError):
                    await self.event(self.make_event(sequence=sequence))
                self.assert_inert()
        self.proof = original

    async def test_signed_resume_uses_the_same_narrow_task_policy(self):
        await self.event(self.make_event(action='resume'))
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(json.loads(self.audits()[0]['payload'])['control']['action'], 'resume')

    async def test_project_retry_result_event_and_generic_resume_cannot_consume_authority(self):
        await self.event(self.make_event(task=False))
        await self.event(self.make_event(sequence=2, kind='result.recorded'))
        with self.assertRaises(ValueError):
            self.orchestrator._resume(self.source, self.fresh, self.f.gate_value, self.path, [], 'delivery', delivery_config=self.config)
        self.assert_inert()

    async def test_missing_operator_attestation_and_non_open_task_are_inert(self):
        self.proof = None
        await self.event()
        self.assert_inert()
        self.proof = self.operator_proof()
        self.native_task['status'] = 'Blocked'
        await self.event(self.make_event(sequence=2))
        self.assert_inert()

    async def test_unsigned_ingress_and_prepublication_task_control_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.event(ingress='erp')
        self.assert_inert()
        old = self.make_event(sequence=2)
        old['source_modified'] = self.receipt['modified']
        with self.assertRaises(ValueError):
            await self.event(old)
        self.assert_inert()

    async def test_lost_native_ack_does_not_consume_until_exact_reconciliation(self):
        self.bridge.lose_ack = True
        event = self.make_event()
        with self.assertRaises(TimeoutError):
            await self.event(event)
        self.assert_inert()
        await self.event(event, receive=False)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.bridge.ack_calls, 1)

    async def test_maintenance_closed_or_changed_scope_cannot_consume_fresh_control(self):
        from open_webui.utils import agentic_maintenance as maintenance
        database = maintenance.path(self.path.parent)
        snapshot = maintenance.status(database)
        maintenance.control('close', expected_epoch=snapshot['epoch'], command_id='fixture-control-close',
                            actor='fixture', db_path=database)
        event = await self.event()
        self.assert_inert()
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT state FROM governance_inbox WHERE event_id=?',
                                        (event['event_id'],)).fetchone()[0], 'processed')
        opened = maintenance.status(database)
        maintenance.control('open', expected_epoch=opened['epoch'], command_id='fixture-control-reopen',
                            actor='fixture', db_path=database)
        with self.assertRaises(ValueError):
            await self.event(event, receive=False)
        self.assert_inert()

    async def test_native_owner_packet_and_completed_step_drift_leave_original_fence(self):
        self.native_task['custom_agov_active_run'] = 'foreign-run'
        with self.assertRaises(ValueError):
            await self.event()
        self.assert_inert()
        self.native_task['custom_agov_active_run'] = self.source['automation_run_id']
        with self.m._db(self.path) as db:
            db.execute("UPDATE governance_delivery_step SET state='uncertain' WHERE invocation_id=? AND step_key='commit'",
                       (self.c.original['invocation_id'],))
            db.commit()
        with self.assertRaises(ValueError):
            await self.event(self.make_event(sequence=2))
        self.assert_inert()

    async def test_atomic_audit_failure_rolls_back_successor_and_fence_transfer(self):
        with self.g.connect(self.path) as db:
            db.execute("CREATE TRIGGER deny_control_recovery BEFORE INSERT ON governance_event WHEN NEW.kind='delivery_control_recovery' BEGIN SELECT RAISE(ABORT,'controlled audit failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'controlled audit failure'):
            await self.event()
        self.assert_inert()

    async def test_newer_stop_between_native_reads_prevents_consumption(self):
        def stop(count):
            if count == 2:
                self.f.controls.task_control(self.source['erp_project'], self.source['erp_task'],
                                             'stop', actor='new-human-stop', db_path=self.path)
        self.bridge.after_read = stop
        with self.assertRaises(ValueError):
            await self.event()
        self.assertEqual(self.audits(), [])
        self.assertEqual(self.delivered, [])
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0], self.initial_invocations)
        self.assertEqual(self.f.remote.posts(), self.initial_remote_posts)

    async def test_historical_edge_survives_newer_cursor_but_rejects_changed_authorization(self):
        await self.event()
        row = await self.claim_child()
        with self.g.connect(self.path) as db:
            parent = dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (self.source['invocation_id'],)).fetchone())
            before = self.policy.verify_edge(db, row, parent)
            db.execute("UPDATE governance_native_control SET sequence=999,command_id='later-human-control' WHERE project=? AND task=?",
                       (self.source['erp_project'], self.source['erp_task']))
        with self.g.connect(self.path) as db:
            self.assertEqual(self.policy.verify_edge(db, row, parent), before)
            self.g._event(db, self.source['erp_project'], row['invocation_id'], self.policy.KIND,
                          self.policy.ACTOR, json.loads(self.audits()[0]['payload']))
        with self.g.connect(self.path) as db:
            with self.assertRaises(ValueError):
                self.policy.verify_edge(db, row, parent)


if __name__ == '__main__':
    unittest.main()

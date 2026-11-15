"""Actual control model/route and real temporary ledger; no app/network/model actions."""
from __future__ import annotations
import ast
import json
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch
from typing import Literal
import httpx
from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent if HERE.name == 'tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
sys.path.insert(0, str(BACKEND / 'tests'))
from test_agentic_controls_events import Fixture, P, T
ROUTER = HERE / 'agentic_governance.selected.py'
if not ROUTER.exists():
    ROUTER = BACKEND.parent / 'docs/evidence/2026-11-15-native-control-route-contract-v1/agentic_governance.selected.py'


class ControlRouteContract(Fixture):
    def setUp(self):
        super().setUp()
        tree = ast.parse(ROUTER.read_text())
        chosen = [x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'ProjectControlInput'
                  or isinstance(x, ast.AsyncFunctionDef) and x.name == 'control_project'
                  or isinstance(x, ast.FunctionDef) and x.name == '_erp_failure']
        self.assertEqual(len(chosen), 3)
        for node in chosen:
            if isinstance(node, ast.AsyncFunctionDef):
                node.decorator_list = []
        mod = types.ModuleType('actual_control_route_contract')
        mod.__dict__.update({'BaseModel': BaseModel, 'ConfigDict': ConfigDict, 'Field': Field,
                             'Literal': Literal, 'Header': Header, 'HTTPException': HTTPException,
                             'Request': types.SimpleNamespace, 'PROJECT_ID': self.g.PROJECT_ID})
        self.ns = mod.__dict__
        self.route_modules = patch.dict(sys.modules, {mod.__name__: mod})
        self.route_modules.start()
        self.addCleanup(self.route_modules.stop)
        prefix = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[prefix, *chosen], type_ignores=[])), str(ROUTER), 'exec'), self.ns)
        self.model = self.ns['ProjectControlInput']
        self.scheduled = []
        self.addCleanup(lambda: [c.close() for c in self.scheduled if c.cr_frame is not None])
        self.request = types.SimpleNamespace(app=types.SimpleNamespace())
        self.auth = []
        def authenticate(value):
            self.auth.append(value)
            if value != 'fixture-control':
                raise HTTPException(401, 'Unauthorized')
        self.ns.update({'_auth': authenticate, '_config': lambda: {'erp_token_file': 'unused-fixture'},
                        '_erp_project': AsyncMock(return_value={'modified': 'project-revision-1'}),
                        '_spawn': lambda app, coro: self.scheduled.append(coro), '_process_event': self.process})
        original_receive = self.g.receive_event
        self.inbox = patch.object(self.g, 'receive_event', side_effect=lambda *a, **kw: original_receive(*a, **kw, db_path=self.path))
        self.inbox.start()
        self.addCleanup(self.inbox.stop)
        self.factory = patch.object(self.bridge_module, 'ERPBridge', return_value=self.bridge)
        self.factory_mock = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.erp = types.SimpleNamespace(get=AsyncMock(side_effect=lambda doctype, ident:
            {'modified': 'project-revision-1'} if doctype == 'Project' else {'project': P}),
            client=types.SimpleNamespace(aclose=AsyncMock()))
        runtime = types.ModuleType('open_webui.utils.agentic_erp_runtime')
        runtime.governed_client = lambda token: self.erp
        self.runtime_patch = patch.dict(sys.modules, {'open_webui.utils.agentic_erp_runtime': runtime})
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def body(self, **changes):
        value = {'action': 'retry', 'command_id': 'fixture-human-command-0001',
                 'control_sequence': 1, 'expected_revision': 'project-revision-1',
                 'actor': 'manager@example.invalid'}
        value.update(changes)
        return value

    def intent(self, data):
        value = {k: data[k] for k in ('action', 'command_id', 'control_sequence', 'actor')}
        value.update(project_id=P, task_id=data.get('task_id') or '', retained_veto=False)
        self.bridge.native[value['task_id']] = value
        return value

    def ledger_counts(self):
        with self.g.connect(self.path) as db:
            return tuple(db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                         for name in ('governance_inbox', 'governance_event', 'governance_invocation'))

    async def call(self, value, secret='fixture-control'):
        return await self.ns['control_project'](P, self.model(**value), self.request, secret)

    async def process(self, app, event_id, event):
        return await self.events.process_event(event_id, event, config={}, project_gate=self.gate,
            provision=self.provision, bridge_factory=lambda cfg: self.bridge, dispatch=self.dispatch,
            scan=self.scan, recover=self.recover, db_path=self.path)

    async def test_missing_noninteger_and_boolean_sequence_rejected_before_route(self):
        before = self.ledger_counts()
        for value in (None, True, False, '1', 0, -1, 1.0):
            data = self.body()
            if value is None:
                del data['control_sequence']
            else:
                data['control_sequence'] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.model(**data)
        self.assertEqual(self.ledger_counts(), before)
        self.assertEqual(self.auth, [])
        self.factory_mock.assert_not_called()

    async def test_mismatch_malformed_or_unavailable_intent_never_mutates_inbox(self):
        value = self.body()
        expected = self.intent(value)
        before = self.ledger_counts()
        mutations = {'actor': 'forged@example.invalid', 'action': 'stop', 'control_sequence': 2,
                     'command_id': 'different-human-command', 'project_id': 'PROJ-0091', 'task_id': T}
        for key, changed in mutations.items():
            self.bridge.native[''] = {**expected, key: changed}
            with self.subTest(key=key), self.assertRaises(HTTPException) as caught:
                await self.call(value)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(self.ledger_counts(), before)
        for response in (None, {}, {'message': None}, {'message': {'project_id': P, 'task_id': '', 'intent': None}}):
            with patch.object(self.bridge, 'request', AsyncMock(return_value=response)):
                with self.assertRaises(HTTPException) as caught:
                    await self.call(value)
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(self.ledger_counts(), before)
        self.assertEqual(self.bridge.ack_calls, 0)
        self.assertEqual(self.scheduled, [])

    async def test_actual_erp_rejection_is_safe_503_and_does_not_record_command(self):
        status = self.load('erp_status_write')
        request = httpx.Request('GET', 'https://erp.invalid/api/method/fixed',
                                headers={'Authorization': 'token fixture-private-value'})
        response = httpx.Response(417, request=request, json={
            'exc_type': 'ValidationError',
            '_server_messages': json.dumps([json.dumps({'message': 'Actual validation failed; token=fixture-private-value'})])})
        before = self.ledger_counts()
        with patch.object(self.bridge, 'request', AsyncMock(side_effect=status.ERPResponseError(response))):
            with self.assertRaises(HTTPException) as caught:
                await self.call(self.body())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('ERP HTTP 417 ValidationError', caught.exception.detail)
        self.assertIn('Actual validation failed', caught.exception.detail)
        self.assertNotIn('fixture-private-value', caught.exception.detail)
        with patch.object(self.bridge, 'request', AsyncMock(side_effect=HTTPException(429, 'Existing HTTP error'))):
            with self.assertRaises(HTTPException) as caught:
                await self.call(self.body())
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (429, 'Existing HTTP error'))
        with patch.object(self.bridge, 'request', AsyncMock(side_effect=TimeoutError('private transport detail'))):
            with self.assertRaises(HTTPException) as caught:
                await self.call(self.body())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('TimeoutError', caught.exception.detail)
        self.assertNotIn('private transport detail', caught.exception.detail)
        self.assertEqual(self.ledger_counts(), before)
        self.assertEqual(self.scheduled, [])
        self.assertEqual(self.bridge.ack_calls, 0)
        self.dispatch.assert_not_awaited()

    async def test_exact_project_intent_normalized_and_applied_once_by_real_processor(self):
        value = self.body()
        self.intent(value)
        first = await self.call(value)
        repeat = await self.call(value)
        self.assertTrue(first['durable'])
        self.assertEqual(repeat, first)
        self.assertEqual(len(self.scheduled), 1)
        event = json.loads(self.g.pending_events(db_path=self.path)[0]['payload'])
        self.assertEqual(event['task'], '')
        self.assertEqual(event['payload']['task_id'], '')
        self.assertEqual(event['payload']['actor'], value['actor'])
        self.assertEqual(event['payload']['control_sequence'], 1)
        await self.scheduled.pop()
        self.assertEqual(self.bridge.ack_calls, 1)
        self.recover.assert_awaited_once()
        self.assertEqual(self.g.pending_events(db_path=self.path), [])
        await self.call(value)
        self.assertEqual(self.scheduled, [])
        self.assertEqual(self.bridge.ack_calls, 1)
        self.dispatch.assert_not_awaited()

    async def test_already_acknowledged_native14_mirror_reconciles_by_get_only(self):
        value = self.body(control_sequence=14)
        self.intent(value)
        original = {'schema_version': 1, 'event_id': 'signed-erp-event-original-0014',
                    'type': 'project.control', 'project': P, 'task': '',
                    'revision': 'project-revision-1',
                    'payload': {**value, 'task_id': ''}}
        self.g.receive_event(original['event_id'], P, original)
        await self.process(self.request.app, original['event_id'], original)
        self.assertEqual(self.bridge.ack_calls, 1)
        self.recover.reset_mock()
        await self.call(value)
        await self.call(value)
        self.assertEqual(len(self.scheduled), 1)
        await self.scheduled.pop()
        self.assertEqual(self.bridge.ack_calls, 1)  # Existing Native ACK read; no second POST.
        self.recover.assert_awaited_once()
        with self.g.connect(self.path) as db:
            row = db.execute('SELECT sequence,command_id,action,phase FROM governance_native_control WHERE project=? AND task=?', (P, '')).fetchone()
            self.assertEqual(tuple(row), (14, value['command_id'], 'retry', 'acknowledged'))
        self.assertEqual(self.g.pending_events(db_path=self.path), [])
        self.dispatch.assert_not_awaited()

    async def test_native_supersession_after_route_is_rechecked_without_action(self):
        value = self.body()
        intent = self.intent(value)
        await self.call(value)
        self.bridge.native[''] = {**intent, 'control_sequence': 2, 'command_id': 'newer-human-command'}
        await self.scheduled.pop()
        self.assertEqual(self.bridge.ack_calls, 0)
        self.recover.assert_not_awaited()
        self.dispatch.assert_not_awaited()
        with self.g.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_native_control'").fetchone())

    async def test_task_scope_and_authentication_precede_inbox(self):
        value = self.body(task_id=T)
        self.intent(value)
        before = self.ledger_counts()
        self.erp.get.side_effect = lambda doctype, ident: {'project': 'PROJ-0091'}
        with self.assertRaises(HTTPException) as caught:
            await self.call(value)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.ledger_counts(), before)
        with self.assertRaises(HTTPException) as caught:
            await self.call(value, secret='wrong')
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(self.ledger_counts(), before)
        self.erp.get.side_effect = lambda doctype, ident: {'project': P}
        await self.call(value)
        event = json.loads(self.g.pending_events(db_path=self.path)[0]['payload'])
        self.assertEqual(event['task'], T)
        self.assertEqual(event['payload']['task_id'], T)

    async def test_signed_pause_remains_available_after_eligibility_revocation(self):
        value = self.body(action='pause')
        self.intent(value)
        self.ns['_erp_project'].side_effect = HTTPException(403, 'Revoked')
        await self.call(value)
        await self.scheduled.pop()
        self.assertEqual(self.g.project_control(P, db_path=self.path)['state'], 'paused')
        self.assertEqual(self.bridge.ack_calls, 1)
        self.ns['_erp_project'].assert_not_awaited()
        self.recover.assert_not_awaited()
        self.dispatch.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

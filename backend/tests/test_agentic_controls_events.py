"""Actual SQLite controls/event coordination; no app, network or model imports."""

from __future__ import annotations
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'
P = 'PROJ-0197'
OTHER = 'PROJ-0091'
T = 'TASK-fixture-0001'
SECOND = 'TASK-fixture-0002'


class Fixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite'
        self.top = types.ModuleType('open_webui')
        self.top.__path__ = [str(ROOT.parent)]
        self.utils = types.ModuleType('open_webui.utils')
        self.utils.__path__ = [str(ROOT)]
        self.top.utils = self.utils
        env = types.ModuleType('open_webui.env')
        env.DATA_DIR = Path(self.temp.name)
        self.modules = patch.dict(
            sys.modules, {'open_webui': self.top, 'open_webui.utils': self.utils, 'open_webui.env': env}
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.g = self.load('agentic_governance')
        self.bridge_module = self.load('agentic_bridge')
        self.controls = self.load('agentic_controls')
        self.events = self.load('agentic_events')
        self.allowed = True
        self.gate_value = {
            'enabled': True,
            'pm_task_automation_enabled': True,
            'project_status': 'Open',
            'assigned_user': 'ai-projects@abitcons.com',
            'revision': 'project-revision-1',
        }
        self.users = {
            'solution_architect': 'ai-projects@abitcons.com',
            'project_manager': 'ai-projects@abitcons.com',
            'developer': 'ai-developer@abitcons.com',
            'qa': 'ai-qa@abitcons.com',
            'operations': 'ai-operations@abitcons.com',
            'it_manager': 'ai-operations@abitcons.com',
        }
        for project in (P, OTHER):
            self.g.enroll(project, 'owner-fixture', self.users, self.path)
            self.g.project_control(
                project,
                'enable',
                actor='owner@example.invalid',
                policy_revision='project-revision-1',
                db_path=self.path,
            )
        self.dispatch = AsyncMock()
        self.scan = AsyncMock()
        self.recover = AsyncMock()
        self.provision = AsyncMock()
        self.bridge = EventBridge(self)

    def load(self, name):
        full = 'open_webui.utils.' + name
        spec = importlib.util.spec_from_file_location(full, ROOT / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        setattr(self.utils, name, module)
        spec.loader.exec_module(module)
        return module

    async def gate(self, project, config):
        if not self.allowed:
            error = ValueError('Project revoked')
            error.status_code = 403
            raise error
        return {}, deepcopy(self.gate_value)

    def packet(self, task=T, role='developer', project=P):
        value = {
            'schema_version': 1,
            'project_id': project,
            'task_id': task,
            'task_revision': 'task-revision-1',
            'binding': {
                'project_id': project,
                'role': role,
                'agent_id': self.g.agent_id(project, role),
                'erp_user': self.users[role],
            },
            'srs': {'revision': 'srs-fixture-1', 'sha256': '1' * 64},
            'source_modified': 'modified-open',
            'project_revision': 'project-revision-1',
            'objective': 'Verify the fixture task',
            'requirement_ids': ['REQ-ONE'],
            'acceptance_tests': [
                {
                    'id': 'REQ-ONE.acceptance',
                    'requirement_ids': ['REQ-ONE'],
                    'method': 'readback',
                    'expected': 'Observed fixture acceptance.',
                }
            ],
            'repository': 'https://github.com/ABiT-Consulting/abit-ai-platform',
            'environment': 'development',
            'tool_scopes': ['inspect_infrastructure'],
            'instructions': 'Fixture only',
            'activity': [],
            'artifacts': [],
            'attachments': [],
            'dependencies': [],
        }
        if role == 'it_manager':
            value['tool_action'] = {
                'request_key': 'fixture-request',
                'operation': 'inspect',
                'risk': 'routine',
                'parameters': {},
            }
        value['content_sha256'] = self.bridge_module.packet_digest(value)
        return value

    def enqueue(self, ident='invocation-fixture-0001', task=T, role='developer', project=P):
        packet = self.packet(task, role, project)
        self.g.enqueue(
            invocation_id=ident,
            project=project,
            task=task,
            from_role='project_manager',
            target_role=role,
            task_revision=packet['task_revision'],
            context='Fixture scope',
            work_packet=packet,
            gate=self.gate_value,
            actor='server-fixture',
            db_path=self.path,
        )
        return self.g.find(ident, self.path, include_private=True)

    def running(self, ident='invocation-fixture-0001', task=T, role='developer', project=P):
        self.enqueue(ident, task, role, project)
        row = self.g.claim_invocation(ident, 'run-' + ident, gate=self.gate_value, db_path=self.path)
        self.g.mark_submitted(
            ident, row['automation_run_id'], row['lease_token'], gate=self.gate_value, db_path=self.path
        )
        return self.g.find(ident, self.path, include_private=True)

    async def event(self, kind='project.changed', action=None, task='', identity='event-fixture-0001'):
        data = {
            'schema_version': 1,
            'event_id': identity,
            'project': P,
            'task': task,
            'type': kind,
            'revision': 'project-revision-1',
            'payload': {'actor': 'manager@example.invalid'},
        }
        if action:
            data['payload']['action'] = action
        self.g.receive_event(identity, P, data, db_path=self.path)
        await self.events.process_event(
            identity,
            data,
            config={},
            project_gate=self.gate,
            provision=self.provision,
            bridge_factory=lambda config: self.bridge,
            dispatch=self.dispatch,
            scan=self.scan,
            recover=self.recover,
            db_path=self.path,
        )
        return data


class EventBridge:
    def __init__(self, fixture):
        self.f = fixture
        self.erp = self
        self.secret = 'fixture-not-a-secret'
        self.prepares = 0

    async def request(self, method, path, **kwargs):
        if path.endswith('prepare_project_start'):
            self.prepares += 1
            return {'message': {'task_id': 'TASK-architecture-anchor'}}
        if path.endswith('prepare_task_start'):
            return {'message': {'target_role': getattr(self, 'task_role', 'project_manager')}}
        raise AssertionError(path)

    async def packet(self, project, task, role, agent, *args):
        value = self.f.packet(task, role, project)
        if role == 'project_manager' and getattr(self, 'adoption', None):
            value['atomic_adoption'] = deepcopy(self.adoption)
            value['content_sha256'] = self.f.bridge_module.packet_digest(value)
        return value

    async def close(self):
        pass


class ControlsAndEvents(Fixture):
    async def test_task_pause_freezes_only_its_unsent_original_id(self):
        first = self.enqueue()
        self.enqueue('invocation-fixture-0002', SECOND)
        self.enqueue('invocation-other-0001', T, project=OTHER)
        self.controls.task_control(P, T, 'pause', actor='manager@example.invalid', db_path=self.path)
        self.assertEqual(self.g.find(first['invocation_id'], self.path)['state'], 'blocked')
        self.assertEqual(self.g.find('invocation-fixture-0002', self.path)['state'], 'queued')
        self.assertEqual(self.g.find('invocation-other-0001', self.path)['state'], 'queued')
        self.controls.task_control(P, T, 'resume', actor='manager@example.invalid', db_path=self.path)
        self.assertTrue(
            self.controls.resume_controlled(
                first['invocation_id'], self.packet(), self.gate_value, 'manager@example.invalid', self.path
            )
        )
        resumed = self.g.find(first['invocation_id'], self.path)
        self.assertEqual(resumed['state'], 'queued')
        self.assertIsNone(resumed['automation_run_id'])
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 2)

    async def test_pause_resume_refuses_changed_snapshot_and_checkbox_off(self):
        row = self.enqueue()
        self.controls.task_control(P, T, 'pause', actor='manager@example.invalid', db_path=self.path)
        self.controls.task_control(P, T, 'resume', actor='manager@example.invalid', db_path=self.path)
        changed = self.packet()
        changed['objective'] = 'Changed while paused'
        with self.assertRaisesRegex(ValueError, 'changed during pause'):
            self.controls.resume_controlled(
                row['invocation_id'], changed, self.gate_value, 'manager@example.invalid', self.path
            )
        with self.assertRaises(ValueError):
            self.controls.resume_controlled(
                row['invocation_id'],
                self.packet(),
                {**self.gate_value, 'pm_task_automation_enabled': False},
                'manager@example.invalid',
                self.path,
            )
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'blocked')

    async def test_task_stop_retains_active_fence_without_canceling_other_task(self):
        row = self.running()
        self.enqueue('invocation-fixture-0002', SECOND)
        await self.event('project.control', 'stop', T)
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'cancel_requested')
        self.assertEqual(self.g.find('invocation-fixture-0002', self.path)['state'], 'queued')
        self.assertEqual(self.g.project_control(P, db_path=self.path)['state'], 'enabled')
        self.dispatch.assert_not_awaited()

    async def test_repeated_startup_events_create_one_architect_invocation(self):
        await self.event()
        await self.event(identity='event-fixture-0002')
        rows = self.g.list_invocations(P, self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['target_role'], 'solution_architect')
        self.assertEqual(self.dispatch.await_count, 1)
        self.assertEqual(self.g.pending_events(db_path=self.path), [])

    async def test_explicit_architecture_task_start_reuses_project_start_identity(self):
        self.bridge.task_role = 'solution_architect'
        await self.event('project.control', 'start', 'TASK-architecture-anchor')
        await self.event(identity='event-fixture-0002')
        rows = self.g.list_invocations(P, self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['target_role'], 'solution_architect')
        self.assertEqual(self.dispatch.await_count, 1)

    async def test_explicit_adopted_atomic_task_starts_one_pm_review(self):
        self.bridge.adoption = {
            'adoption_id': 'human-adoption-fixture',
            'executor_role': 'developer',
            'source_modified': 'modified-open',
            'task_revision': 'task-revision-1',
            'requirement_ids': ['REQ-ONE'],
        }
        await self.event('project.control', 'start', T)
        await self.event('project.control', 'start', T, identity='event-fixture-0002')
        rows = self.g.list_invocations(P, self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['target_role'], 'project_manager')
        self.assertEqual(self.dispatch.await_count, 1)

    async def test_human_pause_survives_project_reconciliation(self):
        self.g.project_control(
            P, 'pause', actor='manager@example.invalid', policy_revision='project-revision-1', db_path=self.path
        )
        await self.event()
        self.assertEqual(self.g.project_control(P, db_path=self.path)['state'], 'paused')
        self.dispatch.assert_not_awaited()
        self.assertEqual(self.bridge.prepares, 0)

    async def test_revoked_project_freezes_queued_work_without_dispatch(self):
        row = self.enqueue()
        self.allowed = False
        await self.event()
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'blocked')
        self.assertEqual(self.g.project_control(P, db_path=self.path)['actor'], 'erp-revocation')
        self.dispatch.assert_not_awaited()

    async def test_task_retry_recovers_original_scope_without_new_invocation(self):
        self.running()
        await self.event('project.control', 'retry', T)
        self.recover.assert_awaited_once_with(P, T, True)
        self.assertEqual(len(self.g.list_invocations(P, self.path)), 1)
        self.dispatch.assert_not_awaited()

    async def test_resume_event_releases_same_queued_task_only(self):
        row = self.enqueue()
        self.enqueue('invocation-fixture-0002', SECOND)
        self.controls.task_control(P, T, 'pause', actor='manager@example.invalid', db_path=self.path)
        await self.event('project.control', 'resume', T)
        self.dispatch.assert_awaited_once_with(row['invocation_id'])
        self.assertEqual(self.g.find('invocation-fixture-0002', self.path)['state'], 'queued')

    async def test_status_reports_pause_requested_for_existing_active_work(self):
        self.running()
        self.g.project_control(
            P, 'pause', actor='manager@example.invalid', policy_revision='project-revision-1', db_path=self.path
        )
        self.assertEqual(self.events.effective_control(P, self.path)['effective_state'], 'paused_requested')

    async def test_stop_waiting_approval_needs_no_upstream_cancellation(self):
        row = self.running(role='it_manager')
        with self.g._transaction(self.path) as db:
            db.execute(
                "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL WHERE invocation_id=?",
                (row['invocation_id'],),
            )
        self.controls.task_control(P, T, 'stop', actor='manager@example.invalid', db_path=self.path)
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'cancelled')
        with self.g.connect(self.path) as db:
            lease = db.execute(
                'SELECT invocation_id FROM governance_task_lease WHERE erp_project=? AND erp_task=?', (P, T)
            ).fetchone()
        self.assertIsNone(lease[0])


if __name__ == '__main__':
    unittest.main()

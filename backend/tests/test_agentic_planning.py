"""Offline real-SQLite planning publication; fake ERP, no model/server imports."""

from __future__ import annotations
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


planning = load('isolated_planning', ROOT / 'agentic_planning.py')
architect_contract = load('isolated_architect_contract', ROOT / 'agentic_contracts/validate_architect_plan.py')
PLAN = json.loads(r''' {
  "schema_version": "1.0",
  "metadata": {
    "project_id": "PROJ-0197",
    "srs_version": "fixture-srs-v1",
    "srs_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
    "source_url": "https://erp.abit.ai/desk/project/PROJ-0197",
    "source_activity_revision": "fixture-activity-1",
    "agent_binding": {
      "project_id": "PROJ-0197",
      "erp_ai_user": "ai-architect@example.invalid",
      "abit_agent_id": "fixture-project-architect-001",
      "role": "solution_architect"
    },
    "run_id": "fixture-run-001",
    "generated_at": "2026-11-13T02:00:00Z",
    "state": "proposed"
  },
  "requirements": [
    {
      "id": "REQ-UI-01",
      "title": "Project governance dashboard",
      "description": "Display execution status, agent roster, QA and task controls.",
      "kind": "ui",
      "status": "ready",
      "acceptance_criteria": [
        "Project user sees a correctly scoped agent roster",
        "Task controls show confirmed execution status"
      ],
      "assumptions": [
        "Requires authenticated ERP Project access"
      ]
    },
    {
      "id": "REQ-API-02",
      "title": "Server-authorized dispatch",
      "description": "Invoke project-scoped agents via authenticated ABIT API.",
      "kind": "api",
      "status": "ready",
      "acceptance_criteria": [
        "A valid task invocation returns one stable execution ID",
        "Unrelated project invocation is denied"
      ],
      "assumptions": []
    }
  ],
  "designs": [
    {
      "id": "DES-ARCH-01",
      "kind": "component",
      "requirement_ids": [
        "REQ-UI-01",
        "REQ-API-02"
      ],
      "summary": "ERP SRS\u2192Architect\u2192governed task creation sequence",
      "artifact": {
        "kind": "content_store",
        "uri": "artifact://architect-component.svg",
        "sha256": "2fc11b603b32ee72ef0600f7d803202dc339b4165dc819163c1ec700f3ba1eff"
      }
    }
  ],
  "mockups": [
    {
      "id": "MOCK-UI-01",
      "requirement_ids": [
        "REQ-UI-01"
      ],
      "screen_name": "ERP Project Agent Governance",
      "form_factors": [
        "desktop"
      ],
      "states": [
        "default"
      ],
      "artifact": {
        "kind": "content_store",
        "uri": "artifact://architect-dashboard.svg",
        "sha256": "3023f7aa1b7d95e5f3ef4f331450b7cbef96693f6d255c92597c2ef043a2c755"
      }
    }
  ],
  "tasks": [
    {
      "task_key": "TASKPLAN-API-01",
      "subject": "Create project-agent dispatch API",
      "description": "Implement authenticated scoped invocation with idempotent run lease.",
      "role": "developer",
      "requirement_ids": [
        "REQ-API-02"
      ],
      "parent_task_key": null,
      "depends_on": [],
      "steps": [
        {
          "input": "Authorized ERP task ID and project role binding",
          "action": "Verify agent mapping, revision and idempotency; claim run lease",
          "output": "Unique accepted agent run or existing run reference",
          "verification": "Attempt same request twice and cross-project negative request"
        }
      ],
      "acceptance_criteria": [
        "Duplicate POST returns same run",
        "Cross-project agent request rejected"
      ],
      "qa_test_ids": [
        "TEST-API-01"
      ],
      "artifact_ids": [
        "DES-ARCH-01"
      ],
      "estimate_hours": 6,
      "priority": "P0",
      "idempotency_key": "PROJ-0197-fixture-srs-v1-API-01"
    },
    {
      "task_key": "TASKPLAN-UI-02",
      "subject": "Build governance task controls",
      "description": "Implement Project/Task UI using scoped agent API and verified run status.",
      "role": "developer",
      "requirement_ids": [
        "REQ-UI-01"
      ],
      "parent_task_key": null,
      "depends_on": [
        "TASKPLAN-API-01"
      ],
      "steps": [
        {
          "input": "Verified API contract and screen mockup",
          "action": "Implement project roster and backend-driven Start/Stop controls",
          "output": "ERP-only task governance interface",
          "verification": "Browser test for role permission and actual status readback"
        }
      ],
      "acceptance_criteria": [
        "Controls match backend state",
        "Unauthorized user cannot invoke actions"
      ],
      "qa_test_ids": [
        "TEST-UI-02"
      ],
      "artifact_ids": [
        "DES-ARCH-01",
        "MOCK-UI-01"
      ],
      "estimate_hours": 8,
      "priority": "P1",
      "idempotency_key": "PROJ-0197-fixture-srs-v1-UI-02"
    }
  ],
  "tests": [
    {
      "id": "TEST-API-01",
      "requirement_ids": [
        "REQ-API-02"
      ],
      "task_keys": [
        "TASKPLAN-API-01"
      ],
      "method": "integration",
      "expected_result": "Exactly one run and no cross-project execution",
      "evidence_required": "Run log and HTTP 202/403 results"
    },
    {
      "id": "TEST-UI-02",
      "requirement_ids": [
        "REQ-UI-01"
      ],
      "task_keys": [
        "TASKPLAN-UI-02"
      ],
      "method": "e2e",
      "expected_result": "Authorized task controls work and reflect stored run status",
      "evidence_required": "Browser screenshots and API status readback"
    }
  ],
  "decisions": []
} ''')


@contextmanager
def dbconnect(path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        yield db
    finally:
        db.close()


class ERP:
    def __init__(self):
        self.erp = self
        self.secret = 'test-fixture-only'
        self.calls = []
        self.tasks = {}
        self.receipts = {}
        self.lose_ack = False
        self.no_write_error = False
        self.tamper = False
        self.entered = None
        self.resume = None
        self.native_parent_dependency = False

    async def create_task(self, packet):
        self.calls.append(deepcopy(packet))
        if self.entered is not None:
            self.entered.set()
            await self.resume.wait()
        if self.no_write_error:
            raise TimeoutError('Unknown original request')
        ident = 'TASK-fixture-' + str(len(self.tasks) + 1)
        self.tasks[ident] = {
            'name': ident,
            'project': packet['project_id'],
            'parent_task': packet['parent_task'],
            'custom_agov_source_run': packet['source_run_id'],
            'custom_agov_requirements': json.dumps(packet['requirement_ids']),
            'subject': packet['subject'],
            'description': packet['description'],
            'custom_agov_acceptance': packet['acceptance_tests'],
            'depends_on': [{'task': dep} for dep in packet['dependencies']],
        }
        self.receipts[packet['idempotency_key']] = {
            'exists': True,
            'task_id': ident,
            'content_sha256': planning._digest(packet),
        }
        if self.native_parent_dependency and packet['parent_task'] in self.tasks:
            self.tasks[packet['parent_task']]['depends_on'].append({'task': ident})
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError('ERP committed; response lost')
        return {'task_id': ident, 'created': True}

    async def get(self, doctype, ident):
        assert doctype == 'Task'
        value = deepcopy(self.tasks[ident])
        if self.tamper:
            value['project'] = 'PROJ-OTHER'
        return value

    async def request(self, method, path, params=None, headers=None):
        assert method == 'GET' and path.endswith('get_task_request_receipt')
        return {'message': deepcopy(self.receipts.get(params['idempotency_key'], {'exists': False}))}


class Planning(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.sqlite'
        modules = {
            'open_webui.utils.agentic_governance': types.SimpleNamespace(connect=dbconnect),
            'open_webui.utils.agentic_contracts.validate_architect_plan': architect_contract,
        }
        self.patch = patch.dict(sys.modules, modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.plan = deepcopy(PLAN)
        self.project = self.plan['metadata']['project_id']
        self.anchor = 'TASK-plan-anchor'
        self.arch = {
            'invocation_id': 'architect-fixture-001',
            'erp_project': self.project,
            'erp_task': self.anchor,
            'target_role': 'solution_architect',
            'state': 'handed_off',
            'agent_id': '00000000-0000-4000-8000-000000000001',
            'automation_run_id': 'architect-run-fixture',
            'source_invocation_id': None,
            'task_revision': 'task-revision-001',
            'lease_fence': 1,
        }
        self.pm = {
            'invocation_id': 'pm-fixture-001',
            'erp_project': self.project,
            'erp_task': self.anchor,
            'target_role': 'project_manager',
            'state': 'execution_finished',
            'agent_id': '00000000-0000-4000-8000-000000000002',
            'automation_run_id': 'pm-run-fixture',
            'source_invocation_id': self.arch['invocation_id'],
            'task_revision': 'task-revision-001',
            'lease_fence': 2,
        }
        self.packet = {
            'schema_version': 1,
            'project_id': self.project,
            'task_id': self.anchor,
            'task_revision': 'task-revision-001',
            'srs': {'revision': self.plan['metadata']['srs_version'], 'sha256': self.plan['metadata']['srs_sha256']},
            'binding': {
                'project_id': self.project,
                'role': 'project_manager',
                'agent_id': self.pm['agent_id'],
                'erp_user': 'ai-projects@abitcons.com',
            },
            'requirement_ids': [row['id'] for row in self.plan['requirements']],
            'objective': 'Review the architect plan.',
        }
        self.plan['metadata']['run_id'] = self.arch['invocation_id']
        self.plan['metadata']['agent_binding'].update(
            abit_agent_id=self.arch['agent_id'], erp_ai_user='ai-projects@abitcons.com'
        )
        self.result = {
            'invocation_id': self.arch['invocation_id'],
            'outcome': 'ready_for_review',
            'actor': {
                'project_id': self.project,
                'agent_id': self.arch['agent_id'],
                'role': 'solution_architect',
                'erp_user': 'ai-projects@abitcons.com',
            },
            'architect_plan': self.plan,
            'evidence': [],
        }
        self.artifacts = []
        for item in self.plan['designs'] + self.plan['mockups']:
            eid = 'evidence-' + item['id']
            kind = 'mockup' if item in self.plan['mockups'] else 'design'
            self.result['evidence'].append(
                {'id': eid, 'kind': kind, 'uri': item['artifact']['uri'], 'sha256': item['artifact']['sha256']}
            )
            self.artifacts.append(
                {
                    'id': eid,
                    'kind': kind,
                    'url': 'https://erp.abit.ai/private/files/' + eid + '.svg?fid=known-' + eid,
                    'sha256': item['artifact']['sha256'],
                }
            )
        with dbconnect(self.path) as db:
            db.executescript('''
              CREATE TABLE governance_invocation(invocation_id TEXT PRIMARY KEY,erp_project TEXT,erp_task TEXT,target_role TEXT,state TEXT,agent_id TEXT,automation_run_id TEXT,source_invocation_id TEXT,task_revision TEXT,lease_fence INTEGER,work_packet TEXT);
              CREATE TABLE governance_task_lease(erp_project TEXT,erp_task TEXT,invocation_id TEXT,fence INTEGER);
              CREATE TABLE governance_project_control(erp_project TEXT,state TEXT);
              CREATE TABLE governance_task_control(project TEXT,task TEXT,state TEXT);
              CREATE TABLE governance_result(invocation_id TEXT PRIMARY KEY,run_id TEXT,result_json TEXT,event_json TEXT,phase TEXT);
            ''')
            for row in (self.arch, self.pm):
                db.execute(
                    'INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    tuple(row.values()) + (json.dumps(self.packet) if row == self.pm else None,),
                )
            db.execute(
                'INSERT INTO governance_task_lease VALUES(?,?,?,?)',
                (self.project, self.anchor, self.pm['invocation_id'], 2),
            )
            db.execute('INSERT INTO governance_project_control VALUES(?,?)', (self.project, 'enabled'))
        self.save_results()
        self.bridge = ERP()

    def save_results(self):
        reviewed = {
            'invocation_id': self.pm['invocation_id'],
            'outcome': 'ready_for_review',
            'actor': self.packet['binding'],
            'work_packet_digest': planning._digest({k: v for k, v in self.packet.items() if k != 'content_sha256'}),
        }
        with dbconnect(self.path) as db:
            for row, result, event in (
                (self.arch, self.result, {'artifacts': self.artifacts}),
                (self.pm, reviewed, {}),
            ):
                db.execute(
                    'INSERT OR REPLACE INTO governance_result VALUES(?,?,?,?,?)',
                    (
                        row['invocation_id'],
                        row['automation_run_id'],
                        json.dumps(result),
                        json.dumps(event),
                        'published',
                    ),
                )

    async def run_plan(self, **kwargs):
        return await planning.materialize_backlog(
            self.bridge, self.arch, self.result, self.pm, self.packet, self.path, **kwargs
        )

    def checkpoint(self, outcome='continue', **changes):
        prior = {
            **self.pm,
            'invocation_id': 'pm-prior-fixture',
            'state': 'handed_off',
            'automation_run_id': 'pm-prior-run',
            'source_invocation_id': self.arch['invocation_id'],
            **changes,
        }
        packet = deepcopy(self.packet)
        reviewed = {
            'invocation_id': prior['invocation_id'],
            'outcome': outcome,
            'actor': packet['binding'],
            'work_packet_digest': planning._digest(packet),
        }
        if outcome == 'ready_for_review':
            reviewed['action_requests'] = [{'request_key': 'fixture-action'}]
        with dbconnect(self.path) as db:
            db.execute(
                'INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                tuple(prior.values()) + (json.dumps(packet),),
            )
            db.execute(
                'UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',
                (prior['invocation_id'], self.pm['invocation_id']),
            )
            db.execute(
                'INSERT INTO governance_result VALUES(?,?,?,?,?)',
                (prior['invocation_id'], prior['automation_run_id'], json.dumps(reviewed), '{}', 'published'),
            )
        self.pm['source_invocation_id'] = prior['invocation_id']
        return prior

    async def test_published_pm_dependency_continuations_preserve_original_architect_plan(self):
        self.checkpoint()
        result = await self.run_plan()
        self.assertEqual(len(result), 2)
        self.assertTrue(all(row['source_run_id'] == self.pm['automation_run_id'] for row in self.bridge.calls))

    async def test_blocked_pm_checkpoint_requires_its_original_published_result(self):
        self.checkpoint('blocked')
        self.assertEqual(len(await self.run_plan()), 2)

    async def test_pm_checkpoint_chain_is_bounded_before_any_erp_write(self):
        prior = self.checkpoint()
        previous = prior['invocation_id']
        with dbconnect(self.path) as db:
            for index in range(21):
                ident = 'pm-depth-' + str(index)
                row = {**prior, 'invocation_id': ident, 'automation_run_id': 'run-depth-' + str(index)}
                db.execute(
                    'UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?', (ident, previous)
                )
                db.execute(
                    'INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    tuple(row.values()) + (json.dumps(self.packet),),
                )
                value = {
                    'invocation_id': ident,
                    'outcome': 'continue',
                    'actor': self.packet['binding'],
                    'work_packet_digest': planning._digest(self.packet),
                }
                db.execute(
                    'INSERT INTO governance_result VALUES(?,?,?,?,?)',
                    (ident, row['automation_run_id'], json.dumps(value), '{}', 'published'),
                )
                previous = ident
        with self.assertRaisesRegex(ValueError, 'bounded'):
            await self.run_plan()
        self.assertEqual(self.bridge.calls, [])

    async def test_pm_ready_with_action_requests_is_a_valid_planning_checkpoint(self):
        self.checkpoint('ready_for_review')
        self.assertEqual(len(await self.run_plan()), 2)

    async def test_unpublished_cross_task_or_unrelated_pm_checkpoint_is_rejected(self):
        prior = self.checkpoint()
        with dbconnect(self.path) as db:
            db.execute(
                "UPDATE governance_result SET phase='uncertain' WHERE invocation_id=?", (prior['invocation_id'],)
            )
        with self.assertRaisesRegex(ValueError, 'published'):
            await self.run_plan()
        with dbconnect(self.path) as db:
            db.execute("UPDATE governance_result SET phase='published'")
            db.execute(
                'UPDATE governance_invocation SET erp_task=? WHERE invocation_id=?',
                ('TASK-other', prior['invocation_id']),
            )
        with self.assertRaisesRegex(ValueError, 'same project Task'):
            await self.run_plan()
        self.assertEqual(self.bridge.calls, [])

    async def test_changed_pm_checkpoint_packet_and_cyclic_chain_are_rejected(self):
        prior = self.checkpoint()
        changed = {**self.packet, 'objective': 'Other source task'}
        with dbconnect(self.path) as db:
            db.execute(
                'UPDATE governance_invocation SET work_packet=? WHERE invocation_id=?',
                (json.dumps(changed), prior['invocation_id']),
            )
        with self.assertRaisesRegex(ValueError, 'business inputs'):
            await self.run_plan()
        with dbconnect(self.path) as db:
            db.execute(
                'UPDATE governance_invocation SET work_packet=?,source_invocation_id=? WHERE invocation_id=?',
                (json.dumps(self.packet), self.pm['invocation_id'], prior['invocation_id']),
            )
        with self.assertRaisesRegex(ValueError, 'acyclic'):
            await self.run_plan()
        self.assertEqual(self.bridge.calls, [])

    async def test_checkpoint_original_result_digest_and_outcome_cannot_be_forged(self):
        prior = self.checkpoint()
        with dbconnect(self.path) as db:
            result = json.loads(
                db.execute(
                    'SELECT result_json FROM governance_result WHERE invocation_id=?', (prior['invocation_id'],)
                ).fetchone()[0]
            )
            result['work_packet_digest'] = '0' * 64
            db.execute(
                'UPDATE governance_result SET result_json=? WHERE invocation_id=?',
                (json.dumps(result), prior['invocation_id']),
            )
        with self.assertRaisesRegex(ValueError, 'original run'):
            await self.run_plan()
        result['work_packet_digest'] = planning._digest(self.packet)
        result['outcome'] = 'ready_for_review'
        with dbconnect(self.path) as db:
            db.execute(
                'UPDATE governance_result SET result_json=? WHERE invocation_id=?',
                (json.dumps(result), prior['invocation_id']),
            )
        with self.assertRaisesRegex(ValueError, 'allowed dependency checkpoint'):
            await self.run_plan()
        self.assertEqual(self.bridge.calls, [])

    async def test_verified_mapping_parent_dependencies_and_payload_fields(self):
        result = await self.run_plan()
        self.assertEqual(len(result), 2)
        self.assertEqual(len(self.bridge.calls), 2)
        first, second = self.bridge.calls
        self.assertEqual(first['parent_task'], self.anchor)
        self.assertEqual(second['dependencies'], [result['TASKPLAN-API-01']])
        self.assertTrue(all(p['defer_dispatch'] for p in self.bridge.calls))
        self.assertTrue(all(p['plan_digest'] == planning._digest(self.plan) for p in self.bridge.calls))
        self.assertIn('Input:', first['description'])
        self.assertIn('Independent QA:', first['acceptance_tests'])
        self.assertEqual(first['source_run_id'], self.pm['automation_run_id'])
        self.assertTrue(first['artifacts'][0]['url'].startswith('https://erp.abit.ai/private/files/'))

    async def test_repeating_publication_reuses_original_tasks_without_write(self):
        first = await self.run_plan()
        second = await self.run_plan()
        self.assertEqual(first, second)
        self.assertEqual(len(self.bridge.calls), 2)

    async def test_unknown_ack_recovers_original_receipt_no_duplicate_write(self):
        self.bridge.lose_ack = True
        with self.assertRaises(TimeoutError):
            await self.run_plan()
        result = await self.run_plan()
        self.assertEqual(len(result), 2)
        self.assertEqual(len(self.bridge.calls), 2)

    async def test_absent_unknown_receipt_never_authorizes_retry(self):
        self.bridge.no_write_error = True
        with self.assertRaises(TimeoutError):
            await self.run_plan()
        with self.assertRaises(planning.BacklogUncertain):
            await self.run_plan()
        self.assertEqual(len(self.bridge.calls), 1)

    async def test_receipt_changed_content_is_rejected_without_resend(self):
        self.bridge.lose_ack = True
        with self.assertRaises(TimeoutError):
            await self.run_plan()
        next(iter(self.bridge.receipts.values()))['content_sha256'] = '0' * 64
        with self.assertRaises(planning.BacklogUncertain):
            await self.run_plan()
        self.assertEqual(len(self.bridge.calls), 1)

    async def test_cross_project_or_stale_pm_refused_before_creation(self):
        self.pm['erp_project'] = 'PROJ-OTHER'
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_canceled_pm_lease_refused(self):
        with dbconnect(self.path) as db:
            db.execute('UPDATE governance_task_lease SET invocation_id=NULL')
        with self.assertRaisesRegex(ValueError, 'lease'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_changed_plan_result_is_not_new_authority(self):
        self.result['architect_plan']['tasks'][0]['subject'] = 'Altered after publication'
        with self.assertRaisesRegex(ValueError, 'result changed'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_cycle_refused_before_partial_write(self):
        self.plan['tasks'][0]['depends_on'] = ['TASKPLAN-UI-02']
        self.save_results()
        with self.assertRaisesRegex(ValueError, 'cycle'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_missing_or_changed_artifact_publication_rejected(self):
        with self.assertRaisesRegex(ValueError, 'publication differs'):
            await self.run_plan(verified_artifacts=[])
        self.result['evidence'][0]['sha256'] = '0' * 64
        self.save_results()
        with self.assertRaisesRegex(ValueError, 'exact original evidence'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_nested_parent_relationship_is_preserved(self):
        self.plan['tasks'][1]['parent_task_key'] = 'TASKPLAN-API-01'
        self.plan['tasks'][1]['depends_on'] = []
        self.save_results()
        mapping = await self.run_plan()
        self.assertEqual(self.bridge.calls[1]['parent_task'], mapping['TASKPLAN-API-01'])
        self.assertTrue(self.bridge.calls[0]['is_group'])
        self.assertFalse(self.bridge.calls[1]['is_group'])

    async def test_task_readback_mismatch_retains_uncertain_original(self):
        self.bridge.tamper = True
        with self.assertRaises(planning.BacklogUncertain):
            await self.run_plan()
        self.bridge.tamper = False
        result = await self.run_plan()
        self.assertEqual(len(result), 2)
        self.assertEqual(len(self.bridge.calls), 2)

    async def test_concurrent_publication_cannot_duplicate_create(self):
        self.bridge.entered = asyncio.Event()
        self.bridge.resume = asyncio.Event()
        pending = asyncio.create_task(self.run_plan())
        await self.bridge.entered.wait()
        with self.assertRaises(planning.BacklogUncertain):
            await self.run_plan()
        self.bridge.resume.set()
        self.assertEqual(len(await pending), 2)
        self.assertEqual(len(self.bridge.calls), 2)

    async def test_all_payload_bounds_checked_before_any_write(self):
        self.plan['tasks'][1]['steps'] = [
            {'input': 'I' * 2000, 'action': 'A' * 2000, 'output': 'O' * 2000, 'verification': 'V' * 2000}
        ]
        self.save_results()
        with self.assertRaises(ValueError):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_plan_task_count_limit_is_checked_before_any_write(self):
        for n in range(49):
            item = deepcopy(self.plan['tasks'][0])
            item['task_key'] = 'TASKPLAN-EXTRA-' + str(n)
            item['idempotency_key'] += '-extra-' + str(n)
            item['subject'] += ' extra ' + str(n)
            self.plan['tasks'].append(item)
            self.plan['tests'][0]['task_keys'].append(item['task_key'])
        self.save_results()
        with self.assertRaisesRegex(ValueError, 'at most 50'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_wrong_artifact_kind_cannot_stand_in_for_design(self):
        self.artifacts[0]['kind'] = 'test_log'
        self.save_results()
        with self.assertRaisesRegex(ValueError, 'unique verified ERP File'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_native_parent_child_dependency_is_normalized_only_for_own_plan(self):
        self.plan['tasks'][1]['parent_task_key'] = 'TASKPLAN-API-01'
        self.plan['tasks'][1]['depends_on'] = []
        self.save_results()
        self.bridge.native_parent_dependency = True
        mapping = await self.run_plan()
        self.assertEqual(await self.run_plan(), mapping)
        self.assertEqual(len(self.bridge.calls), 2)
        self.bridge.tasks[mapping['TASKPLAN-API-01']]['depends_on'].append({'task': 'TASK-human-change'})
        with self.assertRaises(planning.BacklogUncertain):
            await self.run_plan()

    async def test_native_child_dependency_recovers_unknown_child_receipt(self):
        self.plan['tasks'][1]['parent_task_key'] = 'TASKPLAN-API-01'
        self.plan['tasks'][1]['depends_on'] = []
        self.save_results()
        self.bridge.native_parent_dependency = True
        original = self.bridge.create_task

        async def lose_child(packet):
            if packet['subject'] == self.plan['tasks'][1]['subject']:
                self.bridge.lose_ack = True
            return await original(packet)

        self.bridge.create_task = lose_child
        with self.assertRaises(TimeoutError):
            await self.run_plan()
        self.bridge.create_task = original
        self.assertEqual(len(await self.run_plan()), 2)
        self.assertEqual(len(self.bridge.calls), 2)

    async def test_explicit_child_dependency_on_parent_is_a_native_cycle(self):
        self.plan['tasks'][1]['parent_task_key'] = 'TASKPLAN-API-01'
        self.save_results()
        with self.assertRaisesRegex(ValueError, 'native ERP'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_paused_task_or_project_cannot_materialize_backlog(self):
        with dbconnect(self.path) as db:
            db.execute('INSERT INTO governance_task_control VALUES(?,?,?)', (self.project, self.anchor, 'paused'))
        with self.assertRaisesRegex(ValueError, 'Task backlog'):
            await self.run_plan()
        with dbconnect(self.path) as db:
            db.execute('DELETE FROM governance_task_control')
            db.execute("UPDATE governance_project_control SET state='paused'")
        with self.assertRaisesRegex(ValueError, 'Project backlog'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)

    async def test_pm_srs_or_snapshot_changes_rejected(self):
        self.packet['srs']['revision'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'WorkPacket changed'):
            await self.run_plan()
        self.assertFalse(self.bridge.calls)


if __name__ == '__main__':
    unittest.main()

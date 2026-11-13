"""Real isolated Automation/ledger SQLite onboarding and lost-ack recovery."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[1] / 'open_webui'
P = 'PROJ-0091'
USERS = {
    'solution_architect': 'ai-projects@abitcons.com',
    'project_manager': 'ai-projects@abitcons.com',
    'developer': 'ai-developer@abitcons.com',
    'qa': 'ai-qa@abitcons.com',
    'operations': 'ai-operations@abitcons.com',
    'it_manager': 'ai-operations@abitcons.com',
}


class Bridge:
    secret = 'fixture-only-not-a-live-token'

    def __init__(self, fixture, project=P):
        self.f = fixture
        self.erp = self
        self.project = project
        self.bindings = {}
        self.register_calls = 0
        self.lose_ack = False
        self.denied = False
        self.check_pending = False

    async def request(self, method, path, **kwargs):
        self.f.assertEqual(('GET', self.f.onboarding.RPC), (method, path))
        if self.denied:
            raise PermissionError('ERP binding inventory unavailable')
        return {
            'message': {
                'project_id': self.project,
                'project_revision': 'project-rev-1',
                'role_users': deepcopy(USERS),
                'bindings': deepcopy(list(self.bindings.values())),
            }
        }

    async def register(self, project, revision, records):
        self.register_calls += 1
        self.f.assertEqual(self.project, project)
        if self.check_pending:
            rows = await self.f.rows()
            self.f.assertTrue(rows)
            self.f.assertTrue(
                all(not row.is_active and row.meta['governance_onboarding_state'] == 'pending_binding' for row in rows)
            )
        for index, row in enumerate(records):
            value = self.f.binding(row['role'], row['agent_id'], self.project)
            if row['role'] in self.bindings:
                self.f.assertEqual(value, self.bindings[row['role']])
            self.bindings[row['role']] = value
            if self.lose_ack and index == 2:
                self.lose_ack = False
                raise TimeoutError('Original bindings committed; acknowledgment lost')


class Onboarding(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite'
        base = declarative_base()
        self.engine = create_async_engine('sqlite+aiosqlite:///' + str(Path(self.temp.name) / 'app.sqlite'))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @asynccontextmanager
        async def db_context():
            async with self.sessions() as db:
                try:
                    yield db
                except BaseException:
                    await db.rollback()
                    raise

        top = types.ModuleType('open_webui')
        top.__path__ = [str(ROOT)]
        utils = types.ModuleType('open_webui.utils')
        utils.__path__ = [str(ROOT / 'utils')]
        top.utils = utils
        env = types.ModuleType('open_webui.env')
        env.DATA_DIR = Path(self.temp.name)
        db = types.ModuleType('open_webui.internal.db')
        db.Base = base
        db.get_async_db_context = db_context
        misc = types.ModuleType('open_webui.utils.misc')
        misc.json_text_variants = lambda value: [value]
        self.modules = patch.dict(
            sys.modules,
            {
                'open_webui': top,
                'open_webui.utils': utils,
                'open_webui.env': env,
                'open_webui.internal.db': db,
                'open_webui.utils.misc': misc,
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.models = self.load('open_webui.models.automations', ROOT / 'models/automations.py')
        self.g = self.load('open_webui.utils.agentic_governance', ROOT / 'utils/agentic_governance.py')
        utils.agentic_governance = self.g
        self.onboarding = self.load('open_webui.utils.agentic_onboarding', ROOT / 'utils/agentic_onboarding.py')
        async with self.engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
        self.addAsyncCleanup(self.engine.dispose)
        self.bridge = Bridge(self)
        self.gate_hook = None

    def load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def binding(self, role, ident, project=P):
        return {
            'name': 'fixture-' + role,
            'project': project,
            'agent_role': role,
            'slot': 'primary',
            'erp_ai_user': USERS[role],
            'abit_agent_id': ident,
            'agent_url': 'https://abit.ai/automations/' + ident,
            'verification_state': 'Verified',
            'is_enabled': True,
        }

    async def gate(self, project, config):
        if self.gate_hook:
            await self.gate_hook()
            self.gate_hook = None
        return {'modified': 'project-rev-1'}

    async def provision(self, project=P, bridge=None):
        return await self.onboarding.provision(
            project,
            'owner-fixture',
            USERS,
            {},
            'abitdot',
            title={r: r for r in USERS},
            prompt=lambda *args: 'New fixture role policy',
            project_gate=self.gate,
            bridge=bridge or self.bridge,
            db_path=self.path,
        )

    async def rows(self):
        async with self.sessions() as db:
            return list(
                (await db.execute(select(self.models.Automation).order_by(self.models.Automation.id))).scalars()
            )

    async def existing(
        self, role='developer', *, ident=None, owner='owner-fixture', managed=True, project=P, active=True
    ):
        ident = ident or str(uuid5(NAMESPACE_URL, 'existing-specialist:' + project + ':' + role))
        meta = {
            'agentic_project': project,
            'agentic_role': role,
            'erp_role_user': USERS[role],
            'governance_managed': managed,
            'api_triggered_only': managed,
        }
        async with self.sessions() as db:
            db.add(
                self.models.Automation(
                    id=ident,
                    user_id=owner,
                    name='Existing persistent fixture',
                    data={'prompt': 'Preserve original policy'},
                    meta=meta,
                    is_active=active,
                    next_run_at=None,
                    created_at=17,
                    updated_at=18,
                )
            )
            await db.commit()
        return ident

    async def test_bootstrap_six_agents_and_repeated_provision_reuses_originals(self):
        first = await self.provision()
        second = await self.provision()
        self.assertEqual(first, second)
        self.assertEqual(6, len(await self.rows()))
        self.assertEqual(6, len({row['agent_id'] for row in first}))
        self.assertTrue(all(row.is_active and row.next_run_at is None for row in await self.rows()))

    async def test_existing_nonbootstrap_uuids_are_reused_and_bound_without_new_instances(self):
        identities = {role: await self.existing(role) for role in USERS}
        self.bridge.bindings = {role: self.binding(role, ident) for role, ident in identities.items()}
        result = await self.provision()
        self.assertEqual(identities, {row['role']: row['agent_id'] for row in result})
        self.assertEqual(6, len(await self.rows()))
        self.assertTrue(
            all(row.created_at == 17 and row.data['prompt'] == 'Preserve original policy' for row in await self.rows())
        )
        self.assertEqual(identities['developer'], self.g.registered_agent_id(P, 'developer', self.path))
        self.g.project_control(P, 'enable', actor='human-fixture', policy_revision='project-rev-1', db_path=self.path)
        row, _ = self.g.enqueue(
            invocation_id='fixture-original-000001',
            project=P,
            task='TASK-fixture-01',
            from_role='project_manager',
            target_role='developer',
            task_revision='task-1',
            context='Exact fixture',
            gate={
                'enabled': True,
                'pm_task_automation_enabled': True,
                'project_status': 'Open',
                'assigned_user': 'ai-projects@abitcons.com',
                'revision': 'project-rev-1',
            },
            actor='human-fixture',
            db_path=self.path,
        )
        self.assertEqual(identities['developer'], row['agent_id'])

    async def test_legacy_specialist_adoption_is_inactive_until_erp_verifies_every_binding(self):
        ident = await self.existing(managed=False)
        self.bridge.check_pending = True
        result = await self.provision()
        self.assertEqual(ident, next(row['agent_id'] for row in result if row['role'] == 'developer'))
        self.assertEqual(6, len(await self.rows()))
        old = next(row for row in await self.rows() if row.id == ident)
        self.assertEqual({'prompt': 'Preserve original policy'}, old.data)
        self.assertTrue(old.meta['governance_managed'] and old.is_active)

    async def test_ambiguous_role_is_rejected_before_creating_any_other_agents(self):
        await self.existing()
        await self.existing(ident=str(uuid5(NAMESPACE_URL, 'duplicate-project-role')))
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            await self.provision()
        self.assertEqual(2, len(await self.rows()))
        self.assertFalse(self.g.roster(P, self.path))
        self.assertEqual(0, self.bridge.register_calls)

    async def test_existing_erp_and_actual_agent_conflict_is_rejected_before_writes(self):
        await self.existing()
        self.bridge.bindings['developer'] = self.binding('developer', str(uuid5(NAMESPACE_URL, 'different-erp-agent')))
        with self.assertRaisesRegex(ValueError, 'identities conflict'):
            await self.provision()
        self.assertEqual(1, len(await self.rows()))
        self.assertIsNone(self.onboarding.current_plan(P, self.path))

    async def test_cross_owner_specialist_is_not_adopted_or_duplicated(self):
        await self.existing(owner='another-owner')
        with self.assertRaisesRegex(ValueError, 'owner'):
            await self.provision()
        self.assertEqual(1, len(await self.rows()))

    async def test_paused_existing_specialist_is_not_silently_reactivated(self):
        await self.existing(active=False)
        with self.assertRaisesRegex(ValueError, 'paused'):
            await self.provision()
        rows = await self.rows()
        self.assertEqual(1, len(rows))
        self.assertFalse(rows[0].is_active)

    async def test_out_of_scope_bootstrap_id_is_not_overwritten_or_duplicated(self):
        await self.existing(ident=self.g.agent_id(P, 'developer'), project='PROJ-0197')
        with self.assertRaisesRegex(ValueError, 'out-of-scope'):
            await self.provision()
        self.assertEqual(1, len(await self.rows()))
        self.assertFalse(self.g.roster(P, self.path))

    async def test_qa_shared_user_configuration_must_be_independent_before_creation(self):
        users = {**USERS, 'qa': USERS['developer']}
        context = {'project_id': P, 'project_revision': 'project-rev-1', 'role_users': users, 'bindings': []}
        with self.assertRaisesRegex(ValueError, 'Independent QA'):
            self.onboarding.reconcile(P, 'owner-fixture', users, context, [], [])
        self.assertFalse(await self.rows())

    async def test_missing_verified_actual_agent_is_not_silently_recreated(self):
        self.bridge.bindings['developer'] = self.binding('developer', str(uuid5(NAMESPACE_URL, 'missing-actual-agent')))
        with self.assertRaisesRegex(ValueError, 'missing'):
            await self.provision()
        self.assertFalse(await self.rows())

    async def test_global_registry_conflict_is_rejected_before_creating_local_agents(self):
        self.g.enroll(
            'PROJ-0197',
            'owner-fixture',
            {'developer': USERS['developer']},
            self.path,
            identities={'developer': self.g.agent_id(P, 'developer')},
        )
        with self.assertRaisesRegex(ValueError, 'already registered'):
            await self.provision()
        self.assertFalse(await self.rows())
        self.assertIsNone(self.onboarding.current_plan(P, self.path))

    async def test_unavailable_erp_inventory_is_not_interpreted_as_no_existing_agents(self):
        self.bridge.denied = True
        with self.assertRaises(PermissionError):
            await self.provision()
        self.assertFalse(await self.rows())
        self.assertFalse(self.g.roster(P, self.path))

    async def test_revoked_binding_prevents_any_local_creation(self):
        value = self.binding('developer', self.g.agent_id(P, 'developer'))
        value.update(verification_state='Revoked', is_enabled=False)
        self.bridge.bindings['developer'] = value
        with self.assertRaisesRegex(ValueError, 'revoked'):
            await self.provision()
        self.assertFalse(await self.rows())

    async def test_live_legacy_run_prevents_adoption_without_cancelling_it(self):
        ident = await self.existing(managed=False)
        async with self.sessions() as db:
            db.add(
                self.models.AutomationRun(id='existing-live-run', automation_id=ident, status='running', created_at=19)
            )
            await db.commit()
        with self.assertRaisesRegex(ValueError, 'active work'):
            await self.provision()
        self.assertEqual(1, len(await self.rows()))
        async with self.sessions() as db:
            self.assertEqual('running', (await db.get(self.models.AutomationRun, 'existing-live-run')).status)

    async def test_lost_remote_ack_recovers_same_six_inactive_original_ids(self):
        self.bridge.lose_ack = True
        with self.assertRaises(TimeoutError):
            await self.provision()
        original = {row.id for row in await self.rows()}
        self.assertEqual(6, len(original))
        self.assertTrue(all(not row.is_active for row in await self.rows()))
        self.assertEqual(3, len(self.bridge.bindings))
        recovered = await self.provision()
        self.assertEqual(original, {row['agent_id'] for row in recovered})
        self.assertEqual(6, len(self.bridge.bindings))
        self.assertTrue(all(row.is_active for row in await self.rows()))

    async def test_deferred_unknown_external_original_blocks_legacy_adoption(self):
        import sqlite3

        ident = await self.existing(managed=False)
        async with self.sessions() as db:
            db.add(
                self.models.AutomationRun(id='deferred-original', automation_id=ident, status='deferred', created_at=19)
            )
            await db.commit()
        with sqlite3.connect(Path(self.temp.name) / 'abitdot-primary.sqlite') as dot:
            dot.execute(
                'CREATE TABLE dot_primary_runs(automation_id TEXT,original_run_id TEXT,status TEXT,response_text TEXT)'
            )
            dot.execute('INSERT INTO dot_primary_runs VALUES(?,?,?,?)', (ident, 'deferred-original', 'unknown', None))
        with self.assertRaisesRegex(ValueError, 'unresolved external original'):
            await self.provision()
        self.assertEqual(1, len(await self.rows()))
        self.assertFalse(self.g.roster(P, self.path))
        self.assertEqual(0, self.bridge.register_calls)

    async def test_positive_historical_dot_reply_is_not_treated_as_active_work(self):
        import sqlite3

        ident = await self.existing(managed=False)
        async with self.sessions() as db:
            db.add(
                self.models.AutomationRun(id='returned-original', automation_id=ident, status='error', created_at=19)
            )
            await db.commit()
        with sqlite3.connect(Path(self.temp.name) / 'abitdot-primary.sqlite') as dot:
            dot.execute(
                'CREATE TABLE dot_primary_runs(automation_id TEXT,original_run_id TEXT,status TEXT,response_text TEXT)'
            )
            dot.execute(
                'INSERT INTO dot_primary_runs VALUES(?,?,?,?)',
                (ident, 'returned-original', 'delegated', 'Original returned response'),
            )
        self.assertEqual(ident, next(row['agent_id'] for row in await self.provision() if row['role'] == 'developer'))
        self.assertEqual(6, len(await self.rows()))

    async def test_interruption_after_local_commit_recovers_original_plan(self):
        checkpoint = self.onboarding.checkpoint

        def interrupt(plan, phase, db_path=None):
            if phase == 'local_pending':
                raise RuntimeError('Interrupted before ERP registration')
            return checkpoint(plan, phase, db_path)

        with patch.object(self.onboarding, 'checkpoint', interrupt):
            with self.assertRaises(RuntimeError):
                await self.provision()
        original = {row.id for row in await self.rows()}
        self.assertEqual(6, len(original))
        self.assertTrue(all(not row.is_active for row in await self.rows()))
        self.assertEqual(original, {row['agent_id'] for row in await self.provision()})

    async def test_concurrent_ordinary_schedule_edit_is_preserved_before_adoption(self):
        ident = await self.existing(managed=False)

        async def change():
            async with self.sessions() as db:
                row = await db.get(self.models.Automation, ident)
                row.next_run_at = 99999
                row.updated_at = 99
                await db.commit()

        self.gate_hook = change
        with self.assertRaisesRegex(ValueError, 'scheduled work'):
            await self.provision()
        rows = await self.rows()
        self.assertEqual(1, len(rows))
        self.assertEqual(99999, rows[0].next_run_at)
        self.assertEqual(0, self.bridge.register_calls)

    async def test_parallel_provisioning_uses_same_original_six_agents(self):
        import asyncio

        first, second = await asyncio.gather(self.provision(), self.provision())
        self.assertEqual(first, second)
        self.assertEqual(6, len(await self.rows()))

    async def test_two_projects_share_erp_users_and_keep_distinct_persistent_agents(self):
        first = await self.provision()
        second = await self.provision('PROJ-0197', Bridge(self, 'PROJ-0197'))
        self.assertFalse({r['agent_id'] for r in first} & {r['agent_id'] for r in second})
        self.assertEqual({r['erp_user'] for r in first}, {r['erp_user'] for r in second})
        self.assertEqual(12, len(await self.rows()))


if __name__ == '__main__':
    unittest.main()

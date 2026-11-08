"""Scheduled attempt regressions with real SQLite transactions and isolated workers."""

import asyncio
import logging
import os
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import column_property, declarative_base
from typing import Literal, Optional

import test_automation_attachments as fixture


class AttemptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stage = os.environ.get('ABIT_ATTEMPTS_BACKEND_STAGE')
        if stage:
            self.addCleanup(setattr, fixture, 'SOURCE', fixture.SOURCE)
            fixture.SOURCE = Path(stage)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.url = f'sqlite+aiosqlite:///{directory.name}/test.db'
        self.engine = create_async_engine(self.url)
        self.addAsyncCleanup(self.engine.dispose)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        base = declarative_base()

        @asynccontextmanager
        async def context(db=None):
            if db is not None:
                yield db
            else:
                async with self.sessions() as session:
                    yield session

        self.ns = {
            **vars(sa),
            **globals(),
            'Base': base,
            'get_async_db_context': context,
            'get_async_db': context,
            'log': logging.getLogger('automation-attempt-test'),
        }
        fixture.source_exec('models/automations.py', self.ns)
        self.table = self.ns['Automations']
        self.runs = self.ns['AutomationRuns']
        self.form = self.ns['AutomationForm']
        self.now = time.time_ns()
        self.next_at = self.now + 1_000_000_000

        class User(base):
            __tablename__ = 'user'
            id = sa.Column(sa.Text, primary_key=True)
            timezone = sa.Column(sa.Text)

        async with self.engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
            await conn.execute(sa.insert(User).values(id='owner', timezone='UTC'))

        class RecurrenceEvaluationTimeout(Exception):
            pass

        utils = ModuleType('open_webui.utils.automations')
        self.next_run = AsyncMock(side_effect=lambda *_args, **_kwargs: self.next_at)
        utils.next_run_ns = self.next_run
        modules = {
            'open_webui.models.users': SimpleNamespace(User=User),
            'open_webui.utils.automations': utils,
            'open_webui.utils.recurrence': SimpleNamespace(RecurrenceEvaluationTimeout=RecurrenceEvaluationTimeout),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        fixture.source_exec(
            'utils/automations.py',
            self.ns,
            {'execute_scheduled_automation', '_run_scheduled_completion', '_record_run'},
        )

    def make_form(self, same_chat=False, **fields):
        return self.form(name='Fixture', data={
            'prompt': 'test', 'model_id': 'test', 'rrule': 'FREQ=MINUTELY',
            'continue_previous_chat': same_chat,
            'target': {'type': 'chat', 'chat_mode': 'same' if same_chat else 'new'},
        }, **fields)

    async def claim(self):
        claimed = await self.table.claim_due(self.now)
        self.assertEqual(len(claimed), 1)
        self.now = self.next_at
        self.next_at += 1_000_000_000
        return claimed[0]

    async def test_unlimited_and_manual_runs(self):
        automation = await self.table.insert('owner', self.make_form(), self.now)
        self.assertEqual(automation.attempt_limit, 0)
        for count in range(1, 5):
            row, run_id = await self.claim()
            await self.runs.finish_claim(run_id, 'success')
            self.assertEqual(row.attempts_used, count)
        await self.ns['_record_run'](automation.id, 'success')
        row = await self.table.get_by_id(automation.id)
        self.assertEqual(row.attempts_used, 4)
        self.assertFalse(row.is_stale)
        self.assertIsNotNone(row.next_run_at)
        self.assertEqual(len(await self.runs.get_by_automation(automation.id)), 5)

    async def test_finite_failed_and_retried_runs(self):
        automation = await self.table.insert('owner', self.make_form(attempt_limit=2), self.now)
        _, first = await self.claim()
        row, final = await self.claim()
        self.assertEqual(row.attempts_used, 2)
        self.assertIsNone(row.next_run_at)
        self.assertFalse((await self.table.get_by_id(automation.id)).is_stale)
        self.assertEqual(await self.table.claim_due(self.now), [])

        async def executor(_app, row, scheduled_run_id):
            # Simulate several model attempts inside the single scheduled execution.
            upstream = AsyncMock(side_effect=[RuntimeError('busy'), RuntimeError('fallback failed')])
            for _ in range(2):
                try:
                    await upstream()
                except RuntimeError:
                    continue
            await self.ns['_record_run'](row.id, 'error', error='fallback failed', scheduled_run_id=scheduled_run_id)
            await self.ns['_record_run'](row.id, 'success', scheduled_run_id=scheduled_run_id)
            self.assertEqual(upstream.await_count, 2)

        self.ns['execute_automation'] = executor
        await self.ns['execute_scheduled_automation'](None, row, final)
        self.assertFalse((await self.table.get_by_id(row.id)).is_stale)
        await self.runs.finish_claim(first, 'success')
        self.assertTrue((await self.table.get_by_id(row.id)).is_stale)
        self.assertEqual((await self.table.search_automations('owner', status='stale')).total, 1)
        self.assertEqual((await self.table.search_automations('owner', status='active')).total, 0)
        self.assertTrue((await self.table.toggle(row.id, self.now)).is_stale)
        outcomes = {run.id: (run.status, run.error) for run in await self.runs.get_by_automation(row.id)}
        self.assertEqual(outcomes, {first: ('success', None), final: ('error', 'fallback failed')})

    async def test_concurrent_claims_and_legacy_edits(self):
        automation = await self.table.insert('owner', self.make_form(attempt_limit=2), self.now)
        barrier = asyncio.Barrier(2)

        async def next_run(*_args, **_kwargs):
            await barrier.wait()
            return self.next_at

        self.next_run.side_effect = next_run
        claims = await asyncio.wait_for(
            asyncio.gather(self.table.claim_due(self.now), self.table.claim_due(self.now)), timeout=10
        )
        self.assertEqual(sum(map(len, claims)), 1)
        self.assertEqual((await self.table.get_by_id(automation.id)).attempts_used, 1)
        await self.table.update_by_id(automation.id, self.make_form(), self.next_at)
        contenders = await asyncio.gather(*(self.table.claim_due(self.next_at) for _ in range(10)))
        self.assertEqual(sum(map(len, contenders)), 1)
        row = await self.table.get_by_id(automation.id)
        self.assertEqual((row.attempt_limit, row.attempts_used, row.next_run_at), (2, 2, None))

    async def test_same_chat_waits_for_the_previous_turn(self):
        automation = await self.table.insert('owner', self.make_form(same_chat=True, attempt_limit=2), self.now)
        _, first = await self.claim()
        self.assertEqual(await self.table.claim_due(self.now), [])
        await self.runs.finish_claim(first, 'success')
        _, final = await self.claim()
        await self.runs.finish_claim(final, 'success')
        self.assertTrue((await self.table.get_by_id(automation.id)).is_stale)

    async def test_restart_and_raising_the_limit(self):
        automation = await self.table.insert('owner', self.make_form(attempt_limit=1), self.now)
        _, interrupted = await self.claim()
        await self.engine.dispose()
        restarted = create_async_engine(self.url)
        self.addAsyncCleanup(restarted.dispose)
        self.sessions = async_sessionmaker(restarted, expire_on_commit=False)
        persisted = await self.table.get_by_id(automation.id)
        self.assertEqual((persisted.attempt_limit, persisted.attempts_used, persisted.is_stale), (1, 1, False))
        self.assertEqual(await self.table.claim_due(self.now), [])
        await self.runs.expire_claims(time.time_ns() + self.ns['SCHEDULED_RUN_LEASE_NS'])
        self.assertFalse(await self.runs.renew_claim(interrupted))
        self.assertTrue((await self.table.get_by_id(automation.id)).is_stale)
        self.assertEqual((await self.runs.get_latest(automation.id)).status, 'error')
        resumed = await self.table.update_by_id(automation.id, self.make_form(attempt_limit=2), self.now)
        self.assertEqual((resumed.attempts_used, resumed.is_stale), (1, False))
        _, run_id = await self.claim()
        await self.runs.finish_claim(run_id, 'success')
        self.assertTrue((await self.table.get_by_id(automation.id)).is_stale)

    async def test_waits_for_terminal_chat_result(self):
        gate = asyncio.Event()
        read = AsyncMock(return_value={'done': True, 'error': {'content': 'fallback failed'}})
        self.ns['Chats'] = SimpleNamespace(get_message_by_id_and_message_id=read)

        async def handler(request, payload, user):
            request.state.automation_completion_tasks.append(asyncio.create_task(gate.wait()))
            payload.clear()

        app = SimpleNamespace(state=SimpleNamespace(CHAT_COMPLETION_HANDLER=handler))
        check = self.ns['_run_scheduled_completion']
        task = asyncio.create_task(check(app, SimpleNamespace(state=SimpleNamespace()), {'chat_id': 'chat', 'id': 'reply'}, None))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        read.assert_not_called()
        gate.set()
        with self.assertRaisesRegex(RuntimeError, 'fallback failed'):
            await task
        read.assert_awaited_once_with('chat', 'reply')

    async def test_validation_and_legacy_migration(self):
        for value in (-1, 1.5, True, '2', 9_007_199_254_740_992):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.make_form(attempt_limit=value)
        engine = sa.create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE automation (id TEXT PRIMARY KEY)')
            conn.exec_driver_sql('CREATE TABLE automation_run (id TEXT PRIMARY KEY)')
            conn.exec_driver_sql("INSERT INTO automation VALUES ('legacy')")
            conn.exec_driver_sql("INSERT INTO automation_run VALUES ('legacy-run')")
            with Operations.context(MigrationContext.configure(conn)):
                ns = {}
                fixture.source_exec('migrations/versions/6d3c5a71e2fb_add_automation_attempts.py', ns, skip_imports=False)
                ns['upgrade']()
            self.assertEqual(conn.exec_driver_sql('SELECT attempt_limit, attempts_used FROM automation').one(), (0, 0))
            self.assertIsNone(conn.exec_driver_sql('SELECT lease_until FROM automation_run').scalar_one())


if __name__ == '__main__':
    unittest.main()

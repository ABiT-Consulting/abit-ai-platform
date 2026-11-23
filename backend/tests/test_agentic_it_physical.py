"""Real owned temporary file mutations/subprocesses; no package/service/storage change."""

import asyncio
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_agentic_it_changes import changes

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'
spec = importlib.util.spec_from_file_location('physical_it_fixture_module', ROOT / 'agentic_it_physical.py')
physical = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'physical_it_fixture_module': physical, 'open_webui.utils.agentic_it_changes': changes}):
    spec.loader.exec_module(physical)


class PhysicalExecution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'actual-host-mutation.txt'
        self.command = (sys.executable, '-c', 'from pathlib import Path;import sys;p=Path(sys.argv[1]);p.open("ab").write(b"one physical action\\n")', str(self.output))
        self.handler = physical.PhysicalHandler('restart_service', 'owned-canary', 'PROJ-fixture',
            'https://github.com/ABiT-Consulting/abit-ai-platform', 'test', socket.gethostname(), os.geteuid(),
            lambda plan: [self.command], lambda plan: self.output.read_bytes() == b'one physical action\n',
            physical_identity='owned-file-fixture')
        self.executor = physical.DurablePhysicalExecutor(self.root / 'state', [self.handler], run_lookup=lambda invocation: 'run-fixture-001')
        self.plan = {'schema_version': 1, 'operation': 'restart_service', 'resource_id': 'owned-canary',
            'scope': {'project_id': self.handler.project_id, 'repository': self.handler.repository, 'environment': 'test'},
            'host_id': socket.gethostname(), 'policy_digest': '1' * 64, 'target': {}, 'baseline': {},
            'fixed_argv': list(self.command), 'safety': {}, 'state': 'prepared', 'executable': True}
        self.plan['plan_digest'] = changes.plan_authority_digest(self.plan)

    def test_intermarket_worker_requires_exact_scope_and_reviewed_source(self):
        unit = 'intermarket-sbodesk.service'
        self.assertEqual(changes.restart_argv('PROJ-0196', 'abit-dev-linux', unit),
                         list(changes.INTERMARKET_ARGV))
        with self.assertRaises(ValueError):
            changes.restart_argv('PROJ-other', 'abit-dev-linux', unit)
        self.assertEqual(changes.restart_argv('PROJ-other', 'fixture-host', unit),
                         ['/usr/bin/systemctl', 'restart', '--', unit])
        import hashlib
        worker = ROOT.parents[2] / 'deployment/linux/intermarket-original-job/restart_job.py'
        self.assertEqual(hashlib.sha256(worker.read_bytes()).hexdigest(), changes.INTERMARKET_WORKER_SHA)

    async def test_release_parent_holds_without_host_observer_or_fresh_phase_authority(self):
        from types import ModuleType
        from unittest.mock import AsyncMock
        release_spec = importlib.util.spec_from_file_location('release_fixture', ROOT / 'agentic_intermarket_release.py')
        release = importlib.util.module_from_spec(release_spec)
        release_spec.loader.exec_module(release)
        utils = ModuleType('open_webui.utils')
        utils.__path__ = [str(ROOT)]
        utils.agentic_intermarket_release = release
        with patch.dict(sys.modules, {'open_webui.utils': utils}), patch.object(
                physical.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as dispatch:
            with self.assertRaises(physical.ChangeOutcomeUncertain):
                await self.executor._run_parent({'resource_id':'intermarket-pr237','plan_digest': 'a' * 64}, 'original-action', {'receipt_id': 'original'})
            dispatch.assert_not_awaited()
        with self.executor._db(False) as db:
            event = db.execute("SELECT * FROM original_operation_event WHERE action_id=?", ('original-action',)).fetchone()
            self.assertIn('AUTHENTICATED_HOST_HEALTH_OBSERVER_UNAVAILABLE', tuple(event)[3])
            self.assertIn('ORIGINAL_PHASE_AUTHORITY_UNAVAILABLE', tuple(event)[3])

    def test_unprivileged_worker_refuses_before_connecting(self):
        if os.geteuid() == 0:
            self.skipTest('Requires an ordinary OS identity')
        worker = ROOT.parents[2] / 'deployment/linux/intermarket-original-job/restart_job.py'
        result = subprocess.run([sys.executable, '-I', '-B', str(worker)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'fixed host worker required', result.stderr)

    async def test_second_executor_initialization_retains_existing_journal_lock(self):
        with self.executor._db() as original:
            original.execute('BEGIN IMMEDIATE')
            physical.DurablePhysicalExecutor(
                self.root / 'state', [self.handler], run_lookup=lambda invocation: 'run-fixture-001'
            )
            child = subprocess.run(
                [sys.executable, '-I', '-S', '-c',
                 "import sqlite3,sys;c=sqlite3.connect(sys.argv[1],timeout=0.05);"
                 "\ntry:c.execute('BEGIN IMMEDIATE');print('admitted')"
                 "\nexcept sqlite3.OperationalError:print('locked')"
                 "\nfinally:c.close()", str(self.executor.database)],
                capture_output=True, text=True, check=True, timeout=10,
            )
            self.assertEqual(child.stdout.strip(), 'locked')

    async def run_action(self, action='action-fixture', invocation='invocation-fixture', plan=None):
        return await self.executor.execute_once(plan or self.plan, action, invocation, self.command)

    async def test_actual_subprocess_mutates_once_then_original_receipt_is_reused(self):
        first = await self.run_action()
        second = await self.run_action()
        self.assertEqual(first, second)
        self.assertEqual('completed', first['state'])
        self.assertEqual(b'one physical action\n', self.output.read_bytes())
        self.assertEqual(first, await self.executor.observe_original('action-fixture', 'invocation-fixture', self.plan['plan_digest']))
        with self.executor._db(False) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM original_operation').fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM original_operation_event WHERE kind='fixed_step_returned'").fetchone()[0])

    async def test_lost_ack_after_real_mutation_never_dispatches_original_again(self):
        original = self.executor._command
        async def lose_ack(*args):
            await original(*args)
            raise ConnectionResetError('synthetic response loss after actual physical write')
        with patch.object(self.executor, '_command', side_effect=lose_ack):
            with self.assertRaises(ConnectionResetError):
                await self.run_action()
        receipt = await self.executor.observe_original('action-fixture', 'invocation-fixture', self.plan['plan_digest'])
        self.assertEqual('uncertain', receipt['state'])
        with self.assertRaises(changes.ChangeOutcomeUncertain):
            await self.run_action()
        self.assertEqual(b'one physical action\n', self.output.read_bytes())

    async def test_concurrent_same_original_requests_allow_one_subprocess(self):
        replies = await asyncio.gather(self.run_action(), self.run_action(), return_exceptions=True)
        self.assertEqual(1, len([item for item in replies if isinstance(item, dict) and item.get('state') == 'completed']))
        self.assertEqual(1, len([item for item in replies if isinstance(item, changes.ChangeOutcomeUncertain)]))
        self.assertEqual(b'one physical action\n', self.output.read_bytes())

    async def test_uncertain_original_fences_same_resource_against_different_action(self):
        with patch.object(self.executor, '_command', side_effect=ConnectionResetError('synthetic unavailable dispatch')):
            with self.assertRaises(ConnectionResetError):
                await self.run_action()
        with self.assertRaisesRegex(changes.ChangeOutcomeUncertain, 'resource has an unsettled'):
            await self.run_action('different-action', 'different-invocation')
        self.assertFalse(self.output.exists())

    async def test_changed_scope_or_argv_is_denied_before_host_or_intent(self):
        plan = deepcopy(self.plan)
        plan['scope']['project_id'] = 'OTHER'
        plan['plan_digest'] = changes.plan_authority_digest(plan)
        with self.assertRaisesRegex(ValueError, 'scope differs'):
            await self.run_action(plan=plan)
        with self.assertRaisesRegex(ValueError, 'command differs'):
            await self.executor.execute_once(self.plan, 'a', 'i', ('/bin/false',))
        with self.executor._db(False) as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM original_operation').fetchone()[0])
        self.assertFalse(self.output.exists())

    async def test_unverified_postcondition_keeps_original_uncertain_after_real_command(self):
        handler = physical.PhysicalHandler('restart_service', 'owned-canary', self.handler.project_id,
            self.handler.repository, 'test', socket.gethostname(), os.geteuid(), lambda plan: [self.command], lambda plan: False,
            physical_identity='owned-file-fixture')
        self.executor = physical.DurablePhysicalExecutor(self.root / 'state2', [handler], run_lookup=lambda invocation: 'run-fixture-001')
        with self.assertRaisesRegex(changes.ChangeOutcomeUncertain, 'postcondition'):
            await self.run_action()
        self.assertEqual(b'one physical action\n', self.output.read_bytes())
        with self.assertRaises(changes.ChangeOutcomeUncertain):
            await self.run_action()
        self.assertEqual(b'one physical action\n', self.output.read_bytes())

    def test_serialized_handler_and_unsafe_state_permissions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'typed physical handlers'):
            physical.DurablePhysicalExecutor(self.root / 'untrusted', [{'command': 'must-never-run'}], run_lookup=lambda invocation: 'run-fixture-001')
        unsafe = self.root / 'unsafe'
        unsafe.mkdir(mode=0o755)
        unsafe.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'owner/mode'):
            physical.DurablePhysicalExecutor(unsafe, [self.handler], run_lookup=lambda invocation: 'run-fixture-001')

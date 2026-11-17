"""Action gateway tests: fake ERP and temp-file operations only; no host mutation."""

from __future__ import annotations
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load('gateway_isolated', ROOT / 'agentic_tool_gateway.py')
contracts = load('gateway_contracts', ROOT / 'agentic_contracts/validator.py')


@contextmanager
def dbconnect(path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        yield db
    finally:
        db.close()


class Bridge:
    def __init__(self, packet):
        self.erp = self
        self.secret = 'test-fixture-only'
        self.value = packet
        self.approval = None
        self.calls = []
        self.consume_lost = False

    async def packet(self, *args, **kwargs):
        return deepcopy(self.value)

    async def request(self, method, path, json=None, headers=None):
        self.calls.append(path.rsplit('.', 1)[1])
        if path.endswith('get_action_approval'):
            return {'message': deepcopy(self.approval)}
        if path.endswith('consume_action_approval'):
            if self.consume_lost:
                raise TimeoutError('ERP consumption ack lost')
            return {
                'message': {
                    'consumed': True,
                    'approval_id': self.approval['approval_id'],
                    'action_id': json['action']['action_id'],
                    'action_fingerprint': json['action_fingerprint'],
                    'receipt_id': 'consumption-fixture-001',
                }
            }
        raise AssertionError(path)


class Gateway(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'state.sqlite'
        self.project = 'PROJ-0197'
        self.task = 'TASK-action-fixture'
        self.inv = 'invocation-fixture-001'
        self.binding = {
            'project_id': self.project,
            'role': 'it_manager',
            'agent_id': contracts.agent_id(self.project, 'it_manager'),
            'erp_user': 'ai-operations@abitcons.com',
        }
        self.packet = {
            'schema_version': 1,
            'project_id': self.project,
            'task_id': self.task,
            'task_revision': 'revision-fixture-1',
            'binding': self.binding,
            'srs': {'revision': 'fixture-srs-1', 'sha256': '1' * 64},
            'repository': 'https://github.com/ABiT-Consulting/abit-ai-platform',
            'environment': 'development',
            'tool_scopes': ['inspect_infrastructure', 'execute_infrastructure'],
            'instructions': 'Use approved fixtures only',
            'activity': [],
            'requirement_ids': ['REQ-IT-01'],
            'acceptance_tests': [{'id': 'TEST-IT-01'}],
            'dependencies': [],
        }
        self.profile = {
            'host_id': socket.gethostname(),
            'roots': {'test': str(self.root)},
            'services': [],
            'operations': ['inspect', 'cleanup_disposable', 'restart_service', 'install_official_package'],
            'disposable_roots': [str(self.root / 'disposable')],
            'quarantine_root': str(self.root / 'quarantine'),
            'expected_uid': os.getuid(),
            'environment_class': 'development',
            'resources': {},
        }
        (self.root / 'disposable').mkdir(mode=0o700)
        (self.root / 'quarantine').mkdir(mode=0o700)
        self.profile['change_preflight'] = {
            'host_id': socket.gethostname(),
            'checked_at': int(time.time()),
            'backup_ref': 'fixture-original-bytes-quarantine',
            'restore_plan': 'Restore retained original inode from private quarantine',
            'service_impact': 'No services use these fixture files',
            'retention_policy_verified': True,
        }
        self.config = {'agentic_tool_gateway': {'projects': {self.project: self.profile}}}
        self.enabled = True
        self.bridge = Bridge(self.packet)
        self.executions = 0
        self.patches = patch.dict(
            sys.modules,
            {
                'open_webui.utils.agentic_governance': types.SimpleNamespace(connect=dbconnect),
                'open_webui.utils.agentic_contracts.validator': contracts,
            },
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)
        with dbconnect(self.path) as db:
            db.executescript(
                'CREATE TABLE governance_invocation(invocation_id TEXT PRIMARY KEY,erp_project TEXT,erp_task TEXT,target_role TEXT,agent_id TEXT,state TEXT,lease_token TEXT,automation_run_id TEXT,lease_expires_at INTEGER,lease_fence INTEGER,work_packet TEXT); CREATE TABLE governance_task_lease(erp_project TEXT,erp_task TEXT,invocation_id TEXT,fence INTEGER); CREATE TABLE governance_project_control(erp_project TEXT,state TEXT); CREATE TABLE governance_task_control(project TEXT,task TEXT,state TEXT);'
            )
            db.execute(
                'INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    self.inv,
                    self.project,
                    self.task,
                    'it_manager',
                    self.binding['agent_id'],
                    'running',
                    'private-fixture-token',
                    'run-fixture-001',
                    int(time.time()) + 300,
                    1,
                    json.dumps(self.packet),
                ),
            )
            db.execute('INSERT INTO governance_task_lease VALUES(?,?,?,?)', (self.project, self.task, self.inv, 1))
            db.execute('INSERT INTO governance_project_control VALUES(?,?)', (self.project, 'enabled'))

    async def gate(self, project, config):
        return {}, {
            'enabled': self.enabled,
            'pm_task_automation_enabled': True,
            'assigned_user': 'ai-projects@abitcons.com',
            'project_status': 'Open',
        }

    def action(self, operation='inspect', risk='routine'):
        return {
            'schema_version': 1,
            'action_id': 'action-fixture-001',
            'project_id': self.project,
            'task_id': self.task,
            'task_revision': self.packet['task_revision'],
            'actor': deepcopy(self.binding),
            'repository': self.packet['repository'],
            'environment': self.packet['environment'],
            'operation': operation,
            'risk': risk,
            'parameters': {},
            'requested_at': int(time.time()),
        }

    def task_action(self, action):
        self.packet['tool_action'] = {key: deepcopy(action[key]) for key in ('operation', 'risk', 'parameters')}
        self.packet['tool_action']['request_key'] = 'fixture-request-001'
        with dbconnect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?', (json.dumps(self.packet),))

    def approve(self, action):
        now = int(time.time())
        self.bridge.approval = {
            'schema_version': 1,
            'approval_id': 'approval-fixture-001',
            'project_id': self.project,
            'task_id': self.task,
            'action_fingerprint': contracts.action_fingerprint(action),
            'approved_by': 'manager@example.invalid',
            'approved_at': now - 1,
            'expires_at': now + 100,
            'status': 'approved',
            'used_by_action_id': None,
        }

    def adapter(self, *, fail=False, preflight=None, execute=None):
        async def before(*args):
            return {'host_id': socket.gethostname(), 'checked_at': int(time.time())}

        async def run(*args):
            self.executions += 1
            if fail:
                raise TimeoutError('Original adapter outcome unknown')
            return {'fixture_executed': True}

        return module.TrustedAdapter(preflight or before, execute or run, capability='execute_infrastructure')

    async def call(self, action, adapters=None):
        return await module.execute_action(
            self.inv,
            action,
            project_gate=self.gate,
            config=self.config,
            bridge=self.bridge,
            adapters=adapters,
            db_path=self.path,
        )

    def disposable(self, key='expired', name='expired.log'):
        file = self.root / 'disposable' / name
        file.write_bytes(b'fixture disposable bytes')
        file.chmod(0o600)
        info = file.stat()
        self.profile['resources'][key] = {
            'path': str(file),
            'classification': 'disposable_log',
            'retention_until': int(time.time()) - 1,
            'device': info.st_dev,
            'inode': info.st_ino,
            'uid': info.st_uid,
            'size': info.st_size,
            'mtime_ns': info.st_mtime_ns,
        }
        return file

    async def test_fixed_health_checks_require_positive_health_body_not_only_http_200(self):
        import httpx

        original = httpx.AsyncClient
        seen = []
        for body, expected in [
            ({'status': False}, False),
            ({'status': True}, True),
            ({'ready': True}, True),
            ({'status': 'error'}, False),
        ]:

            def handle(request):
                seen.append(str(request.url))
                return httpx.Response(200, json=body)

            transport = httpx.MockTransport(handle)
            with patch.object(
                httpx, 'AsyncClient', side_effect=lambda **kwargs: original(transport=transport, **kwargs)
            ):
                self.assertEqual((await module._health(['abit-ai']))['abit-ai']['healthy'], expected)
        self.assertTrue(all(url == 'http://127.0.0.1:8888/health' for url in seen))
        with self.assertRaises(ValueError):
            await module._health(['https://example.invalid'])

    async def test_fixed_readonly_inspect_has_real_capacity_receipt(self):
        action = self.action()
        receipt = await self.call(action)
        self.assertEqual(receipt['state'], 'completed')
        self.assertTrue(receipt['result']['read_only'])
        self.assertGreater(receipt['result']['roots']['test']['total_bytes'], 0)
        action['requested_at'] += 1
        self.assertEqual((await self.call(action))['result'], receipt['result'])

    async def test_inspection_cannot_name_commands_urls_or_arbitrary_roots(self):
        for params in (
            {'command': 'not-executed'},
            {'url': 'http://example.invalid'},
            {'root_keys': ['other']},
            {'services': ['unknown']},
            {'root_keys': [{}]},
        ):
            value = self.action()
            value['parameters'] = params
            with self.assertRaises(ValueError):
                await self.call(value)
        self.assertIsNone(module.get_action(self.inv, 'action-fixture-001', self.path))

    async def test_default_off_pm_checkbox_cannot_be_bypassed(self):
        for flag in (None, False):

            async def denied(*args):
                gate = {'enabled': True, 'assigned_user': 'ai-projects@abitcons.com', 'project_status': 'Open'}
                if flag is not None:
                    gate['pm_task_automation_enabled'] = flag
                return {}, gate

            with self.assertRaisesRegex(ValueError, 'Project no longer authorizes'):
                await module.execute_action(
                    self.inv,
                    self.action(),
                    project_gate=denied,
                    config=self.config,
                    bridge=self.bridge,
                    db_path=self.path,
                )

    async def test_local_task_and_project_pause_blocks_action_boundary(self):
        with dbconnect(self.path) as db:
            db.execute('INSERT INTO governance_task_control VALUES(?,?,?)', (self.project, self.task, 'paused'))
        with self.assertRaisesRegex(ValueError, 'Task infrastructure'):
            await self.call(self.action())
        with dbconnect(self.path) as db:
            db.execute('DELETE FROM governance_task_control')
            db.execute("UPDATE governance_project_control SET state='paused'")
        with self.assertRaisesRegex(ValueError, 'Project infrastructure'):
            await self.call(self.action())

    async def test_active_invocation_and_lease_are_required(self):
        with dbconnect(self.path) as db:
            db.execute("UPDATE governance_invocation SET state='cancel_requested'")
        with self.assertRaises(ValueError):
            await self.call(self.action())

    async def test_scope_and_current_workpacket_drift_are_denied(self):
        value = self.action()
        value['task_revision'] = 'stale'
        with self.assertRaises(ValueError):
            await self.call(value)
        self.bridge.value = deepcopy(self.packet)
        self.bridge.value['repository'] = 'https://github.com/ABiT-Consulting/other'
        with self.assertRaises(ValueError):
            await self.call(self.action())

    async def test_held_project_host_or_missing_capability_denied(self):
        self.enabled = False
        with self.assertRaises(ValueError):
            await self.call(self.action())
        self.enabled = True
        self.profile['host_id'] = 'other-host'
        with self.assertRaises(ValueError):
            await self.call(self.action())
        self.profile['host_id'] = socket.gethostname()
        self.profile['operations'] = []
        with self.assertRaises(module.CapabilityUnavailable):
            await self.call(self.action())

    async def test_unsupported_install_is_not_pretended_complete(self):
        value = self.action('install_official_package')
        self.task_action(value)
        with self.assertRaises(module.CapabilityUnavailable):
            await self.call(value)
        self.assertEqual(self.executions, 0)

    async def test_sensitive_action_requires_explicit_erp_approval_before_intent(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        with self.assertRaises(module.ApprovalRequired):
            await self.call(value, {'restart_service': self.adapter()})
        self.assertIsNone(module.get_action(self.inv, value['action_id'], self.path))
        self.assertEqual(self.executions, 0)

    async def test_approved_registered_action_consumes_once(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        self.approve(value)
        first = await self.call(value, {'restart_service': self.adapter()})
        value['requested_at'] += 1
        second = await self.call(value, {'restart_service': self.adapter()})
        self.assertEqual(first['result'], second['result'])
        self.assertEqual(self.executions, 1)
        self.assertEqual(self.bridge.calls.count('consume_action_approval'), 1)

    async def test_expired_revoked_self_approval_and_replay_are_denied(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        for key, bad in [
            ('expires_at', int(time.time()) - 1),
            ('status', 'revoked'),
            ('approved_by', 'ai-operations@abitcons.com'),
            ('used_by_action_id', 'previous-fixture'),
        ]:
            self.approve(value)
            self.bridge.approval[key] = bad
            with self.assertRaises(ValueError):
                await self.call(value, {'restart_service': self.adapter()})
        self.assertEqual(self.executions, 0)

    async def test_lost_approval_ack_never_runs_or_consumes_again(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        self.approve(value)
        self.bridge.consume_lost = True
        with self.assertRaises(TimeoutError):
            await self.call(value, {'restart_service': self.adapter()})
        receipt = await self.call(value, {'restart_service': self.adapter()})
        self.assertEqual(receipt['state'], 'uncertain')
        self.assertEqual(self.executions, 0)
        self.assertEqual(self.bridge.calls.count('consume_action_approval'), 1)

    async def test_lost_adapter_receipt_retains_original_uncertainty(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        self.approve(value)
        with self.assertRaises(TimeoutError):
            await self.call(value, {'restart_service': self.adapter(fail=True)})
        receipt = await self.call(value, {'restart_service': self.adapter()})
        self.assertEqual(receipt['state'], 'uncertain')
        self.assertEqual(self.executions, 1)

    async def test_changed_action_content_cannot_reuse_original_id(self):
        value = self.action()
        await self.call(value)
        value['parameters']['root_keys'] = []
        with self.assertRaises(ValueError):
            await self.call(value)

    async def test_mutating_action_must_match_immutable_task_request(self):
        value = self.action('restart_service', 'privileged')
        self.approve(value)
        with self.assertRaises(ValueError):
            await self.call(value, {'restart_service': self.adapter()})

    async def test_revocation_during_preflight_prevents_execution(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        self.approve(value)

        async def revoke(*args):
            self.enabled = False
            return {}

        with self.assertRaises(ValueError):
            await self.call(value, {'restart_service': self.adapter(preflight=revoke)})
        self.assertEqual(self.executions, 0)

    async def test_concurrent_same_action_runs_one_adapter(self):
        value = self.action('restart_service', 'privileged')
        self.task_action(value)
        self.approve(value)
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def run(*args):
            self.executions += 1
            entered.set()
            await resume.wait()
            return {'fixture': True}

        registry = {'restart_service': self.adapter(execute=run)}
        pending = asyncio.create_task(self.call(value, registry))
        await entered.wait()
        self.assertEqual((await self.call(value, registry))['state'], 'uncertain')
        resume.set()
        self.assertEqual((await pending)['state'], 'completed')
        self.assertEqual(self.executions, 1)

    async def test_cleanup_atomic_quarantine_preserves_bytes_and_repeat_receipt(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        registry = {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: False)}
        receipt = await self.call(value, registry)
        self.assertFalse(file.exists())
        self.assertFalse(receipt['result']['deleted'])
        saved = Path(receipt['result']['resources'][0]['quarantine_path'])
        self.assertEqual(saved.read_bytes(), b'fixture disposable bytes')
        self.assertEqual((await self.call(value, registry))['result'], receipt['result'])
        self.assertEqual(len(list((self.root / 'quarantine').iterdir())), 1)

    async def test_cleanup_requires_fresh_backup_recovery_retention_attestation(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        self.profile.pop('change_preflight')
        with self.assertRaisesRegex(ValueError, 'backup/retention'):
            await self.call(value, {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: False)})
        self.assertTrue(file.exists())

    async def test_active_writer_is_never_quarantined(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        with self.assertRaises(ValueError):
            await self.call(value, {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: True)})
        self.assertTrue(file.exists())
        self.assertFalse(list((self.root / 'quarantine').iterdir()))

    async def test_retention_protected_class_and_path_are_denied(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        registry = {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: False)}
        original = deepcopy(self.profile['resources']['expired'])
        for key, bad in [
            ('retention_until', int(time.time()) + 100),
            ('classification', 'audit_log'),
            ('path', str(self.root / 'disposable' / 'application.db-wal')),
        ]:
            self.profile['resources']['expired'] = {**original, key: bad}
            with self.assertRaises(ValueError):
                await self.call(value, registry)
        self.assertTrue(file.exists())

    async def test_changed_inode_owner_device_or_permissions_denied(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        registry = {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: False)}
        original = deepcopy(self.profile['resources']['expired'])
        for key, bad in [
            ('inode', original['inode'] + 1),
            ('device', original['device'] + 1),
            ('uid', original['uid'] + 1),
        ]:
            self.profile['resources']['expired'] = {**original, key: bad}
            with self.assertRaises(ValueError):
                await self.call(value, registry)
        self.profile['resources']['expired'] = original
        file.chmod(0o660)
        with self.assertRaises(ValueError):
            await self.call(value, registry)
        self.assertTrue(file.exists())

    async def test_symlinks_hardlinks_and_unknown_resources_are_denied(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['unknown']}
        self.task_action(value)
        registry = {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: False)}
        with self.assertRaises(ValueError):
            await self.call(value, registry)
        value['parameters']['resource_ids'] = ['expired']
        self.task_action(value)
        alias = file.with_name('hardlink.log')
        os.link(file, alias)
        with self.assertRaises(ValueError):
            await self.call(value, registry)
        alias.unlink()
        backup = file.with_name('original-copy.log')
        file.rename(backup)
        file.symlink_to(backup)
        with self.assertRaises((OSError, ValueError)):
            await self.call(value, registry)
        self.assertEqual(backup.read_bytes(), b'fixture disposable bytes')

    async def test_writer_detected_after_rename_is_rolled_back_without_data_loss(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        count = 0

        def late_writer(*args):
            nonlocal count
            count += 1
            return count == 4

        with self.assertRaisesRegex(ValueError, 'Writer became active'):
            await self.call(value, {'cleanup_disposable': module.cleanup_adapter(writer_probe=late_writer)})
        self.assertEqual(file.read_bytes(), b'fixture disposable bytes')
        receipt = module.get_action(self.inv, value['action_id'], self.path)
        self.assertEqual(receipt['state'], 'uncertain')
        self.assertEqual(receipt['result']['partial_quarantine']['state'], 'rolled_back_active_writer')
        self.assertTrue(receipt['result']['partial_quarantine']['moved'][0]['restored'])

    async def test_production_risk_cannot_be_downgraded_to_routine(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        self.profile['environment_class'] = 'production'
        with self.assertRaisesRegex(ValueError, 'Production changes'):
            await self.call(value, {'cleanup_disposable': module.cleanup_adapter(writer_probe=lambda *_: False)})
        self.assertTrue(file.exists())

    async def test_invalid_sensitive_action_does_not_request_approval(self):
        value = self.action('restart_service', 'privileged')
        value['parameters'] = {'token': 'fixture-sensitive-input'}
        self.task_action(value)
        with self.assertRaisesRegex(ValueError, 'credentials'):
            await self.call(value, {'restart_service': self.adapter()})
        self.assertFalse(self.bridge.calls)
        self.assertEqual(self.executions, 0)

    async def test_last_boundary_file_swap_does_not_move_replacement(self):
        file = self.disposable()
        value = self.action('cleanup_disposable')
        value['parameters'] = {'resource_ids': ['expired']}
        self.task_action(value)
        probes = 0

        def swap(*args):
            nonlocal probes
            probes += 1
            if probes == 3:
                replacement = file.with_name('replacement.log')
                replacement.write_bytes(b'new protected replacement')
                replacement.chmod(0o600)
                file.rename(file.with_name('original.log'))
                replacement.rename(file)
            return False

        with self.assertRaises(ValueError):
            await self.call(value, {'cleanup_disposable': module.cleanup_adapter(writer_probe=swap)})
        self.assertEqual(file.read_bytes(), b'new protected replacement')
        receipt = module.get_action(self.inv, value['action_id'], self.path)
        self.assertEqual(receipt['state'], 'uncertain')
        self.assertEqual(receipt['result']['partial_quarantine']['moved'], [])


if __name__ == '__main__':
    unittest.main()

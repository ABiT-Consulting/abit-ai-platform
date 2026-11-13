"""Pinned IT preparation with synthetic readers/executor; never changes the host."""

import asyncio
from copy import copy, deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_agentic_tool_gateway as gateway_tests

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'
spec = importlib.util.spec_from_file_location('isolated_it_changes', ROOT / 'agentic_it_changes.py')
changes = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'open_webui.utils.agentic_tool_gateway': gateway_tests.module}):
    spec.loader.exec_module(changes)


class Preparation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = int(time.time())
        self.signature_expires = self.now + 3600
        self.host = socket.gethostname()
        self.artifact = self.root / 'official-fixture.deb'
        self.artifact.write_bytes(b'not-an-executable-package; offline fixture only')
        self.artifact.chmod(0o600)
        self.unit = self.root / 'fixture.service'
        self.unit.write_bytes(b'[Service]\nExecStart=/bin/false\n')
        self.unit.chmod(0o600)
        self.binding = {
            'project_id': 'PROJ-0197',
            'role': 'it_manager',
            'agent_id': gateway_tests.contracts.agent_id('PROJ-0197', 'it_manager'),
            'erp_user': 'ai-operations@abitcons.com',
        }
        self.scope = {
            'project_id': 'PROJ-0197',
            'task_id': 'TASK-action-fixture',
            'task_revision': 'revision-fixture-1',
            'binding': deepcopy(self.binding),
            'repository': 'https://github.com/ABiT-Consulting/abit-ai-platform',
            'environment': 'development',
            'invocation_id': 'invocation-fixture-001',
        }
        self.profile = {
            'host_id': self.host,
            'expected_uid': os.getuid(),
            'environment_class': 'development',
            'package_cache_roots': [str(self.root)],
            'unit_roots': [str(self.root)],
            'change_policy': {'revision': 'operator-policy-1'},
            'operations': ['install_official_package', 'restart_service', 'extend_capacity'],
            'official_sources': {
                'fixture-vendor': {
                    'origin': 'https://vendor.example.invalid/packages',
                    'signer_fingerprint': 'A' * 40,
                    'catalog_sha256': 'c' * 64,
                }
            },
            'change_resources': {},
        }
        common = {
            'enabled': True,
            'project_id': self.scope['project_id'],
            'host_id': self.host,
            'repository': self.scope['repository'],
            'environment': self.scope['environment'],
            'exclusive_to_project': True,
            'minimum_free_bytes': 4096,
        }
        self.profile['change_resources'] = {
            'install_official_package': {
                'fixture-package': {
                    **common,
                    'package': 'fixture-package',
                    'version': '2.0-1',
                    'architecture': 'amd64',
                    'artifact_id': 'fixture-deb',
                    'artifact_path': str(self.artifact),
                    'artifact_sha256': hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
                    'official_source_id': 'fixture-vendor',
                    'dependencies': [],
                }
            },
            'restart_service': {
                'fixture-service': {
                    **common,
                    'unit': 'fixture.service',
                    'unit_path': str(self.unit),
                    'unit_sha256': hashlib.sha256(self.unit.read_bytes()).hexdigest(),
                }
            },
            'extend_capacity': {
                'fixture-volume': {
                    **common,
                    'volume_id': 'fixture-volume',
                    'mountpoint': str(self.root),
                    'filesystem': 'ext4',
                    'target_bytes': 1_000_000,
                }
            },
        }
        self.safety = {
            'host_id': self.host,
            'checked_at': self.now,
            'project_id': self.scope['project_id'],
            'environment': self.scope['environment'],
            'backup': {
                'verified': True,
                'reference': 'backup-fixture-1',
                'sha256': 'b' * 64,
                'expires_at': self.now + 3600,
            },
            'restore': {
                'verified': True,
                'reference': 'restore-fixture-1',
                'sha256': 'a' * 64,
                'expires_at': self.now + 3600,
            },
            'retention': {'verified': True, 'policy_sha256': 'd' * 64, 'expires_at': self.now + 3600},
            'capacity': {'filesystem_id': 'fixture-filesystem', 'free_bytes': 8192, 'total_bytes': 65536},
            'impact': {'reviewed': True, 'exclusive_to_project': True, 'sha256': 'e' * 64},
            'privilege': {'authorized': True, 'effective_uid': os.getuid()},
        }
        self.installed = '1.0-1'
        self.service_state = {
            'unit': 'fixture.service',
            'unit_sha256': hashlib.sha256(self.unit.read_bytes()).hexdigest(),
            'active_state': 'active',
            'sub_state': 'running',
            'main_pid': 111,
            'invocation_id': 'service-original-001',
            'healthy': True,
        }
        self.execution_calls = []
        self.receipts = {}
        self.lost_ack = False
        self.bad_receipt = False
        self.apply_postcondition = True
        self.grant_changes = {}
        self.readers = changes.ChangeReaders(
            safety=lambda *args: deepcopy(self.safety),
            package=self.package,
            service=self.service,
            storage=self.storage,
            artifact=self.cached_artifact,
            verifier=changes.OfficialSignatureVerifier(self.signature),
        )
        self.executor = changes.TrustedChangeExecutor(self.execute_once, self.observe_original)
        self.patches = patch.dict(
            sys.modules, {'open_webui.utils.agentic_contracts.validator': gateway_tests.contracts}
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def action(self, operation='install_official_package'):
        return {
            'schema_version': 1,
            'action_id': 'action-fixture-001',
            **{key: self.scope[key] for key in ('project_id', 'task_id', 'task_revision', 'repository', 'environment')},
            'actor': deepcopy(self.binding),
            'operation': operation,
            'risk': 'privileged',
            'parameters': {
                'resource_id': {
                    'install_official_package': 'fixture-package',
                    'restart_service': 'fixture-service',
                    'extend_capacity': 'fixture-volume',
                }[operation]
            },
            'requested_at': self.now,
        }

    def package(self, record, profile):
        return {
            'host_id': self.host,
            'checked_at': self.now,
            'package': record['package'],
            'installed': self.installed is not None,
            'version': self.installed,
            'architecture': 'amd64',
        }

    def service(self, record, profile):
        return {'host_id': self.host, 'checked_at': self.now, **deepcopy(self.service_state)}

    def storage(self, record, profile):
        return {
            'host_id': self.host,
            'checked_at': self.now,
            'volume_id': 'fixture-volume',
            'filesystem_id': 'fixture-filesystem',
            'filesystem': 'ext4',
            'total_bytes': 65536,
            'free_bytes': 8192,
        }

    def cached_artifact(self, record, profile):
        return {
            'artifact_id': record['artifact_id'],
            **changes._read_protected(record['artifact_path'], profile['package_cache_roots'], profile['expected_uid']),
        }

    def signature(self, record, source, artifact, profile):
        return {
            'verified': True,
            **{
                key: record[key]
                for key in ('package', 'version', 'architecture', 'artifact_sha256', 'official_source_id')
            },
            'signer_fingerprint': source['signer_fingerprint'],
            'catalog_sha256': source['catalog_sha256'],
            'valid_until': self.signature_expires,
        }

    def grant(self, action, scope, plan):
        return {
            'consumed': True,
            'receipt_id': 'consumption-original-001',
            'action_id': action['action_id'],
            'action_fingerprint': gateway_tests.contracts.action_fingerprint(action),
            'invocation_id': scope['invocation_id'],
            **{key: action[key] for key in ('project_id', 'task_id', 'task_revision')},
            **self.grant_changes,
        }

    def execute_once(self, plan, action_id, invocation_id, argv):
        self.execution_calls.append((action_id, invocation_id, argv))
        if self.apply_postcondition:
            self.installed = plan['target'].get('version', self.installed)
            self.service_state.update(invocation_id='service-new-002', main_pid=222)
        receipt = {
            'state': 'completed',
            'action_id': action_id,
            'invocation_id': invocation_id,
            'plan_digest': plan['plan_digest'],
            'receipt_id': 'host-original-001',
            'finished_at': self.now,
            'stdout': 'secret fixture extra must not escape',
        }
        self.receipts[(action_id, invocation_id, plan['plan_digest'])] = receipt
        if self.lost_ack:
            raise TimeoutError('synthetic lost acknowledgement, no real host transport')
        if self.bad_receipt:
            return {**receipt, 'invocation_id': 'forged-other-invocation'}
        return deepcopy(receipt)

    def observe_original(self, action, invocation, plan_digest):
        return deepcopy(self.receipts.get((action, invocation, plan_digest)))

    async def plan(self, action=None, **kwargs):
        return await changes.prepare_change(
            action or self.action(),
            self.scope,
            kwargs.pop('profile', self.profile),
            readers=kwargs.pop('readers', self.readers),
            executor=kwargs.pop('executor', self.executor),
            grant_checker=kwargs.pop('grant_checker', self.grant),
            now=self.now,
            **kwargs,
        )

    async def bound(self, operation='install_official_package'):
        action = self.action(operation)
        plan = await self.plan(action)
        action['parameters']['plan_digest'] = plan['plan_digest']
        adapters = changes.change_adapters(
            readers=self.readers, executor=self.executor, grant_checker=self.grant, clock=lambda: self.now
        )
        return action, plan, adapters[operation]

    async def test_missing_executor_returns_usable_plan_artifact_without_claiming_execution(self):
        plan = await self.plan(executor=None, grant_checker=None)
        self.assertEqual(plan['state'], 'prepared_unavailable')
        self.assertEqual(plan['target']['version'], '2.0-1')
        self.assertEqual(plan['baseline']['version'], '1.0-1')
        self.assertEqual(
            {x['code'] for x in plan['prerequisites']},
            {'TRUSTED_EXECUTOR_UNAVAILABLE', 'CONSUMED_ERP_APPROVAL_READER_UNAVAILABLE'},
        )
        artifact = changes.plan_artifact(plan)
        self.assertEqual(json.loads(artifact['content'])['execution_state'], 'not_executed')
        self.assertEqual(artifact['sha256'], hashlib.sha256(artifact['content']).hexdigest())
        self.assertEqual(self.execution_calls, [])

    async def test_adapter_missing_digest_carries_plan_and_specific_prerequisite(self):
        adapter = changes.change_adapters(readers=self.readers, executor=self.executor, grant_checker=self.grant)[
            'install_official_package'
        ]
        with self.assertRaises(changes.PreparedChangeUnavailable) as denied:
            await adapter.preflight(self.action(), self.scope, self.profile)
        self.assertIn('EXACT_PLAN_DIGEST_REQUIRED', {x['code'] for x in denied.exception.plan['prerequisites']})
        self.assertEqual(
            json.loads(denied.exception.artifact['content'])['plan_digest'], denied.exception.plan['plan_digest']
        )
        self.assertFalse(denied.exception.plan['executable'])

    async def test_missing_resource_is_an_inspectable_unavailable_plan(self):
        action = self.action()
        action['parameters']['resource_id'] = 'not-configured'
        plan = await self.plan(action)
        self.assertEqual(plan['state'], 'prepared_unavailable')
        self.assertIn('SERVER_RESOURCE_PROFILE_UNAVAILABLE', {x['code'] for x in plan['prerequisites']})

    async def test_model_cannot_supply_commands_urls_paths_versions_or_signatures(self):
        for key in ('command', 'url', 'path', 'package', 'version', 'signature_verified', 'executor'):
            with self.subTest(key=key):
                action = self.action()
                action['parameters'][key] = 'model-input'
                with self.assertRaises(ValueError):
                    await self.plan(action)
        self.assertEqual(self.execution_calls, [])

    async def test_forged_verifier_boolean_or_serialized_assertion_is_denied(self):
        for verifier in (True, {'verified': True}, lambda *args: True):
            with self.assertRaises(ValueError):
                changes.ChangeReaders(verifier=verifier)
        for proof in (
            True,
            {'verified': True},
            {
                **self.signature(
                    self.profile['change_resources']['install_official_package']['fixture-package'],
                    self.profile['official_sources']['fixture-vendor'],
                    {},
                    self.profile,
                ),
                'artifact_sha256': 'f' * 64,
            },
        ):
            readers = copy(self.readers)
            readers.verifier = changes.OfficialSignatureVerifier(lambda *args, proof=proof: proof)
            plan = await self.plan(readers=readers)
            self.assertFalse(plan['executable'])
            self.assertIn('OFFICIAL_SIGNATURE_OR_CATALOG_INVALID', {x['code'] for x in plan['prerequisites']})

    async def test_profile_pin_drift_requires_new_plan_and_approval(self):
        action, before, adapter = await self.bound()
        mutations = [
            lambda p: p['change_policy'].update(revision='policy-2'),
            lambda p: p['change_resources']['install_official_package']['fixture-package'].update(version='3.0-1'),
            lambda p: p['official_sources']['fixture-vendor'].update(signer_fingerprint='B' * 40),
            lambda p: p['official_sources']['fixture-vendor'].update(catalog_sha256='f' * 64),
            lambda p: p.update(unit_roots=['/new-reviewed-root']),
        ]
        for mutate in mutations:
            profile = deepcopy(self.profile)
            mutate(profile)
            with self.subTest(profile=profile['change_policy']):
                with self.assertRaisesRegex(ValueError, 'pins or baseline changed'):
                    await adapter.execute(action, self.scope, before, profile)
        self.assertEqual(self.execution_calls, [])

    async def test_safeguard_and_baseline_drift_are_bound_to_exact_plan(self):
        action, before, adapter = await self.bound()
        old = deepcopy(self.safety)
        for section, key, value in [
            ('backup', 'reference', 'backup-replaced'),
            ('backup', 'sha256', '0' * 64),
            ('restore', 'sha256', '1' * 64),
            ('retention', 'policy_sha256', '2' * 64),
            ('impact', 'sha256', '3' * 64),
            ('capacity', 'free_bytes', 8191),
        ]:
            self.safety = deepcopy(old)
            self.safety[section][key] = value
            with self.subTest(section=section, key=key):
                with self.assertRaises(ValueError):
                    await adapter.execute(action, self.scope, before, self.profile)
        self.safety = old
        self.installed = '1.1-1'
        with self.assertRaises(ValueError):
            await adapter.execute(action, self.scope, before, self.profile)
        self.assertEqual(self.execution_calls, [])

    async def test_observation_clock_refresh_does_not_change_approved_pins(self):
        before = await self.plan()
        self.safety['checked_at'] += 1
        self.now += 1
        after = await self.plan()
        self.assertEqual(before['plan_digest'], after['plan_digest'])
        self.assertNotEqual(before['prepared_at'], after['prepared_at'])

    async def test_expired_backup_or_restore_and_stale_capacity_prevents_execution(self):
        original = deepcopy(self.safety)
        for section in ('backup', 'restore', 'retention'):
            self.safety = deepcopy(original)
            self.safety[section]['expires_at'] = self.now
            plan = await self.plan()
            self.assertIn(section.upper() + '_PROOF_UNAVAILABLE', {x['code'] for x in plan['prerequisites']})
            self.assertFalse(plan['executable'])
        self.safety = deepcopy(original)
        self.safety['checked_at'] -= 301
        self.assertFalse((await self.plan())['executable'])
        self.safety = deepcopy(original)
        self.safety['capacity']['free_bytes'] = 1
        self.assertIn('CAPACITY_PREFLIGHT_UNSATISFIED', {x['code'] for x in (await self.plan())['prerequisites']})
        self.assertEqual(self.execution_calls, [])

    async def test_host_and_project_ownership_fail_closed(self):
        for key, value in [('host_id', 'another-host'), ('project_id', 'PROJ-other'), ('exclusive_to_project', False)]:
            profile = deepcopy(self.profile)
            profile['change_resources']['install_official_package']['fixture-package'][key] = value
            with self.assertRaises(ValueError):
                await self.plan(profile=profile)
        self.assertEqual(self.execution_calls, [])

    async def test_reader_failure_is_a_sanitized_prepared_unavailable_artifact(self):
        def failed(*args):
            raise PermissionError('credential=must-not-escape; /private/unrelated-path')

        readers = copy(self.readers)
        readers.safety = failed
        readers.package = failed
        plan = await self.plan(readers=readers)
        self.assertFalse(plan['executable'])
        serialized = changes.plan_artifact(plan)['content'].decode()
        self.assertNotIn('must-not-escape', serialized)
        self.assertNotIn('/private/unrelated', serialized)
        self.assertIn('PACKAGE_READBACK_UNAVAILABLE', serialized)

    async def test_same_version_and_unsatisfied_dependencies_do_not_reinstall(self):
        self.installed = '2.0-1'
        plan = await self.plan()
        self.assertIn('TARGET_VERSION_ALREADY_PRESENT', {x['code'] for x in plan['prerequisites']})
        self.installed = '1.0-1'
        self.profile['change_resources']['install_official_package']['fixture-package']['dependencies'] = [
            {'package': 'other-package', 'version': '9.0-1', 'architecture': 'amd64'}
        ]
        plan = await self.plan()
        self.assertIn('PINNED_DEPENDENCY_UNSATISFIED', {x['code'] for x in plan['prerequisites']})
        self.assertEqual(self.execution_calls, [])

    async def test_storage_produces_exact_plan_without_claiming_resize(self):
        action = self.action('extend_capacity')
        plan = await self.plan(action)
        self.assertEqual(plan['target']['target_bytes'], 1_000_000)
        self.assertEqual(plan['baseline']['total_bytes'], 65536)
        self.assertEqual(plan['fixed_argv'], [])
        self.assertIn('TRUSTED_CAPACITY_CONNECTOR_UNAVAILABLE', {x['code'] for x in plan['prerequisites']})
        action['parameters']['plan_digest'] = plan['plan_digest']
        adapter = changes.change_adapters(readers=self.readers, executor=self.executor, grant_checker=self.grant)[
            'extend_capacity'
        ]
        with self.assertRaises(changes.PreparedChangeUnavailable):
            await adapter.preflight(action, self.scope, self.profile)
        self.assertEqual(self.execution_calls, [])

    async def test_no_execution_for_forged_or_wrong_invocation_consumption_receipt(self):
        action, before, adapter = await self.bound()
        for changeset in (
            {'consumed': False},
            {'invocation_id': 'another-run'},
            {'task_revision': 'old-revision'},
            {'action_fingerprint': '0' * 64},
        ):
            self.grant_changes = changeset
            with self.assertRaisesRegex(ValueError, 'consumption was not verified'):
                await adapter.execute(action, self.scope, before, self.profile)
        self.assertEqual(self.execution_calls, [])

    async def test_fixed_package_execution_requires_original_receipt_and_exact_readback(self):
        action, plan, adapter = await self.bound()
        before = await adapter.preflight(action, self.scope, self.profile)
        result = await adapter.execute(action, self.scope, before, self.profile)
        self.assertTrue(result['executed'])
        self.assertTrue(result['readback']['verified'])
        self.assertEqual(
            self.execution_calls,
            [
                (
                    action['action_id'],
                    self.scope['invocation_id'],
                    ('/usr/bin/dpkg', '--install', '--', str(self.artifact)),
                )
            ],
        )
        self.assertNotIn('secret fixture', json.dumps(result))
        self.assertNotIn('stdout', result['original_receipt'])

    async def test_service_requires_healthy_new_invocation_and_exact_unit_hash(self):
        action, before, adapter = await self.bound('restart_service')
        result = await adapter.execute(action, self.scope, before, self.profile)
        self.assertTrue(result['readback']['verified'])
        self.assertEqual(self.execution_calls[0][2], ('/usr/bin/systemctl', 'restart', '--', 'fixture.service'))
        self.service_state['invocation_id'] = None
        self.assertFalse(
            (await changes.verify_change_readback(before, readers=self.readers, profile=self.profile))['verified']
        )

    async def test_lost_ack_recovery_observes_original_without_replaying(self):
        action, before, adapter = await self.bound()
        self.lost_ack = True
        with self.assertRaises(changes.ChangeOutcomeUncertain):
            await adapter.execute(action, self.scope, before, self.profile)
        for _ in range(3):
            recovered = await adapter.recover(action, self.scope, before, self.profile)
            self.assertEqual(recovered['state'], 'uncertain')
            self.assertFalse(recovered['executed'])
            self.assertTrue(recovered['original_receipt_verified'])
            self.assertTrue(recovered['postcondition_verified'])
        self.assertEqual(len(self.execution_calls), 1)

    async def test_matching_current_state_without_original_receipt_is_never_attributed(self):
        action, before, adapter = await self.bound()
        self.installed = '2.0-1'
        recovered = await adapter.recover(action, self.scope, before, self.profile)
        self.assertTrue(recovered['postcondition_verified'])
        self.assertFalse(recovered['original_receipt_verified'])
        self.assertFalse(recovered['executed'])
        self.assertEqual(self.execution_calls, [])

    async def test_forged_host_ack_and_failed_postcondition_stay_uncertain(self):
        action, before, adapter = await self.bound()
        self.bad_receipt = True
        with self.assertRaises(changes.ChangeOutcomeUncertain):
            await adapter.execute(action, self.scope, before, self.profile)
        self.installed = '1.0-1'
        self.bad_receipt = False
        self.apply_postcondition = False
        action['action_id'] = 'action-fixture-002'
        with self.assertRaises(changes.ChangeOutcomeUncertain):
            await adapter.execute(action, self.scope, before, self.profile)
        self.assertEqual(len(self.execution_calls), 2)  # Two independent synthetic original actions.

    async def test_linux_readers_only_issue_fixed_metadata_commands_and_hash_pinned_bytes(self):
        seen = []

        def command(argv):
            seen.append(argv)
            if argv[0] == '/usr/bin/dpkg-query':
                return 0, 'fixture-package\t1.0-1\tamd64\tinstalled'
            return (
                0,
                'Id=fixture.service\nActiveState=active\nSubState=running\nMainPID=111\nInvocationID=original-service\nFragmentPath='
                + str(self.unit),
            )

        readers = changes.LinuxReadOnlyReaders(
            safety=lambda *args: deepcopy(self.safety),
            verifier=self.readers.verifier,
            health=lambda *args: True,
            command_reader=command,
        )
        plan = await self.plan(readers=readers)
        self.assertEqual(plan['artifact']['sha256'], hashlib.sha256(self.artifact.read_bytes()).hexdigest())
        self.assertEqual(seen[0][-2:], ('--', 'fixture-package'))
        self.assertNotIn('--install', seen[0])
        await self.plan(self.action('restart_service'), readers=readers)
        self.assertEqual(seen[1][0:3], ('/usr/bin/systemctl', 'show', '--no-pager'))
        self.assertTrue(all('restart' not in item for argv in seen for item in argv))

    async def test_symlink_writable_hardlinked_or_tampered_artifacts_are_unavailable(self):
        record = self.profile['change_resources']['install_official_package']['fixture-package']
        original = str(self.artifact)
        link = self.root / 'linked.deb'
        link.symlink_to(self.artifact)
        record['artifact_path'] = str(link)
        self.assertIn('PINNED_ARTIFACT_UNVERIFIED', {x['code'] for x in (await self.plan())['prerequisites']})
        record['artifact_path'] = original
        self.artifact.chmod(0o666)
        self.assertFalse((await self.plan())['executable'])
        self.artifact.chmod(0o600)
        hardlink = self.root / 'hardlink.deb'
        os.link(self.artifact, hardlink)
        self.assertFalse((await self.plan())['executable'])
        hardlink.unlink()
        self.artifact.write_bytes(b'changed protected bytes')
        self.assertFalse((await self.plan())['executable'])

    async def test_plan_and_readback_export_only_allowlisted_reader_fields(self):
        self.safety['backup']['secret'] = 'must-not-escape'
        self.service_state['stdout'] = 'must-not-escape'
        plan = await self.plan(self.action('restart_service'))
        self.assertNotIn('must-not-escape', changes.plan_artifact(plan)['content'].decode())
        observed = await changes.verify_change_readback(plan, readers=self.readers, profile=self.profile)
        self.assertNotIn('must-not-escape', json.dumps(observed))


class GatewayIntegration(unittest.IsolatedAsyncioTestCase):
    """Exercise the actual durable gateway once; do not inherit the pure cases."""

    setUp = Preparation.setUp
    action = Preparation.action
    package = Preparation.package
    service = Preparation.service
    storage = Preparation.storage
    cached_artifact = Preparation.cached_artifact
    signature = Preparation.signature
    execute_once = Preparation.execute_once
    observe_original = Preparation.observe_original
    grant = Preparation.grant
    plan = Preparation.plan

    async def fixture(self):
        gateway = gateway_tests.Gateway(methodName='runTest')
        gateway.setUp()
        self.addCleanup(gateway.doCleanups)
        self.addCleanup(gateway.tmp.cleanup)
        gateway.profile.update(deepcopy(self.profile))
        action = self.action()
        plan = await self.plan(action)
        action['parameters']['plan_digest'] = plan['plan_digest']
        gateway.task_action(action)
        gateway.approve(action)

        def consumed_original(action, scope, before):
            # Read the actual durable gateway journal, not a supplied bool.
            with gateway_tests.dbconnect(gateway.path) as db:
                row = db.execute(
                    'SELECT invocation_id,action_json,approval_json FROM governance_action WHERE action_id=?',
                    (action['action_id'],),
                ).fetchone()
            if not row or row[0] != scope['invocation_id'] or json.loads(row[1]) != action:
                return None
            grant = json.loads(row[2]) if row[2] else {}
            return {
                **grant,
                'invocation_id': row[0],
                **{key: action[key] for key in ('project_id', 'task_id', 'task_revision')},
            }

        adapters = changes.change_adapters(
            readers=self.readers, executor=self.executor, grant_checker=consumed_original, clock=lambda: self.now
        )
        return gateway, action, adapters

    async def test_duplicate_gateway_action_executes_once_and_consumes_one_original_approval(self):
        gateway, action, adapters = await self.fixture()
        first = await gateway.call(action, adapters)
        self.assertEqual(first['state'], 'completed')
        for _ in range(3):
            self.assertEqual(await gateway.call(action, adapters), first)
        self.assertEqual(len(self.execution_calls), 1)
        self.assertEqual(gateway.bridge.calls.count('consume_action_approval'), 1)
        self.assertTrue(first['result']['readback']['verified'])

    async def test_lost_ack_persists_original_uncertain_journal_and_never_reexecutes(self):
        gateway, action, adapters = await self.fixture()
        self.lost_ack = True
        with self.assertRaises(changes.ChangeOutcomeUncertain):
            await gateway.call(action, adapters)
        for _ in range(3):
            receipt = await gateway.call(action, adapters)
            self.assertEqual(receipt['state'], 'uncertain')
            self.assertTrue(receipt['result']['original_receipt_verified'])
            self.assertTrue(receipt['result']['postcondition_verified'])
            self.assertFalse(receipt['result']['executed'])
        self.assertEqual(len(self.execution_calls), 1)
        self.assertEqual(gateway.bridge.calls.count('consume_action_approval'), 1)

    async def test_concurrent_original_and_duplicate_share_one_host_intent(self):
        gateway, action, adapters = await self.fixture()
        original = self.executor.execute_once

        async def held_original(*args):
            await asyncio.sleep(0.01)
            return original(*args)

        self.executor.execute_once = held_original
        receipts = await asyncio.gather(gateway.call(action, adapters), gateway.call(action, adapters))
        self.assertTrue(all(row['state'] in {'completed', 'uncertain'} for row in receipts))
        self.assertEqual((await gateway.call(action, adapters))['state'], 'completed')
        self.assertEqual(len(self.execution_calls), 1)
        self.assertEqual(gateway.bridge.calls.count('consume_action_approval'), 1)

    async def test_project_paused_or_pin_changed_before_gateway_dispatch_executes_zero_times(self):
        gateway, action, adapters = await self.fixture()
        gateway.enabled = False
        with self.assertRaises(ValueError):
            await gateway.call(action, adapters)
        gateway.enabled = True
        gateway.profile['change_resources']['install_official_package']['fixture-package']['version'] = '3.0-1'
        with self.assertRaises(ValueError):
            await gateway.call(action, adapters)
        self.assertEqual(self.execution_calls, [])
        self.assertEqual(gateway.bridge.calls.count('consume_action_approval'), 0)
        self.assertIsNone(gateway_tests.module.get_action(gateway.inv, action['action_id'], gateway.path))

    async def test_unknown_erp_consumption_ack_is_fenced_before_any_host_change(self):
        gateway, action, adapters = await self.fixture()
        gateway.bridge.consume_lost = True
        with self.assertRaises(TimeoutError):
            await gateway.call(action, adapters)
        self.assertEqual((await gateway.call(action, adapters))['state'], 'uncertain')
        self.assertEqual(self.execution_calls, [])
        self.assertEqual(gateway.bridge.calls.count('consume_action_approval'), 1)

"""Actual files, SQLite and local HTTP GitHub protocol; no provider/live calls."""

import base64
from pathlib import Path
import tempfile
from unittest.mock import patch

from test_agentic_controls_events import Fixture, P
from test_agentic_gateway_delivery import GitHubFixture


class PlanningRepository(Fixture):
    async def asyncSetUp(self):
        self.delivery = self.load('agentic_gateway_delivery')
        self.ops = self.load('agentic_gateway_operations')
        self.sessions = self.load('agentic_gateway_sessions')
        self.directory = tempfile.TemporaryDirectory(
            prefix='agov-read-fixture-', dir='/srv/abit-dev-linux/abit-ai/deployment'
        )
        self.addCleanup(self.directory.cleanup)
        self.profile = {
            'repository': self.packet()['repository'],
            'environment': self.packet()['environment'],
            'base_branch': 'main',
            'base_commit': 'a' * 40,
            'allowed_paths': ['src'],
            'read_paths': ['README.md'],
            'test_paths': ['src'],
            'workspace_root': str(Path(self.directory.name) / 'workspaces'),
        }
        self.config = {'agentic_repository_gateway': {'projects': {P: self.profile}}}
        self.remote = GitHubFixture(self.delivery, self.profile['repository'])
        self.addCleanup(self.remote.close)
        self.client_patch = patch.object(
            self.delivery,
            'github_client',
            lambda repository: self.delivery.GitHubClient(
                'synthetic-github-test-token-no-production', repository, _test_base=self.remote.url
            ),
        )
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.context_token = self.sessions.CURRENT_DB.set(self.path)
        self.addCleanup(self.sessions.CURRENT_DB.reset, self.context_token)

    def source(self, role='solution_architect'):
        packet = self.packet(role=role)
        return {
            'erp_project': P,
            'erp_task': packet['task_id'],
            'invocation_id': 'planning-read-' + role,
            'target_role': role,
        }, packet

    async def prepare(self, role='solution_architect'):
        row, packet = self.source(role)
        receipt = await self.delivery.prepare_workspace(
            {}, row=row, packet=packet, config=self.config, db_path=self.path
        )
        return row, packet, receipt

    async def test_both_planning_roles_prepare_and_read_exact_pinned_base_without_remote_writes(self):
        for role in ('solution_architect', 'project_manager'):
            with self.subTest(role=role):
                row, packet, receipt = await self.prepare(role)
                self.assertTrue(receipt['read_only_source'])
                self.assertEqual(receipt['commit'], self.profile['base_commit'])
                self.assertEqual(receipt['file_count'], 1)
                self.assertNotIn('_source_manifest', receipt)
                root = Path(receipt['workspace_root'])
                self.assertFalse((root / 'src').exists())
                self.assertFalse((root / 'outside').exists())
                value = await self.ops.repository_read(
                    {'path': 'README.md'}, row=row, packet=packet, config=self.config
                )
                self.assertEqual(base64.b64decode(value['content_base64']), b'Synthetic repository fixture\n')
                self.assertEqual(value['source_commit'], self.profile['base_commit'])
                self.assertEqual(value['repository_snapshot_sha256'], receipt['snapshot_sha256'])
                self.assertEqual(
                    await self.delivery.prepare_workspace(
                        {}, row=row, packet=packet, config=self.config, db_path=self.path
                    ),
                    receipt,
                )
        self.assertEqual(self.remote.posts(), [])

    async def test_role_registry_does_not_grant_planning_tests_writes_or_delivery(self):
        for role in ('solution_architect', 'project_manager'):
            registry = self.ops.registry(role)
            self.assertTrue({'repository.prepare', 'repository.read'} <= set(registry))
            self.assertEqual(
                {key for key in registry if key.startswith('repository.')}, {'repository.prepare', 'repository.read'}
            )
            self.assertEqual(registry['repository.read'].roles, frozenset({role}))
        for role in ('operations', 'it_manager'):
            self.assertFalse(any(key.startswith('repository.') for key in self.ops.registry(role)))

    async def test_developer_cannot_read_or_write_planning_only_path(self):
        row, packet, receipt = await self.prepare('developer')
        root = Path(receipt['workspace_root'])
        self.assertTrue((root / 'src/app.py').is_file())
        self.assertFalse((root / 'README.md').exists())
        with self.assertRaisesRegex(ValueError, 'outside the approved change paths'):
            await self.ops.repository_read({'path': 'README.md'}, row=row, packet=packet, config=self.config)
        with self.assertRaisesRegex(ValueError, 'outside the approved change paths'):
            await self.ops.repository_write(
                {
                    'path': 'README.md',
                    'expected_sha256': None,
                    'content_base64': base64.b64encode(b'Forbidden').decode(),
                },
                row=row,
                packet=packet,
                config=self.config,
            )
        self.assertEqual(self.remote.posts(), [])

    async def test_missing_private_or_malformed_planning_paths_fail_before_fetch(self):
        for paths in (
            None,
            [],
            ['README.md', 'README.md'],
            ['src/.env.production'],
            ['../outside'],
            ['.git/config'],
            ['.'],
        ):
            with self.subTest(paths=paths):
                self.profile['read_paths'] = paths
                with self.assertRaises((ValueError, self.delivery.CapabilityUnavailable)):
                    await self.prepare()
        self.assertEqual(self.remote.requests, [])

    async def test_moved_base_or_wrong_repository_environment_cannot_be_substituted(self):
        row, packet = self.source()
        for key, value in (('environment', 'production'), ('repository', 'https://github.com/Other/Repository')):
            altered = {**packet, key: value}
            with self.assertRaises(self.delivery.CapabilityUnavailable):
                await self.delivery.prepare_workspace(
                    {}, row=row, packet=altered, config=self.config, db_path=self.path
                )
        self.assertEqual(self.remote.requests, [])
        self.remote.refs['main'] = 'b' * 40
        with self.assertRaises(ValueError):
            await self.prepare()
        self.assertFalse(self.delivery._workspace_target(row, self.profile).exists())
        self.assertEqual(self.remote.posts(), [])

    async def test_planning_snapshot_mutation_and_changed_profile_are_held(self):
        row, packet, receipt = await self.prepare()
        target = Path(receipt['workspace_root']) / 'README.md'
        target.write_bytes(b'Unreviewed local edit')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            await self.ops.repository_read({'path': 'README.md'}, row=row, packet=packet, config=self.config)
        target.write_bytes(b'Synthetic repository fixture\n')
        self.profile['read_paths'] = ['README.md', 'src']
        with self.assertRaisesRegex(ValueError, 'scope or approved baseline changed'):
            self.delivery.workspace_receipt(row, packet, self.config, self.path)
        self.assertEqual(self.remote.posts(), [])

    async def test_requested_bytes_are_checked_against_original_manifest_after_snapshot(self):
        row, packet, _ = await self.prepare()
        original = self.ops._read
        reads = 0

        def changed_after_snapshot(fd, name):
            nonlocal reads
            data, metadata = original(fd, name)
            reads += 1
            return (b'Changed after pinned snapshot', metadata) if reads == 2 else (data, metadata)

        with patch.object(self.ops, '_read', changed_after_snapshot):
            with self.assertRaisesRegex(ValueError, 'read bytes differ'):
                await self.ops.repository_read({'path': 'README.md'}, row=row, packet=packet, config=self.config)
        self.assertEqual(reads, 2)

    async def test_unsupported_role_cannot_use_direct_bootstrap_primitive(self):
        row, packet = self.source('operations')
        with self.assertRaises(self.delivery.CapabilityUnavailable):
            await self.delivery.prepare_workspace({}, row=row, packet=packet, config=self.config, db_path=self.path)
        self.assertEqual(self.remote.requests, [])

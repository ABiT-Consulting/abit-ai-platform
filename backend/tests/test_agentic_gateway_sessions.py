"""Real durable gateway journal and files; no provider or production config."""

from copy import deepcopy
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T


class Gateway(Fixture):
    def setUp(self):
        super().setUp()
        self.m = self.load('agentic_gateway_sessions')
        self.ops = self.load('agentic_gateway_operations')
        self.status = self.load('erp_status_write')
        self.status.LOCK_ROOT = Path(self.temp.name) / 'task-locks'
        selector = patch.object(
            self.ops,
            'artifact_directory',
            side_effect=lambda row, config: Path(self.temp.name) / 'artifacts' / row['invocation_id'],
        )
        selector.start()
        self.addCleanup(selector.stop)
        self.primary = self.load('abitdot_primary')
        self.key = patch.object(self.m, 'edge_secret', return_value='fixture-edge-key-no-production-value-0001')
        self.key.start()
        self.addCleanup(self.key.stop)
        self.config = {
            'agentic_action_gateway': {
                'owners': {
                    'owner-fixture': {'account_id': 'a' * 64, 'dot_id': 'dot-fixture', 'mcp_connection_hash': 'b' * 64}
                }
            }
        }
        self.bridge.packet = AsyncMock(
            side_effect=lambda project, task, role, agent, *args: self.packet(task, role, project)
        )
        self.bridge.gateway_boundary = AsyncMock(
            side_effect=lambda scope: {
                'schema_version': 1,
                'project_id': scope['project_id'],
                'task_id': scope['task_id'],
                'role': scope['role'],
                'agent_id': scope['agent_id'],
                'boundary': self.m.BOUNDARY,
                'managed_resource_guards': True,
                'pre_filesystem_file_guard': True,
                'ordinary_owner_plugins_fenced': False,
            }
        )
        self.calls = 0

        async def handler(args, **kwargs):
            self.calls += 1
            return {'observed': args}

        self.registry = {'read.fixture': self.m.Operation('inspect_infrastructure', frozenset({'developer'}), handler)}

    def scope(self, row):
        packet = self.packet()
        run = self.bridge_module.stable_run_id(row['invocation_id'])
        key = self.primary.dot_primary_key('owner-fixture', row['agent_id'], run)
        return {
            'owner': 'owner-fixture',
            'account_id': 'a' * 64,
            'dot_id': 'dot-fixture',
            'project_id': P,
            'task_id': T,
            'role': 'developer',
            'agent_id': row['agent_id'],
            'erp_user': self.users['developer'],
            'repository': packet['repository'],
            'environment': packet['environment'],
            'tool_scopes': sorted(packet['tool_scopes']),
            'work_packet_digest': self.bridge_module.packet_digest(packet),
            'invocation_id': row['invocation_id'],
            'run_id': run,
            'job_id': 'aibit_' + hashlib.sha256(('primary:' + key).encode()).hexdigest()[:48],
        }

    def kwargs(self):
        return dict(
            bridge=self.bridge, project_gate=self.gate, config=self.config, registry=self.registry, db_path=self.path
        )

    async def admitted(self):
        row = self.enqueue()
        scope = self.scope(row)
        first = await self.m.proof(scope, **self.kwargs())
        self.row = self.g.claim_invocation(
            row['invocation_id'], scope['run_id'], gate=self.gate_value, db_path=self.path
        )
        self.g.mark_submitted(
            row['invocation_id'], scope['run_id'], self.row['lease_token'], gate=self.gate_value, db_path=self.path
        )
        final = await self.m.proof(scope, **self.kwargs())
        value = {'protocol_version': 1, 'scope': scope, 'preflight_proof': first, 'submission_proof': final}
        result = await self.m.admit(value, **self.kwargs())
        self.value = value
        self.body = {
            'connection_hash': 'b' * 64,
            'client_session_hash': 'c' * 64,
            'session_id': final['gateway_session_id'],
            'call_id': 'call-fixture-001',
            'operation': 'read.fixture',
            'arguments': {},
        }
        return result

    async def test_exact_original_session_receipt_and_at_most_once_replay(self):
        result = await self.admitted()
        self.assertEqual(result, self.m.original_receipt(self.value, self.path))
        first = await self.m.call(self.body, **self.kwargs())
        second = await self.m.call(self.body, **self.kwargs())
        self.assertEqual(first['state'], 'completed')
        self.assertTrue(second['replayed'])
        self.assertEqual(self.calls, 1)
        # Opening the same durable SQLite state in a fresh module cannot replay.
        fresh = self.load('agentic_gateway_sessions')
        again = await fresh.call(self.body, **self.kwargs())
        self.assertTrue(again['replayed'])
        self.assertEqual(self.calls, 1)

    async def test_expired_proof_and_changed_scope_cannot_admit(self):
        await self.admitted()
        for change in ({'project_id': 'PROJ-other'}, {'role': 'qa'}, {'repository': 'https://github.com/other/repo'}):
            changed = deepcopy(self.value)
            changed['scope'].update(change)
            with self.assertRaises(ValueError):
                await self.m.admit(changed, **self.kwargs())
        changed = deepcopy(self.value)
        changed['submission_proof']['expires_at'] = int(time.time()) - 1
        with self.assertRaises(ValueError):
            await self.m.admit(changed, **self.kwargs())
        self.assertEqual(self.calls, 0)

    async def test_wrong_connection_conversation_operation_or_call_content_denied(self):
        await self.admitted()
        for change in ({'connection_hash': 'f' * 64}, {'operation': 'run_shell'}, {'session_id': 'other-job-session'}):
            with self.assertRaises(ValueError):
                await self.m.call({**self.body, **change}, **self.kwargs())
        await self.m.call(self.body, **self.kwargs())
        for change in ({'client_session_hash': 'f' * 64}, {'arguments': {'role': 'qa', 'project_id': 'PROJ-other'}}):
            with self.assertRaises(ValueError):
                await self.m.call({**self.body, **change}, **self.kwargs())
        self.assertEqual(self.calls, 1)

    async def test_current_lease_project_assignment_and_native_guard_rechecked_each_new_call(self):
        await self.admitted()
        self.allowed = False
        with self.assertRaises(ValueError):
            await self.m.call(self.body, **self.kwargs())
        self.allowed = True
        self.bridge.gateway_boundary.return_value = None
        self.bridge.gateway_boundary.side_effect = lambda scope: {'managed_resource_guards': False}
        with self.assertRaises(ValueError):
            await self.m.call(self.body, **self.kwargs())
        self.bridge.gateway_boundary.side_effect = None
        self.g.project_control(P, 'pause', actor='operator', db_path=self.path)
        with self.assertRaises(ValueError):
            await self.m.call(self.body, **self.kwargs())
        self.assertEqual(self.calls, 0)

    async def test_unknown_call_is_never_executed_again_and_receipt_survives_stop(self):
        await self.admitted()

        async def unknown(args, **kwargs):
            self.calls += 1
            raise ConnectionError('fixture lost acknowledgement')

        self.registry['read.fixture'] = self.m.Operation('inspect_infrastructure', frozenset({'developer'}), unknown)
        with self.assertRaises(ConnectionError):
            await self.m.call(self.body, **self.kwargs())
        self.g.project_control(P, 'stop', actor='operator', db_path=self.path)
        original = await self.m.call(self.body, **self.kwargs())
        self.assertEqual(original['state'], 'uncertain')
        self.assertIsNone(original['result'])
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.m.original_receipt(self.value, self.path), self.m.receipt(self.value))

    async def test_edge_mac_binds_actual_connection_metadata_body_and_freshness(self):
        await self.admitted()
        secret = self.m.edge_secret()
        headers = self.m.edge_headers(self.body, secret)
        self.assertEqual(self.m.verify_edge(self.body, headers, secret), self.body)
        for body, changed_headers in (
            ({**self.body, 'connection_hash': 'f' * 64}, headers),
            ({**self.body, 'arguments': {'operation': 'admin'}}, headers),
            (self.body, self.m.edge_headers(self.body, secret, now=int(time.time()) - 40)),
        ):
            with self.assertRaises(ValueError):
                self.m.verify_edge(body, changed_headers, secret)

    async def test_no_unauthenticated_profile_and_no_synthetic_global_scope_claim(self):
        row = self.enqueue()
        scope = self.scope(row)
        self.config['agentic_action_gateway']['owners']['owner-fixture']['account_id'] = 'f' * 64
        with self.assertRaises(ValueError):
            await self.m.proof(scope, **self.kwargs())
        self.config['agentic_action_gateway']['owners']['owner-fixture']['account_id'] = 'a' * 64
        result = await self.m.proof(scope, **self.kwargs())
        self.assertEqual(result['enforcement_mode'], 'governed_action_gateway')
        self.assertEqual(result['excluded_surfaces'], ['owner_account_plugins'])
        with self.m.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM governance_gateway_session').fetchone()[0], 0)

    async def test_real_run_owned_evidence_immutable_bytes_and_symlink_denials(self):
        row = self.enqueue()
        config = {'agentic_artifact_roots': {P: str(Path(self.temp.name) / 'evidence')}}
        # Production artifact roots are fixed to ABIT-DEV-LINUX. Patch only this
        # selector for a private temporary unit fixture, never runtime policy.
        root = Path(self.temp.name) / 'evidence' / row['erp_task'] / row['invocation_id']
        with patch.object(self.ops, 'artifact_directory', return_value=root):
            args = {'name': 'test.txt', 'content_base64': base64.b64encode(b'actual evidence\n').decode()}
            first = await self.ops.artifact_write(args, row=row, config=config)
            second = await self.ops.artifact_write(args, row=row, config=config)
            self.assertEqual(first, second)
            with self.assertRaises(ValueError):
                await self.ops.artifact_write(
                    {**args, 'content_base64': base64.b64encode(b'different').decode()}, row=row, config=config
                )
            (root / 'link.txt').symlink_to(root / 'test.txt')
            with self.assertRaises(OSError):
                await self.ops.artifact_read({'name': 'link.txt'}, row=row, config=config)
            with self.assertRaises(ValueError):
                await self.ops.artifact_read({'name': '../test.txt'}, row=row, config=config)
            self.assertEqual((root / 'test.txt').read_bytes(), b'actual evidence\n')

    async def test_repository_write_cannot_select_root_escape_scope_or_override_precondition(self):
        row = self.enqueue()
        root = Path(self.temp.name) / 'repo'
        (root / 'src').mkdir(parents=True)
        (root / 'src' / 'feature.py').write_text('before\n')
        profile = {'allowed_paths': ['src']}
        with patch.object(self.ops, '_workspace', return_value=(root, profile)):
            base = {
                'path': 'src/feature.py',
                'expected_sha256': hashlib.sha256(b'before\n').hexdigest(),
                'content_base64': base64.b64encode(b'after\n').decode(),
            }
            await self.ops.repository_write(base, row=row, packet=self.packet(), config={})
            for change in (
                {'path': '../outside'},
                {'path': 'src/../outside'},
                {'path': 'src/.env'},
                {'path': '.git/config'},
                {'expected_sha256': '0' * 64},
                {'root': '/tmp'},
            ):
                with self.assertRaises(ValueError):
                    await self.ops.repository_write({**base, **change}, row=row, packet=self.packet(), config={})
            (root / 'src' / 'linked').symlink_to('/etc/passwd')
            with self.assertRaises(OSError):
                await self.ops.repository_read({'path': 'src/linked'}, row=row, packet=self.packet(), config={})
            self.assertEqual((root / 'src' / 'feature.py').read_bytes(), b'after\n')

    async def test_different_call_ids_cannot_race_original_filesystem_operations(self):
        await self.admitted()
        active = 0
        maximum = 0

        async def handler(args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            active -= 1
            return {'observed': True}

        self.registry['read.fixture'] = self.m.Operation('inspect_infrastructure', frozenset({'developer'}), handler)
        results = await asyncio.gather(
            *(self.m.call({**self.body, 'call_id': 'parallel-call-' + str(n)}, **self.kwargs()) for n in range(2))
        )
        self.assertEqual(maximum, 1)
        self.assertEqual([r['state'] for r in results], ['completed', 'completed'])

    async def test_large_original_log_reads_are_chunked_and_invalid_svg_never_written(self):
        row = self.enqueue()
        value = b'x' * 300_000
        self.ops._store_artifact('large.txt', value, row=row, config={})
        first = await self.ops.artifact_read({'name': 'large.txt'}, row=row, config={})
        second = await self.ops.artifact_read({'name': 'large.txt', 'offset': first['next_offset']}, row=row, config={})
        self.assertLess(len(self.bridge_module.canonical(first).encode()), 300_000)
        self.assertEqual(base64.b64decode(first['content_base64']) + base64.b64decode(second['content_base64']), value)
        root = self.ops.artifact_directory(row, {})
        with self.assertRaises(ValueError):
            self.ops._store_artifact(
                'active.SVG',
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                row=row,
                config={},
            )
        self.assertFalse((root / 'active.SVG').exists())

    async def test_actual_test_namespace_denies_host_files_network_and_git_mutation(self):
        if not Path('/usr/bin/bwrap').is_file():
            self.skipTest('Official bubblewrap runtime not installed on this test host')
        row = self.enqueue()
        root = Path(self.temp.name) / 'sandbox-repo'
        (root / '.git').mkdir(parents=True)
        (root / 'src').mkdir()
        (root / 'src' / 'feature.py').write_text('value = 1\n')
        (root / 'src' / '.env').write_text('SECRET_SENTINEL=not_for_the_test\n')
        code = '''import json,os,socket
result={'host_hidden':not os.path.exists('/srv/abit-dev-linux'),'credential_hidden':not os.path.exists('/workspace/src/.env')}
try:
 socket.create_connection(('127.0.0.1',8810),timeout=0.2)
 result['network_denied']=False
except OSError: result['network_denied']=True
try:
 open('/workspace/.git/probe','w').write('forbidden')
 result['git_read_only']=False
except OSError: result['git_read_only']=True
print(json.dumps(result))'''
        profile = {
            'tests': {'isolation': ['/usr/bin/python3', '-c', code]},
            'test_paths': ['src'],
            'allowed_paths': ['src'],
        }
        with patch.object(self.ops, '_workspace', return_value=(root, profile)):
            result = await self.ops.repository_test({'test_id': 'isolation'}, row=row, packet=self.packet(), config={})
            self.assertEqual(result['exit_code'], 0, result['stderr'])
            self.assertEqual(
                json.loads(result['stdout']),
                {'host_hidden': True, 'network_denied': True, 'git_read_only': True, 'credential_hidden': True},
            )
            with self.assertRaises(ValueError):
                await self.ops.repository_test(
                    {'test_id': 'isolation', 'command': '/bin/sh'}, row=row, packet=self.packet(), config={}
                )
            with self.assertRaises(ValueError):
                await self.ops.repository_test({'test_id': 'unregistered'}, row=row, packet=self.packet(), config={})
        self.assertFalse((root / '.git/probe').exists())

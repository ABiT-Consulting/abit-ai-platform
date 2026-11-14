"""Actual temporary files/SQLite/loopback GitHub protocol, no external GitHub/model.

Only the approved-workspace accessor and prior test-run receipts are controlled.
Delivery snapshots, bytes, Git objects, private evidence and recovery are real.
"""

from copy import deepcopy
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
import sys
from urllib.parse import unquote, urlsplit, parse_qs
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P


class GitHubFixture:
    def __init__(self, module, repository):
        self.m = module
        self.repository = repository
        self.name = repository.split('github.com/')[1]
        self.requests = []
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.refs = {}
        self.pulls = {}
        self.drop = None
        self.discard = False
        self.hidden = False
        old = b'print("old implementation")\n'
        readme = b'Synthetic repository fixture\n'
        self.base = 'a' * 40
        leaves = {}
        for path, data in [
            ('src/app.py', old),
            ('README.md', readme),
            ('src/.env', b'SYNTHETIC_PRIVATE_DO_NOT_FETCH'),
            ('outside/unrelated.py', b'Outside approved paths'),
        ]:
            sha = self.m.git_hash('blob', data)
            self.blobs[sha] = data
            leaves[path] = {'sha': sha, 'mode': '100644', 'type': 'blob'}
        tree = self.m._tree_hash(leaves)
        self.trees[tree] = leaves
        self.commits[self.base] = {'sha': self.base, 'tree': {'sha': tree}, 'parents': [], 'message': 'Base fixture'}
        self.refs['main'] = self.base
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_rpc('GET')

            def do_POST(self):
                self.handle_rpc('POST')

            def handle_rpc(self, method):
                assert self.headers.get('Authorization') == 'Bearer synthetic-github-test-token-no-production'
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'null')
                prefix = '/repos/' + owner.name
                assert self.path.startswith(prefix + '/')
                suffix = self.path[len(prefix) :]
                owner.requests.append((method, suffix, deepcopy(body)))
                route = urlsplit(suffix)
                path = unquote(route.path)
                if method == 'POST' and owner.discard:
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                result = None
                if method == 'GET':
                    if path.startswith('/git/ref/heads/'):
                        name = path[len('/git/ref/heads/') :]
                        sha = owner.refs.get(name)
                        if sha:
                            result = {'ref': 'refs/heads/' + name, 'object': {'sha': sha, 'type': 'commit'}}
                    elif path.startswith('/git/commits/'):
                        result = owner.commits.get(path.rsplit('/', 1)[1])
                    elif path.startswith('/git/trees/'):
                        sha = path.rsplit('/', 1)[1]
                        leaves = owner.trees.get(sha)
                        if leaves is not None:
                            result = {
                                'sha': sha,
                                'truncated': False,
                                'tree': [{'path': p, **v} for p, v in leaves.items()],
                            }
                    elif path.startswith('/git/blobs/'):
                        sha = path.rsplit('/', 1)[1]
                        data = owner.blobs.get(sha)
                        if data is not None and not owner.hidden:
                            result = {
                                'sha': sha,
                                'encoding': 'base64',
                                'content': base64.b64encode(data).decode(),
                                'size': len(data),
                            }
                    elif path == '/pulls':
                        query = parse_qs(route.query)
                        branch = query.get('head', [''])[0].split(':', 1)[-1]
                        result = [pr for pr in owner.pulls.values() if pr['head']['ref'] == branch]
                    elif path.startswith('/pulls/'):
                        result = owner.pulls.get(int(path.rsplit('/', 1)[1]))
                else:
                    if path == '/git/blobs':
                        data = base64.b64decode(body['content'])
                        sha = owner.m.git_hash('blob', data)
                        owner.blobs[sha] = data
                        result = {'sha': sha}
                    elif path == '/git/trees':
                        leaves = deepcopy(owner.trees[body['base_tree']])
                        for item in body['tree']:
                            if item['sha'] is None:
                                leaves.pop(item['path'], None)
                            else:
                                leaves[item['path']] = {k: item[k] for k in ('sha', 'mode', 'type')}
                        sha = owner.m._tree_hash(leaves)
                        owner.trees[sha] = leaves
                        result = {'sha': sha}
                    elif path == '/git/commits':
                        sha = owner.m._commit_hash(body)
                        result = {
                            'sha': sha,
                            'tree': {'sha': body['tree']},
                            'parents': [{'sha': x} for x in body['parents']],
                            'message': body['message'],
                            'author': body['author'],
                            'committer': body['committer'],
                        }
                        owner.commits[sha] = result
                    elif path == '/git/refs':
                        name = body['ref'][len('refs/heads/') :]
                        if name not in owner.refs:
                            owner.refs[name] = body['sha']
                            result = {'ref': body['ref'], 'object': {'sha': body['sha'], 'type': 'commit'}}
                    elif path == '/pulls':
                        number = len(owner.pulls) + 1
                        result = {
                            **body,
                            'number': number,
                            'html_url': owner.repository + '/pull/' + str(number),
                            'state': 'open',
                            'head': {
                                'sha': owner.refs[body['head']],
                                'ref': body['head'],
                                'repo': {'full_name': owner.name},
                            },
                            'base': {'sha': owner.base, 'ref': body['base'], 'repo': {'full_name': owner.name}},
                        }
                        owner.pulls[number] = result
                    if owner.drop == path:
                        owner.drop = None
                        self.connection.shutdown(2)
                        self.connection.close()
                        return
                self.send_response((200 if method == 'GET' else 201) if result is not None else 404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(
                    json.dumps(result if result is not None else {'message': 'Synthetic not found'}).encode()
                )

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def posts(self):
        return [path for method, path, _ in self.requests if method == 'POST']


class Delivery(Fixture):
    async def asyncSetUp(self):
        self.m = self.load('agentic_gateway_delivery')
        self.ops = self.load('agentic_gateway_operations')
        self.sessions = self.load('agentic_gateway_sessions')
        queued = self.enqueue()
        self.packet_value = self.packet()
        self.row = self.g.claim_invocation(
            queued['invocation_id'],
            self.bridge_module.stable_run_id(queued['invocation_id']),
            gate=self.gate_value,
            db_path=self.path,
        )
        self.repo = Path(self.temp.name) / 'repository'
        (self.repo / 'src').mkdir(parents=True)
        (self.repo / 'src/app.py').write_bytes(b'print("verified new implementation")\n')
        self.profile = {
            'repository': self.packet_value['repository'],
            'environment': self.packet_value['environment'],
            'allowed_paths': ['src'],
            'base_branch': 'main',
            'base_commit': 'a' * 40,
            'workspace_root': str(Path(self.temp.name) / 'workspaces'),
            'test_paths': [],
            'git_author': {'name': 'Synthetic Developer', 'email': 'developer@example.invalid'},
            'tests': {'unit': ['python3', '-m', 'unittest']},
            'test_acceptance_ids': {'unit': ['REQ-ONE.acceptance']},
        }
        self.config = {
            'agentic_artifact_roots': {P: str(Path(self.temp.name) / 'evidence')},
            'agentic_repository_gateway': {'projects': {P: self.profile}},
        }
        artifact_root = lambda row, config: Path(self.temp.name) / 'evidence' / row['invocation_id']
        for target in [self.m, self.ops, sys.modules['open_webui.utils.agentic_artifacts']]:
            item = patch.object(target, 'artifact_directory', artifact_root)
            item.start()
            self.addCleanup(item.stop)
        self.workspace = patch.object(self.ops, '_workspace', return_value=(self.repo, self.profile))
        self.workspace.start()
        self.addCleanup(self.workspace.stop)
        self.remote = GitHubFixture(self.m, self.packet_value['repository'])
        self.addCleanup(self.remote.close)
        factory = lambda repository: self.m.GitHubClient(
            'synthetic-github-test-token-no-production', repository, _test_base=self.remote.url
        )
        self.client_patch = patch.object(self.m, 'github_client', factory)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        with self.sessions.connect(self.path) as db:
            db.execute(
                'INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',
                (
                    'fixture-session-001',
                    self.row['invocation_id'],
                    '{}',
                    '{}',
                    'a' * 64,
                    'b' * 64,
                    'admitted',
                    int(time.time()),
                ),
            )
            db.commit()
        await self.seed_test()

    async def seed_test(self, call='fixture-test-call-001', stdout='Synthetic native command passed\n'):
        snapshot = self.m.snapshot_workspace(self.row, self.packet_value, self.config)
        result = {
            'test_id': 'unit',
            'exit_code': 0,
            'command': '["python3","-m","unittest"]',
            'sandbox': 'bubblewrap-no-network-sanitized-copy',
            'repository_snapshot_sha256': snapshot['sha256'],
            'stdout': stdout,
            'stderr': '',
        }
        for key in ('stdout', 'stderr'):
            result[key + '_sha256'] = hashlib.sha256(result[key].encode()).hexdigest()
        artifact = await self.ops.artifact_write(
            {
                'name': call + '.txt',
                'content_base64': base64.b64encode(self.bridge_module.canonical(result).encode()).decode(),
            },
            row=self.row,
            config=self.config,
        )
        result['evidence'] = artifact
        result['output_preview_truncated'] = any(len(result[key]) > 4096 for key in ('stdout', 'stderr'))
        for key in ('stdout', 'stderr'):
            result[key] = result[key][:4096]
        with self.sessions.connect(self.path) as db:
            db.execute(
                'INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',
                (
                    'fixture-session-001',
                    call,
                    'd' * 64,
                    'repository.test',
                    'completed',
                    json.dumps(result),
                    int(time.time()),
                    int(time.time()),
                ),
            )
            db.commit()

    async def deliver(self, **args):
        return await self.m.developer_deliver(
            {'test_call_ids': ['fixture-test-call-001'], **args},
            row=self.row,
            packet=self.packet_value,
            config=self.config,
            db_path=self.path,
        )

    async def reconcile(self):
        return await self.m.reconcile_delivery(
            {}, row=self.row, packet=self.packet_value, config=self.config, db_path=self.path
        )

    def qa_context(self, delivery):
        packet = self.packet(role='qa')
        packet['implementation_run_id'] = self.row['automation_run_id']
        packet['implementation_delivery'] = delivery
        packet['content_sha256'] = self.bridge_module.packet_digest(packet)
        row = {
            **self.row,
            'target_role': 'qa',
            'agent_id': packet['binding']['agent_id'],
            'invocation_id': 'fixture-independent-qa-0001',
            'automation_run_id': 'fixture-independent-qa-run-0001',
            'source_invocation_id': self.row['invocation_id'],
        }
        # This focused GitHub fixture starts at the real Developer->QA handoff
        # boundary; register its exact finished predecessor for role selection.
        with self.g.connect(self.path) as db:
            db.execute(
                "UPDATE governance_invocation SET state='handed_off' WHERE invocation_id=?",
                (self.row['invocation_id'],),
            )
            db.commit()
        return row, packet

    async def test_IT_QA_hook_requires_explicit_verified_helper_and_existing_bridge(self):
        ready = await self.deliver()
        qa, packet = self.qa_context(ready['delivery'])
        packet.pop('implementation_delivery')
        packet['content_sha256'] = self.bridge_module.packet_digest(packet)
        self.load('agentic_it_qa')
        helper = sys.modules['open_webui.utils.agentic_it_qa']
        bridge = object()
        with patch.object(helper, 'assert_it_qa_result', AsyncMock(return_value=True)) as verified:
            await self.m.assert_gateway_result(qa, packet, {'outcome': 'pass'}, self.config, self.path, bridge=bridge)
            self.assertIs(verified.call_args.kwargs['bridge'], bridge)
        with patch.object(helper, 'assert_it_qa_result', AsyncMock(return_value=False)):
            with self.assertRaisesRegex(ValueError, 'Developer delivery'):
                await self.m.assert_gateway_result(
                    qa, packet, {'outcome': 'pass'}, self.config, self.path, bridge=bridge
                )
        with patch.object(helper, 'assert_it_qa_result', AsyncMock(side_effect=ValueError('IT proof missing'))):
            with self.assertRaisesRegex(ValueError, 'IT proof missing'):
                await self.m.assert_gateway_result(
                    qa, packet, {'outcome': 'pass'}, self.config, self.path, bridge=bridge
                )

    async def seed_qa_test(self, row, packet):
        with self.sessions.connect(self.path) as db:
            db.execute(
                'INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',
                (
                    'fixture-session-qa-001',
                    row['invocation_id'],
                    '{}',
                    '{}',
                    'a' * 64,
                    'b' * 64,
                    'admitted',
                    int(time.time()),
                ),
            )
            db.commit()
        original_row, original_packet = self.row, self.packet_value
        self.row, self.packet_value = row, packet
        try:
            snapshot = self.m.snapshot_workspace(row, packet, self.config)
            result = {
                'test_id': 'unit',
                'exit_code': 0,
                'command': '["python3","-m","unittest"]',
                'sandbox': 'bubblewrap-no-network-sanitized-copy',
                'repository_snapshot_sha256': snapshot['sha256'],
                'stdout': 'Independent synthetic QA command passed\n',
                'stderr': '',
            }
            for key in ('stdout', 'stderr'):
                result[key + '_sha256'] = hashlib.sha256(result[key].encode()).hexdigest()
            artifact = await self.ops.artifact_write(
                {
                    'name': 'independent-qa.txt',
                    'content_base64': base64.b64encode(self.bridge_module.canonical(result).encode()).decode(),
                },
                row=row,
                config=self.config,
            )
            result.update(evidence=artifact, output_preview_truncated=False)
            with self.sessions.connect(self.path) as db:
                db.execute(
                    'INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)',
                    (
                        'fixture-session-qa-001',
                        'fixture-independent-qa-test',
                        'd' * 64,
                        'repository.test',
                        'completed',
                        json.dumps(result),
                        int(time.time()),
                        int(time.time()),
                    ),
                )
                db.commit()
        finally:
            self.row, self.packet_value = original_row, original_packet

    async def test_real_files_git_objects_and_draft_PR_complete_once_with_exact_evidence(self):
        result = await self.deliver()
        self.assertEqual(result['state'], 'ready_for_review')
        self.assertEqual(result['delivery']['changed_paths'], ['src/app.py'])
        self.assertEqual(result['delivery']['base_commit'], 'a' * 40)
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertTrue(self.remote.pulls[1]['draft'])
        head = result['delivery']['head_commit']
        tree = self.remote.commits[head]['tree']['sha']
        self.assertIn('README.md', self.remote.trees[tree])
        blob = self.remote.trees[tree]['src/app.py']['sha']
        self.assertEqual(self.remote.blobs[blob], (self.repo / 'src/app.py').read_bytes())
        from open_webui.utils.agentic_contracts.developer_delivery import (
            validate_developer_delivery,
            verify_manifest_bytes,
        )

        contract = {**result, 'defects': []}
        self.assertEqual(validate_developer_delivery(contract, self.packet_value), [])
        commit_evidence = next(x for x in result['evidence'] if x['kind'] == 'commit')
        _, content = self.m.local_bytes(self.m.artifact_directory(self.row, self.config), commit_evidence['uri'])
        self.assertIsNone(verify_manifest_bytes(result['delivery'], content))
        before = self.remote.posts().copy()
        self.assertEqual(await self.deliver(), result)
        self.assertEqual(self.remote.posts(), before)
        self.assertEqual(len([x for x in before if x == '/git/refs']), 1)
        self.assertEqual(len([x for x in before if x == '/pulls']), 1)

    async def test_stale_or_missing_actual_test_receipt_denies_before_any_remote_request(self):
        (self.repo / 'src/app.py').write_bytes(b'Changed after tests\n')
        with self.assertRaises(ValueError):
            await self.deliver()
        self.assertEqual(self.remote.requests, [])
        with self.assertRaises(ValueError):
            await self.deliver(test_call_ids=['not-this-invocation'])
        self.assertEqual(self.remote.requests, [])

    async def test_lost_blob_ACK_has_GET_only_reconciliation_then_no_duplicate_POST(self):
        self.remote.drop = '/git/blobs'
        held = await self.deliver()
        self.assertEqual(held['state'], 'pending_reconciliation')
        before = self.remote.posts().copy()
        self.assertEqual(before, ['/git/blobs'])
        observed = await self.reconcile()
        self.assertEqual(observed['state'], 'reconciled')
        self.assertEqual(self.remote.posts(), before)
        ready = await self.deliver()
        self.assertEqual(ready['state'], 'ready_for_review')
        self.assertEqual(self.remote.posts().count('/git/blobs'), 1)
        self.assertEqual(self.remote.posts().count('/git/refs'), 1)
        self.assertEqual(len(self.remote.pulls), 1)

    async def test_unknown_absent_remote_object_never_permits_automatic_write_retry(self):
        self.remote.discard = True
        self.assertEqual((await self.deliver())['state'], 'pending_reconciliation')
        self.remote.discard = False
        self.remote.hidden = True
        for _ in range(2):
            self.assertEqual((await self.deliver())['state'], 'pending_reconciliation')
        self.assertEqual(self.remote.posts(), ['/git/blobs'])
        self.assertEqual(self.remote.pulls, {})

    async def assert_lost_step(self, route):
        self.remote.drop = route
        self.assertEqual((await self.deliver())['state'], 'pending_reconciliation')
        before = self.remote.posts().copy()
        self.assertEqual(before.count(route), 1)
        self.assertEqual((await self.reconcile())['state'], 'reconciled')
        self.assertEqual(self.remote.posts(), before)
        self.assertEqual((await self.deliver())['state'], 'ready_for_review')
        self.assertEqual(self.remote.posts().count(route), 1)
        self.assertEqual(self.remote.posts().count('/git/refs'), 1)
        self.assertEqual(self.remote.posts().count('/pulls'), 1)

    async def test_lost_tree_ACK_reconciles_the_exact_predicted_tree_only(self):
        await self.assert_lost_step('/git/trees')

    async def test_lost_commit_ACK_reconciles_exact_frozen_commit_without_second_commit(self):
        await self.assert_lost_step('/git/commits')

    async def test_lost_reference_ACK_reconciles_branch_without_duplicate_branch(self):
        await self.assert_lost_step('/git/refs')

    async def test_lost_PR_ACK_reuses_exact_existing_draft_without_second_PR_or_branch(self):
        self.remote.drop = '/pulls'
        self.assertEqual((await self.deliver())['state'], 'pending_reconciliation')
        self.assertEqual(len(self.remote.pulls), 1)
        before = self.remote.posts().copy()
        self.assertEqual((await self.reconcile())['state'], 'reconciled')
        self.assertEqual(self.remote.posts(), before)
        self.assertEqual((await self.deliver())['state'], 'ready_for_review')
        self.assertEqual(self.remote.posts(), before)
        self.assertEqual(before.count('/pulls'), 1)
        self.assertEqual(before.count('/git/refs'), 1)

    async def test_snapshot_excludes_private_names_without_reading_and_denies_links(self):
        (self.repo / 'src/.env').write_text('synthetic-private-never-in-manifest')
        (self.repo / 'src/.env').chmod(0)
        snapshot = self.m.snapshot_workspace(self.row, self.packet_value, self.config)
        self.assertNotIn('src/.env', snapshot['contents'])
        (self.repo / 'src/link.py').symlink_to(self.repo / 'src/app.py')
        with self.assertRaises(ValueError):
            self.m.snapshot_workspace(self.row, self.packet_value, self.config)
        (self.repo / 'src/link.py').unlink()
        (self.repo / 'src/app-copy.py').hardlink_to(self.repo / 'src/app.py')
        with self.assertRaises(ValueError):
            self.m.snapshot_workspace(self.row, self.packet_value, self.config)

    async def test_QA_reads_exact_PR_head_independently_and_rejects_moved_head_or_base(self):
        result = await self.deliver()
        before = self.remote.posts().copy()
        qa, packet = self.qa_context(result['delivery'])
        checked = await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.config, db_path=self.path)
        self.assertTrue(checked['verified'])
        self.assertEqual(checked['head_commit'], result['delivery']['head_commit'])
        self.assertEqual(self.remote.posts(), before)
        self.remote.pulls[1]['head']['sha'] = 'f' * 40
        with self.assertRaises(ValueError):
            await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.config, db_path=self.path)
        self.remote.pulls[1]['head']['sha'] = result['delivery']['head_commit']
        self.remote.pulls[1]['base']['sha'] = 'e' * 40
        with self.assertRaises(ValueError):
            await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.config, db_path=self.path)
        self.assertEqual(self.remote.posts(), before)

    async def test_original_delivery_cannot_change_target_tests_or_source_after_publication(self):
        await self.deliver()
        before = self.remote.posts().copy()
        (self.repo / 'src/app.py').write_bytes(b'Changed after publication\n')
        await self.seed_test('fixture-test-call-002')
        with self.assertRaises(ValueError):
            await self.deliver(test_call_ids=['fixture-test-call-002'])
        self.assertEqual(self.remote.posts(), before)

    async def test_model_selected_remote_target_or_unregistered_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.deliver(repository='https://example.invalid/exfiltrate')
        with self.assertRaises(ValueError):
            await self.m.qa_verify_delivery(
                {'url': 'https://example.invalid'},
                row={**self.row, 'target_role': 'qa'},
                packet=self.packet_value,
                config=self.config,
            )
        self.assertEqual(self.remote.requests, [])

    async def test_full_test_log_digest_binds_truncated_previews(self):
        await self.seed_test('fixture-test-long-002', stdout='Synthetic passed output\n' * 400)
        self.assertEqual((await self.deliver(test_call_ids=['fixture-test-long-002']))['state'], 'ready_for_review')
        before = self.remote.posts().copy()
        with self.sessions.connect(self.path) as db:
            value = json.loads(
                db.execute(
                    'SELECT result_json FROM governance_gateway_call WHERE call_id=?', ('fixture-test-long-002',)
                ).fetchone()[0]
            )
            value['stdout_sha256'] = '0' * 64
            db.execute(
                'UPDATE governance_gateway_call SET result_json=? WHERE call_id=?',
                (json.dumps(value), 'fixture-test-long-002'),
            )
            db.commit()
        with self.assertRaises(ValueError):
            await self.deliver(test_call_ids=['fixture-test-long-002'])
        self.assertEqual(self.remote.posts(), before)

    async def test_concurrent_delivery_requests_never_duplicate_remote_mutations(self):
        results = await asyncio.gather(self.deliver(), self.deliver())
        self.assertIn('ready_for_review', {x['state'] for x in results})
        with self.m._db(self.path) as db:
            row = db.execute('SELECT state FROM governance_developer_delivery').fetchone()
        self.assertEqual(row['state'], 'completed')
        self.assertEqual(self.remote.posts().count('/git/blobs'), 1)
        self.assertEqual(self.remote.posts().count('/git/commits'), 1)
        self.assertEqual(self.remote.posts().count('/git/refs'), 1)
        self.assertEqual(len(self.remote.pulls), 1)

    async def test_actual_Developer_acceptance_cannot_substitute_model_authored_delivery_or_evidence(self):
        ready = await self.deliver()
        result = {**ready, 'outcome': 'ready_for_review'}
        await self.m.assert_gateway_result(self.row, self.packet_value, result, self.config, self.path)
        forged = deepcopy(result)
        forged['delivery']['head_commit'] = 'f' * 40
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(self.row, self.packet_value, forged, self.config, self.path)
        forged = deepcopy(result)
        forged['evidence'] = []
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(self.row, self.packet_value, forged, self.config, self.path)

    async def test_QA_pass_requires_own_actual_head_and_independent_current_source_tests(self):
        ready = await self.deliver()
        qa, packet = self.qa_context(ready['delivery'])
        from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest

        result = {
            'outcome': 'pass',
            'reviewed_delivery_sha256': delivery_digest(ready['delivery']),
            'test_results': [],
            'evidence': [],
        }
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa, packet, result, self.config, self.path)
        facts = await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.config, db_path=self.path)
        self.assertFalse(facts['tests_complete'])
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa, packet, result, self.config, self.path)
        await self.seed_qa_test(qa, packet)
        facts = await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.config, db_path=self.path)
        self.assertTrue(facts['tests_complete'])
        result.update(test_results=facts['test_results'], evidence=facts['evidence'])
        await self.m.assert_gateway_result(qa, packet, result, self.config, self.path)
        (self.repo / 'src/app.py').write_bytes(b'Wrong local code cannot be QA approved')
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa, packet, result, self.config, self.path)

    async def test_server_bootstrap_downloads_only_approved_nonprivate_bytes_and_never_replaces(self):
        with patch.object(self.m, '_safe_workspace_root', lambda profile: Path(profile['workspace_root'])):
            receipt = await self.m.prepare_workspace(
                {}, row=self.row, packet=self.packet_value, config=self.config, db_path=self.path
            )
            target = Path(receipt['workspace_root'])
            self.assertEqual((target / 'src/app.py').read_bytes(), b'print("old implementation")\n')
            self.assertFalse((target / 'src/.env').exists())
            self.assertFalse((target / 'outside').exists())
            private_sha = self.remote.trees[self.remote.commits[self.remote.base]['tree']['sha']]['src/.env']['sha']
            self.assertNotIn('/git/blobs/' + private_sha, [path for _, path, _ in self.remote.requests])
            before = self.remote.posts().copy()
            again = await self.m.prepare_workspace(
                {}, row=self.row, packet=self.packet_value, config=self.config, db_path=self.path
            )
            self.assertEqual(again, receipt)
            self.assertEqual(before, [])
            self.assertEqual(self.remote.posts(), [])
            (target / 'src/app.py').write_bytes(b'Legitimate later Developer edit')
            self.assertEqual(
                self.m.workspace_receipt(self.row, self.packet_value, self.config, self.path)['config_sha256'],
                receipt['config_sha256'],
            )
            (target / '.git/config').write_bytes(b'Unapproved Git configuration')
            with self.assertRaises(ValueError):
                self.m.workspace_receipt(self.row, self.packet_value, self.config, self.path)

    async def test_QA_bootstrap_fetches_exact_Developer_PR_head_and_unknown_tree_is_preserved(self):
        ready = await self.deliver()
        qa, packet = self.qa_context(ready['delivery'])
        with patch.object(self.m, '_safe_workspace_root', lambda profile: Path(profile['workspace_root'])):
            target = self.m._workspace_target(qa, self.profile)
            target.mkdir(parents=True)
            (target / 'human.txt').write_text('Preserve existing unrelated work')
            with self.assertRaises(ValueError):
                await self.m.prepare_workspace({}, row=qa, packet=packet, config=self.config, db_path=self.path)
            self.assertEqual((target / 'human.txt').read_text(), 'Preserve existing unrelated work')
            import shutil

            shutil.rmtree(target)
            receipt = await self.m.prepare_workspace({}, row=qa, packet=packet, config=self.config, db_path=self.path)
            self.assertEqual(receipt['commit'], ready['delivery']['head_commit'])
            self.assertEqual(
                (Path(receipt['workspace_root']) / 'src/app.py').read_bytes(), (self.repo / 'src/app.py').read_bytes()
            )

    async def test_bootstrap_rejects_source_over_the_gateway_192KiB_read_bound(self):
        self.assertEqual(self.m.MAX_FILE, 196608)
        data = b'x' * 196609
        sha = self.m.git_hash('blob', data)
        self.remote.blobs[sha] = data
        leaves = deepcopy(self.remote.trees[self.remote.commits[self.remote.base]['tree']['sha']])
        leaves['src/app.py']['sha'] = sha
        tree = self.m._tree_hash(leaves)
        self.remote.trees[tree] = leaves
        self.remote.commits[self.remote.base]['tree']['sha'] = tree
        with patch.object(self.m, '_safe_workspace_root', lambda profile: Path(profile['workspace_root'])):
            target = self.m._workspace_target(self.row, self.profile)
            with self.assertRaisesRegex(ValueError, 'Bootstrap source bytes exceed their bound'):
                await self.m.prepare_workspace(
                    {}, row=self.row, packet=self.packet_value, config=self.config, db_path=self.path
                )
            self.assertFalse(target.exists())
        self.assertEqual(self.remote.posts(), [])

    async def test_bootstrap_install_lost_ACK_recovers_original_files_without_reinstall_or_download(self):
        original = self.m._install_directory

        def lost(*args):
            original(*args)
            raise RuntimeError('Controlled lost acknowledgment after exact atomic install')

        with patch.object(self.m, '_safe_workspace_root', lambda profile: Path(profile['workspace_root'])):
            with patch.object(self.m, '_install_directory', lost):
                with self.assertRaises(RuntimeError):
                    await self.m.prepare_workspace(
                        {}, row=self.row, packet=self.packet_value, config=self.config, db_path=self.path
                    )
            downloads = len(
                [p for method, p, _ in self.remote.requests if method == 'GET' and p.startswith('/git/blobs/')]
            )
            with patch.object(
                self.m,
                '_install_directory',
                side_effect=AssertionError('Installed original must never be installed twice'),
            ):
                receipt = await self.m.prepare_workspace(
                    {}, row=self.row, packet=self.packet_value, config=self.config, db_path=self.path
                )
            self.assertEqual(
                len([p for method, p, _ in self.remote.requests if method == 'GET' and p.startswith('/git/blobs/')]),
                downloads,
            )
            self.assertTrue(Path(receipt['workspace_root']).is_dir())

    async def test_Git_tree_encoding_matches_independent_readonly_git_hash_object(self):
        blob = self.m.git_hash('blob', b'bytes\n')
        child = b'100644 sha\0' + bytes.fromhex(blob)
        child_sha = (
            subprocess.run(
                ['/usr/bin/git', 'hash-object', '-t', 'tree', '--stdin'],
                input=child,
                capture_output=True,
                check=True,
                cwd=self.temp.name,
                env={
                    'PATH': '/usr/bin:/bin',
                    'HOME': self.temp.name,
                    'GIT_CONFIG_GLOBAL': '/dev/null',
                    'GIT_CONFIG_NOSYSTEM': '1',
                },
            )
            .stdout.decode()
            .strip()
        )
        root = b'40000 folder\0' + bytes.fromhex(child_sha)
        expected = (
            subprocess.run(
                ['/usr/bin/git', 'hash-object', '-t', 'tree', '--stdin'],
                input=root,
                capture_output=True,
                check=True,
                cwd=self.temp.name,
                env={
                    'PATH': '/usr/bin:/bin',
                    'HOME': self.temp.name,
                    'GIT_CONFIG_GLOBAL': '/dev/null',
                    'GIT_CONFIG_NOSYSTEM': '1',
                },
            )
            .stdout.decode()
            .strip()
        )
        self.assertEqual(self.m._tree_hash({'folder/sha': {'sha': blob, 'mode': '100644', 'type': 'blob'}}), expected)

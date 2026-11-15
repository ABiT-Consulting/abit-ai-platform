"""Network-denied original delivery, gateway, provenance and Git readback regressions."""
from copy import deepcopy
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import socket
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch
import unittest

import test_original_delivery_continuation as base
import test_agentic_gateway_delivery as fixture_delivery


class MemoryGit:
    """Exact Git object protocol with no sockets, including lost acknowledgements."""
    def __init__(self, module, repository):
        self.m, self.repository = module, repository
        self.name = repository.split('github.com/')[1]
        self.requests, self.blobs, self.trees, self.commits, self.refs, self.pulls = [], {}, {}, {}, {}, {}
        self.drop, self.discard, self.hidden, self.normalize_message = None, False, False, False
        self.base, self.url = 'a' * 40, 'https://never-opened.invalid'
        leaves = {}
        for name, data in [('src/app.py', b'print("old implementation")\n'),
                           ('README.md', b'Synthetic repository fixture\n'),
                           ('src/.env', b'SYNTHETIC_PRIVATE_DO_NOT_FETCH'),
                           ('outside/unrelated.py', b'Outside approved paths')]:
            sha = module.git_hash('blob', data)
            self.blobs[sha] = data
            leaves[name] = {'sha': sha, 'mode': '100644', 'type': 'blob'}
        tree = module._tree_hash(leaves)
        self.trees[tree] = leaves
        self.commits[self.base] = {'sha': self.base, 'tree': {'sha': tree}, 'parents': [], 'message': 'Base fixture'}
        self.refs['main'] = self.base

    def close(self):
        pass

    def posts(self):
        return [path for method, path, _ in self.requests if method == 'POST']

    async def request(self, method, suffix, body=None):
        self.requests.append((method, suffix, deepcopy(body)))
        route = urlsplit(suffix)
        path = unquote(route.path)
        if method == 'POST' and self.discard:
            raise self.m.RemoteUnknown('Controlled unknown remote outcome')
        result = None
        if method == 'GET':
            if path.startswith('/git/ref/heads/'):
                name = path[len('/git/ref/heads/'):]
                sha = self.refs.get(name)
                if sha:
                    result = {'ref': 'refs/heads/' + name, 'object': {'sha': sha, 'type': 'commit'}}
            elif path.startswith('/git/commits/'):
                result = deepcopy(self.commits.get(path.rsplit('/', 1)[1]))
                if result and self.normalize_message and result['message'].endswith('\n'):
                    result['message'] = result['message'][:-1]
            elif path.startswith('/git/trees/'):
                sha = path.rsplit('/', 1)[1]
                if sha in self.trees:
                    result = {'sha': sha, 'truncated': False, 'tree': [
                        {'path': name, **value} for name, value in self.trees[sha].items()]}
            elif path.startswith('/git/blobs/'):
                sha = path.rsplit('/', 1)[1]
                if sha in self.blobs and not self.hidden:
                    data = self.blobs[sha]
                    result = {'sha': sha, 'encoding': 'base64', 'content': base64.b64encode(data).decode(), 'size': len(data)}
            elif path == '/pulls':
                branch = parse_qs(route.query).get('head', [''])[0].split(':', 1)[-1]
                result = [value for value in self.pulls.values() if value['head']['ref'] == branch]
            elif path.startswith('/pulls/'):
                result = self.pulls.get(int(path.rsplit('/', 1)[1]))
        elif path == '/git/blobs':
            data = base64.b64decode(body['content'])
            sha = self.m.git_hash('blob', data)
            self.blobs[sha] = data
            result = {'sha': sha}
        elif path == '/git/trees':
            leaves = deepcopy(self.trees[body['base_tree']])
            for item in body['tree']:
                if item['sha'] is None:
                    leaves.pop(item['path'], None)
                else:
                    leaves[item['path']] = {key: item[key] for key in ('sha', 'mode', 'type')}
            sha = self.m._tree_hash(leaves)
            self.trees[sha] = leaves
            result = {'sha': sha}
        elif path == '/git/commits':
            sha = self.m._commit_hash(body)
            result = {'sha': sha, 'tree': {'sha': body['tree']},
                      'parents': [{'sha': value} for value in body['parents']],
                      'message': body['message'], 'author': body['author'], 'committer': body['committer']}
            self.commits[sha] = deepcopy(result)
        elif path == '/git/refs':
            name = body['ref'][len('refs/heads/'):]
            if name not in self.refs:
                self.refs[name] = body['sha']
                result = {'ref': body['ref'], 'object': {'sha': body['sha'], 'type': 'commit'}}
        elif path == '/pulls':
            number = len(self.pulls) + 1
            result = {**body, 'number': number, 'html_url': self.repository + '/pull/' + str(number),
                      'state': 'open', 'head': {'sha': self.refs[body['head']], 'ref': body['head'],
                                              'repo': {'full_name': self.name}},
                      'base': {'sha': self.base, 'ref': body['base'], 'repo': {'full_name': self.name}}}
            self.pulls[number] = deepcopy(result)
        if method == 'POST' and self.drop == path:
            self.drop = None
            raise self.m.RemoteUnknown('Controlled stored mutation with lost acknowledgement')
        return deepcopy(result)


class Client:
    def __init__(self, remote):
        self.remote = remote

    async def request(self, method, suffix, body=None):
        return await self.remote.request(method, suffix, body)

    async def close(self):
        pass


class ContinuationGuards(base.OriginalDeliveryContinuation):
    async def asyncSetUp(self):
        for name in ('connect', 'connect_ex'):
            guard = patch.object(socket.socket, name, side_effect=AssertionError('Network disabled in delivery regression'))
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS disabled in delivery regression'))
        guard.start()
        self.addCleanup(guard.stop)
        replacement = patch.object(fixture_delivery, 'GitHubFixture', MemoryGit)
        replacement.start()
        self.addCleanup(replacement.stop)
        original_seed = fixture_delivery.Delivery.seed_test
        async def seed_two_files(fixture, *args, **kwargs):
            (fixture.repo / 'src/helper.py').write_bytes(b'VALUE = 1\n')
            await original_seed(fixture, *args, **kwargs)
        seed = patch.object(fixture_delivery.Delivery, 'seed_test', seed_two_files)
        seed.start()
        self.addCleanup(seed.stop)
        await super().asyncSetUp()
        self.continuation = self.f.load('agentic_delivery_continuation')
        secret = patch.object(self.f.sessions, 'edge_secret', return_value='synthetic-fixture-authenticated-edge-key-0001')
        secret.start()
        self.addCleanup(secret.stop)
        factory = patch.object(self.m, 'github_client', side_effect=lambda repository: Client(self.f.remote))
        factory.start()
        self.addCleanup(factory.stop)

    async def test_existing_commit_readback_missing_one_final_LF_keeps_exact_original_commit(self):
        await self.current_chain()
        self.f.remote.normalize_message = True
        before = deepcopy(self.original_steps)
        ready = await self.resume()
        self.assertEqual(ready['state'], 'ready_for_review')
        with self.m._db(self.path) as db:
            for step in before:
                self.assertEqual(dict(db.execute(
                    'SELECT * FROM governance_delivery_step WHERE invocation_id=? AND step_key=?',
                    (step['invocation_id'], step['step_key'])).fetchone()), step)
        self.assertEqual(self.f.remote.posts().count('/git/commits'), 1)
        self.assertEqual(self.f.remote.posts().count('/git/refs'), 1)

    async def test_status_does_not_copy_evidence_or_write_continuation_receipt_after_completed_origin(self):
        await self.current_chain()
        with patch.object(self.m, '_record_continuation', side_effect=RuntimeError('Crash before current import')):
            with self.assertRaisesRegex(RuntimeError, 'Crash before current import'):
                await self.resume()
        root = self.m.artifact_directory(self.current, self.f.config)
        before_files = sorted(str(path.relative_to(root)) for path in root.rglob('*')) if root.exists() else []
        with self.m._db(self.path) as db:
            before_events = [tuple(value) for value in db.execute(
                'SELECT * FROM governance_event WHERE invocation_id=?', (self.current['invocation_id'],))]
            before_receipt = [tuple(value) for value in db.execute(
                'SELECT * FROM governance_developer_delivery WHERE invocation_id=?', (self.current['invocation_id'],))]
        posts = self.f.remote.posts().copy()
        status = await self.status()
        self.assertEqual(status['next_operation'], 'repository.deliver')
        self.assertEqual(status['arguments'], {'test_call_ids': ['fixture-test-call-001']})
        self.assertEqual(self.f.remote.posts(), posts)
        self.assertEqual(sorted(str(path.relative_to(root)) for path in root.rglob('*')) if root.exists() else [], before_files)
        with self.m._db(self.path) as db:
            self.assertEqual([tuple(value) for value in db.execute(
                'SELECT * FROM governance_event WHERE invocation_id=?', (self.current['invocation_id'],))], before_events)
            self.assertEqual([tuple(value) for value in db.execute(
                'SELECT * FROM governance_developer_delivery WHERE invocation_id=?', (self.current['invocation_id'],))], before_receipt)

    async def open_gateway(self):
        """Actual proof/admission/call path, with only native boundary readbacks controlled."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from test_agentic_gateway_sessions import Gateway
        sessions = self.f.sessions
        self.f.config['agentic_action_gateway'] = {'owners': {'owner-fixture': {
            'account_id': 'a' * 64, 'dot_id': 'dot-fixture', 'mcp_connection_hash': 'b' * 64}}}
        primary = self.f.load('abitdot_primary')
        scope_owner = SimpleNamespace(
            packet=lambda: deepcopy(self.current_packet), primary=primary,
            bridge_module=self.b, users=self.f.users)
        scope = Gateway.scope(scope_owner, self.current)
        bridge = self.f.bridge
        bridge.packet = AsyncMock(side_effect=lambda *args: deepcopy(self.current_packet))
        bridge.gateway_boundary = AsyncMock(side_effect=lambda value: {
            'schema_version': 1, 'project_id': value['project_id'], 'task_id': value['task_id'],
            'role': value['role'], 'agent_id': value['agent_id'], 'boundary': sessions.BOUNDARY,
            'managed_resource_guards': True, 'pre_filesystem_file_guard': True,
            'ordinary_owner_plugins_fenced': False})
        status_write = self.f.load('erp_status_write')
        status_write.LOCK_ROOT = Path(self.f.temp.name) / 'task-locks'
        maintenance = self.f.load('agentic_maintenance')
        maintenance.require_active('governed', self.current['invocation_id'],
                                   db_path=maintenance.path(Path(self.path).parent))
        # Import-loader fixtures may have a prior Operation class instance.
        # Reconstruct only the same registered fields and adapt its capability
        # to this fixture's preexisting immutable inspect_infrastructure grant.
        registry = {}
        for name in ('repository.deliver', 'repository.delivery_status'):
            value = self.f.ops.registry('developer')[name]
            self.assertTrue(value.requires_authority_callback)
            registry[name] = sessions.Operation(
                'inspect_infrastructure', value.roles, value.handler,
                value.read_only, value.requires_authority_callback, value.validate_arguments)
        kwargs = dict(bridge=bridge, project_gate=self.f.gate, config=self.f.config,
                      registry=registry, db_path=self.path)
        # The retained completed-test fixture predates actual transport setup.
        # Move that synthetic prior receipt into this real admitted original
        # session before any delivery journal exists; no new tests are claimed.
        prior_calls = []
        with sessions.connect(self.path) as db:
            seed = db.execute("SELECT * FROM governance_gateway_session WHERE invocation_id=?",
                              (self.current['invocation_id'],)).fetchone()
            if seed and seed['session_id'] == 'fixture-session-001':
                self.assertEqual(self.current['invocation_id'], self.original['invocation_id'])
                prior_calls = [list(value) for value in db.execute(
                    'SELECT * FROM governance_gateway_call WHERE session_id=?', (seed['session_id'],))]
                db.execute('DELETE FROM governance_gateway_call WHERE session_id=?', (seed['session_id'],))
                db.execute('DELETE FROM governance_gateway_session WHERE session_id=?', (seed['session_id'],))
                db.commit()
        initial = await sessions.proof(scope, **kwargs)
        final = await sessions.proof(scope, **kwargs)
        await sessions.admit({'protocol_version': 1, 'scope': scope,
                              'preflight_proof': initial, 'submission_proof': final}, **kwargs)
        with sessions.connect(self.path) as db:
            for value in prior_calls:
                value[0] = final['gateway_session_id']
                db.execute('INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)', value)
            db.commit()
        return {'kwargs': kwargs, 'session_id': final['gateway_session_id']}

    async def gateway_call(self, gateway, operation, arguments, ident):
        body = {'connection_hash': 'b' * 64, 'client_session_hash': 'c' * 64,
                'session_id': gateway['session_id'], 'call_id': ident,
                'operation': operation, 'arguments': arguments}
        secret = 'synthetic-fixture-authenticated-edge-key-0001'
        self.f.sessions.verify_edge(body, self.f.sessions.edge_headers(body, secret), secret)
        return await self.f.sessions.call(body, **gateway['kwargs'])

    async def advance_checkpoint(self, row, packet, *, outcome='continue', evidence=None, artifacts=None, next_packet=None):
        """Exercise real published-source validation, advance and deterministic _resume."""
        from unittest.mock import AsyncMock
        result = {'schema_version': 1, 'invocation_id': row['invocation_id'],
                  'actor': deepcopy(packet['binding']), 'project_id': row['erp_project'],
                  'task_id': row['erp_task'], 'task_revision': packet['task_revision'],
                  'work_packet_digest': packet['content_sha256'], 'outcome': outcome,
                  'summary': 'Continue only the exact frozen original delivery.',
                  'test_results': [], 'evidence': evidence or [], 'implementation_invocation_id': None,
                  'implementation_agent_id': None, 'defects': []}
        event = {'schema_version': 1, 'event_id': 'agov-result-' + self.b.digest([row['invocation_id'], result])[:48],
                 'type': 'run.checkpoint' if outcome == 'continue' else 'run.blocked',
                 'project_id': row['erp_project'], 'task_id': row['erp_task'], 'run_id': row['automation_run_id'],
                 'invocation_id': row['invocation_id'], 'role': 'developer', 'agent_id': row['agent_id'],
                 'task_revision': packet['task_revision'], 'work_packet_digest': packet['content_sha256'],
                 'result_contract': result, 'artifacts': artifacts or [], 'acceptance_passed': False}
        receipt = {'event_id': event['event_id'], 'task_id': row['erp_task'], 'run_id': row['automation_run_id'],
                   'modified': 'fixture-publication-' + row['invocation_id'], 'durable': True,
                   'status': 'Working' if outcome == 'continue' else 'Blocked'}
        self.store.save_result(row['invocation_id'], row['automation_run_id'], result, event, self.path)
        self.g.finish_execution(row['invocation_id'], row['automation_run_id'], row['lease_token'],
                                'execution_finished', db_path=self.path)
        self.store.publication_receipt(row['invocation_id'], receipt, self.path)
        dispatch = AsyncMock()
        with patch.object(self.orchestrator, 'scan_project', AsyncMock()), patch.object(
                self.f.bridge, 'packet', AsyncMock(return_value=deepcopy(next_packet or packet))):
            await self.orchestrator.advance(
                row['invocation_id'], receipt, config=self.f.config, project_gate=self.f.gate,
                dispatch=dispatch, db_path=self.path, bridge=self.f.bridge)
        dispatch.assert_awaited_once()
        ident = dispatch.await_args.args[0]
        run = self.b.stable_run_id(ident)
        fresh = self.g.claim_invocation(ident, run, gate=self.f.gate_value, db_path=self.path)
        self.g.mark_submitted(ident, run, fresh['lease_token'], gate=self.f.gate_value, db_path=self.path)
        return self.g.find(ident, self.path, include_private=True)

    def steps(self):
        with self.m._db(self.path) as db:
            return [dict(value) for value in db.execute(
                'SELECT * FROM governance_delivery_step ORDER BY invocation_id,step_key')]

    async def test_actual_gateway_lifecycle_duplicate_calls_and_old_receipts_preserve_original_ids(self):
        self.current, self.current_packet = self.original, deepcopy(self.original_packet)
        old_gateway = await self.open_gateway()
        self.f.remote.drop = '/git/commits'
        pending = await self.gateway_call(old_gateway, 'repository.deliver',
                                          {'test_call_ids': ['fixture-test-call-001']}, 'original-delivery-call')
        self.assertEqual(pending['state'], 'completed')
        self.assertEqual(pending['result']['state'], 'pending_reconciliation')
        status = await self.gateway_call(old_gateway, 'repository.delivery_status', {}, 'original-status-call')
        self.assertEqual(status['result']['next_operation'], 'repository.deliver')
        frozen_steps = self.steps()
        first = await self.advance_checkpoint(self.original, self.original_packet)
        self.current = first
        self.current_packet = deepcopy(self.original_packet)
        # The actual server continuation advances again, followed by the exact
        # one-time blocked platform-delivery successor.
        second = await self.advance_checkpoint(first, self.current_packet)
        self.current = second
        self.current = await self.advance_checkpoint(second, self.current_packet, outcome='blocked')
        self.f.row, self.f.packet_value = self.current, self.current_packet
        current_gateway = await self.open_gateway()
        before = self.f.remote.posts().copy()
        status = await self.gateway_call(current_gateway, 'repository.delivery_status', {}, 'successor-status-call')
        self.assertEqual(self.f.remote.posts(), before)
        self.assertEqual(status['result']['invocation_id'], self.current['invocation_id'])
        self.assertEqual(status['result']['original_invocation_id'], self.original['invocation_id'])
        replies = await asyncio.gather(*[
            self.gateway_call(current_gateway, 'repository.deliver', status['result']['arguments'],
                              'successor-delivery-call') for _ in range(2)])
        self.assertEqual(sum(value['replayed'] for value in replies), 1)
        self.assertEqual(replies[0]['result'], replies[1]['result'])
        ready = replies[0]['result']
        self.assertFalse(ready['delivery_provenance']['original_tests_reexecuted'])
        await self.m.assert_gateway_result(self.current, self.current_packet, self.contract(ready),
                                           self.f.config, self.path)
        for step in frozen_steps:
            self.assertIn(step, self.steps())
        self.assertEqual(self.f.remote.posts().count('/git/commits'), 1)
        self.assertEqual(self.f.remote.posts().count('/git/refs'), 1)
        self.assertEqual(self.f.remote.posts().count('/pulls'), 1)
        replay = await self.gateway_call(old_gateway, 'repository.deliver',
                                         {'test_call_ids': ['fixture-test-call-001']}, 'original-delivery-call')
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['result'], pending['result'])
        with self.m._db(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM governance_gateway_call c JOIN governance_gateway_session s "
                "ON s.session_id=c.session_id WHERE s.invocation_id=? AND c.operation='repository.test'",
                (self.current['invocation_id'],)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM governance_event WHERE invocation_id=? AND kind='delivery_continuation'",
                (self.current['invocation_id'],)).fetchone()[0], 1)

        # Stop keeps retained calls readable while rejecting new execution.
        self.f.controls.task_control(self.current['erp_project'], self.current['erp_task'], 'stop',
                                     actor='fixture-owner', db_path=self.path)
        posts = self.f.remote.posts().copy()
        cached = await self.gateway_call(current_gateway, 'repository.deliver', status['result']['arguments'],
                                         'successor-delivery-call')
        self.assertTrue(cached['replayed'])
        self.assertEqual(cached['result'], ready)
        with self.assertRaises(ValueError):
            await self.gateway_call(current_gateway, 'repository.delivery_status', {}, 'new-after-stop')
        self.assertEqual(self.f.remote.posts(), posts)

    async def test_revocation_before_new_intent_does_not_poison_never_sent_reference(self):
        await self.current_chain()
        before, posts = self.steps(), self.f.remote.posts().copy()
        checks = 0
        async def check():
            nonlocal checks
            checks += 1
            if checks == 2:
                raise ValueError('Current authority revoked before intent')
            return await self.authority()
        with self.assertRaisesRegex(ValueError, 'revoked before intent'):
            await self.m.developer_deliver(
                {'test_call_ids': ['fixture-test-call-001']}, row=self.current, packet=self.current_packet,
                config=self.f.config, db_path=self.path, check_authority=check)
        self.assertEqual(self.steps(), before)
        self.assertEqual(self.f.remote.posts(), posts)
        ready = await self.resume()
        self.assertEqual(ready['state'], 'ready_for_review')

    async def test_revocation_between_new_steps_stops_before_PR_intent_and_current_import(self):
        await self.current_chain()
        async def check():
            if '/git/refs' in self.f.remote.posts():
                raise ValueError('Current authority revoked after reference')
            return await self.authority()
        with self.assertRaisesRegex(ValueError, 'revoked after reference'):
            await self.m.developer_deliver(
                {'test_call_ids': ['fixture-test-call-001']}, row=self.current, packet=self.current_packet,
                config=self.f.config, db_path=self.path, check_authority=check)
        self.assertNotIn('pull_request', {value['step_key'] for value in self.steps()})
        self.assertEqual(self.f.remote.posts().count('/git/refs'), 1)
        self.assertEqual(self.f.remote.posts().count('/pulls'), 0)
        self.assertFalse(self.m.artifact_directory(self.current, self.f.config).exists())

    async def test_unknown_original_step_never_posts_under_successor_or_fresh_call_id(self):
        self.f.remote.discard = True
        held = await self.f.deliver()
        self.assertEqual(held['state'], 'pending_reconciliation')
        first = await self.continue_original(self.original, self.original_packet)
        self.current, self.current_packet = first, deepcopy(self.original_packet)
        self.f.row, self.f.packet_value = first, self.current_packet
        self.f.remote.discard = False
        gateway = await self.open_gateway()
        before = self.f.remote.posts().copy()
        for ident in ('one-unknown-status', 'two-unknown-status'):
            response = await self.gateway_call(gateway, 'repository.delivery_status', {}, ident)
            self.assertEqual(response['result']['state'], 'pending_reconciliation')
            self.assertEqual(response['result']['invocation_id'], self.current['invocation_id'])
        response = await self.gateway_call(gateway, 'repository.deliver',
                                           {'test_call_ids': ['fixture-test-call-001']}, 'fresh-delivery-call')
        self.assertEqual(response['result']['state'], 'pending_reconciliation')
        self.assertEqual(self.f.remote.posts(), before)

    async def test_commit_readback_rejects_all_other_message_or_Git_identity_changes(self):
        await self.current_chain()
        commit = next(value for value in self.original_steps if value['step_key'] == 'commit')
        body = json.loads(commit['request_json'])['body']
        result = json.loads(commit['result_json'])
        original = deepcopy(self.f.remote.commits[result['sha']])
        for field, replacement in [
            ('message', body['message'].rstrip() + ' changed'),
            ('message', body['message'] + '\n'),
            ('sha', 'f' * 40), ('tree', {'sha': 'f' * 40}), ('parents', [{'sha': 'f' * 40}])]:
            self.f.remote.commits[result['sha']] = {**original, field: replacement}
            with self.subTest(field=field, replacement=replacement), self.assertRaises(self.m.RemoteUnknown):
                await self.m._commit_result(Client(self.f.remote), result, body)
        self.f.remote.commits[result['sha']] = original
        with self.assertRaises(self.m.RemoteUnknown):
            await self.m._commit_result(Client(self.f.remote), {'sha': 'e' * 40}, body)

    async def test_copied_evidence_tamper_fails_actual_readiness_and_QA_before_remote_reads(self):
        await self.current_chain()
        ready = await self.resume()
        contract = self.contract(ready)
        qa, qa_packet = self.f.qa_context(ready['delivery'])
        target = self.m.artifact_directory(self.current, self.f.config) / ready['evidence'][0]['uri'].split('://')[1]
        target.write_bytes(b'Changed copied evidence\n')
        before = deepcopy(self.f.remote.requests)
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(self.current, self.current_packet, contract, self.f.config, self.path)
        with self.assertRaises(ValueError):
            await self.m.qa_verify_delivery({}, row=qa, packet=qa_packet, config=self.f.config, db_path=self.path)
        self.assertEqual(self.f.remote.requests, before)

    async def test_original_test_receipt_change_fails_readiness_even_when_copied_bytes_are_unchanged(self):
        await self.current_chain()
        ready = await self.resume()
        with self.f.sessions.connect(self.path) as db:
            value = json.loads(db.execute(
                "SELECT result_json FROM governance_gateway_call WHERE call_id='fixture-test-call-001'").fetchone()[0])
            value['exit_code'] = 1
            db.execute("UPDATE governance_gateway_call SET result_json=? WHERE call_id='fixture-test-call-001'",
                       (self.b.canonical(value),))
            db.commit()
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(self.current, self.current_packet, self.contract(ready),
                                               self.f.config, self.path)

    async def test_current_receipt_and_audit_commit_atomically_and_exact_orphan_files_recover(self):
        await self.current_chain()
        original_event = self.continuation.ledger._event
        def fail_current(db, project, invocation, kind, actor, payload):
            if kind == 'delivery_continuation':
                raise RuntimeError('Controlled atomic audit failure')
            return original_event(db, project, invocation, kind, actor, payload)
        with patch.object(self.continuation.ledger, '_event', side_effect=fail_current):
            with self.assertRaisesRegex(RuntimeError, 'atomic audit failure'):
                await self.resume()
        with self.m._db(self.path) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM governance_developer_delivery WHERE invocation_id=?',
                                        (self.current['invocation_id'],)).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM governance_event WHERE invocation_id=? AND kind='delivery_continuation'",
                (self.current['invocation_id'],)).fetchone())
        self.assertTrue(self.m.artifact_directory(self.current, self.f.config).exists())
        posts = self.f.remote.posts().copy()
        ready = await self.resume()
        self.assertEqual(self.f.remote.posts(), posts)
        self.assertEqual(await self.resume(), ready)

    async def test_lineage_rejects_missing_server_edge_cycle_ambiguous_journal_and_backlog_drift(self):
        await self.current_chain()
        before = self.f.remote.posts().copy()
        with self.g.connect(self.path) as db:
            unproved = {**self.current, 'invocation_id': 'unproved-successor-fixture',
                        'automation_run_id': self.b.stable_run_id('unproved-successor-fixture')}
            columns = list(unproved)
            db.execute('INSERT INTO governance_invocation (' + ','.join(columns) + ') VALUES (' +
                       ','.join('?' for _ in columns) + ')', tuple(unproved.values()))
            db.commit()
        with self.assertRaisesRegex(ValueError, 'one server lifecycle edge'):
            self.m.original_delivery(unproved, self.current_packet, self.f.config, self.path)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',
                       (self.current['invocation_id'], self.original['invocation_id']))
            db.commit()
        with self.assertRaises(ValueError):
            await self.status()
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET source_invocation_id=NULL WHERE invocation_id=?',
                       (self.original['invocation_id'],))
            origin = db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                                (self.original['invocation_id'],)).fetchone()
            values = list(origin)
            values[0] = self.current['invocation_id']
            db.execute('INSERT INTO governance_developer_delivery VALUES(?,?,?,?,?,?,?)', values)
            db.commit()
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            await self.status()
        self.assertEqual(self.f.remote.posts(), before)

    async def test_unaudited_added_attachment_or_changed_policy_is_denied_before_intents(self):
        await self.current_chain()
        before = self.steps()
        modified = deepcopy(self.current_packet)
        modified['attachments'] = [{'id': 'unpublished-file', 'name': 'unapproved.txt',
                                    'is_private': True, 'content_hash': 'a' * 32}]
        modified['content_sha256'] = self.b.packet_digest(modified)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                       (self.b.canonical(modified), self.b.digest(modified), self.current['invocation_id']))
            db.commit()
        fresh = self.g.find(self.current['invocation_id'], self.path, include_private=True)
        with self.assertRaises(ValueError):
            self.m.original_delivery(fresh, modified, self.f.config, self.path)
        self.assertEqual(self.steps(), before)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                       (self.b.canonical(self.current_packet), self.b.digest(self.current_packet), self.current['invocation_id']))
            db.commit()
        self.f.profile['tests']['unit'] = ['python3', '-m', 'unittest', 'different']
        with self.assertRaises(ValueError):
            await self.resume()
        self.assertEqual(self.steps(), before)

    async def test_published_attachment_and_backlog_readback_deltas_keep_exact_original_bytes(self):
        await self.published_attachment_flow(True)

    async def test_native_integer_private_attachment_keeps_exact_original_file_provenance(self):
        await self.published_attachment_flow(1)

    async def published_attachment_flow(self, private):
        from open_webui.utils.agentic_contracts.validator import validate_work_packet
        original = deepcopy(self.original_packet)
        task = {'task_id': original['task_id'], 'task_revision': 'a' * 64, 'source_modified': 'old',
                'subject': 'Controlled delivery', 'description': 'Frozen fixture scope', 'status': 'Open',
                'parent_task': '', 'is_group': False, 'dependencies': [], 'assignees': [],
                'owner_context_sha256': 'b' * 64, 'managed': True, 'dispatch_released': True,
                'executor_role': 'developer', 'srs_revision': original['srs']['revision'],
                'requirement_ids': original['requirement_ids'], 'acceptance_tests': 'Fixture acceptance',
                'source_run_id': '', 'priority': 'Medium', 'due_date': ''}
        original['project_backlog'] = {
            'project_id': original['project_id'], 'complete': True, 'total_count': 1, 'page_size': 50,
            'tasks': [task], 'context_excluded_task_ids': [original['task_id']],
            'snapshot_sha256': self.b.digest({'project_id': original['project_id'], 'tasks': [task]}),
            'context_sha256': self.b.digest({'project_id': original['project_id'], 'tasks': []})}
        original['content_sha256'] = self.b.packet_digest(original)
        self.assertEqual(validate_work_packet(original), [])
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                       (self.b.canonical(original), self.b.digest(original), self.original['invocation_id']))
            db.commit()
        self.original = self.g.find(self.original['invocation_id'], self.path, include_private=True)
        self.original_packet, self.f.packet_value, self.f.row = original, original, self.original
        await self.prepare_original()
        ref = self.original_input['tested']['evidence'][0]
        _, data = self.m.local_bytes(self.m.artifact_directory(self.original, self.f.config), ref['uri'])
        fid = 'FILE-EXACT-PUBLISHED-FIXTURE'
        publication = {'id': ref['id'], 'kind': ref['kind'], 'sha256': ref['sha256'],
                       'url': 'https://erp.abit.ai/private/files/fixture-log.txt?fid=' + fid}
        successor = deepcopy(original)
        successor['source_modified'] = 'next-readback'
        successor['attachments'] = [{'id': fid, 'name': 'fixture-log.txt', 'is_private': private,
                                     'content_hash': hashlib.md5(data, usedforsecurity=False).hexdigest()}]
        successor['project_backlog']['tasks'][0].update(status='Working', source_modified='next-readback')
        successor['project_backlog']['snapshot_sha256'] = self.b.digest({
            'project_id': original['project_id'], 'tasks': successor['project_backlog']['tasks']})
        successor['content_sha256'] = self.b.packet_digest(successor)
        self.assertEqual(validate_work_packet(successor), [])
        self.current = await self.advance_checkpoint(self.original, original, evidence=[ref],
                                                      artifacts=[publication], next_packet=successor)
        self.current_packet, self.f.packet_value, self.f.row = successor, successor, self.current
        for mutate in ('native-hash', 'backlog-context', 'private-false', 'private-zero'):
            changed = deepcopy(successor)
            if mutate == 'native-hash':
                changed['attachments'][0]['content_hash'] = 'f' * 32
            elif mutate == 'backlog-context':
                changed['project_backlog']['context_sha256'] = 'f' * 64
            else:
                changed['attachments'][0]['is_private'] = False if mutate == 'private-false' else 0
            changed['content_sha256'] = self.b.packet_digest(changed)
            with self.g.connect(self.path) as db:
                db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                           (self.b.canonical(changed), self.b.digest(changed), self.current['invocation_id']))
                db.commit()
            current = self.g.find(self.current['invocation_id'], self.path, include_private=True)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.m.original_delivery(current, changed, self.f.config, self.path)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                       (self.b.canonical(successor), self.b.digest(successor), self.current['invocation_id']))
            db.commit()
        status = await self.status()
        ready = await self.resume(status['arguments'])
        self.assertEqual(ready['state'], 'ready_for_review')
        self.assertFalse(ready['delivery_provenance']['original_tests_reexecuted'])

    async def test_adopted_receipt_is_rederived_from_original_on_another_valid_continuation(self):
        await self.current_chain()
        first = await self.resume()
        posts = self.f.remote.posts().copy()
        previous = self.current
        self.current = await self.continue_original(previous, self.current_packet)
        self.f.row = self.current
        status = await self.status()
        second = await self.resume(status['arguments'])
        self.assertEqual(second['original_invocation_id'], self.original['invocation_id'])
        self.assertEqual(second['delivery'], first['delivery'])
        self.assertEqual(self.f.remote.posts(), posts)
        with self.m._db(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_developer_delivery').fetchone()[0], 3)
            self.assertEqual({value[0] for value in db.execute(
                'SELECT DISTINCT invocation_id FROM governance_delivery_step')}, {self.original['invocation_id']})
        # An audit without its atomic receipt must never become independent work.
        with self.m._db(self.path) as db:
            db.execute('DELETE FROM governance_developer_delivery WHERE invocation_id=?',
                       (previous['invocation_id'],))
            db.commit()
        with self.assertRaises(ValueError):
            await self.status()

    async def test_QA_requires_own_tests_and_revalidates_original_provenance_on_pass(self):
        from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest
        await self.current_chain()
        ready = await self.resume()
        qa, packet = self.f.qa_context(ready['delivery'])
        facts = await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.f.config, db_path=self.path)
        self.assertFalse(facts['tests_complete'])
        result = {'outcome': 'pass', 'reviewed_delivery_sha256': delivery_digest(ready['delivery']),
                  'test_results': [], 'evidence': []}
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa, packet, result, self.f.config, self.path)
        await self.f.seed_qa_test(qa, packet)
        facts = await self.m.qa_verify_delivery({}, row=qa, packet=packet, config=self.f.config, db_path=self.path)
        self.assertTrue(facts['tests_complete'])
        result.update(test_results=facts['test_results'], evidence=facts['evidence'])
        await self.m.assert_gateway_result(qa, packet, result, self.f.config, self.path)
        original_log = self.m.artifact_directory(self.original, self.f.config) / ready['evidence'][0]['uri'].split('://')[1]
        original_log.write_bytes(b'Changed original observation after QA test\n')
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa, packet, result, self.f.config, self.path)

    async def test_completed_remote_PR_drift_denies_import_without_replaying_or_replacing_it(self):
        await self.current_chain()
        with patch.object(self.m, '_record_continuation', side_effect=RuntimeError('Crash before import')):
            with self.assertRaises(RuntimeError):
                await self.resume()
        posts = self.f.remote.posts().copy()
        self.f.remote.pulls[1]['head']['sha'] = 'e' * 40
        with self.assertRaises(self.m.RemoteUnknown):
            await self.resume()
        self.assertEqual(self.f.remote.posts(), posts)
        with self.m._db(self.path) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM governance_developer_delivery WHERE invocation_id=?',
                                        (self.current['invocation_id'],)).fetchone())

    async def test_status_rederives_exact_origin_after_authority_readback(self):
        await self.current_chain()
        requests = deepcopy(self.f.remote.requests)
        async def change_original():
            value = await self.authority()
            with self.m._db(self.path) as db:
                changed = deepcopy(self.original_input)
                changed['commit_date'] = '2000-01-01T00:00:00Z'
                db.execute('UPDATE governance_developer_delivery SET input_json=? WHERE invocation_id=?',
                           (self.b.canonical(changed), self.original['invocation_id']))
                db.commit()
            return value
        with self.assertRaisesRegex(ValueError, 'identity changed before status'):
            await self.m.reconcile_delivery({}, row=self.current, packet=self.current_packet,
                                             config=self.f.config, db_path=self.path,
                                             check_authority=change_original)
        self.assertEqual(self.f.remote.requests, requests)

    async def test_delivery_successor_rejects_malformed_fresh_backlog_without_consuming_fence(self):
        packet = deepcopy(self.original_packet)
        empty = self.b.digest({'project_id': packet['project_id'], 'tasks': []})
        packet['project_backlog'] = {
            'project_id': packet['project_id'], 'complete': True, 'total_count': 0, 'page_size': 50,
            'snapshot_sha256': empty, 'context_sha256': empty, 'context_excluded_task_ids': [], 'tasks': []}
        packet['content_sha256'] = self.b.packet_digest(packet)
        with self.g.connect(self.path) as db:
            db.execute('UPDATE governance_invocation SET work_packet=?,packet_hash=? WHERE invocation_id=?',
                       (self.b.canonical(packet), self.b.digest(packet), self.original['invocation_id']))
            db.commit()
        self.original = self.g.find(self.original['invocation_id'], self.path, include_private=True)
        self.original_packet, self.f.packet_value, self.f.row = packet, packet, self.original
        await self.prepare_original()
        first = await self.continue_original(self.original, packet)
        bad = deepcopy(packet)
        bad['project_backlog']['snapshot_sha256'] = 'f' * 64
        bad['content_sha256'] = self.b.packet_digest(bad)
        with self.g.connect(self.path) as db:
            count = db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'fresh WorkPacket'):
            await self.advance_checkpoint(first, packet, outcome='blocked', next_packet=bad)
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0], count)
            self.assertEqual(db.execute('SELECT state FROM governance_invocation WHERE invocation_id=?',
                                        (first['invocation_id'],)).fetchone()[0], 'execution_finished')
            self.assertEqual(db.execute('SELECT invocation_id FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
                                        (first['erp_project'], first['erp_task'])).fetchone()[0], first['invocation_id'])
            self.assertEqual(db.execute(
                "SELECT count(*) FROM governance_event WHERE kind='delivery_checkpoint_resumed'").fetchone()[0], 0)

    async def repaired_delivery_flow(self, *, existing_delivery, kind):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        if existing_delivery:
            ready = await self.f.deliver()
            self.assertEqual(ready['state'], 'ready_for_review')
        with self.m._db(self.path) as db:
            prior = db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                               (self.original['invocation_id'],)).fetchone()
            prior = dict(prior) if prior else None
        task, ident = 'TASK-fixture-repair-child', 'fixture-repair-child-implementation'
        role = 'it_manager' if kind == 'action' else 'developer'
        artifact = await self.f.ops.artifact_write(
            {'name': 'fixture-blocker.txt', 'content_base64': base64.b64encode(b'Controlled blocked dependency\n').decode()},
            row=self.original, config=self.f.config)
        evidence = {'id': 'EV-REPAIR', 'kind': 'checkpoint', 'uri': artifact['uri'],
                    'sha256': artifact['sha256'], 'requirement_ids': self.original_packet['requirement_ids']}
        result = {'schema_version': 1, 'invocation_id': self.original['invocation_id'],
                  'actor': deepcopy(self.original_packet['binding']), 'project_id': self.original['erp_project'],
                  'task_id': self.original['erp_task'], 'task_revision': self.original_packet['task_revision'],
                  'work_packet_digest': self.original_packet['content_sha256'], 'outcome': 'blocked',
                  'summary': 'A controlled dependency requires independent repair.',
                  'test_results': [], 'evidence': [evidence], 'implementation_invocation_id': None,
                  'implementation_agent_id': None, 'defects': []}
        if kind == 'repair':
            result['defects'] = [{'id': 'DEFECT-CONTROLLED', 'requirement_ids': self.original_packet['requirement_ids'],
                                 'reproduction': 'Inspect the controlled dependency.',
                                 'expected': 'The dependency is independently verified.',
                                 'observed': 'A controlled fixture dependency is unavailable.', 'evidence_ids': ['EV-REPAIR']}]
        else:
            result['action_requests'] = [{'request_key': 'controlled-inspection', 'operation': 'inspect',
                                          'risk': 'routine', 'parameters': {}, 'reason': 'Read the controlled dependency.',
                                          'acceptance_criteria': ['The dependency is independently verified.']}]
        from open_webui.utils.agentic_contracts.validator import validate_result
        self.assertEqual(validate_result(result, self.original_packet), [])
        event = {'schema_version': 1, 'event_id': 'agov-result-' + self.b.digest([self.original['invocation_id'], result])[:48],
                 'type': 'run.blocked', 'project_id': self.original['erp_project'], 'task_id': self.original['erp_task'],
                 'run_id': self.original['automation_run_id'], 'invocation_id': self.original['invocation_id'],
                 'role': 'developer', 'agent_id': self.original['agent_id'],
                 'task_revision': self.original_packet['task_revision'],
                 'work_packet_digest': self.original_packet['content_sha256'],
                 'result_contract': result, 'artifacts': [], 'acceptance_passed': False}
        self.store.save_result(self.original['invocation_id'], self.original['automation_run_id'], result, event, self.path)
        self.g.finish_execution(self.original['invocation_id'], self.original['automation_run_id'],
                                self.original['lease_token'], 'execution_finished', db_path=self.path)
        self.store.publication_receipt(self.original['invocation_id'], {
            'event_id': event['event_id'], 'task_id': self.original['erp_task'],
            'run_id': self.original['automation_run_id'], 'modified': 'fixture-repair-published',
            'status': 'Blocked', 'durable': True}, self.path)
        parent = self.g.find(self.original['invocation_id'], self.path, include_private=True)
        async def native_task(doctype, name):
            self.assertEqual(doctype, 'Task')
            if name == parent['erp_task']:
                return {'custom_agov_repair_depth': 0}
            self.assertEqual(name, task)
            return {'project': parent['erp_project'], 'parent_task': parent['erp_task'],
                    'custom_agov_source_run': parent['automation_run_id']}
        bridge = SimpleNamespace(erp=SimpleNamespace(get=AsyncMock(side_effect=native_task)),
                                 create_task=AsyncMock(return_value={'task_id': task}))
        children = await self.orchestrator._repair_children(
            parent, result, self.original_packet, bridge, self.path, self.f.gate_value)
        self.assertEqual(children, [task])
        child_packet = self.f.packet(task=task, role=role)
        self.g.enqueue(invocation_id=ident, project=parent['erp_project'], task=task,
                       from_role='project_manager', target_role=role,
                       task_revision=child_packet['task_revision'], context='Synthetic verified repair dependency',
                       work_packet=child_packet, gate=self.f.gate_value, actor='fixture-server', db_path=self.path)
        child_run = self.b.stable_run_id(ident)
        child = self.g.claim_invocation(ident, child_run, gate=self.f.gate_value, db_path=self.path)
        self.g.mark_submitted(ident, child_run, child['lease_token'], gate=self.f.gate_value, db_path=self.path)
        self.g.finish_execution(ident, child_run, child['lease_token'], 'execution_finished', db_path=self.path)
        qa_id, qa_packet = 'fixture-repair-child-independent-qa', self.f.packet(task=task, role='qa')
        self.g.handoff(source_invocation_id=ident, source_run_id=child_run, source_lease_token=child['lease_token'],
                       invocation_id=qa_id, project=child['erp_project'], task=task, from_role=role,
                       target_role='qa', task_revision=qa_packet['task_revision'],
                       context='Independent synthetic child verification', work_packet=qa_packet,
                       gate=self.f.gate_value, actor='fixture-server', db_path=self.path)
        qa_run = self.b.stable_run_id(qa_id)
        qa = self.g.claim_invocation(qa_id, qa_run, gate=self.f.gate_value, db_path=self.path)
        self.g.mark_submitted(qa_id, qa_run, qa['lease_token'], gate=self.f.gate_value, db_path=self.path)
        self.g.finish_execution(qa_id, qa_run, qa['lease_token'], 'execution_finished',
                                qa_verdict='pass', db_path=self.path)
        self.g.verify_completion(ident, qa_id, 'a' * 64, 'b' * 64, 'fixture-publisher', db_path=self.path)
        self.orchestrator._wait(parent, kind, children, self.original_packet, self.path)
        successor, created = self.orchestrator._resume(
            parent, self.original_packet, self.f.gate_value, self.path, children, kind)
        self.assertTrue(created)
        run = self.b.stable_run_id(successor['invocation_id'])
        current = self.g.claim_invocation(successor['invocation_id'], run, gate=self.f.gate_value, db_path=self.path)
        self.g.mark_submitted(current['invocation_id'], run, current['lease_token'],
                              gate=self.f.gate_value, db_path=self.path)
        self.current = self.g.find(current['invocation_id'], self.path, include_private=True)
        self.current_packet = deepcopy(self.original_packet)
        self.f.row, self.f.packet_value = self.current, self.current_packet
        self.assertIsNone(self.m.original_delivery(self.current, self.current_packet, self.f.config, self.path))
        posts = self.f.remote.posts().copy()
        with self.assertRaises(ValueError):
            await self.resume({'test_call_ids': ['fixture-test-call-001']})
        self.assertEqual(self.f.remote.posts(), posts)
        (self.f.repo / 'src/app.py').write_bytes(b'print("independently corrected implementation")\n')
        with self.f.sessions.connect(self.path) as db:
            db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',
                       ('fixture-repair-current-session', self.current['invocation_id'], '{}', '{}',
                        'a' * 64, 'b' * 64, 'admitted', 1))
            db.commit()
        await self.f.seed_test('fixture-current-repair-test')
        with self.f.sessions.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET session_id='fixture-repair-current-session' "
                       "WHERE call_id='fixture-current-repair-test'")
            db.commit()
        ready = await self.resume({'test_call_ids': ['fixture-current-repair-test']})
        self.assertEqual(ready['state'], 'ready_for_review')
        self.assertNotIn('delivery_provenance', ready)
        fresh_origin = self.current
        posts = self.f.remote.posts().copy()
        self.current = await self.continue_original(fresh_origin, self.current_packet)
        self.f.row = self.current
        owner = self.m.original_delivery(self.current, self.current_packet, self.f.config, self.path)
        self.assertEqual(owner['row']['invocation_id'], fresh_origin['invocation_id'])
        status = await self.status()
        self.assertEqual(status['arguments'], {'test_call_ids': ['fixture-current-repair-test']})
        continued = await self.resume(status['arguments'])
        self.assertEqual(continued['original_invocation_id'], fresh_origin['invocation_id'])
        self.assertEqual(continued['delivery'], ready['delivery'])
        self.assertEqual(self.f.remote.posts(), posts)
        with self.m._db(self.path) as db:
            old = db.execute('SELECT * FROM governance_developer_delivery WHERE invocation_id=?',
                             (self.original['invocation_id'],)).fetchone()
            self.assertEqual(dict(old) if old else None, prior)
            self.assertEqual({value[0] for value in db.execute(
                'SELECT DISTINCT invocation_id FROM governance_delivery_step')},
                {fresh_origin['invocation_id']} | ({self.original['invocation_id']} if existing_delivery else set()))
        # A forged/missing wait cannot turn a repair pointer into permission to
        # borrow an older journal, including on a later ordinary continuation.
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_lifecycle_wait SET successor='unproved-successor' WHERE source=?",
                       (parent['invocation_id'],))
            db.commit()
        with self.assertRaises(ValueError):
            await self.status()
        self.assertEqual(self.f.remote.posts(), posts)

    async def test_no_original_journal_allows_real_verified_dependency_repair_to_start_own_delivery(self):
        await self.repaired_delivery_flow(existing_delivery=False, kind='repair')

    async def test_completed_old_delivery_is_not_borrowed_across_verified_repair_and_next_continue(self):
        await self.repaired_delivery_flow(existing_delivery=True, kind='repair')

    async def test_completed_old_delivery_is_not_borrowed_across_verified_action_and_next_continue(self):
        await self.repaired_delivery_flow(existing_delivery=True, kind='action')

    async def test_alias_receipt_project_and_task_metadata_must_match_current_binding(self):
        await self.current_chain()
        ready = await self.resume()
        for column, changed, original in [
                ('project_id', 'PROJ-FOREIGN', self.current['erp_project']),
                ('task_id', 'TASK-FOREIGN', self.current['erp_task'])]:
            with self.m._db(self.path) as db:
                db.execute('UPDATE governance_developer_delivery SET ' + column + '=? WHERE invocation_id=?',
                           (changed, self.current['invocation_id']))
                db.commit()
            with self.subTest(column=column), self.assertRaises(ValueError):
                await self.m.assert_gateway_result(self.current, self.current_packet, self.contract(ready),
                                                   self.f.config, self.path)
            with self.m._db(self.path) as db:
                db.execute('UPDATE governance_developer_delivery SET ' + column + '=? WHERE invocation_id=?',
                           (original, self.current['invocation_id']))
                db.commit()


class OriginalDeliveryCompatibility(fixture_delivery.Delivery):
    """Run the existing original-delivery assertions without a loopback server."""
    async def asyncSetUp(self):
        for name in ('connect', 'connect_ex'):
            value = patch.object(socket.socket, name, side_effect=AssertionError('Network disabled'))
            value.start()
            self.addCleanup(value.stop)
        value = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS disabled'))
        value.start()
        self.addCleanup(value.stop)
        value = patch.object(fixture_delivery, 'GitHubFixture', MemoryGit)
        value.start()
        self.addCleanup(value.stop)
        await super().asyncSetUp()
        self.load('agentic_delivery_continuation')
        value = patch.object(self.m, 'github_client', side_effect=lambda repository: Client(self.remote))
        value.start()
        self.addCleanup(value.stop)



if __name__ == '__main__':
    unittest.main()

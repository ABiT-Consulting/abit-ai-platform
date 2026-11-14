"""Offline scope, controlled handoff and original-receipt recovery; no network/model."""

from copy import deepcopy
import json
import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from test_agentic_controls_events import Fixture, P


class Response:
    def __init__(self, fixture, body):
        self.status = fixture.status
        self.raw = fixture.raw
        if self.raw is None:
            self.raw = json.dumps(fixture.proof(body)).encode()
        self.content = self

    async def read(self, limit):
        return self.raw[:limit]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, fixture, **kwargs):
        self.f = fixture
        self.f.session_options.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def post(self, url, **kwargs):
        if not url.endswith('/dot/scope'):
            self.f.actual_sends += 1
            raise AssertionError('No submission endpoint is allowed')
        self.f.calls.append((url, deepcopy(kwargs)))
        return Response(self.f, kwargs['json'])


class RelayScope(Fixture):
    def setUp(self):
        super().setUp()
        self.module = self.load('agentic_relay_scope')
        self.relay = self.load('abitdot_primary_relay')
        self.primary = self.load('abitdot_primary')
        self.status = 200
        self.raw = None
        self.mutate = None
        self.calls = []
        self.session_options = []
        self.actual_sends = 0
        self.credentials = Mock(return_value=('fixture-private-auth-token', 'fixture-user'))
        self.account = AsyncMock(return_value='a' * 64)
        router = types.ModuleType('open_webui.routers.chatgpt_relay')
        router.MESSAGE_URL = 'http://127.0.0.1:8810'
        router.MESSAGE_TOKEN_FILE = '/fixed/fixture/token'
        router._credentials = self.credentials
        self.router_patch = patch.dict(sys.modules, {'open_webui.routers.chatgpt_relay': router})
        self.router_patch.start()
        self.addCleanup(self.router_patch.stop)
        self.env = patch.dict(
            os.environ,
            {
                'ABIT_DOT_PRIMARY_ENABLED': 'true',
                'ABIT_CHATGPT_DOT_OWNER_ID': 'owner-fixture',
                'ABIT_CHATGPT_DOT_URL': 'https://chatgpt.com/dots/dot-fixture-000001',
                'ABIT_DOT_PRIMARY_RELAY_URL': 'http://127.0.0.1:8810',
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.old_token = os.environ.pop('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE', None)
        if self.old_token is not None:
            self.addCleanup(lambda: os.environ.__setitem__('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE', self.old_token))
        import aiohttp

        for target, name, value in (
            (aiohttp, 'ClientSession', lambda **kwargs: Session(self, **kwargs)),
            (self.relay, 'relay_account_for_owner', self.account),
        ):
            item = patch.object(target, name, value)
            item.start()
            self.addCleanup(item.stop)

    def proof(self, body):
        now = int(time.time())
        result = {
            'protocol_version': 1,
            'scope': deepcopy(body['scope']),
            'scope_digest': body['scope_digest'],
            'enforced': True,
            'read_only': True,
            'default_deny': True,
            'enforcement_mode': 'governed_action_gateway',
            'enforcement_boundary': 'gateway_operations_and_managed_erp',
            'excluded_surfaces': ['owner_account_plugins'],
            'gateway_session_id': 'gateway-fixture-session-001',
            'enforced_tool_scopes': body['scope']['tool_scopes'].copy(),
            'external_write_allowlist': [],
            'proof_id': 'scope-fixture-proof-001',
            'issued_at': now,
            'expires_at': now + 30,
        }
        if self.mutate:
            self.mutate(result)
        return result

    async def governed(self):
        row = self.enqueue()
        packet = self.packet()
        proof = await self.check(row, packet)
        scope = proof['scope']
        envelope = self.primary.build_project_envelope(
            owner='owner-fixture',
            automation={
                'id': row['agent_id'],
                'user_id': 'owner-fixture',
                'name': 'Fixture original job',
                'data': {'prompt': 'Original private fixture task'},
                'meta': {
                    'governance_managed': True,
                    'erp_task': packet['task_id'],
                    'erp_role': packet['binding']['role'],
                    'governance_project': packet['project_id'],
                    'governance_packet_digest': scope['work_packet_digest'],
                    'governance_scope_digest': self.bridge_module.digest(scope),
                },
            },
            run_id=scope['run_id'],
            dot_id=scope['dot_id'],
        )
        final = deepcopy(proof)
        final['proof_id'] = 'submission-fixture-proof-002'
        envelope['governance_scope'] = {
            'protocol_version': 1,
            'scope': deepcopy(scope),
            'preflight_proof': proof,
            'submission_proof': final,
        }
        return row, packet, envelope

    def provider(self, envelope):
        binding = envelope['governance_scope']
        scope = binding['scope']
        receipt = {
            key: scope[key]
            for key in ('owner', 'account_id', 'job_id', 'invocation_id', 'run_id', 'work_packet_digest')
        }
        receipt.update(
            protocol_version=1,
            accepted=True,
            scope_digest=self.bridge_module.digest(scope),
            preflight_proof_sha256=self.bridge_module.digest(binding['preflight_proof']),
            submission_proof_sha256=self.bridge_module.digest(binding['submission_proof']),
            proof_id=binding['submission_proof']['proof_id'],
            gateway_session_id='gateway-fixture-session-001',
            enforcement_mode='governed_action_gateway',
            enforcement_boundary='gateway_operations_and_managed_erp',
            excluded_surfaces=['owner_account_plugins'],
        )
        return {
            'dot_handoff': True,
            'account_id': 'a' * 64,
            'response': 'Original controlled reply',
            'conversation_url': 'https://chatgpt.com/c/fixture-conversation-001',
            'scope_receipt': receipt,
        }

    async def check(self, row=None, packet=None, owner='owner-fixture', **kwargs):
        row = row or self.enqueue()
        packet = packet or self.packet()
        return await self.module.require_relay_scope(row, packet, owner, db_path=self.path, **kwargs)

    async def test_valid_exact_proof_binds_stable_job_without_prompt_or_credentials_in_body(self):
        row = self.enqueue()
        packet = self.packet()
        packet['srs']['content'] = 'PRIVATE SRS MUST NOT LEAK'
        packet['instructions'] = 'PRIVATE INSTRUCTIONS'
        packet['activity'] = [{'id': 'comment-fixture', 'text': 'PRIVATE ACTIVITY'}]
        packet['content_sha256'] = self.bridge_module.packet_digest(packet)
        proof = await self.check(row, packet)
        url, args = self.calls[0]
        body = json.dumps(args['json'])
        self.assertEqual(url, 'http://127.0.0.1:8810/dot/scope')
        self.assertFalse(args['allow_redirects'])
        self.assertFalse(self.session_options[0]['trust_env'])
        self.assertEqual(proof['scope']['work_packet_digest'], self.bridge_module.packet_digest(packet))
        self.assertEqual(proof['scope']['run_id'], self.bridge_module.stable_run_id(row['invocation_id']))
        self.assertTrue(proof['scope']['job_id'].startswith('aibit_'))
        self.account.assert_awaited_once_with('owner-fixture')
        for sensitive in (
            'PRIVATE SRS',
            'PRIVATE INSTRUCTIONS',
            'PRIVATE ACTIVITY',
            'fixture-private-auth-token',
            'objective',
            'acceptance_tests',
        ):
            self.assertNotIn(sensitive, body)
        self.assertEqual(args['headers']['Authorization'], 'Bearer fixture-private-auth-token')
        self.assertEqual(self.actual_sends, 0)
        self.assertIsNone(self.g.find(row['invocation_id'], self.path)['automation_run_id'])

    async def test_unsupported_redirect_or_denied_endpoint_fails_without_handoff(self):
        row = self.enqueue()
        for status in (301, 401, 403, 404, 405, 424, 500):
            self.status = status
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check(row)
        self.assertEqual(self.actual_sends, 0)
        self.assertIsNone(self.g.find(row['invocation_id'], self.path)['automation_run_id'])

    async def test_wrong_roster_owner_or_primary_owner_is_denied_before_http(self):
        row = self.enqueue()
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row, owner='different-owner')
        os.environ['ABIT_CHATGPT_DOT_OWNER_ID'] = 'different-owner'
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.actual_sends, 0)

    async def test_wrong_account_owner_or_project_echo_is_rejected(self):
        row = self.enqueue()
        for key, value in [
            ('owner', 'other-owner'),
            ('account_id', 'b' * 64),
            ('project_id', 'PROJ-OTHER'),
            ('agent_id', 'other-agent'),
            ('repository', 'https://github.com/ABiT-Consulting/other'),
        ]:
            self.mutate = lambda proof, key=key, value=value: proof['scope'].__setitem__(key, value)
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check(row)
        self.assertEqual(self.actual_sends, 0)

    async def test_expired_overlong_future_or_boolean_timestamps_are_rejected(self):
        row = self.enqueue()
        now = int(time.time())
        for change in (
            {'expires_at': now - 1},
            {'expires_at': now + 120},
            {'issued_at': now - 10},
            {'issued_at': now + 10},
            {'expires_at': True},
        ):
            self.mutate = lambda proof, change=change: proof.update(change)
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check(row)
        self.assertEqual(self.actual_sends, 0)

    async def test_exact_scope_and_physical_default_deny_are_mandatory(self):
        row = self.enqueue()
        for change in (
            {'scope_digest': '0' * 64},
            {'enforced': False},
            {'enforced': 'true'},
            {'read_only': False},
            {'default_deny': False},
            {'enforcement_mode': 'prompt_only'},
            {'protocol_version': True},
            {'enforced_tool_scopes': ['other-scope']},
            {'external_write_allowlist': ['not-declared']},
            {'proof_id': ''},
            {'unexpected_allow_all': True},
        ):
            self.mutate = lambda proof, change=change: proof.update(change)
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check(row)
        self.assertEqual(self.actual_sends, 0)

    async def test_external_write_allowlist_cannot_duplicate_or_expand_packet_scopes(self):
        row = self.enqueue()
        self.mutate = lambda proof: proof.update(external_write_allowlist=['inspect_infrastructure'])
        await self.check(row)
        for value in (['inspect_infrastructure', 'inspect_infrastructure'], ['*'], [{}]):
            self.mutate = lambda proof, value=value: proof.update(external_write_allowlist=value)
            with self.assertRaises(self.module.CapabilityUnavailable):
                await self.check(row)

    async def test_wildcard_packet_scope_or_invalid_run_id_denied_before_relay(self):
        row = self.enqueue()
        packet = self.packet()
        packet['tool_scopes'] = ['*']
        packet['content_sha256'] = self.bridge_module.packet_digest(packet)
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row, packet)
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row, run_id='different-run')
        self.assertEqual(self.calls, [])

    async def test_arbitrary_url_or_token_file_is_never_used(self):
        row = self.enqueue()
        os.environ['ABIT_DOT_PRIMARY_RELAY_URL'] = 'https://example.invalid/leak'
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row)
        os.environ['ABIT_DOT_PRIMARY_RELAY_URL'] = 'http://127.0.0.1:8810'
        os.environ['ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE'] = '/tmp/other-token'
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row)
        self.assertEqual(self.calls, [])
        self.credentials.assert_not_called()

    async def test_missing_or_oversized_or_malformed_response_is_sanitized(self):
        row = self.enqueue()
        for raw in (b'{}', b'null', b'not-json PRIVATE RESPONSE', b'x' * (self.module.MAX_PROOF_BYTES + 1)):
            self.raw = raw
            with self.assertRaises(self.module.CapabilityUnavailable) as result:
                await self.check(row)
            self.assertNotIn('PRIVATE RESPONSE', str(result.exception))
            self.assertNotIn('fixture-private-auth-token', str(result.exception))
        self.assertEqual(self.actual_sends, 0)

    async def test_proof_is_freshly_requested_again_at_submission_boundary(self):
        row = self.enqueue()
        first = await self.check(row)
        self.mutate = lambda proof: proof.update(enforced=False)
        with self.assertRaises(self.module.CapabilityUnavailable):
            await self.check(row)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(first['scope_digest'], self.calls[1][1]['json']['scope_digest'])
        self.assertEqual(self.actual_sends, 0)

    async def test_retained_proof_is_detached_and_rebound_to_exact_original_packet(self):
        row, packet, envelope = await self.governed()
        proof = envelope['governance_scope']['preflight_proof']
        saved = self.module.proof_for_packet(proof, row, packet, 'owner-fixture', proof['scope']['run_id'])
        proof['scope']['repository'] = 'https://example.invalid/unrelated'
        self.assertEqual(saved['scope']['repository'], packet['repository'])
        with self.assertRaises(self.module.CapabilityUnavailable):
            self.module.proof_for_packet(proof, row, packet, 'owner-fixture', saved['scope']['run_id'])
        changed = deepcopy(packet)
        changed['objective'] = 'Changed business work'
        changed['content_sha256'] = self.bridge_module.packet_digest(changed)
        with self.assertRaises(self.module.CapabilityUnavailable):
            self.module.proof_for_packet(saved, row, changed, 'owner-fixture', saved['scope']['run_id'])

    async def test_handoff_proofs_are_structured_outside_model_prompt(self):
        _, _, envelope = await self.governed()
        snapshot = deepcopy(envelope)
        payload = self.relay.verified_dot_payload(envelope, 'a' * 64)
        self.assertEqual(payload['governance_scope'], envelope['governance_scope'])
        self.assertEqual(payload['job_id'], envelope['governance_scope']['scope']['job_id'])
        self.assertNotIn('submission-fixture-proof-002', payload['message'])
        self.assertNotIn('scope-fixture-proof-001', payload['message'])
        self.assertEqual(envelope, snapshot)
        payload['governance_scope']['scope']['task_id'] = 'TASK-OTHER'
        self.assertEqual(envelope, snapshot)

    async def test_absent_expired_or_cross_job_scope_never_constructs_handoff(self):
        _, _, envelope = await self.governed()
        variants = []
        stripped = deepcopy(envelope)
        stripped.pop('governance_scope')
        variants.append(stripped)
        for field, value in [
            ('run_id', 'different-run'),
            ('owner_id', 'other-owner'),
            ('automation_id', 'other-agent'),
        ]:
            altered = deepcopy(envelope)
            altered[field] = value
            variants.append(altered)
        expired = deepcopy(envelope)
        expired['governance_scope']['submission_proof']['expires_at'] = int(time.time()) - 1
        variants.append(expired)
        changed = deepcopy(envelope)
        changed['governance_scope']['submission_proof']['gateway_session_id'] = 'other-gateway-session'
        variants.append(changed)
        for altered in variants:
            with self.assertRaises(self.relay.RelayPreflightError):
                self.relay.verified_dot_payload(altered, 'a' * 64)
        self.assertEqual(self.actual_sends, 0)

    async def test_narrative_reply_without_exact_gateway_receipt_is_unknown(self):
        _, _, envelope = await self.governed()
        provider = self.provider(envelope)
        self.assertEqual(self.relay.receipt_from_relay(envelope, provider, 'a' * 64)['status'], 'accepted')
        missing = deepcopy(provider)
        missing.pop('scope_receipt')
        self.assertEqual(self.relay.receipt_from_relay(envelope, missing, 'a' * 64), {'status': 'outcome_unknown'})
        for key, value in [
            ('job_id', 'aibit_' + 'b' * 48),
            ('work_packet_digest', '0' * 64),
            ('preflight_proof_sha256', '0' * 64),
            ('submission_proof_sha256', '0' * 64),
            ('gateway_session_id', 'different-session'),
            ('accepted', False),
            ('protocol_version', True),
            ('enforcement_mode', 'scoped_tool_proxy'),
            ('excluded_surfaces', []),
        ]:
            altered = deepcopy(provider)
            altered['scope_receipt'][key] = value
            self.assertEqual(
                self.relay.receipt_from_relay(envelope, altered, 'a' * 64), {'status': 'outcome_unknown'}, key
            )

    async def test_expired_admission_proofs_allow_only_exact_original_receipt_observation(self):
        _, _, envelope = await self.governed()
        provider = self.provider(envelope)
        later = envelope['governance_scope']['submission_proof']['expires_at'] + 300
        with patch.object(self.module.time, 'time', return_value=later):
            with self.assertRaises(self.relay.RelayPreflightError):
                self.relay.verified_dot_payload(envelope, 'a' * 64)
            observed = self.relay.verified_dot_payload(envelope, 'a' * 64, admission=False)
            self.assertEqual(observed['governance_scope'], envelope['governance_scope'])
            self.assertEqual(self.relay.receipt_from_relay(envelope, provider, 'a' * 64)['status'], 'accepted')

    async def test_actual_primary_ledger_persists_receipt_and_duplicates_do_not_send(self):
        _, _, envelope = await self.governed()
        send = AsyncMock(return_value=self.relay.receipt_from_relay(envelope, self.provider(envelope), 'a' * 64))
        path = Path(self.temp.name) / 'primary.sqlite'
        first = await self.primary.deliver(envelope, send, path)
        self.assertEqual(first['state'], 'delegated')
        second = await self.primary.deliver(envelope, send, path)
        self.assertTrue(second['duplicate'])
        send.assert_awaited_once()
        with self.primary.ledger(path) as db:
            row = dict(db.execute('SELECT * FROM dot_primary_runs').fetchone())
        self.assertEqual(json.loads(row['scope_receipt_json']), self.provider(envelope)['scope_receipt'])
        self.primary.verify_saved_scope_receipt(row)
        damaged = {**row, 'response_text': 'Forged saved answer'}
        with self.assertRaises(ValueError):
            self.primary.verify_saved_scope_receipt(damaged)
        with self.assertRaises(ValueError):
            self.primary.verify_saved_scope_receipt({**row, 'scope_receipt_json': None})

    async def test_governed_accepted_adapter_without_receipt_remains_unknown_and_no_replay(self):
        _, _, envelope = await self.governed()
        outcome = self.relay.receipt_from_relay(envelope, self.provider(envelope), 'a' * 64)
        outcome.pop('scope_receipt')
        path = Path(self.temp.name) / 'missing.sqlite'
        send = AsyncMock(return_value=outcome)
        self.assertEqual((await self.primary.deliver(envelope, send, path))['state'], 'unknown')
        self.assertTrue((await self.primary.deliver(envelope, send, path))['duplicate'])
        send.assert_awaited_once()

    async def test_verified_busy_can_renew_only_same_job_and_keeps_admission_history(self):
        _, _, envelope = await self.governed()
        path = Path(self.temp.name) / 'busy.sqlite'
        outcome = {key: envelope[key] for key in ('owner_id', 'automation_id', 'run_id', 'dot_id', 'idempotency_key')}
        outcome['status'] = 'retryable_before_submit'
        send = AsyncMock(return_value=outcome)
        self.assertEqual((await self.primary.deliver(envelope, send, path))['state'], 'prepared')
        renewed = deepcopy(envelope)
        renewed['governance_scope']['submission_proof']['proof_id'] = 'renewed-submission-proof'
        changed = deepcopy(renewed)
        changed['task']['original_prompt'] = 'Different work'
        with self.assertRaises(PermissionError):
            await self.primary.deliver(changed, send, path)
        send.return_value = self.relay.receipt_from_relay(renewed, self.provider(renewed), 'a' * 64)
        self.assertEqual((await self.primary.deliver(renewed, send, path))['state'], 'delegated')
        with self.primary.ledger(path) as db:
            rows = db.execute('SELECT scope_json FROM dot_primary_scope_attempts ORDER BY rowid').fetchall()
            current = dict(db.execute('SELECT * FROM dot_primary_runs').fetchone())
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.loads(rows[0][0]), envelope['governance_scope'])
        self.assertEqual(json.loads(rows[1][0]), renewed['governance_scope'])
        self.primary.verify_saved_scope_receipt(current)

    async def test_legacy_non_governed_receipt_keeps_null_compatibility(self):
        _, _, envelope = await self.governed()
        envelope.pop('governance_scope')
        envelope['task']['meta'] = {}
        outcome = self.relay.receipt_from_relay(
            envelope,
            {
                'dot_handoff': True,
                'account_id': 'a' * 64,
                'response': 'Legacy reply',
                'conversation_url': 'https://chatgpt.com/c/legacy-fixture',
            },
            'a' * 64,
        )
        path = Path(self.temp.name) / 'legacy.sqlite'
        self.assertEqual(
            (await self.primary.deliver(envelope, AsyncMock(return_value=outcome), path))['state'], 'delegated'
        )
        rows = self.primary.read_owner_primary_runs('owner-fixture', envelope['automation_id'], path)
        self.assertIsNone(rows[0]['scope_receipt_json'])

    async def test_actual_dispatch_carries_final_guard_proof_into_original_durable_handoff(self):
        row, packet, envelope = await self.governed()
        execution = self.load('erp_dot_execution')
        execution.STATE_DIR = Path(self.temp.name)
        task = {
            'name': packet['task_id'],
            'project': P,
            'status': 'Working',
            'subject': 'Fixture work',
            'custom_agentic_managed': 1,
        }
        erp = types.SimpleNamespace(get=AsyncMock(return_value=task), assigned=AsyncMock(return_value=True))
        final = deepcopy(envelope['governance_scope']['submission_proof'])
        guard = AsyncMock(return_value=final)
        sent = []

        async def transport(value, **kwargs):
            sent.append(self.relay.verified_dot_payload(value, 'a' * 64))
            return self.relay.receipt_from_relay(value, self.provider(value), 'a' * 64)

        with (
            patch.object(execution, 'synced_task_project', AsyncMock(return_value=None)),
            patch.object(execution, 'task_context', AsyncMock(return_value={'latest_run': 'fixture-context'})),
            patch.object(self.relay, 'send_via_existing_relay', transport),
        ):
            result = await execution.dispatch_task_dot(
                erp,
                task,
                'owner-fixture',
                row['agent_id'],
                final['scope']['run_id'],
                'Original governed work',
                'developer',
                before_submit=guard,
                policy='Server-owned governed policy',
                relay_scope_preflight=envelope['governance_scope']['preflight_proof'],
            )
        self.assertEqual(result['status'], 'delegated')
        guard.assert_awaited_once()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['governance_scope']['submission_proof'], final)
        with self.primary.ledger(Path(self.temp.name) / 'abitdot-primary.sqlite') as db:
            stored = dict(db.execute('SELECT * FROM dot_primary_runs').fetchone())
        self.assertEqual(json.loads(stored['envelope_json'])['governance_scope'], sent[0]['governance_scope'])
        self.assertEqual(json.loads(stored['scope_receipt_json'])['proof_id'], final['proof_id'])

    async def test_expired_final_guard_proof_cannot_claim_or_send_dispatch(self):
        row, packet, envelope = await self.governed()
        execution = self.load('erp_dot_execution')
        execution.STATE_DIR = Path(self.temp.name)
        task = {'name': packet['task_id'], 'project': P, 'status': 'Working', 'custom_agentic_managed': 1}
        erp = types.SimpleNamespace(get=AsyncMock(return_value=task), assigned=AsyncMock(return_value=True))
        final = deepcopy(envelope['governance_scope']['submission_proof'])
        final['expires_at'] = int(time.time()) - 1
        sender = AsyncMock()
        with (
            patch.object(execution, 'synced_task_project', AsyncMock(return_value=None)),
            patch.object(execution, 'task_context', AsyncMock(return_value={'latest_run': 'fixture-context'})),
            patch.object(self.relay, 'send_via_existing_relay', sender),
        ):
            with self.assertRaises(self.module.CapabilityUnavailable):
                await execution.dispatch_task_dot(
                    erp,
                    task,
                    'owner-fixture',
                    row['agent_id'],
                    final['scope']['run_id'],
                    'Fixture',
                    'developer',
                    before_submit=AsyncMock(return_value=final),
                    policy='Governed',
                    relay_scope_preflight=envelope['governance_scope']['preflight_proof'],
                )
        sender.assert_not_awaited()
        self.assertFalse((Path(self.temp.name) / 'abitdot-primary.sqlite').exists())

    async def test_saved_recovery_requires_exact_durable_receipt_without_new_admission(self):
        row, packet, envelope = await self.governed()
        path = Path(self.temp.name) / 'abitdot-primary.sqlite'
        outcome = self.relay.receipt_from_relay(envelope, self.provider(envelope), 'a' * 64)
        await self.primary.deliver(envelope, AsyncMock(return_value=outcome), path)
        execution = self.load('erp_dot_execution')
        recovery = self.load('agentic_recovery')
        row['automation_run_id'] = envelope['run_id']
        automation = types.SimpleNamespace(user_id='owner-fixture')
        save = AsyncMock()
        observe = AsyncMock(side_effect=AssertionError('Saved receipt must not request a new remote observation'))
        later = envelope['governance_scope']['submission_proof']['expires_at'] + 300
        with (
            patch.object(execution, 'save_dot_reply', save),
            patch.object(recovery, 'observe_original', observe),
            patch.object(self.module.time, 'time', return_value=later),
        ):
            recovered = await recovery.read_original(row, automation)
            self.assertEqual(recovered['response_text'], 'Original controlled reply')
            with self.primary.ledger(path) as db:
                db.execute('UPDATE dot_primary_runs SET scope_receipt_json=NULL')
            with self.assertRaises(ValueError):
                await recovery.read_original(row, automation)
        save.assert_awaited_once()
        observe.assert_not_awaited()

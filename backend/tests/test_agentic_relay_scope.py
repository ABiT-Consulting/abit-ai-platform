"""Scope proof validation; fake read-only relay, zero handoff/model/run creation."""

from copy import deepcopy
import json
import os
import sys
import time
import types
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
            'enforcement_mode': 'scoped_tool_proxy',
            'enforced_tool_scopes': body['scope']['tool_scopes'].copy(),
            'external_write_allowlist': [],
            'proof_id': 'scope-fixture-proof-001',
            'issued_at': now,
            'expires_at': now + 30,
        }
        if self.mutate:
            self.mutate(result)
        return result

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

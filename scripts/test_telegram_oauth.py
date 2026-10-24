"""Isolated OAuth security tests; no live database, credentials, or network.

Uses real FastAPI/Pydantic when installed. Minimal response/decorator stand-ins
allow the same business-logic tests during draft review; the runtime suite
separately exercises actual HTTP routing and the application's user database.
"""
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import types
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

try:
    from fastapi import HTTPException
except ModuleNotFoundError:
    fastapi = types.ModuleType('fastapi')
    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None):
            self.status_code, self.detail, self.headers = status_code, detail, headers or {}
    class APIRouter:
        def get(self, path): return lambda fn: fn
        def post(self, path): return lambda fn: fn
    class JSONResponse:
        def __init__(self, content, status_code=200, headers=None):
            self.body = json.dumps(content).encode()
            self.status_code, self.headers = status_code, headers or {}
    class RedirectResponse(JSONResponse):
        def __init__(self, url, status_code=307, headers=None):
            super().__init__({}, status_code, {'location': url, **(headers or {})})
    fastapi.APIRouter, fastapi.HTTPException = APIRouter, HTTPException
    fastapi.Request, fastapi.Depends = object, lambda fn: fn
    responses = types.ModuleType('fastapi.responses')
    responses.JSONResponse, responses.RedirectResponse = JSONResponse, RedirectResponse
    sys.modules['fastapi'], sys.modules['fastapi.responses'] = fastapi, responses
try:
    import pydantic
except ModuleNotFoundError:
    pydantic = types.ModuleType('pydantic')
    class BaseModel:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    pydantic.BaseModel, pydantic.Field = BaseModel, lambda **kwargs: None
    sys.modules['pydantic'] = pydantic

for package in ('open_webui', 'open_webui.utils'):
    module = types.ModuleType(package)
    module.__path__ = []
    sys.modules[package] = module
bridge = types.ModuleType('open_webui.utils.telegram_bridge')
auth = types.ModuleType('open_webui.utils.auth')
auth.get_verified_user = lambda: None
sys.modules[bridge.__name__], sys.modules[auth.__name__] = bridge, auth

HERE = Path(__file__).resolve()
SOURCE = HERE.with_name('telegram_oauth.py')
if not SOURCE.exists():
    SOURCE = HERE.parent.parent / 'backend/open_webui/routers/telegram_oauth.py'
spec = importlib.util.spec_from_file_location('telegram_oauth_under_test', SOURCE)
oauth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oauth)


class Query(dict):
    def __init__(self, values):
        self.values = {key: value if isinstance(value, list) else [value] for key, value in values.items()}
        super().__init__({key: value[-1] for key, value in self.values.items()})
    def getlist(self, key): return self.values.get(key, [])


class Request:
    def __init__(self, data=None, query=None, bearer=None, raw=None, form=False, chunks=None):
        self.query_params = Query(query or {})
        self.headers = {'content-type': 'application/x-www-form-urlencoded' if form else 'application/json'}
        if bearer: self.headers['authorization'] = 'Bearer ' + bearer
        self.state = types.SimpleNamespace()
        self.raw = raw if raw is not None else (urlencode(data or {}).encode() if form else json.dumps(data or {}).encode())
        self.chunks = chunks or [self.raw]
    async def stream(self):
        for chunk in self.chunks: yield chunk


def body(response): return json.loads(response.body)


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abit_telegram_oauth_test_')
        self.db_path = Path(self.temp.name) / 'isolated.sqlite'
        self.clock = 1792832400
        self.config = {'public_url': 'https://app.example.com'}
        self.users = {
            'alice': types.SimpleNamespace(id='alice', name='Alice', email='alice@example.invalid'),
            'bob': types.SimpleNamespace(id='bob', name='Bob', email='bob@example.invalid'),
        }
        self.redirect = 'https://chatgpt.com/connector_platform_oauth_redirect'
        bridge.db, bridge.now = self.db, lambda: self.clock
        bridge.get_config, bridge.active_user = lambda: self.config, self.active_user

    def tearDown(self): self.temp.cleanup()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()

    async def active_user(self, owner): return self.users.get(owner)

    async def client(self, **extra):
        response = await oauth.register(Request({'redirect_uris': [self.redirect], 'client_name': 'ChatGPT', **extra}))
        self.assertEqual(response.status_code, 201, body(response))
        return body(response)['client_id']

    async def pending(self, scope=None, **overrides):
        client = await self.client()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        params = {'client_id': client, 'redirect_uri': self.redirect, 'response_type': 'code',
                  'resource': oauth._resource(), 'code_challenge': challenge,
                  'code_challenge_method': 'S256', 'state': 'original-state<&>'}
        if scope: params['scope'] = scope
        params.update(overrides)
        response = await oauth.authorize(Request(query=params))
        self.assertEqual(response.status_code, 303, getattr(response, 'body', b''))
        request_id = parse_qs(urlparse(response.headers['location']).query)['request'][0]
        return {'client_id': client, 'code_verifier': verifier, 'request_id': request_id,
                'redirect_uri': self.redirect, 'resource': oauth._resource(), 'grant_type': 'authorization_code'}

    async def approved(self, owner='alice', scope=None):
        pending = await self.pending(scope=scope)
        await oauth.request_details(pending['request_id'], self.users[owner])
        result = await oauth.approve(oauth.ConsentForm(request_id=pending['request_id'], approve=True), self.users[owner])
        query = parse_qs(urlparse(body(result)['redirect_url']).query)
        self.assertEqual(query['state'], ['original-state<&>'])
        self.assertEqual(query['iss'], [oauth._issuer()])
        return {**pending, 'code': query['code'][0]}

    async def grant(self, owner='alice', scope=None):
        data = await self.approved(owner=owner, scope=scope)
        result = await oauth.token(Request(data, form=True))
        self.assertEqual(result.status_code, 200, body(result))
        return data, body(result)

    async def assert_http(self, status, awaitable):
        with self.assertRaises(HTTPException) as caught: await awaitable
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def test_metadata_matches_exact_mcp_resource_and_issuer(self):
        meta = oauth.authorization_server_metadata()
        self.assertEqual(meta['issuer'], 'https://app.example.com/api/v1/telegram/oauth')
        self.assertEqual(meta['code_challenge_methods_supported'], ['S256'])
        self.assertEqual(meta['token_endpoint_auth_methods_supported'], ['none'])
        self.assertTrue(meta['authorization_response_iss_parameter_supported'])
        self.assertEqual(oauth.protected_resource_metadata()['resource'], oauth._resource())

    def test_unconfigured_status_is_false(self):
        self.config = {}
        self.assertFalse(oauth.owner_connected('alice'))

    def test_redirect_allowlist_is_exact(self):
        self.assertTrue(oauth._valid_redirect(self.redirect))
        self.assertTrue(oauth._valid_redirect('https://chatgpt.com/connector/oauth/abc-123'))
        for uri in ('https://evil.example/connector/oauth/a', 'https://chatgpt.com.evil.example/connector/oauth/a',
                    'http://chatgpt.com/connector/oauth/a', 'https://user@chatgpt.com/connector/oauth/a',
                    'https://chatgpt.com:443/connector/oauth/a', 'https://chatgpt.com/connector/oauth/a?next=x',
                    'https://chatgpt.com/connector/oauth/a#fragment', 'https://chatgpt.com/anything', 1, None):
            self.assertFalse(oauth._valid_redirect(uri), uri)

    async def test_malformed_registration_returns_errors(self):
        for grants in (None, {}, [None], [{}], [], ['client_credentials']):
            result = await oauth.register(Request({'redirect_uris': [self.redirect], 'grant_types': grants}))
            self.assertEqual(result.status_code, 400)
        result = await oauth.register(Request({'redirect_uris': [self.redirect], 'token_endpoint_auth_method': 'client_secret_post'}))
        self.assertEqual(result.status_code, 400)

    async def test_authorization_rejects_plain_pkce(self):
        client = await self.client()
        result = await oauth.authorize(Request(query={'client_id': client, 'redirect_uri': self.redirect,
            'response_type': 'code', 'resource': oauth._resource(), 'code_challenge': 'a' * 43,
            'code_challenge_method': 'plain'}))
        self.assertEqual(body(result)['error'], 'invalid_request')

    async def test_explicit_review_and_single_owner_required(self):
        pending = await self.pending()
        consent = oauth.ConsentForm(request_id=pending['request_id'], approve=True)
        await self.assert_http(400, oauth.approve(consent, self.users['alice']))
        await oauth.request_details(pending['request_id'], self.users['alice'])
        await self.assert_http(400, oauth.request_details(pending['request_id'], self.users['bob']))
        await self.assert_http(400, oauth.approve(consent, self.users['bob']))

    async def test_consent_requires_explicit_webui_bearer(self):
        await self.assert_http(401, oauth._web_user(Request(), self.users['alice']))
        user = await oauth._web_user(Request(bearer='already-verified-by-webui'), self.users['alice'])
        self.assertEqual(user.id, 'alice')

    async def test_authorization_request_expires(self):
        pending = await self.pending()
        await oauth.request_details(pending['request_id'], self.users['alice'])
        self.clock += oauth.REQUEST_TTL + 1
        await self.assert_http(400, oauth.approve(oauth.ConsentForm(request_id=pending['request_id'], approve=True), self.users['alice']))

    async def test_deny_preserves_state_without_minting_code(self):
        pending = await self.pending()
        await oauth.request_details(pending['request_id'], self.users['alice'])
        result = await oauth.approve(oauth.ConsentForm(request_id=pending['request_id'], approve=False), self.users['alice'])
        query = parse_qs(urlparse(body(result)['redirect_url']).query)
        self.assertEqual(query['error'], ['access_denied'])
        self.assertNotIn('code', query)
        with oauth._db() as conn: self.assertEqual(conn.execute('SELECT count(*) FROM tg_oauth_codes').fetchone()[0], 0)

    async def test_code_exchange_uses_owner_and_hashes_secrets(self):
        data, tokens = await self.grant()
        request = Request(bearer=tokens['access_token'])
        user = await oauth.authenticate_mcp(request, 'telegram:read')
        self.assertEqual(user.id, 'alice')
        self.assertEqual(request.state.user.id, 'alice')
        self.assertEqual(request.state.auth_type, 'telegram_oauth')
        self.assertTrue(oauth.owner_connected('alice'))
        self.assertFalse(oauth.owner_connected('bob'))
        content = self.db_path.read_bytes()
        for raw in (data['code'], tokens['access_token'], tokens['refresh_token']):
            self.assertNotIn(raw.encode(), content)

    async def test_wrong_pkce_does_not_consume_code(self):
        data = await self.approved()
        wrong = await oauth.token(Request({**data, 'code_verifier': 'b' * 43}, form=True))
        self.assertEqual(body(wrong)['error'], 'invalid_grant')
        self.assertEqual((await oauth.token(Request(data, form=True))).status_code, 200)

    async def test_code_cannot_be_replayed(self):
        data, _ = await self.grant()
        self.assertEqual(body(await oauth.token(Request(data, form=True)))['error'], 'invalid_grant')

    async def test_wrong_client_redirect_and_resource_rejected(self):
        data = await self.approved()
        another = await self.client()
        for field, value in (('client_id', another), ('redirect_uri', 'https://chatgpt.com/connector/oauth/other'),
                             ('resource', 'https://other.example/mcp')):
            result = await oauth.token(Request({**data, field: value}, form=True))
            self.assertNotEqual(result.status_code, 200)
        self.assertEqual((await oauth.token(Request(data, form=True))).status_code, 200)

    async def test_expired_code_rejected(self):
        data = await self.approved()
        self.clock += oauth.CODE_TTL + 1
        self.assertEqual(body(await oauth.token(Request(data, form=True)))['error'], 'invalid_grant')

    async def test_disabled_user_cannot_exchange_or_call_mcp(self):
        data, tokens = await self.grant()
        second = await self.approved()
        self.users.pop('alice')
        self.assertEqual(body(await oauth.token(Request(second, form=True)))['error'], 'invalid_grant')
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=tokens['access_token'])))

    async def test_scope_enforcement(self):
        _, tokens = await self.grant(scope='telegram:read')
        await oauth.authenticate_mcp(Request(bearer=tokens['access_token']), 'telegram:read')
        await self.assert_http(403, oauth.authenticate_mcp(Request(bearer=tokens['access_token']), 'telegram:reply'))

    async def test_exact_grant_revocation_does_not_retain_callback_authority(self):
        first_data, first = await self.grant()
        _, second = await self.grant()
        first_request, second_request = Request(bearer=first['access_token']), Request(bearer=second['access_token'])
        await oauth.authenticate_mcp(first_request)
        await oauth.authenticate_mcp(second_request)
        first_id, second_id = first_request.state.telegram_oauth_grant_id, second_request.state.telegram_oauth_grant_id
        self.assertNotEqual(first_id, second_id)
        self.assertTrue(oauth.owner_connected('alice', first_id, 'telegram:read'))
        await oauth.revoke(Request({'token': first['access_token'], 'client_id': first_data['client_id']}, form=True))
        self.assertFalse(oauth.owner_connected('alice', first_id, 'telegram:read'))
        self.assertTrue(oauth.owner_connected('alice', second_id, 'telegram:read'))
        self.assertFalse(oauth.owner_connected('bob', second_id, 'telegram:read'))

    async def test_exact_grant_scope_required_for_event_delivery(self):
        _, tokens = await self.grant(scope='telegram:reply')
        request = Request(bearer=tokens['access_token'])
        await oauth.authenticate_mcp(request, 'telegram:reply')
        grant_id = request.state.telegram_oauth_grant_id
        self.assertTrue(oauth.owner_connected('alice', grant_id, 'telegram:reply'))
        self.assertFalse(oauth.owner_connected('alice', grant_id, 'telegram:read'))

    async def test_refresh_rotation_and_replay_revoke_family(self):
        data, tokens = await self.grant()
        params = {'grant_type': 'refresh_token', 'client_id': data['client_id'],
                  'resource': data['resource'], 'refresh_token': tokens['refresh_token']}
        rotated = body(await oauth.token(Request(params, form=True)))
        self.assertNotEqual(rotated['refresh_token'], tokens['refresh_token'])
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=tokens['access_token'])))
        await oauth.authenticate_mcp(Request(bearer=rotated['access_token']))
        self.assertEqual(body(await oauth.token(Request(params, form=True)))['error'], 'invalid_grant')
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=rotated['access_token'])))

    async def test_revoke_owner_closes_connection(self):
        _, tokens = await self.grant()
        self.assertEqual(oauth.revoke_owner('alice'), 1)
        self.assertFalse(oauth.owner_connected('alice'))
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=tokens['access_token'])))

    async def test_resource_origin_change_invalidates_access(self):
        _, tokens = await self.grant()
        self.config['public_url'] = 'https://moved.example.com'
        self.assertFalse(oauth.owner_connected('alice'))
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=tokens['access_token'])))

    async def test_mcp_revocation_during_active_user_lookup(self):
        _, tokens = await self.grant()
        async def revoking(owner):
            oauth.revoke_owner(owner)
            return self.users[owner]
        bridge.active_user = revoking
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=tokens['access_token'])))

    async def test_code_expiration_during_active_user_lookup(self):
        data = await self.approved()
        async def expiring(owner):
            self.clock += oauth.CODE_TTL + 1
            return self.users[owner]
        bridge.active_user = expiring
        self.assertEqual(body(await oauth.token(Request(data, form=True)))['error'], 'invalid_grant')

    async def test_refresh_expiration_during_active_user_lookup(self):
        data, tokens = await self.grant()
        async def expiring(owner):
            self.clock += oauth.REFRESH_TTL + 1
            return self.users[owner]
        bridge.active_user = expiring
        response = await oauth.token(Request({'grant_type': 'refresh_token', 'client_id': data['client_id'],
            'resource': data['resource'], 'refresh_token': tokens['refresh_token']}, form=True))
        self.assertEqual(body(response)['error'], 'invalid_grant')

    async def test_body_limit_is_applied_while_streaming(self):
        await self.assert_http(413, oauth.register(Request(chunks=[b'{', b'x' * 8192])))
        await self.assert_http(413, oauth.token(Request(form=True, chunks=[b'code=', b'x' * 8192])))

    async def test_duplicate_form_parameters_rejected(self):
        await self.assert_http(400, oauth.token(Request(raw=b'client_id=a&client_id=b', form=True)))

    async def test_revocation_endpoint_checks_client(self):
        data, tokens = await self.grant()
        await oauth.revoke(Request({'token': tokens['access_token'], 'client_id': 'wrong'}, form=True))
        await oauth.authenticate_mcp(Request(bearer=tokens['access_token']))
        await oauth.revoke(Request({'token': tokens['access_token'], 'client_id': data['client_id']}, form=True))
        await self.assert_http(401, oauth.authenticate_mcp(Request(bearer=tokens['access_token'])))


if __name__ == '__main__': unittest.main(verbosity=2)

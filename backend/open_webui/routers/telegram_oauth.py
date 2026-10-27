"""Account-scoped OAuth 2.1 for the Telegram MCP bridge.

This provider issues dedicated opaque tokens, never WebUI session JWTs. All
credentials are hashed at rest, authorization needs an explicit signed-in
consent, and every MCP call rechecks the application's current user status.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from open_webui.utils import telegram_bridge as bridge
from open_webui.utils.auth import get_verified_user

router = APIRouter()
SCOPES = ('telegram:read', 'telegram:reply')
REQUEST_TTL = 600
CODE_TTL = 120
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
GRANT_TTL = 90 * 86400
OPAQUE = re.compile(r'^[A-Za-z0-9_-]{43}$')
PKCE_VERIFIER = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
REDIRECT_PATH = re.compile(r'^/connector/oauth/[A-Za-z0-9_-]{1,200}$')


@contextmanager
def _db():
    with bridge.db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS tg_oauth_clients (
            client_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            redirect_uris TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tg_oauth_requests (
            id TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
            resource TEXT NOT NULL, scope TEXT NOT NULL, challenge TEXT NOT NULL,
            state TEXT NOT NULL, owner TEXT, status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tg_oauth_codes (
            code_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, client_id TEXT NOT NULL,
            resource TEXT NOT NULL, scope TEXT NOT NULL, redirect_uri TEXT NOT NULL,
            challenge TEXT NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS tg_oauth_grants (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, client_id TEXT NOT NULL,
            resource TEXT NOT NULL, scope TEXT NOT NULL, created_at INTEGER NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tg_oauth_tokens (
            access_hash TEXT PRIMARY KEY, refresh_hash TEXT NOT NULL UNIQUE,
            grant_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
            refresh_expires_at INTEGER NOT NULL, refresh_used INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS tg_oauth_grants_owner ON tg_oauth_grants(owner);
        CREATE INDEX IF NOT EXISTS tg_oauth_tokens_grant ON tg_oauth_tokens(grant_id);
        ''')
        yield conn


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _base() -> str:
    value = (bridge.get_config() or {}).get('public_url', '').rstrip('/')
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise HTTPException(503, 'Configure the Telegram application URL first.')
    return value


def _resource() -> str:
    return _base() + '/api/v1/telegram/mcp'


def _issuer() -> str:
    return _base() + '/api/v1/telegram/oauth'


def protected_resource_metadata() -> dict:
    return {
        'resource': _resource(),
        'authorization_servers': [_issuer()],
        'scopes_supported': list(SCOPES),
        'resource_name': 'ABiT Telegram bridge',
        'bearer_methods_supported': ['header'],
    }


def authorization_server_metadata() -> dict:
    issuer = _issuer()
    return {
        'issuer': issuer,
        'authorization_endpoint': issuer + '/authorize',
        'token_endpoint': issuer + '/token',
        'registration_endpoint': issuer + '/register',
        'revocation_endpoint': issuer + '/revoke',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'token_endpoint_auth_methods_supported': ['none'],
        'revocation_endpoint_auth_methods_supported': ['none'],
        'code_challenge_methods_supported': ['S256'],
        'scopes_supported': list(SCOPES),
        'authorization_response_iss_parameter_supported': True,
    }


@router.get('/.well-known/oauth-authorization-server')
def metadata_alias():
    return authorization_server_metadata()


def _error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {'error': error, 'error_description': description}, status_code=status,
        headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
    )


def _challenge(error: str = 'invalid_token') -> str:
    return ('Bearer resource_metadata="' + _base()
            + '/api/v1/telegram/.well-known/oauth-protected-resource", '
            + 'scope="' + ' '.join(SCOPES) + '", error="' + error + '"')


def _valid_redirect(uri: object) -> bool:
    if not isinstance(uri, str) or len(uri) > 512 or any(ord(c) < 33 for c in uri):
        return False
    try:
        parsed = urlparse(uri)
        return (parsed.scheme == 'https' and parsed.netloc == 'chatgpt.com'
                and not parsed.query and not parsed.fragment
                and (parsed.path == '/connector_platform_oauth_redirect'
                     or bool(REDIRECT_PATH.fullmatch(parsed.path))))
    except ValueError:
        return False


def _scope(value: str | None) -> str:
    requested = set((value or ' '.join(SCOPES)).split())
    if not requested or not requested.issubset(SCOPES):
        raise ValueError('Only Telegram read and reply scopes are supported.')
    return ' '.join(scope for scope in SCOPES if scope in requested)


async def _web_user(request: Request, user=Depends(get_verified_user)):
    # Consent comes from the application's authenticated JavaScript page.
    # Never accept an ambient cookie alone for a cross-site OAuth approval.
    scheme, _, credential = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not credential.strip():
        raise HTTPException(401, 'Open this authorization request while signed in to ABiT AI.')
    active = await bridge.active_user(user.id)
    if active is None:
        raise HTTPException(401, 'The application account is not active.')
    return active


async def _json_body(request: Request, max_size: int = 8192) -> dict:
    raw = await _bounded_body(request, max_size)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'A JSON object is required.') from None
    if not isinstance(data, dict):
        raise HTTPException(400, 'A JSON object is required.')
    return data


async def _bounded_body(request: Request, max_size: int = 8192) -> bytes:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > max_size:
            raise HTTPException(413, 'OAuth request is too large.')
        raw.extend(chunk)
    return bytes(raw)


@router.post('/register')
async def register(request: Request):
    _base()
    data = await _json_body(request)
    redirects = data.get('redirect_uris')
    if (not isinstance(redirects, list) or not 1 <= len(redirects) <= 5
            or not all(_valid_redirect(uri) for uri in redirects)):
        return _error('invalid_redirect_uri', 'Use the exact ChatGPT OAuth callback URL.')
    if data.get('token_endpoint_auth_method', 'none') != 'none':
        return _error('invalid_client_metadata', 'This provider uses public clients with PKCE.')
    grants = data.get('grant_types', ['authorization_code', 'refresh_token'])
    if (not isinstance(grants, list) or not grants
            or not all(isinstance(item, str) for item in grants)
            or not set(grants).issubset({'authorization_code', 'refresh_token'})
            or 'authorization_code' not in grants
            or data.get('response_types', ['code']) != ['code']):
        return _error('invalid_client_metadata', 'Only authorization-code and refresh grants are supported.')
    name = data.get('client_name', 'ChatGPT')
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        return _error('invalid_client_metadata', 'A client name under 120 characters is required.')
    now = bridge.now()
    client_id = 'tg_client_' + secrets.token_urlsafe(24)
    with _db() as conn:
        # Registration is public but must not become unbounded storage.
        count = conn.execute('SELECT count(*) FROM tg_oauth_clients WHERE created_at>?',
                             (now - 86400,)).fetchone()[0]
        if count >= 250:
            return _error('temporarily_unavailable', 'Registration limit reached; try later.', 429)
        conn.execute('INSERT INTO tg_oauth_clients VALUES(?,?,?,?)',
                     (client_id, name.strip(), json.dumps(list(dict.fromkeys(redirects))), now))
        conn.commit()
    return JSONResponse({
        'client_id': client_id, 'client_id_issued_at': now, 'client_name': name.strip(),
        'redirect_uris': list(dict.fromkeys(redirects)),
        'token_endpoint_auth_method': 'none',
        'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
        'scope': ' '.join(SCOPES),
    }, status_code=201, headers={'Cache-Control': 'no-store'})


@router.get('/authorize')
async def authorize(request: Request):
    params = request.query_params
    required = ('client_id', 'redirect_uri', 'response_type', 'resource',
                'code_challenge', 'code_challenge_method')
    if any(len(params.getlist(key)) != 1 for key in required):
        return _error('invalid_request', 'Required OAuth parameters must occur exactly once.')
    if any(len(params.getlist(key)) > 1 for key in ('scope', 'state')):
        return _error('invalid_request', 'Duplicate OAuth parameters are not allowed.')
    client_id, redirect = params['client_id'], params['redirect_uri']
    with _db() as conn:
        client = conn.execute('SELECT * FROM tg_oauth_clients WHERE client_id=?',
                              (client_id,)).fetchone()
    if not client or not _valid_redirect(redirect) or redirect not in json.loads(client['redirect_uris']):
        return _error('invalid_request', 'Client or redirect URI is not registered.')
    if params['response_type'] != 'code':
        return _error('unsupported_response_type', 'Only authorization code is supported.')
    if params['resource'] != _resource():
        return _error('invalid_target', 'The requested resource does not match this bridge.')
    if params['code_challenge_method'] != 'S256' or not OPAQUE.fullmatch(params['code_challenge']):
        return _error('invalid_request', 'An S256 PKCE challenge is required.')
    state = params.get('state', '')
    if len(state) > 2048:
        return _error('invalid_request', 'OAuth state is too long.')
    try:
        scope = _scope(params.get('scope'))
    except ValueError as exc:
        return _error('invalid_scope', str(exc))
    request_id, now = secrets.token_urlsafe(32), bridge.now()
    with _db() as conn:
        conn.execute('DELETE FROM tg_oauth_requests WHERE expires_at<?', (now - 3600,))
        conn.execute('DELETE FROM tg_oauth_codes WHERE expires_at<?', (now - 3600,))
        count = conn.execute('SELECT count(*) FROM tg_oauth_requests WHERE expires_at>?',
                             (now,)).fetchone()[0]
        if count >= 1000:
            return _error('temporarily_unavailable', 'Too many pending authorization requests.', 429)
        conn.execute('''INSERT INTO tg_oauth_requests
            (id,client_id,redirect_uri,resource,scope,challenge,state,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (request_id, client_id, redirect, _resource(), scope,
             params['code_challenge'], state, now, now + REQUEST_TTL))
        conn.commit()
    return RedirectResponse(_base() + '/dot/telegram/authorize?' + urlencode({'request': request_id}),
                            status_code=303, headers={'Cache-Control': 'no-store'})


def _pending(conn, request_id: str, owner: str):
    if not OPAQUE.fullmatch(request_id):
        raise HTTPException(404, 'Authorization request was not found.')
    row = conn.execute('SELECT * FROM tg_oauth_requests WHERE id=?', (request_id,)).fetchone()
    if (not row or row['status'] != 'pending' or row['expires_at'] <= bridge.now()
            or row['resource'] != _resource() or (row['owner'] and row['owner'] != owner)):
        raise HTTPException(400, 'Authorization request expired or was already used. Start again in ChatGPT.')
    return row


@router.get('/requests/{request_id}')
async def request_details(request_id: str, user=Depends(_web_user)):
    with _db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _pending(conn, request_id, user.id)
        conn.execute('UPDATE tg_oauth_requests SET owner=? WHERE id=?', (user.id, request_id))
        client = conn.execute('SELECT name FROM tg_oauth_clients WHERE client_id=?',
                              (row['client_id'],)).fetchone()
        conn.commit()
    return JSONResponse({
        'request_id': request_id, 'client_name': client['name'] if client else 'ChatGPT',
        'scopes': row['scope'].split(), 'expires_at': row['expires_at'],
        'account_name': user.name, 'account_email': user.email,
    }, headers={'Cache-Control': 'no-store'})


class ConsentForm(BaseModel):
    request_id: str = Field(min_length=43, max_length=43)
    approve: bool = True


@router.post('/approve')
async def approve(form: ConsentForm, user=Depends(_web_user)):
    now = bridge.now()
    with _db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _pending(conn, form.request_id, user.id)
        if row['owner'] != user.id:
            raise HTTPException(400, 'Review the authorization request before approving it.')
        params = {'state': row['state'], 'iss': _issuer()}
        if form.approve:
            code = secrets.token_urlsafe(32)
            conn.execute('''INSERT INTO tg_oauth_codes
                (code_hash,owner,client_id,resource,scope,redirect_uri,challenge,expires_at)
                VALUES(?,?,?,?,?,?,?,?)''',
                (_hash(code), user.id, row['client_id'], row['resource'], row['scope'],
                 row['redirect_uri'], row['challenge'], now + CODE_TTL))
            params['code'] = code
        else:
            params['error'] = 'access_denied'
        conn.execute('UPDATE tg_oauth_requests SET owner=?,status=? WHERE id=?',
                     (user.id, 'approved' if form.approve else 'denied', form.request_id))
        conn.commit()
    return JSONResponse({'redirect_url': row['redirect_uri'] + '?' + urlencode(params)},
                        headers={'Cache-Control': 'no-store'})


async def _form_body(request: Request) -> dict[str, str]:
    raw = await _bounded_body(request)
    if request.headers.get('content-type', '').split(';', 1)[0].strip() != 'application/x-www-form-urlencoded':
        raise HTTPException(415, 'OAuth token requests use application/x-www-form-urlencoded.')
    try:
        values = parse_qs(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=16)
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'Invalid OAuth form body.') from None
    if any(len(v) != 1 for v in values.values()):
        raise HTTPException(400, 'Duplicate OAuth form fields are not allowed.')
    return {key: value[0] for key, value in values.items()}


def _mint(conn, grant_id: str, scope: str, grant_created: int) -> dict:
    now = bridge.now()
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires_at = min(now + ACCESS_TTL, grant_created + GRANT_TTL)
    refresh_expires_at = min(now + REFRESH_TTL, grant_created + GRANT_TTL)
    conn.execute('''INSERT INTO tg_oauth_tokens
        (access_hash,refresh_hash,grant_id,expires_at,refresh_expires_at) VALUES(?,?,?,?,?)''',
        (_hash(access), _hash(refresh), grant_id, expires_at, refresh_expires_at))
    return {'access_token': access, 'token_type': 'Bearer', 'expires_in': expires_at - now,
            'refresh_token': refresh, 'scope': scope}


@router.post('/token')
async def token(request: Request):
    data = await _form_body(request)
    client_id = data.get('client_id', '')
    resource = data.get('resource', '')
    if resource != _resource():
        return _error('invalid_target', 'The resource must match the Telegram MCP endpoint.')
    if data.get('client_secret') or data.get('client_assertion'):
        return _error('invalid_client', 'Use the public-client PKCE flow.')
    with _db() as conn:
        client = conn.execute('SELECT client_id FROM tg_oauth_clients WHERE client_id=?',
                              (client_id,)).fetchone()
    if not client:
        return _error('invalid_client', 'Client is not registered.', 401)
    if data.get('grant_type') == 'authorization_code':
        return await _exchange_code(data, client_id, resource)
    if data.get('grant_type') == 'refresh_token':
        return await _refresh(data, client_id, resource)
    return _error('unsupported_grant_type', 'Use authorization_code or refresh_token.')


async def _exchange_code(data: dict, client_id: str, resource: str):
    code, verifier = data.get('code', ''), data.get('code_verifier', '')
    if not OPAQUE.fullmatch(code) or not PKCE_VERIFIER.fullmatch(verifier):
        return _error('invalid_grant', 'Authorization code or PKCE verifier is invalid.')
    code_hash = _hash(code)
    with _db() as conn:
        row = conn.execute('SELECT * FROM tg_oauth_codes WHERE code_hash=?', (code_hash,)).fetchone()
    now = bridge.now()
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')
    if (not row or row['used_at'] is not None or row['expires_at'] <= now
            or row['client_id'] != client_id or row['resource'] != resource
            or row['redirect_uri'] != data.get('redirect_uri')
            or not hmac.compare_digest(row['challenge'], computed)):
        return _error('invalid_grant', 'Authorization code or PKCE verifier is invalid.')
    if await bridge.active_user(row['owner']) is None:
        return _error('invalid_grant', 'The application account is not active.')
    with _db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        now = bridge.now()
        if resource != _resource():
            return _error('invalid_target', 'The bridge address changed. Reconnect from ChatGPT.')
        changed = conn.execute('UPDATE tg_oauth_codes SET used_at=? WHERE code_hash=? '
                               'AND used_at IS NULL AND expires_at>?', (now, code_hash, now)).rowcount
        if changed != 1:
            return _error('invalid_grant', 'Authorization code expired or was already used.')
        grant_id = secrets.token_urlsafe(32)
        conn.execute('INSERT INTO tg_oauth_grants(id,owner,client_id,resource,scope,created_at) VALUES(?,?,?,?,?,?)',
                     (grant_id, row['owner'], client_id, resource, row['scope'], now))
        result = _mint(conn, grant_id, row['scope'], now)
        conn.commit()
    return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})


async def _refresh(data: dict, client_id: str, resource: str):
    raw = data.get('refresh_token', '')
    if not OPAQUE.fullmatch(raw):
        return _error('invalid_grant', 'Refresh token is invalid.')
    hashed = _hash(raw)
    with _db() as conn:
        row = conn.execute('''SELECT t.*,g.owner,g.client_id,g.resource,g.scope,g.created_at,
            g.revoked AS grant_revoked FROM tg_oauth_tokens t JOIN tg_oauth_grants g
            ON t.grant_id=g.id WHERE t.refresh_hash=?''', (hashed,)).fetchone()
    now = bridge.now()
    if (not row or row['client_id'] != client_id or row['resource'] != resource
            or row['grant_revoked'] or row['refresh_expires_at'] <= now):
        return _error('invalid_grant', 'Refresh token expired or was revoked.')
    if row['refresh_used']:
        with _db() as conn:
            conn.execute('UPDATE tg_oauth_grants SET revoked=1 WHERE id=?', (row['grant_id'],))
            conn.commit()
        return _error('invalid_grant', 'Refresh token reuse detected. Reconnect from ChatGPT.')
    if row['revoked'] or await bridge.active_user(row['owner']) is None:
        return _error('invalid_grant', 'Refresh token expired or was revoked.')
    if data.get('scope'):
        try:
            requested_scope = _scope(data['scope'])
        except ValueError:
            return _error('invalid_scope', 'Requested scope is not supported.')
        if requested_scope != row['scope']:
            return _error('invalid_scope', 'Reconnect to change the granted scopes.')
    with _db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Recheck revocation after the asynchronous user lookup.
        grant = conn.execute('SELECT revoked FROM tg_oauth_grants WHERE id=?', (row['grant_id'],)).fetchone()
        current = conn.execute('SELECT * FROM tg_oauth_tokens WHERE refresh_hash=?', (hashed,)).fetchone()
        now = bridge.now()
        if (not grant or grant['revoked'] or not current or current['refresh_expires_at'] <= now
                or row['created_at'] + GRANT_TTL <= now or resource != _resource()):
            return _error('invalid_grant', 'Refresh token was revoked.')
        changed = conn.execute('UPDATE tg_oauth_tokens SET refresh_used=1,revoked=1 '
                               'WHERE refresh_hash=? AND refresh_used=0 AND revoked=0', (hashed,)).rowcount
        if changed != 1:
            conn.execute('UPDATE tg_oauth_grants SET revoked=1 WHERE id=?', (row['grant_id'],))
            conn.commit()
            return _error('invalid_grant', 'Refresh token was already used. Reconnect from ChatGPT.')
        result = _mint(conn, row['grant_id'], row['scope'], row['created_at'])
        conn.commit()
    return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})


@router.post('/revoke')
async def revoke(request: Request):
    data = await _form_body(request)
    raw = data.get('token', '')
    if OPAQUE.fullmatch(raw):
        with _db() as conn:
            row = conn.execute('''SELECT t.grant_id FROM tg_oauth_tokens t JOIN tg_oauth_grants g
                ON t.grant_id=g.id WHERE (t.access_hash=? OR t.refresh_hash=?) AND g.client_id=?''',
                (_hash(raw), _hash(raw), data.get('client_id', ''))).fetchone()
            if row:
                conn.execute('UPDATE tg_oauth_grants SET revoked=1 WHERE id=?', (row['grant_id'],))
                conn.commit()
    return JSONResponse({}, headers={'Cache-Control': 'no-store'})


def revoke_owner(owner_id: str) -> int:
    with _db() as conn:
        count = conn.execute('UPDATE tg_oauth_grants SET revoked=1 WHERE owner=? AND revoked=0',
                             (owner_id,)).rowcount
        conn.execute('UPDATE tg_oauth_codes SET used_at=? WHERE owner=? AND used_at IS NULL',
                     (bridge.now(), owner_id))
        conn.execute("UPDATE tg_oauth_requests SET status='revoked' WHERE owner=? AND status='pending'",
                     (owner_id,))
        conn.commit()
    return count


def owner_connected(owner_id: str, grant_id: str | None = None,
                    required_scope: str | None = None) -> bool:
    try:
        resource = _resource()
    except HTTPException:
        return False
    now = bridge.now()
    with _db() as conn:
        query = '''SELECT g.scope FROM tg_oauth_grants g JOIN tg_oauth_tokens t
            ON t.grant_id=g.id WHERE g.owner=? AND g.resource=? AND g.revoked=0
            AND t.revoked=0 AND (t.expires_at>? OR (t.refresh_expires_at>? AND t.refresh_used=0))
            AND g.created_at>?'''
        params = [owner_id, resource, now, now, now - GRANT_TTL]
        if grant_id is not None:
            query += ' AND g.id=?'
            params.append(grant_id)
        rows = conn.execute(query, params).fetchall()
    return any(required_scope is None or required_scope in row['scope'].split() for row in rows)


async def authenticate_mcp(request: Request, required_scope: str | None = None):
    header = request.headers.get('authorization', '')
    scheme, _, raw = header.partition(' ')
    if scheme.lower() != 'bearer' or not OPAQUE.fullmatch(raw):
        raise HTTPException(401, 'Connect the Telegram bridge to your ABiT AI account.',
                            headers={'WWW-Authenticate': _challenge()})
    with _db() as conn:
        row = conn.execute('''SELECT t.grant_id,t.expires_at,t.revoked,g.owner,g.resource,g.scope,g.created_at,
            g.revoked AS grant_revoked FROM tg_oauth_tokens t JOIN tg_oauth_grants g
            ON t.grant_id=g.id WHERE t.access_hash=?''', (_hash(raw),)).fetchone()
    if (not row or row['revoked'] or row['grant_revoked'] or row['expires_at'] <= bridge.now()
            or row['created_at'] + GRANT_TTL <= bridge.now() or row['resource'] != _resource()):
        raise HTTPException(401, 'Telegram bridge authorization expired or was revoked.',
                            headers={'WWW-Authenticate': _challenge()})
    if required_scope and required_scope not in row['scope'].split():
        raise HTTPException(403, 'The Telegram bridge has not been granted this permission.',
                            headers={'WWW-Authenticate': _challenge('insufficient_scope')})
    user = await bridge.active_user(row['owner'])
    if user is None:
        raise HTTPException(401, 'The application account is not active.',
                            headers={'WWW-Authenticate': _challenge()})
    # Disconnect/revoke can race with the application's asynchronous user read.
    # Recheck the exact bearer grant immediately before admitting the MCP call.
    with _db() as conn:
        current = conn.execute('''SELECT t.grant_id,t.expires_at,t.revoked,g.resource,g.created_at,
            g.revoked AS grant_revoked FROM tg_oauth_tokens t JOIN tg_oauth_grants g
            ON t.grant_id=g.id WHERE t.access_hash=?''', (_hash(raw),)).fetchone()
    if (not current or current['revoked'] or current['grant_revoked']
            or current['expires_at'] <= bridge.now() or current['created_at'] + GRANT_TTL <= bridge.now()
            or current['resource'] != _resource()):
        raise HTTPException(401, 'Telegram bridge authorization expired or was revoked.',
                            headers={'WWW-Authenticate': _challenge()})
    request.state.user = user
    request.state.auth_type = 'telegram_oauth'
    request.state.telegram_oauth_grant_id = current['grant_id']
    return user

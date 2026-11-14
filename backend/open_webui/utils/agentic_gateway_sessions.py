"""Job-bound action gateway, independent of the owner's unrelated ChatGPT apps.

Only the authenticated controller admits sessions. The authenticated MCP edge
supplies connection/session identities; tool arguments cannot supply authority.
Every new action revalidates the original native ERP packet and current lease.
An uncertain action is never automatically executed a second time.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest, same_business_context, stable_run_id
from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable, _owned

BOUNDARY = 'gateway_operations_and_managed_erp'
MODE = 'governed_action_gateway'
EXCLUDED = ['owner_account_plugins']
ID = re.compile(r'[A-Za-z0-9_.:@+-]{8,160}')
HASH = re.compile(r'[a-f0-9]{64}')
MANAGED = {'source_modified', 'packet_id', 'content_sha256', 'implementation_user', 'implementation_run_id'}
SECRET_FILE = Path('/var/lib/abit-ai-agentic-gateway/edge-token')
CURRENT_DB = ContextVar('abit_gateway_ledger_path', default=None)


@contextmanager
def connect(db_path=None):
    with ledger.connect(db_path) as db:
        db.executescript('''
          CREATE TABLE IF NOT EXISTS governance_gateway_session(
            session_id TEXT PRIMARY KEY,invocation_id TEXT NOT NULL UNIQUE,
            scope_json TEXT NOT NULL,admission_json TEXT NOT NULL,
            connection_hash TEXT NOT NULL,client_session_hash TEXT,
            state TEXT NOT NULL,created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS governance_gateway_call(
            session_id TEXT NOT NULL,call_id TEXT NOT NULL,request_hash TEXT NOT NULL,
            operation TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT,
            created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
            PRIMARY KEY(session_id,call_id));
          CREATE TABLE IF NOT EXISTS governance_gateway_audit(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,
            kind TEXT NOT NULL,body_json TEXT NOT NULL,created_at INTEGER NOT NULL);
        ''')
        yield db


def _event(db, session, kind, body):
    db.execute(
        'INSERT INTO governance_gateway_audit(session_id,kind,body_json,created_at) VALUES(?,?,?,?)',
        (session, kind, canonical(body), int(time.time())),
    )


def edge_secret():
    """One fixed server-owned transport key, never a model-selected path."""
    try:
        from open_webui.utils.agentic_tool_gateway import _open_dir

        fd = _open_dir(SECRET_FILE.parent)
        try:
            key = os.open(SECRET_FILE.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                info = os.fstat(key)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256 or info.st_mode & 0o027:
                    raise CapabilityUnavailable('Gateway transport credential permissions are not protected')
                value = os.read(key, 257).decode('ascii').strip()
            finally:
                os.close(key)
        finally:
            os.close(fd)
        if len(value) < 32:
            raise CapabilityUnavailable('Gateway transport credential is unavailable')
        return value
    except (OSError, UnicodeError):
        raise CapabilityUnavailable('Gateway transport credential is unavailable') from None


def session_id(scope):
    # A narrow, opaque job capability, not a public digest or ERP credential.
    # Only the admitted owner connection can bind/use it, and only while this
    # original invocation retains current native authority and its lease.
    return (
        'agws_'
        + hmac.new(edge_secret().encode(), ('agov-session\n' + canonical(scope)).encode(), hashlib.sha256).hexdigest()[
            :48
        ]
    )


def edge_headers(body, secret, *, now=None):
    """HMAC is created only at the OAuth edge, never from a model tool argument."""
    if not isinstance(secret, str) or len(secret) < 32:
        raise CapabilityUnavailable('Authenticated gateway edge is not configured')
    stamp = str(int(time.time()) if now is None else int(now))
    signature = hmac.new(secret.encode(), (stamp + '\n' + canonical(body)).encode(), hashlib.sha256).hexdigest()
    return {'X-ABIT-Gateway-Time': stamp, 'X-ABIT-Gateway-Signature': signature}


def verify_edge(body, headers, secret, *, now=None):
    normalized = {k.lower(): v for k, v in headers.items()}
    stamp = str(normalized.get('x-abit-gateway-time', ''))
    if not re.fullmatch(r'[0-9]{10}', stamp) or abs(int(time.time() if now is None else now) - int(stamp)) > 30:
        raise ValueError('Gateway edge signature expired')
    expected = edge_headers(body, secret, now=int(stamp))['X-ABIT-Gateway-Signature']
    if not hmac.compare_digest(str(normalized.get('x-abit-gateway-signature', '')), expected):
        raise ValueError('Gateway edge authentication failed')
    if not isinstance(body, dict) or set(body) != {
        'connection_hash',
        'client_session_hash',
        'session_id',
        'call_id',
        'operation',
        'arguments',
    }:
        raise ValueError('Unsupported gateway edge envelope')
    for key in ('connection_hash', 'client_session_hash'):
        if not isinstance(body[key], str) or not HASH.fullmatch(body[key]):
            raise ValueError('Gateway requires authenticated connection and conversation identities')
    if any(not isinstance(body[k], str) or not ID.fullmatch(body[k]) for k in ('session_id', 'call_id')):
        raise ValueError('Invalid gateway session or idempotency reference')
    if not isinstance(body['arguments'], dict) or len(canonical(body).encode()) > 300_000:
        raise ValueError('Gateway request exceeds its structured boundary')
    return body


def _profile(config, scope):
    profile = (config.get('agentic_action_gateway') or {}).get('owners', {}).get(scope['owner'])
    if not isinstance(profile, dict) or profile.get('account_id') != scope['account_id']:
        raise CapabilityUnavailable('The existing owner/account has no authenticated action gateway profile')
    connection = profile.get('mcp_connection_hash')
    if not isinstance(connection, str) or not HASH.fullmatch(connection):
        raise CapabilityUnavailable('The existing MCP connection has not been verified for this owner')
    if profile.get('dot_id') != scope['dot_id']:
        raise CapabilityUnavailable('The action gateway is not linked to the registered abitdot')
    return profile


async def _authority(scope, *, bridge, project_gate, config, db_path=None, admitted=False):
    """No identity, repository, role, environment or grants come from tool input."""
    row = ledger.find(scope.get('invocation_id', ''), db_path, include_private=True)
    if not row:
        raise ValueError('Gateway invocation is not registered')
    registered = next(
        (
            x
            for x in ledger.roster(row['erp_project'], db_path)
            if x['agent_id'] == row['agent_id'] and x['role'] == row['target_role']
        ),
        None,
    )
    if not registered or registered['owner_id'] != scope.get('owner'):
        raise ValueError('Gateway invocation belongs to a different registered owner')
    if row['state'] not in ({'running'} if admitted else {'queued', 'running'}):
        raise ValueError('Gateway invocation is not eligible for admission or work')
    _, gate = await project_gate(row['erp_project'], config)
    if (
        gate.get('enabled') is not True
        or gate.get('pm_task_automation_enabled') is not True
        or gate.get('assigned_user') != 'ai-projects@abitcons.com'
        or gate.get('project_status') not in {'Open', 'Active', 'In Progress'}
    ):
        raise ValueError('ERP Project no longer authorizes governed actions')
    current = await bridge.packet(
        row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'], row['task_revision']
    )
    original = json.loads(row['work_packet'])
    if not same_business_context(original, current, MANAGED):
        raise ValueError('ERP work packet changed before the gateway operation')
    from open_webui.utils.abitdot_primary import dot_primary_key

    expected = {
        'owner': registered['owner_id'],
        'account_id': scope.get('account_id'),
        'dot_id': scope.get('dot_id'),
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'erp_user': registered['erp_user'],
        'repository': original['repository'],
        'environment': original['environment'],
        'tool_scopes': sorted(original['tool_scopes']),
        'work_packet_digest': packet_digest(original),
        'invocation_id': row['invocation_id'],
        'run_id': stable_run_id(row['invocation_id']),
        'job_id': 'aibit_'
        + hashlib.sha256(
            (
                'primary:'
                + dot_primary_key(registered['owner_id'], row['agent_id'], stable_run_id(row['invocation_id']))
            ).encode()
        ).hexdigest()[:48],
    }
    if scope != expected:
        raise ValueError('Gateway scope differs from the original server-owned work packet')
    _profile(config, expected)
    boundary = await bridge.gateway_boundary(expected)
    if not isinstance(boundary, dict) or boundary != {
        'schema_version': 1,
        'project_id': expected['project_id'],
        'task_id': expected['task_id'],
        'role': expected['role'],
        'agent_id': expected['agent_id'],
        'boundary': BOUNDARY,
        'managed_resource_guards': True,
        'pre_filesystem_file_guard': True,
        'ordinary_owner_plugins_fenced': False,
    }:
        raise CapabilityUnavailable('ERP has not verified its actual loaded managed-resource guards')
    if admitted:
        from open_webui.utils.agentic_maintenance import require_active, path as maintenance_path

        require_active(
            'governed', row['invocation_id'], db_path=maintenance_path(Path(db_path).parent) if db_path else None
        )
        with connect(db_path) as db:
            live = _owned(db, row['invocation_id'])
        if live['automation_run_id'] != expected['run_id'] or live['lease_token'] != row['lease_token']:
            raise ValueError('Gateway action lost its original execution lease')
    return row, original


async def proof(scope, *, bridge, project_gate, config, registry, db_path=None):
    """Read-only capability negotiation. It does not admit a session or run."""
    await _authority(scope, bridge=bridge, project_gate=project_gate, config=config, db_path=db_path)
    if not registry or any(not isinstance(x, Operation) for x in registry.values()):
        raise CapabilityUnavailable('No real gateway operation registry is installed')
    supported = {x.capability for x in registry.values()}
    if not set(scope['tool_scopes']) <= supported:
        raise CapabilityUnavailable('The packet requires an unsupported gateway capability')
    now = int(time.time())
    return {
        'protocol_version': 1,
        'scope': scope,
        'scope_digest': digest(scope),
        'enforced': True,
        'read_only': True,
        'default_deny': True,
        'enforcement_mode': MODE,
        'enforcement_boundary': BOUNDARY,
        'excluded_surfaces': EXCLUDED.copy(),
        'gateway_session_id': session_id(scope),
        'enforced_tool_scopes': scope['tool_scopes'],
        'external_write_allowlist': sorted(
            {x.capability for x in registry.values() if not x.read_only and x.capability in scope['tool_scopes']}
        ),
        'proof_id': 'agwp_' + secrets.token_hex(24),
        'issued_at': now,
        'expires_at': now + 60,
    }


async def admit(value, *, bridge, project_gate, config, registry, db_path=None):
    """Authenticated relay only, after final ERP/lease checks and before Send."""
    from open_webui.utils.agentic_relay_scope import _proof

    if (
        not isinstance(value, dict)
        or set(value) != {'protocol_version', 'scope', 'preflight_proof', 'submission_proof'}
        or type(value['protocol_version']) is not int
        or value['protocol_version'] != 1
    ):
        raise ValueError('Invalid gateway admission envelope')
    scope = value['scope']
    # The initial proof is historical, not later submission authority.
    initial = value['preflight_proof']
    _proof(initial, scope, now=int(time.time()), fresh=False)
    final = _proof(value['submission_proof'], scope, now=int(time.time()), fresh=False)
    if final['expires_at'] <= int(time.time()):
        raise ValueError('Gateway proof expired while preparing the original browser submission')
    if initial['gateway_session_id'] != final['gateway_session_id'] or final['gateway_session_id'] != session_id(scope):
        raise ValueError('Gateway session changed between preflight and submission')
    await _authority(scope, bridge=bridge, project_gate=project_gate, config=config, db_path=db_path, admitted=True)
    if final['expires_at'] <= int(time.time()):
        raise ValueError('Gateway proof expired during native admission checks; no browser submission')
    # Re-check installed capabilities; a formerly available adapter can be revoked.
    if not set(scope['tool_scopes']) <= {v.capability for v in registry.values()}:
        raise CapabilityUnavailable('Gateway operation registry changed before admission')
    profile = _profile(config, scope)
    sid = session_id(scope)
    admission = canonical(value)
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM governance_gateway_session WHERE session_id=?', (sid,)).fetchone()
        if old:
            if old['admission_json'] != admission or old['connection_hash'] != profile['mcp_connection_hash']:
                raise ValueError('Original gateway admission is immutable; no replacement send')
        else:
            _owned(db, scope['invocation_id'])
            db.execute(
                'INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,NULL,?,?)',
                (
                    sid,
                    scope['invocation_id'],
                    canonical(scope),
                    admission,
                    profile['mcp_connection_hash'],
                    'admitted',
                    int(time.time()),
                ),
            )
            _event(db, sid, 'admitted', {'scope_digest': digest(scope), 'job_id': scope['job_id']})
        db.commit()
    return receipt(value)


def receipt(value):
    scope, initial, final = value['scope'], value['preflight_proof'], value['submission_proof']
    return {
        'protocol_version': 1,
        'owner': scope['owner'],
        'account_id': scope['account_id'],
        'job_id': scope['job_id'],
        'invocation_id': scope['invocation_id'],
        'run_id': scope['run_id'],
        'work_packet_digest': scope['work_packet_digest'],
        'scope_digest': digest(scope),
        'preflight_proof_sha256': digest(initial),
        'submission_proof_sha256': digest(final),
        'proof_id': final['proof_id'],
        'gateway_session_id': final['gateway_session_id'],
        'enforcement_mode': MODE,
        'enforcement_boundary': BOUNDARY,
        'excluded_surfaces': EXCLUDED.copy(),
        'accepted': True,
    }


def original_receipt(value, db_path=None):
    """Read the exact accepted original after expiry/Stop; never re-admit it."""
    if not isinstance(value, dict) or not isinstance(value.get('scope'), dict):
        raise ValueError('Invalid original gateway reference')
    with connect(db_path) as db:
        row = db.execute(
            'SELECT admission_json FROM governance_gateway_session WHERE invocation_id=?',
            (value['scope'].get('invocation_id'),),
        ).fetchone()
        if not row or row['admission_json'] != canonical(value):
            raise ValueError('No exact original gateway admission receipt exists')
    return receipt(value)


@dataclass(frozen=True)
class Operation:
    capability: str
    roles: frozenset[str]
    handler: object
    read_only: bool = True
    requires_authority_callback: bool = False

    def __post_init__(self):
        if not callable(self.handler) or not self.roles:
            raise ValueError('Gateway operations require a trusted server handler and explicit roles')


async def call(body, *, bridge, project_gate, config, registry, db_path=None):
    """Serialize the complete original action across processes, including I/O."""
    from open_webui.utils.erp_status_write import task_write_lock

    with connect(db_path) as db:
        session = db.execute(
            'SELECT scope_json FROM governance_gateway_session WHERE session_id=?', (body['session_id'],)
        ).fetchone()
        if not session:
            raise ValueError('Gateway session has not been admitted')
        task = json.loads(session['scope_json'])['task_id']
    async with task_write_lock(task):
        return await _call_locked(
            body, bridge=bridge, project_gate=project_gate, config=config, registry=registry, db_path=db_path
        )


async def _call_locked(body, *, bridge, project_gate, config, registry, db_path=None):
    """Called only after verify_edge. Action receipts survive revocation/restart."""
    sid, cid = body['session_id'], body['call_id']
    request_hash = digest({k: body[k] for k in ('operation', 'arguments')})
    with connect(db_path) as db:
        session = db.execute('SELECT * FROM governance_gateway_session WHERE session_id=?', (sid,)).fetchone()
        if not session or session['connection_hash'] != body['connection_hash']:
            raise ValueError('Gateway session is not admitted for this authenticated connection')
        if session['client_session_hash'] not in (None, body['client_session_hash']):
            raise ValueError('Gateway session is bound to another original conversation')
        scope = json.loads(session['scope_json'])
        # Previously fenced results can be read after Stop, but never executed again.
        old = db.execute(
            'SELECT * FROM governance_gateway_call WHERE session_id=? AND call_id=?', (sid, cid)
        ).fetchone()
        if old:
            if old['request_hash'] != request_hash:
                raise ValueError('Gateway idempotency reference has different immutable content')
            return {
                'call_id': cid,
                'state': old['state'],
                'result': json.loads(old['result_json'] or 'null'),
                'replayed': True,
            }
    op = registry.get(body['operation'])
    if not isinstance(op, Operation) or op.capability not in scope['tool_scopes'] or scope['role'] not in op.roles:
        raise CapabilityUnavailable('This original role has not been granted the requested gateway operation')
    row, packet = await _authority(
        scope, bridge=bridge, project_gate=project_gate, config=config, db_path=db_path, admitted=True
    )
    _profile(config, scope)
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        _owned(db, row['invocation_id'])
        # CAS binds the first authenticated conversation without accepting a model identity.
        changed = db.execute(
            'UPDATE governance_gateway_session SET client_session_hash=? WHERE session_id=? AND state=? AND connection_hash=? AND (client_session_hash IS NULL OR client_session_hash=?)',
            (body['client_session_hash'], sid, 'admitted', body['connection_hash'], body['client_session_hash']),
        ).rowcount
        if changed != 1:
            raise ValueError('Gateway conversation binding was revoked or changed')
        prior = db.execute(
            'SELECT request_hash FROM governance_gateway_call WHERE session_id=? AND call_id=?', (sid, cid)
        ).fetchone()
        if prior:
            if prior['request_hash'] != request_hash:
                raise ValueError('Concurrent gateway idempotency content conflict')
            return {'call_id': cid, 'state': 'uncertain', 'result': None, 'replayed': True}
        if db.execute('SELECT COUNT(*) FROM governance_gateway_call WHERE session_id=?', (sid,)).fetchone()[0] >= 256:
            raise CapabilityUnavailable(
                'Original gateway session reached its bounded operation limit; preserve a checkpoint'
            )
        now = int(time.time())
        db.execute(
            'INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,NULL,?,?)',
            (sid, cid, request_hash, body['operation'], 'uncertain', now, now),
        )
        _event(db, sid, 'intent_fenced', {'call_id': cid, 'request_hash': request_hash, 'operation': body['operation']})
        db.commit()
    # An exception/crash preserves uncertain. No generic automatic retry is safe.
    context = CURRENT_DB.set(db_path)
    try:
        extra = {}
        if op.requires_authority_callback:

            async def check_authority():
                return await _authority(
                    scope,
                    bridge=bridge,
                    project_gate=project_gate,
                    config=config,
                    db_path=db_path,
                    admitted=True,
                )

            extra['check_authority'] = check_authority
        result = await op.handler(
            body['arguments'],
            row=row,
            packet=packet,
            config=config,
            bridge=bridge,
            gateway_call_id=cid,
            db_path=db_path,
            **extra,
        )
    finally:
        CURRENT_DB.reset(context)
    encoded = canonical(result)
    if len(encoded.encode()) > 300_000:
        raise ValueError('Gateway result exceeds its bounded response limit; original intent remains uncertain')
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute(
            'UPDATE governance_gateway_call SET state=?,result_json=?,updated_at=? WHERE session_id=? AND call_id=? AND state=? AND request_hash=?',
            ('completed', encoded, int(time.time()), sid, cid, 'uncertain', request_hash),
        ).rowcount
        if changed != 1:
            raise ValueError('Gateway original receipt changed during execution; reconcile without replay')
        _event(db, sid, 'completed', {'call_id': cid, 'result_sha256': digest(result)})
        db.commit()
    return {'call_id': cid, 'state': 'completed', 'result': result, 'replayed': False}

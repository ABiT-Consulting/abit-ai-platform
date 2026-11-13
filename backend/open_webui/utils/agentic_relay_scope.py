"""Fail-closed protocol for a physically scoped owner relay (protocol version 1).

External dependency: the existing authenticated local relay must implement
POST /dot/scope as a read-only attestation of its actual per-tool proxy. That
proxy must default-deny EVERY external tool/read/write outside the exact scope,
including alternate connectors, delegated agents and secret-bearing reads. It
must bind the scope to the authenticated owner/account/stable job and enforce
it again at /dot/handoff and at each tool invocation; a narrative instruction or
configuration flag is not enforcement. This module does not implement that
proxy. An absent endpoint or unsupported capability is a rollout blocker.

Request: {protocol_version:1, scope:{owner,account_id,dot_id,project_id,task_id,
role,agent_id,erp_user,repository,environment,tool_scopes,work_packet_digest,
invocation_id,run_id,job_id}, scope_digest}. No prompt, SRS, activity or credential
appears in the body. The credential is sent only in the existing auth header.
Response: exact protocol_version/scope/scope_digest, enforced:true,read_only:true,
default_deny:true,enforcement_mode:'scoped_tool_proxy',enforced_tool_scopes equal
the exact declared list, external_write_allowlist a unique subset of that list,
proof_id and integer UTC issued_at/expires_at (maximum lifetime 60 seconds).

Call before preparing a run, then again with the ORIGINAL immutable packet
inside the actual dispatch slot immediately before posting. A preflight proof
is never cached as later submission authority. This function never submits a
model, prepares an AutomationRun, writes ERP, sends a message or falls back.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time

from open_webui.utils.agentic_bridge import digest, packet_digest, stable_run_id
from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable

PROTOCOL_VERSION = 1
MAX_PROOF_BYTES = 16000
MAX_PROOF_SECONDS = 60
PROOF_ID = re.compile(r'[A-Za-z0-9_.:@+-]{1,160}')
SCOPE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}')
PROOF_FIELDS = {
    'protocol_version',
    'scope',
    'scope_digest',
    'enforced',
    'read_only',
    'default_deny',
    'enforcement_mode',
    'enforced_tool_scopes',
    'external_write_allowlist',
    'proof_id',
    'issued_at',
    'expires_at',
}
BLOCKER = 'The owner relay has no verified physical tool-scope enforcement; scoped relay proxy rollout is required'


def _unavailable():
    return CapabilityUnavailable(BLOCKER)


def _proof(value, scope, *, now):
    if not isinstance(value, dict) or set(value) != PROOF_FIELDS:
        raise _unavailable()
    issued = value.get('issued_at')
    expiry = value.get('expires_at')
    external = value.get('external_write_allowlist')
    if (
        type(value.get('protocol_version')) is not int
        or value['protocol_version'] != PROTOCOL_VERSION
        or value.get('enforced') is not True
        or value.get('read_only') is not True
        or value.get('default_deny') is not True
        or value.get('enforcement_mode') != 'scoped_tool_proxy'
        or value.get('scope') != scope
        or value.get('scope_digest') != digest(scope)
        or value.get('enforced_tool_scopes') != scope['tool_scopes']
        or not isinstance(value.get('proof_id'), str)
        or not PROOF_ID.fullmatch(value['proof_id'])
        or type(issued) is not int
        or type(expiry) is not int
        or not now - 5 <= issued <= now + 5
        or not now < expiry <= issued + MAX_PROOF_SECONDS
        or not isinstance(external, list)
        or len(external) > len(scope['tool_scopes'])
        or any(not isinstance(item, str) for item in external)
        or len(set(external)) != len(external)
        or not set(external) <= set(scope['tool_scopes'])
    ):
        raise _unavailable()
    return value


async def require_relay_scope(row, packet, owner_id, *, run_id=None, db_path=None):
    """Verify a fresh actual relay proof, with no bypass or caller-selected URL."""
    try:
        import aiohttp
        from open_webui.utils import agentic_governance as ledger
        from open_webui.utils.agentic_contracts.validator import validate_work_packet
        from open_webui.utils.abitdot_primary import primary_dot_for_owner, dot_primary_key
        from open_webui.utils.abitdot_primary_relay import (
            relay_account_for_owner,
            validated_local_relay_url,
            ACCOUNT_ID,
        )
        from open_webui.routers.chatgpt_relay import MESSAGE_URL, MESSAGE_TOKEN_FILE, _credentials

        binding = packet['binding']
        expected_run = stable_run_id(row['invocation_id'])
        actual_run = run_id or row.get('automation_run_id') or expected_run
        if (
            not isinstance(owner_id, str)
            or not owner_id
            or len(owner_id) > 190
            or actual_run != expected_run
            or row.get('automation_run_id') not in (None, expected_run)
            or any(
                row.get(key) != value
                for key, value in {
                    'erp_project': packet['project_id'],
                    'erp_task': packet['task_id'],
                    'target_role': binding['role'],
                    'agent_id': binding['agent_id'],
                }.items()
            )
        ):
            raise _unavailable()
        registered = next(
            (
                item
                for item in ledger.roster(row['erp_project'], db_path)
                if item['agent_id'] == row['agent_id'] and item['role'] == row['target_role']
            ),
            None,
        )
        if (
            not registered
            or registered.get('owner_id') != owner_id
            or registered.get('erp_user') != binding['erp_user']
        ):
            raise _unavailable()
        if validate_work_packet(
            packet,
            registered_bindings={
                row['target_role']: {'agent_id': registered['agent_id'], 'erp_user': registered['erp_user']}
            },
        ):
            raise _unavailable()
        dot_id = primary_dot_for_owner(owner_id)
        if not dot_id:
            raise _unavailable()
        scopes = packet['tool_scopes']
        if any(not SCOPE_ID.fullmatch(item) or item.casefold() in {'all', 'admin', 'unrestricted'} for item in scopes):
            raise _unavailable()
        account = await relay_account_for_owner(owner_id)
        if not isinstance(account, str) or not ACCOUNT_ID.fullmatch(account):
            raise _unavailable()
        base = validated_local_relay_url(os.getenv('ABIT_DOT_PRIMARY_RELAY_URL') or MESSAGE_URL)
        selected = os.getenv('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE')
        if selected:
            authorized = Path('/srv/abit-dev-linux/relay-runtime/secrets/relay.token').resolve()
            selected_path = Path(selected).resolve(strict=True)
            if selected_path != authorized or not selected_path.is_file():
                raise _unavailable()
        else:
            selected_path = MESSAGE_TOKEN_FILE
        token, _ = _credentials(selected_path)
        if not isinstance(token, str) or not token:
            raise _unavailable()
        key = dot_primary_key(owner_id, row['agent_id'], actual_run)
        scope = {
            'owner': owner_id,
            'account_id': account,
            'dot_id': dot_id,
            'project_id': packet['project_id'],
            'task_id': packet['task_id'],
            'role': binding['role'],
            'agent_id': binding['agent_id'],
            'erp_user': binding['erp_user'],
            'repository': packet['repository'],
            'environment': packet['environment'],
            'tool_scopes': sorted(scopes),
            'work_packet_digest': packet_digest(packet),
            'invocation_id': row['invocation_id'],
            'run_id': actual_run,
            'job_id': 'aibit_' + hashlib.sha256(('primary:' + key).encode()).hexdigest()[:48],
        }
        body = {'protocol_version': PROTOCOL_VERSION, 'scope': scope, 'scope_digest': digest(scope)}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False) as client:
            async with client.post(
                base + '/dot/scope', json=body, headers={'Authorization': 'Bearer ' + token}, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise _unavailable()
                raw = await response.content.read(MAX_PROOF_BYTES + 1)
                if len(raw) > MAX_PROOF_BYTES:
                    raise _unavailable()
                proof = json.loads(raw)
        return _proof(proof, scope, now=int(time.time()))
    except asyncio.CancelledError:
        raise
    except CapabilityUnavailable:
        raise
    except Exception:
        raise _unavailable() from None

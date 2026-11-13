"""Versioned, authenticated ERP bridge. This module never executes model text."""

from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

RPC = '/api/method/abit_ai_assistant.agentic_control.'
EVENT_ID = re.compile(r'[A-Za-z0-9_.:@+-]{12,160}')
MAX_EVENT_BYTES = 220_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def packet_digest(packet):
    return digest({k: v for k, v in packet.items() if k != 'content_sha256'})


def signing_headers(event, secret, *, timestamp=None):
    event_id = event.get('event_id')
    if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
        raise ValueError('Invalid durable event identity')
    stamp = str(int(time.time()) if timestamp is None else int(timestamp))
    value = stamp + '\n' + event_id + '\n' + canonical(event)
    return {
        'X-ABIT-Timestamp': stamp,
        'X-ABIT-Event-ID': event_id,
        'X-ABIT-Signature': hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest(),
    }


def verify_event(event, headers, secret, *, now=None):
    if not isinstance(event, dict) or event.get('schema_version') != 1:
        raise ValueError('Unsupported event schema')
    if len(canonical(event).encode()) > MAX_EVENT_BYTES:
        raise ValueError('Event exceeds the bridge limit')
    if not isinstance(secret, str) or len(secret) < 32:
        raise ValueError('Bridge is not configured')
    normalized = {k.lower(): v for k, v in headers.items()}
    stamp = normalized.get('x-abit-timestamp', '')
    if not re.fullmatch(r'[0-9]{10}', str(stamp)) or abs((time.time() if now is None else now) - int(stamp)) > 300:
        raise ValueError('Bridge signature expired')
    expected = signing_headers(event, secret, timestamp=int(stamp))
    if normalized.get('x-abit-event-id') != event.get('event_id') or not hmac.compare_digest(
        str(normalized.get('x-abit-signature', '')), expected['X-ABIT-Signature']
    ):
        raise ValueError('Invalid bridge signature')
    return event['event_id']


def bridge_secret(config, root=None):
    root = Path(root or '/srv/abit-dev-linux/abit-ai')
    path = Path(
        config.get('agentic_bridge_token_file')
        or os.getenv('ABIT_AGENTIC_BRIDGE_TOKEN_FILE')
        or root / 'config/erp-assignment/control-token'
    )
    secret = path.read_text().strip()
    if len(secret) < 32:
        raise ValueError('Governance bridge credential is unavailable')
    return secret


def validate_packet_identity(packet, project, task, role, agent, revision=None):
    if (
        not isinstance(packet, dict)
        or packet.get('schema_version') != 1
        or packet.get('project_id') != project
        or packet.get('task_id') != task
        or not isinstance(packet.get('task_revision'), str)
        or not packet['task_revision']
        or revision is not None
        and packet['task_revision'] != revision
    ):
        raise ValueError('ERP work packet identity or revision changed')
    binding = packet.get('binding') or {}
    if binding.get('project_id') != project or binding.get('role') != role or binding.get('agent_id') != agent:
        raise ValueError('ERP work packet has a different agent binding')
    if not isinstance(binding.get('erp_user'), str) or not binding['erp_user']:
        raise ValueError('ERP work packet lacks its verified shared role user')
    if packet.get('content_sha256') != packet_digest(packet):
        raise ValueError('ERP work packet fingerprint does not match')
    return packet


class ERPBridge:
    """Only fixed ERP RPCs; redirects/arbitrary destinations are never accepted."""

    def __init__(self, config):
        from open_webui.utils.erp_task_resolution import ERPClient

        self.config = config
        self.erp = ERPClient(config['erp_token_file'])
        self.secret = bridge_secret(config)

    async def register(self, project, revision, records):
        headers = {'X-ABIT-Agentic-Bridge': self.secret}
        for record in records:
            args = {
                'project_id': project,
                'agent_role': record['role'],
                'erp_ai_user': record['erp_user'],
                'abit_agent_id': record['agent_id'],
                'slot': 'primary',
                'source_project_revision': revision,
                'provision_event_id': 'agov-provision-' + digest([project, record['role'], revision])[:40],
            }
            await self.erp.request(
                'POST',
                '/api/method/abit_ai_assistant.project_agent_bindings.bind_project_agent',
                json=args,
                headers=headers,
            )
            receipt = await self.erp.request(
                'POST',
                '/api/method/abit_ai_assistant.project_agent_bindings.verify_project_agent_binding',
                json={
                    k: v
                    for k, v in {**args, 'registry_receipt_sha256': digest(record)}.items()
                    if k in {'project_id', 'agent_role', 'slot', 'abit_agent_id', 'registry_receipt_sha256'}
                },
                headers=headers,
            )
            if (receipt.get('message') or {}).get('verification_state') != 'Verified':
                raise RuntimeError('ERP did not verify its project specialist binding')

    async def close(self):
        await self.erp.client.aclose()

    async def packet(self, project, task, role, agent, revision=None):
        response = await self.erp.request(
            'GET',
            RPC + 'get_work_packet',
            params={
                'project_id': project,
                'task_id': task,
                'role': role,
            },
        )
        return validate_packet_identity(response.get('message'), project, task, role, agent, revision)

    async def publish(self, event):
        headers = {**signing_headers(event, self.secret), 'X-ABIT-Agentic-Bridge': self.secret}
        response = await self.erp.request('POST', RPC + 'receive_result', json={'event': event}, headers=headers)
        receipt = response.get('message')
        if (
            not isinstance(receipt, dict)
            or receipt.get('event_id') != event['event_id']
            or receipt.get('run_id') != event['run_id']
            or receipt.get('durable') is not True
            or receipt.get('task_id') != event['task_id']
            or not receipt.get('modified')
            or not receipt.get('status')
        ):
            raise RuntimeError('ERP did not confirm the original result; preserve it without replay')
        return receipt

    async def create_task(self, packet):
        event = {'schema_version': 1, 'event_id': packet['idempotency_key'], 'payload': packet}
        headers = {**signing_headers(event, self.secret), 'X-ABIT-Agentic-Bridge': self.secret}
        response = await self.erp.request('POST', RPC + 'create_agent_task', json={'packet': packet}, headers=headers)
        result = response.get('message')
        if not isinstance(result, dict) or not result.get('task_id'):
            raise RuntimeError('ERP task creation acknowledgment is unknown; reconcile its key')
        return result


def stable_run_id(invocation):
    return str(uuid5(NAMESPACE_URL, 'https://abit.ai/agentic/run/' + invocation))

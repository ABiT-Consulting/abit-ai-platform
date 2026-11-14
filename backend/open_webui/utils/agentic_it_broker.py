"""Peer-authenticated fixed IT broker transport; never a command/shell service.

Core remains unprivileged. The root broker registers reviewed typed resources
and starts an independent, once-journalled worker for an admitted original.
Accepted work may finish its safe checkpoint after a later Hold/Stop; that does
not grant another dispatch. Receipt reads require the original consumed proof.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import struct
import time

from open_webui.utils.agentic_bridge import same_business_context
from open_webui.utils.agentic_it_changes import (ChangeReaders, TrustedChangeExecutor, OfficialSignatureVerifier,
    TrustedCapacityConnector, OPERATIONS, canonical, digest, plan_authority_digest)

MAX_FRAME = 128 * 1024
DOMAIN = b'ABIT-IT-BROKER-v1\n'


def require(value, message):
    if not value:
        raise ValueError(message)


async def call(callback, *args, **kwargs):
    result = callback(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _mac(key, value):
    return hmac.new(key, DOMAIN + canonical(value).encode(), hashlib.sha256).hexdigest()


def _key(path, owner):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'Canonical broker key path required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 32, 'Broker key owner/mode differs')
        return os.read(descriptor, 33)
    finally:
        os.close(descriptor)


def _peer(writer):
    raw = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    return struct.unpack('3i', raw)  # PID, effective UID, effective GID


async def _send(writer, body, key):
    raw = canonical({'body': body, 'mac': _mac(key, body)}).encode()
    require(len(raw) <= MAX_FRAME, 'Broker response exceeds fixed bound')
    writer.write(struct.pack('!I', len(raw)) + raw)
    await writer.drain()


async def _read(reader, key):
    size = struct.unpack('!I', await asyncio.wait_for(reader.readexactly(4), 15))[0]
    require(0 < size <= MAX_FRAME, 'Broker frame exceeds fixed bound')
    raw = await asyncio.wait_for(reader.readexactly(size), 15)
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {'body','mac'} and isinstance(value['body'], dict)
            and isinstance(value['mac'], str) and hmac.compare_digest(value['mac'], _mac(key, value['body'])),
            'Broker frame authentication failed')
    return value['body']


class BrokerClient:
    def __init__(self, socket_path, key_file, *, authorize, server_uid=0, client_uid=None):
        self.path = Path(socket_path)
        require(self.path.is_absolute() and self.path.parent.resolve() == self.path.parent, 'Canonical broker socket required')
        self.key = _key(key_file, os.geteuid() if client_uid is None else client_uid)
        require(callable(authorize) and type(server_uid) is int, 'Trusted Core authorization callback required')
        self.authorize, self.server_uid = authorize, server_uid

    async def request(self, operation, payload):
        require(operation in {'prepare','execute','observe','verify'}, 'Unsupported fixed broker operation')
        request = {'schema_version': 1, 'operation': operation, 'payload': payload,
                   'requested_at': int(time.time()), 'nonce': secrets.token_hex(24)}
        require(len(canonical(request).encode()) <= MAX_FRAME - 256, 'Broker request exceeds fixed bound')
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.path), 5)
        try:
            require(_peer(writer)[1] == self.server_uid, 'Broker server peer identity differs')
            await _send(writer, request, self.key)
            challenge = await _read(reader, self.key)
            require(set(challenge) == {'challenge','request_sha256','expires_at'}
                    and challenge['request_sha256'] == digest(request)
                    and type(challenge['expires_at']) is int and int(time.time()) < challenge['expires_at'] <= int(time.time()) + 30,
                    'Broker authority challenge differs or expired')
            grant = await call(self.authorize, operation, deepcopy(payload))
            require(isinstance(grant, dict) and grant.get('authorized') is True, 'Core did not authorize this exact broker operation')
            await _send(writer, {**challenge, 'grant': grant}, self.key)
            result = await _read(reader, self.key)
            require(result.get('request_sha256') == digest(request), 'Broker reply belongs to another request')
            require(result.get('ok') is True, 'Fixed IT broker rejected this original operation')
            return result['result']
        finally:
            writer.close()
            await writer.wait_closed()


class BrokerServer:
    """Only typed root registration supplies readers, execution and observations."""

    def __init__(self, socket_path, key_file, *, client_uid, prepare, launch_original, observe_original,
                 key_owner=0, socket_gid=None, verify_peer=None, verify_original=None):
        require(all(callable(item) for item in (prepare, launch_original, observe_original)), 'Fixed server callbacks required')
        self.path, self.client_uid, self.socket_gid = Path(socket_path), client_uid, socket_gid
        self.key = _key(key_file, key_owner)
        self.prepare, self.launch, self.observe = prepare, launch_original, observe_original
        require(verify_peer is None or callable(verify_peer), 'Kernel peer verifier must be server code')
        require(verify_original is None or callable(verify_original), 'Independent QA reader must be server code')
        self.verify_peer,self.verify_original=verify_peer,verify_original
        self.capacity = asyncio.Semaphore(4)
        self.nonces = {}
        self.accepted = set()

    async def start(self):
        require(self.path.is_absolute() and self.path.parent.resolve() == self.path.parent, 'Canonical broker socket required')
        parent = self.path.parent.stat()
        private = parent.st_uid in {os.geteuid(), self.client_uid} and stat.S_IMODE(parent.st_mode) == 0o700
        inherited = parent.st_uid == os.geteuid() and self.socket_gid is not None \
            and parent.st_gid == self.socket_gid and stat.S_IMODE(parent.st_mode) == 0o2710
        require(private or inherited,
                'Private fixed broker socket directory required')
        # Stale sockets require an operator-verified stopped-service cleanup.
        require(not self.path.exists(), 'Broker socket already exists; never replace another instance')
        self.server = await asyncio.start_unix_server(self._handle, path=self.path, limit=MAX_FRAME + 4)
        self.path.chmod(0o600 if self.socket_gid is None else 0o660)
        if self.socket_gid is not None and self.path.stat().st_gid != self.socket_gid:
            os.chown(self.path, os.geteuid(), self.socket_gid)
        self.socket_identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        return self

    async def close(self):
        self.server.close(); await self.server.wait_closed()
        # Accepted launch work remains independent of socket lifetime.
        if self.path.exists() and (self.path.stat().st_dev, self.path.stat().st_ino) == self.socket_identity:
            self.path.unlink()

    async def _handle(self, reader, writer):
        try:
            peer=_peer(writer)
            require(peer[1] == self.client_uid, 'Broker client peer identity differs')
            async with self.capacity:
                peer_proof=await call(self.verify_peer,*peer) if self.verify_peer else {'pid':peer[0],'uid':peer[1],'gid':peer[2]}
                request = await _read(reader, self.key)
                now = int(time.time())
                require(set(request) == {'schema_version','operation','payload','requested_at','nonce'}
                        and request['schema_version'] == 1 and request['operation'] in {'prepare','execute','observe','verify'}
                        and type(request['requested_at']) is int and abs(now-request['requested_at']) <= 30
                        and isinstance(request['nonce'], str) and len(request['nonce']) == 48
                        and isinstance(request['payload'], dict), 'Invalid broker request')
                self.nonces = {key: expiry for key, expiry in self.nonces.items() if expiry >= now}
                require(request['nonce'] not in self.nonces, 'Broker request nonce was already observed')
                self.nonces[request['nonce']] = now + 60
                challenge = {'challenge': secrets.token_hex(32), 'request_sha256': digest(request), 'expires_at': now + 30}
                await _send(writer, challenge, self.key)
                authority = await _read(reader, self.key)
                require(all(authority.get(key) == value for key, value in challenge.items())
                        and set(authority) == set(challenge) | {'grant'} and int(time.time()) < challenge['expires_at'],
                        'Broker authority answer differs or expired')
                grant = authority['grant']
                require(isinstance(grant, dict) and grant.get('authorized') is True
                        and grant.get('request_payload_sha256') == digest(request['payload'])
                        and grant.get('mode') == request['operation'], 'Broker grant does not bind this exact request')
                payload = request['payload']; operation = request['operation']
                if self.verify_peer:
                    require(await call(self.verify_peer,*peer)==peer_proof,'Core process identity changed during authorization')
                grant={**grant,'kernel_peer':peer_proof}
                if operation == 'prepare':
                    require(set(payload) == {'packet'}, 'Preparation accepts one authoritative packet only')
                    result = await call(self.prepare, payload['packet'])
                elif operation == 'execute':
                    require(set(payload) == {'action_id','invocation_id','run_id','plan'}, 'Execution accepts one original plan tuple only')
                    require(grant.get('consumed') is True and grant.get('receipt_id')
                            and all(grant.get(key) == payload[key] for key in ('action_id','invocation_id','run_id'))
                            and grant.get('plan_digest') == payload['plan'].get('plan_digest'), 'Consumed original authority is missing')
                    # Independent worker admission is shielded from client loss.
                    task = asyncio.create_task(call(self.launch, deepcopy(payload), deepcopy(grant)))
                    self.accepted.add(task); task.add_done_callback(self.accepted.discard)
                    result = await asyncio.shield(task)
                elif operation=='observe':
                    require(set(payload) == {'action_id','invocation_id','run_id','plan_digest'}, 'Observation accepts one original receipt tuple only')
                    require(all(grant.get(key) == payload[key] for key in payload), 'Original receipt grant tuple differs')
                    result = await call(self.observe, payload)
                else:
                    require(self.verify_original is not None and grant.get('role')=='qa'
                            and set(payload)=={'action_id','invocation_id','run_id','plan_digest','project_id','task_id',
                                'qa_invocation_id','qa_run_id','qa_agent_id','native_implementation_receipt_sha256'}
                            and all(grant.get(key)==value for key,value in payload.items()),'Independent QA authority differs')
                    result=await call(self.verify_original,payload)
                await _send(writer, {'ok': True, 'request_sha256': digest(request), 'result': result}, self.key)
        except Exception:
            # No raw exception, paths, authority or package content is exported.
            try:
                await _send(writer, {'ok': False, 'error': 'fixed_original_operation_rejected'}, self.key)
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class RemotePhysicalExecutor(TrustedChangeExecutor):
    def __init__(self, client, *, resources, run_lookup):
        require(isinstance(client, BrokerClient) and callable(run_lookup), 'Typed local broker client and run lookup required')
        self.client, self.resources, self.run_lookup = client, frozenset(resources), run_lookup
        super().__init__(self._execute, self._observe)

    def supports(self, operation, resource_id, scope=None, profile=None):
        return (operation, resource_id) in self.resources

    async def _execute(self, plan, action_id, invocation_id, argv):
        require(list(argv) == plan['fixed_argv'] and self.supports(plan['operation'], plan['resource_id']),
                'Unregistered broker resource or plan command differs')
        result = await self.client.request('execute', {'plan': plan, 'action_id': action_id,
                    'invocation_id': invocation_id, 'run_id': self.run_lookup(invocation_id)})
        # The broker returns promptly after independent durable worker admission.
        if result.get('state') != 'completed':
            from open_webui.utils.agentic_it_changes import ChangeOutcomeUncertain
            raise ChangeOutcomeUncertain('Original physical worker is unsettled; observe its protected receipt only')
        return result

    async def _observe(self, action_id, invocation_id, plan_digest):
        return await self.client.request('observe', {'action_id': action_id, 'invocation_id': invocation_id,
                    'run_id': self.run_lookup(invocation_id), 'plan_digest': plan_digest})


def configured_runtime(config, *, bridge, project_gate, db_path=None):
    """Absent/off is the default; no privilege or adapter is inferred from JSON."""
    selected = config.get('agentic_it_physical_broker') or {}
    if not selected:
        return None
    require(isinstance(selected, dict) and set(selected) <= {'enabled','socket_path','key_file','resources'},
            'Invalid fixed IT broker registration')
    if selected.get('enabled') is not True:
        return None
    resources = selected.get('resources')
    require(isinstance(resources, list) and 0 < len(resources) <= 20
            and all(isinstance(row, dict) and set(row) == {'operation','resource_id'}
                    and row['operation'] in OPERATIONS and isinstance(row['resource_id'], str) for row in resources),
            'Exact server resource registration required')
    from open_webui.utils.agentic_it_physical_runtime import PhysicalRuntime, original_run
    from open_webui.utils import agentic_tool_gateway as gateway
    runtime = None

    async def authorize(operation, payload):
        if operation == 'prepare':
            packet = payload['packet']; project = packet['project_id']
            _, gate = await project_gate(project, config)
            require(gate.get('enabled') is True and gate.get('pm_task_automation_enabled') is True
                    and gate.get('assigned_user') == 'ai-projects@abitcons.com'
                    and gate.get('project_status') in {'Open','Active','In Progress'},
                    'Current Project gates deny physical preparation')
            current = await bridge.packet(project, packet['task_id'], 'it_manager', packet['binding']['agent_id'])
            require(packet.get('binding', {}).get('role') == 'it_manager' and same_business_context(packet, current),
                    'Physical preparation packet changed before read admission')
            return {'authorized': True, 'mode': operation, 'request_payload_sha256': digest(payload)}
        original = runtime._original(payload['invocation_id'], payload['action_id'], db_path)
        require(original is not None, 'Original IT physical intent is missing')
        row, saved, action, plan, consumed = original
        require(payload['run_id'] == row['automation_run_id'] and payload.get('plan_digest', (payload.get('plan') or {}).get('plan_digest')) == plan['plan_digest'],
                'Original broker request tuple differs')
        approval = await runtime._native_approval(bridge, row, action, consumed)
        if operation == 'execute':
            require(saved['state'] == 'uncertain' and payload['plan'] == plan, 'Execution plan differs from original intent')
            profile = gateway._profile(config, row['erp_project'])
            registry = runtime.adapters(bridge=bridge, project_gate=project_gate, config=config, db_path=db_path)
            await gateway._fresh(row['invocation_id'], action, project_gate, config, bridge, profile, registry[action['operation']], db_path)
            now = int(time.time())
            require(approval.get('status') == 'approved' and type(approval.get('approved_at')) is int
                    and type(approval.get('expires_at')) is int and approval['approved_at'] <= now < approval['expires_at'],
                    'Current human approval is required for new physical admission')
        return {**consumed, 'authorized': True, 'mode': operation, 'request_payload_sha256': digest(payload),
                'plan_digest': plan['plan_digest']}

    client = BrokerClient(selected['socket_path'], selected['key_file'], authorize=authorize)
    executor = RemotePhysicalExecutor(client, resources=[(x['operation'], x['resource_id']) for x in resources],
                                      run_lookup=lambda invocation: original_run(invocation, db_path))
    # Root returns the complete prepared plan; individual remote readers are
    # intentionally unavailable. Its native schema and digest are checked again.
    class RemoteRuntime(PhysicalRuntime):
        async def prepare_binding(self, packet, **_kwargs):
            plan = await client.request('prepare', {'packet': packet})
            require(plan.get('plan_digest') == plan_authority_digest(plan), 'Broker prepared authority digest differs')
            return plan

        def adapters(self, **kwargs):
            from open_webui.utils.agentic_tool_gateway import TrustedAdapter
            from open_webui.utils.agentic_it_changes import PreparedChangeUnavailable
            local = super().adapters(**kwargs)
            result = {}
            for operation, adapter in local.items():
                async def preflight(action, scope, profile, operation=operation):
                    require(action['operation'] == operation, 'Registered broker operation differs')
                    row = __import__('open_webui.utils.agentic_governance', fromlist=['find']).find(scope['invocation_id'], db_path, include_private=True)
                    packet = await bridge.packet(row['erp_project'],row['erp_task'],'it_manager',row['agent_id'])
                    plan = await client.request('prepare', {'packet': packet})
                    if plan.get('state') != 'prepared' or not plan.get('executable'):
                        raise PreparedChangeUnavailable(plan)
                    require(plan['plan_digest'] == action['parameters'].get('plan_digest') == plan_authority_digest(plan),
                            'Physical plan changed before approval or execution')
                    return plan
                async def execute(action, scope, before, profile, prepare=preflight):
                    fresh = await prepare(action, scope, profile)
                    require(fresh['plan_digest'] == before['plan_digest'], 'Physical plan changed at execution')
                    # The peer-authenticated challenge verifies exact consumed
                    # native authority and fresh lease/gates again before Root
                    # accepts the independent original worker.
                    receipt = await executor.execute_once(before, action['action_id'], scope['invocation_id'], tuple(before['fixed_argv']))
                    require(receipt.get('state') == 'completed' and receipt.get('receipt_mac')
                            and receipt.get('postcondition', {}).get('verified') is True,
                            'Original physical completion receipt is unverified')
                    return {'executed': True, 'plan_digest': before['plan_digest'], 'original_receipt': receipt,
                            'readback': {**receipt['postcondition'], 'executed': False}}
                async def recover(action, scope, before, profile):
                    receipt = await executor.observe_original(action['action_id'], scope['invocation_id'], before['plan_digest'])
                    return {'executed': False, 'original_receipt': receipt, 'replay_permitted': False}
                result[operation] = TrustedAdapter(preflight, execute, capability=adapter.capability,
                                                    roles=adapter.roles, recover=recover)
            return result
    runtime = RemoteRuntime(ChangeReaders(), executor)
    return runtime

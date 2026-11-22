"""Dedicated root service for one reviewed native verification producer.

No command, path, nonce or authority is accepted from a model/API form. The
nonce comes from a root-owned gate tied to the exact Core scope. This module
does not install a service, issue an authority, or enable the Core route.
"""
from __future__ import annotations
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import secrets
import stat
import socket
import struct
import time

# The root-owned standalone bundle imports only stdlib and this pure contract.
# Normal Core imports use the package; the fixed root loader preloads the exact
# hash-verified contract under its standalone name without application imports.
if __package__ == "open_webui.utils":
    from open_webui.utils.agentic_native_contract import (
        canonical, digest, require, nonce, validate_scope, validate_gate, validate_binding,
        validate_receipt, WORKER_HEAD, WORKER_TREE, PLAN_SHA, PROFILE_SHA, RUNTIME_SHA,
    )
else:
    from agentic_native_contract import (
        canonical, digest, require, nonce, validate_scope, validate_gate, validate_binding,
        validate_receipt, WORKER_HEAD, WORKER_TREE, PLAN_SHA, PROFILE_SHA, RUNTIME_SHA,
    )

MAX_FRAME = 128 * 1024
DOMAIN = b'ABIT-IT-BROKER-v1\n'
SOCKET = Path("/run/abit-native-verification/broker.sock")
KEY = Path("/etc/abit/native-verification-broker/key")


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
    return struct.unpack('3i', raw)

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

ROOT = Path("/var/lib/abit/native-verification")
NATIVE = Path("/var/lib/bigrro-verification/proj0202-native-worker-20261122-v5")
ATTEMPTS = Path("/var/lib/abit/proj0202-native-attempts")
CONTROL = NATIVE / "control/native-worker"
CONTROL_HASHES = {
    "worker_policy.py": "2da646c633577c02fa2438482a89f6e4444241f21c09f54770123b4362240d03",
    "admission.py": "53076dced329cc12a67b264de8aa8a4f62b072df4dd6f2360ffbcd86965fa550",
    "bootstrap.py": "a10f383987448d029e830c4a1237c499cf965a8eaa45c955db4b5434380b0efa",
    "controller.py": "7cb49a7d7805e5d0ecb4345d71c61ad19cd7170a7c982ccdbb3c109f3a918c59",
    "linux_boundary.py": "2e97e05bcc63aa1ebb8d8918ead2bb0a45018297d7dde3b424b58b9cc9a98da4",
    "policy.py": "ec71007fe4b444918805d17731354fe254ffa3c24ace74d9babe76827cf2fb32",
}


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "NATIVE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NATIVE_NONFINITE_JSON")))


def root_directory(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "NATIVE_ROOT_PATH_INVALID")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022,
                    "NATIVE_ROOT_DIRECTORY_UNSAFE")
        return fd
    except BaseException:
        os.close(fd)
        raise


def root_bytes(path, limit=65536, *, immutable=True):
    path = Path(path)
    parent = root_directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and
                not stat.S_IMODE(before.st_mode) & (0o222 if immutable else 0o022) and
                0 < before.st_size <= limit, "NATIVE_ROOT_FILE_UNSAFE")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk); size += len(chunk)
        after = os.fstat(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
                                 item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        require(size == before.st_size <= limit and identity(before) == identity(after) and
                identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == identity(before),
                "NATIVE_ROOT_FILE_CHANGED")
        return b"".join(chunks)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def root_document(path, limit=65536, *, immutable=True):
    raw = root_bytes(path, limit, immutable=immutable)
    value = strict_json(raw)
    require(type(value) is dict, "NATIVE_ROOT_DOCUMENT_INVALID")
    return value, hashlib.sha256(raw).hexdigest()


def write_once(parent, name, value):
    require("/" not in name and name not in {"", ".", ".."}, "NATIVE_RECEIPT_NAME_INVALID")
    directory = root_directory(parent)
    fd = None
    try:
        raw = canonical(value).encode()
        require(len(raw) <= 65536, "NATIVE_ROOT_RECEIPT_TOO_LARGE")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o400, dir_fd=directory)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(raw); handle.flush(); os.fsync(handle.fileno()); os.fchmod(handle.fileno(), 0o400)
        os.fsync(directory)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def gate_file(scope):
    return ROOT / "gates" / (hashlib.sha256(scope["invocation_id"].encode()).hexdigest() + ".json")


def load_gate(scope, *, admission=True):
    validate_scope(scope)
    gate, raw_sha = root_document(gate_file(scope), 16384)
    validate_gate(gate, scope, int(time.time()), admission=admission)
    require(raw_sha == digest(gate), "NATIVE_GATE_CANONICAL_BYTES_REQUIRED")
    return gate


def _authority_window(authority, now):
    try:
        issued = datetime.fromisoformat(authority["issued_at_utc"].replace("Z", "+00:00")).timestamp()
        expires = datetime.fromisoformat(authority["expires_at_utc"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("NATIVE_V4_AUTHORITY_WINDOW_INVALID") from None
    require(issued <= now < expires and 0 < expires - issued <= 7200, "NATIVE_V4_AUTHORITY_EXPIRED")


def verify_inputs(gate):
    """Hash exact root controls and unchanged v4 authority before admission.

    The pinned controller performs its complete independent-gate, provider,
    capacity, namespace and FD3 validation again before releasing the worker.
    """
    execution = gate["execution"]
    manifest, manifest_sha = root_document(NATIVE / "EXECUTION.json", 32768)
    require(manifest_sha == execution["execution_manifest_sha256"], "NATIVE_EXECUTION_MANIFEST_CHANGED")
    require(manifest.get("control_files") == CONTROL_HASHES, "NATIVE_CONTROL_MANIFEST_CHANGED")
    for name, expected in CONTROL_HASHES.items():
        raw = root_bytes(CONTROL / name, 256 * 1024)
        if name == "worker_policy.py":
            old = ('EXECUTION_MANIFEST_SHA256 = "' + execution["execution_manifest_sha256"] + '"').encode()
            require(raw.count(old) == 1, "NATIVE_EXECUTION_MANIFEST_UNBOUND")
            raw = raw.replace(old, b'EXECUTION_MANIFEST_SHA256 = "PENDING_DENY"', 1)
        require(hashlib.sha256(raw).hexdigest() == expected, "NATIVE_CONTROL_BYTES_CHANGED")
    authority, _ = root_document(NATIVE / "authority" / (nonce(execution["nonce"]) + ".json"), 32768)
    require(digest(authority) == execution["authority_sha256"] and authority.get("schema_version") == 4 and
            authority.get("purpose") == "bigrro-combined-disposable-verification" and
            authority.get("nonce") == execution["nonce"] and authority.get("head_sha") == WORKER_HEAD and
            authority.get("tree_sha") == WORKER_TREE and authority.get("plan_sha256") == PLAN_SHA and
            authority.get("executor_profile_sha256") == PROFILE_SHA and
            authority.get("runtime_manifest_sha256") == RUNTIME_SHA and
            authority.get("max_attempts") == 1 and authority.get("max_databases") == 2 and
            authority.get("publication_authorized") is False and authority.get("additional_spend_usd") == 0,
            "NATIVE_ROOT_V4_AUTHORITY_CHANGED")
    _authority_window(authority, int(time.time()))
    return execution


def controller_argv(value, state):
    value = nonce(value)
    require(state == ROOT / "attempts" / value, "NATIVE_CONTROLLER_STATE_INVALID")
    # The controller is isolated in a separately owned one-use system unit, so
    # a lost broker socket does not cancel accepted cleanup. Root is the service
    # manager's default identity; numeric User=0 would change capability setup.
    return ["/usr/bin/systemd-run", "--system", "--quiet", "--no-block",
        "--unit=abit-native-controller-" + value + ".service",
        "--property=Type=exec", "--property=Restart=no", "--property=RuntimeMaxSec=3800",
        "--property=TimeoutStopSec=70", "--property=KillMode=process", "--property=UMask=0077",
        "--property=StandardOutput=append:" + str(state / "controller.log"),
        "--property=StandardError=inherit", "--",
        "/usr/bin/python3.12", "-I", "-B", str(CONTROL / "controller.py"), "--execute", "--nonce", value]


async def launch_controller_once(value, state):
    argv = controller_argv(value, state)
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    try:
        code = await asyncio.wait_for(process.wait(), 10)
    except BaseException:
        # Only the bounded systemd client is ceased; an accepted original unit
        # is never killed or resubmitted because its acknowledgment was lost.
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    require(code == 0, "NATIVE_CONTROLLER_START_ACK_UNKNOWN")
    return {"start_acknowledged": True, "unit": "abit-native-controller-" + value + ".service"}


def empty_receipt(binding, state="uncertain"):
    execution = binding["execution"]
    return {"schema_version": 1, "state": state, "binding_sha256": digest(binding),
            "gate_sha256": binding["gate_sha256"],
            **{key: execution[key] for key in ("nonce", "authority_sha256", "execution_manifest_sha256", "worker_head")},
            "outcome_sha256": None, "worker_result_sha256": None, "reservation_sha256": None,
            "cleanup_confirmed": False, "operations_count": 0,
            "automatic_replay_permitted": False, "authority_issued": False}


def observe_worker(binding):
    execution = binding["execution"]
    value = nonce(execution["nonce"])
    state = NATIVE / "state" / value
    receipt = empty_receipt(binding)
    try:
        outcome, outcome_sha = root_document(state / "OUTCOME.json", immutable=False)
    except FileNotFoundError:
        return receipt
    context, _ = root_document(state / "CONTEXT.json", immutable=False)
    require(context.get("nonce") == value and context.get("manifest_sha256") == execution["execution_manifest_sha256"] and
            context.get("authority_sha256") == execution["authority_sha256"], "NATIVE_OUTCOME_CONTEXT_CHANGED")
    require(outcome.get("purpose") == "PROJ0202_NATIVE_FIXED_WORKER" and outcome.get("nonce") == value and
            outcome.get("worker_head") == WORKER_HEAD and outcome.get("authority_sha256") == execution["authority_sha256"] and
            outcome.get("retry_authorized") is False and outcome.get("original_attempts_modified") is False and
            outcome.get("status") in {"PASSED", "FAILED_RETAINED"}, "NATIVE_ROOT_OUTCOME_CHANGED")
    receipt["outcome_sha256"] = outcome_sha
    receipt["cleanup_confirmed"] = outcome.get("cleanup", {}).get("confirmed") is True
    receipt["state"] = "failed_retained"
    if outcome["status"] == "PASSED":
        bootstrap, _ = root_document(state / "BOOTSTRAP_RESULT.json", immutable=False)
        preexec, _ = root_document(state / "PREEXEC.json", immutable=False)
        reservation, reservation_sha = root_document(ATTEMPTS / ("fresh-cluster-attempt-" + value) / "RESERVATION.json", 32768)
        require(outcome.get("outcome") == bootstrap and receipt["cleanup_confirmed"] and
                bootstrap.get("status") == "PASSED" and bootstrap.get("nonce") == value and
                bootstrap.get("purpose") == "PROJ0202_NATIVE_FIXED_WORKER" and
                bootstrap.get("release_issued") is True and bootstrap.get("retry_authorized") is False and
                bootstrap.get("process", {}).get("exit_code") == 0 and
                bootstrap.get("output_capture", {}).get("confirmed") is True and
                bootstrap.get("descendant_cessation", {}).get("confirmed") is True and
                preexec.get("reservation_sha256") == reservation_sha and
                preexec.get("manifest_sha256") == execution["execution_manifest_sha256"] and
                preexec.get("authority_sha256") == execution["authority_sha256"] and
                reservation.get("nonce") == value and reservation.get("authority_sha256") == execution["authority_sha256"] and
                reservation.get("head_sha") == WORKER_HEAD and reservation.get("tree_sha") == WORKER_TREE and
                reservation.get("plan_sha256") == PLAN_SHA and reservation.get("executor_profile_sha256") == PROFILE_SHA and
                reservation.get("runtime_manifest_sha256") == RUNTIME_SHA and reservation.get("state") == "CONSUMED",
                "NATIVE_ROOT_COMPLETION_UNCONFIRMED")
        receipt.update(state="completed", operations_count=10,
                       worker_result_sha256=bootstrap["worker_result_sha256"], reservation_sha256=reservation_sha)
    return validate_receipt(receipt, binding)


class RootNativeService:
    """Fixed registered root callbacks for the dedicated authenticated socket."""
    def __init__(self):
        require(os.geteuid() == 0, "NATIVE_ROOT_SERVICE_REQUIRED")
        for path in (ROOT, ROOT / "gates", ROOT / "attempts"):
            fd = root_directory(path)
            try:
                require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "NATIVE_ROOT_SERVICE_PRIVATE_DIRECTORY_REQUIRED")
            finally:
                os.close(fd)

    async def prepare(self, payload):
        require(type(payload) is dict and set(payload) == {"scope"}, "NATIVE_PREPARE_EXACT_SCOPE_REQUIRED")
        gate = load_gate(payload["scope"])
        verify_inputs(gate)
        return {"gate": gate, "gate_sha256": digest(gate)}

    def original(self, payload, *, admission=False):
        require(type(payload) is dict and set(payload) == {"binding", "gate_sha256"},
                "NATIVE_ORIGINAL_PAYLOAD_INVALID")
        binding = payload["binding"]
        validate_scope(binding.get("scope") if isinstance(binding, dict) else None)
        gate = load_gate(binding["scope"], admission=admission)
        validate_binding(binding, gate)
        require(payload["gate_sha256"] == digest(gate), "NATIVE_ROOT_GATE_REPLACED")
        return binding, gate, ROOT / "attempts" / nonce(binding["execution"]["nonce"])

    async def launch(self, payload, grant):
        binding, gate, state = self.original(payload, admission=True)
        now = int(time.time())
        require(grant.get("binding_sha256") == digest(binding) and
                grant.get("maintenance_admission") == binding["maintenance_admission"] and
                type(grant.get("issued_at")) is int and type(grant.get("expires_at")) is int and
                grant["issued_at"] <= now < grant["expires_at"] <= min(grant["issued_at"] + 15, gate["expires_at"]),
                "NATIVE_FRESH_CORE_GRANT_REQUIRED")
        verify_inputs(gate)
        parent = root_directory(ROOT / "attempts")
        try:
            try:
                os.mkdir(state.name, 0o700, dir_fd=parent)
            except FileExistsError:
                # A partial or unknown admission is permanently one-use.
                previous, _ = root_document(state / "BINDING.json")
                require(previous == binding, "NATIVE_NONCE_ALREADY_BOUND")
                return observe_worker(binding)
            os.fsync(parent)
        finally:
            os.close(parent)
        write_once(state, "BINDING.json", binding)
        write_once(state, "LAUNCH_INTENT.json", {"binding_sha256": digest(binding),
            "argv_sha256": digest(controller_argv(binding["execution"]["nonce"], state)),
            "phase": "invocation_may_have_started", "automatic_replay_permitted": False})
        # Shielding is also applied by the broker handler. Durable admission
        # precedes the only StartUnit call and survives all unknown outcomes.
        acknowledged = await launch_controller_once(binding["execution"]["nonce"], state)
        write_once(state, "LAUNCH_ACK.json", acknowledged)
        return observe_worker(binding)

    async def observe(self, payload):
        binding, gate, state = self.original(payload)
        previous, _ = root_document(state / "BINDING.json")
        require(previous == binding, "NATIVE_ORIGINAL_BINDING_CHANGED")
        intent, _ = root_document(state / "LAUNCH_INTENT.json")
        require(intent == {"binding_sha256": digest(binding),
            "argv_sha256": digest(controller_argv(binding["execution"]["nonce"], state)),
            "phase": "invocation_may_have_started", "automatic_replay_permitted": False},
            "NATIVE_ORIGINAL_LAUNCH_INTENT_CHANGED")
        return observe_worker(binding)


class NativeBrokerServer:
    """Same authenticated framing, with a fixed standalone three-operation service."""
    def __init__(self, socket_path, key_file, *, client_uid, socket_gid, verify_peer, service):
        require(type(service) is RootNativeService and callable(verify_peer) and
                Path(socket_path) == SOCKET and Path(key_file) == KEY and
                type(client_uid) is int and client_uid > 0 and
                type(socket_gid) is int and socket_gid > 0, "NATIVE_FIXED_ROOT_SERVICE_REQUIRED")
        descriptor = root_directory(KEY.parent)
        os.close(descriptor)
        self.path, self.client_uid, self.socket_gid = SOCKET, client_uid, socket_gid
        self.key = _key(KEY, 0)
        self.native, self.verify_peer = service, verify_peer
        self.capacity, self.nonces, self.accepted = asyncio.Semaphore(4), {}, set()
        self.closing = False

    async def start(self):
        descriptor = root_directory(self.path.parent)
        try:
            parent = os.fstat(descriptor)
            require(parent.st_uid == 0 and parent.st_gid == self.socket_gid and
                    stat.S_IMODE(parent.st_mode) == 0o2710, "NATIVE_SOCKET_DIRECTORY_CHANGED")
        finally:
            os.close(descriptor)
        # A manually bound socket never uses asyncio's stale-path unlink. A
        # second service instance must fail without replacing the original.
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.setblocking(False)
            listener.bind(str(self.path))
            info = self.path.lstat()
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0, "NATIVE_SOCKET_OWNER_CHANGED")
            self.socket_identity = (info.st_dev, info.st_ino)
            os.chown(self.path, 0, self.socket_gid, follow_symlinks=False)
            os.chmod(self.path, 0o660, follow_symlinks=False)
            listener.listen(4)
            self.server = await asyncio.start_unix_server(self._handle, sock=listener, limit=MAX_FRAME+4)
        except BaseException:
            listener.close()
            raise
        return self

    async def close(self):
        self.closing = True
        self.server.close()
        await self.server.wait_closed()
        if self.accepted:
            await asyncio.gather(*tuple(self.accepted), return_exceptions=True)
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        require(stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.socket_identity,
                "NATIVE_SOCKET_REPLACED_DURING_CLOSE")
        self.path.unlink()

    def _finished(self, task):
        self.accepted.discard(task)
        if not task.cancelled():
            task.exception()

    async def _handle(self, reader, writer):
        try:
            peer = _peer(writer)
            require(not self.closing and peer[1] == self.client_uid, "NATIVE_CORE_PEER_CHANGED")
            async with self.capacity:
                peer_proof = await call(self.verify_peer, *peer)
                request = await _read(reader, self.key)
                now = int(time.time())
                require(set(request) == {"schema_version", "operation", "payload", "requested_at", "nonce"} and
                        type(request["schema_version"]) is int and request["schema_version"] == 1 and
                        request["operation"] in {"prepare", "execute", "observe"} and
                        type(request["requested_at"]) is int and abs(now - request["requested_at"]) <= 30 and
                        type(request["nonce"]) is str and len(request["nonce"]) == 48 and
                        type(request["payload"]) is dict, "NATIVE_BROKER_REQUEST_INVALID")
                self.nonces = {key: expiry for key, expiry in self.nonces.items() if expiry >= now}
                require(request["nonce"] not in self.nonces, "NATIVE_BROKER_REQUEST_REPLAYED")
                self.nonces[request["nonce"]] = now + 60
                challenge = {"challenge": secrets.token_hex(32), "request_sha256": digest(request), "expires_at": now + 30}
                await _send(writer, challenge, self.key)
                answer = await _read(reader, self.key)
                require(set(answer) == set(challenge) | {"grant"} and
                        all(answer[key] == value for key, value in challenge.items()) and
                        int(time.time()) < challenge["expires_at"], "NATIVE_BROKER_CHALLENGE_EXPIRED")
                grant = answer["grant"]
                require(not self.closing and type(grant) is dict and grant.get("authorized") is True and
                        grant.get("mode") == request["operation"] and
                        grant.get("request_payload_sha256") == digest(request["payload"]) and
                        await call(self.verify_peer, *peer) == peer_proof, "NATIVE_BROKER_GRANT_CHANGED")
                payload = request["payload"]
                if request["operation"] == "prepare":
                    result = await self.native.prepare(payload)
                elif request["operation"] == "execute":
                    task = asyncio.create_task(self.native.launch(deepcopy(payload), deepcopy(grant)))
                    self.accepted.add(task)
                    task.add_done_callback(self._finished)
                    result = await asyncio.shield(task)
                else:
                    require(grant.get("binding_sha256") == digest(payload.get("binding")),
                            "NATIVE_ORIGINAL_READ_GRANT_CHANGED")
                    result = await self.native.observe(payload)
                await _send(writer, {"ok": True, "request_sha256": digest(request), "result": result}, self.key)
        except Exception:
            try:
                await _send(writer, {"ok": False, "error": "fixed_native_operation_rejected"}, self.key)
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

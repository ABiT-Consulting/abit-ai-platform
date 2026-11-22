from pathlib import Path
import ast,json,hashlib
R=Path(__file__).resolve().parents[3]
P=R/'backend/open_webui/utils/agentic_native_broker.py'
s=P.read_text();wire=(R/'backend/open_webui/utils/agentic_it_broker.py').read_text();tree=ast.parse(wire)
helpers=['call','_mac','_key','_peer','_send','_read']
retained='\n\n'.join(ast.get_source_segment(wire,n) for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in helpers)
s=s.replace('import hashlib\n','import hashlib\nimport hmac\nimport inspect\n').replace('import stat\n','import stat\nimport socket\nimport struct\n')
a='''from open_webui.utils.agentic_it_broker import BrokerServer, _peer, _read, _send, call
from open_webui.utils.agentic_native_contract import (
    canonical, digest, require, nonce, validate_scope, validate_gate, validate_binding,
    validate_receipt, WORKER_HEAD, WORKER_TREE, PLAN_SHA, PROFILE_SHA, RUNTIME_SHA,
)
'''
b='''# The root-owned standalone bundle imports only stdlib and this pure contract.
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
DOMAIN = b'ABIT-IT-BROKER-v1\\n'
SOCKET = Path("/run/abit-native-verification/broker.sock")
KEY = Path("/etc/abit/native-verification-broker/key")


'''+retained+'\n'
if s.count(a)!=1:raise RuntimeError('import anchor changed')
s=s.replace(a,b)
a='''class NativeBrokerServer(BrokerServer):
    """Reuse authenticated framing, with a separate exact three-operation handler."""
    def __init__(self, socket_path, key_file, *, client_uid, socket_gid, verify_peer, service):
        require(type(service) is RootNativeService and callable(verify_peer), "NATIVE_FIXED_ROOT_SERVICE_REQUIRED")
        self.native = service
        super().__init__(socket_path, key_file, client_uid=client_uid, socket_gid=socket_gid,
                         verify_peer=verify_peer, prepare=service.prepare,
                         launch_original=service.launch, observe_original=service.observe)
'''
b='''class NativeBrokerServer:
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
'''
if s.count(a)!=1:raise RuntimeError('class anchor changed')
s=s.replace(a,b)
s=s.replace('require(peer[1] == self.client_uid, "NATIVE_CORE_PEER_CHANGED")','require(not self.closing and peer[1] == self.client_uid, "NATIVE_CORE_PEER_CHANGED")')
s=s.replace('require(type(grant) is dict and grant.get("authorized") is True and','require(not self.closing and type(grant) is dict and grant.get("authorized") is True and')
s=s.replace('task.add_done_callback(self.accepted.discard)','task.add_done_callback(self._finished)')
P.write_text(s)
parsed=ast.parse(s);result={}
for name in helpers:
 old=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
 new=next(n for n in parsed.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
 if ast.dump(old,include_attributes=False)!=ast.dump(new,include_attributes=False):raise RuntimeError('wire drift')
 result[name]=hashlib.sha256(ast.dump(new,include_attributes=False).encode()).hexdigest()
(Path(__file__).resolve().parent/'root-wire-preservation.json').write_text(json.dumps({'retained_helper_asts':result,'source_it_broker_sha256':hashlib.sha256(wire.encode()).hexdigest(),'normal_core_client_unchanged':True,'root_imports_application_broker':False},indent=2)+'\n')
print(hashlib.sha256(P.read_bytes()).hexdigest())

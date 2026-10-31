"""Receive a fixed file manifest over authenticated TLS on the private LAN."""
import argparse,hashlib,hmac,http.server,json,os,pathlib,re,shutil,socket,ssl,tempfile,threading,time
p=argparse.ArgumentParser()
p.add_argument("--state",type=pathlib.Path,required=True)
p.add_argument("--destination",type=pathlib.Path,required=True)
p.add_argument("--bind",default="192.168.253.20")
p.add_argument("--port",type=int,default=28683)
p.add_argument("--ttl",type=int,default=1800)
a=p.parse_args()
os.umask(0o077)
auth=json.loads((a.state/"transfer-auth.json").read_text())
entries=json.loads((a.state/"files.json").read_text())["files"]
root=a.destination.resolve()
root.mkdir(mode=0o700,parents=True,exist_ok=True)
assert 0<len(entries)<=512
paths=set()
for e in entries:
    rel=pathlib.PurePosixPath(e["path"])
    if rel.is_absolute() or any(x in ("..",".","") for x in rel.parts) or str(rel)!=e["path"]:
        raise RuntimeError("Unsafe manifest path")
    if e["path"] in paths or not re.fullmatch(r"[0-9a-f]{64}",e["sha256"]):
        raise RuntimeError("Invalid manifest entry")
    if not isinstance(e["size"],int) or not 0<e["size"]<10*1024**3:
        raise RuntimeError("Invalid file size")
    paths.add(e["path"])
if shutil.disk_usage(root).free < sum(e["size"] for e in entries)+2*1024**3:
    raise RuntimeError("Insufficient staging space")
allowed={"192.168.1.100","192.168.253.1"}
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.minimum_version=ssl.TLSVersion.TLSv1_2
context.load_cert_chain(a.state/"server.crt",a.state/"server.key")
done=set()
def target_path(e):
    target=root/e["path"]
    current=root
    for part in target.relative_to(root).parts:
        current=current/part
        if current.is_symlink():raise RuntimeError("Symlink in destination")
    if target.parent.resolve().is_relative_to(root):
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        return target
    raise RuntimeError("Destination escape")
class Server(http.server.HTTPServer):
    def get_request(self):
        sock,address=super().get_request()
        sock.settimeout(30)
        if address[0] not in allowed:
            sock.close();raise OSError("Source not allowed")
        return context.wrap_socket(sock,server_side=True),address
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args):pass
    def answer(self,code,body=b""):
        self.send_response(code);self.send_header("Content-Length",str(len(body)))
        self.send_header("Connection","close");self.end_headers()
        self.wfile.write(body);self.wfile.flush();self.close_connection=True
    def do_PUT(self):
        if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+auth["token"]):
            return self.answer(403)
        match=re.fullmatch(r"/files/([0-9]+)",self.path)
        if not match or self.headers.get("Transfer-Encoding"):return self.answer(400)
        index=int(match.group(1))
        if index>=len(entries) or index in done:return self.answer(409)
        e=entries[index]
        if self.headers.get("Content-Length")!=str(e["size"]):return self.answer(411)
        temporary=None
        try:
            target=target_path(e)
            if target.exists():return self.answer(409)
            fd,temporary=tempfile.mkstemp(prefix=".upload-",dir=target.parent)
            digest=hashlib.sha256();remaining=e["size"]
            with os.fdopen(fd,"wb") as out:
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise RuntimeError("Incomplete transfer")
                    out.write(chunk);digest.update(chunk);remaining-=len(chunk)
                out.flush();os.fsync(out.fileno())
            if digest.hexdigest()!=e["sha256"]:raise RuntimeError("Digest mismatch")
            os.link(temporary,target)  # Atomic no-replace publication.
            os.unlink(temporary);temporary=None
            directory=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
            done.add(index)
            self.answer(200,e["sha256"].encode())
            print(json.dumps({"completed":len(done),"total":len(entries),"bytes":e["size"]}),flush=True)
            if len(done)==len(entries):
                result={"completed":True,"files":len(done),"bytes":sum(x["size"] for x in entries),"verifiedSha256":True}
                with (root/"transfer-result.json").open("x") as f:json.dump(result,f,indent=2)
                threading.Thread(target=server.shutdown,daemon=True).start()
        except Exception as exc:
            if temporary:
                try:os.unlink(temporary)
                except FileNotFoundError:pass
            print(json.dumps({"transferError":type(exc).__name__,"index":index}),flush=True)
            try:self.answer(500,b"Transfer failed")
            except OSError:pass
    def do_GET(self):self.answer(405)
    def do_POST(self):self.answer(405)
server=Server((a.bind,a.port),Handler)
timer=threading.Timer(a.ttl,server.shutdown);timer.daemon=True;timer.start()
print("Authenticated TLS model receiver ready.",flush=True)
try:server.serve_forever(poll_interval=0.5)
finally:timer.cancel();server.server_close()
if len(done)!=len(entries):raise SystemExit("Transfer incomplete")

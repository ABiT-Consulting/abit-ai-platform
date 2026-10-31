"""Stage the exact official Ollama release without root installation or service activation."""
from pathlib import Path
import hashlib,json,os,subprocess,urllib.request
ROOT=Path("/srv/abit-dev-linux/abit-ai-migration-20261031")
expected="c9490e0f520be21271d969e6703f012268b1d39f8b70dd58854e5bfef7a75bb6"
url="https://github.com/ollama/ollama/releases/download/v0.40.13/ollama-linux-amd64.tar.zst"
archive=ROOT/"transfer/ollama-linux-amd64-v0.40.13.tar.zst"
runtime=ROOT/"ollama/runtime"
os.umask(0o077)
runtime.mkdir(parents=True,exist_ok=True)
h=hashlib.sha256();size=0
with urllib.request.urlopen(url,timeout=30) as response,archive.open("xb") as out:
    while block:=response.read(4*1024*1024):
        h.update(block);out.write(block);size+=len(block)
    out.flush();os.fsync(out.fileno())
if size!=1525346571 or h.hexdigest()!=expected:raise RuntimeError("Official archive verification failed")
print(json.dumps({"archiveVerified":True,"bytes":size,"sha256":expected}),flush=True)
members=subprocess.check_output(["tar","--zstd","-tf",str(archive)],text=True).splitlines()
if any(Path(m).is_absolute() or ".." in Path(m).parts for m in members):raise RuntimeError("Unsafe archive member")
subprocess.run(["tar","--zstd","-xf",str(archive),"-C",str(runtime),"--no-same-owner","--no-same-permissions"],check=True)
subprocess.run([str(runtime/"bin/ollama"),"--version"],check=True)
(ROOT/"ollama/runtime-provenance.json").write_text(json.dumps({"version":"0.40.13","source":url,"sha256":expected,"bytes":size},indent=2))
print("Exact Ollama runtime staged.",flush=True)

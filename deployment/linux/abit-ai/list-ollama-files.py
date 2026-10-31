"""Inventory only manifest-referenced Ollama models and explicitly requested identity files."""
import argparse,hashlib,json,pathlib,re
p=argparse.ArgumentParser()
p.add_argument("--source",type=pathlib.Path,required=True)
p.add_argument("--output",type=pathlib.Path,required=True)
p.add_argument("--include-identity",action="store_true")
a=p.parse_args()
root=a.source.resolve()
rows=[];digests=set();manifests=sorted((root/"models/manifests").rglob("*"))
for path in manifests:
    if not path.is_file():continue
    if path.is_symlink():raise RuntimeError("Unexpected manifest symlink")
    content=path.read_bytes();m=json.loads(content)
    rows.append({"path":path.relative_to(root).as_posix(),"source":str(path),"size":len(content),"sha256":hashlib.sha256(content).hexdigest()})
    for item in [m["config"],*m["layers"]]:
        digest=item["digest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}",digest):raise RuntimeError("Invalid blob digest")
        digests.add(digest)
for digest in sorted(digests):
    path=root/"models/blobs"/digest.replace(":","-")
    if not path.is_file() or path.is_symlink():raise RuntimeError("Required model blob unavailable")
    rows.append({"path":path.relative_to(root).as_posix(),"source":str(path),"size":path.stat().st_size,"sha256":digest.split(":")[1]})
if a.include_identity:
    for name in ["id_ed25519","id_ed25519.pub"]:
        path=root/name
        if not path.is_file() or path.is_symlink():raise RuntimeError("Expected Ollama identity file unavailable")
        content=path.read_bytes()
        rows.append({"path":"home/.ollama/"+name,"source":str(path),"size":len(content),"sha256":hashlib.sha256(content).hexdigest()})
result={"version":"0.40.13","files":rows}
with a.output.open("x",encoding="utf-8") as f:json.dump(result,f,indent=2)
print(json.dumps({"files":len(rows),"uniqueBlobs":len(digests),"bytes":sum(r["size"] for r in rows),"identityIncluded":a.include_identity}))

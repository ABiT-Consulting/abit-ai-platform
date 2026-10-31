"""Create an online staging snapshot; never stops or changes the source runtime."""
from __future__ import annotations
import datetime, hashlib, importlib.metadata, json, os, pathlib, re, shutil, sqlite3, subprocess, tarfile, winreg
import psutil
REPO=pathlib.Path(r"C:\Code\open-webui-custom")
LIVE=pathlib.Path(r"C:\Program Files\Python311\Lib\site-packages\open_webui")
STAGE=pathlib.Path(r"C:\ProgramData\ABiT\OpenWebUIBackups\migration-linux-20261031")
PAYLOAD=STAGE/"payload"
def digest(path):
    with path.open("rb") as stream: return hashlib.file_digest(stream,"sha256").hexdigest()
def git(*args):
    return subprocess.check_output(["git","-C",str(REPO),*args],stderr=subprocess.PIPE)
def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding="utf-8")
def sqlite_backup(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(source.as_uri()+"?mode=ro",uri=True,timeout=30)
    dst=sqlite3.connect(destination)
    try:
        src.backup(dst,pages=1024,sleep=0.05)
        integrity=dst.execute("pragma integrity_check").fetchone()[0]
        if integrity!="ok": raise RuntimeError("SQLite backup integrity failure: "+source.name)
        tables=[row[0] for row in dst.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        counts={name:dst.execute('select count(*) from "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        return {"integrity":integrity,"tables":counts}
    finally: dst.close();src.close()
def main():
    if git("branch","--show-current").decode().strip()!="abit-projects": raise RuntimeError("Unexpected branch")
    if PAYLOAD.exists(): raise RuntimeError("Snapshot payload already exists; refusing overwrite")
    PAYLOAD.mkdir()
    config=PAYLOAD/"config";config.mkdir()
    (PAYLOAD/"runtime").mkdir()
    listener=next((c for c in psutil.net_connections(kind="tcp") if c.status=="LISTEN" and c.laddr.ip=="127.0.0.1" and c.laddr.port==8888),None)
    if not listener or not listener.pid: raise RuntimeError("No IPv4 Open WebUI listener")
    process=psutil.Process(listener.pid)
    runtime_env=process.environ()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"SYSTEM\CurrentControlSet\Services\ABiTOpenWebUI\Parameters") as key:
        values={}
        for index in range(winreg.QueryInfoKey(key)[1]):
            name,value,kind=winreg.EnumValue(key,index);values[name]=value
    write_json(config/"windows-service.json",values)
    secret=pathlib.Path(values.get("AppDirectory",process.cwd()))/".webui_secret_key"
    if not secret.is_file(): raise RuntimeError("Existing application secret file unavailable")
    shutil.copy2(secret,config/"webui-secret-key")
    for name,path in {
        "source.env":REPO/".env",
        "runtime-base.env":LIVE.parent.parent/".env",
        "relay.token":pathlib.Path(r"C:\ProgramData\ABiT\erp-chatgpt-relay.token"),
        "cloudflare-tunnel.token":pathlib.Path(r"C:\ProgramData\ABiT\OpenWebUI\tunnel-token.txt"),
    }.items():
        if path.is_file(): shutil.copy2(path,config/name)
    write_json(config/"installed-distributions.json",sorted(
        [{"name":d.metadata["Name"],"version":d.version} for d in importlib.metadata.distributions() if d.metadata.get("Name")],
        key=lambda row:row["name"].lower()))
    def ignore(directory,names):
        excluded=[]
        for name in names:
            if name in {"__pycache__",".git","node_modules"} or name.startswith("~") or name.endswith((".pyc",".pyo")):
                excluded.append(name)
            if pathlib.Path(directory)==LIVE and (name=="data" or name.startswith("frontend.before") or name.startswith("frontend-before")):
                excluded.append(name)
        return excluded
    print("Copying exact live application, frontend and static files.",flush=True)
    shutil.copytree(LIVE,PAYLOAD/"runtime"/"open_webui",ignore=ignore)
    metadata=LIVE.parent/"open_webui-0.11.1.dist-info"
    shutil.copytree(metadata,PAYLOAD/"runtime"/metadata.name)
    print("Backing up current SQLite stores and copying persistent data.",flush=True)
    data=LIVE/"data";target=PAYLOAD/"data";target.mkdir()
    active={pathlib.Path("webui.db"),pathlib.Path("aibit-chatgpt-relay.sqlite"),pathlib.Path("abit-telegram.sqlite"),pathlib.Path("vector_db")/"chroma.sqlite3"}
    backup_results={}
    for base,dirs,files in os.walk(data):
        relative=pathlib.Path(base).relative_to(data)
        (target/relative).mkdir(exist_ok=True)
        for name in files:
            rel=relative/name
            if rel in active:
                backup_results[rel.as_posix()]=sqlite_backup(data/rel,target/rel)
            elif any(rel==pathlib.Path(str(a)+suffix) for a in active for suffix in ["-wal","-shm"]):
                continue
            else: shutil.copy2(data/rel,target/rel)
    # Restrict the transferred environment to application and configured tool references.
    # Never package unrelated machine or connector secrets.
    allowed={"DATA_DIR","FROM_INIT_PY","HOST","PORT","WEBUI_SECRET_KEY","OLLAMA_API_KEY","KMP_DUPLICATE_LIB_OK","KMP_INIT_AT_FORK"}
    patterns=[r"""os\.(?:getenv|environ\.get)\(\s*['"]([A-Z][A-Z0-9_]*)['"]""",r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]*)['"]"""]
    texts=[]
    for p in (PAYLOAD/"runtime/open_webui").rglob("*.py"):
        try:texts.append(p.read_text(encoding="utf-8-sig"))
        except UnicodeError:pass
    con=sqlite3.connect((target/"webui.db").as_uri()+"?mode=ro",uri=True)
    for table in ["function","tool"]:
        if "content" in {row[1] for row in con.execute('pragma table_info("'+table+'")')}:
            texts.extend(str(row[0] or "") for row in con.execute('select content from "'+table+'"'))
    con.close()
    for content in texts:
        for pattern in patterns:allowed.update(re.findall(pattern,content))
    runtime_env={k:v for k,v in runtime_env.items() if k in allowed}
    write_json(config/"runtime-environment.json",runtime_env)
    print("Archiving authoritative current source, including the existing working changes.",flush=True)
    paths={pathlib.Path(value.decode("utf-8")) for value in git("ls-files","-z").split(b"\0") if value}
    for prefix in ["backend/open_webui","deployment/linux/abit-ai","deployment/linux/aibit-relay","deployment/windows/aibit-relay"]:
        folder=REPO/prefix
        if folder.exists():
            for path in folder.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith((".pyc",".pyo")):
                    rel=path.relative_to(REPO)
                    if "data" not in rel.parts: paths.add(rel)
    with tarfile.open(PAYLOAD/"source-current.tar.gz","w:gz",compresslevel=3) as archive:
        for rel in sorted(paths):
            path=REPO/rel
            if path.is_file() and not any(part in {".env",".webui_secret_key","node_modules",".git"} for part in rel.parts):
                archive.add(path,arcname=rel.as_posix(),recursive=False)
    (PAYLOAD/"source-working.patch").write_bytes(git("diff","--binary","HEAD"))
    manifest={
        "createdUtc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sourceHost":os.environ.get("COMPUTERNAME"),"sourceRoot":str(REPO),
        "sourceCommit":git("rev-parse","HEAD").decode().strip(),"branch":"abit-projects",
        "sourceStatus":git("status","--porcelain=v1","-uno").decode().splitlines(),
        "runtimeVersion":importlib.metadata.version("open-webui"),"sourceProcess":listener.pid,
        "snapshotType":"online-staging-not-final-cutover","databaseBackups":backup_results,
        "environmentVariableNames":sorted(runtime_env),"secretKeyPreserved":True,
        "sourceArchiveSha256":digest(PAYLOAD/"source-current.tar.gz"),
        "runtimeFiles":sum(1 for p in (PAYLOAD/"runtime").rglob("*") if p.is_file()),
        "dataFiles":sum(1 for p in (PAYLOAD/"data").rglob("*") if p.is_file()),
    }
    write_json(PAYLOAD/"manifest.json",manifest)
    print("Creating encrypted-transfer input archive.",flush=True)
    archive_path=STAGE/"snapshot.tar.gz"
    with tarfile.open(archive_path,"w:gz",compresslevel=3) as archive: archive.add(PAYLOAD,arcname="payload")
    result={"snapshotPath":str(archive_path),"bytes":archive_path.stat().st_size,"sha256":digest(archive_path),
            "sourceCommit":manifest["sourceCommit"],"runtimeVersion":manifest["runtimeVersion"],
            "databases":{k:{"integrity":v["integrity"],"tables":len(v["tables"])} for k,v in backup_results.items()},
            "runtimeFiles":manifest["runtimeFiles"],"dataFiles":manifest["dataFiles"]}
    write_json(STAGE/"snapshot-result.json",result)
    print(json.dumps(result),flush=True)
if __name__=="__main__": main()

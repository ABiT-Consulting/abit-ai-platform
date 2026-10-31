"""Verify the transferred online backup and create a separate, inactive-worker test copy."""
from pathlib import Path
import hashlib,json,os,shutil,socket,sqlite3,time
ROOT=Path("/srv/abit-dev-linux/abit-ai-migration-20261031")
payload=ROOT/"staging/payload"
manifest=json.loads((payload/"manifest.json").read_text())
assert manifest["snapshotType"]=="online-staging-not-final-cutover"
checks={}
for relative,expected in manifest["databaseBackups"].items():
    p=payload/"data"/relative
    con=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True)
    assert con.execute("pragma integrity_check").fetchone()[0]=="ok",relative
    for table,count in expected["tables"].items():
        actual=con.execute('select count(*) from "'+table.replace('"','""')+'"').fetchone()[0]
        assert actual==count,(relative,table,count,actual)
    con.close();checks[relative]="integrity and all table counts match"
with (payload/"source-current.tar.gz").open("rb") as stream:
    assert hashlib.file_digest(stream,"sha256").hexdigest()==manifest["sourceArchiveSha256"]
token=(payload/"config/relay.token").read_text().strip()
linux_token=Path("/srv/abit-dev-linux/relay-runtime/secrets/relay.token").read_text().strip()
assert token==linux_token,"The relay credential differs; refuse to change accounts."
environment=json.loads((payload/"config/runtime-environment.json").read_text())
secret=(payload/"config/webui-secret-key").read_text().strip()
assert environment.get("WEBUI_SECRET_KEY",secret)==secret,"Existing secret sources differ."
runtime=ROOT/"staging/runtime";data=ROOT/"staging/smoke-data"
if (ROOT/"staging/verification.json").exists():raise RuntimeError("Verified smoke copy already exists")
with socket.socket() as sock:
    if sock.connect_ex(("127.0.0.1",18888))==0:raise RuntimeError("Smoke server is running; refuse to replace files")
def copy_content_tree(source,destination):
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    for base,dirs,files in os.walk(source):
        relative=Path(base).relative_to(source)
        out=destination/relative
        out.mkdir(mode=0o700,parents=True,exist_ok=True)
        for name in files:
            p=Path(base)/name
            if p.is_symlink():raise RuntimeError("Unexpected source symlink")
            shutil.copyfile(p,out/name)
copy_content_tree(payload/"runtime",runtime)
copy_content_tree(payload/"data",data)
auth=runtime/"open_webui/routers/auths.py"
content=auth.read_text()
assert content.count("import ctypes\n")==1
content=content.replace("import ctypes\n","import ctypes\nimport sys\n",1)
old="    upn = f'{normalized_username}@{domain}'\n\n    try:"
new="    upn = f'{normalized_username}@{domain}'\n\n    validator = _validate_windows_ad_credentials\n    if sys.platform.startswith('linux'):\n        from open_webui.utils.linux_ad import validate_linux_ad_credentials\n\n        validator = validate_linux_ad_credentials\n\n    try:"
assert content.count(old)==1
content=content.replace(old,new,1)
old="asyncio.to_thread(_validate_windows_ad_credentials, upn, password)"
assert content.count(old)==1
content=content.replace(old,"asyncio.to_thread(validator, upn, password)",1)
old="    if not ok:\n        # Common Windows account/credential failures."
new="    if not ok:\n        if error_code == -2:\n            # pamtester does not expose native PAM failure codes.\n            return {'ok': False, 'code': 'AD_AUTH_FAILED'}\n        # Common Windows account/credential failures."
assert content.count(old)==1
content=content.replace(old,new,1)
old="            code = identity.get('code')\n            if code in"
new="            code = identity.get('code')\n            if code == 'AD_AUTH_FAILED':\n                raise HTTPException(\n                    status.HTTP_401_UNAUTHORIZED,\n                    detail='Active Directory sign-in could not be completed.',\n                )\n            if code in"
assert content.count(old)==1
content=content.replace(old,new,1)
content=content.replace("Windows Active Directory authentication failed to execute: %s","Active Directory authentication failed to execute: %s")
content=content.replace("Active Directory LogonUser failed for user %s with Windows error %s","Active Directory validation failed for user %s with code %s")
auth.write_text(content)
relay=runtime/"open_webui/routers/chatgpt_relay.py"
content=relay.read_text()
old='TOKEN_FILE = Path(r"C:\\ProgramData\\ABiT\\erp-chatgpt-relay.token")'
new='TOKEN_FILE = Path(os.environ.get("ABIT_CHATGPT_RELAY_TOKEN_FILE", r"C:\\ProgramData\\ABiT\\erp-chatgpt-relay.token"))'
assert content.count(old)==1
relay.write_text(content.replace(old,new,1))
shutil.copyfile(ROOT/"deployment/linux_ad.py",runtime/"open_webui/utils/linux_ad.py")
con=sqlite3.connect(data/"webui.db")
for key in ["automations.enable","calendar.enable"]:
    con.execute("insert into config(key,value,updated_at) values(?,?,?) on conflict(key) do update set value=excluded.value", (key,"false",time.time_ns()))
for key,value in con.execute("select key,value from config").fetchall():
    if "webhook" in key.lower():
        try: parsed=json.loads(value)
        except (ValueError,TypeError):continue
        if isinstance(parsed,str) and parsed.startswith(("http://","https://")):
            con.execute("update config set value=? where key=?",(json.dumps(""),key))
tables={row[0] for row in con.execute("select name from sqlite_master where type='table'")}
cleared={}
for table in ["timer","timers"]:
    if table in tables:
        cleared[table]=con.execute('select count(*) from "'+table+'"').fetchone()[0]
        con.execute('delete from "'+table+'"')
chat_timers=0
if "chat" in tables and "timer_at" in {row[1] for row in con.execute('pragma table_info("chat")')}:
    chat_timers=con.execute('select count(*) from chat where timer_at is not null').fetchone()[0]
    con.execute('update chat set timer_at=null where timer_at is not null')
file_paths=0
prefix="C:/Program Files/Python311/Lib/site-packages/open_webui/data/"
if "file" in tables and "path" in {row[1] for row in con.execute('pragma table_info("file")')}:
    for file_id,old_path in con.execute('select id,path from "file"').fetchall():
        if isinstance(old_path,str) and old_path.replace("\\","/").lower().startswith(prefix.lower()):
            relative=old_path.replace("\\","/")[len(prefix):]
            target=(data/relative).resolve()
            assert target.is_relative_to(data.resolve())
            con.execute('update "file" set path=? where id=?',(str(target),file_id));file_paths+=1
con.commit();con.close()
tg=sqlite3.connect(data/"abit-telegram.sqlite")
tg.execute("update tg_config set enabled=0");tg.commit();tg.close()
result={"snapshotVerified":True,"sourceArchiveVerified":True,"databases":checks,
        "relayTokenMatches":True,"secretKeyMatches":True,
        "backgroundWorkDisabledInSmokeCopy":True,"clearedSmokeTimers":cleared,"clearedSmokeChatTimers":chat_timers,
        "translatedUploadPaths":file_paths,"runtime":str(runtime),"data":str(data)}
(ROOT/"staging/verification.json").write_text(json.dumps(result,indent=2))
print(json.dumps(result))

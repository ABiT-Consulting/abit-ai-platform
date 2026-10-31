"""Authenticate through normal HTTP using an ephemeral account in the isolated smoke database."""
from pathlib import Path
import argparse,bcrypt,html.parser,json,secrets,sqlite3,time,urllib.error,urllib.request,uuid
parser_args=argparse.ArgumentParser();parser_args.add_argument("--relay-status",action="store_true");args=parser_args.parse_args()
root=Path("/srv/abit-dev-linux/abit-ai-migration-20261031")
db=root/"staging/smoke-data/webui.db"
assert "smoke-data" in db.parts
base="http://127.0.0.1:18888"
user_id=str(uuid.uuid4());email="migration-check-"+user_id+"@example.invalid"
password=secrets.token_urlsafe(32)
con=sqlite3.connect(db)
assert con.execute("select value from config where key='automations.enable'").fetchone()[0]=="false"
now=int(time.time())
con.execute('insert into "user"(id,name,email,role,profile_image_url,last_active_at,updated_at,created_at,info,settings,oauth,scim,variables) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(user_id,"Migration verification",email,"admin","/user.png",now,now,now,"{}","{}","{}","{}","{}"))
con.execute('insert into auth(id,email,password,active) values(?,?,?,1)',(user_id,email,bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode()))
con.commit()
result={"method":"normal password sign-in with temporary smoke-only account","checks":{}}
def request(path,token=None,data=None):
    headers={"Content-Type":"application/json"}
    if token:headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(req,timeout=30) as r:
        content=r.read()
        return r.status,r.headers.get("Content-Type",""),content
try:
    status,kind,raw=request("/api/v1/auths/signin",data={"email":email,"password":password})
    login=json.loads(raw)
    assert status==200 and login["id"]==user_id and login["role"]=="admin"
    token=login["token"]
    result["checks"]["passwordSignIn"]=True
    paths=["/api/v1/auths/","/api/v1/users/","/api/v1/folders/","/api/v1/models/all","/api/v1/chats/","/api/models"]
    if args.relay_status:paths.append("/api/v1/chatgpt-relay/status")
    for path in paths:
        status,kind,raw=request(path,token)
        assert status==200 and "application/json" in kind
        value=json.loads(raw)
        result["checks"][path]={"status":status,"count":len(value) if isinstance(value,list) else len(value["data"]) if isinstance(value,dict) and isinstance(value.get("data"),list) else None}
        if path=="/api/models":
            model_ids={m.get("id") for m in value["data"]}
            baseline=sqlite3.connect((root/"staging/payload/data/webui.db").as_uri()+"?mode=ro",uri=True)
            provider_config=json.loads(baseline.execute("select value from config where key='ollama.api_configs'").fetchone()[0]);baseline.close()
            expected=set()
            for provider in provider_config.values():
                if provider.get("enable",True):
                    prefix=provider.get("prefix_id","")
                    expected.update((prefix+"." if prefix else "")+m for m in provider.get("model_ids",[]))
            assert expected,"Review an unfiltered provider separately before changing catalog expectations."
            result["checks"][path]["configuredOllamaModelsPresent"]=expected.issubset(model_ids)
            assert expected.issubset(model_ids)
    status,kind,raw=request("/")
    assert status==200 and b"ABiT AI" in raw
    class Assets(html.parser.HTMLParser):
        paths=[]
        def handle_starttag(self,tag,attrs):
            d=dict(attrs)
            path=d.get("src") if tag=="script" else d.get("href") if tag=="link" and d.get("rel") in ("stylesheet","modulepreload") else None
            if path and path.startswith(("/","./")):self.paths.append(path[1:] if path.startswith("./") else path)
    parser=Assets();parser.feed(raw.decode())
    assets=[]
    for path in parser.paths[:6]:
        status,kind,raw=request(path if path.startswith("/") else "/"+path)
        assert status==200 and "text/html" not in kind
        if path.startswith("/_app/"):assert len(raw)>0
        assets.append({"path":path,"status":status,"bytes":len(raw)})
    result["checks"]["frontendAssets"]=assets
    result["passed"]=True
finally:
    con.execute("delete from auth where id=?",(user_id,))
    con.execute('delete from "user" where id=?',(user_id,))
    con.commit();con.close()
    ledger=sqlite3.connect(root/"staging/smoke-data/aibit-chatgpt-relay.sqlite")
    for (table,) in ledger.execute("select name from sqlite_master where type='table'").fetchall():
        if "owner" in {row[1] for row in ledger.execute('pragma table_info("'+table.replace('"','""')+'")')}:
            ledger.execute('delete from "'+table.replace('"','""')+'" where owner=?',(user_id,))
    ledger.commit();ledger.close()
    result["temporaryAccountRemoved"]=True
(root/"staging/http-verification.json").write_text(json.dumps(result,indent=2))
print(json.dumps(result))

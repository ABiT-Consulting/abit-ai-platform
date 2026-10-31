"""Explicit connectivity probes. --relay creates one check chat; --cloud tests the existing hosted model."""
from pathlib import Path
import argparse,concurrent.futures,datetime,json,time,urllib.error,urllib.request,uuid
p=argparse.ArgumentParser();p.add_argument("--relay",action="store_true");p.add_argument("--cloud",action="store_true");args=p.parse_args()
root=Path("/srv/abit-dev-linux/abit-ai-migration-20261031")
def call(base,path,data=None,token=None,timeout=180):
    headers={"Content-Type":"application/json"}
    if token:headers["Authorization"]="Bearer "+token
    request=urllib.request.Request(base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request,timeout=timeout) as response:return json.load(response)
def model_probe(model):
    start=time.monotonic()
    r=call("http://127.0.0.1:11434","/api/generate",{"model":model,"prompt":"Reply with exactly OK.","stream":False,"think":False,"keep_alive":0,"options":{"num_predict":16,"num_ctx":2048,"temperature":0}})
    return {"model":model,"completed":r.get("done") is True,"outputBytes":len(r.get("response","").encode()),"expectedText":r.get("response","").strip()=="OK","durationSeconds":round(time.monotonic()-start,2)}
def relay_probe():
    token=Path("/srv/abit-dev-linux/relay-runtime/secrets/relay.token").read_text().strip()
    account=call("http://127.0.0.1:8810","/account",token=token,timeout=30)["account_id"]
    body={"message":"System migration connectivity check. Reply with exactly ABIT_RELAY_OK.","account_id":account,"thread_id":"migration-check:"+str(uuid.uuid4()),"stream":True}
    request=urllib.request.Request("http://127.0.0.1:8810/relay",data=json.dumps(body).encode(),headers={"Content-Type":"application/json","Authorization":"Bearer "+token})
    deadline=time.monotonic()+750
    with urllib.request.urlopen(request,timeout=30) as response:
        for line in response:
            if time.monotonic()>deadline:raise TimeoutError("Relay verification deadline")
            if not line.strip():continue
            event=json.loads(line)
            if event.get("type")=="status":print(json.dumps({"relayStage":event.get("state")}),flush=True)
            if event.get("type")=="error":raise RuntimeError("Relay returned an error")
            if event.get("type")=="complete":
                r=event["result"]
                return {"completed":bool(r.get("success")),"expectedText":"ABIT_RELAY_OK" in r.get("response",""),"accountMatches":r.get("account_id")==account,"conversationCaptured":bool(r.get("conversation_url") or (r.get("project") or {}).get("conversation_url"))}
    raise RuntimeError("Relay stream ended without a completed result")
jobs={"configuredLocalModel":lambda:model_probe("bit-2.5:7b")}
if args.cloud:jobs["cloudModel"]=lambda:model_probe("kimi-k3:cloud")
if args.relay:jobs["browserRelay"]=relay_probe
result={"createdUtc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"checks":{}}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    running={pool.submit(work):name for name,work in jobs.items()}
    for future in concurrent.futures.as_completed(running):
        name=running[future]
        try:result["checks"][name]=future.result()
        except Exception as error:result["checks"][name]={"completed":False,"errorType":type(error).__name__,"httpStatus":getattr(error,"code",None)}
        (root/"staging/integration-verification.json").write_text(json.dumps(result,indent=2))
        print(json.dumps({name:result["checks"][name]}),flush=True)
(root/"staging/integration-verification.json").write_text(json.dumps(result,indent=2))
if not all(v.get("completed") for v in result["checks"].values()):raise SystemExit(1)

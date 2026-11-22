"""One bounded read-only Core health and aggregate job-state observation."""
from pathlib import Path
import datetime,hashlib,json,os,re,sqlite3,stat,subprocess,time
import http.client as http_client
BASE=Path("/srv/abit-dev-linux/abit-ai/data")
DEADLINE=time.monotonic()+20
def need(v,c):
 if not v:raise RuntimeError(c)
def process(pid):
 p=Path("/proc")/str(pid);text=(p/"stat").read_text();fields=text.rsplit(")",1)[1].split()
 return {"pid":pid,"start_ticks":int(fields[19]),"uid":p.stat().st_uid,
         "cmdline_sha256":hashlib.sha256((p/"cmdline").read_bytes()).hexdigest()}
def service():
 out=subprocess.run(["systemctl","show","abit-ai.service","-p","MainPID","-p","ActiveState","-p","SubState","-p","Result"],capture_output=True,text=True,timeout=5,check=True).stdout
 row=dict(line.split("=",1)for line in out.splitlines()if "="in line)
 need(set(row)=={"MainPID","ActiveState","SubState","Result"},"EXACT_SERVICE_PROPERTY_SET")
 return row
def http(port,path,limit):
 conn=http_client.HTTPConnection("127.0.0.1",port,timeout=4)
 try:
  conn.request("GET",path,headers={"Connection":"close"});resp=conn.getresponse();raw=resp.read(limit+1)
  need(len(raw)<=limit,"BOUNDED_PUBLIC_RESPONSE");value=json.loads(raw)
  return resp.status,value
 finally:conn.close()
def rows(relative,sql,parameters=()):
 path=BASE/relative;before=path.lstat();need(stat.S_ISREG(before.st_mode)and not path.is_symlink(),"EXACT_REGULAR_LEDGER")
 need(time.monotonic()<DEADLINE,"READ_ONLY_OBSERVATION_DEADLINE")
 db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=2)
 try:
  db.execute("PRAGMA query_only=ON");db.set_progress_handler(lambda:int(time.monotonic()>=DEADLINE),1000)
  db.execute("BEGIN");result=list(db.execute(sql,parameters));need(len(result)<=64,"BOUNDED_AGGREGATE_ROWS")
 finally:db.close()
 after=path.lstat();need((before.st_dev,before.st_ino,before.st_uid,before.st_mode)==(after.st_dev,after.st_ino,after.st_uid,after.st_mode),"LEDGER_IDENTITY_CHANGED")
 return result
def counts(rows):
 out={}
 for state,count in rows:
  need(type(state)is str and re.fullmatch("[a-z_]{1,48}",state)and type(count)is int and count>=0,"FIXED_STATE_COUNT")
  out[state]=count
 return out
unit=service();pid=int(unit["MainPID"]);need(pid>1,"CORE_PROCESS_NOT_RUNNING");before=process(pid)
health_code,health=http(8888,"/health",65536);config_code,config=http(8888,"/api/config",65536)
need(type(health)is dict and type(config)is dict,"PUBLIC_STATUS_OBJECTS")
gate=rows("maintenance-admission/gate.sqlite","SELECT state,epoch FROM gate WHERE id=1")
need(len(gate)==1 and gate[0][0]in{"open","draining","sealed"}and type(gate[0][1])is int,"CURRENT_GATE_STATE")
leases=rows("maintenance-admission/gate.sqlite","SELECT state,CASE WHEN pid=? AND process_start=? THEN 1 ELSE 0 END AS owned,COUNT(*) FROM lease WHERE state IN ('active','unresolved') GROUP BY state,owned",(pid,str(before["start_ticks"])))
lease_out={"current_core":{},"other_processes":{}}
for state,owned,count in leases:
 need(state in {"active","unresolved"}and owned in {0,1}and type(count)is int and count>=0,"BOUNDED_LEASE_COUNTS")
 lease_out["current_core"if owned else"other_processes"][state]=count
specs=(("governance","agentic-governance.sqlite","SELECT state,COUNT(*) FROM governance_invocation GROUP BY state"),
 ("relay","aibit-chatgpt-relay.sqlite","SELECT status,COUNT(*) FROM jobs GROUP BY status"),
 ("dot","abitdot-primary.sqlite","SELECT status,COUNT(*) FROM dot_primary_runs GROUP BY status"),
 ("codex","codex-workspace-runs.sqlite","SELECT state,COUNT(*) FROM runs GROUP BY state"))
jobs={label:counts(rows(path,sql))for label,path,sql in specs}
relay_code,relay=http(8810,"/runtime-status",4096)
need(type(relay)is dict and relay.get("protocol")=="v1_graceful_relay_drain","EXACT_READ_ONLY_RELAY_STATUS")
relay_keys=("pid","draining","server_closed","active_http_handlers","active_relay_turns","erp_active_turns","erp_inspections")
relay_public={key:relay.get(key)for key in relay_keys}
need(service()==unit and process(pid)==before,"CORE_CHANGED_DURING_READ_ONLY_OBSERVATION")
print(json.dumps({"schema_version":1,"at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
 "service":unit,"core_process":before,"health":{"http_status":health_code,"status":health.get("status")},
 "config":{"http_status":config_code,"version":config.get("version")},"port8888_http_reachable":health_code==200 and config_code==200,
 "maintenance":{"state":gate[0][0],"epoch":gate[0][1],"lease_counts":lease_out},
 "stored_job_state_counts":jobs,"relay_runtime_status":{"http_status":relay_code,**relay_public},
 "observation_only":True,"admission_race_excluded":False,"job_rows_exported":0,
 "browser_login_attempted":False,"fabricated_session":False,"mutations":0,"historical_outcomes_settled":False},sort_keys=True))

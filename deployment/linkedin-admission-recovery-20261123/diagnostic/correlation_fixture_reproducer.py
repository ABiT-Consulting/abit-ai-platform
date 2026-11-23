"""Ordinary-user fixture reproducer; never opens a production database.

The fixture body below passed in MCP job4afeb861-c27b-4940-aac6-cc7c66684016.
That invocation supplied identical candidate source in memory from the Windows
authoritative checkout. This persisted reproducer reads the root-exported
candidate at the fixed temporary path and refuses a different source hash.
"""
import tempfile,pathlib,sqlite3,json,hashlib,time
source=pathlib.Path("/srv/abit-dev-linux/tmp/linkedin-admission-recovery-20261123/correlate_original.py").read_text()
assert hashlib.sha256(source.encode()).hexdigest()=="caecdc2b1a85d176627fbe9075c2bf19a9c0f4503352eb3326e932e725e4bf48"
ns={"__name__":"correlation_fixture"}; exec(compile(source,"correlate_original.py","exec"),ns)
checks=[]
def check(value,name):
 assert value,name
 checks.append(name)
try: ns["main"]()
except ns["Refused"] as e: check(str(e)=="Exact delegated service identity and host required","ordinary_caller_refused_before_database_access")
else: raise AssertionError("identity guard did not refuse")
with tempfile.TemporaryDirectory(prefix="linkedin-correlation-fixture-") as temp:
 root=pathlib.Path(temp); paths={k:root/(k+".sqlite") for k in ("gate","webui","dot","relay")}; ns["DATABASES"]=paths
 def fixture_metadata(path):
  assert path in paths.values() and path.resolve()==path
  s=path.stat()
  return {"path":str(path),"device":s.st_dev,"inode":s.st_ino,"uid":s.st_uid,"gid":s.st_gid,"mode":oct(s.st_mode&511),"bytes":s.st_size}
 ns["metadata"]=fixture_metadata
 agent=ns["AGENT"]; lease=ns["LEASE"]; parent=ns["PARENT"]; created=ns["EXPECTED_LEASE"]["created_ns"]; owner="03519eef-e01d-4c45-8e07-266b35d0ac91"
 run1="11111111-1111-1111-1111-111111111111"; run2="22222222-2222-2222-2222-222222222222"; run3="33333333-3333-3333-3333-333333333333"
 chat1="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"; chat2="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"; chat3="cccccccc-cccc-cccc-cccc-cccccccccccc"; secret="SECRET-CANARY-DO-NOT-PRINT"
 gate=sqlite3.connect(paths["gate"])
 gate.executescript("""CREATE TABLE gate(id INTEGER PRIMARY KEY,state TEXT,epoch INTEGER,command_id TEXT,actor TEXT);
 CREATE TABLE lease(id TEXT PRIMARY KEY,kind TEXT,reference TEXT,epoch INTEGER,pid INTEGER,process_start TEXT,created_ns INTEGER,state TEXT,parent_id TEXT,receipt_sha256 TEXT);
 CREATE UNIQUE INDEX one_live_admission ON lease(kind,reference) WHERE state IN ('active','unresolved');
 CREATE TABLE audit(seq INTEGER PRIMARY KEY,event_id TEXT,kind TEXT,payload TEXT,created_ns INTEGER);
 CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Maintenance audit is immutable');END;
 CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Maintenance audit is immutable');END;
 INSERT INTO gate VALUES(1,'open',100,'known-command','known-actor');""")
 gate.execute("INSERT INTO lease VALUES(?,?,?,?,?,?,?,?,?,?)",(lease,"automation",agent,42,4132708,"73246413",created,"unresolved",parent,None))
 gate.execute("INSERT INTO lease VALUES(?,?,?,?,?,?,?,?,?,?)",(parent,"batch","known-batch",42,4132708,"73246413",created-1000000000,"unresolved",None,None))
 gate.execute("INSERT INTO audit VALUES(?,?,?,?,?)",(8602,"known-event","admitted",json.dumps({"lease":lease,"kind":"automation","reference":agent,"epoch":42,"parent":parent,"secret":secret}),created)); gate.commit(); gate.close()
 web=sqlite3.connect(paths["webui"])
 web.executescript("""CREATE TABLE automation(id TEXT,user_id TEXT,folder_id TEXT,name TEXT,data TEXT,meta TEXT,is_active INTEGER,last_run_at INTEGER,next_run_at INTEGER,attempt_limit INTEGER,attempts_used INTEGER,created_at INTEGER,updated_at INTEGER);
 CREATE TABLE automation_run(id TEXT,automation_id TEXT,chat_id TEXT,status TEXT,lease_until INTEGER,error TEXT,created_at INTEGER);""")
 web.execute("INSERT INTO automation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(agent,owner,"folder","LinkedIn",json.dumps({"prompt":secret}),json.dumps({"secret":secret}),1,created,created+1000,0,3,1,2))
 rows=[(run1,agent,chat1,"error",None,secret,created-100),(run2,agent,chat2,"error",None,secret,created-9999999999999),(run3,"other-agent",chat3,"success",None,secret,created)]
 web.executemany("INSERT INTO automation_run VALUES(?,?,?,?,?,?,?)",rows); web.commit(); web.row_factory=sqlite3.Row
 expected_history=hashlib.sha256(json.dumps([dict(r) for r in web.execute("SELECT id,automation_id,chat_id,status,lease_until,error,created_at FROM automation_run WHERE automation_id=? ORDER BY created_at,id",(agent,))],sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest(); web.close()
 dot=sqlite3.connect(paths["dot"]); dot.execute("CREATE TABLE dot_primary_runs(id TEXT,owner TEXT,automation_id TEXT,original_run_id TEXT,dot_id TEXT,status TEXT,envelope_json TEXT,receipt TEXT,response_text TEXT,conversation_url TEXT,reason TEXT,created_at INTEGER,updated_at INTEGER)")
 key=hashlib.sha256(json.dumps([owner,agent,run2],separators=(",",":")).encode()).hexdigest()
 env={"owner_id":owner,"automation_id":agent,"run_id":run2,"dot_id":"known-dot","idempotency_key":key,"prompt":secret}
 dot.execute("INSERT INTO dot_primary_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(key,owner,agent,run2,"known-dot","unknown",json.dumps(env),secret,secret,"https://chatgpt.com/c/known",secret,1,2)); dot.commit(); dot.close()
 relay=sqlite3.connect(paths["relay"]); relay.execute("CREATE TABLE jobs(owner TEXT,job_id TEXT,chat_id TEXT,project_id TEXT,status TEXT,phase TEXT,created_at INTEGER,updated_at INTEGER,response TEXT,error TEXT,response_message_id TEXT,conversation_url TEXT)")
 relay.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",[(owner,"job1",chat1,"project","unknown","submitted",1,2,secret,secret,"message1",secret),(owner,"job2",chat2,"project","completed","completed",1,2,secret,secret,"message2",secret),(owner,"job3",chat3,"project","completed","completed",1,2,secret,secret,"message3",secret),("other-owner","job4",chat1,"project","completed","completed",1,2,secret,secret,"message4",secret)]); relay.commit(); relay.close()
 observations={}; deadline=time.monotonic()+20
 admission,audit_ids,low,high=ns["observe_gate"](deadline,observations); dots,dot_ids=ns["observe_dot"](deadline,observations)
 webui,actualowner,chats=ns["observe_webui"](deadline,observations,low,high,audit_ids,dot_ids); relays=ns["observe_relay"](deadline,observations,actualowner,chats)
 check(not audit_ids and dot_ids=={run2},"exact_lease_without_fabricated_audit_run_binding")
 check({r["id"] for r in webui["candidate_runs"]}=={run1,run2} and {r["job_id"] for r in relays["jobs"]}=={"job1","job2"},"same_agent_candidate_and_owner_chat_scope")
 check(webui["history"]["count"]==2 and webui["history"]["sha256"]==expected_history,"history_hash_matches_independent_snapshot")
 check(all(not r["proven_original_admission"] for r in webui["candidate_runs"]),"proximity_remains_nonproof")
 check(dots[0]["envelope_identity_matches_row"] and dots[0]["idempotency_key_matches_identity"],"dot_explicit_identity_validated")
 check(all(d["total_changes"]==0 for d in observations.values()),"all_fixture_database_total_changes_zero")
 check(secret not in json.dumps({"admission":admission,"dot":dots,"webui":webui,"relay":relays}),"private_prompt_envelope_receipt_response_error_reason_redacted")
 with ns["readonly"]("webui",time.monotonic()+10,{}) as db:
  try: db.execute("DELETE FROM automation_run")
  except sqlite3.DatabaseError: pass
  else: raise AssertionError("query-only delete accepted")
  check(db.execute("SELECT count(*) FROM automation_run").fetchone()[0]==3,"mutation_refused_and_rows_preserved")
 try:
  with ns["readonly"]("webui",time.monotonic()-1,{}) as db: db.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n").fetchone()
 except sqlite3.OperationalError: checks.append("expired_sql_deadline_interrupts_read")
 else: raise AssertionError("expired deadline accepted")
 try: ns["text_evidence"]("x"*(ns["MAX_FIELD_BYTES"]+1))
 except ns["Refused"]: checks.append("oversize_field_refused")
 else: raise AssertionError("oversize field accepted")
 with ns["readonly"]("webui",time.monotonic()+10,{}) as db:
  try: ns["bounded_rows"](db,"SELECT id FROM automation_run ORDER BY id",(),2)
  except ns["Refused"]: checks.append("row_bound_refuses_partial_completeness")
  else: raise AssertionError("row bound accepted")
print(json.dumps({"source_sha256":hashlib.sha256(source.encode()).hexdigest(),"tests_passed":len(checks),"checks":checks,"fixture_only":True,"privileged_execution":False,"production_database_opened":False},indent=2))

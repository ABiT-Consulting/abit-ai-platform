"""Synthetic SQLite tests only; source may be supplied from authoritative checkout."""
import hashlib,json,pathlib,sqlite3,tempfile,time
if "source" not in globals():
    source=pathlib.Path("/srv/abit-dev-linux/tmp/linkedin-admission-recovery-20261123/observe_legacy_hold.py").read_text()
assert hashlib.sha256(source.encode()).hexdigest()=="2ac9aec1459524b2fa1a5f5281e0fe6e638b5ba2e78418c317ff732837676580"
ns={"__name__":"legacy_hold_fixture_subject"};exec(compile(source,"observe_legacy_hold.py","exec"),ns)
checks=[]
try: ns["main"]()
except ns["Refused"] as e:
    assert str(e)=="No arguments; exact service identity and host required"
    checks.append("ordinary_caller_refused_before_database_access")
else: raise AssertionError("ordinary caller accepted")
with tempfile.TemporaryDirectory(prefix="legacy-hold-fixture-") as temp:
    p=pathlib.Path(temp)/"fixture.sqlite"
    db=sqlite3.connect(p)
    db.executescript("""CREATE TABLE abitdot_relay_events(id TEXT,owner TEXT,event_key TEXT,chat_id TEXT,automation_id TEXT,job_id TEXT,status TEXT,failure TEXT,context_json TEXT,response TEXT,created_at INTEGER,updated_at INTEGER);
    CREATE TABLE jobs(owner TEXT,job_id TEXT,chat_id TEXT,project_id TEXT,status TEXT,phase TEXT,created_at INTEGER,updated_at INTEGER,response_message_id TEXT,response TEXT,error TEXT,conversation_url TEXT);""")
    owner,agent=ns["OWNER"],ns["AGENT"]; chat="11111111-1111-1111-1111-111111111111"
    other_chat="22222222-2222-2222-2222-222222222222";job="aibit_"+"a"*48
    secret="SECRET-CANARY-EXCLUDED";event="33333333-3333-3333-3333-333333333333"
    context=json.dumps({"context":{"automation_id":agent,"chat_id":chat,"origin_message_id":"44444444-4444-4444-4444-444444444444","original_prompt":secret,"chat":{"messages":secret}},"job":{"owner":owner,"job_id":job,"chat_id":chat,"account":secret,"response":secret}})
    db.executemany("INSERT INTO abitdot_relay_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",[
        (event,owner,"known-key",chat,agent,job,"unknown",secret,context,secret,1,2),
        ("other-agent",owner,"other-key",other_chat,"different-agent","aibit_"+"b"*48,"unknown",secret,context,secret,1,2),
        ("other-owner","different-owner","other-key",chat,agent,job,"unknown",secret,context,secret,1,2)])
    db.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",[
        (owner,job,chat,"project","unknown","submitted",1,2,"message1",secret,secret,secret),
        ("different-owner",job,chat,"project","completed","completed",1,2,"message2",secret,secret,secret),
        (owner,"aibit_"+"b"*48,other_chat,"project","completed","completed",1,2,"message3",secret,secret,secret)])
    db.commit();db.close()
    db=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,isolation_level=None);db.row_factory=sqlite3.Row
    db.execute("PRAGMA query_only=ON");db.execute("BEGIN")
    allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_RECURSIVE}
    db.set_authorizer(lambda action,*_:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    result=ns["observe"](db)
    assert len(result["events"])==1 and result["events"][0]["id"]==event
    assert len(result["related_jobs"])==1 and result["related_jobs"][0]["job_id"]==job
    assert result["queried_job_ids"]==[job] and result["queried_chat_ids"]==[chat]
    checks.append("only_exact_owner_agent_events_and_related_owner_jobs")
    assert secret not in json.dumps(result)
    assert all(result["events"][0]["identity_matches"].values())
    checks.append("private_context_failure_response_account_and_error_redacted")
    assert result["legacy_hold_present"] and not result["events"][0]["proven_original_admission"]
    assert not result["events"][0]["provider_readback"] and not result["related_jobs"][0]["provider_readback"]
    checks.append("unknown_event_identified_without_claiming_provider_completion")
    try:db.execute("UPDATE abitdot_relay_events SET status='completed'")
    except sqlite3.DatabaseError:pass
    else:raise AssertionError("mutation accepted")
    assert db.total_changes==0 and db.execute("SELECT count(*) FROM abitdot_relay_events WHERE status='unknown'").fetchone()[0]==3
    checks.append("query_only_mutation_refused_and_total_changes_zero")
    db.close()
print(json.dumps({"source_sha256":hashlib.sha256(source.encode()).hexdigest(),"tests_passed":len(checks),"checks":checks,"fixture_only":True,"privileged_execution":False,"production_database_opened":False},indent=2))

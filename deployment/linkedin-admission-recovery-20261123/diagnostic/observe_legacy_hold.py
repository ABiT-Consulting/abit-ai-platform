"""Fixed service-owned read of legacy Dot holds for the LinkedIn agent.
No application imports, network calls, recovery, dispatch or database writes.
A saved event/job is evidence of identity, not proof of a maintenance-run binding
or provider completion. Execute only through the verified read delegation.
"""
from pathlib import Path
import hashlib, json, os, re, resource, signal, socket, sqlite3, stat, sys, time
from datetime import datetime, timezone

DATABASE = Path("/srv/abit-dev-linux/abit-ai/data/aibit-chatgpt-relay.sqlite")
OWNER = "03519eef-e01d-4c45-8e07-266b35d0ac91"
AGENT = "d4a6e561-cb2a-41d9-93b5-2888624cf8d6"
UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")
JOB = re.compile(r"aibit_[a-f0-9]{48}\Z")
ATOM = re.compile(r"[A-Za-z0-9_.:@/~+\-]{1,256}\Z")
LIMIT, MAX_BYTES, SECONDS = 100, 2 * 1024 * 1024, 30

class Refused(RuntimeError):
    pass

def utc():
    return datetime.now(timezone.utc).isoformat()

def evidence(value):
    if value is None:
        return {"present": False, "bytes": 0, "sha256": None}
    if not isinstance(value, str):
        raise Refused("Expected text or null")
    raw = value.encode()
    if len(raw) > MAX_BYTES:
        raise Refused("Fixed text bound exceeded")
    return {"present": True, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

def safe(value):
    if value is None or type(value) in (int, bool):
        return value
    if isinstance(value, str) and ATOM.fullmatch(value):
        return value
    return evidence(value)

def rows(db, sql, parameters):
    found = db.execute(sql + " LIMIT ?", (*parameters, LIMIT + 1)).fetchall()
    if len(found) > LIMIT:
        raise Refused("Fixed row bound exceeded; no completeness claim")
    for row in found:
        for value in row:
            if isinstance(value, str):
                evidence(value)
    return found

def observe(db):
    events = rows(db, "SELECT id,owner,event_key,chat_id,automation_id,job_id,status,"
                  "failure,context_json,response,created_at,updated_at FROM abitdot_relay_events "
                  "WHERE owner=? AND automation_id=? ORDER BY created_at,id", (OWNER, AGENT))
    result, job_ids, chat_ids = [], set(), set()
    for row in events:
        body = json.loads(row["context_json"])
        if not isinstance(body, dict) or not isinstance(body.get("context", {}), dict) or not isinstance(body.get("job", {}), dict):
            raise Refused("Legacy context shape differs")
        context, job = body.get("context", {}), body.get("job", {})
        identity = {"context": {key: safe(context.get(key)) for key in
                                ("automation_id", "chat_id", "origin_message_id")},
                    "job": {key: safe(job.get(key)) for key in ("owner", "job_id", "chat_id")}}
        matches = {"context_automation": context.get("automation_id") == AGENT,
                   "context_chat": context.get("chat_id") == row["chat_id"],
                   "job_owner": job.get("owner") == OWNER,
                   "job_chat": job.get("chat_id") == row["chat_id"],
                   "job_id": job.get("job_id") == row["job_id"]}
        if isinstance(row["job_id"], str) and JOB.fullmatch(row["job_id"]):
            job_ids.add(row["job_id"])
        if isinstance(row["chat_id"], str) and UUID.fullmatch(row["chat_id"]):
            chat_ids.add(row["chat_id"])
        if all(matches.values()) and isinstance(job.get("job_id"), str) and JOB.fullmatch(job["job_id"]):
            job_ids.add(job["job_id"])
        result.append({**{key: safe(row[key]) for key in
                          ("id", "owner", "event_key", "chat_id", "automation_id", "job_id", "status", "created_at", "updated_at")},
                       "failure": evidence(row["failure"]), "context": evidence(row["context_json"]),
                       "response": evidence(row["response"]), "context_identity": identity,
                       "identity_matches": matches, "holds_automation_by_installed_predicate": row["status"] in ("dispatching", "unknown"),
                       "proven_original_admission": False, "provider_readback": False})
    related = []
    if job_ids or chat_ids:
        clauses, parameters = [], [OWNER]
        for column, values in (("job_id", sorted(job_ids)), ("chat_id", sorted(chat_ids))):
            if values:
                clauses.append(column + " IN (" + ",".join("?" for _ in values) + ")")
                parameters.extend(values)
        jobs = rows(db, "SELECT job_id,chat_id,project_id,status,phase,created_at,updated_at,"
                    "response_message_id,response,error,conversation_url FROM jobs WHERE owner=? AND ("
                    + " OR ".join(clauses) + ") ORDER BY created_at,job_id", tuple(parameters))
        for row in jobs:
            related.append({**{key: safe(row[key]) for key in
                               ("job_id", "chat_id", "project_id", "status", "phase", "created_at", "updated_at", "response_message_id")},
                            "response": evidence(row["response"]), "error": evidence(row["error"]),
                            "conversation_url": evidence(row["conversation_url"]),
                            "linked_event_ids": [event["id"] for event in events
                                                 if event["job_id"] == row["job_id"] or event["chat_id"] == row["chat_id"]],
                            "proven_original_admission": False, "provider_readback": False})
    return {"events": result, "related_jobs": related, "queried_job_ids": sorted(job_ids),
            "queried_chat_ids": sorted(chat_ids),
            "legacy_hold_present": any(row["status"] in ("dispatching", "unknown") for row in events)}

def metadata():
    if DATABASE.resolve() != DATABASE:
        raise Refused("Fixed database path is not real")
    value = DATABASE.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_uid != 999
            or value.st_gid != 988 or stat.S_IMODE(value.st_mode) & 0o002):
        raise Refused("Fixed database metadata differs")
    return {"path": str(DATABASE), "device": value.st_dev, "inode": value.st_ino,
            "uid": value.st_uid, "gid": value.st_gid, "mode": oct(stat.S_IMODE(value.st_mode))}

def timeout(*_):
    raise Refused("Fixed observation deadline exceeded")

def main():
    if len(sys.argv) != 1 or os.geteuid() != 999 or os.getegid() != 988 or socket.gethostname() != "abit-dev-linux":
        raise Refused("No arguments; exact service identity and host required")
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    signal.signal(signal.SIGALRM, timeout); signal.alarm(SECONDS)
    started, deadline, before = utc(), time.monotonic() + SECONDS, metadata()
    db = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True, timeout=2, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        if before != metadata():
            raise Refused("Database identity changed while opening")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE}
        db.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        result = observe(db)
        if db.total_changes or before != metadata():
            raise Refused("Database metadata drift or unexpected changes")
        result.update({"database": before, "total_changes": db.total_changes})
    finally:
        db.close()
    result.update({"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "started_utc": started, "finished_utc": utc(), "service_uid": os.geteuid(), "service_gid": os.getegid(),
                   "owner": OWNER, "agent_id": AGENT, "read_only": True, "provider_api_called": False,
                   "lifecycle_mutation": False, "new_execution": False, "original_outcome_reconciled": False,
                   "limits": {"rows_per_query": LIMIT, "field_bytes": MAX_BYTES, "deadline_seconds": SECONDS}})
    output = json.dumps(result, sort_keys=True, indent=2)
    if len(output.encode()) > 1024 * 1024:
        raise Refused("Fixed output bound exceeded")
    signal.alarm(0); print(output)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, Refused) else "Read failed; preserve exception class",
                          "read_only": True, "new_execution": False}), file=sys.stderr)
        raise SystemExit(2)

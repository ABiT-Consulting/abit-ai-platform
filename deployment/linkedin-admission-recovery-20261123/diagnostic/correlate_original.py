"""Fixed read-only correlation of the positively observed LinkedIn admission.

No arguments, provider calls, module imports, schema operations, settlement or
dispatch. Caller authorization is checked outside this script; EUID/GID checks
only enforce the already delegated service read identity. Candidate proximity
and database status do not prove provider completion or authorize replay.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import resource
import signal
import socket
import sqlite3
import stat
import sys
import time

DATA = Path("/srv/abit-dev-linux/abit-ai/data")
AGENT = "d4a6e561-cb2a-41d9-93b5-2888624cf8d6"
LEASE = "210450ddbef3399ebc0259a970eb28b3a2aa035a30fa29b2"
PARENT = "98e868504294ecec5c6c3c0d49994eca2d9837f81188c5bb"
EXPECTED_LEASE = {
    "kind": "automation", "reference": AGENT, "parent_id": PARENT,
    "epoch": 42, "pid": 4132708, "process_start": "73246413",
    "created_ns": 1794783800763462257,
}
DATABASES = {
    "gate": DATA / "maintenance-admission/gate.sqlite",
    "webui": DATA / "webui.db",
    "dot": DATA / "abitdot-primary.sqlite",
    "relay": DATA / "aibit-chatgpt-relay.sqlite",
}
MAX_ROWS = 100
MAX_HISTORY = 10000
MAX_RELATIONS = 256
MAX_FIELD_BYTES = 2 * 1024 * 1024
WINDOW_NS = 120 * 1_000_000_000
DEADLINE_SECONDS = 45
LEASE_COLUMNS = "id,kind,reference,epoch,pid,process_start,created_ns,state,parent_id,receipt_sha256"
RUN_COLUMNS = "id,automation_id,chat_id,status,lease_until,error,created_at"
SAFE_ATOM = re.compile(r"[A-Za-z0-9_.:@/~+\-]{1,256}\Z")
UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")
ADMISSION_ERROR = "Maintenance admission denied before execution; original work retained"


class Refused(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def text_evidence(value):
    if value is None:
        return {"present": False, "bytes": 0, "sha256": None}
    if not isinstance(value, str):
        raise Refused("Expected a text or null evidence field")
    raw = value.encode()
    if len(raw) > MAX_FIELD_BYTES:
        raise Refused("Evidence field exceeds fixed byte bound")
    return {"present": True, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def safe(value):
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str) and SAFE_ATOM.fullmatch(value):
        return value
    return {"redacted_value_sha256": digest(value)}


def safe_fields(row):
    return {key: safe(value) for key, value in dict(row).items()}


def bounded_rows(db, sql, params=(), limit=MAX_ROWS):
    rows = db.execute(sql + " LIMIT ?", (*params, limit + 1)).fetchall()
    if len(rows) > limit:
        raise Refused("Fixed row bound exceeded; no partial completeness claim")
    for row in rows:
        for value in row:
            if isinstance(value, str) and len(value.encode()) > MAX_FIELD_BYTES:
                raise Refused("Selected row field exceeds fixed byte bound")
    return rows


def metadata(path):
    if path not in DATABASES.values() or path.resolve() != path:
        raise Refused("Database path differs from fixed real allowlist")
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != 999 or info.st_gid != 988 or mode & 0o002):
        raise Refused("Database owner, type, link count or writable mode differs")
    if path == DATABASES["gate"]:
        parent = path.parent.stat()
        if parent.st_uid != 999 or parent.st_gid != 988 or stat.S_IMODE(parent.st_mode) != 0o700 or mode != 0o600:
            raise Refused("Protected admission metadata differs")
    return {
        "path": str(path), "device": info.st_dev, "inode": info.st_ino,
        "uid": info.st_uid, "gid": info.st_gid, "mode": oct(mode),
        "bytes": info.st_size,
    }


@contextmanager
def readonly(name, deadline, observations):
    path = DATABASES[name]
    before = metadata(path)
    opened = now()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        after_open = metadata(path)
        if (before["device"], before["inode"]) != (after_open["device"], after_open["inode"]):
            raise Refused("Database identity changed while opening")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE}
        db.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        yield db
        if db.total_changes:
            raise Refused("Unexpected connection changes")
        after = metadata(path)
        if (before["device"], before["inode"]) != (after["device"], after["inode"]):
            raise Refused("Database identity changed during observation")
        observations[name] = {**before, "opened_utc": opened, "closed_utc": now(),
                              "total_changes": db.total_changes, "query_only": True}
    finally:
        db.close()


def observe_gate(deadline, observations):
    with readonly("gate", deadline, observations) as db:
        target = db.execute("SELECT " + LEASE_COLUMNS + " FROM lease WHERE id=?", (LEASE,)).fetchone()
        parent = db.execute("SELECT " + LEASE_COLUMNS + " FROM lease WHERE id=?", (PARENT,)).fetchone()
        if target is None or parent is None:
            raise Refused("Pinned target or parent lease is absent; investigate drift")
        if any(target[key] != value for key, value in EXPECTED_LEASE.items()):
            raise Refused("Pinned target lease identity differs")
        gate = db.execute("SELECT id,state,epoch,command_id,actor FROM gate WHERE id=1").fetchone()
        current = bounded_rows(db, "SELECT " + LEASE_COLUMNS + " FROM lease WHERE kind='automation' "
                               "AND reference=? AND state IN ('active','unresolved') ORDER BY id", (AGENT,), 2)
        relations = bounded_rows(db, "SELECT id,parent_id FROM lease WHERE parent_id IN (?,?) ORDER BY id",
                                 (LEASE, PARENT), MAX_RELATIONS)
        audits = bounded_rows(db, "SELECT seq,event_id,kind,payload,created_ns FROM audit WHERE "
                              "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.lease') END IN (?,?) "
                              "OR CASE WHEN json_valid(payload) THEN json_extract(payload,'$.reference') END=? "
                              "ORDER BY seq", (LEASE, PARENT, AGENT))
        objects = bounded_rows(db, "SELECT name,type,sql FROM sqlite_master WHERE name IN "
                               "('audit_no_update','audit_no_delete','one_live_admission') ORDER BY name", (), 3)
        if {row["name"] for row in objects} != {"audit_no_update", "audit_no_delete", "one_live_admission"}:
            raise Refused("Expected admission integrity objects absent")
        result_audits, explicit = [], set()
        for row in audits:
            body = json.loads(row["payload"])
            if not isinstance(body, dict):
                raise Refused("Audit payload shape differs")
            fields = {key: safe(body.get(key)) for key in
                      ("lease", "kind", "reference", "epoch", "parent", "run_id",
                       "receipt_sha256", "original_receipt")}
            result_audits.append({
                "seq": row["seq"], "event_id": safe(row["event_id"]), "kind": safe(row["kind"]),
                "created_ns": row["created_ns"], "payload": text_evidence(row["payload"]),
                "fields": fields,
            })
            run_id = body.get("run_id")
            if ((body.get("lease") == LEASE or body.get("reference") == AGENT)
                    and isinstance(run_id, str) and UUID.fullmatch(run_id)):
                explicit.add(run_id)
        return ({
            "gate": safe_fields(gate), "target": safe_fields(target), "parent": safe_fields(parent),
            "current_target_admissions": [safe_fields(row) for row in current],
            "child_and_sibling_ids": [dict(row) for row in relations],
            "audit": result_audits,
            "integrity_objects": [{"name": row["name"], "type": row["type"],
                                   "sql": text_evidence(row["sql"])} for row in objects],
        }, explicit, min(target["created_ns"], parent["created_ns"]) - WINDOW_NS,
            max(target["created_ns"], parent["created_ns"]) + WINDOW_NS)


def observe_dot(deadline, observations):
    with readonly("dot", deadline, observations) as db:
        rows = bounded_rows(db, "SELECT id,owner,automation_id,original_run_id,dot_id,status,"
                            "envelope_json,receipt,response_text,conversation_url,reason,created_at,updated_at "
                            "FROM dot_primary_runs WHERE automation_id=? ORDER BY created_at,id", (AGENT,))
        result, explicit = [], set()
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            if not isinstance(envelope, dict):
                raise Refused("Dot envelope shape differs")
            expected = {"owner_id": row["owner"], "automation_id": AGENT,
                        "run_id": row["original_run_id"], "dot_id": row["dot_id"],
                        "idempotency_key": row["id"]}
            key = hashlib.sha256(json.dumps([row["owner"], AGENT, row["original_run_id"]],
                                           separators=(",", ":")).encode()).hexdigest()
            result.append({
                **{key: safe(row[key]) for key in ("id", "owner", "automation_id", "original_run_id",
                                                   "dot_id", "status", "created_at", "updated_at")},
                "envelope_identity": {key: safe(envelope.get(key)) for key in expected},
                "envelope_identity_matches_row": all(envelope.get(key) == value for key, value in expected.items()),
                "idempotency_key_matches_identity": key == row["id"],
                "envelope": text_evidence(row["envelope_json"]),
                "reason": text_evidence(row["reason"]),
                "receipt": text_evidence(row["receipt"]),
                "response": text_evidence(row["response_text"]),
                "conversation_url": text_evidence(row["conversation_url"]),
                "provider_readback": False,
            })
            if isinstance(row["original_run_id"], str) and UUID.fullmatch(row["original_run_id"]):
                explicit.add(row["original_run_id"])
        return result, explicit


def run_evidence(row, lower, upper, explicit_audit, explicit_dot):
    return {
        **{key: safe(row[key]) for key in ("id", "automation_id", "chat_id", "status", "lease_until", "created_at")},
        "error": text_evidence(row["error"]),
        "is_exact_admission_denial_message": row["error"] == ADMISSION_ERROR,
        "candidate_basis": {
            "time_window_only_is_not_identity_proof": lower <= row["created_at"] <= upper,
            "explicit_target_audit_run_id": row["id"] in explicit_audit,
            "explicit_dot_original_run_id": row["id"] in explicit_dot,
        },
        "proven_original_admission": False,
    }


def observe_webui(deadline, observations, lower, upper, explicit_audit, explicit_dot):
    with readonly("webui", deadline, observations) as db:
        agent = db.execute("SELECT id,user_id,folder_id,name,data,meta,is_active,last_run_at,next_run_at,"
                           "attempt_limit,attempts_used,created_at,updated_at FROM automation WHERE id=?", (AGENT,)).fetchone()
        if agent is None:
            raise Refused("Target agent absent")
        for value in agent:
            if isinstance(value, str):
                text_evidence(value)
        config = {key: agent[key] for key in ("id", "user_id", "folder_id", "name", "data", "meta",
                                              "is_active", "attempt_limit")}
        history_count = db.execute("SELECT count(*) FROM automation_run WHERE automation_id=?", (AGENT,)).fetchone()[0]
        if history_count > MAX_HISTORY:
            raise Refused("History exceeds fixed row bound")
        history = hashlib.sha256(b"[")
        counted, status_counts, exact_denials = 0, {}, 0
        for row in db.execute("SELECT " + RUN_COLUMNS + " FROM automation_run WHERE automation_id=? "
                              "ORDER BY created_at,id", (AGENT,)):
            text_evidence(row["error"])
            if counted:
                history.update(b",")
            history.update(canonical(dict(row)))
            counted += 1
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            exact_denials += int(row["error"] == ADMISSION_ERROR)
        history.update(b"]")
        if counted != history_count:
            raise Refused("History snapshot count differs")
        explicit = sorted(explicit_audit | explicit_dot)
        if len(explicit) > MAX_ROWS:
            raise Refused("Explicit candidate IDs exceed fixed bound")
        clause = "(created_at BETWEEN ? AND ?)"
        params = [AGENT, lower, upper]
        if explicit:
            clause += " OR id IN (" + ",".join("?" for _ in explicit) + ")"
            params.extend(explicit)
        rows = bounded_rows(db, "SELECT " + RUN_COLUMNS + " FROM automation_run WHERE automation_id=? AND ("
                            + clause + ") ORDER BY created_at,id", tuple(params))
        candidate_ids = {row["id"] for row in rows}
        chats = sorted({row["chat_id"] for row in rows if row["chat_id"] and UUID.fullmatch(row["chat_id"])})
        return ({
            "agent": {**{key: safe(agent[key]) for key in ("id", "user_id", "folder_id", "is_active",
                                                         "attempt_limit", "attempts_used", "last_run_at",
                                                         "next_run_at", "created_at", "updated_at")},
                      "saved_configuration_sha256": digest(config),
                      "configuration_hash_columns": list(config)},
            "history": {"count": counted, "sha256": history.hexdigest(),
                        "hash_encoding": "JSON array; columns=id,automation_id,chat_id,status,lease_until,error,created_at;"
                                         " order=created_at,id; sort_keys=true; separators=comma/colon; ensure_ascii=true",
                        "status_counts": [{"status": safe(key), "count": value} for key, value in sorted(status_counts.items())],
                        "exact_admission_denials": exact_denials},
            "candidate_window_ns": {"lower_inclusive": lower, "upper_inclusive": upper,
                                    "margin_ns": WINDOW_NS, "proximity_proves_original": False},
            "candidate_runs": [run_evidence(row, lower, upper, explicit_audit, explicit_dot) for row in rows],
            "explicit_ids_without_same_agent_run": sorted(set(explicit) - candidate_ids),
        }, agent["user_id"], chats)


def observe_relay(deadline, observations, owner, chats):
    if not chats:
        return {"queried_chat_ids": [], "jobs": [], "reason": "No same-agent candidate chat IDs"}
    with readonly("relay", deadline, observations) as db:
        rows = bounded_rows(db, "SELECT job_id,chat_id,project_id,status,phase,created_at,updated_at,"
                            "response,error,response_message_id,conversation_url FROM jobs WHERE owner=? AND chat_id IN ("
                            + ",".join("?" for _ in chats) + ") ORDER BY created_at,job_id", (owner, *chats))
        return {"queried_chat_ids": chats, "jobs": [{
            **{key: safe(row[key]) for key in ("job_id", "chat_id", "project_id", "status", "phase",
                                              "created_at", "updated_at", "response_message_id")},
            "response": text_evidence(row["response"]), "error": text_evidence(row["error"]),
            "conversation_url": text_evidence(row["conversation_url"]),
            "provider_readback": False,
        } for row in rows]}


def main():
    if len(sys.argv) != 1:
        raise Refused("No arguments accepted")
    if os.geteuid() != 999 or os.getegid() != 988 or socket.gethostname() != "abit-dev-linux":
        raise Refused("Exact delegated service identity and host required")
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Refused("Total diagnostic deadline exceeded")))
    signal.alarm(DEADLINE_SECONDS)
    started = now()
    deadline, observations = time.monotonic() + DEADLINE_SECONDS, {}
    gate, explicit_audit, lower, upper = observe_gate(deadline, observations)
    dot, explicit_dot = observe_dot(deadline, observations)
    webui, owner, chats = observe_webui(deadline, observations, lower, upper, explicit_audit, explicit_dot)
    relay = observe_relay(deadline, observations, owner, chats)
    result = {
        "schema_version": 1, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_utc": started, "finished_utc": now(),
        "service_uid": os.geteuid(), "service_gid": os.getegid(), "hostname": socket.gethostname(),
        "agent_id": AGENT, "admission": gate, "webui": webui, "dot": dot, "relay": relay,
        "database_observations": observations,
        "limits": {"rows_per_query": MAX_ROWS, "history_rows": MAX_HISTORY, "related_lease_ids": MAX_RELATIONS,
                   "field_bytes": MAX_FIELD_BYTES, "deadline_seconds": DEADLINE_SECONDS},
        "read_only": True, "provider_api_called": False, "lifecycle_mutation": False,
        "new_execution": False, "original_outcome_reconciled": False,
        "database_snapshots_cross_atomic": False,
        "candidates_are_not_original_proof": True,
    }
    output = json.dumps(result, sort_keys=True, indent=2).encode()
    if len(output) > 1024 * 1024:
        raise Refused("Safe output exceeds fixed bound")
    signal.alarm(0)
    print(output.decode())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, Refused) else "Diagnostic failed; preserve stderr class",
                          "read_only": True, "new_execution": False}), file=sys.stderr)
        raise SystemExit(2)

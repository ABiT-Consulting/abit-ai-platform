#!/usr/bin/env python3
"""Fixed-original status projection. Read-only SQL; no application imports or requests."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import sqlite3
import stat
import sys
import time

DATA = Path("/srv/abit-dev-linux/abit-ai/data")
DATABASES = ("abitdot-primary.sqlite", "agentic-governance.sqlite", "webui.db")
RUN = "5955703a-4a31-5a33-8265-63a5299bdace"
INV = "agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0"
PROVIDER = "262d4bafc6201732f911849e48c723961a7d06215066d16b031664254280d00f"
AUTOMATION = "1ce26fc3-a045-5f94-afa0-c9d869963e19"
PROJECT, TASK = "PROJ-0196", "TASK-2026-06222"
MAX_VALUE = 2 * 1024 * 1024
DEADLINE_SECONDS = 15

PROVIDER_SQL = (
    "SELECT id,original_run_id,status,reason,envelope_json,response_text,receipt,"
    "scope_receipt_json,created_at,updated_at FROM dot_primary_runs WHERE original_run_id=?"
)
INVOCATION_SQL = (
    "SELECT invocation_id,automation_run_id,erp_project,erp_task,state,dispatch_attempts,"
    "lease_fence,lease_expires_at,updated_at,packet_hash,packet_version "
    "FROM governance_invocation WHERE invocation_id=?"
)
LEASE_SQL = (
    "SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?"
)
SESSION_SQL = (
    "SELECT session_id,state,scope_json,admission_json FROM governance_gateway_session "
    "WHERE invocation_id=?"
)
CALLS_SQL = (
    "SELECT c.state,COUNT(*) AS row_count,MAX(c.updated_at) AS last_updated_at "
    "FROM governance_gateway_call c JOIN governance_gateway_session s "
    "ON s.session_id=c.session_id WHERE s.invocation_id=? GROUP BY c.state LIMIT 33"
)
CONTROLS_SQL = (
    "SELECT state,COUNT(*) AS row_count FROM governance_task_control "
    "WHERE project=? AND task=? GROUP BY state LIMIT 33"
)
LOCAL_SQL = (
    "SELECT run_id,result_json,created_at FROM governance_local_result WHERE invocation_id=?"
)
FINAL_SQL = (
    "SELECT run_id,result_json,event_json,receipt_json,phase,created_at,updated_at "
    "FROM governance_result WHERE invocation_id=?"
)
AUTOMATION_SQL = (
    "SELECT id,automation_id,status,chat_id IS NULL AS no_chat,error,"
    "lease_until IS NULL AS no_lease,created_at FROM automation_run WHERE id=?"
)


def require(value, code):
    if not value:
        raise RuntimeError(code)


def utc():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    if value is None:
        return {"is_null": True, "bytes": None, "sha256": None}
    require(type(value) in (str, bytes), "VALUE_TYPE")
    raw = value.encode("utf-8") if type(value) is str else value
    require(len(raw) <= MAX_VALUE, "VALUE_BOUND")
    return {"is_null": False, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def state(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_. -]{0,79}", value),
            "STATE_SHAPE")
    return value


def number(value):
    require(value is None or (type(value) in (int, float) and math.isfinite(value)),
            "NUMBER_SHAPE")
    return value


def digest(value):
    require(value is None or (type(value) is str and re.fullmatch("[a-f0-9]{64}", value)),
            "DIGEST_SHAPE")
    return value


def optional(db, sql, args):
    rows = db.execute(sql, args).fetchmany(2)
    require(len(rows) <= 1, "ORIGINAL_ROW_COUNT")
    return dict(rows[0]) if rows else None


def grouped(db, sql, args):
    rows = db.execute(sql, args).fetchmany(33)
    require(len(rows) <= 32, "STATE_GROUP_BOUND")
    result = []
    for row in rows:
        item = {"state": state(row["state"]), "count": number(row["row_count"])}
        if "last_updated_at" in row.keys():
            item["last_updated_at"] = number(row["last_updated_at"])
        result.append(item)
    return result


@contextmanager
def ro(name, observation, deadline):
    require(name in DATABASES, "EXACT_DATABASE")
    path = DATA / name
    require(stat.S_ISREG(path.lstat().st_mode), "REGULAR_DATABASE_REQUIRED")
    observation.update(database=name, started_utc=utc())
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        db.row_factory = sqlite3.Row
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE)
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            yield db
        finally:
            require(db.execute("PRAGMA query_only").fetchone()[0] == 1
                    and db.total_changes == 0, "READ_ONLY_CONTRACT")
            observation.update(query_only=1, total_changes=0, finished_utc=utc())
    finally:
        db.close()


def provider(db):
    row = optional(db, PROVIDER_SQL, (RUN,))
    if row is None:
        return {"row_count": 0}
    return {
        "row_count": 1,
        "identity_matches": row["id"] == PROVIDER and row["original_run_id"] == RUN,
        "status": state(row["status"]),
        "reason": fingerprint(row["reason"]),
        "created_at": number(row["created_at"]),
        "updated_at": number(row["updated_at"]),
        **{name: fingerprint(row[name]) for name in
           ("envelope_json", "response_text", "receipt", "scope_receipt_json")},
    }


def governance(db):
    inv = optional(db, INVOCATION_SQL, (INV,))
    lease = optional(db, LEASE_SQL, (PROJECT, TASK))
    session = optional(db, SESSION_SQL, (INV,))
    local = optional(db, LOCAL_SQL, (INV,))
    final = optional(db, FINAL_SQL, (INV,))
    invocation = {"row_count": int(inv is not None)}
    if inv is not None:
        invocation.update(
            identity_matches=inv["invocation_id"] == INV and inv["automation_run_id"] == RUN
                and inv["erp_project"] == PROJECT and inv["erp_task"] == TASK,
            state=state(inv["state"]), packet_hash=digest(inv["packet_hash"]),
            **{key: number(inv[key]) for key in
               ("dispatch_attempts", "lease_fence", "lease_expires_at", "updated_at", "packet_version")})
    task_lease = {"row_count": int(lease is not None)}
    if lease is not None:
        task_lease.update(owner_present=lease["invocation_id"] is not None,
                          owner_matches_original=lease["invocation_id"] == INV,
                          owner_id=fingerprint(lease["invocation_id"]), fence=number(lease["fence"]))
    gateway = {"row_count": int(session is not None)}
    if session is not None:
        gateway.update(state=state(session["state"]),
                       **{key: fingerprint(session[key]) for key in
                          ("session_id", "scope_json", "admission_json")})
    results = {}
    for label, row in (("local", local), ("final", final)):
        item = {"row_count": int(row is not None)}
        if row is not None:
            item.update(run_matches_original=row["run_id"] == RUN,
                        result_json=fingerprint(row["result_json"]),
                        created_at=number(row["created_at"]))
            if label == "final":
                item.update(phase=state(row["phase"]), event_json=fingerprint(row["event_json"]),
                            receipt_json=fingerprint(row["receipt_json"]),
                            updated_at=number(row["updated_at"]))
        results[label] = item
    return {"invocation": invocation, "task_lease": task_lease, "gateway_session": gateway,
            "gateway_call_states": grouped(db, CALLS_SQL, (INV,)),
            "task_control_states": grouped(db, CONTROLS_SQL, (PROJECT, TASK)),
            "results": results}


def automation(db):
    row = optional(db, AUTOMATION_SQL, (RUN,))
    if row is None:
        return {"row_count": 0}
    require(row["no_chat"] in (0, 1) and row["no_lease"] in (0, 1), "NULLNESS_SHAPE")
    return {"row_count": 1,
            "identity_matches": row["id"] == RUN and row["automation_id"] == AUTOMATION,
            "status": state(row["status"]), "chat_is_null": bool(row["no_chat"]),
            "error": fingerprint(row["error"]), "lease_is_null": bool(row["no_lease"]),
            "created_at": number(row["created_at"])}


def deadline_signal(_signal, _frame):
    raise RuntimeError("READ_DEADLINE")


def main():
    require(sys.argv[1:] == ["--read-only"], "EXPLICIT_READ_ONLY_MODE")
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
            "USE_PYTHON_I_S_B")
    require(os.getuid() == os.geteuid() == 999 and os.getgid() == os.getegid() == 988,
            "READER_UID_GID")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, deadline_signal)
    signal.alarm(DEADLINE_SECONDS)
    deadline = time.monotonic() + DEADLINE_SECONDS
    value = {
        "schema_version": 1, "purpose": "original-be395-status.readonly.v1",
        "run_id": RUN, "invocation_id": INV, "project": PROJECT, "task": TASK,
        "started_utc": utc(), "cross_database_atomic": False,
        "qualification": "Three independent read transactions; these DB snapshots are not cross-DB atomic. "
                         "Counts, lease expiry or missing rows do not establish terminality, execution, "
                         "quiescence or recovery authority.",
        "application_imports": 0, "requests": 0, "model_or_business_actions": 0,
        "raw_payloads_exported": False, "credentials_exported": False,
        "reader_uid": os.geteuid(), "reader_gid": os.getegid(),
        "snapshots": {},
    }
    for name, label, reader in (
        (DATABASES[0], "provider", provider),
        (DATABASES[1], "governance", governance),
        (DATABASES[2], "automation", automation),
    ):
        observation = {}
        with ro(name, observation, deadline) as db:
            projection = reader(db)
        value["snapshots"][label] = {**observation, "projection": projection}
    signal.alarm(0)
    value.update(finished_utc=utc(), complete=True)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    require(len(encoded.encode()) <= 65536, "PUBLIC_OUTPUT_BOUND")
    print(encoded, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        signal.alarm(0)
        code = str(error) if isinstance(error, RuntimeError) else "READ_INCOMPLETE"
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
            code = "READ_INCOMPLETE"
        print(json.dumps({"schema_version": 1, "purpose": "original-be395-status.readonly.v1",
                          "complete": False, "cross_database_atomic": False,
                          "error_class": type(error).__name__, "error_code": code,
                          "requests": 0, "model_or_business_actions": 0,
                          "raw_payloads_exported": False}), flush=True)
        raise SystemExit(2)

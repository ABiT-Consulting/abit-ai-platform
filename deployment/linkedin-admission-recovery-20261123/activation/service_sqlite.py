#!/usr/bin/env python3
"""Fixed read-only service-identity SQLite helper. Invoked only by reviewed operator.

Live WAL visibility is preserved. SQLite may manage normal auxiliaries as the
existing database owner; root never opens live SQLite. Backup bytes are streamed
to an already open private output descriptor, never to a caller-selected path.
"""
from contextlib import closing
from pathlib import Path
import argparse
import hashlib
import json
import os
import resource
import socket
import sqlite3
import stat
import sys
import time

BASE = Path("/srv/abit-dev-linux/abit-ai/data")
DATABASES = ("webui.db", "agentic-governance.sqlite", "abitdot-primary.sqlite",
             "aibit-chatgpt-relay.sqlite", "abit-slack.sqlite", "abit-telegram.sqlite",
             "codex-workspace-runs.sqlite", "erp-dot-issues.sqlite",
             "erp-pending-review-qa.sqlite", "erp-task-resolution.sqlite",
             "maintenance-admission/gate.sqlite")
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
QUERIES = {
    "gate": ("maintenance-admission/gate.sqlite", "SELECT state,epoch,command_id,actor FROM gate WHERE id=1", 0),
    "gate_control": ("maintenance-admission/gate.sqlite", "SELECT kind,payload FROM audit WHERE event_id=?", 1),
    "core_leases": ("maintenance-admission/gate.sqlite",
        "SELECT id FROM lease WHERE state IN ('active','unresolved') AND pid=? AND process_start=?", 2),
    "lease_cohort": ("maintenance-admission/gate.sqlite",
        "SELECT id,kind,reference,parent_id,epoch,pid,process_start,state FROM lease "
        "WHERE state IN ('active','unresolved') ORDER BY id", 0),
    "relay_cohort": ("aibit-chatgpt-relay.sqlite",
        "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY job_id", 0),
    "dot_cohort": ("abitdot-primary.sqlite",
        "SELECT * FROM dot_primary_runs WHERE status IN ('prepared','dispatching') ORDER BY id", 0),
    "governance": ("agentic-governance.sqlite",
        "SELECT state FROM governance_invocation WHERE state IN ('running','cancel_requested')", 0),
    "codex": ("codex-workspace-runs.sqlite",
        "SELECT state FROM runs WHERE state IN ('submitting','running')", 0),
}


def need(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def source_metadata(path):
    s = path.lstat()
    need(path.resolve() == path and stat.S_ISREG(s.st_mode) and s.st_nlink == 1
         and s.st_uid == os.geteuid() and s.st_gid == os.getegid(), "service-owned source required")
    return {"uid": s.st_uid, "gid": s.st_gid, "mode": stat.S_IMODE(s.st_mode),
            "device": s.st_dev, "inode": s.st_ino, "bytes": s.st_size}


def query(base, kind, args=(), seconds=10):
    name, sql, arity = QUERIES[kind]
    need(len(args) == arity, "fixed query arguments differ")
    if kind == "core_leases":
        need(str(args[0]).isdigit() and str(args[1]).isdigit(), "bounded process identity required")
    if kind == "gate_control":
        need(isinstance(args[0], str) and 0 < len(args[0]) <= 512, "bounded control identity required")
    path = base / name
    before = source_metadata(path)
    deadline = time.monotonic() + seconds
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute("BEGIN")
        rows = [dict(r) for r in db.execute(sql, args).fetchmany(10001)]
    need(len(rows) <= 10000 and time.monotonic() < deadline, "observation bound reached")
    after = source_metadata(path)
    need(all(before[k] == after[k] for k in ("uid", "gid", "mode", "device", "inode")),
         "source identity changed")
    if kind.endswith("_cohort"):
        return {"count": len(rows), "sha256": sha(canonical(rows))}
    if kind in ("core_leases", "governance", "codex"):
        return {"count": len(rows)}
    if kind == "gate_control":
        return [{"kind": r["kind"], "payload_sha256": sha(canonical(json.loads(r["payload"])))} for r in rows]
    return rows


def snapshot_bytes(base, name, seconds=60):
    """Per-database SQLite backup into memory; no service-writable private stage."""
    need(name in DATABASES, "fixed backup database required")
    path = base / name
    before = source_metadata(path)
    deadline = time.monotonic() + seconds
    def progress(_status=0, _remaining=0, total=0):
        need(time.monotonic() < deadline, "snapshot deadline reached")
        need(total * page_size <= MAX_SNAPSHOT_BYTES, "growing snapshot memory bound exceeded")
        available = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        need(available > total * page_size * 2 + 512 * 1024 * 1024,
             "snapshot memory reserve reached")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as source:
        source.execute("PRAGMA query_only=ON")
        page_count = source.execute("PRAGMA page_count").fetchone()[0]
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        need(page_count * page_size <= MAX_SNAPSHOT_BYTES, "snapshot memory bound exceeded")
        available = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        need(available > page_count * page_size * 3 + 512 * 1024 * 1024,
             "insufficient free memory for bounded read-only snapshot")
        with closing(sqlite3.connect(":memory:")) as backup:
            backup.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            source.backup(backup, pages=256, progress=progress, sleep=0.025)
            need(backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                 "service snapshot integrity failed")
            value = backup.serialize()
            progress()
    need(len(value) <= MAX_SNAPSHOT_BYTES, "snapshot grew beyond memory bound")
    after = source_metadata(path)
    need(all(before[k] == after[k] for k in ("uid", "gid", "mode", "device", "inode")),
         "source identity changed during snapshot")
    return value, {"status": "SERVICE_IDENTITY_SNAPSHOT", "database": name, "source_metadata": before,
                   "bytes": len(value), "sha256": sha(value), "integrity": "ok"}



def stream_snapshot(fd, value, deadline, reserve_bytes=1024 ** 3):
    """Write only to the preopened FD; retain partial bytes if reserve falls."""
    remaining = memoryview(value)
    while remaining:
        need(time.monotonic() < deadline, "snapshot output deadline reached")
        space = os.fstatvfs(fd)
        need(space.f_bavail * space.f_frsize > len(remaining) + reserve_bytes,
             "snapshot output filesystem reserve reached")
        count = os.write(fd, remaining[:1024 * 1024])
        need(count > 0, "snapshot output made no progress")
        remaining = remaining[count:]
    os.fsync(fd)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("query", "backup"))
    parser.add_argument("name")
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args(argv)
    try:
        need(os.geteuid() == 999 and os.getegid() == 988 and socket.gethostname() == "abit-dev-linux",
             "existing service identity required before live access")
        # Bound this new read-only helper; do not affect Core/provider processes.
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
        if args.operation == "query":
            need(args.name in QUERIES, "unsupported query")
            result = query(BASE, args.name, args.arguments)
            sys.stdout.buffer.write(canonical(result))
        else:
            need(args.name in DATABASES and not args.arguments, "unsupported backup")
            operation_deadline = time.monotonic() + 60
            value, receipt = snapshot_bytes(BASE, args.name, seconds=60)
            output = os.fstat(sys.stdout.fileno())
            meta = os.fstat(sys.stderr.fileno())
            need(stat.S_ISREG(output.st_mode) and stat.S_ISREG(meta.st_mode)
                 and output.st_uid == meta.st_uid == 0
                 and stat.S_IMODE(output.st_mode) == stat.S_IMODE(meta.st_mode) == 0o600,
                 "preopened private root-owned output descriptors required")
            stream_snapshot(sys.stdout.fileno(), value, operation_deadline)
            sys.stderr.buffer.write(canonical(receipt))
            sys.stderr.buffer.flush()
            os.fsync(sys.stderr.fileno())
        return 0
    except BaseException as error:
        # Never echo database contents, paths or exception bodies into logs.
        sys.stderr.write(json.dumps({"status": "SERVICE_READ_FAILED", "error_type": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

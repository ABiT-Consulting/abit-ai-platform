#!/usr/bin/env python3
"""Exact seven-file Core activation. Production binding is deliberately UNBOUND.

No gate controls, original-work settlement, provider commands, saved prompt,
publication, database restore or forced signals exist in this controller.
The operator must independently review and bind real acknowledgement/evidence.
"""
from __future__ import annotations
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request

sys.dont_write_bytecode = True
EXECUTION_BINDING_SHA = "UNBOUND"
COMMIT = "8c1bc2308fdf693659520103559863682333299b"
MANIFEST_SHA = "955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250"
SQLITE_HELPER_SHA = "85073ff9daaa132064b391b2c989298ac82b72794878ee5fa18895e4a83f3161"
BASE = Path("/srv/abit-dev-linux/abit-ai")
PACKAGE = BASE / "deployment/linkedin-admission-recovery-20261123/activation"
STAGE = Path("/var/lib/abit-ai-linkedin-admission-20261123")
GLOBAL_LOCK = Path("/var/lock/abit-ai-governance-release.lock")
OWNER_RECORD = Path("/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json")
LIVE = BASE / "releases/20261031-live-0.11.4/runtime/open_webui"
OVERRIDE = Path("/run/systemd/system/abit-ai.service.d/zzzz-linkedin-admission-20261123.conf")
OVERRIDE_BYTES = b"[Service]\nTimeoutStopSec=infinity\nSendSIGKILL=no\nKillMode=mixed\nRestart=no\n"
ORIGINAL_EFFECTIVE = {"TimeoutStopUSec": "1min 30s", "SendSIGKILL": "yes",
                      "KillMode": "control-group", "KillSignal": "15",
                      "Restart": "on-failure", "RestartUSec": "10s"}
TEMP_EFFECTIVE = {**ORIGINAL_EFFECTIVE, "TimeoutStopUSec": "infinity",
                  "SendSIGKILL": "no", "KillMode": "mixed", "Restart": "no"}
FILES = ("frontend/agent-activity.js", "models/automation_admission.py",
         "models/automations.py", "routers/automations.py", "utils/agentic_maintenance.py",
         "utils/automation_admission.py", "utils/automations.py")
DATABASES = ("webui.db", "agentic-governance.sqlite", "abitdot-primary.sqlite",
             "aibit-chatgpt-relay.sqlite", "abit-slack.sqlite", "abit-telegram.sqlite",
             "codex-workspace-runs.sqlite", "erp-dot-issues.sqlite",
             "erp-pending-review-qa.sqlite", "erp-task-resolution.sqlite",
             "maintenance-admission/gate.sqlite")
EVIDENCE_ROLES = {"owner_acknowledgement", "original_diagnostic", "original_provider",
                  "uploads_preservation", "vector_preservation"}
UNIT_KEYS = ("MainPID", "ActiveState", "SubState", "User", "Group", "WorkingDirectory",
             "FragmentPath", "DropInPaths", "ControlGroup", "Job", *ORIGINAL_EFFECTIVE)
COMMANDS = {
    "reload": ("/usr/bin/systemctl", "daemon-reload"),
    "stop": ("/usr/bin/systemctl", "stop", "--no-block", "abit-ai.service"),
    "start": ("/usr/bin/systemctl", "start", "--no-block", "abit-ai.service"),
}


class Hold(RuntimeError):
    pass


def need(condition, reason):
    if not condition:
        raise Hold(reason)


def now():
    return dt.datetime.now(dt.timezone.utc)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def exists(path):
    return os.path.lexists(path)


def read_file(path, max_bytes=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "exclusive regular file required")
        if max_bytes is not None:
            need(before.st_size <= max_bytes, "file read bound exceeded")
        value = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         "file changed during read")
    return value


def digest_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "exclusive regular file required")
        digest = hashlib.sha256()
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
        after = os.fstat(stream.fileno())
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         "file changed during hash")
    return digest.hexdigest()


def metadata(path):
    s = path.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, "file metadata changed")
    return {"uid": s.st_uid, "gid": s.st_gid, "mode": stat.S_IMODE(s.st_mode),
            "device": s.st_dev, "inode": s.st_ino, "bytes": s.st_size,
            "mtime_ns": s.st_mtime_ns}


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private(path, uid, directory=False):
    s = path.lstat()
    need(path.resolve() == path.absolute() and s.st_uid == uid
         and stat.S_IMODE(s.st_mode) == (0o700 if directory else 0o600)
         and (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode) and s.st_nlink == 1),
         "private artifact metadata changed")


def save_once(path, value, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())
    fsync_dir(path.parent)


def save_json(path, value):
    save_once(path, encoded(value))


def db_summary(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = [(name, db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0])
              for name in names]
    schema = list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name"))
    return {"counts_sha256": sha(canonical(counts)), "schema_sha256": sha(canonical(schema)),
            "table_count": len(names)}


def verify_private_snapshot(destination, restore_path, service_receipt, deadline):
    """Root SQLite opens only private copied databases, never a live source."""
    need(service_receipt["status"] == "SERVICE_IDENTITY_SNAPSHOT"
         and digest_file(destination) == service_receipt["sha256"]
         and destination.stat().st_size == service_receipt["bytes"],
         "service snapshot receipt differs")
    def progress(*_):
        need(time.monotonic() < deadline, "private restore deadline reached")
        need(shutil.disk_usage(destination.parent).free > 1024 ** 3,
             "private restore disk reserve reached; preserve partial stage")
    with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as backup:
        backup.execute("PRAGMA query_only=ON")
        backup.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        need(backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "private backup integrity failed")
        summary = db_summary(backup)
        save_once(restore_path, b"")
        with closing(sqlite3.connect(restore_path)) as restored:
            restored.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            backup.backup(restored, pages=256, progress=progress, sleep=0.025)
            need(restored.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                 "isolated restore integrity failed")
            need(db_summary(restored) == summary, "isolated restored schema/counts differ")
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())
    fsync_dir(destination.parent)
    return {**summary, "sha256": digest_file(destination), "bytes": destination.stat().st_size,
            "source_metadata": service_receipt["source_metadata"],
            "integrity": "ok", "isolated_restore_integrity": "ok",
            "isolated_restore_sha256": digest_file(restore_path),
            "isolated_restore_schema_and_counts_match": True}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def local_get(port, path):
    need((port, path) in ((8888, "/health"), (8888, "/api/config"),
                         (8888, "/agent-activity.js?v=102e794ad614"), (8810, "/health")),
         "unapproved observation URL")
    request = urllib.request.Request("http://127.0.0.1:" + str(port) + path,
                                    headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        need(response.status == 200, "local observation HTTP status differs")
        value = response.read(2 * 1024 * 1024 + 1)
        need(len(value) <= 2 * 1024 * 1024, "local observation response exceeds bound")
        return value


class Service:
    def helper_source(self):
        value = read_file(Path(__file__).resolve().with_name("service_sqlite.py"), 64 * 1024)
        need(sha(value) == SQLITE_HELPER_SHA, "reviewed SQLite service helper changed")
        return value

    def helper_argv(self, operation, name, args=()):
        need(operation in ("query", "backup"), "unsupported service helper operation")
        return ["/usr/bin/sudo", "-n", "-u", "mcpdev", "--", "/usr/bin/python3",
                "-I", "-S", "-B", "-", operation, name, *map(str, args)]

    def query(self, name, args=()):
        code = self.helper_source()
        child = subprocess.Popen(self.helper_argv("query", name, args),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            output, errors = child.communicate(code, timeout=20)
        except subprocess.TimeoutExpired:
            # This is a newly created bounded read-only helper; do not signal it.
            raise Hold("service read helper not yet observed complete") from None
        need(child.returncode == 0 and len(output) <= 2 * 1024 * 1024,
             "service-owned read failed; do not fall back to root SQLite")
        return json.loads(output)

    def snapshot(self, name, destination, receipt_path, child_path, timeout):
        code = self.helper_source()
        save_once(destination, b"")
        save_once(receipt_path, b"")
        with destination.open("r+b") as output, receipt_path.open("r+b") as receipt:
            child = subprocess.Popen(self.helper_argv("backup", name),
                                     stdin=subprocess.PIPE, stdout=output, stderr=receipt)
            fields = (Path("/proc") / str(child.pid) / "stat").read_text().rsplit(")", 1)[1].split()
            save_json(child_path, {"pid": child.pid, "start_ticks": int(fields[19]),
                                  "database": name, "new_readonly_helper": True})
            try:
                child.communicate(code, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise Hold("one-use backup helper completion is unknown; retain its stage") from None
            output.flush()
            receipt.flush()
            os.fsync(output.fileno())
            os.fsync(receipt.fileno())
        need(child.returncode == 0, "service snapshot failed; retain private evidence")
        value = json.loads(read_file(receipt_path, 64 * 1024))
        need(value["source_metadata"]["uid"] == 999 and value["source_metadata"]["gid"] == 988,
             "actual snapshot source identity differs")
        return value

    def show(self):
        result = subprocess.run(["/usr/bin/systemctl", "show", "abit-ai.service",
                                 *[arg for key in UNIT_KEYS for arg in ("-p", key)]],
                                capture_output=True, text=True, check=True, timeout=10)
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def process(self, pid):
        p = Path("/proc") / str(pid)
        first = (p / "stat").read_text().rsplit(")", 1)[1].split()
        result = {"pid": pid, "start_ticks": int(first[19]), "uid": p.stat().st_uid,
                  "cmdline_sha256": sha((p / "cmdline").read_bytes()),
                  "cgroup": (p / "cgroup").read_text().strip()}
        need((p / "stat").read_text().rsplit(")", 1)[1].split()[19] == first[19], "PID changed during read")
        return result

    def absent(self, identity):
        if identity is None:
            return True
        try:
            current = self.process(identity["pid"])
        except FileNotFoundError:
            return True
        need(current == identity or current["start_ticks"] != identity["start_ticks"],
             "original PID changed without new start identity")
        return current["start_ticks"] != identity["start_ticks"]

    def cgroup_empty(self):
        root = Path("/sys/fs/cgroup/system.slice/abit-ai.service")
        if not exists(root):
            return True
        need(root.resolve() == root, "Core cgroup path changed")
        for path, directories, _ in os.walk(root, followlinks=False):
            for name in directories:
                need(not (Path(path) / name).is_symlink(), "cgroup descendant symlink")
            need(not (Path(path) / "cgroup.procs").read_text().split(), "Core descendant remains")
        events = dict(line.split() for line in (root / "cgroup.events").read_text().splitlines())
        return events.get("populated") == "0"

    def ticks(self):
        return int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf("SC_CLK_TCK"))

    def command(self, command):
        need(command in COMMANDS, "unsupported service action")
        result = subprocess.run(COMMANDS[command], capture_output=True, timeout=20, check=False)
        return {"returncode": result.returncode, "stdout_sha256": sha(result.stdout),
                "stderr_sha256": sha(result.stderr)}

    def health(self, frontend_sha):
        need(json.loads(local_get(8888, "/health")).get("status") is True, "Core health failed")
        need(json.loads(local_get(8888, "/api/config")).get("version") == "0.11.4", "Core version differs")
        need(sha(local_get(8888, "/agent-activity.js?v=102e794ad614")) == frontend_sha,
             "served activity asset differs")

    def relay_idle(self):
        value = json.loads(local_get(8810, "/health"))
        need(all(value.get(k) is True for k in ("ok", "ready", "authenticated", "target_ready"))
             and value.get("active_relay_turns") == 0 and value.get("browser_busy") is False,
             "relay is not positively idle; retain original work")


@dataclass(frozen=True)
class Layout:
    base: Path
    live: Path
    current: Path
    stage: Path
    payload: Path
    override: Path
    owner_record: Path


class Controller:
    """Internal constructor is injected only for isolated tests. CLI has no test switch."""
    def __init__(self, layout, binding, binding_bytes, service, private_uid=0, observe_seconds=40):
        self.l, self.b, self.binding_bytes, self.s = layout, binding, binding_bytes, service
        self.uid, self.observe_seconds = private_uid, observe_seconds
        self.rows = {}
        self.binding_sha = sha(binding_bytes)

    def artifact(self, name):
        need(name and "/" not in name and ".." not in name, "unsafe journal name")
        return self.l.stage / "journal" / (name + ".json")

    def load(self, name):
        path = self.artifact(name)
        if not exists(path):
            return None
        private(path, self.uid)
        return json.loads(read_file(path, 2 * 1024 * 1024))

    def record(self, name, value):
        need(self.load(name) is None, "immutable journal already exists")
        save_json(self.artifact(name), value)

    def payload(self):
        raw = read_file(self.l.payload / "manifest.json", 2 * 1024 * 1024)
        need(sha(raw) == MANIFEST_SHA, "approved product manifest changed")
        manifest = json.loads(raw)
        need(len(manifest["files"]) == 7 and {r["path"] for r in manifest["files"]} == set(FILES),
             "release is not the exact seven-file scope")
        self.rows = {r["path"]: r for r in manifest["files"]}
        need(set(self.b["files"]) == set(FILES), "metadata target scope changed")
        for rel, row in self.rows.items():
            source = self.l.payload / "candidate/open_webui" / rel
            need(source.resolve() == source.absolute(), "candidate symlink changed")
            data = read_file(source, 2 * 1024 * 1024)
            need(sha(data) == row["after_sha256"] and len(data) == row["bytes"], "candidate bytes changed")
            need(self.b["files"][rel]["before_sha256"] == row["expected_before_sha256"], "preimage binding differs")

    def owner(self):
        raw = read_file(self.l.owner_record, 2 * 1024 * 1024)
        need(sha(raw) == self.b["operator_session_sha256"], "operator session changed; obtain reviewed continuation")
        value = json.loads(raw)
        need(value.get("owner") == self.b["operator_owner"] and value.get("state") == self.b["operator_state"]
             and value.get("phase") == self.b["operator_phase"], "operator identity/phase changed")
        expiry = dt.datetime.fromisoformat(value["lease_expires_at"].replace("Z", "+00:00"))
        deadline = dt.datetime.fromisoformat(self.b["execution_expires_at"].replace("Z", "+00:00"))
        need(expiry.tzinfo is not None and deadline.tzinfo is not None
             and now() < expiry and now() < deadline, "operator/binding expiry reached; do not seize ownership")

    def evidence(self):
        need({r["role"] for r in self.b["evidence"]} == EVIDENCE_ROLES
             and len(self.b["evidence"]) == len(EVIDENCE_ROLES), "reviewed evidence scope missing")
        for row in self.b["evidence"]:
            path = Path(row["path"])
            private(path, self.uid)
            need(digest_file(path) == row["sha256"], "reviewed private evidence changed")
        # These are externally reviewed proofs, not caller assertions accepted by a CLI.
        # Production execution remains impossible until their real digest binding is reviewed.

    def configuration(self):
        for row in self.b["configuration"]:
            path = Path(row["path"])
            need(path.resolve() == path, "configuration path redirected")
            actual = metadata(path)
            need(all(actual[k] == row[k] for k in ("uid", "gid", "mode", "device", "inode")),
                 "configuration metadata changed")
            need(digest_file(path) == row["sha256"], "runtime configuration changed")

    def preserved(self):
        need(len(self.b["preserved_processes"]) >= 3
             and {"qa", "relay", "relay_watchdog"} <= set(self.b["preserved_processes"]),
             "fresh process preservation cohort required")
        for identity in self.b["preserved_processes"].values():
            need(self.s.process(identity["pid"]) == identity, "preserved process changed")

    def query(self, name, args=()):
        return self.s.query(name, args)

    def gate(self):
        rows = self.query("gate")
        need(len(rows) == 1 and rows[0] == self.b["closed_gate"]
             and rows[0]["state"] in ("draining", "sealed"), "closed gate ownership/epoch changed")
        audit = self.query("gate_control", (rows[0]["command_id"],))
        need(len(audit) == 1 and audit[0]["kind"] == "operator_control"
             and audit[0]["payload_sha256"] == self.b["gate_control_payload_sha256"],
             "closed gate immutable control receipt differs")
        return rows[0]

    def work(self, identity):
        self.gate()
        if identity:
            count = self.query("core_leases", (identity["pid"], str(identity["start_ticks"])))
            need(count["count"] == 0, "current Core retains active/unresolved work")
        leases = self.query("lease_cohort")
        need(leases["sha256"] == self.b["work"]["lease_cohort_sha256"], "original lease cohort changed")
        for label in ("relay", "dot"):
            summary = self.query(label + "_cohort")
            need(summary == self.b["work"][label], "original provider cohort changed")
        need(self.query("governance")["count"] == 0, "governance execution remains")
        need(self.query("codex")["count"] == 0, "Codex execution remains")
        self.s.relay_idle()

    def common(self, identity=None):
        self.owner()
        self.evidence()
        need(self.l.current.resolve() == self.l.live, "active runtime changed")
        self.configuration()
        self.preserved()
        self.work(identity)

    def unit(self, mode):
        value = self.s.show()
        fixed = self.b["unit"]
        for key in ("User", "Group", "WorkingDirectory", "FragmentPath"):
            need(value.get(key) == fixed[key], "service identity changed")
        allowed = ("base", "temp") if mode == "either" else (mode,)
        matched = False
        for possibility in allowed:
            drops = fixed["DropInPaths"].split() + ([str(self.l.override)] if possibility == "temp" else [])
            settings = TEMP_EFFECTIVE if possibility == "temp" else ORIGINAL_EFFECTIVE
            if sorted(value.get("DropInPaths", "").split()) == sorted(drops) and all(
                    value.get(k) == v for k, v in settings.items()):
                matched = True
        need(matched and value.get("ControlGroup", "") in ("", "/system.slice/abit-ai.service"),
             "effective service/drop-in changed")
        return value

    @contextmanager
    def target_parent(self, rel):
        row = self.b["files"][rel]
        parent = (self.l.live / rel).parent
        need(parent.resolve() == parent, "target parent redirected")
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            s = os.fstat(fd)
            need((s.st_dev, s.st_ino, s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) ==
                 (row["parent_device"], row["parent_inode"], row["parent_uid"],
                  row["parent_gid"], row["parent_mode"]), "target parent metadata changed")
            need(parent.stat().st_ino == s.st_ino and parent.stat().st_dev == s.st_dev,
                 "target parent moved")
            yield fd
            need(parent.stat().st_ino == s.st_ino and parent.stat().st_dev == s.st_dev,
                 "target parent changed during operation")
        finally:
            os.close(fd)

    def target_info(self, rel, fd):
        name = Path(rel).name
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        owned_link = False
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 2:
            temp = ".abit-linkedin-apply-" + name
            try:
                linked = os.stat(temp, dir_fd=fd, follow_symlinks=False)
                owned_link = ((linked.st_dev, linked.st_ino) == (info.st_dev, info.st_ino)
                              and self.load("apply-file-" + rel.replace("/", "--") + "-intent") is not None)
            except FileNotFoundError:
                pass
        need(stat.S_ISREG(info.st_mode) and (info.st_nlink == 1 or owned_link),
             "target is not exclusive regular file")
        target_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(target_fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "target changed before read")
            data = stream.read(2 * 1024 * 1024 + 1)
            need(len(data) <= 2 * 1024 * 1024, "target exceeds bound")
            after = os.fstat(stream.fileno())
        need((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) ==
             (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "target changed during read")
        row = self.b["files"][rel]
        need(not owned_link or sha(data) == self.rows[rel]["after_sha256"], "owned link bytes changed")
        need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
             (row["uid"], row["gid"], row["mode"]), "individual target metadata changed")
        return {"sha256": sha(data), "device": info.st_dev, "inode": info.st_ino,
                "mtime_ns": info.st_mtime_ns, "bytes": data}

    def source(self, mode):
        for rel, row in self.rows.items():
            with self.target_parent(rel) as fd:
                actual = self.target_info(rel, fd)
            digest = None if actual is None else actual["sha256"]
            expected = row["expected_before_sha256"] if mode == "before" else row["after_sha256"]
            need(digest == expected, "installed target hash differs: " + rel)

    def stage_valid(self):
        private(self.l.stage, self.uid, True)
        for name in ("journal", "database", "restore-check", "source", "candidate", "configuration"):
            private(self.l.stage / name, self.uid, True)
        private(self.l.stage / "binding.json", self.uid)
        need(read_file(self.l.stage / "binding.json") == self.binding_bytes, "one-use stage binding changed")

    def prepared(self):
        self.stage_valid()
        receipt = self.load("backup-complete")
        need(self.load("backup-failed") is None, "backup failed; preserve the one-use stage")
        need(receipt is not None and receipt["binding_sha256"] == self.binding_sha
             and receipt["database_names"] == list(DATABASES)
             and set(receipt["databases"]) == set(DATABASES)
             and receipt["cross_database_atomic_snapshot_claimed"] is False,
             "verified private backup required")
        for name, row in receipt["databases"].items():
            p = self.l.stage / "database" / name.replace("/", "--")
            restored = self.l.stage / "restore-check" / name.replace("/", "--")
            private(p, self.uid)
            private(restored, self.uid)
            need(digest_file(p) == row["sha256"] and digest_file(restored) == row["isolated_restore_sha256"]
                 and row["integrity"] == row["isolated_restore_integrity"] == "ok"
                 and row["isolated_restore_schema_and_counts_match"] is True,
                 "verified backup or isolated restore changed")
        for rel, product in self.rows.items():
            candidate = self.l.stage / "candidate" / rel.replace("/", "--")
            private(candidate, self.uid)
            need(digest_file(candidate) == product["after_sha256"], "private candidate changed")
            old = self.l.stage / "source" / rel.replace("/", "--")
            if product["expected_before_sha256"] is None:
                need(not exists(old) and receipt["preimages"][rel]["absent"] is True,
                     "new-module absence proof changed")
            else:
                private(old, self.uid)
                need(digest_file(old) == product["expected_before_sha256"]
                     and receipt["preimages"][rel]["sha256"] == product["expected_before_sha256"],
                     "rollback preimage changed")
        for index, expected in enumerate(self.b["configuration"]):
            p = self.l.stage / "configuration" / str(index)
            private(p, self.uid)
            need(digest_file(p) == expected["sha256"], "private runtime configuration changed")
        return receipt

    def backup(self):
        self.payload()
        self.common(self.b["core_before"])
        value = self.unit("base")
        need(value["ActiveState"] == "active" and value["SubState"] == "running"
             and int(value["MainPID"]) == self.b["core_before"]["pid"]
             and self.s.process(int(value["MainPID"])) == self.b["core_before"], "Core predecessor changed")
        need(not exists(self.l.override) and not exists(self.l.stage), "one-use stage/override exists; observe it")
        self.source("before")
        sizes = sum((self.l.base / "data" / name).stat().st_size
                    + ((self.l.base / "data" / (name + "-wal")).stat().st_size
                       if exists(self.l.base / "data" / (name + "-wal")) else 0)
                    for name in DATABASES)
        need(shutil.disk_usage(self.l.stage.parent).free > max(3 * sizes, 3 * 1024 ** 3),
             "insufficient measured backup space")
        self.l.stage.mkdir(mode=0o700)
        fsync_dir(self.l.stage.parent)
        for name in ("journal", "database", "restore-check", "source", "candidate", "configuration"):
            (self.l.stage / name).mkdir(mode=0o700)
        save_once(self.l.stage / "binding.json", self.binding_bytes)
        self.record("backup-intent", {"binding_sha256": self.binding_sha, "utc": now().isoformat()})
        try:
            preimages = {}
            for rel, row in self.rows.items():
                with self.target_parent(rel) as fd:
                    actual = self.target_info(rel, fd)
                if actual is None:
                    need(row["expected_before_sha256"] is None, "preimage disappeared")
                    preimages[rel] = {"absent": True}
                else:
                    need(actual["sha256"] == row["expected_before_sha256"], "preimage drift during backup")
                    save_once(self.l.stage / "source" / rel.replace("/", "--"), actual.pop("bytes"))
                    preimages[rel] = {**actual, "absent": False}
                data = read_file(self.l.payload / "candidate/open_webui" / rel)
                need(sha(data) == row["after_sha256"], "candidate drift during private copy")
                save_once(self.l.stage / "candidate" / rel.replace("/", "--"), data)
            for index, row in enumerate(self.b["configuration"]):
                value = read_file(Path(row["path"]))
                need(sha(value) == row["sha256"], "configuration drift during backup")
                save_once(self.l.stage / "configuration" / str(index), value)
            databases = {}
            deadline = time.monotonic() + 240
            for name in DATABASES:
                self.common(self.b["core_before"])
                need(time.monotonic() < deadline, "one-use backup total deadline reached")
                logical = (self.l.base / "data" / name).stat().st_size
                wal = self.l.base / "data" / (name + "-wal")
                logical += wal.stat().st_size if exists(wal) else 0
                need(shutil.disk_usage(self.l.stage).free > 3 * logical + 1024 ** 3,
                     "backup disk reserve reached; retain partial stage")
                safe_name = name.replace("/", "--")
                self.record("database-" + safe_name + "-intent",
                            {"utc": now().isoformat(), "database": name,
                             "service_helper_sha256": SQLITE_HELPER_SHA})
                destination = self.l.stage / "database" / safe_name
                service_receipt = self.s.snapshot(name, destination,
                    self.l.stage / "journal" / ("database-" + safe_name + "-service.json"),
                    self.l.stage / "journal" / ("database-" + safe_name + "-child.json"),
                    min(70, max(1, deadline - time.monotonic())))
                databases[name] = verify_private_snapshot(destination,
                    self.l.stage / "restore-check" / safe_name, service_receipt, deadline)
            self.common(self.b["core_before"])
            self.source("before")
            self.record("backup-complete", {"binding_sha256": self.binding_sha,
                "utc": now().isoformat(), "database_names": list(DATABASES), "databases": databases,
                "preimages": preimages, "cross_database_atomic_snapshot_claimed": False,
                "automatic_database_restore_allowed": False,
                "preservation_evidence": [r for r in self.b["evidence"] if "preservation" in r["role"]]})
            self.prepared()
        except BaseException as error:
            self.record("backup-failed", {"utc": now().isoformat(), "error_type": type(error).__name__,
                                         "partial_stage_retained": True})
            raise

    def command_once(self, cycle, key, command, before):
        tag = cycle + "-" + key + "-intent"
        intent = self.load(tag)
        if intent is None:
            before()
            intent = {"command": command, "requested_tick": self.s.ticks(),
                      "utc": now().isoformat(), "invocation_may_have_started": True}
            self.record(tag, intent)  # fsync before any subprocess; uncertain outcomes never replay.
            try:
                result = self.s.command(command)
            except BaseException as error:
                self.record(cycle + "-" + key + "-returned", {"exception_type": type(error).__name__})
                raise
            self.record(cycle + "-" + key + "-returned", result)
        else:
            need(intent["command"] == command and intent["invocation_may_have_started"] is True,
                 "lifecycle intent differs")
        return intent

    def override_check(self):
        s = self.l.override.lstat()
        need(self.l.override.resolve() == self.l.override and stat.S_ISREG(s.st_mode)
             and s.st_nlink == 1 and s.st_uid == self.uid and s.st_gid == self.b["private_gid"]
             and stat.S_IMODE(s.st_mode) == 0o644 and read_file(self.l.override) == OVERRIDE_BYTES,
             "owned graceful override changed")

    def override_on(self, cycle):
        self.common()
        parent = self.l.override.parent
        need(parent.resolve() == parent and parent.is_dir(), "reviewed override parent required")
        s = parent.stat()
        expected = self.b["override_parent"]
        need((s.st_dev, s.st_ino, s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) ==
             tuple(expected[k] for k in ("device", "inode", "uid", "gid", "mode")), "override parent changed")
        tag = cycle + "-override-file"
        if self.load(tag) is None:
            need(not exists(self.l.override) or cycle == "rollback" and self.load("apply-override-file") is not None,
                 "unowned override already exists")
            self.record(tag, {"sha256": sha(OVERRIDE_BYTES)})
        if not exists(self.l.override):
            save_once(self.l.override, OVERRIDE_BYTES, 0o644)
        self.override_check()
        if cycle == "rollback" and self.unit("either").get("Restart") == "no":
            if self.load("rollback-reload-on-intent") is None:
                self.record("rollback-reload-on-intent", {"command": "reload",
                    "requested_tick": self.s.ticks(), "invocation_may_have_started": True,
                    "borrowed_verified_effective_override": True})
        else:
            self.command_once(cycle, "reload-on", "reload",
                              lambda: (self.common(), self.override_check(), self.unit("either")))
        self.unit("temp")

    def stopped(self, previous):
        value = self.unit("temp")
        need(value["ActiveState"] in ("inactive", "failed") and value["SubState"] in ("dead", "failed")
             and int(value.get("MainPID") or 0) == 0 and value.get("Job", "") in ("", "0")
             and self.s.absent(previous) and self.s.cgroup_empty(), "Core has not fully ceased; observe, do not force")
        return True

    def observe_until(self, check):
        end = time.monotonic() + self.observe_seconds
        last = None
        while True:
            self.common()
            try:
                return check()
            except (Hold, OSError, TimeoutError) as error:
                last = error
            if time.monotonic() >= end:
                raise Hold("observation incomplete; no lifecycle command was repeated") from last
            time.sleep(min(1, max(0, end - time.monotonic())))

    def stop(self, cycle, previous, already_stopped=False):
        if already_stopped:
            self.stopped(previous)
            if self.load(cycle + "-stop-intent") is None:
                self.record(cycle + "-stop-intent", {"command": "stop", "requested_tick": self.s.ticks(),
                    "invocation_may_have_started": True, "borrowed_original_stop_observation": True})
        else:
            def before():
                self.common(previous)
                self.prepared()
                self.override_check()
                value = self.unit("temp")
                need(value["ActiveState"] == "active" and value["SubState"] == "running"
                     and int(value["MainPID"]) == previous["pid"]
                     and self.s.process(previous["pid"]) == previous, "Core changed before graceful stop")
            self.command_once(cycle, "stop", "stop", before)
        self.observe_until(lambda: self.stopped(previous))

    def recover_link_temp(self, rel, cycle, fd):
        """Only repair our exact two-link create after a crash; never unknown links."""
        name = Path(rel).name
        temp = ".abit-linkedin-" + cycle + "-" + name
        try:
            target = os.stat(name, dir_fd=fd, follow_symlinks=False)
            staged = os.stat(temp, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if target.st_nlink == 2 and (target.st_dev, target.st_ino) == (staged.st_dev, staged.st_ino):
            need(self.load(cycle + "-file-" + rel.replace("/", "--") + "-intent") is not None,
                 "unjournaled new-file link")
            p = self.l.live / rel
            f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(f, "rb") as stream:
                data = stream.read()
            need(stat.S_ISREG(target.st_mode) and sha(data) == self.rows[rel]["after_sha256"]
                 and (target.st_uid, target.st_gid, stat.S_IMODE(target.st_mode)) ==
                     tuple(self.b["files"][rel][k] for k in ("uid", "gid", "mode")),
                 "temporary link metadata/bytes changed")
            os.unlink(temp, dir_fd=fd)
            os.fsync(fd)

    def install_file(self, rel, cycle, previous):
        self.common()
        self.stopped(previous)
        receipt = self.prepared()
        row, meta = self.rows[rel], self.b["files"][rel]
        want = row["after_sha256"] if cycle == "apply" else row["expected_before_sha256"]
        tag = cycle + "-file-" + rel.replace("/", "--") + "-intent"
        intent = self.load(tag)
        with self.target_parent(rel) as fd:
            self.recover_link_temp(rel, "apply", fd)
            actual = self.target_info(rel, fd)
            actual_hash = None if actual is None else actual["sha256"]
            if intent is None:
                if cycle == "apply":
                    need(actual_hash == row["expected_before_sha256"], "unjournaled apply target changed")
                else:
                    need(self.load("apply-file-" + rel.replace("/", "--") + "-intent") is not None
                         or actual_hash == row["expected_before_sha256"], "unowned rollback target")
                    need(actual_hash in (row["expected_before_sha256"], row["after_sha256"]),
                         "rollback cannot overwrite unrelated bytes")
                self.record(tag, {"before_sha256": actual_hash, "wanted_sha256": want,
                                  "preimage_absent": row["expected_before_sha256"] is None})
                intent = self.load(tag)
            need(intent["wanted_sha256"] == want, "file intent differs")
            need(actual_hash in (intent["before_sha256"], want), "ambiguous partial source; preserve it")
            if actual_hash == want:
                return
            name = Path(rel).name
            if want is None:
                need(cycle == "rollback" and receipt["preimages"][rel]["absent"] is True
                     and actual_hash == row["after_sha256"], "new module deletion proof missing")
                os.unlink(name, dir_fd=fd)
                os.fsync(fd)
                return
            data = read_file(self.l.stage / ("candidate" if cycle == "apply" else "source") / rel.replace("/", "--"))
            need(sha(data) == want, "private source hash changed")
            temp = ".abit-linkedin-" + cycle + "-" + name
            try:
                temp_fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            except FileExistsError:
                temp_path = self.l.live / Path(rel).parent / temp
                need(digest_file(temp_path) == want, "owned temporary source differs")
                temp_meta = metadata(temp_path)
                need((temp_meta["uid"], temp_meta["gid"], temp_meta["mode"]) ==
                     (meta["uid"], meta["gid"], meta["mode"]), "owned temporary metadata differs")
            else:
                with os.fdopen(temp_fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchown(stream.fileno(), meta["uid"], meta["gid"])
                    os.fchmod(stream.fileno(), meta["mode"])
                    if cycle == "rollback":
                        os.utime(stream.fileno(), ns=(receipt["preimages"][rel]["mtime_ns"],
                                                     receipt["preimages"][rel]["mtime_ns"]))
                    os.fsync(stream.fileno())
            # Recheck the same directory handle, original target and all live guards.
            self.common()
            self.stopped(previous)
            again = self.target_info(rel, fd)
            need(again == actual, "target changed immediately before replacement")
            parent = (self.l.live / rel).parent.stat()
            need((parent.st_dev, parent.st_ino) == (meta["parent_device"], meta["parent_inode"]),
                 "target parent replaced before write")
            if actual is None:
                # Atomic no-replace creation; crash after link is reconciled above.
                os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temp, dir_fd=fd)
            else:
                os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            need(self.target_info(rel, fd)["sha256"] == want, "installed source readback failed")

    def start_identity(self, cycle, previous, mode="temp"):
        value = self.unit(mode)
        need(value["ActiveState"] == "active" and value["SubState"] == "running"
             and int(value["MainPID"]) > 0 and value.get("Job", "") in ("", "0"),
             "one-use Core start is not settled")
        intent = self.load(cycle + "-start-intent")
        need(intent is not None, "start intent missing")
        identity = self.s.process(int(value["MainPID"]))
        need(identity["uid"] == self.b["core_before"]["uid"]
             and identity["cmdline_sha256"] == self.b["core_before"]["cmdline_sha256"]
             and identity["start_ticks"] >= intent["requested_tick"] and identity != previous
             and identity["cgroup"] == self.b["core_before"]["cgroup"],
             "new runtime does not match the one-use start")
        saved = self.load(cycle + "-started")
        if saved is not None:
            need(saved["identity"] == identity, "previously observed Core changed")
        return identity

    def start(self, cycle, previous):
        wanted = "after" if cycle == "apply" else "before"
        def before():
            self.common()
            self.prepared()
            self.stopped(previous)
            self.source(wanted)
        self.command_once(cycle, "start", "start", before)
        def observe():
            identity = self.start_identity(cycle, previous)
            self.source(wanted)
            self.s.health(self.rows["frontend/agent-activity.js"]["after_sha256" if cycle == "apply" else "expected_before_sha256"])
            return identity
        identity = self.observe_until(observe)
        if self.load(cycle + "-started") is None:
            self.record(cycle + "-started", {"identity": identity, "utc": now().isoformat()})
        return identity

    def override_off(self, cycle, previous):
        self.common()
        identity = self.start_identity(cycle, previous, "either")
        tag = cycle + "-override-remove"
        if self.load(tag) is None:
            self.override_check()
            self.record(tag, {"sha256": sha(OVERRIDE_BYTES)})
        if exists(self.l.override):
            self.override_check()
            self.l.override.unlink()
            fsync_dir(self.l.override.parent)
        self.command_once(cycle, "reload-off", "reload", lambda: (self.common(), self.unit("either")))
        self.unit("base")
        need(self.s.process(identity["pid"]) == identity, "Core changed during override finalization")

    def run_cycle(self, cycle):
        record = self.load(cycle + "-cycle")
        need(record is not None and record["binding_sha256"] == self.binding_sha, "cycle binding missing")
        previous = record["previous_identity"]
        if self.load(cycle + "-complete") is not None:
            return self.verify_complete(cycle)
        if self.load(cycle + "-start-intent") is None:
            self.override_on(cycle)
            self.stop(cycle, previous, record["borrowed_stopped"])
            for rel in FILES:
                self.install_file(rel, cycle, previous)
            self.source("after" if cycle == "apply" else "before")
            self.start(cycle, previous)
        else:
            # Never revisit an earlier stop after start was requested.
            if self.load(cycle + "-override-remove") is None:
                self.start(cycle, previous)
            else:
                self.start_identity(cycle, previous, "either")
        self.override_off(cycle, previous)
        result = self.verify_complete(cycle)
        self.record(cycle + "-complete", result)
        return result

    def verify_complete(self, cycle):
        record = self.load(cycle + "-cycle")
        identity = self.start_identity(cycle, record["previous_identity"], "base")
        self.common(identity)
        self.prepared()
        self.source("after" if cycle == "apply" else "before")
        self.s.health(self.rows["frontend/agent-activity.js"]["after_sha256" if cycle == "apply" else "expected_before_sha256"])
        need(not exists(self.l.override), "owned override remains")
        return {"status": "DEPLOYED_ADMISSION_HELD" if cycle == "apply" else "ROLLED_BACK_ADMISSION_HELD",
                "utc": now().isoformat(), "identity": identity, "binding_sha256": self.binding_sha,
                "product_commit": COMMIT, "gate": self.gate(), "targets": list(FILES),
                "database_restore_performed": False, "original_work_reconciled": False,
                "global_atomic_snapshot_claimed": False, "marketing_agent_executed": False,
                "authenticated_ui_verification_required": True}

    def apply(self):
        self.payload()
        self.prepared()
        need(self.load("apply-cycle") is None and self.load("rollback-cycle") is None,
             "one-use apply exists; observe/finalize its outcome")
        self.common(self.b["core_before"])
        self.source("before")
        value = self.unit("base")
        need(value["ActiveState"] == "active" and value["SubState"] == "running"
             and int(value["MainPID"]) == self.b["core_before"]["pid"]
             and self.s.process(self.b["core_before"]["pid"]) == self.b["core_before"], "Core predecessor changed")
        self.record("apply-cycle", {"binding_sha256": self.binding_sha, "previous_identity": self.b["core_before"],
                                   "borrowed_stopped": False, "utc": now().isoformat()})
        return self.run_cycle("apply")

    def partial_source_owned(self):
        for rel, row in self.rows.items():
            with self.target_parent(rel) as fd:
                actual = self.target_info(rel, fd)
            digest = None if actual is None else actual["sha256"]
            need(digest in (row["expected_before_sha256"], row["after_sha256"]),
                 "rollback source drift; no service command issued")
            if digest == row["after_sha256"]:
                need(self.load("apply-file-" + rel.replace("/", "--") + "-intent") is not None,
                     "candidate bytes lack an owned install intent")

    def rollback(self):
        self.payload()
        self.prepared()
        apply = self.load("apply-cycle")
        need(apply is not None and self.load("rollback-cycle") is None, "only one explicit owned rollback is allowed")
        self.common()
        self.partial_source_owned()
        value = self.unit("either")
        need(value.get("Job", "") in ("", "0"), "pending original service job; observe without rollback")
        previous, borrowed = None, False
        if self.load("apply-stop-intent") is not None and self.load("apply-start-intent") is None:
            need(value["ActiveState"] in ("inactive", "failed") and value["SubState"] in ("dead", "failed")
                 and int(value.get("MainPID") or 0) == 0 and self.s.absent(apply["previous_identity"])
                 and self.s.cgroup_empty(), "original stop unresolved; rollback must observe, never reissue")
        if value["ActiveState"] == "active" and value["SubState"] == "running":
            if self.load("apply-start-intent"):
                previous = self.start_identity("apply", apply["previous_identity"], "either")
            else:
                previous = self.b["core_before"]
                need(self.s.process(int(value["MainPID"])) == previous, "unowned running Core")
        elif value["ActiveState"] in ("inactive", "failed") and value["SubState"] in ("dead", "failed"):
            need(self.load("apply-stop-intent") is not None
                 and self.s.absent(apply["previous_identity"]) and self.s.cgroup_empty(),
                 "inactive state is not our original stop")
            # An unknown start can be rolled back only after a settled failed/inactive unit,
            # no pending job and empty cgroup. Never resubmit that original start or stop.
            previous, borrowed = apply["previous_identity"], True
        else:
            raise Hold("original service transition unresolved; observe without new command")
        self.record("rollback-cycle", {"binding_sha256": self.binding_sha, "previous_identity": previous,
                                      "borrowed_stopped": borrowed, "utc": now().isoformat()})
        return self.run_cycle("rollback")

    def finalize(self):
        self.payload()
        self.prepared()
        cycle = "rollback" if self.load("rollback-cycle") is not None else "apply"
        need(self.load(cycle + "-cycle") is not None, "no original cycle to observe/finalize")
        return self.run_cycle(cycle)

    def observe(self):
        self.payload()
        self.stage_valid()
        self.owner()
        self.gate()
        return {"status": "OBSERVATION_ONLY", "unit": self.unit("either"),
                "journal_names": sorted(p.name for p in (self.l.stage / "journal").iterdir()),
                "gate": self.gate(), "no_lifecycle_command_issued": True}


def validate_production_binding(raw):
    need(EXECUTION_BINDING_SHA != "UNBOUND" and sha(raw) == EXECUTION_BINDING_SHA,
         "fresh operator execution binding is UNBOUND or changed")
    value = json.loads(raw)
    need(value["product_commit"] == COMMIT and value["product_manifest_sha256"] == MANIFEST_SHA,
         "approved seven-file binding differs")
    need(value["runtime_root"] == str(LIVE) and value["stage"] == str(STAGE)
         and value["private_gid"] == 0 and value["core_before"]["uid"] == 999,
         "production identity binding differs")
    need(value["unit"]["User"] == value["unit"]["Group"] == "mcpdev"
         and value["unit"]["WorkingDirectory"] == str(BASE)
         and value["unit"]["FragmentPath"] == "/etc/systemd/system/abit-ai.service",
         "production startup route differs")
    required = {str(BASE / p) for p in (
        "config/runtime-environment.json", "config/final-snapshot.json",
        "config/webui-secret-key", "deployment/run-production.py")}
    required.add(value["unit"]["FragmentPath"])
    required.update(value["unit"]["DropInPaths"].split())
    need({r["path"] for r in value["configuration"]} == required,
         "private runtime/unit/drop-in preservation incomplete")
    for rel, row in value["files"].items():
        need(row["uid"] == (722237112 if rel.startswith("frontend/") else 999)
             and row["gid"] == 988 and row["mode"] == 0o644, "individual production metadata differs")
    return value


@contextmanager
def production_lock():
    # Existing lock only: no newly named/private substitute and no takeover.
    fd = os.open(GLOBAL_LOCK, os.O_RDWR | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == s.st_gid == 0
             and stat.S_IMODE(s.st_mode) == 0o600, "existing global lock metadata differs")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "apply", "observe", "finalize", "rollback"))
    args = parser.parse_args(argv)
    try:
        need(EXECUTION_BINDING_SHA != "UNBOUND", "production execution binding remains UNBOUND")
        need(os.geteuid() == 0 and os.environ.get("SUDO_USER") == "jimran@abitcons.com"
             and socket.gethostname() == "abit-dev-linux", "existing reviewed administrator route required")
        private(PACKAGE / "operator-binding.json", 0)
        raw = read_file(PACKAGE / "operator-binding.json", 2 * 1024 * 1024)
        binding = validate_production_binding(raw)
        layout = Layout(BASE, LIVE, BASE / "current/runtime/open_webui", STAGE,
                        PACKAGE / "product", OVERRIDE, OWNER_RECORD)
        with production_lock():
            controller = Controller(layout, binding, raw, Service())
            result = getattr(controller, args.action)()
        print(json.dumps(result or {"status": "PRIVATE_BACKUPS_VERIFIED_ADMISSION_HELD"}, sort_keys=True))
        return 0
    except BaseException as error:
        # Do not print paths, source data, exception bodies or command output.
        print(json.dumps({"status": "HELD_FOR_OPERATOR_REVIEW", "error_type": type(error).__name__,
                          "production_binding_set": EXECUTION_BINDING_SHA != "UNBOUND",
                          "no_automatic_retry": True, "no_gate_reopen": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded read-only selector metadata for one retained LinkedIn Dot event.
Run only via the independently verified mcpdev delegation. No arguments.
A current selector is NOT proof of a historical send, result, or settlement.
"""
import hashlib
import json
import os
import re
import resource
import signal
import stat
import sys
import time

HOST, UID, GID = "abit-dev-linux", 999, 988
OWNER = "03519eef-e01d-4c45-8e07-266b35d0ac91"
AGENT = "d4a6e561-cb2a-41d9-93b5-2888624cf8d6"
EVENT = "b73e264f-43ba-4596-a6b6-ca97bf8e33a1"
CHAT = "4f21c199-7193-4970-a4c9-07a7a1b4666d"
ORIGIN_MESSAGE = "8659cbfe-000f-4ec1-8255-a01f5784ad4d"
JOB = "aibit_c59316858e7305b761d81de8746af62526b85169c31ee848"
THREAD = "aibit:" + OWNER + ":dot_" + JOB
KEYS = {
    "local_chat": "ddd6c8df82953d07e091bb00e5c33ca9c1647d0f",
    "candidate_dot_thread": "65fc43b54dc31088b6b7b5394d14000670b58750",
}
STATE = "/srv/abit-dev-linux/relay-runtime/state"
REGISTRY = STATE + "/chat-thread-targets.json"
BINDINGS = STATE + "/original-dot-bindings-v1"
SCRIPT = "/srv/abit-dev-linux/tmp/linkedin-admission-recovery-20261123/observe_provider_bindings.py"
SOURCES = {
    "/srv/abit-dev-linux/relay-runtime/service/original_dot_bindings.cjs":
        "bc9a73fed1cfdd86f56d653c4a231efaa2205a6be3014a84ae1ad3d45c1c298b",
    "/srv/abit-dev-linux/relay-runtime/service/chat_thread_tabs.cjs":
        "e5a20f8addf62f575a3c91c55937d5160bfb326ef07c5449cc3bccaab8fc880c",
    "/srv/abit-dev-linux/relay-runtime/service/aibit_dot_handoff.cjs":
        "4d6c26fb17ee1d59c7d0974c29db6d024325e988eb9cc7c96037dae86a024464",
}
MAX_ENTRIES, MAX_BASES, MAX_MATCHES = 1024, 256, 4
MAX_FILE, MAX_REGISTRY, MAX_SOURCE = 8192, 65536, 131072
MAX_TOTAL, MAX_OUTPUT, SECONDS = 3 * 1024 * 1024, 65536, 15
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
TARGET = re.compile(r"[a-fA-F0-9]{16,64}\Z")
ATOM = re.compile(r"[A-Za-z0-9_.:@/~+\-]{1,256}\Z")
DOT_URL = re.compile(r"https://chatgpt\.com/dots/[A-Za-z0-9_-]{8,180}~[A-Za-z0-9_-]{8,180}\Z")
CONVERSATION = re.compile(r"https://chatgpt\.com/(?:g/g-p-[A-Za-z0-9_-]{8,190}/)?c/[A-Za-z0-9_-]{5,190}/?\Z")
BASE_NAME = re.compile(r"[a-f0-9]{64}\.base\.json\Z")
OTHER_NAME = re.compile(r"(?:[a-f0-9]{64}\.(?:root|conversation|terminal)\.json|\.[a-f0-9]{36}\.tmp)\Z")

class Refused(RuntimeError):
    pass

def require(condition, reason):
    if not condition:
        raise Refused(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()

def digest(value):
    return hashlib.sha256(value).hexdigest()

def audit_guard(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        require(not (isinstance(mode, str) and any(c in mode for c in "wax+")),
                "Filesystem mutation refused")
        require(not (isinstance(flags, int) and flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)),
            "Filesystem mutation refused")
    require(not event.startswith(("socket.", "subprocess.", "ctypes.", "shutil.")),
            "Network or action refused")
    require(event not in {
        "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty",
        "os.chmod", "os.chown", "os.mkdir", "os.rmdir", "os.remove",
        "os.rename", "os.link", "os.symlink", "os.truncate", "os.utime",
    }, "Filesystem mutation or process action refused")

class Budget:
    def __init__(self):
        self.deadline = time.monotonic() + SECONDS
        self.bytes = 0
    def check(self):
        require(time.monotonic() <= self.deadline, "Observation deadline exceeded")
    def consume(self, count):
        self.bytes += count
        require(self.bytes <= MAX_TOTAL, "Aggregate read bound exceeded")
        self.check()

def signature(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_uid, s.st_gid,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)

def private_metadata(s, directory=False):
    require((stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
            and s.st_uid == UID and s.st_gid == GID
            and not (stat.S_IMODE(s.st_mode) & 0o077)
            and (directory or s.st_nlink == 1),
            "Private selector metadata differs")

def open_directory(path):
    require(path.startswith("/") and all(p not in (".", "..") for p in path.split("/")),
            "Absolute fixed path required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in path.split("/")[1:]:
            if not part:
                continue
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise

def read_at(directory_fd, name, limit, budget, private=True):
    require("/" not in name and name not in ("", ".", ".."), "Single filename required")
    budget.check()
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                "Regular nonlinked file required")
        if private:
            private_metadata(before)
        require(before.st_size <= limit, "Per-file read bound exceeded")
        chunks, length = [], 0
        while True:
            chunk = os.read(fd, min(16384, limit + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            budget.consume(len(chunk))
            require(length <= limit, "Per-file read bound exceeded")
        after = os.fstat(fd)
        linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        require(signature(before) == signature(after) == signature(linked),
                "File changed during observation")
        raw = b"".join(chunks)
        require(len(raw) == before.st_size, "File size changed during observation")
        return raw, {"sha256": digest(raw), "bytes": len(raw), "device": before.st_dev,
                     "inode": before.st_ino, "uid": before.st_uid, "gid": before.st_gid,
                     "mode": oct(stat.S_IMODE(before.st_mode)),
                     "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}
    finally:
        os.close(fd)

def read_path(path, limit, budget, private=False):
    parent, name = path.rsplit("/", 1)
    fd = open_directory(parent)
    try:
        return read_at(fd, name, limit, budget, private)
    finally:
        os.close(fd)

def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key refused")
            result[key] = value
        return result
    def bad_constant(_):
        raise Refused("Nonfinite JSON number refused")
    result = json.loads(raw, object_pairs_hook=unique, parse_constant=bad_constant)
    require(type(result) is dict, "Object schema required")
    return result

def exact_keys(value, keys):
    require(type(value) is dict and set(value) == set(keys), "Selector schema differs")

def valid_atom(value):
    return isinstance(value, str) and bool(ATOM.fullmatch(value))

def valid_conversation(value):
    return isinstance(value, str) and len(value) <= 450 and bool(CONVERSATION.fullmatch(value))

def observe_registry(budget):
    value = read_path(REGISTRY, MAX_REGISTRY, budget, private=True)
    if value is None:
        return {"present": False, "matching_targets": {name: None for name in KEYS}}
    raw, metadata = value
    data = parse(raw)
    require(len(data) <= MAX_ENTRIES, "Registry entry bound exceeded")
    matches = {}
    for name, key in KEYS.items():
        row = data.get(key)
        if row is None:
            matches[name] = None
            continue
        exact_keys(row, ("id", "touched"))
        require(isinstance(row["id"], str) and bool(TARGET.fullmatch(row["id"])),
                "Registry target schema differs")
        require(type(row["touched"]) in (int, float) and 0 <= row["touched"] <= 2**53,
                "Registry timestamp schema differs")
        matches[name] = {"id": row["id"], "touched": row["touched"]}
    return {"present": True, "file": metadata, "entry_count": len(data),
            "matching_targets": matches}

def checked_base(base, filename):
    exact_keys(base, ("schema", "identity", "target_id", "origin"))
    identity = base["identity"]
    exact_keys(identity, ("owner", "account_id", "job_id", "thread_id", "url",
                          "message_sha256", "scope_sha256"))
    require(identity["owner"] == OWNER and identity["job_id"] == JOB
            and identity["thread_id"] == THREAD, "Target identity differs")
    require(all(isinstance(identity[k], str) and HEX64.fullmatch(identity[k])
                for k in ("account_id", "message_sha256", "scope_sha256")),
            "Selector digest schema differs")
    require(isinstance(identity["url"], str) and len(identity["url"]) <= 320
            and DOT_URL.fullmatch(identity["url"]), "Selector location schema differs")
    require(type(base["schema"]) is int and base["schema"] == 1
            and isinstance(base["target_id"], str) and TARGET.fullmatch(base["target_id"])
            and base["origin"] in ("admitted_original", "visible_original"),
            "Base selector schema differs")
    stem = digest(canonical([OWNER, identity["account_id"], JOB]))
    require(filename == stem + ".base.json", "Selector filename identity differs")
    return stem, digest(canonical(base))

def observe_bindings(budget):
    try:
        fd = open_directory(BINDINGS)
    except FileNotFoundError:
        return {"present": False, "matching_bindings": [], "account_binding_found": False}
    try:
        before = os.fstat(fd)
        private_metadata(before, directory=True)
        names = []
        with os.scandir(fd) as iterator:
            for entry in iterator:
                budget.check()
                names.append(entry.name)
                require(len(names) <= MAX_ENTRIES, "Binding directory count exceeded")
        require(all(BASE_NAME.fullmatch(n) or OTHER_NAME.fullmatch(n) for n in names),
                "Binding directory schema differs")
        available = set(names)
        require(all(not n.endswith((".root.json", ".conversation.json", ".terminal.json"))
                    or n[:64] + ".base.json" in available for n in names),
                "Orphan selector companion refused")
        bases = sorted(n for n in names if BASE_NAME.fullmatch(n))
        require(len(bases) <= MAX_BASES, "Base selector count exceeded")
        matches, base_metadata = [], {}
        for filename in bases:
            value = read_at(fd, filename, MAX_FILE, budget)
            require(value is not None, "Base selector vanished")
            raw, metadata = value
            base_metadata[filename] = metadata
            base = parse(raw)
            identity = base.get("identity")
            # Other owners/jobs are never emitted, and their companion files are not read.
            if not isinstance(identity, dict) or identity.get("owner") != OWNER or identity.get("job_id") != JOB:
                continue
            require(len(matches) < MAX_MATCHES, "Matching selector count exceeded")
            stem, binding_hash = checked_base(base, filename)
            companions, evidence = {}, {"base": metadata}
            for kind in ("root", "conversation", "terminal"):
                v = read_at(fd, stem + "." + kind + ".json", MAX_FILE, budget)
                companions[kind] = None if v is None else parse(v[0])
                if v is not None:
                    evidence[kind] = v[1]
            root, conversation, terminal = (companions[k] for k in ("root", "conversation", "terminal"))
            if root is not None:
                exact_keys(root, ("schema", "binding_sha256", "root_id", "location"))
                require(valid_atom(root["root_id"]) and (
                    root["location"] in (identity["url"], identity["url"] + "/")
                    or valid_conversation(root["location"])), "Root selector differs")
            if conversation is not None:
                exact_keys(conversation, ("schema", "binding_sha256", "root_id", "url"))
                require(root is not None and conversation["root_id"] == root["root_id"]
                        and valid_conversation(conversation["url"])
                        and not conversation["url"].endswith("/")
                        and (not valid_conversation(root["location"])
                             or root["location"].rstrip("/") == conversation["url"]),
                        "Conversation selector differs")
            if terminal is not None:
                exact_keys(terminal, ("schema", "binding_sha256", "root_id", "reply_id", "response_sha256"))
                require(root is not None and terminal["root_id"] == root["root_id"]
                        and valid_atom(terminal["reply_id"])
                        and isinstance(terminal["response_sha256"], str)
                        and HEX64.fullmatch(terminal["response_sha256"]), "Terminal selector differs")
            require(all(v is None or (type(v["schema"]) is int and v["schema"] == 1
                        and v["binding_sha256"] == binding_hash) for v in companions.values()),
                    "Companion selector binding differs")
            matches.append({"identity": dict(identity), "target_id": base["target_id"],
                            "origin": base["origin"], "binding_sha256": binding_hash,
                            "selectors": companions, "files": evidence,
                            "historical_send_verified": False, "provider_outcome_verified": False})
        require(signature(before) == signature(os.fstat(fd)), "Binding directory changed")
        # Recheck every inspected base identity, including nonmatches, before claiming absence.
        for filename, metadata in base_metadata.items():
            budget.check()
            s = os.stat(filename, dir_fd=fd, follow_symlinks=False)
            private_metadata(s)
            require((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) ==
                    (metadata["device"], metadata["inode"], metadata["bytes"],
                     metadata["mtime_ns"], metadata["ctime_ns"]), "Base selector changed")
        # Recheck matched companion bytes as well; none are imported or acted upon.
        for match in matches:
            stem = digest(canonical([OWNER, match["identity"]["account_id"], JOB]))
            for kind, metadata in match["files"].items():
                value = read_at(fd, stem + "." + kind + ".json", MAX_FILE, budget)
                require(value is not None and value[1] == metadata, "Matched selector changed")
        require(signature(before) == signature(os.fstat(fd)), "Binding directory changed")
        return {"present": True, "entry_count": len(names), "bases_examined": len(bases),
                "matching_bindings": matches, "account_binding_found": bool(matches),
                "historical_binding_authenticated": False}
    finally:
        os.close(fd)

def verify_sources(budget):
    for path, expected in SOURCES.items():
        value = read_path(path, MAX_SOURCE, budget)
        require(value is not None and value[1]["sha256"] == expected, "Installed selector source drift")

def deadline(*_):
    raise Refused("Observation deadline exceeded")

def main():
    require(len(sys.argv) == 1 and os.getuid() == os.geteuid() == UID
            and os.getgid() == os.getegid() == GID and os.uname().nodename == HOST,
            "No arguments; exact service identity and host required")
    require(os.path.abspath(__file__) == SCRIPT, "Fixed execution path required")
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    sys.addaudithook(audit_guard)
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(SECONDS)
    budget = Budget()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    source = read_path(SCRIPT, MAX_SOURCE, budget)
    require(source is not None, "Reader source missing")
    verify_sources(budget)
    registry = observe_registry(budget)
    bindings = observe_bindings(budget)
    require(observe_registry(budget) == registry, "Registry changed during observation")
    verify_sources(budget)
    require(read_path(SCRIPT, MAX_SOURCE, budget) == source, "Reader source changed")
    result = {
        "schema": 1, "ok": True, "read_only": True, "host": HOST,
        "service_uid": UID, "service_gid": GID,
        "started_utc": started, "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "script_sha256": source[1]["sha256"], "source_pins": SOURCES,
        "agent_id": AGENT, "owner": OWNER, "legacy_event_id": EVENT,
        "candidate_job_id": JOB, "candidate_only": True,
        "candidate_derivation": "sha256(owner|chat_id|origin_message_id)[:48]; not a provider receipt",
        "target_keys": KEYS, "registry": registry, "binding_store": bindings,
        "original_message_read": False, "provider_api_called": False,
        "historical_send_verified": False, "provider_outcome_verified": False,
        "original_outcome_reconciled": False, "lifecycle_mutation": False,
        "new_execution": False, "unknown_work_preserved": True,
        "limits": {"seconds": SECONDS, "entries": MAX_ENTRIES, "bases": MAX_BASES,
                   "matches": MAX_MATCHES, "file_bytes": MAX_FILE, "aggregate_bytes": MAX_TOTAL},
    }
    output = json.dumps(result, sort_keys=True, indent=2)
    require(len(output.encode()) <= MAX_OUTPUT, "Output bound exceeded")
    budget.check()
    signal.alarm(0)
    print(output)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "read_only": True, "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, Refused) else "Read failed; exception class preserved",
                          "new_execution": False, "original_outcome_reconciled": False}), file=sys.stderr)
        raise SystemExit(2)

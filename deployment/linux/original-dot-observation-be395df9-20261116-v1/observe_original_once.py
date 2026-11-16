#!/usr/bin/env python3
"""One reviewed original-only observation. Default --check reads no auth or HTTP.
Run with Python -I -B as root. No direct submit, recovery, stop, or ownership writes.
The pinned route may activate the existing page, prune its stale mapping, or settle
an existing in-memory capture. A terminal response is evidence, not recovery.
"""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import resource
import signal
import sqlite3
import stat
import sys
import time
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

DATA = Path("/srv/abit-dev-linux/abit-ai/data")
UTILS = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui/utils")
RELAY = Path("/srv/abit-dev-linux/relay-runtime/service")
REGISTRY = Path("/srv/abit-dev-linux/relay-runtime/state/chat-thread-targets.json")
AUTH = Path("/srv/abit-dev-linux/relay-runtime/secrets/relay.token")
OUT = Path("/var/lib/abit-ai-original-observation-be395df9-20261116-v1")
RUN = "5955703a-4a31-5a33-8265-63a5299bdace"
INV = "agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0"
KEY = "262d4bafc6201732f911849e48c723961a7d06215066d16b031664254280d00f"
JOB = "aibit_c988b32cd427abd42258f73ff9dca4ed376167cd61acd070"
ACCOUNT = "7bc7424d450a5dd30b094291dd30649e4d43f7c3280500b7a60d4e55f1725099"
OWNER_SHA = "16d82d1e999e255346fcd7fafbd01a0a7bfe916acce1b9ef8d6f32e0ab81c3e4"
ENV_SHA = "ec3b73c9292f7276d2054d20b801e389617803c5c8139e87582df27d9b7de231"
SCOPE_SHA = "f84c55331a60e7d6be75ff7da97048a4639c102ccf5f4324c30d52d1e8ae1f7a"
SESSION_SHA = "a54aaa706465870b2c33c0125b9251e4f0b749ea37bd6917cb92d6b63c994861"
ADMISSION_SHA = "a512b2cd74ee1039a4ef08a116739140a0b606ac94ed8e0d829cdfc0fbdc1307"
THREAD_KEY = "f451ca1bc0c4cb367e4b3438a78f9cb5b9678702"
TARGET = {"id": "CFE8FB94A61EE88B229F3A1D7EA3A96D", "touched": 1794843406191}
NODE_PID, NODE_START = 2877321, "77927719"
CORE_PID, CORE_START = 2028435, "79574991"
MAX_BODY = 2 * 1024 * 1024
PINS = {
    RELAY / "aibit_relay.cjs": "f8c07bf55d56b6501ffe4d9d05169af7fa8e9e0d876b8695e128c8d58122b48e",
    RELAY / "original_dot_capture.cjs": "e55e4bdaac7371aa49703d5318b627dc6fefcbdf0fe9e328d7f6866448867174",
    RELAY / "aibit_dot_observation.cjs": "1db14ae34793af2eafa8845aac86256cd41cb10cfd10b3092010eb334e890754",
    RELAY / "aibit_dot_handoff.cjs": "4d6c26fb17ee1d59c7d0974c29db6d024325e988eb9cc7c96037dae86a024464",
    RELAY / "chat_thread_tabs.cjs": "1057d64935a8d51f1695893911f8d342701958f7c3f694027f5d17d5d2555dcc",
    RELAY / "aibit_dot_feed.cjs": "e161607523133954ee57c487ca500770dee7bb5edba97fa773b30fc158be655b",
    RELAY / "existing_chrome_cdp.cjs": "548c9f0be69209c2b2c1f2efce9c66572861863343ae0f6fa8ce6e53850bac86",
    RELAY / "agentic-gateway/relay.cjs": "88308fee2b56d8fecd9625b9f9e915164771887caa71d531be45ce1dbddbd603",
    RELAY / "agentic-gateway/wire.cjs": "14b1be3703e17d2fcb9c070ceb55ef34d6cb24272dd875cc7ecb1820cdf24887",
    RELAY / "start-relay.sh": "0040069c1e74eff24e3e2d5c6fd3d412bab298a089bdefe81930c6004e148080",
    UTILS / "agentic_bridge.py": "a0220b323e62fa27c14b00cdda61f5af4439805c0963c94b4b301091bd327f3b",
    UTILS / "abitdot_primary.py": "530d7ed8cbc4eec40c361d9ed1a34a411928f4f832198dc8351518c11b7487fd",
    UTILS / "agentic_tool_gateway.py": "463b9550ccdc1ce8edc2b85759981c8ca316cb8dba1aab3a2acfc206d33ba065",
    UTILS / "agentic_relay_scope.py": "c3cdd4d3dbb5143ec594c7f9422aae725b1ae192287ecfb7e269fe422d4ff6ea",
    UTILS / "abitdot_primary_relay.py": "01b964ab6c1970c01397cc2737a2286a6bbdcae27cf66b5662a049fe4c798132",
    UTILS / "agentic_gateway_sessions.py": "0cc3863d0ee141016886ab96c2402d242691999a3c3da7b39ad9e9153d6d1e2f",
    UTILS / "agentic_governance.py": "a86e819585256aafb904abb7eeb2263a252d0f872f05eb55078b2184f7e26787",
    UTILS.parent / "routers/agentic_governance.py": "ee5c1bca3f2294577f1fb1599b2a3cd46c1f388592603ade47a161307269e2cd",
}


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def utc():
    return datetime.now(timezone.utc).isoformat()


def regular_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "file_bounds")
        with os.fdopen(os.dup(fd), "rb") as handle:
            data = handle.read(limit + 1)
        after = os.fstat(fd)
        require(len(data) <= limit and (before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns), "file_changed")
        return data
    finally:
        os.close(fd)


def source_pins():
    sources = {}
    for path, expected in PINS.items():
        data = regular_bytes(path, 2 * 1024 * 1024)
        require(sha(data) == expected, "source_pin_changed")
        sources[path] = data
    return sources


def pure_helpers(sources):
    # Execute only these exact definitions/constants from hash-pinned modules.
    # Replace the two selected internal imports with the same pinned pure helpers.
    selections = {
        "agentic_bridge.py": "canonical digest stable_run_id",
        "abitdot_primary.py": "dot_primary_key",
        "agentic_tool_gateway.py": "CapabilityUnavailable",
        "agentic_relay_scope.py": "PROTOCOL_VERSION MAX_PROOF_BYTES MAX_PROOF_SECONDS PROOF_ID SESSION_ID SCOPE_FIELDS ENFORCEMENT_MODE ENFORCEMENT_BOUNDARY EXCLUDED_SURFACES PROOF_FIELDS BLOCKER _unavailable _proof handoff_scope",
        "abitdot_primary_relay.py": "ACCOUNT_ID DOT_JOB_ID MAX_MESSAGE RelayPreflightError verified_dot_payload",
        "agentic_gateway_sessions.py": "MODE BOUNDARY EXCLUDED receipt",
    }
    allowed_imports = {
        ("open_webui.utils.abitdot_primary", "dot_primary_key"),
        ("open_webui.utils.agentic_relay_scope", "handoff_scope"),
    }
    removed = set()

    class PureImports(ast.NodeTransformer):
        def visit_Import(self, node):
            raise RuntimeError("unexpected_helper_import")
        def visit_ImportFrom(self, node):
            require(node.level == 0 and len(node.names) == 1 and node.names[0].asname is None,
                    "unexpected_helper_import")
            pair = (node.module, node.names[0].name)
            require(pair in allowed_imports and pair not in removed, "unexpected_helper_import")
            removed.add(pair)
            return None

    ns = dict(json=json, hashlib=hashlib, re=re, time=time, NAMESPACE_URL=NAMESPACE_URL, uuid5=uuid5)
    for filename, words in selections.items():
        wanted = set(words.split())
        selected, found = [], set()
        for node in ast.parse(sources[UTILS / filename]).body:
            names = {node.name} if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else {
                t.id for t in node.targets if isinstance(t, ast.Name)
            } if isinstance(node, ast.Assign) else set()
            if names & wanted:
                require(names <= wanted and not names & found, "ambiguous_helper_selection")
                found |= names
                selected.append(PureImports().visit(node))
        require(found == wanted, "missing_helper_definition")
        tree = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
        exec(compile(tree, str(UTILS / filename), "exec"), ns)
    require(removed == allowed_imports, "pure_import_contract")
    return ns


@contextmanager
def ro(name):
    db = sqlite3.connect((DATA / name).as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    try:
        yield db
        require(db.execute("PRAGMA query_only").fetchone()[0] == 1 and db.total_changes == 0,
                "read_only_contract")
    finally:
        db.close()


def one(db, sql, args):
    rows = db.execute(sql, args).fetchall()
    require(len(rows) == 1, "original_row_count")
    return dict(rows[0])


def snapshot():
    require(os.geteuid() == 999 and os.getegid() == 988, "reader_identity")
    value = {"observed_utc": utc(), "query_only": 1, "total_changes": 0}
    with ro("abitdot-primary.sqlite") as db:
        value["provider"] = one(db,
            "SELECT id,original_run_id,status,reason,envelope_json,created_at,updated_at,"
            "response_text IS NULL AS no_response,receipt IS NULL AS no_receipt,"
            "scope_receipt_json IS NULL AS no_scope_receipt FROM dot_primary_runs WHERE original_run_id=?", (RUN,))
    with ro("agentic-governance.sqlite") as db:
        value["invocation"] = one(db,
            "SELECT invocation_id,automation_run_id,erp_project,erp_task,state,dispatch_attempts,"
            "lease_fence,lease_expires_at,updated_at,packet_hash,packet_version FROM governance_invocation WHERE invocation_id=?", (INV,))
        value["lease"] = one(db,
            "SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?",
            ("PROJ-0196", "TASK-2026-06222"))
        value["gateway"] = one(db,
            "SELECT session_id,state,scope_json,admission_json FROM governance_gateway_session WHERE invocation_id=?", (INV,))
        value["task_controls"] = len(db.execute(
            "SELECT state FROM governance_task_control WHERE project=? AND task=?",
            ("PROJ-0196", "TASK-2026-06222")).fetchall())
        value["result_count"] = sum(db.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE invocation_id=?", (INV,)).fetchone()[0]
            for table in ("governance_result", "governance_local_result"))
    with ro("webui.db") as db:
        value["automation"] = one(db,
            "SELECT id,automation_id,status,chat_id IS NULL AS no_chat,error IS NULL AS no_error,"
            "lease_until IS NULL AS no_lease FROM automation_run WHERE id=?", (RUN,))
    return value


def service_snapshot():
    # The private envelope crosses only an anonymous pipe to this root process.
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            os.setgroups([])
            os.setgid(988)
            os.setuid(999)
            data = canonical(snapshot()).encode()
            with os.fdopen(write_fd, "wb") as out:
                out.write(data)
            os._exit(0)
        except BaseException:
            os._exit(2)
    os.close(write_fd)
    try:
        with os.fdopen(read_fd, "rb") as source:
            data = source.read(200001)
        _, status = os.waitpid(child, 0)
        require(status == 0 and len(data) <= 200000, "service_reader_failed")
        return json.loads(data)
    finally:
        try:
            os.waitpid(child, os.WNOHANG)
        except ChildProcessError:
            pass


def runtime_guard():
    for pid, start in ((NODE_PID, NODE_START), (CORE_PID, CORE_START)):
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        require(fields[19] == start, "process_cohort_changed")
    socket_inodes = set()
    for fd in Path(f"/proc/{NODE_PID}/fd").iterdir():
        try:
            target = os.readlink(fd)
            if target.startswith("socket:["):
                socket_inodes.add(target[8:-1])
        except FileNotFoundError:
            pass
    listeners = [line.split() for line in Path("/proc/net/tcp").read_text().splitlines()[1:]]
    require(any(row[1] == "0100007F:226A" and row[3] == "0A" and row[9] in socket_inodes
                for row in listeners), "exact_relay_listener_missing")  # 8810, loopback only
    data = regular_bytes(REGISTRY, 1024 * 1024)
    require(json.loads(data).get(THREAD_KEY) == TARGET, "original_target_mapping_changed")
    return {"node_pid": NODE_PID, "node_start_ticks": NODE_START,
            "core_pid": CORE_PID, "core_start_ticks": CORE_START,
            "selected_target": TARGET, "registry_sha256": sha(data)}


def prepare(sources, state):
    ns = pure_helpers(sources)
    p, inv, gateway, auto = (state[k] for k in ("provider", "invocation", "gateway", "automation"))
    raw = p["envelope_json"].encode()
    require(len(raw) == 9209 and sha(raw) == ENV_SHA, "original_envelope_changed")
    require(p["id"] == KEY and p["original_run_id"] == RUN and p["status"] == "unknown"
            and p["reason"] == "unverified_dot_receipt" and p["updated_at"] == 1794847005
            and all(p[k] == 1 for k in ("no_response", "no_receipt", "no_scope_receipt")), "provider_changed")
    require(inv["invocation_id"] == INV and inv["automation_run_id"] == RUN and inv["state"] == "uncertain"
            and inv["erp_project"] == "PROJ-0196" and inv["erp_task"] == "TASK-2026-06222"
            and inv["dispatch_attempts"] == 1 and inv["lease_fence"] == 2 and inv["updated_at"] == 1794847005
            and inv["packet_version"] == 1
            and inv["packet_hash"] == "3e8a02f987cb894296bd801f68b6da47d007774c265f471c7e73bbbcf4df550a",
            "invocation_changed")
    require(state["lease"] == {"invocation_id": INV, "fence": 2} and state["task_controls"] == 0
            and state["result_count"] == 0, "ownership_or_result_changed")
    envelope = json.loads(raw)
    require(auto["id"] == RUN and auto["automation_id"] == envelope["automation_id"]
            and auto["status"] == "running" and all(auto[k] == 1 for k in ("no_chat", "no_error", "no_lease")),
            "automation_changed")
    payload = ns["verified_dot_payload"](envelope, ACCOUNT, admission=False)
    require(payload["job_id"] == JOB and payload["channel"] == "abitdot"
            and sha(payload["owner"].encode()) == OWNER_SHA, "original_identity_changed")
    value = payload["governance_scope"]
    require(value["scope"]["invocation_id"] == INV and value["scope"]["run_id"] == RUN
            and ns["digest"](value["scope"]) == SCOPE_SHA, "original_scope_changed")
    require(gateway["state"] == "admitted" and sha(gateway["session_id"].encode()) == SESSION_SHA
            and sha(gateway["admission_json"].encode()) == ADMISSION_SHA
            and gateway["admission_json"] == canonical(value)
            and gateway["scope_json"] == canonical(value["scope"]), "original_admission_changed")
    thread = "aibit:" + payload["owner"] + ":dot_" + JOB
    require(sha(thread.encode())[:40] == THREAD_KEY, "original_thread_changed")
    body = canonical(payload).encode()
    require(len(body) <= 200000 and set(payload) ==
            {"owner", "job_id", "account_id", "channel", "message", "governance_scope"}, "request_contract")
    expected = ns["receipt"](value)
    facts = {"observed_utc": state["observed_utc"], "query_only": 1, "total_changes": 0,
             "run_id": RUN, "invocation_id": INV, "job_id": JOB,
             "provider_status": p["status"], "provider_updated_at": p["updated_at"],
             "governance_state": inv["state"], "automation_status": auto["status"],
             "lease_fence": inv["lease_fence"], "lease_expires_at": inv["lease_expires_at"],
             "envelope_bytes": len(raw), "envelope_sha256": ENV_SHA,
             "request_bytes": len(body), "request_sha256": sha(body),
             "original_message_sha256": sha(payload["message"].encode()),
             "scope_sha256": SCOPE_SHA, "expected_scope_receipt_sha256": ns["digest"](expected)}
    return body, payload, expected, facts


def save_private(name, data):
    fd = os.open(OUT / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def safe_result(raw, status, payload, expected):
    result = json.loads(raw)
    require(isinstance(result, dict), "response_not_object")
    response = result.get("response")
    receipt = result.get("scope_receipt")
    observed = result.get("observed") if isinstance(result.get("observed"), dict) else {}
    correlation = (result.get("owner") == payload["owner"] and result.get("account_id") == ACCOUNT
                   and result.get("job_id") == JOB and receipt == expected)
    url = urlsplit(result.get("conversation_url") or "")
    safe_url = (url.scheme == "https" and url.netloc == "chatgpt.com" and not url.fragment)
    root_id, reply_id = result.get("root_message_id"), result.get("reply_message_id")
    ids_valid = all(isinstance(v, str) and 0 < len(v) <= 256 for v in (root_id, reply_id))
    terminal = (status == 200 and result.get("ok") is True and result.get("read_only") is True
                and result.get("dot_handoff") is True and correlation and safe_url and ids_valid
                and isinstance(response, str) and bool(response.strip()) and observed.get("generating") is False)
    return {"http_status": status, "body_bytes": len(raw), "body_sha256": sha(raw),
            "identity_and_scope_match": correlation, "terminal_evidence_candidate": terminal,
            "generation_idle_observed": observed.get("generating") is False,
            "response_bytes": len(response.encode()) if isinstance(response, str) else None,
            "response_sha256": sha(response.encode()) if isinstance(response, str) else None,
            "root_id_sha256": sha(root_id.encode()) if isinstance(root_id, str) else None,
            "reply_id_sha256": sha(reply_id.encode()) if isinstance(reply_id, str) else None,
            "scope_receipt_sha256": sha(canonical(receipt).encode()) if isinstance(receipt, dict) else None,
            "strict_receipt_recovery_performed": False}


def deadline(_signum, _frame):
    raise TimeoutError("bounded_observation_deadline")


def main():
    require(os.geteuid() == 0, "root_required")
    require(sys.flags.isolated and sys.dont_write_bytecode, "use_python_I_B")
    mode = sys.argv[1:] or ["--check"]
    require(mode in (["--check"], ["--execute-reviewed-once"]), "invalid_mode")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(35)
    sources = source_pins()
    body, payload, expected, facts = prepare(sources, service_snapshot())
    cohort = runtime_guard()
    proof = {"controller_sha256": sha(Path(__file__).read_bytes()), "preflight": facts,
             "cohort": cohort, "source_pins": {str(p): v for p, v in PINS.items()}}
    if mode == ["--check"]:
        signal.alarm(0)
        print(canonical({"mode": "read_only_check", "auth_read": False, "http_requests": 0, **proof}))
        return 0

    # Fixed, never-reused directory is the attempt fence, even if pre-send work fails.
    OUT.mkdir(mode=0o700)
    require(OUT.lstat().st_uid == 0 and stat.S_IMODE(OUT.lstat().st_mode) == 0o700, "private_output_mode")
    save_private("intent.json", canonical({"created_utc": utc(), "route": "POST /dot/observe",
                 "endpoint": "127.0.0.1:8810", "attempt": 1, "automatic_retry": False, **proof}).encode())
    directory = os.open(OUT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    result = {"started_utc": utc(), "request_attempted": False, "automatic_retry": False,
              "run_id": RUN, "job_id": JOB, "strict_receipt_recovery_performed": False}
    connection = None
    try:
        # Recheck target/process/source immediately before reading configured auth.
        source_pins()
        runtime_guard()
        token_stat = AUTH.lstat()
        require(stat.S_ISREG(token_stat.st_mode) and token_stat.st_uid == 999
                and not token_stat.st_mode & 0o007 and 0 < token_stat.st_size <= 4096, "auth_file_metadata")
        token = regular_bytes(AUTH, 4096).decode("utf-8").strip()
        require(token and "\r" not in token and "\n" not in token, "auth_file_format")
        signal.alarm(75)
        connection = http.client.HTTPConnection("127.0.0.1", 8810, timeout=65)
        result["request_attempted"] = True
        connection.request("POST", "/dot/observe", body=body,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                    "Accept": "application/json", "Connection": "close"})
        response = connection.getresponse()
        raw = response.read(MAX_BODY + 1)
        require(len(raw) <= MAX_BODY, "response_body_limit")
        require(token.encode() not in raw, "unexpected_auth_echo_withheld")
        save_private("response.raw", raw)
        result.update(safe_result(raw, response.status, payload, expected))
        result["classification"] = ("terminal_evidence_candidate" if result["terminal_evidence_candidate"]
                                    else "observation_only_unresolved")
    except Exception as error:
        # Never serialize the exception message, request, headers, private DOM or token.
        result["classification"] = "outcome_unknown_no_retry"
        result["error_class"] = type(error).__name__
    finally:
        signal.alarm(0)
        if connection is not None:
            connection.close()
        result["finished_utc"] = utc()
        save_private("result.json", canonical(result).encode())
    print(canonical(result))
    return 0 if result.get("terminal_evidence_candidate") else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(canonical({"classification": "controller_failed_no_retry", "error_class": type(error).__name__}))
        raise SystemExit(2)

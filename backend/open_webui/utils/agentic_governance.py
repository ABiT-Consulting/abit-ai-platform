"""Durable project registry, task fences, work packets and append-only evidence.

This module performs no ERP writes and never invokes a model. Callers must
verify live ERP authorization before passing a gate to the fenced executor.
Legacy ledger entry points remain available for pre-v2 records only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from open_webui.env import DATA_DIR

PROJECT_ID = re.compile(r"^PROJ-[0-9A-Za-z-]{1,60}$")
TASK_ID = re.compile(r"^TASK-[0-9A-Za-z-]{1,60}$")
ROLES = ("solution_architect", "project_manager", "developer", "qa", "operations", "it_manager")
HANDOFFS = {
    "erp": {"solution_architect", "project_manager"},
    "solution_architect": {"project_manager", "it_manager"},
    "project_manager": {"solution_architect", "developer", "qa", "operations", "it_manager"},
    "developer": {"qa", "it_manager", "project_manager"},
    "qa": {"developer", "operations", "project_manager"},
    "operations": {"qa", "it_manager", "project_manager"},
    "it_manager": {"developer", "operations", "project_manager", "qa"},
}
RECONCILE_SECONDS = 1800
RUN_STALE_SECONDS = 3600
LEASE_SECONDS = 120
MAX_DISPATCH_ATTEMPTS = 3
ACTIVE = {"queued", "running", "uncertain", "cancel_requested", "execution_finished", "awaiting_approval"}
TERMINAL = {"completed", "failed", "blocked", "cancelled", "handed_off", "verified"}
PUBLIC_COLUMNS = (
    "invocation_id",
    "erp_project",
    "erp_task",
    "from_role",
    "target_role",
    "agent_id",
    "context_hash",
    "task_revision",
    "state",
    "automation_run_id",
    "created_at",
    "updated_at",
    "reason",
    "packet_version",
    "packet_hash",
    "source_invocation_id",
    "actor",
    "gate_revision",
    "lease_fence",
    "lease_expires_at",
    "submitted_at",
    "dispatch_attempts",
    "qa_verdict",
    "parent_invocation_id",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _actor(actor):
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 254:
        raise ValueError("An attributable authenticated actor is required")
    return actor


def agent_id(project: str, role: str) -> str:
    if not PROJECT_ID.fullmatch(project) or role not in ROLES:
        raise ValueError("Invalid project or role")
    return str(uuid5(NAMESPACE_URL, f"https://abit.ai/agentic/v1/{project}/{role}"))


def work_digest(project: str, task: str, role: str, context: str) -> str:
    return _digest([project, task, role, context])


def work_packet_digest(packet: dict) -> str:
    if (
        not isinstance(packet, dict)
        or type(packet.get("version", packet.get("schema_version"))) is not int
        or packet.get("version", packet.get("schema_version")) != 1
    ):
        raise ValueError("Unsupported versioned WorkPacket")
    encoded = _json(packet)
    if len(encoded.encode()) > 200_000:
        raise ValueError("WorkPacket exceeds the evidence limit")
    return hashlib.sha256(encoded.encode()).hexdigest()


def path() -> Path:
    return Path(DATA_DIR) / "agentic-governance.sqlite"


@contextmanager
def connect(db_path: Path | None = None):
    target = Path(db_path) if db_path else path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Governance state must be a regular file")
    finally:
        os.close(fd)
    conn = sqlite3.connect(target, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS governance_agent(
                agent_id TEXT PRIMARY KEY, erp_project TEXT NOT NULL, erp_user TEXT NOT NULL,
                role TEXT NOT NULL, owner_id TEXT NOT NULL, created_at INTEGER NOT NULL,
                UNIQUE(erp_project,role));
            CREATE TABLE IF NOT EXISTS governance_invocation(
                invocation_id TEXT PRIMARY KEY, erp_project TEXT NOT NULL, erp_task TEXT NOT NULL,
                from_role TEXT NOT NULL, target_role TEXT NOT NULL, agent_id TEXT NOT NULL,
                context_hash TEXT NOT NULL, context TEXT NOT NULL, task_revision TEXT NOT NULL,
                state TEXT NOT NULL, automation_run_id TEXT, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, reason TEXT,
                FOREIGN KEY(agent_id) REFERENCES governance_agent(agent_id));
            CREATE INDEX IF NOT EXISTS ix_gov_invocation_project ON governance_invocation(erp_project,created_at);
            CREATE INDEX IF NOT EXISTS ix_gov_invocation_state ON governance_invocation(state,updated_at);
            CREATE TABLE IF NOT EXISTS governance_project_control(
                erp_project TEXT PRIMARY KEY, state TEXT NOT NULL, actor TEXT NOT NULL,
                policy_revision TEXT NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS governance_task_lease(
                erp_project TEXT NOT NULL, erp_task TEXT NOT NULL, invocation_id TEXT,
                fence INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(erp_project,erp_task));
            CREATE TABLE IF NOT EXISTS governance_event(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                erp_project TEXT NOT NULL, invocation_id TEXT, kind TEXT NOT NULL,
                actor TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,
                previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS governance_event_no_update BEFORE UPDATE ON governance_event
                BEGIN SELECT RAISE(ABORT,'Governance audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS governance_event_no_delete BEFORE DELETE ON governance_event
                BEGIN SELECT RAISE(ABORT,'Governance audit is append-only'); END;
            CREATE TABLE IF NOT EXISTS governance_verification(
                invocation_id TEXT PRIMARY KEY, qa_invocation_id TEXT NOT NULL,
                evidence_digest TEXT NOT NULL, erp_readback_digest TEXT NOT NULL,
                actor TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS governance_inbox(
                event_id TEXT PRIMARY KEY, erp_project TEXT NOT NULL, payload_hash TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL, invocation_id TEXT,
                received_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        """)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(governance_invocation)")}
        additions = {
            "packet_version": "INTEGER NOT NULL DEFAULT 0",
            "packet_hash": "TEXT",
            "work_packet": "TEXT",
            "source_invocation_id": "TEXT",
            "actor": "TEXT",
            "gate_revision": "TEXT",
            "lease_token": "TEXT",
            "lease_fence": "INTEGER NOT NULL DEFAULT 0",
            "lease_expires_at": "INTEGER",
            "submitted_at": "INTEGER",
            "dispatch_attempts": "INTEGER NOT NULL DEFAULT 0",
            "qa_verdict": "TEXT",
            "parent_invocation_id": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                try:
                    conn.execute(f"ALTER TABLE governance_invocation ADD COLUMN {name} {declaration}")
                except sqlite3.OperationalError:
                    if name not in {r[1] for r in conn.execute("PRAGMA table_info(governance_invocation)")}:
                        raise
        yield conn
    finally:
        conn.close()


@contextmanager
def _transaction(db_path=None):
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise


def _event(db, project, invocation, kind, actor, payload):
    previous = db.execute(
        "SELECT event_hash FROM governance_event WHERE erp_project=? ORDER BY seq DESC LIMIT 1", (project,)
    ).fetchone()
    prior = previous[0] if previous else ""
    ident, now = uuid4().hex, int(time.time())
    body = _json(payload)
    digest = _digest([ident, project, invocation, kind, actor, body, now, prior])
    db.execute(
        "INSERT INTO governance_event(event_id,erp_project,invocation_id,kind,actor,payload,created_at,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?)",
        (ident, project, invocation, kind, actor, body, now, prior, digest),
    )


def audit_events(project, after=0, limit=100, db_path=None):
    if not PROJECT_ID.fullmatch(project):
        raise ValueError("Invalid ERP project")
    with connect(db_path) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM governance_event WHERE erp_project=? AND seq>? ORDER BY seq LIMIT ?",
                (project, after, min(max(1, limit), 500)),
            )
        ]


def enroll(
    project: str, owner: str, bindings: dict[str, str], db_path: Path | None = None, *, identities=None
) -> list[dict]:
    if not PROJECT_ID.fullmatch(project) or not owner or not bindings or set(bindings) - set(ROLES):
        raise ValueError("Invalid governance project enrollment")
    if identities is not None and (not isinstance(identities, dict) or set(identities) != set(bindings)):
        raise ValueError('Explicit specialist identities must match the enrolled roles')
    chosen = {role: (identities or {}).get(role, agent_id(project, role)) for role in bindings}
    try:
        if any(str(UUID(value)) != value for value in chosen.values()) or len(set(chosen.values())) != len(chosen):
            raise ValueError('Specialists require distinct canonical UUID identities')
    except (TypeError, AttributeError) as error:
        raise ValueError('Specialists require distinct canonical UUID identities') from error
    with _transaction(db_path) as db:
        for role, erp_user in bindings.items():
            ident = chosen[role]
            if not erp_user or len(erp_user) > 254:
                raise ValueError("Invalid ERP role user")
            existing = db.execute(
                "SELECT agent_id,erp_user,owner_id FROM governance_agent WHERE erp_project=? AND role=?",
                (project, role),
            ).fetchone()
            occupied = db.execute('SELECT erp_project,role FROM governance_agent WHERE agent_id=?', (ident,)).fetchone()
            if occupied and tuple(occupied) != (project, role):
                raise ValueError('Specialist identity belongs to another project or role')
            if existing and tuple(existing) != (ident, erp_user, owner):
                raise ValueError("Registered agent identity must not be silently redirected")
            if not existing:
                db.execute(
                    "INSERT INTO governance_agent VALUES(?,?,?,?,?,?)",
                    (ident, project, erp_user, role, owner, int(time.time())),
                )
                _event(
                    db, project, None, "agent_enrolled", owner, {"role": role, "agent_id": ident, "erp_user": erp_user}
                )
    return roster(project, db_path)


def registered_agent_id(project: str, role: str, db_path: Path | None = None) -> str:
    """Resolve the immutable registered identity; UUID5 is only a bootstrap default."""
    if not PROJECT_ID.fullmatch(project) or role not in ROLES:
        raise ValueError('Invalid project or role')
    with connect(db_path) as db:
        row = db.execute(
            'SELECT agent_id FROM governance_agent WHERE erp_project=? AND role=?', (project, role)
        ).fetchone()
    if not row:
        raise ValueError('Target agent is not registered for this ERP project')
    return row[0]


def roster(project: str, db_path: Path | None = None) -> list[dict]:
    if not PROJECT_ID.fullmatch(project):
        raise ValueError("Invalid project")
    with connect(db_path) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT agent_id,erp_user,role,owner_id FROM governance_agent WHERE erp_project=? ORDER BY role",
                (project,),
            )
        ]


def project_control(project, action="status", *, actor="", policy_revision="", db_path=None):
    if not PROJECT_ID.fullmatch(project) or action not in {"status", "enable", "pause", "stop", "resume"}:
        raise ValueError("Invalid project control")
    if action == "status":
        with connect(db_path) as db:
            row = db.execute("SELECT * FROM governance_project_control WHERE erp_project=?", (project,)).fetchone()
            return dict(row) if row else {"erp_project": project, "state": "disabled", "policy_revision": ""}
    _actor(actor)
    desired = {"enable": "enabled", "resume": "enabled", "pause": "paused", "stop": "stopped"}[action]
    with _transaction(db_path) as db:
        prior = db.execute(
            "SELECT state,policy_revision FROM governance_project_control WHERE erp_project=?", (project,)
        ).fetchone()
        if not prior or tuple(prior) != (desired, policy_revision):
            db.execute(
                "INSERT INTO governance_project_control VALUES(?,?,?,?,?) ON CONFLICT(erp_project) DO UPDATE SET state=excluded.state,actor=excluded.actor,policy_revision=excluded.policy_revision,updated_at=excluded.updated_at",
                (project, desired, actor, str(policy_revision)[:200], int(time.time())),
            )
            _event(
                db,
                project,
                None,
                "project_" + action,
                actor,
                {"state": desired, "policy_revision": str(policy_revision)[:200]},
            )
        if action == "stop":
            for row in db.execute(
                "SELECT * FROM governance_invocation WHERE erp_project=? AND state IN ('queued','running','uncertain','execution_finished','awaiting_approval')",
                (project,),
            ).fetchall():
                _cancel(db, row, actor)
    return project_control(project, db_path=db_path)


def _gate(db, project, gate):
    control = db.execute("SELECT state FROM governance_project_control WHERE erp_project=?", (project,)).fetchone()
    if not control or control[0] != "enabled":
        raise ValueError("ERP Project is disabled, paused or stopped")
    if (
        not isinstance(gate, dict)
        or gate.get("enabled") is not True
        or gate.get("pm_task_automation_enabled") is not True
        or gate.get("project_status") not in {"Open", "Active", "In Progress"}
        or gate.get("assigned_user") != "ai-projects@abitcons.com"
        or not isinstance(gate.get("revision"), str)
        or not gate["revision"]
    ):
        raise ValueError("Live ERP Project opt-in, active status and AI Projects assignment are required")


def _task_gate(db, project, task):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_task_control(
        project TEXT NOT NULL,task TEXT NOT NULL,state TEXT NOT NULL,actor TEXT NOT NULL,
        updated_at INTEGER NOT NULL,PRIMARY KEY(project,task))""")
    row = db.execute('SELECT state FROM governance_task_control WHERE project=? AND task=?', (project, task)).fetchone()
    if row and row[0] != 'enabled':
        raise ValueError('This ERP Task is paused or stopped')


def _public(row):
    return {k: row[k] for k in PUBLIC_COLUMNS}


def _packet(packet, project, task, revision):
    if packet is None:
        return 0, None, None
    digest = work_packet_digest(packet)
    for keys, expected in (
        (('project', 'erp_project', 'project_id'), project),
        (('task', 'task_id', 'erp_task', 'erp_task_id'), task),
        (('task_revision',), revision),
    ):
        if any(key in packet and packet[key] != expected for key in keys):
            raise ValueError("WorkPacket identity does not match invocation")
    return 1, digest, _json(packet)


def _acquire(db, project, task, invocation):
    row = db.execute(
        "SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?", (project, task)
    ).fetchone()
    if row and row[0] is not None:
        raise ValueError("ERP Task already has an active or uncertain execution")
    fence = row[1] + 1 if row else 1
    db.execute(
        "INSERT INTO governance_task_lease VALUES(?,?,?,?) ON CONFLICT(erp_project,erp_task) DO UPDATE SET invocation_id=excluded.invocation_id,fence=excluded.fence",
        (project, task, invocation, fence),
    )
    return fence


def _release(db, row):
    db.execute(
        "UPDATE governance_task_lease SET invocation_id=NULL WHERE erp_project=? AND erp_task=? AND invocation_id=? AND fence=?",
        (row['erp_project'], row['erp_task'], row['invocation_id'], row['lease_fence']),
    )


def _enqueue(
    db,
    *,
    invocation_id,
    project,
    task,
    from_role,
    target_role,
    task_revision,
    context,
    work_packet=None,
    source_invocation_id=None,
    actor="legacy",
    gate=None,
    event_id=None,
    parent_invocation_id=None,
    _qa_review=False,
):
    if isinstance(work_packet, dict) and work_packet.get('qa_review') and not _qa_review:
        raise ValueError('PM coordination requires the verified QA review factory')
    if _qa_review:
        from open_webui.utils.agentic_pm_review import _verified_source

        qa, proof = _verified_source(db, source_invocation_id)
        if (
            from_role != 'qa'
            or target_role != 'project_manager'
            or not work_packet
            or work_packet.get('qa_review') != proof
            or (project, task) != (qa['erp_project'], qa['erp_task'])
        ):
            raise ValueError('PM coordination source verification differs')
    if (
        not PROJECT_ID.fullmatch(project)
        or not TASK_ID.fullmatch(task)
        or from_role not in HANDOFFS
        or target_role not in HANDOFFS[from_role]
        or not isinstance(task_revision, str)
        or not task_revision
        or not isinstance(context, str)
        or not context
        or len(context) > 50_000
        or not re.fullmatch(r"[A-Za-z0-9_.:@+-]{16,160}", invocation_id)
    ):
        raise ValueError("Invalid scoped agent invocation")
    registered = db.execute(
        'SELECT agent_id FROM governance_agent WHERE erp_project=? AND role=?', (project, target_role)
    ).fetchone()
    if not registered:
        raise ValueError("Target agent is not registered for this ERP project")
    ident = registered[0]
    version, packet_hash, encoded = _packet(work_packet, project, task, task_revision)
    digest = work_digest(project, task, target_role, context)
    prior = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation_id,)).fetchone()
    if prior:
        if any(
            prior[k] != v
            for k, v in {
                "erp_project": project,
                "erp_task": task,
                "from_role": from_role,
                "target_role": target_role,
                "agent_id": ident,
                "context_hash": digest,
                "task_revision": task_revision,
                "packet_hash": packet_hash,
                "source_invocation_id": source_invocation_id,
                "parent_invocation_id": parent_invocation_id,
            }.items()
        ):
            raise ValueError("Invocation idempotency key already has different content")
        if event_id:
            _ack_event(db, event_id, invocation_id, project)
        return _public(prior), False
    if gate is not None or version:
        _gate(db, project, gate)
        _task_gate(db, project, task)
    if source_invocation_id and not _qa_review:
        raise ValueError("Source invocations require the atomic handoff operation")
    # Include legacy records, which predate the explicit task-lease table.
    if db.execute(
        "SELECT 1 FROM governance_invocation WHERE erp_project=? AND erp_task=? AND state IN ('queued','running','uncertain','cancel_requested','execution_finished','awaiting_approval') LIMIT 1",
        (project, task),
    ).fetchone():
        raise ValueError("ERP Task already has an active or uncertain execution")
    if parent_invocation_id:
        parent = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (parent_invocation_id,)
        ).fetchone()
        if (
            not parent
            or parent['erp_project'] != project
            or parent['target_role'] != from_role
            or parent['state'] != 'execution_finished'
            or parent['erp_task'] == task
            or not work_packet
            or work_packet.get('parent_task') != parent['erp_task']
        ):
            raise ValueError('Child invocation requires an exact finished source and verified parent Task')
        count = db.execute(
            'SELECT count(*) FROM governance_invocation WHERE parent_invocation_id=?', (parent_invocation_id,)
        ).fetchone()[0]
        if count >= 50:
            raise ValueError('Source invocation child fanout limit reached')
    fence = _acquire(db, project, task, invocation_id)
    now = int(time.time())
    db.execute(
        """INSERT INTO governance_invocation(invocation_id,erp_project,erp_task,from_role,target_role,agent_id,context_hash,context,task_revision,state,created_at,updated_at,packet_version,packet_hash,work_packet,source_invocation_id,actor,gate_revision,lease_fence,parent_invocation_id)
        VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)""",
        (
            invocation_id,
            project,
            task,
            from_role,
            target_role,
            ident,
            digest,
            context,
            task_revision,
            now,
            now,
            version,
            packet_hash,
            encoded,
            source_invocation_id,
            _actor(actor),
            str((gate or {}).get('revision') or ''),
            fence,
            parent_invocation_id,
        ),
    )
    if event_id:
        _ack_event(db, event_id, invocation_id, project)
    _event(
        db,
        project,
        invocation_id,
        "invocation_queued",
        actor,
        {
            "role": target_role,
            "packet_hash": packet_hash,
            "context_hash": digest,
            "fence": fence,
            "parent_invocation_id": parent_invocation_id,
        },
    )
    return (
        _public(db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation_id,)).fetchone()),
        True,
    )


def enqueue(
    *,
    invocation_id: str,
    project: str,
    task: str,
    from_role: str,
    target_role: str,
    task_revision: str,
    context: str,
    db_path: Path | None = None,
    work_packet=None,
    source_invocation_id=None,
    actor="legacy",
    gate=None,
    event_id=None,
    parent_invocation_id=None,
):
    with _transaction(db_path) as db:
        return _enqueue(
            db,
            invocation_id=invocation_id,
            project=project,
            task=task,
            from_role=from_role,
            target_role=target_role,
            task_revision=task_revision,
            context=context,
            work_packet=work_packet,
            source_invocation_id=source_invocation_id,
            actor=actor,
            gate=gate,
            event_id=event_id,
            parent_invocation_id=parent_invocation_id,
        )


def find(invocation_id: str, db_path: Path | None = None, *, include_private=False):
    with connect(db_path) as db:
        row = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation_id,)).fetchone()
        return (dict(row) if include_private else _public(row)) if row else None


def list_invocations(project: str, db_path: Path | None = None, limit: int = 30):
    if not PROJECT_ID.fullmatch(project):
        raise ValueError("Invalid project")
    with connect(db_path) as db:
        return [
            _public(row)
            for row in db.execute(
                "SELECT * FROM governance_invocation WHERE erp_project=? ORDER BY created_at DESC,invocation_id LIMIT ?",
                (project, min(max(1, limit), 100)),
            )
        ]


def _owned(db, invocation_id, run_id, token, states):
    row = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation_id,)).fetchone()
    if (
        not row
        or row['state'] not in states
        or row['automation_run_id'] != run_id
        or not token
        or not row['lease_token']
        or not secrets.compare_digest(row['lease_token'], token)
    ):
        return None
    lease = db.execute(
        "SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?",
        (row['erp_project'], row['erp_task']),
    ).fetchone()
    return row if lease and tuple(lease) == (invocation_id, row['lease_fence']) else None


def claim_invocation(invocation_id, run_id=None, *, gate, db_path=None):
    with _transaction(db_path) as db:
        row = db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation_id,)).fetchone()
        if not row or row['state'] != 'queued':
            return None
        if row['packet_version'] != 1:
            raise ValueError('Execution requires a versioned WorkPacket; refresh legacy queued work')
        _gate(db, row['erp_project'], gate)
        if row['dispatch_attempts'] >= MAX_DISPATCH_ATTEMPTS:
            db.execute(
                "UPDATE governance_invocation SET state='blocked',reason='Pre-submission retry limit reached',updated_at=? WHERE invocation_id=?",
                (int(time.time()), invocation_id),
            )
            _release(db, row)
            _event(db, row['erp_project'], invocation_id, 'retry_limit', 'executor', {})
            return None
        lease = db.execute(
            "SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?",
            (row['erp_project'], row['erp_task']),
        ).fetchone()
        if not lease or tuple(lease) != (invocation_id, row['lease_fence']):
            return None
        now = int(time.time())
        token = secrets.token_hex(32)
        db.execute(
            "UPDATE governance_invocation SET state='running',automation_run_id=COALESCE(?,automation_run_id),lease_token=?,lease_expires_at=?,dispatch_attempts=dispatch_attempts+1,updated_at=? WHERE invocation_id=?",
            (run_id, token, now + LEASE_SECONDS, now, invocation_id),
        )
        _event(
            db,
            row['erp_project'],
            invocation_id,
            'execution_claimed',
            'executor',
            {'fence': row['lease_fence'], 'attempt': row['dispatch_attempts'] + 1},
        )
        return dict(
            db.execute("SELECT * FROM governance_invocation WHERE invocation_id=?", (invocation_id,)).fetchone()
        )


def bind_run(invocation_id, lease_token, run_id, db_path=None):
    if not isinstance(run_id, str) or not run_id:
        raise ValueError('Invalid AutomationRun')
    with _transaction(db_path) as db:
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        if (
            not row
            or not _owned(db, invocation_id, row['automation_run_id'], lease_token, {'running'})
            or row['automation_run_id'] not in (None, run_id)
        ):
            return False
        db.execute(
            'UPDATE governance_invocation SET automation_run_id=?,updated_at=? WHERE invocation_id=?',
            (run_id, int(time.time()), invocation_id),
        )
        return True


def heartbeat(invocation_id, run_id, lease_token, db_path=None):
    with _transaction(db_path) as db:
        row = _owned(db, invocation_id, run_id, lease_token, {'running'})
        if not row:
            return False
        now = int(time.time())
        db.execute(
            'UPDATE governance_invocation SET lease_expires_at=?,updated_at=? WHERE invocation_id=?',
            (now + LEASE_SECONDS, now, invocation_id),
        )
        return True


def mark_submitted(invocation_id, run_id, lease_token, *, gate=None, db_path=None):
    with _transaction(db_path) as db:
        row = _owned(db, invocation_id, run_id, lease_token, {'running'})
        if not row or not run_id or row['lease_expires_at'] < int(time.time()):
            return False
        _gate(db, row['erp_project'], gate)
        if row['submitted_at'] is not None:
            return False
        now = int(time.time())
        db.execute(
            'UPDATE governance_invocation SET submitted_at=?,updated_at=? WHERE invocation_id=?',
            (now, now, invocation_id),
        )
        _event(
            db,
            row['erp_project'],
            invocation_id,
            'submission_fenced',
            'executor',
            {'run_id': run_id, 'fence': row['lease_fence']},
        )
        return True


def finish_execution(
    invocation_id, run_id, lease_token, outcome='execution_finished', reason='', *, qa_verdict=None, db_path=None
):
    if outcome not in {'execution_finished', 'failed', 'uncertain'} or qa_verdict not in {
        None,
        'pass',
        'fail',
        'blocked',
    }:
        raise ValueError('Invalid execution result')
    with _transaction(db_path) as db:
        row = _owned(db, invocation_id, run_id, lease_token, {'running', 'cancel_requested', 'uncertain'})
        if not row:
            return False
        if qa_verdict and row['target_role'] != 'qa':
            raise ValueError('Only independent QA records a QA verdict')
        # A reply after stop is evidence, not cancellation acknowledgment.
        state = 'cancel_requested' if row['state'] == 'cancel_requested' else outcome
        now = int(time.time())
        db.execute(
            'UPDATE governance_invocation SET state=?,reason=?,qa_verdict=?,updated_at=? WHERE invocation_id=?',
            (state, str(reason)[:500], qa_verdict, now, invocation_id),
        )
        if state == 'failed':
            _release(db, row)
        _event(
            db,
            row['erp_project'],
            invocation_id,
            'execution_' + state,
            'executor',
            {'run_id': run_id, 'qa_verdict': qa_verdict},
        )
        return True


def _cancel(db, row, actor):
    state = 'cancelled' if row['state'] in {'queued', 'execution_finished', 'awaiting_approval'} else 'cancel_requested'
    db.execute(
        'UPDATE governance_invocation SET state=?,reason=?,updated_at=? WHERE invocation_id=?',
        (state, 'ERP cancellation requested', int(time.time()), row['invocation_id']),
    )
    if state == 'cancelled':
        _release(db, row)
    _event(db, row['erp_project'], row['invocation_id'], state, actor, {'fence': row['lease_fence']})


def request_cancel(invocation_id, actor, db_path=None):
    _actor(actor)
    with _transaction(db_path) as db:
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not row:
            raise ValueError('Invocation not found')
        if row['state'] in ACTIVE and row['state'] != 'cancel_requested':
            _cancel(db, row, actor)
        return _public(
            db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        )


def confirm_cancel(invocation_id, run_id, lease_token, receipt_id, db_path=None):
    if not isinstance(receipt_id, str) or not receipt_id.strip() or len(receipt_id) > 190:
        raise ValueError('Verified upstream cancellation receipt required')
    with _transaction(db_path) as db:
        row = _owned(db, invocation_id, run_id, lease_token, {'cancel_requested'})
        if not row:
            return False
        db.execute(
            "UPDATE governance_invocation SET state='cancelled',updated_at=? WHERE invocation_id=?",
            (int(time.time()), invocation_id),
        )
        _release(db, row)
        _event(db, row['erp_project'], invocation_id, 'cancellation_confirmed', 'executor', {'receipt_id': receipt_id})
        return True


def defer_unsubmitted(invocation_id, run_id, lease_token, *, proof, reason='', db_path=None):
    if proof not in {'not_called', 'relay_busy_before_submission', 'rejected_before_submission'}:
        raise ValueError('Only confirmed pre-submission rejection permits retry')
    with _transaction(db_path) as db:
        row = _owned(db, invocation_id, run_id, lease_token, {'running'})
        if not row or (row['submitted_at'] is not None and proof == 'not_called'):
            return False
        state = 'blocked' if row['dispatch_attempts'] >= MAX_DISPATCH_ATTEMPTS else 'queued'
        db.execute(
            'UPDATE governance_invocation SET state=?,submitted_at=NULL,lease_token=NULL,lease_expires_at=NULL,reason=?,updated_at=? WHERE invocation_id=?',
            (state, str(reason)[:500], int(time.time()), invocation_id),
        )
        if state == 'blocked':
            _release(db, row)
        _event(
            db,
            row['erp_project'],
            invocation_id,
            'pre_submission_' + state,
            'executor',
            {'proof': proof, 'attempt': row['dispatch_attempts']},
        )
        return True


def handoff(
    *,
    source_invocation_id,
    source_run_id,
    source_lease_token,
    invocation_id,
    project,
    task,
    from_role,
    target_role,
    task_revision,
    context,
    work_packet,
    gate,
    actor,
    db_path=None,
):
    with _transaction(db_path) as db:
        prior = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        if prior:
            version, digest, _ = _packet(work_packet, project, task, task_revision)
            expected = {
                'source_invocation_id': source_invocation_id,
                'erp_project': project,
                'erp_task': task,
                'from_role': from_role,
                'target_role': target_role,
                'context_hash': work_digest(project, task, target_role, context),
                'task_revision': task_revision,
                'packet_hash': digest,
            }
            if any(prior[k] != v for k, v in expected.items()):
                raise ValueError('Invocation idempotency key already has different content')
            return _public(prior), False
        source = _owned(db, source_invocation_id, source_run_id, source_lease_token, {'execution_finished'})
        if (
            not source
            or source['erp_project'] != project
            or source['erp_task'] != task
            or source['target_role'] != from_role
        ):
            raise ValueError('Handoff requires the exact finished source invocation and task fence')
        _gate(db, project, gate)
        # Free the predecessor only inside this transaction; any exception rolls
        # back both its terminal transition and the successor claim.
        db.execute(
            "UPDATE governance_invocation SET state='handed_off',updated_at=? WHERE invocation_id=?",
            (int(time.time()), source_invocation_id),
        )
        _release(db, source)
        result, created = _enqueue(
            db,
            invocation_id=invocation_id,
            project=project,
            task=task,
            from_role=from_role,
            target_role=target_role,
            task_revision=task_revision,
            context=context,
            work_packet=work_packet,
            actor=actor,
            gate=gate,
        )
        db.execute(
            'UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?',
            (source_invocation_id, invocation_id),
        )
        _event(
            db,
            project,
            source_invocation_id,
            'handoff_committed',
            actor,
            {'successor': invocation_id, 'target_role': target_role},
        )
        return (
            _public(
                db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
            ),
            created,
        )


def verify_completion(
    invocation_id, qa_invocation_id, evidence_digest, erp_readback_digest, actor, *, qa_verdict='pass', db_path=None
):
    _actor(actor)
    if qa_verdict != 'pass' or any(
        not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for v in (evidence_digest, erp_readback_digest)
    ):
        raise ValueError('Explicit passing QA and verified evidence/readback digests are required')
    with _transaction(db_path) as db:
        source = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        qa = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (qa_invocation_id,)).fetchone()
        if (
            not source
            or not qa
            or source['invocation_id'] == qa['invocation_id']
            or source['agent_id'] == qa['agent_id']
            or qa['target_role'] != 'qa'
            or source['target_role'] == 'qa'
            or (source['erp_project'], source['erp_task']) != (qa['erp_project'], qa['erp_task'])
        ):
            raise ValueError('Independent QA must verify the same project/task deliverable')
        users = [
            db.execute('SELECT erp_user FROM governance_agent WHERE agent_id=?', (v['agent_id'],)).fetchone()[0]
            for v in (source, qa)
        ]
        if users[0] == users[1]:
            raise ValueError('An implementer cannot approve their own work')
        verified = db.execute(
            'SELECT * FROM governance_verification WHERE invocation_id=?', (invocation_id,)
        ).fetchone()
        if verified:
            if (verified['qa_invocation_id'], verified['evidence_digest'], verified['erp_readback_digest']) != (
                qa_invocation_id,
                evidence_digest,
                erp_readback_digest,
            ):
                raise ValueError('Completion verification idempotency content changed')
            return False
        if (
            source['state'] not in {'execution_finished', 'handed_off'}
            or qa['state'] != 'execution_finished'
            or qa['qa_verdict'] != 'pass'
        ):
            raise ValueError('Independent QA has not returned a passing verified result')
        source_packet = json.loads(source['work_packet'] or '{}')
        qa_packet = json.loads(qa['work_packet'] or '{}')
        for name in ('input_revision', 'task_input_revision'):
            if name in source_packet and source_packet.get(name) != qa_packet.get(name):
                raise ValueError('Independent QA reviewed different task inputs')
        lease = db.execute(
            'SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
            (qa['erp_project'], qa['erp_task']),
        ).fetchone()
        if not lease or tuple(lease) != (qa_invocation_id, qa['lease_fence']):
            raise ValueError('A newer execution owns this task')
        now = int(time.time())
        db.execute(
            "UPDATE governance_invocation SET state='verified',updated_at=? WHERE invocation_id IN (?,?)",
            (now, invocation_id, qa_invocation_id),
        )
        _release(db, qa)
        db.execute(
            'INSERT INTO governance_verification VALUES(?,?,?,?,?,?)',
            (invocation_id, qa_invocation_id, evidence_digest, erp_readback_digest, actor, now),
        )
        _event(
            db,
            source['erp_project'],
            invocation_id,
            'erp_completion_verified',
            actor,
            {
                'qa_invocation': qa_invocation_id,
                'evidence_digest': evidence_digest,
                'erp_readback_digest': erp_readback_digest,
            },
        )
        return True


def transition(
    invocation_id: str,
    expected: str,
    state: str,
    *,
    run_id: str | None = None,
    reason: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Compatibility for old records; v1 work packets require fenced methods."""
    if state not in {'running', 'completed', 'failed', 'blocked', 'uncertain', 'queued'}:
        raise ValueError('Unsupported invocation state')
    with _transaction(db_path) as db:
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not row or row['state'] != expected:
            return False
        if row['packet_version']:
            raise ValueError('Versioned invocations require fenced lifecycle operations')
        if expected in {'uncertain', 'running'} and state == 'queued':
            raise ValueError('An uncertain submission cannot be replayed')
        db.execute(
            'UPDATE governance_invocation SET state=?,automation_run_id=COALESCE(?,automation_run_id),reason=?,updated_at=? WHERE invocation_id=?',
            (state, run_id, (reason or '')[:500], int(time.time()), invocation_id),
        )
        if state in TERMINAL:
            _release(db, row)
        _event(db, row['erp_project'], invocation_id, 'legacy_' + state, 'legacy', {'execution_only': True})
        return True


def receive_event(event_id, project, payload, *, actor='erp', db_path=None):
    if not PROJECT_ID.fullmatch(project) or not re.fullmatch(r'[A-Za-z0-9_.:@+-]{8,190}', event_id):
        raise ValueError('Invalid ERP inbox event')
    encoded = _json(payload)
    if len(encoded.encode()) > 200_000:
        raise ValueError('ERP event exceeds inbox limit')
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with _transaction(db_path) as db:
        prior = db.execute('SELECT * FROM governance_inbox WHERE event_id=?', (event_id,)).fetchone()
        if prior:
            if prior['erp_project'] != project or prior['payload_hash'] != digest:
                raise ValueError('ERP event ID already has different content')
            return {k: prior[k] for k in prior.keys() if k != 'payload'}, False
        now = int(time.time())
        db.execute(
            "INSERT INTO governance_inbox VALUES(?,?,?,?,'pending',NULL,?,?)",
            (event_id, project, digest, encoded, now, now),
        )
        _event(db, project, None, 'erp_event_received', actor, {'event_id': event_id, 'payload_hash': digest})
        return {
            'event_id': event_id,
            'erp_project': project,
            'payload_hash': digest,
            'state': 'pending',
            'invocation_id': None,
            'received_at': now,
            'updated_at': now,
        }, True


def _ack_event(db, event_id, invocation_id, project):
    event = db.execute('SELECT * FROM governance_inbox WHERE event_id=?', (event_id,)).fetchone()
    if (
        not event
        or event['erp_project'] != project
        or (event['invocation_id'] and event['invocation_id'] != invocation_id)
    ):
        raise ValueError('ERP event does not match this invocation')
    if event['state'] != 'processed':
        db.execute(
            "UPDATE governance_inbox SET state='processed',invocation_id=?,updated_at=? WHERE event_id=?",
            (invocation_id, int(time.time()), event_id),
        )
        _event(db, project, invocation_id, 'erp_event_bound', 'erp', {'event_id': event_id})


def acknowledge_event(event_id, project, invocation_id=None, *, actor='erp', db_path=None):
    """Atomically settle a processed control/event; retries preserve its binding."""
    _actor(actor)
    with _transaction(db_path) as db:
        row = db.execute('SELECT * FROM governance_inbox WHERE event_id=?', (event_id,)).fetchone()
        if not row or row['erp_project'] != project:
            raise ValueError('ERP event does not match this project')
        if row['state'] == 'processed':
            if row['invocation_id'] != invocation_id:
                raise ValueError('ERP event was already bound to another invocation')
            return False
        if invocation_id:
            target = db.execute(
                'SELECT erp_project FROM governance_invocation WHERE invocation_id=?', (invocation_id,)
            ).fetchone()
            if not target or target[0] != project:
                raise ValueError('ERP event invocation does not match project')
        db.execute(
            "UPDATE governance_inbox SET state='processed',invocation_id=?,updated_at=? WHERE event_id=?",
            (invocation_id, int(time.time()), event_id),
        )
        _event(db, project, invocation_id, 'erp_event_acknowledged', actor, {'event_id': event_id})
        return True


def pending_events(limit=50, db_path=None):
    with connect(db_path) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM governance_inbox WHERE state='pending' ORDER BY received_at,event_id LIMIT ?",
                (min(max(1, limit), 100),),
            )
        ]


def recoverable(db_path: Path | None = None) -> dict:
    """Recover never-submitted queued work; hold every possible submission."""
    now = int(time.time())
    stale = 0
    with _transaction(db_path) as db:
        for row in db.execute(
            "SELECT * FROM governance_invocation WHERE state='running' AND ((lease_expires_at IS NOT NULL AND lease_expires_at<?) OR (lease_expires_at IS NULL AND updated_at<?))",
            (now, now - RUN_STALE_SECONDS),
        ).fetchall():
            db.execute(
                "UPDATE governance_invocation SET state='uncertain',reason='Execution lease expired; reconcile original run without replay',updated_at=? WHERE invocation_id=?",
                (now, row['invocation_id']),
            )
            _event(
                db,
                row['erp_project'],
                row['invocation_id'],
                'lease_expired',
                'reconciler',
                {'run_id': row['automation_run_id']},
            )
            stale += 1
        queued = [
            r['invocation_id']
            for r in db.execute(
                "SELECT invocation_id FROM governance_invocation WHERE state='queued' AND submitted_at IS NULL AND dispatch_attempts<? ORDER BY created_at,invocation_id LIMIT 50",
                (MAX_DISPATCH_ATTEMPTS,),
            )
        ]
        return {'stale_marked_uncertain': stale, 'queued_for_reconciliation': queued}

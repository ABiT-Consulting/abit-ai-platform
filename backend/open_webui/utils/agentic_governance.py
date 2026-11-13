"""Durable, project-isolated Agentic Governance execution ledger.

The ERP owns Project/Task and human/AI user identities. ABIT.AI owns agent
instances, invocations and execution history. No live SAP or ERP writes here.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from open_webui.env import DATA_DIR

PROJECT_ID = re.compile(r"^PROJ-[0-9A-Za-z-]{1,60}$")
TASK_ID = re.compile(r"^TASK-[0-9A-Za-z-]{1,60}$")
ROLES = (
    "solution_architect", "project_manager", "developer",
    "qa", "operations", "it_manager",
)
# Only authorized directed handoffs. 'erp' is a server-authenticated origin.
HANDOFFS = {
    "erp": {"solution_architect", "project_manager"},
    "solution_architect": {"project_manager", "it_manager"},
    "project_manager": {"solution_architect", "developer", "qa", "operations", "it_manager"},
    "developer": {"qa", "it_manager", "project_manager"},
    "qa": {"developer", "project_manager"},
    "operations": {"qa", "it_manager", "project_manager"},
    "it_manager": {"developer", "operations", "project_manager"},
}
RECONCILE_SECONDS = 1800
RUN_STALE_SECONDS = 3600


def agent_id(project: str, role: str) -> str:
    if not PROJECT_ID.fullmatch(project) or role not in ROLES:
        raise ValueError("Invalid project or role")
    return str(uuid5(NAMESPACE_URL, f"https://abit.ai/agentic/v1/{project}/{role}"))


def work_digest(project: str, task: str, role: str, context: str) -> str:
    return hashlib.sha256(json.dumps(
        [project, task, role, context], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def path() -> Path:
    return Path(DATA_DIR) / "agentic-governance.sqlite"


@contextmanager
def connect(db_path: Path | None = None):
    target = db_path or path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=15, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS governance_agent(
                agent_id TEXT PRIMARY KEY,
                erp_project TEXT NOT NULL,
                erp_user TEXT NOT NULL,
                role TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(erp_project, role)
            );
            CREATE TABLE IF NOT EXISTS governance_invocation(
                invocation_id TEXT PRIMARY KEY,
                erp_project TEXT NOT NULL,
                erp_task TEXT NOT NULL,
                from_role TEXT NOT NULL,
                target_role TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                context_hash TEXT NOT NULL,
                context TEXT NOT NULL,
                task_revision TEXT NOT NULL,
                state TEXT NOT NULL,
                automation_run_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                reason TEXT,
                FOREIGN KEY(agent_id) REFERENCES governance_agent(agent_id)
            );
            CREATE INDEX IF NOT EXISTS ix_gov_invocation_project ON governance_invocation(erp_project, created_at);
            CREATE INDEX IF NOT EXISTS ix_gov_invocation_state ON governance_invocation(state, updated_at);
        """)
        yield conn
    finally:
        conn.close()


def enroll(project: str, owner: str, bindings: dict[str, str], db_path: Path | None = None) -> list[dict]:
    if not PROJECT_ID.fullmatch(project) or not owner or not bindings or set(bindings) - set(ROLES):
        raise ValueError("Invalid governance project enrollment")
    now = int(time.time())
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for role, erp_user in bindings.items():
                ident = agent_id(project, role)
                if not erp_user or len(erp_user) > 254:
                    raise ValueError("Invalid ERP role user")
                existing = db.execute(
                    "SELECT erp_user, owner_id FROM governance_agent WHERE agent_id=?", (ident,)
                ).fetchone()
                if existing and existing != (erp_user, owner):
                    raise ValueError("Registered agent identity must not be silently redirected")
                db.execute(
                    "INSERT OR IGNORE INTO governance_agent VALUES (?,?,?,?,?,?)",
                    (ident, project, erp_user, role, owner, now),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        return roster(project, db_path)


def roster(project: str, db_path: Path | None = None) -> list[dict]:
    if not PROJECT_ID.fullmatch(project):
        raise ValueError("Invalid ERP project")
    with connect(db_path) as db:
        rows = db.execute(
            "SELECT agent_id, erp_user, role, owner_id FROM governance_agent WHERE erp_project=? ORDER BY role",
            (project,),
        ).fetchall()
        return [dict(agent_id=a, erp_user=e, role=r, owner_id=o) for a,e,r,o in rows]


def enqueue(*, invocation_id: str, project: str, task: str, from_role: str,
            target_role: str, task_revision: str, context: str,
            db_path: Path | None = None) -> tuple[dict, bool]:
    if (not PROJECT_ID.fullmatch(project) or not TASK_ID.fullmatch(task)
        or from_role not in HANDOFFS or target_role not in HANDOFFS[from_role]
        or not task_revision or not context or len(context) > 50_000
        or not re.fullmatch(r"[A-Za-z0-9_.:@+-]{16,160}", invocation_id)):
        raise ValueError("Invalid scoped agent invocation")
    ident = agent_id(project, target_role)
    digest = work_digest(project, task, target_role, context)
    now = int(time.time())
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if not db.execute(
                "SELECT 1 FROM governance_agent WHERE agent_id=? AND erp_project=? AND role=?",
                (ident, project, target_role),
            ).fetchone():
                raise ValueError("Target agent is not registered for this ERP project")
            current = db.execute(
                "SELECT invocation_id, erp_project, erp_task, from_role, target_role, agent_id,"
                " context_hash, task_revision, state, automation_run_id, created_at, updated_at, reason"
                " FROM governance_invocation WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
            if current:
                if current[1:8] != (project, task, from_role, target_role, ident, digest, task_revision):
                    raise ValueError("Invocation idempotency key already has different content")
                db.execute("COMMIT")
                return _format_invocation(current), False
            # Exactly one active execution per ERP Task, regardless of specialist role.
            active = db.execute(
                "SELECT invocation_id FROM governance_invocation WHERE erp_project=? AND erp_task=?"
                " AND state IN ('queued','running','uncertain') LIMIT 1",
                (project, task),
            ).fetchone()
            if active:
                raise ValueError("ERP Task already has an active or uncertain execution")
            db.execute(
                "INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (invocation_id, project, task, from_role, target_role, ident, digest,
                 context, task_revision, "queued", None, now, now, None),
            )
            db.execute("COMMIT")
            return find(invocation_id, db_path), True
        except Exception:
            db.execute("ROLLBACK")
            raise


def _format_invocation(r):
    keys = ("invocation_id", "erp_project", "erp_task", "from_role", "target_role", "agent_id",
            "context_hash", "task_revision", "state", "automation_run_id", "created_at", "updated_at", "reason")
    return dict(zip(keys, r))


def find(invocation_id: str, db_path: Path | None = None) -> dict | None:
    with connect(db_path) as db:
        row = db.execute(
            "SELECT invocation_id, erp_project, erp_task, from_role, target_role, agent_id,"
            " context_hash, task_revision, state, automation_run_id, created_at, updated_at, reason"
            " FROM governance_invocation WHERE invocation_id=?", (invocation_id,),
        ).fetchone()
        return _format_invocation(row) if row else None


def transition(invocation_id: str, expected: str, state: str, *,
               run_id: str | None = None, reason: str | None = None, db_path: Path | None = None) -> bool:
    if state not in {"running","completed","failed","blocked","uncertain","queued"}:
        raise ValueError("Unsupported invocation state")
    with connect(db_path) as db:
        result = db.execute(
            "UPDATE governance_invocation SET state=?, automation_run_id=COALESCE(?,automation_run_id),"
            " reason=?, updated_at=? WHERE invocation_id=? AND state=?",
            (state,run_id,(reason or "")[:500],int(time.time()),invocation_id,expected),
        )
        return result.rowcount == 1


def list_invocations(project: str, db_path: Path | None = None, limit: int = 30) -> list[dict]:
    if not PROJECT_ID.fullmatch(project):
        raise ValueError("Invalid project")
    with connect(db_path) as db:
        return [_format_invocation(r) for r in db.execute(
            "SELECT invocation_id, erp_project, erp_task, from_role, target_role, agent_id,"
            " context_hash, task_revision, state, automation_run_id, created_at, updated_at, reason"
            " FROM governance_invocation WHERE erp_project=? ORDER BY created_at DESC LIMIT ?",
            (project,min(max(1,limit),100)),
        )]


def recoverable(db_path: Path | None = None) -> dict:
    """Never resend after a possible model submission. Report uncertain work."""
    now = int(time.time())
    with connect(db_path) as db:
        result = db.execute(
            "UPDATE governance_invocation SET state='uncertain',"
            " reason='Agent run heartbeat expired; manual original-run reconciliation required',"
            " updated_at=? WHERE state='running' AND updated_at<?",
            (now,now-RUN_STALE_SECONDS),
        )
        pending = db.execute(
            "SELECT invocation_id FROM governance_invocation WHERE state='queued' LIMIT 50"
        ).fetchall()
        return {"stale_marked_uncertain":result.rowcount, "queued_for_reconciliation":[r[0] for r in pending]}

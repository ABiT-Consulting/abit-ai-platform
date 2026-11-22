"""Independent integration validation of the exact LinkedIn admission composition.

The candidate is loaded into isolated modules. The real SQLAlchemy models,
SQLite transactions, maintenance admission and receipt logic, scheduler loop,
and owner/permission route checks execute against disposable local databases.
No application startup, live database, browser, relay, provider, or network is used.
Environment integrations (auth identity, timers, calendar, ERP startup, config)
are fixtures. Recurrence is loaded verbatim from its unchanged source dependency.
"""
from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import Context
from datetime import datetime
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from sqlalchemy import JSON, Column, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = Path(os.environ.get(
    "ABIT_ADMISSION_CANDIDATE_ROOT",
    ROOT / "deployment/linkedin-admission-20261122/candidate/open_webui",
)).resolve()
RECURRENCE = Path(os.environ.get(
    "ABIT_ADMISSION_RECURRENCE_SOURCE",
    ROOT / "backend/open_webui/utils/recurrence.py",
)).resolve()
PKG = CANDIDATE.parent.parent


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def exact_functions(path, names, namespace):
    """Compile unchanged AST function nodes, including decorators and checks."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    if {n.name for n in nodes} != set(names):
        raise AssertionError("Required exact candidate functions missing")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


class StopPolling(BaseException):
    pass


class HourClock:
    def __init__(self, polls):
        self.base = time.time_ns()
        self.index = 0
        self.polls = polls

    def time_ns(self):
        return self.base + self.index * 3600 * 1_000_000_000

    def monotonic(self):
        return 1000 + self.index * 3600

    async def sleep(self, seconds):
        self.index += 1
        if self.index >= self.polls:
            raise StopPolling
        await asyncio.sleep(0)


class ExactCompositionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        files = sorted(p for p in CANDIDATE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        manifest = json.loads((PKG / "manifest.json").read_text())
        expected = {x["path"]: x["after_sha256"] for x in manifest["files"]}
        actual = {str(p.relative_to(CANDIDATE)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in files if str(p.relative_to(CANDIDATE)) in expected}
        if actual != expected:
            raise AssertionError("Candidate bytes differ from the release manifest")
        if not RECURRENCE.is_file():
            raise AssertionError("Exact unchanged recurrence dependency is required")
        cls.initial_hashes = actual
        print("CANDIDATE_BINDING " + json.dumps({
            "candidate": str(CANDIDATE), "files": actual,
            "recurrence_source": str(RECURRENCE),
            "recurrence_sha256": hashlib.sha256(RECURRENCE.read_bytes()).hexdigest(),
            "live_mutations": 0, "provider_calls": 0,
        }, sort_keys=True), flush=True)

    @classmethod
    def tearDownClass(cls):
        current = {name: hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest()
                   for name in cls.initial_hashes}
        if current != cls.initial_hashes:
            raise AssertionError("Candidate drifted during integration validation")

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="admission-composition-")
        self.data = Path(self.temp.name).resolve()
        self.webui = self.data / "webui.db"
        self.engine = create_async_engine("sqlite+aiosqlite:///" + str(self.webui))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.Base = declarative_base()
        self.module_patch = patch.dict(sys.modules)
        self.module_patch.start()
        for name in ("open_webui", "open_webui.utils", "open_webui.models", "open_webui.internal"):
            module = ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        env = ModuleType("open_webui.env")
        env.DATA_DIR = self.data
        sys.modules[env.__name__] = env

        @asynccontextmanager
        async def db_context(db=None):
            if db is not None:
                yield db
            else:
                async with self.sessions() as session:
                    yield session
        self.db_context = db_context
        internal = ModuleType("open_webui.internal.db")
        internal.Base = self.Base
        internal.get_async_db_context = db_context
        internal.get_async_db = db_context
        sys.modules[internal.__name__] = internal
        misc = ModuleType("open_webui.utils.misc")
        misc.json_text_variants = lambda value: [value]
        sys.modules[misc.__name__] = misc

        class User(self.Base):
            __tablename__ = "user"
            id = Column(Text, primary_key=True)
            timezone = Column(Text)
        self.User = User
        users = ModuleType("open_webui.models.users")
        users.User = User
        sys.modules[users.__name__] = users

        class Chat(self.Base):
            __tablename__ = "chat"
            id = Column(Text, primary_key=True)
            user_id = Column(Text)
            chat = Column(JSON)
        self.Chat = Chat
        constants = ModuleType("open_webui.constants")
        constants.ERROR_MESSAGES = SimpleNamespace(UNAUTHORIZED="Unauthorized", NOT_FOUND="Not found")
        sys.modules[constants.__name__] = constants
        self.errors = constants.ERROR_MESSAGES

        self.maintenance = load_source("open_webui.utils.agentic_maintenance",
                                       CANDIDATE / "utils/agentic_maintenance.py")
        self.projection = load_source("open_webui.utils.automation_admission",
                                      CANDIDATE / "utils/automation_admission.py")
        self.contract = load_source("open_webui.models.automation_admission",
                                    CANDIDATE / "models/automation_admission.py")
        self.recurrence = load_source("open_webui.utils.recurrence", RECURRENCE)
        self.models = load_source("open_webui.models.automations", CANDIDATE / "models/automations.py")
        async with self.engine.begin() as connection:
            await connection.run_sync(self.Base.metadata.create_all)
        async with self.sessions() as session:
            session.add(User(id="owner", timezone="Asia/Dubai"))
            session.add(User(id="other", timezone="Asia/Dubai"))
            await session.commit()

        self.enabled = True
        self.permissions = True
        case = self
        class Config:
            @staticmethod
            async def get(key, default=None):
                return case.enabled if key == "automations.enable" else False
            @staticmethod
            async def get_many(*keys):
                return {"automations.enable": case.enabled, "user.permissions": {}}
        self.Config = Config

        async def no_held(app):
            return None
        async def no_timers(*args, **kwargs):
            return []
        async def forbidden_timer(*args, **kwargs):
            raise AssertionError("No timer execution expected")
        resume = ModuleType("open_webui.utils.erp_dot_resume")
        resume.start_held_runs = no_held
        sys.modules[resume.__name__] = resume
        timers = ModuleType("open_webui.utils.timers")
        timers.claim_due_timers = no_timers
        timers.execute_due_timer = forbidden_timer
        sys.modules[timers.__name__] = timers

        self.factory_calls = []
        self.local_tasks = []
        async def controlled_executor(app, automation, run_id):
            self.factory_calls.append((automation.id, run_id))
        self.scheduler = ModuleType("open_webui.utils.automations")
        self.scheduler.__dict__.update({
            "_agov_maintenance": self.maintenance,
            "blocked_automation_references": self.projection.blocked_automation_references,
            "denial_status": self.projection.denial_status,
            "AutomationRuns": self.models.AutomationRuns,
            "Automations": self.models.Automations,
            "Config": Config, "get_async_db": db_context,
            "log": logging.getLogger("composition.scheduler"),
            "asyncio": asyncio, "random": random, "time": time,
            "TIMER_POLL_INTERVAL": 1, "SCHEDULER_POLL_INTERVAL": 10,
            "execute_scheduled_automation": controlled_executor,
            "next_run_ns": self.recurrence.next_run_ns,
        })
        sys.modules[self.scheduler.__name__] = self.scheduler
        exact_functions(CANDIDATE / "utils/automations.py",
                        {"_spawn_claimed_automation", "_scheduler_worker_loop"},
                        self.scheduler.__dict__)
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.gate = self.maintenance.path(self.data)
        with self.maintenance._db(self.gate):
            pass

    async def asyncTearDown(self):
        for task in self.local_tasks:
            if not task.done():
                task.cancel()
        if self.local_tasks:
            await asyncio.gather(*self.local_tasks, return_exceptions=True)
        await self.engine.dispose()
        self.module_patch.stop()
        self.temp.cleanup()

    async def seed(self, aid="agent", *, due=None, attempts=0, meta=None, owner="owner"):
        now = time.time_ns()
        async with self.sessions() as session:
            session.add(self.models.Automation(
                id=aid, user_id=owner, name=aid, folder_id="marketing-folder",
                data={"prompt": "Draft only", "model_id": "fixed-model",
                      "rrule": "RRULE:FREQ=HOURLY;BYMINUTE=0", "continue_previous_chat": True,
                      "target": {"type": "chat"}},
                meta=meta or {}, is_active=True, next_run_at=due if due is not None else now - 1,
                last_run_at=None, attempt_limit=0, attempts_used=attempts,
                created_at=now, updated_at=now,
            ))
            await session.commit()

    async def seed_run(self, aid, run_id, *, status="running", chat_id=None):
        async with self.sessions() as session:
            session.add(self.models.AutomationRun(
                id=run_id, automation_id=aid, status=status, chat_id=chat_id,
                created_at=time.time_ns(), lease_until=None,
            ))
            await session.commit()

    async def original(self, aid="agent", run_id="original", *, state="unresolved"):
        with self.maintenance.automation_batch():
            await self.seed_run(aid, run_id)
            lease = self.maintenance.acquire(
                "automation", aid, parent=self.maintenance._BATCH.get(), automation_run_id=run_id)
        await self.models.AutomationRuns.finish_claim(run_id, "error", error="Original outcome uncertain")
        if state == "unresolved":
            lease.unresolved()
        return lease

    def rows(self, table, where="", params=()):
        with sqlite3.connect(self.webui) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM " + table + where, params)]

    def gate_rows(self, table):
        with self.maintenance._db(self.gate, write=False) as db:
            return [dict(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")]

    async def claim(self, *, limit=10):
        return await self.models.Automations.claim_due(
            time.time_ns(), limit=limit,
            excluded_ids=self.projection.blocked_automation_references())

    async def poll(self, number):
        clock = HourClock(number)
        async_stub = SimpleNamespace(create_task=asyncio.create_task, sleep=clock.sleep)
        with patch.object(self.scheduler, "time", clock), patch.object(self.scheduler, "asyncio", async_stub):
            try:
                await self.scheduler._scheduler_worker_loop(self.app)
            except StopPolling:
                pass
        await asyncio.sleep(0)

    async def test_repeated_hourly_polls_keep_original_and_258_attempts(self):
        await self.seed(attempts=258)
        await self.original()
        before = self.rows("automation"), self.rows("automation_run")
        await self.poll(24)
        self.assertEqual((self.rows("automation"), self.rows("automation_run")), before)
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.projection.admission_status("agent")["code"], "original_unresolved")

    async def test_active_original_is_excluded_without_assuming_worker_finished(self):
        await self.seed()
        await self.original(state="active")
        await self.poll(3)
        self.assertEqual(len(self.rows("automation_run")), 1)
        self.assertEqual(self.rows("automation")[0]["attempts_used"], 0)
        self.assertEqual(self.projection.admission_status("agent")["code"], "original_active")
        self.assertEqual(self.factory_calls, [])

    async def test_exclusion_precedes_limit_so_blocked_first_does_not_starve_ready(self):
        now = time.time_ns()
        for i in range(12):
            aid = "blocked-" + str(i)
            await self.seed(aid, due=now - (20 - i) * 3600_000_000_000)
            self.maintenance.acquire("automation", aid).unresolved()
        await self.seed("ready", due=now - 1)
        with self.maintenance.automation_batch():
            batch = await self.claim(limit=1)
        self.assertEqual([a.id for a, _ in batch], ["ready"])
        rows = self.rows("automation")
        self.assertEqual(sum(r["attempts_used"] for r in rows), 1)
        self.assertEqual(len(self.rows("automation_run")), 1)
        self.assertEqual(self.factory_calls, [])

    async def test_closed_gate_repeated_polls_create_no_claim_and_log_once(self):
        await self.seed()
        self.maintenance.control("close", expected_epoch=0, command_id="close", actor="operator")
        before = self.rows("automation")
        with self.assertLogs("composition.scheduler", level="WARNING") as logs:
            await self.poll(12)
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.rows("automation"), before)
        self.assertEqual(self.rows("automation_run"), [])
        self.assertEqual(self.projection.admission_status("agent")["code"], "maintenance_closed")

    async def test_restricted_open_gate_creates_no_generic_claim(self):
        await self.seed()
        self.maintenance.control("close", expected_epoch=0, command_id="close", actor="operator")
        self.maintenance.control("seal", expected_epoch=1, command_id="seal", actor="operator")
        self.maintenance.configure_scope("restrict-project", expected_epoch=2,
            command_id="scope", actor="operator", project_id="PROJ-ONLY")
        self.maintenance.control("open", expected_epoch=2, command_id="reopen", actor="operator")
        await self.poll(3)
        self.assertEqual(self.rows("automation_run"), [])
        self.assertEqual(self.rows("automation")[0]["attempts_used"], 0)
        self.assertEqual(self.projection.admission_status("agent")["code"], "scope_restricted")

    async def test_manual_wins_race_new_scheduler_claim_denied_original_unchanged(self):
        await self.seed()
        hold = asyncio.Event()
        manual_calls = []
        async def manual_factory():
            manual_calls.append(True)
            await hold.wait()
        with self.maintenance.automation_batch():
            auto, run_id = (await self.claim())[0]
            manual_task = Context().run(self.maintenance.spawn_automation, manual_factory, "agent")
            self.local_tasks.append(manual_task)
            await asyncio.sleep(0)
            original = [r for r in self.gate_rows("lease") if r["kind"] == "automation"][0]
            await self.scheduler._spawn_claimed_automation(self.app, auto, run_id)
            self.assertEqual([r for r in self.gate_rows("lease") if r["kind"] == "automation"], [original])
        row = self.rows("automation_run")[0]
        self.assertEqual(row["id"], run_id)
        self.assertEqual(row["status"], "admission_denied")
        self.assertIn("original_active", row["error"])
        self.assertEqual(manual_calls, [True])
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.rows("automation")[0]["attempts_used"], 1)
        await self.poll(3)
        self.assertEqual(len(self.rows("automation_run")), 1)
        self.assertEqual(self.rows("automation")[0]["attempts_used"], 1)
        hold.set()
        await manual_task

    async def test_scheduler_wins_race_manual_factory_never_called(self):
        await self.seed()
        hold = asyncio.Event()
        calls = []
        async def scheduler_factory():
            calls.append("scheduled")
            await hold.wait()
        async def manual_factory():
            calls.append("manual")
        with self.maintenance.automation_batch():
            auto, run_id = (await self.claim())[0]
            task = self.maintenance.spawn_automation(scheduler_factory, auto.id, run_id=run_id)
            self.local_tasks.append(task)
            await asyncio.sleep(0)
            with self.assertRaisesRegex(self.maintenance.MaintenanceDenied, "original admission"):
                Context().run(self.maintenance.spawn_automation, manual_factory, auto.id)
        self.assertEqual(calls, ["scheduled"])
        self.assertEqual(len(self.rows("automation_run")), 1)
        hold.set()
        await task

    async def test_concurrent_manual_and_scheduler_acquires_one_sqlite_owner(self):
        await self.seed()
        await self.seed_run("agent", "scheduled")
        barrier = threading.Barrier(2)
        def contend(scheduled):
            barrier.wait(timeout=5)
            try:
                lease = self.maintenance.acquire("automation", "agent", db_path=self.gate,
                    automation_run_id="scheduled" if scheduled else None)
                return ("admitted", lease.id)
            except self.maintenance.MaintenanceDenied as exc:
                return ("denied", str(exc))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contend, value) for value in (True, False)]
            outcomes = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(x[0] for x in outcomes), ["admitted", "denied"])
        self.assertEqual(len([r for r in self.gate_rows("lease") if r["kind"] == "automation"]), 1)
        self.assertEqual(self.factory_calls, [])

    async def test_exact_run_binding_survives_in_immutable_audit(self):
        await self.seed()
        lease = await self.original()
        before = self.gate_rows("audit")
        result = self.projection.admission_status("agent")
        self.assertEqual(result["original_run_id"], "original")
        self.assertEqual(result["lease_id"], lease.id)
        admitted = [json.loads(r["payload"]) for r in before if r["kind"] == "admitted"]
        self.assertEqual([r["run_id"] for r in admitted if r["kind"] == "automation"], ["original"])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.maintenance._db(self.gate) as db:
                db.execute("UPDATE audit SET payload='{}' WHERE kind='admitted'")
        self.assertEqual(self.gate_rows("audit"), before)

    async def test_missing_wrong_and_terminal_run_bindings_are_rejected(self):
        await self.seed("agent")
        await self.seed("other-agent")
        await self.seed_run("other-agent", "other-run")
        await self.seed_run("agent", "terminal", status="success")
        before = self.gate_rows("audit")
        for run_id in ("missing", "other-run", "terminal"):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(self.maintenance.MaintenanceDenied, "identity differs"):
                    self.maintenance.acquire("automation", "agent", automation_run_id=run_id)
        self.assertEqual(self.gate_rows("audit"), before)
        self.assertEqual(self.gate_rows("lease"), [])

    async def test_original_positive_receipt_settles_then_claims_only_next_occurrence(self):
        await self.seed()
        original = await self.original()
        async with self.sessions() as session:
            await session.execute(update(self.models.AutomationRun).where(
                self.models.AutomationRun.id == "original").values(status="success", chat_id="saved-chat", error=None))
            session.add(self.Chat(id="saved-chat", user_id="owner", chat={"meta": {"automation_id": "agent"}}))
            await session.commit()
        self.assertEqual(await self.claim(), [])
        owned = self.maintenance.continue_owned("automation", "agent")
        self.assertEqual(owned.id, original.id)
        async def reconcile_only():
            proof = self.maintenance.automation_receipt("agent", run_id="original")
            self.assertIsNotNone(proof)
            self.maintenance.settle_reference("automation", "agent", proof)
        await self.maintenance._run(owned, reconcile_only)
        before_original = self.rows("automation_run", " WHERE id=?", ("original",))[0]
        with self.maintenance.automation_batch():
            auto, next_id = (await self.claim())[0]
        self.assertNotEqual(next_id, "original")
        self.assertEqual(self.rows("automation_run", " WHERE id=?", ("original",))[0], before_original)
        self.assertEqual(auto.attempts_used, 1)
        self.assertEqual(len(self.rows("automation_run")), 2)
        self.assertEqual(self.factory_calls, [])
        self.assertEqual([r for r in self.gate_rows("lease") if r["id"] == original.id][0]["state"], "finished")
        self.assertTrue(any(r["kind"] == "original_receipt_settled" for r in self.gate_rows("audit")))

    async def test_unknown_original_has_no_receipt_and_remains_fenced(self):
        await self.seed()
        original = await self.original()
        owned = self.maintenance.continue_owned("automation", "agent")
        async def observe_only():
            self.assertIsNone(self.maintenance.automation_receipt("agent", run_id="original"))
        await self.maintenance._run(owned, observe_only)
        await self.poll(4)
        self.assertEqual(len(self.rows("automation_run")), 1)
        self.assertEqual(self.rows("automation")[0]["attempts_used"], 0)
        self.assertEqual(self.projection.admission_status("agent")["original_run_id"], "original")
        self.assertEqual(self.projection.admission_status("agent")["code"], "original_unresolved")
        self.assertEqual(self.factory_calls, [])

    async def test_transient_original_proof_failures_do_not_strand_later_batch(self):
        for index, failure in enumerate((FileNotFoundError("missing"), PermissionError("denied"),
                                        sqlite3.OperationalError("no such table: automation_run"))):
            with self.subTest(failure=type(failure).__name__):
                first, second = "bad-" + str(index), "ready-" + str(index)
                await self.seed(first)
                await self.seed(second)
                with self.maintenance.automation_batch():
                    batch = await self.claim()
                    selected = [pair for pair in batch if pair[0].id in (first, second)]
                    self.assertEqual(len(selected), 2)
                    ordered = sorted(selected, key=lambda pair: pair[0].id != first)
                    real_reader = self.maintenance._readonly
                    failed = False
                    def fault_once(database):
                        nonlocal failed
                        if not failed and Path(database) == self.webui:
                            failed = True
                            raise failure
                        return real_reader(database)
                    with patch.object(self.maintenance, "_readonly", side_effect=fault_once):
                        for auto, run_id in ordered:
                            await self.scheduler._spawn_claimed_automation(self.app, auto, run_id)
                        await asyncio.sleep(0)
                bad = self.rows("automation_run", " WHERE automation_id=?", (first,))[0]
                self.assertEqual(bad["status"], "admission_denied")
                self.assertFalse(any(aid == first for aid, _ in self.factory_calls))
                self.assertTrue(any(aid == second for aid, _ in self.factory_calls))

    async def api(self):
        case = self
        async def user(request: Request):
            identity = request.headers.get("x-test-user")
            if not identity:
                raise HTTPException(401, "Authentication required")
            return SimpleNamespace(id=identity, role=request.headers.get("x-test-role", "admin"), timezone="Asia/Dubai")
        async def session_dependency():
            async with self.sessions() as session:
                yield session
        async def permission(*args):
            return case.permissions
        namespace = {
            "router": APIRouter(), "Request": Request, "Depends": Depends,
            "AsyncSession": AsyncSession, "get_verified_user": user,
            "get_async_session": session_dependency, "Automations": self.models.Automations,
            "Config": self.Config, "HTTPException": HTTPException, "status": status,
            "ERROR_MESSAGES": self.errors, "has_permission": permission,
            "AutomationAdmissionStatus": self.contract.AutomationAdmissionStatus,
        }
        exact_functions(CANDIDATE / "routers/automations.py",
            {"check_automations_permission", "check_automation_access", "get_automation_admission"}, namespace)
        app = FastAPI()
        app.include_router(namespace["router"], prefix="/api/v1/automations")
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated.test")

    async def test_owned_api_projection_is_read_only_and_links_exact_owned_chat(self):
        await self.seed()
        await self.original()
        async with self.sessions() as session:
            await session.execute(update(self.models.AutomationRun).where(
                self.models.AutomationRun.id == "original").values(chat_id="saved-chat"))
            session.add(self.Chat(id="saved-chat", user_id="owner", chat={"meta": {"automation_id": "agent"}}))
            await session.commit()
        before = self.rows("automation"), self.rows("automation_run"), self.gate_rows("lease"), self.gate_rows("audit")
        async with await self.api() as client:
            response = await client.get("/api/v1/automations/agent/admission", headers={"x-test-user": "owner"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["original_run_id"], "original")
        self.assertEqual(response.json()["original_chat_url"], "/c/saved-chat")
        after = self.rows("automation"), self.rows("automation_run"), self.gate_rows("lease"), self.gate_rows("audit")
        self.assertEqual(after, before)
        self.assertEqual(self.factory_calls, [])

    async def test_api_denies_unauthenticated_other_owner_and_permission(self):
        await self.seed()
        async with await self.api() as client:
            r = await client.get("/api/v1/automations/agent/admission")
            self.assertEqual(r.status_code, 401)
            r = await client.get("/api/v1/automations/agent/admission", headers={"x-test-user": "other"})
            self.assertEqual(r.status_code, 404)
            self.enabled = False
            r = await client.get("/api/v1/automations/agent/admission", headers={"x-test-user": "owner"})
            self.assertEqual(r.status_code, 403)
            self.enabled = True
            self.permissions = False
            r = await client.get("/api/v1/automations/agent/admission",
                headers={"x-test-user": "owner", "x-test-role": "user"})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.rows("automation_run"), [])

    async def test_governed_agent_409_precedes_generic_projection(self):
        await self.seed(meta={"governance_managed": True})
        async with await self.api() as client:
            with patch.object(self.projection, "admission_status",
                              side_effect=AssertionError("Governed agent reached ordinary admission projection")) as helper:
                r = await client.get("/api/v1/automations/agent/admission", headers={"x-test-user": "owner"})
                self.assertEqual(r.status_code, 409, r.text)
                helper.assert_not_called()
                r = await client.get("/api/v1/automations/agent/admission", headers={"x-test-user": "other"})
                self.assertEqual(r.status_code, 404)
                helper.assert_not_called()
        self.assertEqual(self.rows("automation_run"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Queued ChatGPT relay jobs survive browser-lock contention without false sends."""
import ast
import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "open_webui/routers/chatgpt_relay.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
NODE = next(n for n in TREE.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_persist_job_progress")
FUNCTION = compile(ast.Module(body=[NODE], type_ignores=[]), str(SOURCE), "exec")


class TestQueuedRelayHeartbeat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        file = Path(tmp.name) / "jobs.sqlite"

        @contextmanager
        def database():
            db = sqlite3.connect(file)
            try:
                yield db
            finally:
                db.close()

        self.database = database
        with database() as db:
            db.execute("CREATE TABLE jobs(owner TEXT, job_id TEXT, status TEXT, phase TEXT, updated_at INT)")
            db.execute("INSERT INTO jobs VALUES ('u1','j1','queued',NULL,1)")
            db.execute("INSERT INTO jobs VALUES ('u1','j2','completed','completed',2)")
            db.commit()
        ns = {"_db": database, "time": time}
        exec(FUNCTION, ns)
        self.progress = ns["_persist_job_progress"]

    async def test_heartbeat_keeps_queued_job_fresh_without_faking_submission(self):
        await self.progress({"owner": "u1", "job_id": "j1"}, {"type": "heartbeat"})
        with self.database() as db:
            status, phase, timestamp = db.execute(
                "SELECT status,phase,updated_at FROM jobs WHERE job_id='j1'"
            ).fetchone()
        self.assertEqual(status, "queued")
        self.assertEqual(phase, "waiting_for_browser")
        self.assertGreater(timestamp, 1)

    async def test_heartbeat_does_not_modify_completed_job(self):
        await self.progress({"owner": "u1", "job_id": "j2"}, {"type": "heartbeat"})
        with self.database() as db:
            row = db.execute("SELECT status,phase,updated_at FROM jobs WHERE job_id='j2'").fetchone()
        self.assertEqual(row, ("completed", "completed", 2))

    async def test_heartbeat_is_owner_scoped(self):
        await self.progress({"owner": "other", "job_id": "j1"}, {"type": "heartbeat"})
        with self.database() as db:
            row = db.execute("SELECT status,phase,updated_at FROM jobs WHERE job_id='j1'").fetchone()
        self.assertEqual(row, ("queued", None, 1))


if __name__ == "__main__":
    unittest.main()

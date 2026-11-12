"""Linux-only offline contract tests. No live ChatGPT or ERP requests."""
from __future__ import annotations
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "open_webui" / "utils" / "abitdot_failover.py"
spec = importlib.util.spec_from_file_location("abitdot_failover_linux", MODULE)
dot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dot)
URL = "https://chatgpt.com/dots/65f7e516-abc8-4c3f-9ffa-e8afca9cfaed~6ac25fb93f1c8192931d7692e8dc9a0e"


class LedgerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"ledger.sqlite"
        self.owner = "owner"
        self.job = {"owner":"owner","chat_id":"chat",
                    "job_id":"aibit_"+"c"*48,"status":"unknown",
                    "account":"a"*64,"phase":"waiting_for_response"}
        self.context = {"automation_id":"auto","original_prompt":"full "+("prompt "*1000),
                        "folder":{"data":{"system_prompt":"ERP PROJ-0196 only"}},
                        "erp_task":"TASK-2026-05824",
                        "api_token":"DO_NOT_TRANSMIT"}
    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    def append(self,job=None,owner="owner"):
        with self.db() as db:
            return dot.append_event(db,owner,"chat","turn",
                                    self.job if job is None else job,
                                    self.context,"relay_unavailable")
    def test_event_dedupe_by_owner_chat_turn(self):
        a=self.append()
        b=self.append()
        self.assertEqual(a["id"], b["id"])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM abitdot_relay_events").fetchone()[0],1)
    def test_full_prompt_and_erp_context_preserved(self):
        event=self.append()
        message=dot.prompt_for_dot(event)
        self.assertIn("TASK-2026-05824",message)
        self.assertIn("prompt "*30,message)
        self.assertIn("ERP PROJ-0196",message)
        self.assertNotIn("DO_NOT_TRANSMIT",message)
        self.assertIn("[redacted]",message)
    def test_claim_is_atomic(self):
        event=self.append()
        with self.db() as db:
            self.assertTrue(dot.claim_event(db,"owner",event["id"]))
            self.assertFalse(dot.claim_event(db,"owner",event["id"]))
    def test_cross_owner_events_not_shared(self):
        a=self.append()
        b=self.append(owner="different")
        self.assertNotEqual(a["id"],b["id"])
    def test_owner_dot_url_is_strict(self):
        with patch.dict(os.environ,{
            "ABIT_CHATGPT_DOT_URL":URL,
            "ABIT_CHATGPT_DOT_OWNER_ID":"owner",
        }):
            self.assertEqual(dot.dot_url_for("owner"), URL)
            self.assertEqual(dot.dot_url_for("another"), "")
        with patch.dict(os.environ,{
            "ABIT_CHATGPT_DOT_URL": URL+"?evil=yes",
            "ABIT_CHATGPT_DOT_OWNER_ID":"owner",
        }):
            self.assertEqual(dot.dot_url_for("owner"), "")


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"relay.sqlite"
        self.owner="owner"
        self.job={"owner":"owner","chat_id":"chat","job_id":"aibit_"+"d"*48,
                  "status":"unknown","account":"a"*64,"phase":"failed"}
        self.context={"automation_id":"auto","original_prompt":"Implement the task",
                      "origin_message_id":"run_id",
                      "folder":{"data":{"system_prompt":"ERP scoped"}}}
        @contextmanager
        def db():
            con=sqlite3.connect(self.path)
            con.row_factory=sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()
        self.db=db
        with self.db() as dbconn:
            self.event=dot.append_event(dbconn,self.owner,"chat","turn",
                                        self.job,self.context,"project_chat_unavailable")
        module=ModuleType("open_webui.routers.chatgpt_relay")
        module._db=db
        self.stubs={"open_webui.routers.chatgpt_relay":module}
    async def dispatch(self,response=None,error=None,route=True):
        env={"ABIT_CHATGPT_DOT_URL":URL if route else "",
             "ABIT_CHATGPT_DOT_OWNER_ID":"owner"}
        send=AsyncMock(return_value=response or ("accepted","Dot verified and continued"))
        if error: send.side_effect=error
        with patch.dict(sys.modules,self.stubs),patch.dict(os.environ,env),             patch.object(dot,"capture_event",AsyncMock(return_value=self.event)),             patch.object(dot,"_send_to_dot",send):
            outcome=await dot.dispatch(self.owner,"chat","relay_unavailable",account="a"*64)
        return outcome,send
    async def test_success_is_delivered_once(self):
        first,send=await self.dispatch()
        second,again=await self.dispatch()
        self.assertEqual(first["state"],"accepted")
        self.assertEqual(second["state"],"accepted")
        self.assertEqual(second["reply"],first["reply"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(send.await_count,1)
        self.assertEqual(again.await_count,0)
    async def test_definite_pre_submit_unavailable_allows_existing_model(self):
        outcome,_=await self.dispatch(response=("unavailable",None))
        self.assertEqual(outcome["state"],"unavailable")
        self.assertTrue(outcome["safe_to_fallback"])
    async def test_unknown_dot_submission_blocks_replay(self):
        outcome,_=await self.dispatch(error=TimeoutError("outcome uncertain"))
        self.assertEqual(outcome["state"],"unknown")
        self.assertFalse(outcome["safe_to_fallback"])
        second,again=await self.dispatch()
        self.assertEqual(second["state"],"unknown")
        self.assertEqual(again.await_count,0)
    async def test_no_route_falls_back_without_send(self):
        outcome,send=await self.dispatch(route=False)
        self.assertEqual(outcome["state"],"unavailable")
        self.assertEqual(send.await_count,0)
    async def test_active_original_is_not_duplicated(self):
        with self.db() as conn:
            conn.execute("UPDATE abitdot_relay_events SET context_json=? WHERE id=?",
                (__import__("json").dumps({"job":{**self.job,"status":"running"},
                                           "context":self.context}),self.event["id"]))
        self.event=dict(self.event)
        self.event["context_json"]=__import__("json").dumps({
            "job":{**self.job,"status":"running"},"context":self.context})
        result,send=await self.dispatch()
        self.assertEqual(result["state"],"unknown")
        self.assertEqual(send.await_count,0)

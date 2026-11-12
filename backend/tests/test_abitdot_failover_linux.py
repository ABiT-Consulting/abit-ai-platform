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


class HistoricalRelaySelectionTests(unittest.TestCase):
    def test_selects_actual_unknown_turn_before_later_failed_navigation(self):
        """Regression for InterMarket's screenshot: old unknown + newer failed."""
        import ast
        from contextlib import contextmanager
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'open_webui/routers/chatgpt_relay.py').read_text())
        function=next(
            f for f in tree.body
            if isinstance(f,ast.FunctionDef) and f.name=='_latest_unfinished_job'
        )
        with tempfile.TemporaryDirectory() as directory:
            conn=sqlite3.connect(str(Path(directory)/'relay.sqlite'))
            conn.row_factory=sqlite3.Row
            conn.execute(
                'CREATE TABLE jobs(owner TEXT,chat_id TEXT,job_id TEXT,status TEXT,updated_at INT)'
            )
            conn.executemany('INSERT INTO jobs VALUES(?,?,?,?,?)',[
                ('owner','chat','old-unknown','unknown',101),
                ('owner','chat','recent-failed','failed',202),
                ('other','chat','foreign-running','running',300),
            ])
            conn.commit()
            @contextmanager
            def connection():
                yield conn
            namespace={'_db':connection}
            exec(compile(ast.Module(body=[function],type_ignores=[]),
                         '<isolated relay function>','exec'),namespace)
            picked=namespace['_latest_unfinished_job']('owner','chat')
            self.assertEqual(picked['job_id'],'old-unknown')
            self.assertEqual(picked['status'],'unknown')
            self.assertIsNone(namespace['_latest_unfinished_job']('owner','other'))
            conn.close()


class ScreenshotFailureRegressionTests(unittest.IsolatedAsyncioTestCase):
    """The actual InterMarket screenshot had partial UNKNOWN + newer no-send."""

    async def test_partial_unknown_is_not_reported_completed_and_reaches_abitdot(self):
        import ast
        import types
        from unittest.mock import AsyncMock, patch
        import sys
        source=(ROOT/'open_webui/routers/chatgpt_relay.py').read_text()
        tree=ast.parse(source)
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=='_prefer_abitdot')
        dispatcher=types.ModuleType('open_webui.utils.abitdot_failover')
        dispatcher.dispatch=AsyncMock(return_value={'state':'unavailable',
                                                    'safe_to_fallback':True})
        dispatcher.commit_verified_dot_reply=lambda *args: False
        fake={'_job_record':lambda *args: None,
              '_run_background_handoff':AsyncMock()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),
                     '<prefer_abitdot_test>', 'exec'), fake)
        job={'owner':'owner','chat_id':'chat','job_id':'aibit_'+'a'*48,
             'phase':'receiving_response','status':'unknown',
             'response':'A partial ChatGPT answer is NOT a completed turn.'}
        with patch.dict(sys.modules, {'open_webui.utils.abitdot_failover':dispatcher}):
            result=await fake['_prefer_abitdot'](job,'a'*64)
        self.assertEqual(result,'unavailable')
        dispatcher.dispatch.assert_awaited_once()
        self.assertEqual(fake['_run_background_handoff'].await_count,0)

    def test_exact_screenshot_no_send_receipt_is_eligible_for_secondary(self):
        import ast
        source=(ROOT/'open_webui/routers/chatgpt_relay.py').read_text()
        tree=ast.parse(source)
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name=='_is_verified_no_send')
        scope={}
        exec(compile(ast.Module(body=[node],type_ignores=[]),
                     '<no_submit_classifier>', 'exec'),scope)
        classify=scope['_is_verified_no_send']
        self.assertTrue(classify(
            'ChatGPT could not load the selected conversation. No message was submitted; '
            'the conversation needs to be reopened or reconnected before retrying.'
        ))
        self.assertTrue(classify('ChatGPT browser capacity occupied; no submission performed.'))
        self.assertFalse(classify('Submission may have reached ChatGPT. Check the original conversation.'))
        self.assertFalse(classify('Submission acknowledgement uncertain; no resend permitted.'))
        self.assertFalse(classify('The server timed out without a verified response.'))
        self.assertFalse(classify(None))

    def test_verified_dot_answer_can_supersede_partial_unknown_without_replaying(self):
        import types
        import sys
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'jobs.sqlite'
            @contextmanager
            def db():
                conn=sqlite3.connect(path)
                conn.row_factory=sqlite3.Row
                try:
                    yield conn
                    conn.commit()
                finally:
                    conn.close()
            with db() as conn:
                conn.execute('''CREATE TABLE jobs (
                    owner TEXT,job_id TEXT,status TEXT,phase TEXT,
                    fallback_model_id TEXT,primary_error TEXT,error TEXT,
                    response TEXT,updated_at INTEGER)''')
                conn.execute("INSERT INTO jobs(owner,job_id,status,phase,response,error) "
                             "VALUES ('owner','job','unknown','receiving_response',"
                             "'unverified partial','old failure')")
            fake=types.ModuleType('open_webui.routers.chatgpt_relay')
            fake._db=db
            with patch.dict(sys.modules,{'open_webui.routers.chatgpt_relay':fake}):
                self.assertTrue(dot.commit_verified_dot_reply('owner','job',
                                                              'Verified final from abitdot'))
            with db() as conn:
                row=conn.execute('SELECT status,response,fallback_model_id FROM jobs').fetchone()
                self.assertEqual(tuple(row),('completed','Verified final from abitdot','abitdot'))


class PersistedRecoveryRegressionTests(unittest.TestCase):
    def test_recovery_messages_preserve_project_and_original_prompt(self):
        job={'response_message_id':'assistant1','response':'partial output',
             'status':'unknown','phase':'receiving_response'}
        chat={'history':{'messages':{
            'u0':{'id':'u0','parentId':None,'role':'user',
                  'content':'Prior verified task'},
            'a0':{'id':'a0','parentId':'u0','role':'assistant',
                  'content':'Earlier completed work','done':True},
            'u1':{'id':'u1','parentId':'a0','role':'user',
                  'content':'Full original ERPNext task prompt'},
            'assistant1':{'id':'assistant1','parentId':'u1','role':'assistant',
                          'content':'partial output','done':True,'error':{'content':'unverified'}},
        }}}
        messages=dot.recovered_selected_model_messages(chat,job,'Saved project instructions')
        self.assertEqual(messages[-1]['content'],'Full original ERPNext task prompt')
        self.assertEqual(messages[1]['content'],'Saved project instructions')
        self.assertIn('UNVERIFIED partial',messages[2]['content'])
        self.assertTrue(any('side effects' in m['content'] for m in messages if m['role']=='system'))
        unsafe={**chat,'history':{'messages':{**chat['history']['messages'],
                'u1':{**chat['history']['messages']['u1'],'files':[{'id':'image'}]}}}}
        self.assertIsNone(dot.recovered_selected_model_messages(unsafe,job,''))

    def test_stale_unknown_receives_exclusive_original_turn_fallback_claim(self):
        import ast
        from contextlib import contextmanager
        import time as clock
        root=Path(__file__).resolve().parents[1]
        source=(root/'open_webui/routers/chatgpt_relay.py').read_text()
        tree=ast.parse(source)
        wanted={'_prepare_unverified_selected_recovery','claim_postsubmit_handoff',
                'save_postsubmit_fallback_result'}
        selected=[node for node in tree.body if isinstance(node,ast.FunctionDef)
                  and node.name in wanted]
        self.assertEqual(len(selected),3)
        with tempfile.TemporaryDirectory() as tmp:
            filename=Path(tmp)/'jobs.sqlite'
            @contextmanager
            def db():
                conn=sqlite3.connect(filename)
                conn.row_factory=sqlite3.Row
                try:
                    yield conn
                    conn.commit()
                finally:
                    conn.close()
            with db() as connection:
                connection.execute('''CREATE TABLE jobs (
                    owner TEXT,job_id TEXT,status TEXT,phase TEXT,updated_at INTEGER,
                    response TEXT, primary_error TEXT, error TEXT,
                    handoff_claimed_at INTEGER, fallback_model_id TEXT)''')
                connection.execute('''INSERT INTO jobs
                    (owner,job_id,status,phase,updated_at,response,error)
                    VALUES(?,?,?,?,?,?,?)''',
                    ('owner','job','unknown','receiving_response',
                     int(clock.time())-9000,'unverified earlier partial',
                     'original browser observer lost'))
            namespace={'_db':db,'time':clock,'MODEL_ID':
                      'omniroute.abit-chatgpt-relay/project-management'}
            exec(compile(ast.Module(body=selected,type_ignores=[]),
                         '<safely scoped fallback ledger>', 'exec'),namespace)
            with db() as con:
                job=dict(con.execute('SELECT * FROM jobs').fetchone())
            self.assertTrue(namespace['_prepare_unverified_selected_recovery'](job))
            self.assertTrue(namespace['claim_postsubmit_handoff']('owner','job'))
            self.assertFalse(namespace['claim_postsubmit_handoff']('owner','job'))
            self.assertTrue(namespace['save_postsubmit_fallback_result'](
                'owner','job','codex-cli-linux-dev',
                'Verified final from selected model'))
            with db() as con:
                row=con.execute('SELECT status,response,fallback_model_id FROM jobs').fetchone()
                self.assertEqual(tuple(row),
                    ('completed','Verified final from selected model','codex-cli-linux-dev'))

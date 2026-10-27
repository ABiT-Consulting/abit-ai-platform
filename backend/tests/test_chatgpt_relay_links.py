"""Conversation-link regression tests. No live databases, browser or account required."""
import ast
import copy
import sqlite3
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import test_chatgpt_relay_optional_project as fixture
from test_chatgpt_relay_optional_project import ROUTER, PROJECT, ACCOUNT, CONV


class TestConversationLinks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = fixture.TestOptionalProject()
        await self.h.asyncSetUp()
        self.addCleanup(self.h.doCleanups)
        self.ns = self.h.ns
        self.ns.update(Depends=lambda value: None, get_verified_user=lambda: None)
        wanted = {'_job_conversation_url', '_persist_job_progress', 'list_jobs'}
        tree = ast.parse(ROUTER.read_text(encoding='utf-8-sig'))
        nodes = []
        for original in tree.body:
            if isinstance(original, (ast.FunctionDef, ast.AsyncFunctionDef)) and original.name in wanted:
                node = copy.deepcopy(original)
                node.decorator_list = []
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTER), 'exec'), self.ns)
        with self.h.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sync_state(owner TEXT, account TEXT, updated_at INTEGER, count INTEGER)')

    def job(self, job_id='job1', owner='u1', account=ACCOUNT, scope='', chat_id='chat1', url=None):
        with self.h.db() as db:
            db.execute('''INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,
                        status,response_message_id,phase,created_at,updated_at,conversation_url)
                        VALUES(?,?,?,?,?,?,?,? ,?,?,?,?)''',
                       (owner, account, job_id, chat_id, scope, 'hash', 'running',
                        'reply-' + job_id, 'waiting_for_response', int(time.time()), int(time.time()), url))
            db.commit()
        return self.ns['_job_record'](owner, job_id)

    async def progress(self, job, url, account=ACCOUNT, phase='waiting_for_response'):
        await self.ns['_persist_job_progress'](job, {
            'type': 'progress', 'state': phase, 'conversation_url': url, 'account_id': account,
        })
        return self.ns['_job_record'](job['owner'], job['job_id'])

    async def test_link_is_captured_without_any_response_text(self):
        job = self.job()
        current = await self.progress(job, f'https://chatgpt.com/c/{CONV}')
        self.assertEqual(current['conversation_url'], f'https://chatgpt.com/c/{CONV}')
        self.assertIsNone(current['response'])
        self.assertEqual(current['status'], 'running')

    async def test_link_survives_an_unknown_timeout_and_refresh(self):
        job = self.job()
        await self.progress(job, f'https://chatgpt.com/c/{CONV}')
        with self.h.db() as db:
            db.execute("UPDATE jobs SET status='unknown',error='still running' WHERE job_id='job1'")
            db.commit()
        result = await self.ns['list_jobs'](user=SimpleNamespace(id='u1'), chat_id='chat1')
        self.assertEqual(result['jobs'][0]['conversation_url'], f'https://chatgpt.com/c/{CONV}')
        self.assertEqual(result['jobs'][0]['status'], 'unknown')
        self.assertEqual(self.h.catalog_requests, 0)
        self.assertIsNone(self.h.last_payload)

    async def test_link_does_not_switch_to_another_conversation(self):
        job = self.job()
        await self.progress(job, f'https://chatgpt.com/c/{CONV}')
        current = await self.progress(job, 'https://chatgpt.com/c/another-conversation')
        self.assertEqual(current['conversation_url'], f'https://chatgpt.com/c/{CONV}')

    async def test_wrong_account_and_unacknowledged_events_cannot_add_link(self):
        job = self.job()
        for account, phase in [('wrong-account', 'waiting_for_response'), (None, 'waiting_for_response'),
                               (ACCOUNT, 'opening_project')]:
            current = await self.progress(job, f'https://chatgpt.com/c/{CONV}', account, phase)
            self.assertIsNone(current['conversation_url'])

    async def test_scope_mismatch_cannot_add_link(self):
        general = self.job()
        scoped = self.job('scoped', scope=PROJECT)
        self.assertIsNone((await self.progress(general, f'https://chatgpt.com/g/{PROJECT}/c/{CONV}'))['conversation_url'])
        self.assertIsNone((await self.progress(scoped, f'https://chatgpt.com/c/{CONV}'))['conversation_url'])

    async def test_legacy_completed_binding_supplies_link(self):
        self.h.bind(PROJECT)
        job = self.job(scope=PROJECT)
        self.assertEqual(self.ns['_job_conversation_url'](job), f'https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}')

    async def test_other_owners_accounts_chats_and_projects_are_not_fallbacks(self):
        self.h.bind(PROJECT)
        for kw in [dict(owner='u2', scope=PROJECT), dict(account='other', scope=PROJECT),
                   dict(chat_id='different', scope=PROJECT), dict(scope='')]:
            job = self.job(job_id=str(len(kw)) + str(kw), **kw)
            self.assertIsNone(self.ns['_job_conversation_url'](job))

    async def test_no_recorded_link_returns_none_not_the_home_page(self):
        self.assertIsNone(self.ns['_job_conversation_url'](self.job()))

    async def test_current_chat_is_not_limited_to_last_fifteen_global_jobs(self):
        self.job(url=f'https://chatgpt.com/c/{CONV}')
        for number in range(20):
            self.job(job_id='new-' + str(number), chat_id='another-chat')
        result = await self.ns['list_jobs'](user=SimpleNamespace(id='u1'), chat_id='chat1')
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual(result['jobs'][0]['conversation_url'], f'https://chatgpt.com/c/{CONV}')
        self.assertEqual(self.h.catalog_requests, 0)

    async def test_unowned_chat_is_not_readable(self):
        self.job(owner='u2', url=f'https://chatgpt.com/c/{CONV}')
        with self.assertRaises(HTTPException) as error:
            await self.ns['list_jobs'](user=SimpleNamespace(id='u2'), chat_id='chat1')
        self.assertEqual(error.exception.status_code, 404)

    async def test_link_allowlist_and_query_removal(self):
        safe = self.ns['_safe_conversation_url']
        good = f'https://chatgpt.com/c/{CONV}'
        self.assertEqual(safe(good + '?token=not-for-browser#fragment', ''), good)
        self.assertEqual(safe(f'https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}', PROJECT),
                         f'https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}')
        for bad in [None, '', 'javascript:alert(1)', '//chatgpt.com/c/' + CONV,
                    'https://chatgpt.com/', 'https://chatgpt.com.evil.example/c/' + CONV,
                    'https://user:password@chatgpt.com/c/' + CONV,
                    'https://chatgpt.com:444/c/' + CONV, 'https://chatgpt.com//c/' + CONV,
                    good + '\n', 'http://chatgpt.com/c/' + CONV]:
            self.assertIsNone(safe(bad, ''), repr(bad))

    async def test_completed_worker_saves_link(self):
        response = await self.h.send()
        job = self.ns['_job_record_by_id'](response['id'])
        self.assertEqual(job['conversation_url'], f'https://chatgpt.com/c/{CONV}')

    async def test_additive_migration_preserves_legacy_rows_and_is_repeatable(self):
        legacy = Path(self.h.tmp.name) / 'legacy.sqlite'
        with sqlite3.connect(legacy) as db:
            db.execute('''CREATE TABLE jobs(owner TEXT,account TEXT,job_id TEXT PRIMARY KEY,
                          chat_id TEXT,project_id TEXT,message_hash TEXT,status TEXT,
                          response TEXT,error TEXT,created_at INTEGER,updated_at INTEGER)''')
            db.execute("INSERT INTO jobs(job_id,owner,chat_id,response) VALUES('legacy','u1','chat1','keep me')")
        db.close()
        tree = ast.parse(ROUTER.read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_db')
        ns = {'LEDGER': legacy, 'sqlite3': sqlite3, 'contextmanager': contextmanager}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROUTER), 'exec'), ns)
        for _ in range(2):
            with ns['_db']() as db:
                row = db.execute("SELECT response,conversation_url FROM jobs WHERE job_id='legacy'").fetchone()
                self.assertEqual(row['response'], 'keep me')
                self.assertIsNone(row['conversation_url'])

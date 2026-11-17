"""Actual temporary ledger optional unused action schema; no production access."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import unittest

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent
fixture=BACKEND/'tests/test_qa_preflight_retry.py'
spec=importlib.util.spec_from_file_location('qa_preflight_existing_fixture',fixture);original=importlib.util.module_from_spec(spec);sys.modules[spec.name]=original;spec.loader.exec_module(original)

class OptionalActionSchema(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=original.QAPreflightRetry('test_newer_ack_pending_review_one_successor_preserves_original_and_duplicate');self.f.setUp();self.addCleanup(self.f.doCleanups)
        source=BACKEND/'open_webui/utils/agentic_recovery.py'
        name='open_webui.utils.agentic_recovery';spec=importlib.util.spec_from_file_location(name,source);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;setattr(self.f.utils,'agentic_recovery',module);spec.loader.exec_module(module);self.f.recovery=module
    def protected(self):
        with self.f.g.connect(self.f.path)as db:return {t:[dict(r)for r in db.execute('SELECT *FROM '+t+' ORDER BY rowid')]for t in self.f.immutable if t!='governance_action'}
    async def test_absent_unused_action_schema_allows_once_without_creating_it(self):
        f=self.f;before=self.protected()
        with f.g._transaction(f.path)as db:db.execute('DROP TABLE governance_action')
        successor,created=await f.resume();self.assertTrue(created);self.assertEqual(successor['state'],'queued');self.assertEqual(successor['target_role'],'qa');self.assertIsNone(successor['submitted_at']);self.assertEqual(successor['dispatch_attempts'],0);self.assertEqual(self.protected(),before)
        with f.g.connect(f.path)as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name='governance_action'").fetchone());self.assertEqual(db.execute("SELECT count(*)FROM governance_event WHERE kind='qa_preflight_resumed'").fetchone()[0],1)
        self.assertIsNone(await f.resume())
    async def test_present_action_or_missing_required_work_journal_still_denies(self):
        f=self.f
        with f.g._transaction(f.path)as db:db.execute('INSERT INTO governance_action VALUES(?,?,?,?,?,?,?,?,?,?)',('existing-action',f.qa_id,'a'*64,'{}','{}','uncertain',None,None,1,1))
        before=f.history()
        with self.assertRaises(ValueError):await f.resume()
        self.assertEqual(f.history(),before);self.assertEqual(f.g.find(f.qa_id,f.path)['state'],'execution_finished')
        with f.g._transaction(f.path)as db:db.execute('DELETE FROM governance_action');db.execute('DROP TABLE governance_repository_workspace')
        with self.assertRaises(sqlite3.OperationalError):await f.resume()
        with f.g.connect(f.path)as db:self.assertEqual(db.execute('SELECT count(*)FROM governance_invocation').fetchone()[0],2);self.assertEqual(db.execute("SELECT count(*)FROM governance_event WHERE kind='qa_preflight_resumed'").fetchone()[0],0)
if __name__=='__main__':unittest.main()

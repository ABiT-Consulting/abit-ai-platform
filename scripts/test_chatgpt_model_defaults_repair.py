"""Temporary SQLite regression checks for the scoped legacy model repair."""
from __future__ import annotations

import gc
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('model_defaults_repair', ROOT / 'deployment/windows/repair-chatgpt-model-defaults.py')
assert SPEC is not None and SPEC.loader is not None
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)
ACCOUNT = 'a' * 64
PROJECT = 'g-p-fixtureproject123'


class ModelDefaultsRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'webui.db'
        self.ledger = self.root / 'ledger.sqlite'
        self.backups = self.root / 'backups'
        with sqlite3.connect(self.db) as db:
            db.executescript('''
                CREATE TABLE folder(id TEXT PRIMARY KEY,user_id TEXT,name TEXT,
                    meta TEXT,data TEXT,items TEXT,created_at INTEGER,updated_at INTEGER);
                CREATE TABLE chat(id TEXT PRIMARY KEY,content TEXT);
                INSERT INTO chat VALUES('existing-chat','preserve existing conversation');
            ''')
        with sqlite3.connect(self.ledger) as db:
            db.execute('CREATE TABLE projects(owner TEXT,account TEXT,project_id TEXT,folder_id TEXT)')
        self.add('synced', mapped=True)
        self.add('ordinary', mapped=False, meta={})

    def add(self, identifier, *, mapped=True, meta=None, data=None, owner='user-a', ledger_owner=None):
        if meta is None:
            meta = {'chatgpt_project_id': PROJECT + identifier, 'chatgpt_account': ACCOUNT,
                    'background_image_url': 'preserved.png'}
        if data is None:
            data = {'model_ids': ['bit-2.5:7b'], 'system_prompt': 'Keep these instructions',
                    'files': [{'id': 'keep-file'}], 'custom_setting': {'keep': True}}
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO folder VALUES(?,?,?,?,?,?,?,?)',
                       (identifier, owner, 'Original name', json.dumps(meta), json.dumps(data),
                        '["existing-chat"]', 123, 456))
        if mapped:
            with sqlite3.connect(self.ledger) as db:
                db.execute('INSERT INTO projects VALUES(?,?,?,?)',
                           (ledger_owner or owner, ACCOUNT, PROJECT + identifier, identifier))

    def row(self, identifier):
        with sqlite3.connect(self.db) as db:
            return db.execute('SELECT * FROM folder WHERE id=?', (identifier,)).fetchone()

    def test_dry_run_never_writes_or_creates_backups(self):
        before = self.db.read_bytes()
        result = REPAIR.repair(self.db, self.ledger, self.backups)
        self.assertEqual(result, {'affected_projects': 1, 'applied': False})
        self.assertEqual(before, self.db.read_bytes())
        self.assertFalse(self.backups.exists())

    def test_only_model_ids_change_and_backup_contains_original(self):
        synced_before, ordinary_before = self.row('synced'), self.row('ordinary')
        ledger_before = self.ledger.read_bytes()
        result = REPAIR.repair(self.db, self.ledger, self.backups, apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(result['remaining_projects'], 0)
        after = self.row('synced')
        for i in [0, 1, 2, 3, 5, 6, 7]:
            self.assertEqual(after[i], synced_before[i])
        expected = json.loads(synced_before[4]); expected['model_ids'] = [REPAIR.MODEL]
        self.assertEqual(json.loads(after[4]), expected)
        self.assertEqual(self.row('ordinary'), ordinary_before)
        self.assertEqual(self.ledger.read_bytes(), ledger_before)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT content FROM chat').fetchone()[0], 'preserve existing conversation')
        with sqlite3.connect(Path(result['backup']) / 'webui.db') as backup:
            self.assertEqual(backup.execute("SELECT * FROM folder WHERE id='synced'").fetchone(), synced_before)

    def test_repeat_apply_is_a_noop(self):
        REPAIR.repair(self.db, self.ledger, self.backups, apply=True)
        before = self.db.read_bytes()
        second = REPAIR.repair(self.db, self.ledger, self.backups, apply=True)
        self.assertEqual(second, {'affected_projects': 0, 'applied': False})
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_unverified_account_owner_or_project_is_not_repaired(self):
        self.add('wrong-account', meta={'chatgpt_project_id': PROJECT + 'wrong-account', 'chatgpt_account': 'b' * 64})
        self.add('wrong-owner', ledger_owner='other-owner')
        self.add('wrong-project', meta={'chatgpt_project_id': 'g-p-different123', 'chatgpt_account': ACCOUNT})
        self.add('unmapped', mapped=False)
        self.assertEqual([r['id'] for r in REPAIR.plan_repairs(self.db, self.ledger)], ['synced'])

    def test_concurrent_change_rolls_back_all_repairs(self):
        self.add('second')
        updates = sorted(REPAIR.plan_repairs(self.db, self.ledger), key=lambda r: r['id'])
        original = self.row(updates[0]['id'])
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE folder SET data=? WHERE id=?', ('{"new_setting":true}', updates[-1]['id']))
        with self.assertRaises(RuntimeError):
            REPAIR.apply_updates(self.db, updates)
        self.assertEqual(self.row(updates[0]['id']), original)

    def test_corrupt_metadata_fails_before_any_updates(self):
        before = self.row('synced')
        self.add('corrupt')
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE folder SET data='broken-json' WHERE id='corrupt'")
        with self.assertRaises((RuntimeError, ValueError)):
            REPAIR.repair(self.db, self.ledger, self.backups, apply=True)
        self.assertEqual(self.row('synced'), before)
        self.assertFalse(self.backups.exists())

    def test_missing_files_are_not_silently_created(self):
        missing = self.root / 'missing.db'
        with self.assertRaises(RuntimeError):
            REPAIR.plan_repairs(missing, self.ledger)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()

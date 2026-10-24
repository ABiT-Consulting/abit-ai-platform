"""Real router functions with isolated storage/providers; never touches live data."""
import ast
import asyncio
import re
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, HTTPException
from pydantic import BaseModel, SecretStr

SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/routers/chatgpt_relay.py'
NAMES = {'_db', '_status', '_verify_account', '_import_project_catalog', 'sync_projects',
         'ProjectCreateForm', 'RelinkInput', 'create_project', 'enforce_project_model',
         'start_account_relink', 'input_account_relink', 'account_status'}
tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
nodes = []
for node in tree.body:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
        if node.name != '_db':
            node.decorator_list = []
        nodes.append(node)
compiled = compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])), str(SOURCE), 'exec')
ACCOUNT = 'a' * 64
OTHER_ACCOUNT = 'b' * 64
PROJECT = 'g-p-aibittest1234567'
MODEL = 'omniroute.abit-chatgpt-relay/project-management'
REQUEST_ID = 'e4fc2310-4f28-4d88-a1c2-456789abcdef'
ADMIN = SimpleNamespace(id='test-admin', role='admin')

class FolderForm(BaseModel):
    name: str | None = None
    meta: dict | None = None
    data: dict | None = None

class ProjectManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder_rows = {}
        self.calls = []
        self.catalog = []
        self.saved_folder = None
        self.account = ACCOUNT
        self.response = {'ok': True, 'account_id': ACCOUNT,
                         'project': {'id': PROJECT, 'name': 'New Project'}, 'instructions_saved': True}
        async def authorize(user):
            if user.role != 'admin': raise HTTPException(403, 'Forbidden')
        async def context(*args, **kwargs): return 'fake-test-token', self.account, False
        async def relay(method, route, token, payload=None, timeout=30):
            self.calls.append((method, route, payload))
            if route == '/projects': return {'ok': True, 'complete': True, 'account_id': self.account, 'projects': self.catalog}
            if route == '/projects/create-synced':
                if isinstance(self.response, Exception): raise self.response
                return self.response
            if route == '/account/relink/status': return {'connected': True, 'linking': False, 'account_hint': self.account[-10:]}
            return {'ok': True}
        async def get_id(fid, *args, **kwargs): return self.folder_rows.get(fid)
        async def get_name(parent, owner, name, *args, **kwargs):
            return next((f for f in self.folder_rows.values() if f.name == name and f.user_id == owner), None)
        async def insert(owner, form):
            folder = SimpleNamespace(id='folder-'+str(len(self.folder_rows)+1), user_id=owner,
                                     name=form.name, meta=form.meta or {}, data=form.data or {})
            self.folder_rows[folder.id] = folder
            return folder
        async def update(fid, owner, form):
            folder = self.folder_rows[fid]
            if form.name is not None: folder.name = form.name
            if form.meta is not None: folder.meta = {**folder.meta, **form.meta}
            if form.data is not None: folder.data = {**folder.data, **form.data}
            return folder
        async def is_owner(chat, owner): return chat == 'saved-chat' and owner == ADMIN.id
        async def get_chat_folder(chat, owner): return self.saved_folder
        self.ns = {'__builtins__': __builtins__, 'BaseModel': BaseModel, 'SecretStr': SecretStr,
                   'Depends': Depends, 'get_verified_user': lambda: None,
                   'HTTPException': HTTPException, 'contextmanager': contextmanager,
                   'sqlite3': sqlite3, 'time': time, 're': re, 'asyncio': asyncio,
                   'LEDGER': Path(self.tmp.name)/'ledger.sqlite', '_PROJECT_IMPORT_LOCK': asyncio.Lock(),
                   'ACCOUNT_ID': re.compile(r'^[a-f0-9]{64}$'),
                   'PROJECT_ID': re.compile(r'^g-p-[A-Za-z0-9_-]{5,190}$'), 'MODEL_ID': MODEL,
                   '_authorized': authorize, '_account_context': context,
                   '_credentials': lambda: ('fake-test-token', 'fake-fingerprint'), '_relay': relay,
                   'Folders': SimpleNamespace(get_folder_by_id=get_id,
                       get_folder_by_id_and_user_id=get_id,
                       get_folder_by_parent_id_and_user_id_and_name=get_name,
                       insert_new_folder=insert, update_folder_by_id_and_user_id=update),
                   'FolderForm': FolderForm, 'FolderUpdateForm': FolderForm,
                   'Chats': SimpleNamespace(is_chat_owner=is_owner, get_chat_folder_id=get_chat_folder)}
        exec(compiled, self.ns)
        self.ns['RelinkInput'].model_rebuild(_types_namespace=self.ns)

    def form(self, **kwargs):
        return self.ns['ProjectCreateForm'](name=kwargs.pop('name','New Project'),
                                           request_id=kwargs.pop('request_id',REQUEST_ID), **kwargs)
    async def create(self, **kwargs): return await self.ns['create_project'](self.form(**kwargs), ADMIN)
    async def mapped(self):
        await self.ns['_import_project_catalog'](ADMIN, ACCOUNT, {PROJECT:'New Project'})
        return next(iter(self.folder_rows.values()))

    async def test_creation_saves_name_instructions_and_locked_model(self):
        result = await self.create(instructions='  Follow project rules  ')
        self.assertTrue(result['ok'])
        folder = self.folder_rows[result['folder_id']]
        self.assertEqual(folder.data['system_prompt'], 'Follow project rules')
        self.assertEqual(folder.data['model_ids'], [MODEL])
        self.assertEqual(folder.meta['chatgpt_account'], ACCOUNT)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], '/projects/create-synced')
        self.assertEqual(self.calls[0][2]['request_id'], REQUEST_ID)
        self.assertEqual(self.calls[0][2]['account_id'], ACCOUNT)

    async def test_retry_reuses_same_local_folder(self):
        first = await self.create()
        self.response['replayed'] = True
        second = await self.create()
        self.assertEqual(first['folder_id'], second['folder_id'])
        self.assertTrue(second['replayed'])
        self.assertEqual(len(self.folder_rows), 1)

    async def test_invalid_request_never_reaches_browser(self):
        for args in ({'name':' '}, {'name':'x'*121}, {'instructions':'x'*12001},
                     {'request_id':'invalid'}, {'name':'A\nB'}):
            with self.assertRaises(HTTPException) as error: await self.create(**args)
            self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.calls, [])

    async def test_other_account_response_is_not_imported(self):
        self.response['account_id'] = OTHER_ACCOUNT
        with self.assertRaises(HTTPException): await self.create()
        self.assertFalse(self.folder_rows)

    async def test_instruction_failure_is_not_reported_as_success(self):
        self.response['instructions_saved'] = False
        with self.assertRaises(HTTPException): await self.create(instructions='important')
        self.assertFalse(self.folder_rows)

    async def test_duplicate_conflict_passes_back_to_caller(self):
        self.response = HTTPException(409, 'Project already exists')
        with self.assertRaises(HTTPException) as error: await self.create()
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(self.folder_rows)

    async def test_complete_empty_catalog_is_valid_for_new_account(self):
        result = await self.ns['sync_projects'](ADMIN)
        self.assertEqual(result['synced'], 0)
        self.assertEqual(result['count'], 0)

    async def test_sync_repairs_model_without_losing_files_instructions_or_metadata(self):
        folder = await self.mapped()
        folder.data.update(model_ids=['local'], system_prompt='keep', files=['keep-file'])
        folder.meta['background_image_url'] = 'keep-image'
        self.catalog = [{'id':PROJECT, 'name':'New Project'}]
        await self.ns['sync_projects'](ADMIN)
        self.assertEqual(folder.data['model_ids'], [MODEL])
        self.assertEqual(folder.data['system_prompt'], 'keep')
        self.assertEqual(folder.data['files'], ['keep-file'])
        self.assertEqual(folder.meta['background_image_url'], 'keep-image')

    async def test_same_name_different_account_preserves_old_project(self):
        old = await self.mapped()
        old.data['system_prompt'] = 'old-account'
        other_project = 'g-p-newaccount2345678'
        await self.ns['_import_project_catalog'](ADMIN, OTHER_ACCOUNT, {other_project:'New Project'})
        self.assertEqual(len(self.folder_rows), 2)
        self.assertEqual(old.meta['chatgpt_account'], ACCOUNT)
        self.assertEqual(old.data['system_prompt'], 'old-account')
        self.assertEqual(len({f.name for f in self.folder_rows.values()}), 2)

    async def test_saved_chat_model_lock_ignores_spoofed_folder(self):
        folder = await self.mapped()
        self.saved_folder = folder.id
        with self.assertRaises(HTTPException) as error:
            await self.ns['enforce_project_model']({'model':'local', 'chat_id':'saved-chat', 'folder_id':'wrong'}, ADMIN)
        self.assertEqual(error.exception.status_code, 409)

    async def test_new_project_blocks_direct_and_multiple_model_overrides(self):
        folder = await self.mapped()
        for extra in ({'model':'local'}, {'model':MODEL,'model_item':{'direct':True}},
                      {'model':MODEL,'message_ids':[{'model_id':'local','message_id':'reply'}]}):
            with self.assertRaises(HTTPException):
                await self.ns['enforce_project_model']({'folder_id':folder.id, **extra}, ADMIN)
        await self.ns['enforce_project_model']({'model':MODEL,'folder_id':folder.id}, ADMIN)

    async def test_general_chats_keep_normal_model_selection(self):
        await self.ns['enforce_project_model']({'model':'local'}, ADMIN)
        await self.ns['enforce_project_model']({'model':'local','chat_id':'saved-chat'}, ADMIN)

    async def test_non_admin_cannot_create_or_start_account_change(self):
        user = SimpleNamespace(id='regular', role='user')
        with self.assertRaises(HTTPException) as error:
            await self.ns['create_project'](self.form(), user)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException): await self.ns['start_account_relink'](user)
        self.assertEqual(self.calls, [])

    async def test_active_job_blocks_account_change(self):
        with self.ns['_db']() as db:
            db.execute("INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (ADMIN.id,ACCOUNT,'job','chat',PROJECT,'hash','running',int(time.time()),int(time.time())))
            db.commit()
        with self.assertRaises(HTTPException) as error: await self.ns['start_account_relink'](ADMIN)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.calls,[])

    async def test_signin_text_is_secret_in_model_repr_and_scoped_to_admin(self):
        form = self.ns['RelinkInput'](kind='text', text='test-only-sensitive-value')
        self.assertNotIn('test-only-sensitive-value', repr(form))
        await self.ns['input_account_relink'](form,ADMIN)
        self.assertEqual(self.calls[0][2]['owner'],ADMIN.id)
        self.assertEqual(self.calls[0][2]['text'],'test-only-sensitive-value')

if __name__ == '__main__': unittest.main()

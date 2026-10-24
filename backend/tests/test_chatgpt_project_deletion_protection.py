"""Run real folder policy/model/router code against isolated SQLite databases.

Never connects to the live application, relay, or user data.
"""
from __future__ import annotations
import ast
import asyncio
import importlib.util
import logging
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager, closing
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Boolean, Column, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'backend/open_webui'
spec = importlib.util.spec_from_file_location('project_protection', SOURCE/'utils/chatgpt_project_protection.py')
POLICY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(POLICY)


def compile_nodes(nodes, filename):
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    return compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])), str(filename), 'exec')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='aibit-delete-policy-')
        self.addCleanup(self.directory.cleanup)
        self.ledger = Path(self.directory.name)/'relay.sqlite'

    def test_metadata_and_every_ancestor_are_protected(self):
        rows = [{'id':'root'}, {'id':'mid','parent_id':'root'},
                {'id':'project','parent_id':'mid','meta':{'chatgpt_project_id':'g-p-test123456'}},
                {'id':'child','parent_id':'project'}, {'id':'ordinary'}]
        self.assertEqual(POLICY.protected_project_folder_ids(rows), {'root','mid','project'})

    def test_legacy_ledger_mapping_protects_missing_metadata(self):
        rows = [{'id':'root'}, {'id':'legacy','parent_id':'root','meta':None}]
        self.assertEqual(POLICY.protected_project_folder_ids(rows, {'legacy'}), {'root','legacy'})

    def test_cycles_terminate_and_do_not_lock_unrelated_folders(self):
        rows = [{'id':'a','parent_id':'b'}, {'id':'b','parent_id':'a'}, {'id':'c'}]
        self.assertEqual(POLICY.protected_project_folder_ids(rows, {'a'}), {'a','b'})

    def test_no_mapping_does_not_lock_plain_folders(self):
        self.assertEqual(POLICY.protected_project_folder_ids([{'id':'a','meta':{'icon':'lock'}}]), set())

    def test_missing_ledger_is_not_created(self):
        self.assertEqual(POLICY.linked_project_folder_ids('owner', self.ledger), set())
        self.assertFalse(self.ledger.exists())

    def test_ledger_reads_every_account_for_exact_owner(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            db.execute('CREATE TABLE projects(owner TEXT,account TEXT,project_id TEXT,folder_id TEXT)')
            db.executemany('INSERT INTO projects VALUES(?,?,?,?)', [
                ('owner','account-a','g-p-one','one'), ('owner','account-b','g-p-two','two'),
                ('other','account-a','g-p-three','three'), ('owner','account-a','','general')])
            db.commit()
        before = self.ledger.read_bytes()
        self.assertEqual(POLICY.linked_project_folder_ids('owner', self.ledger), {'one','two'})
        self.assertEqual(self.ledger.read_bytes(), before)
        self.ledger.unlink()  # Handles are closed on Windows as well.

    def test_corrupt_ledger_fails_closed(self):
        self.ledger.write_text('not a database')
        with self.assertRaises(POLICY.ProjectProtectionUnavailableError):
            POLICY.linked_project_folder_ids('owner', self.ledger)

    def test_missing_ledger_schema_fails_closed(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            db.execute('CREATE TABLE unrelated(id TEXT)')
        with self.assertRaises(POLICY.ProjectProtectionUnavailableError):
            POLICY.linked_project_folder_ids('owner', self.ledger)


class ModelAndRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='aibit-delete-model-')
        self.addCleanup(self.directory.cleanup)
        self.ledger = Path(self.directory.name)/'relay.sqlite'
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.addAsyncCleanup(self.engine.dispose)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        @asynccontextmanager
        async def context(existing=None):
            if existing is not None:
                try:
                    yield existing
                except Exception:
                    await existing.rollback()
                    raise
            else:
                async with self.sessions() as session:
                    try:
                        yield session
                    except Exception:
                        await session.rollback()
                        raise
        base = declarative_base()
        ns = {
            '__builtins__':__builtins__, '__name__':'aibit_deletion_fixture', 'asyncio':asyncio, 'Base':base,
            'BaseModel':BaseModel, 'ConfigDict':ConfigDict, 'Optional':Optional,
            'JSON':JSON, 'BigInteger':BigInteger, 'Boolean':Boolean, 'Column':Column,
            'Text':Text, 'select':select, 'AsyncSession':AsyncSession,
            'get_async_db_context':context, 'log':logging.getLogger('isolated-deletion-test'),
            'linked_project_folder_ids':lambda owner: POLICY.linked_project_folder_ids(owner,self.ledger),
            'protected_project_folder_ids':POLICY.protected_project_folder_ids,
            'ProjectDeletionProtectedError':POLICY.ProjectDeletionProtectedError,
            'ProjectProtectionUnavailableError':POLICY.ProjectProtectionUnavailableError,
            'PROJECT_DELETE_MESSAGE':POLICY.PROJECT_DELETE_MESSAGE,
        }
        model_file = SOURCE/'models/folders.py'
        tree = ast.parse(model_file.read_text(encoding='utf-8-sig'))
        classes = {'Folder','FolderModel','FolderMetadataResponse','FolderNameIdResponse'}
        methods = {'get_folder_by_id_and_user_id','get_folder_by_id','get_folders_by_user_id',
                   'get_folders_by_parent_id_and_user_id','get_folder_ids_by_id_and_user_id_in_subtree',
                   'get_delete_protected_folder_ids','delete_folder_by_id_and_user_id'}
        nodes = []
        for node in tree.body:
            if isinstance(node,ast.ClassDef) and node.name in classes:
                nodes.append(node)
            elif isinstance(node,ast.ClassDef) and node.name=='FolderTable':
                node.body=[n for n in node.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in methods]
                nodes.append(node)
        exec(compile_nodes(nodes,model_file),ns)
        for name in ('FolderModel','FolderMetadataResponse','FolderNameIdResponse'):
            ns[name].model_rebuild(_types_namespace=ns)
        self.Folder=ns['Folder']
        self.folders=ns['FolderTable']()
        ns['Folders']=self.folders
        async with self.engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with self.sessions() as db:
            db.add_all([
                self.Folder(id='parent',user_id='owner',name='Container',meta={},created_at=1,updated_at=1),
                self.Folder(id='project',user_id='owner',parent_id='parent',name='Linked',
                            meta={'chatgpt_project_id':'g-p-test123456','chatgpt_account':'old-account'},
                            data={'system_prompt':'preserve','model_ids':['relay']},created_at=1,updated_at=1),
                self.Folder(id='sibling',user_id='owner',parent_id='parent',name='Ordinary sibling',created_at=1,updated_at=1),
                self.Folder(id='child',user_id='owner',parent_id='project',name='Ordinary subfolder',created_at=1,updated_at=1),
                self.Folder(id='plain',user_id='owner',name='Plain folder',created_at=1,updated_at=1),
                self.Folder(id='plain-child',user_id='owner',parent_id='plain',name='Plain child',created_at=1,updated_at=1),
                self.Folder(id='legacy',user_id='owner',name='Legacy project',meta={},created_at=1,updated_at=1),
            ])
            await db.commit()
        with closing(sqlite3.connect(self.ledger)) as db:
            db.execute('CREATE TABLE projects(owner TEXT,account TEXT,project_id TEXT,folder_id TEXT)')
            db.execute('INSERT INTO projects VALUES(?,?,?,?)',('owner','old-account','g-p-legacy123','legacy'))
            db.commit()
        self.chats = SimpleNamespace(count_chats_by_folder_ids_and_user_id=AsyncMock(return_value=0),
            delete_chats_by_user_id_and_folder_id=AsyncMock(), move_chats_by_folder_id=AsyncMock())
        self.grants=SimpleNamespace(revoke_all_access=AsyncMock(),get_grants_by_resource=AsyncMock(return_value=[]))
        self.automations=SimpleNamespace(clear_folder_ids=AsyncMock())
        self.event=AsyncMock()
        ns.update({
            'HTTPException':HTTPException,'Depends':Depends,'Request':Request,'status':status,
            'get_verified_user':lambda: None,'get_async_session':lambda: None,
            'check_folders_permission':AsyncMock(), 'get_folder_unread_counts':AsyncMock(return_value={}),
            'Chats':self.chats, 'AccessGrants':self.grants,'Automations':self.automations,
            'publish_event':self.event,'EVENTS':SimpleNamespace(FOLDER_DELETED='folder.deleted'),
            'ERROR_MESSAGES':SimpleNamespace(NOT_FOUND='Not found',ACCESS_PROHIBITED='Access prohibited',DEFAULT=lambda x:x),
            'has_permission':AsyncMock(return_value=True), '_has_folder_access':AsyncMock(return_value=True),
            'Config':SimpleNamespace(get=AsyncMock(return_value={}),get_many=AsyncMock(return_value={})),
            'Groups':SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
        })
        router_file=SOURCE/'routers/folders.py'
        wanted={'get_delete_protected_ids','get_folders','get_folder_by_id','delete_folder_by_id','FolderResponse'}
        nodes=[]
        for node in ast.parse(router_file.read_text(encoding='utf-8-sig')).body:
            if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in wanted:
                node.decorator_list=[]
                nodes.append(node)
        exec(compile_nodes(nodes,router_file),ns)
        ns['FolderResponse'].model_rebuild(_types_namespace=ns)
        self.ns=ns
        self.owner=SimpleNamespace(id='owner',role='admin')
        self.request=SimpleNamespace()

    async def snapshot(self):
        async with self.sessions() as db:
            rows=(await db.execute(select(self.Folder))).scalars().all()
            return {f.id:(f.name,f.parent_id,f.meta,f.data) for f in rows}

    def no_side_effects(self):
        self.chats.delete_chats_by_user_id_and_folder_id.assert_not_awaited()
        self.chats.move_chats_by_folder_id.assert_not_awaited()
        self.grants.revoke_all_access.assert_not_awaited()
        self.automations.clear_folder_ids.assert_not_awaited()
        self.event.assert_not_awaited()

    async def delete(self,folder='project',role='admin',user_id='owner',contents=True):
        async with self.sessions() as db:
            return await self.ns['delete_folder_by_id'](self.request,folder,contents,
                SimpleNamespace(id=user_id,role=role),db)

    async def assert_blocked(self,**args):
        before=await self.snapshot()
        with self.assertRaises(HTTPException) as raised:
            await self.delete(**args)
        self.assertEqual(raised.exception.status_code,403)
        self.assertEqual(await self.snapshot(),before)
        self.no_side_effects()

    async def test_admin_project_delete_with_contents_is_blocked(self):
        await self.assert_blocked()

    async def test_admin_project_delete_without_contents_is_blocked(self):
        await self.assert_blocked(contents=False)

    async def test_regular_owner_is_blocked(self):
        await self.assert_blocked(role='user')

    async def test_different_admin_cannot_bypass_project_protection(self):
        await self.assert_blocked(user_id='another-admin')

    async def test_different_regular_user_cannot_delete(self):
        await self.assert_blocked(user_id='other',role='user')

    async def test_parent_cascade_cannot_remove_project(self):
        await self.assert_blocked(folder='parent')

    async def test_parent_cascade_without_contents_is_also_blocked(self):
        await self.assert_blocked(folder='parent',contents=False)

    async def test_legacy_project_mapping_blocks_delete(self):
        await self.assert_blocked(folder='legacy')

    async def test_direct_model_delete_is_guarded(self):
        before=await self.snapshot()
        with self.assertRaises(POLICY.ProjectDeletionProtectedError):
            await self.folders.delete_folder_by_id_and_user_id('project','owner')
        self.assertEqual(await self.snapshot(),before)

    async def test_direct_model_parent_delete_is_guarded_before_siblings(self):
        before=await self.snapshot()
        with self.assertRaises(POLICY.ProjectDeletionProtectedError):
            await self.folders.delete_folder_by_id_and_user_id('parent','owner')
        self.assertEqual(await self.snapshot(),before)

    async def test_plain_folder_delete_still_works_without_touching_projects(self):
        before=await self.snapshot()
        self.assertTrue(await self.delete(folder='plain'))
        after=await self.snapshot()
        self.assertEqual(after,{key:value for key,value in before.items() if key not in {'plain','plain-child'}})

    async def test_delete_plain_subfolder_does_not_delete_its_project(self):
        before=await self.snapshot()
        self.assertTrue(await self.delete(folder='child',contents=False))
        after=await self.snapshot()
        self.assertEqual(after,{key:value for key,value in before.items() if key!='child'})
        self.chats.delete_chats_by_user_id_and_folder_id.assert_not_awaited()

    async def test_malformed_ledger_stops_deletion_with_503(self):
        self.ledger.write_text('broken')
        before=await self.snapshot()
        with self.assertRaises(HTTPException) as raised:
            await self.delete(folder='plain')
        self.assertEqual(raised.exception.status_code,503)
        self.assertEqual(await self.snapshot(),before)
        self.no_side_effects()

    async def test_nonexistent_folder_is_404(self):
        with self.assertRaises(HTTPException) as raised:
            await self.delete(folder='absent')
        self.assertEqual(raised.exception.status_code,404)
        self.no_side_effects()

    async def test_sidebar_response_preserves_server_protection_flag(self):
        async with self.sessions() as db:
            rows=await self.ns['get_folders'](self.request,self.owner,db)
        flags={row.id:row.model_dump()['deletion_protected'] for row in rows}
        self.assertTrue(flags['project'])
        self.assertTrue(flags['parent'])
        self.assertTrue(flags['legacy'])
        self.assertFalse(flags['plain'])
        self.assertFalse(flags['child'])

    async def test_detail_response_protects_project_and_parent(self):
        for identifier in ['project','parent','legacy']:
            async with self.sessions() as db:
                row=await self.ns['get_folder_by_id'](self.request,identifier,self.owner,db)
            self.assertTrue(row.deletion_protected)

    async def test_other_admin_detail_retains_protection(self):
        async with self.sessions() as db:
            row=await self.ns['get_folder_by_id'](self.request,'project',SimpleNamespace(id='other',role='admin'),db)
        self.assertTrue(row.deletion_protected)


if __name__=='__main__':
    unittest.main()

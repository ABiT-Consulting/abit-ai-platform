"""Isolated sidebar route: managed status never recurses into Native ERP."""
import ast
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from fastapi import HTTPException

ROUTER=Path(__file__).resolve().parents[1]/'open_webui/routers/erp_task_controls.py'
TASK='TASK-2026-06203'
OWNER='fixture-admin-owner'
AGENT='9802cea6-7c1e-5d64-9a41-bbd88815e372'


class GovernedSidebar(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.path=self.root/'agentic-governance.sqlite'
  self.secret='fixture-control-secret-not-a-real-token';token=self.root/'control-token';token.write_text(self.secret)
  config=self.root/'resolver.json';config.write_text(json.dumps({'owner_email':'owner@example.invalid'}))
  env=patch.dict(os.environ,{'ABIT_ERP_TASK_RESOLUTION_CONFIG':str(config),'ABIT_ERP_CONTROL_TOKEN_FILE':str(token)})
  env.start();self.addCleanup(env.stop)
  self.owner=SimpleNamespace(id=OWNER,role='admin')
  self.users=SimpleNamespace(get_user_by_email=AsyncMock(return_value=self.owner))
  self.agent=SimpleNamespace(id=AGENT,user_id=OWNER,meta={
   'governance_managed':True,'agentic_project':'PROJ-0196','agentic_role':'project_manager',
   'erp_role_user':'ai-projects@abitcons.com'})
  self.db=SimpleNamespace(get=AsyncMock(return_value=self.agent),commit=AsyncMock(),scalar=AsyncMock())
  self.native=AsyncMock(side_effect=AssertionError('Managed sidebar reached ERP Task read or legacy Store'))
  self.response=AsyncMock(return_value={'connected':True,'status':'Not Started'})
  self.ns={'hmac':hmac,'json':json,'os':os,'re':re,'sqlite3':sqlite3,'Path':Path,
   'HTTPException':HTTPException,'DATA_DIR':self.root,'TASK_RE':re.compile(r'^TASK-[A-Za-z0-9-]{1,64}$'),
   'Users':self.users,'Automation':object(),'task_context':self.native,'response_for':self.response}
  tree=ast.parse(ROUTER.read_bytes());selected=[]
  for n in tree.body:
   if isinstance(n,ast.AsyncFunctionDef)and n.name in {'governed_response','control_erp_task_agent'}:
    n.decorator_list=[];n.returns=None
    for arg in [*n.args.posonlyargs,*n.args.args,*n.args.kwonlyargs]:arg.annotation=None
    n.args.defaults=[ast.Constant(None)for _ in n.args.defaults]
    selected.append(n)
  exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),str(ROUTER),'exec'),self.ns)
  self.route=self.ns['control_erp_task_agent']

 def known(self,*,state='execution_finished',outcome='blocked'):
  with sqlite3.connect(self.path)as d:
   d.executescript('''CREATE TABLE governance_invocation(invocation_id TEXT,erp_project TEXT,erp_task TEXT,
     agent_id TEXT,target_role TEXT,state TEXT,automation_run_id TEXT,reason TEXT,created_at INTEGER);
    CREATE TABLE governance_agent(agent_id TEXT,erp_project TEXT,owner_id TEXT,role TEXT,erp_user TEXT);
    CREATE TABLE governance_result(invocation_id TEXT,phase TEXT,result_json TEXT);
    CREATE TABLE governance_task_control(project TEXT,task TEXT,state TEXT);''')
   d.execute('INSERT INTO governance_agent VALUES(?,?,?,?,?)',(AGENT,'PROJ-0196',OWNER,'project_manager','ai-projects@abitcons.com'))
   d.execute('INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?)',
    ('invocation-fixture-current','PROJ-0196',TASK,AGENT,'project_manager',state,'run-fixture-current','',2))
   d.execute('INSERT INTO governance_result VALUES(?,?,?)',('invocation-fixture-current','published',
    json.dumps({'outcome':outcome,'summary':'Repository registration is unconfigured; DEV acceptance remains blocked.'})))

 async def call(self,action='status',secret=None):
  return await self.route(SimpleNamespace(),TASK,SimpleNamespace(action=action),
                          control_secret=self.secret if secret is None else secret,db=self.db)

 async def test_managed_status_is_actual_published_result_without_erp_or_store(self):
  self.known();before=self.path.read_bytes();r=await self.call()
  self.assertEqual(r['status'],'Blocked');self.assertEqual(r['governed_state'],'execution_finished')
  self.assertEqual(r['active_run_id'],'run-fixture-current');self.assertIn('unconfigured',r['progress'])
  self.assertTrue(r['legacy_controls_disabled']);self.assertFalse(any(r[k]for k in ['can_start','can_stop','can_restart']))
  self.assertEqual(self.path.read_bytes(),before);self.native.assert_not_awaited();self.db.commit.assert_not_awaited()

 async def test_managed_commands_are_409_before_any_erp_or_legacy_mutation(self):
  self.known();before=self.path.read_bytes()
  for action in ['start','restart','stop']:
   with self.assertRaises(HTTPException)as caught:await self.call(action)
   self.assertEqual(caught.exception.status_code,409);self.assertIn('Agentic Governance',caught.exception.detail)
  self.assertEqual(self.path.read_bytes(),before);self.native.assert_not_awaited();self.db.commit.assert_not_awaited()

 async def test_control_authentication_and_role_ownership_remain_required(self):
  self.known()
  with self.assertRaises(HTTPException)as caught:await self.call(secret='wrong')
  self.assertEqual(caught.exception.status_code,401);self.users.get_user_by_email.assert_not_awaited()
  self.agent.user_id='another-owner'
  with self.assertRaises(HTTPException)as caught:await self.call()
  self.assertEqual(caught.exception.status_code,409);self.native.assert_not_awaited()

 async def test_control_record_without_a_run_still_disables_legacy(self):
  self.known()
  with sqlite3.connect(self.path)as d:
   d.execute('DELETE FROM governance_invocation');d.execute('INSERT INTO governance_task_control VALUES(?,?,?)',('PROJ-0196',TASK,'enabled'))
  r=await self.call();self.assertEqual(r['status'],'Not Started');self.assertFalse(r['can_start'])
  self.assertIsNone(r['active_run_id']);self.native.assert_not_awaited()

 async def test_unmanaged_task_retains_existing_route_behavior(self):
  self.native.side_effect=None;self.native.return_value=({'owner_email':'owner@example.invalid'},{'name':TASK},True,None,None,None)
  r=await self.call();self.assertEqual(r,{'connected':True,'status':'Not Started'})
  self.native.assert_awaited_once_with(TASK,self.db);self.response.assert_awaited_once()

 async def test_ambiguous_owner_or_corrupt_ledger_fails_closed(self):
  self.known()
  with sqlite3.connect(self.path)as d:d.execute('UPDATE governance_agent SET owner_id=?',('other-owner',))
  with self.assertRaises(HTTPException)as caught:await self.call()
  self.assertEqual(caught.exception.status_code,503);self.native.assert_not_awaited()
  self.path.write_bytes(b'not a sqlite database')
  with self.assertRaises(HTTPException)as caught:await self.call()
  self.assertEqual(caught.exception.status_code,503);self.native.assert_not_awaited()


if __name__=='__main__':unittest.main()

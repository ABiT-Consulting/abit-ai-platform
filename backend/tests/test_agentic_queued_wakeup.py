"""Actual queued recovery branch plus real atomic claim in temporary ledgers.
No product module startup, ERP/model/network call, or live SQLite connection.
"""
from __future__ import annotations
import ast
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock,Mock,patch

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
ROUTER=HERE/'agentic_governance.py' if(HERE/'agentic_governance.py').is_file()else BACKEND.parent/'docs/evidence/2026-11-15-queued-original-wakeup/agentic_governance.selected.py'
sys.path.insert(0,str(BACKEND/'tests'))
from test_agentic_controls_events import Fixture,P,T,OTHER,SECOND

class QueuedWakeup(Fixture):
 def setUp(self):
  super().setUp();self._asyncioRunner.get_loop().set_debug(False);self.pending=[];self.started=[];self.denied=[]
  self.recovery=AsyncMock(return_value=None);self.publication=AsyncMock(return_value=None);self.advance=AsyncMock();self.rearm=Mock(wraps=self.load('agentic_unsubmitted').rearm)
  names={'agentic_executor':{'reconcile_publication':self.publication},'agentic_recovery':{'recover_original':self.recovery},'agentic_it_execution':{'resume':AsyncMock(return_value=None),'recover_completed':self.recovery},'agentic_unsubmitted':{'rearm':self.rearm}}
  modules={}
  for name,values in names.items():
   mod=types.ModuleType('open_webui.utils.'+name);mod.__dict__.update(values);modules[mod.__name__]=mod
  self.dependencies=patch.dict(sys.modules,modules);self.dependencies.start();self.addCleanup(self.dependencies.stop)
  self.default_path=patch.object(self.g,'path',return_value=self.path);self.default_path.start();self.addCleanup(self.default_path.stop)
  self.bridge_patch=patch.object(self.bridge_module,'ERPBridge',return_value=self.bridge);self.bridge_patch.start();self.addCleanup(self.bridge_patch.stop)
  self.maintenance=importlib.import_module('open_webui.utils.agentic_maintenance')
  async def deliver(app,ident):
   # The real server's atomic claim/admission and submission fence, with only
   # the downstream ERP/provider boundary controlled. Duplicate schedules do
   # not become a second original start.
   try:claimed=self.g.claim_invocation(ident,self.bridge_module.stable_run_id(ident),gate=self.gate_value,db_path=self.path)
   except self.maintenance.MaintenanceDenied as e:self.denied.append(type(e).__name__);return None
   if claimed is not None:
    self.assertTrue(self.g.mark_submitted(ident,claimed['automation_run_id'],claimed['lease_token'],gate=self.gate_value,db_path=self.path));self.started.append((ident,claimed['automation_run_id']))
   return claimed
  self.namespace={'_config':lambda:{},'_gate':self.gate,'_spawn':lambda app,task:self.pending.append(task),'_deliver':deliver,'_advance':self.advance,'log':Mock()}
  raw=ROUTER.read_bytes();tree=ast.parse(raw);node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)and n.name=='_recover_project');exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROUTER),'exec'),self.namespace)
  self.route=self.namespace['_recover_project'];self.app=object()
 async def flush(self):
  tasks,self.pending=self.pending,[]
  if tasks:await asyncio.gather(*tasks)
 def tearDown(self):
  for task in self.pending:task.close()
  super().tearDown()if hasattr(super(),'tearDown')else None
 async def test_explicit_retry_wakes_existing_queued_identity_without_enqueue(self):
  row=self.enqueue();before=[r['invocation_id']for r in self.g.list_invocations(P,self.path)]
  result=await self.route(self.app,P,T,explicit=True);self.assertEqual(result,[row['invocation_id']]);self.assertEqual(len(self.pending),1);await self.flush()
  current=self.g.find(row['invocation_id'],self.path);self.assertEqual(current['state'],'running');self.assertEqual(current['dispatch_attempts'],1);self.assertEqual(self.started,[(row['invocation_id'],self.bridge_module.stable_run_id(row['invocation_id']))]);self.assertEqual([r['invocation_id']for r in self.g.list_invocations(P,self.path)],before);self.rearm.assert_not_called()
 async def test_duplicate_notifications_have_one_real_claim_and_start(self):
  row=self.enqueue();await asyncio.gather(self.route(self.app,P,T,explicit=True),self.route(self.app,P,T,explicit=True));self.assertEqual(len(self.pending),2);await self.flush()
  self.assertEqual(len(self.started),1);self.assertEqual(self.g.find(row['invocation_id'],self.path)['dispatch_attempts'],1);self.assertEqual(len(self.g.list_invocations(P,self.path)),1)
 async def test_running_uncertain_and_previously_submitted_never_resubmit(self):
  row=self.running();await self.route(self.app,P,T,explicit=True);self.assertEqual(self.pending,[])
  self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'uncertain',reason='controlled original response unknown',db_path=self.path)
  await self.route(self.app,P,T,explicit=True);self.recovery.assert_awaited_once();self.assertEqual(self.pending,[])
  with self.g.connect(self.path)as db:db.execute("UPDATE governance_invocation SET state='queued' WHERE invocation_id=?",(row['invocation_id'],))
  await self.route(self.app,P,T,explicit=True);self.assertEqual(self.pending,[]);self.assertEqual(self.g.find(row['invocation_id'],self.path)['dispatch_attempts'],1);self.rearm.assert_not_called()
 async def test_paused_task_project_and_revoked_project_do_not_schedule(self):
  self.enqueue();self.controls.task_control(P,T,'pause',actor='controlled-manager',db_path=self.path);await self.route(self.app,P,T,explicit=True);self.assertEqual(self.pending,[])
  self.controls.task_control(P,T,'resume',actor='controlled-manager',db_path=self.path);self.g.project_control(P,'pause',actor='controlled-manager',db_path=self.path);await self.route(self.app,P,T,explicit=True);self.assertEqual(self.pending,[])
  self.g.project_control(P,'enable',actor='controlled-manager',policy_revision='project-revision-1',db_path=self.path);self.allowed=False;await self.route(self.app,P,T,explicit=True);self.assertEqual(self.pending,[])
 async def test_closed_maintenance_may_schedule_but_atomic_claim_denies_start(self):
  row=self.enqueue();path=self.maintenance.path(self.path.parent)
  with self.maintenance._db(path)as db:db.execute("UPDATE gate SET state='sealed' WHERE id=1")
  await self.route(self.app,P,T,explicit=True);await self.flush();current=self.g.find(row['invocation_id'],self.path)
  self.assertEqual(self.started,[]);self.assertEqual(current['state'],'queued');self.assertIsNone(current['automation_run_id']);self.assertIsNone(current['submitted_at']);self.assertEqual(current['dispatch_attempts'],0)
 async def test_normal_reconciliation_wakes_only_its_queued_task_and_project(self):
  first=self.enqueue();other=self.enqueue('invocation-foreign-0002',SECOND,project=OTHER)
  await self.route(self.app,P,SECOND,explicit=False);self.assertEqual(self.pending,[])
  await self.route(self.app,P,T,explicit=False);await self.flush();self.assertEqual([r[0]for r in self.started],[first['invocation_id']]);foreign=self.g.find(other['invocation_id'],self.path);self.assertEqual(foreign['state'],'queued');self.assertIsNone(foreign['automation_run_id']);self.rearm.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)

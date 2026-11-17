import json,unittest
from copy import deepcopy
import test_agentic_orchestrator as fixture
P,T,GATE=fixture.P,fixture.T,fixture.GATE

class PMBlockers(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.Lifecycle.asyncSetUp
    packet=fixture.Lifecycle.packet
    gate=fixture.Lifecycle.gate
    dispatch=fixture.Lifecycle.dispatch
    finish=fixture.Lifecycle.finish
    finish_existing=fixture.Lifecycle.finish_existing

    def blocked(self,existing=False,**kwargs):
        row,r=(self.finish_existing(**kwargs) if existing else self.finish(outcome='blocked',**kwargs))
        r['status']='Blocked'
        with self.g.connect(self.db) as db:
            old=db.execute('SELECT event_json FROM governance_result WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
            event=json.loads(old[0]);event['type']='run.blocked';event['status']='Blocked'
            db.execute('UPDATE governance_result SET event_json=?,receipt_json=? WHERE invocation_id=?',(json.dumps(event),json.dumps(r),row['invocation_id']))
        self.bridge.erp.tasks[row['erp_task']].update(status='Blocked',custom_agov_active_run=row['automation_run_id'])
        return row,r

    async def advance(self,row,r):
        return await self.o.advance(row['invocation_id'],r,config={'agentic_pm_blocker_triage':True},project_gate=self.gate,dispatch=self.dispatch,db_path=self.db,bridge=self.bridge)

    def defect(self,**kw):
        return {'id':'fix-service','requirement_ids':['REQ-ONE'],'reproduction':'Observe failed service','observed':'Service unavailable','expected':'Service responds with verified evidence',**kw}

    async def review(self):
        row,r=self.blocked(extras={'defects':[self.defect()]})
        await self.advance(row,r)
        return row,self.sent[-1]

    async def test_blocked_result_starts_one_pm_and_no_automatic_developer_child(self):
        row,r=self.blocked(extras={'defects':[self.defect()]})
        await self.advance(row,r);await self.advance(row,r)
        self.assertEqual(1,len(self.sent));self.assertFalse(self.bridge.erp.creates)
        pm=self.g.find(self.sent[0],self.db,include_private=True)
        self.assertEqual('project_manager',pm['target_role'])
        self.assertEqual(row['invocation_id'],pm['source_invocation_id'])
        self.assertIn('source_result',json.loads(pm['context']))

    async def test_pm_routes_operational_repair_to_operations(self):
        source,ident=await self.review()
        pm,r=self.blocked(existing=True,ident=ident,outcome='blocked',extras={'defects':[self.defect(executor_role='operations')]})
        await self.advance(pm,r)
        self.assertEqual(1,len(self.bridge.erp.creates))
        self.assertEqual('operations',self.bridge.erp.creates[0][0]['target_role'])
        target=self.g.find(self.sent[-1],self.db)
        self.assertEqual('operations',target['target_role'])

    async def test_pm_reuses_existing_child_without_new_task_or_run(self):
        source,r=self.blocked(extras={'defects':[self.defect()]})
        children=await self.o._repair_children(source,{'defects':[self.defect()]},self.packet(),self.bridge,self.db,GATE)
        self.o._wait(source,'repair',children,self.packet(),self.db)
        await self.advance(source,r);ident=self.sent[-1];before=len(self.sent)
        pm,r=self.blocked(existing=True,ident=ident,outcome='blocked',extras={'defects':[self.defect(existing_task_id=children[0])]})
        await self.advance(pm,r);await self.advance(pm,r)
        self.assertEqual(1,len(self.bridge.erp.creates));self.assertEqual(before,len(self.sent))

    async def test_unconfirmed_or_cross_parent_reference_is_rejected(self):
        _,ident=await self.review()
        pm,r=self.blocked(existing=True,ident=ident,outcome='blocked',extras={'defects':[self.defect(existing_task_id='TASK-not-confirmed')]})
        with self.assertRaisesRegex(ValueError,'confirmed same-parent'):
            await self.advance(pm,r)
        self.assertFalse(self.bridge.erp.creates)

    async def test_non_triage_pm_cannot_select_executor(self):
        row,r=self.blocked(role='project_manager',extras={'defects':[self.defect(executor_role='operations')]})
        with self.assertRaisesRegex(ValueError,'exact published blocker PM review'):
            await self.advance(row,r)

    async def test_old_blocked_wait_is_backfilled_to_pm_once(self):
        source,r=self.blocked(extras={'defects':[self.defect()]})
        children=await self.o._repair_children(source,{'defects':[self.defect()]},self.packet(),self.bridge,self.db,GATE)
        self.o._wait(source,'repair',children,self.packet(),self.db)
        await self.o.scan_project(P,config={'agentic_pm_blocker_triage':True},project_gate=self.gate,dispatch=self.dispatch,db_path=self.db,bridge=self.bridge)
        self.assertEqual('project_manager',self.g.find(self.sent[-1],self.db)['target_role'])
        self.assertEqual(1,len(self.bridge.erp.creates))

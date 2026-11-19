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

    async def test_blocked_architect_resumes_once_after_both_role_scoped_repairs(self):
        from open_webui.utils import agentic_start_recovery as start

        source, receipt = self.blocked(
            role='solution_architect',
            extras={'summary': 'The repository read and the service both failed.'},
        )
        await self.advance(source, receipt)
        pm_id = self.sent[-1]
        pm = self.g.find(pm_id, self.db, include_private=True)
        self.assertEqual(pm['target_role'], 'project_manager')
        self.assertEqual(json.loads(pm['context'])['source_result']['summary'], 'The repository read and the service both failed.')

        start_event = {'type': 'run.started', 'role': 'project_manager', 'event_id': 'pm-event',
                       'task_id': T, 'run_id': 'pm-run'}
        start_entry = {'event_json': json.dumps(start_event)}
        blocked_start = {'event_id': 'pm-event', 'task_id': T, 'run_id': 'pm-run',
                         'status': 'Blocked', 'modified': 'pm-start', 'durable': True}
        self.assertEqual(start._receipt(start_entry, blocked_start, row=pm, db_path=self.db), blocked_start)
        with self.assertRaisesRegex(ValueError, 'original active run'):
            start._receipt(start_entry, blocked_start, row={**pm, 'source_invocation_id': None}, db_path=self.db)

        code = self.defect(id='code-repair', executor_role='developer')
        ops = self.defect(id='operations-repair', executor_role='operations')
        pm, receipt = self.blocked(existing=True, ident=pm_id, outcome='blocked', extras={'defects': [code, ops]})
        await self.advance(pm, receipt)
        await self.advance(pm, receipt)
        self.assertEqual(len(self.bridge.erp.creates), 2)
        for proposal, task_id in self.bridge.erp.creates:
            self.assertEqual(proposal['parent_task'], T)
            self.assertIn('Observe failed service', proposal['description'])
            self.assertIn('Service unavailable', proposal['description'])
            self.assertEqual(proposal['acceptance_tests'], 'Service responds with verified evidence')
            self.assertEqual(self.bridge.erp.tasks[task_id]['custom_agov_executor_role'], proposal['target_role'])
        repair_runs = {self.g.find(ident, self.db)['target_role']: ident for ident in self.sent[1:]}
        self.assertEqual(set(repair_runs), {'developer', 'operations'})

        for role, repair_run in repair_runs.items():
            child, receipt = self.finish_existing(repair_run)
            await self.advance(child, receipt)
            qa, receipt = self.finish_existing(
                self.sent[-1], 'pass',
                {'implementation_invocation_id': repair_run, 'implementation_agent_id': child['agent_id']},
            )
            await self.advance(qa, receipt)
            self.assertEqual(self.bridge.erp.tasks[T]['status'], 'Blocked')
            reviewed = self.g.find(self.sent[-1], self.db, include_private=True)
            packet = json.loads(reviewed['work_packet'])
            review, receipt = self.finish_existing(
                reviewed['invocation_id'], 'reviewed', {'qa_review_ack': self.b.digest(packet['qa_review'])},
            )
            await self.advance(review, receipt)
            self.assertEqual(self.bridge.erp.tasks[T]['status'], 'Blocked')
            if role == 'developer':
                self.assertEqual(self.g.find(pm_id, self.db)['state'], 'execution_finished')

        continuations = [self.g.find(ident, self.db, include_private=True) for ident in self.sent if ident != pm_id]
        resumed, = [row for row in continuations if row['erp_task'] == T]
        self.assertEqual(resumed['target_role'], 'solution_architect')
        self.assertEqual(resumed['state'], 'queued')
        self.assertEqual(resumed['source_invocation_id'], pm_id)
        self.assertEqual(
            json.loads(resumed['context'])['completed_repair_task_ids'],
            sorted(task_id for _, task_id in self.bridge.erp.creates),
        )
        await self.o.scan_project(
            P, config={'agentic_pm_blocker_triage': True},
            project_gate=self.gate, dispatch=self.dispatch, db_path=self.db, bridge=self.bridge,
        )
        self.assertEqual(self.sent.count(resumed['invocation_id']), 1)

    async def test_automatic_triage_cannot_publish_or_advance_as_ready(self):
        from open_webui.utils.agentic_executor import reconcile_publication
        from unittest.mock import AsyncMock

        _, pm_id = await self.review()
        pm, receipt = self.finish_existing(pm_id)
        saved = self.s.get_result(pm_id, self.db)
        with self.assertRaisesRegex(ValueError, 'Automated blocker triage'):
            self.s.save_local_result(pm_id, pm['automation_run_id'], saved['result'], self.db)

        # A prepared original from the earlier runtime may already exist in the journal.
        with self.g.connect(self.db) as db:
            db.execute("UPDATE governance_result SET phase='pending',receipt_json=NULL WHERE invocation_id=?", (pm_id,))
        self.bridge.erp.tasks[T]['status'] = 'Blocked'
        self.bridge.publish = AsyncMock(return_value=receipt)

        with self.assertRaisesRegex(ValueError, 'Automated blocker triage'):
            await reconcile_publication(pm_id, self.bridge, self.db)
        self.bridge.publish.assert_not_awaited()
        self.assertEqual(self.s.get_result(pm_id, self.db)['phase'], 'pending')

        # An earlier ERP acknowledgment remains immutable, with no release or child fanout.
        self.s.publication_receipt(pm_id, saved['receipt'], self.db)
        with self.assertRaisesRegex(ValueError, 'Automated blocker triage'):
            await self.advance(pm, receipt)
        self.assertEqual(self.bridge.erp.tasks[T]['status'], 'Blocked')
        self.assertEqual(self.sent, [pm_id])
        self.assertFalse(self.bridge.erp.creates)

    async def test_automated_triage_keeps_its_scope_after_a_coordination_checkpoint(self):
        from open_webui.utils.agentic_blocker_review import require_triage_outcome

        _, pm_id = await self.review()
        pm, receipt = self.finish_existing(pm_id, 'continue')
        saved = self.s.get_result(pm_id, self.db)
        checkpoint = {**saved['event'], 'type': 'run.checkpoint', 'status': 'Blocked'}
        receipt['status'] = 'Blocked'
        with self.g.connect(self.db) as db:
            db.execute(
                'UPDATE governance_result SET event_json=?,receipt_json=? WHERE invocation_id=?',
                (json.dumps(checkpoint), json.dumps(receipt), pm_id),
            )
        self.bridge.erp.tasks[T]['status'] = 'Blocked'
        await self.advance(pm, receipt)
        next_pm = self.g.find(self.sent[-1], self.db, include_private=True)
        self.assertEqual(next_pm['source_invocation_id'], pm_id)
        self.assertFalse(self.bridge.erp.creates)
        with self.assertRaisesRegex(ValueError, 'Automated blocker triage'):
            require_triage_outcome(next_pm, {'outcome': 'ready_for_review'}, self.db, event={'type': 'run.ready'})
        with self.assertRaisesRegex(ValueError, 'Automated blocker triage'):
            require_triage_outcome(
                next_pm, {'outcome': 'continue', 'defects': [self.defect()]}, self.db, event={'type': 'run.checkpoint'},
            )
        self.assertEqual(self.bridge.erp.tasks[T]['status'], 'Blocked')

    async def test_repair_cannot_be_marked_complete_by_removing_managed_ownership(self):
        _, pm_id = await self.review()
        pm, receipt = self.blocked(existing=True, ident=pm_id, outcome='blocked', extras={'defects': [self.defect()]})
        await self.advance(pm, receipt)
        _, repair = self.bridge.erp.creates[0]
        self.bridge.erp.tasks[repair].update(status='Completed', custom_agentic_managed=0)
        with self.assertRaisesRegex(ValueError, 'scope or owner changed'):
            await self.advance(pm, receipt)
        self.assertEqual(self.bridge.erp.tasks[T]['status'], 'Blocked')
        self.assertEqual(self.g.find(pm_id, self.db)['state'], 'execution_finished')

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

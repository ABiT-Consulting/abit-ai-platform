"""Real SQLite retirement fences; only signature verification and native IO are controlled.

No application startup, provider submission, network or production SQLite access.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_agentic_controls_events as fixture

P,T,SECOND=fixture.P,fixture.T,fixture.SECOND


class ExternalCompletion(fixture.Fixture):
    def setUp(self):
        super().setUp()
        self.primary=self.load('abitdot_primary')
        self.scope=self.load('agentic_relay_scope')
        self.gateway=self.load('agentic_gateway_sessions')
        self.maintenance=self.load('agentic_maintenance')
        self.module=self.load('agentic_external_completion')
        ident='original-external-completion-0001'
        self.enqueue(ident)
        self.run_id=self.bridge_module.stable_run_id(ident)
        claimed=self.g.claim_invocation(ident,self.run_id,gate=self.gate_value,db_path=self.path)
        self.g.mark_submitted(ident,self.run_id,claimed['lease_token'],gate=self.gate_value,db_path=self.path)
        self.g.finish_execution(ident,self.run_id,claimed['lease_token'],'uncertain',db_path=self.path)
        self.row=self.g.find(ident,self.path,include_private=True)
        self.packet_value=json.loads(self.row['work_packet'])
        self.automation=types.SimpleNamespace(id=self.row['agent_id'],user_id='owner-fixture',
            meta={'agentic_project':P,'agentic_role':'developer','erp_role_user':self.users['developer'],
                  'governance_managed':True})
        self.provider_path=Path(self.temp.name)/'abitdot-primary.sqlite'
        self.key=self.primary.dot_primary_key(self.automation.user_id,self.automation.id,self.run_id)
        self.dot_id='dot-fixture-000001'
        scope=self.scope.scope_for_packet(self.row,self.packet_value,self.automation.user_id,
                                          'a'*64,self.dot_id,self.run_id)
        envelope={'owner_id':self.automation.user_id,'automation_id':self.automation.id,
                  'run_id':self.run_id,'dot_id':self.dot_id,'idempotency_key':self.key,
                  'governance_scope':{'scope':scope},
                  'task':{'meta':{'governance_managed':True,'erp_task':T,'erp_role':'developer'},
                          'original_prompt':'Original scope, preserved as evidence'}}
        with self.primary.ledger(self.provider_path) as db:
            db.execute("""INSERT INTO dot_primary_runs
                (id,owner,automation_id,original_run_id,dot_id,status,envelope_json,receipt,
                 response_text,conversation_url,reason,created_at,updated_at,scope_receipt_json)
                VALUES(?,?,?,?,?,'delegated',?,?,?,?,NULL,1,2,?)""",
                (self.key,self.automation.user_id,self.automation.id,self.run_id,self.dot_id,
                 json.dumps(envelope),'accepted-original-receipt','Original terminal reply',
                 'https://chatgpt.com/dots/'+self.dot_id,json.dumps({'fixture_signature_boundary':True})))
        with self.gateway.connect(self.path) as db:
            db.execute("INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)",
                ('fixture-terminal-session',ident,json.dumps(scope),'{}','connection-hash',None,'active',1))
            for index in range(2):
                db.execute("INSERT INTO governance_gateway_call VALUES(?,?,?,?,?,?,?,?)",
                    ('fixture-terminal-session','call-original-'+str(index),'c'*64,'context.read',
                     'completed',json.dumps({'read_only':True}),1,2))
        self.task={'name':T,'project':P,'status':'Completed','modified':'2026-11-16 13:51:50',
                   'modified_by':'mimran@abitcons.com','_assign':'[]',
                   'custom_agentic_managed':1,'custom_agov_active_run':self.run_id,
                   'custom_agov_executor_role':'developer'}
        self.reads=[]
        async def get(doctype,name):
            self.assertEqual((doctype,name),('Task',T));self.reads.append(deepcopy(self.task))
            return deepcopy(self.task)
        self.erp=types.SimpleNamespace(get=AsyncMock(side_effect=get),
                                       request=AsyncMock(side_effect=AssertionError('No ERP mutation API permitted')))
        self.native=types.SimpleNamespace(erp=self.erp,
                     packet=AsyncMock(side_effect=AssertionError('Revoked ownership cannot read a fresh WorkPacket')))
        self.closed=[]
        async def close_outer(row,reason):
            proof={'run_id':row['automation_run_id'],'automation_id':row['agent_id'],
                   'status':'cancelled','reason_sha256':hashlib.sha256(reason.encode()).hexdigest()}
            self.closed.append(proof);return proof
        self.close_outer=AsyncMock(side_effect=close_outer)
        self.verifier=patch.object(self.primary,'verify_saved_scope_receipt',return_value=None)
        self.verified=self.verifier.start();self.addCleanup(self.verifier.stop)

    async def retire(self):
        return await self.module.retire(self.row,self.automation,self.native,
                                        db_path=self.path,close_outer=self.close_outer)

    def provider_update(self,field,value):
        self.assertIn(field,{'status','original_run_id','envelope_json','response_text','scope_receipt_json'})
        with self.primary.ledger(self.provider_path) as db:
            db.execute('UPDATE dot_primary_runs SET '+field+'=? WHERE id=?',(value,self.key))

    def current(self):
        return self.g.find(self.row['invocation_id'],self.path,include_private=True)

    def assert_held(self):
        self.assertEqual(self.current()['state'],'uncertain')
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT invocation_id FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
                                        (P,T)).fetchone()[0],self.row['invocation_id'])
        self.close_outer.assert_not_awaited()
        self.native.packet.assert_not_awaited();self.erp.request.assert_not_awaited()

    async def denied(self):
        try:await self.retire()
        except (ValueError,RuntimeError):pass
        self.assert_held()

    async def test_human_completion_retires_only_technical_run_without_qa_or_native_writes(self):
        task_before=deepcopy(self.task)
        with self.primary.ledger(self.provider_path) as db:
            provider_before=dict(db.execute('SELECT * FROM dot_primary_runs WHERE id=?',(self.key,)).fetchone())
        await self.retire()
        retired=self.current()
        self.assertEqual(retired['state'],'cancelled');self.assertIsNone(retired['qa_verdict'])
        self.assertEqual(retired['automation_run_id'],self.run_id)
        with self.g.connect(self.path) as db:
            self.assertIsNone(db.execute('SELECT invocation_id FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
                                        (P,T)).fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM governance_verification').fetchone()[0],0)
        self.assertEqual(self.task,task_before)
        self.native.packet.assert_not_awaited();self.erp.request.assert_not_awaited()
        self.verified.assert_called();self.close_outer.assert_awaited_once()
        with self.primary.ledger(self.provider_path) as db:
            provider_after=dict(db.execute('SELECT * FROM dot_primary_runs WHERE id=?',(self.key,)).fetchone())
        self.assertEqual(provider_before,provider_after)
        self.assertFalse(self.maintenance.has_lease('governed',self.row['invocation_id'],
                           db_path=self.maintenance.path(self.path.parent)))

    async def test_human_cancelled_is_retired_without_changing_native_status(self):
        self.task['status']='Cancelled'
        await self.retire()
        self.assertEqual(self.current()['state'],'cancelled')
        self.assertEqual(self.task['status'],'Cancelled')

    async def test_active_native_task_cannot_be_retired(self):
        self.task['status']='Working'
        await self.denied()

    async def test_ai_authored_completion_cannot_be_retired_as_human(self):
        self.task['modified_by']=self.users['project_manager']
        await self.denied()

    async def test_changed_native_project_does_not_release_original_fence(self):
        self.task['project']='PROJ-foreign'
        await self.denied()

    async def test_changed_provider_run_and_active_provider_state_remain_held(self):
        self.provider_update('original_run_id','foreign-run')
        await self.denied()
        self.provider_update('original_run_id',self.run_id)
        self.provider_update('status','unknown')
        await self.denied()

    async def test_scope_mismatch_denies_even_if_signature_boundary_is_stubbed(self):
        with self.primary.ledger(self.provider_path) as db:
            envelope=json.loads(db.execute('SELECT envelope_json FROM dot_primary_runs WHERE id=?',
                                          (self.key,)).fetchone()[0])
        envelope['governance_scope']['scope']['task_id']='TASK-foreign'
        self.provider_update('envelope_json',json.dumps(envelope))
        await self.denied()

    async def test_invalid_original_provider_receipt_keeps_all_fences(self):
        self.verified.side_effect=ValueError('Controlled invalid original receipt')
        await self.denied()

    async def test_uncertain_gateway_call_never_counts_as_settled(self):
        with self.gateway.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET state='uncertain' WHERE call_id='call-original-0'")
        await self.denied()

    async def test_uncertain_privileged_action_never_counts_as_settled(self):
        with self.g.connect(self.path) as db:
            db.execute('CREATE TABLE governance_action(invocation_id TEXT,state TEXT)')
            db.execute("INSERT INTO governance_action VALUES(?,'uncertain')",(self.row['invocation_id'],))
        await self.denied()

    async def test_native_task_change_during_two_reads_preserves_original_run(self):
        async def changing(doctype,name):
            result=deepcopy(self.task)
            if self.erp.get.await_count>=2:result['status']='Working'
            return result
        self.erp.get.side_effect=changing
        await self.denied()

    async def test_completed_http_does_not_hide_unfinished_repository_test(self):
        with self.gateway.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET operation='repository.test',result_json=? "
                       "WHERE call_id='call-original-0'",(json.dumps({'state':'running'}),))
        await self.denied()

    async def test_completed_delivery_operation_stays_with_its_own_reconciliation(self):
        with self.gateway.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET operation='repository.deliver' "
                       "WHERE call_id='call-original-0'")
        await self.denied()

    async def test_human_reopening_after_outer_close_keeps_original_task_fenced(self):
        original=self.close_outer.side_effect
        async def reopen(row,reason):
            proof=await original(row,reason)
            self.task['status']='Working'
            return proof
        self.close_outer.side_effect=reopen
        with self.assertRaises(ValueError):await self.retire()
        self.assertEqual(self.current()['state'],'uncertain')
        self.assertTrue(self.maintenance.has_lease('governed',self.row['invocation_id'],
                         db_path=self.maintenance.path(self.path.parent)))

    async def test_committed_outer_closure_recovers_after_settlement_io_failure(self):
        with patch.object(self.maintenance,'settle_reference',side_effect=RuntimeError('Controlled settlement failure')):
            with self.assertRaises(RuntimeError):await self.retire()
        self.assertEqual(self.current()['state'],'uncertain')
        self.assertTrue(self.maintenance.has_lease('governed',self.row['invocation_id'],
                         db_path=self.maintenance.path(self.path.parent)))
        await self.retire()
        self.assertEqual(self.current()['state'],'cancelled')
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE invocation_id=? "
                 "AND kind='external_task_outer_closed'",(self.row['invocation_id'],)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE invocation_id=? "
                 "AND kind='external_task_terminal'",(self.row['invocation_id'],)).fetchone()[0],1)

    async def test_outer_close_failure_preserves_lease_then_same_retirement_retries(self):
        original=self.close_outer.side_effect
        self.close_outer.side_effect=RuntimeError('Controlled transport model write failure')
        with self.assertRaises(RuntimeError):await self.retire()
        self.assertEqual(self.current()['state'],'uncertain')
        self.assertTrue(self.maintenance.has_lease('governed',self.row['invocation_id'],
                         db_path=self.maintenance.path(self.path.parent)))
        self.close_outer.side_effect=original
        await self.retire()
        self.assertEqual(self.current()['state'],'cancelled')

    async def test_duplicate_retirement_keeps_one_terminal_audit_and_never_qa_verifies(self):
        await self.retire()
        before=self.g.audit_events(P,db_path=self.path)
        await self.retire()
        self.assertEqual(self.current()['state'],'cancelled')
        after=self.g.audit_events(P,db_path=self.path)
        self.assertEqual(before,after)
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_verification').fetchone()[0],0)

    async def test_recovery_human_terminal_unsettled_tools_cannot_fall_back_to_reader(self):
        with self.gateway.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET state='uncertain' WHERE call_id='call-original-0'")
        executor=types.ModuleType('open_webui.utils.agentic_executor')
        executor.complete_original=AsyncMock();executor.reconcile_publication=AsyncMock()
        executor.DotAdapter=lambda app:None
        reader=AsyncMock(return_value=None)
        with patch.dict(sys.modules,{'open_webui.utils.agentic_executor':executor}):
            recovery=self.load('agentic_recovery')
            with patch.object(recovery,'read_format_correction',new=AsyncMock(return_value=None)) as correction:
                with self.assertRaisesRegex(ValueError,'unsettled|unsupported'):
                    await recovery.recover_original(self.row['invocation_id'],config={},
                        project_gate=self.gate,bridge=self.native,db_path=self.path,
                        reader=reader,automation=self.automation)
                correction.assert_not_awaited()
        reader.assert_not_awaited();executor.complete_original.assert_not_awaited()
        executor.reconcile_publication.assert_not_awaited()
        self.assert_held()

    async def test_recovery_ai_authored_terminal_keeps_original_normal_reader_path(self):
        self.task['modified_by']=self.users['project_manager']
        executor=types.ModuleType('open_webui.utils.agentic_executor')
        executor.complete_original=AsyncMock();executor.reconcile_publication=AsyncMock()
        executor.DotAdapter=lambda app:None
        reader=AsyncMock(return_value=None)
        with patch.dict(sys.modules,{'open_webui.utils.agentic_executor':executor}):
            recovery=self.load('agentic_recovery')
            with patch.object(recovery,'read_format_correction',new=AsyncMock(return_value=None)) as correction:
                result=await recovery.recover_original(self.row['invocation_id'],config={},
                    project_gate=self.gate,bridge=self.native,db_path=self.path,
                    reader=reader,automation=self.automation)
                correction.assert_awaited_once()
        self.assertIsNone(result)
        reader.assert_awaited_once()
        executor.complete_original.assert_not_awaited()
        executor.reconcile_publication.assert_not_awaited()
        self.assert_held()

    async def test_retirement_preserves_every_unrelated_maintenance_lease(self):
        gate=self.maintenance.path(self.path.parent)
        unrelated=self.maintenance.acquire('governed','unrelated-original-admission-0002',
                           db_path=gate,governance_project=fixture.OTHER)
        with self.maintenance._db(gate,write=False) as db:
            before=dict(db.execute('SELECT * FROM lease WHERE id=?',(unrelated.id,)).fetchone())
        await self.retire()
        with self.maintenance._db(gate,write=False) as db:
            after=dict(db.execute('SELECT * FROM lease WHERE id=?',(unrelated.id,)).fetchone())
        self.assertEqual(before,after)
        self.assertTrue(self.maintenance.has_lease('governed','unrelated-original-admission-0002',db_path=gate))
        self.assertFalse(self.maintenance.has_lease('governed',self.row['invocation_id'],db_path=gate))

    async def test_retirement_unblocks_independent_lane_but_manual_done_is_not_ai_qa(self):
        self.enqueue('independent-original-0002',SECOND)
        denied=self.g.claim_invocation('independent-original-0002',
                self.bridge_module.stable_run_id('independent-original-0002'),gate=self.gate_value,
                db_path=self.path,serialize_project=True)
        self.assertIsNone(denied)
        await self.retire()
        admitted=self.g.claim_invocation('independent-original-0002',
                self.bridge_module.stable_run_id('independent-original-0002'),gate=self.gate_value,
                db_path=self.path,serialize_project=True)
        self.assertIsNotNone(admitted)
        orchestrator=self.load('agentic_orchestrator')
        # The external manual terminal state is preserved, not a forged QA proof.
        self.assertFalse(await orchestrator._task_done(self.native,P,T,self.path))


if __name__=='__main__':
    unittest.main()

"""Recover existing final-QA reply after attachment-cap fix; never submit again."""
import asyncio,importlib,json,sqlite3,types,time
from pathlib import Path
import core_project_tree_uat as t
c=t.c
original=json.loads((c.D/'core-project-tree-report.json').read_text());project=original['project'];anchor=original['anchor']
matches=[]
for db in (c.D/'core-runtime').glob('*/ledger.sqlite'):
    with sqlite3.connect(db) as con:
        if con.execute('SELECT count(*) FROM governance_invocation WHERE erp_project=?',(project,)).fetchone()[0]:matches.append(db)
assert len(matches)==1,matches
c.DB=matches[0];c.PROJECT=project;c.config['agentic_artifact_roots'][project]=str(c.DB.parent/'tree-artifacts')
c.REPORT=c.D/'core-project-tree-recovery-report.json';c.report.update(project=project,anchor=anchor,original_db=str(c.DB),recovered_original_reply=True)
recovery=importlib.import_module('open_webui.utils.agentic_recovery')
async def reader(row,automation):
    with c.ledger.connect(c.DB) as db:
        saved=db.execute('SELECT run_id,result_json FROM governance_local_result WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
    assert saved and saved[0]==row['automation_run_id']
    return {'response_text':'AGOV RESULT: '+saved[1]}
async def run():
    try:
        with c.ledger.connect(c.DB) as db:
            before=db.execute('SELECT count(*) FROM governance_invocation WHERE erp_project=?',(project,)).fetchone()[0]
            pending=db.execute("SELECT invocation_id FROM governance_invocation WHERE erp_project=? AND erp_task=? AND target_role='qa' AND state IN ('uncertain','execution_finished','verified')",(project,anchor)).fetchall()
        assert len(pending)==1,pending
        row=c.ledger.find(pending[0][0],c.DB,include_private=True)
        automation=types.SimpleNamespace(id=row['agent_id'],user_id='isolated-core-uat-owner',meta={'agentic_project':project,'agentic_role':'qa','erp_role_user':'ai-qa@abitcons.com','governance_managed':True})
        receipt=await recovery.recover_original(row['invocation_id'],config=c.config,project_gate=c.gate,bridge=c.bridge,
            db_path=c.DB,reader=reader,adapter=c.dot,automation=automation)
        c.check('existing_original_final_QA_reply_published_without_resubmission',receipt is not None and receipt['status']=='QA APPROVED' and not c.dot.calls)
        async def forbidden_dispatch(ident):raise AssertionError('Existing full tree recovery attempted another execution '+ident)
        await c.orchestrator.advance(row['invocation_id'],receipt,config=c.config,project_gate=c.gate,dispatch=forbidden_dispatch,db_path=c.DB,bridge=c.bridge)
        tasks=[r async for r in c.erp.rows('Task',['name','status','parent_task','is_group','custom_agov_dispatch_released'],[['project','=',project]])]
        c.check('actual_ARCH_PM_two_children_and_parent_all_QA_APPROVED',len(tasks)==3 and all(x['status']=='QA APPROVED' for x in tasks))
        with c.ledger.connect(c.DB) as db:
            rows=[dict(r) for r in db.execute('SELECT invocation_id,target_role,erp_task,state,automation_run_id FROM governance_invocation WHERE erp_project=? ORDER BY created_at,rowid',(project,))]
        roles=[r['target_role'] for r in rows]
        c.check('eight_distinct_native_architecture_PM_child_and_parent_work_units',roles==['solution_architect','project_manager','developer','qa','developer','qa','developer','qa'],roles=roles)
        c.check('parent_approval_is_separate_independent_acceptance',sum(r['erp_task']==anchor and r['target_role']=='developer' for r in rows)==1 and sum(r['erp_task']==anchor and r['target_role']=='qa' for r in rows)==1)
        for _ in range(2):
            for item in rows:
                saved=c.store.get_result(item['invocation_id'],c.DB)
                assert saved['phase']=='published'
                await c.orchestrator.advance(item['invocation_id'],saved['receipt'],config=c.config,project_gate=c.gate,dispatch=forbidden_dispatch,db_path=c.DB,bridge=c.bridge)
            await c.orchestrator.scan_project(project,config=c.config,project_gate=c.gate,dispatch=forbidden_dispatch,db_path=c.DB,bridge=c.bridge)
        with c.ledger.connect(c.DB) as db:after=db.execute('SELECT count(*) FROM governance_invocation WHERE erp_project=?',(project,)).fetchone()[0]
        c.check('full_tree_recovery_duplicate_callbacks_and_scans_zero_new_runs',before==after==8 and not c.dot.calls)
        c.report.update(passed=True,tasks=tasks,invocations=rows,finished_at=time.time());c.REPORT.write_text(json.dumps(c.report,indent=2));print('CORE_FULL_NATIVE_TREE_RECOVERY_PASS',len(c.report['checks']),flush=True)
    except Exception as error:
        c.report.update(passed=False,error={'type':type(error).__name__,'message':str(error)[:2500]});c.REPORT.write_text(json.dumps(c.report,indent=2));raise
    finally:await c.erp.client.aclose();await c.admin.client.aclose()
if __name__=='__main__':asyncio.run(run())

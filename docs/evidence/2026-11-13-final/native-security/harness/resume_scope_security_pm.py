"""Resume only existing never-submitted nativePM, then finite original receipt replay."""
import asyncio,hashlib,importlib,json,time,types
from pathlib import Path
import core_scope_security_uat as t
c=t.c
original=json.loads((c.D/'core-scope-security-report.json').read_text())
assert original['project']=='PROJ-0014' and original['task']=='TASK-2026-00041'
c.PROJECT=original['project'];c.RUNTIME=Path(original['runtime']);c.DB=c.RUNTIME/'ledger.sqlite';c.config['agentic_artifact_roots'][c.PROJECT]=str(c.RUNTIME/'security-artifacts');c.report=original;c.report.pop('error',None);c.REPORT=c.D/'core-scope-security-report.json'
with c.ledger.connect(c.DB) as db:rows=[dict(r) for r in db.execute('SELECT * FROM governance_invocation WHERE erp_project=? ORDER BY created_at,rowid',(c.PROJECT,))]
pending=[r for r in rows if r['state']=='uncertain' and r['target_role']=='project_manager' and r['submitted_at'] is None]
assert len(pending)==1 and len(rows)==4
row=pending[0];packet=json.loads(row['work_packet']);assert packet.get('qa_review') and not packet.get('pm_review_continuation')
c.dot.calls=[(r['invocation_id'],r['target_role'],r['erp_task'],r['automation_run_id']) for r in rows if r['submitted_at'] is not None]
assert [x[1] for x in c.dot.calls]==['project_manager','developer','qa']
for r in rows:
 saved=c.store.get_result(r['invocation_id'],c.DB)
 if saved and saved['phase']=='published':t.receipts.append((r['invocation_id'],saved['receipt']))
async def run():
 try:
  t.native('active',row,packet);c.dot.native_verified=True
  c.check('original_PM_not_submitted_before_native_guard_fixture',row['submitted_at'] is None,invocation_id=row['invocation_id'],run_id=row['automation_run_id'])
  recovery=importlib.import_module('open_webui.utils.agentic_recovery')
  automation=types.SimpleNamespace(id=row['agent_id'],user_id='isolated-core-uat-owner',meta={'governance_managed':True,'agentic_project':c.PROJECT,'agentic_role':'project_manager','erp_role_user':'ai-projects@abitcons.com'})
  async def no_original(current,automation):return None
  receipt=await recovery.recover_original(row['invocation_id'],config=c.config,project_gate=c.gate,bridge=c.bridge,db_path=c.DB,reader=no_original,adapter=c.dot,automation=automation)
  assert receipt and receipt['pm_review_state']=='held';t.receipts.append((row['invocation_id'],receipt))
  await c.orchestrator.advance(row['invocation_id'],receipt,config=c.config,project_gate=c.gate,dispatch=t.dispatch,db_path=c.DB,bridge=c.bridge)
  final=await c.erp.get('Task',original['task']);roles=[x[1] for x in c.dot.calls]
  c.check('native_QA_PM_continue_reviewed_sequence',final['status']=='QA APPROVED' and roles==['project_manager','developer','qa','project_manager','project_manager'],roles=roles)
  c.check('PM_continuation_has_new_logical_identity',len(t.pm_packets)==2 and t.pm_packets[0][0]['invocation_id']!=t.pm_packets[1][0]['invocation_id'] and t.pm_packets[1][1]['pm_review_continuation']['generation']==1)
  c.check('latest_PM_review_complete',c.M['agentic_pm_review'].review_complete(c.PROJECT,original['task'],c.DB))
  count=len(c.dot.calls);before=c.bridge_module.digest(final)
  for _ in range(2):
   for old,oldreceipt in t.receipts:await c.orchestrator.advance(old,oldreceipt,config=c.config,project_gate=c.gate,dispatch=t.dispatch,db_path=c.DB,bridge=c.bridge)
   await c.orchestrator.scan_project(c.PROJECT,config=c.config,project_gate=c.gate,dispatch=t.dispatch,db_path=c.DB,bridge=c.bridge)
  c.check('all_original_receipt_replays_and_scans_no_duplicate_or_QA_mutation',len(c.dot.calls)==count and c.bridge_module.digest(await c.erp.get('Task',original['task']))==before,controlled_submission_count=count)
  report=t.native('final',*t.pm_packets[-1]);download=next(item for item in report['checks'] if item['name']=='published_PM_download_links_are_fixed_scoped_RPC')
  response=await c.admin.client.get(download['download_url']);c.check('actual_fixed_PM_download_RPC_returns_exact_private_bytes',response.status_code==200 and hashlib.sha256(response.content).hexdigest()==download['sha256'])
  c.report.update(passed=True,recovered_original=True,finished_at=time.time(),boundaries=[{'invocation_id':x[0],'role':x[1],'task':x[2],'run':x[3]} for x in c.dot.calls],receipts=[{'invocation_id':old,**r} for old,r in t.receipts]);c.REPORT.write_text(json.dumps(c.report,indent=2));print('CORE_SCOPE_SECURITY_RESUME_PASS',len(c.report['checks']),flush=True)
 except Exception as error:
  c.report.update(passed=False,error={'type':type(error).__name__,'message':str(error)[:2000]});c.REPORT.write_text(json.dumps(c.report,indent=2));raise
 finally:await c.erp.client.aclose();await c.admin.client.aclose()
if __name__=='__main__':asyncio.run(run())

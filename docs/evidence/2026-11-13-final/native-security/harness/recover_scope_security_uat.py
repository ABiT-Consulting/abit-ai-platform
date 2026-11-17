"""Recover original PROJ0014 known500 publication once, continue existing native security fixture."""
import asyncio,hashlib,json,subprocess,time
from pathlib import Path
import core_scope_security_uat as t
c=t.c
original=json.loads((c.D/'core-scope-security-report.json').read_text());(c.D/'pre-metadatafix-core-scope-security-report.json').write_text(json.dumps(original,indent=2))
c.PROJECT=original['project'];c.RUNTIME=Path(original['runtime']);c.DB=c.RUNTIME/'ledger.sqlite';c.config['agentic_artifact_roots'][c.PROJECT]=str(c.RUNTIME/'security-artifacts');c.report=original;c.report.pop('error',None);c.REPORT=c.D/'core-scope-security-report.json'
with c.ledger.connect(c.DB) as db:
 rows=[dict(row) for row in db.execute('SELECT * FROM governance_invocation WHERE erp_project=? ORDER BY created_at,rowid',(c.PROJECT,))]
assert len(rows)==2 and rows[1]['target_role']=='developer'
c.dot.calls=[(row['invocation_id'],row['target_role'],row['erp_task'],row['automation_run_id']) for row in rows]
for row in rows:
 saved=c.store.get_result(row['invocation_id'],c.DB)
 if saved and saved['phase']=='published':t.receipts.append((row['invocation_id'],saved['receipt']))
ident=rows[1]['invocation_id'];saved=c.store.get_result(ident,c.DB);assert saved['phase']=='uncertain'
log=c.D/'core-scope-security.log';assert "ERP HTTP 500 KeyError: 'id'" in log.read_text()
from operator_original_publication import recover
async def run():
 try:
  receipt=await recover(c,ident,c.D/'scope-security-operator-decision.json')
  c.check('explicit_known500_operator_recovery_same_event_no_Dev_resubmit',len(c.dot.calls)==2 and receipt['status']=='Pending Review',event_id=saved['event']['event_id'],run_id=saved['event']['run_id'])
  t.receipts.append((ident,receipt));await c.orchestrator.advance(ident,receipt,config=c.config,project_gate=c.gate,dispatch=t.dispatch,db_path=c.DB,bridge=c.bridge)
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
  c.report.update(passed=True,recovered_original=True,finished_at=time.time(),boundaries=[{'invocation_id':x[0],'role':x[1],'task':x[2],'run':x[3]} for x in c.dot.calls],receipts=[{'invocation_id':old,**r} for old,r in t.receipts]);c.REPORT.write_text(json.dumps(c.report,indent=2));print('CORE_SCOPE_SECURITY_RECOVERY_PASS',len(c.report['checks']),flush=True)
 except Exception as error:
  c.report.update(passed=False,error={'type':type(error).__name__,'message':str(error)[:2000]});c.REPORT.write_text(json.dumps(c.report,indent=2));raise
 finally:await c.erp.client.aclose();await c.admin.client.aclose()
if __name__=='__main__':asyncio.run(run())

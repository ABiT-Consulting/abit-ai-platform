"""Focused native P0 permissions + real QA->PM continue->reviewed, separate fresh project."""
import asyncio,hashlib,json,subprocess,uuid,time
from pathlib import Path
from urllib.parse import urlsplit
import core_http_uat as c
c.REPORT=c.D/'core-scope-security-report.json';c.report['scenario']='Fresh atomic adoption and native persisted-scope permission regressions'
c.report['provider_requests']=0;c.report['production_changes']=False
receipts=[];pm_packets=[]
def native(phase,row,packet):
 args={'project':c.PROJECT,'task':row['erp_task'],'phase':phase,'pm_invocation':row['invocation_id'],'qa_run':packet['qa_review']['qa_run_id']}
 result=subprocess.run(['docker','exec','-i','--user','frappe','--workdir','/home/frappe/frappe-bench','agov-uat-20261113-app','/home/frappe/frappe-bench/env/bin/python','/tmp/native_scope_security.py'],input=json.dumps(args),capture_output=True,text=True,timeout=45)
 (c.D/('native-scope-security-'+phase+'.log')).write_text(result.stdout+result.stderr)
 subprocess.run(['docker','cp','agov-uat-20261113-app:/tmp/p0-native-'+phase+'-report.json',str(c.D/('native-scope-security-'+phase+'-report.json'))],check=True,capture_output=True)
 report=json.loads((c.D/('native-scope-security-'+phase+'-report.json')).read_text())
 if result.returncode or not report.get('passed'):raise AssertionError('Native '+phase+' guard scenario failed: '+json.dumps(report.get('error')))
 c.check('actual_native_'+phase+'_persisted_scope_and_evidence_guards',True,checks=len(report['checks']),report=str(c.D/('native-scope-security-'+phase+'-report.json')))
 return report
class SecurityDot(c.ControlledDot):
 async def submit(self,row,packet,run_id,automation,bridge,*,before_submit):
  review=packet.get('qa_review')
  if review:
   pm_packets.append((dict(row),dict(packet)))
   if len(pm_packets)==1 and not getattr(self,'native_verified',False):native('active',row,packet)
  result=await super().submit(row,packet,run_id,automation,bridge,before_submit=before_submit)
  if review and len(pm_packets)==1:
   body=json.loads(result['response_text'].removeprefix('AGOV RESULT: '));body['outcome']='continue';body.pop('qa_review_ack',None)
   body['summary']='Controlled native PM checkpoint: exact QA remains approved, one new bounded coordination turn required.'
   result['response_text']='AGOV RESULT: '+json.dumps(body)
  return result
c.dot=SecurityDot()
async def dispatch(ident):
 receipt=await c.execute(ident)
 if not receipt:
  row=c.ledger.find(ident,c.DB);raise AssertionError('Security flow held '+row['target_role']+' '+row['state']+' '+str(row.get('reason','')))
 receipts.append((ident,receipt))
 await c.orchestrator.advance(ident,receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
async def run():
 try:
  fields={'project_name':'AGOV P0 Native Security '+uuid.uuid4().hex[:8],'company':'AGOV Isolated UAT Company','status':'Open','custom_agentic_governance_enabled':1,'custom_ai_project_manager_task_automation_enabled':1,'custom_github_repository':'https://github.com/ABiT-Consulting/abit-ai-platform','custom_test_environment_name':'Isolated native security UAT','custom_ai_project_srs':'Validate persisted original scopes, actual native private evidence and bounded QA to PM continuation.','custom_ai_developer_instructions':'Synthetic canary only; no external/provider actions.','custom_ai_project_features':[{'requirement_id':'REQ-UAT-01','feature_title':'Native original scope and evidence','feature_kind':'API','description':'Reject detach/retarget/mutable evidence and retain native stop/pause fences.','acceptance_criteria':'Original scope and bytes preserved, actual QA approved then bounded PM acknowledgment.','priority':'P0'}]}
  project=(await c.admin.request('POST','/api/resource/Project',json=fields))['data'];c.PROJECT=project['name'];c.report['project']=c.PROJECT;c.config['agentic_artifact_roots'][c.PROJECT]=str(c.RUNTIME/'security-artifacts')
  await c.admin.request('POST','/api/method/frappe.desk.form.assign_to.add',json={'doctype':'Project','name':c.PROJECT,'assign_to':['ai-projects@abitcons.com'],'description':'Own isolated security canary control','notify':0})
  project=await c.erp.get('Project',c.PROJECT);records=c.ledger.enroll(c.PROJECT,'isolated-core-uat-owner',c.PRIVATE['role_users'],c.DB);await c.bridge.register(c.PROJECT,project['modified'],records)
  _,gate=await c.gate(c.PROJECT,c.config);c.ledger.project_control(c.PROJECT,'enable',actor='authorized-canary-human',policy_revision=gate['revision'],db_path=c.DB)
  task=(await c.admin.request('POST','/api/resource/Task',json={'project':c.PROJECT,'subject':'P0 native guard atomic fixture '+uuid.uuid4().hex[:8],'description':'Fresh isolated Task; actual role chain with controlled protocol responses only.','status':'Open'}))['data'];task_id=task['name'];c.report['task']=task_id
  adopted=(await c.admin.request('POST',c.bridge_module.RPC+'configure_existing_task',json={'project_id':c.PROJECT,'task_id':task_id,'expected_modified':task['modified'],'requirement_ids':['REQ-UAT-01'],'acceptance_tests':'Exact persisted scope, immutable native bytes, independent QA and finite PM review.','target_role':'developer'}))['message']
  c.check('native_human_adoption_keeps_task_Open',adopted['status']=='Open')
  packet=await c.bridge.packet(c.PROJECT,task_id,'project_manager',c.ledger.agent_id(c.PROJECT,'project_manager'));_,gate=await c.gate(c.PROJECT,c.config);ident='security-adoption-'+uuid.uuid4().hex
  c.ledger.enqueue(invocation_id=ident,project=c.PROJECT,task=task_id,from_role='erp',target_role='project_manager',task_revision=packet['task_revision'],context='Fresh native P0 guard fixture; no provider/model.',work_packet=packet,actor='canary-human',gate=gate,db_path=c.DB)
  await dispatch(ident)
  final=await c.erp.get('Task',task_id);roles=[x[1] for x in c.dot.calls]
  c.check('native_QA_PM_continue_reviewed_sequence',final['status']=='QA APPROVED' and roles==['project_manager','developer','qa','project_manager','project_manager'],roles=roles)
  c.check('PM_continuation_has_new_logical_identity',len(pm_packets)==2 and pm_packets[0][0]['invocation_id']!=pm_packets[1][0]['invocation_id'] and pm_packets[1][1]['pm_review_continuation']['generation']==1)
  c.check('latest_PM_review_complete',c.M['agentic_pm_review'].review_complete(c.PROJECT,task_id,c.DB))
  count=len(c.dot.calls);before=c.bridge_module.digest(final)
  for _ in range(2):
   for original,receipt in receipts:await c.orchestrator.advance(original,receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
   await c.orchestrator.scan_project(c.PROJECT,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
  c.check('all_original_receipt_replays_and_scans_no_duplicate_or_QA_mutation',len(c.dot.calls)==count and c.bridge_module.digest(await c.erp.get('Task',task_id))==before,controlled_submission_count=count)
  report=native('final',*pm_packets[-1]);download=next(item for item in report['checks'] if item['name']=='published_PM_download_links_are_fixed_scoped_RPC')
  response=await c.admin.client.get(download['download_url']);c.check('actual_fixed_PM_download_RPC_returns_exact_private_bytes',response.status_code==200 and hashlib.sha256(response.content).hexdigest()==download['sha256'])
  c.report['boundaries']=[{'invocation_id':x[0],'role':x[1],'task':x[2],'run':x[3]} for x in c.dot.calls];c.report['receipts']=[{'invocation_id':ident,**receipt} for ident,receipt in receipts];c.report['runtime']=str(c.RUNTIME);c.report['passed']=True;c.report['finished_at']=time.time();c.REPORT.write_text(json.dumps(c.report,indent=2));print('CORE_SCOPE_SECURITY_PASS',len(c.report['checks']),flush=True)
 except Exception as error:
  c.report['passed']=False;c.report['runtime']=str(c.RUNTIME);c.report['error']={'type':type(error).__name__,'message':str(error)[:2000]};c.REPORT.write_text(json.dumps(c.report,indent=2));raise
 finally:await c.erp.client.aclose();await c.admin.client.aclose()
if __name__=='__main__':asyncio.run(run())

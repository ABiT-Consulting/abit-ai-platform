"""Signed developer/QA callback UAT on the isolated real Frappe site."""
exec(open('/tmp/site_uat.py').read().split('try:\n config=')[0])
# Reuse definitions without rerunning the metadata/fixture setup.
from io import BytesIO
from PIL import Image
from frappe.utils.file_manager import save_file
frappe.init(SITE);frappe.connect();frappe.set_user(SERVICE);request();frappe.flags.mute_emails=True
from abit_ai_assistant import agentic_control as ctrl,agentic_policy as policy
base=json.loads(REPORT.read_text());alpha,beta=base['projects'];pm_task=base['child_task'];report={'site':SITE,'checks':[],'started_at':time.time(),'no_model_or_external_network':True};REPORT=Path('/tmp/agov-flow-report.json')
def event(project,task,role,run,kind,packet,expected_modified=None,**extra):
 doc=current_task(task)
 body={'schema_version':1,'event_id':'uat-flow-'+uuid.uuid4().hex,'type':kind,'project_id':project,'task_id':task,'task_revision':packet['task_revision'],'run_id':run,'invocation_id':'invocation-'+run,'role':role,'agent_id':binding_id(project,role),'summary':'Synthetic real-framework governance callback fixture; no model work.','expected_modified':expected_modified or str(doc.modified),'expected_project_revision':packet['project_revision'],'work_packet_digest':packet['content_sha256'],'artifacts':[],**extra}
 return body
def send(body):signed(body);receipt=ctrl.receive_result(body);frappe.db.commit();return receipt
def start(project,task,role,**extra):
 packet=ctrl.get_work_packet(project,task,role);run='uat-'+role+'-'+uuid.uuid4().hex;body=event(project,task,role,run,'run.started',packet,**extra);return packet,run,send(body)
def create_child(pm_run,subject):
 doc=current_task(pm_task);srs=ctrl._srs(alpha)
 packet={'project_id':alpha,'source_role':'project_manager','source_agent_id':binding_id(alpha,'project_manager'),'target_role':'developer','idempotency_key':'uat-leaf-'+uuid.uuid4().hex,'srs_revision':srs['revision'],'requirement_ids':['REQ-UAT-01'],'source_run_id':pm_run,'source_invocation_id':'invocation-'+pm_run,'subject':subject,'description':'Validate real callback/evidence/cancellation safety with synthetic content.','acceptance_tests':'Exact scope and original run; real private PNG; independent QA all-pass.','reason':'Isolated actual-framework UAT','parent_task':pm_task,'dependencies':[],'estimate_hours':1,'priority':'Medium'}
 Path('/tmp/agov-last-task-request.json').write_text(json.dumps(packet));os.chmod('/tmp/agov-last-task-request.json',0o600)
 signed({'schema_version':1,'event_id':packet['idempotency_key'],'payload':packet});result=ctrl.create_agent_task(packet);frappe.db.commit();return result['task_id']
def artifact(task):
 output=BytesIO();Image.new('RGB',(8,8),(25,160,210)).save(output,format='PNG');data=output.getvalue();file=save_file('agov-private-uat-'+uuid.uuid4().hex+'.png',data,'Task',task,is_private=1);frappe.db.commit();actual=file.get_content();check('actual_private_File_bytes_verified_'+task,actual==data and file.is_private==1 and file.attached_to_name==task)
 return {'id':'UAT-EVIDENCE-1','kind':'screenshot','url':'https://erp.abit.ai'+file.file_url+'?fid='+file.name,'sha256':hashlib.sha256(actual).hexdigest(),'requirement_ids':['REQ-UAT-01']}
try:
 pm_packet,pm_run,pm_receipt=start(alpha,pm_task,'project_manager');check('native_PM_run_started',pm_receipt['status']=='Working')
 leaf=create_child(pm_run,'Synthetic developer and independent QA leaf');report['leaf_task']=leaf;check('native_PM_Group_child_developer_task',current_task(leaf).parent_task==pm_task and current_task(pm_task).is_group)
 packet,dev_run,started=start(alpha,leaf,'developer');check('developer_started_with_original_packet',started['status']=='Working')
 evidence=artifact(leaf);ready=event(alpha,leaf,'developer',dev_run,'run.ready',packet,started['modified'],artifacts=[evidence]);receipt=send(ready);check('developer_ready_stamps_actual_implementation',receipt['status']=='Pending Review' and current_task(leaf).custom_agov_implementation_run==dev_run)
 qapacket,qa_run,qastart=start(alpha,leaf,'qa',implementation_run_id=dev_run);check('independent_QA_claimed_actual_implementation',qastart['status']=='QA Reviewing')
 qaevidence=artifact(leaf);tests=[{'test_id':test['id'],'status':'pass','observed':'Actual isolated Frappe readback and private image verification passed.','evidence_ids':['UAT-EVIDENCE-1']} for test in qapacket['acceptance_tests']]
 contract={'schema_version':1,'invocation_id':'invocation-'+qa_run,'actor':qapacket['binding'],'project_id':alpha,'task_id':leaf,'task_revision':qapacket['task_revision'],'work_packet_digest':qapacket['content_sha256'],'outcome':'pass','summary':'Synthetic independent QA fixture backed by actual private PNG.','test_results':tests,'evidence':[{'id':'UAT-EVIDENCE-1','kind':'screenshot','uri':qaevidence['url'],'sha256':qaevidence['sha256'],'requirement_ids':['REQ-UAT-01']}],'implementation_invocation_id':'invocation-'+dev_run,'implementation_agent_id':binding_id(alpha,'developer'),'defects':[]}
 passed=event(alpha,leaf,'qa',qa_run,'qa.passed',qapacket,qastart['modified'],implementation_run_id=dev_run,acceptance_passed=True,artifacts=[qaevidence],result_contract=contract)
 bad={**passed,'event_id':'uat-bad-'+uuid.uuid4().hex,'result_contract':{**contract,'test_results':[dict(test,status='skipped') for test in tests]}}
 denied('actual_API_rejects_skipped_acceptance_before_QA_approval',lambda:send(bad));check('rejected_QA_did_not_change_Task',current_task(leaf).status=='QA Reviewing')
 bad={**passed,'event_id':'uat-self-'+uuid.uuid4().hex,'result_contract':{**contract,'implementation_agent_id':binding_id(alpha,'qa')}}
 denied('actual_API_rejects_QA_self_implementation_reference',lambda:send(bad))
 cross={**passed,'event_id':'uat-cross-'+uuid.uuid4().hex,'project_id':beta};denied('actual_callback_cross_Project_rejected',lambda:send(cross))
 approved=send(passed);check('actual_developer_to_independent_QA_APPROVED',approved['status']=='QA APPROVED' and current_task(leaf).status=='QA APPROVED')
 before=frappe.db.count(ctrl.RECORD,{'kind':'result'});again=send(passed);check('actual_QA_callback_duplicate_no_second_result',again['duplicate'] and frappe.db.count(ctrl.RECORD,{'kind':'result'})==before)
 stale=create_child(pm_run,'Synthetic stale input callback leaf');oldpacket,oldrun,oldreceipt=start(alpha,stale,'developer');oldartifact=artifact(stale)
 frappe.set_user('Administrator');doc=current_task(stale);doc.description+=' Human revised acceptance input.';doc.save();frappe.db.commit();frappe.set_user(SERVICE);request()
 old=event(alpha,stale,'developer',oldrun,'run.ready',oldpacket,oldreceipt['modified'],artifacts=[oldartifact]);denied('native_stale_task_input_callback_rejected',lambda:send(old));check('stale_result_never_marked_ready',current_task(stale).status=='Working')
 # Standard Project terminal status is a real framework state, not a fabricated hold.
 frappe.set_user('Administrator');project=frappe.get_doc('Project',beta);project.status='Completed';project.save();frappe.db.commit();frappe.set_user(SERVICE);request();denied('native_Completed_Project_gate_rejects_work',lambda:ctrl._project_gate(beta))
 report['passed']=True;report['finished_at']=time.time();REPORT.write_text(json.dumps(report,indent=2));print('ACTUAL_FRAPPE_FLOW_PASS',len(report['checks']),flush=True)
except Exception as error:
 frappe.db.rollback();report['passed']=False;report['error']={'type':type(error).__name__,'message':str(error)[:1200]};REPORT.write_text(json.dumps(report,indent=2));print('ACTUAL_FRAPPE_FLOW_ERROR',json.dumps(report['error']),flush=True);raise
finally:frappe.destroy()

"""Read-only, own-canary proof for the known rejected Developer publication."""
import sys,json,hashlib,socket,os
from pathlib import Path
import frappe
body=json.load(sys.stdin)
assert socket.gethostname()=='9d18c8756fef'
assert body['project_id'] in {'PROJ-0013','PROJ-0014'} and body['type']=='run.ready' and body['role']=='developer'
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='agov-uat.localhost',sites_path='/home/frappe/frappe-bench/sites');frappe.connect();frappe.set_user('Administrator')
try:
 from abit_ai_assistant import agentic_control as c
 from abit_ai_assistant import agentic_permissions as permissions
 c._lock(body['project_id']);frappe.db.get_value('Task',body['task_id'],'name',for_update=True)
 project=c._project_gate(body['project_id']);task=c._task(body['project_id'],body['task_id']);c._execution_allowed(project.name,task.name)
 assert task.custom_agov_active_run==body['run_id'] and task.status=='Working'
 assert str(task.modified)==body['expected_modified'] and str(project.modified)==body['expected_project_revision']
 assert c._task_revision(task)==body['task_revision']
 assert c._lookup('inbox',body['event_id']) is None and c._lookup('result_receipt',body['event_id']) is None
 assert c._lookup('result',[project.name,body['run_id'],body['type']]) is None
 start=c._lookup('run_started',[project.name,body['run_id']]);context=c._lookup('run_context',[project.name,body['run_id']]);assert start and context and start.task==context.task==task.name
 registered=c._payload(start);frozen=c._payload(context)
 assert all(registered[key]==body[key] for key in ('role','agent_id','invocation_id','task_revision','work_packet_digest'))
 assert frozen['work_packet_digest']==body['work_packet_digest']
 fresh=c._context(c.get_work_packet(project.name,task.name,body['role']))
 assert all(fresh.get(key)==frozen.get(key) for key in ('task_revision','activity_sha256','srs_sha256','project_revision'))
 c._check_attachment_snapshot(frozen,c._attachment_manifest(project.name,task.name),body['artifacts'])
 binding=c._binding(project.name,body['role'],body['agent_id'])
 verified=[c._verified_evidence(item,project.name,task.name,binding.erp_ai_user,c._task_requirements(task)) for item in body['artifacts']]
 from abit_ai_assistant.agentic_delivery import validate_developer_ready
 validate_developer_ready(body,task,verified)
 proof={'schema_version':1,'canary_site':'agov-uat.localhost','hostname':socket.gethostname(),'project_id':project.name,'task_id':task.name,
  'event_id':body['event_id'],'run_id':body['run_id'],'invocation_id':body['invocation_id'],'event_sha256':c.policy.digest(body),
  'native_project_and_task_locks':True,'inbox_result_receipt_absent':True,'registered_start_and_context_exact':True,
  'task_status':task.status,'task_modified':str(task.modified),'project_revision':str(project.modified),'current_inputs_and_original_private_artifacts_verified':True,
  'fixed_delivery_validator_accepts_original_exact_event':True,'control_sha256':hashlib.sha256(Path(c.__file__).read_bytes()).hexdigest(),
  'permissions_sha256':hashlib.sha256(Path(permissions.__file__).read_bytes()).hexdigest(),'database_writes':0}
 print('AGOV_OPERATOR_PROOF '+json.dumps(proof,sort_keys=True))
finally:frappe.db.rollback();frappe.destroy()

"""Actual isolated Frappe/ERPNext metadata, hooks and signed API canary.

No worker/server/provider is started. All records are synthetic in the new site.
"""
from pathlib import Path
import hashlib,hmac,json,os,secrets,time,uuid
from datetime import datetime
from werkzeug.wrappers import Request

ROOT=Path('/home/frappe/frappe-bench/sites');os.chdir(ROOT)
import frappe
SITE='agov-uat.localhost';REPORT=Path('/tmp/agov-uat-report.json')
ROLES={'solution_architect':'ai-projects@abitcons.com','project_manager':'ai-projects@abitcons.com','developer':'ai-developer@abitcons.com','qa':'ai-qa@abitcons.com','operations':'ai-operations@abitcons.com','it_manager':'ai-operations@abitcons.com'}
SERVICE=ROLES['developer'];SECRET=os.environ['BRIDGE_SECRET']
report={'site':SITE,'framework':{},'checks':[],'started_at':time.time(),'no_model_or_external_network':True,'fixture_registry_only':True}
def check(name,value=True,**details):
 assert value,name
 report['checks'].append({'name':name,'passed':True,**details});REPORT.write_text(json.dumps(report,indent=2));print('CHECK',name,flush=True)
def denied(name,fn):
 try:fn()
 except (frappe.PermissionError,ValueError,frappe.ValidationError):check(name);frappe.db.rollback();return
 raise AssertionError(name+' unexpectedly allowed')
def request(headers=None):frappe.local.request=Request.from_values(headers={'X-ABIT-Agentic-Bridge':SECRET,**(headers or {})})
def signed(body):
 stamp=str(int(time.time()));data=stamp+'\n'+body['event_id']+'\n'+policy.canonical(body)
 request({'X-ABIT-Timestamp':stamp,'X-ABIT-Event-ID':body['event_id'],'X-ABIT-Signature':hmac.new(SECRET.encode(),data.encode(),hashlib.sha256).hexdigest()})
def binding_id(project,role):return str(uuid.uuid5(uuid.NAMESPACE_URL,f'https://abit.ai/agentic/v1/{project}/{role}'))
def current_task(name):return frappe.get_doc('Task',name)
def publish(project,task,role,run,kind,packet=None,**extra):
 doc=current_task(task);project_doc=frappe.get_doc('Project',project);packet=packet or ctrl.get_work_packet(project,task,role)
 body={'schema_version':1,'event_id':'uat-event-'+uuid.uuid4().hex,'type':kind,'project_id':project,'task_id':task,'task_revision':policy.task_revision(doc.as_dict()),'run_id':run,'invocation_id':'invocation-'+run,'role':role,'agent_id':binding_id(project,role),'summary':'Synthetic isolated canary protocol verification; no AI model executed.','expected_modified':str(doc.modified),'expected_project_revision':str(project_doc.modified),'work_packet_digest':packet['content_sha256'],'artifacts':[],**extra}
 signed(body);receipt=ctrl.receive_result(body);frappe.db.commit();return body,receipt

try:
 config=ROOT/SITE/'site_config.json';settings=json.loads(config.read_text());settings.update(abit_agentic_bridge_token=SECRET,abit_agentic_control_token=SECRET,abit_agentic_service_user=SERVICE,abit_agentic_role_users=ROLES,maintenance_mode=0,pause_scheduler=1,mute_emails=1);config.write_text(json.dumps(settings))
 frappe.init(SITE);frappe.connect();frappe.set_user('Administrator');frappe.flags.mute_emails=True
 import erpnext
 from abit_ai_assistant import agentic_control as ctrl,agentic_policy as policy,project_agent_bindings as bindings,project_srs
 report['framework']={'frappe':frappe.__version__,'erpnext':erpnext.__version__,'installed_apps':frappe.get_installed_apps()}
 check('actual_application_installed','abit_ai_assistant' in frappe.get_installed_apps())
 for name in ('AI Governance Record','AI Project Agent Binding','AI Project SRS Revision','AI Project SRS Feature'):check('metadata_'+name,frappe.db.exists('DocType',name))
 ctrl.ensure_control_fields();project_srs.ensure_project_srs_fields();fieldcount=frappe.db.count('Custom Field');ctrl.ensure_control_fields();project_srs.ensure_project_srs_fields();check('migrations_idempotent',frappe.db.count('Custom Field')==fieldcount)
 options=frappe.get_meta('Task').get_field('status').options.splitlines();check('native_QA_statuses_registered',all(x in options for x in ('Blocked','QA Reviewing','QA APPROVED','QA REJECTED')))
 if not frappe.db.exists('Warehouse Type','Transit'):
  from erpnext.setup.setup_wizard.operations.install_fixtures import install
  install('United States');frappe.db.commit()
 company_name='AGOV Isolated UAT Company'
 if not frappe.db.exists('Company',company_name):frappe.get_doc({'doctype':'Company','company_name':company_name,'abbr':'AGU','country':'United States','default_currency':'USD'}).insert(ignore_permissions=True)
 frappe.db.commit()
 for user in set(ROLES.values())|{'uat-reader@example.invalid'}:
  if not frappe.db.exists('User',user):
   doc=frappe.get_doc({'doctype':'User','email':user,'first_name':'Synthetic UAT','enabled':1,'send_welcome_email':0,'user_type':'System User'});doc.flags.no_welcome_mail=True
   doc.insert(ignore_permissions=True)
   if user!='uat-reader@example.invalid':doc.add_roles('System Manager','Projects Manager','Projects User')
 frappe.db.commit();frappe.set_user(SERVICE);request()
 suffix=datetime.utcnow().strftime('%H%M%S')
 projects=[]
 for label in ('Alpha','Beta'):
  frappe.set_user('Administrator')
  doc=frappe.get_doc({'doctype':'Project','project_name':'AGOV Isolated '+label+' '+suffix,'company':company_name,'status':'Open','custom_agentic_governance_enabled':0,'custom_github_repository':'https://github.com/ABiT-Consulting/abit-ai-platform','custom_test_environment_name':'Isolated UAT','custom_ai_project_srs':'Verify isolated scoped task execution and independent QA.','custom_ai_developer_instructions':'Use only synthetic fixtures; no external/provider/business actions.','custom_ai_project_features':[{'requirement_id':'REQ-UAT-01','feature_title':'Isolated governance','feature_kind':'API','description':'Two projects share role users but distinct scoped agent bindings.','acceptance_criteria':'Reject duplicate/stale/cross-project commands and require independent evidence.','priority':'P0'}]})
  doc.insert();frappe.db.commit();projects.append(doc.name)
  from frappe.desk.form.assign_to import add
  add({'doctype':'Project','name':doc.name,'assign_to':['ai-projects@abitcons.com'],'description':'Synthetic UAT project control','notify':0});frappe.db.commit()
  frappe.set_user(SERVICE);request();denied('checkbox_optin_required_'+label,lambda:ctrl._project_gate(doc.name))
  frappe.set_user('Administrator');doc.reload();doc.custom_agentic_governance_enabled=1;doc.save();frappe.db.commit()
  frappe.set_user(SERVICE);request();denied('separate_PM_checkbox_OFF_denies_'+label,lambda:ctrl._project_gate(doc.name))
  frappe.set_user('Administrator');doc.reload();doc.custom_ai_project_manager_task_automation_enabled=1;doc.save();frappe.db.commit()
  frappe.set_user(SERVICE);request();check('opted_in_open_assigned_'+label,ctrl._project_gate(doc.name).name==doc.name)
  check('authorized_human_explicit_PM_optin_'+label,bool(doc.custom_ai_project_manager_task_automation_enabled))
  check('private_assign_native_omission_handled_'+label,doc.as_dict().get('_assign') is None and 'ai-projects@abitcons.com' in json.loads(doc.get('_assign')))
  project=frappe.get_doc('Project',doc.name)
  for role,user in ROLES.items():
   ident=binding_id(doc.name,role);key='uat-provision-'+hashlib.sha256((doc.name+role).encode()).hexdigest()[:40]
   bindings.bind_project_agent(doc.name,role,user,ident,source_project_revision=str(project.modified),provision_event_id=key)
   bindings.verify_project_agent_binding(doc.name,role,'primary',ident,hashlib.sha256(('fixture-registry'+ident).encode()).hexdigest())
  frappe.db.commit()
  check('six_verified_bindings_'+label,len(bindings.get_project_agent_bindings(doc.name))==6)
 alpha,beta=projects;report['projects']=projects
 check('same_ERP_users_distinct_project_agent_IDs',all(binding_id(alpha,role)!=binding_id(beta,role) for role in ROLES))
 denied('cross_project_agent_identity_rejected',lambda:ctrl._binding(alpha,'developer',binding_id(beta,'developer')))
 frappe.set_user('uat-reader@example.invalid');request();denied('native_Project_read_permission_required',lambda:ctrl.get_work_packet(alpha,'','project_manager'))
 frappe.set_user(SERVICE);request()
 project=frappe.get_doc('Project',alpha);source=ctrl._ensure_architecture_task(project);frappe.db.commit();report['architecture_task']=source
 packet=ctrl.get_work_packet(alpha,source,'solution_architect');check('native_versioned_SRS_packet',bool(packet['srs']['revision']) and packet['binding']['agent_id']==binding_id(alpha,'solution_architect'))
 run='uat-architect-'+uuid.uuid4().hex
 body,receipt=publish(alpha,source,'solution_architect',run,'run.started',packet)
 check('native_Task_started_signed_callback',current_task(source).status=='Working' and receipt['run_id']==run)
 count=frappe.db.count(ctrl.RECORD,{'kind':'inbox'});signed(body);again=ctrl.receive_result(body);frappe.db.commit();check('callback_duplicate_returns_original_receipt',again['duplicate'] is True and frappe.db.count(ctrl.RECORD,{'kind':'inbox'})==count)
 task_packet={'project_id':alpha,'source_role':'solution_architect','source_agent_id':binding_id(alpha,'solution_architect'),'target_role':'project_manager','idempotency_key':'uat-create-'+uuid.uuid4().hex,'srs_revision':packet['srs']['revision'],'requirement_ids':['REQ-UAT-01'],'source_run_id':run,'source_invocation_id':'invocation-'+run,'subject':'Synthetic native child task','description':'Exercise actual Task tree and idempotent protected creation.','acceptance_tests':'One native child; exact requirement and assignment; no duplicate.','reason':'Canary protocol verification','parent_task':source,'dependencies':[],'estimate_hours':1,'priority':'Medium'}
 def create(value):
  signed({'schema_version':1,'event_id':value['idempotency_key'],'payload':value});return ctrl.create_agent_task(value)
 child=create(task_packet);frappe.db.commit();check('native_child_Task_created',child['created'] and current_task(child['task_id']).parent_task==source)
 report['child_task']=child['task_id'];check('native_parent_promoted_to_Group',bool(current_task(source).is_group))
 before=frappe.db.count('Task',{'parent_task':source});again=create(task_packet);frappe.db.commit();check('duplicate_task_key_creates_no_second_Task',again['task_id']==child['task_id'] and not again['created'] and frappe.db.count('Task',{'parent_task':source})==before)
 denied('changed_task_key_payload_rejected',lambda:create({**task_packet,'subject':'Different content'}))
 check('actual_transactional_outbox_created',frappe.db.count(ctrl.RECORD,{'kind':'outbox','state':'Pending'})>0)
 # Audit records are immutable through their real Frappe controller.
 row=frappe.get_doc(ctrl.RECORD,frappe.get_all(ctrl.RECORD,fields=['name'],limit_page_length=1)[0].name)
 original=row.payload_json;row.payload_json='{}';denied('native_audit_controller_rejects_mutation',lambda:row.save(ignore_permissions=True))
 frappe.db.commit();report['passed']=True;report['finished_at']=time.time();REPORT.write_text(json.dumps(report,indent=2));print('NATIVE_UAT_PASS',len(report['checks']),flush=True)
except Exception as error:
 if getattr(frappe.local,'db',None):frappe.db.rollback()
 report['passed']=False;report['error']={'type':type(error).__name__,'message':str(error)[:1500]};REPORT.write_text(json.dumps(report,indent=2));print('NATIVE_UAT_ERROR',json.dumps(report['error']),flush=True);raise
finally:frappe.destroy()

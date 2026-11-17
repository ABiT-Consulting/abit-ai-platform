from pathlib import Path
import os,json,time,uuid
from werkzeug.wrappers import Request
os.chdir('/home/frappe/frappe-bench/sites')
import frappe
from frappe.desk.form.assign_to import add
from abit_ai_assistant import agentic_control as ctrl,agentic_policy as policy
SITE='agov-uat.localhost';PM='custom_ai_project_manager_task_automation_enabled';SERVICE='ai-developer@abitcons.com'
frappe.init(SITE);frappe.connect();frappe.set_user('Administrator')
report={'site':SITE,'checks':[],'started_at':time.time(),'models_sent':0,'committed_unauthorized_mutations':0}
base=json.loads(Path('/tmp/agov-uat-report.json').read_text());project=base['projects'][0];task=base['child_task']
def record(name,passed,**detail):report['checks'].append({'name':name,'passed':bool(passed),**detail});print('CHECK',name,'PASS' if passed else 'FAIL',flush=True)
def denied(name,action):
 try:action()
 except (frappe.PermissionError,frappe.ValidationError,ValueError):record(name,True)
 else:record(name,False,bypass=True)
 finally:frappe.db.rollback()
try:
 field=frappe.get_meta('Project').get_field(PM);record('exact_PM_checkbox_exists',bool(field) and field.label=='Enable AI Project Manager Task Automation');record('PM_checkbox_default_OFF',str(field.default)=='0' and bool(field.no_copy))
 doc=frappe.get_doc('Project',project);doc.set(PM,0);doc.save();frappe.db.commit()
 from abit_ai_assistant.project_srs import ensure_project_srs_fields
 ensure_project_srs_fields();frappe.db.commit();doc.reload();record('existing_project_migration_did_not_enable_PM',not doc.get(PM))
 frappe.set_user(SERVICE);frappe.local.request=Request.from_values()
 denied('OFF_actual_project_gate_denied',lambda:ctrl._project_gate(project))
 frappe.set_user('ai-projects@abitcons.com')
 denied('OFF_direct_PM_native_Task_insert_denied',lambda:frappe.get_doc({'doctype':'Task','project':project,'subject':'UNAUTHORIZED CANARY PM CHECKBOX PROBE '+uuid.uuid4().hex,'description':'Must rollback; proves direct native CRUD cannot bypass PM opt-in.'}).insert())
 denied('OFF_direct_PM_native_assignment_denied',lambda:add({'doctype':'Task','name':task,'assign_to':['ai-operations@abitcons.com'],'description':'UNAUTHORIZED CANARY PM assignment probe','notify':0}))
 def self_enable():
  item=frappe.get_doc('Project',project);item.set(PM,1);item.save()
 denied('AI_PM_cannot_self_enable_automation_permission',self_enable)
 report['passed']=all(c['passed'] for c in report['checks']);report['finished_at']=time.time();Path('/tmp/agov-pm-checkbox-report.json').write_text(json.dumps(report,indent=2));print('PM_CHECKBOX_ACTUAL_UAT',json.dumps({'passed':report['passed'],'checks':len(report['checks'])}))
finally:frappe.db.rollback();frappe.destroy()

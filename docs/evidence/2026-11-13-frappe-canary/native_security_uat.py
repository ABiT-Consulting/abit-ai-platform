"""Actual Frappe native write bypass, revocation and private File regressions."""
exec(open('/tmp/site_uat.py').read().split('try:\n config=')[0])
from frappe.utils.file_manager import save_file
from frappe.desk.form.assign_to import add,remove
from io import BytesIO
from PIL import Image
frappe.init(SITE);frappe.connect();frappe.set_user('Administrator');frappe.flags.mute_emails=True
from abit_ai_assistant import agentic_control as ctrl,agentic_policy as policy
base=json.loads(REPORT.read_text());alpha=base['projects'][0];task=base['child_task'];anchor=base['architecture_task']
report={'checks':[],'started_at':time.time(),'committed_unauthorized_mutations':0,'models_sent':0};REPORT=Path('/tmp/agov-native-security-report.json')
PM='custom_ai_project_manager_task_automation_enabled'
try:
    doc=frappe.get_doc('Project',alpha);doc.set(PM,0);doc.save();frappe.db.commit()
    for user in set(ROLES.values()):
        frappe.set_user(user);request()
        denied('OFF_native_Task_'+user,lambda:frappe.get_doc({'doctype':'Task','project':alpha,'subject':'MUST NEVER COMMIT '+uuid.uuid4().hex}).insert())
        denied('OFF_native_ToDo_'+user,lambda:add({'doctype':'Task','name':task,'assign_to':['ai-operations@abitcons.com'],'description':'MUST NEVER COMMIT','notify':0}))
    frappe.set_user('Administrator');doc.reload();doc.set(PM,1);doc.save();frappe.db.commit()
    frappe.set_user('ai-projects@abitcons.com');request()
    def forged_task():
        doc=frappe.get_doc({'doctype':'Task','project':alpha,'subject':'FORGED FLAGS MUST FAIL '+uuid.uuid4().hex})
        doc.flags.agov_task_creation=True;doc.flags.agov_internal=True;doc.flags.agov_result_write=True
        return doc.insert(ignore_permissions=True)
    denied('ON_client_Document_flags_do_not_grant_server_authority',forged_task)
    frappe.set_user(SERVICE);request()
    def edit_task():
        doc=current_task(task);doc.description+=' UNAUTHORIZED EDIT';doc.flags.agov_result_write=True;doc.save(ignore_permissions=True)
    denied('ON_direct_AI_Task_edit_requires_server_provenance',edit_task)
    denied('ON_direct_AI_assignment_requires_server_provenance',lambda:add({'doctype':'Task','name':task,'assign_to':['ai-operations@abitcons.com'],'description':'UNAUTHORIZED','notify':0}))
    frappe.set_user('Administrator');project=frappe.get_doc('Project',alpha);project.status='Cancelled';project.save()
    check('native_Project_Cancelled_status_readback',project.status=='Cancelled')
    frappe.set_user(SERVICE);request();denied('native_Cancelled_Project_revocation_denies_work',lambda:ctrl._project_gate(alpha))
    frappe.set_user('Administrator');remove('Project',alpha,'ai-projects@abitcons.com')
    frappe.set_user(SERVICE);request();denied('native_Project_unassignment_revocation_denies_work',lambda:ctrl._project_gate(alpha))
    frappe.set_user(SERVICE);request();check('rollback_restored_authorized_Project_gate',ctrl._project_gate(alpha).name==alpha)
    image=BytesIO();color=tuple(uuid.uuid4().bytes[:3]);Image.new('RGB',(8,8),color).save(image,format='PNG');png=image.getvalue()
    def upload(target,private):
        return frappe.get_doc({'doctype':'File','file_name':'uat-evidence-'+uuid.uuid4().hex+'.png','content':png,
            'attached_to_doctype':'Task','attached_to_name':target,'is_private':private}).insert(ignore_permissions=True)
    denied('native_public_AI_evidence_File_denied',lambda:upload(anchor,0))
    denied('native_File_on_inactive_Task_denied',lambda:upload(task,1))
    # Match upload_file's direct content path; legacy save_file prewrites and
    # can re-encode binary during Frappe's subsequent File.before_insert.
    file=frappe.get_doc({'doctype':'File','file_name':'uat-private-allowed-'+uuid.uuid4().hex+'.png','content':png,
        'attached_to_doctype':'Task','attached_to_name':anchor,'is_private':1}).insert(ignore_permissions=True);frappe.db.commit()
    check('actual_Frappe_File_controller_resolves_early_governance_mixin',any(cls.__name__=='GovernedEvidenceFile' for cls in type(file).__mro__))
    file.reload();check('native_private_active_run_File_allowed',file.is_private==1 and file.get_content(encodings=[])==png)
    verified=ctrl._verified_evidence({'kind':'screenshot','url':'https://erp.abit.ai'+file.file_url+'?fid='+file.name,
        'sha256':hashlib.sha256(png).hexdigest()},alpha,anchor,ROLES['solution_architect'])
    check('native_binary_PNG_evidence_SHA_matches_exact_stored_bytes',verified['sha256']==hashlib.sha256(png).hexdigest())
    for role in ('developer','operations','qa'):
        denied('native_wrong_'+role+'_cannot_claim_architecture_Task',lambda role=role:ctrl.get_work_packet(alpha,anchor,role))
    def mutate_file():
        doc=frappe.get_doc('File',file.name);doc.file_name='changed-immutable.png';doc.save(ignore_permissions=True)
    denied('native_existing_private_File_is_immutable',mutate_file)
    denied('native_AI_evidence_File_delete_denied',lambda:frappe.delete_doc('File',file.name,ignore_permissions=True))
    check('denied_AI_File_delete_preserves_exact_original_private_bytes',frappe.db.exists('File',file.name) and frappe.get_doc('File',file.name).get_content(encodings=[])==png)
    report['passed']=True;report['finished_at']=time.time();REPORT.write_text(json.dumps(report,indent=2));print('NATIVE_SECURITY_ACTUAL_PASS',len(report['checks']),flush=True)
except Exception as error:
    frappe.db.rollback();report['passed']=False;report['error']={'type':type(error).__name__,'message':str(error)[:1500]};REPORT.write_text(json.dumps(report,indent=2));raise
finally:frappe.destroy()

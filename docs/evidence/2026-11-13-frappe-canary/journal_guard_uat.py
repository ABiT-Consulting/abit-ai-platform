"""Native JSON document flags probe; all attempted writes roll back."""
exec(open('/tmp/site_uat.py').read().split('try:\n config=')[0])
from frappe.client import insert
frappe.init(SITE);frappe.connect();frappe.set_user(SERVICE);request()
from abit_ai_assistant import agentic_control as ctrl,agentic_policy as policy
project=json.loads(REPORT.read_text())['projects'][0]
before=frappe.db.count(ctrl.RECORD);ident='CANARY-FORGED-'+uuid.uuid4().hex
doc={'doctype':ctrl.RECORD,'record_key':ident,'kind':'inbox','project':project,
    'content_sha256':policy.digest({'synthetic_probe':True}),'payload_json':policy.canonical({'synthetic_probe':True}),
    'state':'Recorded','flags':{'ignore_permissions':1,'agov_internal':1}}
try:
    try:
        insert(json.dumps(doc))
    except Exception as error:
        result={'passed':True,'native_json_insert_denied':True,'error_type':type(error).__name__,'message':str(error)[:500]}
    else:
        result={'passed':False,'native_json_insert_denied':False,'inserted_inside_rollback_only':True}
    finally:frappe.db.rollback()
    result['committed_mutations']=frappe.db.count(ctrl.RECORD)-before
    result['passed']=result['passed'] and result['committed_mutations']==0
    Path('/tmp/agov-journal-guard-report.json').write_text(json.dumps(result,indent=2));print('NATIVE_JOURNAL_FLAGS_PROBE',json.dumps(result),flush=True)
finally:frappe.db.rollback();frappe.destroy()

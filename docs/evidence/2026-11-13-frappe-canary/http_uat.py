"""Real WSGI/API authentication and Frappe permissions, no TCP/public ports."""
from pathlib import Path
import os,json,secrets,time
os.chdir('/home/frappe/frappe-bench/sites')
import frappe
from frappe.utils.password import set_encrypted_password
from werkzeug.test import Client
from werkzeug.wrappers import Response
SITE='agov-uat.localhost';base=json.loads(Path('/tmp/agov-uat-report.json').read_text());project=base['projects'][0];task=base['architecture_task'];tokens={}
frappe.init(SITE);frappe.connect();frappe.set_user('Administrator')
for user in ('ai-developer@abitcons.com','uat-reader@example.invalid'):
 key=secrets.token_hex(16);secret=secrets.token_hex(24);frappe.db.set_value('User',user,'api_key',key,update_modified=False);set_encrypted_password('User',user,secret,'api_secret');tokens[user]=key+':'+secret
flow=json.loads(Path('/tmp/agov-flow-report.json').read_text());files=frappe.get_all('File',filters={'attached_to_doctype':'Task','attached_to_name':flow['leaf_task'],'is_private':1},fields=['name','file_url'],limit_page_length=1);private=files[0];expected=frappe.get_doc('File',private.name).get_content()
frappe.db.commit();frappe.destroy()
from frappe.app import application
client=Client(application,Response);url='/api/method/abit_ai_assistant.agentic_control.get_work_packet';query={'project_id':project,'task_id':task,'role':'solution_architect'}
report={'site':SITE,'checks':[],'real_wsgi':True,'public_ports':False,'at':time.time()}
for user,expected in ((None,403),('uat-reader@example.invalid',403),('ai-developer@abitcons.com',200)):
 headers={'Authorization':'token '+tokens[user]} if user else {}
 response=client.get(url,query_string=query,base_url='http://'+SITE,headers=headers)
 assert response.status_code==expected,(user,response.status_code)
 body=response.get_json(silent=True) or {}
 if expected==200:assert body['message']['project_id']==project and body['message']['task_id']==task
 report['checks'].append({'actor':'Guest' if user is None else user,'status':response.status_code,'passed':True,'packet_scope_verified':expected==200})
 print('HTTP_CHECK','Guest' if user is None else user,response.status_code)
response=client.get(url,query_string={**query,'project_id':base['projects'][1]},base_url='http://'+SITE,headers={'Authorization':'token '+tokens['ai-developer@abitcons.com']})
report['cross_project_denial_status']=response.status_code
assert response.status_code in (400,403,409,417,500)
print('HTTP_CROSS_PROJECT_DENIAL',response.status_code)
assert response.status_code in (400,403,409,417), 'Business denial must not be HTTP500'
for user in (None,'uat-reader@example.invalid','ai-developer@abitcons.com'):
 headers={'Authorization':'token '+tokens[user]} if user else {}
 response=client.get(private.file_url,query_string={'fid':private.name},base_url='http://'+SITE,headers=headers)
 if user=='ai-developer@abitcons.com':assert response.status_code==200 and response.data==expected
 else:assert response.data!=expected and response.status_code in (301,302,401,403,404)
 report['checks'].append({'actor':'Guest' if user is None else user,'private_file_status':response.status_code,'passed':True,'exact_private_bytes':user=='ai-developer@abitcons.com'})
 print('PRIVATE_FILE_HTTP_CHECK','Guest' if user is None else user,response.status_code)
Path('/tmp/agov-http-report.json').write_text(json.dumps(report,indent=2));print('ACTUAL_HTTP_AUTH_UAT_PASS',len(report['checks']))

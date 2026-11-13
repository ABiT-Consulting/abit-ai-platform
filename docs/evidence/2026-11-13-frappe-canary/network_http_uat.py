"""Real Nginx -> Gunicorn -> Frappe auth/private File readback, internal net only."""
import os,json,secrets,hashlib,time
from pathlib import Path
import requests
os.chdir('/home/frappe/frappe-bench/sites');import frappe
from frappe.utils.password import set_encrypted_password
frappe.init('agov-uat.localhost');frappe.connect();frappe.set_user('Administrator')
flow=json.loads(Path('/tmp/agov-flow-report.json').read_text());leaf=flow['leaf_task'];docs=frappe.get_all('File',filters={'attached_to_doctype':'Task','attached_to_name':leaf,'is_private':1},fields=['name','file_url'],limit_page_length=1);file=frappe.get_doc('File',docs[0].name);expected=file.get_content();url=file.file_url;tokens={}
for user in ('ai-developer@abitcons.com','uat-reader@example.invalid'):
 key=secrets.token_hex(16);secret=secrets.token_hex(24);frappe.db.set_value('User',user,'api_key',key,update_modified=False);set_encrypted_password('User',user,secret,'api_secret');tokens[user]=key+':'+secret
frappe.db.commit();frappe.destroy();report={'internal_network_only':True,'checks':[],'at':time.time()}
for user in (None,'uat-reader@example.invalid','ai-developer@abitcons.com'):
 headers={'Authorization':'token '+tokens[user]} if user else {};response=requests.get('http://web:8080'+url,params={'fid':file.name},headers=headers,timeout=20,allow_redirects=False)
 if user=='ai-developer@abitcons.com':assert response.status_code==200 and response.content==expected
 else:assert response.content!=expected and response.status_code in (301,302,401,403,404)
 report['checks'].append({'actor':user or 'Guest','status':response.status_code,'passed':True,'private_bytes_returned':user=='ai-developer@abitcons.com'})
 print('NETWORK_PRIVATE_FILE',user or 'Guest',response.status_code)
response=requests.get('http://web:8080/protected/'+url.lstrip('/'),timeout=10,allow_redirects=False);assert response.status_code==404;report['checks'].append({'name':'nginx_internal_location_not_public','status':404,'passed':True})
report['sha256']=hashlib.sha256(expected).hexdigest();report['bytes']=len(expected);report['passed']=True;Path('/tmp/agov-network-http-report.json').write_text(json.dumps(report,indent=2));print('NETWORK_PRIVATE_FILE_UAT_PASS',len(report['checks']))

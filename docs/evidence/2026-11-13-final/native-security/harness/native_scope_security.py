"""Native Frappe controller P0 regressions; own project from controlled HTTP flow only."""
from pathlib import Path
import hashlib,hmac,json,os,sys,time,uuid
os.chdir('/home/frappe/frappe-bench/sites')
import frappe
from werkzeug.wrappers import Request
from abit_ai_assistant import agentic_control as c,agentic_policy as p,agentic_control_fence as fence
args=json.load(sys.stdin);project=args['project'];task_id=args['task'];phase=args['phase'];report={'phase':phase,'project':project,'task':task_id,'checks':[],'production_changes':False,'provider_calls':0,'framework':'actual Frappe controllers/SQL/private filesystem','permissions_sha256':hashlib.sha256(Path('/home/frappe/frappe-bench/apps/abit_ai_assistant/abit_ai_assistant/agentic_permissions.py').read_bytes()).hexdigest()}
frappe.init('agov-uat.localhost');frappe.connect();frappe.flags.mute_emails=True
service=str(frappe.conf.abit_agentic_service_user);secret=str(frappe.conf.abit_agentic_bridge_token)
def request(body=None):
 headers={'X-ABIT-Agentic-Bridge':secret}
 if body:
  stamp=str(int(time.time()));headers.update({'X-ABIT-Timestamp':stamp,'X-ABIT-Event-ID':body['event_id'],'X-ABIT-Signature':hmac.new(secret.encode(),(stamp+'\n'+body['event_id']+'\n'+p.canonical(body)).encode(),hashlib.sha256).hexdigest()})
 frappe.local.request=Request.from_values(headers=headers)
def check(name,**details):report['checks'].append({'name':name,'passed':True,**details})
def stable_digest(value):return p.digest(json.loads(json.dumps(value,default=str)))
def state():
 task=frappe.get_doc('Task',task_id)
 todos=frappe.get_all('ToDo',filters={'reference_type':'Task','reference_name':task_id},fields=['name','reference_type','reference_name','allocated_to','status'],order_by='name')
 files=frappe.get_all('File',filters={'attached_to_doctype':'Task','attached_to_name':task_id},fields=['name','file_name','file_url','is_private','owner','content_hash'],order_by='name')
 return stable_digest({'task':task.as_dict(),'todos':todos,'files':files})
def file_state(name):
 doc=frappe.get_doc('File',name);content=doc.get_content(encodings=[])
 if isinstance(content,str):content=content.encode()
 return {'metadata_sha256':stable_digest(doc.as_dict()),'bytes_sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content),'file_id':name}
attempt_paths=[]
def deny(name,operation,files=(),expected=None):
 attempted_before=len(attempt_paths)
 before=state();old=[file_state(fid) for fid in files];save='security_'+uuid.uuid4().hex
 frappe.db.savepoint(save)
 try:operation()
 except (ValueError,frappe.PermissionError,frappe.ValidationError) as error:
  error_type=type(error).__name__;message=str(error)
  frappe.db.rollback(save_point=save)
  if expected:assert expected in message,(name,error_type,message[:400])
 else:raise AssertionError(name+' unexpectedly authorized')
 after=state();new=[file_state(fid) for fid in files]
 assert before==after and old==new,name+' changed persisted scope or bytes'
 paths=attempt_paths[attempted_before:]
 assert all(not path.exists() for path in paths),name+' wrote an orphan private blob before denial'
 check(name,error_type=error_type,message=message[:300],before_sha256=before,after_sha256=after,file_receipts=old,denied_new_blob_paths_absent=len(paths))
def mutate(doctype,name,values,content=None):
 doc=frappe.get_doc(doctype,name)
 for key,value in values.items():setattr(doc,key,value)
 doc.flags.ignore_permissions=True;doc.flags.agov_internal=True;doc.flags.agov_result_write=True
 doc._doc_before_save=doc
 if content is not None:doc.content=content
 return doc.save(ignore_permissions=True)
def upload(doctype,name,label):
 filename='p0-'+label+'-'+uuid.uuid4().hex+'.txt';path=Path(frappe.get_site_path('private','files',filename));assert not path.exists();attempt_paths.append(path)
 return frappe.get_doc({'doctype':'File','file_name':filename,'content':('actual-native-'+uuid.uuid4().hex).encode(),'attached_to_doctype':doctype,'attached_to_name':name,'is_private':1}).insert(ignore_permissions=True)
def control(action,scope):
 frappe.set_user('Administrator');request();doc=frappe.get_doc('Project',project)
 receipt=c.project_control(project,action,str(doc.modified),'p0-control-'+uuid.uuid4().hex,task_id=scope);frappe.db.commit()
 check('native_'+action+'_intent_'+('task' if scope else 'project'),command_id=receipt['command_id'],sequence=receipt['control_sequence'])
 return receipt
def resume(scope):
 receipt=control('resume',scope)
 body={'schema_version':1,'event_id':'agov-control-applied-'+p.digest([project,scope,receipt['command_id'],receipt['control_sequence']])[:40],'project_id':project,'task_id':scope,'command_id':receipt['command_id'],'control_sequence':receipt['control_sequence'],'action':'resume','applied_state':'enabled'}
 frappe.set_user(service);request(body);ack=fence.acknowledge_control(body);frappe.db.commit()
 assert ack['durable'] and not ack['superseded'];request();fence.assert_execution_allowed(project,task_id)
 check('native_exact_resume_ACK_'+('task' if scope else 'project'),command_id=ack['command_id'],sequence=ack['control_sequence'])
try:
 frappe.set_user(service);request();task=frappe.get_doc('Task',task_id)
 assert task.project==project and task.status=='QA APPROVED' and task.custom_agov_active_run==args['qa_run']
 initial=state()
 start=c._lookup('pm_review_start',[project,args['pm_invocation']]);assert start and start.task==task_id
 target=start.name
 if phase=='active':
  # The source project is newly created by our host fixture; another existing
  # project is unnecessary to demonstrate detach/retarget before prior scope.
  deny('AI_Task_clear_Project_and_managed_flag',lambda:mutate('Task',task_id,{'project':None,'custom_agentic_managed':0}),expected='Project scope is immutable')
  todos=frappe.get_all('ToDo',filters={'reference_type':'Task','reference_name':task_id,'status':'Open'},fields=['name'])
  assert todos
  deny('AI_ToDo_detach_original_reference',lambda:mutate('ToDo',todos[0].name,{'reference_type':'','reference_name':''}),expected='reference scope is immutable')
  deny('AI_ToDo_retarget_original_reference_to_Project',lambda:mutate('ToDo',todos[0].name,{'reference_type':'Project','reference_name':project}),expected='reference scope is immutable')
  files=frappe.get_all('File',filters={'attached_to_doctype':'Task','attached_to_name':task_id},fields=['name'])
  assert files;fid=files[0].name
  f=frappe.get_doc('File',fid);assert any(cls.__name__=='GovernedEvidenceFile' for cls in type(f).__mro__)
  check('actual_native_File_controller_has_early_MRO_guard',file_id=fid)
  deny('AI_Task_File_detach_and_overwrite',lambda:mutate('File',fid,{'attached_to_doctype':'','attached_to_name':'','file_name':'must-not-rewrite.txt'},b'must-not-rewrite'),(fid,),expected='immutable')
  deny('AI_Task_File_retarget_to_unrelated_Project',lambda:mutate('File',fid,{'attached_to_doctype':'Project','attached_to_name':project}), (fid,),expected='immutable')
  deny('AI_existing_File_delete_preserves_bytes',lambda:frappe.delete_doc('File',fid,ignore_permissions=True),(fid,),expected='cannot be deleted')
  pm_file=upload(c.RECORD,target,'active-pm');frappe.db.commit();pm_id=pm_file.name
  check('actual_active_PM_journal_private_upload_allowed',**file_state(pm_id),record_id=target)
  deny('AI_PM_File_retarget_to_Project',lambda:mutate('File',pm_id,{'attached_to_doctype':'Project','attached_to_name':project}), (pm_id,),expected='immutable')
  frappe.set_user('Administrator')
  deny('human_cannot_rewrite_existing_server_PM_evidence',lambda:mutate('File',pm_id,{'attached_to_doctype':'','attached_to_name':''},b'no-human-rewrite'),(pm_id,),expected='immutable')
  control('stop',task_id);frappe.set_user(service);request()
  count=frappe.db.count('File',{'attached_to_doctype':c.RECORD,'attached_to_name':target})
  deny('native_Task_Stop_vetoes_PM_evidence_before_bytes',lambda:upload(c.RECORD,target,'stopped'),expected='Native Task control veto')
  assert count==frappe.db.count('File',{'attached_to_doctype':c.RECORD,'attached_to_name':target})
  resume(task_id)
  control('pause','');frappe.set_user(service);request()
  deny('native_Project_Pause_vetoes_PM_evidence_before_bytes',lambda:upload(c.RECORD,target,'paused'),expected='Native Project control veto')
  resume('')
  assert state()==initial,'Active PM tests mutated QA feature Task/files/assignment'
  check('all_active_PM_security_actions_preserve_feature_QA_snapshot',sha256=initial)
 elif phase=='final':
  assert c._lookup('pm_review_result',[project,args['pm_invocation']])
  deny('post_result_PM_new_evidence_denied',lambda:upload(c.RECORD,target,'post-pm'),expected='Completed PM review evidence is immutable')
  deny('post_QA_Task_new_evidence_denied',lambda:upload('Task',task_id,'post-qa'),expected='already published a result')
  links=__import__('abit_ai_assistant.agentic_pm_review',fromlist=['review_download_links']).review_download_links(project,target)
  assert links;check('published_PM_download_links_are_fixed_scoped_RPC',count=len(links),sha256=links[0]['sha256'],file_id=links[0]['file_id'],download_url=links[0]['download_url'])
  assert state()==initial;check('post_publication_denials_preserve_feature_QA_snapshot',sha256=initial)
  # Human source input remains valid after workflow verification has completed.
  frappe.set_user('Administrator');human=upload('Task',task_id,'human-input');frappe.db.commit()
  assert human.owner=='Administrator';check('ordinary_human_new_source_attachment_remains_allowed',file_id=human.name)
 else:raise AssertionError('Unknown canary phase')
 report['passed']=True
except Exception as error:
 frappe.db.rollback();report['passed']=False;report['error']={'type':type(error).__name__,'message':str(error)[:1000]};raise
finally:
 Path('/tmp/p0-native-'+phase+'-report.json').write_text(json.dumps(report,indent=2,default=str));frappe.destroy()
print('NATIVE_SCOPE_SECURITY_PASS',phase,len(report['checks']),flush=True)

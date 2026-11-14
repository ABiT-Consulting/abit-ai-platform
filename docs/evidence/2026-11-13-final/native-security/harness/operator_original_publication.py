"""Explicit Root-authorized known-500 recovery for exactly two synthetic fixtures.

Never resets the uncertain outbox. A used decision permits GET reconciliation
only. This helper cannot submit a model, create a Task, or replace event bytes.
"""
import json,subprocess,time
from pathlib import Path
async def recover(c,invocation,decision_path):
 row=c.ledger.find(invocation,c.DB,include_private=True);saved=c.store.get_result(invocation,c.DB)
 assert row and row['erp_project'] in {'PROJ-0013','PROJ-0014'} and row['target_role']=='developer' and row['state']=='execution_finished'
 assert saved and saved['phase']=='uncertain' and saved['event']['type']=='run.ready' and saved['run_id']==row['automation_run_id']
 event=saved['event'];event_hash=c.bridge_module.digest(event);decision='canary-known-500-'+c.bridge_module.digest([invocation,event_hash])[:40]
 response=await c.erp.request('GET',c.bridge_module.RPC+'get_result_receipt',params={'event_id':event['event_id'],'project_id':row['erp_project'],'task_id':row['erp_task']},headers={'X-ABIT-Agentic-Bridge':c.bridge.secret})
 with c.ledger.connect(c.DB) as db:
  used=db.execute("SELECT 1 FROM governance_event WHERE erp_project=? AND kind='operator_original_publication_authorized' AND json_extract(payload,'$.decision_id')=?",(row['erp_project'],decision)).fetchone()
 if used:
  assert response['message'].get('exists') is True,'Original audited attempt remains uncertain; no second POST permitted'
  receipt=response['message'].get('receipt',response['message']);c.store.publication_receipt(invocation,receipt,c.DB);return receipt
 assert response['message'].get('exists') is False
 native_script=Path(__file__).with_name('native_original_publication_proof.py')
 subprocess.run(['docker','cp',str(native_script),'agov-uat-20261113-app:/tmp/agov-native-original-publication-proof.py'],check=True,capture_output=True)
 native=subprocess.run(['docker','exec','-i','--user','frappe','--workdir','/home/frappe/frappe-bench','agov-uat-20261113-app','env/bin/python','/tmp/agov-native-original-publication-proof.py'],input=c.bridge_module.canonical(event),capture_output=True,text=True,check=True)
 lines=[line.removeprefix('AGOV_OPERATOR_PROOF ') for line in native.stdout.splitlines() if line.startswith('AGOV_OPERATOR_PROOF ')]
 assert len(lines)==1;proof=json.loads(lines[0]);assert proof['event_sha256']==event_hash
 record={'decision_id':decision,'authorized_scope':'Root explicit canary-only recovery after known native HTTP500 KeyError(id) transaction rejection',
  'automatic_uncertain_recovery_unchanged':'GET-only','original_outbox_phase':'uncertain','event_sha256':event_hash,'native_proof':proof,'authorized_at':time.time(),'planned_original_event_posts':1,'new_model_or_provider_requests':0}
 with c.ledger._transaction(c.DB) as db:
  current=db.execute('SELECT event_json,phase FROM governance_result WHERE invocation_id=?',(invocation,)).fetchone()
  assert current['phase']=='uncertain' and c.bridge_module.digest(json.loads(current['event_json']))==event_hash
  c.ledger._event(db,row['erp_project'],invocation,'operator_original_publication_authorized','authorized-canary-operator',record)
 decision_path=Path(decision_path);decision_path.write_text(json.dumps(record,indent=2)+'\n')
 receipt=await c.bridge.publish(event)
 c.store.publication_receipt(invocation,receipt,c.DB)
 with c.ledger._transaction(c.DB) as db:c.ledger._event(db,row['erp_project'],invocation,'operator_original_publication_receipt','authorized-canary-operator',{'decision_id':decision,'event_sha256':event_hash,'receipt_sha256':c.bridge_module.digest(receipt),'new_model_requests':0})
 record['receipt']={key:receipt.get(key) for key in ('event_id','task_id','run_id','status','modified','durable')};record['finished_at']=time.time();decision_path.write_text(json.dumps(record,indent=2)+'\n')
 return receipt

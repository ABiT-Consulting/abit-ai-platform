"""Offline checks only: actual pinned payload validation and bounded HTTP contract."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('original_diagnostic_subject',HERE/'diagnose_original.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
a=d.reviewed_helpers()
builder=d.actual_payload_builder(a)
from open_webui.utils.agentic_bridge import digest,stable_run_id
from open_webui.utils.abitdot_primary import dot_primary_key

def envelope():
    owner,agent,account='synthetic-owner','synthetic-agent','a'*64
    run=stable_run_id(d.INVOCATION); key=dot_primary_key(owner,agent,run)
    scope={'owner':owner,'account_id':account,'dot_id':'synthetic-dot~synthetic-owner',
        'project_id':'PROJ-TEST','task_id':'TASK-TEST','role':'developer','agent_id':agent,
        'erp_user':'fixture@example.invalid','repository':'fixture/repository','environment':'fixture',
        'tool_scopes':['context.read'],'work_packet_digest':'b'*64,'invocation_id':d.INVOCATION,
        'run_id':run,'job_id':'aibit_'+__import__('hashlib').sha256(('primary:'+key).encode()).hexdigest()[:48]}
    proof={'protocol_version':1,'scope':scope,'scope_digest':digest(scope),'enforced':True,
        'read_only':True,'default_deny':True,'enforcement_mode':'governed_action_gateway',
        'enforcement_boundary':'gateway_operations_and_managed_erp',
        'excluded_surfaces':['owner_account_plugins'],'gateway_session_id':'fixture-session',
        'enforced_tool_scopes':['context.read'],'external_write_allowlist':[],
        'proof_id':'fixture-proof','issued_at':0,'expires_at':60}
    value={'execution_policy':{'dot_primary':True},'owner_id':owner,'automation_id':agent,
        'run_id':run,'dot_id':scope['dot_id'],'idempotency_key':key,
        'task':{'meta':{'governance_managed':True,'governance_scope_digest':digest(scope),
            'governance_project':'PROJ-TEST','erp_task':'TASK-TEST','erp_role':'developer',
            'governance_packet_digest':'b'*64}},
        'governance_scope':{'protocol_version':1,'scope':scope,
                           'preflight_proof':proof,'submission_proof':copy.deepcopy(proof)}}
    return value,account

def diagnostic():
    return {'ok':True,'protocol':'v1_restored_target_diagnostic','read_only':True,
        'diagnostic_mode':'restored_target','dot_handoff':False,'response':'',
        'root_message_id':None,'reply_message_id':None,'safe_to_replay':False,
        'target_probe':{'code':'ORIGINAL_RESTORED_TARGET_MATCH','complete':True,
            'scope':'currently_mounted_configured_owner_dot_feed',
            **{k:0 for k in d.COUNTS},'candidate_count':1,'examined_count':1,'exact_match_count':1}}

class OriginalDiagnosticTests(unittest.TestCase):
    def test_actual_original_validator_accepts_expired_receipt_but_not_new_admission(self):
        value,account=envelope()
        original=copy.deepcopy(value)
        payload=builder(value,account,admission=False)
        self.assertEqual(value,original)
        self.assertEqual(payload['governance_scope'],value['governance_scope'])
        self.assertEqual(payload['job_id'],value['governance_scope']['scope']['job_id'])
        with self.assertRaises(Exception): builder(value,account,admission=True)

    def test_actual_validator_rejects_changed_owner_account_scope_and_run(self):
        value,account=envelope()
        for field,replacement in [('owner_id','other-owner'),('run_id','other-run')]:
            altered=copy.deepcopy(value);altered[field]=replacement
            with self.subTest(field=field),self.assertRaises(Exception):
                builder(altered,account,admission=False)
        with self.assertRaises(Exception): builder(value,'c'*64,admission=False)
        altered=copy.deepcopy(value);altered['governance_scope']['scope']['role']='qa'
        with self.assertRaises(Exception): builder(altered,account,admission=False)

    def test_sanitizer_accepts_only_fixed_complete_result(self):
        value=diagnostic()
        self.assertEqual(d.sanitized_result(200,value),value)
        value['target_probe']['code']='ORIGINAL_TARGET_PROBE_INCONCLUSIVE'
        with self.assertRaises(d.DiagnosticHeld):d.sanitized_result(200,value)

    def test_private_response_unknown_fields_and_invalid_counts_are_not_reported(self):
        for change in ('field','response','count'):
            value=diagnostic()
            if change=='field':value['private']='synthetic-private-value'
            elif change=='response':value['response']='synthetic-private-value'
            else:value['target_probe']['candidate_count']=True
            with self.subTest(change=change):
                with self.assertRaises(d.DiagnosticHeld) as err:d.sanitized_result(200,value)
                self.assertNotIn('synthetic-private-value',str(err.exception))
                self.assertEqual(str(err.exception),'DIAGNOSTIC_RESPONSE_INVALID')

    def test_actual_http_wrapper_posts_only_observe_once_and_closes(self):
        payload={'synthetic':'payload','diagnostic_mode':'restored_target'}
        calls=[]
        class Connection:
            def __init__(self,*args,**kwargs):calls.append(('connect',args,kwargs))
            def request(self,*args,**kwargs):calls.append(('request',args,kwargs))
            def getresponse(self):
                value=io.BytesIO(json.dumps(diagnostic()).encode());value.status=200;return value
            def close(self):calls.append(('close',))
        with patch.object(d.http.client,'HTTPConnection',Connection):
            status,result=d.call_once(payload,'synthetic-token-not-production')
        requests=[x for x in calls if x[0]=='request']
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0][1],('POST','/dot/observe'))
        self.assertEqual(json.loads(requests[0][2]['body']),payload)
        self.assertEqual(status,200);self.assertTrue(result['read_only'])
        self.assertEqual(calls[-1],('close',))

    def test_uncertain_http_failure_is_not_retried_and_private_detail_is_suppressed(self):
        calls=[]
        class Connection:
            def __init__(self,*args,**kwargs):pass
            def request(self,*args,**kwargs):calls.append('request')
            def getresponse(self):
                value=io.BytesIO(b'{"message":"synthetic-private-value"}');value.status=424;return value
            def close(self):calls.append('close')
        with patch.object(d.http.client,'HTTPConnection',Connection):
            with self.assertRaises(d.DiagnosticHeld) as err:d.call_once({},'synthetic-token')
        self.assertEqual(calls,['request','close'])
        self.assertEqual(err.exception.http_status,424)
        self.assertEqual(str(err.exception),'DIAGNOSTIC_HTTP_REJECTED')
        self.assertNotIn('synthetic-private-value',str(err.exception))

if __name__=='__main__':unittest.main(verbosity=2)

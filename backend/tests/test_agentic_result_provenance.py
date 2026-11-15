"""Versioned provenance compatibility: actual pure contracts and temporary SQLite.
No live data/fixtures, server startup, model call, ERP/network or business action.
"""
from __future__ import annotations
import ast
from copy import deepcopy
import importlib
import importlib.util
import json
import types
from unittest.mock import patch
from pathlib import Path
import sqlite3
import sys
import unittest

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
TESTS=BACKEND/'tests'
sys.path.insert(0,str(TESTS))
from test_agentic_controls_events import Fixture,P,T
ROOT=BACKEND/'open_webui/utils'

def static_plan():
    tree=ast.parse((TESTS/'test_agentic_planning.py').read_bytes())
    value=next(node.value for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name)and t.id=='PLAN'for t in node.targets))
    return json.loads(ast.literal_eval(value.args[0]))

class Provenance(Fixture):
    def setUp(self):
        super().setUp()
        self.provenance=self.candidate('agentic_result_provenance')
        self.store=self.candidate('agentic_result_store')
        self.validator=importlib.import_module('open_webui.utils.agentic_contracts.validator')
        self.ident='agov-provenance-fixture-0001'
        self.packet_value=self.packet(role='solution_architect')
        self.packet_value['srs']={'revision':'fixture-srs-v1','sha256':'1'*64}
        self.packet_value['requirement_ids']=['REQ-UI-01','REQ-API-02']
        self.packet_value['acceptance_tests']=[{'id':'TEST-UI-02','requirement_ids':['REQ-UI-01'],'method':'browser','expected':'Controlled UI acceptance.'},{'id':'TEST-API-01','requirement_ids':['REQ-API-02'],'method':'integration','expected':'Controlled API acceptance.'}]
        empty={'project_id':P,'tasks':[]};epoch=self.bridge_module.digest(empty)
        self.packet_value['project_backlog']={'project_id':P,'complete':True,'total_count':0,'page_size':50,'tasks':[],'context_excluded_task_ids':[],'snapshot_sha256':epoch,'context_sha256':epoch}
        self.packet_value['content_sha256']=self.bridge_module.packet_digest(self.packet_value)
        self.g.enqueue(invocation_id=self.ident,project=P,task=T,from_role='project_manager',target_role='solution_architect',task_revision=self.packet_value['task_revision'],context='Controlled provenance fixture',work_packet=self.packet_value,gate=self.gate_value,actor='server-fixture',db_path=self.path)
        run=self.bridge_module.stable_run_id(self.ident)
        self.row=self.g.claim_invocation(self.ident,run,gate=self.gate_value,db_path=self.path)
        self.g.mark_submitted(self.ident,run,self.row['lease_token'],gate=self.gate_value,db_path=self.path)
        self.row=self.g.find(self.ident,self.path,include_private=True)
        plan=static_plan();meta=plan['metadata'];meta['project_id']=P;meta['run_id']=self.ident;meta['source_url']='https://erp.abit.ai/desk/project/'+P
        meta['source_activity_revision']=self.packet_value['source_modified'];meta['project_backlog_context_sha256']=epoch;meta['project_backlog_snapshot_sha256']=epoch
        binding=self.packet_value['binding'];meta['agent_binding']={'project_id':P,'role':'solution_architect','abit_agent_id':binding['agent_id'],'erp_ai_user':binding['erp_user']}
        self.canonical={'schema_version':1,'invocation_id':self.ident,'work_packet_digest':self.packet_value['content_sha256'],'project_id':P,'task_id':T,'task_revision':self.packet_value['task_revision'],'actor':deepcopy(binding),'outcome':'ready_for_review','summary':'Controlled bounded architecture plan.','implementation_invocation_id':None,'implementation_agent_id':None,'test_results':[],'defects':[],'architect_plan':plan,'evidence':[{'id':'EV-DESIGN','kind':'design','uri':plan['designs'][0]['artifact']['uri'],'sha256':plan['designs'][0]['artifact']['sha256'],'requirement_ids':['REQ-UI-01','REQ-API-02']},{'id':'EV-MOCKUP','kind':'mockup','uri':plan['mockups'][0]['artifact']['uri'],'sha256':plan['mockups'][0]['artifact']['sha256'],'requirement_ids':['REQ-UI-01']}]}
        self.alias=deepcopy(self.canonical);self.alias['architect_plan']['metadata'].update(source_url='https://erp.abit.ai/desk/task/'+T,run_id=run)
        self.raw_reply='AGOV RESULT: '+json.dumps(self.alias)
        self.raw_sha=self.provenance.reply_sha256(self.raw_reply)
        # Existing parser function only: avoid importing unrelated backend models.
        workflow=types.ModuleType('open_webui.utils.erp_dot_workflow');workflow.json=json
        tree=ast.parse((ROOT/'erp_dot_workflow.py').read_bytes());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='parse_contract_json');exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/'erp_dot_workflow.py'),'exec'),workflow.__dict__)
        parser_patch=patch.dict(sys.modules,{'open_webui.utils.erp_dot_workflow':workflow});parser_patch.start();self.addCleanup(parser_patch.stop)
    def candidate(self,name):
        candidate=HERE/(name+'.py')
        if not candidate.is_file():candidate=ROOT/(name+'.py')
        full='open_webui.utils.'+name
        spec=importlib.util.spec_from_file_location(full,candidate);module=importlib.util.module_from_spec(spec);sys.modules[full]=module;setattr(self.utils,name,module);spec.loader.exec_module(module);return module
    def project(self,result=None,**changes):
        return self.provenance.project_provenance(result if result is not None else self.alias,row=changes.get('row',self.row),packet=changes.get('packet',self.packet_value),raw_reply_sha256=changes.get('raw_reply_sha256',self.raw_sha))
    def test_exact_two_aliases_project_only_provenance_without_mutating_input(self):
        before=deepcopy(self.alias);value,audit=self.project()
        self.assertEqual(value,self.canonical);self.assertEqual(self.alias,before);self.assertIsNot(value,self.alias);self.assertIsNotNone(audit)
        self.assertEqual(self.validator.validate_result(value,self.packet_value),[])
        self.assertEqual({x['pointer']for x in audit['changes']},{'/architect_plan/metadata/source_url','/architect_plan/metadata/run_id'})
    def test_canonical_and_single_alias_are_idempotent(self):
        value,audit=self.project(self.canonical);self.assertEqual(value,self.canonical);self.assertIsNone(audit)
        for field in ('source_url','run_id'):
            raw=deepcopy(self.canonical);raw['architect_plan']['metadata'][field]=self.alias['architect_plan']['metadata'][field];value,audit=self.project(raw)
            self.assertEqual(value,self.canonical);self.assertEqual(len(audit['changes']),1)
    def test_malicious_url_and_run_values_are_not_compatibility_aliases(self):
        for url in ('https://evil.invalid/desk/task/'+T,'https://erp.abit.ai/desk/task/OTHER','https://erp.abit.ai/desk/task/'+T+'?other=1','https://erp.abit.ai/desk/task/'+T+'#other','https://user@erp.abit.ai/desk/task/'+T,'https://erp.abit.ai/app/task/'+T,'https://erp.abit.ai:443/desk/task/'+T,'https://erp.abit.ai/desk/task/'+T.replace('TASK','%54ASK',1),'https://erp.abit.ai/desk/task/'+T+'/'):
            with self.subTest(url=url):
                raw=deepcopy(self.alias);raw['architect_plan']['metadata']['source_url']=url
                with self.assertRaises(ValueError):self.project(raw)
        raw=deepcopy(self.alias);raw['architect_plan']['metadata']['run_id']=self.bridge_module.stable_run_id('another-original')
        with self.assertRaises(ValueError):self.project(raw)
    def test_original_identity_packet_srs_and_server_run_drift_deny(self):
        for field in ('invocation_id','work_packet_digest','project_id','task_id','task_revision'):
            with self.subTest(field=field):
                raw=deepcopy(self.alias);raw[field]='wrong'
                with self.assertRaises(ValueError):self.project(raw)
        for field in ('agent_id','erp_user','project_id'):
            with self.subTest(actor=field):
                raw=deepcopy(self.alias);raw['actor'][field]='wrong'
                with self.assertRaises(ValueError):self.project(raw)
        raw=deepcopy(self.alias);raw['actor']['role']='developer';value,audit=self.project(raw);self.assertIsNone(audit);self.assertEqual(value,raw);self.assertTrue(self.validator.validate_result(value,self.packet_value))
        for field in ('srs_version','srs_sha256'):
            raw=deepcopy(self.alias);raw['architect_plan']['metadata'][field]='wrong'
            with self.assertRaises(ValueError):self.project(raw)
        with self.assertRaises(ValueError):self.project(row={**self.row,'automation_run_id':'another-run'})
        with self.assertRaises(ValueError):self.project(packet={**self.packet_value,'objective':'changed'})
        with self.assertRaises(ValueError):self.project(raw_reply_sha256='not-a-sha')
    def test_substantive_content_remains_unchanged_and_normal_validator_still_denies(self):
        raw=deepcopy(self.alias);raw['summary']='Untrusted changed substantive summary';raw['architect_plan']['tasks'][0]['depends_on']=[raw['architect_plan']['tasks'][0]['task_key']];before=deepcopy(raw)
        value,audit=self.project(raw)
        self.assertEqual(raw,before);self.assertEqual(value['summary'],raw['summary']);self.assertEqual(value['architect_plan']['tasks'],raw['architect_plan']['tasks']);self.assertEqual(value['evidence'],raw['evidence'])
        for field in ('source_url','run_id'):before['architect_plan']['metadata'][field]=value['architect_plan']['metadata'][field]
        self.assertEqual(value,before);self.assertTrue(self.validator.validate_result(value,self.packet_value))
    def test_atomic_audit_is_saved_once_with_the_same_local_result(self):
        value,audit=self.project();self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=audit,raw_reply=self.raw_reply);self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=deepcopy(audit),raw_reply=self.raw_reply)
        with self.g.connect(self.path)as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_local_result WHERE invocation_id=?',(self.ident,)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE kind='result_provenance_projection' AND invocation_id=?",(self.ident,)).fetchone()[0],1)
    def test_original_audit_and_raw_reply_conflicts_cannot_overwrite(self):
        value,audit=self.project();self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=audit,raw_reply=self.raw_reply)
        self.store.assert_original_reply(self.ident,self.row['automation_run_id'],self.raw_reply,self.path)
        with self.assertRaises(ValueError):self.store.assert_original_reply(self.ident,self.row['automation_run_id'],self.raw_reply+' ',self.path)
        changed_policy=deepcopy(audit);changed_policy['policy_sha256']='b'*64
        with self.assertRaises(ValueError):self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=changed_policy,raw_reply=self.raw_reply)
        changed=deepcopy(audit);changed['raw_reply_sha256']='b'*64
        with self.assertRaises(ValueError):self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=changed,raw_reply=self.raw_reply)
        with self.assertRaises(ValueError):self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=None,raw_reply=None)
        self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=audit,raw_reply=self.raw_reply)
    def test_audit_failure_rolls_back_local_result_in_same_transaction(self):
        value,audit=self.project()
        with self.g.connect(self.path)as db:
            self.store._schema(db);db.execute("CREATE TRIGGER fail_provenance BEFORE INSERT ON governance_event WHEN NEW.kind='result_provenance_projection' BEGIN SELECT RAISE(ABORT,'controlled audit fault'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=audit,raw_reply=self.raw_reply)
        with self.g.connect(self.path)as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_local_result WHERE invocation_id=?',(self.ident,)).fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM governance_event WHERE kind='result_provenance_projection' AND invocation_id=?",(self.ident,)).fetchone()[0],0)

    def test_published_original_receipt_settles_dead_owner_without_new_admission(self):
        maintenance=importlib.import_module('open_webui.utils.agentic_maintenance')
        admission_path=maintenance.path(self.path.parent)
        with maintenance._db(admission_path)as db:
            before=dict(db.execute("SELECT * FROM lease WHERE kind='governed' AND reference=?",(self.ident,)).fetchone())
            self.assertEqual(before['state'],'active')
            db.execute("UPDATE lease SET pid=?,process_start=? WHERE id=?",(2_000_000_000,'1',before['id']))
            count=db.execute('SELECT count(*) FROM lease').fetchone()[0]
        value,audit=self.project();self.store.save_local_result(self.ident,self.row['automation_run_id'],value,self.path,provenance=audit,raw_reply=self.raw_reply)
        event={'schema_version':1,'event_id':'agov-fixture-result-0001','type':'run.ready','invocation_id':self.ident,'run_id':self.row['automation_run_id'],'task_id':T,'project_id':P}
        secret='controlled-test-signature-only-never-sent-to-network-00000001';headers=self.bridge_module.signing_headers(event,secret);self.bridge_module.verify_event(event,headers,secret)
        self.store.save_result(self.ident,self.row['automation_run_id'],value,event,self.path)
        receipt={'event_id':event['event_id'],'task_id':T,'run_id':self.row['automation_run_id'],'durable':True,'modified':'controlled-positive-native-revision','status':'Pending Review'}
        self.store.publication_receipt(self.ident,receipt,self.path)
        with maintenance._db(admission_path)as db:
            after=dict(db.execute('SELECT * FROM lease WHERE id=?',(before['id'],)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM lease').fetchone()[0],count)
            self.assertEqual(after['state'],'finished');self.assertEqual(after['pid'],2_000_000_000);self.assertEqual(after['process_start'],'1');self.assertTrue(after['receipt_sha256'])
        self.assertFalse(maintenance.has_lease('governed',self.ident,db_path=admission_path))

if __name__=='__main__':unittest.main(verbosity=2)

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
from unittest.mock import AsyncMock, patch
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

    async def _recover_stopped_architect(self, *, aliases):
        """Run real recovery, provenance, validation, storage and gateway checks."""
        recovery = self.candidate('agentic_recovery')
        executor = self.candidate('agentic_executor')
        gateway = self.candidate('agentic_gateway_delivery')
        sessions = self.candidate('agentic_gateway_sessions')
        artifacts = importlib.import_module('open_webui.utils.agentic_artifacts')
        maintenance = importlib.import_module('open_webui.utils.agentic_maintenance')
        source_result = deepcopy(self.alias if aliases else self.canonical)
        original_bytes = ('AGOV RESULT: ' + json.dumps(source_result)).encode('utf-8')
        original_file = self.path.parent / 'saved-original-reply.txt'
        original_file.write_bytes(original_bytes)
        source_before = deepcopy(source_result)
        packet_before = deepcopy(self.packet_value)
        self.g.request_cancel(self.ident, 'manager@example.invalid', self.path)
        stopped = self.g.find(self.ident, self.path, include_private=True)
        self.assertEqual(stopped['state'], 'cancel_requested')
        identity_keys = ('invocation_id', 'automation_run_id', 'lease_token', 'lease_fence',
                         'agent_id', 'erp_project', 'erp_task', 'target_role', 'task_revision',
                         'packet_hash', 'work_packet', 'submitted_at', 'dispatch_attempts')
        identity = {key: stopped[key] for key in identity_keys}
        admission_path = maintenance.path(self.path.parent)
        with maintenance._db(admission_path) as db:
            admissions_before = [tuple(row) for row in db.execute('SELECT * FROM lease ORDER BY id')]
        with sessions.connect(self.path) as db:
            sessions_before = db.execute('SELECT count(*) FROM governance_gateway_session').fetchone()[0]
        automation = types.SimpleNamespace(id=stopped['agent_id'], user_id='owner-fixture', meta={
            'governance_managed': True, 'agentic_project': P,
            'agentic_role': 'solution_architect', 'erp_role_user': self.users['solution_architect'],
        })

        async def read_original(row, specialist):
            self.assertEqual({key: row[key] for key in identity_keys}, identity)
            self.assertIs(specialist, automation)
            return {'response_text': original_file.read_text()}

        adapter = types.SimpleNamespace(requires_governed_gateway=True, record=AsyncMock(),
            prepare=AsyncMock(), submit=AsyncMock(), scope_check=AsyncMock(), preflight=AsyncMock())
        bridge = types.SimpleNamespace(packet=AsyncMock(return_value=deepcopy(self.packet_value)),
            erp=types.SimpleNamespace(request=AsyncMock()), publish=AsyncMock())
        gate = AsyncMock(side_effect=AssertionError('Stopped recovery cannot authorize ERP writes'))
        snapshots = []
        with patch.object(recovery, 'FORMAT_ROOT', self.path.parent / 'absent-format-captures'), \
                patch.object(gateway, 'assert_gateway_result', wraps=gateway.assert_gateway_result) as acceptance, \
                patch.object(artifacts, 'publish_artifacts', new_callable=AsyncMock) as publish, \
                patch.object(executor, 'reconcile_publication', new_callable=AsyncMock) as reconcile, \
                patch.object(sessions, 'admit', new_callable=AsyncMock) as admit, \
                patch.object(sessions, 'call', new_callable=AsyncMock) as tool_call, \
                patch.object(maintenance, 'acquire') as acquire, \
                patch.object(self.g, 'handoff') as handoff:
            for _ in range(2):
                self.assertIsNone(await recovery.recover_original(
                    self.ident, config={}, project_gate=gate, bridge=bridge,
                    db_path=self.path, reader=read_original, adapter=adapter, automation=automation,
                ))
                current = self.g.find(self.ident, self.path, include_private=True)
                self.assertEqual(current['state'], 'cancel_requested')
                self.assertEqual({key: current[key] for key in identity_keys}, identity)
                self.assertEqual(original_file.read_bytes(), original_bytes)
                self.assertEqual(source_result, source_before)
                self.assertEqual(self.packet_value, packet_before)
                self.assertIsNone(self.store.get_result(self.ident, self.path))
                with self.g.connect(self.path) as db:
                    local = [tuple(row) for row in db.execute(
                        'SELECT * FROM governance_local_result WHERE invocation_id=?', (self.ident,))]
                    audits = [tuple(row) for row in db.execute(
                        'SELECT * FROM governance_event WHERE invocation_id=? AND kind=?',
                        (self.ident, self.provenance.AUDIT_KIND))]
                    self.assertEqual(db.execute('SELECT count(*) FROM governance_invocation').fetchone()[0], 1)
                    self.assertEqual(db.execute('SELECT count(*) FROM governance_result').fetchone()[0], 0)
                self.assertEqual(len(local), 1)
                self.assertEqual(local[0][1], identity['automation_run_id'])
                self.assertEqual(json.loads(local[0][2]), self.canonical)
                self.assertEqual(len(audits), 1 if aliases else 0)
                snapshots.append((local, audits))
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertEqual(acceptance.await_count, 2)  # Actual SA gateway identity checks ran.
            for blocked in (publish, reconcile, admit, tool_call):
                blocked.assert_not_awaited()
            acquire.assert_not_called()
            handoff.assert_not_called()
        self.assertEqual(adapter.record.await_count, 2)
        for call in adapter.record.await_args_list:
            self.assertEqual(call.args, (automation, identity['automation_run_id'],
                                        {'response_text': original_bytes.decode('utf-8')}))
        for name in ('prepare', 'submit', 'scope_check', 'preflight'):
            getattr(adapter, name).assert_not_awaited()
        gate.assert_not_awaited()
        bridge.erp.request.assert_not_awaited()
        bridge.publish.assert_not_awaited()
        with maintenance._db(admission_path) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM lease ORDER BY id')], admissions_before)
        with sessions.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_gateway_session').fetchone()[0], sessions_before)
            self.assertEqual(db.execute('SELECT count(*) FROM governance_gateway_call').fetchone()[0], 0)
        if aliases:
            with self.g.connect(self.path) as db:
                audit = json.loads(db.execute('SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
                    (self.ident, self.provenance.AUDIT_KIND)).fetchone()[0])
            self.assertEqual(audit['raw_reply_sha256'], self.provenance.reply_sha256(original_bytes.decode('utf-8')))
            self.assertEqual(audit['canonical_result_sha256'], self.bridge_module.digest(self.canonical))
            self.assertEqual({change['pointer'] for change in audit['changes']}, set(self.provenance.POLICY['pointers']))

    async def test_stop_recovers_canonical_sa_reply_locally_without_publication(self):
        await self._recover_stopped_architect(aliases=False)

    async def test_stop_recovers_exact_sa_aliases_once_without_publication(self):
        await self._recover_stopped_architect(aliases=True)

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

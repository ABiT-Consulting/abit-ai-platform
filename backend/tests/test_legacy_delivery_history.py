"""Historical legacy producer/receipt compatibility, without new authorization.

Real temporary ledgers and gateway calls; exact frozen legacy producers; no
network, native ERP, live service, model requests or historical-row rewrites.
"""
from copy import deepcopy
from pathlib import Path
import ast
import hashlib
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

import test_delivery_control_recovery as control_fixture

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / 'docs/evidence/2026-11-15-github-commit-readback-recovery-v1/runtime'


def functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names]
    assert len(body) == len(names)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class LegacyDeliveryHistory(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c = control_fixture.DeliveryControlRecovery('test_signed_task_retry_creates_one_audited_unit_then_finishes_original_delivery')
        self.addCleanup(self.c.doCleanups)
        await self.c.asyncSetUp()
        self.f, self.g, self.b, self.m = self.c.f, self.c.g, self.c.b, self.c.m
        self.path, self.policy = self.c.path, self.c.policy
        self.source, self.packet = self.c.source, deepcopy(self.c.fresh)
        self.operator = self.c.operator_proof()
        self.operator.update(control_task='', minimum_control_sequence=10,
                             delivery_source_sha256=self.policy.LEGACY_PRODUCERS['agentic_gateway_delivery.py'])
        self.operator_raw = self.b.canonical(self.operator).encode()
        self.operator_sha = hashlib.sha256(self.operator_raw).hexdigest()
        event = self.c.make_event(sequence=11, task=False)
        event['revision'] = '2026-11-14 17:46:01.936937'
        event['source_modified'] = event['revision']
        event['payload'].update(expected_revision=event['revision'], target_role='project_manager')
        self.command = await self.c.event(event)
        clock = patch.object(self.g.time, 'time', return_value=self.c.published_at + 5)
        clock.start()
        self.addCleanup(clock.stop)
        namespace = dict(self.m.__dict__)
        namespace.update(__file__=str(LEGACY/'agentic_gateway_delivery.py'),
                         _CONTINUATION_METADATA={'packet_id','content_sha256','source_modified','attachments','project_backlog'},
                         _read_delivery_recovery_authorization=lambda row:(deepcopy(self.operator),self.operator_sha))
        self.legacy = functions(LEGACY/'agentic_gateway_delivery.py', {
            'original_delivery', '_continuation_payload', '_continuation_evidence',
            '_record_continuation', '_resume_delivery', 'authorize_delivery_recovery'}, namespace)
        old_resume = functions(LEGACY/'agentic_orchestrator.py', {'_resume'}, dict(self.c.orchestrator.__dict__))['_resume']
        with patch.object(self.m, 'authorize_delivery_recovery', self.legacy['authorize_delivery_recovery']):
            child, created = old_resume(self.source,self.packet,self.f.gate_value,self.path,[],'delivery',delivery_config=self.f.config)
        self.assertTrue(created)
        run=self.b.stable_run_id(child['invocation_id'])
        row=self.g.claim_invocation(child['invocation_id'],run,gate=self.f.gate_value,db_path=self.path)
        self.start=self.f.load('agentic_start_recovery')
        event={'schema_version':1,'event_id':'agov-start-'+self.b.digest(row['invocation_id'])[:48],
               'type':'run.started','project_id':row['erp_project'],'task_id':row['erp_task'],
               'task_revision':self.packet['task_revision'],'run_id':run,'invocation_id':row['invocation_id'],
               'role':'developer','agent_id':row['agent_id'],'work_packet_digest':self.b.packet_digest(self.packet),
               'expected_modified':self.packet['source_modified'],'expected_project_revision':self.packet['project_revision'],
               'artifacts':[]}
        self.start.record_start_intent(row,event,self.path)
        self.start.record_start_receipt(row['invocation_id'],{
            'event_id':event['event_id'],'task_id':row['erp_task'],'run_id':run,'modified':self.packet['source_modified'],
            'status':'Working','durable':True},self.path)
        self.g.mark_submitted(row['invocation_id'],run,row['lease_token'],gate=self.f.gate_value,db_path=self.path)
        self.row=self.g.find(row['invocation_id'],self.path,include_private=True)
        self.c.c.current,self.c.c.current_packet=self.row,self.packet
        self.f.row,self.f.packet_value=self.row,self.packet
        self.gateway=await self.c.c.open_gateway()
        operation=self.gateway['kwargs']['registry']['repository.deliver']
        async def handler(args,**kwargs):
            owner=self.legacy['original_delivery'](kwargs['row'],kwargs['packet'],kwargs['config'],kwargs['db_path'])
            return await self.legacy['_resume_delivery'](args,owner=owner,**{
                key:kwargs[key] for key in ('row','packet','config','db_path','bridge','check_authority')})
        self.gateway['kwargs']['registry']['repository.deliver']=self.f.sessions.Operation(
            operation.capability,operation.roles,handler,operation.read_only,True,operation.validate_arguments)
        self.response=await self.c.c.gateway_call(self.gateway,'repository.deliver',
            {'test_call_ids':['fixture-test-call-001']},'fixture-legacy-completed-delivery')
        self.gateway['kwargs']['registry']['repository.deliver']=operation
        self.assertEqual(self.response['result']['state'],'ready_for_review')
        self.objects={}
        def save(raw):
            sha=hashlib.sha256(raw).hexdigest();self.objects[sha+'.bin']=raw;return sha
        save(self.operator_raw)
        proof={'project_id':self.source['erp_project'],'task_id':'',
               'command_id':self.command['payload']['command_id'],'control_sequence':11,'action':'retry'}
        native={'message':{**self.policy.expected_ack(proof),'exists':True,'durable':True}}
        native_sha=save(self.b.canonical(native).encode())
        deployment={'schema_version':1,'purpose':'preserved-legacy-deployment.v1',
                    'source_commit':self.policy.LEGACY_SOURCE_COMMIT,
                    'producer_sha256':self.policy.LEGACY_PRODUCERS,
                    'records':{name:{'sha256':str(index)*64,'mtime_ns':(self.c.published_at-1)*1_000_000_000}
                               for index,name in enumerate(('complete71-after.json','completed-receipt.json','intent.json'),1)},
                    'observed_at':'2026-11-15T10:00:00+00:00',
                    'provenance':'Read-only projection of retained pre-execution deployment records.'}
        deployment_sha=save(self.b.canonical(deployment).encode())
        for name,sha in self.policy.LEGACY_PRODUCERS.items():
            self.assertEqual(hashlib.sha256((LEGACY/name).read_bytes()).hexdigest(),sha)
        with self.g.connect(self.path) as db:
            context=self.policy.legacy_evidence_context(db,self.row)
            facts,_=self.policy.legacy_facts(db,context,self.operator,native,deployment)
        self.index={'schema_version':1,'purpose':self.policy.HISTORY_PURPOSE,'authorization_sha256':self.operator_sha,
                    'native_ack_sha256':native_sha,'deployment_sha256':deployment_sha,
                    'producer_sha256':self.policy.LEGACY_PRODUCERS,'bindings':facts,
                    'retained_at':'2026-11-15T10:00:00+00:00',
                    'provenance':'Retrospective preservation of existing evidence; no new approval or transition.'}
        self.index_name=self.operator_sha+'.history.json'
        self.objects[self.index_name]=self.b.canonical(self.index).encode()
        reader=patch.object(self.policy,'_history_file',side_effect=lambda name,limit=262144:self.objects[name])
        reader.start();self.addCleanup(reader.stop)
        self.continuation=self.c.c.continuation
        baseline=os.environ.get('AGOV_LEGACY_BASELINE_HELPER')
        if baseline:
            namespace=dict(self.continuation.__dict__)
            old=functions(Path(baseline),{'derive'},namespace)['derive']
            swap=patch.object(self.continuation,'derive',old);swap.start();self.addCleanup(swap.stop)

    def owner(self):
        return self.m.original_delivery(self.row,self.packet,self.f.config,self.path)

    def ledger_bytes(self):
        with self.g.connect(self.path) as db:
            return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in ('governance_invocation','governance_result','governance_developer_delivery',
                                  'governance_delivery_step','governance_event','governance_gateway_call')}

    async def test_exact_legacy_cached_call_readiness_and_independent_QA_preserve_all_history(self):
        before=self.ledger_bytes();posts=self.f.remote.posts().copy()
        replay=await self.c.c.gateway_call(self.gateway,'repository.deliver',
            {'test_call_ids':['fixture-test-call-001']},'fixture-legacy-completed-delivery')
        self.assertTrue(replay['replayed']);self.assertEqual(replay['result'],self.response['result'])
        fresh=await self.c.c.gateway_call(self.gateway,'repository.deliver',
            {'test_call_ids':['fixture-test-call-001']},'fixture-legacy-new-call-id')
        self.assertFalse(fresh['replayed']);self.assertEqual(fresh['result'],self.response['result'])
        status=await self.m.reconcile_delivery({},row=self.row,packet=self.packet,config=self.f.config,
                    db_path=self.path,check_authority=self.c.c.authority)
        self.assertEqual(status,self.response['result'])
        ready=await self.m.developer_deliver({'test_call_ids':['fixture-test-call-001']},
            row=self.row,packet=self.packet,config=self.f.config,db_path=self.path,check_authority=self.c.c.authority)
        self.assertEqual(ready,self.response['result'])
        await self.m.assert_gateway_result(self.row,self.packet,self.c.c.contract(ready),self.f.config,self.path)
        after=self.ledger_bytes()
        for table,rows in before.items():
            if table=='governance_gateway_call':
                self.assertTrue(all(row in after[table] for row in rows))
                self.assertEqual(len(after[table]),len(rows)+1)
            else:self.assertEqual(after[table],rows)
        self.assertEqual(self.f.remote.posts(),posts)
        from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest
        qa,packet=self.f.qa_context(ready['delivery'])
        facts=await self.m.qa_verify_delivery({},row=qa,packet=packet,config=self.f.config,db_path=self.path)
        self.assertFalse(facts['tests_complete'])
        result={'outcome':'pass','reviewed_delivery_sha256':delivery_digest(ready['delivery']),'test_results':[],'evidence':[]}
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(qa,packet,result,self.f.config,self.path)
        await self.f.seed_qa_test(qa,packet)
        facts=await self.m.qa_verify_delivery({},row=qa,packet=packet,config=self.f.config,db_path=self.path)
        result.update(test_results=facts['test_results'],evidence=facts['evidence'])
        await self.m.assert_gateway_result(qa,packet,result,self.f.config,self.path)
        self.assertEqual(self.f.remote.posts(),posts)

    async def test_missing_or_drifted_history_objects_do_not_accept_legacy_markers(self):
        original=deepcopy(self.objects)
        for name in list(original):
            with self.subTest(object=name):
                self.objects.pop(name)
                with self.assertRaises((ValueError,KeyError,FileNotFoundError)):self.owner()
                self.objects=deepcopy(original)
                self.objects[name]=original[name]+b' '
                if name==self.index_name:
                    altered=deepcopy(self.index);altered['bindings']['original_input_sha256']='0'*64
                    self.objects[name]=self.b.canonical(altered).encode()
                with self.assertRaises(ValueError):self.owner()
                self.objects=deepcopy(original)

    def copy_ledger(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        with self.g.connect(self.path) as original:original.backup(db)
        self.addCleanup(db.close)
        return db

    async def test_third_marker_and_second_authorization_are_denied(self):
        for kind in ('delivery_checkpoint_resumed','delivery_checkpoint_authorized'):
            with self.subTest(kind=kind):
                db=self.copy_ledger()
                audit=dict(db.execute('SELECT * FROM governance_event WHERE invocation_id=? AND kind=?',
                                      (self.row['invocation_id'],kind)).fetchone())
                self.g._event(db,self.row['erp_project'],self.row['invocation_id'],kind,audit['actor'],json.loads(audit['payload']))
                with self.assertRaises(ValueError):
                    self.continuation.derive(self.row,self.packet,db=db,require_active=False)

    async def test_legacy_binding_call_and_signed_ingress_drift_are_denied(self):
        mutations=[
            ("UPDATE governance_gateway_call SET request_hash=? WHERE operation='repository.deliver'",('0'*64,)),
            ("UPDATE governance_gateway_session SET invocation_id=? WHERE invocation_id=?",('foreign-invocation',self.row['invocation_id'])),
            ("UPDATE governance_inbox SET event_id='different-inbox-key' WHERE event_id=?",(self.command['event_id'],)),
            ("UPDATE governance_event SET actor='foreign' WHERE kind='erp_event_received'",()),
            ("UPDATE governance_event SET actor='foreign' WHERE kind='delivery_checkpoint_authorized'",()),
            ("UPDATE governance_event SET payload='{}' WHERE kind='delivery_continuation'",()),
            ("UPDATE governance_start SET receipt_json='{}' WHERE invocation_id=?",(self.row['invocation_id'],)),
        ]
        for query,args in mutations:
            with self.subTest(query=query):
                db=self.copy_ledger()
                # The ordinary ledger rejects audit UPDATE. Independently test
                # the reader against a corrupted backup, without disabling any
                # trigger or altering a row in the original fixture ledger.
                if 'UPDATE governance_event' in query:
                    with self.assertRaises(sqlite3.IntegrityError):db.execute(query,args)
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='governance_event'").fetchall():
                        db.execute('DROP TRIGGER "'+row[0].replace('"','""')+'"')
                db.execute(query,args)
                with self.assertRaises((ValueError,KeyError,TypeError)):
                    context=self.policy.legacy_evidence_context(db,self.row)
                    self.policy.legacy_marker_proof(db,context)

    async def test_rehashed_native_or_deployment_object_cannot_change_its_meaning(self):
        original=deepcopy(self.objects)
        cases=[('native_ack_sha256',lambda value:value['message'].__setitem__('task_id','foreign-task')),
               ('native_ack_sha256',lambda value:value['message'].__setitem__('control_sequence',12)),
               ('deployment_sha256',lambda value:value['producer_sha256'].__setitem__('agentic_orchestrator.py','0'*64)),
               ('deployment_sha256',lambda value:value['records']['intent.json'].__setitem__('mtime_ns',2**63-1))]
        for key,mutate in cases:
            with self.subTest(object=key):
                index=deepcopy(self.index)
                value=json.loads(original[index[key]+'.bin']);mutate(value)
                raw=self.b.canonical(value).encode();new_sha=hashlib.sha256(raw).hexdigest()
                self.objects=deepcopy(original);self.objects[new_sha+'.bin']=raw
                index[key]=new_sha;self.objects[self.index_name]=self.b.canonical(index).encode()
                with self.assertRaises(ValueError):self.owner()
        self.objects=original

    async def test_newer_cursor_and_current_opt_in_do_not_rewrite_historical_authority(self):
        before=self.owner()['continuation']['core']
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_native_control SET sequence=999,command_id='later-stop',action='stop',phase='acknowledged'")
            db.execute("UPDATE governance_gateway_session SET state='finished' WHERE invocation_id=?",(self.row['invocation_id'],))
        self.c.proof=None
        self.assertEqual(self.owner()['continuation']['core'],before)
        same, created = self.c.orchestrator._resume(self.source,self.packet,self.f.gate_value,self.path,[],'delivery',delivery_config=self.f.config)
        self.assertFalse(created);self.assertEqual(same['invocation_id'], self.row['invocation_id'])

    def publish(self, row, packet, result, kind, status):
        event={'schema_version':1,'event_id':'agov-result-'+self.b.digest([row['invocation_id'],result])[:48],
               'type':kind,'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':packet['task_revision'],
               'run_id':row['automation_run_id'],'invocation_id':row['invocation_id'],'role':row['target_role'],
               'agent_id':row['agent_id'],'work_packet_digest':packet['content_sha256'],
               'result_contract':result,'artifacts':[],'acceptance_passed':False}
        self.c.c.store.save_result(row['invocation_id'],row['automation_run_id'],result,event,self.path)
        self.g.finish_execution(row['invocation_id'],row['automation_run_id'],row['lease_token'],'execution_finished',db_path=self.path)
        self.c.c.store.publication_receipt(row['invocation_id'],{
            'event_id':event['event_id'],'task_id':row['erp_task'],'run_id':row['automation_run_id'],
            'modified':self.packet['source_modified'],'status':status,'durable':True},self.path)
        return self.g.find(row['invocation_id'],self.path,include_private=True)

    async def test_legacy_authorization_cannot_reset_generic_or_new_task_control_budget(self):
        result=deepcopy(self.c.result)
        result.update(invocation_id=self.row['invocation_id'],actor=self.packet['binding'],
                      work_packet_digest=self.packet['content_sha256'])
        source=self.publish(self.row,self.packet,result,'run.blocked','Blocked')
        with self.g.connect(self.path) as db:
            with self.assertRaisesRegex(ValueError,'consumed generic recovery marker'):
                self.policy.original_context(db,source)
        before=self.ledger_bytes()
        with self.assertRaises(ValueError):
            self.c.orchestrator._resume(source,self.packet,self.f.gate_value,self.path,[],
                                        'delivery',delivery_config=self.f.config)
        self.assertEqual(self.ledger_bytes(),before)

    async def test_published_QA_repair_after_legacy_starts_fresh_independent_delivery(self):
        import base64
        ready=self.response['result']
        self.publish(self.row,self.packet,self.c.c.contract(ready),'run.ready','Pending Review')
        qa_packet=self.f.packet(role='qa')
        qa_packet.update(implementation_run_id=self.row['automation_run_id'],implementation_delivery=ready['delivery'])
        qa_packet['content_sha256']=self.b.packet_digest(qa_packet)
        qa_id='legacy-independent-qa-rejection'
        self.g.handoff(source_invocation_id=self.row['invocation_id'],source_run_id=self.row['automation_run_id'],
            source_lease_token=self.row['lease_token'],invocation_id=qa_id,project=self.row['erp_project'],
            task=self.row['erp_task'],from_role='developer',target_role='qa',task_revision=qa_packet['task_revision'],
            context='Independent QA correction boundary',work_packet=qa_packet,gate=self.f.gate_value,
            actor='server-fixture',db_path=self.path)
        run=self.b.stable_run_id(qa_id)
        qa=self.g.claim_invocation(qa_id,run,gate=self.f.gate_value,db_path=self.path)
        self.g.mark_submitted(qa_id,run,qa['lease_token'],gate=self.f.gate_value,db_path=self.path)
        qa=self.g.find(qa_id,self.path,include_private=True)
        artifact=await self.f.ops.artifact_write({'name':'legacy-qa-defect.txt',
            'content_base64':base64.b64encode(b'Controlled independently observed defect').decode()},row=qa,config=self.f.config)
        evidence={'id':'EV-QA-DEFECT','kind':'test_log','uri':artifact['uri'],'sha256':artifact['sha256'],
                  'requirement_ids':['REQ-ONE']}
        failure={'schema_version':1,'invocation_id':qa_id,'actor':qa_packet['binding'],
                 'project_id':qa['erp_project'],'task_id':qa['erp_task'],'task_revision':qa_packet['task_revision'],
                 'work_packet_digest':qa_packet['content_sha256'],'outcome':'fail',
                 'summary':'Independent actual fixture defect requires fresh correction.',
                 'test_results':[{'test_id':qa_packet['acceptance_tests'][0]['id'],'status':'fail',
                                  'observed':'Controlled mismatch','evidence_ids':['EV-QA-DEFECT']}],
                 'evidence':[evidence],'implementation_invocation_id':self.row['invocation_id'],
                 'implementation_agent_id':self.row['agent_id'],
                 'defects':[{'id':'DEFECT-LEGACY-QA','requirement_ids':['REQ-ONE'],'reproduction':'Inspect fixture',
                             'expected':'Specified behavior','observed':'Controlled mismatch','evidence_ids':['EV-QA-DEFECT']}]}
        from open_webui.utils.agentic_contracts.validator import validate_result
        self.assertEqual(validate_result(failure,qa_packet),[])
        self.publish(qa,qa_packet,failure,'qa.rejected','QA REJECTED')
        ident='legacy-new-independent-correction'
        self.g.handoff(source_invocation_id=qa_id,source_run_id=run,source_lease_token=qa['lease_token'],
            invocation_id=ident,project=qa['erp_project'],task=qa['erp_task'],from_role='qa',target_role='developer',
            task_revision=self.packet['task_revision'],context='Fix the independently verified defect',
            work_packet=self.packet,gate=self.f.gate_value,actor='server-fixture',db_path=self.path)
        run=self.b.stable_run_id(ident)
        current=self.g.claim_invocation(ident,run,gate=self.f.gate_value,db_path=self.path)
        self.g.mark_submitted(ident,run,current['lease_token'],gate=self.f.gate_value,db_path=self.path)
        current=self.g.find(ident,self.path,include_private=True)
        self.assertIsNone(self.m.original_delivery(current,self.packet,self.f.config,self.path))
        posts=self.f.remote.posts().copy()
        with self.assertRaises(ValueError):
            await self.m.developer_deliver({'test_call_ids':['fixture-test-call-001']},
                row=current,packet=self.packet,config=self.f.config,db_path=self.path)
        self.assertEqual(self.f.remote.posts(),posts)
        self.f.row,self.f.packet_value=current,self.packet
        (self.f.repo/'src/app.py').write_bytes(b'print("fresh independently tested correction")\n')
        with self.f.sessions.connect(self.path) as db:
            db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',
                ('legacy-new-correction-session',ident,'{}','{}','a'*64,'b'*64,'admitted',1))
        await self.f.seed_test('legacy-new-correction-test')
        with self.f.sessions.connect(self.path) as db:
            db.execute("UPDATE governance_gateway_call SET session_id='legacy-new-correction-session' WHERE call_id='legacy-new-correction-test'")
        corrected=await self.m.developer_deliver({'test_call_ids':['legacy-new-correction-test']},
            row=current,packet=self.packet,config=self.f.config,db_path=self.path)
        self.assertEqual(corrected['state'],'ready_for_review')
        self.assertEqual(corrected['invocation_id'],ident)
        self.assertNotIn('original_invocation_id',corrected)

    async def test_current_authority_revocation_and_copied_evidence_tamper_still_deny(self):
        posts=self.f.remote.posts().copy()
        self.c.c.revoke_next=True
        with self.assertRaises(ValueError):
            await self.c.c.resume()
        self.c.c.revoke_next=False
        evidence=self.response['result']['evidence'][0]
        name,_=self.m.local_bytes(self.m.artifact_directory(self.row,self.f.config),evidence['uri'])
        (self.m.artifact_directory(self.row,self.f.config)/name).write_bytes(b'Changed copied evidence')
        with self.assertRaises(ValueError):
            await self.m.assert_gateway_result(self.row,self.packet,self.c.c.contract(self.response['result']),self.f.config,self.path)
        self.assertEqual(self.f.remote.posts(),posts)


    async def test_preparer_rederives_original_call_read_only_after_new_gateway_call(self):
        import importlib.util
        import sys
        import tempfile
        spec=importlib.util.spec_from_file_location('legacy_retainer_actual_fixture',ROOT/'scripts/prepare_delivery_history.py')
        preparer=importlib.util.module_from_spec(spec);spec.loader.exec_module(preparer)
        await self.c.c.gateway_call(self.gateway,'repository.deliver',
            {'test_call_ids':['fixture-test-call-001']},'fixture-preparer-new-call')
        before=self.ledger_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            for name,raw in self.objects.items():(directory/name).write_bytes(raw)
            index_sha=hashlib.sha256(self.objects[self.index_name]).hexdigest()
            # The preparer imports only selected pure definitions into an
            # isolated namespace; no app import/schema/connect helper runs.
            with patch.dict(sys.modules):
                policy,continuation=preparer.load_policy()
                name,raw,objects=preparer.validate_proposal(directory,index_sha,self.path,policy,continuation)
                self.assertEqual(name,self.index_name)
                self.assertEqual(raw,self.objects[self.index_name])
                self.assertEqual(set(objects),set(self.objects)-{self.index_name})
                with self.assertRaises(ValueError):
                    preparer.validate_proposal(directory,'0'*64,self.path,policy,continuation)
                (directory/'unexpected.json').write_text('{}')
                with self.assertRaises(ValueError):
                    preparer.validate_proposal(directory,index_sha,self.path,policy,continuation)
        self.assertEqual(before,self.ledger_bytes())


if __name__=='__main__':unittest.main()

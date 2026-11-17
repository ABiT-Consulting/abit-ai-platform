"""Actual phase/store/publication/planning, controlled ERP, real temporary ledger.
No live source/ERP/site/model/service actions or copied private reply fixture.
"""
from __future__ import annotations
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
TESTS=BACKEND/'tests';ROOT=BACKEND/'open_webui/utils';sys.path.insert(0,str(TESTS))
import test_agentic_architecture_review as phase_fixture
from test_agentic_planning import ERP


class PlanningFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reuse fixture setup, not its test methods or duplicate suite.
        self.f=phase_fixture.ArchitectureReview('test_exact_published_chain_returns_opaque_immutable_phase_without_writes');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.g=self.f.g;self.path=self.f.path;self.b=self.f.bridge_module
        self.store=self.f.candidate('agentic_result_store');self.planning=self.f.candidate('agentic_planning');self.executor=self.f.candidate('agentic_executor')
        source_path=ROOT/'agentic_contracts/validator.py';candidate=HERE/'agentic_contracts/validator.py'
        if not candidate.is_file():candidate=source_path
        name='open_webui.utils.agentic_contracts.validator';validator=types.ModuleType(name);validator.__file__=str(source_path);sys.modules[name]=validator;exec(compile(candidate.read_bytes(),str(source_path),'exec'),validator.__dict__);self.validator=validator
        # Same actual JSON parser function; avoid unrelated backend model imports.
        workflow=types.ModuleType('open_webui.utils.erp_dot_workflow');workflow.json=json
        tree=ast.parse((ROOT/'erp_dot_workflow.py').read_bytes());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parse_contract_json');exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/'erp_dot_workflow.py'),'exec'),workflow.__dict__)
        parser_patch=patch.dict(sys.modules,{'open_webui.utils.erp_dot_workflow':workflow});parser_patch.start();self.addCleanup(parser_patch.stop)
        # Complete the controlled source fixture's original evidence, matching
        # its already-declared published files, before deriving any PM audit.
        saved=self.store.get_result(self.f.sa,self.path);source=deepcopy(saved['result']);plan=source['architect_plan'];source['evidence']=[]
        for kind,items in (('design',plan['designs']),('mockup',plan['mockups'])):
            for i,item in enumerate(items):source['evidence'].append({'id':'EV-'+kind+str(i),'kind':kind,'uri':item['artifact']['uri'],'sha256':item['artifact']['sha256'],'requirement_ids':item['requirement_ids']})
        event={**saved['event'],'result_contract':source}
        with self.g.connect(self.path) as db:db.execute('UPDATE governance_result SET result_json=?,result_sha256=?,event_json=? WHERE invocation_id=?',(self.b.canonical(source),self.b.digest(source),self.b.canonical(event),self.f.sa))
        self.source=source;self.raw='AGOV RESULT: '+self.b.canonical(self.f.result);self.f.raw_sha=hashlib.sha256(self.raw.encode()).hexdigest();self.phase=self.f.derive()
        self.assertEqual(self.validator.validate_result(self.f.result,self.f.packet_value,architecture_review=self.phase),[])
        self.erp=ERP()

    def publish(self):
        f=self.f;run=f.row['automation_run_id'];self.store.save_local_result(f.pm,run,f.result,self.path,raw_reply=self.raw,architecture_review=self.phase)
        # Exact second read saves neither another local result nor another audit.
        self.store.save_local_result(f.pm,run,deepcopy(f.result),self.path,raw_reply=self.raw,architecture_review=self.phase)
        artifact={'id':'EV-REVIEW','kind':'checkpoint','url':'https://erp.abit.ai/private/files/architecture-review.txt?fid=review-file','sha256':f.result['evidence'][0]['sha256'],'requirement_ids':f.result['evidence'][0]['requirement_ids']}
        event=self.executor.result_event(f.row,f.packet_value,f.result,run,[artifact],'fixture-native-before',architecture_review=self.phase)
        self.assertEqual(event['type'],'run.ready');self.assertIs(event['acceptance_passed'],False);self.assertEqual(event['result_contract']['outcome'],'reviewed')
        self.store.save_result(f.pm,run,f.result,event,self.path);self.g.finish_execution(f.pm,run,f.row['lease_token'],'execution_finished',db_path=self.path)
        receipt={'event_id':event['event_id'],'task_id':f.row['erp_task'],'status':'Pending Review','modified':'fixture-native-after-pm-review','run_id':run,'durable':True};self.store.publication_receipt(f.pm,receipt,self.path)
        self.pm=dict(self.g.find(f.pm,self.path,include_private=True));self.arch=dict(self.g.find(f.sa,self.path,include_private=True))
        self.assertEqual(self.pm['state'],'execution_finished');self.assertEqual(self.store.get_result(f.pm,self.path)['phase'],'published')
        return event

    async def materialize(self,**changes):
        return await self.planning.materialize_backlog(self.erp,changes.get('arch',self.arch),changes.get('source',self.source),changes.get('pm',self.pm),self.f.packet_value,self.path)

    async def test_actual_saved_phase_and_published_review_materialize_exact_backlog_once(self):
        event=self.publish();source_before=self.store.get_result(self.f.sa,self.path)
        actual=await self.materialize();self.assertEqual(len(actual),2);self.assertEqual(len(self.erp.calls),2);self.assertEqual(len(self.erp.tasks),2)
        self.assertEqual(await self.materialize(),actual);self.assertEqual(len(self.erp.calls),2);self.assertEqual(self.store.get_result(self.f.sa,self.path),source_before)
        self.assertTrue(all(x['source_run_id']==self.pm['automation_run_id'] and x['source_invocation_id']==self.pm['invocation_id'] for x in self.erp.calls))
        self.assertFalse(event['acceptance_passed']);self.assertEqual(self.g.find(self.f.pm,self.path)['state'],'execution_finished')
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_local_result WHERE invocation_id=?',(self.f.pm,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM governance_event WHERE invocation_id=? AND kind=?',(self.f.pm,self.f.phase.AUDIT_KIND)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM governance_backlog_plan').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM governance_backlog_task WHERE phase='confirmed'").fetchone()[0],2)

    async def test_missing_or_changed_published_architect_artifact_holds_before_task_creation(self):
        self.publish();saved=self.store.get_result(self.f.sa,self.path);before=self.b.canonical(saved['event'])
        for artifacts in ([],[{**saved['event']['artifacts'][0],'sha256':'f'*64},*saved['event']['artifacts'][1:]]):
            with self.subTest(artifacts=len(artifacts)):
                with self.g.connect(self.path) as db:db.execute('UPDATE governance_result SET event_json=? WHERE invocation_id=?',(self.b.canonical({**saved['event'],'artifacts':artifacts}),self.f.sa))
                with self.assertRaises(ValueError):await self.materialize()
                self.assertEqual(self.erp.calls,[]);self.assertEqual(self.erp.tasks,{})
        with self.g.connect(self.path) as db:db.execute('UPDATE governance_result SET event_json=? WHERE invocation_id=?',(before,self.f.sa))
        with self.assertRaises(ValueError):await self.materialize(arch={**self.arch,'invocation_id':'foreign-source'})
        self.assertEqual(self.erp.calls,[])
        with self.assertRaises(ValueError):await self.materialize(pm={**self.pm,'automation_run_id':'foreign-run'})
        self.assertEqual(self.erp.calls,[])

    async def test_lost_creation_ack_reconciles_same_original_without_duplicate(self):
        self.publish();self.erp.lose_ack=True
        with self.assertRaises(TimeoutError):await self.materialize()
        self.assertEqual(len(self.erp.calls),1);self.assertEqual(len(self.erp.tasks),1)
        result=await self.materialize();self.assertEqual(len(result),2);self.assertEqual(len(self.erp.calls),2);self.assertEqual(len(self.erp.tasks),2)
        self.assertEqual(await self.materialize(),result);self.assertEqual(len(self.erp.calls),2)

    def test_store_rederives_exact_phase_atomically_and_rejects_changed_raw_reply(self):
        f=self.f
        with self.g.connect(self.path) as db:
            self.store._schema(db);db.execute("CREATE TRIGGER fail_architecture_phase BEFORE INSERT ON governance_event WHEN NEW.kind='architecture_review_phase' BEGIN SELECT RAISE(ABORT,'controlled audit fault'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.store.save_local_result(f.pm,f.row['automation_run_id'],f.result,self.path,raw_reply=self.raw,architecture_review=self.phase)
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM governance_local_result WHERE invocation_id=?',(f.pm,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM governance_event WHERE invocation_id=? AND kind=?',(f.pm,f.phase.AUDIT_KIND)).fetchone()[0],0);db.execute('DROP TRIGGER fail_architecture_phase')
        self.store.save_local_result(f.pm,f.row['automation_run_id'],f.result,self.path,raw_reply=self.raw,architecture_review=self.phase)
        self.store.assert_original_reply(f.pm,f.row['automation_run_id'],self.raw,self.path)
        with self.assertRaises(ValueError):self.store.assert_original_reply(f.pm,f.row['automation_run_id'],self.raw+' ',self.path)
        with self.assertRaises(ValueError):self.store.save_local_result(f.pm,f.row['automation_run_id'],f.result,self.path,raw_reply=self.raw+' ',architecture_review=self.phase)
        with self.assertRaises(ValueError):self.store.save_local_result(f.pm,f.row['automation_run_id'],f.result,self.path,raw_reply=self.raw,architecture_review=self.phase.audit)

if __name__=='__main__':unittest.main()

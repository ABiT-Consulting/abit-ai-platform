"""Phase integration: real validator/store/byte checks, controlled ERP boundary."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, patch
import unittest

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
UTILS=BACKEND/'open_webui/utils'
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(BACKEND/'tests'))
import test_agentic_architecture_review as fixtures
from test_agentic_controls_events import P,T

def candidate(name):
    p=HERE/(name.replace('.','/')+'.py')
    return p if p.is_file() else UTILS/(name.replace('.','/')+'.py')

class Boundaries(fixtures.ArchitectureReview):
    # Reuse the actual ledger fixture without rerunning its independent cases.
    for _name in tuple(vars(fixtures.ArchitectureReview)):
        if _name.startswith('test_'):
            locals()[_name]=None

    def setUp(self):
        super().setUp()
        self.store=self.candidate('agentic_result_store')
        self.executor=self.candidate('agentic_executor')
        name='open_webui.utils.agentic_contracts.validator'
        v=types.ModuleType(name);v.__file__=str(UTILS/'agentic_contracts/validator.py')
        v.__package__='open_webui.utils.agentic_contracts'
        sys.modules[name]=v
        exec(compile(candidate('agentic_contracts.validator').read_bytes(),str(candidate('agentic_contracts.validator')),'exec'),v.__dict__)
        self.validator=v

    def test_strict_default_and_opaque_phase_keep_original_verdict_and_native_event_shape(self):
        original=deepcopy(self.result);phase=self.derive()
        self.assertTrue(self.validator.validate_result(self.result,self.packet_value))
        self.assertEqual(self.validator.validate_result(self.result,self.packet_value,architecture_review=phase),[])
        self.assertTrue(self.validator.validate_result(self.result,self.packet_value,architecture_review=True))
        self.assertTrue(self.validator.validate_result(self.result,self.packet_value,architecture_review=phase.audit))
        event=self.executor.result_event(self.row,self.packet_value,self.result,self.row['automation_run_id'],[],
                                         'fixture-current-modified',architecture_review=phase)
        self.assertEqual(event['type'],'run.ready');self.assertIs(event['acceptance_passed'],False)
        self.assertEqual(event['result_contract'],original);self.assertEqual(self.result,original)
        # No native schema additions: compare exact baseline event keys.
        baseline=candidate('agentic_executor').with_suffix('.py.before')
        if not baseline.is_file():
            baseline=BACKEND.parent/'docs/evidence/2026-11-15-pm-architecture-review-v1/baseline/agentic_executor.py'
        tree=ast.parse(baseline.read_bytes());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='result_event')
        ns={'OUTCOMES':self.executor.OUTCOMES,'digest':self.b.digest,'packet_digest':self.b.packet_digest}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_baseline_event','exec'),ns)
        old=ns['result_event'](self.row,self.packet_value,self.result,self.row['automation_run_id'],[],'fixture-current-modified')
        self.assertEqual(set(event),set(old))
        self.assertEqual({k:v for k,v in event.items()if k!='type'},{k:v for k,v in old.items()if k!='type'})

    async def _complete_with_artifact(self, content):
        raw='AGOV RESULT: '+json.dumps(self.result)
        adapter=types.SimpleNamespace(requires_governed_gateway=True,record=AsyncMock())
        # Execute the unchanged actual PM identity acceptance hook, not a fake
        # permissive gateway flag; no Developer/QA branch is invoked for PM.
        delivery_raw=(UTILS/'agentic_gateway_delivery.py').read_bytes()
        node=next(n for n in ast.parse(delivery_raw).body if isinstance(n,ast.AsyncFunctionDef)and n.name=='_assert_gateway_result')
        ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_PM_gateway_identity','exec'),ns)
        delivery=types.ModuleType('open_webui.utils.agentic_gateway_delivery')
        delivery.assert_gateway_result=ns['_assert_gateway_result']
        artifacts=self.load('agentic_artifacts')
        root=Path(self.temp.name)/'review-artifacts';root.mkdir()
        if content is not None:
            p=root/'architecture-review.txt';p.write_bytes(content);p.chmod(0o600)
        calls=[]
        async def request(method,path,**kwargs):
            calls.append((method,path))
            if method=='GET'and path=='/api/method/frappe.auth.get_logged_user':
                return {'message':'ai-projects@abitcons.com'}
            raise AssertionError('No ERP mutation may precede valid real artifact bytes')
        bridge=types.SimpleNamespace(erp=types.SimpleNamespace(request=request),secret='test-only-never-sent')
        with patch.dict(sys.modules,{'open_webui.utils.agentic_gateway_delivery':delivery}),patch.object(artifacts,'artifact_directory',return_value=root):
            try:
                value=await self.executor.complete_original(self.row,self.packet_value,
                    {'response_text':raw},adapter,None,bridge,{'modified':'fixture-current-modified'},
                    self.gate,{},self.path)
            except (FileNotFoundError,ValueError)as error:
                value=type(error).__name__
                if 'work_packet' in str(error):print('FIXTURE_SCHEMA_ERROR',str(error))
        self.assertEqual(adapter.record.await_count,1)
        return value,calls

    async def test_missing_and_changed_own_review_bytes_cannot_publish(self):
        for content in (None,b'changed artifact bytes'):
            with self.subTest(content=content is not None):
                root=Path(self.temp.name)/'review-artifacts'
                if root.exists():
                    for p in root.iterdir():p.unlink()
                    root.rmdir()
                value,calls=await self._complete_with_artifact(content)
                self.assertIn(value,{'FileNotFoundError','ValueError'})
                self.assertTrue(all(method=='GET' for method,_ in calls))
                self.assertIsNone(self.store.get_result(self.pm,self.path))
        with self.g.connect(self.path)as db:
            self.assertEqual(db.execute('SELECT count(*)FROM governance_local_result WHERE invocation_id=?',(self.pm,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*)FROM governance_event WHERE invocation_id=?AND kind=?',(self.pm,self.phase.AUDIT_KIND)).fetchone()[0],1)

    async def test_stop_retains_raw_phase_and_local_result_without_publication(self):
        self.g.request_cancel(self.pm,'manager@example.invalid',self.path)
        self.row=self.g.find(self.pm,self.path,include_private=True)
        value,calls=await self._complete_with_artifact(None)
        self.assertIsNone(value);self.assertEqual(calls,[])
        with self.g.connect(self.path)as db:
            self.assertEqual(db.execute('SELECT count(*)FROM governance_local_result WHERE invocation_id=?',(self.pm,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*)FROM governance_event WHERE invocation_id=?AND kind=?',(self.pm,self.phase.AUDIT_KIND)).fetchone()[0],1)
        self.assertEqual(self.g.find(self.pm,self.path)['state'],'cancel_requested')
        self.assertIsNone(self.store.get_result(self.pm,self.path))

    async def test_catalog_distinguishes_architecture_atomic_and_qa_review(self):
        self.load('agentic_gateway_contracts')
        tree=ast.parse(candidate('agentic_gateway_operations').read_bytes())
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))and n.name in ('exact','context')]
        operation=types.SimpleNamespace(read_only=False)
        ns={'registry':lambda role:{'artifact.write':operation,'pm_review.write':operation,'context.read':operation}}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual_candidate_catalog','exec'),ns)
        normal=await ns['context']({},row=self.row,packet=self.packet_value,config={},db_path=self.path)
        self.assertIn('Architecture review:',normal['pm_phase_guidance'])
        self.assertNotIn('pm_review.write',normal['operations'])
        atomic=await ns['context']({},row=self.row,packet={**self.packet_value,'atomic_adoption':{'adoption_id':'fixture'}},config={},db_path=self.path)
        self.assertIn('PM delegation:',atomic['pm_phase_guidance'])
        self.assertIn('ready_for_review',atomic['pm_phase_guidance'])
        self.assertNotIn('pm_review.write',atomic['operations'])
        qa=await ns['context']({},row=self.row,packet={**self.packet_value,'qa_review':{'bound':'fixture'}},config={},db_path=self.path)
        self.assertIn('QA coordination:',qa['pm_phase_guidance'])
        self.assertIn('qa_review_ack',qa['pm_phase_guidance'])
        self.assertIn('pm_review.write',qa['operations'])

if __name__=='__main__':unittest.main(verbosity=2)

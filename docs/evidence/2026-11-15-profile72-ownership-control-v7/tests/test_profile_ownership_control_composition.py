"""Pure selected-source composition checks; no product imports or live calls."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest

HERE=Path(__file__).resolve().parent
P=HERE.parent if HERE.name=='tests' else HERE
def load(name,fixture):
 path=P/name
 return json.loads((path if path.exists() else P/fixture).read_text())
REPO=Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source')
V6='2d9e02868d44e5b83a1dd7a4f5ccfae2bd2d9bac'
V6_PREFIX='docs/evidence/2026-11-15-profile72-command-format-v6/runtime/'
V7_PREFIX='docs/evidence/2026-11-15-profile72-ownership-control-v7'
ROUTER_COMMIT='9921aa0c16cb8bee21a72450d5c8740e98b5942f'
ROUTER_PATH='docs/evidence/2026-11-15-native-control-route-contract-v1/agentic_governance.selected.py'
OLD_WORKFLOW='QA: verify the implementation delivery, prepare its exact head, run independent tests, verify again, and copy only actual receipt evidence.'
NEW_WORKFLOW='QA: prepare its exact implementation PR head first (preparation verifies PR/head/base), verify delivery, run independent tests, verify again, and copy only actual receipt evidence.'
PRESERVED={
 'utils/agentic_ownership.py':'e7b55be765892bd70cdd155d8ef72e7578bae8756ab5373008eb55c63b521f0d',
 'utils/agentic_recovery.py':'212240ff8b68cb1a438e3fe2730ac76a6dfe897f6211bc9e2ef68eb912ace2b3',
 'utils/agentic_start_recovery.py':'114b721fb885c0351b47e7444527d03f24c2491c7f31cb54a5bbcfa0d388bbcd',
 'utils/agentic_result_store.py':'5068586c80e31eeb5c1f8d42f44f2c11b55c0ceb7f32356962e715793703922a',
 'utils/agentic_orchestrator.py':'60e6003d41adadfd8601945487bff3bf5ef5f984d4504da1825eb05a496e0c4d',
 'utils/agentic_contracts/developer_delivery.py':'90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210',
 'utils/agentic_contracts/schemas/governance_v1.schema.json':'5131fd838234f34173b4a7b30360d3c53d3d18f1fcdbc2afb079ba15db8d897c'}

def raw(relative):
 path=P/'source/backend/open_webui'/relative
 return path.read_bytes() if path.exists() else (P/'runtime'/relative).read_bytes()

def blob(commit,path):
 return subprocess.check_output(['git','-C',str(REPO),'show',commit+':'+path],timeout=10)

def without_docs(tree):
 tree=copy.deepcopy(tree)
 for node in ast.walk(tree):
  if isinstance(node,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
   node.body.pop(0)
 return ast.dump(tree,include_attributes=False)

class Composition(unittest.TestCase):
 def test_router_preserves_exact_installed_functional_contract(self):
  current=raw('routers/agentic_governance.py');old=blob(ROUTER_COMMIT,ROUTER_PATH)
  self.assertEqual(hashlib.sha256(old).hexdigest(),'c159756761ea253ee2865e6fec5ac886e7cfa9b6da47e601dbfcac8b6d744067')
  self.assertEqual(without_docs(ast.parse(current)),without_docs(ast.parse(old)))
  self.assertNotIn(b'recover_delivery_control',current)
  def docs(b):
   return {n.name:ast.get_docstring(n) for n in ast.parse(b).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
  now,prior,v6=docs(current),docs(old),docs(blob(V6,V6_PREFIX+'routers/agentic_governance.py'))
  changed={k for k in now if now[k]!=prior[k]}
  self.assertEqual(changed,{'gateway_call','invoke'})
  for k in changed:self.assertEqual(now[k],v6[k])

 def test_prior_reviewed_targets_and_qa_order(self):
  manifest=load('candidate-manifest.source-bound.json','targets.fixture.json')
  for item in manifest['files']:
   rel=item['runtime_relative_path'];new=raw(rel)
   compile(new,rel,'exec')
   if rel=='routers/agentic_governance.py':continue
   prior=blob(V6,V6_PREFIX+rel)
   if rel=='utils/agentic_gateway_operations.py':
    self.assertEqual(prior.decode().count(OLD_WORKFLOW),1)
    self.assertEqual(new,prior.decode().replace(OLD_WORKFLOW,NEW_WORKFLOW).encode())
    value=next(n.value for n in ast.walk(ast.parse(new)) if isinstance(n,ast.Constant) and isinstance(n.value,str) and NEW_WORKFLOW in n.value)
    self.assertLess(value.index('QA: prepare'),value.index('verify delivery',value.index('QA: prepare')))
    self.assertLess(value.index('verify delivery',value.index('QA: prepare')),value.index('run independent tests',value.index('QA: prepare')))
   else:self.assertEqual(new,prior)

 def test_exact_71_to_72_preserves_repairs_and_two_qa_files(self):
  baseline=load('complete71-before.json','baseline71.fixture.json')
  future=load('complete72-candidate.pending.json','future72.fixture.json')
  manifest=load('candidate-manifest.source-bound.json','targets.fixture.json')
  self.assertEqual(hashlib.sha256((P/('complete71-before.json' if (P/'complete71-before.json').exists() else 'baseline71.fixture.json')).read_bytes()).hexdigest(),'794cd680754c3e1c2e83dc49eb93d644d7095b5cbf9463c48cbe04f900bbad0c')
  before={r['runtime_path']:r['sha256'] for r in baseline['files']};after={r['runtime_path']:r['sha256'] for r in future['files']}
  self.assertEqual((len(before),len(after),len(manifest['files'])),(71,72,8))
  targets={r['runtime_path']:r['after_sha256'] for r in manifest['files']}
  self.assertEqual(after,{**before,**targets})
  self.assertEqual(sum(r['before_sha256'] is None for r in manifest['files']),1)
  self.assertEqual(sum('/qa-service/modules/' in k for k in after),2)
  root='/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui/'
  for rel,pin in PRESERVED.items():self.assertEqual(before[root+rel],pin);self.assertEqual(after[root+rel],pin)
  for row in manifest['files']:
   self.assertEqual(hashlib.sha256(raw(row['runtime_relative_path'])).hexdigest(),row['after_sha256'])
  self.assertFalse(future['installed']);self.assertFalse(manifest['execution_authority'])
  self.assertIsNone(manifest['first_closed_flow']);self.assertIsNone(manifest['future_sealed_epoch'])

 def test_operator_pending_denies_before_runtime_authority(self):
  source=(P/'apply_profiles.template.py').read_text();tree=ast.parse(source)
  func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_bindings')
  def need(ok,code):
   if not ok:raise RuntimeError(code)
  ns={'re':re,'Path':Path,'need':need}
  exec(compile(ast.Module(body=[func],type_ignores=[]),'binding_guard','exec'),ns)
  pending=json.loads((P/'bindings.pending.json').read_text())
  with self.assertRaisesRegex(RuntimeError,'PENDING_FUTURE_COMMIT_EPOCH_OR_FIRST_FLOW_BINDINGS_DENY'):ns['validate_bindings'](pending)
  self.assertIn("prefix=='"+V7_PREFIX+"'",source)
  self.assertIn("baseline.get('source_commits') == BASELINE_SOURCE_COMMITS",source)
  self.assertIn("completed_baseline.get('runtime_manifest') == baseline",source)
  self.assertIn("closed = history.assert_closed_first_flow",source)
  self.assertIn('lifecycle.sealed(hold,epoch)',source)
  self.assertEqual(hashlib.sha256((P/'history_guard.py').read_bytes()).hexdigest(),'1d1804e48c1a23fe6f86ee796e5756bd1c16a44376848e0a83788d79002b2052')
  compile(source,'apply_profiles.template.py','exec')

if __name__=='__main__':unittest.main(verbosity=2)

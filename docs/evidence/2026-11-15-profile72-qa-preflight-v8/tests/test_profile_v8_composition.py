"""Pure exact-source/cohort composition; never imports product or live databases."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest
HERE=Path(__file__).resolve().parent
P=HERE.parent if HERE.name=='tests'else HERE
REPO=Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source')
V3='37ae81eafceecd165e33a263d3967255a276968f'
V7='2b3f21ca24493fbb6e8d0c1035bd8d2d847982a3'
PREFIX='docs/evidence/2026-11-15-profile72-qa-preflight-v8'

def blob(commit,path):return subprocess.check_output(['git','-C',str(REPO),'show',commit+':'+path],timeout=10)
def raw(rel):
 f=P/'source/backend/open_webui'/rel
 return (f if f.exists()else P/'runtime'/rel).read_bytes()
def load(name,fixture):
 f=P/name
 return json.loads((f if f.exists()else P/fixture).read_text())
def nodocs(value):
 t=ast.parse(value)
 for n in ast.walk(t):
  if isinstance(n,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))and n.body and isinstance(n.body[0],ast.Expr)and isinstance(n.body[0].value,ast.Constant)and isinstance(n.body[0].value.value,str):n.body.pop(0)
 return ast.dump(t,include_attributes=False)
class Composition(unittest.TestCase):
 def test_installed_v3_router_is_functionally_exact_with_only_profile_docs(self):
  original=blob(V3,'docs/evidence/2026-11-15-qa-preflight-retry-v3/runtime/routers/agentic_governance.py');new=raw('routers/agentic_governance.py')
  self.assertEqual(hashlib.sha256(original).hexdigest(),'9c08767360232ef9e358148c22222ac4b8ebd8d03c18332ff1a8dfebb09c89d7')
  self.assertEqual(nodocs(original),nodocs(new));self.assertNotIn(b'recover_delivery_control',new)
  def docs(b):return {n.name:ast.get_docstring(n)for n in ast.parse(b).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
  old,now,approved=docs(original),docs(new),json.loads((P/'profile-api-docs.fixture.json').read_text())['docs']
  self.assertEqual({k for k in now if now[k]!=old[k]},{'gateway_call','invoke'})
  for k in ['gateway_call','invoke']:self.assertEqual(now[k],approved[k])
 def test_seven_other_targets_exact_reviewed_v7_and_qa_order(self):
  manifest=load('candidate-manifest.pending.json','targets.fixture.json')
  for r in manifest['files']:
   rel=r['runtime_relative_path'];b=raw(rel);compile(b,rel,'exec')
   if rel!='routers/agentic_governance.py':self.assertEqual(hashlib.sha256(b).hexdigest(),json.loads((P/'profile-api-docs.fixture.json').read_text())['other_target_pins'][rel])
  text=raw('utils/agentic_gateway_operations.py').decode();self.assertIn('QA: prepare its exact implementation PR head first (preparation verifies PR/head/base), verify delivery, run independent tests, verify again',text)
 def test_actual_cohort_preserves_qa_policy_logger_contracts_and_all_non_targets(self):
  baseline=load('complete71-before.json','baseline71.fixture.json');future=load('complete72-candidate.pending.json','future72.fixture.json');m=load('candidate-manifest.pending.json','targets.fixture.json')
  before={r['runtime_path']:r['sha256']for r in baseline['files']};after={r['runtime_path']:r['sha256']for r in future['files']};targets={r['runtime_path']:r['after_sha256']for r in m['files']}
  self.assertEqual((len(before),len(after),len(targets)),(71,72,8));self.assertEqual(after,{**before,**targets});self.assertEqual(sum(r['before_sha256']is None for r in m['files']),1)
  root='/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui/'
  pins={'utils/agentic_recovery.py':'72ecafaf0d5449156525c750b24015bd5ac4cf8536b5f7486abc29633a3ea893','utils/agentic_ownership.py':'e7b55be765892bd70cdd155d8ef72e7578bae8756ab5373008eb55c63b521f0d','utils/agentic_start_recovery.py':'114b721fb885c0351b47e7444527d03f24c2491c7f31cb54a5bbcfa0d388bbcd','utils/agentic_result_store.py':'5068586c80e31eeb5c1f8d42f44f2c11b55c0ceb7f32356962e715793703922a','utils/agentic_contracts/developer_delivery.py':'90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210','utils/agentic_contracts/schemas/governance_v1.schema.json':'5131fd838234f34173b4a7b30360d3c53d3d18f1fcdbc2afb079ba15db8d897c'}
  for rel,pin in pins.items():self.assertEqual(before[root+rel],pin);self.assertEqual(after[root+rel],pin)
  self.assertEqual(sum('/qa-service/modules/'in k for k in after),2)
  for r in m['files']:self.assertEqual(hashlib.sha256(raw(r['runtime_relative_path'])).hexdigest(),r['after_sha256'])
  self.assertFalse(future['installed']);self.assertFalse(m['execution_authority']);self.assertIsNone(m['first_closed_flow']);self.assertIsNone(m['future_sealed_epoch'])
 def test_pending_authority_denies_before_helpers_and_fixed_actual_base_is_bound(self):
  source=(P/'apply_profiles.template.py').read_text();tree=ast.parse(source);func=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='validate_bindings')
  def need(ok,code):
   if not ok:raise RuntimeError(code)
  ns={'re':re,'Path':Path,'need':need};exec(compile(ast.Module(body=[func],type_ignores=[]),'binding_guard','exec'),ns)
  with self.assertRaisesRegex(RuntimeError,'PENDING_FUTURE_COMMIT_EPOCH_OR_FIRST_FLOW_BINDINGS_DENY'):ns['validate_bindings'](json.loads((P/'bindings.pending.json').read_text()))
  self.assertIn("BASELINE_SHA = '365fea0f99575d6d6da441f8584fbf3b2cb33f2a16267746706766ec9e52c2ca'",source);self.assertIn('11aaa59fb6ca6e615bb8914bd79ddb1639ecb0239d6599ea3fa728d1ef3cffba',source);self.assertIn("prefix=='"+PREFIX+"'",source);self.assertIn('closed = history.assert_closed_first_flow',source);compile(source,'operator','exec')
  self.assertEqual(hashlib.sha256((P/'history_guard.py').read_bytes()).hexdigest(),'1d1804e48c1a23fe6f86ee796e5756bd1c16a44376848e0a83788d79002b2052')
if __name__=='__main__':unittest.main(verbosity=2)

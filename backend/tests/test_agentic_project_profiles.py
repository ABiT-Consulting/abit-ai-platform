"""Pure resolver and exact patched caller proofs; no backend imports or actions."""
import ast,copy,importlib.util,json,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent;SRC=HERE.parent/'open_webui/utils'
spec=importlib.util.spec_from_file_location('actual_project_profiles',SRC/'agentic_project_profiles.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
REPO='https://github.com/ABiT-Consulting/abit-ai-agents';ENV='ABIT-DEV-LINUX'
class CapabilityUnavailable(ValueError):pass
class ReachedWorkspace(Exception):pass
def template():return {'enabled':True,'repository':REPO,'environment':ENV,'workspace_base':'/srv/abit-dev-linux/abit-ai/data/project-workspaces','allowed_paths':['acceptance/20261114'],'read_paths':['README.md','acceptance/20261114'],'test_paths':['acceptance/20261114'],'tests':{'unit':['python3','-m','unittest']},'test_acceptance_ids':{'unit':['AC-1']},'base_branch':'main','base_commit':'a'*40,'git_author':{'name':'ABiT service','email':'machine@example.invalid'}}
def config():return {'agentic_repository_gateway':{'projects':{},'templates':{'reviewed':template()}}}
def actual_function(filename,name,extra=None):
 tree=ast.parse((SRC/filename).read_bytes());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 env={'ProfileUnavailable':p.ProfileUnavailable,'resolve_repository_profile':p.resolve_repository_profile,'CapabilityUnavailable':CapabilityUnavailable,'Path':Path};env.update(extra or {});exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'<actual-patched-'+name+'>','exec'),env);return env[name],node
class Profiles(unittest.TestCase):
 def test_exact_enabled_pair_and_distinct_valid_project_roots(self):
  c=config();a,meta=p.resolve_repository_profile(c,'PROJ-A',REPO,ENV);b,_=p.resolve_repository_profile(c,'PROJ-B',REPO,ENV)
  self.assertEqual(a['workspace_root'],'/srv/abit-dev-linux/abit-ai/data/project-workspaces/PROJ-A');self.assertNotEqual(a['workspace_root'],b['workspace_root']);self.assertEqual(meta,{'kind':'template','template_id':'reviewed','ready':True})
  for repo,environment in [(REPO+'-other',ENV),(REPO,ENV+'-other')]:
   with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',repo,environment)
 def test_explicit_project_profile_is_authoritative_even_none_or_mismatch(self):
  for explicit in (None,{}, {'repository':REPO,'environment':'OTHER'}):
   c=config();c['agentic_repository_gateway']['projects']['PROJ-A']=explicit
   with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
  c=config();explicit={**template(),'workspace_root':'/srv/abit-dev-linux/exact-owned'};c['agentic_repository_gateway']['projects']['PROJ-A']=explicit
  result,meta=p.resolve_repository_profile(c,'PROJ-A',REPO,ENV);self.assertEqual(result,explicit);self.assertEqual(meta,{'kind':'project','ready':True})
 def test_disabled_ambiguous_missing_malformed_and_unapproved_pairs_deny(self):
  for drift in ('disabled','ambiguous','missing_policy','unknown_grant','malformed_gateway','malformed_projects','malformed_templates'):
   c=config();g=c['agentic_repository_gateway']
   if drift=='disabled':g['templates']['reviewed']['enabled']=False
   elif drift=='ambiguous':g['templates']['second']=copy.deepcopy(g['templates']['reviewed'])
   elif drift=='missing_policy':del g['templates']['reviewed']['base_commit']
   elif drift=='unknown_grant':g['templates']['reviewed']['IT_grant']={'root_shell':True}
   elif drift=='malformed_gateway':c['agentic_repository_gateway']=None
   elif drift=='malformed_projects':g['projects']=[]
   else:g['templates']=[]
   with self.assertRaises(p.ProfileUnavailable,msg=drift):p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
 def test_template_source_and_test_paths_reject_malformed_policy(self):
  values=(None,[],['../../outside'],['/etc/passwd'],['a/../b'],['a//b'],['a\\b'],['a\x00b'],['.git/config'],['.ssh/key'],['.codex/config'],['a','a'],['x'*401])
  for key in ('allowed_paths','read_paths','test_paths'):
   for value in values:
    with self.subTest(key=key,value=value):
     c=config();c['agentic_repository_gateway']['templates']['reviewed'][key]=value
     with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
     self.assertFalse(p.repository_readiness(c,'PROJ-A',REPO,ENV)['ready'])
 def test_template_tests_and_acceptance_mapping_reject_malformed_policy(self):
  commands=(None,{}, {'unit':'python3 -m unittest'},{'unit':[]},{'unit':['']},{'unit':['python3']*31},{'unit':['x'*501]},{'unit':['python3','bad\x00arg']},{'invalid test':['python3']},{'unit':[123]})
  mappings=(None,[],{'unregistered':['AC-1']},{'unit':[]},{'unit':['AC-1','AC-1']},{'unit':[1]},{'unit':['x'*161]})
  for key,values in (('tests',commands),('test_acceptance_ids',mappings)):
   for value in values:
    with self.subTest(key=key,value=value):
     c=config();c['agentic_repository_gateway']['templates']['reviewed'][key]=value
     with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
  c=config();t=c['agentic_repository_gateway']['templates']['reviewed'];t['tests']={'unit':['p']+['x'*500]*29}
  self.assertEqual(p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)[0]['tests'],t['tests'])
 def test_template_fixed_base_author_and_repository_validation(self):
  cases=(('base_commit',None),('base_commit','a'*39),('base_commit','A'*40),('base_branch',None),('base_branch','main/../other'),('git_author',None),('git_author',{'name':'service','email':'a@example.invalid','extra':True}),('git_author',{'name':'bad\nname','email':'a@example.invalid'}),('git_author',{'name':'service','email':'bad email'}))
  for key,value in cases:
   with self.subTest(key=key,value=value):
    c=config();c['agentic_repository_gateway']['templates']['reviewed'][key]=value
    with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
  for repository in ('https://github.com/../repo','https://github.com/./repo','https://github.com/owner/..','https://github.com/owner/.','https://github.com/-owner/repo','https://github.com/owner/repo/','http://github.com/owner/repo'):
   c=config();c['agentic_repository_gateway']['templates']['reviewed']['repository']=repository
   with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',repository,ENV)
  for environment in ('',ENV+'\n',ENV+'\x00',' '+ENV):
   c=config();c['agentic_repository_gateway']['templates']['reviewed']['environment']=environment
   with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',REPO,environment)
 def test_explicit_nested_profile_is_copied_without_template_fallback(self):
  c=config();explicit={**template(),'workspace_root':'/srv/abit-dev-linux/exact-owned'};c['agentic_repository_gateway']['projects']['PROJ-A']=explicit
  a,_=p.resolve_repository_profile(c,'PROJ-A',REPO,ENV);a['tests']['unit'].append('injected');a['git_author']['email']='changed'
  b,meta=p.resolve_repository_profile(c,'PROJ-A',REPO,ENV);self.assertEqual(b,explicit);self.assertEqual(meta,{'kind':'project','ready':True})
 def test_path_and_project_traversal_cannot_derive_workspace(self):
  for project in ('../PROJ','PROJ/OTHER','/absolute','PROJ\\OTHER','PROJ\nOTHER','.'):
   with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(config(),project,REPO,ENV)
  for base in ('/etc','/srv/abit-dev-linux/x/../escape','/srv/abit-dev-linux//double','/srv/abit-dev-linux/space/','/srv/abit-dev-linux/x\\y'):
   c=config();c['agentic_repository_gateway']['templates']['reviewed']['workspace_base']=base
   with self.assertRaises(p.ProfileUnavailable):p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
 def test_nested_policy_copy_isolation_and_readiness_contains_no_server_authority(self):
  c=config();a,_=p.resolve_repository_profile(c,'PROJ-A',REPO,ENV);a['tests']['unit'].append('injected');a['git_author']['email']='changed';a['allowed_paths'].append('foreign');b,_=p.resolve_repository_profile(c,'PROJ-A',REPO,ENV)
  self.assertEqual(b['tests']['unit'],['python3','-m','unittest']);self.assertEqual(c['agentic_repository_gateway']['templates']['reviewed']['git_author']['email'],'machine@example.invalid')
  ready=p.repository_readiness(c,'PROJ-A',REPO,ENV);self.assertEqual(ready,{'kind':'template','template_id':'reviewed','ready':True});text=json.dumps(ready);self.assertNotIn('/srv',text);self.assertNotIn('python',text);self.assertNotIn('machine@',text);self.assertNotIn('AC-1',text)
  failed=p.repository_readiness(c,'PROJ-A',REPO,'UNREGISTERED');self.assertFalse(failed['ready']);self.assertNotIn('/srv',json.dumps(failed))
 def test_actual_delivery_repository_profile_executes_real_resolver_and_rejects_other_roles(self):
  fn,_=actual_function('agentic_gateway_delivery.py','_repository_profile');packet={'repository':REPO,'environment':ENV,'workspace_root':'/etc','tests':{'evil':['root','shell']},'test_acceptance_ids':{'evil':['FAKE']},'IT_grant':True};row={'erp_project':'PROJ-A','target_role':'developer'}
  result=fn(row,packet,config());self.assertEqual(result['tests'],template()['tests']);self.assertTrue(result['workspace_root'].endswith('/PROJ-A'));self.assertNotIn('IT_grant',result)
  for role in ('operations','it_manager'):
   with self.assertRaises(CapabilityUnavailable):fn({**row,'target_role':role},packet,config())
  c=config();c['agentic_repository_gateway']['projects']['PROJ-A']=None
  with self.assertRaises(CapabilityUnavailable):fn(row,packet,c)
 def test_actual_operations_workspace_uses_only_resolved_server_root_and_context_wires_registration(self):
  observed=[]
  def reached(path):observed.append(str(path));raise ReachedWorkspace()
  fn,_=actual_function('agentic_gateway_operations.py','_workspace',{'_open_dir':reached})
  with self.assertRaises(ReachedWorkspace):fn({'erp_project':'PROJ-A','erp_task':'TASK-A','invocation_id':'INV-A'},{'repository':REPO,'environment':ENV,'workspace_root':'/etc'},config())
  self.assertEqual(observed,['/srv/abit-dev-linux/abit-ai/data/project-workspaces/PROJ-A/TASK-A/INV-A/repository'])
  tree=ast.parse((SRC/'agentic_gateway_operations.py').read_bytes());context=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='context');calls=[n for n in ast.walk(context) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='resolve_repository_profile'];self.assertEqual(len(calls),1)
  expected=ast.parse("resolve_repository_profile(config,row['erp_project'],packet['repository'],packet['environment'])",mode='eval').body;self.assertEqual([ast.dump(v) for v in calls[0].args],[ast.dump(v) for v in expected.args])
  registration=next(v for n in ast.walk(context) if isinstance(n,ast.Dict) for k,v in zip(n.keys,n.values) if isinstance(k,ast.Constant) and k.value=='repository_registration');self.assertIsInstance(registration,ast.Name);self.assertEqual(registration.id,'registration')
if __name__=='__main__':unittest.main(verbosity=2)

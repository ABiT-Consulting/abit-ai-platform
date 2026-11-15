"""Prepare a pinned live-based Core overlay; no deployment, DB or service actions."""
import argparse
import ast
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path('/srv/abit-dev-linux/abit-ai-platform')
LIVE = Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui').resolve(strict=True)
EXPECTED_LIVE = Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
SELECTED = (
 'utils/agentic_gateway_sessions.py', 'utils/agentic_gateway_operations.py',
 'utils/agentic_executor.py', 'routers/agentic_governance.py',
 'utils/agentic_result_provenance.py', 'utils/agentic_gateway_contracts.py',
 'utils/agentic_gateway_delivery.py', 'utils/agentic_orchestrator.py',
 'utils/agentic_delivery_continuation.py', 'utils/agentic_delivery_control.py',
 'utils/agentic_start_recovery.py',
)
NEW_FILES = {'utils/agentic_delivery_continuation.py', 'utils/agentic_delivery_control.py'}
PRESERVED = (
 'utils/agentic_result_store.py', 'utils/agentic_architecture_review.py',
 'utils/agentic_contracts/validator.py', 'utils/agentic_planning.py',
 'utils/erp_dot_execution.py',
)
sha=lambda raw:hashlib.sha256(raw).hexdigest()

def require(value, code):
    if not value: raise RuntimeError(code)
def write(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(raw)
def dump(stage,name,value):
    write(stage/name,(json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode())
def metadata(path):
    value=path.stat()
    return {'uid':value.st_uid,'gid':value.st_gid,'mode':value.st_mode & 0o7777,
            'size':value.st_size,'mtime_ns':value.st_mtime_ns,
            'xattrs':{k:os.getxattr(path,k,follow_symlinks=False).hex()
                      for k in sorted(os.listxattr(path,follow_symlinks=False))}}
def parent_proof(path):
    value=path.lstat()
    require(path.resolve()==path and path.is_dir() and not path.is_symlink(),'EXACT_CANONICAL_PARENT_REQUIRED')
    return {'dev':value.st_dev,'ino':value.st_ino,'uid':value.st_uid,'gid':value.st_gid,
            'mode':value.st_mode & 0o7777,
            'xattrs':{k:os.getxattr(path,k,follow_symlinks=False).hex()
                      for k in sorted(os.listxattr(path,follow_symlinks=False))}}
def proof(path):
    return {'sha256':sha(path.read_bytes()),**metadata(path)}
def function(text,name):
    node=next(n for n in ast.parse(text).body
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    return node,''.join(text.splitlines(keepends=True)[node.lineno-1:node.end_lineno])
def without_docstrings(raw):
    class Strip(ast.NodeTransformer):
        def visit_FunctionDef(self,node):
            self.generic_visit(node)
            if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
                node.body=node.body[1:]
            return node
        visit_AsyncFunctionDef=visit_FunctionDef
    return ast.dump(Strip().visit(ast.parse(raw)),include_attributes=False)
def service(name):
    fields='MainPID,ExecMainStartTimestamp,ExecMainStartTimestampMonotonic,User,WorkingDirectory,ActiveState,SubState,InvocationID'
    raw=subprocess.check_output(['systemctl','show',name,'--property='+fields],text=True)
    return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
def source(commit,path):
    return subprocess.check_output(['git','show',commit+':'+path],cwd=REPO)
def dependency_closure(values, originals, live_root):
    live_root=Path(live_root).resolve(strict=True)
    supplied={**originals,**values}
    def candidates(module):
        parts=module.split('.')
        if parts[0]!='open_webui' or any(not p.isidentifier() for p in parts):
            raise ValueError('Canonical bounded application import required')
        relative='/'.join(parts[1:])
        return ([relative+'.py',relative+'/__init__.py'] if relative else ['__init__.py'])
    def locate(module):
        for rel in candidates(module):
            if rel in supplied:return rel,supplied[rel],True
            p=live_root/rel
            if p.is_file():
                if p.is_symlink() or not p.resolve().is_relative_to(live_root):
                    raise ValueError('Application import resolves outside fixed runtime')
                return rel,p.read_bytes(),False
        relative='/'.join(module.split('.')[1:])
        directory=live_root/relative
        if directory.is_dir() and not directory.is_symlink() and directory.resolve().is_relative_to(live_root):
            return relative+'/',b'',True
        if relative and any(name.startswith(relative+'/') for name in supplied):
            return relative+'/',b'',True
        return None
    # Symbol edges are not modules. Their containing module must exist; only a
    # failed complete import/from target is a missing application dependency.
    def actual_edges(raw,relative):
        tree=ast.parse(raw);result=set()
        module='open_webui.'+relative.removesuffix('.py').replace('/','.')
        package=module.removesuffix('.__init__') if relative.endswith('/__init__.py') else module.rsplit('.',1)[0]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                result.update(v.name for v in node.names if v.name=='open_webui' or v.name.startswith('open_webui.'))
            elif isinstance(node,ast.ImportFrom):
                if node.level:
                    parts=package.split('.');target='.'.join(parts[:len(parts)-node.level+1])
                    if node.module:target+='.'+node.module
                else:target=node.module or ''
                if target=='open_webui' or target.startswith('open_webui.'):
                    result.add(target)
                    parent=locate(target)
                    for item in node.names:
                        if item.name=='*':continue
                        child=target+'.'+item.name
                        if locate(child):result.add(child)
                        elif parent and (parent[0].endswith('/__init__.py') or parent[0].endswith('/')):
                            exported=set()
                            def names(nodes):
                                for value in nodes:
                                    if isinstance(value,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                                        exported.add(value.name)
                                    elif isinstance(value,(ast.Assign,ast.AnnAssign)):
                                        targets=value.targets if isinstance(value,ast.Assign) else [value.target]
                                        for item in targets:
                                            exported.update(n.id for n in ast.walk(item) if isinstance(n,ast.Name))
                                    elif isinstance(value,(ast.Import,ast.ImportFrom)):
                                        exported.update(n.asname or (n.name.split('.')[0] if isinstance(value,ast.Import) else n.name) for n in value.names)
                                    else:
                                        for field in ('body','orelse','finalbody'):
                                            child_nodes=getattr(value,field,None)
                                            if isinstance(child_nodes,list):names(child_nodes)
                                        for handler in getattr(value,'handlers',[]):names(handler.body)
                            names(ast.parse(parent[1]).body)
                            if item.name not in exported:result.add(child)
        return result
    seen={};pending=list(values);missing=[];new_edges=[];boundaries={}
    while pending:
        rel=pending.pop()
        if rel in seen:continue
        raw=supplied[rel]
        edges=actual_edges(raw,rel)
        old=actual_edges(originals[rel],rel) if rel in originals else set()
        seen[rel]={'sha256':hashlib.sha256(raw).hexdigest(),'imports':sorted(edges)}
        for module in sorted(edges):
            found=locate(module)
            if found is None:
                missing.append({'source':rel,'import':module,'introduced':module not in old})
                continue
            target,data,in_fixture=found
            if module not in old:
                if not in_fixture:raise ValueError('New application dependency is outside the reviewed fixture cohort: '+module)
                new_edges.append({'source':rel,'import':module,'resolved_relative_path':target,'sha256':hashlib.sha256(data).hexdigest(),'supplied_by_fixture':in_fixture})
            if in_fixture and not target.endswith('/'):
                pending.append(target)
            else:
                boundaries[target]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
    introduced_missing=[item for item in missing if item['introduced']]
    if introduced_missing:raise ValueError('Unavailable new application import: '+repr(introduced_missing))
    if any('open_webui.utils.agentic_project_profiles' in node['imports'] for node in seen.values()):
        raise ValueError('Excluded profile-template dependency reintroduced')
    return {'method':'Pure AST traversal of selected leaves and all reachable supplied application modules; external preserved module boundaries read as bytes only.',
            'new_application_imports_all_available':True,'excluded_profile_templates_unreferenced':all('open_webui.utils.agentic_project_profiles' not in node['imports'] for node in seen.values()),
            'new_import_edges':new_edges,'supplied_reachable_modules':seen,'preserved_external_boundaries':boundaries,'unchanged_missing_dependencies':missing}


def main(args):
    require(LIVE==EXPECTED_LIVE,'LIVE_RUNTIME_CHANGED')
    require(len(args.source_commit)==40 and all(c in '0123456789abcdef' for c in args.source_commit),'EXACT_SOURCE_COMMIT_REQUIRED')
    baseline_path=args.live_baseline.resolve(strict=True)
    require(baseline_path.parent==REPO/'.git' and baseline_path.suffix=='.json','PRIVATE_CODE_BASELINE_REQUIRED')
    BASELINE,BASELINE_SHA=baseline_path,args.live_baseline_sha256
    require(len(BASELINE_SHA)==64 and all(c in '0123456789abcdef' for c in BASELINE_SHA),'EXACT_BASELINE_HASH_REQUIRED')
    require(args.source_pins.resolve(strict=True).is_relative_to(REPO/'.git'),'PRIVATE_SOURCE_PINS_REQUIRED')
    pins_raw=args.source_pins.read_bytes()
    require(sha(pins_raw)==args.source_pins_sha256,'SOURCE_PINS_HASH_CHANGED')
    pins=json.loads(pins_raw)
    prepare_name='deployment/linux/core-hardening-20261115/prepare_core_overlay.py'
    preparation_raw=Path(__file__).read_bytes()
    require(preparation_raw==source(args.source_commit,prepare_name),'PREPARATION_MUST_EQUAL_COMMITTED_SOURCE')
    require(pins['source_commit']==args.source_commit,'SOURCE_PIN_COMMIT_MISMATCH')
    expected=pins['runtime_sha256']
    require(set(expected)==set(SELECTED),'REVIEWED_RUNTIME_SELECTION_REQUIRED')
    source_bytes={p:source(args.source_commit,'backend/open_webui/'+p) for p in SELECTED}
    require(all(sha(raw)==expected[p] for p,raw in source_bytes.items()),'FROZEN_SOURCE_CHANGED')
    baseline_raw=BASELINE.read_bytes();require(sha(baseline_raw)==BASELINE_SHA,'BASELINE_CHANGED')
    baseline=json.loads(baseline_raw)
    require(baseline.get('activation_eligible') is False,'CODE_ONLY_BASELINE_REQUIRED')
    core=set((LIVE/'utils').rglob('*.py'))|set((LIVE/'utils/agentic_contracts').rglob('*.json'))|{LIVE/'routers/agentic_governance.py'}
    require(len(core)==147 and set(map(str,core))==set(baseline['core_files']),'LIVE_CORE_INVENTORY_CHANGED')
    require(all(proof(Path(p))==v for p,v in baseline['core_files'].items()),'LIVE_CORE_BYTES_OR_METADATA_CHANGED')
    services={name:service(name) for name in ('abit-ai.service','abit-ai-erp-review-qa.service')}
    for key,unit in [('main','abit-ai.service'),('qa','abit-ai-erp-review-qa.service')]:
        expected_process=baseline['processes'][key]
        require(services[unit]['MainPID']==str(expected_process['pid']),'MAIN_QA_IDENTITY_CHANGED')
        raw=Path('/proc/'+str(expected_process['pid'])+'/stat').read_text()
        require(raw.rsplit(')',1)[1].split()[19]==expected_process['start_ticks'],'MAIN_QA_BIRTH_CHANGED')

    commit_timestamp_ns=int(subprocess.check_output(['git','show','-s','--format=%ct',args.source_commit],cwd=REPO,text=True).strip())*1_000_000_000
    for name in NEW_FILES:require(not os.path.lexists(LIVE/name),'REVIEWED_NEW_HELPER_MUST_REMAIN_ABSENT')
    stage=Path(tempfile.mkdtemp(prefix='combined-core-hardening-20261115-',dir=REPO/'.git'))
    stage.chmod(0o700); fixture=stage/'fixture-app'; candidate=stage/'candidate/open_webui'
    observed=datetime.now(timezone.utc).isoformat()
    original={p.relative_to(LIVE).as_posix():p.read_bytes() for p in sorted(core)}
    runtime=[{'relative_path':p.relative_to(LIVE).as_posix(),'sha256':sha(original[p.relative_to(LIVE).as_posix()]),
              'metadata':metadata(p)} for p in sorted(core)]
    for name,raw in original.items():write(fixture/'backend/open_webui'/name,raw)
    values=dict(source_bytes)
    # Preserve current fixed-Project policy; the template increment is not selected.
    ops=values['utils/agentic_gateway_operations.py'].decode()
    profile_import='from open_webui.utils.agentic_project_profiles import ProfileUnavailable, resolve_repository_profile\n'
    require(ops.count(profile_import)==1,'EXPECTED_PROFILE_IMPORT_CHANGED')
    ops=ops.replace(profile_import,'',1)
    a=ops.index('    try:\n        profile, registration = resolve_repository_profile(')
    b=ops.index('    repository = {',a)
    live_ops=original['utils/agentic_gateway_operations.py'].decode()
    profile_line=next(line for line in function(live_ops,'context')[1].splitlines(keepends=True) if line.startswith('    profile = '))
    ops=ops[:a]+profile_line+ops[b:]
    registration="        'repository_registration': registration,\n"
    require(ops.count(registration)==1,'EXPECTED_REGISTRATION_VIEW_CHANGED')
    ops=ops.replace(registration,'',1)
    old_workspace=function(ops,'_workspace')[1];live_workspace=function(live_ops,'_workspace')[1]
    require(ops.count(old_workspace)==1,'EXPECTED_WORKSPACE_CHANGED')
    ops=ops.replace(old_workspace,live_workspace,1)
    require('resolve_repository_profile' not in ops and 'ProfileUnavailable' not in ops,'TEMPLATE_FEATURE_LEAK')
    require(ast.dump(function(ops,'_workspace')[0],include_attributes=False)==ast.dump(function(live_ops,'_workspace')[0],include_attributes=False),'FIXED_WORKSPACE_AST_CHANGED')
    values['utils/agentic_gateway_operations.py']=ops.encode()
    # Bootstrap and delivery keep the same fixed-Project policy as current live.
    delivery=values['utils/agentic_gateway_delivery.py'].decode()
    require(delivery.count(profile_import)==1,'EXPECTED_DELIVERY_PROFILE_IMPORT_CHANGED')
    delivery=delivery.replace(profile_import,'',1)
    old_repository_profile=function(delivery,'_repository_profile')[1]
    live_repository_profile=function(original['utils/agentic_gateway_delivery.py'].decode(),'_repository_profile')[1]
    require(delivery.count(old_repository_profile)==1,'EXPECTED_DELIVERY_PROFILE_CHANGED')
    delivery=delivery.replace(old_repository_profile,live_repository_profile,1)
    require('resolve_repository_profile' not in delivery and 'ProfileUnavailable' not in delivery,'DELIVERY_TEMPLATE_FEATURE_LEAK')
    require(ast.dump(function(delivery,'_repository_profile')[0],include_attributes=False)==ast.dump(function(original['utils/agentic_gateway_delivery.py'].decode(),'_repository_profile')[0],include_attributes=False),'FIXED_DELIVERY_PROFILE_AST_CHANGED')
    values['utils/agentic_gateway_delivery.py']=delivery.encode()
    # Keep source docs for hardening/delivery/start recovery, remove unselected templates.
    router=values['routers/agentic_governance.py'].decode()
    old="""    operation, arguments and stable call_id. context.read reports safe repository
    registration readiness. Repository policy comes from the exact Project
    registration or one enabled server-approved repository/environment template;
    templates derive separate Project workspaces and grant no IT approval.
    Physical dependency verification requires a separate exact Project/SRS
    policy or its existing Project registration. Malformed or conflicting
    policy registrations are denied before a native dependency read.
    Missing or conflicting registrations return a useful prerequisite error.
"""
    new="""    operation, arguments and stable call_id. context.read reports the fixed
    Project's registered repository/environment policy. Physical dependency
    verification requires a separate exact Project/SRS policy or its existing
    Project registration. Malformed or conflicting policy registrations are
    denied before a native dependency read. Missing or conflicting fixed Project
    profiles deny repository operations without selecting another Project.
"""
    require(router.count(old)==1,'EXPECTED_PROFILE_DOCUMENTATION_CHANGED')
    router=router.replace(old,new,1)
    require(without_docstrings(router)==without_docstrings(source_bytes['routers/agentic_governance.py']),'FROZEN_ROUTER_FUNCTIONAL_AST_CHANGED')
    values['routers/agentic_governance.py']=router.encode()
    closure=dependency_closure(values,original,LIVE)
    dump(stage,'application-dependency-closure.json',closure)
    files=[];patch=[]
    for name,raw in values.items():
        compile(raw,str(candidate/name),'exec')
        before=original.get(name,b'')
        require(bool(before) or name in NEW_FILES,'UNREVIEWED_NEW_RUNTIME_FILE')
        require(bool(before)==(name not in NEW_FILES),'EXACT_TWO_ABSENT_HELPERS_REQUIRED')
        write(candidate/name,raw);write(fixture/'backend/open_webui'/name,raw)
        if before:write(stage/'original/open_webui'/name,before)
        files.append({'relative_path':name,'live_path':str(LIVE/name),
          'original_sha256':sha(before) if before else None,'candidate_sha256':sha(raw),'candidate_bytes':len(raw),
          'canonical_source_sha256':sha(source_bytes[name]),'candidate_equals_full_source':raw==source_bytes[name],
          'replacement_required':raw!=before,'new_file':not bool(before),
          'live_metadata':metadata(LIVE/name) if before else None,
          'parent_metadata':parent_proof((LIVE/name).parent),
          'new_metadata':{'uid':999,'gid':988,'mode':0o644,'mtime_ns':commit_timestamp_ns,'xattrs':{}} if not before else None,
          'syntax_valid':True})
        patch.extend(difflib.unified_diff(before.decode().splitlines(True),raw.decode().splitlines(True),
            fromfile='original/'+name if before else '/dev/null',tofile='candidate/'+name))
    tests=[]
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',args.source_commit,'backend/tests'],cwd=REPO,text=True).splitlines()
    for name in names:
        if Path(name).suffix=='.py':
            raw=source(args.source_commit,name);write(fixture/name,raw)
            tests.append({'path':name,'sha256':sha(raw),'source_commit':args.source_commit})
    write(fixture/'backend/tests/agentic_governance.py',values['routers/agentic_governance.py'])
    tests.append({'path':'backend/tests/agentic_governance.py','sha256':sha(values['routers/agentic_governance.py']),'source':'candidate router'})
    for name in [
        'docs/evidence/2026-11-15-pm-architecture-review-v1/baseline/agentic_executor.py',
        'docs/evidence/2026-11-15-github-commit-readback-recovery-v1/runtime/agentic_gateway_delivery.py',
        'docs/evidence/2026-11-15-github-commit-readback-recovery-v1/runtime/agentic_orchestrator.py',
        'scripts/prepare_delivery_history.py',
    ]:
        raw=source(args.source_commit,name);write(fixture/name,raw)
        tests.append({'path':name,'sha256':sha(raw),'source_commit':args.source_commit})
    profile_path='deployment/linux/core-hardening-20261115/test_runtime_profile_overlay.py'
    raw=source(args.source_commit,profile_path)
    write(fixture/'backend/tests/test_runtime_profile_overlay.py',raw)
    tests.append({'path':'backend/tests/test_runtime_profile_overlay.py','sha256':sha(raw),'source_commit':args.source_commit,'source_path':profile_path})
    runner_path='deployment/linux/core-hardening-20261115/run_overlay_tests.py'
    runner=source(args.source_commit,runner_path)
    write(stage/'run_overlay_tests.py',runner)

    require(all(proof(Path(p))==v for p,v in baseline['core_files'].items()),'LIVE_CORE_CHANGED_DURING_CAPTURE')
    require(services=={name:service(name) for name in services},'MAIN_QA_CHANGED_DURING_CAPTURE')
    dump(stage,'runtime-source-snapshot.json',runtime);dump(stage,'test-source-snapshot.json',tests)
    write(stage/'backend-hardening.patch',''.join(patch).encode())
    manifest={'schema_version':2,'status':'PREPARED_OFFLINE_TESTS_PENDING_NO_ACTIVATION',
      'observed_at_utc':observed,'source_commit':args.source_commit,'source_pins_sha256':sha(pins_raw),
      'code_only_baseline':str(BASELINE),'code_only_baseline_sha256':BASELINE_SHA,
      'activation_eligible':False,'historical_f33a_compatibility_increment_pending':pins.get('historical_compatibility_pending',False),
      'preparation_source_sha256':sha(preparation_raw),'test_runner_sha256':sha(runner),
      'live_runtime':str(LIVE),'candidate_root':str(candidate),'fixture_root':str(fixture),
      'services':services,'files':files,'runtime_snapshot_count':len(runtime),
      'dependency_closure_sha256':sha((stage/'application-dependency-closure.json').read_bytes()),
      'additional_test_selection':pins.get('additional_test_selection',[]),
      'replacement_count':sum(f['replacement_required'] for f in files),
      'new_file_count':sum(f['new_file'] for f in files),
      'unchanged_dependency_sha256':{p:sha(original[p]) for p in PRESERVED},
      'candidate_adaptations':{
        'operations_workspace_live_ast_sha256':sha(ast.dump(function(live_ops,'_workspace')[0],include_attributes=False).encode()),
        'operations_context_profile_line_sha256':sha(profile_line.encode()),
        'delivery_repository_profile_live_ast_sha256':sha(ast.dump(function(original['utils/agentic_gateway_delivery.py'].decode(),'_repository_profile')[0],include_attributes=False).encode()),
        'router_functional_ast_frozen_sha256':sha(without_docstrings(source_bytes['routers/agentic_governance.py']).encode()),
        'router_profile_docstring_before_sha256':sha(old.encode()),
        'router_profile_docstring_after_sha256':sha(new.encode()),
        'scope':'Preserve only exact fixed-Project profile functions/selection and corresponding accurate API prose; all other selected source behavior remains canonical.'},
      'preservation_checks':{'fixed_per_project_workspace_function_ast_exact':True,
       'fixed_per_project_delivery_bootstrap_profile_ast_exact':True,
       'fixed_per_project_context_profile_selection_exact':True,'profile_templates_excluded':True,
       'repository_registration_excluded':True,'router_functional_ast_equals_frozen_canonical':True,
       'live_af0_result_store_bytes_unchanged':True,'all_unselected_runtime_bytes_unchanged':True},
      'relay_target_group':'separate reviewed relay-only release; no relay leaf in this Core package',
      'scope_exclusions':['UI activation','Profile templates','ERP data/receipts/packets','Provider submissions','Maintenance controls'],
      'authenticated_browser_task_proof_required_for_Core_restart':True,
      'unsettled_admission_guards_required':True,'production_mutations':False,'deployment_actions':False}
    require(Path(__file__).read_bytes()==preparation_raw,'PREPARATION_SOURCE_CHANGED_DURING_CAPTURE')
    write(stage/'prepare_core_overlay.py',preparation_raw)
    dump(stage,'preflight-manifest.json',manifest)
    write(stage/'README.md',('Prepared Core overlay from '+args.source_commit+'.\n'
      'Fixed Project profiles, live result-store native projection, phase and unchanged files are preserved.\n'
      'Eleven selected runtime leaves include two new pure helpers (continuation and control). No whole-backend copy is permitted.\n'
      'This code-only package is not an activation baseline. The manifest records exact reviewed source and any pending historical compatibility work.\n'
      'No deployment, restart, model, ERP, database or admission action was performed.\n'
      'Core deployment still requires fresh authenticated admin /api/tasks proof, exact process/code/configuration pins, settled admissions, release then dispatch locks, backups and independent review.\n').encode())
    print(json.dumps({'stage':str(stage),'manifest_sha256':sha((stage/'preflight-manifest.json').read_bytes()),
                      'runtime_files':len(runtime),'selected_files':len(files),'new_files':sum(f['new_file'] for f in files)}))
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--source-pins',type=Path,required=True)
    parser.add_argument('--source-pins-sha256',required=True)
    parser.add_argument('--live-baseline',type=Path,required=True)
    parser.add_argument('--live-baseline-sha256',required=True)
    main(parser.parse_args())

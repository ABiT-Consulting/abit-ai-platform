"""Offline candidate fixtures; no application startup, provider or live state."""
from pathlib import Path
from datetime import datetime, timezone
import contextlib
import hashlib
import importlib
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest

stage=Path(__file__).resolve().parent
fixture=(stage/'fixture-app').resolve()
manifest=json.loads((stage/'preflight-manifest.json').read_text())
sha=lambda raw:hashlib.sha256(raw).hexdigest()
assert sha(Path(__file__).read_bytes())==manifest['test_runner_sha256'],'Frozen test runner changed'
validation_path=stage/'runtime_validation.py'
validation_raw=validation_path.read_bytes()
assert sha(validation_raw)==manifest['runtime_validator_sha256'],'Frozen runtime validator changed'
validator=types.ModuleType('frozen_runtime_validation');validator.__file__=str(validation_path)
exec(compile(validation_raw,str(validation_path),'exec'),validator.__dict__)
runtime_kind_counts={'RuntimePython':0,'RuntimeJSON':0}
for item in manifest['files']:
    raw=(fixture/'backend/open_webui'/item['relative_path']).read_bytes()
    assert sha(raw)==item['candidate_sha256']
    assert raw==(stage/'candidate/open_webui'/item['relative_path']).read_bytes()
    kind=validator.validate_runtime(item['relative_path'],raw,item['runtime_kind'])
    runtime_kind_counts[kind]+=1
assert runtime_kind_counts==manifest['runtime_kind_counts']=={'RuntimePython':13,'RuntimeJSON':1},'Exact runtime kinds changed'
replacement_scope=validator.replacement_summary(manifest['files'])
assert all(manifest.get(key)==value for key,value in replacement_scope.items()),'Exact already-current pair summary changed'
runtime_snapshot=json.loads((stage/'runtime-source-snapshot.json').read_text())
expected_runtime={item['relative_path']:item['sha256'] for item in runtime_snapshot}
for item in manifest['files']:
    if item['replacement_required']:
        expected_runtime[item['relative_path']]=item['candidate_sha256']
    else:
        assert expected_runtime[item['relative_path']]==item['original_sha256']==item['candidate_sha256'],'Already-current selected bytes changed'
assert len(runtime_snapshot)==147 and len(expected_runtime)==149,'Exact two-helper cohort changed'
assert sum(name.endswith('.py') for name in expected_runtime)==146
assert sum(name.endswith('.json') for name in expected_runtime)==3
assert manifest['unselected_original_count']==135
assert len(set(item['relative_path'] for item in runtime_snapshot)-set(item['relative_path'] for item in manifest['files']))==135
def assert_runtime_frozen():
    current={p.relative_to(fixture/'backend/open_webui').as_posix() for p in (fixture/'backend/open_webui/utils').rglob('*.py')}
    current|={p.relative_to(fixture/'backend/open_webui').as_posix() for p in (fixture/'backend/open_webui/utils/agentic_contracts').rglob('*.json')}
    current.add('routers/agentic_governance.py')
    assert current==set(expected_runtime),'Frozen runtime fixture inventory changed'
    assert all(sha((fixture/'backend/open_webui'/name).read_bytes())==pin for name,pin in expected_runtime.items()),'Frozen runtime fixture bytes changed'
assert_runtime_frozen()
test_snapshot=json.loads((stage/'test-source-snapshot.json').read_text())
guarded_test_basenames={Path(item['path']).name for item in test_snapshot
                        if Path(item['path']).suffix=='.py' and Path(item['path']).name!='__init__.py'}
def assert_tests_frozen():
    assert all(sha((fixture/item['path']).read_bytes())==item['sha256'] for item in test_snapshot), 'Frozen test or script dependency changed'
assert_tests_frozen()
sys.dont_write_bytecode=True
sys.path.insert(0,str(fixture/'backend/tests'))
executed=set()

LIVE_STATE_ROOTS=(
    Path('/srv/abit-dev-linux/abit-ai/data'),
    Path('/srv/abit-dev-linux/abit-ai/releases'),
    Path('/srv/abit-dev-linux/relay-runtime'),
    Path('/var/lib/abit-ai-erp-rollouts'),
    Path('/var/lib/abit-ai-agentic-gateway'),
)

def confine_metadata(original_stat, original_lstat, forbidden_roots):
    """Check each path component before metadata access, without a global bypass."""
    from collections import deque
    import stat

    roots=tuple(os.fspath(Path(root)) for root in forbidden_roots)

    def deny(path):
        value=os.fspath(path)
        if any(value==root or value.startswith(root+'/') for root in roots):
            raise AssertionError('Live state metadata forbidden by candidate fixture')

    def descriptor_path(descriptor):
        if type(descriptor) is not int or descriptor < 0:
            raise AssertionError('Unsupported metadata descriptor in candidate fixture')
        try:
            target=Path(os.readlink('/proc/self/fd/'+str(descriptor)))
        except (OSError, ValueError) as error:
            raise AssertionError('Unresolved metadata descriptor in candidate fixture') from error
        if not target.is_absolute():
            raise AssertionError('Non-filesystem metadata descriptor in candidate fixture')
        return target

    def checked(path, dir_fd):
        if isinstance(path,int):
            if dir_fd is not None:
                raise AssertionError('Descriptor metadata cannot carry dir_fd')
            absolute=descriptor_path(path)
        else:
            value=Path(os.fsdecode(os.fspath(path)))
            if dir_fd is not None:
                parent=descriptor_path(dir_fd)
            else:
                parent=Path.cwd()
            absolute=value if value.is_absolute() else parent/value
        deny(absolute)
        # Resolve through the original lstat, checking every prefix BEFORE the
        # call. Calling Path.resolve here would recurse through our wrappers.
        remaining=deque(absolute.parts[1:])
        resolved=Path('/')
        links=0
        while remaining:
            part=remaining.popleft()
            if part in ('','.'):
                continue
            if part=='..':
                resolved=resolved.parent
                deny(resolved)
                continue
            candidate=resolved/part
            deny(candidate)
            try:
                info=original_lstat(candidate)
            except (FileNotFoundError,NotADirectoryError):
                resolved=candidate
                continue
            if stat.S_ISLNK(info.st_mode):
                links+=1
                if links>40:
                    raise AssertionError('Metadata symlink resolution limit exceeded')
                target=Path(os.readlink(candidate))
                if target.is_absolute():
                    resolved=Path('/')
                    parts=target.parts[1:]
                else:
                    parts=target.parts
                remaining.extendleft(reversed(parts))
            else:
                resolved=candidate
        deny(resolved)

    def confined_stat(path,*,dir_fd=None,follow_symlinks=True):
        checked(path,dir_fd)
        return original_stat(path,dir_fd=dir_fd,follow_symlinks=follow_symlinks)

    def confined_lstat(path,*,dir_fd=None):
        checked(path,dir_fd)
        return original_lstat(path,dir_fd=dir_fd)

    return confined_stat,confined_lstat

def guard(event,arguments):
    if event=='exec':
        filename=getattr(arguments[0],'co_filename','')
        path=Path(filename)
        if path.is_absolute() and ('open_webui' in path.parts or path.name in guarded_test_basenames):
            resolved=path.resolve()
            if not resolved.is_relative_to(fixture):
                raise AssertionError('Source execution outside frozen candidate: '+str(resolved))
            executed.add(str(resolved.relative_to(fixture)))
    elif event=='open' and arguments and isinstance(arguments[0],str):
        path=Path(arguments[0])
        if path.is_absolute() and any(path.resolve().is_relative_to(root) for root in LIVE_STATE_ROOTS):
            raise AssertionError('Live state access forbidden by candidate fixture')
sys.addaudithook(guard)
os.stat,os.lstat=confine_metadata(os.stat,os.lstat,LIVE_STATE_ROOTS)
def denied(*args,**kwargs):
    raise AssertionError('Network and DNS denied in candidate fixture')
socket.socket.connect=denied
socket.socket.connect_ex=denied
socket.create_connection=denied
socket.getaddrinfo=denied
logging.disable(logging.CRITICAL)
temp_root=stage/'test-tmp'
temp_root.mkdir(mode=0o700,exist_ok=True)
os.environ['TMPDIR']=str(temp_root)
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['AGOV_FORMAT_BACKEND_FIXTURE']=str(fixture/'backend')
# Use the committed portable command analogue; never inherit a private input path.
os.environ.pop('ABIT_TEST_RECORDED_COMMAND_FILE',None)
tempfile.tempdir=None
selected=[
 'test_delivery_continuation_guards',
 'test_delivery_native_readiness',
 'test_denied_start_readiness',
 'test_delivery_control_recovery',
 'test_commit_readback_recovery',
 'test_agentic_artifact_safety',
 'test_agentic_gateway_contracts',
 'test_agentic_gateway_credential_paths',
 'test_agentic_bound_dispatch_context',
 'test_agentic_result_provenance',
 'test_agentic_architecture_review',
 'test_pm_architecture_boundaries.Boundaries.test_strict_default_and_opaque_phase_keep_original_verdict_and_native_event_shape',
 'test_pm_architecture_boundaries.Boundaries.test_missing_and_changed_own_review_bytes_cannot_publish',
 'test_pm_architecture_boundaries.Boundaries.test_stop_retains_raw_phase_and_local_result_without_publication',
 'test_pm_architecture_planning',
 'test_agentic_queued_wakeup',
 'test_runtime_profile_overlay',
 'test_agentic_executor_flow.ExecutorFlow.test_stop_keeps_recovered_reply_as_evidence_without_erp_completion',
 'test_agentic_executor_flow.ExecutorFlow.test_restart_recovers_original_reply_and_same_run_without_model_replay',
 'test_agentic_executor_flow.ExecutorFlow.test_original_reply_recovery_rejects_changed_business_context',
 'test_agentic_executor_flow.ExecutorFlow.test_lost_start_ack_recovers_same_run_before_first_model_submission',
 'test_agentic_executor_flow.ExecutorFlow.test_stop_inside_relay_queue_fences_original_without_model_call',
 'test_agentic_executor_flow.ExecutorFlow.test_saved_original_result_is_immutable_and_duplicate_save_is_idempotent',
 'test_agentic_executor_flow.ExecutorFlow.test_gate_revoked_after_claim_before_submission_never_sends_model',
 'test_agentic_executor_flow.ExecutorFlow.test_paused_project_rejects_before_model',
 'test_agentic_relay_scope.RelayScope.test_saved_recovery_requires_exact_durable_receipt_without_new_admission',
 'test_agentic_relay_scope.RelayScope.test_expired_admission_proofs_allow_only_exact_original_receipt_observation',
 'test_agentic_start_recovery.StartRecovery.test_reclaim_rechecks_controls_and_exact_original_receipt',
 'test_agentic_pm_review.PMReview.test_actual_pm_turn_follows_verified_qa_without_reopening_task_or_duplicate_run',
 'test_agentic_planning.Planning.test_pm_ready_with_action_requests_is_a_valid_planning_checkpoint',
 'test_agentic_planning.Planning.test_published_pm_dependency_continuations_preserve_original_architect_plan',
]
required=['test_returned_reply_format_correction.ReturnedFormat','test_registered_command_contract']
assert manifest.get('required_test_selection')==required,'Required format/command cases changed'
selected.extend(required)
selected.extend(manifest.get('additional_test_selection', []))
excluded={
 'test_actual_test_namespace_denies_host_files_network_and_git_mutation':'Needs service namespace; outside this fixture environment.',
 'test_context_reports_resolved_template_without_exposing_server_paths_or_fallback':'Tests the explicitly excluded future profile-template feature.',
}
def cases(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from cases(item)
        else:yield item
started=datetime.now(timezone.utc).isoformat()
with tempfile.TemporaryDirectory(prefix='isolated-data-') as isolated:
    os.environ['DATA_DIR']=isolated
    from test_agentic_gateway_sessions import Gateway
    gateway=[Gateway(name) for name in unittest.defaultTestLoader.getTestCaseNames(Gateway) if name not in excluded]
    all_cases=gateway+list(cases(unittest.defaultTestLoader.loadTestsFromNames(selected)))
    unique={}
    for case in all_cases:unique.setdefault(case.id(),case)
    actual_ids=list(unique)
    assert len(actual_ids)==len(set(actual_ids))
    import test_agentic_controls_events as fixture_module
    assert fixture_module.ROOT.resolve()==(fixture/'backend/open_webui/utils').resolve()
    with (stage/'backend-tests.log').open('w') as log,contextlib.redirect_stderr(log):
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.TestSuite(unique.values()))
origins={}
for name,module in list(sys.modules.items()):
    filename=getattr(module,'__file__',None)
    if filename and (name.startswith('open_webui.utils.') or Path(filename).name in guarded_test_basenames):
        resolved=Path(filename).resolve()
        assert resolved.is_relative_to(fixture),(name,str(resolved))
        origins[name]=str(resolved.relative_to(fixture))
assert_runtime_frozen()
assert_tests_frozen()
assert sha(Path(__file__).read_bytes())==manifest['test_runner_sha256'],'Frozen test runner changed during verification'
assert sha(validation_path.read_bytes())==manifest['runtime_validator_sha256'],'Runtime validator changed during verification'
assert all(sha((fixture/'backend/open_webui'/v['relative_path']).read_bytes())==v['candidate_sha256'] for v in manifest['files'])
receipt={
 'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),
 'source_commit':manifest['source_commit'],'runner_sha256':sha(Path(__file__).read_bytes()),
 'runtime_validator_sha256':sha(validation_path.read_bytes()),'runtime_kind_counts':runtime_kind_counts,
 **replacement_scope,
 'private_recorded_command_used':False,'format_backend_fixture':str(fixture/'backend'),'tests':result.testsRun,'failures':len(result.failures),
 'errors':len(result.errors),'skipped':len(result.skipped),'successful':result.wasSuccessful(),
 'unique_test_ids':actual_ids,'selection':selected,'excluded_gateway_cases':excluded,
 'phase_catalog_case':'Dedicated live-profile actual-caller case replaces source-template-registration assertion; no source test modified.',
 'network_dns_denied':True,'live_state_access_denied':True,'temporary_data':True,
 'live_state_guard_scope':{
  'covered':['file open','stat','lstat','Path metadata','bytes and relative paths','file descriptors and dir_fd','symlink destinations'],
  'not_claimed':['direct listdir/scandir confinement','OS-level sandboxing'],
 },
 'source_execution_guard':True,'executed_files':sorted(executed),'loaded_module_origins':origins,
 'runtime_snapshot_sha256':sha((stage/'runtime-source-snapshot.json').read_bytes()),
 'test_snapshot_sha256':sha((stage/'test-source-snapshot.json').read_bytes()),
 'candidate_hashes_unchanged':True,'all_runtime_fixture_hashes_unchanged':True,'all_test_fixture_hashes_unchanged':True,
 'runtime_fixture_files':len(expected_runtime),'log':'backend-tests.log',
 'log_sha256':sha((stage/'backend-tests.log').read_bytes()),
}
(stage/'backend-test-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({k:receipt[k] for k in ('tests','failures','errors','skipped','successful','candidate_hashes_unchanged')}))
if not result.wasSuccessful():
    print(json.dumps({'failed_test_ids':[test.id() for test,_ in result.failures+result.errors]}))
    print((stage/'backend-tests.log').read_text()[-16000:])
sys.exit(not result.wasSuccessful())

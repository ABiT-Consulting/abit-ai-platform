"""Future 71->72 profile/prompt/artifact/local-retention increment. Pending bindings DENY all actions.

Only Main and the proven-idle QA service can be stopped/started once each. No database restore,
config merge, ERP action, profile registration, admission change or job replay.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import types

S = Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-full-20261113')
D = S / 'core-profile-prompt-gateway-increment-v8-qa-preflight71-20261115'
SOURCE = Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source')
ROOT = Path('/srv/abit-dev-linux/abit-ai')
ADOPTED_EXTERNAL_FEED = Path('/srv/abit-dev-linux/relay-runtime/service/aibit_dot_feed.cjs')
ADOPTED_EXTERNAL_FEED_SHA = 'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53'
RUNTIME = ROOT / 'releases/20261031-live-0.11.4/runtime/open_webui'
MAIN_UNIT = 'abit-ai.service'
QA_UNIT = 'abit-ai-erp-review-qa.service'
QA_ENTRY = ROOT / 'qa-service/qa-service.py'
ALLOWED = {'utils/agentic_project_profiles.py', 'utils/agentic_gateway_delivery.py',
           'utils/agentic_gateway_operations.py', 'routers/agentic_governance.py', 'utils/agentic_dependency_verification.py',
           'utils/agentic_executor.py', 'utils/agentic_gateway_sessions.py', 'utils/agentic_result_provenance.py'}
BASELINE_SHA = '365fea0f99575d6d6da441f8584fbf3b2cb33f2a16267746706766ec9e52c2ca'
BASELINE_SOURCE_COMMITS = {'routers/agentic_governance.py': '37ae81eafceecd165e33a263d3967255a276968f', 'utils/agentic_gateway_operations.py': '37ae81eafceecd165e33a263d3967255a276968f', 'utils/agentic_recovery.py': '37ae81eafceecd165e33a263d3967255a276968f'}
BASELINE_RECEIPT = Path('/var/lib/abit-ai-erp-rollouts/governed-qa-preflight-retry-v2-20261115/core-apply/completed-receipt.json')
BASELINE_RECEIPT_SHA = '11aaa59fb6ca6e615bb8914bd79ddb1639ecb0239d6599ea3fa728d1ef3cffba'
HISTORY_SHA = '1d1804e48c1a23fe6f86ee796e5756bd1c16a44376848e0a83788d79002b2052'
MAINTENANCE_SHA = '7bacd6aae0ef7078e675f789f4d531dd10ec3393f60fb4a42d801c12512918b3'
HELPERS = {
    'lifecycle': ('core-8c-increment-proposal/apply_increment_v2.py', 'de71c225d025bfdfdcd3edb2e6b490ff3b4f01162e1dddc4e2414fc672e3a094'),
    'cas': ('core-typed-increment-proposal/apply_typed_increment.py', '42115b40228ecad434b86f4f19e736811387560a66f4078b49136ccdfe9952f1'),
    'base': ('release-proposal/apply_core_overlay.py', '64c611b03581593d27b5f954f97abd737cf05190dd273c41dd287b788493af73'),
    'finish': ('release-proposal/finish_release_hold.py', '28a155b2cc5d5646bb04d8678a02bd4770325ba8a8b67002a406db21e5d1c7fb'),
}

QA_RUNTIME_PINS = {'/srv/abit-dev-linux/abit-ai/qa-service/modules/erp_assigned_qa.py': 'cfb72a9542316023dfb2e985b25ec0e3772bfaf9edc42874974085891cd62a71', '/srv/abit-dev-linux/abit-ai/qa-service/modules/erp_pending_review_qa.py': '8de4c6483d911ccf50610ebdc62b52fd5a7eda4c44c7121796b31630f814dc59'}


def need(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, expected=None, *, private=False):
    need(path.resolve() == path, 'CANONICAL_FIXED_FILE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 2_000_000,
             'BOUNDED_SINGLE_LINK_REGULAR_INPUT_REQUIRED')
        need(not private or (info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o600),
             'ROOT_PRIVATE_PROOF_REQUIRED')
        raw = bytearray()
        while len(raw) <= 2_000_000:
            block = os.read(fd, min(65536, 2_000_001 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        need(len(raw) == info.st_size and all(getattr(info, k) == getattr(after, k) for k in
             ('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
             and (expected is None or sha(raw) == expected), 'EXACT_INPUT_BYTES_OR_METADATA_CHANGED')
        return bytes(raw)
    finally:
        os.close(fd)


def module(name, path, pin):
    raw = read(path, pin)
    value = types.ModuleType(name); value.__file__ = str(path); sys.modules[name] = value
    exec(compile(raw, str(path), 'exec'), value.__dict__)
    return value


def validate_bindings(value):
    # This check runs before imports, locks, directories or any service command.
    need(value.get('schema_version') == 1 and value.get('state') == 'REVIEWED_FUTURE_BINDINGS'
         and re.fullmatch('[a-f0-9]{40}', str(value.get('source_commit', '')))
         and value['source_commit'] != '7d69ed7d076180c148fb0a489e5f1e916c694c82'
         and all(re.fullmatch('[a-f0-9]{64}', str(value.get(k, ''))) for k in
                 ('candidate_manifest_sha256', 'first_flow_receipt_sha256'))
         and type(value.get('sealed_epoch')) is int and value['sealed_epoch'] >= 5,
         'PENDING_FUTURE_COMMIT_EPOCH_OR_FIRST_FLOW_BINDINGS_DENY')
    main = value.get('main')
    need(isinstance(main, dict) and type(main.get('pid')) is int and main['pid'] > 1
         and re.fullmatch('[0-9]+', str(main.get('process_start_ticks', '')))
         and re.fullmatch('[a-f0-9]{64}', str(main.get('entrypoint_sha256', ''))),
         'EXACT_FUTURE_CURRENT_MAIN_REQUIRED')
    qa = value.get('qa')
    need(isinstance(qa, dict) and type(qa.get('pid')) is int and qa['pid'] > 1
         and re.fullmatch('[0-9]+', str(qa.get('process_start_ticks', '')))
         and re.fullmatch('[a-f0-9]{64}', str(qa.get('entrypoint_sha256', ''))),
         'EXACT_FUTURE_CURRENT_IDLE_QA_REQUIRED')
    need(isinstance(value.get('untouched_processes'), dict)
         and {'mcp_auth', 'mcp_backend'} <= set(value['untouched_processes'])
         and 'qa' not in value['untouched_processes']
         and len(value['untouched_processes']) <= 8, 'EXACT_UNTOUCHED_PROCESS_BINDINGS_REQUIRED')
    first = Path(value.get('first_flow_receipt', ''))
    dest = Path(value.get('operation_directory', ''))
    need(first.is_absolute() and first.is_relative_to('/var/lib/abit-ai-erp-rollouts')
         and dest.parent == Path('/var/lib/abit-ai-erp-rollouts/core-profile-increments')
         and re.fullmatch('profile-[a-f0-9]{12}', dest.name), 'FIXED_PRIVATE_FUTURE_OPERATION_PATHS_REQUIRED')
    return value


def process(expected):
    p = Path('/proc') / str(expected['pid'])
    info = p.stat(); before = p.joinpath('stat').read_text().split(') ', 1)[1].split()
    need(info.st_uid == expected['uid'] and before[0] != 'Z'
         and before[19] == str(expected['start_ticks'])
         and sha(p.joinpath('cmdline').read_bytes()) == expected['argv_sha256']
         and expected['cgroup'] in p.joinpath('cgroup').read_text().splitlines(),
         'UNTOUCHED_PROCESS_CHANGED')
    need(p.joinpath('stat').read_text().split(') ', 1)[1].split()[19] == before[19],
         'PROCESS_CHANGED_DURING_READ')
    return dict(expected)


def preserved_processes(binding):
    return {name: process(value) for name, value in binding['untouched_processes'].items()}


def channel_status():
    """Existing local controller authentication; no key/header/body export."""
    path = ROOT/'config/erp-assignment/control-token'; info = path.lstat()
    need(info.st_uid == 0 and info.st_gid == 988 and stat.S_IMODE(info.st_mode) == 0o640,
         'EXISTING_CONTROL_KEY_METADATA_CHANGED')
    key = read(path).strip(); need(32 <= len(key) <= 4096, 'EXISTING_CONTROL_KEY_REQUIRED')
    connection = http.client.HTTPConnection('127.0.0.1',8888,timeout=5)
    try:
        connection.request('GET','/api/v1/agentic/erp-channel/status',headers={'X-ABIT-ERP-Control':key.decode()})
        reply=connection.getresponse(); raw=reply.read(65537)
        need(reply.status == 200 and len(raw) <= 65536, 'ACTUAL_LOCAL_AUTHENTICATED_CHANNEL_STATUS_REQUIRED')
        value=json.loads(raw)
        need(value.get('enabled') is True and value.get('connected') is True and value.get('inflight') == 0,
             'EXISTING_NATIVE_CHANNEL_NOT_CONNECTED_AND_IDLE')
        return {key:value[key] for key in ('enabled','connected','bridge_id','site','service_user','inflight')}
    finally:
        key=None; connection.close()


def protected_files(cas):
    paths = [Path('/etc/systemd/system')/(unit) for unit in (MAIN_UNIT,QA_UNIT)]
    paths += [ROOT/'config/erp-assignment/resolver.json', ROOT/'config/runtime-environment.json', ADOPTED_EXTERNAL_FEED]
    need(sha(read(ADOPTED_EXTERNAL_FEED)) == ADOPTED_EXTERNAL_FEED_SHA, 'ADOPTED_EXTERNAL_RELAY_FEED_CHANGED')
    for unit in (MAIN_UNIT,QA_UNIT):
        raw=subprocess.check_output(['/usr/bin/systemctl','show',unit,'--property=DropInPaths','--value'],timeout=5)
        for text in raw.decode().split():
            path=Path(text)
            need(path.parent in {Path('/etc/systemd/system')/(unit+'.d'),Path('/run/systemd/system')/(unit+'.d')}
                 and path.suffix == '.conf', 'EXACT_MAIN_OR_QA_DROPIN_SCOPE_REQUIRED')
            paths.append(path)
    need(len(paths) <= 24 and len(paths) == len(set(paths)), 'BOUNDED_DISTINCT_PROTECTED_PATHS_REQUIRED')
    result={}
    for path in paths:
        value=read(path); info=path.lstat()
        result[str(path)]={'sha256':sha(value),'metadata':{k:getattr(info,k) for k in
            ('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')},
            'xattrs':cas.xattrs(path)}
    return result


def future_source(binding, manifest):
    # Immutable selected exports, never a whole mutable canonical checkout.
    commit=binding['source_commit']
    need(commit==manifest['source_commit']and re.fullmatch('[a-f0-9]{40}',commit), 'FUTURE_SELECTED_EXPORT_COMMIT_REQUIRED')
    subprocess.run(['git','-C',str(SOURCE),'cat-file','-e',commit+'^{commit}'],check=True,timeout=10,capture_output=True)
    prefix=manifest['git_export_prefix']
    need(prefix=='docs/evidence/2026-11-15-profile72-qa-preflight-v8','EXACT_SELECTED_EXPORT_NAMESPACE_REQUIRED')
    for row in manifest['files']:
        need(row['git_blob_path']==prefix+'/runtime/'+row['runtime_relative_path'],'EXACT_SELECTED_RUNTIME_EXPORT_PATH_REQUIRED')
        raw=read(D/'source'/row['source_relative_path'],row['after_sha256'])
        blob=subprocess.check_output(['git','-C',str(SOURCE),'show',commit+':'+row['git_blob_path']],timeout=10)
        need(raw==blob,'FUTURE_COMMITTED_EXPORT_DIFFERS_FROM_REVIEW');compile(raw,row['source_relative_path'],'exec')
    need(set(manifest['required_test_sources'])=={'backend/tests/test_agentic_project_profiles.py','backend/tests/test_agentic_dependency_verification.py'},'EXACT_PROFILE_AND_DEPENDENCY_TEST_SOURCES_REQUIRED')
    for relative,pin in manifest['required_test_sources'].items():
        path=manifest['required_test_git_paths'][relative]
        need(path==prefix+'/tests/'+Path(relative).name,'EXACT_SELECTED_TEST_EXPORT_PATH_REQUIRED')
        blob=subprocess.check_output(['git','-C',str(SOURCE),'show',commit+':'+path],timeout=10)
        need(sha(blob)==pin and blob==read(D/'source'/relative),'EXACT_COMMITTED_PORTABLE_TEST_CHANGED')
    need(manifest['test_sha256']==manifest['required_test_sources'][manifest['test_source_relative_path']], 'PORTABLE_TEST_BINDING_CHANGED')
    need(sha(read(RUNTIME/'utils/agentic_maintenance.py'))==MAINTENANCE_SHA,'UNCHANGED_MAINTENANCE_HELPER_REQUIRED')


def qa_state():
    raw=subprocess.check_output(['/usr/bin/systemctl','show',QA_UNIT,
        '--property=ActiveState,SubState,MainPID,NRestarts'],text=True,timeout=5)
    state=dict(line.split('=',1) for line in raw.splitlines())
    need(set(state)=={'ActiveState','SubState','MainPID','NRestarts'},'EXACT_QA_UNIT_METADATA_REQUIRED')
    return state


def main_restarts_zero():
    value=subprocess.check_output(['/usr/bin/systemctl','show',MAIN_UNIT,'--property=NRestarts','--value'],text=True,timeout=5).strip()
    need(value=='0','MAIN_AUTOMATIC_RESTARTS_NOT_ZERO')


def qa_identity(hold,binding):
    expected=binding['qa'];state=qa_state();p=Path('/proc')/str(expected['pid'])
    need(state['ActiveState']=='active' and state['SubState']=='running' and state['MainPID']==str(expected['pid'])
         and state['NRestarts']=='0','EXACT_IDLE_QA_UNIT_REQUIRED')
    info=p.stat();start=p.joinpath('stat').read_text().split(') ',1)[1].split()[19]
    need(info.st_uid==999 and start==str(expected['process_start_ticks'])
         and sha(read(QA_ENTRY))==expected['entrypoint_sha256']
         and str(QA_ENTRY).encode() in p.joinpath('cmdline').read_bytes().split(b'\0'),
         'EXACT_IDLE_QA_PID_BIRTH_ENTRYPOINT_REQUIRED')
    return dict(expected)


def qa_idle():
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect((ROOT/'data/erp-pending-review-qa.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        count=db.execute("SELECT count(*) FROM review WHERE phase!='done'").fetchone()[0]
    need(count==0,'QA_MUST_BE_IDLE_WITH_HISTORY_PRESERVED')
    return {'non_done_review_rows':0,'historical_rows_required_zero':False}


def qa_service_attempt(action,timeout):
    need(action in {'stop','start'},'EXACT_QA_STOP_OR_START_REQUIRED')
    try:
        result=subprocess.run(['/usr/bin/systemctl',action,QA_UNIT],capture_output=True,timeout=timeout)
        return {'unit':QA_UNIT,'action':action,'attempts':1,'returncode':result.returncode,
                'acknowledgement':'zero' if result.returncode==0 else 'nonzero',
                'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr)}
    except subprocess.TimeoutExpired as error:
        return {'unit':QA_UNIT,'action':action,'attempts':1,'returncode':None,
                'acknowledgement':'timeout_unknown','stdout_sha256':sha(error.stdout or b''),
                'stderr_sha256':sha(error.stderr or b'')}


def qa_stopped(hold,binding):
    deadline=time.monotonic()+60
    while True:
        state=qa_state();group=Path('/sys/fs/cgroup/system.slice')/QA_UNIT
        empty=not group.exists() or all(not p.read_text().strip() for p in group.rglob('cgroup.procs'))
        if state['ActiveState']=='inactive' and state['MainPID']=='0' and empty and not(Path('/proc')/str(binding['qa']['pid'])).exists():
            return {'actual_state':state,'original_pid_gone':True,'cgroup_empty':True,'second_Stop_calls':0}
        need(time.monotonic()<deadline,'ORIGINAL_QA_STOP_UNSETTLED_NO_SECOND_STOP');time.sleep(1)


def stable_new_process(pid, entry, entry_sha256):
    """Credit only the service principal after exec, with two stable reads."""
    p=Path('/proc')/str(pid)
    def sample():
        value={'pid':pid,'uid':p.stat().st_uid,'process_start_ticks':p.joinpath('stat').read_text().split(') ',1)[1].split()[19],
               'argv_sha256':sha(p.joinpath('cmdline').read_bytes()),'cgroup_sha256':sha(p.joinpath('cgroup').read_bytes())}
        need(value['uid']==999 and int(value['process_start_ticks'])>0 and sha(read(entry))==entry_sha256
             and str(entry).encode()in p.joinpath('cmdline').read_bytes().split(b'\0'),'SERVICE_NOT_YET_STABLE_AFTER_EXEC')
        return value
    first=sample();time.sleep(.05);need(sample()==first,'SERVICE_EXEC_IDENTITY_CHANGED_DURING_READ');return first

def qa_started(hold,binding):
    deadline=time.monotonic()+45
    while True:
        state=qa_state()
        if state['ActiveState']=='active'and state['SubState']=='running'and state['MainPID']not in {'0',str(binding['qa']['pid'])}:
            try:
                pid=int(state['MainPID']);proof=stable_new_process(pid,QA_ENTRY,binding['qa']['entrypoint_sha256'])
                need(state['NRestarts']=='0','NEW_QA_RESTART_COUNT_CHANGED')
                return {'service':state,'pid':pid,'process_start_ticks':proof['process_start_ticks'],'entrypoint_sha256':binding['qa']['entrypoint_sha256'],'stable_after_exec':proof}
            except (RuntimeError,FileNotFoundError):pass
        need(time.monotonic()<deadline,'ORIGINAL_QA_START_UNSETTLED_NO_SECOND_START');time.sleep(1)


def file_sha(path):
    digest=hashlib.sha256();fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        with os.fdopen(fd,'rb') as source:
            for part in iter(lambda:source.read(1024*1024),b''):digest.update(part)
    except BaseException:raise
    return digest.hexdigest()


def backup_databases(dest):
    import sqlite3
    from contextlib import closing
    directory=dest/'consistent-databases';directory.mkdir(mode=0o700);rows=[]
    names=('abit-telegram.sqlite','aibit-chatgpt-relay.sqlite','webui.db','agentic-governance.sqlite',
           'codex-workspace-runs.sqlite','erp-pending-review-qa.sqlite','erp-task-resolution.sqlite',
           'abitdot-primary.sqlite','erp-dot-issues.sqlite','abit-slack.sqlite','maintenance-admission/gate.sqlite')
    for name in names:
        src=ROOT/'data'/name;target=directory/name.replace('/','__');fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target)) as copy:
            source.backup(copy);need(copy.execute('pragma quick_check').fetchone()==('ok',),'CONSISTENT_BACKUP_CHECK_FAILED');copy.commit()
        rows.append({'database':name,'bytes':target.stat().st_size,'sha256':file_sha(target)})
    return {'databases':rows,'backup_count':11,'database_restores':0}


def stopped(lifecycle, hold, finish, dest, binding, retained, untouched):
    deadline = time.monotonic() + 60
    while True:
        state = hold.services()[MAIN_UNIT]
        group = Path('/sys/fs/cgroup/system.slice/abit-ai.service')
        empty = not group.exists() or all(not p.read_text().strip() for p in group.rglob('cgroup.procs'))
        if state['ActiveState'] == 'inactive' and state['MainPID'] == '0' and empty and not (
                Path('/proc') / str(binding['main']['pid'])).exists():
            return lifecycle.stopped_readback(hold, finish, dest, binding['main'], binding['sealed_epoch'], retained)
        need(time.monotonic() < deadline, 'ORIGINAL_STOP_UNSETTLED_RECONCILE_NO_RETRY')
        need(preserved_processes(binding) == untouched, 'UNTOUCHED_SERVICES_CHANGED_DURING_STOP')
        lifecycle.sealed(hold, binding['sealed_epoch']); time.sleep(1)



def portable_runtime_manifest(after, source_commit):
    need(len(after) == 72 and {key: after.get(key) for key in QA_RUNTIME_PINS} == QA_RUNTIME_PINS,
         'EXACT_SEVENTY_TWO_FILE_CORE_QA_COHORT_REQUIRED')
    core = {}
    for path, pin in after.items():
        if path in QA_RUNTIME_PINS:
            continue
        item = Path(path)
        need(item.is_absolute() and '..' not in item.parts and item.is_relative_to(RUNTIME)
             and re.fullmatch('[a-f0-9]{64}', pin), 'CANONICAL_CORE_COHORT_PATH_REQUIRED')
        core[str(item.relative_to(RUNTIME))] = pin
    need(len(core) == 70, 'EXACT_SEVENTY_CORE_FILES_REQUIRED')
    return {'schema_version': 1, 'state': 'CORE_PROFILE_INCREMENT_RUNTIME_VERIFIED',
            'source_commit': source_commit, 'live_root': str(RUNTIME), 'files': core,
            'preserved_qa_files': dict(QA_RUNTIME_PINS), 'managed_cohort_count': 72, 'installed': True}

def main(args):
    need(args.execute != args.preflight_only and os.geteuid() == 0
         and sha(read(Path(__file__))) == args.controller_sha256, 'REVIEWED_ROOT_CONTROLLER_REQUIRED')
    binding = validate_bindings(json.loads(read(args.bindings, args.bindings_sha256, private=True)))
    need(BASELINE_SHA is not None and BASELINE_SOURCE_COMMITS is not None
         and BASELINE_RECEIPT is not None and BASELINE_RECEIPT_SHA is not None,
         'PENDING_ACTUAL_V3_BASELINE_AND_ROOT_SUCCESS_RECEIPT_DENY')
    manifest = json.loads(read(D/'candidate-manifest.source-bound.json', binding['candidate_manifest_sha256']))
    need(manifest.get('state') == 'REVIEWED_FUTURE_CANDIDATE' and manifest['before_runtime_count'] == 71
         and manifest['after_runtime_count'] == 72 and len(manifest['files']) == 8
         and {r['runtime_relative_path'] for r in manifest['files']} == ALLOWED,
         'EXACT_EIGHT_FILE_FUTURE_CANDIDATE_REQUIRED')
    future_source(binding, manifest)
    os.umask(0o077); cache = Path(tempfile.mkdtemp(prefix='abit-profile-pinned-', dir='/run'))
    sys.pycache_prefix = str(cache); sys.dont_write_bytecode = True
    helpers = {name: module('future_profile_'+name, S/path, pin) for name,(path,pin) in HELPERS.items()}
    lifecycle, cas, base, finish = (helpers[k] for k in ('lifecycle','cas','base','finish'))
    history = module('future_profile_history', D/'history_guard.py', HISTORY_SHA)
    hold = base.load_hold(); lock = finish.private_lock('/run/lock/abit-ai-agentic-release.lock', 0)
    dispatch = None
    try:
        with hold.as_service():
            dispatch = finish.private_lock(hold.DATA/'erp-dot-dispatch.lock', os.geteuid())
        baseline = json.loads(read(D/'complete71-before.json', BASELINE_SHA))
        before = {r['runtime_path']: r['sha256'] for r in baseline['files']}
        completed_baseline = json.loads(read(BASELINE_RECEIPT, BASELINE_RECEIPT_SHA, private=True))
        need(len(before) == 71 and baseline.get('source_commits') == BASELINE_SOURCE_COMMITS
             and completed_baseline.get('ok') is True and completed_baseline.get('runtime_manifest') == baseline
             and lifecycle.matches_hashes(base, before), 'CURRENT_COMPLETE71_BASELINE_CHANGED')
        epoch = binding['sealed_epoch']; lifecycle.sealed(hold, epoch); lifecycle.security()
        args.main_pid = binding['main']['pid']; args.main_start_ticks = binding['main']['process_start_ticks']
        original = lifecycle.identity(hold, args); need(original == binding['main'], 'FUTURE_MAIN_IDENTITY_CHANGED')
        qa_original = qa_identity(hold, binding); qa_idle(); main_restarts_zero()
        untouched = preserved_processes(binding)
        hold.browser_tasks_proof(args, {MAIN_UNIT: original})
        need(hold.health(8888).get('status') is True, 'CURRENT_MAIN_HEALTH_REQUIRED')
        first_raw = read(Path(binding['first_flow_receipt']), binding['first_flow_receipt_sha256'], private=True)
        closed = history.assert_closed_first_flow(hold, json.loads(first_raw)); idle = history.assert_idle(hold)
        # SEALED rejects even terminal QA recovery admissions. Already-finished
        # receipt bookkeeping stays in this exact nonzero-history snapshot.
        protected = history.snapshot(hold); unit_config = protected_files(cas); channel = channel_status()
        files = []
        for row in manifest['files']:
            target = RUNTIME / row['runtime_relative_path']; old = before.get(str(target))
            need(row['runtime_path'] == str(target) and row['source_relative_path'] == 'backend/open_webui/'+row['runtime_relative_path']
                 and row['before_sha256'] == old and base.sha(target) == old, 'EXACT_BEFORE_TARGET_CHANGED')
            raw = read(D/'source'/row['source_relative_path'], row['after_sha256'])
            info = target.lstat() if old is not None else types.SimpleNamespace(st_uid=999,st_gid=988,st_mode=0o644)
            need(old is None or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'REGULAR_EXISTING_RUNTIME_REQUIRED')
            files.append((row,target,raw,info,cas.xattrs(target) if old is not None else {}))
        need(sum(row['before_sha256'] is None for row in manifest['files']) == 1, 'EXACT_ONE_NEW_MODULE_REQUIRED')
        dest = Path(binding['operation_directory']); need(not os.path.lexists(dest), 'ORIGINAL_OPERATION_EXISTS_OBSERVE_ONLY')
        if args.preflight_only:
            print(json.dumps({'state':'FUTURE_CORE_PROFILE_READONLY_PREFLIGHT_PASSED','source_commit':binding['source_commit'],
                'sealed_epoch':epoch,'history_sha256':protected['sha256'],'closed_first_flow':closed,
                'private_operation_created':False,'service_actions':0,'runtime_writes':0})); return
        need(dest.parent.resolve() == dest.parent and dest.parent.stat().st_uid == 0
             and stat.S_IMODE(dest.parent.stat().st_mode) == 0o700, 'PRESTAGED_ROOT_PRIVATE_OPERATION_PARENT_REQUIRED')
        dest.mkdir(mode=0o700)
        save = lambda name,value: history.private_new(dest/name, history.canonical(value))
        save('release-intent.json',{'schema_version':1,'source_commit':binding['source_commit'],'main':original,
            'controller_sha256':args.controller_sha256,'bindings_sha256':args.bindings_sha256,'sealed_epoch':epoch,
            'first_flow_receipt_sha256':binding['first_flow_receipt_sha256'],'closed_first_flow':closed,
            'untouched_processes':untouched,'qa':qa_original,'qa_idle':qa_idle(),'history':protected,'idle':idle,'unit_configuration':unit_config,
            'original_channel_status':channel,'database_restore_allowed':False})
        history.private_new(dest/'first-flow-closed.private.json',first_raw)
        for index,(path,proof) in enumerate(unit_config.items()):
            history.private_new(dest/('protected-file-'+str(index)+'.private'),read(Path(path),proof['sha256']))
        for index,(row,target,raw,info,attrs) in enumerate(files):
            if row['before_sha256'] is not None: history.private_new(dest/(str(index)+'.before'),read(target,row['before_sha256']))
            history.private_new(dest/(str(index)+'.after'),raw)
            save(str(index)+'.metadata.json',{'target':str(target),'before':row['before_sha256'],'after':row['after_sha256'],
                 'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),'xattrs':attrs})
        need(history.snapshot(hold) == protected, 'HISTORY_CHANGED_BEFORE_STOP')
        need(protected_files(cas)==unit_config and channel_status()==channel,'PROTECTED_CONFIGURATION_OR_CHANNEL_CHANGED')
        lifecycle.sealed(hold,epoch); lifecycle.identity(hold,args); history.assert_idle(hold)
        hold.browser_tasks_proof(args,{MAIN_UNIT:original}); need(preserved_processes(binding)==untouched,'UNTOUCHED_PROCESS_CHANGED_BEFORE_STOP')
        need(not os.path.lexists(finish.MARKER) and not os.path.lexists(finish.DROPIN),'UNOWNED_MAIN_FENCE_PRESERVED')
        finish.install_start_fence(hold,dest,original)
        retained = hold.retained_inventory()['metadata_sha256']
        save('stop-attempt.json',{'unit':MAIN_UNIT,'original':original,'attempts':1})
        ack = lifecycle.service_attempt('stop',MAIN_UNIT,105); save('stop-acknowledgement.json',ack)
        save('stop-readback.json',stopped(lifecycle,hold,finish,dest,binding,retained,untouched))
        need(qa_identity(hold,binding)==qa_original,'ORIGINAL_QA_CHANGED_BEFORE_STOP'); qa_idle()
        save('qa-stop-attempt.json',{'unit':QA_UNIT,'original':qa_original,'attempts':1})
        qa_ack=qa_service_attempt('stop',50);save('qa-stop-acknowledgement.json',qa_ack)
        save('qa-stop-readback.json',qa_stopped(hold,binding))
        save('consistent-database-backups.json',backup_databases(dest))
        need(history.snapshot(hold,dest) == protected, 'FULL_LOGICAL_HISTORY_CHANGED_DURING_STOP')
        bytecode=dest/'pycache-originals';bytecode.mkdir(mode=0o700);retained_bytecode=[]
        for row,target,raw,info,attrs in files:
            if row['before_sha256'] is None: lifecycle.create_only(base,target,raw,info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))
            else: cas.replace_preserving_metadata(base,target,raw,info,row['before_sha256'],attrs)
            for pyc in sorted((target.parent/'__pycache__').glob(target.stem+'.*.pyc')):
                current=pyc.lstat();need(stat.S_ISREG(current.st_mode) and current.st_nlink==1 and not pyc.is_symlink(),
                                        'OWNED_BYTECODE_SCOPE_CHANGED')
                retained_bytecode.append({'original_path':str(pyc),'sha256':file_sha(pyc),'bytes':current.st_size})
                need(not os.path.lexists(bytecode/pyc.name),'BYTECODE_BACKUP_COLLISION_DENIED')
                os.rename(pyc,bytecode/pyc.name)
        save('retained-owned-bytecode.json',retained_bytecode)
        after = {**before,**{r['runtime_path']:r['after_sha256'] for r in manifest['files']}}
        need(len(after)==72 and lifecycle.matches_hashes(base,after),'COMPLETE72_INSTALL_READBACK_CHANGED')
        need(history.snapshot(hold)==protected and preserved_processes(binding)==untouched,'HISTORY_OR_UNTOUCHED_PROCESS_CHANGED_BEFORE_START')
        save('installed.json',{'all72_files_verified':True,'history_sha256':protected['sha256'],'database_changes':False})
        lifecycle.security(); lifecycle.sealed(hold,epoch)
        fence_plan = hold.read_json(dest/'runtime-start-fence-plan.json')
        need(fence_plan['original_intent_sha256']==sha(read(dest/'release-intent.json')),'OWNED_FENCE_PLAN_CHANGED')
        finish.root_file(finish.DROPIN,0o644,finish.FENCE_BYTES)
        finish.root_file(finish.MARKER,0o600,finish.marker_bytes(hold,dest,original))
        finish.DROPIN.unlink(); subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=15)
        props=finish.fence_properties(); need(props.get('RefuseManualStart')=='no' and str(finish.DROPIN) not in props.get('DropInPaths','').split(),'OWNED_FENCE_RELEASE_UNCONFIRMED')
        finish.MARKER.unlink(); lifecycle.sealed(hold,epoch)
        save('start-attempt.json',{'unit':MAIN_UNIT,'attempts':1,'all72_verified':True})
        ack=lifecycle.service_attempt('start',MAIN_UNIT,45); save('start-acknowledgement.json',ack)
        deadline=time.monotonic()+45; state=None
        while state is None:
            try: state=lifecycle.started_state(hold,original['pid'])
            except Exception: state=None
            if state:
                try:
                    main_after=stable_new_process(int(state['MainPID']),ROOT/'deployment/run-production.py',binding['main']['entrypoint_sha256']);break
                except (RuntimeError,FileNotFoundError):state=None
            need(time.monotonic()<deadline,'ORIGINAL_START_UNSETTLED_OBSERVE_NO_RETRY'); time.sleep(1)
        save('qa-start-attempt.json',{'unit':QA_UNIT,'attempts':1,'all72_verified':True})
        qa_ack=qa_service_attempt('start',50);save('qa-start-acknowledgement.json',qa_ack)
        qa_after=qa_started(hold,binding);save('qa-start-readback.json',qa_after); main_restarts_zero()
        lifecycle.sealed(hold,epoch); lifecycle.security(); history.assert_idle(hold)
        need(history.snapshot(hold)==protected and lifecycle.matches_hashes(base,after)
             and preserved_processes(binding)==untouched and protected_files(cas)==unit_config,
             'POSTSTART_HISTORY_CODE_CONFIG_OR_UNTOUCHED_PROCESS_CHANGED')
        deadline=time.monotonic()+45; current_channel=None
        while current_channel is None:
            try: current_channel=channel_status()
            except Exception: current_channel=None
            if current_channel: break
            need(time.monotonic()<deadline,'ORIGINAL_NATIVE_CHANNEL_RECONNECT_UNCONFIRMED_NO_SERVICE_ACTION');time.sleep(1)
        need(current_channel==channel,'NATIVE_CHANNEL_IDENTITY_CHANGED_DURING_CORE_RESTART')
        runtime72 = portable_runtime_manifest(after, binding['source_commit'])
        save('runtime72-verified.private.json', runtime72)
        save('started.json',{'state':'CORE_PROFILE_INCREMENT_LIVE_ADMISSION_SEALED','source_commit':binding['source_commit'],
            'main':state,'all72_verified':True,'history_sha256':protected['sha256'],'closed_first_flow':closed,
            'actual_authenticated_channel_status':current_channel,'unit_configuration_preserved':True,
            'runtime72_manifest_sha256':sha(history.canonical(runtime72)),
            'CoreMain_Stop_calls':1,'CoreMain_Start_calls':1,'edge_MCP_actions':0,'database_restores':0,
            'ERP_actions':0,'profile_registrations':0,'model_requests':0,'Core_unseal_calls':0,'QA_Stop_calls':1,'QA_Start_calls':1,'QA_after':qa_after,'QA_idle_readback':qa_idle(),'Main_stable_after_exec':main_after})
        print(json.dumps({'state':'CORE_PROFILE_INCREMENT_LIVE_ADMISSION_SEALED','all72_verified':True,
                          'history_sha256':protected['sha256'],'main_pid':state['MainPID']}))
    finally:
        if dispatch is not None: os.close(dispatch)
        os.close(lock)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--controller-sha256',required=True)
    parser.add_argument('--bindings',type=Path,required=True); parser.add_argument('--bindings-sha256',required=True)
    parser.add_argument('--main-task-receipt',type=Path,required=True); parser.add_argument('--main-task-receipt-sha256',required=True)
    parser.add_argument('--execute',action='store_true'); parser.add_argument('--preflight-only',action='store_true')
    try: main(parser.parse_args())
    except Exception as error:
        print(json.dumps({'state':'HELD_PRESERVE_ORIGINAL_NO_AUTOMATIC_RETRY',
            'error_code':str(error) if isinstance(error,RuntimeError) else type(error).__name__,
            'instruction':'Observe original PID/unit/intent. Never repeat Stop/Start or restore history.'})); raise SystemExit(65)

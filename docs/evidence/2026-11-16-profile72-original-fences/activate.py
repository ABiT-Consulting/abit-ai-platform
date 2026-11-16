"""Code-only Core upgrade with idle producers and retained unresolved originals.

DRAINING denies new admission. It is deliberately not called SEALED: unknown
originals remain fenced in their unchanged journals. No provider settlement,
database restoration, configuration change, ERP write or model send is allowed.
"""
import argparse, fcntl, hashlib, json, os, sqlite3, stat, subprocess, time
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path('/srv/abit-dev-linux/abit-ai')
DATA = ROOT / 'data'
RUNTIME = ROOT / 'releases/20261031-live-0.11.4/runtime/open_webui'
PACKAGE = Path(__file__).resolve().parent
OUT = Path('/var/lib/abit-ai-erp-rollouts/core-profile72-original-fences-20261116')
MAIN, QA = 'abit-ai.service', 'abit-ai-erp-review-qa.service'
ALLOWED = {'routers/agentic_governance.py', 'utils/agentic_dependency_verification.py',
    'utils/agentic_executor.py', 'utils/agentic_gateway_delivery.py',
    'utils/agentic_gateway_operations.py', 'utils/agentic_gateway_sessions.py',
    'utils/agentic_project_profiles.py', 'utils/agentic_result_provenance.py',
    'utils/agentic_ownership.py', 'utils/automations.py'}
CANONICAL_BEFORE = '3eff8e8b3fa14409008e5df97cc623a14a10863485e32d4c99cdc054e7c8d0ca'
EXPECTED = {MAIN:(4132708,'73246413'), QA:(314775,'70770673')}
PROTECTED = {'Node':1552823, 'watchdog':2996798, 'Chrome':2998209,
    'egress':2997073, 'Xvfb':2997675, 'tunnel':3002922, 'MCP':2995736, 'auth':675402}

def need(value, code):
    if not value: raise RuntimeError(code)
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(path):
    need(not path.is_symlink() and path.is_file(), 'REGULAR_FILE_REQUIRED')
    return path.read_bytes()
def save(name, value):
    raw=value if isinstance(value,bytes) else canonical(value)
    fd=os.open(OUT/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(raw);f.flush();os.fsync(f.fileno())
def query(name, sql, args=()):
    with sqlite3.connect((DATA/name).as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        db.execute('PRAGMA query_only=ON')
        return db.execute(sql,args).fetchall()
def identity(pid):
    p=Path('/proc')/str(pid);s=p.joinpath('stat').read_text().split(') ',1)[1].split()
    need(s[0]!='Z','LIVE_PROCESS_REQUIRED')
    return {'pid':pid,'uid':p.stat().st_uid,'start':s[19],
        'argv_sha256':sha(p.joinpath('cmdline').read_bytes()),
        'cgroup_sha256':sha(p.joinpath('cgroup').read_bytes())}
def service(unit):
    raw=subprocess.check_output(['systemctl','show',unit,
        '--property=MainPID,ActiveState,SubState,NRestarts,Restart,KillMode,SendSIGKILL,TimeoutStopUSec'],text=True,timeout=10)
    return dict(line.split('=',1) for line in raw.splitlines())
def http(path, headers=None, port=8888):
    c=HTTPConnection('127.0.0.1',port,timeout=8)
    try:
        c.request('GET',path,headers=headers or {});r=c.getresponse();raw=r.read(100001)
        need(r.status==200 and len(raw)<=100000,'LOCAL_STATUS_UNAVAILABLE')
        return json.loads(raw)
    finally:c.close()
def producer_counts():
    h=http('/health',port=8810)
    need(h.get('authenticated') is True and h.get('target_ready') is True,'ORIGINAL_RELAY_NOT_READY')
    return {'relay_turns':h['active_relay_turns'],
        'running_automations':query('webui.db',"SELECT count(*) FROM automation_run WHERE status='running'")[0][0],
        'live_governed_runs':query('agentic-governance.sqlite',"SELECT count(*) FROM governance_invocation WHERE state='running'")[0][0],
        'dispatching_provider_rows':query('abitdot-primary.sqlite',"SELECT count(*) FROM dot_primary_runs WHERE status='dispatching'")[0][0],
        'legacy_QA_work':query('erp-pending-review-qa.sqlite',"SELECT count(*) FROM review WHERE phase!='done'")[0][0],
        'active_admissions':query('maintenance-admission/gate.sqlite',"SELECT count(*) FROM lease WHERE state='active'")[0][0]}
def originals():
    result={}
    for name,sql in [('maintenance-admission/gate.sqlite',"SELECT * FROM lease WHERE state='unresolved' ORDER BY id"),
        ('abitdot-primary.sqlite',"SELECT * FROM dot_primary_runs WHERE status IN ('unknown','native_verified') ORDER BY id")]:
        rows=query(name,sql);result[name]={'rows':len(rows),'sha256':sha(canonical(rows))}
    return result
def configuration():
    paths=[ROOT/'config/erp-assignment/resolver.json',ROOT/'config/runtime-environment.json']
    for unit in (MAIN,QA):
        paths.append(Path('/etc/systemd/system')/unit)
        drops=subprocess.check_output(['systemctl','show',unit,'--property=DropInPaths','--value'],text=True,timeout=10).split()
        paths.extend(Path(v) for v in drops)
    return {str(p):sha(read(p)) for p in paths}
def gate(action, epoch):
    command='profile72-fenced-originals-'+action+'-20261116'
    args=['sudo','-n','-u','#999','-g','#988','/usr/bin/python3','-I','-B',
        str(RUNTIME/'utils/agentic_maintenance.py'),action,'--data-dir',str(DATA),
        '--expected-epoch',str(epoch),'--command-id',command,'--actor','jimran@abitcons.com']
    save('gate-'+action+'.intent.json',{'command_id':command,'expected_epoch':epoch,'attempts':1})
    r=subprocess.run(args,capture_output=True,timeout=20)
    save('gate-'+action+'.ack.json',{'returncode':r.returncode,'stdout':r.stdout.decode(),'stderr_sha256':sha(r.stderr)})
    need(r.returncode==0,'GATE_OUTCOME_REQUIRES_ORIGINAL_READBACK')
    return json.loads(r.stdout)
def lifecycle(unit, action):
    save(unit+'.'+action+'.intent.json',{'unit':unit,'action':action,'attempts':1})
    r=subprocess.run(['systemctl',action,unit],capture_output=True,timeout=105)
    save(unit+'.'+action+'.ack.json',{'returncode':r.returncode,'stdout_sha256':sha(r.stdout),'stderr_sha256':sha(r.stderr)})
    need(r.returncode==0,'ORIGINAL_SERVICE_OUTCOME_UNCONFIRMED_NO_REPEAT')
def backup():
    directory=OUT/'databases';directory.mkdir(mode=0o700);rows=[]
    for p in sorted(list(DATA.glob('*.sqlite'))+[DATA/'webui.db',DATA/'maintenance-admission/gate.sqlite']):
        dest=directory/p.name
        with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(dest) as b:
            a.backup(b);need(b.execute('PRAGMA quick_check').fetchone()[0]=='ok','BACKUP_NOT_CONSISTENT')
        dest.chmod(0o600);rows.append({'source':str(p),'sha256':sha(read(dest))})
    save('database-backups.json',rows)
def main(a):
    need(os.geteuid()==0 and a.execute!=a.preflight_only,'ROOT_EXPLICIT_MODE_REQUIRED')
    need(sha(read(Path(__file__)))==a.controller_sha256,'REVIEWED_CONTROLLER_REQUIRED')
    manifest_raw=read(PACKAGE/'manifest.json');need(sha(manifest_raw)==a.manifest_sha256,'EXACT_MANIFEST_REQUIRED')
    m=json.loads(manifest_raw);baseline_raw=read(PACKAGE/'complete71-before.json')
    need(sha(baseline_raw)==CANONICAL_BEFORE and len(m['files'])==10 and
        {r['runtime_relative_path'] for r in m['files']}==ALLOWED,'EXACT_TEN_REVIEWED_SOURCES_REQUIRED')
    before={r['runtime_path']:r['sha256'] for r in json.loads(baseline_raw)['files']}
    need(len(before)==71 and all(sha(read(Path(p)))==pin for p,pin in before.items()),'ACTUAL_CORE71_CHANGED')
    api=m.get('additional_application_files')
    need(api==[{'runtime_relative_path':'utils/developer_api.py',
        'before_sha256':'c0d339f5216411e5753f15d392a0f9bb81cd1154e1fb420d98495849d70ab367',
        'after_sha256':'8f05f58c3ceae52c5b82fe1696dca91ab5be062119e16459cc543f67134b93c5'}],
        'EXACT_TESTED_API_CATALOGUE_SUPPORT_FILE_REQUIRED')
    api_path=str(RUNTIME/'utils/developer_api.py')
    need(sha(read(Path(api_path)))==api[0]['before_sha256'],'PRESERVED_API_BASELINE_CHANGED')
    need(before.get(api_path)==api[0]['before_sha256'],'API_ALREADY_BELONGS_TO_CURRENT_COHORT');targets=m['files']+api
    for r in targets:
        path=RUNTIME/r['runtime_relative_path'];source=read(PACKAGE/'runtime'/r['runtime_relative_path'])
        need(before.get(str(path))==r['before_sha256'] and sha(source)==r['after_sha256'],'SOURCE_OR_BASELINE_CHANGED')
        compile(source,str(path),'exec')
    current={u:identity(EXPECTED[u][0]) for u in EXPECTED}
    for unit,p in current.items():
        need(p['start']==EXPECTED[unit][1] and p['uid']==999 and service(unit)['MainPID']==str(p['pid'])
            and service(unit)['NRestarts']=='0','EXACT_ORIGINAL_SERVICE_REQUIRED')
    fixed={k:identity(pid) for k,pid in PROTECTED.items()};config=configuration();counts=producer_counts()
    if a.preflight_only:
        print(json.dumps({'state':'READONLY_PREFLIGHT','producer_counts':counts,'idle':all(v==0 for v in counts.values()),
            'unknown_originals_retained':originals(),'runtime_writes':0,'service_actions':0}));return
    need(all(v==0 for v in counts.values()),'WAIT_FOR_ORIGINAL_PRODUCERS_NO_STOP')
    proof=json.loads(read(a.main_task_proof));need(proof['tasks']==[] and proof['main_pid']==current[MAIN]['pid']
        and 0<=time.time()-proof['observed_at']<=120,'FRESH_AUTHENTICATED_MAIN_TASKS_PROOF_REQUIRED')
    lock=os.open(DATA/'erp-dot-dispatch.lock',os.O_RDWR|os.O_NOFOLLOW);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        need(not OUT.exists(),'ORIGINAL_OPERATION_EXISTS_OBSERVE_ONLY');OUT.mkdir(mode=0o700)
        save('intent.json',{'manifest_sha256':a.manifest_sha256,'controller_sha256':a.controller_sha256,
            'old_services':current,'protected':fixed,'configuration':config,'counts':counts,
            'admission_state':'DRAINING with unresolved original fences retained; not SEALED','model_sends':0})
        if a.preclosed_gate_proof:
            info=a.preclosed_gate_proof.lstat()
            need(info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o600,
                'ROOT_PRIVATE_EXISTING_DRAIN_PROOF_REQUIRED')
            raw=read(a.preclosed_gate_proof);closed=json.loads(raw)
            need(closed['state']=='draining' and closed['epoch']==47
                and closed['command_id']=='core-profile72-wait-original-close-20261116'
                and query('maintenance-admission/gate.sqlite','SELECT state,epoch,command_id FROM gate')==
                    [('draining',47,'core-profile72-wait-original-close-20261116')],
                'EXACT_ALREADY_ACKNOWLEDGED_DRAIN47_REQUIRED')
            save('existing-drain-proof.json',raw)
        else:
            closed=gate('close',a.expected_epoch)
        need(closed['state']=='draining','EXACT_DRAINING_GATE_REQUIRED');epoch=closed['epoch']
        need(all(v==0 for v in producer_counts().values()),'PRODUCER_BECAME_ACTIVE_PRESERVE_DRAIN_NO_STOP')
        retained=originals();save('originals-before.json',retained)
        need(configuration()==config and all(identity(pid)==fixed[k] for k,pid in PROTECTED.items()),'PROTECTED_STATE_CHANGED')
        for unit in (MAIN,QA):
            lifecycle(unit,'stop');s=service(unit);group=Path('/sys/fs/cgroup/system.slice')/unit
            need(s['ActiveState']=='inactive' and s['MainPID']=='0' and not(Path('/proc')/str(current[unit]['pid'])).exists()
                and (not group.exists() or all(not p.read_text().strip() for p in group.rglob('cgroup.procs'))),
                'ORIGINAL_SERVICE_NOT_STOPPED_NO_REPEAT')
        backup();need(originals()==retained,'ORIGINAL_FENCES_CHANGED_BEFORE_INSTALL')
        bytecode=OUT/'old-bytecode';bytecode.mkdir(mode=0o700)
        for i,r in enumerate(targets):
            path=RUNTIME/r['runtime_relative_path'];raw=read(PACKAGE/'runtime'/r['runtime_relative_path'])
            st=path.lstat() if r['before_sha256'] else None
            need((sha(read(path)) if st else None)==r['before_sha256'],'RUNTIME_CAS_CHANGED')
            if st:save(str(i)+'.before',read(path))
            temp=path.with_name('.'+path.name+'.profile72-new');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(raw);f.flush();os.fchown(f.fileno(),st.st_uid if st else 999,st.st_gid if st else 988)
                os.fchmod(f.fileno(),stat.S_IMODE(st.st_mode) if st else 0o644);os.fsync(f.fileno())
            if st:
                for key in os.listxattr(path):os.setxattr(temp,key,os.getxattr(path,key))
            os.replace(temp,path)
            for j,pyc in enumerate(sorted((path.parent/'__pycache__').glob(path.stem+'.*.pyc'))):
                need(not pyc.is_symlink() and pyc.is_file(),'OWNED_BYTECODE_REGULAR_FILE_REQUIRED')
                os.rename(pyc,bytecode/(str(i)+'-'+str(j)+'-'+pyc.name))
        after={**before,**{str(RUNTIME/r['runtime_relative_path']):r['after_sha256'] for r in targets}}
        need(len(after)==72 and all(sha(read(Path(p)))==v for p,v in after.items()),'COMPLETE72_READBACK_REQUIRED')
        save('runtime72.json',{'files':after,'count':72,'API_catalogue_included':True,'total_files_verified':72});need(originals()==retained and configuration()==config,'ORIGINAL_OR_CONFIGURATION_CHANGED')
        for unit in (MAIN,QA):
            lifecycle(unit,'start')
        deadline=time.monotonic()+150
        while True:
            try:
                new={u:service(u) for u in (MAIN,QA)}
                need(all(s['ActiveState']=='active' and s['SubState']=='running' and s['NRestarts']=='0'
                    and int(s['MainPID']) not in (0,current[u]['pid']) for u,s in new.items()),'NEW_SERVICES_NOT_READY')
                need(http('/health').get('status') is True,'NEW_CORE_HEALTH_NOT_READY')
                break
            except Exception:
                need(time.monotonic()<deadline,'ORIGINAL_START_UNKNOWN_OBSERVE_NO_REPEAT');time.sleep(1)
        need(originals()==retained and configuration()==config and all(identity(pid)==fixed[k] for k,pid in PROTECTED.items()),
            'PRESERVED_ORIGINAL_OR_PROTECTED_STATE_CHANGED')
        need(all(sha(read(Path(p)))==v for p,v in after.items()),'POSTSTART_COMPLETE72_CHANGED')
        save('installed-readback.json',{'services':new,'originals':retained,'all72_verified':True,'unknown_replays':0})
        opened=gate('open',epoch);need(opened['state']=='open','OPEN_REQUIRES_ORIGINAL_READBACK')
        receipt={'state':'CORE72_LIVE_ORIGINAL_FENCES_RETAINED','runtime_count':72,'API_catalogue_included':True,'total_files_verified':72,'gate':opened,
            'services':{u:identity(int(s['MainPID'])) for u,s in new.items()},'unknown_originals_retained':retained,
            'database_restores':0,'model_sends':0,'ERP_writes':0,'Main_Stop_Start_calls_each':1,'QA_Stop_Start_calls_each':1}
        save('completed-receipt.json',receipt);print(json.dumps(receipt))
    finally:os.close(lock)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--preflight-only',action='store_true')
    p.add_argument('--controller-sha256',required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--expected-epoch',type=int,default=46);p.add_argument('--main-task-proof',type=Path)
    p.add_argument('--preclosed-gate-proof',type=Path)
    try:main(p.parse_args())
    except Exception as e:
        print(json.dumps({'state':'HELD_PRESERVE_ORIGINAL_OPERATION','error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}));raise SystemExit(65)

"""One existing Task-controls router fix; no dispatch or business-state mutation.

Drain idle producers, preserve every governance/provider/control row and lease,
back up databases, replace one reviewed existing leaf, then reload Main and QA
once each. All other 74 runtime files and protected processes remain unchanged.
"""
import argparse, fcntl, hashlib, json, os, sqlite3, stat, subprocess, time
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path('/srv/abit-dev-linux/abit-ai')
DATA = ROOT / 'data'
RUNTIME = ROOT / 'releases/20261031-live-0.11.4/runtime/open_webui'
PACKAGE = Path(__file__).resolve().parent
OUT = Path('/var/lib/abit-ai-erp-rollouts/core-task-controls-existing-channel-20261116')
MAIN, QA = 'abit-ai.service', 'abit-ai-erp-review-qa.service'
ALLOWED = {'routers/erp_task_controls.py'}
NEW = set()
COHORT = Path('/var/lib/abit-ai-erp-rollouts/core-task06203-transport-retry-20261116/runtime74.json')
COHORT_SHA = 'f00730943631357f0cbc84b669c370668f319b2f7927e0a4a69cc7fdd1d797da'
LEGACY_ROUTER_SHA = 'c1fac1308b08f09595327ccc0938d90f2229cf5ba231d43f5c9bd9ee4bce2264'
CONFIG = ROOT / 'config/runtime-environment.json'
CONFIG_SHA = 'ef71d6f8f56ace9bd7dd9941f8a100f3de026cbdab5956a0962333adac1b1d2a'
PROJECT = 'g-p-6a9b28cd72f48191ab2ee2bd226d16f2'
RUN = '4f399cc6-f323-5d98-8155-c48c3ca692fe'
INV = 'agov-adoption-f804fe47d15981100bc37ded27e5d0dbba29880315bab1d1'
EXPECTED = {MAIN:(3469940,'78178657'), QA:(3469960,'78178661')}
NODE = (2877321,'77927719')
PROTECTED = {'watchdog':1073226, 'Chrome':1074156, 'egress':1073233,
    'Xvfb':1073566, 'tunnel':1075923, 'MCP':2995736, 'auth':675402}

def need(value, code):
    if not value: raise RuntimeError(code)
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),default=lambda v: {'bytes_hex':v.hex()} if isinstance(v,bytes) else str(v)).encode()
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
        'running_automations':query('webui.db',"SELECT count(*) FROM automation_run WHERE status='running' ",())[0][0],
        'live_governed_runs':query('agentic-governance.sqlite',"SELECT count(*) FROM governance_invocation WHERE state='running'",())[0][0],
        'dispatching_provider_rows':query('abitdot-primary.sqlite',"SELECT count(*) FROM dot_primary_runs WHERE status='dispatching'")[0][0],
        'legacy_QA_work':query('erp-pending-review-qa.sqlite',"SELECT count(*) FROM review WHERE phase!='done'")[0][0]}
def originals():
    result={}
    tables={
        'maintenance-admission/gate.sqlite':['lease'],
        'abitdot-primary.sqlite':[r[0] for r in query('abitdot-primary.sqlite',"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")],
        'agentic-governance.sqlite':[r[0] for r in query('agentic-governance.sqlite',"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")],
        'webui.db':['automation','automation_run'],
        'erp-pending-review-qa.sqlite':['review'],
    }
    for database,names in tables.items():
        for table in names:
            need(table.replace('_','').isalnum(),'TABLE_IDENTIFIER_REQUIRED')
            columns=query(database,'PRAGMA table_info("'+table+'")')
            ordering=','.join(str(i+1) for i in range(len(columns)))
            rows=query(database,'SELECT * FROM "'+table+'" ORDER BY '+ordering)
            result[database+':'+table]={'rows':len(rows),'sha256':sha(canonical(rows))}
    return result
def configuration():
    paths=[ROOT/'config/erp-assignment/resolver.json',ROOT/'config/runtime-environment.json']
    for unit in (MAIN,QA):
        paths.append(Path('/etc/systemd/system')/unit)
        drops=subprocess.check_output(['systemctl','show',unit,'--property=DropInPaths','--value'],text=True,timeout=10).split()
        paths.extend(Path(v) for v in drops)
    return {str(p):sha(read(p)) for p in paths}
def gate(action, epoch):
    command='task-controls-existing-channel-'+action+'-20261116'
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
def metadata(path):
    st=path.lstat();need(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'OWNED_REGULAR_SINGLE_LINK_FILE_REQUIRED')
    return {'uid':st.st_uid,'gid':st.st_gid,'mode':stat.S_IMODE(st.st_mode),
        'xattrs':{k:sha(os.getxattr(path,k)) for k in os.listxattr(path)}}
def install_new(path, raw):
    need(not path.exists() and not path.is_symlink(),'NEW_MODULE_ALREADY_EXISTS')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(raw);f.flush();os.fchown(f.fileno(),999,988);os.fchmod(f.fileno(),0o644);os.fsync(f.fileno())
    need(metadata(path)=={'uid':999,'gid':988,'mode':0o644,'xattrs':{}},'NEW_MODULE_METADATA_READBACK_REQUIRED')
def install(path, raw, backup_name):
    st=path.lstat();save(backup_name,read(path))
    temp=path.with_name('.'+path.name+'.task-controls-new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(raw);f.flush();os.fchown(f.fileno(),st.st_uid,st.st_gid)
        os.fchmod(f.fileno(),stat.S_IMODE(st.st_mode));os.fsync(f.fileno())
    for key in os.listxattr(path):os.setxattr(temp,key,os.getxattr(path,key))
    os.replace(temp,path)

def original():
    rows=query('agentic-governance.sqlite',"SELECT invocation_id,state,dispatch_attempts,target_role FROM governance_invocation WHERE erp_task='TASK-2026-06203' ORDER BY created_at")
    need(rows==[(INV,'failed',1,'project_manager'),
        ('agov-transport-retry-9729e771769c6effcd5a52080227acaca861f60e6f5ee7f2','execution_finished',1,'project_manager')],
        'EXACT_TASK_HISTORY_CHANGED_NO_STOP')

def draining(a):
    proof=json.loads(read(OUT/'gate-close.ack.json'));need(proof['returncode']==0,'ACKNOWLEDGED_DRAIN_REQUIRED')
    closed=json.loads(proof['stdout'])
    need(closed['state']=='draining' and closed['epoch']==55
        and closed['command_id']=='task-controls-existing-channel-close-20261116'
        and query('maintenance-admission/gate.sqlite','SELECT state,epoch,command_id FROM gate')==
            [('draining',55,'task-controls-existing-channel-close-20261116')],
        'EXACT_ACKNOWLEDGED_DRAIN55_REQUIRED')
    return closed

def main(a):
    need(os.geteuid()==0 and a.execute!=a.preflight_only,'ROOT_EXPLICIT_MODE_REQUIRED')
    need(sha(read(Path(__file__)))==a.controller_sha256,'REVIEWED_CONTROLLER_REQUIRED')
    raw=read(PACKAGE/'runtime-manifest.json');need(sha(raw)==a.manifest_sha256,'EXACT_MANIFEST_REQUIRED')
    manifest=json.loads(raw);files={r['path']:r for r in manifest['modules']}
    need(len(manifest['modules'])==1 and set(files)==ALLOWED,'ONLY_ONE_REVIEWED_LEAF_REQUIRED')
    baseline=read(COHORT);need(sha(baseline)==COHORT_SHA,'EXACT_CORE74_COHORT_REQUIRED')
    before=json.loads(baseline)['files'];need(len(before)==74 and all(sha(read(Path(p)))==v for p,v in before.items()),'ACTUAL_CORE74_CHANGED')
    need(str(RUNTIME/'routers/erp_task_controls.py') not in before,'LEGACY_LEAF_MUST_BE_OUTSIDE_COHORT')
    need(sha(read(RUNTIME/'routers/erp_task_controls.py'))==LEGACY_ROUTER_SHA,'ACTUAL_LEGACY_ROUTER_CHANGED')
    before={**before,str(RUNTIME/'routers/erp_task_controls.py'):LEGACY_ROUTER_SHA}
    for relative,r in files.items():
        target=RUNTIME/relative;source=read(PACKAGE/'runtime'/relative)
        need(before.get(str(target))==r['before_sha256'] and sha(source)==r['after_sha256'],'SOURCE_OR_BASELINE_CHANGED')
        if relative in NEW:need(r['before_sha256'] is None and not target.exists() and not target.is_symlink(),'NEW_MODULE_CREATE_ONLY_REQUIRED')
        else:need(r['before_sha256'] is not None,'EXISTING_MODULE_BASELINE_REQUIRED')
        compile(source,str(target),'exec')
    need(sha(read(CONFIG))==CONFIG_SHA,'EXACT_PRESERVED_CONFIGURATION_REQUIRED')
    metadata_before={str(RUNTIME/k):metadata(RUNTIME/k) for k in files if k not in NEW}
    current={u:identity(EXPECTED[u][0]) for u in EXPECTED}
    for unit,p in current.items():
        need(p['start']==EXPECTED[unit][1] and p['uid']==999 and service(unit)['MainPID']==str(p['pid'])
            and service(unit)['NRestarts']=='0','EXACT_ORIGINAL_SERVICE_REQUIRED')
    need((a.node_pid,a.node_start)==NODE,'EXACT_REVIEWED_NODE_REQUIRED')
    node=identity(a.node_pid);need(node['start']==a.node_start and node['uid']==999,'EXACT_NEW_NODE_REQUIRED')
    fixed={**{k:identity(pid) for k,pid in PROTECTED.items()},'Node':node}
    config=configuration();need(query('maintenance-admission/gate.sqlite','SELECT state,epoch FROM gate')==[('open',54)],'EXACT_OPEN54_REQUIRED');original();counts=producer_counts()
    if a.preflight_only:
        print(json.dumps({'state':'READONLY_PREFLIGHT','producer_counts':counts,'all_producers_idle':all(v==0 for v in counts.values()),'originals':originals(),'runtime_writes':0,'service_actions':0}));return
    need(all(v==0 for v in counts.values()),'OTHER_ACTIVE_PRODUCERS_NO_STOP')
    proof=json.loads(read(a.main_task_proof));need(proof['tasks']==[] and proof['main_pid']==current[MAIN]['pid']
        and 0<=time.time()-proof['observed_at']<=120,'FRESH_AUTHENTICATED_MAIN_TASKS_PROOF_REQUIRED')
    lock=os.open(DATA/'erp-dot-dispatch.lock',os.O_RDWR|os.O_NOFOLLOW);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        need(not OUT.exists(),'ORIGINAL_OPERATION_EXISTS_OBSERVE_ONLY');OUT.mkdir(mode=0o700)
        retained=originals();save('originals-before.json',retained)
        save('intent.json',{'manifest_sha256':a.manifest_sha256,'controller_sha256':a.controller_sha256,'old_services':current,'protected':fixed,'configuration':config,'counts':counts,'model_sends':0,'configuration_writes':0,'original_run':RUN,'existing_file_metadata':metadata_before})
        closed=gate('close',54);need(closed['state']=='draining' and closed['epoch']==55,'EXACT_DRAIN55_REQUIRED')
        need(draining(a)==closed and all(v==0 for v in producer_counts().values()),'PRODUCER_OR_GATE_CHANGED_NO_STOP')
        original();need(originals()==retained and configuration()==config and all(identity(v['pid'])==v for v in fixed.values()),'PROTECTED_STATE_CHANGED')
        for unit in (MAIN,QA):
            lifecycle(unit,'stop');s=service(unit);group=Path('/sys/fs/cgroup/system.slice')/unit
            need(s['ActiveState']=='inactive' and s['MainPID']=='0' and not(Path('/proc')/str(current[unit]['pid'])).exists()
                and (not group.exists() or all(not p.read_text().strip() for p in group.rglob('cgroup.procs'))),'ORIGINAL_SERVICE_NOT_STOPPED_NO_REPEAT')
        backup();need(originals()==retained and configuration()==config,'ORIGINAL_FENCES_OR_CONFIGURATION_CHANGED_BEFORE_INSTALL')
        bytecode=OUT/'old-bytecode';bytecode.mkdir(mode=0o700)
        for i,(relative,r) in enumerate(files.items()):
            target=RUNTIME/relative
            if relative in NEW:install_new(target,read(PACKAGE/'runtime'/relative))
            else:
                need(sha(read(target))==r['before_sha256'] and metadata(target)==metadata_before[str(target)],'RUNTIME_CAS_OR_METADATA_CHANGED')
                install(target,read(PACKAGE/'runtime'/relative),str(i)+'.before')
                need(metadata(target)==metadata_before[str(target)],'EXISTING_METADATA_NOT_PRESERVED')
            for j,pyc in enumerate(sorted((target.parent/'__pycache__').glob(target.stem+'.*.pyc'))):
                need(not pyc.is_symlink() and pyc.is_file(),'OWNED_BYTECODE_REGULAR_FILE_REQUIRED')
                os.rename(pyc,bytecode/(str(i)+'-'+str(j)+'-'+pyc.name))
        expected_config=config
        after={**before,**{str(RUNTIME/k):v['after_sha256'] for k,v in files.items()}}
        need(len(after)==75 and all(sha(read(Path(p)))==v for p,v in after.items()),'COMPLETE75_READBACK_REQUIRED')
        save('runtime75.json',{'files':after,'count':75,'API_catalogue_included':True,'all75_verified':True})
        need(originals()==retained and configuration()==expected_config and draining(a)==closed,'ORIGINAL_CONFIG_OR_GATE_CHANGED')
        for unit in (MAIN,QA):lifecycle(unit,'start')
        deadline=time.monotonic()+150
        while True:
            try:
                new={u:service(u) for u in (MAIN,QA)}
                need(all(s['ActiveState']=='active' and s['SubState']=='running' and s['NRestarts']=='0' and int(s['MainPID']) not in (0,current[u]['pid']) for u,s in new.items()),'NEW_SERVICES_NOT_READY')
                need(http('/health').get('status') is True,'NEW_CORE_HEALTH_NOT_READY');break
            except Exception:
                need(time.monotonic()<deadline,'ORIGINAL_START_UNKNOWN_OBSERVE_NO_REPEAT');time.sleep(1)
        need(originals()==retained and configuration()==expected_config and all(identity(v['pid'])==v for v in fixed.values()),'PRESERVED_ORIGINAL_OR_PROTECTED_STATE_CHANGED')
        need(all(sha(read(Path(p)))==v for p,v in after.items()) and draining(a)==closed,'POSTSTART_RUNTIME_OR_GATE_CHANGED')
        save('installed-readback.json',{'services':new,'originals':retained,'all75_verified':True,'unknown_replays':0})
        opened=gate('open',55);need(opened['state']=='open' and opened['epoch']==56,'OPEN_REQUIRES_ORIGINAL_READBACK')
        receipt={'state':'CORE_TASK_CONTROLS_EXISTING_CHANNEL_LIVE_HISTORY_RETAINED','runtime_count':75,'gate':opened,'services':{u:identity(int(s['MainPID'])) for u,s in new.items()},'unknown_originals_retained':retained,'database_restores':0,'model_sends':0,'ERP_writes':0,'Main_Stop_Start_calls_each':1,'QA_Stop_Start_calls_each':1,'configuration_writes':0}
        save('completed-receipt.json',receipt);print(json.dumps(receipt))
    finally:os.close(lock)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--preflight-only',action='store_true')
    p.add_argument('--controller-sha256',required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--main-task-proof',type=Path)
    p.add_argument('--node-pid',type=int,required=True);p.add_argument('--node-start',required=True)
    try:main(p.parse_args())
    except Exception as e:
        print(json.dumps({'state':'HELD_PRESERVE_ORIGINAL_OPERATION','error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}));raise SystemExit(65)

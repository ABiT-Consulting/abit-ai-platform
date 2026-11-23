#!/usr/bin/env python3
"""Fail-closed, Root-owned, Core-only Codex release. No approvals or lease transfers.

Operator must copy the independently reviewed script into the protected Root
stage and supply separate production approval and actual scoped run clearance.
No ERP writes, automation admission, provider replay or database restoration.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
import argparse,fcntl,hashlib,json,os,shutil,socket,sqlite3,stat,subprocess,sys,tempfile,urllib.request
from pathlib import Path,PurePosixPath

MANIFEST_SHA='f095e84498e4c888b9c4190520312c3a62a36e6825d4ff175bad4085e8bff047'
LINEAGE_SHA='96ae9e18fc543ba06f6d83f06d2b8818c8552ee3cac52d10f704ae7b7611c660'
SOURCE_COMMIT='badf6f3290132ab0b65fea941404a4ee5c58ead8'
SOURCE_REPOSITORY=Path('/srv/abit-dev-linux/abit-ai-prompt04-release-20261123')
RELEASE_NAME='codex-primary-continuation-20261123'
OWNER_FILE=Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
GATE=Path('/srv/abit-dev-linux/abit-ai/data/maintenance-admission/gate.sqlite')
LIVE=Path('/srv/abit-dev-linux/abit-ai/current/runtime')
DATA=Path('/srv/abit-dev-linux/abit-ai/data')
STAGE=Path('/var/lib/abit-ai-codex-p05')
DROPIN=Path('/etc/systemd/system/abit-ai.service.d/50-agentic-codex.conf')
OVERRIDE=Path('/run/systemd/system/abit-ai.service.d/zzzz-codex-p05-graceful.conf')
ROOT_LOCK=Path('/var/lock/abit-ai-governance-release.lock')
UNIT_FILES=[Path('/etc/systemd/system/abit-ai.service'),
            Path('/etc/systemd/system/abit-ai.service.d/40-agentic-gateway.conf'),
            Path('/etc/systemd/system/abit-ai.service.d/erp-assignment.conf')]
OVERRIDE_BYTES=b'[Service]\nTimeoutStopSec=infinity\nSendSIGKILL=no\nKillMode=mixed\nRestart=no\n'

class ReleaseDenied(RuntimeError):pass

def require(ok,message):
    if not ok:raise ReleaseDenied(message)

def sha(data):return hashlib.sha256(data).hexdigest()

def now():return datetime.now(timezone.utc).isoformat()

def raw(path):
    p=Path(path);st=p.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'Not a single-link regular file: '+str(p))
    return p.read_bytes()

def file_sha(path):
    try:return sha(raw(path))
    except FileNotFoundError:return None

def root_dir(path):
    st=Path(path).lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o700,
            'Protected Root directory changed')

def protected_metadata(path):
    """Validate the Root-owned regular inode without decoding its contents."""
    try:
        st=Path(path).lstat()
    except FileNotFoundError:
        raise ReleaseDenied('Missing protected Root authorization or journal: '+str(path)) from None
    require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and
            stat.S_IMODE(st.st_mode)==0o600,'Protected receipt metadata changed')
    return st


def protected(path):
    protected_metadata(path)
    return json.loads(raw(path))

def new_file(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())

def atom_json(path,data):
    path=Path(path);tmp=path.with_name('.'+path.name+'.new')
    new_file(tmp,(json.dumps(data,indent=2,sort_keys=True)+'\n').encode())
    os.replace(tmp,path)

def release_lock(path):
    fd=os.open(path,os.O_CLOEXEC|os.O_NOFOLLOW|os.O_CREAT|os.O_RDWR,0o600)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and
                stat.S_IMODE(st.st_mode)==0o600,'Root release lock changed')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd);raise

class Controller:
    def __init__(self,*,repository,runtime=LIVE,data=DATA,stage=STAGE,dropin=DROPIN,
                 override=OVERRIDE,operator=OWNER_FILE,gate=GATE,command=None,
                 test_mode=False,unit_paths=None):
        self.repo=Path(repository);self.runtime=Path(runtime);self.data=Path(data)
        self.stage=Path(stage);self.dropin=Path(dropin);self.override=Path(override)
        self.operator=Path(operator);self.gate=Path(gate);self.test_mode=test_mode
        self.command=command or self.run_command
        self.unit_paths=list(unit_paths if unit_paths is not None else UNIT_FILES)
        if not test_mode:require(self.unit_paths==UNIT_FILES,'Unit scope changed')
        self.release=self.repo/'deployment'/RELEASE_NAME
        encoded=raw(self.release/'release-manifest.json');self.expected_sha=sha(encoded)
        if not test_mode:require(self.expected_sha==MANIFEST_SHA,'Pinned manifest changed')
        self.manifest=json.loads(encoded);self.changes=self.manifest['files']
        lineage=raw(self.release/'source-parity-manifest.json')
        if not test_mode:require(sha(lineage)==LINEAGE_SHA,'Pinned provenance changed')
        self.lineage=json.loads(lineage)
        require(len(self.changes)==13 and
                len({r['path'] for r in self.changes})==13 and
                not self.manifest.get('approval_for_this_package') and
                not self.manifest.get('installed'),'Unexpected release scope/state')
        for r in self.changes:
            p=PurePosixPath(r['path'])
            require(not p.is_absolute() and '..' not in p.parts and
                    str(p).startswith('open_webui/'),'Unapproved runtime path')

    @staticmethod
    def run_command(*args,timeout=25):
        r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
        require(r.returncode==0,'Admin operation failed: '+args[0])
        return r.stdout.strip()

    def preflight(self,expected='before'):
        require(expected in {'before','after'},'Unknown preflight mode')
        for row in self.changes:
            name=row['path'];target=self.runtime/name
            expected_sha=row['before_sha256'] if expected=='before' else row['candidate_sha256']
            require(file_sha(self.repo/'backend'/name)==row['candidate_sha256'],
                    'Reviewed candidate changed: '+name)
            require(file_sha(target)==expected_sha,'Installed source changed: '+name)
            if expected_sha is not None and not self.test_mode:
                st=target.stat()
                require((st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))==
                        (999,988,int(row.get('mode_before') or '0o644',8)),
                        'Installed runtime ownership/mode changed: '+name)
        for r in self.lineage['modules']:
            require(file_sha(self.repo/'backend'/r['path'])==r['sha256'] and
                    file_sha(self.runtime/r['path'])==r['sha256'],
                    'Already installed dependency changed: '+r['path'])
        cf=self.manifest['config_files'][0]
        require(file_sha(self.release/'50-agentic-codex.conf')==cf['candidate_sha256'],
                'Systemd drop-in source changed')
        require(file_sha(self.dropin)==(cf['before_sha256'] if expected=='before' else
                                       cf['candidate_sha256']),
                'Installed systemd drop-in changed')
        return {'expected':expected,'manifest_sha256':self.expected_sha,
                'runtime_file_count':len(self.changes),
                'unchanged_lineage_file_count':len(self.lineage['modules']),
                'systemd_dropin_matches':True,'no_release_write':True}

    def gate_status(self):
        with closing(sqlite3.connect(self.gate.as_uri()+'?mode=ro',uri=True,timeout=4)) as db:
            db.execute('PRAGMA query_only=ON')
            state,epoch=db.execute('SELECT state,epoch FROM gate WHERE id=1').fetchone()
            leases=list(db.execute("SELECT id,kind,reference,pid,process_start,state FROM lease WHERE state IN ('active','unresolved') ORDER BY id"))
        core=[]
        for ident,kind,reference,pid,start,status in leases:
            try:cg=(Path('/proc')/str(pid)/'cgroup').read_text()
            except (FileNotFoundError,PermissionError,ProcessLookupError):continue
            if '/system.slice/abit-ai.service' in cg:
                core.append({'id':ident,'kind':kind,'state':status})
        return {'state':state,'epoch':epoch,
                'active':sum(x[-1]=='active' for x in leases),
                'unresolved':sum(x[-1]=='unresolved' for x in leases),
                'affected_core_admissions':core,
                'leases_sha256':sha(json.dumps(leases,sort_keys=True,separators=(',',':')).encode())}

    def operator_record(self):
        x=json.loads(raw(self.operator))
        require(x.get('state')=='ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY' and x.get('owner') and
                datetime.fromisoformat(x['lease_expires_at'].replace('Z','+00:00'))>datetime.now(timezone.utc),
                'No currently authorized original owner lease')
        return {'owner':x['owner'],'phase':x.get('phase'),'expires_at':x['lease_expires_at'],
                'operator_jobs_in_progress':len(x.get('operator_jobs_in_progress') or [])}

    def root_check(self):
        if self.test_mode:return
        require(os.geteuid()==0 and os.environ.get('SUDO_USER')=='jimran@abitcons.com' and
                socket.gethostname()=='abit-dev-linux','Wrong operator administrative entry')
        root_dir(self.stage)
        script=Path(__file__).resolve()
        require(script==(self.stage/'activation_controller.py').resolve(),
                'Run the independently approved protected Root copy, not mutable Git source')
        protected_metadata(script)
        require(protected(self.stage/'production-approval.json').get('controller_sha256')==file_sha(script),
                'Root controller bytes not bound to approved review')

    def approval(self):
        root_dir(self.stage)
        approved=protected(self.stage/'production-approval.json')
        clearance=protected(self.stage/'operator-execution-clearance.json')
        owner=self.operator_record();gate=self.gate_status()
        require(approved.get('status')=='APPROVED' and
                approved.get('source_commit')==SOURCE_COMMIT and
                approved.get('manifest_sha256')==self.expected_sha and
                approved.get('allowed_services')==['abit-ai.service'] and
                approved.get('independent_review_approved') is True and
                approved.get('required_hosted_ci_passed') is True and
                approved.get('production_release_authorized') is True,
                'No current exact reviewed/CI-passed production approval')
        require(clearance.get('status')=='ACKNOWLEDGED_EXECUTION' and
                clearance.get('owner')==owner['owner'] and
                clearance.get('manifest_sha256')==self.expected_sha and
                clearance.get('gate_epoch')==gate['epoch'] and
                clearance.get('leases_sha256')==gate['leases_sha256'] and
                clearance.get('no_original_replay') is True and
                clearance.get('core_only_drain_accepted') is True and
                clearance.get('unaffected_qa_and_historical_leases_preserved') is True and
                clearance.get('current_core_handlers_drained') is True and
                clearance.get('unresolved_originals_reconciled') is True and
                clearance.get('original_06448_replay_forbidden') is True and
                clearance.get('acknowledged_scoped_owner_or_execution') is True,
                'Current original operator has not acknowledged this specific fenced run')
        require(gate['state']=='sealed' and not gate['affected_core_admissions'],
                'Core not sealed/drained; never bulk-cancel independent QA')
        return {'approval':approved,'operator':owner,'gate':gate}

    def stage_info(self):
        root_dir(self.stage)
        result=protected(self.stage/'journal.json')
        require(result.get('schema_version')==1 and
                result.get('manifest_sha256')==self.expected_sha,
                'Staged release journal not bound to exact package')
        return result

    def journal_update(self,journal,**values):
        journal.update(values,updated_at=now())
        atom_json(self.stage/'journal.json',journal)

    def verify_backups(self,journal):
        require(journal.get('backups'),'Consistent database backups missing')
        for item in journal['backups']:
            require(file_sha(self.stage/'db-backups'/item['path'])==item['sha256'],
                    'Database backup changed after staging')
        require(len(journal.get('units',[]))==len(self.unit_paths),'Unit backups missing')
        for item in journal['units']:
            require(file_sha(self.stage/item['backup_path'])==item['sha256'] and
                    file_sha(item['path'])==item['sha256'],
                    'Core systemd unit drifted after backup')
        for row in journal.get('files',[]):
            if row['before_sha256']:
                require(file_sha(self.stage/'preimages'/row['path'])==row['before_sha256'],
                        'Source rollback file changed')
            require(file_sha(self.stage/'candidate'/row['path'])==row['after_sha256'],
                    'Staged candidate changed')

    def stage_backup(self):
        self.root_check();self.approval();self.preflight('before')
        require(self.command('/usr/bin/systemctl','show','abit-ai.service',
                             '-p','User','-p','Group',timeout=15)==
                'User=mcpdev\nGroup=mcpdev','Core service UID changed')
        require(not (self.stage/'journal.json').exists(),
                'Existing partial/complete stage requires operator reconciliation')
        for name in ('candidate','preimages','db-backups','unit-backups'):
            (self.stage/name).mkdir(mode=0o700)
        entries=[]
        for row in self.changes:
            name=row['path'];payload=raw(self.repo/'backend'/name)
            require(sha(payload)==row['candidate_sha256'],'Candidate changed')
            dest=self.stage/'candidate'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            new_file(dest,payload)
            if row['before_sha256']:
                prior=raw(self.runtime/name)
                require(sha(prior)==row['before_sha256'],'Source changed during stage')
                before=self.stage/'preimages'/name
                before.parent.mkdir(parents=True,exist_ok=True);new_file(before,prior)
            entries.append({'path':name,'before_sha256':row['before_sha256'],
                            'after_sha256':row['candidate_sha256']})
        units=[]
        for index,source in enumerate(self.unit_paths):
            data=raw(source);dest=self.stage/'unit-backups'/(str(index)+'.before')
            new_file(dest,data)
            units.append({'path':str(source),'sha256':sha(data),
                          'backup_path':str(dest.relative_to(self.stage))})
        if self.dropin.is_file():new_file(self.stage/'dropin.before',raw(self.dropin))
        new_file(self.stage/'dropin.candidate',raw(self.release/'50-agentic-codex.conf'))
        candidates=[]
        for p in sorted(self.data.rglob('*')):
            if not p.is_file() or p.is_symlink() or p.suffix not in {'.db','.sqlite','.sqlite3'}:
                continue
            with p.open('rb') as stream:
                if stream.read(16)!=b'SQLite format 3\x00':continue
            candidates.append(p)
        needed=2*sum(p.stat().st_size for p in candidates)+2_147_483_648
        require(candidates and shutil.disk_usage(self.stage).free>needed,
                'Insufficient safe space for consistent SQLite backup')
        backups=[]
        for source in candidates:
            rel=source.relative_to(self.data);dest=self.stage/'db-backups'/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=20)) as current:
                with closing(sqlite3.connect(dest,timeout=20)) as archive:
                    current.backup(archive,pages=500)
                    require(archive.execute('PRAGMA quick_check').fetchone()[0]=='ok',
                            'Consistent backup quick_check failed')
            os.chmod(dest,0o600)
            backups.append({'path':str(rel),'sha256':file_sha(dest),'bytes':dest.stat().st_size})
        receipt={'schema_version':1,'manifest_sha256':self.expected_sha,
                 'source_commit':SOURCE_COMMIT,'state':'staged','created_at':now(),
                 'files':entries,'backups':backups,'units':units,'steps':[],
                 'no_database_restore':True,'no_original_replay':True}
        new_file(self.stage/'journal.json',
                 (json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode())
        return {'state':'staged','file_count':len(entries),'verified_backup_count':len(backups)}

    def replace_file(self,target,data,*,owner=999,group=988,mode=0o644):
        p=Path(target)
        require(p.parent.is_dir() and not p.is_symlink(),'Release target identity changed')
        fd,scratch=tempfile.mkstemp(prefix='.p05-',dir=p.parent)
        try:
            with os.fdopen(fd,'wb') as output:
                output.write(data);output.flush();os.fsync(output.fileno())
            os.chmod(scratch,mode);os.chown(scratch,owner,group)
            os.replace(scratch,p)
            fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if os.path.exists(scratch):os.unlink(scratch)

    def graceful_override(self):
        require(not self.override.exists(),'Unowned graceful-stop override exists')
        self.override.parent.mkdir(parents=True,exist_ok=True)
        new_file(self.override,OVERRIDE_BYTES,mode=0o644)
        self.command('/usr/bin/systemctl','daemon-reload',timeout=20)

    def service_stop(self):
        self.command('/usr/bin/systemctl','stop','abit-ai.service',timeout=180)
        require(self.command('/usr/bin/systemctl','show','abit-ai.service',
                             '--property=ActiveState','--value',timeout=15)=='inactive',
                'Core did not stop cleanly; never force-kill a provider job')
        if not self.test_mode:
            base=Path('/sys/fs/cgroup/system.slice/abit-ai.service')
            if base.is_dir():
                for directory in [base,*base.rglob('*')]:
                    if directory.is_dir():
                        procs=directory/'cgroup.procs'
                        if procs.is_file():
                            require(not procs.read_text().strip(),
                                    'Delegated Core process still alive; do not install')

    def service_auth(self):
        if self.test_mode:return True
        result=self.command('/usr/sbin/runuser','-u','mcpdev','--','/usr/bin/env',
                'CODEX_HOME=/srv/abit-dev-linux/abit-ai/data/agentic-codex-auth',
                '/usr/local/bin/codex','login','status',timeout=20)
        require(result=='Logged in using ChatGPT',
                'Production service Codex authentication not independently verified')
        return True

    def core_health(self):
        if self.test_mode:return {'status':True}
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            'http://127.0.0.1:8888/health',timeout=8) as response:
            require(response.status==200,'Core release health unavailable')
            status=json.load(response)
        require(status.get('status') is True or status.get('ok') is True,
                'Core post-release health contract failed')
        return status

    def activate(self):
        self.root_check();self.approval();self.preflight('before');self.service_auth()
        journal=self.stage_info()
        require(journal['state']=='staged','Prior release intent must be reconciled')
        self.verify_backups(journal)
        require(not self.override.exists(),'Core already in protected stop transition')
        self.journal_update(journal,state='stop_intent')
        self.graceful_override();self.service_stop()
        self.journal_update(journal,state='core_stopped')
        for row in sorted(self.changes,key=lambda r:(r['path'].endswith('routers/agentic_governance.py'),
                                                       r['path'].endswith('agentic_executor.py'),r['path'])):
            name=row['path'];target=self.runtime/name
            require(file_sha(target)==row['before_sha256'],
                    'Installed runtime changed after clean stop: '+name)
            self.journal_update(journal,state='installing',current_step=name)
            data=raw(self.stage/'candidate'/name)
            require(sha(data)==row['candidate_sha256'],'Protected candidate changed')
            if target.is_file():
                st=target.stat();uid,gid,mode=st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)
            else:uid,gid,mode=999,988,0o644
            self.replace_file(target,data,owner=uid,group=gid,mode=mode)
            require(file_sha(target)==row['candidate_sha256'],'Applied code hash mismatch')
            journal['steps'].append(name)
            self.journal_update(journal,state='installing',current_step=None)
        config=self.manifest['config_files'][0]
        require(file_sha(self.dropin)==config['before_sha256'],
                'Systemd config modified during Core-only apply')
        self.journal_update(journal,state='dropin_intent')
        self.replace_file(self.dropin,raw(self.stage/'dropin.candidate'),owner=0,group=0)
        self.command('/usr/bin/systemctl','daemon-reload',timeout=20)
        self.journal_update(journal,state='start_intent')
        self.command('/usr/bin/systemctl','start','abit-ai.service',timeout=150)
        self.preflight('after');self.core_health()
        if not self.test_mode:
            pid=self.command('/usr/bin/systemctl','show','abit-ai.service',
                             '--property=MainPID','--value',timeout=15)
            require(pid.isdigit() and int(pid)>0,'New Core PID not verified')
        self.journal_update(journal,state='applied_pending_acceptance')
        return {'state':journal['state'],'applied_file_count':len(journal['steps']),
                'admission_reopened':False}

    def rollback(self):
        self.root_check();self.approval()
        journal=self.stage_info()
        require(journal['state'] in {'stop_intent','core_stopped','installing',
                'dropin_intent','start_intent','applied_pending_acceptance','rollback_intent'},
                'No owned release to roll back')
        self.verify_backups(journal)
        # A recorded intent before any Core stop/file write is an inert aborted
        # attempt, not a reason to restart a healthy production service.
        if journal['state']=='stop_intent' and not self.override.exists():
            self.preflight('before')
            self.journal_update(journal,state='rolled_back_without_core_interruption')
            return {'state':journal['state'],'database_restored':False,
                    'original_replayed':False,'admission_reopened':False}
        self.journal_update(journal,state='rollback_intent')
        if not self.override.is_file():self.graceful_override()
        self.service_stop()
        for row in self.changes:
            require(file_sha(self.runtime/row['path']) in
                    {row['before_sha256'],row['candidate_sha256']},
                    'Concurrent newer source cannot be overwritten by rollback')
        config=self.manifest['config_files'][0]
        require(file_sha(self.dropin) in {config['before_sha256'],config['candidate_sha256']},
                'Concurrent systemd config cannot be overwritten')
        for row in reversed(self.changes):
            target=self.runtime/row['path']
            if row['before_sha256'] is None:
                if file_sha(target)==row['candidate_sha256']:target.unlink()
            elif file_sha(target)==row['candidate_sha256']:
                data=raw(self.stage/'preimages'/row['path'])
                require(sha(data)==row['before_sha256'],'Rollback preimage changed')
                st=target.stat()
                self.replace_file(target,data,owner=st.st_uid,group=st.st_gid,
                                  mode=stat.S_IMODE(st.st_mode))
        if config['before_sha256'] is None:
            if file_sha(self.dropin)==config['candidate_sha256']:self.dropin.unlink()
        elif file_sha(self.dropin)==config['candidate_sha256']:
            self.replace_file(self.dropin,raw(self.stage/'dropin.before'),owner=0,group=0)
        self.command('/usr/bin/systemctl','daemon-reload',timeout=20)
        self.command('/usr/bin/systemctl','start','abit-ai.service',timeout=150)
        self.preflight('before');self.core_health()
        require(file_sha(self.override)==sha(OVERRIDE_BYTES),
                'Unowned graceful override cannot be removed')
        self.override.unlink()
        self.command('/usr/bin/systemctl','daemon-reload',timeout=20)
        self.journal_update(journal,state='rolled_back_preserving_data')
        return {'state':journal['state'],'database_restored':False,
                'original_replayed':False,'admission_reopened':False}

    def finalize(self):
        self.root_check();self.approval()
        journal=self.stage_info()
        require(journal['state']=='applied_pending_acceptance',
                'Only accepted installed package may be finalized')
        receipt=protected(self.stage/'runtime-acceptance.json')
        require(receipt.get('manifest_sha256')==self.expected_sha and
                receipt.get('status')=='ACCEPTED' and
                receipt.get('independent_qa_receipt') and
                receipt.get('real_erp_readback_receipt') and
                receipt.get('relay_original_no_replay_receipt') and
                (receipt.get('automatic_successor_receipt') or
                 receipt.get('truthful_idle_receipt')) and
                receipt.get('operator_owner')==self.operator_record()['owner'],
                'Missing independent production acceptance and owner receipt')
        self.preflight('after');self.core_health()
        require(file_sha(self.override)==sha(OVERRIDE_BYTES),
                'Unowned graceful override changed')
        self.journal_update(journal,state='finalize_intent')
        self.override.unlink()
        self.command('/usr/bin/systemctl','daemon-reload',timeout=20)
        self.journal_update(journal,state='accepted_pending_operator_admission_reopen')
        return {'state':journal['state'],'admission_reopened':False}

def cli():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preflight','stage','apply','rollback','finalize'))
    action=parser.parse_args().action
    controller=Controller(repository=SOURCE_REPOSITORY)
    if action=='preflight':
        result=controller.preflight()
        result['maintenance']=controller.gate_status()
        result['operator']=controller.operator_record()
    else:
        controller.root_check()
        descriptor=release_lock(ROOT_LOCK)
        try:
            if action=='stage':result=controller.stage_backup()
            elif action=='apply':result=controller.activate()
            elif action=='rollback':result=controller.rollback()
            else:result=controller.finalize()
        finally:os.close(descriptor)
    print(json.dumps(result,sort_keys=True,indent=2))

if __name__=='__main__':cli()

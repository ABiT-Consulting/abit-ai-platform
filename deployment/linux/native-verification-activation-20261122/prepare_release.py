"""One create-only online backup and fresh Core release binding; no service control.

The eleven SQLite backups are individually consistent snapshots. They are never
restored automatically. Output contains metadata/hashes only; private copies stay
inside the new root-only stage. An incomplete stage is retained and never reused.
"""
from pathlib import Path
from contextlib import closing
import datetime, fcntl, hashlib, json, os, shutil, socket, sqlite3, stat, subprocess, sys, time, urllib.request
BASE = Path('/srv/abit-dev-linux/abit-ai')
LIVE = BASE / 'releases/20261031-live-0.11.4/runtime/open_webui'
PACKAGE = BASE / 'deployment/native-verification-20261122T1615'
STAGE = Path('/var/lib/abit-ai-native-verification-20261122T1615')
LEASE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
OWNER = 'chat-authorized-root-orchestration-recovery-20261122T1007'
PREPARATION_SHA = 'UNBOUND'
SOURCE_MANIFEST_SHA = '77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6'
CONFIG_PATH = BASE / 'config/erp-assignment/resolver.json'
CONFIG = ('config/runtime-environment.json', 'config/final-snapshot.json',
          'config/webui-secret-key', 'deployment/run-production.py',
          'config/erp-assignment/resolver.json')
DATABASES = ('webui.db', 'agentic-governance.sqlite', 'abitdot-primary.sqlite',
             'aibit-chatgpt-relay.sqlite', 'abit-slack.sqlite', 'abit-telegram.sqlite',
             'codex-workspace-runs.sqlite', 'erp-dot-issues.sqlite',
             'erp-pending-review-qa.sqlite', 'erp-task-resolution.sqlite',
             'maintenance-admission/gate.sqlite')
ALLOWED = {'routers/agentic_governance.py', 'utils/agentic_contracts/validator.py',
           'utils/agentic_contracts/schemas/governance_v1.schema.json',
           'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
           'utils/agentic_native_execution.py'}
NEW_LEAVES = {'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
              'utils/agentic_native_execution.py'}
UNIT_FILES = ['/etc/systemd/system/abit-ai.service',
              '/etc/systemd/system/abit-ai.service.d/40-agentic-gateway.conf',
              '/etc/systemd/system/abit-ai.service.d/erp-assignment.conf']
ORIGINAL_EFFECTIVE = {'TimeoutStopUSec': '1min 30s', 'SendSIGKILL': 'yes',
                      'KillMode': 'control-group', 'KillSignal': '15',
                      'Restart': 'on-failure', 'RestartUSec': '10s'}
OVERRIDE = Path('/run/systemd/system/abit-ai.service.d/zzzz-native-verification-20261122T1615.conf')
OVERRIDE_BYTES = b'[Service]\nTimeoutStopSec=infinity\nSendSIGKILL=no\nKillMode=mixed\nRestart=no\n'
CGROUP = '/system.slice/abit-ai.service'
GLOBAL_LOCK = Path('/var/lock/abit-ai-governance-release.lock')
DEADLINE = time.monotonic() + 240
def require(ok,msg):
 if not ok: raise RuntimeError(msg)

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(raw): return hashlib.sha256(raw).hexdigest()

def regular(p):
 s=p.lstat()
 require(stat.S_ISREG(s.st_mode) and not stat.S_ISLNK(s.st_mode),'Regular source required')
 return s

def save(p,raw):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:
  f.write(raw); f.flush(); os.fsync(f.fileno())

def json_save(p,obj): save(p,(json.dumps(obj,sort_keys=True,indent=2)+'\n').encode())

def checkpoint(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try: os.fsync(fd)
 finally: os.close(fd)

def table_summary(db):
 names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
 rows=[]
 for name in names:
  quoted='"'+name.replace('"','""')+'"'
  rows.append((name,db.execute('SELECT count(*) FROM '+quoted).fetchone()[0]))
 schema=list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name"))
 return {'table_count':len(names),'counts_sha256':sha(json.dumps(rows).encode()),'schema_sha256':sha(json.dumps(schema).encode())}

def deadline():
 require(time.monotonic()<DEADLINE,'Backup time bound exceeded; preserve partial stage')

def sql_deadline():return 1 if time.monotonic()>=DEADLINE else 0

def progress(status,remaining,total):deadline()

def filehash(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):
   deadline();h.update(block)
 return h.hexdigest()

def validate_owner():
 s=json.loads(LEASE.read_text())
 require(s['owner']==OWNER and s['state']=='ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY','Operator lease changed')
 expiry=datetime.datetime.fromisoformat(s['lease_expires_at'].replace('Z','+00:00'))
 require(expiry>datetime.datetime.now(datetime.timezone.utc),'Operator lease expired')

def raw(path):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'Regular file required')
  return f.read()

def process(pid):
 p=Path('/proc')/str(pid);fields=(p/'stat').read_text().rsplit(')',1)[1].split()
 return {'pid':pid,'start_ticks':int(fields[19]),'uid':p.stat().st_uid,'cmdline_sha256':sha((p/'cmdline').read_bytes())}

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def http(path, port=8888):
    require((port, path) in ((8888, '/health'), (8888, '/api/config'),
                            (8810, '/runtime-status')), 'Unsupported status path')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                     headers={'Connection': 'close'}, method='GET')
    maximum = 4096 if port == 8810 else 262144
    with opener.open(request, timeout=5) as response:
        require(response.status == 200, 'Status HTTP code differs')
        encoded = response.read(maximum + 1)
        require(len(encoded) <= maximum, 'Status body exceeded fixed bound')
        value = json.loads(encoded)
        require(type(value) is dict, 'Status body is not an object')
        return value

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def db_rows(m, relative, queries):
    path = BASE / 'data' / relative
    before = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(before.st_mode)
            and before.st_uid == m['core_before']['uid'], 'Work ledger metadata changed')
    deadline = time.monotonic() + 15
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute('BEGIN')
        result = [[dict(row) for row in db.execute(sql, params)] for sql, params in queries]
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_uid, before.st_mode) ==
            (after.st_dev, after.st_ino, after.st_uid, after.st_mode),
            'Work ledger identity changed during read')
    require(time.monotonic() < deadline, 'Work observation deadline exceeded')
    return result

def gate(m):
    rows = db_rows(m, 'maintenance-admission/gate.sqlite',
                   [('SELECT state,epoch,command_id,actor FROM gate WHERE id=1', ())])[0]
    require(len(rows) == 1 and rows[0]['state'] in ('open', 'draining', 'sealed')
            and type(rows[0]['epoch']) is int, 'Maintenance gate unavailable')
    return rows[0]

def preserve(m):
 for value in m['preserved_processes'].values():require(process(value['pid'])==value,'Original relay/browser/QA process changed')

def relay_idle(m):
    expected = m['preserved_processes']['relay']
    require(process(expected['pid']) == expected, 'Relay process changed before status')
    value = http('/runtime-status', 8810)
    require(value.get('ok') is True and value.get('protocol') == 'v1_graceful_relay_drain'
            and value.get('pid') == expected['pid']
            and value.get('draining') is False and value.get('server_closed') is False,
            'Relay lifecycle status differs')
    counts = ('active_http_handlers', 'active_relay_turns', 'erp_active_turns', 'erp_inspections')
    require(all(type(value.get(k)) is int and value[k] == 0 for k in counts),
            'Relay has active accepted work; preserve it')
    require(process(expected['pid']) == expected, 'Relay changed during status')
    return {k: value[k] for k in ('protocol', 'pid', 'draining', 'server_closed', *counts)}

def ledger_snapshot(m, identity, strict=True, idle=True, check_identity=True):
    checks = [
        ('SELECT state,epoch FROM gate WHERE id=1', ()),
        ("SELECT state,COUNT(*) AS n FROM lease WHERE state IN ('active','unresolved') "
         "AND pid=? AND process_start=? GROUP BY state",
         (identity['pid'] if identity else -1, str(identity['start_ticks']) if identity else '')),
    ]
    gate_rows, owned = db_rows(m, 'maintenance-admission/gate.sqlite', checks)
    current_count = sum(row['n'] for row in owned)
    result = {'observed_utc': now(), 'current_core_lease_count': current_count,
              'maintenance_state': gate_rows[0]['state'],
              'maintenance_epoch': gate_rows[0]['epoch'], 'historical_queues': {}}
    if strict:
        require(current_count == 0, 'Current Core has active/unresolved leases')
    for label, relative, table, key, states in [
        ('relay', 'aibit-chatgpt-relay.sqlite', 'jobs', 'job_id', "('queued','running')"),
        ('dot', 'abitdot-primary.sqlite', 'dot_primary_runs', 'id', "('prepared','dispatching')"),
    ]:
        rows = db_rows(m, relative,
                       [('SELECT * FROM ' + table + ' WHERE status IN ' + states +
                         ' ORDER BY ' + key + ' LIMIT 65', ())])[0]
        require(len(rows) <= 64, 'Bounded provider observation exceeded')
        counts = {}
        for row in rows:
            counts[row['status']] = counts.get(row['status'], 0) + 1
        summary = {'count': len(rows), 'states': counts, 'full_rows_sha256': sha(canonical(rows)),
                   'created_min': min((row['created_at'] for row in rows), default=None),
                   'updated_max': max((row['updated_at'] for row in rows), default=None)}
        result['historical_queues'][label] = summary
        if strict:
            require(summary == m['graceful']['historical'][label],
                    'Provider cohort changed; preserve unknown originals and hold: ' + label)
    for label, relative, query in [
        ('governance', 'agentic-governance.sqlite',
         "SELECT state,COUNT(*) AS n FROM governance_invocation "
         "WHERE state IN ('running','cancel_requested') GROUP BY state"),
        ('codex', 'codex-workspace-runs.sqlite',
         "SELECT state,COUNT(*) AS n FROM runs WHERE state IN ('submitting','running') GROUP BY state"),
    ]:
        rows = db_rows(m, relative, [(query, ())])[0]
        result[label] = {row['state']: row['n'] for row in rows}
        if strict:
            require(not rows, 'Current execution remains in ' + label)
    if idle:
        result['relay_runtime_status'] = relay_idle(m)
    if identity and check_identity:
        require(process(identity['pid']) == identity, 'Core changed during work observation')
    result['historical_outcomes_completed'] = False
    result['globally_sealed_drain'] = False
    return result

def unit(name='abit-ai.service'):
    require(name == 'abit-ai.service', 'Only Core may be controlled')
    props = ['MainPID', 'ActiveState', 'SubState', 'User', 'FragmentPath',
             'ControlGroup', 'DropInPaths', 'TimeoutStopUSec', 'SendSIGKILL',
             'KillMode', 'KillSignal', 'Restart', 'RestartUSec']
    result = subprocess.run(['/usr/bin/systemctl', 'show', name,
                             *sum((['-p', key] for key in props), [])],
                            check=True, capture_output=True, text=True, timeout=10)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

def file_metadata(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and info.st_nlink == 1, 'Unit file is not an exclusive regular file')
    return {'path': str(path), 'sha256': filehash(path), 'uid': info.st_uid,
            'gid': info.st_gid, 'mode': oct(stat.S_IMODE(info.st_mode)), 'bytes': info.st_size}

def boot_identity():
    values = dict(line.split(None, 1) for line in Path('/proc/stat').read_text().splitlines()
                  if line.startswith('btime '))
    return {'btime': int(values['btime']), 'clock_ticks': os.sysconf('SC_CLK_TCK'),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}

def config_binding(identity):
    require(process(identity['pid']) == identity, 'Core changed before environment proof')
    encoded = raw_proc_environment(identity['pid'])
    prefix = b'ABIT_ERP_TASK_RESOLUTION_CONFIG='
    matches = [part[len(prefix):] for part in encoded.split(b'\0') if part.startswith(prefix)]
    require(matches == [str(CONFIG_PATH).encode()], 'Actual Core integration configuration differs')
    require(process(identity['pid']) == identity, 'Core changed during environment proof')

def raw_proc_environment(pid):
    with open('/proc/' + str(pid) + '/environ', 'rb') as stream:
        value = stream.read(1024 * 1024 + 1)
    require(len(value) <= 1024 * 1024, 'Process environment exceeded bounded private read')
    return value

def default_off(m):
    require(m.get('native_registration_enabled') is False
            and m.get('native_configuration_path') == str(CONFIG_PATH),
            'Native default-off configuration binding required')
    info = CONFIG_PATH.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_size <= 2 * 1024 * 1024 and CONFIG_PATH.resolve() == CONFIG_PATH,
            'Native configuration file metadata differs')
    config = json.loads(raw(CONFIG_PATH))
    require(type(config) is dict, 'Core configuration is not an object')
    selected = config.get('agentic_native_verification')
    require(selected is None or
            (type(selected) is dict and selected.get('enabled') is False),
            'Native verification must remain explicitly disabled or absent')

def current_hash(path):
    return filehash(path) if os.path.lexists(path) else None

def source(m, mode):
    require(mode in ('before', 'after', 'partial'), 'Unsupported source phase')
    require((BASE / 'current/runtime/open_webui').resolve() == LIVE, 'Active runtime changed')
    baseline = m['baseline']
    require(len(baseline) == 378 and set(m['changes']) == ALLOWED
            and {k for k, v in baseline.items() if v['sha256'] is None} == NEW_LEAVES,
            'Unexpected complete source scope')
    expected_paths = set(baseline) - NEW_LEAVES
    actual_paths = {str(p.relative_to(LIVE)) for p in LIVE.rglob('*.py')}
    actual_paths.update(str(p.relative_to(LIVE)) for p in
                        (LIVE / 'utils/agentic_contracts/schemas').glob('*.json'))
    require(actual_paths - NEW_LEAVES == expected_paths
            and actual_paths <= set(baseline), 'Source inventory changed')
    for rel, row in baseline.items():
        path = LIVE / rel
        require(path.resolve() == path and not path.is_symlink(), 'Source path changed')
        expected = row['sha256']
        if rel in ALLOWED and mode == 'after':
            expected = m['changes'][rel]['after_sha256']
        permitted = {expected}
        if rel in ALLOWED and mode == 'partial':
            permitted.add(m['changes'][rel]['after_sha256'])
        actual = current_hash(path)
        require(actual in permitted, 'Installed source changed: ' + rel)
        if actual is not None:
            info = path.stat()
            require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                    (row['uid'], row['gid'], int(row['mode'], 8)),
                    'Source metadata changed: ' + rel)
    for rel, row in m['changes'].items():
        info = (LIVE / rel).parent.stat()
        require((info.st_dev, info.st_ino) == (row['parent_device'], row['parent_inode']),
                'Target parent changed')
    for row in m['configuration']:
        require(file_metadata(Path(row['path'])) == row, 'Runtime configuration changed')
    default_off(m)

def acquire_release_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                'Release lock metadata changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise

def owner():
    validate_owner()


def backup_databases(report):
    """Retained eleven-file online backup and isolated restore verification."""
    for name in DATABASES:
       deadline()
       src=BASE/'data'/name; srcmeta=regular(src)
       destination=STAGE/'database'/name.replace('/','--')
       fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
       with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True,timeout=5) as source:
        source.execute('PRAGMA query_only=ON')
        started=now()
        with sqlite3.connect(destination,timeout=5) as backup:
         backup.set_progress_handler(sql_deadline,1000)
         source.backup(backup,pages=256,progress=progress,sleep=0.025)
         require(backup.execute('PRAGMA quick_check').fetchall()==[('ok',)],'Backup integrity failed')
         summary=table_summary(backup)
        source_version=source.execute('PRAGMA data_version').fetchone()[0]
       os.chmod(destination,0o600)
       regular(destination)
       with open(destination,'rb') as f:os.fsync(f.fileno())
       # Exercise the same SQLite engine restoring the backup to a new isolated file.
       restored=STAGE/'restore-check'/destination.name
       fd=os.open(restored,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
       with sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True) as backup:
        backup.execute('PRAGMA query_only=ON')
        with sqlite3.connect(restored) as restore:
         restore.set_progress_handler(sql_deadline,1000)
         backup.backup(restore,pages=256,progress=progress,sleep=0.025)
         require(restore.execute('PRAGMA quick_check').fetchall()==[('ok',)],'Isolated restored copy integrity failed')
         require(table_summary(restore)==summary,'Isolated restored copy differs')
       row={'relative_source':name,'snapshot_started_utc':started,'snapshot_finished_utc':now(),
        'source_uid':srcmeta.st_uid,'source_gid':srcmeta.st_gid,'source_mode':oct(stat.S_IMODE(srcmeta.st_mode)),
        'backup_bytes':destination.stat().st_size,'backup_sha256':filehash(destination),
        'integrity':'ok','isolated_restore_integrity':'ok','isolated_restore_schema_and_counts_match':True,
        'source_data_version_observed':source_version,**summary}
       report['databases'].append(row)
       restored.unlink()


def read_preparation():
    require(PREPARATION_SHA != 'UNBOUND', 'Unbound preparation cannot execute')
    value = raw(PACKAGE / 'preparation.json')
    require(sha(value) == PREPARATION_SHA, 'Preparation binding changed')
    m = json.loads(value)
    require(m['purpose'] == 'NATIVE_VERIFICATION_DEFAULT_OFF_CORE_INSTALL_V1'
            and m['runtime_root'] == str(LIVE)
            and m['source_package_sha256'] == SOURCE_MANIFEST_SHA,
            'Wrong source preparation scope')
    product_bytes = raw(PACKAGE / 'source-manifest.json')
    require(sha(product_bytes) == SOURCE_MANIFEST_SHA, 'Reviewed source package changed')
    product = json.loads(product_bytes)
    expected = {r['path']: r for r in product['core_files']}
    require(set(m['changes']) == ALLOWED == set(expected), 'Unexpected six-file scope')
    for rel, row in m['changes'].items():
        require(row['before_sha256'] == expected[rel]['preimage_sha256']
                and row['after_sha256'] == expected[rel]['postimage_sha256']
                and row['candidate'] == str(PACKAGE / 'payload/open_webui' / rel)
                and filehash(Path(row['candidate'])) == row['after_sha256'],
                'Candidate source changed')
    for row in m['evidence']:
        require(filehash(Path(row['path'])) == row['sha256'], 'Source review evidence changed')
    return m


def fresh_snapshot(m, initial):
    validate_owner()
    require((BASE / 'current/runtime/open_webui').resolve() == LIVE, 'Active runtime changed')
    if initial:
        m['configuration'] = [file_metadata(BASE / rel) for rel in CONFIG]
        m['graceful']['unit_files'] = [file_metadata(Path(p)) for p in UNIT_FILES]
    source(m, 'before')
    require(not os.path.lexists(OVERRIDE), 'Previous temporary override is present')
    identity = m['core_before']
    require(process(identity['pid']) == identity, 'Current Core identity changed')
    config_binding(identity)
    preserve(m)
    value = unit()
    require(value.get('User') == 'mcpdev'
            and value.get('FragmentPath') == UNIT_FILES[0]
            and sorted(value.get('DropInPaths', '').split()) == sorted(UNIT_FILES[1:])
            and value.get('ControlGroup') == CGROUP
            and value.get('ActiveState') == 'active' and value.get('SubState') == 'running'
            and int(value.get('MainPID') or 0) == identity['pid']
            and all(value.get(k) == v for k, v in ORIGINAL_EFFECTIVE.items()),
            'Current effective Core service differs')
    for row in m['graceful']['unit_files']:
        require(file_metadata(Path(row['path'])) == row, 'Unit changed during preparation')
        require(row['uid'] == row['gid'] == 0 and int(row['mode'], 8) == 0o644,
                'Unit ownership/mode changed')
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == '0.11.4', 'Core health/config failed')
    before_gate = gate(m)
    require(before_gate['state'] == 'open', 'Preparation requires original open admission')
    work = ledger_snapshot(m, identity, strict=not initial)
    require(work['current_core_lease_count'] == 0
            and not work['governance'] and not work['codex'], 'Current Core work is active')
    safe_gate = {'state': before_gate['state'], 'epoch': before_gate['epoch'],
                 'command_id_sha256': sha(before_gate['command_id'].encode()),
                 'actor_sha256': sha(before_gate['actor'].encode())}
    if initial:
        m['graceful']['gate_before'] = safe_gate
        m['graceful']['historical'] = work['historical_queues']
        m['graceful']['boot'] = boot_identity()
        identities = [identity, m['preserved_processes']['relay'],
                      m['preserved_processes']['preserved_2']]
        boot = m['graceful']['boot']
        m['graceful']['original_process_birth_min'] = boot['btime'] + min(
            r['start_ticks'] for r in identities) / boot['clock_ticks']
    else:
        require(safe_gate == m['graceful']['gate_before']
                and boot_identity() == m['graceful']['boot'], 'Admission/boot changed')
    for summary in work['historical_queues'].values():
        require(summary['count'] == 0 or
                summary['updated_max'] < m['graceful']['original_process_birth_min'],
                'Current provider work is not an old preserved cohort')
    require(gate(m) == before_gate and process(identity['pid']) == identity,
            'Core/admission changed during preparation')
    preserve(m)
    return work


def capacity_preflight():
    total = 0
    for name in DATABASES:
        path = BASE / 'data' / name
        info = regular(path)
        require(path.resolve() == path and info.st_nlink == 1, 'Database alias changed')
        total += info.st_size
        wal = Path(str(path) + '-wal')
        if os.path.lexists(wal):
            meta = regular(wal)
            require(wal.resolve() == wal and meta.st_nlink == 1, 'Database WAL alias changed')
            total += meta.st_size
    needed = max(3 * 1024**3, 2 * total + 512 * 1024**2)
    require(shutil.disk_usage(STAGE.parent).free > needed, 'Backup reserve insufficient')
    return {'measured_source_and_wal_bytes': total, 'minimum_free_bytes': needed}


def backup_configuration(m, report):
    for rel in CONFIG:
        deadline()
        source_path = BASE / rel
        expected = next(row for row in m['configuration'] if row['path'] == str(source_path))
        require(file_metadata(source_path) == expected, 'Configuration preimage changed')
        encoded = raw(source_path)
        require(sha(encoded) == expected['sha256'], 'Configuration changed before copy')
        save(STAGE / 'configuration' / rel.replace('/', '--'), encoded)
        require(file_metadata(source_path) == expected, 'Configuration changed during backup')
        report['configuration'].append(expected)
    for index, row in enumerate(m['graceful']['unit_files']):
        deadline()
        source_path = Path(row['path'])
        require(file_metadata(source_path) == row, 'Unit preimage changed')
        encoded = raw(source_path)
        require(sha(encoded) == row['sha256'], 'Unit changed before backup')
        save(STAGE / 'configuration' / ('unit--' + str(index)), encoded)
        require(file_metadata(source_path) == row, 'Unit changed during backup')
    report['unit_files'] = m['graceful']['unit_files']


def backup_sources(m, report):
    for rel, row in sorted(m['changes'].items()):
        deadline()
        path = LIVE / rel
        expected = row['before_sha256']
        require(current_hash(path) == expected, 'Source changed before rollback backup')
        if expected is None:
            require(rel in NEW_LEAVES, 'Unexpected absent preimage')
            report['source'].append({'relative_source': rel, 'exists': False, 'sha256': None})
        else:
            info = regular(path)
            encoded = raw(path)
            require(sha(encoded) == expected, 'Source changed while copying preimage')
            save(STAGE / 'source' / rel.replace('/', '--'), encoded)
            require(current_hash(path) == expected, 'Source changed after rollback backup')
            report['source'].append({'relative_source': rel, 'exists': True,
                                    'sha256': expected, 'bytes': len(encoded),
                                    'uid': info.st_uid, 'gid': info.st_gid,
                                    'mode': oct(stat.S_IMODE(info.st_mode))})


def main():
    require(os.geteuid() == 0 and os.environ.get('SUDO_USER') == 'jimran@abitcons.com',
            'Existing jimran administrator route required')
    require(socket.gethostname() == 'abit-dev-linux', 'Wrong host')
    require(len(sys.argv) == 1, 'Preparation takes no runtime arguments')
    validate_owner()
    lock = acquire_release_lock(GLOBAL_LOCK)
    try:
        m = read_preparation()
        require(not os.path.lexists(STAGE), 'One-use stage exists; inspect original result')
        preflight = fresh_snapshot(m, initial=True)
        reserve = capacity_preflight()
        require(not os.path.lexists(STAGE), 'One-use stage appeared during preflight')
        STAGE.mkdir(mode=0o700)
        checkpoint(STAGE.parent)
        for name in ('database', 'restore-check', 'configuration', 'source'):
            (STAGE / name).mkdir(mode=0o700)
        report = {'schema_version': 1, 'started_utc': now(),
                  'purpose': 'NATIVE_VERIFICATION_CORE_PROTECTED_ONLINE_BACKUP',
                  'source_database_modified': False,
                  'cross_database_atomic_snapshot_claimed': False,
                  'automatic_database_restore_allowed': False,
                  'databases': [], 'configuration': [], 'source': [],
                  'preflight': preflight, 'capacity': reserve}
        json_save(STAGE / 'backup-intent.json', {'started_utc': now(), 'operator': OWNER,
                  'preparation_sha256': PREPARATION_SHA, 'databases': DATABASES})
        checkpoint(STAGE)
        try:
            backup_databases(report)
            backup_configuration(m, report)
            backup_sources(m, report)
            report['post_backup'] = fresh_snapshot(m, initial=False)
            report.update(status='PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED',
                          finished_utc=now(), sqlite_version=sqlite3.sqlite_version)
            json_save(STAGE / 'backup-receipt.json', report)
            m.update(created_utc=now(), backup_receipt_sha256=filehash(STAGE / 'backup-receipt.json'),
                     live_activation_authorized=False, native_registration_enabled=False)
            m['validation']['reviewed'] = False
            m['graceful']['observed_utc'] = now()
            json_save(STAGE / 'release-manifest.draft.json', m)
            for name in ('database', 'restore-check', 'configuration', 'source'):
                checkpoint(STAGE / name)
            checkpoint(STAGE)
            print(json.dumps({'status': report['status'], 'stage': str(STAGE),
                              'database_count': len(report['databases']),
                              'backup_receipt_sha256': filehash(STAGE / 'backup-receipt.json'),
                              'draft_manifest_sha256': filehash(STAGE / 'release-manifest.draft.json'),
                              'source_or_service_changed': False,
                              'native_registration_enabled': False}), flush=True)
        except BaseException as error:
            json_save(STAGE / 'backup-failed.json',
                      {'failed_utc': now(), 'error_type': type(error).__name__,
                       'completed_databases': len(report['databases']),
                       'partial_stage_retained': True})
            checkpoint(STAGE)
            raise
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()

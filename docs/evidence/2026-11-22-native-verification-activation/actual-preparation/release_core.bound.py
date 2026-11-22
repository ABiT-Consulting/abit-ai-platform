"""One-use Core-only graceful activation; never kills providers or replays work.

Source candidate. MANIFEST_SHA must be bound by the separately reviewing operator.
Historical records remain unsettled; this is not a globally sealed provider drain.
"""
from pathlib import Path
from contextlib import closing
import datetime, fcntl, hashlib, json, os, socket, sqlite3, stat, subprocess, sys, time, urllib.request
PACKAGE = Path('/srv/abit-dev-linux/abit-ai/deployment/native-verification-20261122T1615')
STAGE = Path('/var/lib/abit-ai-native-verification-20261122T1615')
BASE = Path('/srv/abit-dev-linux/abit-ai')
LIVE = BASE / 'releases/20261031-live-0.11.4/runtime/open_webui'
LEASE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
OWNER = 'chat-authorized-root-orchestration-recovery-20261122T1007'
COMMAND_PREFIX = OWNER + ':native-verification-20261122T1615'
ACTOR = COMMAND_PREFIX + ':core-graceful'
MANIFEST_SHA = 'd98cd51865d15f37089440ff26f5a2c25fbdacd5fad8b6321e1d40540b656b4f'
ALLOWED = {'routers/agentic_governance.py', 'utils/agentic_contracts/validator.py',
           'utils/agentic_contracts/schemas/governance_v1.schema.json',
           'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
           'utils/agentic_native_execution.py'}
NEW_LEAVES = {'utils/agentic_native_contract.py', 'utils/agentic_native_verification.py',
              'utils/agentic_native_execution.py'}
CONFIG_PATH = BASE / 'config/erp-assignment/resolver.json'
SOURCE_MANIFEST_SHA = '77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6'
PRESERVED_NAMES = {'preserved_0', 'preserved_1', 'preserved_2', 'preserved_3', 'qa', 'relay', 'watchdog'}
DATABASES = ('webui.db', 'agentic-governance.sqlite', 'abitdot-primary.sqlite',
             'aibit-chatgpt-relay.sqlite', 'abit-slack.sqlite', 'abit-telegram.sqlite',
             'codex-workspace-runs.sqlite', 'erp-dot-issues.sqlite',
             'erp-pending-review-qa.sqlite', 'erp-task-resolution.sqlite',
             'maintenance-admission/gate.sqlite')
UNIT_FILES = ['/etc/systemd/system/abit-ai.service',
              '/etc/systemd/system/abit-ai.service.d/40-agentic-gateway.conf',
              '/etc/systemd/system/abit-ai.service.d/erp-assignment.conf']
CGROUP = '/system.slice/abit-ai.service'
OVERRIDE = Path('/run/systemd/system/abit-ai.service.d/zzzz-native-verification-20261122T1615.conf')
OVERRIDE_BYTES = b'[Service]\nTimeoutStopSec=infinity\nSendSIGKILL=no\nKillMode=mixed\nRestart=no\n'
ORIGINAL_EFFECTIVE = {'TimeoutStopUSec': '1min 30s', 'SendSIGKILL': 'yes',
                      'KillMode': 'control-group', 'KillSignal': '15',
                      'Restart': 'on-failure', 'RestartUSec': '10s'}
TEMP_EFFECTIVE = {'TimeoutStopUSec': 'infinity', 'SendSIGKILL': 'no',
                  'KillMode': 'mixed', 'KillSignal': '15',
                  'Restart': 'no', 'RestartUSec': '10s'}
OBSERVE_SECONDS = 150
GLOBAL_LOCK = Path('/var/lock/abit-ai-governance-release.lock')

def require(ok,msg):
 if not ok:raise RuntimeError(msg)

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(data):return hashlib.sha256(data).hexdigest()

def raw(path):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'Regular file required')
  return f.read()

def filehash(path):
 h=hashlib.sha256()
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'Regular file required')
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def fsyncdir(path):
 fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(fd)
 finally:os.close(fd)

def save(path,data):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())

def savejson(path,value):save(path,(json.dumps(value,indent=2,sort_keys=True)+'\n').encode())

def protected(path,directory=False):
 s=path.lstat()
 require(not stat.S_ISLNK(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==(0o700 if directory else 0o600),'Protected stage metadata changed')
 require(stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode),'Protected stage type changed')

def owner():
 s=json.loads(LEASE.read_text())
 require(s.get('owner')==OWNER and s.get('state')=='ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY','Operator lease changed')
 require(datetime.datetime.fromisoformat(s['lease_expires_at'].replace('Z','+00:00'))>datetime.datetime.now(datetime.timezone.utc),'Operator lease expired')

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

def preserve(m):
 for value in m['preserved_processes'].values():require(process(value['pid'])==value,'Original relay/browser/QA process changed')

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

def backup(m):
    protected(STAGE, True)
    protected(STAGE / 'backup-receipt.json')
    require(filehash(STAGE / 'backup-receipt.json') == m['backup_receipt_sha256'],
            'Backup receipt changed')
    report = json.loads(raw(STAGE / 'backup-receipt.json'))
    require(report['status'] == 'PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED'
            and [r['relative_source'] for r in report['databases']] == list(DATABASES),
            'All eleven verified backups required')
    for row in report['databases']:
        path = STAGE / 'database' / row['relative_source'].replace('/', '--')
        protected(path)
        require(filehash(path) == row['backup_sha256'] and row['integrity'] == 'ok'
                and row['isolated_restore_integrity'] == 'ok'
                and row['isolated_restore_schema_and_counts_match'] is True,
                'Backup verification changed')
    recorded = {r['relative_source']: r for r in report['source']}
    require(set(recorded) == ALLOWED, 'Rollback source coverage changed')
    for rel, row in m['changes'].items():
        path = STAGE / 'source' / rel.replace('/', '--')
        record = recorded[rel]
        require(record['sha256'] == row['before_sha256'], 'Rollback preimage binding changed')
        if row['before_sha256'] is None:
            require(record.get('exists') is False and not os.path.lexists(path),
                    'New-leaf original absence proof changed')
        else:
            protected(path)
            require(filehash(path) == row['before_sha256'], 'Rollback bytes changed')
    require(report['unit_files'] == m['graceful']['unit_files'], 'Unit backup scope differs')
    for index, row in enumerate(report['unit_files']):
        path = STAGE / 'configuration' / ('unit--' + str(index))
        protected(path)
        require(filehash(path) == row['sha256'], 'Unit backup changed')

def base_manifest():
    require(MANIFEST_SHA != 'UNBOUND', 'Unbound release cannot execute')
    encoded = raw(PACKAGE / 'release-manifest.json')
    require(sha(encoded) == MANIFEST_SHA, 'Release manifest changed')
    m = json.loads(encoded)
    require(m['purpose'] == 'NATIVE_VERIFICATION_DEFAULT_OFF_CORE_INSTALL_V1'
            and m['runtime_root'] == str(LIVE)
            and m['source_package_sha256'] == SOURCE_MANIFEST_SHA,
            'Unexpected release scope')
    package_bytes = raw(PACKAGE / 'source-manifest.json')
    require(sha(package_bytes) == SOURCE_MANIFEST_SHA, 'Reviewed source package changed')
    product = json.loads(package_bytes)
    pinned = {r['path']: r for r in product['core_files']}
    require(set(m['changes']) == ALLOWED == set(pinned), 'Unexpected runtime targets')
    for rel, row in m['changes'].items():
        require(row['before_sha256'] == m['baseline'][rel]['sha256']
                == pinned[rel]['preimage_sha256']
                and row['after_sha256'] == pinned[rel]['postimage_sha256'],
                'Source pre/postimage binding changed')
        require(row['candidate'] == str(PACKAGE / 'payload/open_webui' / rel),
                'Candidate path changed')
        require(filehash(Path(row['candidate'])) == row['after_sha256'], 'Candidate changed')
    for row in m['evidence']:
        require(filehash(Path(row['path'])) == row['sha256'], 'Reviewed evidence changed')
    require(m['validation']['reviewed'] is True
            and m['validation']['api_verify_passed'] is True
            and m['validation']['api_integration_failed'] == 0,
            'Required review/API gates incomplete')
    return m, encoded

def install(p,data,row):
 temp=p.with_name(p.name+'.recovery-'+str(os.getpid())+'.tmp')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,int(row['mode'],8))
 try:
  with os.fdopen(fd,'wb') as f:
   f.write(data);f.flush();os.fchmod(f.fileno(),int(row['mode'],8));os.fchown(f.fileno(),row['uid'],row['gid']);os.fsync(f.fileno())
  os.replace(temp,p);fsyncdir(p.parent)
 finally:
  if temp.exists():temp.unlink()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def update(receipt):
    receipt['updated_utc'] = now()
    temp = STAGE / ('graceful-activation-' + str(os.getpid()) + '.tmp')
    savejson(temp, receipt)
    os.replace(temp, STAGE / 'graceful-activation.json')
    fsyncdir(STAGE)
    print(json.dumps({'state': receipt['state'], 'utc': now()}), flush=True)

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

def unit_files(m):
    expected = m['graceful']['unit_files']
    require([row['path'] for row in expected] == UNIT_FILES, 'Unit file allowlist differs')
    for row in expected:
        require(file_metadata(Path(row['path'])) == row, 'Pinned unit/drop-in changed')
        require(row['uid'] == row['gid'] == 0 and int(row['mode'], 8) == 0o644,
                'Unit file ownership/mode differs')

def override_exists():
    return os.path.lexists(OVERRIDE)

def check_override():
    info = OVERRIDE.lstat()
    require(OVERRIDE.resolve() == OVERRIDE and stat.S_ISREG(info.st_mode)
            and info.st_uid == info.st_gid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o644
            and raw(OVERRIDE) == OVERRIDE_BYTES, 'Our runtime override changed')

def service(m, override=None):
    unit_files(m)
    value = unit()
    require(value.get('User') == 'mcpdev'
            and value.get('FragmentPath') == UNIT_FILES[0], 'Core unit identity changed')
    actual = value.get('DropInPaths', '').split()
    wanted = UNIT_FILES[1:] + ([str(OVERRIDE)] if override else [])
    if override is not None:
        require(sorted(actual) == sorted(wanted), 'Effective drop-in set changed')
        expected = (TEMP_EFFECTIVE if override else m['graceful']['unit_effective'])
        for key, expected_value in expected.items():
            require(value.get(key) == expected_value, 'Effective unit setting differs: ' + key)
    else:
        require(sorted(actual) in [sorted(UNIT_FILES[1:]),
                                   sorted(UNIT_FILES[1:] + [str(OVERRIDE)])],
                'Unreviewed effective drop-in')
    group = value.get('ControlGroup', '')
    require(group in ('', CGROUP), 'Core cgroup changed')
    return value

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

def gate_before_matches(m, value):
    expected = m['graceful']['gate_before']
    return (value['state'] == expected['state'] and value['epoch'] == expected['epoch']
            and sha(value['command_id'].encode()) == expected['command_id_sha256']
            and sha(value['actor'].encode()) == expected['actor_sha256'])

def control_receipt(m, intent):
    value = gate(m)
    expected_body = {'action': intent['action'], 'expected_epoch': intent['expected_epoch'],
                     'actor': ACTOR}
    rows = db_rows(m, 'maintenance-admission/gate.sqlite',
                   [('SELECT kind,payload FROM audit WHERE event_id=?',
                     (intent['command_id'],))])[0]
    require(len(rows) == 1 and rows[0]['kind'] == 'operator_control'
            and json.loads(rows[0]['payload']) == expected_body,
            'Exact maintenance control receipt is absent; never repeat an unknown command')
    expected_state = 'draining' if intent['action'] == 'close' else 'open'
    require(value == {'state': expected_state, 'epoch': intent['expected_epoch'] + 1,
                      'command_id': intent['command_id'], 'actor': ACTOR},
            'Owned maintenance epoch/command changed')
    return value

def held(m, cycle):
    require('close' in cycle['intents'], 'Owned close intent required')
    return control_receipt(m, cycle['intents']['close'])

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

def validate_manifest():
    m, encoded = base_manifest()
    require(m.get('live_activation_authorized') is True, 'Final activation binding required')
    require(set(m['preserved_processes']) == PRESERVED_NAMES,
            'Exact seven-process preservation set required')
    g = m.get('graceful', {})
    require(g.get('policy') == 'CORE_ONLY_GRACEFUL_V1'
            and g.get('cgroup') == CGROUP
            and g.get('temporary_dropin_sha256') == sha(OVERRIDE_BYTES),
            'Graceful release policy is unbound')
    require(g.get('maintenance_module_sha256') ==
            m['baseline']['utils/agentic_maintenance.py']['sha256'],
            'Maintenance CLI source binding differs')
    require(g.get('gate_before', {}).get('state') == 'open'
            and type(g['gate_before'].get('epoch')) is int,
            'Initial open epoch required')
    require(g.get('boot') == boot_identity(), 'Original boot evidence changed')
    identities = [m['core_before'], m['preserved_processes']['relay'],
                  m['preserved_processes']['preserved_2']]
    birth = g['boot']['btime'] + min(r['start_ticks'] for r in identities) / g['boot']['clock_ticks']
    require(g.get('original_process_birth_min') == birth, 'Process birth binding differs')
    require(set(g.get('historical', {})) == {'relay', 'dot'}, 'Historical cohorts missing')
    for summary in g['historical'].values():
        require(type(summary['count']) is int and 0 <= summary['count'] <= 64
                and sum(summary['states'].values()) == summary['count'],
                'Historical cohort exceeds fixed bounds')
        require(summary['count'] == 0 or summary['updated_max'] < birth,
                'Unsettled provider record is not older than the preserved cohort')
    require(g['unit_effective'] == ORIGINAL_EFFECTIVE, 'Original unit behavior differs')
    require(m['app_version'] == '0.11.4' and m['native_registration_enabled'] is False,
            'Default-off installation contract differs')
    unit_files(m)
    default_off(m)
    return m, encoded

def consistent_snapshot(m, mode, identity, override):
    owner()
    source(m, mode)
    backup(m)
    preserve(m)
    value = service(m, override)
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and int(value['MainPID']) == identity['pid']
            and process(identity['pid']) == identity, 'Core process changed')
    config_binding(identity)
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == m['app_version'], 'Core health/config failed')
    work = ledger_snapshot(m, identity)
    require(service(m, override) == value and process(identity['pid']) == identity,
            'Core changed during preflight')
    return work

def begin_intent(r, cycle, key, fields):
    existing = cycle['intents'].get(key)
    if existing is not None:
        return existing, False
    value = dict(fields, requested_utc=now(),
                 requested_tick=int(time.clock_gettime(time.CLOCK_BOOTTIME) *
                                    os.sysconf('SC_CLK_TCK')))
    cycle['intents'][key] = value
    r['state'] = cycle['mode'] + '_' + key + '_intent'
    update(r)
    return value, True

def finished(r, cycle, key, proof):
    intent = cycle['intents'][key]
    intent['fulfilled_utc'] = now()
    intent['proof'] = proof
    r['state'] = cycle['mode'] + '_' + key + '_verified'
    update(r)

def completed(cycle, key):
    return bool(cycle['intents'].get(key, {}).get('fulfilled_utc'))

def command_once(r, cycle, key, fields, argv, before):
    intent, fresh = begin_intent(r, cycle, key, dict(fields, command_phase='intent_saved'))
    if fresh or intent.get('command_phase') in ('intent_saved', 'precondition_failed'):
        try:
            before()
        except BaseException:
            # This exact state proves no command was invoked. A later finalize
            # may retry its preconditions; an invocation marker may never retry.
            intent['command_phase'] = 'precondition_failed'
            update(r)
            raise
        intent['command_phase'] = 'invocation_may_have_started'
        update(r)
        try:
            result = subprocess.run(argv, capture_output=True, timeout=30, check=False)
            intent['command_returncode'] = result.returncode
            intent['stdout_sha256'] = sha(result.stdout)
            intent['stderr_sha256'] = sha(result.stderr)
            update(r)
            require(result.returncode == 0, 'Operator command did not succeed: ' + key)
        except BaseException as error:
            intent['exception_type'] = type(error).__name__
            update(r)
            raise
    # Existing intents are observed only. Never reissue an unknown command.
    return intent

def prepare(r, m, encoded):
    protected(STAGE, True)
    for name in ('graceful-candidate', 'graceful-unit-backup'):
        path = STAGE / name
        if not path.exists():
            path.mkdir(mode=0o700)
            fsyncdir(STAGE)
        protected(path, True)
    paths = [(STAGE / 'graceful-release-manifest.json', encoded)]
    for rel, row in m['changes'].items():
        data = raw(Path(row['candidate']))
        require(sha(data) == row['after_sha256'], 'Candidate drift during protected preparation')
        paths.append((STAGE / 'graceful-candidate' / rel.replace('/', '--'), data))
    for index, row in enumerate(m['graceful']['unit_files']):
        data = raw(Path(row['path']))
        require(sha(data) == row['sha256'], 'Unit source changed during backup')
        paths.append((STAGE / 'graceful-unit-backup' / (str(index) + '.before'), data))
    for path, data in paths:
        if not path.exists():
            save(path, data)
            fsyncdir(path.parent)
        protected(path)
        require(raw(path) == data, 'Protected release input changed')
    r['prepared'] = True
    r['state'] = 'protected_release_prepared'
    update(r)

def check_prepared(m):
    protected(STAGE, True)
    protected(STAGE / 'graceful-release-manifest.json')
    require(filehash(STAGE / 'graceful-release-manifest.json') == MANIFEST_SHA,
            'Protected release manifest changed')
    for rel, row in m['changes'].items():
        path = STAGE / 'graceful-candidate' / rel.replace('/', '--')
        protected(path)
        require(filehash(path) == row['after_sha256'], 'Protected candidate changed')
    for index, row in enumerate(m['graceful']['unit_files']):
        path = STAGE / 'graceful-unit-backup' / (str(index) + '.before')
        protected(path)
        require(filehash(path) == row['sha256'], 'Pinned unit backup changed')
    backup(m)

def override_on(m, r, cycle):
    if completed(cycle, 'override_on'):
        check_override()
        service(m, True)
        return
    intent, fresh = begin_intent(r, cycle, 'override_file',
                                {'path': str(OVERRIDE), 'sha256': sha(OVERRIDE_BYTES)})
    # A fixed local create can resume from exact absence; an existing file
    # is accepted only after the same owner/content/type checks as first use.
    owner()
    unit_files(m)
    if not OVERRIDE.parent.exists():
        OVERRIDE.parent.mkdir(mode=0o755)
        fsyncdir(OVERRIDE.parent.parent)
    info = OVERRIDE.parent.lstat()
    require(OVERRIDE.parent.resolve() == OVERRIDE.parent and stat.S_ISDIR(info.st_mode)
            and info.st_uid == info.st_gid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022, 'Runtime drop-in directory differs')
    if not override_exists():
        save(OVERRIDE, OVERRIDE_BYTES)
        os.chmod(OVERRIDE, 0o644, follow_symlinks=False)
        fd = os.open(OVERRIDE, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        fsyncdir(OVERRIDE.parent)
    elif fresh:
        require(cycle['mode'] == 'rollback', 'Unexpected preexisting runtime override')
    check_override()
    finished(r, cycle, 'override_file', {'sha256': sha(OVERRIDE_BYTES)})
    current = service(m, None)
    if (cycle['mode'] == 'rollback' and
            all(current.get(k) == v for k, v in TEMP_EFFECTIVE.items()) and
            str(OVERRIDE) in current.get('DropInPaths', '').split()):
        begin_intent(r, cycle, 'override_on', {'borrowed_exact_existing_override': True})
    else:
        command_once(r, cycle, 'override_on', {'action': 'daemon-reload'},
                     ['/usr/bin/systemctl', 'daemon-reload'],
                     lambda: (owner(), check_override(), unit_files(m), preserve(m)))
    service(m, True)
    finished(r, cycle, 'override_on', {'effective': TEMP_EFFECTIVE})

def close_gate(m, r, cycle):
    if completed(cycle, 'close'):
        held(m, cycle)
        return
    if cycle.get('borrowed_close'):
        borrowed = cycle['borrowed_close']
        intent, _ = begin_intent(r, cycle, 'close',
                                 {k: borrowed[k] for k in ('action', 'expected_epoch', 'command_id')})
        control_receipt(m, intent)
    else:
        before = cycle['gate_before']
        fields = {'action': 'close', 'expected_epoch': before['epoch'],
                  'command_id': COMMAND_PREFIX + ':' + cycle['mode'] + ':core-close'}
        def check():
            owner()
            check_override()
            service(m, True)
            preserve(m)
            require(gate(m) == before, 'Maintenance gate changed before close')
            require(filehash(LIVE / 'utils/agentic_maintenance.py') ==
                    m['graceful']['maintenance_module_sha256'], 'Maintenance CLI changed')
        argv = ['/usr/bin/sudo', '-n', '-u', 'mcpdev', '--', '/usr/bin/python3',
                '-I', '-S', '-B', str(LIVE / 'utils/agentic_maintenance.py'), 'close',
                '--data-dir', str(BASE / 'data'), '--expected-epoch', str(before['epoch']),
                '--command-id', fields['command_id'], '--actor', ACTOR]
        intent = command_once(r, cycle, 'close', fields, argv, check)
        control_receipt(m, intent)
    finished(r, cycle, 'close', {'owned_epoch': intent['expected_epoch'] + 1})

def original_absent(identity):
    if identity is None:
        return True
    try:
        current = process(identity['pid'])
    except FileNotFoundError:
        return True
    require(current == identity or current['start_ticks'] != identity['start_ticks'],
            'Original PID metadata changed without a new start identity')
    return current['start_ticks'] != identity['start_ticks']

def cgroup_empty():
    path = Path('/sys/fs/cgroup') / CGROUP.lstrip('/')
    if not os.path.lexists(path):
        return True
    require(path.resolve() == path and path.is_dir(), 'Core cgroup path changed')
    procs = (path / 'cgroup.procs').read_text().split()
    events = dict(line.split() for line in (path / 'cgroup.events').read_text().splitlines())
    return not procs and events.get('populated') == '0'

def stopped(m, cycle):
    value = service(m, True)
    require(value['ActiveState'] == 'inactive' and value['SubState'] == 'dead'
            and int(value.get('MainPID') or 0) == 0
            and original_absent(cycle['previous_identity']) and cgroup_empty(),
            'Core has not fully ceased; leave its graceful drain running')
    return {'unit_inactive': True, 'main_pid_zero': True,
            'original_identity_absent': True, 'cgroup_empty': True, 'observed_utc': now()}

def stop_core(m, r, cycle):
    if completed(cycle, 'stop'):
        stopped(m, cycle)
        return
    previous = cycle['previous_identity']
    def check():
        owner()
        held(m, cycle)
        check_override()
        preserve(m)
        check_prepared(m)
        value = service(m, True)
        if previous:
            require(int(value.get('MainPID') or 0) == previous['pid']
                    and value['ActiveState'] == 'active' and value['SubState'] == 'running'
                    and process(previous['pid']) == previous, 'Core changed before stop')
            ledger_snapshot(m, previous)
        else:
            require(cycle['mode'] == 'rollback' and int(value.get('MainPID') or 0) == 0
                    and value['ActiveState'] in ('inactive', 'failed') and cgroup_empty(),
                    'Absent Core is not the owned rollback predecessor')
        source(m, 'partial')
    if cycle.get('borrowed_stop'):
        old_intent = cycle['borrowed_stop']
        begin_intent(r, cycle, 'stop', {'action': 'observe-original-stop',
                                     'original_stop_sha256': sha(canonical(old_intent)),
                                     'previous_identity': previous})
    else:
        command_once(r, cycle, 'stop', {'action': 'stop', 'previous_identity': previous},
                     ['/usr/bin/systemctl', 'stop', '--no-block', 'abit-ai.service'], check)
    deadline = time.monotonic() + OBSERVE_SECONDS
    while time.monotonic() < deadline:
        owner()
        held(m, cycle)
        preserve(m)
        check_override()
        value = service(m, True)
        if (value['ActiveState'] == 'inactive' and value['SubState'] == 'dead'
                and int(value.get('MainPID') or 0) == 0
                and original_absent(previous) and cgroup_empty()):
            proof = stopped(m, cycle)
            finished(r, cycle, 'stop', proof)
            return
        require(value['ActiveState'] in ('active', 'deactivating', 'failed', 'inactive'),
                'Unexpected Core transition; no additional control issued')
        time.sleep(1)
    raise RuntimeError('Graceful stop observation timed out; no signal or repeated stop issued')

def install_source(m, r, cycle):
    target_mode = 'after' if cycle['mode'] == 'apply' else 'before'
    if completed(cycle, 'install'):
        source(m, target_mode)
        return
    owner()
    held(m, cycle)
    preserve(m)
    stopped(m, cycle)
    check_prepared(m)
    work = ledger_snapshot(m, cycle['previous_identity'], strict=cycle['mode'] == 'apply',
                           idle=cycle['mode'] == 'apply', check_identity=False)
    source(m, 'partial')
    begin_intent(r, cycle, 'install', {'mode': cycle['mode'], 'post_stop_work': work,
                                     'no_database_restore': True})
    for rel, row in sorted(m['changes'].items()):
        owner()
        held(m, cycle)
        preserve(m)
        stopped(m, cycle)
        target = LIVE / rel
        wanted = row['after_sha256'] if cycle['mode'] == 'apply' else row['before_sha256']
        actual = current_hash(target)
        require(actual in (row['before_sha256'], row['after_sha256']),
                'Concurrent source change')
        if actual != wanted:
            if wanted is None:
                require(cycle['mode'] == 'rollback' and rel in NEW_LEAVES
                        and actual == row['after_sha256'],
                        'Only an owned new postimage can be removed')
                info = target.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                        (m['baseline'][rel]['uid'], m['baseline'][rel]['gid'],
                         int(m['baseline'][rel]['mode'], 8)), 'New source metadata changed')
                target.unlink()
                fsyncdir(target.parent)
            else:
                path = STAGE / ('graceful-candidate' if cycle['mode'] == 'apply' else 'source') / rel.replace('/', '--')
                protected(path)
                data = raw(path)
                require(sha(data) == wanted, 'Protected source bytes changed')
                if actual is None:
                    require(cycle['mode'] == 'apply' and rel in NEW_LEAVES,
                            'Unexpected absent existing source')
                    install_new(target, data, m['baseline'][rel])
                else:
                    install(target, data, m['baseline'][rel])
        require(current_hash(target) == wanted, 'Installed source readback failed')
    source(m, target_mode)
    stopped(m, cycle)
    finished(r, cycle, 'install', {'source_mode': target_mode, 'targets': sorted(ALLOWED),
                                  'databases_restored': False})

def core_from_start(m, cycle, override=True, receipt=None):
    value = service(m, override)
    intent = cycle['intents']['start']
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and int(value.get('MainPID') or 0) > 0, 'Owned Core start not active')
    current = process(int(value['MainPID']))
    require(current['start_ticks'] >= intent['requested_tick']
            and current != cycle['previous_identity'], 'Core predates the one-use start')
    generation = {'pid': current['pid'], 'start_ticks': current['start_ticks']}
    if 'start_process' in cycle:
        require(generation == cycle['start_process'], 'One-use start generation changed')
    else:
        cycle['start_process'] = generation
        if receipt is not None:
            update(receipt)
    if cycle.get('core_after'):
        require(current == cycle['core_after'], 'Observed new Core identity changed')
    if (current['uid'] != m['core_before']['uid']
            or current['cmdline_sha256'] != m['core_before']['cmdline_sha256']):
        # A Type=simple launcher may be visible before its same-PID exec.
        # Waiting does not accept it, signal it, or permit another start.
        raise StartIdentityPending('Awaiting exact same-generation runtime exec identity')
    return current

def start_core(m, r, cycle):
    if completed(cycle, 'start'):
        core_from_start(m, cycle)
        return
    def check():
        owner()
        held(m, cycle)
        preserve(m)
        stopped(m, cycle)
        source(m, 'after' if cycle['mode'] == 'apply' else 'before')
        check_prepared(m)
    command_once(r, cycle, 'start', {'action': 'start', 'previous_identity': cycle['previous_identity']},
                 ['/usr/bin/systemctl', 'start', '--no-block', 'abit-ai.service'], check)
    deadline = time.monotonic() + OBSERVE_SECONDS
    while time.monotonic() < deadline:
        owner()
        held(m, cycle)
        preserve(m)
        value = service(m, True)
        require(value['ActiveState'] != 'failed', 'One-use Core start failed; no automatic retry')
        try:
            if value['ActiveState'] == 'active' and value['SubState'] == 'running':
                current = core_from_start(m, cycle, receipt=r)
                if (http('/health').get('status') is True
                        and http('/api/config').get('version') == m['app_version']):
                    cycle['core_after'] = current
                    finished(r, cycle, 'start', {'identity': current, 'health': True,
                                                'config_version': m['app_version']})
                    return
        except (OSError, TimeoutError, urllib.error.URLError, StartIdentityPending):
            pass
        time.sleep(1)
    raise RuntimeError('Core start observation timed out; finalize only, never start again')

def restore_override(m, r, cycle):
    if completed(cycle, 'override_off'):
        require(not override_exists(), 'Owned override unexpectedly returned')
        service(m, False)
        return
    owner()
    held(m, cycle)
    preserve(m)
    current = core_from_start(m, cycle, None)
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == m['app_version'], 'Core health/config failed')
    _, fresh = begin_intent(r, cycle, 'override_remove',
                            {'path': str(OVERRIDE), 'sha256': sha(OVERRIDE_BYTES)})
    if override_exists():
        # Only the exact owned file can be removed; absence is the completed
        # local outcome, not permission to repeat any systemd command.
        check_override()
        OVERRIDE.unlink()
        fsyncdir(OVERRIDE.parent)
    require(not override_exists(), 'Owned override removal did not complete')
    command_once(r, cycle, 'override_off', {'action': 'daemon-reload'},
                 ['/usr/bin/systemctl', 'daemon-reload'],
                 lambda: (owner(), held(m, cycle), unit_files(m), preserve(m)))
    service(m, False)
    require(process(current['pid']) == current, 'Core changed during override restoration')
    finished(r, cycle, 'override_off', {'effective': m['graceful']['unit_effective'],
                                      'only_owned_dropin_removed': True})

def healthy(m, cycle, override):
    mode = 'after' if cycle['mode'] == 'apply' else 'before'
    source(m, mode)
    check_prepared(m)
    preserve(m)
    value = service(m, override)
    current = process(int(value['MainPID'])) if int(value.get('MainPID') or 0) else None
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and current == cycle.get('core_after'), 'Bound Core identity is not active')
    config_binding(current)
    require(http('/health').get('status') is True
            and http('/api/config').get('version') == m['app_version'], 'Core health/config failed')
    return current

def open_gate(m, r, cycle):
    if completed(cycle, 'open'):
        control_receipt(m, cycle['intents']['open'])
        return
    if ('open' in cycle['intents'] and cycle['intents']['open'].get('command_phase')
            not in ('intent_saved', 'precondition_failed')):
        healthy(m, cycle, False)
        proof = control_receipt(m, cycle['intents']['open'])
        finished(r, cycle, 'open', {'owned_epoch': proof['epoch'], 'reconciled_original': True})
        return
    previous = held(m, cycle)
    current = healthy(m, cycle, False)
    # A rollback can restore old-runtime availability while holding admission
    # if post-stop work changed. Never clear or settle that work to reopen.
    try:
        proof = ledger_snapshot(m, current)
    except BaseException:
        if cycle['mode'] == 'rollback':
            r['state'] = 'rolled_back_health_verified_admission_held'
            update(r)
        raise
    fields = {'action': 'open', 'expected_epoch': previous['epoch'],
              'command_id': COMMAND_PREFIX + ':' + cycle['mode'] + ':core-open'}
    def check():
        owner()
        held(m, cycle)
        healthy(m, cycle, False)
        ledger_snapshot(m, current)
        require(filehash(LIVE / 'utils/agentic_maintenance.py') ==
                m['graceful']['maintenance_module_sha256'], 'Maintenance CLI changed')
    argv = ['/usr/bin/sudo', '-n', '-u', 'mcpdev', '--', '/usr/bin/python3',
            '-I', '-S', '-B', str(LIVE / 'utils/agentic_maintenance.py'), 'open',
            '--data-dir', str(BASE / 'data'), '--expected-epoch', str(previous['epoch']),
            '--command-id', fields['command_id'], '--actor', ACTOR]
    intent = command_once(r, cycle, 'open', fields, argv, check)
    control_receipt(m, intent)
    finished(r, cycle, 'open', {'owned_epoch': previous['epoch'] + 1,
                              'pre_open_work': proof})

def run_cycle(m, r, cycle):
    if completed(cycle, 'open'):
        healthy(m, cycle, False)
        control_receipt(m, cycle['intents']['open'])
    else:
        # Do not re-evaluate an earlier inactive-state proof after a later
        # one-use start; only the current phase may issue its next command.
        if not completed(cycle, 'override_on'):
            override_on(m, r, cycle)
        if not completed(cycle, 'close'):
            close_gate(m, r, cycle)
        if not completed(cycle, 'stop'):
            stop_core(m, r, cycle)
        if not completed(cycle, 'install'):
            install_source(m, r, cycle)
        if not completed(cycle, 'start'):
            start_core(m, r, cycle)
        if not completed(cycle, 'override_off'):
            restore_override(m, r, cycle)
        if not completed(cycle, 'open'):
            open_gate(m, r, cycle)
        healthy(m, cycle, False)
    r.update(state='deployed_health_verified' if cycle['mode'] == 'apply'
             else 'rolled_back_health_verified', verified_utc=now(),
             core_after=cycle['core_after'], business_workflow_complete=False,
             browser_and_relay_and_qa_preserved=True, databases_restored=False,
             historical_outcomes_completed=False, globally_sealed_drain=False,
             original_jobs_resubmitted=False)
    update(r)

def begin_rollback(m, r):
    previous = r['cycles'][-1]
    require(previous['mode'] == 'apply', 'Only one explicit rollback is allowed')
    owner()
    check_prepared(m)
    source(m, 'partial')
    preserve(m)
    value = service(m, None)
    current_gate = gate(m)
    borrowed_close = None
    if 'close' in previous['intents']:
        try:
            control_receipt(m, previous['intents']['close'])
            borrowed_close = previous['intents']['close']
        except RuntimeError:
            require(completed(previous, 'open'), 'Unknown gate control; preserve it')
            control_receipt(m, previous['intents']['open'])
    else:
        require(gate_before_matches(m, current_gate), 'Initial gate changed')
    identity = None
    borrowed_stop = None
    if (previous['intents'].get('stop') and not previous['intents'].get('start')):
        # A requested apply stop is never reissued by rollback.
        identity = previous['previous_identity']
        borrowed_stop = previous['intents']['stop']
        require(borrowed_close is not None, 'Original stop lacks owned close')
    elif int(value.get('MainPID') or 0):
        require(value['ActiveState'] == 'active' and value['SubState'] == 'running',
                'Original transition has not settled; observe it before rollback')
        identity = process(int(value['MainPID']))
        if previous['intents'].get('start'):
            expected = previous['intents']['start']
            require(identity['uid'] == m['core_before']['uid']
                    and identity['cmdline_sha256'] == m['core_before']['cmdline_sha256']
                    and identity['start_ticks'] >= expected['requested_tick'],
                    'Rollback runtime is not our apply start')
        else:
            require(identity == m['core_before'], 'Original Core changed before rollback')
    else:
        require(previous['intents'].get('stop') and value['ActiveState'] in ('inactive', 'failed')
                and original_absent(previous['previous_identity']) and cgroup_empty(),
                'Stopped Core is not an owned apply outcome')
    cycle = {'mode': 'rollback', 'previous_identity': identity, 'gate_before': current_gate,
             'intents': {}, 'started_utc': now(), 'previous_apply_sha256': sha(canonical(previous))}
    if borrowed_close:
        cycle['borrowed_close'] = {k: borrowed_close[k]
                                  for k in ('action', 'expected_epoch', 'command_id')}
    if borrowed_stop:
        cycle['borrowed_stop'] = borrowed_stop
    r['cycles'].append(cycle)
    r['state'] = 'rollback_requested'
    update(r)
    return cycle

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

def main():
    require(os.geteuid() == 0 and os.environ.get('SUDO_USER') == 'jimran@abitcons.com',
            'Existing authorized administrator route required')
    require(socket.gethostname() == 'abit-dev-linux', 'Wrong host')
    require(len(sys.argv) == 2 and sys.argv[1] in ('preflight', 'apply', 'finalize', 'rollback'),
            'Explicit graceful release action required')
    action = sys.argv[1]
    owner()
    global_lock = acquire_release_lock(GLOBAL_LOCK)
    try:
        m, encoded = validate_manifest()
        protected(STAGE, True)
        lock_path = STAGE / 'graceful-release.lock'
        lock = acquire_release_lock(lock_path)
        try:
            receipt_path = STAGE / 'graceful-activation.json'
            if action == 'preflight':
                require(not receipt_path.exists() and not override_exists(), 'Existing release requires finalize')
                require(gate_before_matches(m, gate(m)), 'Initial maintenance epoch changed')
                work = consistent_snapshot(m, 'before', m['core_before'], False)
                print(json.dumps({'state': 'graceful_preflight_passed', 'manifest_sha256': MANIFEST_SHA,
                                  'work': work, 'globally_sealed_drain': False}), flush=True)
                return
            if action == 'apply':
                require(not receipt_path.exists() and not override_exists(), 'Existing release requires finalize')
                require(gate_before_matches(m, gate(m)), 'Initial maintenance epoch changed')
                work = consistent_snapshot(m, 'before', m['core_before'], False)
                cycle = {'mode': 'apply', 'previous_identity': m['core_before'],
                         'gate_before': gate(m), 'intents': {}, 'started_utc': now()}
                r = {'schema_version': 1, 'manifest_sha256': MANIFEST_SHA, 'state': 'prepare_intent',
                     'started_utc': now(), 'cycles': [cycle], 'preflight_work': work,
                     'original_jobs_resubmitted': False, 'databases_restored': False}
                savejson(receipt_path, r)
                fsyncdir(STAGE)
            else:
                protected(receipt_path)
                r = json.loads(raw(receipt_path))
                require(r.get('schema_version') == 1 and r.get('manifest_sha256') == MANIFEST_SHA,
                        'Release journal binding changed')
                require([c['mode'] for c in r['cycles']] in (['apply'], ['apply', 'rollback']),
                        'Release lifecycle is ambiguous')
            if not r.get('prepared'):
                prepare(r, m, encoded)
            check_prepared(m)
            cycle = r['cycles'][-1]
            if action == 'rollback' and cycle['mode'] != 'rollback':
                cycle = begin_rollback(m, r)
            try:
                run_cycle(m, r, cycle)
            except BaseException as error:
                r['held_utc'] = now()
                r['held_error_type'] = type(error).__name__
                r['held_at_state'] = r['state']
                # Never force-kill, reopen, remove the owned safety override, start
                # another process, restore databases or replay provider work here.
                update(r)
                raise
        finally:
            os.close(lock)
    finally:
        os.close(global_lock)

class StartIdentityPending(RuntimeError):
    """Only bounded observation may wait for the owned launcher to exec."""


def current_hash(path):
    return filehash(path) if os.path.lexists(path) else None


def boot_identity():
    values = dict(line.split(None, 1) for line in Path('/proc/stat').read_text().splitlines()
                  if line.startswith('btime '))
    return {'btime': int(values['btime']), 'clock_ticks': os.sysconf('SC_CLK_TCK'),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


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


def install_new(path, data, row):
    """Link a fully fsynced owned inode into an absent target without replacement."""
    temp = path.with_name(path.name + '.native-new-' + str(os.getpid()) + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, int(row['mode'], 8))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), int(row['mode'], 8))
            os.fchown(stream.fileno(), row['uid'], row['gid'])
            os.fsync(stream.fileno())
        os.link(temp, path, follow_symlinks=False)
        fsyncdir(path.parent)
    finally:
        if temp.exists():
            temp.unlink()
            fsyncdir(temp.parent)

if __name__ == '__main__':
    main()

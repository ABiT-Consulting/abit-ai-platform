"""One-use, exact three-file relay activation with a bootstrap admission seal; root binds immutable inputs separately.

Only the owned Node process may receive one graceful SIGTERM per recorded cycle.
Chrome, watchdog, Core, QA, original provider rows and selectors are preserved.
No provider/browser action, whole-unit restart, force kill or unknown-action retry.
"""
from contextlib import closing
from pathlib import Path
import datetime
import fcntl
import hashlib
import http.client
import json
import os
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time

PACKAGE = Path('/srv/abit-dev-linux/abit-ai-restored-target-activation-20261122/deployment/linux/restored-target-activation-20261122')
SOURCE_PACKAGE = PACKAGE / 'runtime'
SOURCE_BASE_COMMIT = '26a374594f9d6f7b477d5f440fc36ae1b41a4390'
SOURCE_MANIFEST_SHA = 'd6001e29d6b3ef1b5b6c3ba544e92526080e6745b1d0e4279c11eab0d293adc1'
MANIFEST_SHA = 'UNBOUND'
STAGE = Path('/var/lib/abit-ai-restored-target-activation-20261122')
BASE = Path('/srv/abit-dev-linux/abit-ai')
LIVE = Path('/srv/abit-dev-linux/relay-runtime/service')
STATE = LIVE.parent / 'state'
CORE = BASE / 'releases/20261031-live-0.11.4/runtime/open_webui'
LEASE = Path('/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json')
GLOBAL_LOCK = Path('/var/lock/abit-ai-governance-release.lock')
CORE_RECEIPT = BASE / 'deployment/orchestration-recovery-20261122T1015/core-independent-live-readback.json'
CORE_RECEIPT_SHA = '827fd094665f8407cab1bc5166aebe21bf20673bfc34a9335cd1de3abfcfdaf6'
TARGETS = {
    'original_dot_target_probe.cjs': (None, 'b0c08836cbc116754e8c6fe53cea738cd18804671df8450c2b02614c316a884f'),
    'aibit_relay.cjs': ('5bf18e2aac4a26d422ea3951fe0b99411950161d63b47a19ebe47870b2d6b18e',
                        '06dd2e383c67748be7351eb2d1924943a001bff94d92423c07aa766a64344475'),
    'erp_task_relay.cjs': ('0355af93e5aefb29836cad7ca2088171f6cca205e9f880952702ec77ef33a336',
                            'b28c6137be4218ef019de22f1a2012cd962b1228e9b4a903068bf44bf3de3865'),
}
PRESERVED_NAMES = {'chrome', 'watchdog', 'egress', 'xvfb', 'tunnel', 'qa', 'core'}
OBSERVE_SECONDS = 55
IDLE_MAX_AGE_SECONDS = 60
MAX_READ = 8 * 1024 * 1024
PROTECTED_CONFIGURATION = (
    BASE / 'config/runtime-environment.json',
    STATE / 'aibit-relay-target-id.txt', STATE / 'chat-thread-targets.json',
    STATE / 'aibit-relay-rate-limit.json',
)
UNIT_NAMES = ('aibit-linux-relay-watchdog.service', 'abit-ai.service', 'abit-ai-erp-review-qa.service')
UNIT_FIELDS = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'User', 'Group',
               'FragmentPath', 'DropInPaths', 'ControlGroup', 'KillMode', 'KillSignal',
               'SendSIGKILL', 'TimeoutStopUSec', 'Restart', 'RestartUSec')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def raw(path, limit=MAX_READ):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 <= before.st_size <= limit, 'Bounded single-link regular input required')
        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        attrs = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(data) == before.st_size <= limit
                and all(getattr(before, key) == getattr(after, key) ==
                        getattr(path.lstat(), key) for key in attrs), 'Input changed during read')
        return bytes(data)
    finally:
        os.close(fd)


def filehash(path):
    try:
        return sha(raw(path))
    except FileNotFoundError:
        return None


def fsyncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsyncdir(Path(path).parent)


def savejson(path, value):
    save(path, canonical(value) + b'\n')


def protected(path, directory=False):
    item = Path(path).lstat()
    require(item.st_uid == item.st_gid == 0 and not stat.S_ISLNK(item.st_mode)
            and stat.S_IMODE(item.st_mode) == (0o700 if directory else 0o600),
            'Protected operator input metadata differs')
    require(stat.S_ISDIR(item.st_mode) if directory else
            stat.S_ISREG(item.st_mode) and item.st_nlink == 1, 'Protected operator input type differs')


def process(pid):
    directory = Path('/proc') / str(pid)
    fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    require(fields[0] not in ('Z', 'X'), 'Owned process is not live')
    result = {'pid': pid, 'start_ticks': int(fields[19]), 'uid': directory.stat().st_uid,
              'cmdline_sha256': sha((directory / 'cmdline').read_bytes())}
    require((directory / 'stat').read_text().rsplit(')', 1)[1].split()[19] == fields[19],
            'Process changed during identity read')
    return result


def owner(m):
    value = json.loads(raw(LEASE))
    require(value.get('owner') == m['operator']['owner'] and
            value.get('state') == 'ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY', 'Operator lease changed')
    expires = datetime.datetime.fromisoformat(value['lease_expires_at'].replace('Z', '+00:00'))
    require(expires > datetime.datetime.now(datetime.timezone.utc), 'Operator lease expired')


def preserve(m):
    for expected in m['preserved_processes'].values():
        require(process(expected['pid']) == expected, 'Preserved process changed')


def units(m):
    result = {}
    for name in UNIT_NAMES:
        output = subprocess.run(['/usr/bin/systemctl', 'show', name, '--no-pager',
                                 '--property=' + ','.join(UNIT_FIELDS)],
                                capture_output=True, text=True, timeout=10, check=True)
        result[name] = dict(line.split('=', 1) for line in output.stdout.splitlines() if '=' in line)
    require(result == m['units'], 'Effective unit state changed')
    require(result['aibit-linux-relay-watchdog.service']['MainPID'] ==
            str(m['preserved_processes']['watchdog']['pid']), 'Watchdog ownership changed')
    return result


def db_rows(m, relative, queries):
    require(relative in {'maintenance-admission/gate.sqlite', 'aibit-chatgpt-relay.sqlite',
                        'abitdot-primary.sqlite', 'agentic-governance.sqlite',
                        'codex-workspace-runs.sqlite'}, 'Unapproved work-ledger path')
    path = BASE / 'data' / relative
    before = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(before.st_mode)
            and before.st_uid == m['preserved_processes']['core']['uid'], 'Work ledger metadata differs')
    deadline = time.monotonic() + 15
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute('BEGIN')
        result = [[dict(row) for row in db.execute(sql, params)] for sql, params in queries]
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_uid, before.st_mode) ==
            (after.st_dev, after.st_ino, after.st_uid, after.st_mode), 'Work ledger identity changed')
    require(time.monotonic() < deadline, 'Work observation deadline exceeded')
    return result


def gate(m):
    rows = db_rows(m, 'maintenance-admission/gate.sqlite',
                   [('SELECT state,epoch,command_id,actor FROM gate WHERE id=1', ())])[0]
    require(len(rows) == 1 and rows[0]['state'] in ('open', 'draining', 'sealed')
            and type(rows[0]['epoch']) is int, 'Maintenance gate unavailable')
    return rows[0]


def control_receipt(m, intent):
    value = gate(m)
    body = {'action': intent['action'], 'expected_epoch': intent['expected_epoch'],
            'actor': m['operator']['actor']}
    rows = db_rows(m, 'maintenance-admission/gate.sqlite',
                   [('SELECT kind,payload FROM audit WHERE event_id=?', (intent['command_id'],))])[0]
    require(len(rows) == 1 and rows[0]['kind'] == 'operator_control'
            and json.loads(rows[0]['payload']) == body,
            'Exact maintenance receipt absent; never repeat an unknown command')
    state = 'draining' if intent['action'] == 'close' else 'open'
    require(value == {'state': state, 'epoch': intent['expected_epoch'] + 1,
                      'command_id': intent['command_id'], 'actor': m['operator']['actor']},
            'Owned maintenance epoch or command changed')
    return value


def ledger_snapshot(m, verify=True):
    core = m['preserved_processes']['core']
    active = db_rows(m, 'maintenance-admission/gate.sqlite', [
        ("SELECT state,COUNT(*) AS n FROM lease WHERE state IN ('active','unresolved') "
         'AND pid=? AND process_start=? GROUP BY state', (core['pid'], str(core['start_ticks']))),
    ])[0]
    require(not active, 'Current Core has active or unresolved admissions')
    historical = {}
    for label, relative, table, key, states in (
        ('relay', 'aibit-chatgpt-relay.sqlite', 'jobs', 'job_id', "('queued','running')"),
        ('dot', 'abitdot-primary.sqlite', 'dot_primary_runs', 'id', "('prepared','dispatching')"),
    ):
        rows = db_rows(m, relative, [('SELECT * FROM ' + table + ' WHERE status IN ' + states +
                                      ' ORDER BY ' + key + ' LIMIT 65', ())])[0]
        require(len(rows) <= 64, 'Provider observation bound exceeded')
        historical[label] = {'count': len(rows), 'full_rows_sha256': sha(canonical(rows))}
    if verify:
        require(historical == m['historical_queues'], 'Historical provider cohort changed; preserve originals')
    for relative, sql in (
        ('agentic-governance.sqlite', "SELECT state,COUNT(*) AS n FROM governance_invocation "
         "WHERE state IN ('running','cancel_requested') GROUP BY state"),
        ('codex-workspace-runs.sqlite', "SELECT state,COUNT(*) AS n FROM runs "
         "WHERE state IN ('submitting','running') GROUP BY state"),
    ):
        require(not db_rows(m, relative, [(sql, ())])[0], 'Current execution remains active')
    originals = {}
    for table, key in (
        ('governance_invocation', 'invocation_id'), ('governance_start', 'invocation_id'),
        ('governance_result', 'invocation_id'), ('governance_local_result', 'invocation_id'),
    ):
        rows = db_rows(m, 'agentic-governance.sqlite',
                       [('SELECT * FROM ' + table + ' ORDER BY ' + key + ' LIMIT 4097', ())])[0]
        require(len(rows) <= 4096 and len(canonical(rows)) <= MAX_READ,
                'Original governance preservation bound exceeded')
        originals[table] = {'count': len(rows), 'full_rows_sha256': sha(canonical(rows))}
    if verify:
        require(originals == m['original_governance'], 'Original governance rows changed')
    return {'historical_queues': historical, 'original_governance': originals}


def browser_snapshot(m, verify=True):
    # CDP inventory only. Never evaluate, navigate, allocate or close a target.
    connection = http.client.HTTPConnection('127.0.0.1', 9228, timeout=5)
    try:
        connection.request('GET', '/json/list')
        response = connection.getresponse()
        data = response.read(1024 * 1024 + 1)
        require(response.status == 200 and len(data) <= 1024 * 1024, 'Read-only CDP inventory unavailable')
        targets = json.loads(data)
    finally:
        connection.close()
    require(type(targets) is list and len(targets) <= 256, 'CDP target inventory bound exceeded')
    pages = sorted(({'id': row['id'], 'url': row['url']} for row in targets
                    if type(row) is dict and row.get('type') == 'page'), key=lambda row: row['id'])
    require(all(type(row['id']) is str and type(row['url']) is str for row in pages),
            'CDP page inventory invalid')
    dedicated = raw(STATE / 'aibit-relay-target-id.txt', 128).decode().strip()
    require(any(row['id'] == dedicated and row['url'].startswith('https://chatgpt.com/')
                for row in pages), 'Existing dedicated relay target is absent; do not allocate one')
    proof = {'page_count': len(pages), 'pages_sha256': sha(canonical(pages)),
             'dedicated_target_sha256': sha(dedicated.encode())}
    if verify:
        require(proof == m['browser_inventory'], 'Existing browser targets changed')
    return proof


def file_proof(path):
    data = raw(path)
    value = Path(path).lstat()
    return {'sha256': sha(data), 'bytes': len(data), 'uid': value.st_uid, 'gid': value.st_gid,
            'mode': oct(stat.S_IMODE(value.st_mode)), 'mtime_ns': value.st_mtime_ns,
            'device': value.st_dev, 'inode': value.st_ino}


def private_preservation(m, verify=True):
    configuration = {}
    for path in PROTECTED_CONFIGURATION:
        configuration[str(path)] = file_proof(path) if os.path.lexists(path) else None
    bindings = STATE / 'original-dot-bindings-v1'
    require(bindings.resolve() == bindings and bindings.is_dir(), 'Original binding directory unavailable')
    leaves = sorted(bindings.iterdir())
    require(len(leaves) <= 4096, 'Original binding inventory exceeded')
    inventory = {}
    for path in leaves:
        require(re.fullmatch(r'[a-f0-9]{64}\.(base|root|conversation|terminal)\.json', path.name) is not None,
                'Unexpected original binding leaf')
        inventory[path.name] = file_proof(path)
    erp = STATE / 'erp-assignment'
    require(erp.resolve() == erp and erp.is_dir(), 'ERP relay journal unavailable')
    journals = sorted(erp.iterdir())
    require(len(journals) <= 4096, 'ERP relay inventory exceeded')
    journal_hashes = {}
    for path in journals:
        require(re.fullmatch(r'[a-f0-9]{64}\.json', path.name) is not None, 'Unexpected ERP journal leaf')
        value = json.loads(raw(path))
        require(value.get('status') in ('completed', 'failed', 'cancelled'),
                'Existing ERP deadline observer may act; preserve original work')
        journal_hashes[path.name] = file_proof(path)
    dev = LIVE / 'development-relay-runs.json'
    dev_proof = None
    if os.path.lexists(dev):
        value = json.loads(raw(dev))
        require(type(value) is dict and len(value) <= 4096 and all(
                type(row) is dict and row.get('state') in ('completed', 'failed', 'cancelled')
                for row in value.values()), 'Development relay work remains active')
        dev_proof = file_proof(dev)
    proof = {'configuration_sha256': sha(canonical(configuration)),
             'original_bindings_count': len(inventory), 'original_bindings_sha256': sha(canonical(inventory)),
             'erp_terminal_count': len(journal_hashes), 'erp_journals_sha256': sha(canonical(journal_hashes)),
             'development_journal': dev_proof}
    if verify:
        require(proof == m['private_preservation'], 'Protected relay state changed')
    return proof


def backup(m):
    root = Path(m['backup']['stage'])
    require(root in (Path('/var/lib/abit-ai-orchestration-recovery-20261122T1015'), STAGE / 'backup'),
            'Unreviewed backup source')
    protected(root, True)
    receipt = root / 'backup-receipt.json'
    protected(receipt)
    require(filehash(receipt) == m['backup']['receipt_sha256'], 'Verified backup receipt changed')
    value = json.loads(raw(receipt))
    require(value['status'] == 'PROTECTED_ONLINE_BACKUPS_AND_ISOLATED_RESTORE_CHECKS_PASSED'
            and len(value['databases']) == 11, 'Eleven verified online backups required')
    for row in value['databases']:
        relative = row['relative_source']
        require(type(relative) is str and '..' not in Path(relative).parts
                and not Path(relative).is_absolute(), 'Backup relative path differs')
        path = root / 'database' / relative.replace('/', '--')
        protected(path)
        require(row['integrity'] == row['isolated_restore_integrity'] == 'ok'
                and row['isolated_restore_schema_and_counts_match'] is True, 'Backup restore proof differs')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        require(digest.hexdigest() == row['backup_sha256'], 'Verified database backup bytes differ')


def source(m, allowed):
    require((BASE / 'current/runtime/open_webui').resolve() == CORE, 'Core runtime changed')
    require(filehash(CORE_RECEIPT) == CORE_RECEIPT_SHA, 'Accepted Core release proof changed')
    for relative, digest in m['unchanged_core'].items():
        require(relative in ('utils/agentic_transport_retry.py', 'utils/agentic_relay_scope.py',
                             'routers/agentic_governance.py', 'utils/agentic_maintenance.py'),
                'Unexpected Core source dependency')
        require(filehash(CORE / relative) == digest, 'Installed Core source changed')
    for name, row in m['runtime_files'].items():
        path = LIVE / name
        require(path.parent.resolve() == LIVE and not path.is_symlink(), 'Relay target path changed')
        parent = path.parent.lstat()
        require([parent.st_dev, parent.st_ino] == row['parent_identity'], 'Relay target parent changed')
        actual = filehash(path)
        require(actual in allowed[name], 'Relay source drift')
        if actual is not None:
            item = path.lstat()
            require([item.st_uid, item.st_gid, oct(stat.S_IMODE(item.st_mode))] == row['metadata'],
                    'Relay source metadata changed')
    for path, digest in m['unchanged_files'].items():
        value = Path(path)
        require(value.is_absolute() and value.resolve() == value and
                (value.parent in (LIVE, LIVE / 'agentic-gateway') or value == BASE / 'deployment/relay-watchdog.sh'
                 or str(value).startswith('/etc/systemd/system/')), 'Dependency path outside fixed source scope')
        require(filehash(value) == digest, 'Unchanged launcher, unit or source dependency changed')


def load_manifest():
    require(MANIFEST_SHA != 'UNBOUND', 'Operator manifest is unbound')
    encoded = raw(PACKAGE / 'activation-manifest.json')
    require(sha(encoded) == MANIFEST_SHA, 'Immutable activation manifest changed')
    m = json.loads(encoded)
    require(m['purpose'] == 'RESTORED_TARGET_DIAGNOSTIC_GUARDED_NODE_V2'
            and m['source_base_commit'] == SOURCE_BASE_COMMIT and m['source_manifest_sha256'] == SOURCE_MANIFEST_SHA,
            'Reviewed source identity differs')
    require(filehash(SOURCE_PACKAGE / 'manifest.json') == SOURCE_MANIFEST_SHA, 'Source package changed')
    package = json.loads(raw(SOURCE_PACKAGE / 'manifest.json'))
    require(m['unchanged_files'] == package['unchanged_files'], 'Exact runtime dependency closure required')
    normalized = raw(Path(__file__)).replace(
        ("MANIFEST_SHA = '" + MANIFEST_SHA + "'").encode(), b"MANIFEST_SHA = 'UNBOUND'", 1)
    require(sha(normalized) == m['release_helper_unbound_sha256'], 'Reviewed release helper changed')
    require(m['live_activation_authorized'] is True and set(m['runtime_files']) == set(TARGETS),
            'Exact reviewed three-file activation required')
    require(set(m['preserved_processes']) == PRESERVED_NAMES, 'Exact seven preserved processes required')
    require(set(m['unchanged_core']) == {'utils/agentic_transport_retry.py', 'utils/agentic_relay_scope.py',
                                      'routers/agentic_governance.py', 'utils/agentic_maintenance.py'},
            'Current Core dependency set differs')
    require(m['gate_before']['state'] == 'open' and type(m['gate_before']['epoch']) is int,
            'Fresh open gate binding required')
    require(set(m['historical_queues']) == {'relay', 'dot'}, 'Historical originals binding required')
    require(m['operator']['actor'] == m['operator']['owner'] + ':restored-target-relay',
            'Distinct relay operator actor required')
    for name, row in m['runtime_files'].items():
        require((row['before_sha256'], row['after_sha256']) == TARGETS[name]
                and row['metadata'] == [999, 988, '0o644'], 'Exact relay payload identity differs')
        require(filehash(SOURCE_PACKAGE / 'payload' / name) == row['after_sha256'], 'Reviewed payload changed')
    expected_operator = {'relay_bootstrap.py', 'relay_runtime_guard.py', 'browser_lock_keeper.cjs'}
    require(set(m['operator_source']) == expected_operator and
            all(filehash(PACKAGE / name) == digest for name, digest in m['operator_source'].items()),
            'Exact reviewed operator modules required')
    require(m['allow_rebuildable_catalog_expiry'] is True,
            'Only explicit derived-catalog expiry may be accepted')
    require(re.fullmatch('[a-f0-9]{64}', m['nft_before_sha256']) is not None,
            'Fresh complete firewall preimage required')
    return m, encoded


def operator_module(m, name):
    # Verify bytes before executing the fixed helper module. No caller imports.
    import types
    data = raw(PACKAGE / (name + '.py'))
    require(sha(data) == m['operator_source'][name + '.py'], 'Operator module changed')
    result = types.ModuleType(name)
    exec(compile(data, str(PACKAGE / (name + '.py')), 'exec'), result.__dict__)
    return result


def barriers(m, r, cycle):
    api = sys.modules[__name__]
    bootstrap = operator_module(m, 'relay_bootstrap').Bootstrap(api, m, r, cycle)
    guard = operator_module(m, 'relay_runtime_guard').RuntimeGuard(api, m, r, cycle, bootstrap)
    return bootstrap, guard


def allowed_images(mode):
    return {name: {pair[0 if mode == 'before' else 1]} if mode != 'mixed' else set(pair)
            for name, pair in TARGETS.items()}


def consistent(m, mode):
    owner(m)
    preserve(m)
    units(m)
    source(m, allowed_images(mode))
    ledger_snapshot(m)
    private_preservation(m)
    browser_snapshot(m)
    preserve(m)


def update(r):
    path = STAGE / ('journal-' + str(os.getpid()) + '.tmp')
    savejson(path, r)
    os.replace(path, STAGE / 'journal.json')
    fsyncdir(STAGE)


def begin_intent(r, cycle, key, fields):
    existing = cycle['intents'].get(key)
    if existing is not None:
        return existing, False
    value = dict(fields, requested_utc=now(), requested_tick=int(
        time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK')))
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


def command_once(r, cycle, key, fields, argv, before):
    intent, fresh = begin_intent(r, cycle, key, dict(fields, command_phase='intent_saved'))
    if fresh or intent.get('command_phase') in ('intent_saved', 'precondition_failed'):
        try:
            before()
        except BaseException:
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
    return intent


def gate_action(m, r, cycle, action, issue):
    if action in cycle['intents']:
        intent = cycle['intents'][action]
        if intent.get('command_phase') not in ('intent_saved', 'precondition_failed'):
            value = control_receipt(m, intent)
            if not intent.get('fulfilled_utc'):
                finished(r, cycle, action, value)
            return value
    require(issue, 'Recorded gate action has not been invoked; apply or rollback must continue')
    before_gate = cycle['gate_before'] if action == 'close' else control_receipt(m, cycle['intents']['close'])
    fields = {'action': action, 'expected_epoch': before_gate['epoch'],
              'command_id': m['operator']['owner'] + ':restored-target-relay:' + cycle['mode'] + ':' + action}
    def before():
        owner(m)
        preserve(m)
        require(gate(m) == before_gate, 'Maintenance gate changed before command')
        require(filehash(CORE / 'utils/agentic_maintenance.py') ==
                m['unchanged_core']['utils/agentic_maintenance.py'], 'Maintenance CLI source changed')
        if action == 'open':
            source(m, allowed_images('after' if cycle['mode'] == 'apply' else 'before'))
            require(cycle.get('sealed_preservation_verified') is True and
                    cycle['intents'].get('nft_unseal', {}).get('fulfilled_utc'),
                    'Sealed preservation and exact barrier removal required before Core reopen')
            verify_replacement(m, cycle, require_idle=False)
    argv = ['/usr/bin/sudo', '-n', '-u', 'mcpdev', '--', '/usr/bin/python3', '-I', '-S', '-B',
            str(CORE / 'utils/agentic_maintenance.py'), action, '--data-dir', str(BASE / 'data'),
            '--expected-epoch', str(before_gate['epoch']), '--command-id', fields['command_id'],
            '--actor', m['operator']['actor']]
    intent = command_once(r, cycle, action, fields, argv, before)
    value = control_receipt(m, intent)
    finished(r, cycle, action, value)
    return value


def save_or_verify(path, data):
    if os.path.lexists(path):
        protected(path)
        require(raw(path) == data, 'Previously prepared immutable input differs')
    else:
        save(path, data)


def prepare(m, encoded):
    protected(STAGE, True)
    protected(STAGE / 'inputs', True)
    journal = STAGE / 'journal.json'
    if os.path.lexists(journal):
        protected(journal)
        r = json.loads(raw(journal))
        require(r.get('manifest_sha256') == MANIFEST_SHA, 'Existing journal belongs to another activation')
    else:
        require(gate(m) == m['gate_before'] and process(m['relay_before']['pid']) == m['relay_before'],
                'Initial gate or Node changed')
        r = {'manifest_sha256': MANIFEST_SHA, 'state': 'preparing', 'cycles': {}, 'created_utc': now()}
        update(r)
    save_or_verify(STAGE / 'manifest.json', encoded)
    nft = operator_module(m, 'relay_bootstrap')
    existing, owned = nft.split_ruleset(nft.read_ruleset())
    require(nft.digest(existing) == m['nft_before_sha256'], 'Firewall preimage drift before staging')
    save_or_verify(STAGE / 'nft-before.json', canonical(existing) + b'\n')
    for name in ('before', 'after'):
        directory = STAGE / name
        if not os.path.lexists(directory):
            directory.mkdir(mode=0o700)
            fsyncdir(STAGE)
        protected(directory, True)
    for name, pair in TARGETS.items():
        save_or_verify(STAGE / 'after' / name, raw(SOURCE_PACKAGE / 'payload' / name))
        if pair[0] is None:
            save_or_verify(STAGE / 'before' / (name + '.absent.json'),
                           canonical({'path': name, 'before_sha256': None, 'manifest_sha256': MANIFEST_SHA}) + b'\n')
        elif not (STAGE / 'before' / name).exists():
            require(filehash(LIVE / name) == pair[0], 'Original rollback source unavailable')
            save(STAGE / 'before' / name, raw(LIVE / name))
    stage_check(m)
    if 'apply' not in r['cycles']:
        r['cycles']['apply'] = {'mode': 'apply', 'gate_before': m['gate_before'],
                                'node_before': m['relay_before'], 'intents': {}}
        r['state'] = 'prepared'
        update(r)
    return r


def stage_check(m):
    protected(STAGE, True)
    require(filehash(STAGE / 'manifest.json') == MANIFEST_SHA, 'Protected manifest differs')
    for name, pair in TARGETS.items():
        protected(STAGE / 'after' / name)
        require(filehash(STAGE / 'after' / name) == pair[1], 'Staged candidate differs')
        before = STAGE / 'before' / (name + '.absent.json' if pair[0] is None else name)
        protected(before)
        if pair[0] is None:
            require(json.loads(raw(before)) == {'path': name, 'before_sha256': None,
                    'manifest_sha256': MANIFEST_SHA}, 'Original absence proof differs')
        else:
            require(filehash(before) == pair[0], 'Staged rollback source differs')


def install(path, data, metadata, parent_identity):
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = path.name + '.restored-target-' + str(os.getpid())
    created = False
    try:
        parent = os.fstat(directory)
        require([parent.st_dev, parent.st_ino] == parent_identity, 'Destination directory changed')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     int(metadata[2], 8), dir_fd=directory)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchown(stream.fileno(), metadata[0], metadata[1])
            os.fchmod(stream.fileno(), int(metadata[2], 8))
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


def install_source(m, r, cycle):
    stage_check(m)
    source(m, allowed_images('mixed'))
    control_receipt(m, cycle['intents']['close'])
    names = list(TARGETS) if cycle['mode'] == 'apply' else list(reversed(TARGETS))
    for name in names:
        wanted = TARGETS[name][1 if cycle['mode'] == 'apply' else 0]
        current = filehash(LIVE / name)
        if current == wanted:
            continue
        owner(m)
        preserve(m)
        control_receipt(m, cycle['intents']['close'])
        require(current in TARGETS[name], 'Concurrent source replacement')
        intent, _ = begin_intent(r, cycle, 'source:' + name, {'wanted_sha256': wanted})
        if wanted is None:
            require(current == TARGETS[name][1], 'Refuse deletion of foreign helper')
            (LIVE / name).unlink()
            fsyncdir(LIVE)
        else:
            data = raw(STAGE / ('after' if cycle['mode'] == 'apply' else 'before') / name)
            require(sha(data) == wanted, 'Staged source bytes changed')
            install(LIVE / name, data, m['runtime_files'][name]['metadata'],
                    m['runtime_files'][name]['parent_identity'])
        require(filehash(LIVE / name) == wanted, 'Source readback failed')
        finished(r, cycle, 'source:' + name, {'sha256': wanted})
    source(m, allowed_images('after' if cycle['mode'] == 'apply' else 'before'))
    if cycle.get('watchdog_recovery') and 'source_complete_tick' not in cycle:
        cycle['source_complete_tick'] = int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
        update(r)


def signal_once(r, cycle, expected, before):
    intent, fresh = begin_intent(r, cycle, 'node_term',
                                {'node': expected, 'command_phase': 'intent_saved', 'signal': 'SIGTERM'})
    if fresh or intent.get('command_phase') in ('intent_saved', 'precondition_failed'):
        fd = None
        try:
            before()
            require(process(expected['pid']) == expected, 'Original Node identity changed')
            fd = os.pidfd_open(expected['pid'])
            require(process(expected['pid']) == expected, 'Node identity changed after pidfd open')
        except BaseException:
            intent['command_phase'] = 'precondition_failed'
            update(r)
            if fd is not None:
                os.close(fd)
            raise
        try:
            intent['command_phase'] = 'invocation_may_have_started'
            update(r)
            signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
            intent['signal_call_returned'] = True
            update(r)
        except BaseException as error:
            intent['exception_type'] = type(error).__name__
            update(r)
            raise
        finally:
            os.close(fd)
    return intent


def original_absent(expected):
    try:
        fields = (Path('/proc') / str(expected['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return True
    return int(fields[19]) != expected['start_ticks']


def relay_transport():
    # This authenticated relay's root path rejects before browser/health logic.
    connection = http.client.HTTPConnection('127.0.0.1', 8810, timeout=5)
    try:
        connection.request('GET', '/', headers={'Connection': 'close'})
        response = connection.getresponse()
        body = response.read(4097)
        require(response.status == 401 and len(body) <= 4096
                and json.loads(body) == {'ok': False, 'message': 'Unauthorized.'},
                'Fresh Node transport did not match the fixed unauthenticated response')
        return {'http_status': 401, 'browser_health_called': False, 'provider_readiness_proved': False}
    finally:
        connection.close()


def relay_current(m):
    raw_pid = raw(STATE / 'relay.pid', 64).strip()
    require(re.fullmatch(rb'[1-9][0-9]{0,8}', raw_pid) is not None, 'Relay PID file invalid')
    current = process(int(raw_pid))
    reference = m['relay_before']
    require(current['uid'] == reference['uid'] and
            current['cmdline_sha256'] == reference['cmdline_sha256'], 'Replacement executable identity differs')
    fields = (Path('/proc') / str(current['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()
    require(int(fields[1]) == m['preserved_processes']['watchdog']['pid'], 'Replacement has another owner')
    return current


def listener_owned(pid):
    rows = Path('/proc/net/tcp').read_text().splitlines()[1:]
    inodes = {row.split()[9] for row in rows if row.split()[1] == '0100007F:' + format(8810, '04X')
              and row.split()[3] == '0A'}
    require(len(inodes) == 1, 'Exact loopback relay listener unavailable')
    handles = list((Path('/proc') / str(pid) / 'fd').iterdir())
    require(len(handles) <= 4096, 'Relay descriptor inventory bound exceeded')
    found = set()
    for path in handles:
        try:
            target = os.readlink(path)
        except FileNotFoundError:
            continue
        match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
        if match:
            found.add(match.group(1))
    require(inodes <= found, 'Loopback listener belongs to another process')


def verify_replacement(m, cycle, require_idle=True):
    expected = cycle['node_before']
    intent = cycle['intents'].get('node_term')
    if cycle.get('watchdog_recovery'):
        require(intent is None and cycle.get('source_complete_tick') is not None,
                'Watchdog recovery requires completed rollback source')
        current = relay_current(m)
        require(current['start_ticks'] > cycle['source_complete_tick'],
                'Watchdog replacement may predate restored source; hold for review')
        if cycle.get('watchdog_after'):
            require(current == cycle['watchdog_after'], 'Recorded watchdog successor changed')
    elif cycle.get('no_node_transition'):
        require(intent is None and process(expected['pid']) == expected, 'Untouched original Node changed')
        current = expected
    else:
        require(intent is not None and intent.get('command_phase') == 'invocation_may_have_started',
                'No original Node transition was issued')
        require(original_absent(expected), 'Original Node has not ceased and been reaped')
        current = relay_current(m)
        require(current != expected and current['start_ticks'] >= intent['requested_tick'],
                'Replacement predates the single activation intent')
        recorded = intent.get('proof', {}).get('node_after')
        if recorded:
            require(current == recorded, 'Previously verified Node changed')
    preserve(m)
    listener_owned(current['pid'])
    transport = relay_transport()
    if cycle['mode'] == 'apply':
        connection = http.client.HTTPConnection('127.0.0.1', 8810, timeout=5)
        try:
            connection.request('GET', '/runtime-status', headers={'Connection': 'close'})
            response = connection.getresponse()
            body = response.read(4097)
            require(response.status == 200 and len(body) <= 4096, 'Pure lifecycle status unavailable')
            status = json.loads(body)
            require(status.get('ok') is True and status.get('protocol') == 'v1_graceful_relay_drain'
                    and status.get('pid') == current['pid'] and status.get('draining') is False
                    and status.get('server_closed') is False, 'Fresh candidate lifecycle differs')
            for key in ('active_http_handlers', 'active_relay_turns', 'erp_active_turns', 'erp_inspections'):
                require(type(status.get(key)) is int and status[key] >= 0
                        and (not require_idle or status[key] == 0), 'Fresh candidate lifecycle is not idle')
            transport['lifecycle_protocol'] = status['protocol']
        finally:
            connection.close()
    require(process(current['pid']) == current, 'Node changed during transport check')
    preserve(m)
    return {'node_after': current, 'transport': transport}


def observe_replacement(m, r, cycle):
    deadline = time.monotonic() + OBSERVE_SECONDS
    while True:
        owner(m)
        preserve(m)
        try:
            proof = verify_replacement(m, cycle)
            if cycle.get('watchdog_recovery') and not cycle.get('watchdog_after'):
                cycle['watchdog_after'] = proof['node_after']
                update(r)
            elif not cycle.get('no_node_transition') and not cycle.get('watchdog_recovery'):
                finished(r, cycle, 'node_term', proof)
            return proof
        except (OSError, RuntimeError, ValueError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Original Node transition unresolved; no repeated signal or force kill') from None
        time.sleep(1)


def begin_rollback(m, r):
    if 'rollback' in r['cycles']:
        return r['cycles']['rollback']
    apply = r['cycles']['apply']
    term = apply['intents'].get('node_term')
    watchdog_recovery = False
    if term and term.get('command_phase') == 'invocation_may_have_started':
        require(original_absent(apply['node_before']),
                'Original signal outcome unresolved; cannot start another transition')
        try:
            expected = relay_current(m)
            no_transition = False
        except (FileNotFoundError, ProcessLookupError):
            # Restoring bytes can allow the unchanged watchdog to recover an absent
            # Node; never launch one or signal an unobserved replacement ourselves.
            expected = None
            no_transition = False
            watchdog_recovery = True
    else:
        expected = apply['node_before']
        require(process(expected['pid']) == expected, 'Original Node changed before rollback')
        no_transition = True
    close = apply['intents'].get('close')
    opened = apply['intents'].get('open')
    if opened and opened.get('command_phase') == 'invocation_may_have_started':
        before_gate = control_receipt(m, opened)
        borrowed = None
    else:
        require(close is not None, 'No owned maintenance close to roll back')
        before_gate = control_receipt(m, close)
        borrowed = dict(close)
    cycle = {'mode': 'rollback', 'node_before': expected, 'gate_before': before_gate,
             'no_node_transition': no_transition, 'watchdog_recovery': watchdog_recovery, 'intents': {}}
    if borrowed:
        cycle['intents']['close'] = borrowed
        cycle['borrowed_close'] = True
    seal = apply['intents'].get('nft_seal')
    unseal = apply['intents'].get('nft_unseal')
    if seal and not unseal:
        cycle['intents']['nft_seal'] = dict(seal)
        cycle['borrowed_seal'] = True
    lock = apply['intents'].get('browser_lock')
    if lock:
        require(lock.get('command_phase') in ('intent_saved', 'precondition_failed',
                'invocation_may_have_started'), 'Unknown keeper intent phase')
        if lock['command_phase'] == 'invocation_may_have_started' and not (STAGE / 'lock-apply-released.json').exists():
            cycle['keeper_cycle'] = 'apply'
    r['cycles']['rollback'] = cycle
    r['state'] = 'rollback_prepared'
    update(r)
    return cycle


def run_cycle(m, r, cycle, issue):
    mode = 'after' if cycle['mode'] == 'apply' else 'before'
    bootstrap, guard = barriers(m, r, cycle)
    opened = cycle['intents'].get('open')
    if opened and opened.get('command_phase') == 'invocation_may_have_started':
        # A completed release permits ordinary work. Do not compare evolving
        # business rows to an old idle snapshot after intake has reopened.
        stage_check(m)
        owner(m)
        preserve(m)
        source(m, allowed_images(mode))
        verify_replacement(m, cycle, require_idle=False)
        bootstrap.snapshot(False)
        gate_action(m, r, cycle, 'open', False)
        r['state'] = cycle['mode'] + '_source_and_node_verified'
        update(r)
        return
    if not issue:
        # Observe only. No firewall, source, signal, gate or keeper-release action.
        stage_check(m)
        consistent(m, mode)
        guard.cache_check()
        proof = verify_replacement(m, cycle)
        term = cycle['intents'].get('node_term')
        if term and not term.get('fulfilled_utc'):
            finished(r, cycle, 'node_term', proof)
        if cycle['intents'].get('nft_unseal', {}).get('command_phase') == 'invocation_may_have_started':
            bootstrap.unseal(False)
        else:
            bootstrap.seal(False)
        control_receipt(m, cycle['intents']['close'])
        r['state'] = cycle['mode'] + '_source_and_node_verified_admission_held'
        update(r)
        return
    gate_action(m, r, cycle, 'close', True)
    # Resume a lost lock-release/unseal acknowledgement without reacquiring the
    # lock, sealing intake again or comparing legitimate reopened work to old rows.
    if cycle['intents'].get('nft_unseal') or (cycle.get('sealed_preservation_verified') and
            guard.keeper_path('release').exists()):
        require(cycle.get('sealed_preservation_verified') is True, 'Missing sealed preservation proof')
        source(m, allowed_images(mode))
        cycle['lock_release'] = guard.release(False)
        update(r)
        bootstrap.unseal(True)
        gate_action(m, r, cycle, 'open', True)
        r['state'] = cycle['mode'] + '_source_and_node_verified'
        update(r)
        return
    consistent(m, 'mixed')
    guard.prepare_cache()
    bootstrap.seal(True)
    if cycle.get('watchdog_recovery'):
        # Missing Node: retain the borrowed seal/lock and restore bytes only.
        require(cycle.get('keeper_cycle'), 'Watchdog recovery requires the original retained lock')
        guard.keeper_check()
        install_source(m, r, cycle)
    else:
        guard.acquire(True)
        term = cycle['intents'].get('node_term')
        if not term or term.get('command_phase') in ('intent_saved', 'precondition_failed'):
            if not cycle.get('no_node_transition'):
                quiet = bootstrap.quiet(cycle['node_before'], guard.keeper_check)
                readiness = guard.readiness(cycle['node_before'])
                cycle['bootstrap_drain'] = quiet
                update(r)
            install_source(m, r, cycle)
            if not cycle.get('no_node_transition'):
                def before():
                    consistent(m, mode)
                    control_receipt(m, cycle['intents']['close'])
                    guard.before_signal(cycle['node_before'], readiness)
                signal_once(r, cycle, cycle['node_before'], before)
        else:
            # The exact original signal may have happened; never reissue it.
            source(m, allowed_images(mode))
            guard.keeper_check()
    observe_replacement(m, r, cycle)
    consistent(m, mode)
    guard.cache_check()
    bootstrap.snapshot(True)
    cycle['sealed_preservation_verified'] = True
    cycle['sealed_preservation_utc'] = now()
    update(r)
    cycle['lock_release'] = guard.release(True)
    update(r)
    # Restore ordinary intake first, then reopen governed Core admissions.
    # The preserved originals are proved immediately before this boundary.
    bootstrap.unseal(True)
    gate_action(m, r, cycle, 'open', True)
    owner(m)
    preserve(m)
    source(m, allowed_images(mode))
    r['state'] = cycle['mode'] + '_source_and_node_verified'
    update(r)


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


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    require(len(args) == 1 and args[0] in ('preflight', 'apply', 'finalize', 'rollback'),
            'Use exactly one fixed activation mode')
    require(os.getresuid() == (0, 0, 0) and os.getresgid() == (0, 0, 0), 'Root operator required')
    os.umask(0o077)
    def deadline(_signum, _frame):
        raise TimeoutError('Bounded relay activation deadline exceeded; observe original intent')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(240)
    m, encoded = load_manifest()
    lock = acquire_release_lock(GLOBAL_LOCK)
    try:
        owner(m)
        backup(m)
        if args[0] == 'preflight':
            consistent(m, 'before')
            require(gate(m) == m['gate_before'] and process(m['relay_before']['pid']) == m['relay_before'],
                    'Preflight gate or original Node changed')
            from_types = operator_module(m, 'relay_bootstrap')
            listener_owned(m['relay_before']['pid'])
            require(from_types.source_timeout(m['relay_before']['pid']) == m['hermes_transport'],
                    'Actual Node runtime settings changed')
            other, owned = from_types.split_ruleset(from_types.read_ruleset())
            require(not owned and from_types.digest(other) == m['nft_before_sha256'],
                    'Initial complete firewall preimage changed')
            print(json.dumps({'state': 'preflight_verified', 'source_only_until_apply': True,
                              'provider_health_called': False, 'firewall_preimage_verified': True,
                              'historical_outcomes_completed': False, 'globally_sealed_drain': False}))
            return 0
        if args[0] == 'finalize':
            protected(STAGE / 'journal.json')
            r = json.loads(raw(STAGE / 'journal.json'))
            require(r['manifest_sha256'] == MANIFEST_SHA, 'Journal belongs to another activation')
        else:
            r = prepare(m, encoded)
        if args[0] == 'rollback':
            cycle = begin_rollback(m, r)
        else:
            require(args[0] != 'apply' or 'rollback' not in r['cycles'], 'Interrupted rollback must remain rollback')
            cycle = r['cycles'].get('rollback', r['cycles']['apply'])
        run_cycle(m, r, cycle, args[0] != 'finalize')
        print(json.dumps({'state': r['state'], 'manifest_sha256': MANIFEST_SHA,
                          'provider_readiness_proved_before_signal': bool(r.get('readiness_observations')),
                          'original_recovery_executed': False,
                          'historical_outcomes_completed': False}))
        return 0
    finally:
        os.close(lock)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        # Raw transport, private data, tokens and exception chains never leave this helper.
        print(json.dumps({'state': 'held', 'error_type': type(error).__name__,
                          'resume_original_only': True, 'force_kill_allowed': False}), file=sys.stderr)
        raise SystemExit(78) from None

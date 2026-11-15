#!/usr/bin/env python3
"""One reviewed relay detector leaf; preparation is not deployment.

The sole permitted live mutation is the pinned detector replacement followed by
one graceful SIGTERM to the exact idle Node process. Its existing watchdog owns
replacement. No unit/browser action, admission change, callback, recovery or send.
"""
import argparse
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import types

REPO = Path('/srv/abit-dev-linux/abit-ai-platform')
ROOT = Path('/srv/abit-dev-linux/abit-ai')
DATA = ROOT / 'data'
RELAY = Path('/srv/abit-dev-linux/relay-runtime')
LIVE = RELAY / 'service'
TARGET = LIVE / 'aibit_dot_feed.cjs'
CANDIDATE = REPO / 'deployment/windows/aibit-relay/aibit_dot_feed.cjs'
BEFORE = '1ce594960acc19485d54db9d2be21e037992f7d55adacceffd1c8aab7fdf350e'
AFTER = 'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53'
MAIN_LOOP = 'b3cd84de552c487e4751986929cd0e1cb70abb0af60d366459ac791df739627d'
BASELINE = REPO / '.git/relay-leaf-deploy-validation/frozen-settled-baseline.json'
BASELINE_SHA = '7eaa6db20c1c7e9ae9189fa64e3073652fcb0790e4d8a2dafbcc9b9560872531'
PARENT = Path('/var/lib/abit-ai-erp-rollouts/relay-feed-hardening')
INVOCATION = 'agov-parent-bc60a4e72de7c6771af1cf25f317e8c19f517d36b6696ff1'
RUN = '74685093-2067-561e-80b0-b51852f4e7ba'
START_SHA = '109d144c5554b36e2dd4de5e112a3134772bd99b5ec1bef4f44c06b2d9a7c1b3'
RESULT_SHA = 'b185e94680c390b9c8422bfee9cb2fafbf6ab1ab6dbf946258c648e67cba8aa6'
GATE_EPOCH = 20
QA_REFERENCE = 'TASK-2026-05624:5e944318-a000-4f44-8650-31ea6ea55847'
QA_RECEIPT = '123c6e8d2ae56d2b10431f23d0f21daea94d85454c3872fe981f56d728a2ba37'
DATABASES = (
    'abit-telegram.sqlite', 'aibit-chatgpt-relay.sqlite', 'webui.db',
    'agentic-governance.sqlite', 'codex-workspace-runs.sqlite',
    'erp-pending-review-qa.sqlite', 'erp-task-resolution.sqlite',
    'abitdot-primary.sqlite', 'erp-dot-issues.sqlite', 'abit-slack.sqlite',
    'maintenance-admission/gate.sqlite',
)
S = ROOT / 'deployment/agentic-governance-full-20261113'
HELPERS = {
    'hold': ('release-proposal/prepare_release_hold.py',
             'd63d5c4bce65432def99529c378384007dc346c7de3d823ab815d0e3bd7c3740'),
    'finish': ('release-proposal/finish_release_hold.py',
               '28a155b2cc5d5646bb04d8678a02bd4770325ba8a8b67002a406db21e5d1c7fb'),
    'history': ('core-profile-prompt-gateway-increment-v4-architecture71-20261115/history_guard.py',
                '1d1804e48c1a23fe6f86ee796e5756bd1c16a44376848e0a83788d79002b2052'),
}
LIMIT = 2_000_000
MIN_FREE_AFTER = 1_073_741_824


def need(value, code):
    if not value:
        raise RuntimeError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, pin=None):
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_INPUT_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= LIMIT,
             'BOUNDED_REGULAR_INPUT_REQUIRED')
        raw = bytearray()
        while len(raw) <= LIMIT:
            part = os.read(fd, min(65536, LIMIT + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        need(len(raw) == before.st_size and all(getattr(before, key) == getattr(after, key)
             for key in ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
                         'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'INPUT_CHANGED_DURING_READ')
        need(pin is None or sha(raw) == pin, 'PINNED_INPUT_CHANGED')
        return bytes(raw)
    finally:
        os.close(fd)


def xattrs(path):
    return {key: os.getxattr(path, key, follow_symlinks=False).hex()
            for key in sorted(os.listxattr(path, follow_symlinks=False))}


def file_proof(path):
    raw = read(path)
    info = path.lstat()
    return {'sha256': sha(raw), 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode), 'size': len(raw),
            'mtime_ns': info.st_mtime_ns, 'xattrs': xattrs(path)}


def module(name, relative, pin):
    path = S / relative
    value = types.ModuleType('reviewed_relay_leaf_' + name)
    value.__file__ = str(path)
    sys.modules[value.__name__] = value
    exec(compile(read(path, pin), str(path), 'exec'), value.__dict__)
    return value


def process(pid):
    p = Path('/proc') / str(pid)
    info = p.stat()
    before = p.joinpath('stat').read_text().split(') ', 1)[1].split()
    need(before[0] not in {'Z', 'X'}, 'PROCESS_NOT_LIVE')
    value = {'pid': pid, 'uid': info.st_uid, 'gid': info.st_gid,
             'parent': int(before[1]), 'start_ticks': before[19],
             'exe': os.readlink(p / 'exe'), 'argv_sha256': sha(p.joinpath('cmdline').read_bytes()),
             'cgroup': p.joinpath('cgroup').read_text().splitlines()}
    need(p.joinpath('stat').read_text().split(') ', 1)[1].split()[19] == before[19],
         'PROCESS_CHANGED_DURING_READ')
    return value


def service_state(name):
    fields = 'FragmentPath,DropInPaths,ActiveState,SubState,MainPID,NRestarts,InvocationID,ExecMainStartTimestampMonotonic'
    raw = subprocess.check_output(['/usr/bin/systemctl', 'show', name, '--property=' + fields],
                                  timeout=5, text=True)
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def code_check(baseline, installed=False):
    runtime = Path(baseline['runtime'])
    need((ROOT / 'current/runtime/open_webui').resolve() == runtime, 'CURRENT_RELEASE_CHANGED')
    current = set((runtime / 'utils').rglob('*.py'))
    current |= set((runtime / 'utils/agentic_contracts').rglob('*.json'))
    current.add(runtime / 'routers/agentic_governance.py')
    need(len(current) == 147 and set(map(str, current)) == set(baseline['core_files']),
         'CORE_COHORT_INVENTORY_CHANGED')
    current_relay = set(LIVE.glob('*.cjs')) | set(LIVE.glob('*.sh')) | set((LIVE / 'agentic-gateway').glob('*.cjs'))
    current_relay |= {ROOT / 'deployment/relay-watchdog.sh', ROOT / 'deployment/run-production.py',
                      ROOT / 'qa-service/qa-service.py'}
    need(set(map(str, current_relay)) == set(baseline['relay_code']), 'RELAY_CODE_INVENTORY_CHANGED')
    for group in ('core_files', 'relay_code', 'protected_configuration'):
        for name, proof in baseline[group].items():
            expected = dict(proof)
            if installed and name == str(TARGET):
                expected['sha256'] = AFTER
                expected['size'] = len(read(CANDIDATE, AFTER))
            need(file_proof(Path(name)) == expected, 'CODE_OR_CONFIGURATION_CHANGED')
    read(LIVE / 'aibit_relay.cjs', MAIN_LOOP)
    for relative, pin in HELPERS.values():
        read(S / relative, pin)


def processes_check(baseline, replacement=None):
    for name, expected in baseline['processes'].items():
        if name == 'node':
            continue
        need(process(expected['pid']) == expected, 'PRESERVED_PROCESS_CHANGED')
    for name, expected in baseline['units'].items():
        need(service_state(name) == expected, 'PRESERVED_UNIT_CHANGED')
    pid = int(read(RELAY / 'state/relay.pid').strip())
    current = process(pid)
    expected = replacement or baseline['processes']['node']
    need(current == expected, 'EXACT_NODE_IDENTITY_CHANGED')
    need(current['parent'] == baseline['processes']['watchdog']['pid'], 'NODE_WATCHDOG_PARENT_CHANGED')
    return current


def health_check(value):
    need(value.get('ok') is True and value.get('ready') is True
         and value.get('authenticated') is True and value.get('target_ready') is True
         and type(value.get('active_relay_turns')) is int and value['active_relay_turns'] == 0
         and value.get('browser_busy') is False and value.get('isolated_chat_tabs') == 16,
         'RELAY_NOT_POSITIVELY_IDLE')
    return {key: value[key] for key in ('ok', 'ready', 'authenticated', 'target_ready',
                                       'active_relay_turns', 'browser_busy', 'isolated_chat_tabs')}


def health():
    connection = http.client.HTTPConnection('127.0.0.1', 8810, timeout=12)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        raw = response.read(32769)
        need(response.status == 200 and len(raw) <= 32768, 'READ_ONLY_RELAY_HEALTH_UNAVAILABLE')
        return health_check(json.loads(raw))
    finally:
        connection.close()


def legacy_files():
    directory = RELAY / 'state/erp-assignment'
    need(directory.resolve() == directory and directory.is_dir(), 'ERP_RELAY_JOURNAL_MISSING')
    files = sorted(directory.glob('*.json'))
    need(len(files) <= 4096 and all(re.fullmatch('[a-f0-9]{64}[.]json', p.name) for p in files),
         'BOUNDED_ERP_RELAY_JOURNAL_REQUIRED')
    result = {}
    for path in files:
        raw = read(path)
        row = json.loads(raw)
        need(row.get('status') in {'completed', 'failed', 'cancelled'},
             'CURRENT_ERP_RELAY_WORK_PRESENT')
        result[str(path)] = sha(raw)
    dev = LIVE / 'development-relay-runs.json'
    if dev.exists() or dev.is_symlink():
        raw = read(dev)
        rows = json.loads(raw)
        need(isinstance(rows, dict) and len(rows) <= 4096
             and all(isinstance(v, dict) and v.get('state') in {'completed', 'failed', 'cancelled'}
                     for v in rows.values()), 'CURRENT_DEVELOPMENT_RELAY_WORK_PRESENT')
        result[str(dev)] = sha(raw)
    else:
        result[str(dev)] = None
    return result


def select_rows(db, sql, parameters=()):
    return [dict(row) for row in db.execute(sql, parameters)]


def original_row_pins(value):
    """Store only counts/hashes; provider scope and response bytes stay private."""
    groups = ('invocation', 'start', 'automation', 'leases', 'original_leases',
              'result', 'local_result', 'gateway_sessions', 'gateway_calls', 'dot', 'relay')
    pins = {}
    for key in groups:
        pins[key + '_count'] = len(value[key])
        pins[key + '_sha256'] = sha(canonical(value[key]))
    pins['gate_count'] = len(value['gate_full'])
    pins['gate_sha256'] = sha(canonical(value['gate_full']))
    return pins


def exact_receipt(raw, event, status):
    value = json.loads(raw or '{}')
    need(isinstance(value, dict) and value.get('durable') is True
         and value.get('duplicate') is False and value.get('event_id') == event
         and value.get('status') == status and value.get('run_id') == RUN
         and value.get('task_id') == 'TASK-2026-06200',
         'ORIGINAL_DURABLE_RECEIPT_CHANGED')


def validate_original(value, baseline):
    """Only the exact settled, published blocked original is permitted."""
    singles = ('invocation', 'start', 'automation', 'result', 'local_result',
               'gateway_sessions', 'dot')
    need(all(isinstance(value.get(k), list) and len(value[k]) == 1 for k in singles),
         'EXACT_SINGLE_ORIGINAL_REQUIRED')
    row, start, automation = (value[k][0] for k in ('invocation', 'start', 'automation'))
    need(value['gate'] == [{'state': 'draining', 'epoch': GATE_EPOCH}],
         'EXACT_BLOCKED_GATE_REQUIRED')
    need(row['invocation_id'] == INVOCATION and row['automation_run_id'] == RUN
         and row['erp_project'] == 'PROJ-0198' and row['erp_task'] == 'TASK-2026-06200'
         and row['state'] == 'execution_finished' and row['submitted_at'] == 1794732638
         and row['dispatch_attempts'] == 1, 'ORIGINAL_SETTLED_STATE_CHANGED')
    need(start['invocation_id'] == INVOCATION and start['run_id'] == RUN
         and start['event_sha256'] == START_SHA, 'ORIGINAL_START_JOURNAL_CHANGED')
    exact_receipt(start['receipt_json'],
                  'agov-start-ff4f815af2fd97f9f9423cd2b84c2772fc8f51ae5dbb3d10', 'Working')
    result = value['result'][0]
    need(result['invocation_id'] == INVOCATION and result['run_id'] == RUN
         and result['phase'] == 'published' and result['result_sha256'] == RESULT_SHA
         and json.loads(result['result_json']).get('outcome') == 'blocked',
         'ORIGINAL_PUBLISHED_RESULT_CHANGED')
    exact_receipt(result['receipt_json'],
                  'agov-result-98c7fd3b1f4391b5e0fe09cfb89cf03d6ac9f0295025703c', 'Blocked')
    local = value['local_result'][0]
    need(local['invocation_id'] == INVOCATION and local['run_id'] == RUN,
         'ORIGINAL_LOCAL_RESULT_CHANGED')
    need(automation['id'] == RUN and automation['status'] == 'success'
         and bool(automation['chat_id']) and automation['error'] is None,
         'ORIGINAL_AUTOMATION_CHANGED')
    need(value['leases'] == [] and value['active_invocations'] == []
         and value['running_automations'] == [], 'OTHER_RUNNING_OR_ADMITTED_WORK_PRESENT')
    old = value['original_leases']
    need(len(old) == 2
         and [(r['epoch'], r['pid'], r['process_start']) for r in old]
             == [(17, 505071, '68273241'), (19, 923235, '68517753')]
         and all(r['kind'] == 'governed' and r['reference'] == INVOCATION
                 and r['state'] == 'finished' for r in old),
         'ORIGINAL_SETTLED_ADMISSIONS_CHANGED')
    session = value['gateway_sessions'][0]
    calls = value['gateway_calls']
    need(session['invocation_id'] == INVOCATION and session['state'] == 'admitted'
         and len(calls) == 4 and all(c['state'] == 'completed' for c in calls)
         and sorted(c['operation'] for c in calls)
             == ['context.read', 'repository.deliver', 'repository.delivery_status',
                 'repository.delivery_status'], 'ORIGINAL_GATEWAY_WORK_CHANGED')
    dot = value['dot'][0]
    need(dot['original_run_id'] == RUN and dot['status'] == 'delegated'
         and value['relay'] == []
         and all(value[key] == 0 for key in
                 ('action_count', 'current_original_links', 'legacy_qa_work')),
         'ORIGINAL_PROVIDER_OR_LINKED_WORK_PRESENT')
    need(original_row_pins(value) == baseline['original_rows'],
         'ORIGINAL_IMMUTABLE_ROW_CHANGED')
    return {'invocation': INVOCATION, 'run': RUN, 'state': 'execution_finished',
            'dispatch_attempts': 1, 'submitted': True, 'outcome': 'blocked',
            'result_phase': 'published', 'result_sha256': RESULT_SHA,
            'start_receipt_present': True, 'result_receipt_present': True,
            'gateway_sessions': 1, 'completed_gateway_calls': 4,
            'gate': value['gate'][0], 'active_admissions': 0,
            'settled_original_admissions': 2, 'historical_provider_liveness_proved': False}


def collect_original_state(hold):
    """Read-only row capture; never print or persist provider/envelope row bodies."""
    with closing(hold.db('agentic-governance.sqlite')) as db:
        db.execute('BEGIN')
        value = {'invocation': select_rows(db, 'SELECT * FROM governance_invocation WHERE invocation_id=?', (INVOCATION,)),
                 'start': select_rows(db, 'SELECT * FROM governance_start WHERE invocation_id=?', (INVOCATION,)),
                 'active_invocations': [r[0] for r in db.execute(
                     "SELECT invocation_id FROM governance_invocation WHERE state IN ('running','uncertain','cancel_requested') ORDER BY invocation_id")]}
        for table, key in [('governance_gateway_session', 'gateway_sessions'),
                           ('governance_result', 'result'), ('governance_local_result', 'local_result')]:
            value[key] = select_rows(db, 'SELECT * FROM ' + table + ' WHERE invocation_id=?', (INVOCATION,))
        value['gateway_calls'] = select_rows(db,
            'SELECT c.* FROM governance_gateway_call c JOIN governance_gateway_session s '
            'ON s.session_id=c.session_id WHERE s.invocation_id=? ORDER BY c.call_id', (INVOCATION,))
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action'").fetchone()
        value['action_count'] = db.execute('SELECT count(*) FROM governance_action WHERE invocation_id=?',
            (INVOCATION,)).fetchone()[0] if exists else 0
    with closing(hold.db('webui.db')) as db:
        db.execute('BEGIN')
        value['automation'] = select_rows(db, 'SELECT * FROM automation_run WHERE id=?', (RUN,))
        value['running_automations'] = [r[0] for r in db.execute("SELECT id FROM automation_run WHERE status='running' ORDER BY id")]
        need(len(value['automation']) == 1, 'EXACT_SINGLE_ORIGINAL_REQUIRED')
        owner = db.execute('SELECT user_id FROM automation WHERE id=?',
                           (value['automation'][0]['automation_id'],)).fetchone()
        need(owner and owner[0], 'ORIGINAL_OWNER_BINDING_MISSING')
        key = hashlib.sha256(json.dumps([owner[0], value['automation'][0]['automation_id'], RUN],
                                       separators=(',', ':')).encode()).hexdigest()
        job = 'aibit_' + sha(('primary:' + key).encode())[:48]
    with closing(hold.db('abitdot-primary.sqlite')) as db:
        value['dot'] = select_rows(db, 'SELECT * FROM dot_primary_runs WHERE original_run_id=? OR id=?', (RUN, key))
    with closing(hold.db('aibit-chatgpt-relay.sqlite')) as db:
        value['relay'] = select_rows(db, 'SELECT * FROM jobs WHERE job_id=? OR response_message_id=?', (job, RUN))
    with closing(hold.db('maintenance-admission/gate.sqlite')) as db:
        db.execute('BEGIN')
        value['gate_full'] = select_rows(db, 'SELECT * FROM gate')
        value['gate'] = select_rows(db, 'SELECT state,epoch FROM gate')
        value['leases'] = select_rows(db, "SELECT * FROM lease WHERE state IN ('active','unresolved') ORDER BY id")
        value['original_leases'] = select_rows(db, 'SELECT * FROM lease WHERE reference=? ORDER BY created_ns,id', (INVOCATION,))
    retained = hold.retained_inventory()
    value['current_original_links'] = retained['current_original_links']
    with closing(hold.db('erp-pending-review-qa.sqlite')) as db:
        value['legacy_qa_work'] = db.execute("SELECT count(*) FROM review WHERE phase IN ('testing','publish')").fetchone()[0]
    return value


def original_state(hold, baseline):
    return validate_original(collect_original_state(hold), baseline)


def gate_history(hold):
    with closing(hold.db('maintenance-admission/gate.sqlite')) as db:
        db.execute('BEGIN')
        tables = {}
        for name in ('gate', 'lease', 'audit'):
            rows = select_rows(db, 'SELECT * FROM ' + name + ' LIMIT 100001')
            need(len(rows) <= 100000, 'GATE_HISTORY_BOUND')
            tables[name] = rows
        return tables


def preserve_gate_history(before, after, qa):
    """Retain all prior rows; allow only completed passive QA receipt pairs."""
    need(after['gate'] == before['gate'], 'GATE_CHANGED')
    additions = {}
    for table, primary in [('lease', 'id'), ('audit', 'seq')]:
        old = {row[primary]: row for row in before[table]}
        now = {row[primary]: row for row in after[table]}
        need(len(old) == len(before[table]) and len(now) == len(after[table])
             and all(now.get(key) == value for key, value in old.items()), 'HISTORICAL_GATE_ROW_CHANGED')
        additions[table] = [row for key, row in now.items() if key not in old]
    pairs = {}
    for row in additions['lease']:
        need(row['kind'] == 'qa' and row['state'] == 'finished' and row['epoch'] == GATE_EPOCH
             and row['reference'] == QA_REFERENCE and row['receipt_sha256'] == QA_RECEIPT
             and row['pid'] == qa['pid'] and row['process_start'] == qa['start_ticks']
             and row['parent_id'] is None, 'UNREVIEWED_ADMISSION_ADDITION')
        pairs[row['id']] = set()
    for row in additions['audit']:
        payload = json.loads(row['payload'])
        need(row['seq'] > max((x['seq'] for x in before['audit']), default=0)
             and payload.get('lease') in pairs and payload.get('kind') == 'qa'
             and payload.get('reference') == QA_REFERENCE, 'UNREVIEWED_AUDIT_ADDITION')
        if row['kind'] == 'admitted_original_recovery':
            need(payload.get('epoch') == GATE_EPOCH and payload.get('parent') is None
                 and payload.get('original_receipt') == QA_RECEIPT, 'QA_RECOVERY_AUDIT_CHANGED')
        elif row['kind'] == 'original_receipt_settled':
            need(payload.get('receipt_sha256') == QA_RECEIPT, 'QA_RECEIPT_AUDIT_CHANGED')
        else:
            raise RuntimeError('UNREVIEWED_AUDIT_KIND')
        need(row['kind'] not in pairs[payload['lease']], 'DUPLICATE_QA_AUDIT_KIND')
        pairs[payload['lease']].add(row['kind'])
    need(all(kinds == {'admitted_original_recovery', 'original_receipt_settled'}
             for kinds in pairs.values()), 'INCOMPLETE_PASSIVE_QA_AUDIT_PAIR')
    return {'prior_rows_unchanged': True, 'passive_qa_pairs_appended': len(pairs)}


def history_snapshot(hold, history):
    value = history.snapshot(hold)
    value['databases'].pop('maintenance-admission/gate.sqlite')
    return value['databases'], gate_history(hold)


def history_check(hold, history, before, baseline):
    tables, gate = history_snapshot(hold, history)
    need(tables == before[0], 'BUSINESS_OR_ORIGINAL_HISTORY_CHANGED')
    return preserve_gate_history(before[1], gate, baseline['processes']['qa'])


def idle(hold, baseline, installed=False, replacement=None):
    code_check(baseline, installed)
    node = processes_check(baseline, replacement)
    registry = file_proof(RELAY / 'state/chat-thread-targets.json')
    need(registry == baseline['target_registry']
         and len(json.loads(read(RELAY / 'state/chat-thread-targets.json'))) == 16,
         'TARGET_REGISTRY_CHANGED')
    return {'node': node, 'health': health(), 'original': original_state(hold, baseline),
            'legacy_files': legacy_files(), 'registry_sha256': registry['sha256']}


def capacity(hold, directory):
    sizes = {}
    for name in DATABASES:
        path = DATA / name
        need(path.resolve() == path and path.is_file(), 'BACKUP_DATABASE_PATH_CHANGED')
        with closing(hold.db(name)) as db:
            pages = db.execute('PRAGMA page_count').fetchone()[0]
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
        sizes[name] = max(path.stat().st_size, pages * page_size)
    required = 2 * sum(sizes.values()) + 134_217_728
    free = shutil.disk_usage(directory).free
    need(free >= required + MIN_FREE_AFTER, 'INSUFFICIENT_BACKUP_CAPACITY')
    return {'database_count': 11, 'estimated_database_bytes': sum(sizes.values()),
            'backup_reserve_bytes': required, 'free_bytes': free,
            'minimum_remaining_bytes': MIN_FREE_AFTER}


def backups(hold, history, dest):
    directory = dest / 'consistent-databases'
    directory.mkdir(mode=0o700)
    rows = []
    deadline = time.monotonic() + 60
    def progress(status, remaining, total):
        need(time.monotonic() <= deadline, 'BACKUP_DEADLINE_HELD_NO_RETRY')
    for name in DATABASES:
        capacity(hold, dest)
        target = directory / name.replace('/', '__')
        history.private_new(target, b'')
        with closing(hold.db(name)) as source, closing(sqlite3.connect(target)) as copy:
            source.backup(copy, pages=1024, progress=progress, sleep=0.05)
            need(copy.execute('PRAGMA quick_check').fetchone() == ('ok',), 'SQLITE_BACKUP_CHECK_FAILED')
            copy.commit()
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
            with os.fdopen(os.dup(fd), 'rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        finally:
            os.close(fd)
        rows.append({'database': name, 'bytes': target.stat().st_size, 'sha256': digest})
    return {'backup_count': len(rows), 'databases': rows, 'database_restores': 0}


def atomic_leaf(raw, baseline):
    expected = baseline['relay_code'][str(TARGET)]
    need(file_proof(TARGET) == expected and sha(raw) == AFTER, 'LEAF_CAS_PRECONDITION_CHANGED')
    old = TARGET.lstat()
    temporary = TARGET.parent / ('.agov-feed-' + os.urandom(16).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fchown(stream.fileno(), old.st_uid, old.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(old.st_mode))
            for key, value in expected['xattrs'].items():
                os.setxattr(stream.fileno(), key, bytes.fromhex(value))
            os.utime(stream.fileno(), ns=(old.st_atime_ns, old.st_mtime_ns))
            os.fsync(stream.fileno())
        new_expected = {**expected, 'sha256': AFTER, 'size': len(raw)}
        need(file_proof(temporary) == new_expected and file_proof(TARGET) == expected,
             'LEAF_METADATA_OR_CAS_CHANGED')
        os.replace(temporary, TARGET)
        parent = os.open(TARGET.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        need(file_proof(TARGET) == new_expected, 'LEAF_INSTALL_READBACK_CHANGED')
    finally:
        temporary.unlink(missing_ok=True)


def send_term_once(pidfd, record, sender=signal.pidfd_send_signal):
    record('node-term-intent.json', {'signal': 'SIGTERM', 'attempt': 1, 'force_kill_allowed': False})
    # No exception branch retries this operation. The intent remains authoritative.
    sender(pidfd, signal.SIGTERM, None, 0)
    record('node-term-call-returned.json', {'call_returned': True, 'attempt': 1})


def replacement_node(baseline, old):
    pid = int(read(RELAY / 'state/relay.pid').strip())
    need(pid != old['pid'], 'WATCHDOG_REPLACEMENT_NOT_YET_OBSERVED')
    value = process(pid)
    need({k: v for k, v in value.items() if k not in {'pid', 'start_ticks'}}
         == {k: v for k, v in old.items() if k not in {'pid', 'start_ticks'}}
         and int(value['start_ticks']) > int(old['start_ticks']),
         'WATCHDOG_REPLACEMENT_IDENTITY_CHANGED')
    return value


def main(args):
    need(args.execute != args.preflight_only and os.geteuid() == 0
         and os.uname().nodename == 'abit-dev-linux'
         and re.fullmatch('[a-f0-9]{64}', args.controller_sha256 or '')
         and sha(read(Path(__file__).resolve())) == args.controller_sha256,
         'EXACT_REVIEWED_ROOT_CONTROLLER_REQUIRED')
    need(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'PIDFD_REQUIRED_NO_PID_FALLBACK')
    baseline = json.loads(read(BASELINE, BASELINE_SHA))
    need(baseline['detector_before'] == BEFORE and baseline['detector_after'] == AFTER
         and baseline['expected_node'] == {'pid': 3593239, 'start_ticks': '67645222'},
         'PINNED_RELAY_SCOPE_CHANGED')
    raw = read(CANDIDATE, AFTER)
    subprocess.run(['/usr/local/bin/node', '--check', str(CANDIDATE)], check=True,
                   capture_output=True, timeout=8)
    os.umask(0o077)
    sys.dont_write_bytecode = True
    helpers = {name: module(name, relative, pin) for name, (relative, pin) in HELPERS.items()}
    hold, finish, history = (helpers[name] for name in ('hold', 'finish', 'history'))
    release = finish.private_lock('/run/lock/abit-ai-agentic-release.lock', 0)
    dispatch = pidfd = None
    dest = None
    created = False
    stage = 'preflight'
    term_attempts = 0
    try:
        with hold.as_service():
            dispatch = finish.private_lock(DATA / 'erp-dot-dispatch.lock', os.geteuid())
        first = idle(hold, baseline)
        preserved = history_snapshot(hold, history)
        capacity_before = capacity(hold, PARENT.parent)
        second = idle(hold, baseline)
        need(first == second, 'IDLE_PROOF_CHANGED')
        history_check(hold, history, preserved, baseline)
        summary = {'schema_version': 1, 'observed_at_ns': time.time_ns(),
                   'state': 'RELAY_LEAF_READ_ONLY_PREFLIGHT_PASSED',
                   'controller_sha256': args.controller_sha256, 'baseline_sha256': BASELINE_SHA,
                   'candidate_sha256': AFTER, 'before_sha256': BEFORE,
                   'current_node_pid': first['node']['pid'], 'current_node_birth': first['node']['start_ticks'],
                   'preserved_processes': {k: {'pid': v['pid'], 'birth': v['start_ticks']}
                                           for k, v in baseline['processes'].items() if k != 'node'},
                   'health': first['health'], 'original': first['original'], 'capacity': capacity_before,
                   'registry_count': 16, 'runtime_writes': 0, 'signals': 0,
                   'unit_actions': 0, 'ERP_actions': 0, 'model_requests': 0, 'gate_mutations': 0}
        if args.preflight_only:
            print(json.dumps(summary, sort_keys=True))
            return
        dest = args.operation_directory
        need(dest is not None and dest.is_absolute() and dest.parent == PARENT
             and re.fullmatch('relay-feed-[a-f0-9]{12}', dest.name)
             and not os.path.lexists(dest), 'NEW_FIXED_OPERATION_DIRECTORY_REQUIRED')
        parent = PARENT.lstat()
        need(PARENT.resolve() == PARENT and stat.S_ISDIR(parent.st_mode)
             and parent.st_uid == parent.st_gid == 0 and stat.S_IMODE(parent.st_mode) == 0o700,
             'PRESTAGED_ROOT_PRIVATE_PARENT_REQUIRED')
        dest.mkdir(mode=0o700)
        created = True
        record = lambda name, value: history.private_new(dest / name, canonical(value))
        record('preflight.json', summary)
        history.private_new(dest / 'frozen-baseline.json', read(BASELINE, BASELINE_SHA))
        record('intent.json', {'baseline_sha256': BASELINE_SHA, 'controller_sha256': args.controller_sha256,
                               'target': str(TARGET), 'before': BEFORE, 'after': AFTER,
                               'node': first['node'], 'gate': first['original']['gate'],
                               'no_admission_settlement': True, 'automatic_rollback': False,
                               'database_restore_allowed': False})
        history.private_new(dest / 'detector.before.cjs', read(TARGET, BEFORE))
        history.private_new(dest / 'detector.after.cjs', raw)
        record('detector-metadata.json', baseline['relay_code'][str(TARGET)])
        for index, (name, proof) in enumerate(baseline['protected_configuration'].items()):
            history.private_new(dest / ('protected-' + str(index) + '.private'), read(Path(name), proof['sha256']))
        record('protected-file-map.json', baseline['protected_configuration'])
        history.private_new(dest / 'target-registry.private.json',
                            read(RELAY / 'state/chat-thread-targets.json', first['registry_sha256']))
        for index, (name, pin) in enumerate(first['legacy_files'].items()):
            if pin is not None:
                history.private_new(dest / ('relay-journal-' + str(index) + '.private.json'),
                                    read(Path(name), pin))
        record('relay-journal-map.private.json', first['legacy_files'])
        stage = 'sqlite_backups'
        record('consistent-database-backups.json', backups(hold, history, dest))
        need(idle(hold, baseline) == first, 'IDLE_CHANGED_DURING_BACKUPS')
        history_check(hold, history, preserved, baseline)
        # Pin the kernel process handle before changing source. No PID-only signal fallback.
        pidfd = os.pidfd_open(first['node']['pid'], 0)
        need(process(first['node']['pid']) == first['node'], 'NODE_CHANGED_BEFORE_INSTALL')
        stage = 'single_leaf_replace'
        atomic_leaf(raw, baseline)
        record('installed.json', {'target': str(TARGET), 'sha256': AFTER, 'metadata_preserved': True,
                                  'Main_actions': 0, 'QA_actions': 0, 'Chrome_actions': 0})
        need(idle(hold, baseline, installed=True) == first, 'IDLE_CHANGED_BEFORE_TERM')
        history_check(hold, history, preserved, baseline)
        need(process(first['node']['pid']) == first['node'], 'NODE_CHANGED_BEFORE_TERM')
        stage = 'one_graceful_node_term'
        term_attempts = 1
        send_term_once(pidfd, record)
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
        deadline = time.monotonic() + 55
        new_node = None
        stage = 'watchdog_replacement_readback'
        while time.monotonic() < deadline:
            # Preserve every other process and exact blocked gate during the wait.
            for name, expected in baseline['processes'].items():
                if name != 'node':
                    need(process(expected['pid']) == expected, 'PRESERVED_PROCESS_CHANGED_DURING_WAIT')
            original_state(hold, baseline)
            if not poller.poll(0):
                time.sleep(0.5)
                continue
            try:
                candidate = replacement_node(baseline, first['node'])
            except (FileNotFoundError, ProcessLookupError):
                time.sleep(0.5)
                continue
            except RuntimeError as error:
                if str(error) != 'WATCHDOG_REPLACEMENT_NOT_YET_OBSERVED':
                    raise
                time.sleep(0.5)
                continue
            need(new_node is None or new_node == candidate, 'SECOND_NODE_REPLACEMENT_DENIED')
            new_node = candidate
            try:
                after = idle(hold, baseline, installed=True, replacement=new_node)
            except (ConnectionError, TimeoutError, OSError, http.client.HTTPException):
                time.sleep(0.5)
                continue
            need(after['legacy_files'] == first['legacy_files']
                 and after['registry_sha256'] == first['registry_sha256'], 'RELAY_HISTORY_CHANGED')
            preserved_result = history_check(hold, history, preserved, baseline)
            record('completed-receipt.json', {'state': 'RELAY_DETECTOR_INSTALLED_GATE_UNCHANGED',
                   'old_node': first['node'], 'new_node': new_node, 'health': after['health'],
                   'gate': after['original']['gate'], 'settled_original_preserved': after['original'],
                   'installed_sha256': AFTER, 'baseline_sha256': BASELINE_SHA,
                   'registry_count': 16, 'history': preserved_result, 'Node_TERM_calls': 1,
                   'force_kills': 0, 'unit_actions': 0, 'Chrome_actions': 0, 'watchdog_actions': 0,
                   'Main_actions': 0, 'QA_actions': 0, 'database_restores': 0,
                   'ERP_actions': 0, 'model_requests': 0, 'gate_mutations': 0})
            print(json.dumps({'state': 'RELAY_DETECTOR_INSTALLED_GATE_UNCHANGED',
                              'old_node_pid': first['node']['pid'], 'new_node_pid': new_node['pid'],
                              'candidate_sha256': AFTER, 'Node_TERM_calls': 1,
                              'unit_actions': 0, 'model_requests': 0, 'gate_mutations': 0}))
            return
        raise RuntimeError('WATCHDOG_REPLACEMENT_UNCONFIRMED_NO_RETRY_OR_KILL')
    except BaseException as error:
        if created:
            try:
                history.private_new(dest / 'held.json', canonical({
                    'state': 'HELD_OBSERVE_ORIGINAL_NO_REPLAY', 'stage': stage,
                    'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                    'Node_TERM_calls': term_attempts, 'force_kills': 0,
                    'automatic_rollback': False, 'database_restores': 0, 'gate_mutations': 0}))
            except BaseException:
                pass
        raise
    finally:
        if pidfd is not None:
            os.close(pidfd)
        if dispatch is not None:
            os.close(dispatch)
        os.close(release)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller-sha256', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--operation-directory', type=Path)
    try:
        main(parser.parse_args())
    except Exception as error:
        print(json.dumps({'state': 'HELD_NO_AUTOMATIC_RETRY',
                          'error_code': str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                          'runtime_liveness_not_assumed': True}))
        raise SystemExit(65)

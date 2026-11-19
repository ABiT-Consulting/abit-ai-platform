"""Recover an original unsubmitted start without replaying uncertain work.

Ordinary recovery only observes the immutable original positive receipt. A
protected Root-authorized, definitively denied readiness case may append one
metadata-only amendment and post it once, preserving the original event/run.
An existing amendment or unknown acknowledgement is always GET-only. Model
submission still requires the normal shared ownership, gate and before_submit.
"""

from __future__ import annotations
from open_webui.utils.agentic_bridge import same_business_context
import hashlib
import json
import secrets
import time
from pathlib import Path

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest

RECEIPT_RPC = '/api/method/abit_ai_assistant.agentic_control.get_result_receipt'
MANAGED = {'source_modified', 'packet_id', 'content_sha256', 'implementation_user', 'implementation_run_id'}


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_start(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,packet_hash TEXT NOT NULL,
        lease_fence INTEGER NOT NULL,lease_token_sha256 TEXT NOT NULL,event_json TEXT NOT NULL,
        event_sha256 TEXT NOT NULL,receipt_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)''')


def _token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _unsubmitted(db, row, states):
    if (
        not row
        or row['state'] not in states
        or row['submitted_at'] is not None
        or not row['automation_run_id']
        or not row['lease_token']
    ):
        return False
    if not ledger._owned(db, row['invocation_id'], row['automation_run_id'], row['lease_token'], states):
        return False
    # A returned result or host intent is incompatible with a never-submitted
    # start. Preserve it for original-receipt recovery, never infer a safe retry.
    for table in ('governance_local_result', 'governance_result', 'governance_action'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            if db.execute(
                'SELECT 1 FROM ' + table + ' WHERE invocation_id=? LIMIT 1', (row['invocation_id'],)
            ).fetchone():
                return False
    return True


def _identity(row, event):
    packet = json.loads(row['work_packet'])
    expected = {
        'schema_version': 1,
        'event_id': 'agov-start-' + digest(row['invocation_id'])[:48],
        'type': 'pm.review.started' if packet.get('qa_review') else 'run.started',
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'run_id': row['automation_run_id'],
        'invocation_id': row['invocation_id'],
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'work_packet_digest': packet_digest(packet),
        'expected_modified': packet['source_modified'],
        'expected_project_revision': packet['project_revision'],
        'artifacts': [],
    }
    if any(event.get(key) != value for key, value in expected.items()):
        raise ValueError('Start event does not match its original immutable run and packet')
    if packet.get('qa_review') and event.get('work_packet') != packet:
        raise ValueError('PM review start must retain its exact immutable coordination packet')
    if len(canonical(event).encode()) > (220000 if packet.get('qa_review') else 24000):
        raise ValueError('Original start event exceeds the journal bound')


def record_start_intent(row, event, db_path=None):
    """Write before the first ERP publish; identical writes are idempotent."""
    with ledger._transaction(db_path) as db:
        _schema(db)
        current = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        if not _unsubmitted(db, current, {'running'}):
            raise ValueError('Start journal requires the original unsubmitted active run')
        current = dict(current)
        _identity(current, event)
        if any(
            current.get(key) != row.get(key)
            for key in ('automation_run_id', 'lease_token', 'lease_fence', 'packet_hash')
        ):
            raise ValueError('Original start identity changed before journaling')
        value = (
            current['automation_run_id'],
            current['packet_hash'],
            current['lease_fence'],
            _token(current['lease_token']),
            canonical(event),
            digest(event),
        )
        prior = db.execute(
            'SELECT run_id,packet_hash,lease_fence,lease_token_sha256,event_json,event_sha256 FROM governance_start WHERE invocation_id=?',
            (row['invocation_id'],),
        ).fetchone()
        if prior:
            if tuple(prior) != value:
                raise ValueError('Original start intent is immutable')
            return False
        now = int(time.time())
        db.execute(
            'INSERT INTO governance_start VALUES(?,?,?,?,?,?,?,NULL,?,?)', (row['invocation_id'], *value, now, now)
        )
        ledger._event(
            db,
            current['erp_project'],
            current['invocation_id'],
            'original_start_journaled',
            'executor',
            {'event_sha256': value[-1]},
        )
        return True


def _matching(row, entry):
    if not entry:
        return False
    return (
        entry['run_id'] == row['automation_run_id']
        and entry['packet_hash'] == row['packet_hash']
        and entry['lease_fence'] == row['lease_fence']
        and row['lease_token']
        and secrets.compare_digest(entry['lease_token_sha256'], _token(row['lease_token']))
    )


def _receipt(entry, value, *, row=None, db_path=None):
    if not isinstance(value, dict):
        raise ValueError('Original ERP start receipt is missing')
    payload = value.get('receipt', value)
    if not isinstance(payload, dict):
        raise ValueError('Original ERP start receipt is invalid')
    result = {key: item for key, item in payload.items() if key not in {'exists', 'content_sha256'}}
    event = json.loads(entry['event_json'])
    sha = value.get('content_sha256', payload.get('content_sha256'))
    if sha is not None and sha != entry['event_sha256']:
        raise ValueError('ERP start receipt content hash differs from the saved original event')
    expected_status = 'QA Reviewing' if event['role'] == 'qa' else 'Working'
    if (
        row is not None
        and event.get('type') == 'run.started'
        and event['role'] == 'project_manager'
        and result.get('status') == 'Blocked'
    ):
        from open_webui.utils.agentic_blocker_review import origin

        # Older ERP start receipts remain valid while the native update rolls out.
        if origin(dict(row), db_path):
            expected_status = 'Blocked'
    if event.get('type') == 'pm.review.started':
        expected_status = 'QA APPROVED'
        from open_webui.utils.agentic_pm_review import artifact_target

        artifact_target(
            {
                'target_role': event['role'],
                'work_packet': canonical(event.get('work_packet') or {}),
                'erp_project': event['project_id'],
                'invocation_id': event['invocation_id'],
            },
            result,
        )
        if result.get('pm_review_state') != 'started':
            raise ValueError('ERP did not confirm the original PM coordination start journal')
    if (
        any(result.get(key) != event[key] for key in ('event_id', 'task_id', 'run_id'))
        or result.get('durable') is not True
        or result.get('status') != expected_status
        or not isinstance(result.get('modified'), str)
        or not result['modified']
    ):
        raise ValueError('ERP start receipt does not confirm the original active run')
    return result


def record_start_receipt(invocation_id, receipt, db_path=None):
    with ledger._transaction(db_path) as db:
        _schema(db)
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not row or not _matching(row, entry):
            raise ValueError('Original start journal identity changed')
        value = canonical(_receipt(entry, receipt, row=row, db_path=db_path))
        if entry['receipt_json'] and entry['receipt_json'] != value:
            raise ValueError('Original ERP start receipt is immutable')
        db.execute(
            'UPDATE governance_start SET receipt_json=?,updated_at=? WHERE invocation_id=?',
            (value, int(time.time()), invocation_id),
        )
        effective,readiness=_readiness_entry(db,dict(row),entry)
        if readiness:
            proof={'event_sha256':effective['event_sha256'],'receipt_sha256':digest(json.loads(value)),
                   'authorization_sha256':readiness['authorization_sha256']}
            prior=db.execute("SELECT payload FROM governance_event WHERE invocation_id=? AND kind='original_start_readiness_receipt'",(invocation_id,)).fetchall()
            if prior and (len(prior)!=1 or json.loads(prior[0]['payload'])!=proof):
                raise ValueError('Original amended start receipt is immutable')
            if not prior:ledger._event(db,row['erp_project'],invocation_id,'original_start_readiness_receipt','server-readiness-policy',proof)
        return json.loads(value)


def original_start_receipt(row, db_path=None):
    """Read the exact confirmed start for an existing submitted original reply."""
    with ledger.connect(db_path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_start'").fetchone():
            raise ValueError('Original start receipt journal is missing')
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
        if not _matching(row, entry) or not entry['receipt_json']:
            raise ValueError('Original start receipt identity is unavailable')
        _identity(row, json.loads(entry['event_json']))
        return _receipt(entry, json.loads(entry['receipt_json']), row=row, db_path=db_path)


async def get_confirmed_start(row, bridge, project_gate, config, db_path=None):
    """Observe positive original receipts; one authorized denied-start amendment may POST."""
    with ledger.connect(db_path) as db:
        _schema(db)
        current = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
        if not _unsubmitted(db, current, {'uncertain'}) or not _matching(current, entry):
            return None
        current = dict(current)
        entry = dict(entry)
        entry, readiness = _readiness_entry(db, current, entry)
        if any(
            current.get(key) != row.get(key)
            for key in ('automation_run_id', 'lease_token', 'lease_fence', 'packet_hash')
        ):
            return None
    if entry['receipt_json']:
        receipt = _receipt(entry, json.loads(entry['receipt_json']), row=current, db_path=db_path)
    else:
        event = json.loads(entry['event_json'])
        response = await bridge.erp.request(
            'GET',
            RECEIPT_RPC,
            params={key: event[key] for key in ('event_id', 'project_id', 'task_id')},
            headers={'X-ABIT-Agentic-Bridge': bridge.secret},
        )
        value = response.get('message') if isinstance(response, dict) else None
        if not isinstance(value, dict) or value.get('exists') is not True:
            if readiness:return None
            return await _recover_readiness_start(current, entry, bridge, project_gate, config, db_path)
        receipt = _receipt(entry, value, row=current, db_path=db_path)
    _, gate = await project_gate(current['erp_project'], config)
    packet = json.loads(current['work_packet'])
    fresh = await bridge.packet(
        current['erp_project'], current['erp_task'], current['target_role'], current['agent_id']
    )
    if not same_business_context(packet, fresh, MANAGED):
        raise ValueError('WorkPacket changed after the original ERP start')
    if fresh.get('source_modified') != receipt['modified']:
        from open_webui.utils.agentic_execution_observations import acknowledged_modified
        try:
            acknowledged_modified(current, receipt['modified'], fresh.get('source_modified'), db_path, fresh_packet=fresh)
        except ValueError:
            raise ValueError('ERP Task changed after the original start receipt') from None
    with ledger._transaction(db_path) as db:
        latest = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        if not _unsubmitted(db, latest, {'uncertain'}) or not _matching(latest, entry):
            return None
        ledger._gate(db, current['erp_project'], gate)
        ledger._task_gate(db, current['erp_project'], current['erp_task'])
    return record_start_receipt(row['invocation_id'], receipt, db_path)


def reclaim_same(invocation_id, receipt, *, gate, db_path=None):
    """CAS only the positively confirmed original run back to its active lease."""
    with ledger._transaction(db_path) as db:
        _schema(db)
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        entry = db.execute('SELECT * FROM governance_start WHERE invocation_id=?', (invocation_id,)).fetchone()
        if not _unsubmitted(db, row, {'uncertain'}) or not _matching(row, entry) or not entry['receipt_json']:
            return None
        if canonical(_receipt(entry, receipt, row=row, db_path=db_path)) != entry['receipt_json']:
            raise ValueError('Reclaim requires the saved exact original start receipt')
        ledger._gate(db, row['erp_project'], gate)
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        # Reuse only this server-confirmed, never-submitted original after a
        # controlled process restart; the prior admission remains in its audit.
        from open_webui.utils import agentic_maintenance as maintenance

        maintenance.recover_governed_start(
            invocation_id, db,
            db_path=maintenance.path(Path(db_path).parent if db_path else ledger.DATA_DIR),
        )
        now = int(time.time())
        db.execute(
            "UPDATE governance_invocation SET state='running',lease_expires_at=?,reason=NULL,updated_at=? WHERE invocation_id=? AND state='uncertain' AND submitted_at IS NULL",
            (now + ledger.LEASE_SECONDS, now, invocation_id),
        )
        ledger._event(
            db,
            row['erp_project'],
            invocation_id,
            'original_unsubmitted_start_reclaimed',
            'executor',
            {'run_id': row['automation_run_id'], 'fence': row['lease_fence']},
        )
        return dict(
            db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        )


READINESS_KIND = 'original_start_readiness'
READINESS_POLICY = 'denied-start-readiness.v1'
READINESS_ROOT = Path('/var/lib/abit-ai-agentic-gateway/start-readiness')
NATIVE_SOURCE = '6107188ff9189035e438b9226f24acdd4f8bff69c1f000852631c61c6ad27127'
NATIVE_POLICY = '59bc3e0a979a23ec81acc53c829487a72f5ea7291580c6c700a71e32db372e12'
DENIED_REASON = 'ERPResponseError: ERP HTTP 409 GovernanceValidationError: Task is not ready for a new execution'


def _read_readiness_proof(row, entry):
    """Only a protected, explicit server-operator authorization; never tool input."""
    import os, stat
    from open_webui.utils.agentic_tool_gateway import _open_dir
    if not __import__('re').fullmatch(r'[A-Za-z0-9_.:@+-]{8,160}', row['invocation_id']):
        raise ValueError('Invalid original readiness identity')
    try:
        directory = _open_dir(READINESS_ROOT)
    except FileNotFoundError:
        return None
    try:
        parent = os.fstat(directory)
        if parent.st_uid != 0 or parent.st_gid != 988 or stat.S_IMODE(parent.st_mode) != 0o750:
            raise ValueError('Readiness authorization directory is not server-owned')
        try:
            fd = os.open(row['invocation_id']+'.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                    or info.st_gid != 988 or stat.S_IMODE(info.st_mode) != 0o640):
                raise ValueError('Readiness authorization is not a protected Root input')
            raw = stream.read(65537)
    finally:
        os.close(directory)
    if len(raw) > 65536:
        raise ValueError('Readiness authorization exceeds its bound')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _readiness_packet(original, fresh):
    """Rebuild and verify only actual status-save metadata; preserve every input."""
    from copy import deepcopy
    if not same_business_context(original, fresh, MANAGED):
        raise ValueError('Business context changed before readiness recovery')
    if (fresh.get('source_modified') == original.get('source_modified')
            or fresh.get('content_sha256') != packet_digest(fresh)
            or fresh.get('packet_id') != 'erp-packet-'+digest({k:v for k,v in fresh.items() if k not in {'packet_id','content_sha256'}})[:48]):
        raise ValueError('Fresh readiness packet metadata is not native-derived')
    old, new = original.get('project_backlog'), fresh.get('project_backlog')
    if not isinstance(old,dict) or not isinstance(new,dict) or old.get('context_sha256') != new.get('context_sha256'):
        raise ValueError('Readiness cannot change backlog business/ownership context')
    normalized = deepcopy(new)
    for backlog in (old,new):
        if (backlog.get('complete') is not True or backlog.get('snapshot_sha256')
                != digest({'project_id':original['project_id'],'tasks':backlog.get('tasks')})):
            raise ValueError('Readiness requires the complete actual backlog snapshot')
    found = []
    prior = [item for item in old['tasks'] if item['task_id']==original['task_id']]
    for item in normalized['tasks']:
        if item['task_id']==original['task_id']:
            found.append(item)
            if len(prior)!=1 or prior[0].get('status')!='Blocked' or item.get('status')!='Open' or item.get('source_modified')!=fresh['source_modified']:
                raise ValueError('Readiness is only the authorized Blocked-to-Open save')
            item['status']=prior[0]['status'];item['source_modified']=prior[0]['source_modified']
    normalized['snapshot_sha256']=old['snapshot_sha256']
    if len(found)!=1 or normalized!=old:
        raise ValueError('Another backlog entry changed during readiness')
    view={k:v for k,v in fresh.items() if k not in {'packet_id','content_sha256','source_modified','project_backlog'}}
    before={k:v for k,v in original.items() if k not in {'packet_id','content_sha256','source_modified','project_backlog'}}
    if view!=before:
        raise ValueError('Readiness metadata cannot hide another packet field change')
    return {'source_modified':fresh['source_modified'],'packet_id':fresh['packet_id'],
            'content_sha256':fresh['content_sha256'],'backlog_snapshot_sha256':new['snapshot_sha256']}


def _frame_packet(original, frame):
    from copy import deepcopy
    fresh=deepcopy(original)
    for key in ('source_modified','packet_id','content_sha256'):fresh[key]=frame[key]
    fresh['project_backlog']['snapshot_sha256']=frame['backlog_snapshot_sha256']
    for item in fresh['project_backlog']['tasks']:
        if item['task_id']==original['task_id']:
            item['status']='Open';item['source_modified']=frame['source_modified']
    if _readiness_packet(original,fresh)!=frame:
        raise ValueError('Readiness packet projection is not exact')
    return fresh


def _readiness_entry(db, row, entry):
    audits=db.execute('SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
                     (row['invocation_id'],READINESS_KIND)).fetchall()
    if not audits:return dict(entry),None
    if len(audits)!=1:raise ValueError('Original readiness amendment is ambiguous')
    value=json.loads(audits[0]['payload']);original=json.loads(row['work_packet']);event=json.loads(entry['event_json'])
    _identity(row,event)
    if digest(event)!=entry['event_sha256']:raise ValueError('Original start event bytes changed')
    if (value.get('policy')!=READINESS_POLICY or value.get('invocation_id')!=row['invocation_id']
            or value.get('run_id')!=row['automation_run_id'] or value.get('original_event_sha256')!=entry['event_sha256']
            or value.get('original_packet_hash')!=row['packet_hash'] or value.get('lease_fence')!=row['lease_fence']
            or value.get('lease_token_sha256')!=_token(row['lease_token']) or not _matching(row,entry)):
        raise ValueError('Original readiness binding changed')
    native=_frame_packet(original,value['native_frame'])
    amended={**event,'expected_modified':native['source_modified'],'work_packet_digest':packet_digest(native)}
    if amended!=value.get('event') or digest(amended)!=value.get('event_sha256'):
        raise ValueError('Effective readiness event changed')
    return {**dict(entry),'event_json':canonical(amended),'event_sha256':digest(amended)},value


def native_packet_digest(row, packet, *, db=None, db_path=None):
    """Native transport identity; Core/model/gateway packet remains unchanged."""
    if db is None:
        with ledger.connect(db_path) as current:
            return native_packet_digest(row,packet,db=current,db_path=db_path)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_start'").fetchone():return packet_digest(packet)
    entry=db.execute('SELECT * FROM governance_start WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
    if not entry:return packet_digest(packet)
    effective,audit=_readiness_entry(db,row,entry)
    if not audit:return packet_digest(packet)
    if json.loads(row['work_packet'])!=packet or not entry['receipt_json']:
        raise ValueError('Native readiness frame has no original positive start receipt')
    receipt=_receipt(effective,json.loads(entry['receipt_json']))
    proofs=db.execute("SELECT payload FROM governance_event WHERE invocation_id=? AND kind='original_start_readiness_receipt'",(row['invocation_id'],)).fetchall()
    expected={'event_sha256':effective['event_sha256'],'receipt_sha256':digest(receipt),'authorization_sha256':audit['authorization_sha256']}
    if len(proofs)!=1 or json.loads(proofs[0]['payload'])!=expected:
        raise ValueError('Native readiness frame lacks its atomic positive receipt audit')
    return audit['native_frame']['content_sha256']


def project_native_result(row, packet, result, event, *, db):
    """Only two server-proven transport pointers; no outcome/evidence rewriting."""
    from copy import deepcopy
    native=native_packet_digest(row,packet,db=db);original=packet_digest(packet)
    if native==original:return event,None
    local=db.execute('SELECT run_id,result_json FROM governance_local_result WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
    if not local or local['run_id']!=row['automation_run_id'] or json.loads(local['result_json'])!=result:
        raise ValueError('Readiness callback requires its unchanged validated local result')
    if (result.get('invocation_id')!=row['invocation_id'] or result.get('actor')!=packet['binding']
            or result.get('work_packet_digest')!=original or event.get('result_contract')!=result
            or event.get('work_packet_digest')!=original or event.get('run_id')!=row['automation_run_id']
            or any(event.get(k)!=row.get(v) for k,v in [('invocation_id','invocation_id'),('project_id','erp_project'),('task_id','erp_task'),('task_revision','task_revision'),('role','target_role'),('agent_id','agent_id')])):
        raise ValueError('Readiness transport projection cannot change result identity/content')
    projected=deepcopy(event);projected['work_packet_digest']=native;projected['result_contract']['work_packet_digest']=native
    audit={'policy':READINESS_POLICY,'invocation_id':row['invocation_id'],'run_id':row['automation_run_id'],
           'original_packet_hash':row['packet_hash'],'original_native_digest':original,'effective_native_digest':native,
           'result_sha256':digest(result),'original_event_sha256':digest(event),'native_event_sha256':digest(projected),
           'pointers':['/work_packet_digest','/result_contract/work_packet_digest']}
    return projected,audit


def _provider_absent(row, db_path):
    import sqlite3
    root=Path(db_path).parent if db_path else ledger.path().parent
    p=root/'abitdot-primary.sqlite'
    if not p.is_file():
        raise ValueError('Original provider ledger is unavailable; no-model absence is unproven')
    with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as provider:
        provider.execute('PRAGMA query_only=ON')
        if not provider.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dot_primary_runs'").fetchone():
            raise ValueError('Original provider ledger schema is unavailable')
        return provider.execute('SELECT 1 FROM dot_primary_runs WHERE original_run_id=? LIMIT 1',(row['automation_run_id'],)).fetchone() is None


def _no_readiness_work(db,row):
    if not _unsubmitted(db,row,{'uncertain'}) or row['dispatch_attempts']!=1 or row['reason']!=DENIED_REASON:
        raise ValueError('Readiness requires the exact definitively denied never-submitted start')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_gateway_session'").fetchone():
        if db.execute('SELECT 1 FROM governance_gateway_session WHERE invocation_id=?',(row['invocation_id'],)).fetchone():
            raise ValueError('A gateway session prevents readiness amendment')


def _readiness_admission_open(row, db_path):
    from open_webui.utils import agentic_maintenance as maintenance
    database=maintenance.path(Path(db_path).parent if db_path else ledger.path().parent)
    try:
        with maintenance._db(database,write=False) as admission:
            gate=admission.execute('SELECT state FROM gate WHERE id=1').fetchone()
            if not gate or gate['state']!='open':return False
            maintenance._scope_admission(database,'governed',row['erp_project'],False)
            return True
    except maintenance.MaintenanceDenied:
        return False


def _readiness_control(db,row,denial,native):
    controls=db.execute("SELECT * FROM governance_native_control WHERE project=? AND task IN ('',?) AND phase='acknowledged' AND action IN ('retry','resume') ORDER BY sequence DESC",(row['erp_project'],row['erp_task'])).fetchall()
    for control in controls:
        if control['task']!=denial.get('control_task') or control['sequence']<=denial.get('control_sequence',-1):continue
        for audit in db.execute("SELECT actor,payload,created_at FROM governance_event WHERE erp_project=? AND kind='native_control_applied' ORDER BY seq DESC LIMIT 100",(row['erp_project'],)):
            value=json.loads(audit['payload'])
            if (value.get('command_id')==control['command_id'] and value.get('control_sequence')==control['sequence']
                    and value.get('action')==control['action'] and audit['created_at']>native['captured_at'] and audit['actor']):
                return {'command_id':control['command_id'],'sequence':control['sequence'],'actor':audit['actor']}
    return None


async def _recover_readiness_start(row, entry, bridge, project_gate, config, db_path):
    _identity(row,json.loads(entry['event_json']))
    if digest(json.loads(entry['event_json']))!=entry['event_sha256']:raise ValueError('Original denied start bytes changed')
    authorization=_read_readiness_proof(row,entry)
    if authorization is None:return None
    proof,proof_sha=authorization;denial=proof.get('denial') or {};native=proof.get('native') or {}
    expected={'schema_version':1,'purpose':READINESS_POLICY,'invocation_id':row['invocation_id'],
              'run_id':row['automation_run_id'],'event_id':json.loads(entry['event_json'])['event_id'],
              'event_sha256':entry['event_sha256']}
    if (any(proof.get(k)!=v for k,v in expected.items()) or denial.get('status')!=409
            or denial.get('error_type')!='GovernanceValidationError' or denial.get('message')!='Task is not ready for a new execution'
            or denial.get('reason_sha256')!=digest(DENIED_REASON)
            or native.get('complete') is not True or native.get('source_sha256')!=NATIVE_SOURCE
            or native.get('policy_sha256')!=NATIVE_POLICY
            or not __import__('re').fullmatch('[a-f0-9]{64}',str((proof.get('core') or {}).get('no_model_proof_sha256','')))
            or native.get('run_id')!=row['automation_run_id'] or native.get('event_id')!=expected['event_id']
            or native.get('event_sha256')!=entry['event_sha256'] or native.get('active_run_is_target') is not False
            or type(native.get('all_run_records_count')) is not int or native['all_run_records_count']!=0
            or set(native.get('counts',{}))!={'run_started','run_context','result','inbox','result_receipt'}
            or any(type(n) is not int or n!=0 for n in native['counts'].values())
            or not isinstance(native.get('raw_proof_sha256'),str) or not __import__('re').fullmatch('[a-f0-9]{64}',native['raw_proof_sha256'])
            or type(native.get('captured_at')) is not int or native['captured_at']<entry['created_at']):
        raise ValueError('Complete native no-start and exact denial authorization is required')
    if not _readiness_admission_open(row,db_path):return None
    with ledger.connect(db_path) as db:
        _,prior=_readiness_entry(db,row,entry)
        if prior:return None  # An uncertain amended POST is GET-only forever.
        _no_readiness_work(db,row)
        if _readiness_control(db,row,denial,native) is None:return None
    if not _provider_absent(row,db_path):raise ValueError('A provider receipt prevents readiness amendment')
    _,gate=await project_gate(row['erp_project'],config)
    task=await bridge.erp.get('Task',row['erp_task'])
    if task.get('status')!='Open':return None
    fresh=await bridge.packet(row['erp_project'],row['erp_task'],row['target_role'],row['agent_id'],row['task_revision'])
    original=json.loads(row['work_packet']);frame=_readiness_packet(original,fresh)
    old_event=json.loads(entry['event_json'])
    if (task.get('name')!=row['erp_task'] or task.get('project')!=row['erp_project'] or task.get('status')!='Open'
            or str(task.get('modified'))!=fresh['source_modified']
            or task.get('custom_agov_active_run')!=old_event.get('previous_run_id')
            or task.get('custom_agov_active_run')==row['automation_run_id']):
        raise ValueError('Fresh human readiness or unchanged prior execution ownership is missing')
    amended={**old_event,'expected_modified':fresh['source_modified'],'work_packet_digest':packet_digest(fresh)}
    with ledger._transaction(db_path) as db:
        current=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
        _no_readiness_work(db,current)
        if not _matching(current,entry) or current['packet_hash']!=row['packet_hash']:
            raise ValueError('Original denied start identity changed')
        _,prior=_readiness_entry(db,dict(current),entry)
        if prior:return None
        selected=_readiness_control(db,row,denial,native)
        if not selected or not _readiness_admission_open(row,db_path):return None
        ledger._gate(db,row['erp_project'],gate);ledger._task_gate(db,row['erp_project'],row['erp_task'])
        value={'policy':READINESS_POLICY,'invocation_id':row['invocation_id'],'run_id':row['automation_run_id'],
               'original_event_sha256':entry['event_sha256'],'original_packet_hash':row['packet_hash'],
               'lease_fence':row['lease_fence'],'lease_token_sha256':_token(row['lease_token']),
               'authorization_sha256':proof_sha,'native_proof_sha256':native['raw_proof_sha256'],
               'control':selected,'native_frame':frame,'event':amended,'event_sha256':digest(amended)}
        ledger._event(db,row['erp_project'],row['invocation_id'],READINESS_KIND,'server-readiness-policy',value)
    # Exactly one POST follows its durable amendment intent. All later paths GET.
    if not _readiness_admission_open(row,db_path):return None
    await bridge.publish(amended)
    response=await bridge.erp.request('GET',RECEIPT_RPC,params={k:amended[k] for k in ('event_id','project_id','task_id')},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    observed=response.get('message') if isinstance(response,dict) else None
    if not isinstance(observed,dict) or observed.get('exists') is not True:return None
    effective={**entry,'event_json':canonical(amended),'event_sha256':digest(amended)}
    receipt=_receipt(effective,observed)
    return record_start_receipt(row['invocation_id'],receipt,db_path)

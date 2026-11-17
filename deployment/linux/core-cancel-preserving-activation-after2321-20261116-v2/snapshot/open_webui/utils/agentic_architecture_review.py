"""Server-derived architecture-review phase; read-only and never QA acceptance.

The opaque value classifies one frozen PM response to one published architect.
It grants no publication, execution, materialization or current-control authority.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest, stable_run_id

POLICY = {'version': 'architecture-review.v1', 'role': 'project_manager',
          'outcome': 'reviewed', 'source_role': 'solution_architect',
          'source_outcome': 'ready_for_review', 'qa_acceptance': False,
          'implementation_acceptance': False}
POLICY_SHA256 = digest(POLICY)
AUDIT_KIND = 'architecture_review_phase'
_AUTHORITY = object()
_STATES = {'running', 'uncertain', 'cancel_requested', 'execution_finished', 'handed_off'}
_IDENTITY = ('invocation_id', 'erp_project', 'erp_task', 'target_role', 'agent_id',
             'automation_run_id', 'task_revision', 'packet_hash', 'lease_fence',
             'submitted_at', 'source_invocation_id', 'context_hash')


def _need(value, message):
    if not value:
        raise ValueError(message)


@dataclass(frozen=True)
class _Phase:
    _seal: object
    _result_sha256: str
    _packet_sha256: str
    _audit_json: str

    @property
    def audit(self):
        return phase_audit(self)


def matches_phase(phase, result, packet):
    """A dict/boolean/audit cannot authorize the phase-specific validator."""
    return (type(phase) is _Phase and phase._seal is _AUTHORITY
            and phase._result_sha256 == digest(result)
            and phase._packet_sha256 == digest(packet))


def phase_audit(phase):
    _need(type(phase) is _Phase and phase._seal is _AUTHORITY,
          'Architecture phase requires its server-derived opaque authority')
    return json.loads(phase._audit_json)


@contextmanager
def _connection(db, db_path):
    if db is not None:
        yield db
        return
    if db_path is None:
        from open_webui.utils.agentic_governance import path
        db_path = path()
    # Deliberately do not call ledger.connect: this helper creates no schema/file.
    target = Path(db_path).resolve(strict=True)
    conn = sqlite3.connect('file:' + quote(str(target), safe='/') + '?mode=ro',
                           uri=True, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()


def _one(db, query, values):
    cursor = db.execute(query, values)
    row = cursor.fetchone()
    return None if row is None else dict(zip((field[0] for field in cursor.description), row))


def _packet(row, role, *, require_run=True):
    value = json.loads(row.get('work_packet') or 'null')
    binding = value.get('binding', {}) if isinstance(value, dict) else {}
    _need(isinstance(value, dict) and row.get('target_role') == role
          and row.get('packet_hash') == digest(value)
          and value.get('content_sha256') == packet_digest(value)
          and value.get('project_id') == row.get('erp_project')
          and value.get('task_id') == row.get('erp_task')
          and value.get('task_revision') == row.get('task_revision')
          and binding.get('project_id') == row.get('erp_project')
          and binding.get('role') == role and binding.get('agent_id') == row.get('agent_id')
          and (not require_run or row.get('automation_run_id') == stable_run_id(row.get('invocation_id', ''))),
          'Architecture review requires each exact frozen invocation packet')
    return value


def _source(db, current, packet):
    source = _one(db, 'SELECT * FROM governance_invocation WHERE invocation_id=?',
                  (current.get('source_invocation_id'),))
    _need(source is not None and source.get('state') == 'handed_off'
          and source.get('target_role') == 'solution_architect'
          and (source.get('erp_project'), source.get('erp_task'))
          == (current['erp_project'], current['erp_task']),
          'Architecture review requires its direct handed-off architect')
    source_packet = _packet(source, 'solution_architect')
    saved = _one(db, 'SELECT * FROM governance_result WHERE invocation_id=?',
                 (source['invocation_id'],))
    _need(saved is not None and saved.get('phase') == 'published'
          and saved.get('run_id') == source['automation_run_id'],
          'Architecture review requires the original published architect result')
    result, event, receipt = (json.loads(saved.get(key) or 'null')
                              for key in ('result_json', 'event_json', 'receipt_json'))
    _need(isinstance(result, dict) and isinstance(event, dict) and isinstance(receipt, dict)
          and saved.get('result_sha256') == digest(result)
          and result.get('outcome') == 'ready_for_review'
          and result.get('invocation_id') == source['invocation_id']
          and result.get('actor') == source_packet['binding']
          and result.get('project_id') == source['erp_project']
          and result.get('task_id') == source['erp_task']
          and result.get('task_revision') == source['task_revision']
          and result.get('work_packet_digest') == source_packet['content_sha256'],
          'Published architect result identity or immutable hash changed')
    _need(event.get('type') == 'run.ready'
          and event.get('result_contract') == result
          and all(event.get(k) == v for k, v in {
              'invocation_id': source['invocation_id'], 'run_id': source['automation_run_id'],
              'project_id': source['erp_project'], 'task_id': source['erp_task'],
              'role': 'solution_architect', 'agent_id': source['agent_id'],
              'task_revision': source['task_revision'],
              'work_packet_digest': source_packet['content_sha256']}.items())
          and receipt.get('event_id') == event.get('event_id')
          and bool(event.get('event_id')) and receipt.get('task_id') == source['erp_task']
          and receipt.get('durable') is True
          and receipt.get('run_id') == source['automation_run_id']
          and event.get('acceptance_passed') is False
          and bool(receipt.get('modified')) and receipt.get('status') == 'Pending Review',
          'Architect publication event or positive ERP receipt changed')
    context_raw = current.get('context') or ''
    _need(len(context_raw.encode()) <= 50000 and current.get('context_hash') == digest([current['erp_project'], current['erp_task'], 'project_manager', context_raw]),
          'Architecture review context digest changed')
    context = json.loads(context_raw)
    plan = result.get('architect_plan')
    artifacts = event.get('artifacts')
    _need(isinstance(context, dict) and isinstance(plan, dict)
          and context.get('predecessor_invocation') == source['invocation_id']
          and context.get('architect_plan') == plan
          and context.get('published_artifacts') == artifacts
          and isinstance(context.get('instruction'), str) and bool(context['instruction'])
          and isinstance(artifacts, list) and 1 <= len(artifacts) <= 100,
          'PM context must be the exact published architecture and artifacts')
    srs = packet.get('srs') or {}
    meta = plan.get('metadata') or {}
    _need(source_packet.get('srs') == srs
          and source_packet.get('requirement_ids') == packet.get('requirement_ids')
          and meta.get('project_id') == current['erp_project']
          and meta.get('run_id') == source['invocation_id']
          and meta.get('srs_version') == srs.get('revision')
          and meta.get('srs_sha256') == srs.get('sha256')
          and meta.get('agent_binding') == {
              'project_id': source['erp_project'], 'role': 'solution_architect',
              'abit_agent_id': source['agent_id'],
              'erp_ai_user': source_packet['binding'].get('erp_user')},
          'Architecture review must retain the exact SRS and architect binding')
    for source_backlog in (source_packet.get('project_backlog'), packet.get('project_backlog')):
        _need(isinstance(source_backlog, dict) and source_backlog.get('complete') is True
              and source_backlog.get('project_id') == current['erp_project']
              and re.fullmatch('[a-f0-9]{64}', source_backlog.get('context_sha256') or '')
              and meta.get('project_backlog_context_sha256') == source_backlog['context_sha256'],
              'Architecture review backlog semantic context changed')
    _need(meta.get('project_backlog_snapshot_sha256')
          == source_packet['project_backlog'].get('snapshot_sha256'),
          'Published architecture backlog snapshot changed')
    from open_webui.utils.agentic_contracts.validate_architect_plan import validate_plan
    _need(not validate_plan(plan), 'Architecture source plan is not schema-valid')
    for item in plan['designs'] + plan['mockups']:
        reference = item['artifact']
        _need(any(isinstance(value, dict) and value.get('sha256') == reference['sha256']
                  and value.get('kind') == ('design' if item in plan['designs'] else 'mockup')
                  and isinstance(value.get('url'), str)
                  for value in artifacts),
              'Published architecture artifact references changed')
    return source, source_packet, result, event, receipt, context


def derive_phase(row, packet, result, *, db=None, db_path=None, raw_reply_sha256=None):
    """Derive one phase from server DB state; no model field is phase authority."""
    if row.get('target_role') != 'project_manager' or result.get('outcome') != 'reviewed' or packet.get('qa_review'):
        return None
    _need(re.fullmatch('[a-f0-9]{64}', raw_reply_sha256 or ''),
          'Architecture review requires the immutable original raw reply hash')
    with _connection(db, db_path) as connection:
        current = _one(connection, 'SELECT * FROM governance_invocation WHERE invocation_id=?',
                       (row.get('invocation_id'),))
        _need(current is not None and current.get('state') in _STATES
              and all(current.get(key) == row.get(key) for key in _IDENTITY)
              and current.get('lease_token') == row.get('lease_token')
              and bool(current.get('lease_token'))
              and type(current.get('lease_fence')) is int and current['lease_fence'] > 0
              and type(current.get('submitted_at')) is int and current['submitted_at'] > 0,
              'Architecture review original submitted invocation changed')
        frozen = _packet(current, 'project_manager')
        _need(frozen == packet, 'Architecture review supplied packet differs from server ledger')
        _need(result.get('schema_version') == 1 and type(result.get('schema_version')) is int
              and all(result.get(key) == value for key, value in {
                  'invocation_id': current['invocation_id'], 'actor': packet['binding'],
                  'project_id': current['erp_project'], 'task_id': current['erp_task'],
                  'task_revision': current['task_revision'], 'work_packet_digest': packet['content_sha256']}.items())
              and result.get('test_results') == [] and result.get('defects') == []
              and result.get('implementation_invocation_id') is None
              and result.get('implementation_agent_id') is None
              and not any(key in result for key in ('qa_review_ack', 'reviewed_delivery_sha256',
                          'delivery', 'architect_plan', 'action_requests', 'dependency_verifications')),
              'Architecture review cannot claim QA, implementation, actions or a replacement plan')
        evidence = result.get('evidence')
        _need(isinstance(evidence, list) and 1 <= len(evidence) <= 30
              and all(isinstance(item, dict) and item.get('kind') == 'checkpoint'
                      and re.fullmatch(r'artifact://[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', item.get('uri') or '')
                      and '..' not in item['uri'].removeprefix('artifact://')
                      and re.fullmatch('[a-f0-9]{64}', item.get('sha256') or '')
                      for item in evidence),
              'Architecture review requires its own bounded scoped review artifact')
        source, source_packet, original, event, receipt, context = _source(connection, current, packet)
        identity = {key: current.get(key) for key in _IDENTITY}
        identity['lease_token_sha256'] = digest(current['lease_token'])
        audit = {'policy_version': POLICY['version'], 'policy_sha256': POLICY_SHA256,
                 'invocation_id': current['invocation_id'], 'run_id': current['automation_run_id'],
                 'row_sha256': digest(identity), 'packet_sha256': digest(packet),
                 'work_packet_digest': packet['content_sha256'], 'raw_reply_sha256': raw_reply_sha256,
                 'result_sha256': digest(result), 'source_invocation_id': source['invocation_id'],
                 'source_run_id': source['automation_run_id'], 'source_packet_sha256': digest(source_packet),
                 'published_source_result_sha256': digest(original),
                 'published_source_event_sha256': digest(event),
                 'published_source_receipt_sha256': digest(receipt),
                 'architect_plan_sha256': digest(original['architect_plan']),
                 'published_artifacts_sha256': digest(event['artifacts']),
                 'srs_sha256': digest(packet['srs']), 'context_sha256': digest(context)}
        return _Phase(_AUTHORITY, digest(result), digest(packet), canonical(audit))


def verify_saved_phase(row, packet, result, *, db=None, db_path=None):
    """Re-derive a stored published phase; never trust the audit as authority."""
    with _connection(db, db_path) as connection:
        records = connection.execute('SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
                                     (row['invocation_id'], AUDIT_KIND)).fetchall()
        _need(len(records) == 1, 'Architecture review requires exactly one stored phase audit')
        audit = json.loads(records[0][0])
        phase = derive_phase(row, packet, result, db=connection,
                             raw_reply_sha256=audit.get('raw_reply_sha256'))
        _need(phase is not None and phase_audit(phase) == audit,
              'Stored architecture-review phase or source changed')
        return phase


def architecture_context(row, packet, *, db_path=None):
    """Read-only prompt/catalog preview only; never result or write authority.

    A queued PM has not acquired a run yet, so preview verifies frozen source
    context without asserting submission. derive_phase retains submitted checks.
    """
    if row.get('target_role') != 'project_manager' or packet.get('qa_review') or packet.get('atomic_adoption'):
        return False
    try:
        with _connection(None, db_path) as connection:
            current = _one(connection, 'SELECT * FROM governance_invocation WHERE invocation_id=?',
                           (row.get('invocation_id'),))
            if (current is None or current.get('target_role') != 'project_manager'
                    or any(current.get(key) != row.get(key) for key in _IDENTITY)):
                return False
            frozen = _packet(current, 'project_manager', require_run=False)
            if frozen != packet or frozen.get('qa_review') or frozen.get('atomic_adoption'):
                return False
            _source(connection, current, frozen)
            return True
    except ValueError:
        return False

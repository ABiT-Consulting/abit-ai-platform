"""Retire a finished original after authoritative external human completion.

Internal server coordination only. This module never writes ERP, submits a
model, requests cancellation, publishes an AI result or manufactures QA proof.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import digest, stable_run_id

POLICY = 'external-task-terminal.v1'
ACTOR = 'external-task-terminal-coordinator'
RETIRED_REASON = 'Original AI execution retired after external human completion; no AI QA verification'
TERMINAL_TASK = {'Completed', 'Cancelled'}
# These operations have no privileged/external mutation. Unknown operations,
# including GitHub delivery or infrastructure operations, stay fenced.
SAFE_OPERATIONS = frozenset({
    'context.read', 'artifact.read', 'artifact.write', 'pm_review.write',
    'attachment.read', 'attachment.present', 'repository.prepare',
    'repository.read', 'repository.write', 'repository.patch', 'repository.mkdir',
    'repository.test', 'repository.verify_delivery', 'repository.delivery_status',
    'dependency.verify', 'dependency.status',
})
PENDING = {'running','queued','uncertain','pending','pending_reconciliation','awaiting_approval','prepared'}
IDENTITY = ('invocation_id','erp_project','erp_task','agent_id','target_role',
            'automation_run_id','task_revision','packet_hash','work_packet','lease_fence','submitted_at')
TASK_FIELDS = ('name','project','status','modified','modified_by','_assign',
               'custom_agentic_managed','custom_agov_active_run','custom_agov_executor_role')


def require(value, message):
    if not value:
        raise ValueError(message)


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_external_retirement(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,proof_sha256 TEXT NOT NULL,
        proof_json TEXT NOT NULL,phase TEXT NOT NULL,outer_json TEXT,updated_at INTEGER NOT NULL)""")


def _exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def human_terminal(task, configured_ai):
    author=task.get('modified_by') if isinstance(task,dict) else None
    return bool(task.get('status') in TERMINAL_TASK and isinstance(author,str)
                and author==author.strip() and len(author)<=254 and '@' in author
                and not author.casefold().startswith(('ai-','system@','service@'))
                and author.casefold() not in configured_ai)


def _native(row, task, configured_ai):
    require(isinstance(task,dict) and task.get('name')==row['erp_task']
            and task.get('project')==row['erp_project'], 'External completion Task identity differs')
    require(task.get('status') in TERMINAL_TASK, 'ERP Task is not externally terminal')
    require(task.get('custom_agentic_managed') in (1,True,'1')
            and task.get('custom_agov_active_run')==row['automation_run_id'],
            'External completion no longer identifies the original managed run')
    author=task.get('modified_by')
    require(isinstance(author,str) and author==author.strip() and len(author)<=254
            and '@' in author and not author.casefold().startswith(('ai-','system@','service@'))
            and author.casefold() not in configured_ai,
            'External completion is not attributed to a native human account')
    require(isinstance(task.get('modified'),str) and bool(task['modified']),
            'External completion has no native revision')
    return {key:task.get(key) for key in TASK_FIELDS}


def _provider(row, automation):
    from open_webui.env import DATA_DIR
    from open_webui.utils.abitdot_primary import dot_primary_key, verify_saved_scope_receipt
    from open_webui.utils.agentic_relay_scope import scope_for_packet
    database=Path(DATA_DIR)/'abitdot-primary.sqlite'
    require(database.is_file(), 'Original provider journal is absent')
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows=db.execute('SELECT * FROM dot_primary_runs WHERE owner=? AND automation_id=? AND original_run_id=?',
                        (automation.user_id,row['agent_id'],row['automation_run_id'])).fetchall()
    require(len(rows)==1,'Original terminal provider receipt is absent or ambiguous')
    original=dict(rows[0])
    require(original.get('status')=='delegated' and bool(original.get('response_text'))
            and bool(original.get('receipt')),'Original upstream work is not conclusively finished')
    envelope=json.loads(original['envelope_json'])
    packet=json.loads(row['work_packet'])
    require(row['automation_run_id']==stable_run_id(row['invocation_id'])
            and digest(packet)==row['packet_hash'], 'Original invocation or packet digest changed')
    meta=envelope.get('task',{}).get('meta') or {}
    scope=(envelope.get('governance_scope') or {}).get('scope') or {}
    key=dot_primary_key(automation.user_id,row['agent_id'],row['automation_run_id'])
    require(all(envelope.get(k)==v for k,v in {
        'owner_id':automation.user_id,'automation_id':row['agent_id'],
        'run_id':row['automation_run_id'],'dot_id':original['dot_id'],
        'idempotency_key':key}.items()) and original.get('id')==key
        and meta.get('governance_managed') is True and meta.get('erp_task')==row['erp_task']
        and meta.get('erp_role')==row['target_role'],
        'Original provider identity or task binding changed')
    require(scope==scope_for_packet(row,packet,automation.user_id,scope.get('account_id'),
                                    original['dot_id'],row['automation_run_id']),
            'Original provider scope does not identify this immutable work packet')
    verify_saved_scope_receipt(original)
    # Never store private prompt/reply/session bytes in the public audit.
    return {'provider_key_sha256':digest(original['id']),
            'envelope_sha256':hashlib.sha256(original['envelope_json'].encode()).hexdigest(),
            'reply_sha256':hashlib.sha256(original['response_text'].encode()).hexdigest(),
            'receipt_sha256':digest(original['receipt']),
            'scope_receipt_sha256':digest(original.get('scope_receipt_json')),
            'terminal_state':'delegated'}


def _settled(db, row):
    invocation=row['invocation_id']
    # Do not replace or discard an already produced result/publication.
    for table in ('governance_result','governance_local_result','governance_action'):
        if _exists(db,table):
            require(not db.execute('SELECT 1 FROM '+table+' WHERE invocation_id=? LIMIT 1',
                                   (invocation,)).fetchone(),
                    'Original has published/pending result or privileged action evidence')
    require(not db.execute(
        "SELECT 1 FROM governance_invocation WHERE (source_invocation_id=? OR parent_invocation_id=?) "
        "AND state IN ('queued','running','uncertain','cancel_requested','execution_finished','awaiting_approval') LIMIT 1",
        (invocation,invocation)).fetchone(), 'Original has an unresolved successor or child')
    calls=[]
    if _exists(db,'governance_gateway_call') and _exists(db,'governance_gateway_session'):
        for value in db.execute(
            'SELECT c.* FROM governance_gateway_call c JOIN governance_gateway_session s ON s.session_id=c.session_id '
            'WHERE s.invocation_id=? ORDER BY c.session_id,c.call_id',(invocation,)):
            require(value['state']=='completed' and value['operation'] in SAFE_OPERATIONS,
                    'Original gateway operation is unsettled or has unsupported external effects')
            result=json.loads(value['result_json']) if value['result_json'] else {}
            if value['operation'].startswith('repository.') and isinstance(result,dict):
                require(str(result.get('state','')).casefold() not in PENDING
                        and str(result.get('status','')).casefold() not in PENDING,
                        'Completed HTTP receipt still describes unfinished repository work')
            if value['operation']=='repository.test':
                require(isinstance(result,dict) and type(result.get('exit_code')) is int,
                        'Original repository test has no terminal process exit receipt')
            calls.append({'session_id_sha256':digest(value['session_id']),
                          'call_id':value['call_id'],'operation':value['operation'],
                          'request_hash':value['request_hash'],'result_sha256':digest(result)})
    # Delivery/remote steps may have begun outside the final returned operation.
    for table in ('governance_developer_delivery','governance_delivery_step'):
        if _exists(db,table):
            require(not db.execute('SELECT 1 FROM '+table+' WHERE invocation_id=? LIMIT 1',
                                   (invocation,)).fetchone(),
                    'Original external delivery requires its own reconciliation')
    return digest(calls)


def _proof(row, native, provider, calls):
    return {'policy':POLICY, **{key:row[key] for key in
            ('invocation_id','erp_project','erp_task','agent_id','target_role','automation_run_id',
             'task_revision','packet_hash','lease_fence')},
            'native_task':native,'provider':provider,'settled_gateway_sha256':calls,
            'ai_qa_verified':False,'erp_writes':0,'new_upstream_submissions':0}


def _prepare(row, proof, db_path):
    with ledger._transaction(db_path) as db:
        _schema(db)
        current=ledger._owned(db,row['invocation_id'],row['automation_run_id'],
                              row['lease_token'],{'uncertain','cancel_requested'})
        require(current and all(current[k]==row[k] for k in IDENTITY),
                'Original submitted task fence changed before external retirement')
        require(current['submitted_at'] is not None and current['qa_verdict'] is None,
                'External retirement cannot replace an unsubmitted run or QA verdict')
        require(_settled(db,current)==proof['settled_gateway_sha256'],'Original gateway receipts changed')
        old=db.execute('SELECT * FROM governance_external_retirement WHERE invocation_id=?',
                       (row['invocation_id'],)).fetchone()
        encoded=ledger._json(proof);fingerprint=digest(proof)
        if old:
            require(old['run_id']==row['automation_run_id'] and old['proof_sha256']==fingerprint
                    and old['proof_json']==encoded,'External retirement proof changed during recovery')
        else:
            db.execute("INSERT INTO governance_external_retirement VALUES(?,?,?,?, 'prepared',NULL,?)",
                       (row['invocation_id'],row['automation_run_id'],fingerprint,encoded,int(time.time())))
            ledger._event(db,row['erp_project'],row['invocation_id'],'external_task_retirement_prepared',
                          ACTOR,{'policy':POLICY,'proof_sha256':fingerprint,
                                 'task_status':proof['native_task']['status'],
                                 'modified_by':proof['native_task']['modified_by'],'ai_qa_verified':False})


async def _close_outer(row, reason):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.automations import AutomationRun
    async with get_async_db_context() as db:
        original=await db.get(AutomationRun,row['automation_run_id'])
        require(original and original.automation_id==row['agent_id']
                and (original.status in {'running','error'}
                     or original.status=='cancelled' and original.error==reason),
                'Exact original outer AutomationRun cannot be retired')
        original.status='cancelled';original.error=reason
        if hasattr(original,'lease_until'):original.lease_until=None
        await db.commit()
    return {'run_id':row['automation_run_id'],'automation_id':row['agent_id'],
            'status':'cancelled','reason_sha256':hashlib.sha256(reason.encode()).hexdigest()}


def _record_outer(row, proof, outer, db_path):
    require(outer=={'run_id':row['automation_run_id'],'automation_id':row['agent_id'],
                   'status':'cancelled','reason_sha256':hashlib.sha256(RETIRED_REASON.encode()).hexdigest()},
            'Outer closure did not confirm the exact original run')
    with ledger._transaction(db_path) as db:
        _schema(db)
        current=ledger._owned(db,row['invocation_id'],row['automation_run_id'],row['lease_token'],
                              {'uncertain','cancel_requested'})
        require(current and all(current[k]==row[k] for k in IDENTITY),'Original fence changed after outer closure')
        require(_settled(db,current)==proof['settled_gateway_sha256'],'Tool receipt changed after outer closure')
        saved=db.execute('SELECT * FROM governance_external_retirement WHERE invocation_id=?',
                         (row['invocation_id'],)).fetchone()
        require(saved and saved['proof_sha256']==digest(proof),'Prepared external retirement is missing')
        if saved['outer_json']:
            require(saved['outer_json']==ledger._json(outer),'Original outer closure receipt changed')
        else:
            db.execute("UPDATE governance_external_retirement SET phase='outer_closed',outer_json=?,updated_at=? "
                       "WHERE invocation_id=?",(ledger._json(outer),int(time.time()),row['invocation_id']))
            ledger._event(db,row['erp_project'],row['invocation_id'],'external_task_outer_closed',
                          ACTOR,{'policy':POLICY,'proof_sha256':digest(proof),'outer_sha256':digest(outer),
                                 'ai_qa_verified':False})


def _settlement_receipt(invocation, db_path):
    """Mint only from the committed server journal, never a caller assertion."""
    from open_webui.utils import agentic_maintenance as maintenance
    with ledger.connect(db_path) as db:
        saved=db.execute('SELECT * FROM governance_external_retirement WHERE invocation_id=?',
                         (invocation,)).fetchone()
        event=db.execute("SELECT * FROM governance_event WHERE invocation_id=? "
                         "AND kind='external_task_outer_closed' ORDER BY seq DESC LIMIT 1",
                         (invocation,)).fetchone()
        require(saved and saved['phase'] in {'outer_closed','retired'} and saved['outer_json']
                and event and event['actor']==ACTOR,'Committed external retirement settlement proof is absent')
        proof=json.loads(saved['proof_json']);outer=json.loads(saved['outer_json'])
        require(digest(proof)==saved['proof_sha256'] and
                json.loads(event['payload'])=={'policy':POLICY,'proof_sha256':saved['proof_sha256'],
                                               'outer_sha256':digest(outer),'ai_qa_verified':False},
                'Committed external retirement audit differs')
        fingerprint=event['event_hash']
    return maintenance._proof('governed',invocation,
                              {'policy':POLICY,'run_id':saved['run_id'],
                               'proof_sha256':saved['proof_sha256'],'audit_sha256':fingerprint})


def _finish(row, proof, db_path):
    with ledger._transaction(db_path) as db:
        _schema(db)
        current=ledger._owned(db,row['invocation_id'],row['automation_run_id'],row['lease_token'],
                              {'uncertain','cancel_requested'})
        require(current and all(current[k]==row[k] for k in IDENTITY),'Original fence changed before retirement')
        require(_settled(db,current)==proof['settled_gateway_sha256'],'Original tools changed before retirement')
        saved=db.execute('SELECT * FROM governance_external_retirement WHERE invocation_id=?',
                         (row['invocation_id'],)).fetchone()
        require(saved and saved['phase']=='outer_closed' and saved['proof_sha256']==digest(proof),
                'Exact original outer closure is required')
        db.execute("UPDATE governance_invocation SET state='cancelled',reason=?,updated_at=? WHERE invocation_id=?",
                   (RETIRED_REASON,int(time.time()),row['invocation_id']))
        ledger._release(db,current)
        db.execute("UPDATE governance_external_retirement SET phase='retired',updated_at=? WHERE invocation_id=?",
                   (int(time.time()),row['invocation_id']))
        ledger._event(db,row['erp_project'],row['invocation_id'],'external_task_terminal',ACTOR,
                      {'policy':POLICY,'proof_sha256':digest(proof),'native_status':proof['native_task']['status'],
                       'modified_by':proof['native_task']['modified_by'],'ai_qa_verified':False,
                       'summary':RETIRED_REASON})


def _already_retired(row, db_path):
    with ledger.connect(db_path) as db:
        if not _exists(db,'governance_external_retirement'):
            return None
        current=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',
                           (row['invocation_id'],)).fetchone()
        saved=db.execute('SELECT * FROM governance_external_retirement WHERE invocation_id=?',
                         (row['invocation_id'],)).fetchone()
        if not current or not saved or current['state']!='cancelled' or saved['phase']!='retired':
            return None
        proof=json.loads(saved['proof_json'])
        require(all(current[k]==row[k] for k in IDENTITY)
                and current['qa_verdict'] is None and saved['run_id']==row['automation_run_id']
                and digest(proof)==saved['proof_sha256'],'Retired original identity changed')
        require(db.execute("SELECT 1 FROM governance_event WHERE invocation_id=? "
                           "AND kind='external_task_terminal' AND actor=?",
                           (row['invocation_id'],ACTOR)).fetchone(),
                'Retired original has no external completion audit')
        return {'retired':True,'already_retired':True,'invocation_id':row['invocation_id'],
                'run_id':row['automation_run_id'],'task_status':proof['native_task']['status'],
                'ai_qa_verified':False}


async def retire(row, automation, bridge, db_path=None, close_outer=None):
    """Read terminal native state twice; retire only its settled exact original."""
    previous=_already_retired(row,db_path)
    if previous:return previous
    require(row['state'] in {'uncertain','cancel_requested'} and row['target_role']!='it_manager',
            'External retirement supports only a non-privileged original recovery')
    configured_ai={r['erp_user'].casefold() for r in ledger.roster(row['erp_project'],db_path)}
    task=await bridge.erp.get('Task',row['erp_task'])
    native=_native(row,task,configured_ai)
    provider=_provider(row,automation)
    with ledger.connect(db_path) as db:
        calls=_settled(db,row)
    proof=_proof(row,native,provider,calls)
    before_close=_native(row,await bridge.erp.get('Task',row['erp_task']),configured_ai)
    require(before_close==native and _provider(row,automation)==provider,
            'Native Task or original provider changed before outer closure')
    _prepare(row,proof,db_path)
    outer=await (close_outer or _close_outer)(row,RETIRED_REASON)
    # Human reopening/ownership/revision changes cannot race a guessed closure.
    fresh=_native(row,await bridge.erp.get('Task',row['erp_task']),configured_ai)
    require(fresh==native and _provider(row,automation)==provider,
            'Native Task or original provider receipt changed before retirement')
    _record_outer(row,proof,outer,db_path)
    from open_webui.utils import agentic_maintenance as maintenance
    receipt=_settlement_receipt(row['invocation_id'],db_path)
    maintenance.settle_reference('governed',row['invocation_id'],receipt,
                                db_path=maintenance.path(Path(db_path).parent if db_path else None))
    _finish(row,proof,db_path)
    return {'retired':True,'invocation_id':row['invocation_id'],'run_id':row['automation_run_id'],
            'task_status':native['status'],'ai_qa_verified':False}

"""Explicit ERP Retry for a positively rejected outgoing transport only.

The failed request stays immutable. Native clears its exact failed active run,
then the existing executor receives one new generation with a fresh WorkPacket.
No model result, QA verdict or business completion is manufactured here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest, stable_run_id, RPC


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_transport_retry(
        original_invocation TEXT PRIMARY KEY,command_id TEXT NOT NULL,
        new_invocation TEXT NOT NULL UNIQUE,proof_json TEXT NOT NULL,
        event_json TEXT NOT NULL,receipt_json TEXT,phase TEXT NOT NULL)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS transport_retry_identity_immutable
        BEFORE UPDATE ON governance_transport_retry WHEN
        OLD.original_invocation!=NEW.original_invocation OR OLD.command_id!=NEW.command_id OR
        OLD.new_invocation!=NEW.new_invocation OR OLD.proof_json!=NEW.proof_json OR
        OLD.event_json!=NEW.event_json OR
        (OLD.receipt_json IS NOT NULL AND OLD.receipt_json!=NEW.receipt_json)
        BEGIN SELECT RAISE(ABORT,'Original transport retry evidence is immutable'); END''')


def _authority(db, row, event_id):
    """Only the current acknowledged signed Native Task Retry is authority."""
    ledger._task_gate(db, row['erp_project'], row['erp_task'])
    inbox = db.execute('SELECT * FROM governance_inbox WHERE event_id=?', (event_id,)).fetchone()
    if not inbox:
        raise ValueError('Transport recovery requires its signed Native Task Retry')
    event = json.loads(inbox['payload'])
    value = event.get('payload') or {}
    actor = value.get('actor')
    if (inbox['state'] not in {'pending', 'processed'} or digest(event) != inbox['payload_hash']
        or event.get('type') != 'project.control' or event.get('event_id') != event_id
        or event.get('project') != row['erp_project'] or inbox['erp_project'] != row['erp_project']
        or event.get('task') != row['erp_task'] or value.get('task_id') != row['erp_task']
        or value.get('action') != 'retry' or type(value.get('control_sequence')) is not int
        or not isinstance(actor, str) or not actor.strip() or len(actor) > 254
        or actor.casefold() in {'guest','signed-erp','signed-erp-event','erp'}
        or db.execute('SELECT 1 FROM governance_agent WHERE lower(erp_user)=lower(?) OR agent_id=?',
                      (actor,actor)).fetchone()):
        raise ValueError('Transport recovery requires the exact human Native Task Retry')
    native = db.execute('SELECT * FROM governance_native_control WHERE project=? AND task=?',
                        (row['erp_project'],row['erp_task'])).fetchone()
    if (not native or native['phase'] != 'acknowledged' or native['applied_state'] != 'enabled'
        or native['action'] != 'retry' or native['command_id'] != value.get('command_id')
        or native['sequence'] != value['control_sequence']):
        raise ValueError('Native Task Retry was superseded or is not acknowledged')
    return {'command_id':native['command_id'],'sequence':native['sequence'],'actor':actor}


def _unexecuted(db, row, envelope):
    """A failed outgoing UI marker cannot erase any accepted reply or tool work."""
    current=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',
                       (row['invocation_id'],)).fetchone()
    if (not current or current['state'] != 'uncertain'
        or any(current[k]!=row[k] for k in ('automation_run_id','lease_token','lease_fence','packet_hash'))
        or not ledger._owned(db,row['invocation_id'],row['automation_run_id'],row['lease_token'],{'uncertain'})
        or not row['submitted_at']
        or row['automation_run_id'] != stable_run_id(row['invocation_id'])):
        raise ValueError('Original transport is not an uncertain stable submitted run')
    scope = envelope['governance_scope']['scope']
    if any(scope.get(k) != v for k,v in {
        'invocation_id':row['invocation_id'],'run_id':row['automation_run_id'],
        'project_id':row['erp_project'],'task_id':row['erp_task'],'role':row['target_role'],
        'agent_id':row['agent_id'],'work_packet_digest':packet_digest(json.loads(row['work_packet']))}.items()):
        raise ValueError('Original rejection scope differs from its immutable invocation')
    sessions = db.execute('SELECT * FROM governance_gateway_session WHERE invocation_id=?',
                          (row['invocation_id'],)).fetchall()
    if (len(sessions) != 1 or sessions[0]['client_session_hash'] is not None
        or json.loads(sessions[0]['scope_json']) != scope
        or db.execute('SELECT 1 FROM governance_gateway_call WHERE session_id=? LIMIT 1',
                      (sessions[0]['session_id'],)).fetchone()):
        raise ValueError('Original gateway accepted a client or tool call; preserve its execution')
    for table in ('governance_result','governance_local_result','governance_action'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            if db.execute('SELECT 1 FROM '+table+' WHERE invocation_id=? LIMIT 1',
                          (row['invocation_id'],)).fetchone():
                raise ValueError('Original has execution/result evidence; transport retry is forbidden')


def _receipt(event, value):
    value = value.get('receipt',value) if isinstance(value,dict) else None
    if isinstance(value,dict):
        value={k:v for k,v in value.items() if k not in {'exists','content_sha256'}}
    rejection = event['transport_rejection']
    if (not isinstance(value,dict) or value.get('durable') is not True
        or any(value.get(k) != event[k] for k in ('event_id','task_id','run_id'))
        or value.get('status') != 'Open' or not value.get('modified')
        or value.get('not_submitted') is not True
        or value.get('proof_sha256') != rejection['proof_sha256']
        or value.get('transport_rejection_sha256') != digest(rejection)
        or not value.get('current_task_revision')):
        raise ValueError('Native did not confirm the exact original transport rejection')
    return value


def settle_rejected_admission(invocation, db_path=None):
    """Settle only this failed original's maintenance lease from durable proof.

    Other unresolved references remain byte-for-byte untouched. A provider error
    by itself is insufficient: Native must have acknowledged the exact cleanup.
    """
    from open_webui.env import DATA_DIR
    from open_webui.utils import agentic_maintenance as maintenance
    from open_webui.utils.abitdot_original_rejection import verify_rejection

    root=Path(db_path).parent if db_path else Path(DATA_DIR)
    with ledger.connect(db_path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_transport_retry'").fetchone():
            raise ValueError('Original failed transport has no durable Native cleanup receipt')
        row=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(invocation,)).fetchone()
        entry=db.execute('SELECT * FROM governance_transport_retry WHERE original_invocation=?',(invocation,)).fetchone()
        if not row or row['state']!='failed' or not entry or entry['phase']!='queued' or not entry['receipt_json']:
            raise ValueError('Original failed transport has no durable Native cleanup receipt')
        event=json.loads(entry['event_json']);receipt=_receipt(event,json.loads(entry['receipt_json']))
        proof=json.loads(entry['proof_json'])
        if (event['invocation_id']!=invocation or event['run_id']!=row['automation_run_id']
            or row['automation_run_id']!=stable_run_id(invocation)):
            raise ValueError('Original maintenance rejection identity differs')
        sessions=db.execute('SELECT * FROM governance_gateway_session WHERE invocation_id=?',(invocation,)).fetchall()
        if (len(sessions)!=1 or sessions[0]['client_session_hash'] is not None
            or db.execute('SELECT 1 FROM governance_gateway_call WHERE session_id=? LIMIT 1',
                          (sessions[0]['session_id'],)).fetchone()):
            raise ValueError('Original gateway work prevents rejection settlement')
    with maintenance._readonly(root/'abitdot-primary.sqlite') as db:
        provider=db.execute('SELECT * FROM dot_primary_runs WHERE original_run_id=? AND automation_id=?',
                            (row['automation_run_id'],row['agent_id'])).fetchall()
        if len(provider)!=1:raise ValueError('Exact original provider is missing')
        original=provider[0];envelope=json.loads(original['envelope_json'])
        checked=verify_rejection(envelope,proof)
        if (original['status']!='unavailable' or original['receipt'] is not None
            or original['response_text'] is not None or original['scope_receipt_json'] is not None
            or digest(checked)!=event['transport_rejection']['proof_sha256']
            or not db.execute('SELECT 1 FROM dot_primary_rejections WHERE run_key=? AND proof_sha256=? AND proof_json=?',
                              (original['id'],digest(checked),canonical(checked))).fetchone()):
            raise ValueError('Immutable negative provider receipt is missing')
    value={'run_id':row['automation_run_id'],'event_sha256':digest(event),
           'native_receipt_sha256':digest(receipt),'original_rejection_sha256':digest(checked)}
    return maintenance.settle_reference('governed',invocation,
        maintenance._proof('governed',invocation,value),db_path=maintenance.path(root))


async def retry_rejected_transport(invocation, event_id, *, automation, adapter, bridge,
                                   project_gate, config, db_path=None, observer=None):
    """Return one fresh queued generation, or None while original ACK is unknown.

    No send occurs here. Existing dispatch/claim/lease logic owns the next run.
    A published callback with unknown acknowledgement is reconciled GET-only.
    """
    from open_webui.env import DATA_DIR
    from open_webui.utils.abitdot_primary import ledger as dot_ledger, dot_primary_key
    from open_webui.utils.abitdot_original_rejection import verify_rejection, record_rejection, DIALOG
    from open_webui.utils.agentic_recovery import observe_original
    from open_webui.utils.agentic_ownership import submission_guard

    row = ledger.find(invocation,db_path,include_private=True)
    if not row or not automation or row['target_role'] not in ledger.HANDOFFS['erp']:
        return None
    _, gate = await project_gate(row['erp_project'],config)
    with ledger._transaction(db_path) as db:
        _schema(db);ledger._gate(db,row['erp_project'],gate)
        authority = _authority(db,row,event_id)
        prior = db.execute('SELECT * FROM governance_transport_retry WHERE original_invocation=?',
                           (invocation,)).fetchone()
        queued = prior['new_invocation'] if prior and prior['phase']=='queued' else None
        if prior and prior['command_id'] != authority['command_id']:
            raise ValueError('The original rejection already belongs to an earlier explicit Retry')
    if queued:
        settle_rejected_admission(invocation,db_path)
        return queued
    # Read-only provider identity lookup. The original request is never replaced.
    provider_path = (Path(db_path).parent if db_path else Path(DATA_DIR)) / 'abitdot-primary.sqlite'
    if not provider_path.is_file():
        return None
    with dot_ledger(provider_path) as db:
        found = db.execute('SELECT * FROM dot_primary_runs WHERE id=?',
            (dot_primary_key(automation.user_id,row['agent_id'],row['automation_run_id']),)).fetchone()
        original = dict(found) if found else None
    if not original:
        return None
    envelope = json.loads(original['envelope_json'])
    registered = next((r for r in ledger.roster(row['erp_project'],db_path)
                       if r['agent_id'] == row['agent_id']),None)
    meta = automation.meta or {}
    if (not registered or automation.id != row['agent_id'] or automation.user_id != registered['owner_id']
        or meta.get('governance_managed') is not True or meta.get('agentic_project') != row['erp_project']
        or meta.get('agentic_role') != row['target_role'] or meta.get('erp_role_user') != registered['erp_user']
        or envelope.get('owner_id') != automation.user_id or envelope.get('run_id') != row['automation_run_id']
        or envelope.get('automation_id') != row['agent_id'] or original['receipt'] is not None
        or original['response_text'] is not None or original['scope_receipt_json'] is not None):
        raise ValueError('Original provider/Automation ownership or acceptance changed')
    async with submission_guard(row,db_path):
        with ledger._transaction(db_path) as db:
            locked=db.execute('SELECT * FROM governance_transport_retry WHERE original_invocation=?',
                              (invocation,)).fetchone()
            locked_queued=locked['new_invocation'] if locked and locked['phase']=='queued' else None
            if locked_queued:
                _authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate)
            if locked:
                prior=locked
        if locked_queued:
            settle_rejected_admission(invocation,db_path)
            return locked_queued
        if not prior:
            outcome, observed = await (observer or observe_original)(envelope)
            if not outcome or outcome.get('status') != 'rejected_before_submit':
                return None
            proof = verify_rejection(envelope,outcome['original_rejection'])
            with ledger._transaction(db_path) as db:
                _schema(db);ledger._gate(db,row['erp_project'],gate)
                _authority(db,row,event_id);_unexecuted(db,row,envelope)
            with dot_ledger(provider_path) as db:
                db.execute('BEGIN IMMEDIATE');rejected=record_rejection(db,envelope,proof)
            fresh = await bridge.packet(row['erp_project'],row['erp_task'],row['target_role'],row['agent_id'])
            with ledger._transaction(db_path) as db:
                _schema(db);ledger._gate(db,row['erp_project'],gate)
                _authority(db,row,event_id);_unexecuted(db,row,envelope)
                start = db.execute('SELECT * FROM governance_start WHERE invocation_id=?',(invocation,)).fetchone()
                if not start or not start['receipt_json']:
                    raise ValueError('Original confirmed Native start journal is required')
                source = json.loads(start['event_json'])
                from open_webui.utils.agentic_start_recovery import _identity, _matching, _receipt as start_receipt
                if not _matching(row,start):raise ValueError('Original start fence changed')
                _identity(row,source);start_receipt(start,json.loads(start['receipt_json']))
                new_id='agov-transport-retry-'+digest([invocation,authority['command_id']])[:48]
                rejection={'code':'CHATGPT_MESSAGE_TOO_LONG','not_submitted':True,
                    'root_message_id':proof.get('root_message_id'),
                    'proof_sha256':rejected['original_rejection_sha256'],
                    'scope_digest':proof['scope_receipt']['scope_digest'],
                    'source_event_id':source['event_id'],'source_task_revision':source['task_revision'],
                    'source_project_revision':source['expected_project_revision']}
                event={'schema_version':1,'event_id':'agov-transport-rejected-'+digest([invocation,authority['command_id']])[:40],
                    'type':'run.transport_rejected','project_id':row['erp_project'],'task_id':row['erp_task'],
                    'task_revision':fresh['task_revision'],'run_id':row['automation_run_id'],
                    'invocation_id':invocation,'role':row['target_role'],'agent_id':row['agent_id'],
                    'work_packet_digest':packet_digest(json.loads(row['work_packet'])),
                    'expected_modified':fresh['source_modified'],'expected_project_revision':fresh['project_revision'],
                    'artifacts':[],'transport_rejection':rejection,
                    'retry_command_id':authority['command_id'],'retry_control_sequence':authority['sequence'],
                    'summary':'ChatGPT transport rejected the original message: CHATGPT_MESSAGE_TOO_LONG; no accepted turn.'}
                db.execute('INSERT INTO governance_transport_retry VALUES(?,?,?,?,?,NULL,?)',
                           (invocation,authority['command_id'],new_id,canonical(proof),canonical(event),'prepared'))
                ledger._event(db,row['erp_project'],invocation,'transport_rejection_intent',authority['actor'],
                              {'event_id':event['event_id'],'proof_sha256':rejection['proof_sha256'],
                               'retry_command_id':authority['command_id'],'new_invocation':new_id})
        with ledger._transaction(db_path) as db:
            entry=dict(db.execute('SELECT * FROM governance_transport_retry WHERE original_invocation=?',(invocation,)).fetchone())
            event=json.loads(entry['event_json'])
            _authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate);_unexecuted(db,row,envelope)
            publish = entry['phase'] == 'prepared'
            if publish:db.execute("UPDATE governance_transport_retry SET phase='publishing' WHERE original_invocation=? AND phase='prepared'",(invocation,))
        if entry['receipt_json']:
            receipt=_receipt(event,json.loads(entry['receipt_json']))
        elif publish:
            receipt=_receipt(event,await bridge.publish(event))
        else:
            response=await bridge.erp.request('GET',RPC+'get_result_receipt',
                params={'project_id':row['erp_project'],'event_id':event['event_id']},
                headers={'X-ABIT-Agentic-Bridge':bridge.secret})
            value=response.get('message') or {}
            if value.get('exists') is not True:return None
            receipt=_receipt(event,value)
        with ledger._transaction(db_path) as db:
            _authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate);_unexecuted(db,row,envelope)
            saved=db.execute('SELECT receipt_json FROM governance_transport_retry WHERE original_invocation=?',(invocation,)).fetchone()[0]
            if saved and json.loads(saved)!=receipt:raise ValueError('Native transport rejection receipt is immutable')
            db.execute("UPDATE governance_transport_retry SET receipt_json=?,phase='acknowledged' WHERE original_invocation=?",
                       (canonical(receipt),invocation))
        # Ordinary AutomationRun remains historical and records the real error.
        await adapter.reject_transport(automation,row['automation_run_id'],'CHATGPT_MESSAGE_TOO_LONG: '+DIALOG)
        _, gate = await project_gate(row['erp_project'],config)
        fresh = await bridge.packet(row['erp_project'],row['erp_task'],row['target_role'],row['agent_id'])
        with ledger._transaction(db_path) as db:
            _authority(db,row,event_id);ledger._gate(db,row['erp_project'],gate);_unexecuted(db,row,envelope)
            current=db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(invocation,)).fetchone()
            if not ledger._owned(db,invocation,row['automation_run_id'],row['lease_token'],{'uncertain'}):
                raise ValueError('Original governance lease changed during transport reconciliation')
            db.execute("UPDATE governance_invocation SET state='failed',reason=?,updated_at=? WHERE invocation_id=?",
                       ('CHATGPT_MESSAGE_TOO_LONG: verified original outgoing rejection',int(time.time()),invocation))
            ledger._release(db,current)
            ledger._event(db,row['erp_project'],invocation,'execution_failed','executor',
                          {'run_id':row['automation_run_id'],'qa_verdict':None})
            new,created=ledger._enqueue(db,invocation_id=entry['new_invocation'],project=row['erp_project'],
                task=row['erp_task'],from_role='erp',target_role=row['target_role'],task_revision=fresh['task_revision'],
                context='Retry this exact ERP role after its proven outgoing transport rejection. Reconcile all fresh human checkpoints and existing source work before acting.',
                work_packet=fresh,actor=authority['actor'],gate=gate)
            db.execute("UPDATE governance_transport_retry SET phase='queued' WHERE original_invocation=?",(invocation,))
            ledger._event(db,row['erp_project'],invocation,'transport_rejected_retry_queued',authority['actor'],
                {'native_event_id':event['event_id'],'proof_sha256':event['transport_rejection']['proof_sha256'],
                 'new_invocation':new['invocation_id'],'retry_command_id':authority['command_id']})
        settle_rejected_admission(invocation,db_path)
        return new['invocation_id']

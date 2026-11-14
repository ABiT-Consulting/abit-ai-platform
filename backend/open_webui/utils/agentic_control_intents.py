"""Apply ERP control sequences atomically before acknowledging the native veto."""
from __future__ import annotations
import json,time
from open_webui.utils import agentic_governance as ledger
from open_webui.utils import agentic_controls as controls
from open_webui.utils.agentic_bridge import digest,signing_headers
RPC='/api/method/abit_ai_assistant.agentic_control_fence.'


def _schema(db):
    controls.schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS governance_native_control(
        project TEXT NOT NULL,task TEXT NOT NULL,sequence INTEGER NOT NULL,
        command_id TEXT NOT NULL,action TEXT NOT NULL,applied_state TEXT NOT NULL,
        phase TEXT NOT NULL,PRIMARY KEY(project,task))''')


def _apply(project,task,intent,revision,actor,path):
    """Cursor, effective state and cancellation/freeze are one SQLite commit."""
    action=intent['action'];seq=intent['control_sequence'];command=intent['command_id']
    with ledger._transaction(path) as db:
        _schema(db)
        previous=db.execute('SELECT * FROM governance_native_control WHERE project=? AND task=?',(project,task)).fetchone()
        if previous:
            if previous['sequence']>seq:return None
            if previous['sequence']==seq:
                if (previous['command_id'],previous['action'])!=(command,action):raise ValueError('Native control sequence conflicts')
                return previous['applied_state']
        desired={'start':'enabled','resume':'enabled','pause':'paused','stop':'stopped'}.get(action)
        if action=='retry':
            row=db.execute('SELECT state FROM governance_task_control WHERE project=? AND task=?',(project,task)).fetchone() if task else db.execute('SELECT state FROM governance_project_control WHERE erp_project=?',(project,)).fetchone()
            desired=row['state'] if row else ('enabled' if task else 'disabled')
        elif task:
            db.execute('INSERT INTO governance_task_control VALUES(?,?,?,?,?) ON CONFLICT(project,task) DO UPDATE SET state=excluded.state,actor=excluded.actor,updated_at=excluded.updated_at',(project,task,desired,actor,int(time.time())))
        else:
            db.execute('INSERT INTO governance_project_control VALUES(?,?,?,?,?) ON CONFLICT(erp_project) DO UPDATE SET state=excluded.state,actor=excluded.actor,policy_revision=excluded.policy_revision,updated_at=excluded.updated_at',(project,desired,actor,str(revision),int(time.time())))
        if action in {'pause','stop'}:
            query="SELECT * FROM governance_invocation WHERE erp_project=? AND state IN ('queued','running','uncertain','execution_finished','awaiting_approval')"
            args=[project]
            if task:query+=' AND erp_task=?';args.append(task)
            for row in db.execute(query,args).fetchall():
                if action=='stop':ledger._cancel(db,row,actor)
                elif row['state']=='queued' and row['submitted_at'] is None:
                    db.execute("UPDATE governance_invocation SET state='blocked',reason='ERP control prevents new work',updated_at=? WHERE invocation_id=?",(int(time.time()),row['invocation_id']))
                    db.execute("INSERT OR REPLACE INTO governance_control_wait VALUES(?,?,?,'waiting')",(row['invocation_id'],project,row['erp_task']))
                    ledger._release(db,row)
        db.execute("INSERT INTO governance_native_control VALUES(?,?,?,?,?,?,'applied_pending_ack') ON CONFLICT(project,task) DO UPDATE SET sequence=excluded.sequence,command_id=excluded.command_id,action=excluded.action,applied_state=excluded.applied_state,phase=excluded.phase",(project,task,seq,command,action,desired))
        ledger._event(db,project,None,'native_control_applied',actor,{'task_id':task,'command_id':command,'control_sequence':seq,'action':action,'state':desired,'upstream_cancel_confirmed':False})
        return desired


async def apply_current(event,bridge,*,db_path=None):
    payload=event.get('payload') or {};project=event['project'];task=event.get('task') or payload.get('task_id') or ''
    action=payload.get('action');seq=payload.get('control_sequence');command=payload.get('command_id')
    if action not in {'start','resume','pause','stop','retry'} or not isinstance(seq,int) or isinstance(seq,bool) or seq<1 or not isinstance(command,str):
        raise ValueError('Signed control event lacks its exact native intent sequence')
    if payload.get('task_id','')!=task:raise ValueError('Control Task scope differs')
    response=await bridge.erp.request('GET',RPC+'get_control_intent',params={'project_id':project,'task_id':task},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    current=response.get('message') or {};intent=current.get('intent')
    if current.get('project_id')!=project or current.get('task_id')!=task:raise ValueError('Native control readback scope differs')
    if not intent or any(intent.get(key)!=value for key,value in {'project_id':project,'task_id':task,'command_id':command,'control_sequence':seq,'action':action,'actor':payload.get('actor')}.items()):return False
    state=_apply(project,task,intent,event['revision'],str(payload.get('actor') or 'signed-erp-event'),db_path)
    if state is None:return False
    ack={'schema_version':1,'event_id':'agov-control-applied-'+digest([project,task,command,seq])[:40],'project_id':project,'task_id':task,'command_id':command,'control_sequence':seq,'action':action,'applied_state':state}
    # Reconcile the original acknowledgement before another signed write.
    response=await bridge.erp.request('GET',RPC+'get_control_application_receipt',params={'project_id':project,'task_id':task,'command_id':command,'control_sequence':seq},headers={'X-ABIT-Agentic-Bridge':bridge.secret})
    receipt=response.get('message') or {}
    if receipt.get('exists') is not True:
        response=await bridge.erp.request('POST',RPC+'acknowledge_control',json={'ack':ack},headers={'X-ABIT-Agentic-Bridge':bridge.secret,**signing_headers(ack,bridge.secret)})
        receipt=response.get('message') or {}
    if receipt.get('superseded') is True:return False
    if receipt.get('durable') is not True or any(receipt.get(key)!=value for key,value in ack.items()):raise ValueError('Native control application acknowledgement differs')
    with ledger._transaction(db_path) as db:
        _schema(db)
        changed=db.execute("UPDATE governance_native_control SET phase='acknowledged' WHERE project=? AND task=? AND sequence=? AND command_id=?",(project,task,seq,command)).rowcount
    return changed==1

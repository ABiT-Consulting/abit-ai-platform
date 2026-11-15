"""A technical preflight failure is retryable only with proof no work was sent."""

import time
from open_webui.utils import agentic_governance as ledger


def block(invocation, reason, db_path=None):
    with ledger._transaction(db_path) as db:
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation,)).fetchone()
        if not row or row['state'] != 'queued' or row['submitted_at'] is not None or row['automation_run_id']:
            return False
        db.execute(
            "UPDATE governance_invocation SET state='blocked',reason=?,updated_at=? WHERE invocation_id=?",
            (str(reason)[:500], int(time.time()), invocation),
        )
        ledger._release(db, row)
        ledger._event(
            db,
            row['erp_project'],
            invocation,
            'unsubmitted_preflight_blocked',
            'executor',
            {'reason': str(reason)[:500]},
        )
        return True


def rearm(invocation, packet, gate, actor, db_path=None):
    with ledger._transaction(db_path) as db:
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation,)).fetchone()
        if (
            not row
            or row['state'] != 'blocked'
            or row['submitted_at'] is not None
            or row['automation_run_id']
            or row['packet_hash'] != ledger.work_packet_digest(packet)
        ):
            raise ValueError('Retry requires the unchanged original packet and proof it was never submitted')
        ledger._gate(db, row['erp_project'], gate)
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        retries = db.execute(
            "SELECT COUNT(*) FROM governance_event WHERE invocation_id=? AND kind='original_unsubmitted_rearmed'",
            (invocation,),
        ).fetchone()[0]
        claimed = db.execute(
            "SELECT 1 FROM governance_event WHERE invocation_id=? AND kind='execution_claimed' LIMIT 1",
            (invocation,),
        ).fetchone()
        if claimed or row['dispatch_attempts'] not in {0, retries}:
            raise ValueError('Original preflight history does not prove it was never claimed')
        if retries >= 3:
            raise ValueError('Original preflight retry limit reached')
        fence = ledger._acquire(db, row['erp_project'], row['erp_task'], invocation)
        db.execute(
            "UPDATE governance_invocation SET state='queued',lease_fence=?,reason=NULL,dispatch_attempts=0,updated_at=? WHERE invocation_id=?",
            (fence, int(time.time()), invocation),
        )
        ledger._event(db, row['erp_project'], invocation, 'original_unsubmitted_rearmed', actor,
                      {'fence': fence, 'preflight_attempt': retries + 1,
                       'previous_dispatch_attempts': row['dispatch_attempts'], 'dispatch_attempts': 0})
    return ledger.find(invocation, db_path)

"""Reconcile a completed original Dot post without submitting another prompt."""
import json
import os
from pathlib import Path
import re

import aiohttp
from fastapi import HTTPException
from sqlalchemy import select


async def observe_original(envelope):
    from open_webui.routers.chatgpt_relay import MESSAGE_URL, MESSAGE_TOKEN_FILE, _credentials
    from open_webui.utils.abitdot_primary_relay import (
        relay_account_for_owner, validated_local_relay_url, verified_dot_payload, receipt_from_relay,
    )
    base = validated_local_relay_url(os.getenv('ABIT_DOT_PRIMARY_RELAY_URL') or MESSAGE_URL)
    token_path = os.getenv('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE')
    if token_path and Path(token_path).resolve() != Path('/srv/abit-dev-linux/relay-runtime/secrets/relay.token'):
        raise HTTPException(409, 'The configured Dot relay credential path is not approved.')
    token, _ = _credentials(Path(token_path) if token_path else MESSAGE_TOKEN_FILE)
    account = await relay_account_for_owner(envelope['owner_id'])
    payload = verified_dot_payload(envelope, account)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35), trust_env=False) as client:
        async with client.post(base + '/dot/observe', json=payload,
                               headers={'Authorization': 'Bearer ' + token}, allow_redirects=False) as response:
            if response.status != 200:
                return None, None
            data = await response.json(content_type=None)
    if (not isinstance(data, dict) or data.get('read_only') is not True
            or data.get('owner') != envelope['owner_id'] or data.get('job_id') != payload['job_id']
            or not data.get('root_message_id') or not data.get('reply_message_id')):
        return None, None
    receipt = receipt_from_relay(envelope, data, account)
    return (receipt, data) if receipt.get('status') == 'accepted' else (None, None)


async def reconcile_dot_run(automation, owner_id, run_id):
    from open_webui.env import DATA_DIR
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.automations import AutomationRun
    from open_webui.utils.abitdot_primary import ledger, record_receipt
    from open_webui.utils.erp_dot_execution import save_dot_reply
    from open_webui.utils.erp_task_resolution import (
        ERPClient, AGENT_URL, agent_url, task_audit, task_revision,
    )
    if (automation.user_id != owner_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', run_id)
            or (automation.meta or {}).get('erp_standard_agent') is not True):
        raise HTTPException(403, 'Only the owning ERP agent can reconcile its Dot run.')
    path = Path(DATA_DIR) / 'abitdot-primary.sqlite'
    if not path.is_file():
        raise HTTPException(404, 'No original Dot run is available.')
    with ledger(path) as db:
        row = db.execute('SELECT * FROM dot_primary_runs WHERE owner=? AND automation_id=? AND original_run_id=?',
                         (owner_id, automation.id, run_id)).fetchone()
        record = dict(row) if row else None
    if record is None:
        raise HTTPException(404, 'The original owned Dot run was not found.')
    async with get_async_db_context() as db:
        run = await db.get(AutomationRun, run_id)
        latest = await db.scalar(select(AutomationRun.id).where(AutomationRun.automation_id == automation.id)
                                 .order_by(AutomationRun.created_at.desc()).limit(1))
        if not run or run.automation_id != automation.id or latest != run_id:
            raise HTTPException(409, 'A different run owns the current ERP execution; no state changed.')
        if run.status == 'success' and run.chat_id:
            return {'automation_id': automation.id, 'run_id': run_id, 'state': 'already_reconciled', 'chat_id': run.chat_id}
        if run.status == 'running':
            return {'automation_id': automation.id, 'run_id': run_id, 'state': 'pending'}
    envelope = json.loads(record['envelope_json'])
    config = json.loads(Path(os.environ['ABIT_ERP_TASK_RESOLUTION_CONFIG']).read_text())
    erp = ERPClient(config['erp_token_file'])
    try:
        task = await erp.get('Task', automation.meta['erp_task'])
        if (task.get(AGENT_URL) != agent_url(automation.id) or task['status'] != 'Working'
                or task_audit(task).get('run_id') != run_id
                or task_revision(task) != automation.meta.get('erp_task_revision') or not await erp.assigned(task)):
            raise HTTPException(409, 'The ERP task or its ownership changed; original evidence was preserved.')
        receipt, observed = await observe_original(envelope)
        if not receipt:
            return {'automation_id': automation.id, 'run_id': run_id, 'state': 'pending'}
        fresh = await erp.get('Task', task['name'])
        if fresh['modified'] != task['modified']:
            raise HTTPException(409, 'The ERP task changed during readback; no outcome was applied.')
        with ledger(path) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT status,envelope_json FROM dot_primary_runs WHERE id=? AND owner=?',
                                 (record['id'], owner_id)).fetchone()
            if not current or current['envelope_json'] != record['envelope_json']:
                raise HTTPException(409, 'The original Dot claim changed during readback.')
            if current['status'] != 'delegated':
                if current['status'] not in {'unknown', 'unavailable'}:
                    raise HTTPException(409, 'The original Dot delivery still owns an active claim.')
                db.execute("UPDATE dot_primary_runs SET status='dispatching' WHERE id=?", (record['id'],))
                outcome = record_receipt(db, envelope, receipt)
                if outcome['state'] != 'delegated':
                    raise HTTPException(409, 'The original Dot response did not verify.')
            record = dict(db.execute('SELECT * FROM dot_primary_runs WHERE id=?', (record['id'],)).fetchone())
        record['root_message_id'] = observed['root_message_id']
        record['reply_message_id'] = observed['reply_message_id']
        chat_id = await save_dot_reply(automation, run_id, envelope['task']['original_prompt'], record)
        async with get_async_db_context() as db:
            run = await db.get(AutomationRun, run_id)
            if run.status not in {'deferred', 'error'} or run.automation_id != automation.id:
                raise HTTPException(409, 'The run outcome changed during reconciliation.')
            run.status, run.chat_id, run.error, run.lease_until = 'success', chat_id, None, None
            await db.commit()
        return {'automation_id': automation.id, 'run_id': run_id, 'state': 'reconciled', 'chat_id': chat_id,
                'root_message_id': observed['root_message_id'], 'reply_message_id': observed['reply_message_id']}
    finally:
        await erp.client.aclose()

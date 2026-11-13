"""Read the original relay/tool receipt; never issue another model or host action."""

from __future__ import annotations
from open_webui.utils.agentic_bridge import same_business_context
import json
import os
from pathlib import Path

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import digest


async def observe_original(envelope):
    import aiohttp
    from open_webui.routers.chatgpt_relay import MESSAGE_URL, MESSAGE_TOKEN_FILE, _credentials
    from open_webui.utils.abitdot_primary_relay import (
        relay_account_for_owner,
        validated_local_relay_url,
        verified_dot_payload,
        receipt_from_relay,
    )

    base = validated_local_relay_url(os.getenv('ABIT_DOT_PRIMARY_RELAY_URL') or MESSAGE_URL)
    token_path = os.getenv('ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE')
    if token_path and Path(token_path).resolve() != Path('/srv/abit-dev-linux/relay-runtime/secrets/relay.token'):
        raise ValueError('Configured relay credential path is not approved')
    token, _ = _credentials(Path(token_path) if token_path else MESSAGE_TOKEN_FILE)
    account = await relay_account_for_owner(envelope['owner_id'])
    payload = verified_dot_payload(envelope, account)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35), trust_env=False) as client:
        async with client.post(
            base + '/dot/observe', json=payload, headers={'Authorization': 'Bearer ' + token}, allow_redirects=False
        ) as response:
            if response.status != 200:
                return None, None
            data = await response.json(content_type=None)
    if (
        not isinstance(data, dict)
        or data.get('read_only') is not True
        or data.get('owner') != envelope['owner_id']
        or data.get('job_id') != payload['job_id']
        or not data.get('root_message_id')
        or not data.get('reply_message_id')
    ):
        return None, None
    receipt = receipt_from_relay(envelope, data, account)
    return (receipt, data) if receipt.get('status') == 'accepted' else (None, None)


async def read_original(row, automation):
    from open_webui.env import DATA_DIR
    from open_webui.utils.abitdot_primary import ledger as dot_ledger, record_receipt, dot_primary_key
    from open_webui.utils.erp_dot_execution import save_dot_reply

    path = Path(DATA_DIR) / 'abitdot-primary.sqlite'
    if not path.is_file():
        return None
    with dot_ledger(path) as db:
        original = db.execute(
            'SELECT * FROM dot_primary_runs WHERE owner=? AND automation_id=? AND original_run_id=?',
            (automation.user_id, row['agent_id'], row['automation_run_id']),
        ).fetchone()
        original = dict(original) if original else None
    if not original:
        return None
    envelope = json.loads(original['envelope_json'])
    meta = envelope.get('task', {}).get('meta') or {}
    if (
        envelope.get('owner_id') != automation.user_id
        or envelope.get('automation_id') != row['agent_id']
        or envelope.get('run_id') != row['automation_run_id']
        or envelope.get('idempotency_key')
        != dot_primary_key(automation.user_id, row['agent_id'], row['automation_run_id'])
        or meta.get('governance_managed') is not True
        or meta.get('erp_task') != row['erp_task']
        or meta.get('erp_role') != row['target_role']
    ):
        raise ValueError('Original relay request does not match this project specialist and Task')
    if original['status'] != 'delegated' or not original.get('response_text'):
        receipt, observed = await observe_original(envelope)
        if not receipt:
            return None
        with dot_ledger(path) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute(
                'SELECT status,envelope_json FROM dot_primary_runs WHERE id=?', (original['id'],)
            ).fetchone()
            if not current or current['envelope_json'] != original['envelope_json']:
                raise ValueError('Original relay ownership changed during receipt observation')
            # This update only records a verified read-only receipt. It never
            # calls deliver/claim/send or permits a replacement upstream job.
            if current['status'] not in {'unknown', 'unavailable', 'dispatching', 'delegated'}:
                raise ValueError('Original relay was not submitted')
            db.execute("UPDATE dot_primary_runs SET status='dispatching' WHERE id=?", (original['id'],))
            outcome = record_receipt(db, envelope, receipt)
            if outcome['state'] != 'delegated':
                raise ValueError('Original relay response did not verify')
            original = dict(db.execute('SELECT * FROM dot_primary_runs WHERE id=?', (original['id'],)).fetchone())
        original['root_message_id'] = observed['root_message_id']
        original['reply_message_id'] = observed['reply_message_id']
    await save_dot_reply(automation, row['automation_run_id'], envelope['task']['original_prompt'], original)
    return original


async def recover_original(
    invocation, *, config, project_gate, bridge, app=None, db_path=None, reader=None, adapter=None, automation=None
):
    from open_webui.utils.agentic_executor import complete_original, reconcile_publication, DotAdapter
    from open_webui.utils.agentic_result_store import get_result

    row = ledger.find(invocation, db_path, include_private=True)
    if not row:
        return None
    saved = get_result(invocation, db_path)
    if saved:
        # Read-only receipt recovery remains possible after revocation. The
        # caller must re-gate before it dispatches any successor work.
        if saved['phase'] == 'pending':
            from open_webui.utils.agentic_controls import task_control

            if (
                row['state'] not in {'running', 'uncertain', 'execution_finished'}
                or ledger.project_control(row['erp_project'], db_path=db_path)['state'] != 'enabled'
                or task_control(row['erp_project'], row['erp_task'], db_path=db_path)['state'] != 'enabled'
            ):
                return None
            await project_gate(row['erp_project'], config)
        return await reconcile_publication(invocation, bridge, db_path)
    if row['state'] not in {'uncertain', 'cancel_requested'} or not row['automation_run_id']:
        return None
    if row['target_role'] == 'it_manager':
        # The trusted action journal, rather than a Dot session, owns IT.
        return None
    from open_webui.utils.agentic_bridge import stable_run_id

    if row['automation_run_id'] != stable_run_id(invocation):
        raise ValueError('Original run identity changed; recovery cannot replace it')
    if automation is None:
        from open_webui.models.automations import Automations

        automation = await Automations.get_by_id(row['agent_id'])
    registered = next((v for v in ledger.roster(row['erp_project'], db_path) if v['agent_id'] == row['agent_id']), None)
    meta = (automation.meta or {}) if automation else {}
    if (
        not automation
        or not registered
        or automation.user_id != registered['owner_id']
        or meta.get('agentic_project') != row['erp_project']
        or meta.get('agentic_role') != row['target_role']
        or meta.get('erp_role_user') != registered['erp_user']
        or meta.get('governance_managed') is not True
    ):
        raise ValueError('Original specialist binding/owner changed')
    record = await (reader or read_original)(row, automation)
    if not record:
        if row['submitted_at'] is None and row['state'] == 'uncertain':
            return await resume_unsubmitted(
                row, automation, adapter or DotAdapter(app), bridge, project_gate, config, db_path
            )
        return None
    packet = json.loads(row['work_packet'])
    fresh = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    managed = {
        'source_modified',
        'packet_id',
        'content_sha256',
        'implementation_user',
        'implementation_run_id',
        'attachments',
    }
    if not same_business_context(packet, fresh, managed):
        raise ValueError('ERP inputs changed while the original response was uncertain; preserve original evidence')
    # ERP verifies the original frozen context and allows only original result
    # attachments, so additional human files cannot be hidden by this readback.
    if row['state'] != 'cancel_requested':
        await project_gate(row['erp_project'], config)
    receipt = {'modified': fresh['source_modified']}
    if packet.get('qa_review'):
        from open_webui.utils.agentic_start_recovery import original_start_receipt

        receipt = original_start_receipt(row, db_path)
        if receipt['modified'] != fresh['source_modified']:
            raise ValueError('PM feature Task changed since its original coordination start')
    return await complete_original(
        row,
        packet,
        record,
        adapter or DotAdapter(app),
        automation,
        bridge,
        receipt,
        project_gate,
        config,
        db_path,
    )


async def resume_unsubmitted(row, automation, adapter, bridge, project_gate, config, db_path=None):
    """Positive ERP start proof permits the SAME run's first model submission."""
    import asyncio
    from open_webui.utils.agentic_start_recovery import get_confirmed_start, reclaim_same
    from open_webui.utils.agentic_ownership import submission_guard
    from open_webui.utils.agentic_executor import submit_bound_original

    receipt = await get_confirmed_start(row, bridge, project_gate, config, db_path)
    if not receipt:
        return None
    packet = json.loads(row['work_packet'])
    claimed = None
    beat = None
    try:
        async with submission_guard(row, db_path):
            _, gate = await project_gate(row['erp_project'], config)
            claimed = reclaim_same(row['invocation_id'], receipt, gate=gate, db_path=db_path)
            if not claimed:
                return None
            if hasattr(adapter, 'recover_prepare'):
                automation = await adapter.recover_prepare(claimed, packet, row['automation_run_id'])

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                if not ledger.heartbeat(row['invocation_id'], row['automation_run_id'], row['lease_token'], db_path):
                    return

        beat = asyncio.create_task(heartbeat())
        return await submit_bound_original(
            claimed, packet, automation, adapter, bridge, receipt, project_gate, config, db_path
        )
    except BaseException:
        if claimed:
            ledger.finish_execution(
                row['invocation_id'],
                row['automation_run_id'],
                row['lease_token'],
                'uncertain',
                reason='Original pre-submission recovery interrupted; inspect original receipt without replay',
                db_path=db_path,
            )
        raise
    finally:
        if beat:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)

"""Own evidence bytes for a verified PM coordination turn, without plan grants."""
from __future__ import annotations

import json

from open_webui.utils import agentic_governance as ledger
from open_webui.utils import agentic_pm_review as review
from open_webui.utils import agentic_start_recovery as starts
from open_webui.utils import agentic_tool_gateway as tools
from open_webui.utils.agentic_bridge import digest, same_business_context

RECEIPT_RPC = '/api/method/abit_ai_assistant.agentic_control.get_result_receipt'


def require(value, message):
    if not value:
        raise ValueError(message)


def _local(db, row, packet):
    """Read trusted journals; packet.qa_review alone is never authority."""
    current = tools._owned(db, row['invocation_id'])
    require(
        current['target_role'] == 'project_manager'
        and all(current.get(key) == row.get(key) for key in
                ('erp_project', 'erp_task', 'target_role', 'agent_id', 'automation_run_id', 'lease_token', 'lease_fence'))
        and json.loads(current['work_packet']) == packet,
        'PM evidence requires its exact original active invocation and immutable packet',
    )
    binding, qa_review = packet.get('binding') or {}, packet.get('qa_review')
    require(
        binding.get('role') == 'project_manager' and binding.get('agent_id') == current['agent_id']
        and binding.get('project_id') == current['erp_project'] and isinstance(qa_review, dict)
        and 'read_project' in packet.get('tool_scopes', [])
        and not set(packet.get('tool_scopes', [])) - {'read_project', 'read_srs', 'review_evidence'},
        'PM review evidence cannot grant ordinary Task or planning capabilities',
    )
    tables = {item[0] for item in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    require(
        {'governance_pm_review', 'governance_pm_review_turn', 'governance_start'} <= tables,
        'PM evidence requires its existing verified coordination and native start journals',
    )
    for name in ('governance_local_result', 'governance_result'):
        require(name not in tables or not db.execute(
            'SELECT 1 FROM ' + name + ' WHERE invocation_id=?', (current['invocation_id'],)
        ).fetchone(), 'PM evidence closes after the original model result is saved')
    registered = db.execute(
        "SELECT agent_id,erp_user FROM governance_agent WHERE erp_project=? AND role='project_manager'",
        (current['erp_project'],),
    ).fetchone()
    require(
        registered and tuple(registered) == (current['agent_id'], binding.get('erp_user')),
        'PM evidence identity is not the current registered project manager',
    )
    _, verified = review._verified_source(db, qa_review.get('qa_invocation_id'))
    require(verified == qa_review, 'PM evidence source is not the exact verified QA publication')
    head = db.execute(
        'SELECT * FROM governance_pm_review WHERE qa_invocation_id=?', (qa_review['qa_invocation_id'],)
    ).fetchone()
    turn = db.execute(
        'SELECT * FROM governance_pm_review_turn WHERE review_invocation_id=?', (current['invocation_id'],)
    ).fetchone()
    require(
        head and turn and head['review_invocation_id'] == current['invocation_id']
        and (head['project'], head['task']) == (current['erp_project'], current['erp_task'])
        and head['phase'] == turn['phase'] == 'queued'
        and head['source_sha256'] == turn['source_sha256'] == digest(qa_review)
        and turn['qa_invocation_id'] == qa_review['qa_invocation_id']
        and current['invocation_id'] == review.review_id(
            current['erp_project'], current['erp_task'], qa_review, packet.get('pm_review_continuation')
        ),
        'PM evidence has no exact current registered coordination turn',
    )
    entry = db.execute(
        'SELECT * FROM governance_start WHERE invocation_id=?', (current['invocation_id'],)
    ).fetchone()
    require(starts._matching(current, entry) and entry['receipt_json'], 'PM evidence native original start is unconfirmed')
    starts._identity(current, json.loads(entry['event_json']))
    receipt = starts._receipt(entry, json.loads(entry['receipt_json']))
    return current, dict(entry), receipt


def _receipt_identity(receipt):
    return {key: receipt.get(key) for key in
            ('event_id', 'run_id', 'task_id', 'modified', 'status', 'durable', 'pm_review_state', 'artifact_target')}


async def write(args, *, row, packet, config, bridge, db_path=None, check_authority=None, **kwargs):
    """Write only this review's bounded artifact after exact local/native proof."""
    from open_webui.utils.agentic_gateway_operations import exact, _decode, _store_artifact

    exact(args, ('name', 'content_base64'))
    data = _decode(args['content_base64'])
    require(callable(check_authority) and bridge is not None,
            'PM review evidence requires the actual governed gateway authority callback')
    await check_authority()
    with ledger.connect(db_path) as db:
        current, entry, receipt = _local(db, row, packet)
    event = json.loads(entry['event_json'])
    response = await bridge.erp.request(
        'GET', RECEIPT_RPC,
        params={key: event[key] for key in ('event_id', 'project_id', 'task_id')},
        headers={'X-ABIT-Agentic-Bridge': bridge.secret},
    )
    original = response.get('message') if isinstance(response, dict) else None
    require(isinstance(original, dict) and original.get('exists') is True
            and original.get('content_sha256') == entry['event_sha256'],
            'Native PM review start journal is missing or differs from the original event')
    native = starts._receipt(entry, original)
    require(_receipt_identity(native) == _receipt_identity(receipt),
            'Native PM coordination start receipt differs from the original confirmed journal')
    fresh = await bridge.packet(current['erp_project'], current['erp_task'], 'project_manager', current['agent_id'])
    require(same_business_context(packet, fresh, {'packet_id', 'content_sha256'}),
            'PM review business context changed before evidence creation')
    await check_authority()
    # Serialize final ownership/result checks and local bytes against other Core
    # control/lease writers. An already accepted local operation may settle at a
    # safe checkpoint after a later external Stop; it never grants another action.
    with ledger._transaction(db_path) as db:
        final, final_entry, final_receipt = _local(db, row, packet)
        require(final_entry['event_sha256'] == entry['event_sha256']
                and _receipt_identity(final_receipt) == _receipt_identity(receipt),
                'PM review original authority changed before its artifact write')
        return _store_artifact(args['name'], data, row=final, config=config)

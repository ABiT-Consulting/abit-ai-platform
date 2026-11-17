"""Prepare and bind a fresh IT child before deriving its single invocation key."""

from __future__ import annotations

from copy import deepcopy
import re

from open_webui.utils.agentic_bridge import same_business_context
from open_webui.utils.agentic_it_changes import OPERATIONS, plan_authority_digest

RPC = '/api/method/abit_ai_assistant.agentic_it_plan.'


def require(value, message):
    if not value:
        raise ValueError(message)


async def bind_child_plan(packet, *, bridge, config, project_gate, db_path=None, runtime=None):
    """Never creates a Task/run, retries a POST, or replaces an existing plan."""
    template = packet.get('tool_action') or {}
    if packet.get('binding', {}).get('role') != 'it_manager' or template.get('operation') not in OPERATIONS:
        return packet
    args = {'project_id': packet['project_id'], 'task_id': packet['task_id']}
    headers = {'X-ABIT-Agentic-Bridge': bridge.secret}
    response = await bridge.erp.request('GET', RPC + 'get_it_plan_binding_receipt', params=args, headers=headers)
    receipt = response.get('message') or {}
    supplied = template.get('parameters', {}).get('plan_digest')
    if supplied:
        require(receipt.get('exists') is True and receipt.get('durable') is True
                and receipt.get('plan_digest') == supplied and receipt.get('bound_task_revision') == packet['task_revision'],
                'IT child has no exact original server plan binding receipt')
        return packet
    require(receipt.get('exists') is False, 'Original IT plan binding exists but the Task digest is absent')
    require(set(template.get('parameters', {})) == {'resource_id'}, 'Fresh physical child must select only its registered resource')
    from open_webui.utils import agentic_governance as ledger
    with ledger.connect(db_path) as db:
        prior = db.execute('SELECT 1 FROM governance_invocation WHERE erp_project=? AND erp_task=? LIMIT 1',
                           (packet['project_id'],packet['task_id'])).fetchone()
    require(prior is None, 'Existing IT invocation history requires original-run reconciliation; never replan automatically')
    if runtime is None:
        from open_webui.utils.agentic_it_broker import configured_runtime
        runtime = configured_runtime(config, bridge=bridge, project_gate=project_gate, db_path=db_path)
    require(runtime is not None, 'Physical IT plan preparation is not registered; keep the child held for reviewed adapter setup')
    plan = await runtime.prepare_binding(packet, config=config, bridge=bridge, project_gate=project_gate, db_path=db_path)
    require(isinstance(plan, dict) and plan.get('state') == 'prepared' and plan.get('executable') is True
            and plan.get('task_revision') == packet['task_revision'] and plan.get('plan_digest') == plan_authority_digest(plan),
            'Physical child preparation did not produce a complete original plan')
    body = {**args, 'expected_modified': packet['source_modified'], 'expected_task_revision': packet['task_revision'],
            'expected_project_revision': packet['project_revision'], 'plan': plan}
    try:
        response = await bridge.erp.request('POST', RPC + 'bind_it_plan', json=body, headers=headers)
        receipt = response.get('message') or {}
    except Exception:
        # The original POST may have committed. GET is the only recovery action.
        response = await bridge.erp.request('GET', RPC + 'get_it_plan_binding_receipt', params=args, headers=headers)
        receipt = response.get('message') or {}
    require(receipt.get('exists') is True and receipt.get('durable') is True
            and receipt.get('plan_digest') == plan['plan_digest']
            and receipt.get('original_task_revision') == packet['task_revision'],
            'Original IT plan binding outcome is unresolved; no invocation may be enqueued')
    fresh = await bridge.packet(packet['project_id'], packet['task_id'], 'it_manager', packet['binding']['agent_id'])
    expected = deepcopy(packet)
    expected['tool_action']['parameters']['plan_digest'] = plan['plan_digest']
    # Only server-certified modified/revision/hash fields change with this CAS.
    for key in ('source_modified', 'task_revision', 'content_sha256'):
        expected[key] = fresh.get(key)
    require(receipt.get('bound_task_revision') == fresh['task_revision']
            and receipt.get('modified') == fresh['source_modified']
            and re.fullmatch(r'[a-f0-9]{64}', str(receipt.get('bound_task_revision', '')))
            and same_business_context(expected, fresh), 'IT child changed during server plan binding')
    return fresh

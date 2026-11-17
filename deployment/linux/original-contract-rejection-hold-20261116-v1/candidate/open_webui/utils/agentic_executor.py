"""Governed original-run execution. ERP publication and model submission are separate.

A saved reply can be published/reconciled repeatedly without another model call.
Every dispatch uses a project gate, task fence, immutable packet and original ID.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import ERPBridge, digest, packet_digest, stable_run_id
from open_webui.utils.agentic_tool_gateway import ApprovalRequired
from open_webui.utils.agentic_result_store import (
    get_result,
    save_result,
    save_local_result,
    publication_attempt,
    publication_receipt,
)

log = logging.getLogger(__name__)
OUTCOMES = {
    'continue': 'run.checkpoint',
    'ready_for_review': 'run.ready',
    'pass': 'qa.passed',
    'fail': 'qa.rejected',
    'blocked': 'run.blocked',
}


def implementation_source(row, packet, db_path=None):
    run = packet.get('implementation_run_id')
    if run:
        with ledger.connect(db_path) as db:
            candidates = db.execute(
                "SELECT invocation_id FROM governance_invocation WHERE erp_project=? AND erp_task=? AND automation_run_id=? AND target_role IN ('developer','operations','it_manager')",
                (row['erp_project'], row['erp_task'], run),
            ).fetchall()
        if len(candidates) != 1:
            raise ValueError('QA requires the exact current implementation run')
        source = ledger.find(candidates[0][0], db_path, include_private=True)
    else:
        source = ledger.find(row.get('source_invocation_id') or '', db_path, include_private=True)
    if (
        not source
        or source['target_role'] not in {'developer', 'operations', 'it_manager'}
        or source['erp_project'] != row['erp_project']
        or source['erp_task'] != row['erp_task']
        or source['state'] not in {'execution_finished', 'handed_off', 'verified'}
    ):
        raise ValueError('Independent QA has no current implementation predecessor')
    return source


class OriginalResultContractError(ValueError):
    """Only a pure original-reply parse or result-contract rejection."""

    def __init__(self, message, *, stage):
        super().__init__(message)
        self.stage = stage


def _decode_original_result(text, packet, invocation_id, *, registered_bindings=None, original_row=None,
                            architecture_review=None, registered_blocked_tests=frozenset()):
    from open_webui.utils.agentic_contracts.validator import validate_result
    from open_webui.utils.agentic_result_provenance import (
        parse_original_reply, project_provenance, reply_sha256,
    )

    try:
        result = parse_original_reply(text)
    except ValueError as error:
        raise OriginalResultContractError(str(error), stage='parse') from error
    projection = None
    if original_row is not None:
        result, projection = project_provenance(
            result, row=original_row, packet=packet, raw_reply_sha256=reply_sha256(text),
        )
    errors = validate_result(result, packet, registered_bindings=registered_bindings,
                             architecture_review=architecture_review,
                             registered_blocked_tests=registered_blocked_tests)
    if errors:
        raise OriginalResultContractError('; '.join(errors)[:2000], stage='contract')
    if result.get('architect_plan'):
        for item in result['architect_plan']['designs'] + result['architect_plan']['mockups']:
            artifact = item['artifact']
            if not any(
                e['uri'] == artifact['uri']
                and e['sha256'] == artifact['sha256']
                and set(item['requirement_ids']) <= set(e['requirement_ids'])
                for e in result['evidence']
            ):
                raise OriginalResultContractError('Every design/mockup must reference real verified evidence bytes', stage='contract')
    if result['invocation_id'] != invocation_id:
        raise OriginalResultContractError('Returned invocation identity differs from original request', stage='contract')
    return result, projection


def decode_result(text, packet, invocation_id, *, registered_bindings=None):
    """Strict original contract validation; no compatibility without server context."""
    return _decode_original_result(text, packet, invocation_id, registered_bindings=registered_bindings)[0]


def result_event(row, packet, result, run_id, artifacts, expected_modified, *, architecture_review=None):
    event_type = OUTCOMES.get(result['outcome'])
    if architecture_review is not None:
        from open_webui.utils.agentic_architecture_review import matches_phase
        if not matches_phase(architecture_review, result, packet):
            raise ValueError('Architecture review publication phase changed')
        event_type = 'run.ready'  # Planning readiness, never QA or completion.
    if packet.get('qa_review'):
        event_type = 'pm.reviewed' if result['outcome'] == 'reviewed' else 'pm.review.checkpoint'
    return {
        'schema_version': 1,
        'event_id': 'agov-result-' + digest([row['invocation_id'], result])[:48],
        'type': event_type,
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'run_id': run_id,
        'invocation_id': row['invocation_id'],
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'summary': result['summary'],
        'expected_modified': expected_modified,
        'expected_project_revision': packet['project_revision'],
        'implementation_run_id': packet.get('implementation_run_id'),
        'implementation_user': packet.get('implementation_user'),
        'artifacts': artifacts,
        'result_contract': result,
        'work_packet_digest': packet_digest(packet),
        'acceptance_passed': result['outcome'] == 'pass'
        and bool(result['test_results'])
        and all(v['status'] == 'pass' for v in result['test_results']),
    }


async def reconcile_publication(invocation_id, bridge, db_path=None):
    """Read original server receipt after an unknown ack; never resubmit a model."""
    saved = get_result(invocation_id, db_path)
    if not saved:
        return None
    if saved['phase'] == 'published':
        return saved['receipt']
    if saved['phase'] == 'uncertain':
        response = await bridge.erp.request(
            'GET',
            '/api/method/abit_ai_assistant.agentic_control.get_result_receipt',
            params={
                'event_id': saved['event']['event_id'],
                'project_id': saved['event']['project_id'],
                'task_id': saved['event']['task_id'],
            },
            headers={'X-ABIT-Agentic-Bridge': bridge.secret},
        )
        receipt = response.get('message')
        if not isinstance(receipt, dict) or receipt.get('exists') is not True:
            # An ambiguous negative readback cannot authorize retry. An operator
            # may investigate original ERP inbox; execution stays fenced.
            return None
        receipt = receipt.get('receipt', receipt)
        receipt = {k: v for k, v in receipt.items() if k not in {'exists', 'content_sha256'}}
    else:
        if not publication_attempt(invocation_id, db_path):
            return None
        receipt = await bridge.publish(saved['event'])
    publication_receipt(invocation_id, receipt, db_path)
    return receipt


class DotAdapter:
    """Reuse the existing owner/project-verified relay and its durable dedup ledger."""

    requires_governed_gateway = True

    def __init__(self, app):
        self.app = app

    async def scope_check(self, row, packet, run_id=None):
        from open_webui.utils.agentic_relay_scope import require_relay_scope

        registered = next((v for v in ledger.roster(row['erp_project']) if v['agent_id'] == row['agent_id']), None)
        if not registered:
            raise ValueError('No registered owner for this scoped relay invocation')
        return await require_relay_scope(row, packet, registered['owner_id'], run_id=run_id)

    async def preflight(self, row, packet):
        return await self.scope_check(row, packet)

    async def bound_automation(self, row, packet):
        from open_webui.models.automations import Automations

        automation = await Automations.get_by_id(row['agent_id'])
        binding = packet['binding']
        meta = (automation.meta or {}) if automation else {}
        registered = next((v for v in ledger.roster(row['erp_project']) if v['agent_id'] == row['agent_id']), None)
        if (
            not automation
            or not automation.is_active
            or not registered
            or automation.user_id != registered['owner_id']
            or meta.get('agentic_project') != row['erp_project']
            or meta.get('agentic_role') != row['target_role']
            or meta.get('erp_role_user') != binding['erp_user']
            or meta.get('governance_managed') is not True
        ):
            raise ValueError('Project specialist owner, role or ERP binding differs')
        return automation

    async def prepare(self, row, packet, run_id):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import AutomationRun

        automation = await self.bound_automation(row, packet)
        async with get_async_db_context() as db:
            prior = await db.get(AutomationRun, run_id)
            if prior:
                raise ValueError('Original execution already has a run; reconcile without replay')
            db.add(AutomationRun(id=run_id, automation_id=automation.id, status='running', created_at=time.time_ns()))
            await db.commit()
        return automation

    async def recover_prepare(self, row, packet, run_id):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import AutomationRun

        automation = await self.bound_automation(row, packet)
        async with get_async_db_context() as db:
            original = await db.get(AutomationRun, run_id)
            if not original or original.automation_id != automation.id or original.status != 'running':
                raise ValueError('Only the exact original running AutomationRun may resume before submission')
        return automation

    async def submit(self, row, packet, run_id, automation, bridge, *, before_submit):
        from open_webui.utils.erp_dot_execution import dispatch_task_dot, save_dot_reply
        from open_webui.utils.agentic_artifacts import artifact_directory, ensure_directory
        from open_webui.utils.agentic_relay_scope import proof_for_packet
        from open_webui.utils.agentic_gateway_contracts import developer_result_guidance

        # A supported never-submitted start recovery reuses this original run.
        # Its transient initial proof may have been lost across the restart;
        # reacquire an actual read-only proof, never reconstruct an attestation.
        preflight = row.get('_relay_scope_preflight')
        if preflight is None:
            preflight = await self.scope_check(row, packet, run_id)
        preflight = proof_for_packet(preflight, row, packet, automation.user_id, run_id)

        root = artifact_directory(row, bridge.config)
        ensure_directory(root)
        source = implementation_source(row, packet) if row['target_role'] == 'qa' else None
        implementation = (
            {'implementation_invocation_id': source['invocation_id'], 'implementation_agent_id': source['agent_id']}
            if source
            else {}
        )
        if packet.get('qa_review'):
            review = packet['qa_review']
            implementation = {key: review[key] for key in ('implementation_invocation_id', 'implementation_agent_id')}
        packet_reference = {
            'invocation_id': row['invocation_id'],
            'run_id': run_id,
            'project_id': packet['project_id'],
            'project_revision': packet['project_revision'],
            'task_id': packet['task_id'],
            'task_revision': packet['task_revision'],
            'actor': packet['binding'],
            'work_packet_digest': packet_digest(packet),
            'handoff_context_digest': digest(row['context']),
        }
        prompt = (
            'Execute this exact versioned ERP work packet in a separate abitdot request. '
            'Review and execute the assigned ' + row['target_role'] + ' action for ERP Task '
            'https://erp.abit.ai/desk/task/' + row['erp_task'] + '. '
            'FIRST call the existing agentic_governance_action with operation=context.read, arguments={}, '
            'the admitted session_id and a stable call_id. It returns the complete frozen work_packet, '
            'handoff_context, role result schemas and allowed operation catalogue. Verify the exact reference '
            'below, then follow that server-held context. ERP controls task changes, approvals and handoffs. '
            'Preserve original run ownership and evidence. Save real evidence under ' + str(root) + '. '
            'Return the specified AGOV RESULT JSON contract. Its invocation_id must be '
            + row['invocation_id'] + ' and work_packet_digest ' + packet_digest(packet) + '.\n'
            'EXACT IMPLEMENTATION IDENTITY: ' + json.dumps(implementation) + '\n'
            'IMMUTABLE GOVERNANCE CONTEXT REFERENCE: ' + json.dumps(packet_reference, ensure_ascii=False) + '\n'
        )
        developer_guidance = developer_result_guidance(row['target_role'])
        if developer_guidance:
            prompt += '\n' + developer_guidance
        if row['target_role'] == 'solution_architect':
            metadata_identity = {
                'project_id': row['erp_project'],
                'srs_version': packet['srs']['revision'],
                'srs_sha256': packet['srs']['sha256'],
                'source_url': 'https://erp.abit.ai/desk/project/' + row['erp_project'],
                'run_id': row['invocation_id'],
                'agent_binding': {
                    'project_id': row['erp_project'],
                    'role': row['target_role'],
                    'abit_agent_id': row['agent_id'],
                    'erp_ai_user': packet['binding']['erp_user'],
                },
            }
            prompt += (
                '\nARCHITECT PLAN METADATA IDENTITY: ' + json.dumps(metadata_identity, ensure_ascii=False) + '\n'
                'Copy every field above exactly into architect_plan.metadata. Its run_id is the governance '
                'invocation ID and its source_url is the canonical ERP Project URL. Keep this plan identity '
                'separate from the outer AutomationRun transport identity below. A Task URL or outer run ID '
                'in those metadata fields will fail validation.\n'
                'OUTER AUTOMATION RUN (transport only): ' + run_id + '\n'
                'ARCHITECT BACKLOG CONTRACT: Copy packet.project_backlog.context_sha256 into '
                'architect_plan.metadata.project_backlog_context_sha256 and '
                'packet.project_backlog.snapshot_sha256 into architect_plan.metadata.project_backlog_snapshot_sha256. '
                'Inspect every existing Project Task before proposing new work. Reuse existing work only through '
                'an exact existing_task_id and existing_task_revision from that authoritative snapshot. '
                'Existing references are read-only: preserve their parents, owners, status and content. '
                'Do not infer equivalence from similar titles. If work overlaps ambiguously, report a concrete '
                'reconciliation blocker before creating or assigning another Task.'
            )
        from open_webui.utils.agentic_architecture_review import architecture_context
        if architecture_context(row, packet):
            prompt += (
                '\nPM ARCHITECTURE REVIEW PHASE: Review the exact published Architect predecessor and '
                'its plan/evidence against this frozen SRS. A completed positive architecture review may return '
                'outcome reviewed with your real artifact.write review evidence, no test_results or QA claims, '
                'and null implementation identities. This is planning readiness only; it does not approve QA '
                'or complete the business Task. Use continue or blocked for unresolved planning issues. '
                'Do not call pm_review.write: that operation is only for a WorkPacket containing qa_review. '
                'Do not create Tasks, release work, invoke agents, or claim implementation tests yourself; '
                'the server validates and coordinates the exact existing plan.'
            )
        if packet.get('qa_review'):
            prompt += (
                '\nDEDICATED PM QA COORDINATION: The feature Task is already independently QA APPROVED. '
                'Review the exact QA contract, its private published evidence and the complete informational backlog. '
                'Do not reopen, modify, reassign, implement, create Tasks, invoke other agents or perform infrastructure actions. '
                'Save your own real review file in the run-owned artifact directory above. '
                'Return outcome reviewed only with qa_review_ack='
                + digest(packet['qa_review'])
                + ' and a passing PM-QA-REVIEW test referencing your own evidence. '
                'Copy the exact implementation_invocation_id and implementation_agent_id from qa_review. '
                'If you cannot acknowledge the actual QA evidence, use blocked or continue with a concrete checkpoint; '
                'a known published continue may receive at most three new logical coordination turns without replaying any original request. '
                'Blocked work requires human ERP Resume or Retry after resolving the checkpoint and clearing current vetoes. '
                'Downstream work stays held until the exact QA acknowledgment is reviewed. '
                'Do not include architect_plan, action_requests or developer delivery fields.'
            )
        task = await bridge.erp.get('Task', row['erp_task'])
        from urllib.parse import urlparse
        from open_webui.utils.erp_dot_execution import synced_task_project

        project = await bridge.erp.get('Project', row['erp_project'])
        folder = None
        if project.get('custom_chatgpt_chat_url'):
            import re

            url = urlparse(project['custom_chatgpt_chat_url'])
            match = re.match(r'^/g/(g-p-[a-f0-9]{32})(?:[-/]|$)', url.path)
            if url.scheme != 'https' or url.netloc != 'chatgpt.com' or not match:
                raise ValueError('Related ChatGPT Project URL is invalid')
            folder = await synced_task_project({'custom_chatgpt_project_id': match[1]}, automation.user_id, None)
        elif getattr(automation, 'folder_id', None):
            from open_webui.models.folders import Folders

            owned = await Folders.get_folder_by_id_and_user_id(automation.folder_id, automation.user_id)
            folder_data = owned.model_dump() if owned is not None else None
            if not folder_data or folder_data.get('user_id') != automation.user_id:
                raise ValueError('The automation ChatGPT Project has no verified owner folder')
            folder = await synced_task_project({}, automation.user_id, folder_data)
        elif os.environ.get('ABIT_AGENTIC_CHATGPT_PROJECT_ID'):
            folder = await synced_task_project(
                {'custom_chatgpt_project_id': os.environ['ABIT_AGENTIC_CHATGPT_PROJECT_ID']},
                automation.user_id,
                None,
            )
        record = await dispatch_task_dot(
            bridge.erp,
            task,
            automation.user_id,
            automation.id,
            run_id,
            prompt,
            row['target_role'],
            folder,
            before_submit=before_submit,
            relay_scope_preflight=preflight,
            governed_packet=packet,
            policy='Execute only this authenticated governance role and versioned packet. Return the AGOV RESULT contract requested below; ERP status, task creation, handoffs and approvals are handled by the governance server.',
        )
        if record is None:
            return None
        await save_dot_reply(automation, run_id, prompt, record)
        return record

    async def record(self, automation, run_id, record):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import AutomationRun
        from uuid import NAMESPACE_URL, uuid5

        chat_id = str(uuid5(NAMESPACE_URL, f'erp-dot:{automation.user_id}:{automation.id}:{run_id}'))
        async with get_async_db_context() as db:
            original = await db.get(AutomationRun, run_id)
            if (
                original
                and original.automation_id == automation.id
                and original.status == 'success'
                and original.chat_id == chat_id
            ):
                return
            if not original or original.automation_id != automation.id or original.status != 'running':
                raise ValueError('Original AutomationRun changed before response readback')
            original.status = 'success'
            original.chat_id = chat_id
            original.error = None
            if hasattr(original, 'lease_until'):
                original.lease_until = None
            await db.commit()


    async def reject_transport(self, automation, run_id, error):
        """Record a proven failed outgoing request, never a synthetic model reply."""
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import AutomationRun

        async with get_async_db_context() as db:
            original = await db.get(AutomationRun, run_id)
            if (not original or original.automation_id != automation.id or original.chat_id is not None
                or original.status not in {'running','error'}
                or (original.status == 'error' and original.error != error)):
                raise ValueError('Original AutomationRun changed before transport rejection')
            original.status='error'
            original.error=error
            original.lease_until=None
            await db.commit()


async def execute(invocation_id, *, project_gate, config, adapter, db_path=None, bridge=None):
    row = ledger.find(invocation_id, db_path, include_private=True)
    if not row or row['state'] != 'queued':
        return None
    owned_bridge = bridge is None
    bridge = bridge or ERPBridge(config)
    bridge.config = config
    claimed = None
    beat = None
    try:
        project, gate = await project_gate(row['erp_project'], config)
        packet = await bridge.packet(
            row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'], row['task_revision']
        )
        if packet.get('qa_review'):
            from open_webui.utils.agentic_bridge import same_business_context

            original_packet = json.loads(row['work_packet'])
            if not same_business_context(original_packet, packet, {'packet_id', 'content_sha256'}):
                raise ValueError('PM coordination source changed while queued')
            packet = original_packet
        from open_webui.utils.agentic_contracts.validator import validate_work_packet

        bindings = {
            v['role']: {'agent_id': v['agent_id'], 'erp_user': v['erp_user']}
            for v in ledger.roster(row['erp_project'], db_path)
        }
        errors = validate_work_packet(packet, registered_bindings=bindings)
        if errors:
            raise ValueError('; '.join(errors)[:2000])
        preflight = await adapter.preflight(row, packet) if hasattr(adapter, 'preflight') else None
        # Full snapshot at enqueue must still match, including user activity/SRS.
        if ledger.work_packet_digest(packet) != row['packet_hash']:
            raise ValueError('Work packet changed while queued; refresh from ERP before dispatch')
        from open_webui.utils.agentic_ownership import submission_guard

        async with submission_guard(row, db_path):
            project, gate = await project_gate(row['erp_project'], config)
            claimed = ledger.claim_invocation(invocation_id, stable_run_id(invocation_id), gate=gate, db_path=db_path)
            if claimed is None:
                return None
            run_id = claimed['automation_run_id']
            token = claimed['lease_token']
            if preflight is not None:
                # Private execution metadata; never changes the business packet
                # or the durable invocation's immutable packet hash.
                claimed['_relay_scope_preflight'] = json.loads(json.dumps(preflight))
            automation = await adapter.prepare(row, packet, run_id)

            async def heartbeat():
                while True:
                    await asyncio.sleep(30)
                    if not ledger.heartbeat(invocation_id, run_id, token, db_path):
                        return

            beat = asyncio.create_task(heartbeat())
            started = {
                'schema_version': 1,
                'event_id': 'agov-start-' + digest(invocation_id)[:48],
                'type': 'pm.review.started' if packet.get('qa_review') else 'run.started',
                'project_id': row['erp_project'],
                'task_id': row['erp_task'],
                'task_revision': packet['task_revision'],
                'run_id': run_id,
                'invocation_id': invocation_id,
                'role': row['target_role'],
                'agent_id': row['agent_id'],
                'summary': 'Original governed execution claimed.',
                'implementation_run_id': packet.get('implementation_run_id'),
                'previous_run_id': (ledger.find(row.get('source_invocation_id') or '', db_path) or {}).get(
                    'automation_run_id'
                ),
                'expected_modified': packet['source_modified'],
                'expected_project_revision': packet['project_revision'],
                'work_packet_digest': packet_digest(packet),
                'artifacts': [],
            }
            if packet.get('qa_review'):
                started['work_packet'] = packet
            # Unknown ERP start acknowledgment prevents any model submission.
            from open_webui.utils.agentic_start_recovery import record_start_intent, record_start_receipt

            record_start_intent(claimed, started, db_path)
            receipt = await bridge.publish(started)
            record_start_receipt(invocation_id, receipt, db_path)
            bridge.start_receipt = receipt
        return await submit_bound_original(
            claimed, packet, automation, adapter, bridge, receipt, project_gate, config, db_path
        )
    except ApprovalRequired as error:
        from open_webui.utils.agentic_it_execution import suspend

        return await suspend(invocation_id, packet, adapter, bridge, receipt, error, db_path)
    except asyncio.CancelledError:
        if claimed:
            ledger.finish_execution(
                invocation_id,
                claimed['automation_run_id'],
                claimed['lease_token'],
                'uncertain',
                reason='Service interrupted; reconcile original evidence',
                db_path=db_path,
            )
        raise
    except Exception as error:
        log.exception('Governance execution preserved its original request: %s', invocation_id)
        if claimed is None:
            from open_webui.utils.agentic_unsubmitted import block

            block(invocation_id, str(error), db_path)
        if claimed:
            ledger.finish_execution(
                invocation_id,
                claimed['automation_run_id'],
                claimed['lease_token'],
                'uncertain',
                reason=(type(error).__name__ + ': ' + str(error))[:500],
                db_path=db_path,
            )
        return None
    finally:
        if beat:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        if owned_bridge:
            await bridge.close()


async def complete_original(
    row, packet, record, adapter, automation, bridge, receipt, project_gate, config, db_path=None
):
    invocation_id = row['invocation_id']
    run_id = row['automation_run_id']
    token = row['lease_token']
    original = None
    if row['target_role'] == 'solution_architect' and getattr(adapter, 'requires_governed_gateway', False):
        from open_webui.utils.agentic_result_provenance import original_identity
        from open_webui.utils.agentic_result_store import assert_original_reply

        # Reload the private server ledger, not a request-provided provenance flag.
        original = ledger.find(invocation_id, db_path, include_private=True)
        if not original or any(original.get(key) != row.get(key) for key in (
                'automation_run_id', 'lease_token', 'lease_fence', 'agent_id',
                'erp_project', 'erp_task', 'target_role', 'task_revision', 'packet_hash')):
            raise ValueError('Original invocation changed before provenance validation')
        original_identity(original, packet)
        assert_original_reply(invocation_id, run_id, record['response_text'], db_path)
    architecture_review = None
    if row['target_role'] == 'project_manager':
        from open_webui.utils.agentic_architecture_review import derive_phase
        from open_webui.utils.agentic_result_provenance import parse_original_reply, reply_sha256
        from open_webui.utils.agentic_result_store import assert_original_reply

        assert_original_reply(invocation_id, run_id, record['response_text'], db_path)
        architecture_review = derive_phase(
            row, packet, parse_original_reply(record['response_text']), db_path=db_path,
            raw_reply_sha256=reply_sha256(record['response_text']),
        )
    bindings = {
        v['role']: {'agent_id': v['agent_id'], 'erp_user': v['erp_user']}
        for v in ledger.roster(row['erp_project'], db_path)
    }
    registered_blocked_tests = frozenset()
    if getattr(adapter, 'requires_governed_gateway', False):
        from open_webui.utils.agentic_project_profiles import resolve_repository_profile, ProfileUnavailable
        try:
            profile, _ = resolve_repository_profile(
                config, row['erp_project'], packet['repository'], packet['environment'])
            registered_blocked_tests = frozenset(profile['tests'])
        except ProfileUnavailable:
            pass  # No registration cannot authorize an unknown command diagnostic.
    try:
        result, provenance = _decode_original_result(
            record['response_text'], packet, invocation_id, registered_bindings=bindings,
            original_row=original, architecture_review=architecture_review,
            registered_blocked_tests=registered_blocked_tests,
        )
    except OriginalResultContractError as error:
        if row['target_role'] != 'developer':
            raise
        from open_webui.utils.agentic_result_store import hold_contract_rejection

        # An accepted transport reply is not a valid result or terminal authority.
        # The original provider bytes, AutomationRun and both ownership fences stay held.
        hold_contract_rejection(row, packet, record, automation, error, db_path)
        return None
    save_local_result(invocation_id, run_id, result, db_path,
                      provenance=provenance, raw_reply=record['response_text'],
                      architecture_review=architecture_review)
    if getattr(adapter, 'requires_governed_gateway', False):
        from open_webui.utils.agentic_gateway_delivery import assert_gateway_result

        await assert_gateway_result(row, packet, result, config, db_path=db_path, bridge=bridge)
    if row['target_role'] == 'qa':
        source = implementation_source(row, packet, db_path)
        if (
            not source
            or result.get('implementation_invocation_id') != source['invocation_id']
            or result.get('implementation_agent_id') != source['agent_id']
        ):
            raise ValueError('QA verdict does not name the exact original implementation predecessor')
    from open_webui.utils.agentic_artifacts import publish_artifacts, verify_developer_manifest

    verify_developer_manifest(row, config, result)

    async def publication_gate():
        from open_webui.utils.agentic_controls import task_control

        current = ledger.find(invocation_id, db_path)
        if (
            not current
            or current['state'] not in {'running', 'uncertain'}
            or ledger.project_control(row['erp_project'], db_path=db_path)['state'] != 'enabled'
            or task_control(row['erp_project'], row['erp_task'], db_path=db_path)['state'] != 'enabled'
        ):
            raise ValueError('Original reply preserved locally; current ERP control prevents new writes')
        await project_gate(row['erp_project'], config)

    try:
        await publication_gate()
    except Exception:
        ledger.finish_execution(
            invocation_id,
            run_id,
            token,
            'uncertain',
            reason='Original reply preserved locally pending current ERP authorization',
            db_path=db_path,
        )
        return None
    target = None
    if packet.get('qa_review'):
        from open_webui.utils.agentic_pm_review import artifact_target

        target = artifact_target(row, receipt)
    artifacts = await publish_artifacts(
        bridge.erp,
        row,
        config,
        result['evidence'],
        before_write=publication_gate,
        attachment_target=target,
        bridge=bridge,
    )
    # Artifact publication verifies the actual bytes and reuses exact same-ID
    # receipts. Only then may the original turn succeed, before the business result.
    await adapter.record(automation, run_id, record)
    event = result_event(row, packet, result, run_id, artifacts, receipt['modified'],
                         architecture_review=architecture_review)
    save_result(invocation_id, run_id, result, event, db_path)
    qa = result['outcome'] if row['target_role'] == 'qa' and result['outcome'] in {'pass', 'fail', 'blocked'} else None
    ledger.finish_execution(invocation_id, run_id, token, 'execution_finished', qa_verdict=qa, db_path=db_path)
    current = ledger.find(invocation_id, db_path)
    if current['state'] != 'execution_finished':
        return None  # Stop/pause never fabricates approval.
    await project_gate(row['erp_project'], config)
    return await reconcile_publication(invocation_id, bridge, db_path)


async def submit_bound_original(
    claimed, packet, automation, adapter, bridge, receipt, project_gate, config, db_path=None
):
    row = claimed
    invocation_id = row['invocation_id']
    run_id = row['automation_run_id']
    token = row['lease_token']

    async def before_submit():
        from open_webui.utils.agentic_ownership import submission_guard

        async with submission_guard(claimed, db_path):
            project, gate = await project_gate(row['erp_project'], config)
            current = ledger.find(invocation_id, db_path, include_private=True)
            control = ledger.project_control(row['erp_project'], db_path=db_path)
            from open_webui.utils.agentic_controls import task_control

            task_mode = task_control(row['erp_project'], row['erp_task'], db_path=db_path)['state']
            if (
                task_mode != 'enabled'
                or not current
                or current['state'] != 'running'
                or current['lease_token'] != token
                or current['automation_run_id'] != run_id
                or control['state'] != 'enabled'
                or current['lease_expires_at'] < int(time.time())
            ):
                raise ValueError('Execution was paused, stopped or superseded before submission')
            fresh = await bridge.packet(
                row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'], packet['task_revision']
            )
            managed = {'source_modified', 'packet_id', 'content_sha256', 'implementation_user', 'implementation_run_id'}
            from open_webui.utils.agentic_bridge import same_business_context

            if not same_business_context(packet, fresh, managed):
                raise ValueError('Work packet changed before actual relay submission')
            if fresh.get('source_modified') != receipt['modified']:
                raise ValueError('Task changed after original claim; no relay submission')
            proof = await adapter.scope_check(current, packet, run_id) if hasattr(adapter, 'scope_check') else None
            if current['submitted_at'] is None and not ledger.mark_submitted(
                invocation_id, run_id, token, gate=gate, db_path=db_path
            ):
                raise ValueError('Original submission fence was revoked')
            return json.loads(json.dumps(proof)) if proof is not None else None

    record = await adapter.submit(claimed, packet, run_id, automation, bridge, before_submit=before_submit)
    if not record:
        ledger.finish_execution(
            invocation_id,
            run_id,
            token,
            'uncertain',
            reason='Original relay delivery unresolved; no replay',
            db_path=db_path,
        )
        return None
    return await complete_original(
        claimed, packet, record, adapter, automation, bridge, receipt, project_gate, config, db_path
    )

"""Durable event coordination; no timer and no implicit replay of submitted work."""

from __future__ import annotations

import json
from open_webui.utils import agentic_governance as ledger
from open_webui.utils import agentic_controls as controls
from open_webui.utils.agentic_bridge import digest, RPC


async def process_event(
    event_id, event, *, config, project_gate, provision, bridge_factory, dispatch, scan, recover, db_path=None
):
    if isinstance(event, str):
        event = json.loads(event)
    project = event['project']
    payload = event.get('payload') or {}
    kind = event['type']
    action = payload.get('action')
    task = event.get('task') or payload.get('task_id') or None
    actor = str(payload.get('actor') or 'signed-erp-event')[:254]

    def ack():
        ledger.acknowledge_event(event_id, project, actor=actor, db_path=db_path)

    if kind == 'project.control' and action in {'pause', 'stop'}:
        if task:
            controls.task_control(project, task, action, actor=actor, db_path=db_path)
        else:
            ledger.project_control(project, action, actor=actor, policy_revision=event['revision'], db_path=db_path)
            if action == 'pause':
                controls.freeze_queued(project, actor=actor, db_path=db_path)
        ack()
        return

    try:
        _, gate = await project_gate(project, config)
    except Exception as error:
        if getattr(error, 'status_code', None) not in {403, 409}:
            raise
        mode = ledger.project_control(project, db_path=db_path)
        if mode['state'] not in {'paused', 'stopped'} or mode.get('actor') == 'erp-revocation':
            ledger.project_control(
                project, 'pause', actor='erp-revocation', policy_revision=event['revision'], db_path=db_path
            )
        controls.freeze_queued(project, actor='erp-revocation', db_path=db_path)
        ack()
        return

    mode = ledger.project_control(project, db_path=db_path)
    if kind == 'project.changed' or (kind == 'project.control' and action == 'start' and not task):
        # Enroll before asking ERP to create/reuse its SRS architecture anchor.
        # Human Pause/Stop survives opt-in, SRS edits and registry reconciliation.
        await provision(project)
        mode = ledger.project_control(project, db_path=db_path)
        explicit_start = kind == 'project.control' and action == 'start'
        if explicit_start or mode['state'] == 'disabled' or mode.get('actor') == 'erp-revocation':
            ledger.project_control(project, 'enable', actor=actor, policy_revision=gate['revision'], db_path=db_path)
        if ledger.project_control(project, db_path=db_path)['state'] != 'enabled':
            ack()
            return
        bridge = bridge_factory(config)
        try:
            response = await bridge.erp.request(
                'POST',
                RPC + 'prepare_project_start',
                json={'project_id': project},
                headers={'X-ABIT-Agentic-Bridge': bridge.secret},
            )
            seed = response.get('message') or {}
            task = seed.get('task_id')
            if not task:
                raise ValueError('ERP did not confirm its original architecture anchor')
            packet = await bridge.packet(
                project, task, 'solution_architect', ledger.agent_id(project, 'solution_architect')
            )
            ident = 'agov-architecture-' + digest([project, task, packet['task_revision']])[:48]
            # A replay after handoff/completion acknowledges the original seed.
            existing = ledger.find(ident, db_path)
            if existing is None:
                row, created = ledger.enqueue(
                    invocation_id=ident,
                    project=project,
                    task=task,
                    from_role='erp',
                    target_role='solution_architect',
                    task_revision=packet['task_revision'],
                    context='Analyze this exact ERP SRS and produce real designs and a bounded acceptance-tested backlog.',
                    work_packet=packet,
                    gate=gate,
                    actor=actor,
                    db_path=db_path,
                )
                if created:
                    await dispatch(row['invocation_id'])
        finally:
            await bridge.close()
        ack()
        return

    if kind == 'project.control' and action in {'start', 'resume'}:
        if task:
            controls.task_control(project, task, action, actor=actor, db_path=db_path)
        else:
            ledger.project_control(project, 'resume', actor=actor, policy_revision=gate['revision'], db_path=db_path)
        bridge = bridge_factory(config)
        try:
            for ident in controls.waiting(project, task, db_path):
                row = ledger.find(ident, db_path, include_private=True)
                packet = await bridge.packet(project, row['erp_task'], row['target_role'], row['agent_id'])
                if controls.resume_controlled(ident, packet, gate, actor, db_path):
                    await dispatch(ident)
        finally:
            await bridge.close()

    if ledger.project_control(project, db_path=db_path)['state'] != 'enabled':
        ack()
        return
    # Results, approvals and retries always inspect the original journal first.
    # They never create a replacement run for a possibly submitted request.
    if kind == 'result.recorded' or (kind == 'project.control' and action in {'retry', 'start', 'resume'}):
        await recover(project, task, action == 'retry')
    if kind == 'project.control' and action == 'start' and task:
        with ledger.connect(db_path) as db:
            current = db.execute(
                "SELECT 1 FROM governance_invocation WHERE erp_project=? AND erp_task=? AND state IN ('queued','running','uncertain','cancel_requested','execution_finished','awaiting_approval') LIMIT 1",
                (project, task),
            ).fetchone()
        if not current:
            bridge = bridge_factory(config)
            try:
                response = await bridge.erp.request(
                    'POST',
                    RPC + 'prepare_task_start',
                    json={'project_id': project, 'task_id': task},
                    headers={'X-ABIT-Agentic-Bridge': bridge.secret},
                )
                prepared = response.get('message') or {}
                target = prepared.get('target_role')
                if target not in {'project_manager', 'solution_architect'}:
                    raise ValueError('Task start requires its ERP-confirmed architecture anchor or atomic PM review')
                packet = await bridge.packet(project, task, target, ledger.agent_id(project, target))
                if target == 'solution_architect':
                    ident = 'agov-architecture-' + digest([project, task, packet['task_revision']])[:48]
                    context = (
                        'Analyze this exact ERP SRS and produce real designs and a bounded acceptance-tested backlog.'
                    )
                else:
                    adoption = packet.get('atomic_adoption') or {}
                    if not adoption.get('adoption_id'):
                        raise ValueError('Task start requires its explicit immutable human adoption receipt')
                    ident = (
                        'agov-adoption-'
                        + digest([project, task, packet['task_revision'], adoption['adoption_id']])[:48]
                    )
                    context = 'Review this explicitly adopted atomic Task and delegate only its bound accountable executor. Preserve the exact human acceptance scope.'
                if not ledger.find(ident, db_path):
                    row, created = ledger.enqueue(
                        invocation_id=ident,
                        project=project,
                        task=task,
                        from_role='erp',
                        target_role=target,
                        task_revision=packet['task_revision'],
                        context=context,
                        work_packet=packet,
                        gate=gate,
                        actor=actor,
                        db_path=db_path,
                    )
                    if created:
                        await dispatch(row['invocation_id'])
            finally:
                await bridge.close()
    await scan(project)
    ack()


def effective_control(project, db_path=None):
    mode = ledger.project_control(project, db_path=db_path)
    active = ledger.list_invocations(project, db_path, limit=100)
    unsettled = any(r['state'] in {'running', 'uncertain', 'cancel_requested'} for r in active)
    mode['effective_state'] = (
        mode['state'] + '_requested' if unsettled and mode['state'] in {'paused', 'stopped'} else mode['state']
    )
    return mode

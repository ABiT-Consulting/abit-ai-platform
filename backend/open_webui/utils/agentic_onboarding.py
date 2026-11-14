"""Reconcile persistent specialist identities before any Automation creation.

An interrupted registration retains the exact pending, inactive specialists and
its durable plan. Recovery reuses those IDs; it never creates replacement agents
or assumes an unknown ERP acknowledgment failed. No model is invoked here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from uuid import UUID

from sqlalchemy import or_, select, update
from open_webui.utils import agentic_governance as ledger

RPC = '/api/method/abit_ai_assistant.project_agent_bindings.get_project_onboarding_context'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _uuid(value):
    try:
        if str(UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError('Existing specialist identity must be a canonical UUID')


def _erp_bindings(project, users, context):
    if not isinstance(context, dict) or context.get('project_id') != project or context.get('role_users') != users:
        raise ValueError('ERP onboarding scope or configured shared role identities differ')
    if users['qa'] in {users['developer'], users['operations'], users['it_manager']}:
        raise ValueError('Independent QA requires a different shared ERP identity from every implementer')
    rows = context.get('bindings')
    if not isinstance(rows, list) or len(rows) > 100 or not context.get('project_revision'):
        raise ValueError('ERP did not return a bounded authoritative binding inventory')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('project') != project:
            raise ValueError('ERP returned a cross-project binding')
        role = row.get('agent_role')
        if role not in ledger.ROLES or row.get('slot') != 'primary':
            continue  # Preserve unrelated release/secondary bindings.
        if role in result:
            raise ValueError('Multiple primary ERP bindings require controlled reconciliation')
        ident = _uuid(row.get('abit_agent_id'))
        if row.get('erp_ai_user') != users[role] or row.get('agent_url') != 'https://abit.ai/automations/' + ident:
            raise ValueError('Existing ERP specialist role identity or URL differs')
        state = row.get('verification_state')
        if state not in {'Pending Verification', 'Verified'}:
            raise ValueError('Existing ERP binding is revoked or unavailable; controlled reconciliation required')
        enabled = row.get('is_enabled')
        if type(enabled) not in {bool, int} or enabled not in {0, 1} or bool(enabled) != (state == 'Verified'):
            raise ValueError('Existing ERP binding enablement is inconsistent')
        result[role] = row
    if len({v['abit_agent_id'] for v in result.values()}) != len(result):
        raise ValueError('One specialist cannot belong to multiple roles')
    return result


def reconcile(project, owner, users, context, automations, registered, *, prior=None, running=(), unresolved=()):
    """Pure preflight across ERP bindings, actual Automations and durable registry."""
    if not ledger.PROJECT_ID.fullmatch(project) or not owner or set(users) != set(ledger.ROLES):
        raise ValueError('Complete project specialist identities are required')
    erp = _erp_bindings(project, users, context)
    by_role = {}
    actual = {}
    running = set(running)
    unresolved = set(unresolved)
    for row in automations:
        ident = _uuid(row['id'])
        if ident in actual:
            raise ValueError('Duplicate Automation inventory identity')
        actual[ident] = row
        meta = row.get('meta') or {}
        if meta.get('agentic_project') == project:
            role = meta.get('agentic_role')
            if role not in ledger.ROLES or role in by_role:
                raise ValueError('Ambiguous existing project specialists require controlled reconciliation')
            if row['user_id'] != owner or meta.get('erp_role_user') != users[role]:
                raise ValueError('Existing project specialist owner or shared ERP role differs')
            by_role[role] = row
    durable = {}
    for row in registered:
        role = row.get('role')
        if role not in ledger.ROLES or role in durable:
            raise ValueError('Ambiguous durable specialist registry')
        if row.get('owner_id') != owner or row.get('erp_user') != users[role]:
            raise ValueError('Registered specialist owner or shared ERP role differs')
        durable[role] = _uuid(row.get('agent_id'))
    identities, existing = {}, {}
    for role in ledger.ROLES:
        candidates = set()
        if role in erp:
            candidates.add(erp[role]['abit_agent_id'])
        if role in durable:
            candidates.add(durable[role])
        if role in by_role:
            candidates.add(by_role[role]['id'])
        if len(candidates) > 1:
            raise ValueError('ERP, Automation and registry specialist identities conflict')
        ident = next(iter(candidates), ledger.agent_id(project, role))
        row = actual.get(ident)
        if row and by_role.get(role) is not row:
            raise ValueError('Specialist identity is occupied by an out-of-scope Automation')
        if not row and (role in durable or role in erp):
            # Only our journal can recover a known interrupted local insertion.
            if (
                not prior
                or prior['identities'].get(role) != ident
                or erp.get(role, {}).get('verification_state') == 'Verified'
            ):
                raise ValueError('Existing registered specialist is missing; restore it before provisioning')
        if row:
            meta = row.get('meta') or {}
            pending = meta.get('governance_onboarding_state') == 'pending_binding'
            if pending:
                if not prior or meta.get('governance_onboarding_id') != prior.get('onboarding_id') or row['is_active']:
                    raise ValueError('Unowned pending specialist cannot be adopted')
            elif not row['is_active']:
                raise ValueError('Existing specialist is paused; explicit controlled activation is required')
            if row.get('next_run_at') is not None:
                raise ValueError('Existing specialist has scheduled work; drain and reconcile it first')
            managed = meta.get('governance_managed') is True and meta.get('api_triggered_only') is True
            if not managed and ident in running:
                raise ValueError('Existing legacy specialist has active work; preserve and drain it first')
            if not managed and ident in unresolved:
                raise ValueError(
                    'Existing legacy specialist has an unresolved external original; reconcile its receipt before adoption'
                )
            existing[role] = row
        identities[role] = ident
    if len(set(identities.values())) != len(ledger.ROLES):
        raise ValueError('Project specialists require six distinct persistent identities')
    body = {'project': project, 'owner': owner, 'role_users': users, 'identities': identities}
    body['onboarding_id'] = 'agov-onboarding-' + hashlib.sha256(_json(body).encode()).hexdigest()[:48]
    if prior and prior != body:
        raise ValueError('Original specialist onboarding plan is immutable; controlled reconciliation required')
    return body, existing


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_onboarding(
        project TEXT PRIMARY KEY, plan_json TEXT NOT NULL, phase TEXT NOT NULL,
        updated_at INTEGER NOT NULL)''')


def current_plan(project, db_path=None):
    with ledger.connect(db_path) as db:
        _table(db)
        row = db.execute('SELECT plan_json FROM governance_onboarding WHERE project=?', (project,)).fetchone()
    return json.loads(row[0]) if row else None


def checkpoint(plan, phase, db_path=None):
    if phase not in {'planned', 'local_pending', 'erp_verified', 'ready'}:
        raise ValueError('Unsupported onboarding checkpoint')
    with ledger.connect(db_path) as db:
        _table(db)
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute(
            'SELECT plan_json FROM governance_onboarding WHERE project=?', (plan['project'],)
        ).fetchone()
        encoded = _json(plan)
        if previous and previous[0] != encoded:
            raise ValueError('Original specialist onboarding plan is immutable')
        db.execute(
            'INSERT INTO governance_onboarding VALUES(?,?,?,?) ON CONFLICT(project) DO UPDATE SET phase=excluded.phase,updated_at=excluded.updated_at',
            (plan['project'], encoded, phase, int(time.time())),
        )
        db.execute('COMMIT')


@asynccontextmanager
async def project_lock(project, db_path=None):
    directory = Path(db_path or ledger.path()).parent / 'governance-onboarding-locks'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(
        directory / (hashlib.sha256(project.encode()).hexdigest() + '.lock'),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Onboarding lock must be a regular file')
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Original project onboarding is still being reconciled')
                await asyncio.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _snapshot(row):
    return {key: getattr(row, key) for key in ('id', 'user_id', 'meta', 'is_active', 'next_run_at', 'updated_at')}


async def unresolved_legacy_originals(db, snapshots, data_root, run_model):
    """Bounded read-only metadata: a deferred run is not proof of no submission."""
    legacy = {
        row['id']
        for row in snapshots
        if not (
            (row.get('meta') or {}).get('governance_managed') is True
            and (row.get('meta') or {}).get('api_triggered_only') is True
        )
    }
    if not legacy:
        return set()
    history = list(
        (
            await db.execute(
                select(run_model.automation_id, run_model.id, run_model.status)
                .where(run_model.automation_id.in_(legacy), run_model.status != 'success')
                .limit(501)
            )
        ).all()
    )
    if len(history) > 500:
        return legacy
    positive, unresolved = set(), set()
    path = Path(data_root) / 'abitdot-primary.sqlite'
    if path.exists():
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3) as dot:
                dot.execute('PRAGMA query_only=ON')
                marks = ','.join('?' for _ in legacy)
                rows = dot.execute(
                    'SELECT automation_id,original_run_id,status,length(response_text)>0 '
                    'FROM dot_primary_runs WHERE automation_id IN (' + marks + ') LIMIT 501',
                    tuple(legacy),
                ).fetchall()
                if len(rows) > 500:
                    return legacy
                for agent, run, state, has_reply in rows:
                    if state == 'delegated' and has_reply:
                        positive.add((agent, run))
                    else:
                        unresolved.add(agent)
        except (OSError, sqlite3.Error):
            return legacy
    for agent, run, _ in history:
        if (agent, run) not in positive:
            unresolved.add(agent)
    return unresolved


async def provision(project, owner, users, config, model_id, *, title, prompt, project_gate, bridge, db_path=None):
    """Reconcile and recover the same six original agents; fail closed while pending."""
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.automations import Automation, AutomationRun

    async with project_lock(project, db_path):
        response = await bridge.erp.request(
            'GET', RPC, params={'project_id': project}, headers={'X-ABIT-Agentic-Bridge': bridge.secret}
        )
        context = response.get('message')
        erp = _erp_bindings(project, users, context)
        registered = ledger.roster(project, db_path)
        prior = current_plan(project, db_path)
        ids = {ledger.agent_id(project, role) for role in ledger.ROLES}
        ids.update(row['abit_agent_id'] for row in erp.values())
        ids.update(row['agent_id'] for row in registered)
        if prior:
            ids.update(prior['identities'].values())
        async with get_async_db_context() as db:
            rows = list(
                (
                    await db.execute(
                        select(Automation).where(
                            or_(
                                Automation.id.in_(ids),
                                Automation.meta['agentic_project'].as_string() == project,
                            )
                        )
                    )
                ).scalars()
            )
            snapshots = [_snapshot(row) for row in rows]
            running = set(
                (
                    await db.execute(
                        select(AutomationRun.automation_id).where(
                            AutomationRun.automation_id.in_([row.id for row in rows]),
                            AutomationRun.status == 'running',
                        )
                    )
                ).scalars()
            )
            unresolved = await unresolved_legacy_originals(
                db, snapshots, Path(db_path or ledger.path()).parent, AutomationRun
            )
        plan, existing = reconcile(
            project, owner, users, context, snapshots, registered, prior=prior, running=running, unresolved=unresolved
        )
        # Check global ownership before creating even an inactive local row.
        # A stale registry in another project is not permission to reuse its ID.
        reverse = {ident: role for role, ident in plan['identities'].items()}
        with ledger.connect(db_path) as db:
            placeholders = ','.join('?' for _ in reverse)
            occupied = db.execute(
                'SELECT agent_id,erp_project,role,owner_id,erp_user FROM governance_agent WHERE agent_id IN ('
                + placeholders
                + ')',
                tuple(reverse),
            ).fetchall()
            for row in occupied:
                role = reverse[row['agent_id']]
                if (row['erp_project'], row['role'], row['owner_id'], row['erp_user']) != (
                    project,
                    role,
                    owner,
                    users[role],
                ):
                    raise ValueError('Specialist identity is already registered to another project, role or owner')
        fresh = await project_gate(project, config)
        if str(fresh.get('modified')) != context['project_revision']:
            raise ValueError('ERP Project changed before specialist provisioning; refresh its authorization')
        checkpoint(plan, 'planned', db_path)
        now = time.time_ns()
        async with get_async_db_context() as db:
            # Re-read under the original project lock immediately before the
            # atomic local write; reject a concurrent ordinary-UI change/run.
            current = list(
                (
                    await db.execute(
                        select(Automation)
                        .where(
                            or_(
                                Automation.id.in_(ids),
                                Automation.meta['agentic_project'].as_string() == project,
                            )
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            fresh_running = set(
                (
                    await db.execute(
                        select(AutomationRun.automation_id).where(
                            AutomationRun.automation_id.in_([row.id for row in current]),
                            AutomationRun.status == 'running',
                        )
                    )
                ).scalars()
            )
            fresh_snapshots = [_snapshot(row) for row in current]
            fresh_unresolved = await unresolved_legacy_originals(
                db, fresh_snapshots, Path(db_path or ledger.path()).parent, AutomationRun
            )
            _, fresh_existing = reconcile(
                project,
                owner,
                users,
                context,
                fresh_snapshots,
                registered,
                prior=plan,
                running=fresh_running,
                unresolved=fresh_unresolved,
            )
            if _json({r: v for r, v in existing.items()}) != _json(fresh_existing):
                raise ValueError('Existing specialist changed before adoption; preserve the newer state')
            for role in ledger.ROLES:
                old = fresh_existing.get(role)
                pending_meta = {
                    'agentic_project': project,
                    'agentic_role': role,
                    'erp_role_user': users[role],
                    'governance_managed': True,
                    'api_triggered_only': True,
                    'governance_onboarding_id': plan['onboarding_id'],
                    'governance_onboarding_state': 'pending_binding',
                }
                if old:
                    meta = old.get('meta') or {}
                    if (
                        meta.get('governance_managed') is True
                        and meta.get('api_triggered_only') is True
                        and meta.get('governance_onboarding_state') != 'pending_binding'
                    ):
                        continue
                    changed = await db.execute(
                        update(Automation)
                        .where(Automation.id == old['id'], Automation.updated_at == old['updated_at'])
                        .values(meta={**meta, **pending_meta}, is_active=False, next_run_at=None, updated_at=now)
                    )
                    if changed.rowcount != 1:
                        raise ValueError('Existing specialist changed during adoption')
                else:
                    db.add(
                        Automation(
                            id=plan['identities'][role],
                            user_id=owner,
                            folder_id=None,
                            name=f'AGOV {project} — {title[role]}',
                            data={
                                'prompt': prompt(fresh, role, project),
                                'model_id': model_id,
                                'continue_previous_chat': False,
                                'rrule': 'DTSTART:20260101T000000Z\nRRULE:FREQ=DAILY;COUNT=1',
                            },
                            meta=pending_meta,
                            is_active=False,
                            next_run_at=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            await db.commit()
        records = ledger.enroll(project, owner, users, db_path, identities=plan['identities'])
        checkpoint(plan, 'local_pending', db_path)
        # The bridge's bind/verify RPCs are idempotent for these exact IDs.
        # Any unknown acknowledgment leaves the local agents inactive/pending.
        await bridge.register(project, str(fresh['modified']), records)
        checkpoint(plan, 'erp_verified', db_path)
        await project_gate(project, config)
        async with get_async_db_context() as db:
            for role in ledger.ROLES:
                row = await db.get(Automation, plan['identities'][role])
                if not row:
                    raise ValueError('Original specialist disappeared during ERP registration')
                meta = row.meta or {}
                if (
                    row.user_id != owner
                    or meta.get('agentic_project') != project
                    or meta.get('agentic_role') != role
                    or meta.get('erp_role_user') != users[role]
                ):
                    raise ValueError('Original specialist scope changed during ERP registration')
                if meta.get('governance_onboarding_state') == 'pending_binding':
                    if meta.get('governance_onboarding_id') != plan['onboarding_id'] or row.is_active:
                        raise ValueError('Original pending specialist was modified during ERP registration')
                    row.meta = {**meta, 'governance_onboarding_state': 'verified'}
                    row.is_active = True
                    row.updated_at = time.time_ns()
            await db.commit()
        checkpoint(plan, 'ready', db_path)
        return records

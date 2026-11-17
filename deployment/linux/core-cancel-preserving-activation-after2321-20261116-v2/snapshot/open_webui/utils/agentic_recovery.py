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
    # This endpoint observes the already submitted original. An expired
    # admission proof cannot authorize new work, but remains its receipt key.
    payload = verified_dot_payload(envelope, account, admission=False)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35), trust_env=False) as client:
        async with client.post(
            base + '/dot/observe', json=payload, headers={'Authorization': 'Bearer ' + token}, allow_redirects=False
        ) as response:
            if response.status != 200:
                return None, None
            data = await response.json(content_type=None)
    if isinstance(data, dict) and isinstance(data.get('rejection'), dict):
        from open_webui.utils.abitdot_original_rejection import verify_rejection

        try:
            proof = verify_rejection(envelope, data)
        except ValueError:
            return None, None
        return {'status': 'rejected_before_submit', 'original_rejection': proof}, data
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
    from open_webui.utils.abitdot_primary import (
        ledger as dot_ledger,
        record_receipt,
        dot_primary_key,
        verify_saved_scope_receipt,
    )
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
        if receipt.get('status') == 'rejected_before_submit':
            from open_webui.utils.abitdot_original_rejection import record_rejection

            with dot_ledger(path) as db:
                db.execute('BEGIN IMMEDIATE')
                record_rejection(db, envelope, receipt['original_rejection'])
            return None  # A failed outgoing request is not an accepted model result.
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
    verify_saved_scope_receipt(original)
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
    record = await read_format_correction(
        row, automation, config=config, project_gate=project_gate, bridge=bridge, db_path=db_path
    )
    if record is False:
        return None  # Protected capture exists, but current authorization is held.
    if record is None:
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


def _recovery_failure(error):
    """Retain bounded native validation diagnostics, never arbitrary prompt/error text."""
    from open_webui.utils.erp_status_write import ERPResponseError
    from open_webui.utils.agentic_erp_channel import ERPChannelError

    kind = type(error).__name__
    if isinstance(error, ERPResponseError):
        # ERPResponseError uses the existing credential-free Frappe formatter.
        return kind + ': ' + str(error)[:700]
    if isinstance(error, ERPChannelError) and str(error) in {
        'ERP channel native operation is not allowed',
        'Authenticated native ERP channel is unavailable',
        'Original native ERP channel disconnected',
        'Original native ERP HTTP outcome is unavailable',
    }:
        return kind + ': ' + str(error)
    return kind


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
    except BaseException as error:
        if claimed:
            ledger.finish_execution(
                row['invocation_id'],
                row['automation_run_id'],
                row['lease_token'],
                'uncertain',
                reason='Original pre-submission recovery interrupted (' + _recovery_failure(error)
                + '); inspect original receipt without replay',
                db_path=db_path,
            )
        raise
    finally:
        if beat:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)


# The only correction input is an administrator-owned, per-original capture.
# It never sends, observes a browser, admits a gateway session, or changes raw
# provider/message records. Completion still uses the ordinary strict pipeline.
FORMAT_POLICY = 'returned-reply-format.v1'
FORMAT_ROOT = Path('/var/lib/abit-ai-agentic-gateway/returned-reply-format')
FORMAT_AUDIT = 'returned_reply_format_selected'


def _format_file(row):
    import hashlib
    import re
    import stat
    ident = row['invocation_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', ident):
        raise ValueError('Invalid original format-correction identity')
    path = FORMAT_ROOT / (ident + '.json')
    try:
        directory = FORMAT_ROOT.lstat()
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (FORMAT_ROOT.resolve() != FORMAT_ROOT or path.resolve() != path
            or not stat.S_ISDIR(directory.st_mode)
            or (directory.st_uid, directory.st_gid, stat.S_IMODE(directory.st_mode)) != (0, 988, 0o750)
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 988, 0o640)
            or not 0 < info.st_size <= 400000):
        raise ValueError('Protected original format-correction file required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        actual = os.fstat(descriptor)
        if (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns) != (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            raise ValueError('Format-correction file changed while opening')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(400001)
    finally:
        os.close(descriptor)
    if len(raw) != info.st_size:
        raise ValueError('Format-correction file changed while reading')
    return json.loads(raw.decode('utf-8', errors='strict')), hashlib.sha256(raw).hexdigest()


def _format_provider(row, automation):
    import sqlite3
    from open_webui.env import DATA_DIR
    path = Path(DATA_DIR) / 'abitdot-primary.sqlite'
    if not path.is_file():
        raise ValueError('Original provider journal is absent')
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows = db.execute(
            'SELECT * FROM dot_primary_runs WHERE owner=? AND automation_id=? AND original_run_id=?',
            (automation.user_id, row['agent_id'], row['automation_run_id']),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError('Original provider journal is ambiguous')
    return dict(rows[0])


def _format_authority(db, row, proof, packet, original):
    import hashlib
    import re
    from open_webui.utils.agentic_bridge import stable_run_id
    from open_webui.utils.agentic_controls import task_control
    from open_webui.utils.agentic_start_recovery import _matching, _identity, _receipt
    # No PID transfer or replacement run: this is a submitted response readback.
    current = ledger._owned(db, row['invocation_id'], row['automation_run_id'],
                            row['lease_token'], {'uncertain'})
    if (not current or current['submitted_at'] is None or current['dispatch_attempts'] != 1
            or current['target_role'] != 'developer'
            or any(current[k] != row[k] for k in (
                'work_packet', 'packet_hash', 'lease_fence', 'agent_id', 'erp_project',
                'erp_task', 'task_revision'))
            or row['automation_run_id'] != stable_run_id(row['invocation_id'])
            or digest(packet) != row['packet_hash']):
        raise ValueError('Format correction requires the exact owned submitted original')
    for table in ('governance_local_result', 'governance_result', 'governance_action'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            if db.execute('SELECT 1 FROM ' + table + ' WHERE invocation_id=?',
                          (row['invocation_id'],)).fetchone():
                raise ValueError('Original already has result or action evidence')
    if db.execute(
            "SELECT 1 FROM governance_gateway_call c JOIN governance_gateway_session s "
            "ON s.session_id=c.session_id WHERE s.invocation_id=? AND c.state!='completed'",
            (row['invocation_id'],)).fetchone():
        raise ValueError('Original gateway calls are not all settled')
    # Read controls in this SAME transaction; caller gates are also rechecked.
    project = db.execute('SELECT state FROM governance_project_control WHERE erp_project=?',
                         (row['erp_project'],)).fetchone()
    task = db.execute('SELECT state FROM governance_task_control WHERE project=? AND task=?',
                      (row['erp_project'], row['erp_task'])).fetchone()
    if not project or project['state'] != 'enabled' or (task and task['state'] != 'enabled'):
        raise ValueError('Current ERP control prevents format-correction readback')
    start = db.execute('SELECT * FROM governance_start WHERE invocation_id=?',
                       (row['invocation_id'],)).fetchone()
    if not _matching(row, start) or not start['receipt_json']:
        raise ValueError('Original native start is not confirmed')
    _identity(row, json.loads(start['event_json']))
    _receipt(start, json.loads(start['receipt_json']))
    sessions = db.execute('SELECT * FROM governance_gateway_session WHERE invocation_id=?',
                          (row['invocation_id'],)).fetchall()
    if len(sessions) != 1 or not sessions[0]['admission_json']:
        raise ValueError('Original gateway admission receipt is missing or ambiguous')
    admission = json.loads(sessions[0]['admission_json'])
    envelope = json.loads(original['envelope_json'])
    scope = envelope.get('governance_scope', {}).get('scope', {})
    from open_webui.utils.agentic_relay_scope import scope_receipt
    from open_webui.utils.agentic_gateway_sessions import receipt as gateway_receipt
    if admission != envelope.get('governance_scope'):
        raise ValueError('Original stored gateway admission changed')
    scope_receipt(envelope, scope.get('account_id'), gateway_receipt(admission))
    expected = {
        'schema_version': 1, 'purpose': FORMAT_POLICY,
        'invocation_id': row['invocation_id'], 'run_id': row['automation_run_id'],
        'project_id': row['erp_project'], 'task_id': row['erp_task'],
        'agent_id': row['agent_id'], 'packet_sha256': row['packet_hash'],
        'native_start_event_sha256': start['event_sha256'],
        'native_start_receipt_sha256': digest(json.loads(start['receipt_json'])),
        'provider_envelope_sha256': hashlib.sha256(original['envelope_json'].encode()).hexdigest(),
        'scope_sha256': digest(envelope.get('governance_scope')),
        'gateway_admission_sha256': digest(admission),
        'owner_id': envelope.get('owner_id'), 'account_id': scope.get('account_id'),
        'dot_id': envelope.get('dot_id'),
        'conversation_url': 'https://chatgpt.com/dots/' + envelope.get('dot_id', ''),
    }
    capture_fields = {'original_root_id','original_reply_id','original_visible_reply_sha256',
                      'correction_root_id','correction_reply_id','corrected_reply_utf8',
                      'corrected_reply_sha256','correction_intent_sha256',
                      'capture_release_proof_sha256','relay_idle_proof_sha256',
                      'pause_control_sequence','captured_at'}
    if (not isinstance(proof, dict) or set(proof) != set(expected) | capture_fields
            or any(proof.get(k) != value for k, value in expected.items())):
        raise ValueError('Protected format-correction binding changed')
    if (envelope.get('run_id') != row['automation_run_id']
            or envelope.get('automation_id') != row['agent_id']
            or scope.get('project_id') != row['erp_project']
            or scope.get('task_id') != row['erp_task']
            or scope.get('role') != row['target_role']
            or scope.get('agent_id') != row['agent_id']
            or scope.get('work_packet_digest') != packet['content_sha256']
            or scope.get('invocation_id') != row['invocation_id']
            or scope.get('run_id') != row['automation_run_id']
            or original['status'] not in {'unknown', 'unavailable', 'dispatching'}
            or original.get('response_text') or original.get('receipt')):
        raise ValueError('Original returned provider record is not unresolved')
    for field in ('original_visible_reply_sha256', 'corrected_reply_sha256',
                  'correction_intent_sha256', 'capture_release_proof_sha256', 'relay_idle_proof_sha256'):
        if not isinstance(proof.get(field), str) or not re.fullmatch('[a-f0-9]{64}', proof[field]):
            raise ValueError('Exact correction capture provenance is required')
    ids = [proof.get(k) for k in (
        'original_root_id', 'original_reply_id', 'correction_root_id', 'correction_reply_id')]
    message_shape = re.compile(r'([a-f0-9]{32}~[a-f0-9]{32})~CalpicoMessage~Sentinel_[a-f0-9]{32}')
    identities = [message_shape.fullmatch(x) if isinstance(x, str) else None for x in ids]
    # The Dot URL is an alias, not the rendered Calpico room namespace.
    # Root attests the observed original pair; all four IDs must share THAT room.
    if (any(x is None for x in identities) or len(set(ids)) != 4
            or any(x.group(1) != identities[0].group(1) for x in identities)):
        raise ValueError('Correction must name distinct messages in the same original chat')
    text = proof.get('corrected_reply_utf8')
    if (not isinstance(text, str) or not 0 < len(text.encode()) <= 200000 or '\x00' in text
            or hashlib.sha256(text.encode()).hexdigest() != proof['corrected_reply_sha256']):
        raise ValueError('Exact captured corrected UTF-8 reply is required')
    boundary = proof.get('pause_control_sequence')
    captured = proof.get('captured_at')
    if (type(boundary) is not int or boundary < 11 or type(captured) is not int or captured <= 0):
        raise ValueError('Correction pause boundary and capture time are required')
    control = db.execute(
        "SELECT * FROM governance_native_control WHERE project=? AND phase='acknowledged' "
        "AND action='retry' AND sequence>? AND task IN ('',?) ORDER BY sequence DESC LIMIT 1",
        (row['erp_project'], boundary, row['erp_task'])).fetchone()
    if not control:
        return None
    audit = db.execute(
        "SELECT actor,payload,created_at FROM governance_event WHERE erp_project=? "
        "AND kind='native_control_applied' ORDER BY seq DESC LIMIT 100", (row['erp_project'],)).fetchall()
    matched = any(json.loads(x['payload']).get('command_id') == control['command_id']
                  and json.loads(x['payload']).get('control_sequence') == control['sequence']
                  and json.loads(x['payload']).get('action') == 'retry'
                  and bool(x['actor']) and x['created_at'] > captured for x in audit)
    if not matched:
        return None
    return {'policy': FORMAT_POLICY, 'invocation_id': row['invocation_id'],
            'run_id': row['automation_run_id'], 'packet_sha256': row['packet_hash'],
            'lease_fence': row['lease_fence'],
            **{k: proof[k] for k in (
                'native_start_event_sha256', 'native_start_receipt_sha256', 'provider_envelope_sha256',
                'scope_sha256', 'gateway_admission_sha256', 'original_root_id', 'original_reply_id', 'original_visible_reply_sha256',
                'correction_root_id', 'correction_reply_id', 'corrected_reply_sha256',
                'correction_intent_sha256', 'capture_release_proof_sha256', 'relay_idle_proof_sha256')},
            'control_sequence': control['sequence'], 'control_command': control['command_id']}


async def read_format_correction(row, automation, *, config, project_gate, bridge, db_path=None):
    selected = _format_file(row)
    if selected is None:
        return None
    proof, proof_sha = selected
    from open_webui.utils import agentic_maintenance
    from open_webui.utils.agentic_controls import task_control
    if (agentic_maintenance.status()['state'] != 'open'
            or ledger.project_control(row['erp_project'], db_path=db_path)['state'] != 'enabled'
            or task_control(row['erp_project'], row['erp_task'], db_path=db_path)['state'] != 'enabled'):
        return False
    await project_gate(row['erp_project'], config)
    packet = json.loads(row['work_packet'])
    fresh = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    managed = {'source_modified','packet_id','content_sha256','implementation_user','implementation_run_id','attachments'}
    if not same_business_context(packet, fresh, managed):
        raise ValueError('ERP inputs changed before original format-correction readback')
    original = _format_provider(row, automation)
    with ledger._transaction(db_path) as db:
        audit = _format_authority(db, row, proof, packet, original)
        if audit is None:
            return False
        audit['protected_file_sha256'] = proof_sha
        prior = db.execute('SELECT payload FROM governance_event WHERE invocation_id=? AND kind=?',
                            (row['invocation_id'], FORMAT_AUDIT)).fetchall()
        if prior and (len(prior) != 1 or json.loads(prior[0]['payload']) != audit):
            raise ValueError('Original format-correction selection is immutable')
        if not prior:
            ledger._event(db, row['erp_project'], row['invocation_id'], FORMAT_AUDIT,
                          'root-authorized-format-readback', audit)
    # Keep original provider records untouched. Only this private in-memory view
    # carries actual separately captured model bytes to normal complete_original.
    return {**original, 'response_text': proof['corrected_reply_utf8'],
            'format_correction_provenance': audit}


QA_PREFLIGHT_POLICY = 'qa-preflight-retry.v1'
QA_PREFLIGHT_AUDIT = 'qa_preflight_resumed'


def _qa_preflight_proof(db, row, fresh, config):
    """Derive eligibility from the published source, never a caller proof flag."""
    from open_webui.utils.agentic_bridge import packet_digest, stable_run_id
    from open_webui.utils.agentic_gateway_delivery import _repository_profile, _workspace_target

    row = dict(row)
    original = json.loads(row['work_packet'])
    saved = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (row['invocation_id'],)).fetchone()
    if not saved or saved['phase'] != 'published':
        return None
    result = json.loads(saved['result_json'])
    event = json.loads(saved['event_json'])
    receipt = json.loads(saved['receipt_json'] or '{}')
    if (row['target_role'] != 'qa' or row['state'] != 'execution_finished' or row['submitted_at'] is None
            or row['automation_run_id'] != stable_run_id(row['invocation_id'])
            or type(row['dispatch_attempts']) is not int or not 1 <= row['dispatch_attempts'] <= 3
            or digest(original) != row['packet_hash'] or saved['run_id'] != row['automation_run_id']
            or digest(result) != saved['result_sha256'] or result.get('outcome') != 'blocked'
            or any(result.get(k) for k in ('test_results', 'evidence', 'defects', 'action_requests',
                                           'dependency_verifications', 'delivery', 'architect_plan',
                                           'reviewed_delivery_sha256', 'qa_review_ack'))
            or result.get('actor') != original['binding']
            or any(result.get(k) != original.get(k) for k in ('project_id', 'task_id', 'task_revision'))
            or result.get('invocation_id') != row['invocation_id']
            or result.get('work_packet_digest') != packet_digest(original)
            or event.get('type') != 'run.blocked' or event.get('result_contract') != result
            or event.get('event_id') != 'agov-result-' + digest([row['invocation_id'], result])[:48]
            or event.get('run_id') != row['automation_run_id']
            or event.get('invocation_id') != row['invocation_id']
            or event.get('project_id') != row['erp_project'] or event.get('task_id') != row['erp_task']
            or event.get('role') != 'qa' or event.get('agent_id') != row['agent_id']
            or event.get('task_revision') != row['task_revision']
            or event.get('work_packet_digest') != packet_digest(original)
            or event.get('acceptance_passed') is not False or event.get('artifacts') != []
            or receipt.get('event_id') != event['event_id'] or receipt.get('run_id') != row['automation_run_id']
            or receipt.get('task_id') != row['erp_task'] or receipt.get('durable') is not True
            or receipt.get('status') != 'Blocked'):
        raise ValueError('QA preflight retry requires its exact published empty blocked checkpoint')
    if not same_business_context(original, fresh, {'source_modified', 'packet_id', 'content_sha256'}):
        raise ValueError('QA preflight retry cannot change implementation or business context')
    own = [v for v in (fresh.get('project_backlog') or {}).get('tasks', []) if v.get('task_id') == row['erp_task']]
    if len(own) != 1 or own[0].get('status') != 'Pending Review' or own[0].get('source_modified') != fresh.get('source_modified'):
        raise ValueError('QA preflight retry requires fresh normal Pending Review readiness')
    source = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (row['source_invocation_id'],)).fetchone()
    implementation = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (row['source_invocation_id'],)).fetchone()
    if not source or not implementation:
        raise ValueError('QA preflight retry has no published implementation predecessor')
    delivered = json.loads(implementation['result_json'])
    published = json.loads(implementation['event_json'])
    accepted = json.loads(implementation['receipt_json'] or '{}')
    if (source['erp_project'] != row['erp_project'] or source['erp_task'] != row['erp_task']
            or source['target_role'] != 'developer' or source['state'] not in {'handed_off', 'verified'}
            or source['submitted_at'] is None or source['automation_run_id'] != stable_run_id(source['invocation_id'])
            or digest(json.loads(source['work_packet'])) != source['packet_hash']
            or implementation['phase'] != 'published' or implementation['run_id'] != source['automation_run_id']
            or digest(delivered) != implementation['result_sha256']
            or delivered.get('invocation_id') != source['invocation_id'] or delivered.get('outcome') != 'ready_for_review'
            or delivered.get('actor') != json.loads(source['work_packet'])['binding']
            or fresh.get('implementation_run_id') != source['automation_run_id']
            or original.get('implementation_delivery') != delivered.get('delivery')
            or result.get('implementation_invocation_id') != source['invocation_id']
            or result.get('implementation_agent_id') != source['agent_id']
            or published.get('type') != 'run.ready' or published.get('run_id') != source['automation_run_id']
            or published.get('result_contract') != delivered
            or published.get('event_id') != 'agov-result-' + digest([source['invocation_id'], delivered])[:48]
            or published.get('invocation_id') != source['invocation_id']
            or accepted.get('event_id') != published.get('event_id') or accepted.get('run_id') != source['automation_run_id']
            or accepted.get('task_id') != row['erp_task'] or accepted.get('durable') is not True
            or accepted.get('status') != 'Pending Review'):
        raise ValueError('QA preflight retry changed its exact published Developer delivery')
    # Any prepare/test/evidence/action makes this ordinary QA work, not this
    # exceptional zero-work preflight. An uncertain read is retained untouched.
    for table in ('governance_repository_workspace', 'governance_qa_delivery',
                  'governance_developer_delivery', 'governance_action'):
        # The action facility is initialized only when first used. An absent
        # optional journal contains no action; all required work tables stay strict.
        if table == 'governance_action' and not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action'").fetchone():
            continue
        if db.execute('SELECT 1 FROM ' + table + ' WHERE invocation_id=?', (row['invocation_id'],)).fetchone():
            raise ValueError('QA preflight retry cannot repeat prior workspace or independent work')
    profile = _repository_profile(row, original, config)
    if os.path.lexists(_workspace_target(row, profile)):
        raise ValueError('QA preflight retry cannot adopt or repeat an existing workspace')
    sessions = db.execute('SELECT * FROM governance_gateway_session WHERE invocation_id=?', (row['invocation_id'],)).fetchall()
    if len(sessions) != 1:
        raise ValueError('QA preflight gateway ownership is absent or ambiguous')
    session = sessions[0]
    scope = json.loads(session['scope_json'])
    admission = json.loads(session['admission_json'])
    expected = {'project_id': row['erp_project'], 'task_id': row['erp_task'], 'role': 'qa',
                'agent_id': row['agent_id'], 'invocation_id': row['invocation_id'],
                'run_id': row['automation_run_id'], 'work_packet_digest': packet_digest(original),
                'erp_user': original['binding']['erp_user'], 'repository': original['repository'],
                'environment': original['environment'], 'tool_scopes': sorted(original['tool_scopes'])}
    registered = db.execute("SELECT * FROM governance_agent WHERE erp_project=? AND role='qa'", (row['erp_project'],)).fetchone()
    if (not registered or registered['agent_id'] != row['agent_id'] or registered['owner_id'] != scope.get('owner')
            or registered['erp_user'] != scope.get('erp_user')
            or any(scope.get(k) != v for k, v in expected.items()) or admission.get('scope') != scope):
        raise ValueError('QA preflight gateway/registered specialist scope changed')
    calls = db.execute('SELECT * FROM governance_gateway_call WHERE session_id=? ORDER BY operation', (session['session_id'],)).fetchall()
    if (len(calls) != 2 or [(v['operation'], v['state']) for v in calls] !=
            [('context.read', 'completed'), ('repository.verify_delivery', 'uncertain')]
            or any(v['request_hash'] != digest({'operation': v['operation'], 'arguments': {}}) for v in calls)
            or calls[1]['result_json'] is not None):
        raise ValueError('QA preflight retry permits only its two original non-mutating calls')
    context = json.loads(calls[0]['result_json'])
    repository_view = {k: profile.get(k) for k in ('repository', 'environment', 'allowed_paths', 'read_paths', 'test_paths', 'test_acceptance_ids')}
    repository_view['test_ids'] = sorted((profile.get('tests') or {}).keys())
    if (context.get('invocation_id') != row['invocation_id'] or context.get('work_packet') != original
            or context.get('repository_profile') != repository_view):
        raise ValueError('QA preflight context/profile differs from its original read')
    control = None
    for candidate in db.execute("SELECT * FROM governance_native_control WHERE project=? AND task IN ('',?) AND action='retry' AND phase='acknowledged' AND applied_state='enabled'", (row['erp_project'], row['erp_task'])):
        for item in db.execute("SELECT seq,actor,payload,created_at FROM governance_event WHERE erp_project=? AND kind='native_control_applied' ORDER BY seq DESC LIMIT 100", (row['erp_project'],)):
            value = json.loads(item['payload'])
            if (item['created_at'] > saved['updated_at'] and item['actor']
                    and value.get('command_id') == candidate['command_id']
                    and value.get('control_sequence') == candidate['sequence']
                    and value.get('task_id') == candidate['task'] and value.get('action') == 'retry'
                    and value.get('state') == 'enabled' and (control is None or item['seq'] > control['audit_seq'])):
                control = {'command_id': candidate['command_id'], 'sequence': candidate['sequence'],
                           'task_id': candidate['task'], 'actor': item['actor'], 'audit_seq': item['seq']}
    if control is None:
        return None
    return {'policy': QA_PREFLIGHT_POLICY, 'blocked_invocation_id': row['invocation_id'],
            'blocked_run_id': row['automation_run_id'], 'blocked_result_sha256': saved['result_sha256'],
            'native_block_receipt_sha256': digest(receipt), 'original_packet_sha256': row['packet_hash'],
            'fresh_packet_sha256': digest(fresh), 'implementation_invocation_id': source['invocation_id'],
            'implementation_run_id': source['automation_run_id'],
            'implementation_result_sha256': implementation['result_sha256'],
            'delivery_sha256': digest(delivered['delivery']), 'scope_sha256': digest(scope),
            'original_calls': [{k: v[k] for k in ('call_id', 'request_hash', 'operation', 'state')} for v in calls],
            'repository_profile_sha256': digest(repository_view), 'control': control}


async def resume_qa_preflight(invocation, *, bridge, project_gate, config, db_path=None):
    """One new independent QA unit after a newer acknowledged human Retry.

    No old run, call, reply, result, Native receipt or implementation is retried
    or rewritten. Existing task-fence primitives transfer ownership atomically.
    """
    from open_webui.utils.agentic_bridge import canonical
    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row['target_role'] != 'qa' or row['state'] != 'execution_finished':
        return None
    # Cheap pending guard avoids fresh ERP reads before actual publication.
    with ledger.connect(db_path) as db:
        saved = db.execute('SELECT phase FROM governance_result WHERE invocation_id=?', (invocation,)).fetchone()
        if not saved or saved['phase'] != 'published':
            return None
    _, gate = await project_gate(row['erp_project'], config)
    packet = await bridge.packet(row['erp_project'], row['erp_task'], 'qa', row['agent_id'])
    ident = 'agov-parent-' + digest([invocation, 'qa_preflight', [], 'qa'])[:48]
    with ledger._transaction(db_path) as db:
        source = ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'execution_finished'})
        if not source:
            return None
        ledger._gate(db, row['erp_project'], gate)
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        proof = _qa_preflight_proof(db, source, packet, config)
        if proof is None:
            return None
        if db.execute("SELECT 1 FROM governance_event WHERE erp_project=? AND kind=? AND json_extract(payload,'$.task_id')=?", (row['erp_project'], QA_PREFLIGHT_AUDIT, row['erp_task'])).fetchone():
            raise ValueError('QA preflight continuation is consumed; explicit PM repair issues are required')
        context = canonical({'policy': QA_PREFLIGHT_POLICY, 'predecessor_invocation': invocation,
            'implementation_invocation': proof['implementation_invocation_id'],
            'implementation_run_id': proof['implementation_run_id'],
            'instruction': 'The prior QA published only a preflight blocker and performed no workspace preparation or independent tests. Prepare the exact implementation PR head FIRST (preparation verifies PR/head/base), verify delivery, run your own registered independent tests, verify again and publish only actual evidence. Preserve the previous blocked result and uncertain read; never replay that original call or implementation.'})
        db.execute("UPDATE governance_invocation SET state='handed_off' WHERE invocation_id=?", (invocation,))
        ledger._release(db, source)
        successor, created = ledger._enqueue(db, invocation_id=ident, project=row['erp_project'], task=row['erp_task'],
            from_role='project_manager', target_role='qa', task_revision=packet['task_revision'],
            context=context, work_packet=packet, actor='governed-qa-preflight-recovery', gate=gate)
        db.execute('UPDATE governance_invocation SET source_invocation_id=? WHERE invocation_id=?', (invocation, ident))
        ledger._event(db, row['erp_project'], ident, QA_PREFLIGHT_AUDIT, 'governed-qa-preflight-recovery',
                      {**proof, 'task_id': row['erp_task'], 'successor': ident, 'kind': 'qa_preflight'})
        return successor, created

"""Current parent acceptance from native descendant proofs and fresh host reads.

No execution/approval/reopen, model-selected child, Task adoption or extra timer.
An immutable per-call artifact is preserved; recovery revalidates it read-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import packet_digest, same_business_context
from open_webui.utils.agentic_contracts.dependency_verification import (
    canonical,
    digest,
    require,
    policy_for,
    required_mappings,
    ancestry,
    reference,
    stable_observation,
)
from open_webui.utils.agentic_result_store import get_result
from open_webui.utils.agentic_it_broker import BrokerClient

RPC = '/api/method/abit_ai_assistant.agentic_dependency_verification.get_dependency_receipts'


def policy(packet, config):
    registered = (
        (config.get('agentic_repository_gateway') or {})
        .get('projects', {})
        .get(packet['project_id'], {})
        .get('dependency_acceptance_policy')
    )
    return policy_for(packet, registered)


def _owned(row, packet, path):
    require(
        row['target_role'] in {'developer', 'qa'}
        and all(row[key] == packet[field] for key, field in (('erp_project', 'project_id'), ('erp_task', 'task_id')))
        and packet['binding']['role'] == row['target_role']
        and packet['binding']['agent_id'] == row['agent_id'],
        'Dependency read requires its original current parent Developer/QA identity',
    )
    with ledger.connect(path) as db:
        current = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchone()
        lease = db.execute(
            'SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
            (row['erp_project'], row['erp_task']),
        ).fetchone()
        require(
            current
            and current['state'] in {'running', 'uncertain'}
            and current['lease_expires_at'] > int(time.time())
            and current['lease_token'] == row['lease_token']
            and current['automation_run_id'] == row['automation_run_id']
            and lease
            and tuple(lease) == (row['invocation_id'], row['lease_fence'])
            and packet_digest(json.loads(current['work_packet'])) == packet_digest(packet),
            'Parent lost its original lease, Task fence or immutable packet',
        )
        ledger._task_gate(db, row['erp_project'], row['erp_task'])


def _published(invocation, path, outcome):
    value = get_result(invocation, path)
    require(
        value
        and value['phase'] == 'published'
        and value['result'].get('outcome') == outcome
        and value['receipt']
        and value['receipt'].get('durable') is True,
        'Dependency publication is not authoritative',
    )
    return value


def _local_child(proof, parent, packet, path):
    """Cross-check exact latest native child facts against immutable Core history."""
    from open_webui.utils.agentic_contracts.validator import action_fingerprint
    from open_webui.utils.agentic_it_changes import plan_authority_digest
    from open_webui.utils import agentic_tool_gateway as gateway

    ancestry(parent['erp_task'], proof['child_task_id'], proof['ancestry'])
    with ledger.connect(path) as db:
        source = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (proof['implementation_invocation_id'],)
        ).fetchone()
        qa = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (proof['qa_invocation_id'],)
        ).fetchone()
        pm = db.execute(
            'SELECT * FROM governance_invocation WHERE invocation_id=?', (proof['pm_invocation_id'],)
        ).fetchone()
        latest = db.execute(
            '''SELECT v.* FROM governance_verification v JOIN governance_invocation i ON i.invocation_id=v.invocation_id
                             WHERE i.erp_project=? AND i.erp_task=? ORDER BY v.rowid DESC LIMIT 1''',
            (parent['erp_project'], proof['child_task_id']),
        ).fetchone()
        coordination = db.execute(
            'SELECT * FROM governance_pm_review WHERE qa_invocation_id=?', (proof['qa_invocation_id'],)
        ).fetchone()
    require(
        source
        and qa
        and pm
        and source['target_role'] == 'it_manager'
        and qa['target_role'] == 'qa'
        and pm['target_role'] == 'project_manager'
        and source['state'] == qa['state'] == 'verified'
        and all(
            (value['erp_project'], value['erp_task']) == (parent['erp_project'], proof['child_task_id'])
            for value in (source, qa, pm)
        )
        and len({value['agent_id'] for value in (source, qa, pm)}) == 3
        and latest
        and latest['invocation_id'] == source['invocation_id']
        and latest['qa_invocation_id'] == qa['invocation_id']
        and coordination
        and coordination['phase'] == 'completed'
        and coordination['review_invocation_id'] == pm['invocation_id'],
        'Dependency requires its latest independent verified IT/QA and completed PM',
    )
    require(
        proof['implementation_agent_id'] == source['agent_id']
        and proof['implementation_run_id'] == source['automation_run_id']
        and proof['qa_run_id'] == qa['automation_run_id']
        and proof['pm_run_id'] == pm['automation_run_id'],
        'Native dependency original role/run identities differ',
    )
    original = json.loads(source['work_packet'])
    tested = json.loads(qa['work_packet'])
    reviewed = json.loads(pm['work_packet'])
    require(
        original['task_revision'] == tested['task_revision'] == proof['child_task_revision']
        and original['srs']['sha256'] == tested['srs']['sha256'] == packet['srs']['sha256']
        and original['repository'] == packet['repository']
        and original['environment'] == packet['environment']
        and len({value['binding']['erp_user'] for value in (original, tested, reviewed)}) == 3,
        'Dependency current inputs, SRS, repository or role independence differ',
    )
    ready = _published(source['invocation_id'], path, 'ready_for_review')
    passed = _published(qa['invocation_id'], path, 'pass')
    acknowledged = _published(pm['invocation_id'], path, 'reviewed')
    stable = lambda receipt: {
        key: receipt[key] for key in ('event_id', 'run_id', 'task_id', 'modified', 'status', 'durable')
    }
    action_refs = [item for item in ready['result'].get('evidence', []) if item.get('id') == 'action-receipt']
    require(
        len(action_refs) == 1
        and action_refs[0].get('sha256') == proof['action_file_sha256']
        and ready['event'].get('event_id') == proof['ready_event_id'],
        'Original published physical File differs',
    )
    require(
        proof['ready_result_sha256'] == digest(ready['event'])
        and proof['ready_receipt_sha256'] == digest(stable(ready['receipt']))
        and proof['qa_result_sha256'] == digest(passed['result'])
        and proof['qa_receipt_sha256'] == digest(stable(passed['receipt']))
        and proof['pm_result_sha256'] == digest(acknowledged['result'])
        and proof['pm_receipt_sha256']
        == digest({key: value for key, value in acknowledged['receipt'].items() if key != 'duplicate'})
        and reviewed['qa_review']['qa_invocation_id'] == qa['invocation_id']
        and acknowledged['result'].get('qa_review_ack') == digest(reviewed['qa_review']),
        'Dependency native QA/PM publication differs from its original Core result',
    )
    with gateway._connect(path) as db:
        actions = db.execute(
            "SELECT * FROM governance_action WHERE invocation_id=? AND state='completed'", (source['invocation_id'],)
        ).fetchall()
    require(len(actions) == 1, 'Dependency needs one original physical action')
    action_row = actions[0]
    action = json.loads(action_row['action_json'])
    plan = json.loads(action_row['preflight_json'])
    consumed = json.loads(action_row['approval_json'] or 'null')
    actual = json.loads(action_row['result_json'] or 'null')
    template = original['tool_action']
    require(
        action['actor'] == original['binding']
        and action['project_id'] == parent['erp_project']
        and action['task_id'] == proof['child_task_id']
        and action['task_revision'] == proof['child_task_revision']
        and action['repository'] == packet['repository']
        and action['environment'] == packet['environment']
        and all(action[key] == template[key] for key in ('operation', 'risk', 'parameters'))
        and action['operation'] == proof['operation']
        and action['parameters'].get('resource_id') == proof['resource_id']
        and action['parameters'].get('plan_digest') == proof['plan_digest'] == plan_authority_digest(plan)
        and action_row['request_hash'] == gateway._digest([action_row['action_id'], action_fingerprint(action)]),
        'Dependency action is not its original exact operation/resource/plan',
    )
    require(
        consumed
        and consumed.get('consumed') is True
        and consumed['run_id'] == proof['implementation_run_id'] == source['automation_run_id']
        and consumed['invocation_id'] == source['invocation_id']
        and consumed['receipt_id'] == proof['consumed_approval_receipt_id']
        and consumed['action_id'] == proof['action_id'] == action_row['action_id']
        and consumed['action_fingerprint'] == proof['action_fingerprint'] == action_fingerprint(action)
        and digest(actual['original_receipt']) == proof['host_receipt_sha256'],
        'Dependency original host/consumption authority differs',
    )


async def _native(row, packet, config, bridge, path):
    require(bridge is not None, 'Dependency verification requires the existing governed bridge')
    _owned(row, packet, path)
    registered = policy(packet, config)
    mappings = required_mappings(packet, registered)
    require(mappings, 'No mapped physical acceptance for this parent')
    response = await bridge.erp.request(
        'GET',
        RPC,
        params={
            'project_id': row['erp_project'],
            'task_id': row['erp_task'],
            'role': row['target_role'],
            'run_id': row['automation_run_id'],
            'invocation_id': row['invocation_id'],
            'agent_id': row['agent_id'],
            'expected_task_revision': packet['task_revision'],
        },
        headers={'X-ABIT-Agentic-Bridge': bridge.secret},
    )
    bundle = response.get('message') or {}
    expected = {
        'schema_version': 1,
        'project_id': row['erp_project'],
        'parent_task_id': row['erp_task'],
        'parent_role': row['target_role'],
        'parent_invocation_id': row['invocation_id'],
        'parent_run_id': row['automation_run_id'],
        'parent_agent_id': row['agent_id'],
        'parent_task_revision': packet['task_revision'],
        'srs_sha256': packet['srs']['sha256'],
        'policy_sha256': digest(registered),
    }
    require(
        isinstance(bundle, dict)
        and set(bundle) == set(expected) | {'receipts'}
        and all(bundle.get(k) == v for k, v in expected.items())
        and isinstance(bundle['receipts'], list)
        and len(bundle['receipts']) == len(mappings),
        'Native current parent proof differs',
    )
    supplied = bundle['receipts']
    require(len({item['acceptance_id'] for item in supplied}) == len(supplied), 'Duplicate native physical mapping')
    require(
        [
            {key: item[key] for key in ('acceptance_id', 'requirement_id', 'operation', 'resource_id')}
            for item in supplied
        ]
        == mappings,
        'Native policy mapping differs from the approved current SRS',
    )
    for proof in supplied:
        _local_child(proof, row, packet, path)
    current = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    require(same_business_context(packet, current), 'Parent business context changed before physical read')
    return bundle


async def _read_current(row, packet, config, bridge, path, check_authority=None):
    if check_authority:
        await check_authority()
    bundle = await _native(row, packet, config, bridge, path)
    observations = []
    selected = config.get('agentic_it_physical_broker') or {}
    require(selected.get('enabled') is True, 'Dependency verification requires the fixed physical broker')
    for proof in bundle['receipts']:
        payload = {
            key: proof[key]
            for key in (
                'action_id',
                'plan_digest',
                'child_task_id',
                'implementation_invocation_id',
                'implementation_run_id',
                'acceptance_id',
                'requirement_id',
                'operation',
                'resource_id',
                'host_receipt_sha256',
            )
        }
        payload.update(
            {
                key: bundle[key]
                for key in (
                    'project_id',
                    'parent_task_id',
                    'parent_role',
                    'parent_invocation_id',
                    'parent_run_id',
                    'parent_agent_id',
                    'parent_task_revision',
                    'srs_sha256',
                    'policy_sha256',
                )
            }
        )
        payload['native_dependency_receipt_sha256'] = digest(bundle)

        async def authorize(operation, requested):
            require(
                operation == 'verify_dependency' and requested == payload,
                'Dependency broker target cannot be selected by the model',
            )
            require(
                await _native(row, packet, config, bridge, path) == bundle,
                'Native dependency proof changed before host admission',
            )
            if check_authority:
                await check_authority()
            return {
                'authorized': True,
                'mode': operation,
                'role': row['target_role'],
                **payload,
                'request_payload_sha256': digest(payload),
            }

        client = BrokerClient(selected['socket_path'], selected['key_file'], authorize=authorize)
        observed = await client.request('verify_dependency', payload)
        require(
            isinstance(observed, dict)
            and observed.get('kind') == 'parent_dependency_current_readback'
            and observed.get('verified') is True
            and observed.get('executed') is False
            and observed.get('receipt_mac')
            and all(observed.get(k) == v for k, v in payload.items())
            and type(observed.get('checked_at')) is int
            and 0 <= int(time.time()) - observed['checked_at'] <= 30,
            'Fresh physical dependency postcondition failed or belongs to another parent',
        )
        observations.append(observed)
    require(
        await _native(row, packet, config, bridge, path) == bundle, 'Native dependency changed during physical read'
    )
    if check_authority:
        await check_authority()
    return bundle, observations


async def _read(row, packet, config, bridge, path, check_authority=None):
    # Leave room inside the existing sixty-second authenticated edge deadline.
    # Timeout is an unconfirmed read, never authority to execute/replan anything.
    async with asyncio.timeout(50):
        return await _read_current(row, packet, config, bridge, path, check_authority)


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_dependency_verification(
        invocation_id TEXT NOT NULL,call_id TEXT NOT NULL,packet_sha256 TEXT NOT NULL,
        bundle_json TEXT NOT NULL,observations_json TEXT NOT NULL,result_json TEXT NOT NULL,
        PRIMARY KEY(invocation_id,call_id))''')
    for verb in ('UPDATE', 'DELETE'):
        db.execute(
            'CREATE TRIGGER IF NOT EXISTS dependency_no_'
            + verb.lower()
            + ' BEFORE '
            + verb
            + " ON governance_dependency_verification BEGIN SELECT RAISE(ABORT,'Dependency proof is immutable'); END"
        )


async def verify_dependencies(
    args, *, row, packet, config, bridge, db_path=None, gateway_call_id=None, check_authority=None, **kwargs
):
    require(
        args == {} and isinstance(gateway_call_id, str) and gateway_call_id,
        'Dependency verification derives every target server-side',
    )
    # A saved same-call result is READ-ONLY revalidated, never replaced/recomputed
    # into a different original. A changed host/Task requires explicit rework.
    with ledger.connect(db_path) as db:
        _schema(db)
        prior = db.execute(
            'SELECT * FROM governance_dependency_verification WHERE invocation_id=? AND call_id=?',
            (row['invocation_id'], gateway_call_id),
        ).fetchone()
    if prior:
        result = json.loads(prior['result_json'])
        if check_authority:
            await check_authority()
        require(
            await _native(row, packet, config, bridge, db_path) == json.loads(prior['bundle_json']),
            'Original dependency recovery inputs changed',
        )
        _install_artifacts(
            row, config, json.loads(prior['bundle_json']), json.loads(prior['observations_json']), result
        )
        await assert_dependency_evidence(
            row, packet, result, config, db_path, bridge=bridge, check_authority=check_authority
        )
        return result
    bundle, observations = await _read(row, packet, config, bridge, db_path, check_authority)
    refs, evidence, tests = [], [], []
    for proof, observed in zip(bundle['receipts'], observations):
        mapping = {key: proof[key] for key in ('acceptance_id', 'requirement_id', 'operation', 'resource_id')}
        ident = 'DEPENDENCY-' + digest([row['invocation_id'], gateway_call_id, proof['acceptance_id']])[:32]
        content = canonical(
            {
                'schema_version': 1,
                'parent': {key: value for key, value in bundle.items() if key != 'receipts'},
                'native_dependency': proof,
                'host_observation': observed,
            }
        ).encode()
        artifact = {
            'id': ident,
            'kind': 'test_log',
            'uri': 'artifact://' + ident + '.json',
            'sha256': hashlib.sha256(content).hexdigest(),
            'requirement_ids': [proof['requirement_id']],
        }
        evidence.append(artifact)
        refs.append(reference(mapping, policy(packet, config), proof, observed, ident))
        tests.append(
            {
                'test_id': proof['acceptance_id'],
                'status': 'pass',
                'observed': 'Fresh physical postcondition with exact descendant IT/QA/PM provenance',
                'evidence_ids': [ident],
            }
        )
    result = {'dependency_verifications': refs, 'evidence': evidence, 'test_results': tests}
    with ledger.connect(db_path) as db:
        _schema(db)
        db.execute('BEGIN IMMEDIATE')
        require(
            db.execute(
                'SELECT count(*) FROM governance_dependency_verification WHERE invocation_id=?', (row['invocation_id'],)
            ).fetchone()[0]
            < 10,
            'Parent dependency observation limit reached',
        )
        db.execute(
            'INSERT INTO governance_dependency_verification VALUES(?,?,?,?,?,?)',
            (
                row['invocation_id'],
                gateway_call_id,
                packet_digest(packet),
                canonical(bundle),
                canonical(observations),
                canonical(result),
            ),
        )
        db.commit()
    _install_artifacts(row, config, bundle, observations, result)
    return result


def _install_artifacts(row, config, bundle, observations, result):
    from open_webui.utils.agentic_gateway_operations import _store_artifact

    for artifact, proof, observed in zip(result['evidence'], bundle['receipts'], observations):
        content = canonical(
            {
                'schema_version': 1,
                'parent': {key: value for key, value in bundle.items() if key != 'receipts'},
                'native_dependency': proof,
                'host_observation': observed,
            }
        ).encode()
        require(hashlib.sha256(content).hexdigest() == artifact['sha256'], 'Original dependency intent bytes differ')
        _store_artifact(artifact['uri'].removeprefix('artifact://'), content, row=row, config=config)


async def status(args, *, row, packet, config, bridge, db_path=None, check_authority=None, **kwargs):
    require(args == {}, 'Dependency recovery accepts no caller-selected target')
    if check_authority:
        await check_authority()
    result = await latest_dependencies(row, packet, config, db_path, bridge=bridge)
    if check_authority:
        await check_authority()
    return result


async def assert_dependency_evidence(row, packet, result, config, db_path=None, *, bridge, check_authority=None):
    """Own-invocation receipts only, including saved-result publication recovery."""
    mappings = required_mappings(packet, policy(packet, config))
    refs = result.get('dependency_verifications') or []
    if not mappings:
        require(not refs, 'Unmapped dependency claims are denied')
        return {'dependency_verifications': [], 'evidence': [], 'test_results': []}
    require(refs, 'Mapped physical acceptance needs its own original gateway proof')
    with ledger.connect(db_path) as db:
        _schema(db)
        saved = db.execute(
            'SELECT * FROM governance_dependency_verification WHERE invocation_id=?', (row['invocation_id'],)
        ).fetchall()
    matches = [
        item
        for item in saved
        if item['packet_sha256'] == packet_digest(packet)
        and json.loads(item['result_json'])['dependency_verifications'] == refs
    ]
    require(len(matches) == 1, 'Dependency proofs were not produced by this same parent invocation')
    chosen = matches[0]
    original = json.loads(chosen['result_json'])
    bundle = json.loads(chosen['bundle_json'])
    observations = json.loads(chosen['observations_json'])
    from open_webui.utils.agentic_artifacts import artifact_directory, local_bytes

    actual = {item['id']: item for item in result.get('evidence', [])}
    for artifact, proof, observed in zip(original['evidence'], bundle['receipts'], observations):
        require(actual.get(artifact['id']) == artifact, 'Parent dependency evidence differs from its own original')
        _, raw = local_bytes(artifact_directory(row, config), artifact['uri'])
        require(
            hashlib.sha256(raw).hexdigest() == artifact['sha256']
            and json.loads(raw)
            == {
                'schema_version': 1,
                'parent': {key: value for key, value in bundle.items() if key != 'receipts'},
                'native_dependency': proof,
                'host_observation': observed,
            },
            'Immutable parent dependency evidence bytes differ',
        )
    fresh, current = await _read(row, packet, config, bridge, db_path, check_authority)
    require(
        fresh == bundle
        and [stable_observation(value) for value in current] == [stable_observation(value) for value in observations],
        'Parent dependency state changed after the original readback; no original proof replacement',
    )
    return original


async def latest_dependencies(row, packet, config, db_path=None, *, bridge):
    if not required_mappings(packet, policy(packet, config)):
        return {'dependency_verifications': [], 'evidence': [], 'test_results': []}
    with ledger.connect(db_path) as db:
        _schema(db)
        saved = db.execute(
            'SELECT * FROM governance_dependency_verification WHERE invocation_id=? ORDER BY rowid DESC LIMIT 1',
            (row['invocation_id'],),
        ).fetchone()
    require(saved, 'Run dependency.verify before publishing or accepting the implementation')
    value = json.loads(saved['result_json'])
    require(
        await _native(row, packet, config, bridge, db_path) == json.loads(saved['bundle_json']),
        'Original dependency recovery scope changed',
    )
    _install_artifacts(row, config, json.loads(saved['bundle_json']), json.loads(saved['observations_json']), value)
    return await assert_dependency_evidence(row, packet, value, config, db_path, bridge=bridge)

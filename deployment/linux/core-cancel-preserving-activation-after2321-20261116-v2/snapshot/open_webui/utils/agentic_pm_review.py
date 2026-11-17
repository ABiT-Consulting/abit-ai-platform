"""One actual PM coordination turn after independently verified QA publication.

The feature Task remains QA APPROVED. This separately fenced turn acknowledges
the immutable QA result, records its own evidence, and gates downstream work.
"""

from __future__ import annotations

import json
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import ERPBridge, canonical, digest, same_business_context
from open_webui.utils.agentic_result_store import get_result

ACTOR = 'verified-qa-to-pm'
STABLE_RECEIPT = ('event_id', 'run_id', 'task_id', 'modified', 'status', 'durable')


def receipt_identity(receipt):
    result = {key: receipt.get(key) for key in STABLE_RECEIPT}
    if (
        result['status'] != 'QA APPROVED'
        or result['durable'] is not True
        or any(not result[key] for key in STABLE_RECEIPT)
    ):
        raise ValueError('PM review requires a durable independent QA approval receipt')
    return result


def review_id(project, task, review, continuation=None):
    if continuation:
        return 'agov-pm-review-' + digest(['continuation', project, task, digest(review), continuation])[:48]
    return (
        'agov-pm-review-'
        + digest([project, task, review['qa_invocation_id'], review['qa_result_sha256'], review['qa_receipt_sha256']])[
            :48
        ]
    )


def artifact_target(row, receipt):
    if row.get('target_role') != 'project_manager' or not json.loads(row.get('work_packet') or '{}').get('qa_review'):
        raise ValueError('Only dedicated PM coordination may publish journal evidence')
    expected = {
        'doctype': 'AI Governance Record',
        'name': 'AGOV-' + digest(['pm_review_start', [row['erp_project'], row['invocation_id']]])[:48],
    }
    if receipt.get('artifact_target') != expected:
        raise ValueError('PM evidence target is not its original server-derived review journal')
    return expected


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_pm_review(
        qa_invocation_id TEXT PRIMARY KEY,project TEXT NOT NULL,task TEXT NOT NULL,
        review_invocation_id TEXT NOT NULL UNIQUE,source_sha256 TEXT NOT NULL,
        phase TEXT NOT NULL,receipt_sha256 TEXT,updated_at INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS governance_pm_review_turn(
        review_invocation_id TEXT PRIMARY KEY,qa_invocation_id TEXT NOT NULL,
        generation INTEGER NOT NULL,previous_invocation_id TEXT,source_sha256 TEXT NOT NULL,
        phase TEXT NOT NULL,receipt_sha256 TEXT,updated_at INTEGER NOT NULL,
        UNIQUE(qa_invocation_id,generation))''')
    db.execute(
        '''INSERT OR IGNORE INTO governance_pm_review_turn
        SELECT review_invocation_id,qa_invocation_id,0,NULL,source_sha256,phase,receipt_sha256,updated_at
        FROM governance_pm_review WHERE qa_invocation_id NOT IN (SELECT qa_invocation_id FROM governance_pm_review_turn)'''
    )


def _control_proof(db, project, task, continuation):
    command, sequence = continuation.get('resume_command_id'), continuation.get('resume_control_sequence')
    if not command or not isinstance(sequence, int):
        raise ValueError('Blocked PM review requires a fresh human Resume/Retry application')
    rows = db.execute(
        '''SELECT payload FROM governance_event WHERE erp_project=? AND kind='native_control_applied'
        AND json_extract(payload,'$.command_id')=? AND json_extract(payload,'$.control_sequence')=?''',
        (project, command, sequence),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError('PM review Resume/Retry has no original local control application')
    applied = json.loads(rows[0][0])
    scope = applied.get('task_id') or ''
    if scope not in {'', task} or applied.get('action') not in {'resume', 'retry'} or applied.get('state') != 'enabled':
        raise ValueError('PM review resume control scope or state differs')
    current = db.execute(
        'SELECT * FROM governance_native_control WHERE project=? AND task=?', (project, scope)
    ).fetchone()
    if (
        not current
        or current['sequence'] < sequence
        or current['phase'] != 'acknowledged'
        or current['applied_state'] != 'enabled'
    ):
        raise ValueError('PM review Resume/Retry lacks a current acknowledged enabled application')
    return {
        'project_id': project,
        'task_id': scope,
        'command_id': command,
        'control_sequence': sequence,
        'action': applied['action'],
        'applied_state': 'enabled',
    }


def _continuation(db, qa, packet, resume_receipt=None):
    value = packet.get('pm_review_continuation')
    if not value:
        return None
    previous = db.execute(
        'SELECT * FROM governance_invocation WHERE invocation_id=?', (value['previous_invocation_id'],)
    ).fetchone()
    turn = db.execute(
        'SELECT * FROM governance_pm_review_turn WHERE review_invocation_id=?', (value['previous_invocation_id'],)
    ).fetchone()
    saved = db.execute(
        'SELECT * FROM governance_result WHERE invocation_id=?', (value['previous_invocation_id'],)
    ).fetchone()
    if (
        not previous
        or not turn
        or not saved
        or turn['phase'] != 'held'
        or previous['state'] != 'blocked'
        or saved['phase'] != 'published'
    ):
        raise ValueError('PM continuation requires its exact published held checkpoint, never an uncertain run')
    before = json.loads(previous['work_packet'])
    result = json.loads(saved['result_json'])
    receipt = json.loads(saved['receipt_json'])
    if (
        turn['qa_invocation_id'] != qa['invocation_id']
        or before.get('qa_review') != packet.get('qa_review')
        or value['generation'] != turn['generation'] + 1
        or not 1 <= value['generation'] <= 3
        or value['previous_result_sha256'] != digest(result)
        or value['previous_receipt_sha256'] != digest(receipt_identity(receipt))
        or not same_business_context(before, packet, {'packet_id', 'content_sha256', 'pm_review_continuation'})
    ):
        raise ValueError('PM continuation QA, generation or original business context differs')
    if result['outcome'] == 'continue':
        if 'resume_command_id' in value or 'resume_control_sequence' in value:
            raise ValueError('Known PM continuation cannot substitute a human resume identity')
    elif result['outcome'] == 'blocked':
        expected = _control_proof(db, qa['erp_project'], qa['erp_task'], value)
        baseline = receipt.get('pm_review_control_sequence')
        if not isinstance(baseline, int) or value['resume_control_sequence'] <= baseline:
            raise ValueError('PM review Resume/Retry predates its held checkpoint')
        if (
            not resume_receipt
            or resume_receipt.get('exists') is not True
            or resume_receipt.get('durable') is not True
            or any(resume_receipt.get(k) != v for k, v in expected.items())
        ):
            raise ValueError('Native ERP did not confirm the exact human PM review Resume/Retry acknowledgement')
    else:
        raise ValueError('Only a known continue or explicitly resumed blocked PM review may continue')
    return value


def _verified_source(db, qa_id):
    qa = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (qa_id,)).fetchone()
    proof = db.execute('SELECT * FROM governance_verification WHERE qa_invocation_id=?', (qa_id,)).fetchone()
    if not qa or not proof or qa['target_role'] != 'qa' or qa['state'] != 'verified' or qa['qa_verdict'] != 'pass':
        raise ValueError('Only a verified independent QA publication may request PM coordination')
    source = db.execute(
        'SELECT * FROM governance_invocation WHERE invocation_id=?', (proof['invocation_id'],)
    ).fetchone()
    if (
        not source
        or source['state'] != 'verified'
        or (source['erp_project'], source['erp_task']) != (qa['erp_project'], qa['erp_task'])
    ):
        raise ValueError('PM review requires the verified original implementation')
    saved = db.execute('SELECT * FROM governance_result WHERE invocation_id=?', (qa_id,)).fetchone()
    if not saved or saved['phase'] != 'published' or saved['run_id'] != qa['automation_run_id']:
        raise ValueError('PM coordination requires the original published QA outcome')
    result, event, receipt = (json.loads(saved[name]) for name in ('result_json', 'event_json', 'receipt_json'))
    stable = receipt_identity(receipt)
    if (
        result.get('outcome') != 'pass'
        or result.get('invocation_id') != qa_id
        or result.get('implementation_invocation_id') != source['invocation_id']
        or result.get('implementation_agent_id') != source['agent_id']
        or proof['evidence_digest'] != digest(result['evidence'])
        or proof['erp_readback_digest'] not in {digest(receipt), digest(stable)}
        or stable['run_id'] != qa['automation_run_id']
        or stable['task_id'] != qa['erp_task']
        or stable['event_id'] != event.get('event_id')
        or event.get('type') != 'qa.passed'
    ):
        raise ValueError('PM coordination QA identity or verified receipt differs')
    review = {
        'qa_invocation_id': qa_id,
        'qa_run_id': qa['automation_run_id'],
        'qa_agent_id': qa['agent_id'],
        'implementation_invocation_id': source['invocation_id'],
        'implementation_run_id': source['automation_run_id'],
        'implementation_agent_id': source['agent_id'],
        'qa_result_sha256': digest(result),
        'qa_receipt_sha256': digest(stable),
        'result_contract': result,
        'receipt': stable,
        'artifacts': event['artifacts'],
    }
    return dict(qa), review


def enqueue_review(qa_id, packet, gate, db_path=None, *, resume_receipt=None):
    """Private trusted factory; generic invocation and handoff cannot use this mode."""
    with ledger._transaction(db_path) as db:
        _schema(db)
        qa, review = _verified_source(db, qa_id)
        if (
            packet.get('qa_review') != review
            or packet.get('project_id') != qa['erp_project']
            or packet.get('task_id') != qa['erp_task']
            or packet.get('task_revision') != qa['task_revision']
        ):
            raise ValueError('ERP PM review packet differs from the verified QA source')
        pm = db.execute(
            'SELECT agent_id,erp_user FROM governance_agent WHERE erp_project=? AND role=\'project_manager\'',
            (qa['erp_project'],),
        ).fetchone()
        if not pm or packet.get('binding') != {
            'project_id': qa['erp_project'],
            'role': 'project_manager',
            'agent_id': pm['agent_id'],
            'erp_user': pm['erp_user'],
        }:
            raise ValueError('PM coordination packet does not identify its registered Project Manager')
        users = {
            r[0]
            for r in db.execute(
                'SELECT erp_user FROM governance_agent WHERE agent_id IN (?,?)',
                (review['qa_agent_id'], review['implementation_agent_id']),
            )
        }
        if pm['erp_user'] in users:
            raise ValueError('PM coordination must use a separate ERP identity from QA and implementation')
        continuation = _continuation(db, qa, packet, resume_receipt)
        ident = review_id(qa['erp_project'], qa['erp_task'], review, continuation)
        previous = db.execute(
            'SELECT project,task,review_invocation_id,source_sha256 FROM governance_pm_review WHERE qa_invocation_id=?',
            (qa_id,),
        ).fetchone()
        expected = (qa['erp_project'], qa['erp_task'], ident, digest(review))
        existing_turn = db.execute(
            'SELECT * FROM governance_pm_review_turn WHERE review_invocation_id=?', (ident,)
        ).fetchone()
        if previous and not continuation and tuple(previous) != expected:
            # An old QA event may observe the current continuation; preserve it.
            raise ValueError('PM review factory did not return the current immutable coordination turn')
        if existing_turn:
            if existing_turn['qa_invocation_id'] != qa_id or existing_turn['source_sha256'] != digest(review):
                raise ValueError('Original PM coordination source is immutable')
            original = db.execute(
                'SELECT work_packet FROM governance_invocation WHERE invocation_id=?', (ident,)
            ).fetchone()
            if not original:
                raise ValueError('Original PM coordination invocation is missing; do not create a replacement')
            original_packet = json.loads(original['work_packet'])
            if not same_business_context(original_packet, packet, {'packet_id', 'content_sha256'}):
                raise ValueError('Original PM coordination business context changed before recovery')
            packet = original_packet
        result, created = ledger._enqueue(
            db,
            invocation_id=ident,
            project=qa['erp_project'],
            task=qa['erp_task'],
            from_role='qa',
            target_role='project_manager',
            task_revision=packet['task_revision'],
            context='Review the exact independent QA approval, record the downstream coordination decision, and acknowledge its immutable digest. Do not reopen or modify the feature Task.',
            work_packet=packet,
            source_invocation_id=qa_id,
            actor=ACTOR,
            gate=gate,
            _qa_review=True,
        )
        db.execute(
            'INSERT OR IGNORE INTO governance_pm_review VALUES(?,?,?,?,?,\'queued\',NULL,?)',
            (qa_id, *expected, int(time.time())),
        )
        if continuation and not existing_turn:
            if not previous or previous['review_invocation_id'] != continuation['previous_invocation_id']:
                raise ValueError('A different PM continuation already owns the latest head')
            db.execute(
                "UPDATE governance_pm_review SET review_invocation_id=?,phase='queued',receipt_sha256=NULL,updated_at=? WHERE qa_invocation_id=? AND review_invocation_id=?",
                (ident, int(time.time()), qa_id, continuation['previous_invocation_id']),
            )
        db.execute(
            "INSERT OR IGNORE INTO governance_pm_review_turn VALUES(?,?,?,?,?,'queued',NULL,?)",
            (
                ident,
                qa_id,
                continuation['generation'] if continuation else 0,
                continuation['previous_invocation_id'] if continuation else None,
                digest(review),
                int(time.time()),
            ),
        )
        return result, created


async def ensure_review(qa_invocation_id, *, config, project_gate, dispatch, db_path=None, bridge=None):
    owned = bridge is None
    bridge = bridge or ERPBridge(config)
    try:
        with ledger.connect(db_path) as db:
            _schema(db)
            qa, review = _verified_source(db, qa_invocation_id)
        _, gate = await project_gate(qa['erp_project'], config)
        packet = await bridge.packet(
            qa['erp_project'],
            qa['erp_task'],
            'project_manager',
            ledger.registered_agent_id(qa['erp_project'], 'project_manager', db_path),
        )
        resume_receipt = None
        continuation = packet.get('pm_review_continuation') or {}
        if continuation.get('resume_command_id'):
            with ledger.connect(db_path) as db:
                expected = _control_proof(db, qa['erp_project'], qa['erp_task'], continuation)
            response = await bridge.erp.request(
                'GET',
                '/api/method/abit_ai_assistant.agentic_control_fence.get_control_application_receipt',
                params={k: expected[k] for k in ('project_id', 'task_id', 'command_id', 'control_sequence')},
                headers={'X-ABIT-Agentic-Bridge': bridge.secret},
            )
            resume_receipt = response.get('message') or {}
        row, created = enqueue_review(qa_invocation_id, packet, gate, db_path, resume_receipt=resume_receipt)
        if created:
            await dispatch(row['invocation_id'])
        return row
    finally:
        if owned:
            await bridge.close()


def finish_review(invocation_id, receipt, db_path=None):
    saved = get_result(invocation_id, db_path)
    if not saved or saved['phase'] != 'published' or receipt_identity(receipt) != receipt_identity(saved['receipt']):
        raise ValueError('PM coordination requires its published original ERP journal receipt')
    with ledger._transaction(db_path) as db:
        _schema(db)
        row = db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?', (invocation_id,)).fetchone()
        packet = json.loads(row['work_packet']) if row else {}
        review = packet.get('qa_review')
        if not row or row['target_role'] != 'project_manager' or not review:
            raise ValueError('Only the dedicated PM coordination invocation may finish a review')
        _, current = _verified_source(db, review['qa_invocation_id'])
        if current != review or saved['result'].get('actor') != packet['binding']:
            raise ValueError('Original PM review scope or actor differs')
        result = saved['result']
        completed = result['outcome'] == 'reviewed'
        if receipt.get('pm_review_state') != ('reviewed' if completed else 'held'):
            raise ValueError('ERP PM journal did not confirm the exact reviewed or held outcome')
        if saved['event'].get('type') != ('pm.reviewed' if completed else 'pm.review.checkpoint'):
            raise ValueError('Published PM journal event has a different coordination outcome')
        if completed and (result.get('qa_review_ack') != digest(review) or not result.get('evidence')):
            raise ValueError('PM coordination did not acknowledge the exact QA result with evidence')
        if result['outcome'] not in {'reviewed', 'blocked', 'continue'}:
            raise ValueError('PM coordination cannot route an architecture or implementation result')
        entry = db.execute(
            'SELECT * FROM governance_pm_review_turn WHERE review_invocation_id=?', (invocation_id,)
        ).fetchone()
        if not entry or entry['source_sha256'] != digest(review):
            raise ValueError('PM coordination has no immutable source journal')
        if entry['phase'] in {'completed', 'held'}:
            if entry['receipt_sha256'] != digest(receipt_identity(receipt)):
                raise ValueError('PM review receipt changed after completion')
            return completed
        if row['state'] != 'execution_finished':
            raise ValueError('PM review execution has not finished')
        lease = db.execute(
            'SELECT invocation_id,fence FROM governance_task_lease WHERE erp_project=? AND erp_task=?',
            (row['erp_project'], row['erp_task']),
        ).fetchone()
        if not lease or tuple(lease) != (invocation_id, row['lease_fence']):
            raise ValueError('Original PM coordination no longer owns its Task fence')
        phase = 'completed' if completed else 'held'
        db.execute(
            'UPDATE governance_pm_review SET phase=?,receipt_sha256=?,updated_at=? WHERE review_invocation_id=?',
            (phase, digest(receipt_identity(receipt)), int(time.time()), invocation_id),
        )
        db.execute(
            'UPDATE governance_pm_review_turn SET phase=?,receipt_sha256=?,updated_at=? WHERE review_invocation_id=?',
            (phase, digest(receipt_identity(receipt)), int(time.time()), invocation_id),
        )
        db.execute(
            'UPDATE governance_invocation SET state=?,reason=?,updated_at=? WHERE invocation_id=?',
            (
                'completed' if completed else 'blocked',
                (
                    'PM reviewed independent QA'
                    if completed
                    else (
                        (
                            'PM continuation limit reached; human ERP intervention required. '
                            if entry['generation'] >= 3
                            else (
                                'PM review blocked; resolve the checkpoint, clear current vetoes and use human ERP Resume/Retry. '
                                if result['outcome'] == 'blocked'
                                else 'Known PM continuation checkpoint awaiting its next bounded coordination turn. '
                            )
                        )
                        + result['summary']
                    )[:500]
                ),
                int(time.time()),
                invocation_id,
            ),
        )
        ledger._release(db, row)
        ledger._event(
            db,
            row['erp_project'],
            invocation_id,
            'pm_review_' + phase,
            ACTOR,
            {'qa_invocation_id': review['qa_invocation_id'], 'qa_review_sha256': digest(review)},
        )
        return completed


def review_complete(project, task, db_path=None):
    with ledger.connect(db_path) as db:
        _schema(db)
        row = db.execute(
            '''SELECT r.phase FROM governance_verification v
            JOIN governance_invocation i ON i.invocation_id=v.invocation_id
            LEFT JOIN governance_pm_review r ON r.qa_invocation_id=v.qa_invocation_id
            WHERE i.erp_project=? AND i.erp_task=? ORDER BY v.rowid DESC LIMIT 1''',
            (project, task),
        ).fetchone()
        return bool(row and row[0] == 'completed')


def pending_reviews(project, db_path=None):
    """Latest verified QA per Task with no coordination journal; recovery only."""
    with ledger.connect(db_path) as db:
        _schema(db)
        rows = db.execute(
            '''SELECT v.qa_invocation_id FROM governance_verification v
            JOIN governance_invocation i ON i.invocation_id=v.invocation_id
            LEFT JOIN governance_pm_review r ON r.qa_invocation_id=v.qa_invocation_id
            WHERE i.erp_project=? AND (r.qa_invocation_id IS NULL OR r.phase='held')
            AND v.rowid=(SELECT MAX(v2.rowid) FROM governance_verification v2
                JOIN governance_invocation i2 ON i2.invocation_id=v2.invocation_id
                WHERE i2.erp_project=i.erp_project AND i2.erp_task=i.erp_task)
            ORDER BY v.rowid LIMIT 201''',
            (project,),
        ).fetchall()
        if len(rows) > 200:
            raise ValueError('PM review recovery exceeds the bounded project backlog; explicitly decompose it')
        return [row[0] for row in rows]

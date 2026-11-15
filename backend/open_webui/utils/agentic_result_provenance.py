"""Versioned, server-derived SA provenance compatibility; never a work verdict."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from open_webui.utils.agentic_bridge import canonical, digest, packet_digest, stable_run_id

POLICY = {
    'version': 'agov.sa-provenance-alias.v1',
    'role': 'solution_architect',
    'outcome': 'ready_for_review',
    'source_url_alias': 'exact_original_task_to_frozen_project_srs',
    'run_id_alias': 'exact_stable_run_to_original_invocation',
    'pointers': ['/architect_plan/metadata/source_url', '/architect_plan/metadata/run_id'],
}
POLICY_SHA256 = digest(POLICY)
AUDIT_KIND = 'result_provenance_projection'
_ROW_KEYS = (
    'invocation_id', 'erp_project', 'erp_task', 'target_role', 'agent_id',
    'automation_run_id', 'task_revision', 'packet_hash', 'lease_fence', 'submitted_at',
)


def reply_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def parse_original_reply(text):
    from open_webui.utils.erp_dot_workflow import parse_contract_json

    rows = [line[len('AGOV RESULT:'):].strip() for line in text.splitlines()
            if line.startswith('AGOV RESULT:')]
    if len(rows) != 1 or len(rows[0].encode()) > 200_000:
        raise ValueError('Exactly one bounded AGOV RESULT contract is required')
    return parse_contract_json(rows[0])


def original_identity(row, packet):
    """A caller supplies the server-loaded row, never request-provided scope."""
    if (
        row.get('state') not in {'running', 'uncertain'}
        or row.get('target_role') != 'solution_architect'
        or not row.get('lease_token')
        or type(row.get('lease_fence')) is not int or row['lease_fence'] < 1
        or type(row.get('submitted_at')) is not int or row['submitted_at'] < 1
        or row.get('automation_run_id') != stable_run_id(row.get('invocation_id', ''))
        or row.get('packet_hash') != digest(packet)
        or json.loads(row.get('work_packet') or 'null') != packet
        or packet.get('content_sha256') != packet_digest(packet)
        or packet.get('project_id') != row.get('erp_project')
        or packet.get('task_id') != row.get('erp_task')
        or packet.get('task_revision') != row.get('task_revision')
        or packet.get('binding', {}).get('project_id') != row.get('erp_project')
        or packet.get('binding', {}).get('agent_id') != row.get('agent_id')
        or packet.get('binding', {}).get('role') != row.get('target_role')
        or not re.fullmatch(r'PROJ-[A-Za-z0-9-]+', row.get('erp_project', ''))
        or not re.fullmatch(r'TASK-[A-Za-z0-9-]+', row.get('erp_task', ''))
    ):
        raise ValueError('Original SA provenance row or frozen packet changed')
    proof = {key: row[key] for key in _ROW_KEYS}
    proof['lease_token_sha256'] = reply_sha256(row['lease_token'])
    return proof


def project_provenance(raw_result, *, row, packet, raw_reply_sha256):
    """Project only authenticated machine provenance; validate semantics afterward.

    The input is never changed. This policy does not accept URLs generally,
    derive claims from model text, change a verdict, or grant write authority.
    """
    identity = original_identity(row, packet)
    if not re.fullmatch(r'[a-f0-9]{64}', raw_reply_sha256 or ''):
        raise ValueError('Original raw reply digest is required')
    result = deepcopy(raw_result)
    if (result.get('actor', {}).get('role') != POLICY['role']
            or result.get('outcome') != POLICY['outcome']
            or not isinstance(result.get('architect_plan'), dict)):
        return result, None
    meta = result['architect_plan'].get('metadata') or {}
    binding, srs = packet['binding'], packet['srs']
    plan_binding = {'project_id': binding['project_id'], 'role': binding['role'],
                    'abit_agent_id': binding['agent_id'], 'erp_ai_user': binding['erp_user']}
    if (
        type(result.get('schema_version')) is not int or result['schema_version'] != 1
        or result.get('invocation_id') != row['invocation_id']
        or result.get('project_id') != packet['project_id']
        or result.get('task_id') != packet['task_id']
        or result.get('task_revision') != packet['task_revision']
        or result.get('work_packet_digest') != packet['content_sha256']
        or result.get('actor') != binding
        or meta.get('project_id') != packet['project_id']
        or meta.get('agent_binding') != plan_binding
        or meta.get('srs_version') != srs['revision']
        or meta.get('srs_sha256') != srs['sha256']
        or meta.get('source_activity_revision') != packet['source_modified']
        or any(meta.get('project_backlog_' + key) != packet['project_backlog'][key]
               for key in ('context_sha256', 'snapshot_sha256'))
    ):
        raise ValueError('SA provenance requires the exact actor, SRS and frozen packet')
    expected = {
        'source_url': 'https://erp.abit.ai/desk/project/' + packet['project_id'],
        'run_id': row['invocation_id'],
    }
    aliases = {
        'source_url': 'https://erp.abit.ai/desk/task/' + packet['task_id'],
        'run_id': row['automation_run_id'],
    }
    changes = []
    for field in ('source_url', 'run_id'):
        before, after = meta.get(field), expected[field]
        if before not in (after, aliases[field]):
            raise ValueError('SA provenance is not an exact original identity alias')
        if before != after:
            meta[field] = after
            changes.append({'pointer': '/architect_plan/metadata/' + field,
                            'before_sha256': digest(before), 'after_sha256': digest(after)})
    if not changes:
        return result, None
    # Retain an explicit assertion that every non-policy field remains exact.
    restored = deepcopy(result)
    for field in ('source_url', 'run_id'):
        restored['architect_plan']['metadata'][field] = raw_result['architect_plan']['metadata'][field]
    if restored != raw_result:
        raise ValueError('Provenance projection changed semantic result content')
    projection = {
        'policy_version': POLICY['version'], 'policy_sha256': POLICY_SHA256,
        'invocation_id': row['invocation_id'], 'run_id': row['automation_run_id'],
        'row_sha256': digest(identity), 'packet_sha256': digest(packet),
        'work_packet_digest': packet['content_sha256'], 'raw_reply_sha256': raw_reply_sha256,
        'raw_result_sha256': digest(raw_result), 'canonical_result_sha256': digest(result),
        'allowed_pointers': list(POLICY['pointers']), 'changes': changes,
    }
    return result, projection

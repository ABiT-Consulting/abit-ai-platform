"""Typed physical acceptance, separate from command execution (proposal v1).

Pure validation never turns a supplied proof into authority. Core, native ERP,
and the protected broker must each derive/check the referenced original facts.
"""

from __future__ import annotations

import hashlib
import json
import re

SHA = re.compile(r'[a-f0-9]{64}')
ID = re.compile(r'[A-Za-z0-9_.:@+-]{1,160}')
OPERATIONS = frozenset({'install_official_package', 'restart_service', 'extend_capacity'})
POLICY_FIELDS = {'schema_version', 'project_id', 'srs_sha256', 'mappings'}
MAPPING_FIELDS = {'acceptance_id', 'requirement_id', 'operation', 'resource_id'}
REF_FIELDS = MAPPING_FIELDS | {
    'child_task_id',
    'child_task_revision',
    'implementation_invocation_id',
    'implementation_run_id',
    'qa_invocation_id',
    'pm_invocation_id',
    'policy_sha256',
    'native_receipt_sha256',
    'host_receipt_sha256',
    'observation_sha256',
    'log_evidence_id',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def policy_for(packet, registered):
    """An exact server policy is required; child requirement IDs grant nothing."""
    advertised = packet.get('dependency_acceptance_policy')
    if advertised is None and registered is None:
        return None
    require(
        isinstance(registered, dict) and advertised == registered,
        'Dependency acceptance requires the exact server-owned policy',
    )
    policy = registered
    require(
        set(policy) == POLICY_FIELDS
        and type(policy['schema_version']) is int
        and policy['schema_version'] == 1
        and policy['project_id'] == packet['project_id']
        and policy['srs_sha256'] == packet['srs']['sha256']
        and isinstance(policy['srs_sha256'], str)
        and SHA.fullmatch(policy['srs_sha256']),
        'Dependency policy Project or SRS differs',
    )
    mappings = policy['mappings']
    require(isinstance(mappings, list) and 1 <= len(mappings) <= 20, 'Bounded dependency mapping required')
    features = {feature['requirement_id'] for feature in packet['srs']['features']}
    accepted, requirements, resources = set(), set(), set()
    for item in mappings:
        require(
            isinstance(item, dict)
            and set(item) == MAPPING_FIELDS
            and all(isinstance(v, str) and ID.fullmatch(v) for v in item.values())
            and item['operation'] in OPERATIONS
            and item['requirement_id'] in features
            and item['acceptance_id'] == item['requirement_id'] + '.acceptance',
            'Exact SRS feature and physical operation/resource mapping required',
        )
        resource = (item['operation'], item['resource_id'])
        require(
            item['acceptance_id'] not in accepted
            and item['requirement_id'] not in requirements
            and resource not in resources,
            'Duplicate or conflicting dependency mapping',
        )
        accepted.add(item['acceptance_id'])
        requirements.add(item['requirement_id'])
        resources.add(resource)
    require(
        mappings == sorted(mappings, key=lambda item: item['acceptance_id']),
        'Dependency policy must have a deterministic mapping order',
    )
    return policy


def required_mappings(packet, policy):
    if policy is None:
        return []
    requirements = set(packet['requirement_ids'])
    selected = [item for item in policy['mappings'] if item['requirement_id'] in requirements]
    tests = {item['id']: item for item in packet['acceptance_tests']}
    for item in selected:
        require(
            item['acceptance_id'] in tests
            and tests[item['acceptance_id']]['requirement_ids'] == [item['requirement_id']],
            'Physical feature needs its distinct native acceptance test',
        )
    return selected


def ancestry(parent, child, edges):
    """Exact persisted parent chain only; no title/requirement or owner inference."""
    require(isinstance(edges, list) and 1 <= len(edges) <= 10, 'Bounded descendant ancestry required')
    current, seen = child, {child}
    for item in edges:
        require(
            isinstance(item, dict)
            and set(item) == {'child_task_id', 'parent_task_id', 'task_revision', 'source_run_id', 'request_sha256'}
            and item['child_task_id'] == current
            and isinstance(item['task_revision'], str)
            and SHA.fullmatch(item['task_revision'])
            and isinstance(item['request_sha256'], str)
            and SHA.fullmatch(item['request_sha256'])
            and all(
                isinstance(item[key], str) and ID.fullmatch(item[key])
                for key in ('child_task_id', 'parent_task_id', 'source_run_id')
            ),
            'Unverified or disconnected descendant creation edge',
        )
        current = item['parent_task_id']
        require(current not in seen, 'Dependency ancestry cycle denied')
        seen.add(current)
    require(current == parent and parent != child, 'Dependency belongs to another parent Task')


def validate_ref(value):
    require(isinstance(value, dict) and set(value) == REF_FIELDS, 'Exact typed dependency reference required')
    for key, item in value.items():
        pattern = SHA if key.endswith('_sha256') or key == 'child_task_revision' else ID
        require(isinstance(item, str) and pattern.fullmatch(item), 'Malformed typed dependency ' + key)
    require(value['operation'] in OPERATIONS, 'Unsupported physical dependency operation')


def coverage(packet, policy, repo_runs, refs, evidence, observed):
    """Exact disjoint union; physical reads never acquire a fake command/exit code."""
    mappings = {item['acceptance_id']: item for item in required_mappings(packet, policy)}
    expected = {test['id'] for test in packet['acceptance_tests']}
    actual, resources = {}, set()
    for item in refs:
        validate_ref(item)
        ident = item['acceptance_id']
        resource = (item['operation'], item['resource_id'])
        require(
            ident in mappings
            and ident not in actual
            and resource not in resources
            and all(item[key] == mappings[ident][key] for key in MAPPING_FIELDS)
            and item['policy_sha256'] == digest(policy),
            'Dependency is duplicate, conflicting or outside policy',
        )
        log = evidence.get(item['log_evidence_id'], {})
        require(
            log.get('kind') == 'test_log' and log.get('requirement_ids') == [item['requirement_id']],
            'Physical dependency requires its own precisely scoped evidence',
        )
        require(
            observed.get(ident, {}).get('status') == 'pass'
            and item['log_evidence_id'] in observed.get(ident, {}).get('evidence_ids', []),
            'Physical acceptance must reference its exact independent readback',
        )
        actual[ident] = item
        resources.add(resource)
    require(set(actual) == set(mappings), 'Every mapped physical feature needs a typed dependency proof')
    repo_ids = {ident for run in repo_runs for ident in run['test_ids']}
    require(not repo_ids & set(actual), 'Repository tests cannot claim physical acceptance')
    require(repo_ids | set(actual) == expected, 'Repository and physical proof union must equal every acceptance ID')


def reference(mapping, policy, proof, observation, evidence_id):
    value = {
        **mapping,
        **{
            key: proof[key]
            for key in (
                'child_task_id',
                'child_task_revision',
                'implementation_invocation_id',
                'implementation_run_id',
                'qa_invocation_id',
                'pm_invocation_id',
            )
        },
        'policy_sha256': digest(policy),
        'native_receipt_sha256': digest(proof),
        'host_receipt_sha256': proof['host_receipt_sha256'],
        'observation_sha256': digest(observation),
        'log_evidence_id': evidence_id,
    }
    validate_ref(value)
    return value


def stable_observation(value):
    result = {key: item for key, item in value.items() if key not in {'checked_at', 'receipt_mac'}}
    # Match the broker's existing postcondition comparison. These are read-time
    # samples only; the actual verifier still checks current safety thresholds.
    # All physical identity/content/version/geometry/health fields remain exact.
    if isinstance(result.get('observed'), dict):
        result['observed'] = {
            key: item for key, item in result['observed'].items() if key not in {'checked_at', 'free_bytes'}
        }
    return result

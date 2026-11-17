"""Root-broker read-only descendant verification; no implementer impersonation.

The existing peer/MAC challenge validates CURRENT parent authority. The Root
registry independently pins SRS feature -> operation/resource, and reads the
completed original from its protected execution journal before a fresh probe.
"""

from __future__ import annotations
from copy import deepcopy
import hashlib
import hmac
import time

from open_webui.utils.agentic_contracts.dependency_verification import canonical, digest, require, stable_observation

PAYLOAD_FIELDS = frozenset(
    {
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
        'project_id',
        'parent_task_id',
        'parent_role',
        'parent_invocation_id',
        'parent_run_id',
        'parent_agent_id',
        'parent_task_revision',
        'srs_sha256',
        'policy_sha256',
        'native_dependency_receipt_sha256',
    }
)


def check_grant(payload, grant):
    require(
        isinstance(payload, dict)
        and set(payload) == PAYLOAD_FIELDS
        and isinstance(grant, dict)
        and grant.get('authorized') is True
        and grant.get('mode') == 'verify_dependency'
        and grant.get('role') == payload['parent_role']
        and payload['parent_role'] in {'developer', 'qa'}
        and payload['child_task_id'] != payload['parent_task_id']
        and all(grant.get(key) == value for key, value in payload.items())
        and grant.get('request_payload_sha256') == digest(payload),
        'Current parent dependency read authority differs',
    )


async def verify_dependency(resources, payload):
    """Only registered Root resources may invoke this callback; no JSON reader."""
    require(isinstance(payload, dict) and set(payload) == PAYLOAD_FIELDS, 'Exact parent dependency payload required')
    profile = resources.profile
    policy = profile.get('dependency_acceptance_policy')
    require(
        isinstance(policy, dict)
        and policy.get('project_id') == payload['project_id'] == profile.get('project_id')
        and policy.get('srs_sha256') == payload['srs_sha256']
        and digest(policy) == payload['policy_sha256'],
        'Protected Root SRS dependency policy differs',
    )
    matches = [item for item in policy.get('mappings', []) if item.get('acceptance_id') == payload['acceptance_id']]
    require(
        len(matches) == 1
        and all(
            matches[0].get(key) == payload[key]
            for key in ('acceptance_id', 'requirement_id', 'operation', 'resource_id')
        ),
        'Protected Root feature/operation/resource binding differs',
    )
    plan, receipt = await resources.executor.original_plan(
        payload['action_id'], payload['implementation_invocation_id'], payload['plan_digest']
    )
    require(
        plan['scope']['project_id'] == payload['project_id']
        and plan['scope']['task_id'] == payload['child_task_id']
        and plan['operation'] == payload['operation']
        and plan['resource_id'] == payload['resource_id']
        and receipt['run_id'] == payload['implementation_run_id']
        and receipt['state'] == 'completed'
        and receipt['action_id'] == payload['action_id']
        and receipt['invocation_id'] == payload['implementation_invocation_id']
        and digest(receipt) == payload['host_receipt_sha256'],
        'Protected original physical resource/receipt differs',
    )
    handler = resources.executor.handlers[(plan['operation'], plan['resource_id'])]
    current = await handler.verify_postcondition(deepcopy(plan))
    require(
        isinstance(current, dict) and current.get('verified') is True,
        'Current physical dependency postcondition failed',
    )
    stable = lambda value: {key: item for key, item in value.items() if key not in {'checked_at', 'free_bytes'}}
    require(
        stable(current.get('observed', {})) == stable(receipt['postcondition'].get('observed', {})),
        'Physical dependency was changed after its original completion',
    )
    result = {
        'kind': 'parent_dependency_current_readback',
        'verified': True,
        'executed': False,
        **payload,
        'original_plan_sha256': digest(plan),
        'observed': current['observed'],
        'checked_at': int(time.time()),
    }
    result['receipt_mac'] = hmac.new(
        resources.executor._key, b'abit-it-parent-dependency-v1\0' + canonical(result).encode(), hashlib.sha256
    ).hexdigest()
    return result

"""Prepare exact, server-pinned IT changes; no default mutating executor.

Model inputs select a resource ID and an existing plan digest. They never choose
a URL, package/version, service unit, volume, path, command or executable. The
gateway retains ERP authority, one-use approval and durable uncertainty fencing.
Official-source signature verification and host execution are server callbacks;
their absence produces an inspectable unavailable plan, never a claimed action.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import socket
import stat
import time
from urllib.parse import urlsplit

from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable, TrustedAdapter

OPERATIONS = frozenset({'install_official_package', 'restart_service', 'extend_capacity'})
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}')
SHA = re.compile(r'[a-f0-9]{64}')
PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]{0,99}')
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9:.+~_-]{0,119}')
UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,150}\.service')
MAX_PLAN_BYTES = 48_000
MAX_OBSERVATION_AGE = 300


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


async def _call(callback, *args):
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


async def _observe(callback, args, missing, code, description):
    if callback is None:
        missing(code, description)
        return None
    try:
        return await _call(callback, *args)
    except Exception:
        # A plan remains inspectable when a legitimate host reader lacks access.
        # Never export a callback's raw exception/path/credential arguments.
        missing(code, description)
        return None


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, limit=1000):
    return isinstance(value, str) and 0 < len(value) <= limit


def _canonical_path(value):
    return isinstance(value, str) and value.startswith('/') and '..' not in Path(value).parts and '\\' not in value


def _fresh(observation, host, now):
    return (
        isinstance(observation, dict)
        and observation.get('host_id') == host
        and type(observation.get('checked_at')) is int
        and 0 <= now - observation['checked_at'] <= MAX_OBSERVATION_AGE
    )


class OfficialSignatureVerifier:
    """Server code supplies cryptographic vendor/catalog verification, not JSON."""

    def __init__(self, verify):
        _require(callable(verify), 'Official signature verifier must be a server callable')
        self.verify = verify


class ChangeReaders:
    def __init__(self, *, safety=None, package=None, service=None, storage=None, artifact=None, verifier=None):
        for name, value in {
            'safety': safety,
            'package': package,
            'service': service,
            'storage': storage,
            'artifact': artifact,
        }.items():
            _require(value is None or callable(value), name + ' reader must be a server callable')
            setattr(self, name, value)
        _require(
            verifier is None or isinstance(verifier, OfficialSignatureVerifier),
            'Verifier cannot be a boolean or serialized assertion',
        )
        self.verifier = verifier


class TrustedChangeExecutor:
    """Registered server capability. This module never creates one by default.

    execute_once must durably deduplicate (action, invocation, plan), recheck the
    physical file/unit identity immediately at dispatch and return the original
    receipt. A transport that cannot guarantee that contract is not an executor.
    """

    def __init__(self, execute_once, observe_original):
        _require(
            callable(execute_once) and callable(observe_original),
            'Executor requires execute-once and read-only receipt callbacks',
        )
        self.execute_once = execute_once
        self.observe_original = observe_original


class PreparedChangeUnavailable(CapabilityUnavailable):
    def __init__(self, plan):
        self.plan = deepcopy(plan)
        self.artifact = plan_artifact(plan)
        super().__init__('Prepared IT change is unavailable: ' + ', '.join(x['code'] for x in plan['prerequisites']))


class ChangeOutcomeUncertain(RuntimeError):
    """A host intent may have executed. Observe its receipt; never repeat it."""


def plan_artifact(plan):
    content = canonical(plan).encode()
    _require(len(content) <= MAX_PLAN_BYTES, 'Prepared plan exceeds the evidence limit')
    return {
        'filename': 'it-change-plan-' + plan['plan_digest'][:24] + '.json',
        'content': content,
        'sha256': hashlib.sha256(content).hexdigest(),
        'kind': 'checkpoint',
        'executed': False,
    }


def _scope(action, scope, profile):
    _require(isinstance(action, dict) and action.get('operation') in OPERATIONS, 'Unsupported change operation')
    _require(isinstance(scope, dict) and isinstance(profile, dict), 'Server scope/profile required')
    for key in ('project_id', 'task_id', 'task_revision', 'repository', 'environment'):
        _require(_text(action.get(key), 500) and action[key] == scope.get(key), 'Action scope differs: ' + key)
    _require(
        action.get('actor') == scope.get('binding') and (action.get('actor') or {}).get('role') == 'it_manager',
        'Change requires the exact bound IT specialist',
    )
    _require(
        action.get('risk') in {'privileged', 'destructive', 'sensitive'},
        'IT changes require an explicit privileged risk classification',
    )
    _require(profile.get('host_id') == socket.gethostname(), 'Profile host differs from the execution server')
    _require(
        profile.get('environment_class') in {'development', 'test', 'staging', 'production'},
        'Explicit environment classification required',
    )
    parameters = action.get('parameters')
    _require(
        isinstance(parameters, dict) and set(parameters) <= {'resource_id', 'plan_digest'},
        'Change parameters may select resource_id and plan_digest only',
    )
    _require(
        isinstance(parameters.get('resource_id'), str) and ID.fullmatch(parameters['resource_id']),
        'Server resource ID required',
    )
    if 'plan_digest' in parameters:
        _require(
            isinstance(parameters['plan_digest'], str) and SHA.fullmatch(parameters['plan_digest']),
            'Invalid prepared plan digest',
        )
    return parameters['resource_id']


def _record(action, scope, profile, resource_id):
    record = ((profile.get('change_resources') or {}).get(action['operation']) or {}).get(resource_id)
    if not isinstance(record, dict):
        return None
    for key, expected in [
        ('project_id', scope['project_id']),
        ('host_id', profile['host_id']),
        ('environment', scope['environment']),
        ('repository', scope['repository']),
    ]:
        _require(record.get(key) == expected, 'Server resource ownership differs: ' + key)
    _require(record.get('enabled') is True, 'Server resource is not enabled')
    _require(
        record.get('exclusive_to_project') is True,
        'Shared tenant changes require a separately scoped server capability',
    )
    return deepcopy(record)


def _meaningful_safety(value):
    # Observation time may refresh without changing a backup/restore policy.
    # Every authority-bearing identity/hash/expiry/impact remains in the digest.
    fields = {
        'backup': ('verified', 'reference', 'sha256', 'expires_at'),
        'restore': ('verified', 'reference', 'sha256', 'expires_at'),
        'retention': ('verified', 'policy_sha256', 'expires_at'),
        'capacity': ('filesystem_id', 'free_bytes', 'total_bytes'),
        'impact': ('reviewed', 'exclusive_to_project', 'sha256'),
        'privilege': ('authorized', 'effective_uid'),
    }
    return {
        key: {field: deepcopy((value.get(key) or {}).get(field)) for field in names if field in (value.get(key) or {})}
        for key, names in fields.items()
    }


def _safety(observation, scope, profile, record, now, missing):
    if not _fresh(observation, profile['host_id'], now):
        missing(
            'FRESH_CHANGE_PREFLIGHT_UNAVAILABLE',
            'A current server backup, restore, retention, capacity and impact readback is required.',
        )
        return {}
    _require(
        observation.get('project_id') == scope['project_id'] and observation.get('environment') == scope['environment'],
        'Safety readback scope differs',
    )
    required = {
        'backup': ('verified', 'reference', 'sha256'),
        'restore': ('verified', 'reference', 'sha256'),
        'retention': ('verified', 'policy_sha256'),
    }
    for name, fields in required.items():
        value = observation.get(name) or {}
        if (
            value.get(fields[0]) is not True
            or any(not _text(value.get(k), 500) for k in fields[1:])
            or any(not SHA.fullmatch(str(value.get(k, ''))) for k in fields if k.endswith('sha256'))
            or type(value.get('expires_at')) is not int
            or value['expires_at'] <= now
            or 'reference' in fields
            and not ID.fullmatch(str(value.get('reference', '')))
        ):
            missing(
                name.upper() + '_PROOF_UNAVAILABLE', 'Verified ' + name + ' identity and policy evidence are required.'
            )
    capacity = observation.get('capacity') or {}
    minimum = record.get('minimum_free_bytes')
    _require(type(minimum) is int and minimum >= 0, 'Explicit nonnegative free-capacity pin required')
    if (
        not _text(capacity.get('filesystem_id'), 160)
        or type(capacity.get('free_bytes')) is not int
        or type(capacity.get('total_bytes')) is not int
        or not 0 <= capacity['free_bytes'] <= capacity['total_bytes']
        or capacity['free_bytes'] < minimum
    ):
        missing('CAPACITY_PREFLIGHT_UNSATISFIED', 'The fixed filesystem must have the server-pinned free capacity.')
    impact = observation.get('impact') or {}
    if (
        impact.get('reviewed') is not True
        or impact.get('exclusive_to_project') is not True
        or not SHA.fullmatch(str(impact.get('sha256', '')))
    ):
        missing('SERVICE_IMPACT_UNVERIFIED', 'The exact project-exclusive service impact must be reviewed.')
    privilege = observation.get('privilege') or {}
    _require(
        type(profile.get('expected_uid')) is int and profile['expected_uid'] >= 0,
        'Server operator identity pin required',
    )
    if (
        privilege.get('authorized') is not True
        or type(privilege.get('effective_uid')) is not int
        or privilege.get('effective_uid') != profile['expected_uid']
    ):
        missing(
            'HOST_PRIVILEGE_UNAVAILABLE', 'A server-owned operator capability for the exact host identity is required.'
        )
    return _meaningful_safety(observation)


async def prepare_change(action, scope, profile, *, readers, executor=None, grant_checker=None, now=None):
    """Return an ERP-reviewable plan even when a trusted physical capability is absent."""
    _require(isinstance(readers, ChangeReaders), 'Readers must be registered server objects')
    _require(
        executor is None or isinstance(executor, TrustedChangeExecutor),
        'Executor cannot be serialized or model supplied',
    )
    _require(grant_checker is None or callable(grant_checker), 'Approval reader must be a server callable')
    now = int(time.time()) if now is None else int(now)
    resource_id = _scope(action, scope, profile)
    record = _record(action, scope, profile, resource_id)
    prerequisites = []

    def missing(code, description):
        if not any(x['code'] == code for x in prerequisites):
            prerequisites.append({'code': code, 'description': description})

    plan = {
        'schema_version': 1,
        'kind': 'it_change_plan',
        'operation': action['operation'],
        'resource_id': resource_id,
        'scope': {
            key: deepcopy(action[key]) for key in ('project_id', 'task_id', 'actor', 'repository', 'environment')
        },
        'task_revision': action['task_revision'],
        'host_id': profile['host_id'],
        'prepared_at': now,
        'execution_state': 'not_executed',
        'prerequisites': prerequisites,
        'target': {},
        'baseline': {},
        'fixed_argv': [],
    }
    if record is None:
        missing(
            'SERVER_RESOURCE_PROFILE_UNAVAILABLE',
            'An administrator must pin this resource and its ownership, target and safeguards.',
        )
        record = {}
    source = {}
    if record:
        observation = await _observe(
            readers.safety,
            (record, scope, profile),
            missing,
            'FRESH_CHANGE_PREFLIGHT_UNAVAILABLE',
            'A current server backup, restore, retention, capacity and impact readback is required.',
        )
        plan['safety'] = _safety(observation, scope, profile, record, now, missing)
        if action['operation'] == 'install_official_package':
            _require(
                PACKAGE.fullmatch(str(record.get('package', ''))) and VERSION.fullmatch(str(record.get('version', ''))),
                'Exact server-pinned package/version required',
            )
            _require(
                ID.fullmatch(str(record.get('architecture', '')))
                and SHA.fullmatch(str(record.get('artifact_sha256', ''))),
                'Architecture and package SHA256 required',
            )
            source_id = record.get('official_source_id')
            source = (profile.get('official_sources') or {}).get(source_id) or {}
            origin = urlsplit(str(source.get('origin', '')))
            _require(
                isinstance(source_id, str)
                and ID.fullmatch(source_id)
                and origin.scheme == 'https'
                and origin.netloc
                and not origin.username
                and not origin.password
                and not origin.query
                and not origin.fragment,
                'Server-pinned official source origin required',
            )
            _require(
                re.fullmatch(r'[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}', str(source.get('signer_fingerprint', '')))
                and SHA.fullmatch(str(source.get('catalog_sha256', ''))),
                'Official signer/catalog hash must be pinned',
            )
            plan['target'] = {
                key: deepcopy(record[key]) for key in ('package', 'version', 'architecture', 'artifact_sha256')
            }
            plan['target']['official_source'] = {
                key: source[key] for key in ('origin', 'signer_fingerprint', 'catalog_sha256')
            }
            before = await _observe(
                readers.package,
                (record, profile),
                missing,
                'PACKAGE_READBACK_UNAVAILABLE',
                'Current installed package/version readback is required.',
            )
            if not _fresh(before, profile['host_id'], now):
                missing('PACKAGE_READBACK_UNAVAILABLE', 'Current installed package/version readback is required.')
            else:
                _require(
                    before.get('package') == record['package'] and type(before.get('installed')) is bool,
                    'Installed package readback differs',
                )
                _require(
                    not before['installed']
                    or VERSION.fullmatch(str(before.get('version', '')))
                    and ID.fullmatch(str(before.get('architecture', ''))),
                    'Installed package identity is malformed',
                )
                plan['baseline'] = {key: before.get(key) for key in ('package', 'installed', 'version', 'architecture')}
                if (
                    before.get('installed') is True
                    and before.get('version') == record['version']
                    and before.get('architecture') == record['architecture']
                ):
                    missing(
                        'TARGET_VERSION_ALREADY_PRESENT',
                        'Do not reinstall the same version. Verify installed provenance/integrity and use independent QA instead.',
                    )
            artifact = await _observe(
                readers.artifact,
                (record, profile),
                missing,
                'PINNED_ARTIFACT_UNVERIFIED',
                'Exact protected cached package bytes and SHA256 must be verified.',
            )
            if (
                not isinstance(artifact, dict)
                or artifact.get('sha256') != record['artifact_sha256']
                or artifact.get('artifact_id') != record.get('artifact_id')
            ):
                missing(
                    'PINNED_ARTIFACT_UNVERIFIED', 'Exact protected cached package bytes and SHA256 must be verified.'
                )
            else:
                plan['artifact'] = {
                    key: artifact.get(key)
                    for key in ('artifact_id', 'sha256', 'size', 'device', 'inode', 'mtime_ns', 'owner_uid')
                }
            if readers.verifier is None:
                missing(
                    'OFFICIAL_SIGNATURE_VERIFIER_UNAVAILABLE',
                    'A trusted cryptographic verifier for the pinned official catalog and artifact is required.',
                )
            elif isinstance(artifact, dict):
                verified = await _observe(
                    readers.verifier.verify,
                    (record, source, artifact, profile),
                    missing,
                    'OFFICIAL_SIGNATURE_OR_CATALOG_INVALID',
                    'The actual vendor signature/catalog must prove the pinned package bytes.',
                )
                expected = {
                    **plan['target'],
                    'official_source_id': source_id,
                    'signer_fingerprint': source['signer_fingerprint'],
                    'catalog_sha256': source['catalog_sha256'],
                }
                valid = (
                    isinstance(verified, dict)
                    and verified.get('verified') is True
                    and all(verified.get(k) == v for k, v in expected.items() if k != 'official_source')
                )
                valid = valid and type(verified.get('valid_until')) is int and verified['valid_until'] > now
                if not valid:
                    missing(
                        'OFFICIAL_SIGNATURE_OR_CATALOG_INVALID',
                        'The actual vendor signature/catalog must prove the exact package/version/architecture/hash and freshness.',
                    )
                else:
                    plan['signature'] = {
                        key: verified[key]
                        for key in ('official_source_id', 'signer_fingerprint', 'catalog_sha256', 'valid_until')
                    }
            dependencies = record.get('dependencies')
            _require(
                isinstance(dependencies, list) and len(dependencies) <= 20, 'Explicit pinned dependency list required'
            )
            for dependency in dependencies:
                _require(
                    isinstance(dependency, dict)
                    and PACKAGE.fullmatch(str(dependency.get('package', '')))
                    and VERSION.fullmatch(str(dependency.get('version', ''))),
                    'Dependency must have an exact server pin',
                )
                observed = await _observe(
                    readers.package,
                    (dependency, profile),
                    missing,
                    'PINNED_DEPENDENCY_UNSATISFIED',
                    'Each exact dependency must be independently installed and verified.',
                )
                if (
                    not _fresh(observed, profile['host_id'], now)
                    or observed.get('package') != dependency['package']
                    or observed.get('installed') is not True
                    or observed.get('version') != dependency['version']
                    or observed.get('architecture') != dependency.get('architecture', record['architecture'])
                ):
                    missing(
                        'PINNED_DEPENDENCY_UNSATISFIED',
                        'Install/verify each exact dependency as a separate reviewed resource first.',
                    )
            path = record.get('artifact_path')
            _require(_canonical_path(path) and path.endswith('.deb'), 'Pinned local Debian artifact path required')
            plan['fixed_argv'] = ['/usr/bin/dpkg', '--install', '--', path]
        elif action['operation'] == 'restart_service':
            _require(
                UNIT.fullmatch(str(record.get('unit', ''))) and SHA.fullmatch(str(record.get('unit_sha256', ''))),
                'Exact unit and unit-file SHA256 required',
            )
            before = await _observe(
                readers.service,
                (record, profile),
                missing,
                'SERVICE_READBACK_UNAVAILABLE',
                'Current unit identity, process and health readback is required.',
            )
            plan['target'] = {
                'unit': record['unit'],
                'unit_sha256': record['unit_sha256'],
                'healthy': True,
                'active_state': 'active',
            }
            if not _fresh(before, profile['host_id'], now):
                missing(
                    'SERVICE_READBACK_UNAVAILABLE', 'Current unit identity, process and health readback is required.'
                )
            else:
                _require(before.get('unit') == record['unit'], 'Service unit readback differs')
                if (
                    before.get('unit_sha256') != record['unit_sha256']
                    or before.get('healthy') is not True
                    or before.get('active_state') != 'active'
                    or not _text(before.get('invocation_id'), 160)
                    or type(before.get('main_pid')) is not int
                    or before['main_pid'] <= 0
                ):
                    missing(
                        'SERVICE_BASELINE_UNHEALTHY',
                        'The pinned service must match its reviewed unit and healthy baseline before restart.',
                    )
                plan['baseline'] = {
                    key: before.get(key)
                    for key in (
                        'unit',
                        'unit_sha256',
                        'active_state',
                        'sub_state',
                        'main_pid',
                        'invocation_id',
                        'healthy',
                    )
                }
            plan['fixed_argv'] = ['/usr/bin/systemctl', 'restart', '--', record['unit']]
        else:
            _require(
                ID.fullmatch(str(record.get('volume_id', '')))
                and type(record.get('target_bytes')) is int
                and record['target_bytes'] > 0,
                'Exact volume and target size required',
            )
            before = await _observe(
                readers.storage,
                (record, profile),
                missing,
                'STORAGE_READBACK_UNAVAILABLE',
                'Current filesystem/volume identity and capacity readback is required.',
            )
            plan['target'] = {
                'volume_id': record['volume_id'],
                'target_bytes': record['target_bytes'],
                'filesystem': record.get('filesystem'),
            }
            if not _fresh(before, profile['host_id'], now):
                missing(
                    'STORAGE_READBACK_UNAVAILABLE',
                    'Current filesystem/volume identity and capacity readback is required.',
                )
            else:
                _require(before.get('volume_id') == record['volume_id'], 'Storage identity differs')
                plan['baseline'] = {
                    key: before.get(key) for key in ('volume_id', 'filesystem_id', 'filesystem', 'total_bytes')
                }
                _require(
                    type(before.get('total_bytes')) is int and record['target_bytes'] >= before['total_bytes'],
                    'Storage shrink is never an extension',
                )
            missing(
                'TRUSTED_CAPACITY_CONNECTOR_UNAVAILABLE',
                'A concrete trusted hypervisor/storage connector and reviewed resize/filesystem policy must be registered; no size change was executed.',
            )
    if executor is None:
        missing('TRUSTED_EXECUTOR_UNAVAILABLE', 'No server-owned mutating executor is registered for this resource.')
    if grant_checker is None:
        missing(
            'CONSUMED_ERP_APPROVAL_READER_UNAVAILABLE',
            'The gateway consumption receipt must be independently read before any host action.',
        )
    plan['policy_digest'] = digest(
        {
            'resource': record,
            'official_source': source,
            'host': profile['host_id'],
            'environment_class': profile.get('environment_class'),
            'expected_uid': profile.get('expected_uid'),
            'change_policy': profile.get('change_policy'),
            'package_cache_roots': profile.get('package_cache_roots'),
            'unit_roots': profile.get('unit_roots'),
            'operations': profile.get('operations'),
        }
    )
    binding = {
        key: deepcopy(plan.get(key))
        for key in (
            'schema_version',
            'operation',
            'resource_id',
            'scope',
            'host_id',
            'policy_digest',
            'target',
            'baseline',
            'fixed_argv',
            'safety',
            'artifact',
            'signature',
        )
    }
    plan['plan_digest'] = digest(binding)
    supplied = action['parameters'].get('plan_digest')
    if supplied is not None:
        _require(
            supplied == plan['plan_digest'],
            'Prepared change pins or baseline changed; request a new exact plan and ERP approval',
        )
    plan['state'] = 'prepared_unavailable' if prerequisites else 'prepared'
    plan['executable'] = not prerequisites
    plan_artifact(plan)  # Enforce a bounded, reviewable artifact before returning.
    return plan


async def verify_change_readback(plan, *, readers, profile):
    """State observation is evidence, never attribution of an original action."""
    operation = plan['operation']
    record = ((profile.get('change_resources') or {}).get(operation) or {}).get(plan['resource_id'])
    if not isinstance(record, dict):
        raise ValueError('Original resource profile disappeared')
    callback = {
        'install_official_package': readers.package,
        'restart_service': readers.service,
        'extend_capacity': readers.storage,
    }[operation]
    try:
        observed = await _call(callback, record, profile) if callback else None
    except Exception:
        return {'verified': False, 'executed': False, 'reason': 'Readback unavailable'}
    if not isinstance(observed, dict):
        return {'verified': False, 'executed': False, 'reason': 'Readback unavailable'}
    if not _fresh(observed, profile['host_id'], int(time.time())):
        return {'verified': False, 'executed': False, 'reason': 'Readback is stale or belongs to another host'}
    if operation == 'install_official_package':
        matched = observed.get('installed') is True and all(
            observed.get(k) == plan['target'][k] for k in ('package', 'version', 'architecture')
        )
    elif operation == 'restart_service':
        matched = (
            observed.get('unit') == plan['target']['unit']
            and observed.get('unit_sha256') == plan['target']['unit_sha256']
            and observed.get('healthy') is True
            and observed.get('active_state') == 'active'
            and _text(observed.get('invocation_id'), 160)
            and observed.get('invocation_id') != plan['baseline'].get('invocation_id')
            and type(observed.get('main_pid')) is int
            and observed['main_pid'] > 0
        )
    else:
        matched = (
            observed.get('volume_id') == plan['target']['volume_id']
            and observed.get('total_bytes') == plan['target']['target_bytes']
        )
    fields = {
        'install_official_package': ('package', 'installed', 'version', 'architecture'),
        'restart_service': ('unit', 'unit_sha256', 'active_state', 'sub_state', 'main_pid', 'invocation_id', 'healthy'),
        'extend_capacity': ('volume_id', 'filesystem_id', 'filesystem', 'total_bytes', 'free_bytes'),
    }[operation]
    return {
        'verified': bool(matched),
        'executed': False,
        'observed': {key: deepcopy(observed.get(key)) for key in fields},
        'attribution': 'Requires original executor receipt; matching state alone cannot prove this action ran.',
    }


def change_adapters(*, readers, executor=None, grant_checker=None, clock=None):
    """Proposed explicit registry; Root must wire it after reviewing the module."""
    _require(isinstance(readers, ChangeReaders), 'Registered readers required')
    _require(executor is None or isinstance(executor, TrustedChangeExecutor), 'Registered executor required')
    now = clock or time.time

    async def preflight(action, scope, profile):
        plan = await prepare_change(
            action, scope, profile, readers=readers, executor=executor, grant_checker=grant_checker, now=int(now())
        )
        if 'plan_digest' not in action['parameters']:
            plan['prerequisites'].append(
                {
                    'code': 'EXACT_PLAN_DIGEST_REQUIRED',
                    'description': 'Persist this exact plan digest in the immutable Task action and obtain its one-use ERP approval.',
                }
            )
            plan['state'] = 'prepared_unavailable'
            plan['executable'] = False
        if not plan['executable']:
            raise PreparedChangeUnavailable(plan)
        return plan

    async def execute(action, scope, before, profile):
        fresh = await prepare_change(
            action, scope, profile, readers=readers, executor=executor, grant_checker=grant_checker, now=int(now())
        )
        _require(
            fresh['executable'] and fresh['plan_digest'] == before['plan_digest'],
            'Exact prepared change is no longer authorized',
        )
        grant = await _call(grant_checker, action, scope, before)
        from open_webui.utils.agentic_contracts.validator import action_fingerprint

        _require(
            isinstance(grant, dict)
            and grant.get('consumed') is True
            and _text(grant.get('receipt_id'), 160)
            and grant.get('action_id') == action['action_id']
            and grant.get('action_fingerprint') == action_fingerprint(action)
            and grant.get('invocation_id') == scope.get('invocation_id')
            and all(grant.get(key) == action[key] for key in ('project_id', 'task_id', 'task_revision')),
            'Original one-use ERP approval consumption was not verified',
        )
        try:
            receipt = await _call(
                executor.execute_once, before, action['action_id'], scope['invocation_id'], tuple(before['fixed_argv'])
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise ChangeOutcomeUncertain(
                'Original host action acknowledgement is unknown; observe its original receipt without replay'
            ) from error
        if not (
            isinstance(receipt, dict)
            and receipt.get('action_id') == action['action_id']
            and receipt.get('invocation_id') == scope['invocation_id']
            and receipt.get('plan_digest') == before['plan_digest']
            and receipt.get('state') == 'completed'
            and _text(receipt.get('receipt_id'), 160)
        ):
            raise ChangeOutcomeUncertain('Host receipt does not confirm the exact original change; do not replay')
        observed = await verify_change_readback(before, readers=readers, profile=profile)
        if not observed['verified']:
            raise ChangeOutcomeUncertain(
                'Original host action returned but its exact postcondition is unverified; do not replay'
            )
        safe_receipt = {
            key: deepcopy(receipt.get(key))
            for key in ('state', 'action_id', 'invocation_id', 'plan_digest', 'receipt_id', 'finished_at')
        }
        return {
            'executed': True,
            'plan_digest': before['plan_digest'],
            'original_receipt': safe_receipt,
            'readback': observed,
        }

    async def recover(action, scope, before, profile):
        if executor is None:
            return {
                'executed': False,
                'state': 'uncertain',
                'plan': before,
                'reason': 'No original receipt reader is registered.',
            }
        receipt = await _call(
            executor.observe_original, action['action_id'], scope['invocation_id'], before['plan_digest']
        )
        observed = await verify_change_readback(before, readers=readers, profile=profile)
        original = (
            isinstance(receipt, dict)
            and receipt.get('action_id') == action['action_id']
            and receipt.get('invocation_id') == scope['invocation_id']
            and receipt.get('plan_digest') == before['plan_digest']
            and receipt.get('state') == 'completed'
            and _text(receipt.get('receipt_id'), 160)
        )
        safe_receipt = (
            {
                key: deepcopy(receipt.get(key))
                for key in ('state', 'action_id', 'invocation_id', 'plan_digest', 'receipt_id', 'finished_at')
            }
            if original
            else None
        )
        return {
            'state': 'uncertain',
            'executed': False,
            'original_receipt_verified': bool(original),
            'postcondition_verified': observed['verified'],
            'original_receipt': safe_receipt,
            'readback': observed,
            'reason': 'Read-only recovery evidence; gateway must reconcile the original durable action, never execute again.',
        }

    return {
        operation: TrustedAdapter(preflight, execute, capability='execute_infrastructure', recover=recover)
        for operation in OPERATIONS
    }


def _read_protected(path, roots, expected_uid, maximum=2_000_000_000):
    _require(
        _canonical_path(path) and any(Path(path).is_relative_to(Path(root)) for root in roots if _canonical_path(root)),
        'Artifact path is outside server-owned cache roots',
    )
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file, 'rb') as handle:
            meta = os.fstat(handle.fileno())
            _require(
                stat.S_ISREG(meta.st_mode)
                and meta.st_uid == expected_uid
                and meta.st_nlink == 1
                and not meta.st_mode & 0o022
                and 0 < meta.st_size <= maximum,
                'Cached artifact is not a protected bounded regular file',
            )
            sha = hashlib.sha256()
            for block in iter(lambda: handle.read(1_048_576), b''):
                sha.update(block)
            end = os.fstat(handle.fileno())
            _require(
                (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns)
                == (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns),
                'Artifact changed while hashing',
            )
            return {
                'sha256': sha.hexdigest(),
                'size': meta.st_size,
                'device': meta.st_dev,
                'inode': meta.st_ino,
                'mtime_ns': meta.st_mtime_ns,
                'owner_uid': meta.st_uid,
            }
    finally:
        os.close(fd)


class LinuxReadOnlyReaders(ChangeReaders):
    """Fixed read-only commands; backup/signature/health attestations are injected."""

    def __init__(self, *, safety=None, verifier=None, health=None, command_reader=None):
        self.health = health
        self.command_reader = command_reader or self._command
        super().__init__(
            safety=safety,
            verifier=verifier,
            package=self._package,
            service=self._service,
            storage=self._storage,
            artifact=self._artifact,
        )

    async def _command(self, argv):
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
        )
        try:
            data, _ = await asyncio.wait_for(process.communicate(), 5)
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            raise
        _require(len(data) <= 16384, 'Read-only command output exceeds metadata bound')
        return process.returncode, data.decode('utf-8')

    async def _package(self, record, profile):
        name = record['package']
        _require(PACKAGE.fullmatch(name), 'Invalid server package pin')
        code, text = await _call(
            self.command_reader,
            (
                '/usr/bin/dpkg-query',
                '--show',
                '--showformat=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}',
                '--',
                name,
            ),
        )
        if code == 1:
            return {
                'host_id': socket.gethostname(),
                'checked_at': int(time.time()),
                'package': name,
                'installed': False,
                'version': None,
                'architecture': None,
            }
        _require(code == 0 and len(text.splitlines()) == 1, 'Package metadata query failed')
        fields = text.split('\t')
        _require(len(fields) == 4 and fields[0] == name, 'Package metadata identity differs')
        return {
            'host_id': socket.gethostname(),
            'checked_at': int(time.time()),
            'package': name,
            'installed': fields[3] == 'installed',
            'version': fields[1],
            'architecture': fields[2],
        }

    async def _service(self, record, profile):
        unit = record['unit']
        _require(UNIT.fullmatch(unit), 'Invalid server unit pin')
        properties = ('Id', 'ActiveState', 'SubState', 'MainPID', 'InvocationID', 'FragmentPath')
        code, text = await _call(
            self.command_reader,
            ('/usr/bin/systemctl', 'show', '--no-pager', '--property=' + ','.join(properties), '--', unit),
        )
        _require(code == 0, 'Service metadata query failed')
        values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        _require(
            values.get('Id') == unit and values.get('FragmentPath') == record.get('unit_path'),
            'Actual unit fragment differs from the server pin',
        )
        identity = _read_protected(
            record['unit_path'], profile.get('unit_roots', []), profile.get('expected_uid'), 1_000_000
        )
        healthy = await _call(self.health, record, profile) if self.health else False
        return {
            'host_id': socket.gethostname(),
            'checked_at': int(time.time()),
            'unit': unit,
            'unit_sha256': identity['sha256'],
            'active_state': values.get('ActiveState'),
            'sub_state': values.get('SubState'),
            'main_pid': int(values.get('MainPID') or 0),
            'invocation_id': values.get('InvocationID'),
            'healthy': healthy is True,
        }

    async def _storage(self, record, profile):
        path = record.get('mountpoint')
        _require(_canonical_path(path), 'Pinned mountpoint required')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in Path(path).parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
        except BaseException:
            os.close(fd)
            raise
        try:
            meta = os.fstat(fd)
            disk = os.fstatvfs(fd)
        finally:
            os.close(fd)
        return {
            'host_id': socket.gethostname(),
            'checked_at': int(time.time()),
            'volume_id': record['volume_id'],
            'filesystem_id': str(meta.st_dev),
            'filesystem': record.get('filesystem'),
            'total_bytes': disk.f_blocks * disk.f_frsize,
            'free_bytes': disk.f_bavail * disk.f_frsize,
        }

    async def _artifact(self, record, profile):
        return {
            'artifact_id': record['artifact_id'],
            **_read_protected(
                record['artifact_path'], profile.get('package_cache_roots', []), profile.get('expected_uid')
            ),
        }

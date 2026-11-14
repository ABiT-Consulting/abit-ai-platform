#!/usr/bin/env python3
"""Pure governance contract checks; never grant ERP or infrastructure authority.

Fresh ERP readback, binding lookup, artifact-byte verification and atomic approval
consumption remain mandatory in the server adapter. No network, subprocess, file
cleanup, model submission or business write is performed by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
LOCAL_SCHEMA = Path(__file__).resolve().parent / 'schemas/governance_v1.schema.json'
SCHEMA_PATH = LOCAL_SCHEMA if LOCAL_SCHEMA.is_file() else ROOT / 'schemas/governance_v1.schema.json'
ROLES = ('solution_architect', 'project_manager', 'developer', 'qa', 'operations', 'it_manager')
HANDOFFS = {
    'solution_architect': {'project_manager', 'it_manager'},
    'project_manager': {'solution_architect', 'developer', 'qa', 'operations', 'it_manager'},
    'developer': {'qa', 'it_manager', 'project_manager'},
    'qa': {'developer', 'operations', 'project_manager'},
    'operations': {'qa', 'it_manager', 'project_manager'},
    'it_manager': {'developer', 'operations', 'project_manager', 'qa'},
}
ROLE_USERS = {'solution_architect': 'ai-projects@abitcons.com', 'project_manager': 'ai-projects@abitcons.com',
              'developer': 'ai-developer@abitcons.com', 'qa': 'ai-qa@abitcons.com',
              'operations': 'ai-operations@abitcons.com', 'it_manager': 'ai-operations@abitcons.com'}
RISK_RANK = {'routine': 0, 'privileged': 1, 'destructive': 2, 'sensitive': 3}
MIN_RISK = {'inspect': 'routine', 'install_official_package': 'routine', 'cleanup_disposable': 'routine',
            'extend_capacity': 'privileged', 'restart_service': 'privileged',
            'change_permissions': 'sensitive', 'deploy_release': 'privileged', 'sap_posting': 'sensitive'}
PROTECTED_CLASSES = {'active_log', 'audit_log', 'security_log', 'database', 'business_data', 'backup', 'unknown'}


def canonical_digest(value: dict) -> str:
    """Canonical UTF-8 JSON hash; packet's self-referential hash is excluded."""
    clean = {key: item for key, item in value.items() if key != 'content_sha256'}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def schema_errors(kind: str, value: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text())
    if kind not in schema['$defs']:
        return ['Unknown governance contract kind']
    selected = {'$schema': schema['$schema'], '$ref': '#/$defs/' + kind, '$defs': schema['$defs']}
    validator = Draft202012Validator(selected, format_checker=FormatChecker())
    return sorted(f"{kind}.{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
                  for error in validator.iter_errors(value))


def agent_id(project: str, role: str) -> str:
    return str(uuid5(NAMESPACE_URL, f'https://abit.ai/agentic/v1/{project}/{role}'))


def validate_binding(binding: dict, *, project: str, registered_bindings: dict | None = None) -> list[str]:
    errors = schema_errors('binding', binding)
    if errors:
        return errors
    if binding['project_id'] != project:
        errors.append('Binding belongs to another project')
    if registered_bindings is None and binding['agent_id'] != agent_id(project, binding['role']):
        errors.append('Agent ID does not match the deterministic project/role identity')
    expected_user = ROLE_USERS[binding['role']]
    if registered_bindings is not None:
        # The mapping comes from trusted server configuration, never the packet.
        configured = registered_bindings.get(binding['role'])
        if not isinstance(configured, dict) or configured.get('agent_id') != binding['agent_id']:
            errors.append('Agent is not registered for this project')
        else:
            expected_user = configured.get('erp_user')
    if binding['erp_user'] != expected_user:
        errors.append('ERP role user does not match the registered binding')
    return errors


def _unique(values: list, name: str, errors: list) -> None:
    if any(count > 1 for count in Counter(values).values()):
        errors.append('Duplicate ' + name)


def validate_work_packet(packet: dict, *, registered_bindings: dict | None = None) -> list[str]:
    errors = schema_errors('work_packet', packet)
    if errors:
        return errors
    errors += validate_binding(packet['binding'], project=packet['project_id'], registered_bindings=registered_bindings)
    requirements = set(packet['requirement_ids'])
    features = packet['srs'].get('features', [])
    if features:
        _unique([feature['requirement_id'] for feature in features], 'SRS feature ID', errors)
        if requirements - {feature['requirement_id'] for feature in features}:
            errors.append('WorkPacket requirement does not exist in the source SRS')
    covered = set()
    _unique([test['id'] for test in packet['acceptance_tests']], 'acceptance test ID', errors)
    for test in packet['acceptance_tests']:
        if set(test['requirement_ids']) - requirements:
            errors.append('Acceptance test references an unknown requirement')
        covered.update(test['requirement_ids'])
    if requirements - covered:
        errors.append('Every requirement needs an acceptance test')
    if packet['task_id'] in packet['dependencies']:
        errors.append('Task cannot depend on itself')
    if 'content_sha256' in packet and packet['content_sha256'] != canonical_digest(packet):
        errors.append('WorkPacket content hash mismatch')
    if packet.get('dependency_acceptance_policy') is not None:
        try:
            try: from .dependency_verification import policy_for,required_mappings
            except ImportError: from dependency_verification import policy_for,required_mappings
            selected = policy_for(packet,packet['dependency_acceptance_policy'])
            if packet['binding']['role'] in {'developer','qa'} and not packet.get('tool_action'):
                required_mappings(packet,selected)
        except ValueError as error: errors.append(str(error))
    if 'tool_action' in packet:
        errors.extend(_safe_action_template(packet['tool_action']))
    adoption = packet.get('atomic_adoption')
    if adoption:
        if set(adoption['requirement_ids']) != requirements:
            errors.append('Atomic adoption requirements must match the WorkPacket requirements')
        if adoption['task_revision'] != packet['task_revision']:
            errors.append('Atomic adoption task revision must match current business inputs')
        if packet['binding']['role'] not in {'project_manager', 'qa', adoption['executor_role']}:
            errors.append('Atomic adoption permits only PM, the bound executor and independent QA')
    backlog = packet.get('project_backlog')
    if backlog is not None:
        rows = backlog['tasks']; ids = [row['task_id'] for row in rows]
        if backlog['project_id'] != packet['project_id'] or len(rows) != backlog['total_count'] or len(ids) != len(set(ids)):
            errors.append('Project backlog must be a complete unique snapshot of this Project')
        if backlog['snapshot_sha256'] != canonical_digest({'project_id':packet['project_id'], 'tasks':rows}):
            errors.append('Project backlog snapshot digest mismatch')
        excluded = set(backlog['context_excluded_task_ids'])
        if not excluded <= set(ids):
            errors.append('Project backlog context exclusion refers to an unknown Task')
        context = [{'task_id':row['task_id'], 'task_revision':row['task_revision'],
                    'owner_context_sha256':row['owner_context_sha256']} for row in rows if row['task_id'] not in excluded]
        if backlog['context_sha256'] != canonical_digest({'project_id':packet['project_id'], 'tasks':context}):
            errors.append('Project backlog business context digest mismatch')
    review = packet.get('qa_review')
    if review is not None:
        qa = review['result_contract']; receipt = review['receipt']
        if packet['binding']['role'] != 'project_manager':
            errors.append('QA coordination review belongs only to the registered Project Manager')
        if (qa['outcome'] != 'pass' or qa['actor']['role'] != 'qa' or qa['project_id'] != packet['project_id']
                or qa['task_id'] != packet['task_id'] or qa['task_revision'] != packet['task_revision']
                or qa['invocation_id'] != review['qa_invocation_id'] or qa['actor']['agent_id'] != review['qa_agent_id']
                or qa['implementation_invocation_id'] != review['implementation_invocation_id']
                or qa['implementation_agent_id'] != review['implementation_agent_id']
                or receipt['run_id'] != review['qa_run_id'] or receipt['task_id'] != packet['task_id']
                or not qa['test_results'] or any(t['status'] != 'pass' for t in qa['test_results']) or qa['defects']):
            errors.append('PM coordination requires the exact independently approved Task and implementation')
        errors.extend(validate_binding(qa['actor'], project=packet['project_id'], registered_bindings=registered_bindings))
        if review['qa_result_sha256'] != canonical_digest(qa) or review['qa_receipt_sha256'] != canonical_digest(receipt):
            errors.append('PM coordination QA publication digest mismatch')
        artifacts = {a['id']:a for a in review['artifacts']}
        if len(artifacts) != len(review['artifacts']) or any(
                ref['id'] not in artifacts or any(ref[k] != artifacts[ref['id']][k]
                    for k in ('kind','sha256','requirement_ids')) for ref in qa['evidence']):
            errors.append('PM coordination evidence must match the exact published QA artifacts')
        for artifact in review['artifacts']:
            errors.extend(_evidence_scope({'uri':artifact['url'], 'requirement_ids':artifact['requirement_ids']}, requirements))
    continuation = packet.get('pm_review_continuation')
    if continuation is not None:
        if not review or packet['binding']['role'] != 'project_manager':
            errors.append('A PM review continuation requires its exact bound QA coordination source')
        elif continuation['previous_invocation_id'] in {review['qa_invocation_id'], review['implementation_invocation_id']}:
            errors.append('A PM review continuation must identify a distinct published PM checkpoint')
    repository = urlsplit(packet['repository'])
    if (repository.scheme != 'https' or not repository.hostname or repository.username or repository.password
            or repository.query or repository.fragment):
        errors.append('Repository must be a credential-free HTTPS repository URL')
    for artifact in packet.get('artifact_manifest', []) + packet.get('artifacts', []):
        errors += _evidence_scope(artifact, requirements)
    _unique([v['id'] for v in packet.get('artifact_manifest', []) + packet.get('artifacts', [])], 'artifact ID', errors)
    return sorted(set(errors))


def validate_handoff(handoff: dict, *, registered_bindings: dict | None = None) -> list[str]:
    errors = schema_errors('handoff', handoff)
    if errors:
        return errors
    packet = handoff['work_packet']; project = packet['project_id']
    errors += validate_work_packet(packet, registered_bindings=registered_bindings)
    for binding in (handoff['from_binding'], handoff['to_binding']):
        errors += validate_binding(binding, project=project, registered_bindings=registered_bindings)
    if handoff['to_binding'] != packet['binding']:
        errors.append('Handoff target does not match WorkPacket actor')
    if handoff['to_binding']['role'] not in HANDOFFS[handoff['from_binding']['role']]:
        errors.append('Role handoff is not allowed')
    if handoff['invocation_id'] == handoff['source_invocation_id']:
        errors.append('A successor invocation must differ from its predecessor')
    if handoff['work_packet_digest'] != canonical_digest(packet):
        errors.append('Handoff WorkPacket digest mismatch')
    return sorted(set(errors))


def _evidence_scope(evidence: dict, requirements: set[str]) -> list[str]:
    errors = []
    if set(evidence['requirement_ids']) - requirements:
        errors.append('Evidence references an unknown requirement')
    uri = evidence['uri']; parsed = urlsplit(uri)
    if uri.startswith('repo:'):
        path = PurePosixPath(uri[5:])
        if not uri[5:] or path.is_absolute() or '..' in path.parts or '\\' in uri:
            errors.append('Unsafe repository evidence path')
    elif uri.startswith('artifact://'):
        # This is a scoped storage key, never an arbitrary remote URL or path.
        filename = uri.removeprefix('artifact://')
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', filename)
                or filename in {'.', '..'} or '..' in filename):
            errors.append('Unsafe scoped artifact filename')
    else:
        basename = unquote(parsed.path.removeprefix('/private/files/'))
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query_ok = not query or len(query) == 1 and query[0][0] == 'fid' and bool(
            re.fullmatch(r'[A-Za-z0-9_-]{1,140}', query[0][1]))
        if (parsed.scheme != 'https' or parsed.netloc != 'erp.abit.ai' or parsed.username
                or parsed.password or parsed.fragment or not query_ok
                or not parsed.path.startswith('/private/files/') or not basename
                or any(char in basename for char in '/\\%?#')
                or any(ord(char) < 32 or ord(char) == 127 for char in basename)):
            errors.append('Evidence must reference scoped artifact, approved repository or private ERP storage')
    return errors


def validate_result(result: dict, packet: dict, *, registered_bindings: dict | None = None,
                    artifact_root: Path = ROOT) -> list[str]:
    errors = schema_errors('result', result) + validate_work_packet(packet, registered_bindings=registered_bindings)
    if errors:
        return sorted(set(errors))
    errors += validate_binding(result['actor'], project=packet['project_id'], registered_bindings=registered_bindings)
    for key in ('project_id', 'task_id', 'task_revision'):
        if result[key] != packet[key]:
            errors.append('Result ' + key + ' does not match WorkPacket')
    if result['actor'] != packet['binding']:
        errors.append('Result actor does not match assigned executor')
    if result['work_packet_digest'] != canonical_digest(packet):
        errors.append('Result WorkPacket digest mismatch')
    role = result['actor']['role']; outcome = result['outcome']
    requests=result.get('action_requests') or []
    _unique([item['request_key'] for item in requests], 'action request key', errors)
    if requests and 'it_manager' not in HANDOFFS.get(role,set()):
        errors.append('This role cannot create infrastructure task requests')
    for template in requests:
        errors.extend(_safe_action_template(template))
    if outcome == 'ready_for_review' and not result['evidence']:
        errors.append('Ready for review requires concrete evidence references')
    if role == 'solution_architect' and outcome == 'ready_for_review' and 'architect_plan' not in result:
        errors.append('Architect readiness requires an inspectable architecture and task plan')
    if 'architect_plan' in result:
        if role != 'solution_architect':
            errors.append('Only the Solution Architect may submit an architecture plan')
        try:
            from .validate_architect_plan import validate_plan
        except ImportError:
            from validate_architect_plan import validate_plan
        plan = result['architect_plan']
        plan_errors = validate_plan(plan, root=artifact_root)
        errors.extend('Architect plan: ' + error for error in plan_errors)
        if not plan_errors:
            meta = plan['metadata']; binding = meta['agent_binding']
            if {row['id'] for row in plan['requirements']} != set(packet['requirement_ids']):
                errors.append('Architect plan must cover the full WorkPacket requirement set')
            if (meta['project_id'] != packet['project_id'] or meta['srs_version'] != packet['srs']['revision']
                    or meta['srs_sha256'] != packet['srs']['sha256'] or meta['run_id'] != result['invocation_id']
                    or binding['project_id'] != packet['project_id']
                    or binding['abit_agent_id'] != result['actor']['agent_id']
                    or binding['erp_ai_user'] != result['actor']['erp_user']):
                errors.append('Architect plan identity or SRS revision does not match the invocation')
            for item in plan['designs'] + plan['mockups']:
                errors.extend(_evidence_scope({'uri': item['artifact']['uri'],
                    'requirement_ids': item['requirement_ids']}, set(packet['requirement_ids'])))
    if outcome in {'pass', 'fail'} and role != 'qa':
        errors.append('Only independent QA may return a verification verdict')
    if role == 'qa':
        if (not result['implementation_agent_id'] or result['implementation_agent_id'] == result['actor']['agent_id']
                or not result['implementation_invocation_id']
                or result['implementation_invocation_id'] == result['invocation_id']):
            errors.append('QA must reference a distinct implementation agent and invocation')
        executors = {r: ((registered_bindings or {}).get(r) or {}).get('agent_id')
                     if registered_bindings is not None else agent_id(packet['project_id'], r)
                     for r in ('developer', 'operations', 'it_manager')}
        matching_roles = [r for r, ident in executors.items() if ident == result['implementation_agent_id']]
        if len(matching_roles) != 1:
            errors.append('QA implementation agent is outside the approved project executors')
        else:
            implementation_role = matching_roles[0]
            registered = (registered_bindings or {}).get(implementation_role)
            implementation_user = registered.get('erp_user') if isinstance(registered, dict) else ROLE_USERS[implementation_role]
            if packet.get('implementation_user', implementation_user) != implementation_user:
                errors.append('Implementation ERP identity does not match its project role')
            if implementation_user == result['actor']['erp_user']:
                errors.append('QA reviewer cannot share the implementation ERP identity')
    requirements = set(packet['requirement_ids']); tests = {v['id']: v for v in packet['acceptance_tests']}
    evidence = {v['id']: v for v in result['evidence']}
    _unique([v['id'] for v in result['evidence']], 'result evidence ID', errors)
    _unique([v['test_id'] for v in result['test_results']], 'result test ID', errors)
    for row in result['evidence']:
        errors += _evidence_scope(row, requirements)
    for row in result['test_results']:
        if row['test_id'] not in tests:
            errors.append('Result includes an unknown acceptance test')
        for eid in row['evidence_ids']:
            if eid not in evidence:
                errors.append('Test result evidence does not exist')
            elif row['test_id'] in tests and set(tests[row['test_id']]['requirement_ids']) - set(evidence[eid]['requirement_ids']):
                errors.append('Test evidence does not cover its requirements')
    for defect in result['defects']:
        if set(defect['requirement_ids']) - requirements or set(defect['evidence_ids']) - set(evidence):
            errors.append('Defect traceability references unknown requirements or evidence')
    if outcome == 'pass':
        observed = {v['test_id']: v['status'] for v in result['test_results']}
        if set(observed) != set(tests) or any(v != 'pass' for v in observed.values()):
            errors.append('QA pass requires every acceptance test to pass')
        if result['defects']:
            errors.append('QA pass cannot contain unresolved defects')
        if not evidence:
            errors.append('QA pass requires actual evidence references')
    try:
        from .developer_delivery import validate_developer_delivery, validate_delivery, delivery_digest
    except ImportError:
        from developer_delivery import validate_developer_delivery, validate_delivery, delivery_digest
    if role == 'developer' and outcome == 'ready_for_review':
        errors.extend(validate_developer_delivery(result, packet))
    elif 'delivery' in result:
        errors.append('Only a ready Developer result may declare a code delivery')
    if result.get('dependency_verifications') and not (role == 'qa' and outcome == 'pass' and packet.get('dependency_acceptance_policy') and packet.get('implementation_delivery')):
        errors.append('Only passing Developer-source QA may declare own dependency verification references')
    implementation_delivery = packet.get('implementation_delivery')
    if role == 'qa' and implementation_delivery is not None:
        errors.extend(validate_delivery(implementation_delivery, packet['repository']))
        if outcome == 'pass' and result.get('reviewed_delivery_sha256') != delivery_digest(implementation_delivery):
            errors.append('QA must review the exact implementation delivery digest')
    elif 'reviewed_delivery_sha256' in result:
        errors.append('QA delivery review requires a bound implementation delivery')
    review = packet.get('qa_review')
    if outcome == 'reviewed':
        if role != 'project_manager' or not review:
            errors.append('Only the registered PM may acknowledge a bound QA coordination review')
        elif (result.get('qa_review_ack') != canonical_digest(review)
                or result['implementation_invocation_id'] != review['implementation_invocation_id']
                or result['implementation_agent_id'] != review['implementation_agent_id']):
            errors.append('PM acknowledgement does not identify the exact QA publication and implementer')
        if (not evidence or not tests or {v['test_id'] for v in result['test_results']} != set(tests)
                or any(v['status'] != 'pass' for v in result['test_results']) or result['defects']):
            errors.append('PM acknowledgement requires complete observed review tests and real evidence')
    elif 'qa_review_ack' in result:
        errors.append('Only a reviewed PM result may acknowledge QA coordination')
    if review and (outcome == 'ready_for_review' or any(key in result for key in ('architect_plan','action_requests','delivery'))):
        errors.append('QA coordination cannot rematerialize a backlog or perform another implementation/action')
    if outcome == 'fail' and not result['defects']:
        errors.append('QA failure requires a reproducible defect')
    return sorted(set(errors))


def _contains_secret_key(value) -> bool:
    if isinstance(value, dict):
        return any(re.search(r'(?i)^(password|secret|token|authorization|cookie|api[_-]?key)$', str(key))
                   or _contains_secret_key(item) for key, item in value.items())
    return isinstance(value, list) and any(_contains_secret_key(item) for item in value)


def _safe_action_template(template: dict) -> list[str]:
    def unsafe(value):
        if isinstance(value,dict):
            return any(re.fullmatch(r'(?i)(actor|agent_id|project_id|task_id|run_id|invocation_id|approval_id|role|command|shell|script|argv|url)',str(key))
                       or unsafe(item) for key,item in value.items())
        if isinstance(value,list):return any(unsafe(item) for item in value)
        return isinstance(value,str) and bool(re.search(r'(?i)\b(?:https?|file|ssh|ftp)://',value))
    parameters=template['parameters']
    try:
        if len(json.dumps(parameters,allow_nan=False).encode())>16000:
            return ['Infrastructure parameters exceed the bounded request']
    except (ValueError,TypeError,RecursionError):return ['Infrastructure parameters must be finite JSON']
    if _contains_secret_key(parameters) or unsafe(parameters):
        return ['Infrastructure templates cannot carry credentials, authority fields, commands or URLs']
    return []


def action_fingerprint(action: dict) -> str:
    """Bind authority to the exact operation/scope, excluding retry metadata."""
    fields = ('project_id', 'task_id', 'task_revision', 'actor', 'repository', 'environment', 'operation', 'risk', 'parameters')
    return canonical_digest({key: action[key] for key in fields})


def authorize_action(action: dict, approval: dict | None, *, now: int, scope: dict) -> list[str]:
    """Validate trusted readback snapshots. Server must consume approval atomically.

    scope is server-owned, never accepted from model input. Required keys:
    project_id,task_id,task_revision,repository,environment,binding,operations.
    """
    errors = schema_errors('action', action)
    if errors:
        return errors
    for key in ('project_id', 'task_id', 'task_revision', 'repository', 'environment'):
        if action[key] != scope.get(key):
            errors.append('Action scope mismatch: ' + key)
    if action['actor'] != scope.get('binding'):
        errors.append('Action actor is not the authenticated scoped binding')
    errors += validate_binding(action['actor'], project=action['project_id'], registered_bindings=scope.get('registered_bindings'))
    if action['operation'] not in scope.get('operations', []):
        errors.append('Operation is outside the granted capability')
    try:
        parameter_bytes = json.dumps(action['parameters'], allow_nan=False).encode()
        if len(parameter_bytes) > 16000:
            errors.append('Action parameters exceed the bounded payload')
    except (ValueError, TypeError, RecursionError):
        return sorted(set(errors + ['Action parameters must be finite JSON data']))
    if _contains_secret_key(action['parameters']):
        errors.append('Action parameters must not carry credentials')
    if action['requested_at'] > now + 30 or action['requested_at'] < now - 300:
        errors.append('Action request is stale or future-dated')
    if RISK_RANK[action['risk']] < RISK_RANK[MIN_RISK[action['operation']]]:
        errors.append('Action understates the required risk class')
    sensitive = (action['risk'] != 'routine' or action['operation'] in {'extend_capacity', 'restart_service',
                 'change_permissions', 'deploy_release', 'sap_posting'}
                 or action['environment'].lower() in {'prod', 'production'} and action['operation'] != 'inspect')
    if not sensitive:
        return sorted(set(errors))
    if approval is None:
        return sorted(set(errors + ['Current ERP approval is required']))
    approval_errors = schema_errors('approval', approval)
    if approval_errors:
        return sorted(set(errors + approval_errors))
    if (approval['status'] != 'approved' or approval['expires_at'] <= now
            or approval['approved_at'] > now or approval['approved_at'] >= approval['expires_at']):
        errors.append('ERP approval is expired, revoked, rejected or not yet valid')
    if approval['project_id'] != action['project_id'] or approval['task_id'] != action['task_id']:
        errors.append('ERP approval belongs to another project or task')
    if approval['action_fingerprint'] != action_fingerprint(action):
        errors.append('ERP approval does not match the exact action fingerprint')
    ai_users = set(ROLE_USERS.values()) | {v.get('erp_user') for v in (scope.get('registered_bindings') or {}).values()
                                         if isinstance(v, dict)}
    if approval['approved_by'] == action['actor']['erp_user'] or approval['approved_by'] in ai_users:
        errors.append('AI role users cannot approve their own privileged actions')
    if approval['used_by_action_id'] is not None:
        errors.append('ERP approval was already consumed; reconcile its original action')
    return sorted(set(errors))


def validate_it_change(change: dict, approval: dict | None, *, now: int, scope: dict) -> list[str]:
    errors = schema_errors('it_change', change)
    if errors:
        return errors
    action = change['action']; pre = change['preflight']
    errors += authorize_action(action, approval, now=now, scope=scope)
    if action['actor']['role'] != 'it_manager':
        errors.append('IT change requires the project IT Manager binding')
    if change['host_id'] != scope.get('host_id') or pre['host_id'] != change['host_id']:
        errors.append('IT host identity mismatch')
    if pre != scope.get('verified_preflight'):
        errors.append('IT preflight is not the server-attested host record')
    if pre['checked_at'] > now or pre['checked_at'] < now - 300:
        errors.append('IT preflight is stale or future-dated')
    if action['operation'] == 'cleanup_disposable':
        if not change['resources']:
            errors.append('Cleanup needs an explicit bounded resource manifest')
        roots = scope.get('disposable_roots') or []
        for resource in change['resources']:
            inventory = (scope.get('resource_inventory') or {}).get(resource['path'])
            if inventory != {key: value for key, value in resource.items() if key != 'path'}:
                errors.append('Cleanup resource is not the server-attested inventory record')
            path = PurePosixPath(resource['path'])
            protected_path = (not path.is_absolute() or '..' in path.parts or '\\' in str(path)
                or any(word in {p.lower() for p in path.parts} for word in {'audit', 'security', 'backups', 'backup'})
                or str(path).startswith(('/var/lib/', '/etc/', '/home/'))
                or any(str(path) == root or str(path).startswith(root.rstrip('/') + '/') for root in scope.get('protected_roots', []))
                or re.search(r'(?i)(^|[/_.-])(audit|security|secure|auth)([/_.-]|$)', str(path))
                or re.search(r'(?i)(\.db(?:-wal|-shm)?|\.sqlite(?:-wal|-shm)?|\.wal|\.bak|\.sql|auth\.log|secure)$', str(path)))
            if resource['classification'] in PROTECTED_CLASSES or protected_path:
                errors.append('Protected resource can never enter disposable cleanup')
            if resource['active_writer'] or resource['is_symlink']:
                errors.append('Active or symlink resource cannot be disposed')
            if resource['retention_until'] > now:
                errors.append('Resource retention has not expired')
            if not any(str(path).startswith(root.rstrip('/') + '/') for root in roots
                       if isinstance(root, str) and root.startswith('/') and '..' not in PurePosixPath(root).parts):
                errors.append('Cleanup path is outside server-approved disposable roots')
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['work_packet', 'handoff'])
    parser.add_argument('path', type=Path)
    args = parser.parse_args(); value = json.loads(args.path.read_text())
    errors = validate_work_packet(value) if args.kind == 'work_packet' else validate_handoff(value)
    for error in errors:
        print('INVALID: ' + error, file=sys.stderr)
    if not errors:
        print('VALID offline contract; ERP authority and artifact bytes still require server verification.')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())

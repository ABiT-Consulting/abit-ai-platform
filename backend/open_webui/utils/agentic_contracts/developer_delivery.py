"""Pure delivery evidence contract, shared by the executor and ERP inbox.

These checks bind code/test claims to exact private evidence bytes. They do not
attest a remote PR's existence or replace independent QA/provider tool receipts.
No model-provided command is executed by this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

FIELDS = {'repository', 'base_commit', 'head_commit', 'pull_request_url',
          'changed_paths', 'commit_evidence_id', 'test_runs'}
MANIFEST_FIELDS = ('repository', 'base_commit', 'head_commit', 'pull_request_url', 'changed_paths')
SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
IDENT = re.compile(r'[A-Za-z0-9_.:@+-]{1,160}')


def delivery_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def delivery_manifest(value):
    return {key: value[key] for key in MANIFEST_FIELDS}


def validate_delivery(value, repository):
    """Validate bounded code/PR/test metadata without trusting it as authority."""
    if not isinstance(value, dict) or set(value) not in (FIELDS, FIELDS | {'dependency_verifications'}):
        return ['Developer delivery requires exact repository, commits, PR, paths and test receipts']
    errors = []
    if value['repository'] != repository:
        errors.append('Developer delivery repository differs from the WorkPacket')
    base, head = value['base_commit'], value['head_commit']
    if (not isinstance(base, str) or not SHA.fullmatch(base)
            or not isinstance(head, str) or not SHA.fullmatch(head)
            or len(base) != len(head) or base == head):
        errors.append('Developer delivery requires distinct full base and head commits')
    try:
        repo, pr = urlsplit(repository), urlsplit(value['pull_request_url'])
        prefix = repo.path.rstrip('/').removesuffix('.git')
        if (repo.scheme != 'https' or repo.netloc != 'github.com'
                or pr.scheme != 'https' or pr.netloc != repo.netloc
                or pr.query or pr.fragment
                or not re.fullmatch(re.escape(prefix) + r'/pull/[1-9][0-9]*', pr.path, re.I)):
            errors.append('Developer PR must belong to the exact approved GitHub repository')
    except (TypeError, ValueError, AttributeError):
        errors.append('Developer PR must belong to the exact approved GitHub repository')
    paths = value['changed_paths']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 256:
        errors.append('Developer delivery requires a bounded changed-path inventory')
    else:
        safe = set()
        for path in paths:
            if (not isinstance(path, str) or not 1 <= len(path) <= 500
                    or path.startswith('/') or '\\' in path
                    or any(ord(c) < 32 or ord(c) == 127 for c in path)
                    or any(part in {'', '.', '..', '.git'} for part in path.split('/'))
                    or PurePosixPath(path).is_absolute()):
                errors.append('Developer changed paths must be safe relative repository paths')
            elif path in safe:
                errors.append('Developer changed paths cannot contain duplicates')
            else:
                safe.add(path)
    if not isinstance(value['commit_evidence_id'], str) or not IDENT.fullmatch(value['commit_evidence_id']):
        errors.append('Developer delivery requires a commit evidence ID')
    runs = value['test_runs']
    if not isinstance(runs, list) or not 1 <= len(runs) <= 100:
        errors.append('Developer delivery requires bounded test command receipts')
    else:
        for run in runs:
            if (not isinstance(run, dict)
                    or set(run) != {'test_ids', 'command', 'exit_code', 'log_evidence_id'}):
                errors.append('Developer test receipt fields are invalid')
                continue
            ids = run['test_ids']
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                    or any(not isinstance(t, str) or not IDENT.fullmatch(t) for t in ids)
                    or len(ids) != len(set(ids))):
                errors.append('Developer test receipt requires unique acceptance test IDs')
            if (not isinstance(run['command'], str) or not run['command'].strip()
                    or len(run['command']) > 2000 or '\x00' in run['command']):
                errors.append('Developer test receipt requires a bounded observed command')
            if type(run['exit_code']) is not int or run['exit_code'] != 0:
                errors.append('Developer readiness requires successful test command exit codes')
            if not isinstance(run['log_evidence_id'], str) or not IDENT.fullmatch(run['log_evidence_id']):
                errors.append('Developer test receipt requires a test-log evidence ID')
    if 'dependency_verifications' in value:
        try:
            try: from .dependency_verification import validate_ref
            except ImportError: from dependency_verification import validate_ref
            refs = value['dependency_verifications']
            if not isinstance(refs,list) or not 1 <= len(refs) <= 20:
                raise ValueError('Bounded typed dependency references required')
            for ref in refs: validate_ref(ref)
        except ValueError as error:
            errors.append(str(error))
    return sorted(set(errors))


def validate_developer_delivery(result, packet):
    value = result.get('delivery')
    errors = validate_delivery(value, packet.get('repository'))
    if errors:
        return errors
    evidence_rows = result.get('evidence')
    tests = result.get('test_results')
    if (not isinstance(evidence_rows, list) or not isinstance(tests, list)
            or any(not isinstance(r, dict) for r in evidence_rows + tests)):
        return ['Developer readiness requires structured test results and evidence']
    evidence = {r.get('id'): r for r in evidence_rows if isinstance(r.get('id'), str)}
    commit = evidence.get(value['commit_evidence_id'], {})
    if commit.get('kind') != 'commit':
        errors.append('Developer delivery requires its referenced commit manifest evidence')
    definitions = {t['id']: t for t in packet.get('acceptance_tests', [])}
    expected = set(definitions)
    if len(evidence) != len(evidence_rows):
        errors.append('Developer evidence IDs must be unique')
    if not set(packet.get('requirement_ids', [])) <= set(commit.get('requirement_ids') or []):
        errors.append('Developer commit evidence must cover its Task requirements')
    observed = {r.get('test_id'): r for r in tests if isinstance(r.get('test_id'), str)}
    if (not expected or len(observed) != len(tests) or set(observed) != expected
            or any(r.get('status') != 'pass' for r in tests) or result.get('defects') != []):
        errors.append('Developer readiness requires every acceptance test to pass without unresolved defects')
    covered = set()
    for run in value['test_runs']:
        ids = set(run['test_ids'])
        if ids - expected:
            errors.append('Developer test receipt references an unknown acceptance test')
        log = evidence.get(run['log_evidence_id'], {})
        if log.get('kind') != 'test_log':
            errors.append('Developer command receipt must reference actual test-log evidence')
        for test in ids:
            if not set(definitions.get(test, {}).get('requirement_ids', [])) <= set(log.get('requirement_ids') or []):
                errors.append('Developer command log does not cover its acceptance requirements')
            refs = observed.get(test, {}).get('evidence_ids') or []
            if not isinstance(refs, list) or run['log_evidence_id'] not in refs:
                errors.append('Developer test result must reference its command log evidence')
        covered.update(ids)
    if packet.get('dependency_acceptance_policy') is not None or value.get('dependency_verifications'):
        try:
            try: from .dependency_verification import coverage,policy_for
            except ImportError: from dependency_verification import coverage,policy_for
            policy = policy_for(packet,packet.get('dependency_acceptance_policy'))
            coverage(packet,policy,value['test_runs'],value.get('dependency_verifications') or [],evidence,observed)
        except ValueError as error:
            errors.append(str(error))
    elif covered != expected:
        errors.append('Developer command receipts must cover every acceptance test')
    return sorted(set(errors))


def verify_manifest_bytes(value, content):
    """Compare actual private commit-manifest bytes with the claimed delivery."""
    try:
        parsed = json.loads(content.decode('utf-8') if isinstance(content, bytes) else content)
    except (ValueError, UnicodeError, TypeError):
        raise ValueError('Developer commit evidence must be a UTF-8 JSON manifest') from None
    if parsed != delivery_manifest(value):
        raise ValueError('Developer commit evidence bytes do not match the exact delivery')

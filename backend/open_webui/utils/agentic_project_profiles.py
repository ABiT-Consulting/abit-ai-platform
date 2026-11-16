"""Resolve server-approved repository profiles without per-project config writes.

ERP supplies only the already-authorized Project, repository and environment.
Paths, commands, acceptance mappings and Git identities come from server config.
This resolver grants no invocation, infrastructure capability or approval.
"""
from copy import deepcopy
from pathlib import PurePosixPath
import re


class ProfileUnavailable(ValueError):
    """An operator must register or correct an approved repository profile."""


REPOSITORY = re.compile(r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})')
PROJECT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,139}')
TEMPLATE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
PROFILE_FIELDS = {
    'repository', 'environment', 'allowed_paths', 'read_paths', 'test_paths',
    'tests', 'test_acceptance_ids', 'base_branch', 'base_commit', 'git_author',
}
TEMPLATE_FIELDS = PROFILE_FIELDS | {'enabled', 'workspace_base', 'test_resources'}
TEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,100}')


def _need(condition, message):
    if not condition:
        raise ProfileUnavailable(message)


def _pair(profile, repository, environment):
    return (isinstance(profile, dict) and profile.get('repository') == repository
            and profile.get('environment') == environment)


def _template_policy(template):
    # Validate registrations before advertising them. Runtime source snapshots,
    # credential-path filters, sandboxing and delivery checks still apply.
    for key in ('allowed_paths', 'read_paths', 'test_paths'):
        paths = template[key]
        _need(isinstance(paths, list) and 0 < len(paths) <= 100
              and all(isinstance(p, str) and 0 < len(p) <= 400 and '\\' not in p and '\x00' not in p
                      and not PurePosixPath(p).is_absolute() and str(PurePosixPath(p)) == p
                      and not any(part in {'', '.', '..', '.git', '.ssh', '.codex'} for part in PurePosixPath(p).parts)
                      for p in paths) and len(set(paths)) == len(paths),
              'Approved template source and test paths must be explicit canonical relative paths')
    tests = template['tests']
    _need(isinstance(tests, dict) and 0 < len(tests) <= 100,
          'Approved template test commands are missing')
    for name, command in tests.items():
        _need(isinstance(name, str) and TEST_ID.fullmatch(name) and isinstance(command, list)
              and 0 < len(command) <= 30 and bool(command[0])
              and all(isinstance(arg, str) and len(arg) <= 500 and '\x00' not in arg for arg in command),
              'Approved template tests require bounded registered argument arrays')
    mapping = template['test_acceptance_ids']
    _need(isinstance(mapping, dict) and set(mapping) <= set(tests)
          and all(isinstance(ids, list) and 0 < len(ids) <= 100
                  and all(isinstance(value, str) and 0 < len(value) <= 160 for value in ids)
                  and len(set(ids)) == len(ids) for ids in mapping.values()),
          'Approved template test acceptance mappings are invalid')
    branch, commit, author = template['base_branch'], template['base_commit'], template['git_author']
    _need(isinstance(branch, str) and BRANCH.fullmatch(branch) and '..' not in branch
          and isinstance(commit, str) and re.fullmatch('[a-f0-9]{40}', commit),
          'Approved template needs a fixed base branch and full commit')
    _need(isinstance(author, dict) and set(author) == {'name', 'email'}
          and isinstance(author['name'], str) and 1 <= len(author['name']) <= 100
          and not any(c in author['name'] for c in '<>\r\n\x00')
          and isinstance(author['email'], str)
          and re.fullmatch(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+', author['email']),
          'Approved template requires a fixed commit author')


def resolve_repository_profile(config, project, repository, environment):
    """Return an independent profile and its safe registration provenance.

    An explicit Project entry is authoritative, including a malformed/mismatched
    entry: it can never fall back to a more permissive template. Templates are
    opt-in server registrations matched by the exact repository/environment pair.
    """
    _need(isinstance(config, dict) and isinstance(project, str) and project,
          'A valid authorized Project context is required')
    repo_match = REPOSITORY.fullmatch(repository) if isinstance(repository, str) else None
    _need(repo_match is not None and repo_match[2] not in {'.', '..'}
          and isinstance(environment, str) and 0 < len(environment) <= 160
          and environment == environment.strip() and not any(c in environment for c in '\x00\r\n'),
          'A valid repository and environment are required')
    gateway = config.get('agentic_repository_gateway', {})
    _need(isinstance(gateway, dict), 'Repository gateway registration is invalid')
    projects = gateway.get('projects', {})
    _need(isinstance(projects, dict), 'Project repository registration is invalid')
    if project in projects:
        profile = projects[project]
        _need(_pair(profile, repository, environment),
              'The registered Project profile does not match its repository and environment')
        from open_webui.utils.agentic_test_resources import limits
        try:
            limits(profile)
        except ValueError as error:
            raise ProfileUnavailable(str(error)) from None
        return deepcopy(profile), {'kind': 'project', 'ready': True}

    templates = gateway.get('templates', {})
    _need(isinstance(templates, dict) and len(templates) <= 100,
          'Approved repository templates are invalid')
    matches = []
    for name, template in templates.items():
        if isinstance(template, dict) and template.get('enabled') is True and _pair(template, repository, environment):
            matches.append((name, template))
    _need(len(matches) == 1,
          'Select an approved repository/environment pair or ask an administrator to register one template')
    name, template = matches[0]
    _need(isinstance(name, str) and TEMPLATE_ID.fullmatch(name), 'Approved template identity is invalid')
    _need(set(template) <= TEMPLATE_FIELDS and PROFILE_FIELDS <= set(template),
          'Approved repository template contains invalid or missing policy fields')
    _template_policy(template)
    _need(PROJECT.fullmatch(project), 'Project identity cannot form a safe repository workspace')
    base = template.get('workspace_base')
    _need(isinstance(base, str) and base.startswith('/srv/abit-dev-linux/')
          and '\\' not in base and PurePosixPath(base).is_absolute()
          and str(PurePosixPath(base)) == base and '..' not in PurePosixPath(base).parts,
          'Approved template workspace base is invalid')
    from open_webui.utils.agentic_test_resources import limits
    try:
        limits(template)
    except ValueError as error:
        raise ProfileUnavailable(str(error)) from None
    profile = {key: deepcopy(template[key]) for key in PROFILE_FIELDS}
    if 'test_resources' in template:
        profile['test_resources'] = deepcopy(template['test_resources'])
    profile['workspace_root'] = str(PurePosixPath(base) / project)
    return profile, {'kind': 'template', 'template_id': name, 'ready': True}


def repository_readiness(config, project, repository, environment):
    """Return no commands, server paths, author identity or credentials."""
    try:
        _, provenance = resolve_repository_profile(config, project, repository, environment)
        return provenance
    except ProfileUnavailable as error:
        return {'ready': False, 'kind': 'unconfigured', 'message': str(error)}

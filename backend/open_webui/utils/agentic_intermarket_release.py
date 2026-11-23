"""PR237 parent protocol. Registration and execution require the original ERP/broker path."""
import re
import stat
from pathlib import Path

PROJECT = 'PROJ-0196'
HOST = 'abit-dev-linux'
REPOSITORY = 'https://github.com/ABiT-Consulting/sbodesk-sap-b1-enter-INTERMARKET'
ENVIRONMENT = 'ABIT-DEV-LINUX'
RESOURCE = 'intermarket-pr237'
UNIT = 'intermarket-sbodesk.service'
MANIFEST = '3af33454471443a9248a9a725ef951acf5f6ff5f967e4f1ecd2a1ee6c47a53d8'
SOURCE = '26432b2ef62bd8e61bd1a62016a874cef0347b11'
DLL = '5f31565f4999500a9f7555a9591e50593e70840026241dc5aba1c589d3720406'
INDEX = 'c572a3dda02ce5ccccad79d56a641d667fecd7a8146aa43aff30dd2be35a0325'
# Separate operator-owned package controller. PR233 has a different CLI, source and target.
DRIVER = '/usr/libexec/abit/intermarket-pr237/package_controller.py'
COMMAND = ('/usr/bin/python3', '-I', '-B', DRIVER)
SHA = re.compile('[0-9a-f]{64}')
MISSING_CAPABILITIES = {
    'ORIGINAL_PHASE_AUTHORITY_UNAVAILABLE':
        'Detached worker requires a fresh original Core and native decision for each mutating phase.',
    'AUTHENTICATED_HOST_HEALTH_OBSERVER_UNAVAILABLE':
        'The AF_UNIX-only worker requires a registered host observer for original candidate and prior health receipts.',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def scope_ok(project, repository, environment, host, resource):
    return (project, repository, environment, host, resource) == (PROJECT, REPOSITORY, ENVIRONMENT, HOST, RESOURCE)


def target(record, profile):
    require(scope_ok(record.get('project_id'), record.get('repository'), record.get('environment'),
                     profile.get('host_id'), record.get('resource_id')) and record.get('unit') == UNIT
            and profile.get('production_enabled') is False
            and profile.get('environment_class') in {'development', 'test'}, 'Wrong release parent scope')
    require(record.get('manifest_sha256') == MANIFEST and record.get('source') == SOURCE,
            'PR237 manifest/source mismatch')
    pins = {}
    for field in ('controller_sha256', 'rollback_manifest_sha256', 'rollback_policy_sha256',
                  'prior_dll_sha256', 'prior_index_sha256'):
        value = record.get(field)
        require(isinstance(value, str) and SHA.fullmatch(value), 'Missing reviewed release/rollback pin: ' + field)
        pins[field] = value
    require(pins['controller_sha256'] != '9397c144d8dd616e0f3abfac1d4fab5cadc87d25432a4b16c260c7d4bc19fae7'
            and pins['rollback_manifest_sha256'] != MANIFEST, 'Incompatible package controller or rollback target')
    from open_webui.utils.agentic_it_changes import INTERMARKET_WORKER_SHA
    return dict(pins, manager_worker_sha256=INTERMARKET_WORKER_SHA, manifest_sha256=MANIFEST, source=SOURCE, unit=UNIT, dll_sha256=DLL, index_sha256=INDEX,
                conditional_rollback='only-after-original-job-done-and-attributed-candidate-rejection')


def check_plan(plan):
    from open_webui.utils.agentic_it_changes import plan_authority_digest
    scope = plan['scope']
    t = plan['target']
    require(scope_ok(scope.get('project_id'), scope.get('repository'), scope.get('environment'),
                     plan.get('host_id'), plan.get('resource_id')) and plan['operation'] == 'deploy_release'
            and scope.get('actor', {}).get('role') == 'it_manager' and tuple(plan['fixed_argv']) == COMMAND
            and plan['plan_digest'] == plan_authority_digest(plan), 'Original release plan differs')
    expected = target(dict(t, **scope, resource_id=RESOURCE),
                      {'host_id': HOST, 'environment_class': 'development', 'production_enabled': False})
    require(t == expected, 'Original release target differs')


def check_worker(pin):
    from open_webui.utils.agentic_it_changes import _read_protected
    path = Path(DRIVER)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'Unprotected package controller directory')
    proof = _read_protected(str(path), [str(path.parent)], 0, maximum=512000)
    require(proof['sha256'] == pin, 'Registered package controller source changed')

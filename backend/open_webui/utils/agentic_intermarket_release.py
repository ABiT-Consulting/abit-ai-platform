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


NATIVE_FIELDS = ('project_id', 'task_id', 'action_id', 'run_id', 'invocation_id', 'plan_digest',
                 'receipt_id', 'approval_id', 'action_fingerprint')
PHASES = ('preflight', 'publish', 'activation-restart', 'verify-candidate', 'restore', 'rollback-restart', 'verify-prior')
PROTOCOL = 'original-core-release-v1'


def original_authority(grant):
    return {k: grant[k] for k in (*NATIVE_FIELDS, 'maintenance_lease_id','maintenance_epoch','approval_expires_at')}


def check_challenge(challenge, plan, original):
    from open_webui.utils.agentic_it_changes import digest
    import time
    q, request = challenge['phase_request'], challenge['request']
    require(set(challenge) == {'phase_request','request','expires_at'} and
            type(challenge['expires_at']) is int and time.time() < challenge['expires_at'] <= int(time.time()) + 30,
            'Expired original phase challenge')
    require(set(q) == {'phase','original','plan','previous'} and q['original'] == original and q['plan'] == plan
            and q['phase'] in PHASES and set(request) == {'op','phase','checkpoint','nonce','request_sha256'}
            and request['op'] in ('authorize','observe') and request['phase'] == q['phase']
            and SHA.fullmatch(request['nonce']) and re.fullmatch('[a-z0-9-]{1,64}',request['checkpoint'])
            and request['request_sha256'] == digest(q), 'Phase challenge differs from original')
    if request['op'] == 'observe':
        require((request['checkpoint'] == 'served-identity' and q['phase'] in ('preflight','publish','verify-candidate','verify-prior'))
                or (request['checkpoint'] == 'selector-preimage' and q['phase'] in ('publish','restore')),
                'Unknown host observation')


def check_decision(proof, request):
    from open_webui.utils.agentic_it_changes import digest
    import time
    require(proof.get('authorized') is True and proof.get('phase') == request['phase']
            and proof.get('checkpoint') == request['checkpoint'] and proof.get('challenge_sha256') == digest(request)
            and type(proof.get('expires_at')) is int and time.time() < proof['expires_at'] <= int(time.time()) + 5,
            'Phase authority is stale or belongs to another boundary')


async def run_controller(mode, body, pin, exchange=None):
    """Execute only the registered controller. The child inherits one private IPC fd."""
    import asyncio
    import json
    import socket
    import struct
    from open_webui.utils.agentic_it_changes import canonical
    require(mode in ('observe-host','preflight','publish','verify-candidate','restore','verify-prior'),
            'Unsupported controller operation')
    check_worker(pin)
    left = right = None
    proc = None
    env = {'LANG':'C.UTF-8','PATH':'/usr/bin:/bin'}
    try:
        fds = ()
        if exchange:
            left, right = socket.socketpair()
            env['INTERMARKET_PHASE_FD'] = str(right.fileno())
            fds = (right.fileno(),)
        proc = await asyncio.create_subprocess_exec(*COMMAND, mode, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,env=env,pass_fds=fds,limit=65536)
        if right:
            right.close()
        async def collect():
            raw = await proc.stdout.readuntil(b'\n')
            require(len(raw) <= 64000 and not await proc.stdout.read(1) and await proc.wait() == 0,
                    'Original controller receipt unavailable')
            return json.loads(raw)

        async def serve():
            if not left:
                return
            reader, writer = await asyncio.open_connection(sock=left)
            try:
                for _ in range(16):
                    try:
                        header = await reader.readexactly(4)
                    except asyncio.IncompleteReadError as error:
                        require(not error.partial, 'Incomplete checkpoint frame')
                        return
                    size = struct.unpack('!I', header)[0]
                    require(0 < size <= 4096, 'Oversized checkpoint frame')
                    request = json.loads(await reader.readexactly(size))
                    proof = await exchange(body, request)
                    reply = canonical({'request':request, 'proof':proof}).encode()
                    require(len(reply) <= 64000, 'Oversized phase proof')
                    writer.write(struct.pack('!I',len(reply)) + reply)
                    await writer.drain()
                raise ValueError('Controller exceeded checkpoint count')
            finally:
                writer.close()
                await writer.wait_closed()
        async with asyncio.timeout(60):
            proc.stdin.write(canonical(body).encode())
            await proc.stdin.drain()
            proc.stdin.close()
            async with asyncio.TaskGroup() as group:
                output = group.create_task(collect())
                group.create_task(serve())
            return output.result()
    finally:
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        if left:
            left.close()
        if right:
            right.close()


async def host_observation(challenge):
    from open_webui.utils.agentic_it_changes import digest
    import time
    request, q = challenge['request'], challenge['phase_request']
    plan, original, previous = q['plan'], q['original'], q['previous']
    proof = await run_controller('observe-host', {'request':request, 'phase_request':q},plan['target']['controller_sha256'])
    prior = q['phase'] not in ('restore','verify-candidate')
    generation = plan['baseline']['invocation_id'] if q['phase'] in ('preflight','publish') else (
        previous['invocation_id'] if q['phase'] == 'restore' else previous['invocationId'])
    require(set(proof) == set('original_sha256 plan_digest request_sha256 challenge_sha256 phase unit invocation_id pid start_ticks manifest_sha256 dll_sha256 index_sha256 bundle_path bundle_sha256 main_path main_sha256 health_sha256 host_preimage_sha256 job_sha256 observed_at'.split())
            and proof['original_sha256'] == digest(original) and proof['plan_digest'] == plan['plan_digest']
            and proof['request_sha256'] == digest(q) and proof['challenge_sha256'] == digest(request)
            and proof['phase'] == q['phase'] and proof['unit'] == UNIT and proof['invocation_id'] == generation
            and proof['job_sha256'] == digest(previous) and type(proof['observed_at']) is int
            and 0 <= time.time() - proof['observed_at'] <= 10 and type(proof['pid']) is int and proof['pid'] > 1
            and str(proof['start_ticks']).isdigit() and int(proof['start_ticks']) > 0
            and proof['manifest_sha256'] == plan['target']['rollback_manifest_sha256' if prior else 'manifest_sha256']
            and proof['dll_sha256'] == plan['target']['prior_dll_sha256' if prior else 'dll_sha256']
            and all(SHA.fullmatch(proof[k]) for k in ('index_sha256','bundle_sha256','main_sha256','health_sha256','host_preimage_sha256')),
            'Host observer identity, generation or original phase changed')
    return proof


OBSERVE = ('observe','served-identity')
SELECTOR = (('authorize','prepare-selector'), ('observe','selector-preimage'),
            ('authorize','switch-selector'), ('authorize','reload-manager'))
CHECKPOINTS = {
    'preflight': (OBSERVE,),
    'publish': (OBSERVE,) + tuple(('authorize',k) for k in ('initialize-journal','stage-private','publish-tree')) + SELECTOR,
    'verify-candidate': (OBSERVE,),
    'restore': SELECTOR,
    'verify-prior': (OBSERVE,),
}
BUNDLES = {
    False: ('/assets/index-BIhTIZ92.js','4a06464295478472a7b0a547780e96b5a2b3f66e6f3253fffcd92c3896a5748f'),
    True: ('/assets/index-DdI_Fl_f.js','b4e9ae265c9824eba16ef63b0d67141c3c2665fb4a268406d83a7ad1d53ddbef'),
}


MAIN = {
    False: ('/assets/main-CJ-z5CQi.js','eb18252308dfb93dd0e1042fcd4e50471f5bd846c8f361d3ebdcd9e1d263e360'),
    True: ('/assets/main-CO2VyvjM.js','cfeabe2302e09af4a37a79049d13fb106f14fd7995a7a5ed26aca158048d8c7a'),
}


def served_matches(proof, candidate, index):
    require(proof['bundle_path'] in {v[0] for v in BUNDLES.values()} and proof['main_path'] in {v[0] for v in MAIN.values()},
            'Unrecognized served application entry point')
    return (proof['index_sha256'],proof['bundle_path'],proof['bundle_sha256'],proof['main_path'],proof['main_sha256']) == (
        index,*BUNDLES[candidate],*MAIN[candidate])


async def driver_phase(q, exchange):
    from open_webui.utils.agentic_it_changes import digest
    phase, plan = q['phase'], q['plan']
    target, previous = plan['target'], q['previous']
    steps = iter(CHECKPOINTS[phase])
    observed = None

    async def checkpoint(body, request):
        nonlocal observed
        require(body == q and next(steps, None) == (request.get('op'), request.get('checkpoint'))
                and request.get('request_sha256') == digest(q) and request.get('phase') == phase,
                'Unexpected controller checkpoint order or identity')
        proof = await exchange(q, request)
        if request['op'] == 'authorize':
            check_decision(proof, request)
        else:
            observed = proof
            candidate = phase in ('restore','verify-candidate')
            expected_index = target['index_sha256' if candidate else 'prior_index_sha256']
            require(candidate or served_matches(proof, False, expected_index), 'Original preimage served bytes differ')
        return proof

    result = await run_controller(phase, q, target['controller_sha256'], checkpoint)
    require(next(steps, None) is None, 'Controller returned before its final authenticated checkpoint')
    candidate = phase in ('publish','verify-candidate')
    gen = previous['invocationId'] if phase.startswith('verify-') else (
        previous['invocation_id'] if phase == 'restore' else plan['baseline']['invocation_id'])
    verdict = 'verified'
    if phase == 'verify-candidate' and not served_matches(observed, True, target['index_sha256']):
        verdict = 'rejected'
    require(set(result) == set('phase original plan_digest verdict manifest_sha256 dll_sha256 index_sha256 invocation_id evidence_sha256'.split())
            and result['phase'] == phase and result['original'] == q['original'] and result['plan_digest'] == plan['plan_digest']
            and result['verdict'] == verdict and result['invocation_id'] == gen and SHA.fullmatch(result['evidence_sha256'])
            and result['manifest_sha256'] == target['manifest_sha256' if candidate else 'rollback_manifest_sha256']
            and result['dll_sha256'] == target['dll_sha256' if candidate else 'prior_dll_sha256']
            and result['index_sha256'] == target['index_sha256' if candidate else 'prior_index_sha256'],
            'Driver result differs from authenticated phase observations')
    return result

"""PR241 AUTH follow-on parent protocol. Registration and execution require the original ERP/broker path."""
import re
import stat
from pathlib import Path

PROJECT = 'PROJ-0196'
HOST = 'abit-dev-linux'
REPOSITORY = 'https://github.com/ABiT-Consulting/sbodesk-sap-b1-enter-INTERMARKET'
ENVIRONMENT = 'ABIT-DEV-LINUX'
RESOURCE = 'intermarket-pr241-auth-guard'
UNIT = 'intermarket-sbodesk.service'
MANIFEST = '7bf2c4e8adbcc197ebf58b3da7c10068da4932dfc28b244422b08334aec33675'
SOURCE = '922218853c587d2d1c5c7ff41332078b67c7455c'
DLL = 'b4fe119151fddcf9674796f04840abeefee195066b185ca62bf0415e9ad67e5c'
INDEX = 'b33393b92dd11754843add693b88f42d6d6f34e47d9226dda010fb8e9d299322'
# Separate operator-owned package controller. PR233 has a different CLI, source and target.
DRIVER = '/usr/libexec/abit/intermarket-pr241-auth-guard/package_controller.py'
CONTROLLER_SHA256 = '51fce4c13f2300bc0a23e3162fbe139a78f17cad56a2e64409fc7d116117cb58'
COMMAND = ('/usr/bin/python3', '-I', '-B', DRIVER)
SHA = re.compile('[0-9a-f]{64}')
RESTART_GUARD_SHA256 = '685f8bb73ad68225f8d97d0734ac3b69f0066c0b46b27dbd1d6390ed63544d6e'
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
            'PR241 AUTH follow-on manifest/source mismatch')
    pins = {}
    for field in ('controller_sha256', 'rollback_manifest_sha256', 'rollback_policy_sha256',
                  'prior_dll_sha256', 'prior_index_sha256'):
        value = record.get(field)
        require(isinstance(value, str) and SHA.fullmatch(value), 'Missing reviewed release/rollback pin: ' + field)
        pins[field] = value
    require(pins['controller_sha256'] == CONTROLLER_SHA256
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
    require(pin == CONTROLLER_SHA256, 'Unreviewed fixed follow-on controller pin')
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
        require(('observe', request['checkpoint']) in CHECKPOINTS.get(q['phase'], ()),
                'Unknown fixed follow-on host observation')


def check_phase_scope(challenge):
    """Validate the requested boundary before consulting the native authority."""
    q, frame = challenge['phase_request'], challenge['request']
    phase, prev, plan = q['phase'], q['previous'], q['plan']
    allowed = (('authorize','phase-entry'),) + CHECKPOINTS.get(phase, ())
    require((frame['op'],frame['checkpoint']) in allowed, 'Unregistered original release boundary')
    if phase == 'preflight':
        require(prev is None, 'Unexpected preflight predecessor')
        return
    if phase.startswith('verify-'):
        job = prev['managerJob']
        require(prev['exit_code'] == 0 and job['unit'] == UNIT and job['result'] == 'done'
                and type(job['jobId']) is int and 0 < job['jobId'] < 2**32
                and re.fullmatch('[0-9a-f]{32}', prev['invocationId']), 'Original completed manager job required')
        return
    before = {'publish':'preflight','activation-restart':'publish','restore':'verify-candidate','rollback-restart':'restore'}[phase]
    candidate = before in ('publish','verify-candidate')
    pins = plan['target']
    require(prev['phase'] == before and prev['original'] == q['original'] and prev['plan_digest'] == plan['plan_digest']
            and prev['verdict'] == ('rejected' if phase == 'restore' else 'verified') and SHA.fullmatch(prev['evidence_sha256'])
            and prev['manifest_sha256'] == pins['manifest_sha256' if candidate else 'rollback_manifest_sha256']
            and prev['dll_sha256'] == pins['dll_sha256' if candidate else 'prior_dll_sha256']
            and prev['index_sha256'] == pins['index_sha256' if candidate else 'prior_index_sha256']
            and re.fullmatch('[0-9a-f]{32}',prev['invocation_id']), 'Original package predecessor required')


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


def check_resource_guard(guard, phase, checkpoint):
    """Validate fixed refusal thresholds from the authenticated host observer."""
    fields = set('memoryAvailableBefore memoryAvailableAfter minimumMemoryAvailableBytes '
                 'minimumRemainingDiskBytes minimumRemainingInodes additionalBytes '
                 'additionalInodes filesystems policy'.split())
    require(type(guard) is dict and set(guard) == fields, 'Host resource guard shape differs')
    integer_fields = fields - {'filesystems', 'policy'}
    require(all(type(guard[k]) is int and guard[k] >= 0 for k in integer_fields),
            'Host resource observations must be nonnegative integers')
    allocating = (phase, checkpoint) in {
        ('preflight', 'served-identity'), ('publish', 'served-identity'), ('publish', 'stage-preimage')}
    additional = (346877952, 1217) if allocating else (0, 0)
    require((guard['minimumMemoryAvailableBytes'], guard['minimumRemainingDiskBytes'],
             guard['minimumRemainingInodes']) == (2281701376, 5368709120, 4096)
            and (guard['additionalBytes'], guard['additionalInodes']) == additional
            and guard['memoryAvailableBefore'] >= 2281701376
            and guard['memoryAvailableAfter'] >= 2281701376
            and guard['policy'] == 'additional-local-refusal-only-server-guards-unchanged',
            'Current fixed memory or allocation guard failed')
    paths = {'/var/lib/intermarket-pr241-auth-guard-activation',
             '/etc/systemd/system/intermarket-sbodesk.service.d',
             '/srv/abit-dev-linux/intermarket-dev-deployment/storage'}
    require(type(guard['filesystems']) is dict and set(guard['filesystems']) == paths,
            'Fixed release guard filesystems differ')
    for observation in guard['filesystems'].values():
        require(type(observation) is dict and set(observation) == {'device', 'freeBytes', 'freeInodes'}
                and all(type(observation[k]) is int for k in observation)
                and observation['device'] > 0
                and observation['freeBytes'] >= 5368709120 + additional[0]
                and observation['freeInodes'] >= 4096 + additional[1],
                'Current disk or inode recovery reserve failed')


def check_host_observation(proof, challenge):
    from open_webui.utils.agentic_it_changes import digest
    import time
    request, q = challenge['request'], challenge['phase_request']
    plan, original, previous = q['plan'], q['original'], q['previous']
    # The selected files and the running process differ before a restart.
    # Package/controller verifies the loaded selector independently.
    prior = q['phase'] not in ('restore', 'verify-candidate', 'rollback-restart')
    if q['phase'] in ('preflight', 'publish', 'activation-restart'):
        generation = plan['baseline']['invocation_id']
    elif q['phase'] in ('restore', 'rollback-restart'):
        generation = previous['invocation_id']
    else:
        generation = previous['invocationId']
    require(set(proof) == set('original_sha256 plan_digest request_sha256 challenge_sha256 phase unit invocation_id pid start_ticks manifest_sha256 dll_sha256 index_sha256 bundle_path bundle_sha256 main_path main_sha256 health_sha256 host_preimage_sha256 job_sha256 observed_at resource_guard restart_sample'.split())
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
    check_resource_guard(proof['resource_guard'], q['phase'], request['checkpoint'])
    if request['checkpoint'] == 'restart-preimage':
        check_restart_sample(proof['restart_sample'], proof)
    else:
        require(proof['restart_sample'] is None, 'Unexpected restart sample on full observation')
    return proof


async def host_observation(challenge):
    q = challenge['phase_request']
    proof = await run_controller('observe-host',
        {'request': challenge['request'], 'phase_request': q}, q['plan']['target']['controller_sha256'])
    if challenge['request']['checkpoint'] == 'final-restart-resources':
        return check_final_restart_observation(proof, challenge)
    return check_host_observation(proof, challenge)


def check_restart_observation(proof, challenge):
    q = challenge['phase_request']
    require(q['phase'] in ('activation-restart', 'rollback-restart')
            and challenge['request']['op'] == 'observe'
            and challenge['request']['checkpoint'] == 'restart-preimage',
            'Fixed pre-restart observation required')
    check_host_observation(proof, challenge)
    if q['phase'] == 'activation-restart':
        require(served_matches(proof, False, q['plan']['target']['prior_index_sha256']),
                'Original pre-restart application bytes differ')
    else:
        require(not served_matches(proof, True, q['plan']['target']['index_sha256']),
                'Original candidate rejection is no longer observable')


def check_restart_sample(sample, proof):
    require(type(sample) is dict and set(sample) == {
        'unit','pid','start_ticks','invocation_id','manager_sha256','dropin_directory','fragments'}
        and sample['unit'] == UNIT and type(sample['pid']) is int and sample['pid'] > 1
        and type(sample['start_ticks']) is int and sample['start_ticks'] > 0
        and sample['pid'] == proof['pid'] and str(sample['start_ticks']) == str(proof['start_ticks'])
        and sample['invocation_id'] == proof['invocation_id']
        and SHA.fullmatch(str(sample['manager_sha256']))
        and type(sample['dropin_directory']) is dict and type(sample['fragments']) is dict
        and 1 <= len(sample['fragments']) <= 33,
        'Exact current restart sample differs from observed process')
    keys = {'st_dev','st_ino','st_uid','st_gid','st_mode','st_size','st_mtime_ns','st_ctime_ns'}
    def valid_token(value, directory=False):
        return (type(value) is dict and set(value) == keys
                and all(type(v) is int and v >= 0 for v in value.values())
                and value['st_dev'] > 0 and value['st_ino'] > 0
                and (stat.S_ISDIR(value['st_mode']) if directory else stat.S_ISREG(value['st_mode'])))
    require(valid_token(sample['dropin_directory'], True), 'Restart directory token differs')
    require('/etc/systemd/system/intermarket-sbodesk.service' in sample['fragments'],
            'Restart unit fragment missing')
    for path, value in sample['fragments'].items():
        permitted = (path == '/etc/systemd/system/intermarket-sbodesk.service' or
                     type(path) is str and Path(path).parent == Path('/etc/systemd/system/intermarket-sbodesk.service.d')
                     and re.fullmatch('[A-Za-z0-9_.-]+[.]conf', Path(path).name)
                     and not Path(path).name.startswith('.'))
        require(permitted and type(value) is dict and set(value) == {'identity','sha256'}
                and valid_token(value['identity']) and 0 < value['identity']['st_size'] <= 65536
                and SHA.fullmatch(str(value['sha256'])), 'Restart fragment token or digest differs')


def check_final_restart_observation(proof, challenge, heavy=None):
    """A fresh bounded read; it never supplies authority to dispatch restart."""
    from open_webui.utils.agentic_it_changes import digest
    import time
    q, request = challenge['phase_request'], challenge['request']
    require(q['phase'] in ('activation-restart', 'rollback-restart')
            and request['op'] == 'observe' and request['checkpoint'] == 'final-restart-resources',
            'Fixed final restart read required')
    fields = set('original_sha256 plan_digest request_sha256 challenge_sha256 phase unit '
                 'invocation_id pid start_ticks job_sha256 observed_at resource_guard '
                 'restart_sample prior_observation_sha256'.split())
    generation = (q['plan']['baseline']['invocation_id'] if q['phase'] == 'activation-restart'
                  else q['previous']['invocation_id'])
    require(type(proof) is dict and set(proof) == fields
            and proof['original_sha256'] == digest(q['original'])
            and proof['plan_digest'] == q['plan']['plan_digest']
            and proof['request_sha256'] == digest(q)
            and proof['challenge_sha256'] == digest(request)
            and proof['phase'] == q['phase'] and proof['unit'] == UNIT
            and proof['invocation_id'] == generation and proof['job_sha256'] == digest(q['previous'])
            and type(proof['pid']) is int and proof['pid'] > 1
            and str(proof['start_ticks']).isdigit() and int(proof['start_ticks']) > 0
            and type(proof['observed_at']) is int and 0 <= time.time() - proof['observed_at'] <= 10
            and SHA.fullmatch(str(proof['prior_observation_sha256'])),
            'Final restart read differs from original challenge or generation')
    check_resource_guard(proof['resource_guard'], q['phase'], request['checkpoint'])
    check_restart_sample(proof['restart_sample'], proof)
    if heavy is not None:
        require(proof['prior_observation_sha256'] == digest(heavy)
                and proof['restart_sample'] == heavy['restart_sample']
                and {p:v['device'] for p,v in proof['resource_guard']['filesystems'].items()} ==
                    {p:v['device'] for p,v in heavy['resource_guard']['filesystems'].items()},
                'Final read does not match original heavy restart preimage')
    return proof


async def require_current_restart_sample(expected):
    """Read the fixed unit after native awaits, with no extra worker privilege."""
    import asyncio
    from open_webui.utils import agentic_intermarket_restart_guard as guard
    from open_webui.utils.agentic_it_changes import _read_protected
    path = Path(guard.__file__).absolute()
    require(path.name == 'agentic_intermarket_restart_guard.py'
            and path.resolve(strict=True) == path, 'Protected fixed restart sampler required')
    for parent in path.parents:
        meta = parent.lstat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022,
                'Restart sampler ancestor is writable')
    source = _read_protected(str(path), [str(path.parent)], 0, maximum=65536)
    require(source['sha256'] == RESTART_GUARD_SHA256, 'Reviewed restart sampler source changed')
    actual = await asyncio.to_thread(guard.sample_restart_precondition)
    require(actual == expected, 'Original service or selector changed after native decision')
    return actual


OBSERVE = ('observe','served-identity')
SELECTOR = (('observe','prepare-selector-preimage'), ('authorize','prepare-selector'),
            ('observe','selector-preimage'), ('authorize','switch-selector'),
            ('observe','reload-preimage'), ('authorize','reload-manager'))
CHECKPOINTS = {
    'preflight': (OBSERVE,),
    'publish': (OBSERVE, ('authorize','initialize-journal'),
                ('observe','stage-preimage'), ('authorize','stage-private'),
                ('observe','publish-preimage'), ('authorize','publish-tree')) + SELECTOR,
    'activation-restart': (('observe','restart-preimage'), ('observe','final-restart-resources')),
    'verify-candidate': (OBSERVE,),
    'restore': SELECTOR,
    'rollback-restart': (('observe','restart-preimage'), ('observe','final-restart-resources')),
    'verify-prior': (OBSERVE,),
}
BUNDLES = {
    False: ('/assets/index-BIhTIZ92.js','4a06464295478472a7b0a547780e96b5a2b3f66e6f3253fffcd92c3896a5748f'),
    True: ('/assets/index-kyenB3H8.js','bf0c25e457f256a235561a28c11a023dbe3da1386e9e0ce3c685b9f897b0f84c'),
}


MAIN = {
    False: ('/assets/main-CJ-z5CQi.js','eb18252308dfb93dd0e1042fcd4e50471f5bd846c8f361d3ebdcd9e1d263e360'),
    True: ('/assets/main-BmAnUSyN.js','72636819476ef3b91033d3a437f70ae33a9c34490c341ff14d7b97f049014b63'),
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
            if phase == 'restore':
                require(not served_matches(proof, True, expected_index), 'Original candidate rejection is no longer observable')
            elif not candidate:
                require(served_matches(proof, False, expected_index), 'Original preimage served bytes differ')
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

"""Synthetic isolated protocol, journal and race tests; no host service dispatch."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, ROOT


class FixedFollowOn(Fixture):
    def setUp(self):
        super().setUp()
        self.changes = self.load('agentic_it_changes')
        self.physical = self.load('agentic_it_physical')
        self.old = self.load('agentic_intermarket_release')
        self.new = self.load('agentic_intermarket_pr241_release')
        self.registry = self.load('agentic_intermarket_release_registry')
        self.guard = self.load('agentic_intermarket_restart_guard')

    def plan(self, module):
        record = dict(project_id=module.PROJECT, repository=module.REPOSITORY,
            environment=module.ENVIRONMENT, resource_id=module.RESOURCE, unit=module.UNIT,
            manifest_sha256=module.MANIFEST, source=module.SOURCE,
            controller_sha256=getattr(module,'CONTROLLER_SHA256','c'*64), rollback_manifest_sha256='8'*64,
            rollback_policy_sha256='d'*64, prior_dll_sha256='1'*64, prior_index_sha256='e'*64)
        target = module.target(record, dict(host_id=module.HOST, production_enabled=False,
                                            environment_class='development'))
        value = dict(schema_version=1, operation='deploy_release', resource_id=module.RESOURCE,
            scope=dict(project_id=module.PROJECT, task_id='TASK-synthetic-release',
                repository=module.REPOSITORY, environment=module.ENVIRONMENT,
                actor=dict(role='it_manager')), host_id=module.HOST, policy_digest='b'*64,
            target=target, baseline=dict(unit=module.UNIT, invocation_id='2'*32),
            fixed_argv=list(module.COMMAND), safety={}, state='prepared', executable=True)
        value['plan_digest'] = self.changes.plan_authority_digest(value)
        return value

    def authority(self, plan):
        return dict(project_id='PROJ-0196', task_id='TASK-synthetic-release',
            action_id='action-synthetic-release', run_id='run-synthetic-release',
            invocation_id='invocation-synthetic-release', plan_digest=plan['plan_digest'],
            receipt_id='receipt-synthetic', approval_id='approval-synthetic',
            action_fingerprint='a'*64, maintenance_lease_id='lease-synthetic',
            maintenance_epoch=100, approval_expires_at=int(time.time())+60)

    def predecessor(self, plan, authority, phase):
        prior = phase in ('preflight', 'restore', 'verify-prior')
        return dict(phase=phase, original=authority, plan_digest=plan['plan_digest'],
            verdict='verified', evidence_sha256='a'*64,
            manifest_sha256=plan['target']['rollback_manifest_sha256' if prior else 'manifest_sha256'],
            dll_sha256=plan['target']['prior_dll_sha256' if prior else 'dll_sha256'],
            index_sha256=plan['target']['prior_index_sha256' if prior else 'index_sha256'],
            invocation_id='3'*32 if phase in ('restore','verify-candidate','verify-prior') else '2'*32)

    def resources(self, phase, checkpoint):
        allocating=(phase,checkpoint) in {('preflight','served-identity'),
            ('publish','served-identity'),('publish','stage-preimage')}
        return dict(memoryAvailableBefore=3*1024**3, memoryAvailableAfter=3*1024**3,
            minimumMemoryAvailableBytes=2281701376, minimumRemainingDiskBytes=5368709120,
            minimumRemainingInodes=4096, additionalBytes=346877952 if allocating else 0,
            additionalInodes=1217 if allocating else 0,
            filesystems={p:dict(device=2049,freeBytes=8*1024**3,freeInodes=20000) for p in (
                '/var/lib/intermarket-pr241-auth-guard-activation',
                '/etc/systemd/system/intermarket-sbodesk.service.d',
                '/srv/abit-dev-linux/intermarket-dev-deployment/storage')},
            policy='additional-local-refusal-only-server-guards-unchanged')

    def sample(self, generation='2'*32, pid=123):
        token=dict(st_dev=2049,st_ino=20,st_uid=0,st_gid=0,st_mode=stat.S_IFREG|0o644,
                   st_size=100,st_mtime_ns=1,st_ctime_ns=1)
        return dict(unit=self.new.UNIT,pid=pid,start_ticks=321,invocation_id=generation,
            manager_sha256='a'*64,dropin_directory=dict(token,st_mode=stat.S_IFDIR|0o755),
            fragments={'/etc/systemd/system/intermarket-sbodesk.service':
                       dict(identity=token,sha256='f'*64)})

    def observation(self, q, request):
        prior=q['phase'] not in ('restore','verify-candidate','rollback-restart')
        generation=(q['plan']['baseline']['invocation_id'] if q['phase'] in ('preflight','publish','activation-restart')
                    else q['previous']['invocation_id'] if q['phase'] in ('restore','rollback-restart')
                    else q['previous']['invocationId'])
        proof=dict(original_sha256=self.changes.digest(q['original']),plan_digest=q['plan']['plan_digest'],
            request_sha256=self.changes.digest(q),challenge_sha256=self.changes.digest(request),
            phase=q['phase'],unit=self.new.UNIT,invocation_id=generation,pid=123,start_ticks='321',
            manifest_sha256=q['plan']['target']['rollback_manifest_sha256' if prior else 'manifest_sha256'],
            dll_sha256=q['plan']['target']['prior_dll_sha256' if prior else 'dll_sha256'],
            index_sha256=q['plan']['target']['prior_index_sha256' if prior else 'index_sha256'],
            bundle_path=self.new.BUNDLES[not prior][0],bundle_sha256=self.new.BUNDLES[not prior][1],
            main_path=self.new.MAIN[not prior][0],main_sha256=self.new.MAIN[not prior][1],
            health_sha256='a'*64,host_preimage_sha256='b'*64,
            job_sha256=self.changes.digest(q['previous']),observed_at=int(time.time()),
            resource_guard=self.resources(q['phase'],request['checkpoint']),restart_sample=None)
        if request['checkpoint']=='restart-preimage':
            proof['restart_sample']=self.sample(generation)
        return proof

    def final_observation(self,q,request,heavy):
        result={k:deepcopy(heavy[k]) for k in ('original_sha256','plan_digest','phase','unit',
            'invocation_id','pid','start_ticks','job_sha256','restart_sample')}
        result.update(request_sha256=self.changes.digest(q),challenge_sha256=self.changes.digest(request),
            observed_at=int(time.time()),resource_guard=self.resources(q['phase'],request['checkpoint']),
            prior_observation_sha256=self.changes.digest(heavy))
        return result

    def executor(self, name):
        handlers=[]
        for module in (self.old,self.new):
            handlers.append(self.physical.PhysicalHandler('deploy_release',module.RESOURCE,
                module.PROJECT,module.REPOSITORY,module.ENVIRONMENT,socket.gethostname(),os.geteuid(),
                lambda plan:[tuple(plan['fixed_argv'])],lambda plan:True,
                physical_identity='systemd:intermarket-sbodesk.service'))
        handlers.append(self.physical.PhysicalHandler('restart_service','same-dedicated-service',
            self.new.PROJECT,self.new.REPOSITORY,self.new.ENVIRONMENT,socket.gethostname(),os.geteuid(),
            lambda plan:[tuple(plan['fixed_argv'])],lambda plan:True,
            physical_identity='systemd:intermarket-sbodesk.service'))
        return self.physical.DurablePhysicalExecutor(Path(self.temp.name)/name,handlers,
                                                    run_lookup=lambda invocation:'run-synthetic-release')

    def test_only_two_fixed_packages_and_publication_roots_are_selectable(self):
        self.assertIs(self.registry.release_for(self.old.RESOURCE),self.old)
        self.assertIs(self.registry.release_for(self.new.RESOURCE),self.new)
        self.assertEqual(self.registry.activation_parent(self.new.RESOURCE),
                         '/var/lib/intermarket-pr241-auth-guard-activation')
        for bad in ('arbitrary','../../module',None,{'resource':'intermarket-pr237'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.registry.release_for(bad)
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.registry.activation_parent(bad)
        self.assertEqual(hashlib.sha256((ROOT/'agentic_intermarket_release.py').read_bytes()).hexdigest(),
                         'd37847e692062a7dcf5fd4efb52457dff9ff7d50abd1bcf6a25911e423f6c272')
        self.assertEqual(hashlib.sha256((ROOT/'agentic_intermarket_restart_guard.py').read_bytes()).hexdigest(),
                         self.new.RESTART_GUARD_SHA256)

    def test_cross_resource_and_mixed_candidate_plans_are_rejected(self):
        for module,other in ((self.old,self.new),(self.new,self.old)):
            plan=self.plan(module);module.check_plan(plan)
            with self.assertRaises(ValueError):other.check_plan(plan)
            for field in ('manifest_sha256','source','dll_sha256','index_sha256'):
                altered=deepcopy(plan);altered['target'][field]=self.plan(other)['target'][field]
                altered['plan_digest']=self.changes.plan_authority_digest(altered)
                with self.subTest(resource=module.RESOURCE,field=field),self.assertRaises(ValueError):
                    module.check_plan(altered)
            altered=deepcopy(plan);altered['fixed_argv']=list(other.COMMAND)
            altered['plan_digest']=self.changes.plan_authority_digest(altered)
            with self.assertRaises(ValueError):module.check_plan(altered)

    def test_wrong_reviewed_controller_hash_denies_before_dispatch(self):
        meta=SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)
        with patch.object(Path,'lstat',return_value=meta),patch.object(
                self.changes,'_read_protected',return_value={'sha256':'0'*64}) as read:
            with self.assertRaisesRegex(ValueError,'controller source changed'):
                self.new.check_worker(self.new.CONTROLLER_SHA256)
            self.assertEqual(read.call_args.args[0],self.new.DRIVER)

    async def test_both_release_directions_and_restart_share_one_uncertain_unit_reservation(self):
        labels=('old','new','restart')
        for first,second in ((a,b) for a in labels for b in labels if a!=b):
            with self.subTest(first=first,second=second):
                executor=self.executor('state-'+first+'-'+second)
                plans={'old':self.plan(self.old),'new':self.plan(self.new)}
                plans['restart']=deepcopy(plans['new'])
                plans['restart'].update(operation='restart_service',resource_id='same-dedicated-service',
                                        fixed_argv=['/bin/false'])
                plans['restart']['plan_digest']=self.changes.plan_authority_digest(plans['restart'])
                parent=AsyncMock(side_effect=self.physical.ChangeOutcomeUncertain('synthetic no dispatch'))
                command=AsyncMock(side_effect=self.physical.ChangeOutcomeUncertain('synthetic no dispatch'))
                async def execute(label, suffix):
                    plan=plans[label]
                    return await executor._execute_once(plan,'action-'+suffix,'invocation-'+suffix,
                        tuple(plan['fixed_argv']),release_authority={'synthetic':True} if label!='restart' else None)
                with patch.object(executor,'_run_parent',parent),patch.object(executor,'_command',command):
                    with self.assertRaises(self.physical.ChangeOutcomeUncertain):await execute(first,'first')
                    calls=(parent.await_count,command.await_count)
                    with self.assertRaisesRegex(self.physical.ChangeOutcomeUncertain,'unsettled'):
                        await execute(second,'second')
                    with self.assertRaisesRegex(self.physical.ChangeOutcomeUncertain,'uncertain'):
                        await execute(first,'first')
                    self.assertEqual(calls,(parent.await_count,command.await_count))
                with executor._db(False) as db:
                    rows=db.execute('SELECT resource_id,physical_identity,state FROM original_operation').fetchall()
                self.assertEqual(len(rows),1)
                self.assertEqual(tuple(rows[0]),(plans[first]['resource_id'],'systemd:intermarket-sbodesk.service','uncertain'))

    def test_cross_resource_original_grant_challenge_and_predecessor_replay_denied(self):
        plan=self.plan(self.new);auth=self.authority(plan);old=self.plan(self.old)
        q=dict(phase='publish',original=auth,plan=plan,
               previous=self.predecessor(plan,auth,'preflight'))
        request=dict(op='authorize',phase='publish',checkpoint='phase-entry',
                     nonce='a'*64,request_sha256=self.changes.digest(q))
        challenge=dict(phase_request=q,request=request,expires_at=int(time.time())+20)
        self.new.check_challenge(challenge,plan,auth);self.new.check_phase_scope(challenge)
        for key,value in (('plan',old),('original',self.authority(old))):
            bad=deepcopy(challenge);bad['phase_request'][key]=value
            bad['request']['request_sha256']=self.changes.digest(bad['phase_request'])
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.new.check_challenge(bad,plan,auth)
        bad=deepcopy(challenge)
        bad['phase_request']['previous']=self.predecessor(old,self.authority(old),'preflight')
        with self.assertRaises(ValueError):self.new.check_phase_scope(bad)

    def test_wrong_original_manager_terminal_and_generation_rejected(self):
        plan=self.plan(self.new);q=dict(phase='verify-candidate',plan=plan,
            previous=dict(exit_code=0,managerJob=dict(unit=self.new.UNIT,result='done',jobId=7),
                          invocationId='3'*32))
        frame=dict(op='authorize',checkpoint='phase-entry')
        self.new.check_phase_scope(dict(phase_request=q,request=frame))
        for badjob in (dict(unit='other.service',result='done',jobId=7),
                       dict(unit=self.new.UNIT,result='failed',jobId=7),
                       dict(unit=self.new.UNIT,result='done',jobId=True)):
            bad=deepcopy(q);bad['previous']['managerJob']=badjob
            with self.assertRaises(ValueError):self.new.check_phase_scope(dict(phase_request=bad,request=frame))
        bad=deepcopy(q);bad['previous']['invocationId']='not-an-invocation'
        with self.assertRaises(ValueError):self.new.check_phase_scope(dict(phase_request=bad,request=frame))

    def test_exact_current_resource_guard_and_mixed_frontend_pins(self):
        value=self.resources('publish','stage-preimage')
        self.new.check_resource_guard(value,'publish','stage-preimage')
        for field,badvalue in (('memoryAvailableBefore',1),('memoryAvailableAfter',True),
                               ('additionalBytes',0),('additionalInodes',0)):
            bad=deepcopy(value);bad[field]=badvalue
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.new.check_resource_guard(bad,'publish','stage-preimage')
        bad=deepcopy(value);next(iter(bad['filesystems'].values()))['freeInodes']=4096
        with self.assertRaises(ValueError):self.new.check_resource_guard(bad,'publish','stage-preimage')
        proof=dict(index_sha256=self.new.INDEX,bundle_path=self.new.BUNDLES[True][0],
                   bundle_sha256=self.new.BUNDLES[True][1],main_path=self.new.MAIN[True][0],
                   main_sha256=self.new.MAIN[True][1])
        self.assertTrue(self.new.served_matches(proof,True,self.new.INDEX))
        proof['main_path'],proof['main_sha256']=self.old.MAIN[True]
        with self.assertRaises(ValueError):self.new.served_matches(proof,True,self.new.INDEX)

    async def test_new_parent_final_checks_hold_drift_revocation_low_headroom_and_expiry(self):
        scenarios=('unchanged','generation','selector','low-headroom','wrong-heavy','missing-grant','local-drift','expired')
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                executor=self.executor('parent-'+scenario);plan=self.plan(self.new);auth=self.authority(plan)
                order=[];held={};clock=[int(time.time())]
                async def exchange(q,request):
                    order.append((q['phase'],request['op'],request['checkpoint']))
                    self.new.check_phase_scope(dict(phase_request=q,request=request))
                    if request['op']=='authorize':
                        proof=dict(authorized=True,phase=q['phase'],checkpoint=request['checkpoint'],
                                   challenge_sha256=self.changes.digest(request),expires_at=clock[0]+5)
                        if q['phase']=='activation-restart' and scenario=='missing-grant':proof['authorized']=False
                        return proof
                    if request['checkpoint']=='final-restart-resources':
                        proof=self.final_observation(q,request,held['heavy'])
                        if scenario=='generation':
                            proof['pid']=124;proof['restart_sample']['pid']=124
                        if scenario=='selector':
                            proof['restart_sample']['fragments']['/etc/systemd/system/intermarket-sbodesk.service']['sha256']='0'*64
                        if scenario=='low-headroom':proof['resource_guard']['memoryAvailableAfter']=1
                        if scenario=='wrong-heavy':proof['prior_observation_sha256']='0'*64
                        return proof
                    proof=self.observation(q,request)
                    if request['checkpoint']=='restart-preimage':held['heavy']=deepcopy(proof)
                    return proof
                async def driver(q,checkpoint):
                    request=dict(op='observe',phase=q['phase'],checkpoint='served-identity',
                                 nonce='f'*64,request_sha256=self.changes.digest(q))
                    await checkpoint(q,request)
                    result=self.predecessor(plan,auth,q['phase'])
                    if q['phase']=='verify-candidate':result['invocation_id']='3'*32
                    return result
                async def sample(expected):
                    order.append(('local','sample','current'))
                    if scenario=='local-drift':raise ValueError('foreign generation appeared after final host read')
                    if scenario=='expired':clock[0]+=6
                    return expected
                manager=AsyncMock(return_value=dict(exit_code=0,
                    managerJob=dict(unit=self.new.UNIT,result='done',jobId=11),invocationId='3'*32))
                with patch.object(time,'time',side_effect=lambda:clock[0]),patch.object(
                        self.new,'driver_phase',side_effect=driver),patch.object(
                        self.new,'require_current_restart_sample',side_effect=sample),patch.object(
                        self.changes,'verify_intermarket_worker'),patch.object(executor,'_manager_restart',manager):
                    if scenario=='unchanged':
                        result=await executor._run_parent(plan,'action-'+scenario,auth,exchange)
                        self.assertEqual(result['outcome'],'deployed');manager.assert_awaited_once()
                        sequence=[entry[2] for entry in order if entry[0]=='activation-restart']
                        self.assertEqual(sequence,['restart-preimage','phase-entry','final-restart-resources'])
                        with executor._db(False) as db:
                            count=db.execute("SELECT count(*) FROM original_operation_event WHERE kind='host_activation-restart_restart-preimage'").fetchone()[0]
                        self.assertEqual(count,1)
                    else:
                        with self.assertRaises(ValueError):
                            await executor._run_parent(plan,'action-'+scenario,auth,exchange)
                        manager.assert_not_awaited()

    def test_fixed_sampler_rejects_manager_jobs_and_completed_generation_changes(self):
        fields={name:'' for name in self.guard.PROPERTIES}
        fields.update(Id=self.new.UNIT,LoadState='loaded',ActiveState='active',SubState='running',
            MainPID='123',ControlPID='0',InvocationID='2'*32,Job='',NeedDaemonReload='no',
            FragmentPath=str(self.guard.UNIT_FILE),DropInPaths='')
        raw=lambda data:SimpleNamespace(returncode=0,stdout=''.join(k+'='+v+'\n' for k,v in data.items()).encode())
        with patch.object(self.guard.subprocess,'run',return_value=raw(fields)):
            self.assertEqual(self.guard.manager_state()[0]['MainPID'],'123')
        for change in ({'Job':'52'}, {'ControlPID':'600'}, {'NeedDaemonReload':'yes'}):
            with patch.object(self.guard.subprocess,'run',return_value=raw(dict(fields,**change))):
                with self.assertRaises(ValueError):self.guard.manager_state()
        directory=dict(st_dev=1)
        sample=dict(identity={},sha256='f'*64)
        with patch.object(self.guard,'manager_state',side_effect=[(fields,[]),(dict(fields,InvocationID='3'*32),[])]),\
             patch.object(self.guard,'start_ticks',return_value=321),\
             patch.object(self.guard,'dropin_inventory',return_value=(directory,[])),\
             patch.object(self.guard,'fragment',return_value=sample):
            with self.assertRaisesRegex(ValueError,'generation or selector changed'):
                self.guard.sample_restart_precondition()

    def test_fixed_sampler_detects_unloaded_or_replaced_selector_inventory(self):
        fields={name:'' for name in self.guard.PROPERTIES}
        fields.update(MainPID='123',InvocationID='2'*32)
        with patch.object(self.guard,'manager_state',return_value=(fields,[])),\
             patch.object(self.guard,'start_ticks',return_value=321),\
             patch.object(self.guard,'dropin_inventory',return_value=({},['95-foreign.conf'])):
            with self.assertRaisesRegex(ValueError,'inventory differ'):self.guard.sample_restart_precondition()
        before={'st_ino':1};after={'st_ino':2}
        with patch.object(self.guard,'manager_state',return_value=(fields,[])),\
             patch.object(self.guard,'start_ticks',return_value=321),\
             patch.object(self.guard,'dropin_inventory',side_effect=[(before,[]),(after,[])]),\
             patch.object(self.guard,'fragment',return_value={}):
            with self.assertRaisesRegex(ValueError,'generation or selector changed'):
                self.guard.sample_restart_precondition()

    async def test_compact_final_guard_uses_same_original_worker_heavy_proof(self):
        worker=self.load('agentic_it_worker')
        for tampered in (False,True):
            with self.subTest(tampered=tampered):
                plan=self.plan(self.new);auth=self.authority(plan)
                action='relay-' + str(tampered);auth['action_id']=action
                q=dict(phase='activation-restart',original=auth,plan=plan,
                       previous=self.predecessor(plan,auth,'publish'))
                frames=[dict(op='observe',phase=q['phase'],checkpoint=checkpoint,
                             nonce=str(i)*64,request_sha256=self.changes.digest(q))
                        for i,checkpoint in enumerate(('restart-preimage','final-restart-resources'),1)]
                challenges=[dict(phase_request=q,request=frame,expires_at=int(time.time())+20) for frame in frames]
                heavy=self.observation(q,frames[0]);final=self.final_observation(q,frames[1],heavy)
                self.assertNotIn('host_preimage_sha256',final)
                if tampered:final['prior_observation_sha256']='0'*64
                journal=worker.OriginalWorkerJournal(Path(self.temp.name)/('relay-journal-'+str(tampered)),
                                                     owner_uid=os.geteuid())
                executor=self.executor('relay-state-'+str(tampered))
                launcher=worker.SystemdOriginalLauncher(journal,executor,profile_sha256='a'*64,
                    worker_argv=('/usr/bin/python3','/fixed/entry.py','worker','/fixed/profile.json'),
                    recheck_plan=lambda value:value,writable_paths=(str(Path(self.temp.name)),))
                payload={k:auth[k] for k in ('action_id','invocation_id','run_id')};payload['plan']=plan
                journal.phase_event(action,'original-host-preimage',heavy['host_preimage_sha256'])
                for challenge in challenges:
                    frame=challenge['request']
                    journal.phase_event(action,'ask-'+frame['phase']+'-'+frame['op']+'-'+frame['checkpoint'],challenge)
                async def exchange(challenge):
                    frame=challenge['request']
                    return dict(authorized=True,phase=frame['phase'],checkpoint=frame['checkpoint'],
                                challenge_sha256=self.changes.digest(frame),expires_at=int(time.time())+5)
                with patch.object(launcher,'launch',AsyncMock(return_value={'state':'uncertain'})),\
                     patch.object(launcher,'observe',AsyncMock(side_effect=[{'state':'uncertain'},{'state':'completed'}])),\
                     patch.object(executor,'check_release_reservation'),\
                     patch.object(self.new,'host_observation',AsyncMock(side_effect=[heavy,final])):
                    if tampered:
                        with self.assertRaisesRegex(ValueError,'original heavy'):
                            await launcher.release_session(payload,auth,exchange)
                    else:
                        result=await launcher.release_session(payload,auth,exchange)
                        self.assertEqual(result['state'],'completed')
                with journal.db() as db:
                    original=db.execute('SELECT payload_json FROM worker_launch_event WHERE event_id=?',
                        (self.changes.digest([action,'original-restart-preimage-activation-restart']),)).fetchone()
                    reply=db.execute('SELECT payload_json FROM worker_launch_event WHERE event_id=?',
                        (self.changes.digest([action,'reply-'+self.changes.digest(challenges[1])]),)).fetchone()
                self.assertEqual(json.loads(original[0]),heavy)
                self.assertEqual(reply is not None,not tampered)

    def test_concrete_broker_inventory_rejects_omissions_before_import_and_loads_in_isolation(self):
        import importlib.util
        import subprocess
        import sys
        root=ROOT.parents[2]
        entry=root/'deployment/linux/abit-ai/agentic-it-broker/entry.py'
        spec=importlib.util.spec_from_file_location('fixed_broker_entry_fixture',entry)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        docs=root/'docs/prompt03-pr241-release-parent-20261123'
        mapping=json.loads((docs/'broker-source-map.json').read_text())
        manifest=json.loads((docs/'broker-code-manifest.json').read_text())
        self.assertEqual(set(mapping),module.FOLLOW_ON_SOURCE_FILES)
        profile={'change_resources':{'deploy_release':{self.new.RESOURCE:{}}}}
        module.validate_code_inventory(manifest,profile)
        for name in ('agentic_intermarket_release_registry','agentic_intermarket_pr241_release',
                     'agentic_intermarket_restart_guard','agentic_it_worker','agentic_it_physical'):
            bad=dict(manifest);del bad['lib/open_webui/utils/'+name+'.py']
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'incomplete'):
                module.validate_code_inventory(bad,profile)
        module.validate_code_inventory({'entry.py':'a'*64},
            {'change_resources':{'deploy_release':{self.old.RESOURCE:{}}}})
        stage=Path(self.temp.name)/'inert-source-bundle'
        for relative,source in mapping.items():
            raw=(root/source).read_bytes() if source else b''
            self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest[relative])
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        command=[sys.executable,'-I','-S','-c',
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "from open_webui.utils import agentic_it_broker_resources,agentic_it_dependency_readback;"
            "from open_webui.utils.agentic_intermarket_release_registry import release_for;"
            "assert release_for('intermarket-pr241-auth-guard').SOURCE=='922218853c587d2d1c5c7ff41332078b67c7455c';"
            "print('ISOLATED_STDLIB_BROKER_IMPORT_PASS')",str(stage/'lib')]
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'ISOLATED_STDLIB_BROKER_IMPORT_PASS')

    async def test_complete_attributed_rollback_and_current_rollback_guard_denial(self):
        for scenario in ('rollback-completes','rollback-generation','rollback-low-headroom'):
            with self.subTest(scenario=scenario):
                executor=self.executor(scenario);plan=self.plan(self.new);auth=self.authority(plan)
                heavy={};calls=[];order=[]
                async def exchange(q,request):
                    self.new.check_phase_scope(dict(phase_request=q,request=request))
                    order.append((q['phase'],request['op'],request['checkpoint']))
                    if request['op']=='authorize':
                        return dict(authorized=True,phase=q['phase'],checkpoint=request['checkpoint'],
                            challenge_sha256=self.changes.digest(request),expires_at=int(time.time())+5)
                    if request['checkpoint']=='final-restart-resources':
                        result=self.final_observation(q,request,heavy[q['phase']])
                        if q['phase']=='rollback-restart':
                            if scenario=='rollback-generation':
                                result['invocation_id']='9'*32
                                result['restart_sample']['invocation_id']='9'*32
                            if scenario=='rollback-low-headroom':
                                result['resource_guard']['filesystems']['/srv/abit-dev-linux/intermarket-dev-deployment/storage']['freeBytes']=1
                        return result
                    result=self.observation(q,request)
                    if q['phase'] in ('verify-candidate','restore','rollback-restart'):
                        result['index_sha256']='0'*64  # attributable served-byte rejection
                    if request['checkpoint']=='restart-preimage':
                        heavy[q['phase']]=deepcopy(result)
                    return result
                async def driver(q,checkpoint):
                    for index,(op,name) in enumerate(self.new.CHECKPOINTS[q['phase']],1):
                        request=dict(op=op,phase=q['phase'],checkpoint=name,
                            request_sha256=self.changes.digest(q),nonce=f'{index:064x}')
                        response=await checkpoint(q,request)
                        if op=='authorize':self.new.check_decision(response,request)
                    result=self.predecessor(plan,auth,q['phase'])
                    if q['phase'].startswith('verify-'):
                        result['invocation_id']=q['previous']['invocationId']
                    if q['phase']=='verify-candidate':result['verdict']='rejected'
                    return result
                async def manager(action,command,timeout,prefix=''):
                    calls.append(prefix)
                    return dict(exit_code=0,managerJob=dict(unit=self.new.UNIT,result='done',
                        jobId=11 if prefix=='activation_' else 12,
                        bootId='00000000-0000-0000-0000-000000000001',manager=':1.200'),
                        invocationId='3'*32 if prefix=='activation_' else '4'*32)
                with patch.object(self.new,'driver_phase',side_effect=driver),\
                     patch.object(self.new,'require_current_restart_sample',AsyncMock(side_effect=lambda expected:expected)),\
                     patch.object(self.changes,'verify_intermarket_worker'),\
                     patch.object(executor,'_manager_restart',side_effect=manager):
                    if scenario=='rollback-completes':
                        result=await executor._run_parent(plan,'action-'+scenario,auth,exchange)
                        self.assertEqual(result['outcome'],'restored')
                        self.assertEqual(calls,['activation_','rollback_'])
                        self.assertEqual(result['verification']['phase'],'verify-prior')
                        self.assertEqual(result['verification']['invocation_id'],'4'*32)
                        for phase in ('activation-restart','rollback-restart'):
                            self.assertEqual([x[2] for x in order if x[0]==phase],
                                ['restart-preimage','phase-entry','final-restart-resources'])
                    else:
                        with self.assertRaises(ValueError):
                            await executor._run_parent(plan,'action-'+scenario,auth,exchange)
                        self.assertEqual(calls,['activation_'])

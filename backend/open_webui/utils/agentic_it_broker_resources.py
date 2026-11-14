"""Construct only the reviewed, fixed physical resource implementations.

The installed Root-owned profile is operator policy, never model input. This
registry has no generic command, URL fetch, host package or block-device adapter.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import stat
import hashlib
import hmac
import time

from open_webui.utils.agentic_it_changes import ChangeReaders, LinuxReadOnlyReaders, prepare_change, plan_authority_digest, OPERATIONS
from open_webui.utils.agentic_it_changes import canonical,digest
from open_webui.utils.agentic_it_physical import DurablePhysicalExecutor
from open_webui.utils.agentic_it_host_resources import OwnedContainerPackages, PinnedService
from open_webui.utils.agentic_it_ext4_image import Ext4ImageConnector
from open_webui.utils.agentic_it_recovery_proofs import RecoveryProofStore
from open_webui.utils.agentic_it_signature import DebianArchiveVerifier, protected_bytes
from open_webui.utils.agentic_it_worker import OriginalWorkerJournal, SystemdOriginalLauncher


def require(value,message):
    if not value:raise ValueError(message)


class RegisteredBrokerResources:
    def __init__(self,profile,*,original_run,owner_uid=0):
        require(isinstance(profile,dict) and profile.get('registry')=='fixed-owned-physical-resources-v1'
                and profile.get('host_id')==socket.gethostname() and profile.get('expected_uid')==owner_uid==os.geteuid(),
                'Exact reviewed host operator registry required')
        require(profile.get('environment_class') in {'development','test'} and profile.get('production_enabled') is False,
                'This registry is limited to explicitly nonproduction owned resources')
        require(set(profile.get('operations',[]))<=OPERATIONS and profile.get('operations'), 'Fixed physical operations required')
        self.profile=deepcopy(profile)
        self.proofs=RecoveryProofStore(profile['recovery_proof_root'],profile['recovery_proof_key'])
        self.packages,self.service,self.capacity=OwnedContainerPackages(),PinnedService(),Ext4ImageConnector()
        self.readers=ChangeReaders(safety=self.proofs.safety,package=self.packages.package,service=self.service.read,
            storage=self.capacity.read,artifact=LinuxReadOnlyReaders()._artifact,
            verifier=DebianArchiveVerifier(),capacity_connector=self.capacity)
        handlers=[]
        for operation,resources in profile.get('change_resources',{}).items():
            require(operation in profile['operations'] and isinstance(resources,dict) and len(resources)<=20,'Bounded registered resources required')
            for resource,record in resources.items():
                require(record.get('operation')==operation and record.get('resource_id')==resource
                        and record.get('enabled') is True and record.get('exclusive_to_project') is True,
                        'Exact enabled project-exclusive resource required')
                require(record.get('host_id')==profile['host_id'] and record.get('project_id')==profile.get('project_id')
                        and record.get('repository')==profile.get('repository') and record.get('environment')==profile.get('environment'),
                        'Registered resource scope differs')
                builder={'install_official_package':self.packages,'restart_service':self.service,'extend_capacity':self.capacity}[operation]
                handlers.append(builder.handler(resource,record,profile))
        self.executor=DurablePhysicalExecutor(profile['execution_state_root'],handlers,run_lookup=original_run)

    async def prepare(self,packet):
        require(isinstance(packet,dict) and packet.get('binding',{}).get('role')=='it_manager'
                and packet.get('project_id')==self.profile['project_id']
                and packet.get('repository')==self.profile['repository'] and packet.get('environment')==self.profile['environment'],
                'Current authoritative IT packet is outside this registry')
        template=packet.get('tool_action') or {}
        action={key:packet[key] for key in ('project_id','task_id','task_revision','repository','environment')}
        action.update(actor=packet['binding'],operation=template.get('operation'),risk=template.get('risk'),parameters=template.get('parameters'))
        scope={key:packet[key] for key in ('project_id','task_id','task_revision','repository','environment')}
        scope['binding']=packet['binding']
        return await prepare_change(action,scope,self.profile,readers=self.readers,executor=self.executor,
                                    grant_checker=self._consumption_is_separate)

    @staticmethod
    def _consumption_is_separate(*args):
        raise ValueError('Only the broker exact consumed-authority challenge can admit execution')

    async def recheck(self,plan):
        scope=plan['scope']
        packet={key:scope[key] for key in ('project_id','task_id','repository','environment')}
        packet.update(task_revision=plan['task_revision'],binding=scope['actor'],tool_action={
            'operation':plan['operation'],'risk':'privileged','parameters':{'resource_id':plan['resource_id'],'plan_digest':plan['plan_digest']}})
        # Risk is checked in native authority and original immutable action. The
        # physical plan binds its exact resource/safety regardless of risk label.
        return await self.prepare(packet)

    async def verify_original(self,payload):
        plan,receipt=await self.executor.original_plan(payload['action_id'],payload['invocation_id'],payload['plan_digest'])
        require(receipt['run_id']==payload['run_id'] and all(plan['scope'][key]==payload[key] for key in ('project_id','task_id')),
                'Independent QA requested another original physical scope')
        handler=self.executor.handlers[(plan['operation'],plan['resource_id'])]
        current=await handler.verify_postcondition(deepcopy(plan))
        require(isinstance(current,dict) and current.get('verified') is True,'Independent current host postcondition failed')
        stable=lambda value:{key:item for key,item in value.items() if key not in {'checked_at','free_bytes'}}
        require(stable(current.get('observed',{}))==stable(receipt['postcondition'].get('observed',{})),
                'Independent QA found a later host mutation after the original completion')
        result={'kind':'independent_qa_current_readback','verified':True,'executed':False,**payload,
            'host_receipt_sha256':digest(receipt),'original_plan_sha256':digest(plan),'checked_at':int(time.time()),
            'observed':current['observed']}
        result['receipt_mac']=hmac.new(self.executor._key,b'abit-it-qa-readback-v1\0'+canonical(result).encode(),hashlib.sha256).hexdigest()
        return result

    async def verify_dependency(self,payload):
        from open_webui.utils.agentic_it_dependency_readback import verify_dependency
        return await verify_dependency(self,payload)

    async def core_peer(self,pid,uid,gid):
        require(self.profile.get('core_unit')=='abit-ai.service' and uid==self.profile.get('core_uid')==999
                and gid==self.profile.get('core_gid')==988,
                'Exact existing Core machine identity required')
        properties=('MainPID','User','Group','InvocationID','NoNewPrivileges','ProtectSystem','ProtectHome','PrivateTmp')
        args=('/usr/bin/systemctl','show','--no-pager',*[value for key in properties for value in ('--property',key)],'--','abit-ai.service')
        code,text=await LinuxReadOnlyReaders()._command(args)
        values=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        require(code==0 and values.get('MainPID')==str(pid) and values.get('User')=='mcpdev' and values.get('Group')=='mcpdev'
                and values.get('NoNewPrivileges')=='yes' and values.get('ProtectSystem')=='strict'
                and values.get('ProtectHome')=='yes' and values.get('PrivateTmp')=='yes'
                and len(values.get('InvocationID',''))==32,'Current fixed Core unit/hardening identity differs')
        process=Path('/proc')/str(pid)
        status=dict(line.split(':',1) for line in (process/'status').read_text().splitlines() if ':' in line)
        ticks=(process/'stat').read_text().rsplit(') ',1)[1].split()[19]
        require(status.get('NoNewPrivs','').strip()=='1' and status.get('Uid','').split()[1]=='999'
                and ticks.isdigit(),'Kernel Core privilege/start identity differs')
        return {'pid':pid,'start_ticks':int(ticks),'uid':uid,'gid':gid,'unit':'abit-ai.service','unit_invocation_id':values['InvocationID']}


def protected_profile(profile_file,*,owner_uid=0):
    path=Path(profile_file)
    require(path.is_absolute() and path.resolve()==path,'Canonical private broker profile required')
    cursor=path.parent
    while cursor!=cursor.parent:
        info=cursor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==owner_uid and not info.st_mode&0o022,
                'Broker profile ancestor is not protected')
        cursor=cursor.parent
    raw,metadata=protected_bytes(str(path),[str(path.parent)],owner_uid,128000)
    require(stat.S_IMODE(path.stat().st_mode)==0o600,'Private broker profile required')
    profile=json.loads(raw)
    return profile,metadata['sha256']


def installed_resources(profile_file):
    """Root installation entry; no implicit profile or privileged registration."""
    profile,sha=protected_profile(profile_file)
    journal=OriginalWorkerJournal(profile['worker_state_root'])
    resources=RegisteredBrokerResources(profile,original_run=journal.original_run)
    # The fixed protected script is a minimal Root-owned module entry point.
    worker=Path(profile['worker_entrypoint'])
    require(worker.resolve()==worker and worker.stat().st_uid==0 and stat.S_ISREG(worker.stat().st_mode)
            and not worker.stat().st_mode&0o022,'Protected installed worker entrypoint required')
    launcher=SystemdOriginalLauncher(journal,resources.executor,profile_sha256=sha,
        worker_argv=('/usr/bin/python3',str(worker),'worker',str(Path(profile_file))),
        recheck_plan=resources.recheck,writable_paths=profile['worker_writable_paths'])
    return profile,resources,launcher

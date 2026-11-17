"""Structured IT execution through trusted adapters; never a broad Dot session."""

from __future__ import annotations
from open_webui.utils.agentic_bridge import same_business_context
import asyncio
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
import re
import socket
import stat
from urllib.parse import urlsplit
from pathlib import Path
import secrets
import time

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, digest, packet_digest
from open_webui.utils.agentic_tool_gateway import execute_action, ApprovalRequired, CapabilityUnavailable

RPC = '/api/method/abit_ai_assistant.agentic_approval_actions.'


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS governance_action_wait(
        invocation_id TEXT PRIMARY KEY,action_json TEXT NOT NULL,action_fingerprint TEXT NOT NULL,
        approval_id TEXT NOT NULL,phase TEXT NOT NULL,created_at INTEGER NOT NULL)''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(governance_action_wait)')}
    for name in ('request_json', 'checkpoint_json', 'checkpoint_phase', 'checkpoint_receipt_json'):
        if name not in columns:
            db.execute('ALTER TABLE governance_action_wait ADD COLUMN ' + name + ' TEXT')
    if 'renewals' not in columns:
        db.execute('ALTER TABLE governance_action_wait ADD COLUMN renewals INTEGER NOT NULL DEFAULT 0')


class ITAdapter:
    def __init__(self, app, project_gate, config, db_path=None, adapters=None, physical_runtime=None):
        self.app = app
        self.project_gate = project_gate
        self.config = config
        self.db_path = db_path
        configured_runtime = physical_runtime if physical_runtime is not None else getattr(getattr(app, 'state', None), 'agentic_it_physical_runtime', None)
        if configured_runtime is not None:
            from open_webui.utils.agentic_it_physical_runtime import PhysicalRuntime
            if not isinstance(configured_runtime, PhysicalRuntime):
                raise ValueError('Physical runtime must be an internal trusted server object')
        self.physical_runtime = configured_runtime
        from open_webui.utils.agentic_it_changes import change_adapters,LinuxReadOnlyReaders
        from open_webui.utils.agentic_tool_gateway import TrustedAdapter
        self.adapters=change_adapters(readers=LinuxReadOnlyReaders(),executor=None,grant_checker=None)
        if adapters is not None:
            if not isinstance(adapters,dict) or any(not isinstance(value,TrustedAdapter) for value in adapters.values()):
                raise ValueError('IT adapter overrides must be internal trusted server objects')
            self.adapters.update(adapters)
        self.action = None

    def bind_physical_runtime(self, bridge):
        if self.physical_runtime is None and self.config.get('agentic_it_physical_broker'):
            from open_webui.utils.agentic_it_broker import configured_runtime
            self.physical_runtime = configured_runtime(self.config, bridge=bridge,
                project_gate=self.project_gate, db_path=self.db_path)
        if self.physical_runtime is not None:
            self.adapters.update(self.physical_runtime.adapters(bridge=bridge, project_gate=self.project_gate,
                                                               config=self.config, db_path=self.db_path))

    async def preflight(self, row, packet):
        template = packet.get('tool_action')
        if not isinstance(template, dict):
            raise CapabilityUnavailable(
                'IT requires a structured ERP tool action; broad owner-relay tools are not an IT executor'
            )
        profile = ((self.config.get('agentic_tool_gateway') or {}).get('projects') or {}).get(row['erp_project'])
        if not isinstance(profile, dict) or template['operation'] not in profile.get('operations', []):
            raise CapabilityUnavailable('No authorized infrastructure adapter profile is configured for this project')
        if template['operation'] not in {'inspect', 'cleanup_disposable'} and template['operation'] not in (
            self.adapters or {}
        ):
            raise CapabilityUnavailable('No trusted adapter is implemented for this operation')

    async def prepare(self, row, packet, run_id):
        from open_webui.utils.agentic_executor import DotAdapter

        # Reuse binding/run validation only; submit never invokes Dot.
        return await DotAdapter(self.app).prepare(row, packet, run_id)

    async def submit(self, row, packet, run_id, automation, bridge, *, before_submit):
        await before_submit()
        self.bind_physical_runtime(bridge)
        action = make_action(row, packet)
        self.action = action
        from open_webui.utils.agentic_it_changes import OPERATIONS,PreparedChangeUnavailable
        from open_webui.utils.agentic_tool_gateway import _fresh,_profile
        original=_prepared_record(row,packet,self.config,self.db_path)
        if original is not None:return original
        if action['operation'] in OPERATIONS and not _has_host_intent(row['invocation_id'],self.db_path):
            selected=self.adapters[action['operation']];profile=_profile(self.config,row['erp_project'])
            _,scope=await _fresh(row['invocation_id'],action,self.project_gate,self.config,bridge,profile,selected,self.db_path)
            try:
                await selected.preflight(action,scope,profile)
            except PreparedChangeUnavailable as error:
                # A typed read-only preparation is conclusive only BEFORE any
                # gateway intent; never relabel an unknown host outcome.
                if _has_host_intent(row['invocation_id'],self.db_path):raise
                return _save_prepared(row,packet,action,error,self.config,self.db_path)
        receipt = await execute_action(
            row['invocation_id'], action,project_gate=self.project_gate,config=self.config,
            bridge=bridge,adapters=self.adapters,db_path=self.db_path,
        )
        return make_record(row, packet, receipt, self.config)

    async def record(self, automation, run_id, record):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.automations import AutomationRun

        async with get_async_db_context() as db:
            original = await db.get(AutomationRun, run_id)
            if (
                original
                and original.automation_id == automation.id
                and original.status == 'success'
                and original.chat_id is None
            ):
                return
            if not original or original.automation_id != automation.id or original.status != 'running':
                raise ValueError('Original IT run changed before trusted adapter receipt')
            original.status = 'success'
            original.chat_id = None
            original.error = None
            if hasattr(original, 'lease_until'):
                original.lease_until = None
            await db.commit()


def make_action(row, packet):
    template = packet['tool_action']
    return {
        'schema_version': 1,
        'action_id': 'agov-action-' + digest([row['invocation_id'], template])[:48],
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'actor': packet['binding'],
        'repository': packet['repository'],
        'environment': packet['environment'],
        'operation': template['operation'],
        'risk': template['risk'],
        'parameters': template['parameters'],
        'requested_at': int(time.time()),
    }


def make_record(row, packet, receipt, config):
    if not receipt or receipt.get('state') != 'completed' or receipt.get('invocation_id') != row['invocation_id']:
        raise RuntimeError('Original infrastructure operation remains uncertain; no repeat')
    from open_webui.utils.agentic_artifacts import artifact_directory, ensure_directory

    root = artifact_directory(row, config)
    ensure_directory(root)
    content = canonical(receipt).encode()
    path = root / 'trusted-action-receipt.json'
    import os, hashlib

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    except FileExistsError:
        from open_webui.utils.agentic_artifacts import local_bytes

        _, prior = local_bytes(root, 'artifact://' + path.name)
        if prior != content:
            raise ValueError('Original infrastructure receipt bytes changed')
    else:
        with os.fdopen(fd, 'wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
            os.fchmod(file.fileno(), 0o640)
    result = {
        'schema_version': 1,
        'invocation_id': row['invocation_id'],
        'actor': packet['binding'],
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'work_packet_digest': packet_digest(packet),
        'outcome': 'ready_for_review',
        'summary': 'Trusted infrastructure adapter completed the exact governed action. Independent QA must verify the Task acceptance criteria.',
        'test_results': [
            {
                'test_id': test['id'],
                'status': 'skipped',
                'observed': 'Adapter receipt recorded; independent acceptance testing remains required.',
                'evidence_ids': ['action-receipt'],
            }
            for test in packet['acceptance_tests']
        ],
        'evidence': [
            {
                'id': 'action-receipt',
                'kind': 'test_log',
                'uri': 'artifact://' + path.name,
                'sha256': hashlib.sha256(content).hexdigest(),
                'requirement_ids': packet['requirement_ids'],
            }
        ],
        'implementation_invocation_id': None,
        'implementation_agent_id': None,
        'defects': [],
    }
    return {
        'response_text': 'AGOV RESULT: ' + canonical(result),
        'transport': 'trusted-tool-gateway',
        'model_requests': 0,
    }


_PLAN_KEYS={'schema_version','kind','operation','resource_id','scope','task_revision','host_id','prepared_at','execution_state','prerequisites','target','baseline','fixed_argv','safety','artifact','signature','policy_digest','plan_digest','state','executable'}
_PLAN_NESTED={
 'scope':{'project_id','task_id','actor','repository','environment'},
 'actor':{'project_id','role','agent_id','erp_user'},
 'target':{'package','version','architecture','artifact_sha256','official_source','unit','unit_sha256','healthy','active_state','volume_id','target_bytes','filesystem'},
 'official_source':{'origin','signer_fingerprint','catalog_sha256'},
 'baseline':{'package','installed','version','architecture','unit','unit_sha256','active_state','sub_state','main_pid','invocation_id','healthy','volume_id','filesystem_id','filesystem','total_bytes'},
 'safety':{'backup','restore','retention','capacity','impact','privilege'},
 'backup':{'verified','reference','sha256','expires_at'},'restore':{'verified','reference','sha256','expires_at'},
 'retention':{'verified','policy_sha256','expires_at'},'capacity':{'filesystem_id','free_bytes','total_bytes'},
 'impact':{'reviewed','exclusive_to_project','sha256'},'privilege':{'authorized','effective_uid'},
 'artifact':{'artifact_id','sha256','size','device','inode','mtime_ns','owner_uid'},
 'signature':{'official_source_id','signer_fingerprint','catalog_sha256','valid_until'},
}


def _has_host_intent(invocation,path):
    with ledger.connect(path) as db:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action'").fetchone() and db.execute('SELECT 1 FROM governance_action WHERE invocation_id=?',(invocation,)).fetchone())


def _prepared_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS governance_prepared_it(
        invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,action_id TEXT NOT NULL,
        packet_sha256 TEXT NOT NULL,filename TEXT NOT NULL,content_sha256 TEXT NOT NULL)""")


def _validate_prepared(plan,packet,action):
    """Reviewable metadata only; a server profile is never copied into evidence."""
    if not isinstance(plan,dict) or set(plan)-_PLAN_KEYS:raise ValueError('Unknown prepared-plan metadata')
    def bounded(value,key=''):
        if isinstance(value,dict):
            if key not in _PLAN_NESTED or set(value)-_PLAN_NESTED[key]:raise ValueError('Unknown prepared-plan nested metadata')
            for name,item in value.items():bounded(item,name)
        elif isinstance(value,list):
            if key=='prerequisites':
                if not 0<len(value)<=40:raise ValueError('Prepared prerequisites exceed bounds')
                for item in value:
                    if not isinstance(item,dict) or set(item)!={'code','description'} or not re.fullmatch('[A-Z][A-Z0-9_]{1,100}',str(item.get('code',''))):raise ValueError('Malformed prepared prerequisite')
                    bounded(item['description'],'description')
            elif key=='fixed_argv':
                if len(value)>8:raise ValueError('Prepared argv exceeds bounds')
                for item in value:bounded(item,key)
            else:raise ValueError('Unknown prepared-plan list')
        elif isinstance(value,str):
            if len(value)>6000 or re.search(r'(?i)(?:file|ssh|ftp|data)://',value):raise ValueError('Unsafe prepared-plan text')
            if '://' in value:
                parsed=urlsplit(value)
                if key not in {'repository','origin'} or parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Unsafe prepared-plan URL')
        elif value is not None and type(value) not in {int,bool}:raise ValueError('Prepared-plan metadata must be JSON scalar values')
    for key,value in plan.items():
        if key in _PLAN_NESTED and not isinstance(value,dict):raise ValueError('Prepared-plan structured metadata must be an object')
        if key in {'prerequisites','fixed_argv'} and not isinstance(value,list):raise ValueError('Prepared-plan structured metadata must be a list')
        bounded(value,key)
    if (type(plan.get('schema_version')) is not int or plan.get('schema_version')!=1 or plan.get('kind')!='it_change_plan' or plan.get('state')!='prepared_unavailable'
        or plan.get('execution_state')!='not_executed' or plan.get('executable') is not False
        or plan.get('task_revision')!=packet['task_revision'] or plan.get('operation')!=action['operation']
        or plan.get('resource_id')!=action['parameters'].get('resource_id')
        or plan.get('host_id')!=socket.gethostname()
        or plan.get('scope')!={key:action[key] for key in ('project_id','task_id','actor','repository','environment')}):
        raise ValueError('Prepared-unavailable plan does not bind its original Task action')
    from open_webui.utils.agentic_it_changes import plan_authority_digest
    if plan.get('plan_digest')!=plan_authority_digest(plan) or not re.fullmatch('[a-f0-9]{64}',str(plan.get('policy_digest',''))):raise ValueError('Prepared plan digest differs')
    if not plan.get('prerequisites') or len({item['code'] for item in plan['prerequisites']})!=len(plan['prerequisites']):raise ValueError('Unavailable preparation must identify unique concrete prerequisites')


def _prepared_result(row,packet,plan,filename,content_sha):
    groups={}
    for item in plan['prerequisites']:
        code=item['code']
        group=('scope' if code=='SERVER_RESOURCE_PROFILE_UNAVAILABLE' else 'execution' if code in {'TRUSTED_EXECUTOR_UNAVAILABLE','TRUSTED_CAPACITY_CONNECTOR_UNAVAILABLE'} else 'authorization' if code in {'CONSUMED_ERP_APPROVAL_READER_UNAVAILABLE','EXACT_PLAN_DIGEST_REQUIRED'} else 'safety' if any(word in code for word in ('PREFLIGHT','BACKUP','RESTORE','RETENTION','IMPACT','PRIVILEGE')) else 'readback')
        groups.setdefault(group,[]).append(item)
    defects=[{'id':'it-preparation-'+group,'requirement_ids':packet['requirement_ids'],'evidence_ids':['prepared-it-plan'],
        'reproduction':'Prepare '+plan['operation']+' resource '+plan['resource_id']+' using plan '+plan['plan_digest']+'. No host action was executed.',
        'observed':'\n'.join(item['code']+': '+item['description'] for item in items),
        'expected':'Resolve these exact prerequisite codes through reviewed source/configuration and fresh evidence before a separately authorized IT change: '+', '.join(item['code'] for item in items)} for group,items in sorted(groups.items())]
    if len(defects)>5 or any(len(item['observed'])>6000 for item in defects):raise ValueError('Prepared prerequisites require explicit bounded decomposition')
    result={'schema_version':1,'invocation_id':row['invocation_id'],'actor':packet['binding'],'project_id':row['erp_project'],'task_id':row['erp_task'],
        'task_revision':packet['task_revision'],'work_packet_digest':packet_digest(packet),'outcome':'blocked',
        'summary':'IT change was prepared but not executed; '+str(len(plan['prerequisites']))+' explicit prerequisites remain. Review the exact checkpoint and repair children before authorizing a new action.',
        'test_results':[{'test_id':test['id'],'status':'skipped','observed':'No infrastructure action was executed; prepared-unavailable checkpoint only.','evidence_ids':['prepared-it-plan']} for test in packet['acceptance_tests']],
        'evidence':[{'id':'prepared-it-plan','kind':'checkpoint','uri':'artifact://'+filename,'sha256':content_sha,'requirement_ids':packet['requirement_ids']}],
        'implementation_invocation_id':None,'implementation_agent_id':None,'defects':defects}
    return {'response_text':'AGOV RESULT: '+canonical(result),'transport':'trusted-tool-gateway-preparation','model_requests':0}


def _prepared_record(row,packet,config,path):
    from open_webui.utils.agentic_artifacts import artifact_directory
    with ledger.connect(path) as db:
        _prepared_schema(db);entry=db.execute('SELECT * FROM governance_prepared_it WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
    if not entry:return None
    action=make_action(row,packet)
    if (entry['run_id']!=row['automation_run_id'] or entry['action_id']!=action['action_id'] or entry['packet_sha256']!=packet_digest(packet)
        or _has_host_intent(row['invocation_id'],path)):raise ValueError('Prepared checkpoint has a different original run/action or a host intent')
    from open_webui.utils.agentic_tool_gateway import _open_dir
    root=artifact_directory(row,config);filename=entry['filename']
    expected_name='prepared-unavailable-'+digest([row['invocation_id'],row['automation_run_id']])[:40]+'.json'
    if filename!=expected_name:raise ValueError('Prepared checkpoint filename differs from its original run')
    directory=_open_dir(root)
    try:
        fd=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as handle:
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:raise ValueError('Prepared checkpoint is not a server-owned protected file')
            content=handle.read(48001)
    finally:os.close(directory)
    if hashlib.sha256(content).hexdigest()!=entry['content_sha256'] or len(content)>48000:raise ValueError('Original prepared checkpoint bytes changed')
    plan=json.loads(content);_validate_prepared(plan,packet,action)
    if canonical(plan).encode()!=content:raise ValueError('Original prepared checkpoint is not canonical immutable JSON')
    return _prepared_result(row,packet,plan,filename,entry['content_sha256'])


def _save_prepared(row,packet,action,error,config,path):
    from open_webui.utils.agentic_artifacts import artifact_directory,ensure_directory,local_bytes
    _validate_prepared(error.plan,packet,action)
    content=error.artifact.get('content');sha=hashlib.sha256(content).hexdigest() if isinstance(content,bytes) else ''
    if (content!=canonical(error.plan).encode() or len(content)>48000 or error.artifact.get('sha256')!=sha
        or error.artifact.get('kind')!='checkpoint' or error.artifact.get('executed') is not False):raise ValueError('Prepared plan artifact differs from its exact bytes')
    filename='prepared-unavailable-'+digest([row['invocation_id'],row['automation_run_id']])[:40]+'.json'
    expected=(row['automation_run_id'],action['action_id'],packet_digest(packet),filename,sha)
    with ledger._transaction(path) as db:
        _prepared_schema(db)
        current=ledger._owned(db,row['invocation_id'],row['automation_run_id'],row['lease_token'],{'running'})
        if not current or db.execute('SELECT 1 FROM governance_action WHERE invocation_id=?',(row['invocation_id'],)).fetchone():raise ValueError('Original IT preparation no longer owns a run without host intent')
        previous=db.execute('SELECT run_id,action_id,packet_sha256,filename,content_sha256 FROM governance_prepared_it WHERE invocation_id=?',(row['invocation_id'],)).fetchone()
        if previous and tuple(previous)!=expected:raise ValueError('Original prepared checkpoint is immutable')
        db.execute('INSERT OR IGNORE INTO governance_prepared_it VALUES(?,?,?,?,?,?)',(row['invocation_id'],*expected))
    root=artifact_directory(row,config);ensure_directory(root)
    from open_webui.utils.agentic_tool_gateway import _open_dir
    directory=_open_dir(root)
    try:
        try:fd=os.open(filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=directory)
        except FileExistsError:
            _,prior=local_bytes(root,'artifact://'+filename)
            if prior!=content:raise ValueError('Original prepared checkpoint bytes changed')
        else:
            with os.fdopen(fd,'wb') as handle:handle.write(content);handle.flush();os.fsync(handle.fileno());os.fchmod(handle.fileno(),0o640)
            os.fsync(directory)
    finally:os.close(directory)
    return _prepared_record(row,packet,config,path)


async def suspend(invocation, packet, adapter, bridge, receipt, error, db_path=None):
    row = ledger.find(invocation, db_path, include_private=True)
    action = adapter.action
    if not row or not action:
        raise ValueError('Approval wait has no original bound action')
    approval_id = 'agov-approval-' + digest([invocation, error.action_fingerprint])[:48]
    with ledger._transaction(db_path) as db:
        _table(db)
        current = ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'running'})
        if not current:
            raise ValueError('Original action lease changed before approval wait')
        prior = db.execute(
            'SELECT action_fingerprint FROM governance_action_wait WHERE invocation_id=?', (invocation,)
        ).fetchone()
        if prior and prior[0] != error.action_fingerprint:
            raise ValueError('Original pending action changed')
        db.execute(
            "INSERT OR IGNORE INTO governance_action_wait(invocation_id,action_json,action_fingerprint,approval_id,phase,created_at) VALUES(?,?,?,?,'waiting',?)",
            (invocation, canonical(action), error.action_fingerprint, approval_id, int(time.time())),
        )
        db.execute(
            "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL,updated_at=? WHERE invocation_id=?",
            (int(time.time()), invocation),
        )
        ledger._event(
            db,
            row['erp_project'],
            invocation,
            'action_awaiting_approval',
            'trusted-tool-gateway',
            {
                'action_id': action['action_id'],
                'approval_id': approval_id,
                'action_fingerprint': error.action_fingerprint,
            },
        )
    args = {
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'run_id': row['automation_run_id'],
        'invocation_id': invocation,
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'action': action,
        'action_fingerprint': error.action_fingerprint,
        'approval_id': approval_id,
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    event = {
        'schema_version': 1,
        'event_id': 'agov-action-wait-' + digest(invocation)[:48],
        'type': 'run.checkpoint',
        'project_id': row['erp_project'],
        'task_id': row['erp_task'],
        'task_revision': packet['task_revision'],
        'run_id': row['automation_run_id'],
        'invocation_id': invocation,
        'role': row['target_role'],
        'agent_id': row['agent_id'],
        'summary': 'Awaiting current human ERP approval for exact action '
        + action['action_id']
        + '. No infrastructure operation was performed.',
        'expected_modified': receipt['modified'],
        'expected_project_revision': packet['project_revision'],
        'work_packet_digest': packet_digest(packet),
        'artifacts': [],
    }
    with ledger._transaction(db_path) as db:
        _table(db)
        db.execute(
            "UPDATE governance_action_wait SET request_json=coalesce(request_json,?),checkpoint_json=coalesce(checkpoint_json,?),checkpoint_phase=coalesce(checkpoint_phase,'pending') WHERE invocation_id=?",
            (canonical(args), canonical(event), invocation),
        )
    await reconcile_wait(invocation, bridge, db_path)
    return None  # A checkpoint is not a completed result and cannot trigger QA.


async def reconcile_wait(invocation, bridge, db_path=None):
    """Replay only the idempotent approval draft; observe unknown checkpoint acks."""
    with ledger.connect(db_path) as db:
        _table(db)
        pending = db.execute('SELECT * FROM governance_action_wait WHERE invocation_id=?', (invocation,)).fetchone()
    if not pending or not pending['request_json'] or not pending['checkpoint_json']:
        return False
    args = json.loads(pending['request_json'])
    expiry = datetime.fromisoformat(args['expires_at'].replace('Z', '+00:00'))
    if expiry <= datetime.now(timezone.utc):
        from open_webui.utils.agentic_tool_gateway import get_action

        if get_action(invocation, args['action']['action_id'], db_path) is not None:
            raise ValueError('An existing host intent cannot receive a renewed approval')
        if pending['renewals'] >= 3:
            raise ValueError('Approval renewal budget exhausted; human intervention required')
        args['approval_id'] = (
            'agov-approval-'
            + digest([invocation, pending['action_fingerprint'], 'renewal', pending['renewals'] + 1])[:48]
        )
        args['expires_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with ledger._transaction(db_path) as db:
            updated = db.execute(
                'UPDATE governance_action_wait SET request_json=?,approval_id=?,renewals=renewals+1 WHERE invocation_id=? AND renewals=?',
                (canonical(args), args['approval_id'], invocation, pending['renewals']),
            )
            if updated.rowcount != 1:
                return False
            row = db.execute(
                'SELECT erp_project FROM governance_invocation WHERE invocation_id=?', (invocation,)
            ).fetchone()
            ledger._event(
                db,
                row[0],
                invocation,
                'expired_approval_draft_renewed',
                'trusted-tool-gateway',
                {'approval_id': args['approval_id']},
            )
    await bridge.erp.request(
        'POST', RPC + 'request_action_approval', json=args, headers={'X-ABIT-Agentic-Bridge': bridge.secret}
    )
    if pending['checkpoint_phase'] == 'published':
        return True
    event = json.loads(pending['checkpoint_json'])
    if pending['checkpoint_phase'] == 'pending':
        with ledger._transaction(db_path) as db:
            if (
                db.execute(
                    "UPDATE governance_action_wait SET checkpoint_phase='uncertain' WHERE invocation_id=? AND checkpoint_phase='pending'",
                    (invocation,),
                ).rowcount
                != 1
            ):
                return False
        receipt = await bridge.publish(event)
    else:
        response = await bridge.erp.request(
            'GET',
            '/api/method/abit_ai_assistant.agentic_control.get_result_receipt',
            params={'event_id': event['event_id'], 'project_id': event['project_id'], 'task_id': event['task_id']},
            headers={'X-ABIT-Agentic-Bridge': bridge.secret},
        )
        value = response.get('message') or {}
        if value.get('exists') is not True:
            return False
        receipt = {k: v for k, v in value.get('receipt', value).items() if k not in {'exists', 'content_sha256'}}
    if (
        receipt.get('event_id') != event['event_id']
        or receipt.get('run_id') != event['run_id']
        or receipt.get('task_id') != event['task_id']
        or not receipt.get('modified')
    ):
        raise ValueError('Approval checkpoint receipt does not match the original run')
    with ledger._transaction(db_path) as db:
        db.execute(
            "UPDATE governance_action_wait SET checkpoint_phase='published',checkpoint_receipt_json=? WHERE invocation_id=?",
            (canonical(receipt), invocation),
        )
    return True


async def resume(invocation, *, config, project_gate, bridge, app, db_path=None, adapters=None):
    from open_webui.utils.agentic_executor import complete_original

    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row['state'] != 'awaiting_approval':
        return None
    with ledger.connect(db_path) as db:
        _table(db)
        pending = db.execute('SELECT * FROM governance_action_wait WHERE invocation_id=?', (invocation,)).fetchone()
    if not pending:
        return None
    if not await reconcile_wait(invocation, bridge, db_path):
        return None
    action = json.loads(pending['action_json'])
    action['requested_at'] = int(time.time())
    packet = json.loads(row['work_packet'])
    fresh = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    managed = {'source_modified', 'packet_id', 'content_sha256', 'implementation_user', 'implementation_run_id'}
    if not same_business_context(packet, fresh, managed):
        raise ValueError('Original infrastructure context changed while awaiting approval')
    await project_gate(row['erp_project'], config)
    with ledger._transaction(db_path) as db:
        current = ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'awaiting_approval'})
        control = db.execute(
            'SELECT state FROM governance_project_control WHERE erp_project=?', (row['erp_project'],)
        ).fetchone()
        if not current or not control or control[0] != 'enabled':
            return None
        ledger._task_gate(db, row['erp_project'], row['erp_task'])
        db.execute(
            "UPDATE governance_invocation SET state='running',lease_expires_at=?,updated_at=? WHERE invocation_id=?",
            (int(time.time()) + 120, int(time.time()), invocation),
        )
        ledger._event(
            db,
            row['erp_project'],
            invocation,
            'approved_action_resumed',
            'trusted-tool-gateway',
            {'action_id': action['action_id']},
        )
    beat = None
    try:

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                if not ledger.heartbeat(invocation, row['automation_run_id'], row['lease_token'], db_path):
                    return

        beat = asyncio.create_task(heartbeat())
        result = await execute_action(
            invocation,
            action,
            project_gate=project_gate,
            config=config,
            bridge=bridge,
            adapters=adapters,
            db_path=db_path,
        )
        record = make_record(row, packet, result, config)
        from open_webui.models.automations import Automations

        automation = await Automations.get_by_id(row['agent_id'])
        adapter = ITAdapter(app, project_gate, config, db_path, adapters)
        return await complete_original(
            row,
            packet,
            record,
            adapter,
            automation,
            bridge,
            {'modified': fresh['source_modified']},
            project_gate,
            config,
            db_path,
        )
    except ApprovalRequired:
        with ledger._transaction(db_path) as db:
            if ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'running'}):
                db.execute(
                    "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL WHERE invocation_id=?",
                    (invocation,),
                )
        return None
    except Exception as error:
        from open_webui.utils.agentic_tool_gateway import get_action

        if get_action(invocation, action['action_id'], db_path) is None:
            # No durable host intent exists: expiry/denial/preflight failure is
            # known before execution, so a fresh human decision may wake the
            # SAME original run and action. Unknown host intents stay fenced.
            with ledger._transaction(db_path) as db:
                if ledger._owned(db, invocation, row['automation_run_id'], row['lease_token'], {'running'}):
                    db.execute(
                        "UPDATE governance_invocation SET state='awaiting_approval',lease_expires_at=NULL,reason=? WHERE invocation_id=?",
                        ((type(error).__name__ + ': ' + str(error))[:500], invocation),
                    )
            return None
        ledger.finish_execution(
            invocation,
            row['automation_run_id'],
            row['lease_token'],
            'uncertain',
            reason='Original infrastructure action/result requires receipt reconciliation; do not rerun',
            db_path=db_path,
        )
        raise
    except BaseException:
        ledger.finish_execution(
            invocation,
            row['automation_run_id'],
            row['lease_token'],
            'uncertain',
            reason='Original infrastructure action/result requires receipt reconciliation; do not rerun',
            db_path=db_path,
        )
        raise
    finally:
        if beat:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)


async def recover_completed(
    invocation, *, config, project_gate, bridge, app=None, db_path=None, adapter=None, automation=None
):
    """Recover an already committed adapter receipt without calling execute_action."""
    from open_webui.utils.agentic_tool_gateway import get_action
    from open_webui.utils.agentic_executor import complete_original

    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row['target_role'] != 'it_manager' or row['state'] not in {'uncertain', 'cancel_requested'}:
        return None
    packet = json.loads(row['work_packet'])
    action = make_action(row, packet)
    result = get_action(invocation, action['action_id'], db_path)
    if result and result['state']!='completed':
        chosen_adapter = adapter or ITAdapter(app, project_gate, config, db_path)
        if isinstance(chosen_adapter, ITAdapter):chosen_adapter.bind_physical_runtime(bridge)
        runtime = getattr(chosen_adapter, 'physical_runtime', None)
        if runtime is None:return None
        result = await runtime.reconcile_original(invocation, action['action_id'], bridge=bridge, db_path=db_path)
        if not result or result['state']!='completed':return None
    if result is None:
        try:record=_prepared_record(row,packet,config,db_path)
        except (ValueError,OSError,TypeError,KeyError):return None
        if record is None:return None
    else:record=None
    fresh = await bridge.packet(row['erp_project'], row['erp_task'], row['target_role'], row['agent_id'])
    managed = {
        'source_modified',
        'packet_id',
        'content_sha256',
        'implementation_user',
        'implementation_run_id',
        'attachments',
    }
    if not same_business_context(packet, fresh, managed):
        raise ValueError('Infrastructure context changed before original receipt recovery')
    if row['state'] != 'cancel_requested':
        await project_gate(row['erp_project'], config)
    if result is not None:record=make_record(row,packet,result,config)
    if automation is None:
        from open_webui.models.automations import Automations

        automation = await Automations.get_by_id(row['agent_id'])
    return await complete_original(
        row,
        packet,
        record,
        adapter or ITAdapter(app, project_gate, config, db_path),
        automation,
        bridge,
        {'modified': fresh['source_modified']},
        project_gate,
        config,
        db_path,
    )

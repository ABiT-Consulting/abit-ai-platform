#!/usr/bin/env python3
"""Prepare or retain existing delivery evidence; never authorize or run work.

prepare writes a private proposal only. retain validates the entire proposal and
current immutable ledger evidence, then installs Root-owned objects create-only,
with the index last. It makes no ERP, network, service or governance writes.
"""
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
from types import ModuleType, SimpleNamespace
from uuid import NAMESPACE_URL, uuid5
import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
UTILS = ROOT / 'backend/open_webui/utils'
DATABASE = Path('/srv/abit-dev-linux/abit-ai/data/agentic-governance.sqlite')
HISTORY = Path('/var/lib/abit-ai-agentic-gateway/delivery-recovery-history')
OPERATOR = Path('/var/lib/abit-ai-agentic-gateway/delivery-recoveries')
ROLLOUTS = Path('/var/lib/abit-ai-erp-rollouts')
LIMIT = 262144


def need(value, message):
    if not value:
        raise ValueError(message)


def denied(*args, **kwargs):
    raise RuntimeError('Network and application connections are disabled')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path, limit=LIMIT):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'Evidence path must be absolute')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:-1]:
            nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = nxt
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                 'Evidence must be a bounded regular file without hard links')
            raw = stream.read(limit + 1)
    finally:
        os.close(directory)
    need(len(raw) <= limit, 'Evidence exceeds its bound')
    return raw, info


def selected(path, names, constants=(), namespace=None):
    tree = ast.parse(path.read_text())
    body = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            body.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id in constants for x in node.targets):
            body.append(node)
    need(found == set(names), 'Reviewed pure source function is missing')
    env = dict(namespace or {})
    env.update(json=json, hashlib=hashlib, re=re, secrets=secrets, deepcopy=deepcopy,
               datetime=datetime, uuid5=uuid5, NAMESPACE_URL=NAMESPACE_URL)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), 'exec'), env)
    return env


def load_policy():
    bridge = selected(UTILS/'agentic_bridge.py',
                      {'canonical','digest','packet_digest','same_business_context','stable_run_id'})
    ident = selected(UTILS/'agentic_governance.py', {'_json','_digest','work_digest'})
    ledger = SimpleNamespace(**{k: ident[k] for k in ('_digest','work_digest')}, connect=denied)
    for name in ('open_webui','open_webui.utils'):
        module = ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    start = selected(UTILS/'agentic_start_recovery.py',
        {'_token','_identity','_matching','_receipt','_readiness_packet','_frame_packet',
         '_readiness_entry','native_packet_digest','project_native_result'},
        {'MANAGED','READINESS_POLICY','READINESS_KIND'}, {**bridge,'ledger':ledger})
    module = ModuleType('open_webui.utils.agentic_start_recovery')
    for name in ('project_native_result','_identity','_receipt'):
        setattr(module,name,start[name])
    sys.modules[module.__name__] = module
    delivery = selected(UTILS/'agentic_gateway_delivery.py', {'git_hash','_commit_hash'})
    module = ModuleType('open_webui.utils.agentic_gateway_delivery')
    module._commit_hash = delivery['_commit_hash']
    sys.modules[module.__name__] = module
    continuation = selected(UTILS/'agentic_delivery_continuation.py',
        {'need','one','frozen_packet','_core_publication','published','scope_equal','_edge','test_calls'},
        {'POLICY','POLICY_SHA256','MANAGED','KEY'}, {**bridge,'ledger':ledger})
    holder = SimpleNamespace(**continuation)
    policy = selected(UTILS/'agentic_delivery_control.py',
        {'need','audit_valid','stamp','legacy_evidence_context','_legacy_event','_legacy_control','_legacy_call','legacy_facts'},
        {'LEGACY_POLICY','LEGACY_RECEIPT','HISTORY_PURPOSE','LEGACY_PRODUCERS','LEGACY_SOURCE_COMMIT'},
        {**bridge,'ledger':ledger,'continuation':holder})
    return policy, continuation


def readonly_facts(database, child_id, policy, continuation, operator, native, deployment, *, bound_call=None):
    need(re.fullmatch(r'[A-Za-z0-9_.:@+-]{8,160}', child_id), 'Existing child identity is invalid')
    old_uid, old_gid = os.geteuid(), os.getegid()
    db = None
    try:
        if old_uid == 0:
            os.setegid(988)
            os.seteuid(999)
        db = sqlite3.connect(Path(database).as_uri()+'?mode=ro',uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        row = continuation['one'](db,'SELECT * FROM governance_invocation WHERE invocation_id=?',(child_id,))
        need(row, 'Existing legacy child was not found')
        context = policy['legacy_evidence_context'](db,row)
        auth = policy['_legacy_event'](db,child_id,'delivery_checkpoint_authorized','governance-lifecycle-coordinator')
        facts, _ = policy['legacy_facts'](db,context,operator,native,deployment,bound_call=bound_call)
        need(db.total_changes == 0, 'Evidence preparation must be read-only')
        return facts, json.loads(auth['payload'])
    finally:
        if db is not None:
            db.close()
        if old_uid == 0:
            os.seteuid(0)
            os.setegid(old_gid)


def deployment_projection(path, policy):
    raw, _ = read_file(path)
    proof = json.loads(raw)
    need(proof['source_commit'] == policy['LEGACY_SOURCE_COMMIT'], 'Unsupported historical source')
    expected = policy['LEGACY_PRODUCERS']
    selected_pins = {Path(x['runtime_path']).name:x['sha256'] for x in proof['selected']}
    need(len(proof['selected']) == 2 and selected_pins == expected, 'Historical selected producers differ')
    required = {'complete71-after.json','completed-receipt.json','intent.json'}
    need(set(proof['records']) == required, 'Historical deployment records are incomplete')
    records, parsed = {}, {}
    for name, entry in proof['records'].items():
        source = Path(entry['path'])
        need(source.name == name and source.is_relative_to(ROLLOUTS), 'Unexpected rollout record path')
        data, info = read_file(source)
        need(info.st_uid == 0 and sha(data) == entry['sha256'] and info.st_mtime_ns == entry['mtime_ns'],
             'Original Root deployment record changed')
        parsed[name] = json.loads(data)
        records[name] = {'sha256':sha(data),'mtime_ns':info.st_mtime_ns}
    after, completed, intent = (parsed[name] for name in
                               ('complete71-after.json','completed-receipt.json','intent.json'))
    need(after.get('purpose') == 'actual71-after-original-commit-readback-recovery'
         and after.get('source_commit') == policy['LEGACY_SOURCE_COMMIT']
         and after.get('runtime_count') == len(after.get('files',[])) == 71,
         'Original deployed cohort structure differs')
    after_pins = {Path(x['runtime_path']).name:x['sha256'] for x in after['files']
                  if Path(x['runtime_path']).name in expected}
    installed = completed.get('installed') or []
    need(after_pins == expected and completed.get('ok') is True
         and completed.get('stage') == 'MAIN_AND_IDLE_QA_STARTED_DRAINING20_BLOCKED_ORIGINAL_RETAINED'
         and completed.get('runtime_manifest') == after and len(installed) == 2
         and {Path(x['path']).name:x['sha256'] for x in installed} == expected
         and all(x.get('uid') == 999 and x.get('gid') == 988 and x.get('mode') == '0644' for x in installed)
         and all(completed.get(name) == 1 for name in ('Main_Start_calls','Main_Stop_calls','QA_Start_calls','QA_Stop_calls'))
         and intent.get('source') == policy['LEGACY_SOURCE_COMMIT'],
         'Original completed deployment record does not prove the two producers')
    return {'schema_version':1,'purpose':'preserved-legacy-deployment.v1',
            'source_commit':policy['LEGACY_SOURCE_COMMIT'],'producer_sha256':expected,
            'records':records,'observed_at':datetime.now(timezone.utc).isoformat(),
            'provenance':'Read-only projection of retained pre-execution deployment records.'}, intent


def validate_proposal(directory, expected_index_sha, database, policy, continuation):
    directory = Path(directory)
    names = sorted(x.name for x in directory.iterdir())
    indexes = [name for name in names if re.fullmatch(r'[a-f0-9]{64}\.history\.json',name)]
    need(len(indexes) == 1, 'Proposal requires one exact history index')
    raw, _ = read_file(directory/indexes[0],65536)
    need(sha(raw) == expected_index_sha, 'Reviewed proposal index changed')
    index = json.loads(raw)
    need(set(index) == {'schema_version','purpose','authorization_sha256','native_ack_sha256',
                       'deployment_sha256','producer_sha256','bindings','retained_at','provenance'}
         and index['schema_version'] == 1 and index['purpose'] == policy['HISTORY_PURPOSE']
         and index['producer_sha256'] == policy['LEGACY_PRODUCERS']
         and index['provenance'] == 'Retrospective preservation of existing evidence; no new approval or transition.'
         and indexes[0] == index['authorization_sha256']+'.history.json',
         'Proposal index schema differs')
    objects = {}
    for key in ('authorization_sha256','native_ack_sha256','deployment_sha256'):
        value = index[key]
        need(isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value), 'Invalid evidence hash')
        data, _ = read_file(directory/(value+'.bin'))
        need(sha(data) == value, 'Proposed evidence object changed')
        objects[value+'.bin'] = data
    need(set(names) == set(objects) | {indexes[0]}, 'Unexpected files in proposed history')
    facts, authorization = readonly_facts(database,index['bindings']['child_invocation_id'],policy,continuation,
        *(json.loads(objects[index[key]+'.bin']) for key in
          ('authorization_sha256','native_ack_sha256','deployment_sha256')),
        bound_call=index['bindings'].get('completed_gateway'))
    need(index['bindings'] == facts and authorization['authorization_sha256'] == index['authorization_sha256'],
         'Original ledger history changed after proposal preparation')
    return indexes[0], raw, objects


def open_directory(path):
    path=Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'Archive directory must be canonical')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        return fd
    except BaseException:
        os.close(fd);raise


def install_create_only(directory, index_name, raw, objects):
    """All proposal checks must have passed before calling; index is installed last."""
    need(os.geteuid() == 0, 'Only Root can retain historical evidence')
    directory = Path(directory)
    parent=open_directory(directory.parent)
    try:
        try:
            os.mkdir(directory.name,0o750,dir_fd=parent)
            created=True
        except FileExistsError:
            created=False
        fd=os.open(directory.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        if created:
            os.fchown(fd,0,988);os.fchmod(fd,0o750)
        info=os.fstat(fd)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 988
             and stat.S_IMODE(info.st_mode) == 0o750,
             'Historical evidence directory is not protected')
    except BaseException:
        if 'fd' in locals():os.close(fd)
        raise
    finally:
        os.close(parent)
    try:
        all_values = {**objects,index_name:raw}
        # Preflight existing files before any creation. Partial correct objects
        # are reusable; conflicting bytes are never deleted or overwritten.
        for name,data in all_values.items():
            if (directory/name).exists() or (directory/name).is_symlink():
                prior, metadata = read_file(directory/name)
                need(metadata.st_uid == 0 and metadata.st_gid == 988
                     and stat.S_IMODE(metadata.st_mode) == 0o640 and prior == data,
                     'Existing historical evidence conflicts; preserve it')
        for name,data in all_values.items():
            try:
                target = os.open(name,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o640,dir_fd=fd)
            except FileExistsError:
                prior,_=read_file(directory/name)
                need(prior == data,'Historical evidence changed during retention')
                continue
            with os.fdopen(target,'wb') as stream:
                os.fchown(stream.fileno(),0,988)
                os.fchmod(stream.fileno(),0o640)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = denied
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='mode',required=True)
    prep=sub.add_parser('prepare')
    prep.add_argument('--child',required=True)
    prep.add_argument('--operator-file',type=Path,required=True)
    prep.add_argument('--native-ack-file',type=Path,required=True)
    prep.add_argument('--deployment-proof-file',type=Path,required=True)
    prep.add_argument('--delivery-producer',type=Path,required=True)
    prep.add_argument('--orchestrator-producer',type=Path,required=True)
    prep.add_argument('--output',type=Path,required=True)
    prep.add_argument('--ledger',type=Path,default=DATABASE)
    retain=sub.add_parser('retain')
    retain.add_argument('--proposal',type=Path,required=True)
    retain.add_argument('--expected-index-sha256',required=True)
    retain.add_argument('--ledger',type=Path,default=DATABASE)
    args=parser.parse_args()
    policy,continuation=load_policy()
    if args.mode=='prepare':
        need(args.output.is_absolute() and not args.output.exists() and not args.output.is_relative_to(HISTORY),
             'Prepare requires a new private proposal path outside the live archive')
        operator, info=read_file(args.operator_file,16384)
        need(info.st_uid==0 and info.st_gid==988 and stat.S_IMODE(info.st_mode)==0o640,
             'Original operator evidence is not protected')
        native,_=read_file(args.native_ack_file,16384)
        for name,path in (('agentic_gateway_delivery.py',args.delivery_producer),
                          ('agentic_orchestrator.py',args.orchestrator_producer)):
            data,_=read_file(path)
            need(sha(data)==policy['LEGACY_PRODUCERS'][name],'Preserved producer bytes changed')
        deployment,intent=deployment_projection(args.deployment_proof_file,policy)
        facts,authorization=readonly_facts(args.ledger,args.child,policy,continuation,
                                           json.loads(operator),json.loads(native),deployment)
        need(authorization['authorization_sha256']==sha(operator)
             and intent.get('original_invocation')==authorization['blocked_invocation_id']
             and intent.get('run_id')==authorization['blocked_run_id'],
             'Preserved operator or deployment intent belongs to another original')
        objects={sha(value)+'.bin':value for value in (operator,native,canonical(deployment).encode())}
        index={'schema_version':1,'purpose':policy['HISTORY_PURPOSE'],'authorization_sha256':sha(operator),
               'native_ack_sha256':sha(native),'deployment_sha256':sha(canonical(deployment).encode()),
               'producer_sha256':policy['LEGACY_PRODUCERS'],'bindings':facts,
               'retained_at':datetime.now(timezone.utc).isoformat(),
               'provenance':'Retrospective preservation of existing evidence; no new approval or transition.'}
        raw=(json.dumps(index,indent=2)+'\n').encode()
        args.output.mkdir(mode=0o700,parents=False)
        for name,value in {**objects,sha(operator)+'.history.json':raw}.items():
            fd=os.open(args.output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(value)
        validate_proposal(args.output,sha(raw),args.ledger,policy,continuation)
        print(json.dumps({'stage':'PROPOSAL_ONLY','proposal':str(args.output),'index_sha256':sha(raw),
                          'objects':{name:sha(value) for name,value in objects.items()},'ledger_writes':0,
                          'network_requests':0,'archive_installed':False}))
    else:
        name,raw,objects=validate_proposal(args.proposal,args.expected_index_sha256,args.ledger,policy,continuation)
        install_create_only(HISTORY,name,raw,objects)
        print(json.dumps({'stage':'HISTORICAL_EVIDENCE_RETAINED','index_sha256':sha(raw),
                          'existing_history_preserved':True,'new_authorization':False,'ledger_writes':0,
                          'network_requests':0,'service_actions':0}))


if __name__=='__main__':
    main()

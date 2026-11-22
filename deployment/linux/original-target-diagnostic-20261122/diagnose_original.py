"""One fixed read-only original-target diagnostic. No admission, replay or settlement.

Uses the actual pinned payload validator and the relay's original gateway receipt
path. All private inputs stay in process; only fixed diagnostic metadata is saved.
"""
from pathlib import Path
import datetime
import hashlib
import http.client
import importlib
import json
import os
import signal
import sys
import types

ROOT = Path('/srv/abit-dev-linux/abit-ai-restored-target-activation-20261122')
OP = ROOT / 'deployment/linux/restored-target-activation-20261122'
STAGE = Path('/var/lib/abit-ai-restored-target-activation-20261122')
CORE = Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui')
RELAY = Path('/srv/abit-dev-linux/relay-runtime/service')
INVOCATION = 'agov-step-e5f3296880a510bb5b63b738cefb0c203e32a5f9175690fe'
RUN = '341ed593-70fb-59bf-83c2-80cbf6f6cbf6'
BLOCKED = 'agov-adoption-9cccb407a25b175ace6004c3bab464b326c3bd06c0634256'
NODE_PID, NODE_START = 701646, 130530086
HELPER_SHA = '4cbf14905acd67d0acabded257b9090443aa023ca1038d424c0baea83c764481'
MANIFEST_SHA = '9ce4cdd6140a4bca9f8f76a80b935fd8622097745c609f9f9312062fb5e1fc43'
LIVE_RECEIPT_SHA = 'c74b07e27c9766b54c1611d833a9d94bb1e27688e022a0c03f91381b9718e18f'
CORE_SOURCE = {
    '__init__.py': 'efaa6278ec425396d95e7537d454781b126bf178cdbfb8f91ab1e699ace027f1',
    'utils/abitdot_primary_relay.py': '01b964ab6c1970c01397cc2737a2286a6bbdcae27cf66b5662a049fe4c798132',
    'utils/abitdot_primary.py': '530d7ed8cbc4eec40c361d9ed1a34a411928f4f832198dc8351518c11b7487fd',
    'utils/agentic_relay_scope.py': '8b97ec02bd14262f8a4704cfc57c3483a4d756bd9448392c013d47bad2428d21',
    'utils/agentic_bridge.py': 'feec63bb819ad058000e2a0a11a0710e64b6be332c50b1d19ad728b847a67180',
    'utils/agentic_tool_gateway.py': '463b9550ccdc1ce8edc2b85759981c8ca316cb8dba1aab3a2acfc206d33ba065',
}
CODES = {
    'ORIGINAL_CANONICAL_RECOVERY_AVAILABLE', 'ORIGINAL_TARGET_PROBE_SELECTOR_UNAVAILABLE',
    'ORIGINAL_TARGET_PROBE_ALREADY_TERMINAL', 'ORIGINAL_TARGET_PROBE_CHANGED',
    'ORIGINAL_TARGET_PROBE_UNVERIFIED', 'ORIGINAL_RESTORED_TARGET_AMBIGUOUS',
    'ORIGINAL_RESTORED_TARGET_ROOT_MISMATCH', 'ORIGINAL_RESTORED_TARGET_ACCOUNT_MISMATCH',
    'ORIGINAL_TARGET_PROBE_INCONCLUSIVE', 'ORIGINAL_RESTORED_TARGET_MATCH',
    'ORIGINAL_RESTORED_TARGET_ABSENT', 'ORIGINAL_TARGET_PROBE_TIMEOUT',
    'ORIGINAL_TARGET_PROBE_LIMIT', 'ORIGINAL_TARGET_STILL_PRESENT',
}
COUNTS = {'candidate_count','examined_count','exact_match_count','wrong_account_count',
          'different_root_count','ambiguous_count','unreadable_count'}
NO_ACTIONS = {'provider_send':False,'new_gateway_proof':False,'admission':False,
              'navigation':False,'selector_write':False,'settlement':False,
              'business_ledger_write':False}

class DiagnosticHeld(RuntimeError):
    def __init__(self, code):
        self.code=code
        super().__init__(code)

def require(ok, code):
    if not ok:
        raise DiagnosticHeld(code)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                    ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def reviewed_helpers():
    raw = (OP/'release_relay.py').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == HELPER_SHA, 'OPERATOR_SOURCE_CHANGED')
    value = types.ModuleType('reviewed_original_read_helpers')
    value.__file__ = str(OP/'release_relay.py')
    exec(compile(raw, value.__file__, 'exec'), value.__dict__)
    return value

def actual_payload_builder(a):
    for relative, expected in CORE_SOURCE.items():
        require(a.sha(a.raw(CORE/relative)) == expected, 'CORE_SOURCE_CHANGED')
    sys.path.insert(0,str(CORE.parent))
    mod = importlib.import_module('open_webui.utils.abitdot_primary_relay')
    require(Path(mod.__file__).resolve() == CORE/'utils/abitdot_primary_relay.py',
            'PAYLOAD_MODULE_MISMATCH')
    require(not any(name in sys.modules for name in
        ('open_webui.main','open_webui.env','open_webui.internal.db','open_webui.routers.chatgpt_relay')),
        'UNEXPECTED_APPLICATION_IMPORT')
    return mod.verified_dot_payload

def original_inputs(a,m):
    queries = [
        ('SELECT * FROM governance_invocation WHERE invocation_id=? LIMIT 2',(INVOCATION,)),
        ('SELECT * FROM governance_gateway_session WHERE invocation_id=? LIMIT 2',(INVOCATION,)),
        ('SELECT * FROM governance_start WHERE invocation_id=? LIMIT 2',(INVOCATION,)),
        ('SELECT * FROM governance_result WHERE invocation_id=? LIMIT 2',(INVOCATION,)),
        ('SELECT * FROM governance_local_result WHERE invocation_id=? LIMIT 2',(INVOCATION,)),
        ('SELECT * FROM governance_invocation WHERE invocation_id=? LIMIT 2',(BLOCKED,)),
    ]
    rows = a.db_rows(m,'agentic-governance.sqlite',queries)
    require(len(rows[0]) == len(rows[1]) == len(rows[5]) == 1, 'ORIGINAL_ROWS_UNAVAILABLE')
    row, admission, blocked = rows[0][0],rows[1][0],rows[5][0]
    require(row['erp_task']=='TASK-2026-06448' and row['erp_project']=='PROJ-0202'
            and row['state']=='uncertain' and row['dispatch_attempts']==1,
            'ORIGINAL_TASK_MISMATCH')
    require(row['automation_run_id']==RUN and row['submitted_at'] is not None,
            'ORIGINAL_WAS_NOT_SUBMITTED')
    require(blocked['erp_task']=='TASK-2026-06455' and blocked['state']=='blocked'
            and blocked.get('dispatch_attempts')==0 and blocked.get('submitted_at') is None,
            'BLOCKED_ORIGINAL_CHANGED')
    originals = a.db_rows(m,'abitdot-primary.sqlite',[
        ('SELECT * FROM dot_primary_runs WHERE automation_id=? AND original_run_id=? LIMIT 2',
         (row['agent_id'],RUN))])[0]
    require(len(originals)==1,'ORIGINAL_ENVELOPE_UNAVAILABLE')
    original = originals[0]; envelope = json.loads(original['envelope_json'])
    scope = envelope['governance_scope']['scope']
    require(scope['invocation_id']==INVOCATION and scope['run_id']==RUN
            and scope['agent_id']==row['agent_id']
            and scope['task_id']==row['erp_task']
            and envelope['owner_id']==original['owner']
            and envelope['automation_id']==original['automation_id']
            and envelope['run_id']==RUN and not original.get('response_text')
            and not original.get('receipt'), 'ORIGINAL_ENVELOPE_MISMATCH')
    require(json.loads(admission['admission_json'])==envelope['governance_scope'],
            'ORIGINAL_ADMISSION_MISMATCH')
    accounts = a.db_rows(m,'aibit-chatgpt-relay.sqlite',[
        ('SELECT account FROM sync_state WHERE owner=? ORDER BY updated_at DESC LIMIT 2',
         (original['owner'],))])[0]
    require(accounts and all(x['account']==scope['account_id'] for x in accounts),
            'CURRENT_ACCOUNT_BINDING_CHANGED')
    proof={'governance_rows_sha256':digest(rows),'dot_original_sha256':digest(originals),
           'account_bindings_sha256':digest(accounts),
           'original_envelope_sha256':hashlib.sha256(original['envelope_json'].encode()).hexdigest(),
           'original_admission_sha256':digest(envelope['governance_scope'])}
    return envelope,scope['account_id'],proof

def sanitized_result(status, value):
    require(status==200,'DIAGNOSTIC_HTTP_REJECTED')
    expected={'ok','protocol','read_only','diagnostic_mode','dot_handoff','response',
              'root_message_id','reply_message_id','safe_to_replay','target_probe'}
    require(type(value) is dict and set(value)==expected,'DIAGNOSTIC_RESPONSE_INVALID')
    require(type(value['ok']) is bool and value['protocol']=='v1_restored_target_diagnostic'
        and value['read_only'] is True and value['diagnostic_mode']=='restored_target'
        and value['dot_handoff'] is False and value['response']==''
        and value['root_message_id'] is None and value['reply_message_id'] is None
        and value['safe_to_replay'] is False,'DIAGNOSTIC_RESPONSE_INVALID')
    probe=value['target_probe']
    require(type(probe) is dict and set(probe)==COUNTS|{'code','complete','scope'}
        and probe['code'] in CODES and type(probe['complete']) is bool
        and probe['scope']=='currently_mounted_configured_owner_dot_feed'
        and all(type(probe[k]) is int and 0<=probe[k]<=128 for k in COUNTS),
        'DIAGNOSTIC_RESPONSE_INVALID')
    require(value['ok']==probe['complete'] and
        probe['complete']==(probe['code'] in
            {'ORIGINAL_RESTORED_TARGET_MATCH','ORIGINAL_RESTORED_TARGET_ABSENT'}),
        'DIAGNOSTIC_RESPONSE_INVALID')
    return value

def call_once(payload,token):
    connection=http.client.HTTPConnection('127.0.0.1',8810,timeout=70)
    try:
        body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        require(len(body)<=300000,'DIAGNOSTIC_REQUEST_OVERSIZED')
        connection.request('POST','/dot/observe',body=body,headers={
            'Authorization':'Bearer '+token,'Content-Type':'application/json',
            'Connection':'close'})
        response=connection.getresponse(); raw=response.read(16385)
        require(len(raw)<=16384,'DIAGNOSTIC_RESPONSE_OVERSIZED')
        try:
            return response.status,sanitized_result(response.status,json.loads(raw))
        except Exception as error:
            error.http_status=response.status
            raise
    finally:
        connection.close()

def run(mode):
    require(mode in {'preflight','observe'},'UNSUPPORTED_MODE')
    require(os.getresuid()==(0,0,0) and os.getresgid()==(0,0,0),'ROOT_OPERATOR_REQUIRED')
    a=reviewed_helpers()
    a.protected(STAGE,True)
    require(a.sha(a.raw(STAGE/'independent-live-readback.json'))==LIVE_RECEIPT_SHA,
            'LIVE_ACTIVATION_RECEIPT_CHANGED')
    a.protected(STAGE/'manifest.json')
    manifest_raw=a.raw(STAGE/'manifest.json')
    require(a.sha(manifest_raw)==MANIFEST_SHA,'LIVE_MANIFEST_CHANGED')
    m=json.loads(manifest_raw);a.owner(m);a.preserve(m)
    node=a.process(NODE_PID)
    require(node['start_ticks']==NODE_START and node['uid']==999,'RELAY_PROCESS_CHANGED')
    require(a.gate(m)['state']=='open' and a.gate(m)['epoch']==92,'MAINTENANCE_GATE_CHANGED')
    package=json.loads(a.raw(OP/'runtime/manifest.json'))
    for item in package['runtime_files']:
        require(a.sha(a.raw(RELAY/item['path']))==item['after_sha256'],'RELAY_SOURCE_CHANGED')
    for filename,expected in package['unchanged_files'].items():
        require(a.sha(a.raw(Path(filename)))==expected,'RELAY_DEPENDENCY_CHANGED')
    envelope,account,original_before=original_inputs(a,m)
    builder=actual_payload_builder(a)
    payload=builder(envelope,account,admission=False)
    require(payload['governance_scope']['scope']['invocation_id']==INVOCATION,
            'PAYLOAD_IDENTITY_CHANGED')
    payload={**payload,'diagnostic_mode':'restored_target'}
    preservation=a.private_preservation(m,verify=False)
    browser=a.browser_snapshot(m,verify=False)
    catalog_path=a.STATE/'aibit-relay-projects.json'
    catalog=a.file_proof(catalog_path) if catalog_path.exists() else None
    report={'schema_version':1,'purpose':'ONE_READ_ONLY_ORIGINAL_TARGET_DIAGNOSTIC',
        'invocation_id':INVOCATION,'run_id':RUN,'started_utc':a.now(),
        'helper_sha256':a.sha(Path(__file__).read_bytes()),'mode':mode,
        'original_proof':original_before,'provider_request_count':0,
        'new_proof_request_count':0,'diagnostic_post_count':0,**NO_ACTIONS,
        'blocked_06455':'UNCHANGED_BLOCKED_NO_QUEUED_PROOF_AUTHORITY'}
    token_path=Path('/srv/abit-dev-linux/relay-runtime/secrets/relay.token')
    token=a.raw(token_path,4096).decode().strip()
    token_metadata=token_path.stat()
    # This is the existing relay credential, not the distinct gateway-control
    # credential. Preserve its observed owner/group/mode; never change it.
    require(32<=len(token)<=4096 and token_metadata.st_uid==999
            and token_metadata.st_gid==988 and token_metadata.st_mode&0o777==0o660,
            'RELAY_CREDENTIAL_UNAVAILABLE')
    if mode=='preflight':
        report['state']='READY_FOR_ONE_ORIGINAL_DIAGNOSTIC'
        return report
    intent={'invocation_id':INVOCATION,'run_id':RUN,'helper_sha256':report['helper_sha256'],
            'payload_sha256':digest(payload),'created_utc':a.now()}
    a.savejson(STAGE/'original-06448-diagnostic.intent.json',intent)
    report['diagnostic_post_count']=1
    signal.signal(signal.SIGALRM,lambda signum,frame: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(80)
    try:
        report['http_status'],report['diagnostic']=call_once(payload,token)
        report['state']='OBSERVED'
    except BaseException as error:
        report['state']='DIAGNOSTIC_HELD'
        report['error_type']=type(error).__name__
        if isinstance(error,DiagnosticHeld): report['error_code']=error.code
        if type(getattr(error,'http_status',None)) is int: report['http_status']=error.http_status
    finally:
        signal.alarm(0)
    try:
        _,_,original_after=original_inputs(a,m)
        require(original_before==original_after,'ORIGINAL_ROWS_CHANGED')
        require(preservation==a.private_preservation(m,verify=False),'AUTHORITATIVE_STATE_CHANGED')
        require(browser==a.browser_snapshot(m,verify=False),'BROWSER_TARGETS_CHANGED')
        require(catalog==(a.file_proof(catalog_path) if catalog_path.exists() else None),
                'CATALOG_CACHE_CHANGED')
        require(a.process(NODE_PID)==node,'RELAY_PROCESS_CHANGED')
        a.owner(m);a.preserve(m)
        report['preservation_verified']=True
    except BaseException as error:
        report['preservation_verified']=False
        report['state']='PRESERVATION_HELD'
        report['preservation_error_type']=type(error).__name__
    report['finished_utc']=a.now()
    a.savejson(STAGE/'original-06448-diagnostic.result.json',report)
    return report

if __name__=='__main__':
    try:
        result=run(sys.argv[1] if len(sys.argv)==2 else '')
        print(json.dumps(result,sort_keys=True))
        raise SystemExit(0 if result['state'] in {'OBSERVED','READY_FOR_ONE_ORIGINAL_DIAGNOSTIC'} else 78)
    except BaseException as error:
        if isinstance(error,SystemExit):
            raise
        print(json.dumps({'state':'DIAGNOSTIC_OPERATOR_HELD','error_type':type(error).__name__,
                          'error_code':error.code if isinstance(error,DiagnosticHeld) else 'OPERATOR_BOUNDARY_UNVERIFIED',
                          'diagnostic_post_count':None,**NO_ACTIONS},sort_keys=True))
        raise SystemExit(78) from None

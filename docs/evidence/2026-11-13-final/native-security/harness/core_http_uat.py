"""Actual core coordinator -> actual isolated Frappe HTTP, controlled Dot only.

Every HTTP request is restricted to the owned internal canary. No main app,
live database, production credential, external provider or actual model is used.
"""
import asyncio,hashlib,importlib,json,logging,os,sys,time,types,uuid
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from urllib.parse import quote,urlsplit
import httpx
from PIL import Image

D=Path(__file__).resolve().parent
SOURCE=Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend/open_webui')
PRIVATE=json.loads((D/'core-private.json').read_text())
RUNTIME=D/'core-runtime'/str(int(time.time()));RUNTIME.mkdir(parents=True,mode=0o700)
DB=RUNTIME/'ledger.sqlite';REPORT=D/'core-http-report.json';PROJECT=PRIVATE['project']
package=types.ModuleType('open_webui');package.__path__=[str(SOURCE)]
utils=types.ModuleType('open_webui.utils');utils.__path__=[str(SOURCE/'utils')]
env=types.ModuleType('open_webui.env');env.DATA_DIR=RUNTIME
sys.modules.update({'open_webui':package,'open_webui.utils':utils,'open_webui.env':env})
M={name:importlib.import_module('open_webui.utils.'+name) for name in
   ('agentic_governance','agentic_bridge','agentic_executor','agentic_orchestrator','agentic_artifacts','agentic_result_store','agentic_pm_review')}
ledger=M['agentic_governance'];bridge_module=M['agentic_bridge'];executor=M['agentic_executor'];orchestrator=M['agentic_orchestrator'];artifacts=M['agentic_artifacts'];store=M['agentic_result_store']
importlib.import_module('open_webui.utils.erp_status_write').LOCK_ROOT=RUNTIME/'locks'
report={'fixture_limitations':'Synthetic protocol assertions and controlled role replies; does not prove business implementation, remote PR-head verification, or actual provider/model execution.','checks':[],'started_at':time.time(),'actual_framework_http':True,'actual_models':False,'controlled_dot':True,'provider_requests':0,'business_implementation_or_remote_PR_head_verification':False,'production_changes':False,'project':PROJECT,
    'erp_source':json.loads((D/'source-final-manifest.json').read_text()).get('commit'),
    'core_source_commit':'d6d9b97498fa02ea05a59eeeef815749af7cab75','core_modules_sha256':{name:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for name,module in M.items()}}
def check(name,value=True,**details):
    assert value,name
    report['checks'].append({'name':name,'passed':True,**details});REPORT.write_text(json.dumps(report,indent=2));print('CHECK',name,flush=True)

class Transport:
    def __init__(self,user='ai-developer@abitcons.com'):
        self.client=httpx.AsyncClient(base_url=PRIVATE['base_url'],timeout=30,follow_redirects=False,trust_env=False,
            headers={'Authorization':'token '+PRIVATE['tokens'][user]})
        self.calls=[];self.lose_ack=False
    async def request(self,method,path,**kwargs):
        assert path.startswith('/') and not path.startswith('//') and not urlsplit(path).netloc
        response=await self.client.request(method,path,**kwargs)
        self.calls.append((method,path,response.status_code))
        if response.is_error:
            from open_webui.utils.erp_status_write import ERPResponseError
            raise ERPResponseError(response)
        value=response.json()
        event=kwargs.get('json',{}).get('event',{})
        if self.lose_ack and event.get('type')=='run.ready':
            self.lose_ack=False
            raise TimeoutError('Controlled lost acknowledgment AFTER actual canary ERP committed')
        return value
    async def get(self,doctype,ident):
        return (await self.request('GET','/api/resource/'+quote(doctype,safe='')+'/'+quote(ident,safe='')))['data']
    async def rows(self,doctype,fields,filters):
        start=0
        while True:
            response=await self.request('GET','/api/resource/'+quote(doctype,safe=''),params={
                'fields':json.dumps(fields),'filters':json.dumps(filters),'limit_page_length':100,'limit_start':start,'order_by':'name asc'})
            for row in response['data']:yield row
            if len(response['data'])<100:return
            start+=len(response['data'])

erp=Transport();admin=Transport('Administrator');bridge=object.__new__(bridge_module.ERPBridge)
token_path=RUNTIME/'canary-bridge-token'
with os.fdopen(os.open(token_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w') as token_file:token_file.write(PRIVATE['bridge_secret'])
config={'agentic_artifact_roots':{PROJECT:str(RUNTIME/'artifacts')},'agentic_max_project_inflight':6,'agentic_bridge_token_file':str(token_path)}
bridge.erp=erp;bridge.secret=PRIVATE['bridge_secret'];bridge.config=config

async def gate(project,config):
    # Native RPC itself enforces both checkboxes, active status, exact _assign +
    # open Project ToDo, real read permissions, role binding and current SRS.
    await erp.request('GET',bridge_module.RPC+'get_work_packet',params={'project_id':project,'task_id':'','role':'project_manager'})
    doc=await erp.get('Project',project)
    assert doc['custom_agentic_governance_enabled']==1 and doc['custom_ai_project_manager_task_automation_enabled']==1
    return doc,{'enabled':True,'pm_task_automation_enabled':True,'project_status':doc['status'],
        'assigned_user':'ai-projects@abitcons.com','revision':str(doc['modified'])}

class ControlledDot:
    def __init__(self):self.calls=[];self.records=[];self.reject_first_qa=False
    async def prepare(self,row,packet,run_id):
        return types.SimpleNamespace(id=row['agent_id'],user_id='isolated-core-uat-owner')
    async def submit(self,row,packet,run_id,automation,bridge,*,before_submit):
        await before_submit()
        self.calls.append((row['invocation_id'],row['target_role'],row['erp_task'],run_id))
        qa=row['target_role']=='qa';fail=qa and self.reject_first_qa
        if fail:self.reject_first_qa=False
        review=packet.get('qa_review')
        outcome='reviewed' if review else 'fail' if fail else 'pass' if qa else 'ready_for_review'
        root=artifacts.artifact_directory(row,config);artifacts.ensure_directory(root)
        image=BytesIO();Image.new('RGB',(16,16),(18,150,210)).save(image,format='PNG');data=image.getvalue()
        file=root/'actual-canary.png';file.write_bytes(data);file.chmod(0o640)
        evidence={'id':'UAT-EVIDENCE-1','kind':'screenshot','uri':'artifact://actual-canary.png','sha256':hashlib.sha256(data).hexdigest(),'requirement_ids':packet['requirement_ids']}
        source=executor.implementation_source(row,packet,DB) if qa else None
        result={'schema_version':1,'invocation_id':row['invocation_id'],'actor':deepcopy(packet['binding']),
            'project_id':PROJECT,'task_id':row['erp_task'],'task_revision':packet['task_revision'],
            'work_packet_digest':bridge_module.packet_digest(packet),'outcome':outcome,
            'summary':'Controlled adapter result verified against actual isolated ERP and private PNG bytes; no model work.',
            'test_results':[{'test_id':t['id'],'status':'fail' if fail else 'pass','observed':'Synthetic independent acceptance observation on real canary HTTP.','evidence_ids':['UAT-EVIDENCE-1']} for t in packet['acceptance_tests']],
            'evidence':[evidence],'implementation_invocation_id':source['invocation_id'] if source else None,
            'implementation_agent_id':source['agent_id'] if source else None,
            'defects':[{'id':'UAT-DEFECT-1','requirement_ids':packet['requirement_ids'],'reproduction':'Repeat the controlled acceptance check.','expected':'The original task satisfies its acceptance checks.','observed':'Controlled first QA deliberately rejects for rework.','evidence_ids':['UAT-EVIDENCE-1']}] if fail else []}
        if row['target_role']=='developer':
            import subprocess
            repo=SOURCE.parents[1]
            def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            head=git('rev-parse','HEAD');base=git('rev-parse','HEAD^')
            paths=git('diff','--name-only',base,head).splitlines()
            # Protocol fixture references local commit identities, not verified remote PR heads.
            # It does not claim this controlled adapter implemented that code.
            delivery={'repository':packet['repository'],'base_commit':base,'head_commit':head,
                'pull_request_url':packet['repository'].rstrip('/')+'/pull/4','changed_paths':paths[:256],
                'commit_evidence_id':'UAT-COMMIT','test_runs':[{'test_ids':[x['id'] for x in packet['acceptance_tests']],
                'command':'controlled native ERP HTTP protocol assertions; no model/provider execution',
                'exit_code':0,'log_evidence_id':'UAT-EVIDENCE-1'}]}
            log=json.dumps({'fixture':'controlled native HTTP protocol','packet':packet['content_sha256'],
                'observed':'Native authorized task and private artifact publication path; not business code acceptance.'}).encode()
            log_file=root/'protocol.txt';log_file.write_bytes(log);log_file.chmod(0o640)
            result['evidence'][0]={**evidence,'kind':'test_log','uri':'artifact://protocol.txt','sha256':hashlib.sha256(log).hexdigest()}
            from open_webui.utils.agentic_contracts.developer_delivery import delivery_manifest
            content=json.dumps(delivery_manifest(delivery)).encode();file=root/'commit.json';file.write_bytes(content);file.chmod(0o640)
            result['evidence'].append({'id':'UAT-COMMIT','kind':'commit','uri':'artifact://commit.json',
                'sha256':hashlib.sha256(content).hexdigest(),'requirement_ids':packet['requirement_ids']})
            result['delivery']=delivery
        if qa and packet.get('implementation_delivery'):
            from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest
            result['reviewed_delivery_sha256']=delivery_digest(packet['implementation_delivery'])
        if review:
            result['implementation_invocation_id']=review['implementation_invocation_id']
            result['implementation_agent_id']=review['implementation_agent_id']
            result['qa_review_ack']=bridge_module.digest(review)
            actual=json.dumps({'qa_result_sha256':review['qa_result_sha256'],'qa_receipt_sha256':review['qa_receipt_sha256'],
                'qa_run_id':review['qa_run_id'],'observed':'Controlled PM reviewed exact native ERP QA publication.'}).encode()
            file=root/'pm-review.json';file.write_bytes(actual);file.chmod(0o640)
            result['evidence'][0]={**evidence,'kind':'erp_readback','uri':'artifact://pm-review.json','sha256':hashlib.sha256(actual).hexdigest()}
        return {'response_text':'AGOV RESULT: '+json.dumps(result)}
    async def record(self,automation,run_id,record):self.records.append(run_id)

dot=ControlledDot()
async def execute(ident):return await executor.execute(ident,project_gate=gate,config=config,adapter=dot,db_path=DB,bridge=bridge)
async def dispatch(ident):
    receipt=await execute(ident)
    if receipt:await orchestrator.advance(ident,receipt,config=config,project_gate=gate,dispatch=dispatch,db_path=DB,bridge=bridge)

async def enqueue(task,ident):
    packet=await bridge.packet(PROJECT,task,'developer',ledger.agent_id(PROJECT,'developer'))
    _,policy=await gate(PROJECT,config)
    return ledger.enqueue(invocation_id=ident,project=PROJECT,task=task,from_role='project_manager',target_role='developer',
        task_revision=packet['task_revision'],context='Synthetic actual HTTP core UAT; no external actions.',work_packet=packet,
        actor='isolated-native-uat',gate=policy,db_path=DB)[0]

async def run():
    try:
        ledger.enroll(PROJECT,'isolated-core-uat-owner',PRIVATE['role_users'],DB)
        project,policy=await gate(PROJECT,config)
        ledger.project_control(PROJECT,'enable',actor='authorized-canary-admin',policy_revision=policy['revision'],db_path=DB)
        task=PRIVATE['tasks']['pass'];ident='canary-developer-'+uuid.uuid4().hex
        await enqueue(task,ident);erp.lose_ack=True
        receipt=await execute(ident)
        check('actual_unknown_ERP_publish_ack_preserves_original',receipt is None and (await erp.get('Task',task))['status']=='Pending Review' and len(dot.calls)==1)
        for _ in range(2):await execute(ident)
        check('duplicate_original_execute_has_one_model_boundary',len(dot.calls)==1)
        receipt=await executor.reconcile_publication(ident,bridge,DB)
        check('actual_ERP_original_receipt_recovered_without_replay',receipt['status']=='Pending Review' and len(dot.calls)==1)
        await orchestrator.advance(ident,receipt,config=config,project_gate=gate,dispatch=dispatch,db_path=DB,bridge=bridge)
        taskdoc=await erp.get('Task',task)
        check('actual_core_coordinator_developer_to_independent_QA',taskdoc['status']=='QA APPROVED' and [r[1] for r in dot.calls]==['developer','qa'])
        check('actual_QA_verification_releases_business_fence',ledger.find(ident,DB)['state']=='verified')
        count=len(dot.calls)
        for _ in range(2):
            await executor.reconcile_publication(ident,bridge,DB)
            await orchestrator.advance(ident,receipt,config=config,project_gate=gate,dispatch=dispatch,db_path=DB,bridge=bridge)
            await orchestrator.scan_project(PROJECT,config=config,project_gate=gate,dispatch=dispatch,db_path=DB,bridge=bridge)
        check('duplicate_callback_coordinator_recovery_no_extra_execution',len(dot.calls)==count)
        for call in dot.calls:
            saved=store.get_result(call[0],DB);artifact=saved['event']['artifacts'][0]
            url=urlsplit(artifact['url']);response=await erp.client.get(url.path+'?'+url.query)
            check('actual_private_File_HTTP_byte_hash_'+call[1],response.status_code==200 and hashlib.sha256(response.content).hexdigest()==artifact['sha256'])
        dot.reject_first_qa=True;rework=PRIVATE['tasks']['rework'];reworkid='canary-rework-'+uuid.uuid4().hex
        await enqueue(rework,reworkid);before=len(dot.calls);await dispatch(reworkid)
        check('actual_QA_rejection_developer_rework_new_QA_pass',
            (await erp.get('Task',rework))['status']=='QA APPROVED' and [r[1] for r in dot.calls[before:]]==['developer','qa','developer','qa'])
        calls=dot.calls[before:];check('each_rework_work_unit_distinct_original_identity',len({c[0] for c in calls})==4 and len({c[3] for c in calls})==4)
        gated=PRIVATE['tasks']['gates'];gateid='canary-gate-'+uuid.uuid4().hex
        await enqueue(gated,gateid);before=len(dot.calls);original=(await erp.get('Task',gated))['modified']
        path='/api/resource/Project/'+PROJECT
        await admin.request('PUT',path,json={'custom_ai_project_manager_task_automation_enabled':0})
        await execute(gateid)
        check('actual_PM_checkbox_disabled_while_queued_zero_model',len(dot.calls)==before and (await erp.get('Task',gated))['modified']==original)
        await admin.request('PUT',path,json={'custom_ai_project_manager_task_automation_enabled':1})
        report['passed']=True;report['model_submission_boundaries']=len(dot.calls);report['finished_at']=time.time();REPORT.write_text(json.dumps(report,indent=2));print('CORE_ACTUAL_HTTP_UAT_PASS',len(report['checks']),flush=True)
    except Exception as error:
        report['passed']=False;report['error']={'type':type(error).__name__,'message':str(error)[:2000]};REPORT.write_text(json.dumps(report,indent=2));raise
    finally:
        await erp.client.aclose();await admin.client.aclose()

if __name__=='__main__':asyncio.run(run())

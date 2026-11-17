"""Real ERP human adoption -> PM -> Developer/IT -> independent QA.

Dot is controlled. IT uses actual read-only gateway inspection of this owned
test directory and actual canonical human ERP approval/consumption APIs.
Automation-model storage is isolated in-memory; no main app/live DB is loaded.
"""
import asyncio,importlib,json,socket,time,types,uuid,sys
import core_http_uat as c
it=importlib.import_module('open_webui.utils.agentic_it_execution')
gateway=importlib.import_module('open_webui.utils.agentic_tool_gateway')
c.REPORT=c.D/'core-adoption-it-report.json'
c.report.update(actual_read_only_IT_adapter=True,actual_approval_HTTP=True)
c.config['agentic_tool_gateway']={'projects':{c.PROJECT:{'host_id':socket.gethostname(),
    'operations':['inspect'],'roots':{'owned_canary':str(c.D)},'services':[]}}}
automation_records={}
class CanaryIT(it.ITAdapter):
    async def prepare(self,row,packet,run_id):
        value=types.SimpleNamespace(id=row['agent_id'],user_id='isolated-core-uat-owner');automation_records[row['agent_id']]=value;return value
    async def record(self,automation,run_id,record):c.dot.records.append(run_id)
it.ITAdapter=CanaryIT
class Automations:
    @staticmethod
    async def get_by_id(ident):return automation_records[ident]
models=types.ModuleType('open_webui.models');models.__path__=[]
automations=types.ModuleType('open_webui.models.automations');automations.Automations=Automations
sys.modules.update({'open_webui.models':models,'open_webui.models.automations':automations})
IT_CALLS=[]
human=c.Transport('canary-human-approver@example.invalid')
async def execute(ident):
    row=c.ledger.find(ident,c.DB)
    adapter=CanaryIT(None,c.gate,c.config,c.DB) if row['target_role']=='it_manager' else c.dot
    receipt=await c.executor.execute(ident,project_gate=c.gate,config=c.config,adapter=adapter,db_path=c.DB,bridge=c.bridge)
    if row['target_role']=='it_manager':IT_CALLS.append(ident)
    return receipt
async def dispatch(ident):
    receipt=await execute(ident)
    if receipt:await c.orchestrator.advance(ident,receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)

async def adopt(role,risk='routine'):
    result=await c.admin.request('POST','/api/resource/Task',json={'project':c.PROJECT,'subject':'Synthetic human adopted '+role+' '+uuid.uuid4().hex[:8],
        'description':'Actual native human-adopted task; inspect only owned canary resources.','status':'Open'})
    doc=result['data'];args={'project_id':c.PROJECT,'task_id':doc['name'],'expected_modified':doc['modified'],
        'requirement_ids':['REQ-UAT-01'],'acceptance_tests':'Real original identity, exact private evidence, human authority and independent QA.', 'target_role':role}
    if role=='it_manager':args['tool_action']={'operation':'inspect','risk':risk,'parameters':{'host_id':socket.gethostname(),'root_keys':['owned_canary'],'services':[]},'request_key':'canary-inspect-'+uuid.uuid4().hex}
    value=(await c.admin.request('POST',c.bridge_module.RPC+'configure_existing_task',json=args))['message']
    c.check('human_adoption_keeps_'+role+'_Task_Open',value['status']=='Open')
    pre=(await c.erp.request('POST',c.bridge_module.RPC+'prepare_task_start',json={'project_id':c.PROJECT,'task_id':doc['name']},headers={'X-ABIT-Agentic-Bridge':c.bridge.secret}))['message']
    c.check('explicit_'+role+'_Start_requires_PM_review',pre['target_role']=='project_manager' and pre['executor_role']==role)
    packet=await c.bridge.packet(c.PROJECT,doc['name'],'project_manager',c.ledger.agent_id(c.PROJECT,'project_manager'))
    _,gate=await c.gate(c.PROJECT,c.config);ident='canary-adopted-'+uuid.uuid4().hex
    c.ledger.enqueue(invocation_id=ident,project=c.PROJECT,task=doc['name'],from_role='erp',target_role='project_manager',
        task_revision=packet['task_revision'],context='Explicit authorized human adopted atomic Task start.',work_packet=packet,actor='canary-human',gate=gate,db_path=c.DB)
    return doc['name'],ident

async def run():
    try:
        c.ledger.enroll(c.PROJECT,'isolated-core-uat-owner',c.PRIVATE['role_users'],c.DB)
        _,gate=await c.gate(c.PROJECT,c.config);c.ledger.project_control(c.PROJECT,'enable',actor='authorized-canary-human',policy_revision=gate['revision'],db_path=c.DB)
        # Empty-task Project packet uses native datetime and is now serialized.
        response=await c.erp.request('GET',c.bridge_module.RPC+'get_work_packet',params={'project_id':c.PROJECT,'task_id':'','role':'project_manager'})
        c.check('native_Project_only_packet_datetime_digest_serializes',response['message']['project_id']==c.PROJECT)
        task,ident=await adopt('developer');before=len(c.dot.calls);await dispatch(ident)
        c.check('actual_human_adoption_PM_developer_independent_QA',
            (await c.erp.get('Task',task))['status']=='QA APPROVED' and [x[1] for x in c.dot.calls[before:]]==['project_manager','developer','qa'])
        task,ident=await adopt('it_manager');before=len(c.dot.calls);await dispatch(ident)
        c.check('actual_PM_IT_readonly_gateway_independent_QA',
            (await c.erp.get('Task',task))['status']=='QA APPROVED' and [x[1] for x in c.dot.calls[before:]]==['project_manager','qa'])
        with c.ledger.connect(c.DB) as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM governance_action')]
        c.check('IT_actual_adapter_inspected_only_owned_directory',len(rows)==1 and json.loads(rows[0]['result_json'])['read_only'] is True
            and set(json.loads(rows[0]['result_json'])['roots'])=={'owned_canary'})
        task,ident=await adopt('it_manager','privileged');before=len(c.dot.calls);await dispatch(ident)
        with c.ledger.connect(c.DB) as db:
            pending=db.execute("SELECT * FROM governance_invocation WHERE erp_task=? AND state='awaiting_approval'",(task,)).fetchone()
            c.check('privileged_inspection_waits_for_real_human_ERP_approval',pending is not None and [x[1] for x in c.dot.calls[before:]]==['project_manager'])
            pending=dict(pending);wait=dict(db.execute('SELECT * FROM governance_action_wait WHERE invocation_id=?',(pending['invocation_id'],)).fetchone())
        request=json.loads(wait['request_json']);action=request['action']
        details=(await c.admin.request('GET',c.bridge_module.RPC+'record_detail',params={'project_id':c.PROJECT,'record_id':'AGOV-'+c.bridge_module.digest(['approval',wait['approval_id']])[:48]}))['message']
        # Dashboard details expose the immutable exact action; no credential is present.
        approval_digest=c.bridge_module.digest(action)
        try:
            await c.erp.request('POST',c.bridge_module.RPC+'decide_approval',json={'project_id':c.PROJECT,'approval_id':wait['approval_id'],'decision':'approved','expected_digest':approval_digest})
        except Exception as e:c.check('AI_service_cannot_self_approve_real_action','409' in str(e))
        else:raise AssertionError('AI/service approved its own action')
        await human.request('POST',c.bridge_module.RPC+'decide_approval',json={'project_id':c.PROJECT,'approval_id':wait['approval_id'],'decision':'approved','expected_digest':approval_digest})
        receipt=await it.resume(pending['invocation_id'],config=c.config,project_gate=c.gate,bridge=c.bridge,app=None,db_path=c.DB)
        c.check('real_human_approval_resumes_original_IT_run',receipt is not None and receipt['status']=='Pending Review')
        await c.orchestrator.advance(pending['invocation_id'],receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
        c.check('approved_IT_action_independent_QA_APPROVED',(await c.erp.get('Task',task))['status']=='QA APPROVED')
        with c.ledger.connect(c.DB) as db:
            count=db.execute('SELECT count(*) FROM governance_action').fetchone()[0]
        before=len(c.dot.calls)
        for _ in range(2):
            await it.resume(pending['invocation_id'],config=c.config,project_gate=c.gate,bridge=c.bridge,app=None,db_path=c.DB)
            await c.orchestrator.advance(pending['invocation_id'],receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
        with c.ledger.connect(c.DB) as db:after=db.execute('SELECT count(*) FROM governance_action').fetchone()[0]
        c.check('duplicate_IT_resume_and_advance_no_extra_host_or_model',after==count and len(c.dot.calls)==before)
        c.report['passed']=True;c.report['finished_at']=time.time();c.REPORT.write_text(json.dumps(c.report,indent=2));print('CORE_ADOPTION_IT_ACTUAL_PASS',len(c.report['checks']),flush=True)
    except Exception as error:
        c.report['passed']=False;c.report['error']={'type':type(error).__name__,'message':str(error)[:2000]};c.REPORT.write_text(json.dumps(c.report,indent=2));raise
    finally:await c.erp.client.aclose();await c.admin.client.aclose();await human.client.aclose()

if __name__=='__main__':asyncio.run(run())

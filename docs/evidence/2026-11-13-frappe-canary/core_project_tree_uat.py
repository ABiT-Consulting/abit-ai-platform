"""Real native architecture->PM->released DAG->child QA->parent acceptance.

All ERP HTTP, private Files, task tree, plan materialization and coordinator
logic are real. Only remote model/Automation storage is controlled.
"""
import asyncio,json,time,uuid,hashlib
from copy import deepcopy
from pathlib import Path
import core_http_uat as c
c.REPORT=c.D/'core-project-tree-report.json'
FIXTURE=Path('/srv/abit-dev-linux/abit-ai-agents/tests/fixtures')
class ArchitectureDot(c.ControlledDot):
    async def submit(self,row,packet,run_id,automation,bridge,*,before_submit):
        record=await super().submit(row,packet,run_id,automation,bridge,before_submit=before_submit)
        if row['target_role']=='solution_architect':
            result=json.loads(record['response_text'].removeprefix('AGOV RESULT: '))
            plan=json.loads((FIXTURE/'solution_architect_valid.json').read_text())
            plan['metadata'].update(project_id=c.PROJECT,srs_version=packet['srs']['revision'],srs_sha256=packet['srs']['sha256'],
                source_url='https://erp.abit.ai/desk/project/'+c.PROJECT,source_activity_revision=packet['source_modified'],run_id=row['invocation_id'],
                agent_binding={'project_id':c.PROJECT,'erp_ai_user':packet['binding']['erp_user'],'abit_agent_id':row['agent_id'],'role':'solution_architect'})
            root=c.artifacts.artifact_directory(row,c.config)
            for kind,items,filename in [('design',plan['designs'],'architect-component.svg'),('mockup',plan['mockups'],'architect-dashboard.svg')]:
                data=(FIXTURE/filename).read_bytes();p=root/filename;p.write_bytes(data);p.chmod(0o640)
                for item in items:
                    item['artifact']={'kind':'content_store','uri':'artifact://'+filename,'sha256':hashlib.sha256(data).hexdigest()}
                    result['evidence'].append({'id':item['id'],'kind':kind,'uri':'artifact://'+filename,'sha256':hashlib.sha256(data).hexdigest(),'requirement_ids':item['requirement_ids']})
            for item in plan['tasks']:item['idempotency_key']=c.PROJECT+'-native-'+item['task_key']
            result['architect_plan']=plan;record['response_text']='AGOV RESULT: '+json.dumps(result)
        return record
c.dot=ArchitectureDot()
receipts=[]
async def dispatch(ident):
    receipt=await c.execute(ident)
    if not receipt:
        row=c.ledger.find(ident,c.DB);raise AssertionError('Native tree dispatch held '+row['target_role']+' '+row['state']+' '+str(row.get('reason','')))
    receipts.append((ident,receipt))
    await c.orchestrator.advance(ident,receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)

async def run():
    try:
        fields={'project_name':'AGOV Full Native Tree '+uuid.uuid4().hex[:8],'company':'AGOV Isolated UAT Company','status':'Open',
            'custom_agentic_governance_enabled':1,'custom_ai_project_manager_task_automation_enabled':1,
            'custom_github_repository':'https://github.com/ABiT-Consulting/abit-ai-platform','custom_test_environment_name':'Isolated UAT',
            'custom_ai_project_srs':'Synthetic complete architecture, two dependency ordered tasks, independent child and parent QA.',
            'custom_ai_developer_instructions':'Use only synthetic canary scope; no external/model/provider actions.',
            'custom_ai_project_features':[{'requirement_id':req,'feature_title':title,'feature_kind':kind,'description':description,'acceptance_criteria':'Exact native scope, real private evidence, no duplicate, independent QA.','priority':'P0'} for req,title,kind,description in
                [('REQ-UI-01','Project governance dashboard','UI','Display task controls and exact scoped status.'),('REQ-API-02','Server-authorized dispatch','API','Enforce authenticated scope and durable idempotency.')]]}
        project=(await c.admin.request('POST','/api/resource/Project',json=fields))['data']
        c.PROJECT=project['name'];c.report['project']=c.PROJECT;c.config['agentic_artifact_roots'][c.PROJECT]=str(c.RUNTIME/'tree-artifacts')
        await c.admin.request('POST','/api/method/frappe.desk.form.assign_to.add',json={'doctype':'Project','name':c.PROJECT,'assign_to':['ai-projects@abitcons.com'],'description':'Synthetic full-tree UAT assignment','notify':0})
        project=await c.erp.get('Project',c.PROJECT)
        records=c.ledger.enroll(c.PROJECT,'isolated-core-uat-owner',c.PRIVATE['role_users'],c.DB)
        await c.bridge.register(c.PROJECT,project['modified'],records)
        _,gate=await c.gate(c.PROJECT,c.config);c.ledger.project_control(c.PROJECT,'enable',actor='authorized-canary-human',policy_revision=gate['revision'],db_path=c.DB)
        prepared=(await c.erp.request('POST',c.bridge_module.RPC+'prepare_project_start',json={'project_id':c.PROJECT},headers={'X-ABIT-Agentic-Bridge':c.bridge.secret}))['message']
        anchor=prepared['task_id'];c.report['anchor']=anchor
        packet=await c.bridge.packet(c.PROJECT,anchor,'solution_architect',c.ledger.agent_id(c.PROJECT,'solution_architect'))
        ident='canary-full-architecture-'+uuid.uuid4().hex
        c.ledger.enqueue(invocation_id=ident,project=c.PROJECT,task=anchor,from_role='erp',target_role='solution_architect',
            task_revision=packet['task_revision'],context='Actual synthetic full native project lifecycle.',work_packet=packet,actor='canary-human',gate=gate,db_path=c.DB)
        c.check('real_Project_provisioned_six_scoped_bindings_and_architecture_anchor',len(c.ledger.roster(c.PROJECT,c.DB))==6)
        await dispatch(ident)
        tasks=[r async for r in c.erp.rows('Task',['name','status','parent_task','is_group','custom_agov_dispatch_released'],[['project','=',c.PROJECT]])]
        c.report['tasks']=tasks;c.report['boundaries']=[{'invocation_id':x[0],'role':x[1],'task':x[2],'run':x[3]} for x in c.dot.calls]
        c.check('actual_ARCH_PM_two_task_DAG_and_parent_reach_QA_APPROVED',len(tasks)==3 and all(t['status']=='QA APPROVED' for t in tasks))
        roles=[x[1] for x in c.dot.calls]
        c.check('actual_native_full_tree_role_sequence',roles==['solution_architect','project_manager','developer','qa','developer','qa','developer','qa'],roles=roles)
        c.check('parent_acceptance_is_separate_from_both_children',sum(x[1]=='developer' and x[2]==anchor for x in c.dot.calls)==1 and sum(x[1]=='qa' and x[2]==anchor for x in c.dot.calls)==1)
        before=len(c.dot.calls)
        for _ in range(2):
            for original,receipt in receipts:await c.orchestrator.advance(original,receipt,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
            await c.orchestrator.scan_project(c.PROJECT,config=c.config,project_gate=c.gate,dispatch=dispatch,db_path=c.DB,bridge=c.bridge)
        c.check('full_tree_duplicate_results_and_scans_no_extra_runs_or_children',len(c.dot.calls)==before and len([r async for r in c.erp.rows('Task',['name'],[['project','=',c.PROJECT]])])==3)
        c.report['passed']=True;c.report['finished_at']=time.time();c.REPORT.write_text(json.dumps(c.report,indent=2));print('CORE_FULL_NATIVE_TREE_PASS',len(c.report['checks']),flush=True)
    except Exception as error:
        c.report['passed']=False;c.report['error']={'type':type(error).__name__,'message':str(error)[:2500]}
        if c.PROJECT:
            try:c.report['tasks']=[r async for r in c.erp.rows('Task',['name','status','parent_task','is_group','custom_agov_dispatch_released'],[['project','=',c.PROJECT]])]
            except Exception:pass
        c.REPORT.write_text(json.dumps(c.report,indent=2));raise
    finally:await c.erp.client.aclose();await c.admin.client.aclose()

if __name__=='__main__':asyncio.run(run())

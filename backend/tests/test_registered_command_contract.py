"""Recorded command receipts only; never execute the command or contact services."""
from copy import deepcopy
import hashlib,importlib.util,json,os,sys,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

HERE=Path(__file__).resolve().parent
BACKEND=HERE.parent if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-live-20261113/source/backend')
LIVE=BACKEND/'open_webui/utils' if HERE.name=='tests' else Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui/utils')
sys.path.insert(0,str(BACKEND/'tests'))
import test_agentic_executor_flow as flow_fixture
from test_agentic_controls_events import Fixture,P,T

def command_fixture():
    private=os.environ.get('ABIT_TEST_RECORDED_COMMAND_FILE')
    if private:
        return json.loads(Path(private).read_text())['command']
    # Sanitized portable analogue: same 14 argv /4409 chars/4420 UTF-8 bytes.
    return json.dumps(['python3','é'*11+'x'*328]+['x'*335]*12,ensure_ascii=False,separators=(',',':'))

def candidate_root():
    return HERE/'agentic_contracts' if (HERE/'agentic_contracts').is_dir() else BACKEND/'open_webui/utils/agentic_contracts'

def install_contract():
    path=candidate_root()/'developer_delivery.py';full='open_webui.utils.agentic_contracts.developer_delivery'
    spec=importlib.util.spec_from_file_location(full,path);m=importlib.util.module_from_spec(spec);sys.modules[full]=m;spec.loader.exec_module(m)
    validator=sys.modules['open_webui.utils.agentic_contracts.validator']
    validator.SCHEMA_PATH=candidate_root()/'schemas/governance_v1.schema.json'
    return m,validator

class RegisteredCommand(unittest.IsolatedAsyncioTestCase):
    async def flow(self):
        flow_fixture.UTILS=LIVE
        f=flow_fixture.ExecutorFlow('test_real_gateway_adapter_cannot_publish_model_only_developer_delivery')
        await f.asyncSetUp();self.addCleanup(f.doCleanups)
        helper,validator=install_contract()
        row,packet,_=await f.enqueue()
        run=f.bridge_module.stable_run_id(row['invocation_id'])
        _,gate=await f.project_gate(row['erp_project'],f.config)
        row=f.ledger.claim_invocation(row['invocation_id'],run,gate=gate,db_path=f.db)
        start={'schema_version':1,'event_id':'agov-start-'+f.bridge_module.digest(row['invocation_id'])[:48],'type':'run.started','invocation_id':row['invocation_id'],'run_id':run,'project_id':row['erp_project'],'task_id':row['erp_task'],'task_revision':row['task_revision'],'role':'developer','agent_id':row['agent_id'],'work_packet_digest':packet['content_sha256'],'expected_modified':packet['source_modified'],'expected_project_revision':packet['project_revision'],'artifacts':[]}
        from open_webui.utils.agentic_start_recovery import record_start_intent,record_start_receipt
        record_start_intent(row,start,f.db)
        f.erp.advance('Working');receipt={'event_id':start['event_id'],'run_id':run,'task_id':row['erp_task'],'modified':f.erp.task['modified'],'status':'Working','durable':True}
        record_start_receipt(row['invocation_id'],receipt,f.db)
        f.ledger.mark_submitted(row['invocation_id'],run,row['lease_token'],gate=gate,db_path=f.db)
        row=f.ledger.find(row['invocation_id'],f.db,include_private=True)
        automation=types.SimpleNamespace(id=row['agent_id'],user_id='verified-owner')
        record=await f.adapter.submit(row,packet,run,automation,f.bridge,before_submit=AsyncMock())
        result=json.loads(record['response_text'].split('AGOV RESULT: ',1)[1]);result['delivery']['test_runs'][0]['command']=command_fixture()
        record['response_text']='```text\nAGOV RESULT: '+json.dumps(result,ensure_ascii=False)+'\n```'
        return f,helper,validator,row,packet,automation,record,result,receipt

    async def test_recorded_long_command_crosses_unchanged_complete_original_and_artifact_validation(self):
        f,h,v,row,packet,automation,record,result,receipt=await self.flow()
        command=result['delivery']['test_runs'][0]['command'];self.assertEqual((len(command),len(command.encode())),(4409,4420))
        args=json.loads(command);self.assertLessEqual(len(args),30);self.assertTrue(all(len(x)<=500 and '\0'not in x for x in args))
        self.assertEqual(v.validate_result(result,packet),[])
        with f.ledger.connect(f.db)as db:before=[dict(x)for x in db.execute('SELECT *FROM governance_start')]
        # Reproduce the deployed cap at the real completion boundary, without
        # printing the private command or weakening any downstream validator.
        old_helper_path=HERE/'developer_delivery.py.before';old_schema=HERE/'governance_v1.schema.json.before'
        if old_helper_path.is_file() and old_schema.is_file():
            old=types.ModuleType(h.__name__);old.__package__=h.__package__;exec(compile(old_helper_path.read_bytes(),str(old_helper_path),'exec'),old.__dict__)
            self.assertTrue(old.validate_developer_delivery(result,packet))
            with patch.dict(sys.modules,{h.__name__:old}),patch.object(v,'SCHEMA_PATH',old_schema):
                with self.assertRaises(ValueError)as rejected:
                    await f.executor.complete_original(row,packet,record,f.adapter,automation,f.bridge,receipt,f.project_gate,f.config,f.db)
                self.assertTrue(str(rejected.exception).startswith('result.delivery.test_runs.0.command:'))
            self.assertIsNone(f.modules['agentic_result_store'].get_result(row['invocation_id'],f.db))
        result_receipt=await f.executor.complete_original(row,packet,record,f.adapter,automation,f.bridge,receipt,f.project_gate,f.config,f.db)
        self.assertEqual(result_receipt['status'],'Pending Review');self.assertTrue(result_receipt['durable'])
        saved=f.modules['agentic_result_store'].get_result(row['invocation_id'],f.db)
        self.assertEqual(saved['result']['delivery']['test_runs'][0]['command'],command)
        self.assertEqual(f.erp.publish_calls,['run.ready']);self.assertTrue(f.erp.uploads)
        current=f.ledger.find(row['invocation_id'],f.db,include_private=True)
        self.assertEqual((current['automation_run_id'],current['dispatch_attempts'],current['submitted_at']),(row['automation_run_id'],1,row['submitted_at']))
        with f.ledger.connect(f.db)as db:self.assertEqual(before,[dict(x)for x in db.execute('SELECT *FROM governance_start')])

    async def test_exact_registration_capacity_and_overbound_core_and_shared_native_helper(self):
        f,h,v,row,packet,automation,record,result,receipt=await self.flow()
        limit=h.MAX_REGISTERED_COMMAND_CHARS;self.assertEqual(limit,90091)
        maximum=f.bridge_module.canonical(['\x01'*500]*30);self.assertEqual(len(maximum),limit)
        result['delivery']['test_runs'][0]['command']=maximum
        self.assertEqual(v.validate_result(result,packet),[]);self.assertEqual(h.validate_developer_delivery(result,packet),[]);self.assertEqual(h.validate_delivery(result['delivery'],packet['repository']),[])
        # Exercise the exact candidate shared leaf under the Native namespace.
        # Actual installed consumer/image identity is separate deployment proof.
        native=types.ModuleType('abit_ai_assistant.developer_delivery');native.__package__='abit_ai_assistant'
        exec(compile((candidate_root()/'developer_delivery.py').read_bytes(),'<same-native-shared-leaf>','exec'),native.__dict__)
        self.assertEqual(native.validate_developer_delivery(result,packet),[]);self.assertEqual(native.validate_delivery(result['delivery'],packet['repository']),[])
        for invalid in [maximum+'x','x\0y']:
            result['delivery']['test_runs'][0]['command']=invalid
            self.assertTrue(v.validate_result(result,packet));self.assertTrue(h.validate_developer_delivery(result,packet));self.assertTrue(h.validate_delivery(result['delivery'],packet['repository']))
            self.assertTrue(native.validate_developer_delivery(result,packet));self.assertTrue(native.validate_delivery(result['delivery'],packet['repository']))

class RegisteredCommandJournal(Fixture):
    def load(self,name):
        path=LIVE/(name+'.py')
        if not path.exists():path=BACKEND/'open_webui/utils'/(name+'.py')
        full='open_webui.utils.'+name;spec=importlib.util.spec_from_file_location(full,path);m=importlib.util.module_from_spec(spec);sys.modules[full]=m;setattr(self.utils,name,m);spec.loader.exec_module(m);return m
    async def test_recorded_command_still_requires_exact_registered_command_snapshot_and_log_bytes(self):
        self.utils.__path__.insert(0,str(LIVE));m=self.load('agentic_gateway_delivery')
        queued=self.enqueue();packet=self.packet();row=self.g.claim_invocation(queued['invocation_id'],self.bridge_module.stable_run_id(queued['invocation_id']),gate=self.gate_value,db_path=self.path)
        sessions=self.load('agentic_gateway_sessions');root=Path(self.temp.name)/'evidence';root.mkdir();snapshot={'sha256':'a'*64}
        command=command_fixture();profile={'tests':{'registered':json.loads(command)},'test_acceptance_ids':{'registered':[packet['acceptance_tests'][0]['id']]}}
        observed={'test_id':'registered','exit_code':0,'command':command,'sandbox':'bubblewrap-no-network-sanitized-copy','repository_snapshot_sha256':snapshot['sha256'],'stdout':'Controlled original test receipt\n','stderr':''}
        for stream in ('stdout','stderr'):observed[stream+'_sha256']=hashlib.sha256(observed[stream].encode()).hexdigest()
        raw=self.bridge_module.canonical(observed).encode();(root/'recorded-log.json').write_bytes(raw)
        (root/'recorded-log.json').chmod(0o640)
        observed['evidence']={'uri':'artifact://recorded-log.json','sha256':hashlib.sha256(raw).hexdigest()}
        with sessions.connect(self.path)as db:
            db.execute('INSERT INTO governance_gateway_session VALUES(?,?,?,?,?,?,?,?)',('recorded-session',row['invocation_id'],'{}','{}','b'*64,'c'*64,'ADMITTED',0))
            db.execute("INSERT INTO governance_gateway_call VALUES(?,?,?,'repository.test','completed',?,0,0)",('recorded-session','recorded-call','d'*64,json.dumps(observed)))
        with patch.object(m,'artifact_directory',return_value=root):
            facts=m._tests(['recorded-call'],row,packet,profile,snapshot,{},self.path)
            self.assertEqual(facts['test_runs'][0]['command'],command);self.assertTrue(facts['evidence']);self.assertTrue(facts['test_results'])
            wrong=deepcopy(profile);wrong['tests']['registered']=['different-approved-command']
            with self.assertRaisesRegex(ValueError,'no longer approved'):m._tests(['recorded-call'],row,packet,wrong,snapshot,{},self.path)
            with self.assertRaisesRegex(ValueError,'exact current implementation snapshot'):m._tests(['recorded-call'],row,packet,profile,{'sha256':'e'*64},{},self.path)
            (root/'recorded-log.json').write_bytes(raw+b' changed')
            with self.assertRaisesRegex(ValueError,'log bytes changed'):m._tests(['recorded-call'],row,packet,profile,snapshot,{},self.path)

if __name__=='__main__':unittest.main()

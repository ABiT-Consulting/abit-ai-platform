"""Exact staged registry, native channel admission and acceptance hooks."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock,patch
SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE/'tests'))
from test_agentic_controls_events import Fixture,P,T
MODULES=SOURCE/'open_webui/utils'
NATIVE_CLIENT=SOURCE.parent/'deployment/linux/abit-ai/agentic-gateway/erp_channel_client.py'


def standalone(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module

class ExactChannelMethods(unittest.TestCase):
    def test_both_channel_sides_allow_only_exact_new_methods_and_no_human_configure_api(self):
        for index,path in enumerate([MODULES/'agentic_erp_channel.py',NATIVE_CLIENT]):
            module=standalone('staged_channel_'+str(index),path)
            for rpc in ['agentic_operations.get_controls_audit_receipt','agentic_operations.get_controls_audit_current',
                        'agentic_operations.get_controls_audit_for_qa','agentic_it_plan.get_it_routine_implementation_receipt']:
                value='/api/method/abit_ai_assistant.'+rpc
                self.assertEqual(value,module.validate_path('GET',value))
                with self.assertRaises(ValueError):module.validate_path('POST',value)
            value='/api/method/abit_ai_assistant.agentic_operations.record_controls_audit'
            self.assertEqual(value,module.validate_path('POST',value))
            with self.assertRaises(ValueError):module.validate_path('GET',value)
            for method,value in [('POST','/api/method/abit_ai_assistant.agentic_operations.configure_controls_audit'),
                ('POST','/api/method/abit_ai_assistant.agentic_operations.any_other_operation'),
                ('GET','/api/method/abit_ai_assistant.agentic_operations.get_controls_audit_for_qa?cmd=frappe.client.save')]:
                with self.assertRaises(ValueError):module.validate_path(method,value)

class SpecialistWiring(Fixture):
    def staged(self,name):
        module=standalone('open_webui.utils.'+name,MODULES/(name+'.py'));setattr(self.utils,name,module);return module

    def setUp(self):
        super().setUp()
        self.load('agentic_it_qa')
        for name in ['agentic_operations','agentic_operations_qa','agentic_it_routine_qa','agentic_specialist_qa','agentic_gateway_delivery','agentic_gateway_operations']:
            self.staged(name)
        self.operations=sys.modules['open_webui.utils.agentic_gateway_operations']
        self.facade=sys.modules['open_webui.utils.agentic_specialist_qa']
        self.delivery=sys.modules['open_webui.utils.agentic_gateway_delivery']
        self.executor=self.load('agentic_executor')

    async def test_registry_role_capabilities_and_catalogue_expose_fixed_targetless_shapes(self):
        ops=self.operations.registry('operations');qa=self.operations.registry('qa')
        self.assertIn('operations.audit_controls',ops);self.assertNotIn('operations.verify_original',ops)
        self.assertTrue(ops['operations.audit_controls'].requires_authority_callback)
        self.assertFalse(ops['operations.audit_controls'].read_only)
        for name in ['infrastructure.verify_original','operations.verify_original']:
            self.assertIn(name,qa);self.assertTrue(qa[name].requires_authority_callback)
        for role in ['developer','solution_architect','project_manager','it_manager']:
            self.assertFalse({'operations.audit_controls','operations.verify_original'}&set(self.operations.registry(role)))
        row=self.running('operations-catalogue-original',T,'operations')
        result=await self.operations.context({},row=row,packet=self.packet(T,'operations'),config={})
        self.assertEqual({},result['operations']['operations.audit_controls']['arguments'])
        self.assertIn('Unsupported business connectors',result['specialist_evidence_guidance'])

    async def test_operations_ready_cannot_use_a_model_claim_or_developer_delivery(self):
        row=self.running('operations-no-actual-receipt',T,'operations')
        with self.assertRaisesRegex(ValueError,'actual named connector'):
            await self.delivery.assert_gateway_result(row,self.packet(T,'operations'),{'outcome':'ready_for_review','evidence':[]}, {},self.path,bridge=self.bridge)

    async def test_non_it_non_ops_qa_still_requires_original_developer_pr(self):
        row=self.running('qa-no-original-delivery',T,'qa');packet=self.packet(T,'qa')
        with patch.object(self.executor,'implementation_source',return_value={'target_role':'developer'}):
            with self.assertRaisesRegex(ValueError,'exact original Developer delivery'):
                await self.delivery.assert_gateway_result(row,packet,{'outcome':'pass','evidence':[]},{},self.path,bridge=self.bridge)
        with patch.object(self.executor,'implementation_source',return_value={'target_role':'project_manager'}):
            self.assertFalse(await self.facade.assert_specialist_qa(row,packet,{}, {},self.path,bridge=self.bridge))

    async def test_infrastructure_route_selects_only_actual_it_typed_operation(self):
        routine=sys.modules['open_webui.utils.agentic_it_routine_qa'];physical=sys.modules['open_webui.utils.agentic_it_qa']
        args=dict(row={'target_role':'qa'},packet={},config={},bridge=self.bridge,db_path=self.path)
        for operation,module in [('inspect',routine),('cleanup_disposable',routine),('restart_service',physical)]:
            source={'target_role':'it_manager','work_packet':json.dumps({'tool_action':{'operation':operation}})}
            with patch.object(self.executor,'implementation_source',return_value=source),patch.object(module,'verify_original',new=AsyncMock(return_value={'selected':operation})) as handler:
                self.assertEqual({'selected':operation},await self.facade.verify_infrastructure({},**args));handler.assert_awaited_once()
        with patch.object(self.executor,'implementation_source',return_value={'target_role':'operations'}):
            with self.assertRaisesRegex(ValueError,'authoritative IT predecessor'):await self.facade.verify_infrastructure({},**args)

if __name__=='__main__':unittest.main()

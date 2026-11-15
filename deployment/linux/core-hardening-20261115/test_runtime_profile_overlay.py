"""Actual overlay callers preserve the deployed fixed-Project policy."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from test_agentic_controls_events import Fixture
from test_agentic_architecture_review import ArchitectureReview

class CurrentProjectPolicy(Fixture):
    async def test_only_fixed_project_profile_is_exposed_and_workspace_templates_stay_unavailable(self):
        operations=self.load('agentic_gateway_operations')
        row=self.enqueue()
        packet=self.packet()
        profile={'repository':packet['repository'],'environment':packet['environment'],
                 'workspace_root':'/srv/abit-dev-linux/fixture-owned-root',
                 'allowed_paths':['controlled'],'read_paths':['controlled'],'test_paths':['controlled'],
                 'tests':{'actual-test':['python3','-m','unittest']},
                 'test_acceptance_ids':{'actual-test':['REQ-ONE.acceptance']}}
        config={'agentic_repository_gateway':{'projects':{packet['project_id']:profile},
                 'templates':{'not-activated':{**profile,'enabled':True,
                               'workspace_base':'/srv/abit-dev-linux/not-activated'}}}}
        before=deepcopy(config)
        result=await operations.context({},row=row,packet=packet,config=config,db_path=self.path)
        self.assertEqual(result['repository_profile']['repository'],packet['repository'])
        self.assertEqual(result['repository_profile']['test_ids'],['actual-test'])
        self.assertNotIn('workspace_root',result['repository_profile'])
        self.assertNotIn('repository_registration',result)
        self.assertIn('constraints',result['operations']['artifact.write'])
        self.assertEqual(config,before)
        templates_only=deepcopy(config);templates_only['agentic_repository_gateway']['projects']={}
        unavailable=await operations.context({},row=row,packet=packet,config=templates_only,db_path=self.path)
        self.assertIsNone(unavailable['repository_profile']['repository'])
        self.assertEqual(unavailable['repository_profile']['test_ids'],[])
        with self.assertRaisesRegex(ValueError,'No server-owned repository/environment profile'):
            operations._workspace(row,packet,templates_only)

class PhaseAndCurrentProfile(ArchitectureReview):
    for _name in tuple(vars(ArchitectureReview)):
        if _name.startswith('test_'):
            locals()[_name]=None
    async def test_actual_phase_catalog_keeps_shared_contract_and_closing_pm_fence(self):
        operations=self.load('agentic_gateway_operations')
        before=deepcopy(self.packet_value)
        normal=await operations.context({},row=self.row,packet=self.packet_value,config={},db_path=self.path)
        self.assertIn('architecture-review.v1',normal['pm_phase_guidance'])
        self.assertIn('PLANNING PM RESULT CONTRACT:',normal['result_contract_guidance'])
        self.assertIn('constraints',normal['operations']['artifact.write'])
        self.assertNotIn('pm_review.write',normal['operations'])
        self.assertNotIn('repository_registration',normal)
        self.assertEqual(self.packet_value,before)
        atomic=await operations.context({},row=self.row,packet={**before,'atomic_adoption':{'adoption_id':'fixture'}},config={},db_path=self.path)
        self.assertIn('PM delegation:',atomic['pm_phase_guidance'])
        self.assertIn('ready_for_review',atomic['pm_phase_guidance'])
        self.assertNotIn('pm_review.write',atomic['operations'])
        qa=await operations.context({},row=self.row,packet={**before,'qa_review':{'bound':'fixture'}},config={},db_path=self.path)
        self.assertIn('QA coordination:',qa['pm_phase_guidance'])
        self.assertIn('qa_review_ack',qa['pm_phase_guidance'])
        self.assertIn('pm_review.write',qa['operations'])
        self.assertNotIn('PLANNING PM RESULT CONTRACT:',qa['result_contract_guidance'])
        self.assertEqual(self.packet_value,before)


class DeliveryFixedProjectPolicy(Fixture):
    def profiles(self, packet):
        fixed={'repository':packet['repository'],'environment':packet['environment'],
               'workspace_root':'/srv/abit-dev-linux/fixture-owned-root',
               'allowed_paths':['controlled'],'read_paths':['controlled'],'test_paths':['controlled'],
               'tests':{'actual-test':['python3','-m','unittest']},
               'test_acceptance_ids':{'actual-test':['REQ-ONE.acceptance']},
               'base_branch':'main','base_commit':'a'*40,
               'git_author':{'name':'Synthetic service','email':'machine@example.invalid'}}
        template={k:deepcopy(v) for k,v in fixed.items() if k!='workspace_root'}
        template.update(enabled=True,workspace_base='/srv/abit-dev-linux/fixture-template-root')
        return fixed,template

    async def test_fixed_delivery_profile_preserves_exact_registered_policy_and_role_boundary(self):
        delivery=self.load('agentic_gateway_delivery')
        row=self.enqueue();packet=self.packet()
        fixed,template=self.profiles(packet)
        config={'agentic_repository_gateway':{'projects':{packet['project_id']:fixed},
                                             'templates':{'excluded':template}}}
        before=deepcopy(config)
        injected={**packet,'workspace_root':'/etc','tests':{'injected':['false']}}
        self.assertEqual(delivery._repository_profile(row,injected,config),fixed)
        for role in ('solution_architect','project_manager','developer','qa'):
            self.assertEqual(delivery._repository_profile({**row,'target_role':role},packet,config),fixed)
        for role in ('operations','it_manager'):
            with self.assertRaises(delivery.CapabilityUnavailable):
                delivery._repository_profile({**row,'target_role':role},packet,config)
        self.assertEqual(config,before)

    async def test_bootstrap_denies_missing_foreign_and_template_only_profiles_before_remote_client(self):
        delivery=self.load('agentic_gateway_delivery')
        row=self.enqueue();packet=self.packet()
        fixed,template=self.profiles(packet)
        templates={'excluded':template}
        cases={
            'missing':{'projects':{}},
            'foreign':{'projects':{'PROJ-FOREIGN':fixed}},
            'template_only':{'projects':{},'templates':templates},
            'foreign_plus_template':{'projects':{'PROJ-FOREIGN':fixed},'templates':templates},
            'mismatched_repository':{'projects':{packet['project_id']:{**fixed,'repository':fixed['repository']+'-other'}},'templates':templates},
            'mismatched_environment':{'projects':{packet['project_id']:{**fixed,'environment':'foreign'}},'templates':templates},
            'explicit_null':{'projects':{packet['project_id']:None},'templates':templates},
        }
        with patch.object(delivery,'github_client',side_effect=AssertionError('Remote client must not be reached')) as remote:
            for name,gateway in cases.items():
                with self.subTest(name=name):
                    config={'agentic_repository_gateway':deepcopy(gateway)};before=deepcopy(config)
                    with self.assertRaises(delivery.CapabilityUnavailable):
                        await delivery.prepare_workspace({},row=row,packet=packet,config=config,db_path=self.path)
                    self.assertEqual(config,before)
            remote.assert_not_called()

if __name__=='__main__':unittest.main()

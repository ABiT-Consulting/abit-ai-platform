"""Offline checks of the four-file candidate against the installed helper cohort.

Only retained unittest fixtures run. ERP/relay boundaries remain controlled doubles;
DATA_DIR, governance/provider databases and evidence files stay in TemporaryDirectory.
No application startup, provider call, live ledger mutation or deployment occurs.
"""
from pathlib import Path
import importlib.abc
import importlib.machinery
import importlib.util
import json
import sys
import types
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

REPO = Path('/srv/abit-dev-linux/abit-ai-platform')
LIVE = Path(__file__).resolve().parent / 'snapshot/open_webui/utils'
CANDIDATE = Path(__file__).resolve().parent / 'candidate/open_webui/utils'
OVERRIDES = {'agentic_executor', 'agentic_result_store', 'agentic_gateway_contracts', 'agentic_blocker_review'}


class CandidateLoader(importlib.machinery.SourceFileLoader):
    def exec_module(self, module):
        super().exec_module(module)
        if module.__name__.endswith('.agentic_gateway_contracts'):
            # Deployment uses the installed schema directory; do not copy schemas.
            module.SCHEMAS = LIVE / 'agentic_contracts/schemas'


class CandidateFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('open_webui.utils.') and fullname.rsplit('.', 1)[-1] in OVERRIDES:
            file = CANDIDATE / (fullname.rsplit('.', 1)[-1] + '.py')
            return importlib.util.spec_from_file_location(fullname, file,
                loader=CandidateLoader(fullname, str(file)))
        return None


sys.path.insert(0, str(REPO / 'backend/tests'))
sys.meta_path.insert(0, CandidateFinder())
import test_agentic_executor_flow as flow
flow.UTILS = LIVE
names = [
    'test_proof_bound_legacy_developer_failure_publishes_blocker_without_qa_verdict',
    'test_failure_diagnostic_audit_requires_current_owned_run_packet_and_fence',
    'test_failure_proof_needs_same_original_failed_call_and_exact_evidence',
    'test_enabled_failure_routing_holds_only_pure_parse_rejection',
    'test_failure_proof_io_or_authority_error_is_not_contract_rejection',
    'test_invalid_developer_original_is_held_once_without_success_or_publication',
    'test_contract_rejection_preserves_stop_and_cancellation_reason',
    'test_contract_rejection_refuses_changed_identity_receipt_and_existing_result',
    'test_parse_rejection_is_typed_but_unrelated_validation_errors_stay_uncertain',
    'test_valid_developer_blocked_result_keeps_genuine_failing_test_evidence',
    'test_changed_delivery_without_matching_private_manifest_cannot_publish',
    'test_forged_artifact_hash_cannot_publish_ready_or_approval',
    'test_real_gateway_adapter_cannot_publish_model_only_developer_delivery',
    'test_restart_recovers_original_reply_and_same_run_without_model_replay',
    'test_stop_keeps_recovered_reply_as_evidence_without_erp_completion',
    'test_unknown_erp_result_ack_reads_receipt_without_model_or_callback_replay',
    'test_developer_then_separate_qa_pass_with_real_artifact_bytes_and_receipt',
]
suite = unittest.TestSuite(flow.ExecutorFlow(name) for name in names)

import test_agentic_controls_events as controlled
controlled.ROOT = LIVE

def fixture_load(self, name):
    full = 'open_webui.utils.' + name
    file = (CANDIDATE if name in OVERRIDES else LIVE) / (name + '.py')
    spec = importlib.util.spec_from_file_location(full, file,
        loader=CandidateLoader(full, str(file)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    setattr(self.utils, name, module)
    spec.loader.exec_module(module)
    return module

controlled.Fixture.load = fixture_load
import test_agentic_bound_dispatch_context as bound

class CandidateContext(bound.BoundDispatchContext):
    async def test_candidate_compact_dispatch_and_actual_context_share_developer_guidance(self):
        self.prepare_identity_packet('developer', 'PROJ-0197')
        before = deepcopy(self.packet)
        proof = await self.preflight()
        row = {**self.row, '_relay_scope_preflight': proof}
        automation = types.SimpleNamespace(id=row['agent_id'], user_id='owner-fixture')
        bridge = types.SimpleNamespace(erp=self.client, config={})
        with patch.object(self.execution, 'task_context', self.no_legacy_context), patch.object(
                self.f.relay, 'send_via_existing_relay', self.sender):
            result = await self.executor.DotAdapter(None).submit(
                row, self.packet, self.run_id, automation, bridge,
                before_submit=AsyncMock(return_value=proof))
        self.assertEqual(result['status'], 'delegated')
        self.assertEqual(len(self.sent), 1)
        envelope, payload = self.sent[0]
        prompt = envelope['task']['original_prompt']
        from open_webui.utils.agentic_gateway_contracts import developer_result_guidance
        guidance = developer_result_guidance('developer')
        self.assertEqual(prompt.count(guidance), 1)
        self.assertIn('operation=context.read', prompt)
        self.assertIn('IMMUTABLE GOVERNANCE CONTEXT REFERENCE:', prompt)
        self.assertNotIn('GOVERNANCE WORK PACKET\n', prompt)
        self.assertEqual(self.packet, before)
        self.assertEqual(payload['governance_scope']['scope']['run_id'], self.run_id)
        self.no_legacy_context.assert_not_awaited()
        operations = self.f.load('agentic_gateway_operations')
        response = await operations.context({}, row=self.row, packet=self.packet,
                                             config={}, db_path=self.f.path)
        self.assertEqual(response['result_contract_guidance'].count(guidance), 1)
        self.assertIn('Repository command IDs are operational checks, not acceptance IDs',
                      response['result_contract_guidance'])
        self.assertEqual(response['work_packet'], before)
        self.assertEqual(len(self.sent), 1)

suite.addTest(CandidateContext(
    'test_candidate_compact_dispatch_and_actual_context_share_developer_guidance'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({'candidate_tests': result.testsRun, 'failures': len(result.failures),
                  'errors': len(result.errors), 'skipped': len(result.skipped),
                  'successful': result.wasSuccessful(), 'deployment_performed': False}))
raise SystemExit(0 if result.wasSuccessful() else 1)

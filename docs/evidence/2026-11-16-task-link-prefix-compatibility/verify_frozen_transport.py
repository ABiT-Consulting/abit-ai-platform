"""Exercise only the frozen relay leaves with actual isolated governance fixtures.

No live services, credentials, browser, application startup or production writes.
Source-only planning guidance is intentionally outside this runtime increment.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import unittest

PACKAGE = Path(__file__).resolve().parent
SOURCE = PACKAGE.parents[2]
sys.path.insert(0, str(SOURCE / 'backend/tests'))
import test_agentic_controls_events as fixtures
import test_agentic_bound_dispatch_context as tests

original_load = fixtures.Fixture.load


def load(self, name):
    if name not in {'agentic_executor', 'erp_dot_execution', 'agentic_gateway_operations'}:
        return original_load(self, name)
    full = 'open_webui.utils.' + name
    leaf = PACKAGE / 'runtime/utils' / (name + '.py')
    if not leaf.is_file():
        leaf = PACKAGE.parent / '2026-11-16-compact-existing-relay/runtime/utils' / (name + '.py')
    spec = importlib.util.spec_from_file_location(full, leaf)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    setattr(self.utils, name, module)
    spec.loader.exec_module(module)
    return module


async def oversized(self):
    self.prepare_identity_packet('project_manager', 'PROJ-0197', oversized=True)
    before, handoff = deepcopy(self.packet), self.row['context']
    await self.capture_identity_dispatch()
    payload = self.sent[-1][1]
    for marker in ('SRS-PRIVATE-', 'BACKLOG-PRIVATE-', 'HANDOFF-PRIVATE-'):
        self.assertNotIn(marker, payload['message'])
    response = await self.f.load('agentic_gateway_operations').context(
        {}, row=self.row, packet=self.packet, config={}, db_path=self.f.path)
    self.assertEqual(response['work_packet'], before)
    self.assertEqual(response['handoff_context'], handoff)
    result = {'schema_version': 1, 'invocation_id': self.row['invocation_id'],
              'project_id': before['project_id'], 'task_id': before['task_id'],
              'task_revision': before['task_revision'], 'work_packet_digest': before['content_sha256'],
              'actor': before['binding'], 'outcome': 'ready_for_review'}
    subprocess.run(['node', str(PACKAGE / 'verify_actual_feed.cjs')],
                   input=json.dumps({'message': payload['message'], 'result': result}),
                   text=True, check=True, timeout=20)
    print(json.dumps({'frozen_relay_message_characters': len(payload['message']),
                      'full_packet_characters': len(json.dumps(before)),
                      'handoff_characters': len(handoff)}))


if __name__ == '__main__':
    fixtures.Fixture.load = load
    tests.BoundDispatchContext.test_frozen_oversized = oversized
    excluded = {
        'test_actual_architecture_phase_keeps_shared_guidance_and_exact_packet',
        'test_actual_guidance_matches_unchanged_rejection_of_task_url_outer_run_and_foreign_identity',
        'test_architect_guidance_binds_each_project_and_keeps_outer_run_and_source_packet',
        'test_context_read_exposes_same_packet_scoped_planning_pm_guidance',
        'test_oversized_srs_backlog_and_handoff_remain_complete_in_context_not_relay',
        'test_planning_pm_evidence_with_no_test_claims_passes_existing_strict_validator',
        'test_planning_pm_prompt_exposes_exact_result_mode_without_changing_packet',
    }
    selected = [name for name in unittest.defaultTestLoader.getTestCaseNames(tests.BoundDispatchContext)
                if name not in excluded]
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(
        tests.BoundDispatchContext(name) for name in selected))
    raise SystemExit(not result.wasSuccessful())

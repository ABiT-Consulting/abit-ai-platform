"""Backlog concurrency must not disable exact Task authorization checks."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'packet_context_bridge', Path(__file__).resolve().parents[1] / 'open_webui/utils/agentic_bridge.py'
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class PacketContextTests(unittest.TestCase):
    def packet(self, role='developer'):
        return {'project_id':'PROJ-1', 'task_id':'TASK-1', 'task_revision':'original-inputs',
                'binding':{'role':role, 'agent_id':'registered-original-agent'},
                'project_backlog':{'complete':True, 'context_sha256':'a'*64,
                                   'snapshot_sha256':'b'*64, 'tasks':[{'status':'Working'}]}}

    def test_planner_accepts_runtime_advance_with_unchanged_business_context(self):
        for role in ('project_manager', 'solution_architect'):
            original = self.packet(role); fresh = copy.deepcopy(original)
            fresh['project_backlog'].update(snapshot_sha256='c'*64, tasks=[{'status':'QA APPROVED'}])
            self.assertTrue(bridge.same_business_context(original, fresh))

    def test_planner_rejects_new_business_scope(self):
        original = self.packet('project_manager'); fresh = copy.deepcopy(original)
        fresh['project_backlog']['context_sha256'] = 'c'*64
        self.assertFalse(bridge.same_business_context(original, fresh))

    def test_unrelated_backlog_changes_do_not_lock_executor_tasks(self):
        for role in ('developer', 'qa', 'operations', 'it_manager'):
            original = self.packet(role); fresh = copy.deepcopy(original)
            fresh['project_backlog'].update(context_sha256='c'*64, tasks=[{'status':'QA APPROVED'}])
            self.assertTrue(bridge.same_business_context(original, fresh))
            fresh['task_revision'] = 'human-business-change'
            self.assertFalse(bridge.same_business_context(original, fresh))

    def test_missing_partial_or_invalid_snapshot_never_passes(self):
        original = self.packet()
        for invalid in (None, {}, {'complete':False,'context_sha256':'a'*64},
                        {'complete':True,'context_sha256':'arbitrary'}):
            fresh = copy.deepcopy(original); fresh['project_backlog'] = invalid
            self.assertFalse(bridge.same_business_context(original, fresh))

    def test_only_explicit_server_fields_may_be_ignored(self):
        original = self.packet(); fresh = copy.deepcopy(original)
        fresh['source_modified'] = 'new-technical-timestamp'
        self.assertFalse(bridge.same_business_context(original, fresh))
        self.assertTrue(bridge.same_business_context(original, fresh, {'source_modified'}))
        fresh['binding']['agent_id'] = 'another-agent'
        self.assertFalse(bridge.same_business_context(original, fresh, {'source_modified'}))

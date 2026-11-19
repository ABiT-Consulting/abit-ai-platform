import unittest
from unittest.mock import patch

import test_agentic_control as fixture
from test_agentic_control import Doc, c, fake, records, setUpModule, tearDownModule


class ShowstopperStatus(unittest.TestCase):
    setUp = fixture.ControlTests.setUp
    base = fixture.ControlTests.base
    packet = fixture.ControlTests.packet
    context = fixture.ControlTests.context

    def test_triage_stays_blocked_until_verified_children_allow_executor_start(self):
        self.task.status = 'Blocked'
        self.task.custom_agov_executor_role = 'solution_architect'
        self.task.custom_agov_project_anchor = 1
        self.task.custom_agov_dispatch_released = 1
        self.task.custom_agentic_managed = 1
        self.task.is_group = 1
        project = 'PROJ-1'
        source = self.task.custom_agov_active_run
        inputs = self.base()['task_revision']
        c._record('run_started', [project, source], project,
                  {'role': 'solution_architect', 'task_revision': inputs}, task=self.task.name)
        c._record('result', [project, source, 'run.blocked'], project,
                  {'type': 'run.blocked'}, task=self.task.name)
        pm = {**self.base(), 'type': 'run.started', 'event_id': 'pm-triage-event-0001',
              'role': 'project_manager', 'run_id': 'pm-triage-run-0001', 'previous_run_id': source}
        child = Doc(status='Open')

        def read_doc(doctype, name):
            return Doc(modified='project-version') if doctype == 'Project' else records[name]

        with self.context(), patch.object(fake, 'get_doc', side_effect=read_doc), patch.multiple(
            c, _execution_allowed=lambda *args: True, _ensure_role_assignment=lambda *args: None,
            _attachment_manifest=lambda *args: [], _project_pickup_adoption=lambda *args: False,
            _parent_dependency_rows=lambda *args: [child],
        ):
            self.assertEqual(c.receive_result(pm)['status'], 'Blocked')
            self.assertEqual(self.task.custom_agov_active_run, pm['run_id'])
            self.assertEqual(c.receive_result(pm)['status'], 'Blocked')
            self.assertIsNotNone(c._blocked_pm_predecessor(self.task))
            executor = {
                **self.base(), 'type': 'run.started', 'event_id': 'resumed-event-0001',
                'role': 'solution_architect', 'run_id': 'resumed-run-0001',
                'expected_modified': self.task.modified, 'previous_run_id': pm['run_id'],
            }
            with self.assertRaisesRegex(ValueError, 'Group waits for required child QA'):
                c.receive_result(executor)
            self.assertEqual(self.task.status, 'Blocked')
            child.status = 'QA APPROVED'
            self.assertEqual(c.receive_result(executor)['status'], 'Working')
            self.assertEqual(self.task.custom_agov_active_run, executor['run_id'])

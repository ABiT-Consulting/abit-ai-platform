"""Fail-closed Project hold / ai-projects assignment contract tests.

The authenticated ERP Project record, Project._assign, and OPEN Project ToDo
must all agree. These tests do not mutate ERP, ABIT.AI or real customer tasks.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
UTILS = ROOT / "open_webui/utils/agentic_project_policy.py"
ROUTER = ROOT / "open_webui/routers/agentic_governance.py"
spec = importlib.util.spec_from_file_location("agov_project_policy_test", UTILS)
policy = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(policy)

P = "PROJ-0091"
PM = "ai-projects@abitcons.com"
OTHER = "jamkamran@abitcons.com"


def project(status="Open", project_id=P, opted_in=True):
    return {
        "name": project_id,
        "status": status,
        "custom_agentic_governance_enabled": 1 if opted_in else 0,
        "custom_ai_project_manager_task_automation_enabled": 1,
    }


def assigned(project_id=P, user=PM, status="Open"):
    return {
        "name": "TODO-PM",
        "reference_type": "Project",
        "reference_name": project_id,
        "allocated_to": user,
        "status": status,
    }


class ProjectEligibilityPureTests(unittest.TestCase):
    def evaluate(self, status="Open", assignment=None, todos=None, project_id=P):
        if assignment is None:
            assignment = json.dumps([PM])
        if todos is None:
            todos = [assigned()]
        return policy.project_eligibility_reason(
            project(status),
            assignment,
            todos,
            project_id=project_id,
        )

    def test_open_project_with_exact_pm_assignment_passes(self):
        self.assertIsNone(self.evaluate())

    def test_multiple_assignees_including_pm_passes(self):
        self.assertIsNone(self.evaluate(assignment=json.dumps([OTHER, PM])))

    def test_held_project_never_runs_even_if_assigned(self):
        for status in ("Hold", "On Hold", "Paused", "Suspended", "Blocked"):
            with self.subTest(status=status):
                self.assertIn("on hold", self.evaluate(status=status))

    def test_closed_or_unknown_status_fails_closed(self):
        for status in ("Cancelled", "Completed", "", "Unknown", None):
            with self.subTest(status=status):
                self.assertIsNotNone(self.evaluate(status=status))

    def test_missing_pm_in_assign_field_blocks(self):
        self.assertIn("not assigned", self.evaluate(assignment=json.dumps([OTHER])))

    def test_malformed_assign_field_blocks(self):
        for payload in ("{wrong json", "[\"ai-projects@abitcons.com\"", "null", "{}", 9):
            with self.subTest(payload=payload):
                self.assertIsNotNone(self.evaluate(assignment=payload))

    def test_project_todo_closed_blocks(self):
        self.assertIn("no open", self.evaluate(todos=[assigned(status="Closed")]))

    def test_project_todo_missing_blocks(self):
        self.assertIn("no open", self.evaluate(todos=[]))

    def test_project_todo_other_assignee_blocks(self):
        self.assertIn("no open", self.evaluate(todos=[assigned(user=OTHER)]))

    def test_project_todo_other_project_blocks(self):
        self.assertIn("no open", self.evaluate(todos=[assigned(project_id="PROJ-0193")]))

    def test_project_identity_mismatch_blocks(self):
        self.assertIn("identity mismatch", self.evaluate(project_id="PROJ-0193"))

    def test_missing_exact_email_match_blocks(self):
        self.assertIn("not assigned", self.evaluate(assignment=json.dumps([PM + ".test"])))

    def test_project_object_invalid_blocks(self):
        self.assertIn(
            "identity mismatch", policy.project_eligibility_reason(None, json.dumps([PM]), [assigned()], project_id=P)
        )

    def test_no_todo_is_not_authorized_by_stale_assign(self):
        self.assertIsNotNone(self.evaluate(todos=[{"name": "old"}]))

    def test_reconciliation_eligible_status_is_not_exception(self):
        for state in policy.ACTIVE_STATUSES:
            self.assertIsNone(self.evaluate(status=state))


class ERPHTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class OfflineERPResponseError(Exception):
    pass


class FakeERPState:
    def __init__(self):
        self.item = project()
        self.assignment = json.dumps([PM])
        self.todos = [assigned()]
        self.fail = None
        self.called = []
        self.closed = False


class FakeERPClient:
    state = None

    def __init__(self, token_file):
        self.state = FakeERPClient.state
        self.client = types.SimpleNamespace(aclose=self.close)

    async def close(self):
        self.state.closed = True

    async def get(self, doctype, name):
        self.state.called.append(("get", doctype, name))
        if self.state.fail == "get":
            raise ConnectionError("ERP unavailable")
        if self.state.fail == 'erp_get':
            raise OfflineERPResponseError('ERP HTTP 404 DoesNotExistError: Task does not exist')
        return dict(self.state.item)

    async def request(self, method, path, **kwargs):
        self.state.called.append(("request", method, path))
        if self.state.fail == "request":
            raise ConnectionError("ERP unavailable")
        if self.state.fail == "erp":
            raise OfflineERPResponseError("ERP HTTP 417 TimestampMismatchError: Document was modified")
        return {"message": {"_assign": self.state.assignment}}

    async def rows(self, doctype, fields, filters):
        self.state.called.append(("rows", doctype))
        if self.state.fail == "todos":
            raise ConnectionError("ERP unavailable")
        for row in self.state.todos:
            yield dict(row)


def isolated_router_function(name):
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    fns = [
        node for node in tree.body if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    ]
    if len(fns) != 1:
        raise AssertionError(f"Expected one {name} function")
    node = fns[0]
    node.decorator_list = []
    node.returns = None
    for arg in node.args.args + node.args.kwonlyargs:
        arg.annotation = None
    return compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(ROUTER), "exec")


class ERPProjectGateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeERPState()
        FakeERPClient.state = self.state
        # Keep pure function in one isolated compilation environment, without
        # importing the production service or running Open WebUI startup.
        self.modname = "open_webui.utils.agentic_erp_runtime"
        self.old_module = sys.modules.get(self.modname)
        fake = types.ModuleType(self.modname)
        fake.governed_client = FakeERPClient
        sys.modules[self.modname] = fake
        self.statusmodname = 'open_webui.utils.erp_status_write'
        self.old_status_module = sys.modules.get(self.statusmodname)
        status = types.ModuleType(self.statusmodname)
        status.ERPResponseError = OfflineERPResponseError
        sys.modules[self.statusmodname] = status
        self.globals = {
            "HTTPException": ERPHTTPError,
            "PROJECT_ID": re.compile(r"^PROJ-[0-9A-Za-z-]{1,60}$"),
            "project_eligibility_reason": policy.project_eligibility_reason,
            "project_status_allowed": policy.project_status_allowed,
            "REQUIRED_PROJECT_MANAGER": PM,
        }
        exec(isolated_router_function("_erp_failure"), self.globals)
        exec(isolated_router_function("_erp_project"), self.globals)
        self.call = self.globals["_erp_project"]

    def tearDown(self):
        if self.old_status_module is None:
            sys.modules.pop(self.statusmodname, None)
        else:
            sys.modules[self.statusmodname] = self.old_status_module
        if self.old_module is None:
            sys.modules.pop(self.modname, None)
        else:
            sys.modules[self.modname] = self.old_module

    def check(self, expected_code, config=None):
        with self.assertRaises(ERPHTTPError) as context:
            asyncio.run(self.call(P, config or {"erp_token_file": "/not-used", "projects": {P: {}}}))
        self.assertEqual(context.exception.status_code, expected_code)
        self.assertTrue(self.state.closed)

    def test_well_assigned_open_project_accepted(self):
        result = asyncio.run(self.call(P, {"erp_token_file": "/not-used", "projects": {P: {}}}))
        self.assertEqual(result["name"], P)
        self.assertTrue(self.state.closed)
        self.assertEqual([x[0] for x in self.state.called], ["get", "request", "rows"])

    def test_upstream_erp_error_is_reported_with_its_actual_type(self):
        self.state.fail = 'erp'
        with self.assertRaises(ERPHTTPError) as context:
            asyncio.run(self.call(P, {'erp_token_file': '/not-used'}))
        self.assertEqual(context.exception.status_code, 503)
        self.assertIn('ERP HTTP 417 TimestampMismatchError', context.exception.detail)
        self.assertTrue(self.state.closed)

    def test_task_error_preserves_the_actual_sanitized_erp_detail(self):
        self.state.fail = 'erp_get'
        self.globals['TASK_ID'] = re.compile(r'^TASK-[A-Za-z0-9-]{1,60}$')
        exec(isolated_router_function('_erp_task'), self.globals)
        with self.assertRaises(ERPHTTPError) as context:
            asyncio.run(self.globals['_erp_task'](P, 'TASK-test-001', 'revision', {'erp_token_file': '/not-used'}))
        self.assertEqual(context.exception.status_code, 503)
        self.assertIn('ERP HTTP 404 DoesNotExistError', context.exception.detail)
        self.assertTrue(self.state.closed)

    def test_held_project_does_not_reach_assignment_query(self):
        self.state.item["status"] = "On Hold"
        self.check(409)
        self.assertEqual([x[0] for x in self.state.called], ["get"])

    def test_unassigned_legacy_pilot_does_not_bypass(self):
        self.state.assignment = json.dumps([OTHER])
        self.state.todos = [assigned(user=OTHER)]
        self.check(403)

    def test_stale_todo_instead_of_pm_blocks(self):
        self.state.todos = []
        self.check(403)

    def test_todo_without_assign_field_blocks(self):
        self.state.assignment = "[]"
        self.check(403)

    def test_project_without_optin_or_legacy_allowlist_blocked(self):
        self.state.item["custom_agentic_governance_enabled"] = 0
        self.check(403, config={"erp_token_file": "/not-used", "projects": {}})
        self.assertEqual([x[0] for x in self.state.called], ["get"])

    def test_disabled_checkbox_is_not_overridden_by_legacy_allowlist(self):
        self.state.item['custom_agentic_governance_enabled'] = 0
        self.check(403, config={'erp_token_file': '/not-used', 'projects': {P: {}}})

    def test_pm_task_automation_checkbox_disabled_or_missing_blocks(self):
        for value in (0, None, '0'):
            self.state.item['custom_ai_project_manager_task_automation_enabled'] = value
            self.check(403)

    def test_backend_erp_transport_error_fails_closed(self):
        self.state.fail = "request"
        self.check(503)

    def test_invalid_project_id_400(self):
        with self.assertRaises(ERPHTTPError) as ctx:
            asyncio.run(self.call("../PROJ-0091", {"erp_token_file": "/not-used", "projects": {}}))
        self.assertEqual(ctx.exception.status_code, 400)


class DeliveryAndManualRunGuards(unittest.TestCase):
    def test_source_manual_run_restricted_to_governance_api(self):
        source = (ROOT / "open_webui/routers/automations.py").read_text()
        self.assertIn("if mutation and (automation.meta or {}).get('governance_managed') is True:", source)
        for endpoint in (
            "update_automation_by_id",
            "toggle_automation_by_id",
            "run_automation_by_id",
            "delete_automation_by_id",
        ):
            body = source.split(f"async def {endpoint}(", 1)[1].split("\n\n################", 1)[0]
            self.assertIn("check_automation_access(automation, user, mutation=True)", body)


if __name__ == "__main__":
    unittest.main()

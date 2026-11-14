"""Surgical source adapters preserve live scheduling logic and deny early starts."""

import asyncio
import ast
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CandidateAdmission(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        self.m = load('candidate_maintenance', ROOT / 'backend/open_webui/utils/agentic_maintenance.py')
        original = self.m.path
        self.m.path = lambda data_dir=None: original(self.root if data_dir is None else data_dir)
        self.builder = load('candidate_builder', ROOT / 'scripts/prepare_agentic_candidate.py')

    def execute(self, text, namespace):
        text = text.replace('from open_webui.utils import agentic_maintenance as _agov_maintenance\n', '')
        namespace['_agov_maintenance'] = self.m
        exec(text, namespace)
        return namespace

    async def test_manual_Play_guard_runs_before_background_factory_and_returns503(self):
        original = '''async def run_automation_by_id(request,automation):
    asyncio.create_task(execute_automation(request.app, automation))
    return 'accepted'
'''
        patched = self.builder.manual_admission(original)
        self.assertEqual(patched, self.builder.manual_admission(patched))

        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code

        calls = []

        async def execute(*args):
            calls.append(True)

        ns = self.execute(patched, {'asyncio': asyncio, 'HTTPException': HTTPException, 'execute_automation': execute})
        self.m.control('close', expected_epoch=0, command_id='close', actor='operator', db_path=self.m.path())
        with self.assertRaises(HTTPException) as caught:
            await ns['run_automation_by_id'](
                types.SimpleNamespace(app=None), types.SimpleNamespace(id='automation-one')
            )
        self.assertEqual(503, caught.exception.status_code)
        self.assertEqual([], calls)

    async def test_scheduler_blocks_before_claim_but_preserves_timer_calendar_work(self):
        original = '''async def scheduler_poll(app,Automations,events):
    events.append('timer')
    try:
        batch = await Automations.claim_due(1)
        for automation in batch:
            asyncio.create_task(execute_automation(app, automation))
    except Exception:
        pass
    events.append('calendar')
'''
        patched = self.builder.scheduler_admission(original)
        self.assertEqual(patched, self.builder.scheduler_admission(patched))
        calls = []

        async def claim(*args):
            calls.append('claim')
            return []

        async def execute(*args):
            calls.append('provider')

        ns = self.execute(patched, {'asyncio': asyncio, 'execute_automation': execute})
        self.m.control('close', expected_epoch=0, command_id='close-scheduler', actor='operator', db_path=self.m.path())
        events = []
        await ns['scheduler_poll'](None, types.SimpleNamespace(claim_due=claim), events)
        self.assertEqual([], calls)
        self.assertEqual(['timer', 'calendar'], events)

    async def test_class_QA_claim_and_ERP_adapters_preserve_exact_signatures_bodies(self):
        original = '''from __future__ import annotations
class ReviewStore:
    def claim(self, task, run, binding):
        """Existing native claim body."""
        return (task, run, binding)
async def dispatch_event(resolver, task, revision, creator):
    return await creator(resolver,task,revision)
'''
        patched = self.builder.admission_decorator(original, 'claim', 'qa_claim', 'ReviewStore')
        patched = self.builder.admission_decorator(patched, 'dispatch_event', 'legacy_erp')
        self.assertEqual(patched, self.builder.admission_decorator(patched, 'claim', 'qa_claim', 'ReviewStore'))
        before = ast.parse(original)
        after = ast.parse(patched)

        def functions(tree):
            return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

        for name, node in functions(before).items():
            changed = functions(after)[name]
            self.assertEqual(ast.dump(node.args), ast.dump(changed.args))
            self.assertEqual([ast.dump(n) for n in node.body], [ast.dump(n) for n in changed.body])
        with self.assertRaises(ValueError):
            self.builder.admission_decorator(original, 'missing', 'legacy_erp')

    async def test_only_exact_before_send_branches_receive_rejection_receipts(self):
        original = '''async def execute_automation(app,automation):
    user = await get_user()
    if not user:
        await _record_run(automation.id,'error',error='User not found')
        return
    if user.role not in ('user','admin'):
        error = 'Owner no longer permitted to run automations'
        await _record_run(automation.id,'error',error=error)
        return
    chat = await create_chat()
    if not chat:
        error = 'Failed to create chat'
        await _record_run(automation.id,'error',error=error)
        return
    try:
        await provider()
    except Exception as error:
        await _record_run(automation.id,'error',error=error)
'''
        patched = self.builder.pre_submission_receipts(original)
        self.assertEqual(patched, self.builder.pre_submission_receipts(patched))
        calls = [n for n in ast.walk(ast.parse(patched)) if isinstance(n, ast.Call)]
        self.assertEqual(
            3, sum(isinstance(n.func, ast.Attribute) and n.func.attr == 'record_pre_submission' for n in calls)
        )
        self.assertEqual(1, sum(isinstance(n.func, ast.Name) and n.func.id == '_record_run' for n in calls))
        with self.assertRaises(ValueError):
            self.builder.pre_submission_receipts(original.replace('if not user:', 'if changed_user_gate:'))
        primary = '''async def run_primary_dot(app,automation,user,scheduled_run_id=None):
    try:
        folder = await lookup()
        if folder is None:
            await _record_run(automation.id,'deferred',scheduled_run_id=scheduled_run_id)
            return True
    except (ValueError,PermissionError,KeyError):
        await _record_run(automation.id,'deferred',scheduled_run_id=scheduled_run_id)
        return True
    await provider()
    await _record_run(automation.id,'deferred',scheduled_run_id=scheduled_run_id)
'''
        patched = self.builder.pre_submission_receipts(primary, primary=True)
        self.assertEqual(patched, self.builder.pre_submission_receipts(patched, primary=True))
        self.assertEqual(2, patched.count('_agov_maintenance.record_pre_submission('))


if __name__ == '__main__':
    unittest.main()

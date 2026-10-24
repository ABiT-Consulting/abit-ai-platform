"""Exercise the real project modal against malformed replies and UI refresh failures.

Only local isolated API fixtures are used. No live login or project is changed.
"""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import tempfile
import threading
import unittest

from playwright.sync_api import sync_playwright, expect
from test_chatgpt_project_ui_browser import BUILDER, Quiet, ROOT


class ProjectRetryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = tempfile.TemporaryDirectory(prefix='aibit-project-retry-')
        builder = BUILDER.replace(
            'onSubmit:async data=>window.submitted.push(data)',
            "onSubmit:async data=>{if(window.failProjectRefresh)throw Error('Simulated sidebar refresh failure');window.submitted.push(data)}"
        )
        if builder == BUILDER:
            raise RuntimeError('Fixture callback anchor changed')
        subprocess.run(['node', '-e', builder, cls.output.name], cwd=ROOT, check=True, timeout=90)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=cls.output.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True, executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe'
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.output.cleanup()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.calls = []
        self.payload = {'ok': True, 'folder_id': 'fixture-folder'}
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        def route_api(route):
            self.calls.append(route.request.post_data_json)
            route.fulfill(status=200, content_type='application/json', body=json.dumps(self.payload))
        self.page.route('**/api/v1/chatgpt-relay/projects/create', route_api)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/')
        self.page.wait_for_function('window.fixtureReady')
        self.page.evaluate("window.render('project')")
        self.page.locator('#folder-name').fill('Retry fixture project')
        self.page.get_by_role('textbox', name='Project Instructions').fill('Retry fixture instructions')

    def submit(self):
        self.page.get_by_role('button', name='Create project', exact=True).click()

    def test_malformed_http_success_never_closes_form_or_clears_retry_key(self):
        for payload in ({}, {'ok': False}, {'ok': True}, {'ok': True, 'folder_id': ''},
                        {'ok': True, 'folder_id': 7}):
            with self.subTest(payload=payload):
                self.payload = payload
                self.submit()
                expect(self.page.get_by_role('alert')).to_contain_text('not confirmed')
                expect(self.page.get_by_role('dialog')).to_be_visible()
                self.assertEqual(self.page.evaluate('window.submitted.length'), 0)
        self.assertEqual(len({call['request_id'] for call in self.calls}), 1)
        self.assertFalse(self.errors)

    def test_refresh_failure_retries_same_creation_id_then_releases_key(self):
        self.page.evaluate('window.failProjectRefresh=true')
        self.submit()
        expect(self.page.get_by_role('alert')).to_contain_text('sidebar refresh failure')
        first_id = self.calls[-1]['request_id']
        self.page.evaluate('window.failProjectRefresh=false')
        self.submit()
        self.page.wait_for_function('window.submitted.length===1')
        self.assertEqual(self.calls[-1]['request_id'], first_id)
        self.assertEqual(self.page.evaluate(
            "Object.keys(sessionStorage).filter(key=>key.startsWith('aibit-project-create:')).length"), 0)
        self.assertFalse(self.errors)

    def test_successful_creation_closes_form_and_releases_key(self):
        self.submit()
        self.page.wait_for_function('window.submitted.length===1')
        expect(self.page.get_by_role('dialog')).to_have_count(0)
        self.assertEqual(self.page.evaluate(
            "Object.keys(sessionStorage).filter(key=>key.startsWith('aibit-project-create:')).length"), 0)
        self.assertFalse(self.errors)


if __name__ == '__main__':
    unittest.main()

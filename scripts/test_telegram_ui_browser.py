"""Browser checks for the actual Telegram Svelte page and API client.

Run from the canonical checkout with Python + Playwright installed. This uses a
temporary static fixture and mocked HTTP responses; it never starts Open WebUI,
uses a real Telegram token, contacts Telegram, or changes application records.
"""
from __future__ import annotations

import argparse
import base64
import functools
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect


COMPILER = r"""
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const [repo, output] = process.argv.slice(2);
const require = createRequire(path.join(repo, 'package.json'));
const esbuild = require('esbuild');
const {compile} = require('svelte/compiler');
const pagePath = path.join(repo, 'src/routes/(app)/dot/telegram/+page.svelte');
await esbuild.build({
  absWorkingDir: repo,
  stdin: {
    contents: `import {mount,unmount,tick} from 'svelte';
      import Page from ${JSON.stringify(pagePath)};
      import {user} from '$lib/stores';
      const app = mount(Page, {target:document.getElementById('app')});
      window.telegramFixture = {unmount:()=>unmount(app),tick,setUser:(value)=>user.set(value)};`,
    resolveDir: repo,
    sourcefile: 'telegram-fixture-entry.ts',
    loader: 'ts'
  },
  outfile: path.join(output, 'bundle.js'),
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: 'es2020',
  conditions: ['browser'],
  nodePaths: [path.join(repo, 'node_modules')],
  logLevel: 'warning',
  plugins: [{
    name: 'telegram-fixture',
    setup(build) {
      build.onResolve({filter:/^\$lib\/apis\/telegram$/},()=>({path:path.join(repo,'src/lib/apis/telegram/index.ts')}));
      build.onResolve({filter:/^\$lib\/(stores|constants)$/},args=>({path:args.path,namespace:'fixture'}));
      build.onResolve({filter:/^\$app\/(stores|navigation)$/},args=>({path:args.path,namespace:'fixture'}));
      build.onLoad({filter:/.*/,namespace:'fixture'},args=>{
        if(args.path === '$lib/stores') return {
          contents:`import {writable} from 'svelte/store';
            export const user=writable({id:'fixture-owner',role:window.fixtureRole,name:'Fixture User'});`,
          loader:'js',resolveDir:repo
        };
        if(args.path === '$lib/constants') return {contents:`export const WEBUI_API_BASE_URL='/api/v1';`,loader:'js'};
        if(args.path === '$app/stores') return {contents:`import {writable} from 'svelte/store';export const page=writable({url:new URL(location.href)});`,loader:'js',resolveDir:repo};
        return {contents:`export const goto=async()=>{};`,loader:'js'};
      });
      build.onLoad({filter:/\.svelte$/},args=>{
        const result=compile(fs.readFileSync(args.path,'utf8'),{
          filename:args.path,generate:'client',css:'injected',dev:true
        });
        return {contents:result.js.code,loader:'js',resolveDir:path.dirname(args.path)};
      });
    }
  }]
});
"""


def initial_status(configured=True):
    return {
        "configured": configured,
        "bot_username": "fixture_abit_bot" if configured else None,
        "public_url": "https://configured.abit.example" if configured else None,
        "linked": False,
        "binding": None,
        "pairing": None,
        "dot_connected": False,
        "dot_subscription_active": False,
        "mcp_url": "https://configured.abit.example/api/v1/telegram/mcp" if configured else None,
    }


class ApiFixture:
    def __init__(self, configured=True):
        self.status = initial_status(configured)
        self.messages = []
        self.calls = []
        self.configs = []
        self.conflicting_webhook = False
        self.pairing_lifetime = 300
        self.pair_number = 0

    def count(self, method, suffix):
        return sum(1 for item in self.calls if item[0] == method and item[1] == suffix)

    def handle(self, route):
        request = route.request
        suffix = urlparse(request.url).path.removeprefix('/api/v1/telegram')
        method = request.method
        self.calls.append((method, suffix))
        code = 200
        body = self.status
        if method == 'GET' and suffix == '/status':
            pass
        elif method == 'GET' and suffix == '/messages':
            body = {"messages": self.messages}
        elif method == 'POST' and suffix == '/config':
            config = request.post_data_json
            self.configs.append(config)
            if self.conflicting_webhook and not config.get('replace_existing_webhook'):
                code = 409
                body = {"detail": "This bot already has a webhook registered with another service."}
            else:
                self.status.update(
                    configured=True,
                    bot_username='fixture_abit_bot',
                    public_url=config['public_url'],
                    mcp_url=config['public_url'].rstrip('/') + '/api/v1/telegram/mcp',
                )
        elif method == 'POST' and suffix == '/pairings':
            self.pair_number += 1
            pairing = {
                'id': f'fixture-pair-{self.pair_number}',
                'status': 'pending',
                'expires_at': int(time.time()) + self.pairing_lifetime,
                'telegram_username': None,
                'display_name': None,
            }
            self.status['pairing'] = pairing
            # A local SVG fixture exercises image handling; backend tests decode
            # and verify the actual generated QR payload separately.
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="224" height="224"><rect width="224" height="224" fill="white"/><path d="M20 20h60v60H20zM144 20h60v60h-60zM20 144h60v60H20z" fill="black"/></svg>'
            body = {
                **pairing,
                'deep_link': f'https://t.me/fixture_abit_bot?start=fixture_{self.pair_number}',
                'qr_data_url': 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode(),
            }
        elif method == 'POST' and suffix.endswith('/confirm'):
            pairing = self.status['pairing']
            if not pairing or pairing['status'] != 'claimed':
                code, body = 409, {'detail': 'No claimed pairing is available.'}
            else:
                self.status['linked'] = True
                self.status['binding'] = {
                    'id': 'fixture-binding',
                    'telegram_username': pairing['telegram_username'],
                    'display_name': pairing['display_name'],
                    'linked_at': int(time.time()),
                }
                self.status['pairing'] = None
        elif method == 'DELETE' and suffix.startswith('/pairings/'):
            self.status['pairing'] = None
        elif method == 'DELETE' and suffix == '/link':
            self.status.update(linked=False, binding=None, pairing=None)
        elif method == 'POST' and suffix == '/disconnect-dot':
            self.status.update(dot_connected=False, dot_subscription_active=False)
        else:
            code, body = 404, {'detail': f'Unexpected fixture route: {method} {suffix}'}
        route.fulfill(status=code, content_type='application/json', body=json.dumps(body))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def refresh(page):
    button = page.get_by_role('button', name='Refresh Telegram connection')
    expect(button).to_be_enabled()
    button.click()
    expect(button).to_be_enabled()


def test_link_confirm_disconnect(page, fixture):
    page.get_by_role('button', name='Generate QR code', exact=True).click()
    expect(page.get_by_alt_text('Scan this QR code to open your Telegram bot and start account linking')).to_be_visible()
    expect(page.get_by_role('link', name='Open Telegram', exact=True)).to_have_attribute('href', 'https://t.me/fixture_abit_bot?start=fixture_1')
    assert fixture.count('POST', '/pairings') == 1
    fixture.status['pairing'].update(status='claimed', display_name='Imran Fixture', telegram_username='imran_fixture')
    refresh(page)
    expect(page.get_by_text('Confirm this account', exact=True)).to_be_visible()
    expect(page.get_by_text('@imran_fixture', exact=True)).to_be_visible()
    assert not fixture.status['linked'], 'Scanning must not activate the binding before confirmation.'
    page.get_by_role('button', name='Confirm linking', exact=True).click()
    expect(page.get_by_role('button', name='Disconnect Telegram', exact=True)).to_be_visible()
    assert fixture.status['linked']
    page.get_by_role('button', name='Disconnect Telegram', exact=True).click()
    expect(page.get_by_role('heading', name='Disconnect your Telegram account?')).to_be_visible()
    assert fixture.count('DELETE', '/link') == 0
    page.get_by_role('button', name='Keep connected', exact=True).click()
    assert fixture.status['linked']
    page.get_by_role('button', name='Disconnect Telegram', exact=True).click()
    page.get_by_role('button', name='Disconnect', exact=True).click()
    expect(page.get_by_role('button', name='Generate QR code', exact=True)).to_be_visible()
    assert fixture.count('DELETE', '/link') == 1


def test_expiry_and_cancel(page, fixture):
    fixture.pairing_lifetime = 2
    page.get_by_role('button', name='Generate QR code', exact=True).click()
    expect(page.get_by_role('link', name='Open Telegram', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Generate new QR code', exact=True)).to_be_visible(timeout=6000)
    expect(page.get_by_role('link', name='Open Telegram', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Confirm linking', exact=True)).to_have_count(0)
    fixture.pairing_lifetime = 300
    page.get_by_role('button', name='Generate new QR code', exact=True).click()
    expect(page.get_by_role('link', name='Open Telegram', exact=True)).to_be_visible()
    page.get_by_role('button', name='Cancel', exact=True).click()
    expect(page.get_by_role('button', name='Generate QR code', exact=True)).to_be_visible()
    assert fixture.count('DELETE', '/pairings/fixture-pair-2') == 1
    assert fixture.status['pairing'] is None


def test_manage_bot_url(page, fixture):
    expect(page.get_by_label('Bot token', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Manage bot', exact=True).click()
    expect(page.get_by_label('Application URL', exact=True)).to_have_value('https://configured.abit.example')
    page.get_by_label('Bot token', exact=True).fill('123456:fixture-rotation-token')
    page.get_by_label('Application URL', exact=True).fill('https://updated.abit.example')
    refresh(page)
    expect(page.get_by_label('Application URL', exact=True)).to_have_value('https://updated.abit.example')
    page.get_by_role('button', name='Save bot settings', exact=True).click()
    expect(page.get_by_role('button', name='Manage bot', exact=True)).to_be_visible()
    expect(page.get_by_label('Bot token', exact=True)).to_have_count(0)
    assert fixture.configs[-1]['public_url'] == 'https://updated.abit.example'
    page.get_by_role('button', name='Manage bot', exact=True).click()
    expect(page.get_by_label('Bot token', exact=True)).to_have_value('')
    expect(page.get_by_label('Application URL', exact=True)).to_have_value('https://updated.abit.example')


def test_webhook_takeover(page, fixture):
    fixture.conflicting_webhook = True
    page.get_by_label('Bot token', exact=True).fill('123456:fixture-setup-token')
    page.get_by_label('Application URL', exact=True).fill('https://configured.abit.example')
    page.get_by_role('button', name='Connect bot', exact=True).click()
    checkbox = page.get_by_role('checkbox', name='This bot is connected to another service. Replace that connection with this application.')
    expect(checkbox).to_be_visible()
    expect(checkbox).not_to_be_checked()
    expect(page.get_by_role('button', name='Connect bot', exact=True)).to_be_disabled()
    assert len(fixture.configs) == 1 and fixture.configs[0]['replace_existing_webhook'] is False
    checkbox.check()
    page.get_by_role('button', name='Connect bot', exact=True).click()
    expect(page.get_by_role('button', name='Manage bot', exact=True)).to_be_visible()
    assert len(fixture.configs) == 2 and fixture.configs[1]['replace_existing_webhook'] is True


def test_nonadmin_setup(page, fixture):
    expect(page.get_by_label('Bot token', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Manage bot', exact=True)).to_have_count(0)
    expect(page.get_by_text('An administrator needs to connect the application’s Telegram bot first.', exact=False)).to_be_visible()
    expect(page.get_by_role('button', name='Generate QR code', exact=True)).to_be_disabled()
    assert not fixture.configs


def test_dot_transcript_mobile(page, fixture):
    fixture.status['dot_connected'] = True
    fixture.messages = [{
        'id': 'fixture-message', 'text': '<img src=x onerror="window.fixtureXss=true">',
        'created_at': int(time.time()), 'status': 'delivered',
        'response_text': '<script>window.fixtureXss=true</script>', 'error': None,
    }]
    refresh(page)
    expect(page.get_by_text('Authorized', exact=True)).to_be_visible()
    expect(page.get_by_text('Subscription active', exact=True)).to_have_count(0)
    expect(page.locator('article').get_by_text(fixture.messages[0]['text'], exact=True)).to_be_visible()
    expect(page.locator('article img, article script')).to_have_count(0)
    assert page.evaluate('window.fixtureXss === undefined')
    fixture.status['dot_subscription_active'] = True
    refresh(page)
    expect(page.get_by_text('Subscription active', exact=True)).to_be_visible()
    page.set_viewport_size({'width': 375, 'height': 812})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'Mobile layout overflows horizontally.'
    page.get_by_role('button', name='Disconnect dot connector', exact=True).click()
    expect(page.get_by_role('heading', name='Revoke dot connector access?')).to_be_visible()
    assert fixture.count('POST', '/disconnect-dot') == 0
    page.get_by_role('button', name='Disconnect', exact=True).click()
    expect(page.get_by_text('Not connected', exact=True)).to_be_visible()
    assert fixture.count('POST', '/disconnect-dot') == 1


def test_polling_cleanup(page, fixture):
    page.get_by_role('button', name='Generate QR code', exact=True).click()
    expect(page.get_by_role('link', name='Open Telegram', exact=True)).to_be_visible()
    page.evaluate('window.telegramFixture.unmount()')
    count = fixture.count('GET', '/status')
    page.wait_for_timeout(3400)
    assert fixture.count('GET', '/status') == count, 'Polling continued after component destruction.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = repo / 'src/routes/(app)/dot/telegram/+page.svelte'
    if not source.is_file():
        raise SystemExit(f'Canonical Telegram page is missing: {source}')
    with tempfile.TemporaryDirectory(prefix='abit-telegram-ui-') as temporary:
        work = Path(temporary)
        compiler = work / 'compile.mjs'
        compiler.write_text(COMPILER, encoding='utf-8')
        build = subprocess.run(['node', str(compiler), str(repo), str(work)], cwd=repo, capture_output=True, text=True, timeout=120)
        if build.returncode:
            raise RuntimeError('Actual Svelte/API fixture compilation failed:\n' + build.stderr[-12000:])
        # Reuse the built application styles when available, including Tailwind.
        css_files = sorted((repo / 'build/_app/immutable/assets').glob('*.css'))
        css = '\n'.join(path.read_text(encoding='utf-8') for path in css_files)
        # Preflight also makes the fixture deterministic before the first build.
        css += '\nhtml,body{margin:0;width:100%;height:100%;font-family:Arial,sans-serif}*{box-sizing:border-box}#app{width:100%;height:100vh}input,textarea{font:inherit}button{cursor:pointer}'
        (work / 'styles.css').write_text(css, encoding='utf-8')
        (work / 'index.html').write_text('''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="app"></div><script>window.fixtureRole=new URLSearchParams(location.search).get('role')||'admin';localStorage.setItem('token','fixture-only-token');</script><script src="/bundle.js"></script></body></html>''', encoding='utf-8')
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(work)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f'http://127.0.0.1:{server.server_address[1]}'
        cases = [
            ('QR claim confirmation and unlink', test_link_confirm_disconnect, True, 'user'),
            ('QR expiry and cancellation', test_expiry_and_cancel, True, 'user'),
            ('Admin rotation and configured URL', test_manage_bot_url, True, 'admin'),
            ('Explicit existing-webhook replacement', test_webhook_takeover, False, 'admin'),
            ('Non-admin setup restrictions', test_nonadmin_setup, False, 'user'),
            ('Dot status, escaped transcript, mobile layout', test_dot_transcript_mobile, True, 'user'),
            ('Unmount cancels polling', test_polling_cleanup, True, 'user'),
        ]
        results = []
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Exception:
                    chrome = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
                    if not chrome.is_file():
                        raise
                    browser = playwright.chromium.launch(headless=True, executable_path=str(chrome))
                try:
                    for name, test, configured, role in cases:
                        context = browser.new_context(viewport={'width': 1280, 'height': 960})
                        page = context.new_page()
                        fixture = ApiFixture(configured)
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.route('**/api/v1/telegram/**', fixture.handle)
                        try:
                            page.goto(base_url + '/?role=' + role)
                            expect(page.get_by_role('heading', name='Telegram', exact=True)).to_be_visible()
                            expect(page.get_by_role('button', name='Refresh Telegram connection')).to_be_enabled()
                            test(page, fixture)
                            assert not errors, 'Browser errors: ' + repr(errors)
                            results.append({'case': name, 'passed': True})
                        except Exception as error:
                            results.append({'case': name, 'passed': False, 'error': str(error), 'traceback': traceback.format_exc()})
                            if args.artifacts:
                                args.artifacts.mkdir(parents=True, exist_ok=True)
                                page.screenshot(path=str(args.artifacts / (str(len(results)) + '-failure.png')), full_page=True)
                        finally:
                            context.close()
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        report = {'compiled_actual_source': True, 'production_css_files': len(css_files), 'passed': sum(item['passed'] for item in results), 'total': len(results), 'results': results}
        print(json.dumps(report, indent=2))
        if args.artifacts:
            args.artifacts.mkdir(parents=True, exist_ok=True)
            (args.artifacts / 'telegram-ui-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return 0 if all(item['passed'] for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

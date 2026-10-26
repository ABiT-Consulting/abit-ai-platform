"""Browser regression for the embedded ChatGPT relay sandbox.

Compiles the actual Svelte components and mocks only the authenticated
ABiT backend API. No live relay, account, or ChatGPT requests are performed.
"""
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BUILDER = r"""
const fs = require('node:fs');
const path = require('node:path');
const { build } = require('esbuild');
const { compile } = require('svelte/compiler');
(async () => {
  const out = process.argv[1];
  const result = await build({
    stdin: {
      contents: `
        import { mount, unmount, tick } from 'svelte';
        import Link from './src/lib/components/chat/Messages/ResponseMessage/ChatGPTConversationLink.svelte';
        import { safeChatGPTConversationUrl } from './src/lib/utils/chatgptConversation.ts';
        let instance;
        window.renderLink = async (url, chatId = 'chat123') => {
          if (instance) await unmount(instance);
          const target = document.getElementById('fixture');
          target.replaceChildren();
          instance = mount(Link, { target, props: { url, chatId } });
          await tick();
        };
        window.safeChatGPTConversationUrl = safeChatGPTConversationUrl;
        window.fixtureReady = true;
      `,
      resolveDir: process.cwd(),
      sourcefile: 'relay-sandbox-fixture.js',
    },
    bundle: true,
    platform: 'browser',
    format: 'iife',
    define: { APP_VERSION: '"0.11.1"', APP_BUILD_HASH: '"test-fixture"' },
    conditions: ['browser'],
    alias: { '$lib': path.join(process.cwd(), 'src/lib') },
    write: false,
    plugins: [{
      name: 'real-svelte-component',
      setup(b) {
        b.onLoad({ filter: /\.svelte$/ }, args => {
          const output = compile(fs.readFileSync(args.path, 'utf8'), {
            filename: args.path, generate: 'client', css: 'injected'
          });
          return { contents: output.js.code, loader: 'js', resolveDir: path.dirname(args.path) };
        });
      },
    }],
  });
  fs.writeFileSync(path.join(out, 'fixture.js'), result.outputFiles[0].text);
  fs.writeFileSync(path.join(out, 'index.html'),
    '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0}.fixed{position:fixed}.inset-0{inset:0}.flex{display:flex}.flex-col{flex-direction:column}.min-w-0{min-width:0}.overflow-hidden{overflow:hidden}.overflow-auto{overflow:auto}.h-full{height:100%}.w-full{width:100%}.block{display:block}img{max-width:100%}</style></head><body><main id="fixture"></main><script src="/fixture.js"></script></body></html>');
})().catch(error => { console.error(error); process.exit(1); });
"""

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix='abit-sandbox-ui-') as output:
        subprocess.run(['node', '-e', BUILDER, output], cwd=ROOT, check=True, timeout=75)
        server = ThreadingHTTPServer(('127.0.0.1', 0),
            partial(QuietHandler, directory=output))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception:
                    chrome = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
                    browser = p.chromium.launch(headless=True, executable_path=str(chrome))
                context = browser.new_context(viewport={'width': 1440, 'height': 920})
                page = context.new_page()
                errors = []
                pops = []
                calls = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('popup', lambda child: pops.append(child))
                # Valid JPEG encoding is needed to test pixel coordinate mapping.
                from PIL import Image
                from io import BytesIO
                picture = BytesIO()
                Image.new('RGB', (1280, 720), (45, 70, 100)).save(picture, format='JPEG')
                frame = 'data:image/jpeg;base64,' + base64.b64encode(picture.getvalue()).decode()
                active = {'unavailable': False}
                def mock_api(route):
                    body = json.loads(route.request.post_data or '{}')
                    operation = route.request.url.rsplit('/', 1)[-1]
                    calls.append((operation, body))
                    assert route.request.headers.get('authorization') == 'Bearer sandbox-fixture'
                    assert body['chat_id'] == 'chat123'
                    if active['unavailable']:
                        route.fulfill(status=409, content_type='application/json',
                                      body=json.dumps({'detail': 'The relay is showing another conversation.'}))
                    elif operation == 'view':
                        route.fulfill(status=200, content_type='application/json',
                                      body=json.dumps({'ok':True,'image':frame,'width':1280,'height':720}))
                    else:
                        route.fulfill(status=200, content_type='application/json',
                                      body=json.dumps({'ok':True}))
                page.route('**/api/v1/chatgpt-relay/browser/**', mock_api)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                try:
                    page.wait_for_function('window.fixtureReady === true', timeout=5000)
                except Exception:
                    print('UI_STARTUP_ERRORS', errors, 'PAGE', page.content()[:800])
                    raise
                page.evaluate("localStorage.token = 'sandbox-fixture'")
                general = 'https://chatgpt.com/c/test-conversation-12345'
                project = 'https://chatgpt.com/g/g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-slug/c/test-conversation-12345'
                for label, url in [('general', general), ('project', project)]:
                    page.evaluate('value => window.renderLink(value)', url)
                    trigger = page.get_by_test_id('open-chatgpt-conversation')
                    assert trigger.is_enabled()
                    assert trigger.get_attribute('type') == 'button'
                    trigger.click()
                    page.get_by_role('dialog', name='ChatGPT relay browser sandbox').wait_for()
                    page.get_by_role('img', name='Current screen of the existing ChatGPT relay tab').wait_for()
                    assert calls[-2][0] == 'open' and calls[-1][0] == 'view', calls[-4:]
                    assert calls[-1][1] == {'chat_id': 'chat123'}
                    assert not pops and page.url.startswith('http://127.0.0.1:')
                    passed.append(label + '_same_page_live_relay_view')
                    screen = page.get_by_role('button', name='Interactive relay browser screen')
                    screen.click(position={'x': 100, 'y': 100})
                    page.wait_for_function("window.__sandboxNoop !== undefined || true")
                    page.wait_for_timeout(400)
                    assert any(kind == 'input' and data.get('kind') == 'click' and 0 <= data['x'] <= 1280 for kind, data in calls)
                    page.get_by_label('Type or paste into the selected field in ChatGPT').fill('revised prompt')
                    page.get_by_role('button', name='Type', exact=True).click()
                    page.wait_for_timeout(500)
                    assert any(kind == 'input' and data.get('kind') == 'text' and data.get('text') == 'revised prompt' for kind, data in calls)
                    page.get_by_role('button', name='Enter', exact=True).click()
                    page.wait_for_timeout(400)
                    assert any(kind == 'input' and data.get('kind') == 'key' and data.get('key') == 'Enter' for kind, data in calls)
                    passed.append(label + '_click_type_and_approve')
                    page.get_by_test_id('close-relay-sandbox').click()
                    assert page.get_by_role('dialog').count() == 0
                for invalid in [None, 'javascript:alert(1)', 'https://evil.example/c/test12345', 'https://chatgpt.com/']:
                    page.evaluate('value => window.renderLink(value)', invalid)
                    assert page.get_by_test_id('open-chatgpt-conversation').is_disabled()
                passed.append('untrusted_links_never_enable_browser')
                page.evaluate('value => window.renderLink(value)', general + '?secret=not-for-browser#x')
                assert page.get_by_test_id('open-chatgpt-conversation').is_enabled()
                assert page.get_by_role('link').count() == 0
                passed.append('no_chatgpt_href_or_new_tab')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_test_id('open-chatgpt-conversation').click()
                page.get_by_role('dialog').wait_for()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not pops
                passed.append('mobile_panel_fits_viewport')
                page.get_by_test_id('close-relay-sandbox').click()
                assert not errors, errors
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print(json.dumps({'browser_checks_passed': len(passed), 'checks': passed, 'live_requests': 0}))

if __name__ == '__main__':
    main()

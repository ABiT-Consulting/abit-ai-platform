"""Compile and browser-test the real conversation-link component in isolation.

Run from the canonical repository with Python + Playwright installed. The fixture
uses a fresh headless browser and intercepts ChatGPT requests: no live chat,
account, message, server deployment, or production database is touched.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
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
        window.renderLink = async (url) => {
          if (instance) await unmount(instance);
          const target = document.getElementById('fixture');
          target.replaceChildren();
          instance = mount(Link, { target, props: { url } });
          await tick();
        };
        window.safeChatGPTConversationUrl = safeChatGPTConversationUrl;
        window.fixtureReady = true;
      `,
      resolveDir: process.cwd(),
      sourcefile: 'conversation-link-fixture.js',
    },
    bundle: true,
    platform: 'browser',
    format: 'iife',
    conditions: ['browser'],
    alias: { '$lib': path.join(process.cwd(), 'src/lib') },
    write: false,
    plugins: [{
      name: 'real-svelte-component',
      setup(b) {
        b.onLoad({ filter: /\.svelte$/ }, args => {
          const output = compile(fs.readFileSync(args.path, 'utf8'), {
            filename: args.path, generate: 'client', css: 'injected',
          });
          return { contents: output.js.code, loader: 'js', resolveDir: path.dirname(args.path) };
        });
      },
    }],
  });
  fs.writeFileSync(path.join(out, 'fixture.js'), result.outputFiles[0].text);
  fs.writeFileSync(path.join(out, 'index.html'), '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><main id="fixture"></main><script src="/fixture.js"></script></body></html>');
})().catch(error => { console.error(error); process.exit(1); });
"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix='aibit-link-test-') as output:
        subprocess.run(['node', '-e', BUILDER, output], cwd=ROOT, check=True, timeout=60)
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=output))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception:
                    chrome = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
                    if not chrome.exists():
                        raise
                    browser = p.chromium.launch(headless=True, executable_path=str(chrome))
                context = browser.new_context()
                # Navigation is observed in an actual separate tab, but never
                # reaches the real ChatGPT site or the relay's browser session.
                context.route('https://chatgpt.com/**', lambda route: route.fulfill(
                    status=200, content_type='text/html', body='<html><body>Verified test destination</body></html>'
                ))
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_function('window.fixtureReady === true')
                general = 'https://chatgpt.com/c/test-conversation-12345'
                project = 'https://chatgpt.com/g/g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-project/c/test-conversation-12345'
                for label, url in [('general', general), ('project', project)]:
                    page.evaluate('value => window.renderLink(value)', url)
                    link = page.get_by_role('link', name='Open in ChatGPT (new tab)')
                    assert link.get_attribute('href') == url
                    assert link.get_attribute('target') == '_blank'
                    assert set(link.get_attribute('rel').split()) >= {'noopener', 'noreferrer'}
                    with context.expect_page() as opened:
                        link.click()
                    new_tab = opened.value
                    new_tab.wait_for_load_state()
                    assert new_tab.url == url
                    assert new_tab.evaluate('window.opener === null')
                    assert page.url.startswith('http://127.0.0.1:')
                    new_tab.close()
                    passed.append(label + '_opens_exact_chat_in_separate_tab')
                for invalid in [None, 'javascript:alert(1)', 'https://evil.example/c/test12345', 'https://chatgpt.com/']:
                    page.evaluate('value => window.renderLink(value)', invalid)
                    assert page.get_by_role('button', name='Open in ChatGPT').is_disabled()
                    assert page.get_by_role('link').count() == 0
                passed.append('missing_or_untrusted_link_is_disabled')
                bad_urls = [
                    'https://user:pass@chatgpt.com/c/test12345',
                    'https://chatgpt.com:444/c/test12345',
                    'https://chatgpt.com.evil.example/c/test12345',
                    'http://chatgpt.com/c/test12345',
                    'https://chatgpt.com//c/test12345',
                ]
                for url in bad_urls:
                    assert page.evaluate('value => window.safeChatGPTConversationUrl(value)', url) is None
                passed.append('untrusted_origin_and_credentials_rejected')
                page.evaluate('value => window.renderLink(value)', general + '?token=not-public#fragment')
                assert page.get_by_role('link').get_attribute('href') == general
                passed.append('query_and_fragment_are_not_exposed')
                page.reload()
                page.wait_for_function('window.fixtureReady === true')
                page.evaluate('value => window.renderLink(value)', general)
                assert page.get_by_role('link').get_attribute('href') == general
                passed.append('restored_link_renders_after_refresh')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.get_by_role('link').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                passed.append('mobile_button_visible_without_horizontal_overflow')
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

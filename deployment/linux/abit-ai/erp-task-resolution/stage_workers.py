"""Assemble worker sources in staging. This script never modifies live files."""
import argparse
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent

RELAY = r"""
async function recoverErpRelay(row, stop, acknowledgeStop) {
  const target = (await cdpTargets()).find(t => t.id === row.target && isChatGptPage(t));
  if (!target || await aibitAccountFingerprint(target).catch(() => '') !== row.account) return {};
  const session = await new CdpSession(target).open();
  try {
    const url = await session.location();
    if (parseProjectId(url) !== row.project || !parseConversationId(url)) return {};
    if (!stop) {
      const tail = await readConversationTail(session, url);
      return {findings: verifiedCurrentReply(tail, row.prompt, '', true)};
    }
    const expression = `(() => {
      const user = [...document.querySelectorAll('[data-message-author-role="user"]')].pop();
      const marker = ${JSON.stringify('[ABIT-RUN:' + row.id + ']')};
      const owned = location.href === ${JSON.stringify(url)} &&
        String(user?.innerText || '').trim().startsWith(marker);
      if (!owned) return {owned: false};
      const button = document.querySelector('[data-testid="stop-button"]');
      return {owned, generating: Boolean(button), ready: document.readyState === 'complete'};
    })()`;
    let settled = 0;
    let clicked = Boolean(row.stopClicked);
    for (let i = 0; i < 8; i++) {
      const check = await session.evaluate(expression);
      if (!check?.owned) return {};
      if (check.generating && !clicked) {
        clicked = await session.evaluate(`(() => {
          const check = ${expression};
          const button = document.querySelector('[data-testid="stop-button"]');
          if (!check.owned || !button) return false;
          button.click();
          return true;
        })()`);
        if (clicked) acknowledgeStop();
      }
      settled = check.ready && !check.generating ? settled + 1 : 0;
      if (settled >= 3) {
        // Require an actual Stop interaction or an independently verified
        // terminal reply. A missing spinner alone is not stop confirmation.
        if (clicked) return {confirmed: true};
        const tail = await readConversationTail(session, url);
        const findings = verifiedCurrentReply(tail, row.prompt, '', true);
        return findings ? {findings} : {};
      }
      await sleep(350);
    }
    return {};
  } finally { session.close(); }
}

const erpRelay = process.env.ABIT_ERP_RELAY_STATE_DIR
  ? require('./erp_task_relay.cjs').makeErpRelay({
    root: process.env.ABIT_ERP_RELAY_STATE_DIR,
    reserve: row => {
      try { return reserveRelayTurn({thread_id: 'aibit:erp:' + row.id}); }
      catch (err) { if ([423, 503].includes(err.statusCode)) return null; throw err; }
    },
    preflight: async body => {
      const account = await aibitAccountFingerprint().catch(() => '');
      return {ready: account === body.account,
        error: 'ChatGPT Browser Relay is unavailable or its account is signed out'};
    },
    run: async (row, lifecycle) => {
      const target = await createIsolatedThreadTarget('erp_' + row.id);
      lifecycle.target(target.id);
      lifecycle.guard();
      const result = await relayMessage({message: row.prompt, chatgpt_project_id: row.project,
        account_id: row.account},
        lifecycle.progress, target, lifecycle);
      if (!result.success || result.account_id !== row.account ||
          result.project.id !== row.project || !result.conversation_id)
        throw new Error('Could not verify the project reply');
      return result.response;
    },
    recover: recoverErpRelay,
    cleanup: closeChromeTarget,
  }) : null;

"""


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Expected one occurrence of {old!r}')
    return source.replace(old, new)


def stage_relay(original, output):
    src = original.read_text()
    src = replace(src, 'async function relayMessage(body, onProgress, target) {',
                  'async function relayMessage(body, onProgress, target, lifecycle = null) {')
    src = replace(src, 'await submitPrompt(session, message, progress);',
                  'await submitPrompt(session, message, progress, lifecycle);')
    src = replace(src, 'async function submitPrompt(session, message, onProgress) {',
                  'async function submitPrompt(session, message, onProgress, lifecycle = null) {')
    src = replace(src, '  session.submissionAttempted = true;',
                  '  lifecycle?.sending();\n  session.submissionAttempted = true;')
    src = replace(src, '    if (formSubmitted === false) {',
                  '    if (formSubmitted === false) {\n      lifecycle?.guard();')
    src = replace(src, 'while (onProgress || Date.now() - start < RESPONSE_TIMEOUT_MS) {',
                  'while (onProgress || Date.now() - start < RESPONSE_TIMEOUT_MS) {\n    lifecycle?.guard();')
    src = replace(src, 'const server = http.createServer(async (req, res) => {',
                  RELAY + 'const server = http.createServer(async (req, res) => {')
    src = replace(src, "    if (req.method === 'POST' && path === '/development/register') {", """    if (req.method === 'POST' && erpRelay && /^\\/erp-task\\/(preflight|observe|stop)$/.test(path)) {
      return json(res, 200, await erpRelay(path.split('/').pop(), await readJson(req)));
    }
    if (req.method === 'POST' && path === '/development/register') {""")
    output.write_text(src)


def stage_cli(original, output):
    src = original.read_text()
    src = replace(src, "        if self.path != '/jobs':", """        if self.path in ('/erp-task/preflight', '/erp-task/observe', '/erp-task/stop') and self.server.erp_jobs:
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 512_000:
                    return self.send(413, {'error': 'Invalid ERP task request length'})
                self.connection.settimeout(8)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Expected an object')
                return self.send(200, self.server.erp_jobs.handle(self.path.rsplit('/', 1)[-1], payload))
            except ValueError as exc:
                return self.send(200, {'ready': False, 'status': 'failed', 'error': str(exc)[:240]})
            except (OSError, subprocess.SubprocessError, RuntimeError):
                return self.send(503, {'error': 'Linux worker unavailable; retry observation'})
        if self.path != '/jobs':""")
    src = replace(src, '    server.worker = Worker(config)', """    server.worker = Worker(config)
    server.erp_jobs = None
    if config.get('erp_task_config'):
        from erp_task_cli import CodingJobs
        server.erp_jobs = CodingJobs(config['erp_task_config'], config['model'])""")
    output.write_text(src)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relay-source', required=True, type=Path)
    parser.add_argument('--cli-source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700)
    relay = args.output / 'relay'
    cli = args.output / 'cli'
    # Copy the exact Linux runtime dependencies beside their patched entry.
    relay.mkdir()
    cli.mkdir()
    for module in args.relay_source.parent.glob('*.cjs'):
        if module != args.relay_source:
            shutil.copyfile(module, relay / module.name)
    stage_relay(args.relay_source, relay / args.relay_source.name)
    stage_cli(args.cli_source, cli / args.cli_source.name)
    shutil.copyfile(HERE / 'erp_task_relay.cjs', relay / 'erp_task_relay.cjs')
    shutil.copyfile(HERE / 'erp_task_cli.py', cli / 'erp_task_cli.py')


if __name__ == '__main__':
    main()

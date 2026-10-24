'use strict';
// Exercise the real relay observer with a fake CDP session. No browser or sends.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'deployment/windows/aibit-relay/aibit_relay.cjs'), 'utf8');
const start = source.indexOf('async function submitPrompt(');
const end = source.indexOf('\nasync function ensureComposerWithRecovery(', start);
assert(start >= 0 && end > start);
const implementation = source.slice(start, end);

function fixture({ url, delayed = false, remainsActive = false }) {
  let clock = 100000;
  let locations = 0;
  let evaluations = 0;
  let snapshots = 0;
  let submissions = 0;
  const events = [];
  const session = {
    location: async () => (++locations === 1 || (delayed && locations === 2)) ? 'https://chatgpt.com/' : url,
    evaluate: async () => {
      evaluations += 1;
      if (evaluations === 1) return { ok: true, submitted: false, send: null };
      if (evaluations === 2) { submissions += 1; return true; }
      return { status: 200 };
    },
    call: async () => { throw Error('No fallback keyboard/mouse submit is expected'); },
  };
  const ctx = {
    Date: class extends Date { static now() { return clock; } },
    RESPONSE_TIMEOUT_MS: remainsActive ? 2500 : 10000,
    stableComposerBaseline: async () => ({ count: 0, users: 0, last: '', lastAssistantKey: '' }),
    sleep: async milliseconds => { clock += milliseconds; },
    waitForSubmission: async () => true,
    rateLimitOverlay: async () => ({ active: false }),
    parseConversationId: value => (String(value).match(/\/c\/([^/?#]+)/) || [])[1] || '',
    composerSnapshot: async () => {
      snapshots += 1;
      if (remainsActive || snapshots === 1) return { count: 0, last: '', generating: true };
      return { count: 1, last: 'Verified reply', lastAssistantKey: 'new-reply', complete: true, generating: false };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(implementation + '\nglobalThis.submit = submitPrompt;', ctx);
  return { events, run: () => ctx.submit(session, 'test only', event => events.push(event)),
           submissionCount: () => submissions };
}

test('general chat link is available on submission before any assistant text', async () => {
  const url = 'https://chatgpt.com/c/test-general-conversation';
  const f = fixture({ url });
  assert.equal(await f.run(), 'Verified reply');
  assert.equal(f.events[0].state, 'message_submitted');
  assert.equal(f.events[0].conversation_url, url);
  assert.equal(f.submissionCount(), 1);
});

test('delayed project URL appears while the assistant is still thinking', async () => {
  const url = 'https://chatgpt.com/g/g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-test/c/test-project-conversation';
  const f = fixture({ url, delayed: true });
  await f.run();
  const link = f.events.findIndex(e => e.state === 'waiting_for_response' && e.conversation_url === url);
  const text = f.events.findIndex(e => e.response);
  assert(link >= 0 && text > link);
  assert.equal(f.submissionCount(), 1);
});

test('active Stop timeout still exposes the conversation without resending', async () => {
  const url = 'https://chatgpt.com/c/test-long-running-conversation';
  const f = fixture({ url, delayed: true, remainsActive: true });
  await assert.rejects(f.run(), error => error.statusCode === 504 && /active Stop control/.test(error.message));
  assert(f.events.some(event => event.conversation_url === url));
  assert(!f.events.some(event => event.response));
  assert.equal(f.submissionCount(), 1);
});

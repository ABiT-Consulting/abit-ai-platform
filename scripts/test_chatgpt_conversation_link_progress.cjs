'use strict';
// Exercise the real relay observer with a fake CDP session. No browser or sends.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { verifiedCurrentReply } = require('../deployment/windows/aibit-relay/conversation_readback.cjs');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'deployment/windows/aibit-relay/aibit_relay.cjs'), 'utf8');
const start = source.indexOf('async function submitPrompt(');
const end = source.indexOf('\nasync function ensureComposerWithRecovery(', start);
assert(start >= 0 && end > start);
const implementation = source.slice(start, end);

function fixture({ url, delayed = false, remainsActive = false, domInvisible = false,
                   existing = false, apiTail = { status: 200 }, longMissing = false,
                   baselineTail = { status: 200, userId: 'previous-user' } }) {
  let clock = 100000;
  let locations = 0;
  let evaluations = 0;
  let snapshots = 0;
  let submissions = 0;
  let readbacks = 0;
  const events = [];
  const session = {
    location: async () => {
      locations++;
      if (existing) return url;
      return (locations === 1 || (delayed && locations === 2)) ? 'https://chatgpt.com/' : url;
    },
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
    RESPONSE_TIMEOUT_MS: remainsActive ? 2500 : (longMissing ? 160000 : domInvisible ? 16000 : 10000),
    stableComposerBaseline: async () => ({ count: 0, users: 0, last: '', lastAssistantKey: '' }),
    sleep: async milliseconds => { clock += milliseconds; },
    waitForSubmission: async () => true,
    rateLimitOverlay: async () => ({ active: false }),
    parseConversationId: value => (String(value).match(/\/c\/([^/?#]+)/) || [])[1] || '',
    parseProjectId: value => (String(value).match(/\/g\/(g-p-[a-z0-9_-]+)/i) || [])[1] || '',
    readConversationTail: async () => {
      readbacks++;
      if (existing && readbacks === 1) return baselineTail;
      return apiTail;
    },
    verifiedCurrentReply,
    composerSnapshot: async () => {
      snapshots++;
      if (domInvisible) return { count: 0, last: '', generating: false };
      if (remainsActive || snapshots === 1) return { count: 0, last: '', generating: true };
      return { count: 1, last: 'Verified reply', lastAssistantKey: 'new-reply', complete: true, generating: false };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(implementation + '\nglobalThis.submit = submitPrompt;', ctx);
  return {
    events, run: () => ctx.submit(session, 'test only', event => events.push(event)),
    submissionCount: () => submissions, readbacks: () => readbacks,
  };
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

const completedTail = {
  status: 200, leafRole: 'assistant', userId: 'new-user', userText: 'test only',
  assistants: [{ recipient: 'all', hidden: false, status: 'finished_successfully',
                 endTurn: true, text: 'Readback verified reply' }],
};

test('API fallback completes a fresh request when the DOM contains no assistant', async () => {
  const url = 'https://chatgpt.com/c/new-thread';
  const f = fixture({ url, domInvisible: true, apiTail: completedTail });
  assert.equal(await f.run(), 'Readback verified reply');
  assert(f.readbacks() >= 1);
  assert.equal(f.submissionCount(), 1);
  assert.equal(f.events.filter(event => event.response).at(-1).response, 'Readback verified reply');
});

test('API fallback completes a followup only when the user ID advanced', async () => {
  const url = 'https://chatgpt.com/c/existing-thread';
  const f = fixture({ url, existing: true, domInvisible: true, apiTail: completedTail });
  assert.equal(await f.run(), 'Readback verified reply');
  assert(f.readbacks() >= 2);
  assert.equal(f.submissionCount(), 1);
});

test('stale user ID cannot result in an old response being returned', async () => {
  const url = 'https://chatgpt.com/c/existing-thread';
  const f = fixture({ url, existing: true, domInvisible: true,
    apiTail: { ...completedTail, userId: 'previous-user' } });
  await assert.rejects(f.run(), error => error.statusCode === 504);
  assert.equal(f.submissionCount(), 1);
  assert(!f.events.some(event => event.response));
});

test('incomplete upstream response is not returned, and the prompt is not replayed', async () => {
  const url = 'https://chatgpt.com/c/new-thread';
  const f = fixture({ url, domInvisible: true,
    apiTail: { ...completedTail, assistants: [{ ...completedTail.assistants[0], endTurn: false }] } });
  await assert.rejects(f.run(), error => error.statusCode === 504);
  assert.equal(f.submissionCount(), 1);
  assert(!f.events.some(event => event.response));
});


test('HTTP 429 stops further conversation readback requests', async () => {
  const f = fixture({ url: 'https://chatgpt.com/c/new-thread', domInvisible: true,
                      longMissing: true, apiTail: { status: 429 } });
  await assert.rejects(f.run(), error => error.statusCode === 504 && /HTTP 429/.test(error.message));
  assert.equal(f.readbacks(), 1);
  assert.equal(f.submissionCount(), 1);
});

test('pre-submit HTTP 429 on follow-up avoids all additional API requests', async () => {
  const f = fixture({ url: 'https://chatgpt.com/c/existing-thread', existing: true,
                      domInvisible: true, baselineTail: { status: 429 } });
  await assert.rejects(f.run(), error => error.statusCode === 504 && /HTTP 429/.test(error.message));
  assert.equal(f.readbacks(), 1);
  assert.equal(f.submissionCount(), 1);
});

test('missing DOM response is checked a bounded number of times', async () => {
  const f = fixture({ url: 'https://chatgpt.com/c/new-thread', domInvisible: true,
                      longMissing: true, apiTail: { status: 200 } });
  await assert.rejects(f.run(), error => error.statusCode === 504);
  assert.equal(f.readbacks(), 3); // 20s, 85s and one final read at timeout
  assert.equal(f.submissionCount(), 1);
});

test('a tab that switched projects cannot return the wrong conversation API reply', async () => {
  const f = fixture({
    url: 'https://chatgpt.com/g/g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-test/c/changed-project',
    domInvisible: true, apiTail: completedTail,
  });
  await assert.rejects(f.run(), error => error.statusCode === 504);
  assert.equal(f.readbacks(), 0);
  assert.equal(f.submissionCount(), 1);
});

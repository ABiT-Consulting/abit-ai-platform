'use strict';
const assert = require('node:assert/strict');
const { createBrowserSandbox } = require('../deployment/windows/aibit-relay/browser_sandbox.cjs');

const ACCOUNT = 'a'.repeat(64);
const CONVERSATION = 'https://chatgpt.com/c/conversation123';
const OTHER = 'https://chatgpt.com/c/unrelated456';
let current = CONVERSATION;
let fingerprint = ACCOUNT;
let busy = false;
let relinking = false;
let ownedActiveTurn = true;
let switchDuringScreenshot = false;
let target = { id: 'dedicated-tab' };
let opened = 0;
let navigations = [];
let commands = [];
let closes = 0;
let locks = 0;

const service = createBrowserSandbox({
  getTarget: async () => target,
  createSession: async () => ({
    location: async () => current,
    evaluate: async () => ({ width: 1280, height: 720 }),
    call: async (method, params) => {
      commands.push({ method, params });
      if (method === 'Page.captureScreenshot') {
        if (switchDuringScreenshot) current = OTHER;
        return { data: '/9j/2Q==' };
      }
      return {};
    },
    navigate: async (url) => { navigations.push(url); current = url; },
    close: () => { closes += 1; }
  }),
  accountFingerprint: async () => fingerprint,
  withLock: async fn => { locks++; return fn(); },
  isBusy: () => busy,
  isRelinking: () => relinking,
  isActiveFor: () => ownedActiveTurn,
});
const payload = (url = CONVERSATION, extra = {}) =>
  ({ conversation_url: url, account_id: ACCOUNT, ...extra });
async function denied(call, status) {
  await assert.rejects(call, error => {
    assert.equal(error.statusCode, status, String(error));
    return true;
  });
}

(async () => {
  // A live view opens the existing *exact* relay tab: no new CDP target.
  busy = true;
  assert.deepEqual(await service.open(payload()), { ok: true, already_open: true });
  const shot = await service.view(payload());
  assert.equal(shot.image, 'data:image/jpeg;base64,/9j/2Q==');
  assert.equal(shot.width, 1280);
  assert.equal(shot.height, 720);
  assert.equal(locks, 0);
  await service.input(payload(CONVERSATION, { kind: 'click', x: 300, y: 400 }));
  await service.input(payload(CONVERSATION, { kind: 'text', text: 'corrected prompt' }));
  await service.input(payload(CONVERSATION, { kind: 'key', key: 'Ctrl+A' }));
  await service.input(payload(CONVERSATION, { kind: 'key', key: 'Enter' }));
  await service.input(payload(CONVERSATION, { kind: 'scroll', x: 250, y: 200, direction: -1 }));
  assert(commands.some(c => c.method === 'Input.insertText' && c.params.text === 'corrected prompt'));
  assert(commands.some(c => c.method === 'Input.dispatchMouseEvent' && c.params.type === 'mousePressed'));
  assert(commands.some(c => c.method === 'Input.dispatchMouseEvent' && c.params.type === 'mouseWheel' && c.params.deltaY < 0));
  assert.equal(navigations.length, 0);
  // A concurrent relay send from another owner/chat must never be visible.
  ownedActiveTurn = false;
  await denied(() => service.view(payload()), 423);
  await denied(() => service.input(payload(CONVERSATION, { kind: 'key', key: 'Enter' })), 423);
  ownedActiveTurn = true;
  // A frame captured just as ChatGPT switches chats must be discarded.
  switchDuringScreenshot = true;
  await denied(() => service.view(payload()), 409);
  switchDuringScreenshot = false;
  current = CONVERSATION;

  // No other conversation is ever captured or clicked (even in same account).
  current = OTHER;
  const before = commands.length;
  await denied(() => service.view(payload()), 409);
  await denied(() => service.input(payload(CONVERSATION, { kind: 'click', x: 1, y: 1 })), 409);
  assert.equal(commands.length, before);
  await denied(() => service.open(payload()), 423);
  assert.equal(navigations.length, 0);

  // The user can explicitly reopen their verified conversation only when idle.
  busy = false;
  const openedResult = await service.open(payload());
  assert.equal(openedResult.already_open, false);
  assert.equal(current, CONVERSATION);
  assert.equal(locks, 1);
  assert.deepEqual(navigations, [CONVERSATION]);

  // A project slug change may not disclose a different project/conversation.
  const expected = payload('https://chatgpt.com/g/g-p-' + 'b'.repeat(32) + '-old/c/conversation123');
  current = 'https://chatgpt.com/g/g-p-' + 'b'.repeat(32) + '-new/c/conversation123';
  assert((await service.view(expected)).ok);
  current = 'https://chatgpt.com/g/g-p-' + 'c'.repeat(32) + '-new/c/conversation123';
  await denied(() => service.view(expected), 409);
  current = CONVERSATION;

  // The screenshot must not expose login pages or a different active account.
  fingerprint = 'b'.repeat(64);
  await denied(() => service.view(payload()), 403);
  await denied(() => service.input(payload(CONVERSATION, { kind: 'text', text: 'no' })), 403);
  fingerprint = ACCOUNT;
  relinking = true;
  await denied(() => service.view(payload()), 423);
  relinking = false;
  target = null;
  await denied(() => service.view(payload()), 503);
  target = { id: 'dedicated-tab' };

  // Reject fabricated target URLs, secret URL parameters, and invalid actions.
  for (const url of ['https://chatgpt.com/', 'http://chatgpt.com/c/conversation123',
     'https://chatgpt.com:444/c/conversation123', CONVERSATION + '?session=secret',
     'https://evil.example/c/conversation123', 'https://chatgpt.com/c/123/else']) {
    await denied(() => service.view(payload(url)), 400);
  }
  await denied(() => service.input(payload(CONVERSATION, { kind: 'click', x: -2, y: 50 })), 400);
  await denied(() => service.input(payload(CONVERSATION, { kind: 'key', key: 'Alt+F4' })), 400);
  await denied(() => service.input(payload(CONVERSATION, { kind: 'text', text: 'x'.repeat(12001) })), 400);
  assert(opened === 0);
  assert(closes > 0);
  console.log('RELAY_SANDBOX_NODE_TESTS_OK view/input during send, lock, scope, account, navigation, validation');
})().catch(e => { console.error(e); process.exitCode = 1; });

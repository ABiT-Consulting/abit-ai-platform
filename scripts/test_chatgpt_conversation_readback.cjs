'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {
  conversationId, browserConversationTail, readConversationTail, verifiedCurrentReply,
} = require('../deployment/windows/aibit-relay/conversation_readback.cjs');

const GENERAL = 'https://chatgpt.com/c/test-conversation';
const PROJECT = 'https://chatgpt.com/g/g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-test/c/test-conversation';

function mapping({ prompt = 'Please review this', answer = 'Completed work', userId = 'user-new',
                   assistantStatus = 'finished_successfully', endTurn = true, recipient = 'all' } = {}) {
  return {
    conversation_id: 'test-conversation',
    current_node: 'assistant-new',
    mapping: {
      [userId]: { id: userId, parent: 'prior-assistant', message: {
        id: userId, author: { role: 'user' }, content: { parts: [prompt] },
      }},
      'assistant-new': { id: 'assistant-new', parent: userId, message: {
        id: 'assistant-new', author: { role: 'assistant' }, recipient,
        status: assistantStatus, end_turn: endTurn, content: { parts: [answer] },
      }},
    },
  };
}

async function readInFakeBrowser(data, options = {}) {
  let calls = 0;
  const url = options.url || GENERAL;
  const pathname = options.path || new URL(url).pathname;
  const fetch = async path => {
    calls++;
    if (path === '/api/auth/session') return {
      ok: true, json: async () => ({ user: { email: 'example@invalid.test' }, accessToken: 'not-a-real-token' }),
    };
    if (path.startsWith('/backend-api/conversation/')) return options.status
      ? { ok: false, status: options.status }
      : { ok: true, json: async () => data };
    throw Error('unexpected endpoint');
  };
  const ctx = { URL, AbortSignal, location: { origin: 'https://chatgpt.com', pathname }, fetch };
  const expression = '(' + browserConversationTail.toString() + ')(' +
    JSON.stringify(conversationId(url)) + ',' + JSON.stringify(url) + ')';
  const result = await vm.runInNewContext(expression, ctx);
  return { result, calls };
}

test('recognizes project and general conversation links but no external domains', () => {
  assert.equal(conversationId(GENERAL), 'test-conversation');
  assert.equal(conversationId(PROJECT), 'test-conversation');
  assert.equal(conversationId('https://other.example/c/test-conversation'), '');
});

test('reads a completed assistant from the current conversation branch only', async () => {
  const { result, calls } = await readInFakeBrowser(mapping());
  assert.equal(calls, 2);
  assert.equal(result.status, 200);
  assert.equal(result.userId, 'user-new');
  assert.equal(result.userText, 'Please review this');
  assert.equal(verifiedCurrentReply(result, 'Please review this', 'user-before', false), 'Completed work');
});

test('accepts a completed response from the newly created conversation', async () => {
  const { result } = await readInFakeBrowser(mapping(), { url: PROJECT });
  assert.equal(verifiedCurrentReply(result, 'Please review this', '', true), 'Completed work');
});

test('rejects a repeated prompt when the user node is not newer than the baseline', async () => {
  const { result } = await readInFakeBrowser(mapping({ userId: 'old-user' }));
  assert.equal(verifiedCurrentReply(result, 'Please review this', 'old-user', false), '');
});

test('rejects followups without an API baseline, even if text matches', async () => {
  const { result } = await readInFakeBrowser(mapping());
  assert.equal(verifiedCurrentReply(result, 'Please review this', '', false), '');
});

test('rejects replies for a different prompt and for incomplete assistant turns', async () => {
  const { result: wrong } = await readInFakeBrowser(mapping({ prompt: 'Earlier user message' }));
  assert.equal(verifiedCurrentReply(wrong, 'Please review this', 'user-before', false), '');
  const { result: unfinished } = await readInFakeBrowser(mapping({ endTurn: false }));
  assert.equal(verifiedCurrentReply(unfinished, 'Please review this', 'user-before', false), '');
  const { result: pending } = await readInFakeBrowser(mapping({ assistantStatus: 'in_progress' }));
  assert.equal(verifiedCurrentReply(pending, 'Please review this', 'user-before', false), '');
  const { result: tool } = await readInFakeBrowser(mapping({ recipient: 'python' }));
  assert.equal(verifiedCurrentReply(tool, 'Please review this', 'user-before', false), '');
});

test('refuses to read a different active conversation after tab navigation', async () => {
  const { result, calls } = await readInFakeBrowser(mapping(), { path: '/c/different-conversation' });
  assert.equal(result.status, 409);
  assert.equal(calls, 0);
});

test('propagates upstream 429 as status without inventing a reply', async () => {
  const { result } = await readInFakeBrowser(mapping(), { status: 429 });
  assert.equal(result.status, 429);
  assert.equal(verifiedCurrentReply(result, 'Please review this', 'old-user', false), '');
});

test('readback serializes only a browser-side read, never sends a prompt', async () => {
  let expression = '';
  const fakeSession = {
    evaluate: async script => { expression = script; return { status: 200, userId: 'u' }; },
  };
  const tail = await readConversationTail(fakeSession, GENERAL);
  assert.equal(tail.status, 200);
  assert(expression.includes('/backend-api/conversation/'));
  assert(!expression.includes('send-button'));
});


test('rejects finished assistant if a newer assistant turn is still processing', () => {
  const previous = {
    status: 200, leafRole: 'assistant', userId: 'new-user', userText: 'Please review this',
    assistants: [
      { recipient: 'all', endTurn: true, status: 'finished_successfully', text: 'Early result' },
      { recipient: 'python', endTurn: false, status: 'in_progress', text: 'Tool pending' },
    ],
  };
  assert.equal(verifiedCurrentReply(previous, 'Please review this', 'previous-user', false), '');
});

test('rejects conversation API tail whose last branch node is a tool', () => {
  const tail = {
    status: 200, leafRole: 'tool', userId: 'new-user', userText: 'Please review this',
    assistants: [{ recipient: 'all', endTurn: true,
      status: 'finished_successfully', text: 'Earlier answer' }],
  };
  assert.equal(verifiedCurrentReply(tail, 'Please review this', 'previous-user', false), '');
});

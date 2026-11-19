const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/agent-run.js'), 'utf8');
const agent = '53a04df5-19a6-48df-9fa6-139300be3b3b';
const id = '76ab7fc2-ca07-427d-b60f-2aac3e58920b';
const chatId = '57d90e06-102f-42c5-8fe8-3df074954c63';
const run = { id, automation_id: agent, status: 'success', chat_id: chatId, created_at: 1795018632275999048 };

async function load(pages, query = `?agent=${agent}&run=${id}`) {
  const nodes = new Map();
  const calls = [];
  const targets = [];
  const document = {
    getElementById(name) {
      if (!nodes.has(name)) {
        nodes.set(name, { hidden: true, addEventListener() {}, removeAttribute() {} });
      }
      return nodes.get(name);
    },
  };
  vm.runInNewContext(source, {
    document, URLSearchParams, localStorage: { getItem: () => 'fixture-token' },
    location: { pathname: '/agent-run.html', search: query, replace: target => targets.push(target) },
    fetch: async (url, options) => {
      const page = pages[calls.length];
      calls.push({ url, options });
      return { ok: Array.isArray(page), status: page, json: async () => page };
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  return { nodes, calls, targets };
}

test('an older run opens the exact saved chat instead of the latest chat', async () => {
  const page = Array.from({ length: 50 }, (_, i) => ({ ...run, id: 'other-' + i }));
  const { calls, targets } = await load([page, [run]]);
  assert.deepEqual(targets, ['/c/' + chatId]);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, `/api/v1/automations/${agent}/runs?skip=0&limit=50`);
  assert.equal(calls[1].url, `/api/v1/automations/${agent}/runs?skip=50&limit=50`);
  assert.equal(calls[0].options.credentials, 'same-origin');
});

test('run without a chat shows only its own saved state and error', async () => {
  const { nodes, targets } = await load([[{ ...run, chat_id: null, status: 'error', error: 'Failed <step> token=sample' }]]);
  assert.deepEqual(targets, []);
  assert.equal(nodes.get('record').hidden, false);
  assert.equal(nodes.get('run-id').textContent, id);
  assert.equal(nodes.get('state').textContent, 'Failed');
  assert.equal(nodes.get('error').textContent, 'Failed <step> token=[redacted]');
});

test('missing exact runs and records from another agent never redirect', async () => {
  const { nodes, targets } = await load([[{ ...run, automation_id: 'other' }, { ...run, id: 'other' }]]);
  assert.deepEqual(targets, []);
  assert.match(nodes.get('status').textContent, /not in ABIT.AI history/);
});

test('signed-out and inaccessible responses follow existing auth paths', async () => {
  const guest = await load([401]);
  assert.equal(guest.nodes.get('sign-in').hidden, false);
  assert.match(guest.nodes.get('sign-in').href, /^\/auth\?redirect=%2Fagent-run.html/);
  const denied = await load([404]);
  assert.equal(denied.nodes.get('record').hidden, true);
  assert.match(denied.nodes.get('status').textContent, /do not have access/);
});

test('malformed IDs do not make requests and unsafe chat IDs cannot navigate', async () => {
  const invalid = await load([], `?agent=${agent}&run=../other`);
  assert.equal(invalid.calls.length, 0);
  assert.match(invalid.nodes.get('status').textContent, /invalid/);
  const unsafe = await load([[{ ...run, chat_id: 'https://elsewhere.example/' }]]);
  assert.deepEqual(unsafe.targets, []);
  assert.equal(unsafe.nodes.get('record').hidden, false);
});

'use strict';
// Exercises the real composer wait function against deterministic browser DOM
// fixtures. No network traffic and no real ChatGPT messages are sent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../deployment/windows/aibit-relay/aibit_relay.cjs'), 'utf8');
const a = source.indexOf('async function waitForComposer(');
const b = source.indexOf('\nasync function composerSnapshot(', a);
assert(a >= 0 && b > a);
const impl = source.slice(a, b);

function browserFixture({ label = 'Ask ChatGPT', absent = false, delayed = 0, hidden = false, loadFailure = false } = {}) {
  let now = 0;
  let reads = 0;
  const url = 'https://chatgpt.com/';
  const node = {
    isConnected: true,
    disabled: false,
    getAttribute: key => key === 'aria-label' ? label : null,
    getBoundingClientRect: () => ({ width: 200, height: hidden ? 0 : 40 }),
  };
  const document = {
    body: { innerText: loadFailure ? 'Could not load this ChatGPT conversation' : '' },
    querySelector: selector => {
      if (selector === '[role="textbox"][contenteditable="true"]' && !absent && reads > delayed) return node;
      return null;
    },
  };
  const browser = {
    document,
    location: { href: url, pathname: '/' },
    getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
  };
  const session = {
    evaluate: async expression => {
      reads += 1;
      return vm.runInNewContext(expression, browser);
    },
  };
  const context = {
    Date: class { static now() { return now; } },
    sleep: async ms => { now += ms; },
  };
  vm.createContext(context);
  vm.runInContext(impl + '\nglobalThis.wait = waitForComposer;', context);
  return { run: timeoutMs => context.wait(session, timeoutMs), reads: () => reads };
}

test('ignores unrelated contenteditable sidebar search input', async () => {
  const x = browserFixture({ label: 'Search chats' });
  await assert.rejects(x.run(1400), /composer did not become ready/);
});

test('waits for the actual composer to hydrate and remain stable', async () => {
  const x = browserFixture({ delayed: 3 });
  const result = await x.run(4000);
  assert.equal(result.composerReady, true);
  assert(x.reads() >= 6);
});

test('ignores composer that is hidden or detached from visible layout', async () => {
  const x = browserFixture({ hidden: true });
  await assert.rejects(x.run(1400), /composer did not become ready/);
});

test('does not mistake missing composer for a ready page', async () => {
  const x = browserFixture({ absent: true });
  await assert.rejects(x.run(1400), /composer did not become ready/);
});

test('fails explicitly when the conversation cannot load', async () => {
  const x = browserFixture({ loadFailure: true, absent: true });
  await assert.rejects(x.run(1400), e => e.statusCode === 409 && /could not load/.test(e.message));
});

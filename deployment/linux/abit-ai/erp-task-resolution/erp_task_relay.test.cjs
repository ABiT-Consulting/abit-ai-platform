'use strict';

const assert = require('node:assert/strict');
const {mkdtempSync, rmSync} = require('node:fs');
const {tmpdir} = require('node:os');
const {join} = require('node:path');
const {test} = require('node:test');
const {setImmediate: tick} = require('node:timers/promises');
const {makeErpRelay} = require('./erp_task_relay.cjs');

function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), 'erp-relay-test-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const make = extra => makeErpRelay({root, preflight: () => ({ready: true}),
    reserve: () => () => {}, recover: () => ({}), ...options, ...extra});
  const input = {owner: 'test', account: 'a'.repeat(64), chatgpt_project: 'g-p-' + 'b'.repeat(32),
    project: 'PROJ-TEST', agent: 'erp-' + 'c'.repeat(32), id: 'd'.repeat(64),
    deadline: Date.now() / 1000 + 1190, task: 'TASK-TEST'};
  return {make, input};
}

test('concurrent observations submit once and report informational waiting', async t => {
  let submitted = 0, finish;
  const {make, input} = fixture(t, {
    run: async (row, lifecycle) => {
      lifecycle.target('target');
      lifecycle.sending();
      submitted++;
      lifecycle.progress({state: 'message_submitted'});
      return new Promise(resolve => {finish = resolve;});
    },
  });
  const relay = make();
  await Promise.all([relay('observe', input), relay('observe', input), relay('observe', input)]);
  assert.equal(submitted, 1);
  assert.match((await relay('observe', input)).progress, /ChatGPT is still working/);
  finish('verified current reply');
  await tick();
  const recovered = make();
  assert.equal((await recovered('observe', input)).findings, 'verified current reply');
  assert.equal(submitted, 1);
});

test('uncertain submission is observed after restart and cannot send again', async t => {
  let submitted = 0;
  const {make, input} = fixture(t, {
    run: async (row, lifecycle) => {
      lifecycle.target('target');
      lifecycle.sending();
      submitted++;
      throw new Error('observer disconnected');
    },
  });
  await make()('observe', input);
  await tick();
  const recovered = make({recover: async row => {
    assert.equal(row.target, 'target');
    assert.ok(row.prompt.startsWith('[ABIT-RUN:' + row.id + ']'));
    return {findings: 'recovered'};
  }});
  await recovered('observe', input);
  await tick();
  assert.equal((await recovered('observe', input)).findings, 'recovered');
  assert.equal(submitted, 1);
});

test('stop is unconfirmed until active turn exits and browser stop is verified', async t => {
  let resume, browserStopped = false;
  const {make, input} = fixture(t, {
    run: async (row, lifecycle) => {
      lifecycle.target('target');
      lifecycle.sending();
      await new Promise(resolve => {resume = resolve;});
      lifecycle.guard();
    },
    recover: async (row, stopping, acknowledge) => {
      assert.equal(stopping, true);
      if (browserStopped) acknowledge();
      return {confirmed: browserStopped};
    },
  });
  const relay = make();
  await relay('observe', input);
  assert.equal((await relay('stop', input)).confirmed, false);
  resume();
  await tick();
  assert.equal((await relay('stop', input)).confirmed, false);
  await tick();
  browserStopped = true;
  await relay('stop', input);
  await tick();
  assert.equal((await relay('stop', input)).confirmed, true);
  assert.equal((await relay('observe', input)).status, 'cancelled');
});

test('a revocation or expired deadline prevents later submission', async t => {
  const {make, input} = fixture(t, {run: () => assert.fail('revoked request executed')});
  const relay = make();
  assert.equal((await relay('stop', input)).confirmed, true);
  assert.equal((await relay('observe', input)).status, 'cancelled');
  assert.equal((await relay('observe', {...input, id: 'e'.repeat(64), deadline: Date.now() / 1000 - 1})).status, 'cancelled');
  await assert.rejects(relay('observe', {...input, owner: 'different'}), /ownership mismatch/);
});

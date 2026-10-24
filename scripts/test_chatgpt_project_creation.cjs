'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createSynchronizedProject } = require('../deployment/windows/aibit-relay/aibit_project_creation.cjs');
const ACCOUNT = 'a'.repeat(64), OTHER = 'b'.repeat(64);
const PROJECT = 'g-p-testproject123456';
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'aibit-project-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const state = { creates: 0, saves: 0, account: ACCOUNT, catalog: [], instructionFailure: false, createFailure: false };
  const body = { owner: 'test-owner', account_id: ACCOUNT, name: 'Test Project', instructions: 'Test-only instructions', request_id: 'a1111111-2222-4333-8444-555555555555' };
  const deps = {
    journalFile: path.join(directory, 'journal.json'), account: async () => state.account,
    catalog: async () => state.catalog,
    create: async name => {
      state.creates++;
      if (state.createFailure) throw Error('Simulated uncertain browser outcome');
      const project = { id: PROJECT, name };
      state.catalog.push(project);
      return project;
    },
    instructions: async () => { state.saves++; if (state.instructionFailure) throw Error('Simulated failed save');
      return { success: true, verified: true, instructions_saved: true }; },
  };
  return { body, deps, state };
}
test('empty new account can create its first project with verified instructions', async t => {
  const { body, deps, state } = fixture(t);
  const result = await createSynchronizedProject(body, deps);
  assert.equal(result.project.id, PROJECT); assert.equal(result.instructions_saved, true);
  assert.equal(state.creates, 1); assert.equal(state.saves, 1);
  assert.ok(!fs.readFileSync(deps.journalFile, 'utf8').includes(body.instructions));
});
test('completed retry survives reloading journal without duplicate creation', async t => {
  const { body, deps, state } = fixture(t);
  await createSynchronizedProject(body, deps);
  const replay = await createSynchronizedProject({ ...body }, { ...deps });
  assert.equal(replay.replayed, true); assert.equal(state.creates, 1); assert.equal(state.saves, 1);
});
test('failed instructions retry resumes same project', async t => {
  const { body, deps, state } = fixture(t); state.instructionFailure = true;
  await assert.rejects(createSynchronizedProject(body, deps), /same request/);
  state.instructionFailure = false;
  assert.equal((await createSynchronizedProject(body, deps)).ok, true);
  assert.equal(state.creates, 1); assert.equal(state.saves, 2);
});
test('uncertain creation is never automatically repeated', async t => {
  const { body, deps, state } = fixture(t); state.createFailure = true;
  await assert.rejects(createSynchronizedProject(body, deps), /uncertain/);
  state.createFailure = false;
  await assert.rejects(createSynchronizedProject(body, deps), /uncertain/);
  assert.equal(state.creates, 1);
});
test('account mismatch fails before any mutation', async t => {
  const { body, deps, state } = fixture(t); state.account = OTHER;
  await assert.rejects(createSynchronizedProject(body, deps), /account changed/);
  assert.equal(state.creates, 0); assert.equal(fs.existsSync(deps.journalFile), false);
});
test('same name never overwrites another project instructions', async t => {
  const { body, deps, state } = fixture(t); state.catalog = [{ id: 'g-p-existing12345', name: 'test project' }];
  await assert.rejects(createSynchronizedProject(body, deps), /already exists/);
  assert.equal(state.creates, 0); assert.equal(state.saves, 0);
});
test('reused request with altered payload is rejected', async t => {
  const { body, deps, state } = fixture(t); await createSynchronizedProject(body, deps);
  await assert.rejects(createSynchronizedProject({ ...body, instructions: 'changed' }, deps), /different content/);
  assert.equal(state.creates, 1);
});
test('corrupted creation journal fails closed', async t => {
  const { body, deps, state } = fixture(t); fs.writeFileSync(deps.journalFile, '{broken');
  await assert.rejects(createSynchronizedProject(body, deps), /journal is unavailable/);
  assert.equal(state.creates, 0);
});
test('oversized and invalid fields fail before browser actions', async t => {
  const { body, deps, state } = fixture(t);
  for (const invalid of [{ name: '' }, { name: 'x'.repeat(121) }, { instructions: 'x'.repeat(12001) }, { request_id: 'bad' }, { name: 'a\nb' }]) {
    await assert.rejects(createSynchronizedProject({ ...body, ...invalid }, deps), error => error.statusCode === 400);
  }
  assert.equal(state.creates, 0);
});

test('completed retry checks that the upstream project still exists', async t => {
  const { body, deps, state } = fixture(t);
  await createSynchronizedProject(body, deps);
  state.catalog = [];
  await assert.rejects(createSynchronizedProject(body, deps), error => error.statusCode === 409);
  assert.equal(state.creates, 1); assert.equal(state.saves, 1);
});
test('completed retry never restores an obsolete upstream project name', async t => {
  const { body, deps, state } = fixture(t);
  await createSynchronizedProject(body, deps);
  state.catalog[0].name = 'Renamed in ChatGPT';
  await assert.rejects(createSynchronizedProject(body, deps), error => error.statusCode === 409);
  assert.equal(state.creates, 1); assert.equal(state.saves, 1);
});
test('incomplete retry cannot overwrite instructions after an upstream rename', async t => {
  const { body, deps, state } = fixture(t); state.instructionFailure = true;
  await assert.rejects(createSynchronizedProject(body, deps), /same request/);
  state.instructionFailure = false; state.catalog[0].name = 'Renamed by user';
  await assert.rejects(createSynchronizedProject(body, deps), error => error.statusCode === 409);
  assert.equal(state.creates, 1); assert.equal(state.saves, 1);
});
test('account is rechecked after reading catalog on a completed retry', async t => {
  const { body, deps, state } = fixture(t);
  await createSynchronizedProject(body, deps);
  deps.catalog = async () => { state.account = OTHER; return state.catalog; };
  await assert.rejects(createSynchronizedProject(body, deps), error => error.statusCode === 409);
  assert.equal(state.creates, 1);
});

function relinkFixture() {
  const source = fs.readFileSync(path.join(__dirname, '../deployment/windows/aibit-relay/aibit_relay.cjs'), 'utf8');
  const begin = source.indexOf('// The relink flow stays inside AIBIT:');
  const end = source.indexOf('// AIBIT uses only its own browser target.', begin);
  const state = { target: { id: 'own-target', type: 'page' }, url: 'https://chatgpt.com/', account: ACCOUNT,
    login: { authenticated: true, status: 'Ready' }, persisted: null, clearCount: 0, calls: [] };
  const context = vm.createContext({
    URL, Date, console, Error, Number, String, Set,
    projectCatalogCache: { expiresAt: 123, projects: [{ id: 'old' }] },
    persistProjectCatalog: projects => { if (!projects.length) state.clearCount++; },
    persistDedicatedTargetId: id => { state.persisted = id; },
    cdpTargets: async () => [state.target], findChatGptTarget: async () => state.target,
    aibitAccountFingerprint: async target => { assert.equal(target?.id || state.target.id, state.target.id); return state.account; },
    browserLoginState: async () => state.login,
    CdpSession: class {
      async open() { return this; } close() {} async location() { return state.url; }
      async evaluate() { return { width: 1200, height: 700, w: 1200, h: 700 }; }
      async call(method, params) { state.calls.push({ method, params }); return { data: 'test-image' }; }
    }
  });
  vm.runInContext(source.slice(begin, end) + '\nglobalThis.api = { beginAibitRelink, viewAibitRelink, inputAibitRelink, aibitRelinkStatus, cancelAibitRelink, activeRelink, requireRelink };', context);
  return { state, context, api: context.api };
}
test('account relink is exclusive to its administrator and cancel is idempotent', async () => {
  const { api } = relinkFixture(); await api.beginAibitRelink('owner');
  await assert.rejects(api.beginAibitRelink('different-owner'), error => error.statusCode === 423);
  assert.throws(() => api.cancelAibitRelink('different-owner'), error => error.statusCode === 403);
  assert.equal((await api.aibitRelinkStatus('owner')).owned, true);
  api.cancelAibitRelink('owner'); api.cancelAibitRelink('owner'); assert.equal(api.activeRelink(), null);
});
test('transient login-state failure cannot falsely complete a switch', async () => {
  const { api, state } = relinkFixture(); await api.beginAibitRelink('owner');
  state.login = { authenticated: false, status: 'Session check unavailable' };
  await api.viewAibitRelink('owner');
  state.login = { authenticated: true, status: 'Ready' };
  assert.equal((await api.viewAibitRelink('owner')).active, true);
});
test('new account completion clears cache and retains exact owned tab', async () => {
  const { api, state } = relinkFixture(); await api.beginAibitRelink('owner'); state.account = OTHER;
  const result = await api.viewAibitRelink('owner');
  assert.equal(result.connected, true); assert.equal(api.activeRelink(), null);
  assert.equal(state.persisted, 'own-target'); assert.equal(state.clearCount, 1);
});
test('sign-in cannot control unapproved sites or out-of-bounds coordinates', async () => {
  const { api, state } = relinkFixture(); await api.beginAibitRelink('owner');
  await assert.rejects(api.inputAibitRelink({ owner: 'owner', kind: 'click', x: -1, y: 0 }), error => error.statusCode === 400);
  state.url = 'https://unapproved.example/';
  await assert.rejects(api.inputAibitRelink({ owner: 'owner', kind: 'text', text: 'test' }), error => error.statusCode === 403);
  assert.equal(state.calls.length, 0);
});
test('queued chat operations recheck relink state when they actually execute', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../deployment/windows/aibit-relay/aibit_relay.cjs'), 'utf8');
  const a = source.indexOf('function withBrowserLock('), b = source.indexOf('\nasync function cdpTargets', a);
  const context = vm.createContext({ Error, Promise, queueTail: Promise.resolve(),
    withSharedBrowserLock: fn => fn(), activeRelink: () => ({ owner: 'owner' }) });
  vm.runInContext(source.slice(a, b) + '\nglobalThis.lock = withBrowserLock;', context);
  await assert.rejects(context.lock(() => 'should not execute'), error => error.statusCode === 423);
  assert.equal(await context.lock(() => 'allowed-login-control', true), 'allowed-login-control');
});

test('a queued AIBIT message cannot be sent through a newly switched account', () => {
  const { assertChatAccount } = require('../deployment/windows/aibit-relay/aibit_project_creation.cjs');
  assert.doesNotThrow(() => assertChatAccount({ thread_id: 'aibit:owner:chat', account_id: ACCOUNT }, ACCOUNT));
  assert.throws(() => assertChatAccount({ thread_id: 'aibit:owner:chat', account_id: ACCOUNT }, OTHER), /No message was sent/);
  assert.throws(() => assertChatAccount({ thread_id: 'aibit:owner:chat' }, ACCOUNT), /No message was sent/);
});

// Never type credentials into a downgraded or non-default sign-in origin.
for (const url of ['http://chatgpt.com/', 'https://chatgpt.com:444/',
                   'https://user:pass@chatgpt.com/', 'http://auth.openai.com/']) {
  test('relink rejects insecure or ambiguous origin '+url, async () => {
    const { api, state } = relinkFixture(); await api.beginAibitRelink('owner');
    state.url = url;
    await assert.rejects(api.inputAibitRelink({owner:'owner',kind:'text',text:'fixture-only'}),
                         error => error.statusCode === 403);
    await assert.rejects(api.viewAibitRelink('owner'), error => error.statusCode === 403);
    assert.equal(state.calls.length,0);
  });
}
test('approved HTTPS sign-in origin still accepts input', async () => {
  const { api, state } = relinkFixture(); await api.beginAibitRelink('owner');
  state.url = 'https://auth.openai.com/login';
  assert.equal((await api.inputAibitRelink({owner:'owner',kind:'text',text:'fixture-only'})).ok,true);
  assert.equal(state.calls.length,1);
});

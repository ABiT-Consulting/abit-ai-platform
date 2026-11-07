'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chatThreadKey, createChatThreadTabs} = require('../chat_thread_tabs.cjs');

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'aibit-chat-tabs-'));
  try {
    const stateFile = path.join(tmp, 'thread-targets.json');
    let next = 1;
    const targets = new Map();
    const active = new Set();
    const closed = [];
    const callbacks = {
      stateFile,
      listTargets: async () => [...targets.values()],
      createTarget: async () => {
        await new Promise(resolve => setTimeout(resolve, 30));
        const id = next.toString(16).padStart(32, '0');
        next++;
        const row = {id, type: 'page', url: 'https://chatgpt.com/'};
        targets.set(id, row);
        return row;
      },
      closeTarget: async id => {closed.push(id);targets.delete(id);},
      isChatGptPage: row => row.type === 'page' && row.url.startsWith('https://chatgpt.com/'),
      isActive: key => active.has(key),
      maxTracked: 2,
    };
    const tabs = createChatThreadTabs(callbacks);
    const a = 'aibit:owner-one:chat-a';
    const b = 'aibit:owner-one:chat-b';
    assert.notEqual(chatThreadKey(a), chatThreadKey(b));
    assert.equal(chatThreadKey('malformed'), '');
    const [a1,a2,b1] = await Promise.all([
      tabs.acquire(a), tabs.acquire(a), tabs.acquire(b),
    ]);
    assert.equal(a1.id, a2.id, 'duplicate requests in the same chat must share the tab');
    assert.notEqual(a1.id, b1.id, 'different chats must have isolated tabs');
    assert.equal(next, 3, 'create exactly two tabs');
    assert.equal((await tabs.existing(a)).id, a1.id);
    const reloaded = createChatThreadTabs(callbacks);
    assert.equal((await reloaded.acquire(b)).id, b1.id, 'tab survives relay process restart');
    active.add(chatThreadKey(a));
    const c1 = await reloaded.acquire('aibit:owner-one:chat-c');
    assert.ok(c1.id && c1.id !== a1.id && c1.id !== b1.id);
    assert.ok(targets.has(a1.id), 'busy chat must never be evicted');
    assert.ok(closed.includes(b1.id), 'idle old tab can be evicted');
    const saved = JSON.parse(fs.readFileSync(stateFile,'utf8'));
    assert.equal(Object.keys(saved).length, 2);
    assert.ok(!JSON.stringify(saved).includes('owner-one'), 'no owner ID in registry');
    console.log('PASS: parallel distinct tabs, same-chat dedupe, reboot reuse, safe LRU eviction');
  } finally {
    fs.rmSync(tmp, {recursive:true,force:true});
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});

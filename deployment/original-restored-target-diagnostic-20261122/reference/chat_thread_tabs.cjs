'use strict';

// Durable per-conversation Chrome target ownership. No prompts, auth tokens,
// emails or chat transcripts are ever stored in the registry.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

function chatThreadKey(value) {
  const thread = String(value || '');
  if (!/^aibit:[^:]{1,256}:[^:]{1,256}$/.test(thread)) return '';
  return crypto.createHash('sha256').update(thread).digest('hex').slice(0,40);
}

function createChatThreadTabs({
  stateFile, listTargets, createTarget, closeTarget, isChatGptPage, recoverTarget,
  isActive = () => false, isPinned = () => false, maxTracked = 16,
}) {
  if (!stateFile || !listTargets || !createTarget || !closeTarget || !isChatGptPage ||
      !Number.isInteger(maxTracked) || maxTracked < 1) {
    throw new Error('Chat thread target registry requires scoped browser operations.');
  }
  let entries = {};
  try {
    const loaded = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    if (loaded && typeof loaded === 'object' && !Array.isArray(loaded)) {
      for (const [key, row] of Object.entries(loaded)) {
        if (/^[a-f0-9]{40}$/.test(key) &&
            row && /^[a-f0-9]{16,64}$/i.test(String(row.id || ''))) {
          entries[key] = { id: String(row.id), touched: Number(row.touched || 0) };
        }
      }
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const inflight = new Map(), readers = new Map();
  let allocation = Promise.resolve();
  const protectedKey = key => isActive(key) || readers.has(key) || isPinned(key);
  function persist() {
    fs.mkdirSync(path.dirname(stateFile), { recursive: true });
    const tmp = stateFile + '.' + process.pid + '.tmp';
    try {
      fs.writeFileSync(tmp, JSON.stringify(entries), { mode: 0o600 });
      fs.renameSync(tmp, stateFile);
    } finally {
      try { fs.unlinkSync(tmp); } catch (_) {}
    }
  }
  async function existing(threadId) {
    const key = chatThreadKey(threadId);
    if (!key || !entries[key]) return null;
    const found = (await listTargets()).find(row =>
      row && row.id === entries[key].id && isChatGptPage(row));
    if (!found && !protectedKey(key)) {
      delete entries[key];
      persist();
    }
    return found || null;
  }
  async function makeRoom(key) {
    // Serialize allocation, not browser reads. Never exceed the bounded tab
    // budget by silently discarding an unresolved original's only selector.
    if (entries[key]) return;
    while (Object.keys(entries).length >= maxTracked) {
      const candidates = Object.entries(entries)
        .filter(([other]) => other !== key && !protectedKey(other))
        .sort((a,b) => a[1].touched - b[1].touched);
      if (!candidates.length) throw Object.assign(
        Error('Original governed conversations occupy the available tab capacity.'),
        {code:'DOT_TAB_CAPACITY_PINNED',statusCode:503,notSubmitted:true});
      const [other, stale] = candidates[0];
      delete entries[other];
      persist();
      await closeTarget(stale.id).catch(() => {});
    }
  }
  async function scopedTarget(threadId, factory) {
    const key = chatThreadKey(threadId);
    if (!key) return null;
    if (inflight.has(key)) return inflight.get(key);
    const task = (async () => {
      const found = await existing(threadId);
      if (found) {
        entries[key].touched = Date.now();
        persist();
        return found;
      }
      const create = allocation.then(async () => {
        // Recheck after another allocation completed.
        const again = await existing(threadId);
        if (again) return again;
        await makeRoom(key);
        const row = await factory(key);
        if (!row || !/^[a-f0-9]{16,64}$/i.test(row.id || '') || !isChatGptPage(row))
          throw new Error('New isolated ChatGPT target was not verified.');
        entries[key] = {id:row.id, touched:Date.now()};
        persist();
        return row;
      });
      allocation = create.catch(() => {});
      return create;
    })();
    inflight.set(key, task);
    try { return await task; }
    finally { if (inflight.get(key) === task) inflight.delete(key); }
  }
  async function acquire(threadId) {
    return scopedTarget(threadId, createTarget);
  }
  async function recover(threadId, binding) {
    // The caller has verified this immutable binding against the original scope
    // and account. Loading this exact URL never enters the submission path.
    const url = binding?.conversation_url;
    let valid = false;
    try {
      const u = new URL(url);
      valid = u.origin === 'https://chatgpt.com' && !u.username && !u.password &&
        !u.port && !u.search && !u.hash &&
        /^\/(?:g\/g-p-[A-Za-z0-9_-]{8,190}\/)?c\/[A-Za-z0-9_-]{5,190}$/.test(u.pathname);
    } catch (_) {}
    if (!recoverTarget || !valid || binding?.identity?.thread_id !== threadId ||
        !binding.root_id || !binding.binding_sha256)
      throw Object.assign(Error('Exact original conversation selector is unavailable.'),
        {code:'DOT_BINDING_RECOVERY_DENIED',statusCode:409});
    return scopedTarget(threadId, key => recoverTarget(key, url));
  }
  function hold(threadId) {
    const key = chatThreadKey(threadId);
    if (!key) throw Error('Invalid original thread identity.');
    readers.set(key, (readers.get(key) || 0) + 1);
    let released = false;
    return () => {
      if (released) return;
      released = true;
      const count = readers.get(key) - 1;
      if (count) readers.set(key, count); else readers.delete(key);
    };
  }
  function stats() {
    return {tracked:Object.keys(entries).length, creating:inflight.size};
  }
  return {key:chatThreadKey, existing, acquire, recover, hold, stats};
}
module.exports = {chatThreadKey, createChatThreadTabs};

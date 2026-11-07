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
  stateFile, listTargets, createTarget, closeTarget, isChatGptPage,
  isActive = () => false, maxTracked = 16,
}) {
  if (!stateFile || !listTargets || !createTarget || !closeTarget || !isChatGptPage) {
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
  const inflight = new Map();
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
    if (!found) {
      delete entries[key];
      persist();
    }
    return found || null;
  }
  async function acquire(threadId) {
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
      const row = await createTarget(key);
      if (!row || !row.id || !isChatGptPage(row)) {
        throw new Error('New isolated ChatGPT target was not verified.');
      }
      entries[key] = { id: row.id, touched: Date.now() };
      persist();
      // Close only idle tabs that this registry owns, never a tab in use.
      const candidates = Object.entries(entries)
        .filter(([other]) => other !== key && !isActive(other))
        .sort((a, b) => a[1].touched - b[1].touched);
      while (Object.keys(entries).length > maxTracked && candidates.length) {
        const [other, stale] = candidates.shift();
        delete entries[other];
        persist();
        await closeTarget(stale.id).catch(() => {});
      }
      return row;
    })();
    inflight.set(key, task);
    try { return await task; }
    finally { if (inflight.get(key) === task) inflight.delete(key); }
  }
  function stats() {
    return { tracked: Object.keys(entries).length, creating: inflight.size };
  }
  return { key: chatThreadKey, existing, acquire, stats };
}

module.exports = { chatThreadKey, createChatThreadTabs };

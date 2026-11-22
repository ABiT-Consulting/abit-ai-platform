'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const LOCK_FILE = process.env.ABIT_SHARED_CHATGPT_LOCK_FILE || 'C:\\ProgramData\\ABiT\\SharedChatGPT\\browser.lock';
const WAIT_MS = Number(process.env.ABIT_SHARED_CHATGPT_LOCK_WAIT_MS || 12 * 60 * 1000);
const STALE_MS = Number(process.env.ABIT_SHARED_CHATGPT_LOCK_STALE_MS || 15 * 60 * 1000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function processIsAlive(pid) {
  const value = Number(pid);
  if (!Number.isInteger(value) || value <= 0) return false;
  try {
    process.kill(value, 0);
    return true;
  } catch (error) {
    return error && error.code === 'EPERM';
  }
}

function removeStaleLock() {
  try {
    const current = JSON.parse(fs.readFileSync(LOCK_FILE, 'utf8'));
    const age = Date.now() - Number(current.createdAt || 0);
    // A crashed owner cannot release its lock. Do not make every demo request
    // wait 15 minutes after a supervised relay restart. A live owner is never
    // evicted, regardless of the lock's age.
    const validPid = Number.isInteger(Number(current.pid)) && Number(current.pid) > 0;
    if ((validPid || age > STALE_MS) && !processIsAlive(current.pid)) {
      fs.unlinkSync(LOCK_FILE);
      return true;
    }
  } catch (error) {
    if (error && error.code === 'ENOENT') return true;
  }
  return false;
}

async function acquireSharedBrowserLock(options = {}) {
  const requested = options.waitMs;
  if (requested !== undefined && (!Number.isInteger(requested) || requested < 1 || requested > WAIT_MS))
    throw new TypeError('Invalid shared browser lock wait bound');
  const waitMs = requested === undefined ? WAIT_MS : requested;
  fs.mkdirSync(path.dirname(LOCK_FILE), { recursive: true });
  const owner = crypto.randomUUID();
  const deadline = Date.now() + waitMs;
  while (Date.now() < deadline) {
    try {
      const fd = fs.openSync(LOCK_FILE, 'wx');
      try {
        fs.writeFileSync(fd, JSON.stringify({ owner, pid: process.pid, createdAt: Date.now() }), 'utf8');
      } finally {
        fs.closeSync(fd);
      }
      return () => {
        try {
          const current = JSON.parse(fs.readFileSync(LOCK_FILE, 'utf8'));
          if (current.owner === owner) fs.unlinkSync(LOCK_FILE);
        } catch (_) {}
      };
    } catch (error) {
      if (!error || error.code !== 'EEXIST') throw error;
      removeStaleLock();
      await sleep(Math.min(250, Math.max(0, deadline - Date.now())));
    }
  }
  throw Object.assign(new Error('Shared ChatGPT browser is busy. Retry the request.'), {code: 'CHATGPT_BROWSER_LOCK_BUSY'});
}

async function withSharedBrowserLock(action, options) {
  const release = await acquireSharedBrowserLock(options);
  try {
    return await action();
  } finally {
    release();
  }
}

module.exports = { withSharedBrowserLock };
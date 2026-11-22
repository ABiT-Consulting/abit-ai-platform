'use strict';
// Private, append-only selectors for an admitted ORIGINAL. No prompts, replies,
// credentials or gateway receipts are persisted; a selector is never a receipt.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {safeDotUrl} = require('./aibit_dot_handoff.cjs');
const {chatThreadKey} = require('./chat_thread_tabs.cjs');
const canonical = value => JSON.stringify(value, function (_, item) {
  return item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item;
});
const digest = value => crypto.createHash('sha256').update(canonical(value)).digest('hex');
const validId = value => typeof value === 'string' && value.length > 0 &&
  value.length <= 256 && !/[\u0000-\u0020\u007f]/.test(value);
const fail = (code, statusCode = 409) => Object.assign(
  Error('Original Dot recovery selector could not be verified.'), {code, statusCode});
function exactConversationUrl(raw) {
  if (typeof raw !== 'string' || raw.length > 450) return '';
  try {
    const u = new URL(raw);
    if (u.origin !== 'https://chatgpt.com' || u.username || u.password || u.port || u.search || u.hash ||
        !/^\/(?:g\/g-p-[A-Za-z0-9_-]{8,190}\/)?c\/[A-Za-z0-9_-]{5,190}\/?$/.test(u.pathname)) return '';
    return u.origin + u.pathname.replace(/\/$/, '');
  } catch (_) { return ''; }
}
function selectorLocation(raw, configured) {
  const conversation = exactConversationUrl(raw);
  if (conversation) return conversation;
  // The approved landing page can pin its original tab, but cannot reopen a turn.
  if (raw === configured || raw === configured + '/') return configured;
  return '';
}
function checkedIdentity(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw fail('DOT_BINDING_IDENTITY_INVALID');
  const names = ['owner','account_id','job_id','thread_id','url','message_sha256','scope_sha256'];
  const identity = Object.fromEntries(names.map(name => [name, value[name]]));
  if (Object.keys(value).sort().join('|') !== names.sort().join('|') ||
      !validId(identity.owner) || identity.owner.includes(':') ||
      !/^[a-f0-9]{64}$/.test(identity.account_id || '') ||
      !/^aibit_[a-f0-9]{48}$/.test(identity.job_id || '') ||
      !/^[a-f0-9]{64}$/.test(identity.message_sha256 || '') ||
      !/^[a-f0-9]{64}$/.test(identity.scope_sha256 || '') ||
      identity.thread_id !== 'aibit:' + identity.owner + ':dot_' + identity.job_id ||
      safeDotUrl(identity.url) !== identity.url) throw fail('DOT_BINDING_IDENTITY_INVALID');
  return Object.freeze(identity);
}
function createOriginalDotBindings({directory}) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory))
    throw fail('DOT_BINDING_STORE_UNAVAILABLE', 503);
  const key = value => digest([value.owner, value.account_id, value.job_id]);
  function checkStat(stat, directoryFlag) {
    if (!(directoryFlag ? stat.isDirectory() : stat.isFile()) ||
        (process.platform !== 'win32' && ((stat.mode & 0o077) ||
         (typeof process.getuid === 'function' && stat.uid !== process.getuid()))))
      throw fail('DOT_BINDING_STORE_UNSAFE', 503);
  }
  function ensureDirectory(create = false) {
    try {
      if (create) {
        let created = false;
        try { fs.mkdirSync(directory, {mode:0o700}); created = true; }
        catch (error) { if (error.code !== 'EEXIST') throw error; }
        if (created && process.platform !== 'win32') {
          const parent = fs.openSync(path.dirname(directory),
            fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | (fs.constants.O_NOFOLLOW || 0));
          try { fs.fsyncSync(parent); } finally { fs.closeSync(parent); }
        }
      }
      const stat = fs.lstatSync(directory);
      checkStat(stat, true); // lstat rejects a symlink, including an existing one.
      return true;
    } catch (error) {
      if (!create && error.code === 'ENOENT') return false;
      if (error.code?.startsWith('DOT_')) throw error;
      throw fail('DOT_BINDING_STORE_UNAVAILABLE', 503);
    }
  }
  function syncDirectory() {
    if (process.platform === 'win32') return;
    const fd = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  }
  function read(name) {
    if (!ensureDirectory()) return null;
    let fd;
    try {
      fd = fs.openSync(path.join(directory, name), fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
      const stat = fs.fstatSync(fd);
      checkStat(stat, false);
      if (stat.size > 8192) throw fail('DOT_BINDING_STORE_CORRUPT', 503);
      const value = JSON.parse(fs.readFileSync(fd, 'utf8'));
      if (!value || value.schema !== 1 || typeof value !== 'object' || Array.isArray(value))
        throw fail('DOT_BINDING_STORE_CORRUPT', 503);
      return value;
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      if (error.code?.startsWith('DOT_')) throw error;
      throw fail('DOT_BINDING_STORE_CORRUPT', 503);
    } finally { if (fd !== undefined) fs.closeSync(fd); }
  }
  function append(name, value, exclusive = false) {
    ensureDirectory(true);
    const current = read(name);
    if (current) {
      if (exclusive || canonical(current) !== canonical(value)) throw fail('DOT_BINDING_CONFLICT');
      return;
    }
    const temporary = path.join(directory, '.' + crypto.randomBytes(18).toString('hex') + '.tmp');
    let fd;
    try {
      fd = fs.openSync(temporary, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL, 0o600);
      fs.writeFileSync(fd, canonical(value) + '\n');
      fs.fsyncSync(fd);
      fs.closeSync(fd); fd = undefined;
      // Same-filesystem link provides atomic visibility AND no replacement.
      try { fs.linkSync(temporary, path.join(directory, name)); }
      catch (error) {
        if (error.code !== 'EEXIST') throw error;
        if (exclusive || canonical(read(name)) !== canonical(value)) throw fail('DOT_BINDING_CONFLICT');
      }
      fs.unlinkSync(temporary);
      syncDirectory();
    } catch (error) {
      if (error.code?.startsWith('DOT_')) throw error;
      throw fail('DOT_BINDING_STORE_UNAVAILABLE', 503);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
      try { fs.unlinkSync(temporary); } catch (_) {}
    }
  }
  function lookup(value) {
    const identity = checkedIdentity(value), id = key(identity);
    const base = read(id + '.base.json');
    const root = read(id + '.root.json'), conversation = read(id + '.conversation.json'),
      terminal = read(id + '.terminal.json');
    if (!base) {
      if (root || conversation || terminal) throw fail('DOT_BINDING_STORE_CORRUPT', 503);
      return null;
    }
    if (canonical(checkedIdentity(base.identity)) !== canonical(identity)) throw fail('DOT_BINDING_CONFLICT');
    if (base.schema !== 1 || !/^[a-f0-9]{16,64}$/i.test(base.target_id || '') ||
        !['admitted_original','visible_original'].includes(base.origin) ||
        Object.keys(base).sort().join('|') !== 'identity|origin|schema|target_id')
      throw fail('DOT_BINDING_STORE_CORRUPT', 503);
    const binding = digest(base);
    if (root && (root.binding_sha256 !== binding || !validId(root.root_id) ||
        !selectorLocation(root.location, identity.url) ||
        Object.keys(root).sort().join('|') !== 'binding_sha256|location|root_id|schema'))
      throw fail('DOT_BINDING_STORE_CORRUPT', 503);
    if (conversation && (!root || conversation.binding_sha256 !== binding ||
        conversation.root_id !== root.root_id ||
        exactConversationUrl(conversation.url) !== conversation.url ||
        (exactConversationUrl(root.location) && exactConversationUrl(root.location) !== conversation.url) ||
        Object.keys(conversation).sort().join('|') !== 'binding_sha256|root_id|schema|url'))
      throw fail('DOT_BINDING_STORE_CORRUPT', 503);
    if (terminal && (!root || terminal.binding_sha256 !== binding ||
        terminal.root_id !== root.root_id || !validId(terminal.reply_id) ||
        !/^[a-f0-9]{64}$/.test(terminal.response_sha256 || '') ||
        Object.keys(terminal).sort().join('|') !== 'binding_sha256|reply_id|response_sha256|root_id|schema'))
      throw fail('DOT_BINDING_STORE_CORRUPT', 503);
    return Object.freeze({identity, target_id:base.target_id, origin:base.origin,
      binding_sha256:binding, root_id:root?.root_id || null,
      location:conversation?.url || root?.location || identity.url,
      conversation_url:conversation?.url || (root && exactConversationUrl(root.location)) || null,
      terminal:terminal ? Object.freeze(terminal) : null});
  }
  function begin(value, targetId) {
    const identity = checkedIdentity(value);
    if (!/^[a-f0-9]{16,64}$/i.test(targetId || '')) throw fail('DOT_BINDING_TARGET_INVALID');
    // An admitted original cannot be submitted twice, even after process restart.
    if (lookup(identity)) throw fail('DOT_ORIGINAL_ALREADY_BOUND');
    append(key(identity) + '.base.json',
      {schema:1, identity, target_id:targetId, origin:'admitted_original'}, true);
    return lookup(identity);
  }
  function rememberRoot(value, {target_id, root_id, conversation_url}) {
    const record = lookup(value);
    if (!record || !validId(root_id)) throw fail('DOT_BINDING_ROOT_INVALID');
    const location = selectorLocation(conversation_url, record.identity.url);
    if (!location) throw fail('DOT_BINDING_LOCATION_INVALID');
    if (record.root_id && record.root_id !== root_id) throw fail('DOT_BINDING_ROOT_CONFLICT');
    if (!record.conversation_url && target_id !== record.target_id) throw fail('DOT_BINDING_TARGET_CONFLICT');
    if (record.conversation_url && record.conversation_url !== location) throw fail('DOT_BINDING_LOCATION_CONFLICT');
    if (!record.root_id) {
      if (target_id !== record.target_id) throw fail('DOT_BINDING_TARGET_CONFLICT');
      append(key(value) + '.root.json',
        {schema:1, binding_sha256:record.binding_sha256, root_id, location});
    }
    const conversation = exactConversationUrl(location);
    if (conversation) append(key(value) + '.conversation.json',
      {schema:1, binding_sha256:record.binding_sha256, root_id, url:conversation});
    return lookup(value);
  }
  function adoptVisible(value, proof) {
    const identity = checkedIdentity(value);
    if (!proof || proof.full_envelope_matched !== true || proof.owned_mapping !== true ||
        proof.account_verified !== true || proof.scope_verified !== true ||
        !/^[a-f0-9]{16,64}$/i.test(proof.target_id || '') || !validId(proof.root_id) ||
        !selectorLocation(proof.conversation_url, identity.url))
      throw fail('DOT_BINDING_ADOPTION_DENIED');
    if (!lookup(identity)) append(key(identity) + '.base.json',
      {schema:1, identity, target_id:proof.target_id, origin:'visible_original'});
    return rememberRoot(identity, proof);
  }
  function terminal(value, proof) {
    const record = lookup(value);
    if (!record || !record.root_id || !proof || proof.root_id !== record.root_id ||
        !validId(proof.reply_id) || typeof proof.response !== 'string' || !proof.response ||
        selectorLocation(proof.conversation_url, record.identity.url) !== record.location)
      throw fail('DOT_BINDING_TERMINAL_INVALID');
    append(key(value) + '.terminal.json', {schema:1, binding_sha256:record.binding_sha256,
      root_id:proof.root_id, reply_id:proof.reply_id, response_sha256:digest(proof.response)});
  }
  function isPinned(threadKey) {
    if (!ensureDirectory()) return false;
    const names = fs.readdirSync(directory), available = new Set(names);
    let pinned = false;
    for (const name of names) {
      if (!/^[a-f0-9]{64}\.base\.json$/.test(name)) {
        if (/^\.[a-f0-9]{36}\.tmp$/.test(name)) continue;
        if (/^[a-f0-9]{64}\.(root|conversation|terminal)\.json$/.test(name)) {
          if (!available.has(name.slice(0,64) + '.base.json')) throw fail('DOT_BINDING_STORE_CORRUPT', 503);
          continue;
        }
        throw fail('DOT_BINDING_STORE_CORRUPT', 503);
      }
      const base = read(name), identity = checkedIdentity(base?.identity);
      if (name !== key(identity) + '.base.json') throw fail('DOT_BINDING_STORE_CORRUPT', 503);
      const record = lookup(identity);
      if (chatThreadKey(identity.thread_id) === threadKey &&
          (!record.terminal || !record.conversation_url)) pinned = true;
    }
    return pinned;
  }
  return {lookup, begin, rememberRoot, adoptVisible, terminal, isPinned};
}
module.exports = {createOriginalDotBindings, exactConversationUrl, selectorLocation};

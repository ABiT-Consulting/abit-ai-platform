'use strict';
// Called under the relay's browser lock. The journal survives HTTP timeouts and
// process restarts. It stores no credentials or instruction text.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const fail = (message, statusCode = 409) => Object.assign(new Error(message), { statusCode });
const digest = value => crypto.createHash('sha256').update(value).digest('hex');

function readJournal(filename) {
  try {
    const data = JSON.parse(fs.readFileSync(filename, 'utf8'));
    if (!data || data.version !== 1 || typeof data.records !== 'object' || !data.records || Array.isArray(data.records)) {
      throw fail('Project creation journal is unavailable; no project was created.', 503);
    }
    return data;
  } catch (error) {
    if (error.code === 'ENOENT') return { version: 1, records: {} };
    throw fail('Project creation journal is unavailable; no project was created.', 503);
  }
}

function writeJournal(filename, journal) {
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  const temporary = filename + '.' + process.pid + '.tmp';
  const fd = fs.openSync(temporary, 'w', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(journal), 'utf8'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(temporary, filename);
}

async function createSynchronizedProject(body, deps) {
  const name = typeof body.name === 'string' ? body.name.trim() : '';
  const instructions = typeof body.instructions === 'string' ? body.instructions.trim() : '';
  const account = String(body.account_id || '');
  const owner = String(body.owner || '');
  const requestId = String(body.request_id || '');
  if (!owner || owner.length > 200 || !/^[a-f0-9]{64}$/.test(account) ||
      !/^[a-f0-9-]{36}$/i.test(requestId) || !name || name.length > 120 ||
      /[\u0000-\u001f]/.test(name) || instructions.length > 12000) {
    throw fail('Invalid project name, instructions or creation request.', 400);
  }
  if (await deps.account() !== account) throw fail('ChatGPT account changed. Refresh before creating a project.');
  const key = digest(JSON.stringify([account, owner, requestId]));
  const contentHash = digest(JSON.stringify([name, instructions]));
  const journal = readJournal(deps.journalFile);
  let record = journal.records[key];
  if (record && record.contentHash !== contentHash) throw fail('Project request ID was reused for different content.');
  if (record && !record.project) {
    throw fail('Project creation outcome is uncertain. Sync and inspect ChatGPT before creating again.');
  }
  // A persisted success is evidence of the original creation, not permission
  // to recreate a deleted project or restore its old name on a later retry.
  const catalog = await deps.catalog();
  if (!Array.isArray(catalog)) throw fail('ChatGPT project catalog unavailable; no project created.', 503);
  if (await deps.account() !== account) throw fail('ChatGPT account changed while checking the project. Sync before retrying.');
  if (record) {
    const current = catalog.find(p => (p.chatgpt_project_id || p.id) === record.project.id);
    if (!current) {
      throw fail('Previously created project is no longer available in this ChatGPT account.');
    }
    if (current.name !== name) {
      throw fail('The created project was renamed in ChatGPT. Sync instead of retrying its old name.');
    }
    if (record.state === 'complete') {
      return { ok: true, account_id: account, project: record.project, instructions_saved: true, replayed: true };
    }
  } else {
    if (catalog.some(p => String(p.name || '').trim().toLowerCase() === name.toLowerCase())) {
      throw fail('ChatGPT project name already exists. Sync instead.');
    }
    if (Object.keys(journal.records).length >= 10000) throw fail('Project creation journal capacity reached.', 503);
    record = { contentHash, state: 'pending', updatedAt: Date.now() };
    journal.records[key] = record;
    writeJournal(deps.journalFile, journal); // Write BEFORE clicking Create.
    let project;
    try { project = await deps.create(name); }
    catch (_) { throw fail('Project creation outcome is uncertain. Sync and inspect ChatGPT before retrying.', 502); }
    const id = project?.chatgpt_project_id || project?.id;
    if (!/^g-p-[A-Za-z0-9_-]{5,190}$/.test(String(id || '')) || project?.name !== name) {
      throw fail('ChatGPT did not confirm the new project. Sync before retrying.', 502);
    }
    record.project = { id, chatgpt_project_id: id, name };
    record.state = 'project_created';
    record.updatedAt = Date.now();
    writeJournal(deps.journalFile, journal);
  }
  if (instructions) {
    try {
      const result = await deps.instructions({ project_id: record.project.id, project_name: name, instructions });
      if (!result?.success || !result?.instructions_saved || !result?.verified) throw Error('Not verified');
    } catch (_) {
      throw fail('Project created in ChatGPT, but instructions were not verified. Retry this same request to finish synchronization without creating another project.', 502);
    }
  }
  if (await deps.account() !== account) throw fail('ChatGPT account changed during project creation. Sync before retrying.');
  record.state = 'complete';
  record.updatedAt = Date.now();
  writeJournal(deps.journalFile, journal);
  return { ok: true, account_id: account, project: record.project, instructions_saved: true, replayed: false };
}

function assertChatAccount(body, actualAccount) {
  const expected = body && body.account_id;
  if ((String(body && body.thread_id || '').startsWith('aibit:') || expected) &&
      (!/^[a-f0-9]{64}$/.test(String(expected || '')) || expected !== actualAccount)) {
    throw fail('ChatGPT account changed before sending. No message was sent; reconnect the original account.');
  }
}

module.exports = { createSynchronizedProject, assertChatAccount };

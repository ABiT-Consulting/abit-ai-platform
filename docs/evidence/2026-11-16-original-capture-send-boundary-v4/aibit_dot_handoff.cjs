'use strict';
const crypto = require('node:crypto');
const DOT_URL = /^\/dots\/[A-Za-z0-9_-]{8,180}~[A-Za-z0-9_-]{8,180}$/;

function safeDotUrl(raw) {
  if (typeof raw !== 'string' || raw.length > 320) return '';
  try {
    const url = new URL(raw);
    if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com' ||
        url.port || url.username || url.password || url.search || url.hash ||
        !DOT_URL.test(url.pathname)) return '';
    return url.origin + url.pathname;
  } catch (_) { return ''; }
}
function dotLocationMatches(actual, expected) {
  const approved = safeDotUrl(expected);
  if (!approved) return false;
  try {
    const url = new URL(actual);
    return url.origin === 'https://chatgpt.com' &&
           (url.pathname === new URL(approved).pathname ||
            url.pathname.startsWith(new URL(approved).pathname + '/'));
  } catch (_) { return false; }
}
// A Dot landing page can become a conversation URL after Send.  The browser
// stays within the same verified, owner-scoped ChatGPT tab; an HTTPS /c/<id>
// transition does not invalidate the original Dot selection.
function dotResponseLocationMatches(actual, expected) {
  if (dotLocationMatches(actual, expected)) return true;
  if (!safeDotUrl(expected)) return false;
  try {
    const url=new URL(actual);
    if (url.protocol!=='https:' || url.hostname!=='chatgpt.com' ||
        url.port || url.username || url.password || url.hash) return false;
    return /^\/c\/[A-Za-z0-9_-]{5,190}\/?.*$/.test(url.pathname) ||
           /^\/g\/g-p-[A-Za-z0-9_-]{8,190}\/c\/[A-Za-z0-9_-]{5,190}\/?.*$/.test(url.pathname);
  } catch (_) { return false; }
}

function validateDotHandoff(body, configuredUrl, configuredOwner) {
  const url = safeDotUrl(configuredUrl);
  const owner = String(body && body.owner || '');
  if (!url || !owner || owner !== configuredOwner) {
    const error = new Error('abitdot has not been configured for this owner.');
    error.statusCode = 424;
    error.notSubmitted = true;
    throw error;
  }
  if (!/^aibit_[a-f0-9]{48}$/.test(String(body.job_id || ''))) {
    const error = new Error('A durable ABIT.AI relay job reference is required.');
    error.statusCode = 400;
    error.notSubmitted = true;
    throw error;
  }
  if (!/^[a-f0-9]{64}$/.test(String(body.account_id || ''))) {
    const error = new Error('The verified ChatGPT account fingerprint is required.');
    error.statusCode = 400;
    error.notSubmitted = true;
    throw error;
  }
  const message = String(body.message || '');
  if (!message.trim() || message.length > 160000) {
    const error = new Error('Complete abitdot context is required and must fit the relay limit.');
    error.statusCode = 400;
    error.notSubmitted = true;
    throw error;
  }
  const thread_id = 'aibit:' + owner + ':dot_' + body.job_id;
  return {url, owner, message, thread_id};
}
function safeToUseSecondary(submitBoundary, submitted, message, notSubmitted) {
  if (!submitBoundary) return true;
  if (submitted) return false;
  const detail = String(message || '').toLowerCase();
  return Boolean(notSubmitted) && !/uncertain|no resend/.test(detail);
}
function dotKey(owner, jobId) {
  return crypto.createHash('sha256').update(JSON.stringify([owner,jobId])).digest('hex');
}
module.exports = {safeDotUrl, dotLocationMatches, dotResponseLocationMatches,
  validateDotHandoff, safeToUseSecondary, dotKey};

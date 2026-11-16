'use strict';
// Actual Python-generated relay message through the deployed Node feed parser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {dotFeedSnapshotDOM} = require('../2026-11-16-owned-prompt-rejection-v5/aibit_dot_feed.cjs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function row(id, self, text) {
  const body = {innerText: text, textContent: text};
  return {innerText: text, textContent: text,
    classList: {contains: name => name === 'self' && self},
    getAttribute: name => name === 'data-message-id' ? id : null,
    querySelector: () => body};
}
function snapshot(expected) {
  global.document = {querySelectorAll: () => [
    row('fixture-original', true, expected),
    row('fixture-reply', false, 'AGOV RESULT: ' + JSON.stringify(input.result))]};
  try { return dotFeedSnapshotDOM(expected, null, ''); }
  finally { delete global.document; }
}
assert.equal(snapshot(input.message).complete, true, 'actual compact dispatcher message must retain Node binding');
const fixed = 'Execute this exact versioned ERP work packet in a separate abitdot request. ';
assert.ok(input.message.includes(fixed));
assert.equal(snapshot(input.message.replace(fixed, 'Changed transport prefix. ')).complete, false);
process.stdout.write('Actual generated message → Node feed binding passed\n');

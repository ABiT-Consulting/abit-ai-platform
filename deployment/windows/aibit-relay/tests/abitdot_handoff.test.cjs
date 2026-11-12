'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {
  safeDotUrl, dotLocationMatches, validateDotHandoff,
  safeToUseSecondary, dotKey,
} = require('../aibit_dot_handoff.cjs');
const url = 'https://chatgpt.com/dots/65f7e516-abc8-4c3f-9ffa-e8afca9cfaed~6ac25fb93f1c8192931d7692e8dc9a0e';
const owner = 'owner-for-test';
const job = 'aibit_' + 'e'.repeat(48);
const account = 'a'.repeat(64);
const request = {owner, job_id:job, account_id:account, message:'complete original project prompt and context'};

test('restrict Dot host and path to configured owner', () => {
  assert.equal(safeDotUrl(url), url);
  for (const bad of [
    'http://chatgpt.com/dots/abc~def',
    'https://chatgpt.com.evil.test/dots/abc~def',
    url + '?redirect=evil', url + '#fragment',
    'https://chatgpt.com/c/another-room',
    'https://evil.test/dots/dot~owner',
  ]) assert.equal(safeDotUrl(bad), '');
  assert.equal(dotLocationMatches(url + '/chat',url),true);
  assert.equal(dotLocationMatches('https://chatgpt.com/c/other',url),false);
});
test('require real ownership, account fingerprint and a durable turn', () => {
  const d=validateDotHandoff(request,url,owner);
  assert.equal(d.thread_id,'aibit:'+owner+':dot_'+job);
  assert.throws(()=>validateDotHandoff({...request,owner:'other'},url,owner));
  assert.throws(()=>validateDotHandoff({...request,job_id:'bad'},url,owner));
  assert.throws(()=>validateDotHandoff({...request,account_id:'unknown'},url,owner));
  assert.throws(()=>validateDotHandoff({...request,message:''},url,owner));
  assert.throws(()=>validateDotHandoff(request,'',owner));
});
test('identical turns produce identical idempotency keys', () => {
  assert.equal(dotKey(owner,job),dotKey(owner,job));
  assert.notEqual(dotKey('other',job),dotKey(owner,job));
});
test('selected model allowed only with verified pre-submission Dot failure', () => {
  assert.equal(safeToUseSecondary(false,false,'Dot URL not found',true),true);
  assert.equal(safeToUseSecondary(true,false,'No message sent.',true),true);
  assert.equal(safeToUseSecondary(true,true,'timeout',false),false);
  assert.equal(safeToUseSecondary(true,false,'acknowledgement uncertain',false),false);
  assert.equal(safeToUseSecondary(true,false,'timeout',false),false);
});

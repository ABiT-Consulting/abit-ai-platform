'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const {hold}=require('./browser_lock_keeper.cjs');
const modulePath=path.resolve(__dirname,'../../../docs/evidence/2026-11-22-restored-target-activation/reference/shared_chatgpt_browser_lock.cjs');
assert.equal(crypto.createHash('sha256').update(fs.readFileSync(modulePath)).digest('hex'),
  'a3625394a8cfb684d742434f17feb0af66292279801e7012eb2ddb7419ba8081');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check){const end=Date.now()+2000;while(!check()){assert.ok(Date.now()<end,'fixture deadline');await sleep(5);}}
function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'shared-lock-keeper-test-'));
  const lock=path.join(root,'browser.lock');const prior=process.env.ABIT_SHARED_CHATGPT_LOCK_FILE;
  process.env.ABIT_SHARED_CHATGPT_LOCK_FILE=lock;delete require.cache[modulePath];
  const api=require(modulePath);
  t.after(()=>{delete require.cache[modulePath];if(prior===undefined)delete process.env.ABIT_SHARED_CHATGPT_LOCK_FILE;else process.env.ABIT_SHARED_CHATGPT_LOCK_FILE=prior;fs.rmSync(root,{recursive:true,force:true});});
  return {root,lock,withLock:api.withSharedBrowserLock};
}

test('actual shared lock remains owned through unknown operator state until exact release',async t=>{
  const f=fixture(t);let held=false,release=false,released=0,probeErrors=true;
  const task=hold({withLock:f.withLock,onHeld(){held=true;},canRelease(){if(probeErrors)throw Error('synthetic disconnected operator');return release;},onReleased(){released++;}});
  t.after(async()=>{probeErrors=false;release=true;await task;});
  await until(()=>held);const original=fs.readFileSync(f.lock);
  await assert.rejects(f.withLock(async()=>{throw Error('must not enter original owner lock');},{waitMs:40}),
    error=>error.code==='CHATGPT_BROWSER_LOCK_BUSY');
  assert.deepEqual(fs.readFileSync(f.lock),original);assert.equal(released,0);
  probeErrors=false;await sleep(120);assert.deepEqual(fs.readFileSync(f.lock),original);
  release=true;await task;
  assert.equal(released,1);assert.equal(fs.existsSync(f.lock),false);
});

test('interrupted acquisition has no held proof and cannot claim a shared lock',async t=>{
  const f=fixture(t);let released=0,held=0;
  await assert.rejects(hold({withLock:f.withLock,onHeld(){held++;throw Error('synthetic receipt write interruption');},
    canRelease(){throw Error('must not poll unrecorded keeper');},onReleased(){released++;}}));
  assert.equal(held,1);assert.equal(released,0);assert.equal(fs.existsSync(f.lock),false);
});

test('application lock release never unlinks a replacement owner',async t=>{
  const f=fixture(t);let held=false,release=false;
  const task=hold({withLock:f.withLock,onHeld(){held=true;},canRelease(){return release;},onReleased(){}});
  t.after(async()=>{release=true;await task;});await until(()=>held);
  const foreign=JSON.stringify({owner:'synthetic-foreign-owner',pid:process.pid,createdAt:Date.now()});
  fs.writeFileSync(f.lock,foreign);release=true;await task;
  assert.equal(fs.readFileSync(f.lock,'utf8'),foreign);
});

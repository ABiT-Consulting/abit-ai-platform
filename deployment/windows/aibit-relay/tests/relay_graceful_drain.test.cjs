'use strict';
// Real loopback HTTP + exact source functions. No browser, provider, root or live service.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const vm = require('node:vm');
const {once} = require('node:events');
const {makeErpRelay} = require('../erp_task_relay.cjs');
const {chatThreadKey} = require('../../../original-restored-target-diagnostic-20261122/reference/chat_thread_tabs.cjs');
const source = fs.readFileSync(path.join(__dirname, '../aibit_relay.cjs'), 'utf8');
const originalErp = fs.readFileSync(path.join(__dirname, '../erp_task_relay.cjs'), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, limit=2500) { const end=Date.now()+limit; while(!fn()) { if(Date.now()>end)throw Error('fixture deadline');await delay(10); } }
function sliceExact(start, end) {
  const first=source.indexOf(start);assert.notEqual(first,-1);
  const last=source.indexOf(end,first+start.length);assert.notEqual(last,-1);
  return source.slice(first,last);
}
const reservations=sliceExact('let activeRelayTurns = 0;', "let dedicatedTargetId = '';");
const reader=sliceExact('function readJson(req) {', 'function boundedText(');
const lifecycle=source.slice(source.indexOf('// Count handler promises, not just live sockets:'));
assert.ok(lifecycle.includes("process.on('SIGTERM', beginRelayShutdown)"));

async function relayFixture(t, handler, erpRelay=null) {
  const exits=[]; const signals={}; const timers=new Set(); let calls=0;
  const context=vm.createContext({
    http, Buffer, URL, PORT:0, HOST:'127.0.0.1', erpRelay, REQUEST_LIMIT:4096,
    process:{pid:process.pid,exit:code=>exits.push(code),on:(name,fn)=>{signals[name]=fn;}},
    console:{log(){}},
    setInterval:(fn,ms)=>{const id=setInterval(fn,ms);timers.add(id);return id;},
    clearInterval:id=>{clearInterval(id);timers.delete(id);},
    chatThreadTabs:{key:chatThreadKey}, MAX_PARALLEL_RELAY_TURNS:4, activeRelink:()=>false,
    requestPath:req=>new URL(req.url||'/', 'http://127.0.0.1').pathname,
    json:(res,status,value)=>{const text=JSON.stringify(value);res.writeHead(status,{'Content-Type':'application/json','Content-Length':Buffer.byteLength(text)});res.end(text);},
    handleRelayRequest:(req,res)=>{calls++;return handler(req,res,context);},
  });
  vm.runInContext(reservations+'\n'+reader+'\n'+lifecycle+'\nglobalThis.fixture={server,relayRuntimeStatus,beginRelayShutdown,reserveRelayTurn,readJson};',context);
  const api=context.fixture; await once(api.server,'listening');
  t.after(async()=>{
    for(const timer of timers)clearInterval(timer);
    api.server.closeAllConnections();
    await new Promise(resolve=>api.server.close(()=>resolve()));
  });
  return {context,api,exits,signals,port:api.server.address().port,calls:()=>calls};
}
function request(port, route='/work') {
  const req=http.request({hostname:'127.0.0.1',port,path:route,method:'GET',agent:false});
  req.on('error',()=>{});req.end();return req;
}
function body(root, id='a'.repeat(64), deadline=Date.now()/1000+90) {
  return {id,deadline,account:'b'.repeat(64),chatgpt_project:'g-p-'+'c'.repeat(32),
    owner:'synthetic-owner',project:'synthetic-project',agent:'synthetic-agent',task:'synthetic-task'};
}
function erpFixture(t, overrides={}) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'relay-drain-fixture-'));
  const counts={reserve:0,release:0,run:0,recover:0,cleanup:0};
  const relay=makeErpRelay({root,
    reserve:()=>{counts.reserve++;return()=>{counts.release++;};},
    preflight:async()=>({ready:true}),
    run:async(...args)=>{counts.run++;return overrides.run?overrides.run(...args):'synthetic result';},
    recover:async(...args)=>{counts.recover++;return overrides.recover?overrides.recover(...args):{};},
    cleanup:async()=>{counts.cleanup++;},
  });
  t.after(()=>{relay.beginDrain();fs.rmSync(root,{recursive:true,force:true});});
  return {root,relay,counts};
}

// The old socket-only SIGTERM would exit while this detached handler still owns work.
test('disconnected HTTP handler remains owned until its promise settles',async t=>{
  const pending=deferred(),started=deferred();
  const fixture=await relayFixture(t,async()=>{started.resolve();await pending.promise;});
  t.after(()=>pending.resolve());
  const req=request(fixture.port);await started.promise;req.destroy();await delay(30);
  fixture.signals.SIGTERM();
  await until(()=>fixture.api.relayRuntimeStatus().server_closed);
  assert.equal(fixture.api.relayRuntimeStatus().active_http_handlers,1);
  assert.equal(fixture.api.relayRuntimeStatus().active_relay_turns,0);
  assert.deepEqual(fixture.exits,[]);
  pending.resolve();await until(()=>fixture.exits.length===1);
  assert.equal(fixture.api.relayRuntimeStatus().active_http_handlers,0);
  assert.deepEqual(fixture.exits,[0]);
});

test('scoped turn started after an idle observation is retained through disconnect and drain',async t=>{
  const pending=deferred(),started=deferred();
  const fixture=await relayFixture(t,async(req,res,context)=>{
    const release=context.fixture.reserveRelayTurn({thread_id:'aibit:synthetic-owner:synthetic-scoped-chat'});
    started.resolve();try{await pending.promise;}finally{release();}
  });
  t.after(()=>pending.resolve());
  assert.equal(fixture.api.relayRuntimeStatus().active_relay_turns,0);
  const req=request(fixture.port);await started.promise;req.destroy();
  fixture.signals.SIGTERM();await delay(40);
  assert.equal(fixture.api.relayRuntimeStatus().active_relay_turns,1);
  assert.deepEqual(fixture.exits,[]);
  pending.resolve();await until(()=>fixture.exits.length===1);
  assert.equal(fixture.api.relayRuntimeStatus().active_relay_turns,0);
});

test('a delayed reservation admitted before sealing cannot begin provider work afterward',async t=>{
  const bodyReady=deferred(),started=deferred();let sends=0,denial=null;
  const fixture=await relayFixture(t,async(req,res,context)=>{
    started.resolve();await bodyReady.promise;
    try { const release=context.fixture.reserveRelayTurn({thread_id:'aibit:synthetic-owner:delayed'});sends++;release(); }
    catch(error){denial={code:error.code,status:error.statusCode};}
    res.end();
  });
  t.after(()=>bodyReady.resolve());
  const req=request(fixture.port);await started.promise;fixture.signals.SIGTERM();bodyReady.resolve();
  await until(()=>fixture.exits.length===1);
  assert.equal(sends,0);assert.deepEqual(denial,{code:'RELAY_DRAINING',status:503});req.destroy();
});

test('new requests and reservations are denied synchronously after sealing',async t=>{
  const fixture=await relayFixture(t,async()=>{throw Error('should not reach provider handler');});
  fixture.signals.SIGTERM();
  assert.throws(()=>fixture.api.reserveRelayTurn({thread_id:'aibit:synthetic-owner:fresh'}),error=>error.code==='RELAY_DRAINING'&&error.statusCode===503);
  const result={};const res={writeHead(status,headers){result.status=status;result.headers=headers;},end(text){result.body=JSON.parse(text);}};
  fixture.api.server.listeners('request')[0]({method:'POST',url:'/relay'},res);
  assert.equal(result.status,503);assert.equal(result.body.code,'RELAY_DRAINING');
  assert.equal(fixture.calls(),0);assert.equal(fixture.api.relayRuntimeStatus().active_http_handlers,0);
  assert.equal(fixture.api.relayRuntimeStatus().active_relay_turns,0);
});

test('runtime status is bounded coarse state and never calls the browser handler',async t=>{
  const fixture=await relayFixture(t,async()=>{throw Error('status must not call browser/account/health');});
  const response=await fetch('http://127.0.0.1:'+fixture.port+'/runtime-status',{headers:{Connection:'close'}});
  assert.equal(response.status,200);const value=await response.json();
  assert.deepEqual(Object.keys(value).sort(),['active_http_handlers','active_relay_turns','draining','erp_active_turns','erp_inspections','ok','pid','protocol','server_closed'].sort());
  assert.equal(value.protocol,'v1_graceful_relay_drain');assert.equal(value.pid,process.pid);
  assert.equal(value.draining,false);assert.equal(value.active_http_handlers,0);assert.equal(fixture.calls(),0);
  assert.ok(JSON.stringify(value).length<512);
  fixture.signals.SIGTERM();await until(()=>fixture.exits.length===1);
});

test('repeated shutdown signals create only one server close and keep the original drain',async t=>{
  const pending=deferred(),started=deferred();
  const fixture=await relayFixture(t,async()=>{started.resolve();await pending.promise;});
  t.after(()=>pending.resolve());
  const req=request(fixture.port);await started.promise;req.destroy();
  let closes=0;const close=fixture.api.server.close.bind(fixture.api.server);
  fixture.api.server.close=(...args)=>{closes++;return close(...args);};
  fixture.signals.SIGTERM();fixture.signals.SIGINT();fixture.signals.SIGTERM();
  assert.equal(closes,1);assert.deepEqual(fixture.exits,[]);
  pending.resolve();await until(()=>fixture.exits.length===1);assert.equal(closes,1);
});

test('ERP rejects new observe/stop/preflight work before journal mutation during drain',async t=>{
  const erp=erpFixture(t);erp.relay.beginDrain();
  for(const operation of ['observe','stop','preflight'])
    await assert.rejects(erp.relay(operation,body(erp.root)),error=>error.code==='RELAY_DRAINING'&&error.statusCode===503);
  assert.deepEqual(fs.readdirSync(erp.root),[]);
  assert.deepEqual(erp.relay.pending(),{active:0,inspections:0});
  assert.deepEqual(erp.counts,{reserve:0,release:0,run:0,recover:0,cleanup:0});
});

test('ERP admitted turn remains owned and completes without cancellation after drain',async t=>{
  const pending=deferred(),started=deferred();
  const erp=erpFixture(t,{run:async(row,lifecycle)=>{lifecycle.sending();started.resolve();await pending.promise;return 'retained synthetic result';}});
  t.after(()=>pending.resolve());
  await erp.relay('observe',body(erp.root));await started.promise;erp.relay.beginDrain();
  assert.deepEqual(erp.relay.pending(),{active:1,inspections:0});
  assert.equal(erp.counts.release,0);pending.resolve();await until(()=>erp.relay.pending().active===0);
  const row=JSON.parse(fs.readFileSync(path.join(erp.root,'a'.repeat(64)+'.json')));
  assert.equal(row.status,'completed');assert.equal(row.findings,'retained synthetic result');
  assert.notEqual(row.stop,true);assert.equal(erp.counts.run,1);assert.equal(erp.counts.release,1);
});

test('actual ERP inspection map prevents Node exit after its HTTP client has gone',async t=>{
  const pending=deferred(),started=deferred();
  const erp=erpFixture(t,{recover:async()=>{started.resolve();await pending.promise;return {findings:'original synthetic reply'};}});
  t.after(()=>pending.resolve());
  const requestBody=body(erp.root);const crypto=require('node:crypto');
  const identity=crypto.createHash('sha256').update(JSON.stringify([requestBody.owner,requestBody.account,requestBody.chatgpt_project,requestBody.project,requestBody.agent,requestBody.deadline])).digest('hex');
  fs.writeFileSync(path.join(erp.root,requestBody.id+'.json'),JSON.stringify({...requestBody,identity,status:'waiting',sent:true,project:requestBody.chatgpt_project}));
  const fixture=await relayFixture(t,async(req,res)=>{await erp.relay('observe',requestBody);res.end();},erp.relay);
  const req=request(fixture.port);await started.promise;req.destroy();
  fixture.signals.SIGTERM();await until(()=>fixture.api.relayRuntimeStatus().server_closed);
  assert.equal(fixture.api.relayRuntimeStatus().erp_inspections,1);assert.deepEqual(fixture.exits,[]);
  pending.resolve();await until(()=>fixture.exits.length===1);
  assert.equal(fixture.api.relayRuntimeStatus().erp_inspections,0);
  assert.equal(JSON.parse(fs.readFileSync(path.join(erp.root,requestBody.id+'.json'))).findings,'original synthetic reply');
});

test('deadline observer cannot begin another row after an existing inspection drains',async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'relay-timer-drain-fixture-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  let tick,clears=0;const exported={exports:{}};
  vm.runInNewContext(originalErp,{require,module:exported,exports:exported.exports,
    setInterval:fn=>{tick=fn;return {unref(){}};},clearInterval:()=>{clears++;},Date,Promise});
  const pending=deferred(),started=deferred();let recoveries=0;
  const relay=exported.exports.makeErpRelay({root,reserve:()=>()=>{},run:async()=>'',preflight:async()=>({}),
    recover:async()=>{recoveries++;started.resolve();await pending.promise;return {};}});
  t.after(()=>{pending.resolve();relay.beginDrain();});
  const first={id:'a'.repeat(64),deadline:Date.now()/1000-10,status:'waiting',sent:true};
  const second={id:'b'.repeat(64),deadline:Date.now()/1000-10,status:'waiting',sent:true};
  fs.writeFileSync(path.join(root,first.id+'.json'),JSON.stringify(first));
  const originalSecond=JSON.stringify(second);fs.writeFileSync(path.join(root,second.id+'.json'),originalSecond);
  const running=tick();await started.promise;relay.beginDrain();pending.resolve();await running;
  assert.equal(recoveries,1);assert.equal(clears,1);
  assert.equal(fs.readFileSync(path.join(root,second.id+'.json'),'utf8'),originalSecond);
  assert.equal(relay.pending().inspections,0);
});


test('aborted partial JSON body cannot leave a detached handler counted forever',async t=>{
  const started=deferred();
  const fixture=await relayFixture(t,async(req,res,context)=>{started.resolve();await context.fixture.readJson(req);res.end();});
  const req=http.request({hostname:'127.0.0.1',port:fixture.port,path:'/relay',method:'POST',headers:{'Content-Length':'100'},agent:false});
  req.on('error',()=>{});req.write('{');await started.promise;req.destroy();
  fixture.signals.SIGTERM();await until(()=>fixture.exits.length===1);
  assert.equal(fixture.api.relayRuntimeStatus().active_http_handlers,0);
});

test('a rejected streaming handler settles without writing a second response header',async t=>{
  const started=deferred(),pending=deferred();
  const fixture=await relayFixture(t,async(req,res)=>{res.writeHead(200,{'Content-Type':'text/plain'});res.write('started');started.resolve();await pending.promise;throw Error('synthetic private failure');});
  t.after(()=>pending.resolve());const req=request(fixture.port);await started.promise;
  fixture.signals.SIGTERM();pending.resolve();await until(()=>fixture.exits.length===1);
  assert.equal(fixture.api.relayRuntimeStatus().active_http_handlers,0);req.destroy();
});

'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const crypto=require('node:crypto');
const {createProjectInBrowser,saveProjectInstructionsInBrowser}=require('../deployment/windows/aibit-relay/aibit_project_browser.cjs');
const ID='g-p-browserapitest123456';
const EMAIL='fixture@example.invalid', TOKEN='test-only-never-a-live-token';
const ACCOUNT=crypto.createHash('sha256').update(EMAIL).digest('hex');
function fixture(options={}){
 const state={calls:[],created:false,name:'Test project',instructions:options.instructions??'Test instructions',
   before:false,captured:null,postStatus:options.postStatus||200,getCount:0};
 const gizmo=()=>({id:options.spaceInstead?'page_space_test':ID,display:{name:state.name,emoji:'X',theme:'fixture'},instructions:state.instructions});
 const reply=(status,body)=>({ok:status>=200&&status<300,status,json:async()=>body});
 const context=vm.createContext({location:{origin:options.origin||'https://chatgpt.com'},crypto:crypto.webcrypto,TextEncoder,Uint8Array,AbortSignal,
  fetch:async(url,init={})=>{
   state.calls.push({url,method:init.method||'GET',body:init.body?JSON.parse(init.body):null});
   if(url==='/api/auth/session')return reply(options.authStatus||200,{user:{email:EMAIL},accessToken:TOKEN});
   assert.equal(init.headers.Authorization,'Bearer '+TOKEN);
   if(url==='/backend-api/projects'){
    assert.equal(state.before,true,'durable beforeSubmit must run before dispatch');
    if(options.networkFailure)throw Error('Unknown submission outcome');
    if(state.postStatus!==200)return reply(state.postStatus,{detail:'Do not return provider body '+TOKEN});
    state.created=true;state.name=JSON.parse(init.body).name;state.instructions=JSON.parse(init.body).instructions;
    return reply(200,{resource:{gizmo:gizmo()}});
   }
   if(url==='/backend-api/projects/'+ID){state.instructions=JSON.parse(init.body).instructions;return reply(200,{resource:{gizmo:gizmo()}});}
   if(url==='/backend-api/gizmos/'+ID){
    state.getCount++;if(options.getFailure)return reply(503,{error:'unavailable'});
    const value=gizmo();if(options.verifyMismatch&&state.getCount>1)value.instructions='not saved';
    return reply(200,{gizmo:value});
   }
   throw Error('Unexpected API route');
  }});
 const session={call:async(method,params)=>{
  assert.equal(method,'Runtime.evaluate');
  if(options.cdpTimeout)throw Error('CDP timed out');
  return {result:{value:await vm.runInContext(params.expression,context)}};
 }};
 const lifecycle={account:ACCOUNT,instructions:'Test instructions',beforeSubmit:async()=>{state.before=true},onCreated:project=>{state.captured=project}};
 return {session,state,lifecycle};
}
test('actual in-page implementation creates a g-p project with name and instructions',async()=>{
 const f=fixture();const result=await createProjectInBrowser(f.session,'Test project',f.lifecycle);
 assert.equal(result.id,ID);assert.equal(f.state.created,true);assert.equal(f.state.captured.id,ID);
 assert.deepEqual(f.state.calls.find(c=>c.url==='/backend-api/projects').body,{name:'Test project',instructions:'Test instructions'});
 assert.ok(!JSON.stringify(result).includes(TOKEN));assert.ok(!JSON.stringify(result).includes(EMAIL));
});
test('before-submit failure never sends a creation request',async()=>{
 const f=fixture();f.lifecycle.beforeSubmit=async()=>{throw Error('Journal unavailable')};
 await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle));assert.equal(f.state.calls.length,0);
});
test('page checks expected account before submitting',async()=>{
 const f=fixture();f.lifecycle.account='b'.repeat(64);
 await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle),e=>e.notSubmitted===true);
 assert.equal(f.state.created,false);assert.equal(f.state.calls.length,1);
});
test('expired login fails safely before project POST',async()=>{
 const f=fixture({authStatus:401});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle),e=>e.notSubmitted===true);
 assert.equal(f.state.created,false);
});
test('definite validation rejection permits same request retry without leaking provider text',async()=>{
 const f=fixture({postStatus:422});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle),e=>e.notSubmitted===true&&!e.message.includes(TOKEN));
 assert.equal(f.state.created,false);
});
test('unknown network outcome cannot be classified as not submitted',async()=>{
 const f=fixture({networkFailure:true});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle),e=>e.notSubmitted===false);
});
test('CDP timeout remains uncertain rather than permitting another create',async()=>{
 const f=fixture({cdpTimeout:true});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle),e=>e.notSubmitted===false);
});
test('created project ID is captured before a failing read-back',async()=>{
 const f=fixture({getFailure:true});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle));
 assert.equal(f.state.captured.id,ID);assert.equal(f.state.calls.filter(c=>c.method==='POST').length,1);
});
test('Library Space result is rejected instead of being treated as a project',async()=>{
 const f=fixture({spaceInstead:true});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle));assert.equal(f.state.captured,null);
});
test('instructions already saved during creation are verified without another write',async()=>{
 const f=fixture();const result=await saveProjectInstructionsInBrowser(f.session,{project_id:ID,project_name:'Test project',instructions:'Test instructions',account_id:ACCOUNT});
 assert.equal(result.verified,true);assert.equal(f.state.calls.filter(c=>c.method!=='GET').length,0);assert.equal(f.state.getCount,2);
});
test('instruction update preserves existing name and appearance and verifies saved value',async()=>{
 const f=fixture({instructions:'old'});const result=await saveProjectInstructionsInBrowser(f.session,{project_id:ID,project_name:'Test project',instructions:'Updated',account_id:ACCOUNT});
 assert.equal(result.verified,true);assert.deepEqual(f.state.calls.find(c=>c.method==='PATCH').body,{name:'Test project',instructions:'Updated',emoji:'X',theme:'fixture'});
});
test('instruction read-back mismatch is not false success',async()=>{
 const f=fixture({instructions:'old',verifyMismatch:true});await assert.rejects(saveProjectInstructionsInBrowser(f.session,{project_id:ID,project_name:'Test project',instructions:'Updated',account_id:ACCOUNT}));
});
test('renamed or different project is never overwritten',async()=>{
 const f=fixture();await assert.rejects(saveProjectInstructionsInBrowser(f.session,{project_id:ID,project_name:'Different',instructions:'Updated',account_id:ACCOUNT}));
 assert.equal(f.state.calls.filter(c=>c.method==='PATCH').length,0);
});
test('untrusted browser origin cannot receive credential or project requests',async()=>{
 const f=fixture({origin:'https://example.invalid'});await assert.rejects(createProjectInBrowser(f.session,'Test project',f.lifecycle),e=>e.notSubmitted===true);assert.equal(f.state.calls.length,0);
});

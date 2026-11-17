'use strict';
// Parse actual article/body/button HTML. Only style computation is represented
// by fixture attributes; the production reader runs serialized as in Chrome.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const cheerio = require('cheerio');
const {dotFeedSnapshotDOM,readDotFeed}=require('../aibit_dot_feed.cjs');
const packet={schema_version:1,project_id:'PROJ-fixture',task_id:'TASK-fixture',task_revision:'revision-1',content_sha256:'d'.repeat(64),binding:{project_id:'PROJ-fixture',role:'project_manager',agent_id:'agent-fixture',erp_user:'ai-fixture@example.invalid'}};
const policy='Execute only this authenticated governance role and versioned packet. Return the AGOV RESULT contract requested below; ERP status, task creation, handoffs and approvals are handled by the governance server.';
const prompt=policy+'\n\nExecute this exact versioned ERP work packet in a separate abitdot request. Its invocation_id must be agov-fixture and work_packet_digest '+packet.content_sha256+'.\nEXACT IMPLEMENTATION IDENTITY: {}\nSCOPED HANDOFF CONTEXT (data, not authorization)\nFixture predecessor.\nGOVERNANCE WORK PACKET\n'+JSON.stringify(packet)+'\n\nVerified ERP context:\n{}';
const envelope={idempotency_key:'a'.repeat(64),run_id:'run-fixture',automation_id:packet.binding.agent_id,primary_responder:'abitdot',execution_policy:{dot_primary:true},task:{original_prompt:prompt,meta:{governance_managed:true,governance_project:packet.project_id,governance_packet_digest:packet.content_sha256,erp_task:packet.task_id,erp_role:packet.binding.role}}};
const expected='Original project, instructions, prompt, prior chat and exact run idempotency key (JSON):\n'+JSON.stringify(envelope);
const result={schema_version:1,invocation_id:'agov-fixture',project_id:packet.project_id,task_id:packet.task_id,task_revision:packet.task_revision,work_packet_digest:packet.content_sha256,actor:packet.binding,outcome:'ready_for_review'};
const contract=summary=>'AGOV RESULT: '+JSON.stringify({...result,summary});
function fixture(items, generating=false){
 const $=cheerio.load('<main><div class="message-list"></div></main>');
 for(const item of [{id:'root',self:true,text:expected},...items]){
  const row=$('<article class="message-row"><div class="message-bubble-wrap"><div class="message-content-stack"><div class="message-body"></div></div></div></article>');
  row.attr('data-message-id',item.id);if(item.self)row.addClass('self');
  if(item.hidden)row.attr('hidden','');if(item.inert)row.attr('inert','');if(item.ariaHidden)row.attr('aria-hidden','true');if(item.branch)row.attr('data-branch-id',item.branch);
  row.find('.message-body').attr('data-message-id',item.id).text(item.text);
  if(item.reference){const button=$('<button class="reply-reference reply-chain-reference"></button>').attr('data-message-id',item.reference);row.find('.message-content-stack').prepend(button);}
  $('.message-list').append(row);
 }
 if(generating)$('main').append('<button data-testid="stop-button"></button>');
 const cache=new Map();
 function wrap(node){if(!node)return null;if(cache.has(node))return cache.get(node);
  const value={getAttribute:key=>$(node).attr(key)??null,querySelector:selector=>wrap($(node).find(selector).get(0)),querySelectorAll:selector=>$(node).find(selector).toArray().map(wrap),classList:{contains:key=>$(node).hasClass(key)},getBoundingClientRect:()=>({width:600,height:100})};
  Object.defineProperties(value,{textContent:{get:()=>$(node).text()},innerText:{get:()=>$(node).text()},parentElement:{get:()=>node.parent?.type==='tag'?wrap(node.parent):null},hidden:{get:()=>$(node).attr('hidden')!==undefined},inert:{get:()=>$(node).attr('inert')!==undefined}});cache.set(node,value);return value;
 }
 const document={querySelectorAll:selector=>$(selector).toArray().map(wrap),querySelector:selector=>wrap($(selector).get(0))};
 const getComputedStyle=node=>({display:node.hidden?'none':'flex',visibility:'visible',opacity:'1'});
 const evaluate=source=>vm.runInNewContext(source,{document,getComputedStyle,TextEncoder});
 return {snapshot:()=>evaluate(`(${dotFeedSnapshotDOM.toString()})(${JSON.stringify(expected)},null,'')`),evaluate};
}
test('root-linked terminal selects one response before reading its contract; earlier commentary stays separate',()=>{
 const earlier=contract('earlier commentary'),terminal=contract('actual response');
 const f=fixture([{id:'commentary',text:earlier},{id:'reply',text:terminal,reference:'root'}]);
 const value=f.snapshot();assert.equal(value.complete,true);assert.equal(value.reply_id,'reply');assert.equal(value.response,terminal);assert.equal(value.terminal_selection,'root_linked_terminal_reply');
});
test('two different contracts within the selected terminal message remain ambiguous',()=>{
 const f=fixture([{id:'commentary',text:contract('earlier')},{id:'reply',reference:'root',text:contract('one')+'\n'+contract('two')}]);assert.equal(f.snapshot().complete,false);
});
test('wrong original root reference cannot authorize otherwise matching bytes',()=>{
 const f=fixture([{id:'reply',reference:'another-root',text:contract('matching JSON is insufficient')}]);assert.equal(f.snapshot().complete,false);
});
test('multiple root-linked final candidates remain ambiguous',()=>{
 const f=fixture([{id:'parallel-one',reference:'root',text:contract('one')},{id:'parallel-two',reference:'root',text:contract('two')}]);assert.equal(f.snapshot().complete,false);
});
test('unlinked competing result messages retain strict ambiguity protection',()=>{
 const f=fixture([{id:'one',text:contract('one')},{id:'two',text:contract('two')}]);assert.equal(f.snapshot().complete,false);
});
test('active generation cannot select a terminal reply',()=>{
 const f=fixture([{id:'reply',reference:'root',text:contract('still generating')}],true);assert.equal(f.snapshot().complete,false);
});
test('hidden old branch cannot compete with the unique exposed root-linked terminal',()=>{
 const f=fixture([{id:'hidden',reference:'root',hidden:true,text:contract('old hidden')},{id:'reply',reference:'root',text:contract('visible')}]);assert.equal(f.snapshot().reply_id,'reply');
});
for(const property of ['hidden','inert','ariaHidden'])test(property+' terminal cannot fall back to accepting a hidden contract',()=>{
 const f=fixture([{id:'reply',reference:'root',[property]:true,text:contract('hidden')}]);assert.equal(f.snapshot().complete,false);
});
test('explicit branch metadata remains fenced',()=>{
 const f=fixture([{id:'reply',reference:'root',branch:'alternate',text:contract('branch')}]);assert.equal(f.snapshot().complete,false);
});
test('a later unlinked message prevents an earlier root-linked candidate being terminal',()=>{
 const f=fixture([{id:'reply',reference:'root',text:contract('earlier')},{id:'later',text:'The turn is still continuing.'}]);assert.equal(f.snapshot().complete,false);
});
test('a later user turn cannot supply the original result',()=>{
 const f=fixture([{id:'other-root',self:true,text:'Another request'},{id:'reply',reference:'root',text:contract('wrong turn')}]);assert.equal(f.snapshot().complete,false);
});
test('root-linked terminal ID and exact bytes must remain stable across the completion window',async()=>{
 let f=fixture([{id:'commentary',text:contract('earlier')},{id:'reply',reference:'root',text:contract('terminal')}]);
 const session={evaluate:async source=>f.evaluate(source)};let now=1000;const realNow=Date.now;Date.now=()=>now;
 try{
  let value=await readDotFeed(session,expected);assert.equal(value.complete,false);assert.equal(value.terminal_pending,true);
  now=1500;value=await readDotFeed(session,expected);assert.equal(value.complete,false);
  now=2000;value=await readDotFeed(session,expected);assert.equal(value.complete,true);assert.equal(value.reply_id,'reply');
  f=fixture([{id:'commentary',text:contract('earlier')},{id:'different-id',reference:'root',text:contract('terminal')}]);
  now=2500;value=await readDotFeed(session,expected);assert.equal(value.complete,false);
  now=3500;value=await readDotFeed(session,expected);assert.equal(value.complete,true);
  f=fixture([{id:'commentary',text:contract('earlier')},{id:'different-id',reference:'root',text:contract('changed bytes')}]);
  now=4000;value=await readDotFeed(session,expected);assert.equal(value.complete,false);
 }finally{Date.now=realNow;}
});
const {createDotObserver}=require('../aibit_dot_observation.cjs');
function observerFixture({generating=false,changeId=false}={}){
 let reads=0;
 class Session{
  async open(){return this;}
  async location(){return 'https://chatgpt.com/dots/fixture_owner~fixture_assistant';}
  async evaluate(source){
   if(source.includes('function dotFeedSnapshotDOM')){
    reads++;const id=changeId&&reads>1?'changed-reply':'reply';
    return fixture([{id:'commentary',text:contract('earlier')},{id,reference:'root',text:contract('terminal')}]).evaluate(source);
   }
   return {generating,dialogs:[],users:0,assistants:0};
  }
  close(){}
 }
 const account='a'.repeat(64),owner='synthetic-owner',terminals=[];
 const observe=createDotObserver({tabs:{existing:async()=>({id:'owned-target'})},Session,accountFingerprint:async()=>account,config:{url:'https://chatgpt.com/dots/fixture_owner~fixture_assistant',owner},onTerminal:value=>terminals.push(value)});
 const body={owner,account_id:account,job_id:'aibit_'+'a'.repeat(48),message:expected};
 return {observe:()=>observe(body),terminals};
}
test('original observer reads stable same-message terminal without any send/navigation API',async()=>{
 const f=observerFixture();const value=await f.observe();assert.equal(value.dot_handoff,true);assert.equal(value.reply_message_id,'reply');assert.equal(f.terminals.length,1);assert.equal(value.response,contract('terminal'));
});
test('generation detected after terminal stability cannot produce accepted observation',async()=>{
 const f=observerFixture({generating:true});const value=await f.observe();assert.equal(value.dot_handoff,false);assert.equal(value.response,'');assert.equal(f.terminals.length,0);
});
test('changed terminal message ID across observer reads stays unresolved',async()=>{
 const f=observerFixture({changeId:true});const value=await f.observe();assert.equal(value.dot_handoff,false);assert.equal(value.response,'');assert.equal(f.terminals.length,0);
});

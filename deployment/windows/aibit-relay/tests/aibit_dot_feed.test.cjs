'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {dotFeedSnapshotDOM,readDotFeed} = require('../aibit_dot_feed.cjs');
const marker='a'.repeat(64), digest='b'.repeat(64), revision='c'.repeat(64);
function fixture(role='solution_architect') {
 const binding={agent_id:'agent-1',erp_user:'ai@example.invalid',project_id:'PROJ-1',role};
 const packet={binding,project_id:'PROJ-1',task_id:'TASK-1',task_revision:revision,content_sha256:digest};
 const prompt='Its invocation_id must be agov-original and work_packet_digest '+digest+
  '. EXACT IMPLEMENTATION IDENTITY: {}\nGOVERNANCE WORK PACKET\n'+JSON.stringify(packet)+
  '\nROLE INSTRUCTIONS: subsequent text is not part of the packet.';
 const context={idempotency_key:marker,run_id:'run-original',automation_id:'agent-1',
  task:{meta:{governance_managed:true,governance_packet_digest:digest,governance_project:'PROJ-1',
   erp_task:'TASK-1',erp_role:role},original_prompt:prompt}};
 const result={schema_version:1,invocation_id:'agov-original',work_packet_digest:digest,
  project_id:'PROJ-1',task_id:'TASK-1',task_revision:revision,actor:binding,outcome:'ready_for_review'};
 return {expected:'Approved context idempotency key (JSON):\n'+JSON.stringify(context),result,context};
}
function row(id,text,{self=false,emptyVisible=false}={}) {
 const body={innerText:emptyVisible?'':text,textContent:text};
 return {classList:{contains:c=>c==='self'&&self},textContent:text,innerText:body.innerText,
  getAttribute:k=>k==='data-message-id'?id:null,querySelector:()=>body};
}
function read(expected,rows,known='',markdown) {
 const old=global.document;
 global.document={querySelectorAll:s=>{assert.equal(s,'article.message-row[data-message-id]');return rows;}};
 try{return dotFeedSnapshotDOM(expected,markdown,known);}finally{global.document=old;}
}
function contract(result){return 'AGOV RESULT: '+JSON.stringify(result);}
test('SA, Developer, QA and PM terminal contracts remain bound to the original packet',()=>{
 for(const role of ['solution_architect','developer','qa','project_manager']) {
  const {expected,result}=fixture(role);
  if(role==='solution_architect')result.architect_plan={metadata:{run_id:'agov-original'}};
  const text=contract(result),rows=[row('old','unrelated'),row('original',expected,{self:true,emptyVisible:true}),row('reply',text)];
  const resultFeed=read(expected,rows,'',()=>{throw Error('Must not escape the governed JSON as Markdown');});
  assert.equal(resultFeed.complete,true);assert.equal(resultFeed.root_id,'original');
  assert.equal(resultFeed.reply_id,'reply');assert.equal(resultFeed.response,text);
 }
});
test('every governed identity mismatch, malformed JSON and duplicate contract is incomplete',()=>{
 const {expected,result}=fixture();
 for(const key of ['invocation_id','work_packet_digest','project_id','task_id','task_revision','schema_version','outcome']) {
  const changed=structuredClone(result);changed[key]='wrong';
  assert.equal(read(expected,[row('root',expected,{self:true}),row('reply',contract(changed))]).complete,false,key);
 }
 for(const key of ['agent_id','erp_user','project_id','role']) {
  const changed=structuredClone(result);changed.actor[key]='wrong';
  assert.equal(read(expected,[row('root',expected,{self:true}),row('reply',contract(changed))]).complete,false,key);
 }
 for(const text of ['AGOV RESULT: {',contract(result)+'\n'+contract(result),'AGOV RESULT: '+JSON.stringify(result)+' trailing']) {
  assert.equal(read(expected,[row('root',expected,{self:true}),row('reply',text)]).complete,false);
 }
 const wrongRun=structuredClone(result);wrongRun.run_id='other';
 assert.equal(read(expected,[row('root',expected,{self:true}),row('reply',contract(wrongRun))]).complete,false);
});
test('unique original self row and later-self barrier prevent unrelated result association',()=>{
 const {expected,result}=fixture(),reply=row('reply',contract(result));
 assert.equal(read(expected,[reply]).present,false);
 assert.equal(read(expected,[row('a',expected,{self:true}),row('b',expected,{self:true}),reply]).present,false);
 assert.equal(read(expected,[row('root',expected,{self:true}),row('new','',{self:true}),reply]).complete,false);
 assert.equal(read(expected,[reply,row('root',expected,{self:true})]).complete,false);
});
test('known original root remains usable only through marker-scoped readDotFeed cache',async()=>{
 const {expected,result}=fixture(),rows=[row('root','virtualized original',{self:true}),row('reply',contract(result))];
 let calls=0;const session={dotFeedBinding:{marker,root_id:'root'},evaluate:async source=>{
  calls++;return vm.runInNewContext(source,{document:{querySelectorAll:()=>rows}});
 }};
 assert.equal((await readDotFeed(session,expected)).reply_id,'reply');assert.equal(calls,1);
 session.dotFeedBinding={marker:'d'.repeat(64),root_id:'root'};
 assert.equal((await readDotFeed(session,expected)).present,false);
});
test('legacy Developer and QA terminals and Markdown renderer keep their existing behavior',()=>{
 const expected=JSON.stringify({idempotency_key:marker,run_id:'run-original'});
 for(const text of ['done\nERP RESULT: READY FOR REVIEW','ERP QA RESULT: '+JSON.stringify({run_id:'run-original',status:'pass'})]) {
  const result=read(expected,[row('root',expected,{self:true}),row('reply',text)],'',body=>'MARKDOWN:'+body.textContent);
  assert.equal(result.complete,true);assert.equal(result.response,'MARKDOWN:'+text);
 }
 const result=read(expected,[row('root',expected,{self:true}),row('reply','ERP QA RESULT: '+JSON.stringify({run_id:'wrong',status:'pass'}))]);
 assert.equal(result.complete,false);
});
test('governed replies require their exact packet binding and cannot fall back to legacy markers',()=>{
 const f=fixture(), context=structuredClone(f.context);
 context.task.meta.governance_packet_digest='d'.repeat(64);
 const expected='Approved context idempotency key (JSON):\n'+JSON.stringify(context);
 for(const text of [contract(f.result),'ERP RESULT: READY FOR REVIEW']) {
  assert.equal(read(expected,[row('root',expected,{self:true}),row('reply',text)]).complete,false);
 }
 const tooLong=contract({...f.result,summary:'x'.repeat(200000)});
 assert.equal(read(f.expected,[row('root',f.expected,{self:true}),row('reply',tooLong)]).complete,false);
});
test('JSON packet boundary handles escaped quotes and braces without consuming role instructions',()=>{
 const f=fixture(), c=structuredClone(f.context);
 const packet=JSON.parse(c.task.original_prompt.split('GOVERNANCE WORK PACKET\n')[1].split('\nROLE')[0]);
 packet.objective='Quoted \\" brace } and { are data';packet.instructions=['} \\" {'];
 c.task.original_prompt=c.task.original_prompt.split('GOVERNANCE WORK PACKET\n')[0]+
  'GOVERNANCE WORK PACKET\n'+JSON.stringify(packet)+'\nROLE INSTRUCTIONS';
 const expected='Approved context idempotency key (JSON):\n'+JSON.stringify(c);
 assert.equal(read(expected,[row('root',expected,{self:true}),row('reply',contract(f.result))]).complete,true);
});

test('server-admitted tool trailer is outside context JSON and semantic plan checks remain in Core',()=>{
 const f=fixture();f.result.architect_plan={metadata:{run_id:'semantic-error-for-Core',source_url:'wrong'}};
 const expected=f.expected+'\n\nServer-admitted governed tool context:\nfixture-session';
 const text=contract(f.result);
 const response=read(expected,[row('root',expected,{self:true}),row('reply',text)]);
 assert.equal(response.complete,true);assert.equal(response.response,text);
});

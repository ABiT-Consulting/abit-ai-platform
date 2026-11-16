'use strict';
// Controlled actual helper + extracted actual handler/capture functions. No browser/network.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path'),{createRequire}=require('node:module');
const D=process.env.AGOV_CAPTURE_SOURCE_DIR||__dirname;
const req=createRequire(path.resolve(__dirname,'../../../package.json'));
const acorn=req('acorn');
const {identity,createCaptureRegistry,TOO_LONG}=require(path.join(D,'original_dot_capture.cjs'));
const handoff=require(path.join(D,'aibit_dot_handoff.cjs'));
const clone=x=>JSON.parse(JSON.stringify(x));
const URL='https://chatgpt.com/dots/owner_alias_12345678~dot_alias_123456789';
function fixture(){
 const body={owner:'synthetic-owner',account_id:'a'.repeat(64),job_id:'aibit_'+'b'.repeat(48),message:'Exact immutable original public context. Résumé "quoted".',stream:true};
 const scope={owner:body.owner,account_id:body.account_id,dot_id:URL.split('/dots/')[1],project_id:'PROJ-FIXTURE',task_id:'TASK-FIXTURE',role:'qa',agent_id:'fixture-qa',erp_user:'qa@example.invalid',repository:'https://github.com/Example/fixture',environment:'isolated-test',tool_scopes:['repository_read'],work_packet_digest:'c'.repeat(64),invocation_id:'agov-fixture-original',run_id:'00000000-0000-5000-8000-000000000001',job_id:body.job_id};
 const proof={protocol_version:1,scope,scope_digest:'d'.repeat(64),enforced:true,read_only:true,default_deny:true,enforcement_mode:'governed_action_gateway',enforcement_boundary:'gateway_operations_and_managed_erp',excluded_surfaces:['owner_account_plugins'],gateway_session_id:'agws_'+'e'.repeat(48),enforced_tool_scopes:scope.tool_scopes,external_write_allowlist:[],proof_id:'agwp_'+'f'.repeat(48),issued_at:100,expires_at:160};
 body.governance_scope={protocol_version:1,scope,preflight_proof:clone(proof),submission_proof:clone(proof)};
 const verified=handoff.validateDotHandoff(body,URL,body.owner),id=identity(body,verified);
 const receipt={protocol_version:1,owner:body.owner,account_id:body.account_id,job_id:body.job_id,invocation_id:scope.invocation_id,run_id:scope.run_id,work_packet_digest:scope.work_packet_digest,scope_digest:'d'.repeat(64),preflight_proof_sha256:'1'.repeat(64),submission_proof_sha256:'2'.repeat(64),proof_id:proof.proof_id,gateway_session_id:proof.gateway_session_id,enforcement_mode:proof.enforcement_mode,enforcement_boundary:proof.enforcement_boundary,excluded_surfaces:proof.excluded_surfaces,accepted:true};
 const root='1'.repeat(32)+'~'+'1'.repeat(32)+'~CalpicoMessage~Sentinel_'+'2'.repeat(32),reply=root.replace(/2{32}$/,'3'.repeat(32));
 const response='```text\nAGOV RESULT: '+JSON.stringify({schema_version:1,invocation_id:scope.invocation_id,summary:'Exact "quotes" and Résumé Ω',outcome:'pass'})+'\n```\n';
 const result={ok:true,read_only:true,dot_handoff:true,owner:body.owner,account_id:body.account_id,job_id:body.job_id,response,root_message_id:root,reply_message_id:reply,conversation_url:URL};
 const observation={target_id:'B'.repeat(32),thread_id:verified.thread_id,result,idle:true};
 return{body,verified,id,receipt,root,reply,response,result,observation};
}
function active(f){const registry=createCaptureRegistry(),handle=registry.register(f.id),wakes=[];handle.admit(f.receipt);handle.begin({targetId:f.observation.target_id,rootId:f.root,wake:e=>wakes.push(e)});return{registry,handle,wakes};}

test('immutable original identity ignores stream flags but binds full scope/message',()=>{
 const f=fixture();assert(Object.isFrozen(f.id));const transport=clone(f.body);transport.stream=false;transport.channel='abitdot';assert.deepEqual(identity(transport,f.verified),f.id);
 const changed=clone(f.body);changed.governance_scope.submission_proof.proof_id='other';assert.notEqual(identity(changed,f.verified).scope_sha256,f.id.scope_sha256);
 changed.message='other';assert.notEqual(identity(changed,{...f.verified,message:changed.message}).message_sha256,f.id.message_sha256);
 assert.throws(()=>identity({...f.body,governance_scope:null},f.verified));
});
test('only admitted begun original may settle; exact bytes and private sentinel retained once',()=>{
 const f=fixture(),registry=createCaptureRegistry(),handle=registry.register(f.id),wakes=[];
 assert.throws(()=>handle.begin({targetId:f.observation.target_id,rootId:f.root,wake:e=>wakes.push(e)}));assert.equal(registry.settle(f.id,f.observation,f.receipt),false);handle.admit(f.receipt);handle.begin({targetId:f.observation.target_id,rootId:f.root,wake:e=>wakes.push(e)});
 assert.equal(registry.settle(f.id,f.observation,f.receipt),true);assert.equal(wakes.length,1);assert.throws(()=>handle.guard(),e=>e===wakes[0]);const result=handle.resultFor(wakes[0]);assert.equal(result.response,f.response);assert.equal(result.root_message_id,f.root);assert.deepEqual(result.scope_receipt,f.receipt);assert.equal(handle.resultFor(Object.assign(Error('forged'),{code:'DOT_ORIGINAL_CAPTURE_OBSERVED'})),null);
 assert.equal(registry.settle(f.id,f.observation,f.receipt),false);assert.equal(wakes.length,1);assert.throws(()=>handle.complete(),e=>e===wakes[0]);handle.close();assert.equal(registry.settle(f.id,f.observation,f.receipt),false);
});
test('owner/account/job/thread/target/root/scope/receipt and terminal-idle mismatches deny',()=>{
 const f=fixture(),a=active(f);const trials=[];
 for(const k of ['owner','account_id','job_id','thread_id','url','message_sha256','scope_sha256'])trials.push({id:{...f.id,[k]:'foreign'}});
 for(const k of ['target_id','thread_id'])trials.push({observation:{...clone(f.observation),[k]:'foreign'}});
 trials.push({observation:{...clone(f.observation),idle:false}});
 for(const [k,v]of[['owner','foreign'],['account_id','foreign'],['job_id','foreign'],['root_message_id','foreign'],['reply_message_id',''],['response',''],['read_only',false],['dot_handoff',false],['ok',false],['conversation_url','https://example.invalid/c/foreign']]){const o=clone(f.observation);o.result[k]=v;trials.push({observation:o});}
 trials.push({receipt:{...f.receipt,proof_id:'foreign'}});
 for(const x of trials){assert.equal(a.registry.settle(x.id||f.id,x.observation||f.observation,x.receipt||f.receipt),false);assert.equal(a.wakes.length,0);assert.doesNotThrow(()=>a.handle.guard());}
 assert.equal(a.registry.settle(f.id,f.observation,f.receipt),true);assert.equal(a.wakes.length,1);
});
test('one original cannot settle another active capture or rewrite known root',()=>{
 const f=fixture(),registry=createCaptureRegistry(),one=registry.register(f.id);one.admit(f.receipt);one.begin({targetId:f.observation.target_id,rootId:f.root,wake:()=>{}});assert.throws(()=>registry.register(f.id));assert.throws(()=>one.rememberRoot('different-root'));
 const g=fixture();g.body.job_id='aibit_'+'9'.repeat(48);g.id=identity(g.body,handoff.validateDotHandoff(g.body,URL,g.body.owner));const two=registry.register(g.id);two.admit(g.receipt);let wake2=0;two.begin({targetId:g.observation.target_id,rootId:g.root,wake:()=>wake2++});assert.equal(registry.settle(g.id,f.observation,f.receipt),false);assert.equal(wake2,0);one.close();two.close();
});

const source=fs.readFileSync(path.join(D,'aibit_relay.cjs'),'utf8'),tree=acorn.parse(source,{ecmaVersion:'latest'});
function find(node,predicate,result=[]){if(node&&typeof node==='object'){if(predicate(node))result.push(node);for(const value of Object.values(node))if(value&&typeof value==='object'){if(Array.isArray(value))for(const n of value)find(n,predicate,result);else find(value,predicate,result);}}return result;}
function declared(name,type='FunctionDeclaration'){const xs=tree.body.filter(n=>n.type===type&&n.id?.name===name);assert.equal(xs.length,1,name);return source.slice(xs[0].start,xs[0].end);}
const routes=find(tree,n=>n.type==='IfStatement'&&find(n.test,x=>x.type==='Literal'&&x.value==='/dot/handoff').length);assert.equal(routes.length,1);const routeSource=source.slice(routes[0].start,routes[0].end);
function context(f,{terminal=true,generating=false,observerError=false,legacy=false,normalReply=false,foreignFinalLocation=false,tooLong=false,preexistingDialog=false,rejectionFields={}}={}){
 const stats={now:0,observerTimes:[],sends:0,releases:0,ends:0,registers:0,frames:[],sessions:[],reconnects:0};const registry=createCaptureRegistry();const captures={register:id=>{stats.registers++;return registry.register(id);},settle:(...args)=>registry.settle(...args),reject:(...args)=>registry.reject(...args)};
 const feed={readDotFeed:async(session,message,markdown)=>tooLong?{present:false,complete:false,response:''}:markdown===undefined?{present:true,root_id:f.root,reply_id:f.reply,complete:terminal,response:terminal?f.response:''}:{present:true,root_id:f.root,complete:legacy||normalReply,response:legacy?'ERP RESULT: READY FOR REVIEW':normalReply?f.response:''}};
 const c={RESPONSE_TIMEOUT_MS:3600000,console:{warn:()=>{}},Error,Date:{now:()=>stats.now},process:{env:{ABIT_CHATGPT_DOT_URL:URL,ABIT_CHATGPT_DOT_OWNER_ID:f.body.owner}},setInterval:()=>1,clearInterval:()=>{},originalDotCaptures:captures,captureIdentity:identity,validateDotHandoff:handoff.validateDotHandoff,safeDotUrl:handoff.safeDotUrl,dotLocationMatches:handoff.dotLocationMatches,dotResponseLocationMatches:handoff.dotResponseLocationMatches,safeToUseSecondary:handoff.safeToUseSecondary,rateLimitCircuitOpen:()=>false,chatGptRateLimitError:()=>Error('rate'),cleanText:x=>String(x||''),parseConversationId:()=>'',parseProjectId:()=>'',clearChatGptRateLimit:()=>{},waitForComposer:async()=>true,stableComposerBaseline:async()=>({count:0,users:0,last:'',generating:false,tooLongDialog:preexistingDialog}),waitForDotSubmission:async()=>!tooLong,waitForSubmission:async()=>true,readConversationTail:async()=>({status:0}),assistantMarkdown:function(){},captureAssistantRichResponse:async()=>{stats.metadataReads=(stats.metadataReads||0)+1;if(normalReply)throw Error('Optional metadata must not run for governed reply');return{html:'',attachments:[]};},activeRelink:()=>false,withBrowserLock:fn=>fn(),activeRelayThreadKeys:new Set(),activeRelayTurns:0,MAX_PARALLEL_RELAY_TURNS:2,primaryTokenAuthorized:()=>true,readJson:async()=>legacy?Object.fromEntries(Object.entries(f.body).filter(([k])=>k!=='governance_scope')):clone(f.body),json:(res,status,data)=>{stats.status=status;stats.json=data;return data;},reserveRelayTurn:()=>{let released=false;return()=>{assert.equal(released,false);released=true;stats.releases++;};},sleep:async ms=>{stats.now+=ms;if(stats.now>225000&&!terminal)throw Object.assign(Error('controlled test boundary'),{statusCode:504});},isCdpConnectionError:e=>e.code==='CHATGPT_CDP_UNAVAILABLE',cdpConnectionError:()=>Object.assign(Error('CDP closed'),{code:'CHATGPT_CDP_UNAVAILABLE'}),relayBrowserError:(stage,e)=>e};
 c.chatThreadTabs={key:x=>x,acquire:async()=>({id:f.observation.target_id}),existing:async()=>({id:f.observation.target_id})};c.accountFingerprint=async()=>f.body.account_id;c.aibitAccountFingerprint=async()=>f.body.account_id;c.assertChatAccount=(body,account)=>assert.equal(account,body.account_id);
 c.governedGateway={prepare:body=>({payload:{...body,message:body.message+'\nServer governed tool context'}}),admit:async()=>({receipt:clone(f.receipt)}),observe:async body=>{stats.observerTimes.push(stats.now);if(observerError)throw Error('controlled observer unavailable');return{payload:body,receipt:clone(f.receipt)};}};
 const sandbox=vm.createContext(c);const actualClass=vm.runInContext('('+declared('CdpSession','ClassDeclaration')+')',sandbox);
 class Session{constructor(target){this.target=target;this.pending=new Map();this.observerReconnected=false;stats.sessions.push(this);}async open(){return this;}async navigate(){return URL;}async location(){return foreignFinalLocation&&stats.now>=3000?"https://example.invalid/foreign":URL;}async call(){this.captureGuard?.();return{};}async evaluate(text){if(text.includes('const message ='))return{ok:true,send:null};if(text.includes('send.click()')){stats.sends++;return true;}if(text.includes('original_user_visible'))return{generating,original_user_visible:false,users:0,assistants:0,dialogs:tooLong?[TOO_LONG]:[],...rejectionFields};if(text.includes('document.title'))return'Fixture';throw Error('Unexpected controlled evaluation');}observe(read){return actualClass.prototype.observe.call(this,read);}close(error){if(error)this.closeSignal=error;}}
 c.CdpSession=Session;
 const module={exports:{}};const observerContext=vm.createContext({module,exports:module.exports,URL,require:name=>{if(name==='./aibit_dot_handoff.cjs')return handoff;if(name==='./aibit_dot_feed.cjs')return feed;throw Error('Unexpected dependency '+name);}});vm.runInContext(fs.readFileSync(path.join(D,'aibit_dot_observation.cjs'),'utf8'),observerContext);
 c.require=name=>{if(name==='./aibit_dot_feed.cjs')return feed;if(name==='./aibit_dot_observation.cjs')return module.exports;throw Error('Unexpected dependency '+name);};
 for(const name of ['submitPrompt','relayMessage','executeRelayTurn','observeOriginalDot'])vm.runInContext(declared(name),sandbox);
 const handler=vm.runInContext('(async function(req,res){const path="/dot/handoff";'+routeSource+'})',sandbox);
 const res={destroyed:false,writableEnded:false,writeHead(){},write(raw){stats.frames.push(JSON.parse(raw));},end(){stats.ends++;this.writableEnded=true;}};
 return{stats,c,sandbox,actualClass,handler,res};
}
test('actual streaming handler: one 150s internal observer settles original bytes and finally once',async()=>{
 const f=fixture(),x=context(f);await x.handler({method:'POST'},x.res);assert.equal(x.stats.observerTimes.length,1);assert(x.stats.observerTimes[0]>=150300);assert.equal(x.stats.sends,1);assert.equal(x.stats.releases,1);assert.equal(x.stats.ends,1);const complete=x.stats.frames.filter(r=>r.type==='complete');assert.equal(complete.length,1);assert.equal(complete[0].result.response,f.response);assert.equal(complete[0].result.root_message_id,f.root);assert.equal(x.stats.frames.filter(r=>r.type==='error').length,0);
});
test('actual 150s once-read: nonterminal, generating or error never repeats or fabricates completion',async()=>{
 for(const mode of[{terminal:false},{terminal:true,generating:true},{terminal:false,observerError:true}]){const f=fixture(),x=context(f,mode);if(mode.generating)x.c.sleep=async ms=>{x.stats.now+=ms;if(x.stats.now>225000)throw Object.assign(Error('controlled test boundary'),{statusCode:504});};await x.handler({method:'POST'},x.res);assert.equal(x.stats.observerTimes.length,1);assert(x.stats.observerTimes[0]>=150300);assert.equal(x.stats.sends,1);assert.equal(x.stats.frames.filter(r=>r.type==='complete').length,0);assert.equal(x.stats.frames.filter(r=>r.type==='error').length,1);assert.equal(x.stats.releases,1);assert.equal(x.stats.ends,1);}
});
test('actual CDP observe/call guards stop reconnect and further commands after private settlement',async()=>{
 const f=fixture(),x=context(f),a=active(f);let reject;const read=new Promise((_,r)=>reject=r);const session={captureGuard:()=>a.handle.guard(),observerReconnected:false,open:async()=>{x.stats.reconnects++;},close:()=>{}};const pending=x.actualClass.prototype.observe.call(session,()=>read);a.registry.settle(f.id,f.observation,f.receipt);reject(a.wakes[0]);await assert.rejects(pending,e=>e===a.wakes[0]);assert.equal(x.stats.reconnects,0);let wire=0;x.c.existingChrome={call:()=>{wire++;}};assert.throws(()=>x.actualClass.prototype.call.call({...session,browserSessionId:'owned'},'Runtime.evaluate'),e=>e===a.wakes[0]);assert.equal(wire,0);a.handle.close();
});
test('actual non-governed streaming path retains normal completion without registration/observer',async()=>{
 const f=fixture(),x=context(f,{legacy:true});await x.handler({method:'POST'},x.res);assert.equal(x.stats.registers,0);assert.equal(x.stats.observerTimes.length,0);assert.equal(x.stats.sends,1);assert.equal(x.stats.frames.filter(r=>r.type==='complete').length,1);assert.equal(x.stats.releases,1);assert.equal(x.stats.ends,1);
});

// Regression: the old handler removed capture ownership before optional CDP metadata.
test('actual governed terminal reply returns before optional metadata and without observer',async()=>{
 const f=fixture(),x=context(f,{normalReply:true});await x.handler({method:'POST'},x.res);
 assert.equal(x.stats.sends,1);assert.equal(x.stats.registers,1);assert.equal(x.stats.metadataReads||0,0);assert.equal(x.stats.observerTimes.length,0);assert(x.stats.now<150000);assert.equal(x.stats.releases,1);assert.equal(x.stats.ends,1);
 const done=x.stats.frames.filter(r=>r.type==='complete');assert.equal(done.length,1);assert.equal(done[0].result.response,f.response.trim());assert.deepEqual(JSON.parse(JSON.stringify(done[0].result.scope_receipt)),f.receipt);assert.equal(done[0].result.response_html,'');assert.equal(done[0].result.attachments.length,0);
});
test('actual governed terminal reply still rejects a changed final conversation location',async()=>{
 const f=fixture(),x=context(f,{normalReply:true,foreignFinalLocation:true});await x.handler({method:'POST'},x.res);assert.equal(x.stats.sends,1);assert.equal(x.stats.frames.filter(r=>r.type==='complete').length,0);assert.equal(x.stats.frames.filter(r=>r.type==='error').length,1);assert.equal(x.stats.releases,1);assert.equal(x.stats.metadataReads||0,0);
});

// Regression: admission may succeed while the original Send acknowledgement stalls.
test('terminal original observed during Send acknowledgement releases the original handler once',async()=>{
 const f=fixture(),x=context(f);x.c.waitForDotSubmission=async()=>{await x.c.observeOriginalDot(f.body);return true;};
 await x.handler({method:'POST'},x.res);
 assert.equal(x.stats.sends,1);assert.equal(x.stats.observerTimes.length,1);assert(x.stats.now<5000);
 assert.equal(x.stats.releases,1);assert.equal(x.stats.ends,1);
 assert.equal(x.stats.frames.filter(r=>r.type==='error').length,0);
 assert.equal(x.stats.frames.filter(r=>r.type==='complete').length,1);
});


function rejectedFixture(){
 const f=fixture();f.result={ok:true,read_only:true,owner:f.body.owner,account_id:f.body.account_id,
 job_id:f.body.job_id,dot_handoff:false,response:'',conversation_url:URL,root_message_id:null,reply_message_id:null,observed:{body_tail:'private fixture context'},
 rejection:{code:'CHATGPT_MESSAGE_TOO_LONG',not_submitted:true,dialog:TOO_LONG,original_user_visible:false,
 feed_present:false,generating:false,users:0,assistants:0}};
 f.observation={target_id:'B'.repeat(32),thread_id:f.verified.thread_id,result:f.result,idle:true};return f;
}
function activeRejection(f,{sent=true,accepted=false,baseline={count:0,users:0,generating:false,tooLongDialog:false},rootId}={}){
 const registry=createCaptureRegistry(),handle=registry.register(f.id),wakes=[];handle.admit(f.receipt);
 handle.begin({targetId:f.observation.target_id,wake:e=>wakes.push(e),baseline,rootId});if(sent)handle.sent();if(accepted)handle.accepted();return{registry,handle,wakes};
}
test('positive too-long rejection wakes only exact original once, retaining bound admission proof',()=>{
 const f=rejectedFixture(),a=activeRejection(f);
 assert.equal(a.registry.reject(f.id,f.observation,f.receipt),true);assert.equal(a.wakes.length,1);
 assert.throws(()=>a.handle.guard(),e=>e===a.wakes[0]&&e.notSubmitted===true&&e.statusCode===424&&e.code==='DOT_MESSAGE_TOO_LONG_REJECTED');
 const r=a.handle.rejectionFor(a.wakes[0]);assert.equal(r.observed,undefined);assert.deepEqual(r.scope_receipt,f.receipt);assert.deepEqual(r.rejection_capture,{send_attempted:true,baseline_empty:true,baseline_rejection_absent:true,accepted_observed:false});
 assert.equal(a.handle.resultFor(a.wakes[0]),null);assert.equal(a.handle.rejectionFor(Object.assign(Error('forged'),{code:a.wakes[0].code})),null);
 assert.equal(a.registry.reject(f.id,f.observation,f.receipt),false);assert.equal(a.wakes.length,1);a.handle.close();
});
test('no Send, pre-existing dialog/turn, accepted model, known root and incomplete baseline cannot establish rejection',()=>{
 const f=rejectedFixture();
 for(const mode of[{sent:false},{accepted:true},{rootId:f.root},{baseline:null},{baseline:{}},
 {baseline:{count:0,users:0,generating:false,tooLongDialog:true}},
 {baseline:{count:1,users:0,generating:false,tooLongDialog:false}},
 {baseline:{count:0,users:1,generating:false,tooLongDialog:false}},
 {baseline:{count:0,users:0,generating:true,tooLongDialog:false}}]){
  const a=activeRejection(f,mode);
  assert.equal(a.registry.reject(f.id,f.observation,f.receipt),false);assert.equal(a.wakes.length,0);a.handle.close();
 }
 const registry=createCaptureRegistry(),h=registry.register(f.id);assert.equal(registry.reject(f.id,f.observation,f.receipt),false);h.close();
});
test('rejection denies changed original, account, target, scope, admission, dialog, generation or any accepted evidence',()=>{
 const f=rejectedFixture(),a=activeRejection(f),trials=[];
 for(const k of ['owner','account_id','job_id','thread_id','url','message_sha256','scope_sha256'])trials.push({id:{...f.id,[k]:'foreign'}});
 for(const k of ['target_id','thread_id'])trials.push({o:{...clone(f.observation),[k]:'foreign'}});
 trials.push({o:{...clone(f.observation),idle:false}},{receipt:{...f.receipt,proof_id:'foreign'}});
 for(const [k,v]of[['owner','foreign'],['account_id','foreign'],['job_id','foreign'],['root_message_id','accepted'],['reply_message_id','accepted'],['response','reply'],['read_only',false],['dot_handoff',true],['ok',false],['conversation_url','https://example.invalid/c/foreign']]){const o=clone(f.observation);o.result[k]=v;trials.push({o});}
 for(const [k,v]of[['code','other'],['not_submitted',false],['dialog','Please retry'],['original_user_visible',true],['feed_present',true],['generating',true],['users',1],['assistants',1]]){const o=clone(f.observation);o.result.rejection[k]=v;trials.push({o});}
 for(const t of trials){assert.equal(a.registry.reject(t.id||f.id,t.o||f.observation,t.receipt||f.receipt),false);assert.equal(a.wakes.length,0);}
 assert.equal(a.registry.reject(f.id,f.observation,f.receipt),true);
});
test('actual streaming original: one Send, one owned dialog read, positive rejection releases original once',async()=>{
 const f=fixture(),x=context(f,{tooLong:true});await x.handler({method:'POST'},x.res);
 assert.equal(x.stats.sends,1);assert.equal(x.stats.observerTimes.length,1);assert.equal(x.stats.releases,1);assert.equal(x.stats.ends,1);
 assert.equal(x.stats.frames.filter(r=>r.type==='complete').length,0);const errors=x.stats.frames.filter(r=>r.type==='error');assert.equal(errors.length,1);
 assert.equal(errors[0].code,'DOT_MESSAGE_TOO_LONG_REJECTED');assert.equal(errors[0].safe_to_fallback,true);assert.equal(errors[0].status,424);
 assert.equal(errors[0].original_rejection.rejection.dialog,TOO_LONG);assert.deepEqual(JSON.parse(JSON.stringify(errors[0].original_rejection.scope_receipt)),f.receipt);
});
test('actual streaming absent acknowledgement stays unknown without exact new rejection',async()=>{
 for(const mode of[{preexistingDialog:true},{rejectionFields:{dialogs:[]}},{rejectionFields:{dialogs:['The message was rejected.']}},
 {rejectionFields:{generating:true}},{rejectionFields:{users:1}},{rejectionFields:{assistants:1}},
 {rejectionFields:{original_user_visible:true}}]){
  const f=fixture(),x=context(f,{tooLong:true,...mode});await x.handler({method:'POST'},x.res);
  assert.equal(x.stats.sends,1);assert.equal(x.stats.observerTimes.length,1);assert.equal(x.stats.releases,1);
  const errors=x.stats.frames.filter(r=>r.type==='error');assert.equal(errors.length,1);assert.equal(errors[0].safe_to_fallback,false);
  assert.equal(errors[0].original_rejection,undefined);assert.equal(x.stats.frames.filter(r=>r.type==='complete').length,0);
 }
});

test('row-bound failed outgoing original has distinct proof without retroactive baseline claims',()=>{
 const f=rejectedFixture();f.result.protocol='v4_outgoing_rejection';f.result.root_message_id=f.root;
 f.result.rejection={code:'CHATGPT_MESSAGE_TOO_LONG',not_submitted:true,dialog:TOO_LONG};
 f.result.root_failure={root_id:f.root,original_envelope_matched:true,failed_to_send:true,
 failed_send_label:'Failed to send. Click to retry',generating:false};
 f.observation.result=f.result;
 const a=activeRejection(f,{sent:false,accepted:true,baseline:null,rootId:f.root});
 assert.equal(a.registry.reject(f.id,f.observation,f.receipt),true);assert.equal(a.wakes.length,1);
 const r=a.handle.rejectionFor(a.wakes[0]);assert.equal(r.protocol,'v4_outgoing_rejection');
 assert.equal(r.rejection_capture,undefined);assert.deepEqual(r.root_failure,f.result.root_failure);
 assert.equal(r.observed,undefined);a.handle.close();
 for(const mutate of[
 o=>{o.result.root_failure.root_id='foreign';},o=>{o.result.root_failure.failed_to_send=false;},
 o=>{o.result.root_failure.original_envelope_matched=false;},o=>{o.result.root_failure.failed_send_label='Retry';},
 o=>{o.result.root_failure.generating=true;},o=>{o.result.reply_message_id='actual-reply';},
 o=>{o.result.response='actual response';},o=>{o.result.rejection.dialog='A different failure';},
 ]){const o=clone(f.observation);mutate(o);const b=activeRejection(f,{accepted:true,rootId:f.root});
 assert.equal(b.registry.reject(f.id,o,f.receipt),false);assert.equal(b.wakes.length,0);b.handle.close();}
 const other=activeRejection(f,{rootId:'another-original-root'});
 assert.equal(other.registry.reject(f.id,f.observation,f.receipt),false);other.handle.close();
});

'use strict';
// Actual loopback HTTP + SDK bearer middleware; only synthetic identities/secrets.
// No app import, production configuration, browser, account, provider or model.
const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createRequire } = require('node:module');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname,'../deployment/linux/abit-ai/agentic-gateway');
const wire = require(path.join(root,'wire.cjs'));
const { createGatewayAdapter, expectedReceipt } = require(path.join(root,'relay.cjs'));
const installed = createRequire('/opt/abit-dev-linux-mcp/package.json');
const express = installed('express');
const z = installed('zod').z;
let edgeModule, adapter, core, front, backend, backendUrl, base, publicUrl, dir, controlFile, edgeFile;
let coreCalls = [], admissions = new Map(), mutation = null, ordinary = 0;
const CONTROL='synthetic-control-secret-for-offline-test-only-0001';
const EDGE='synthetic-edge-secret-for-offline-test-only-0002';
const authInfo = family => ({ token:'fixture-not-forwarded-token',clientId:'fixture-existing-client',family,
  scopes:['development'],resource:'https://synthetic.invalid/mcp',expiresAt:Math.floor(Date.now()/1000)+3600 });
const listen = server => new Promise(resolve => server.listen(0,'127.0.0.1',() => resolve('http://127.0.0.1:'+server.address().port)));
const close = server => new Promise(resolve => {server.closeAllConnections();server.close(resolve);});
function sample() {
  const scope={owner:'fixture-owner',account_id:'a'.repeat(64),dot_id:'fixture-dot-0001',project_id:'PROJ-TEST',task_id:'TASK-TEST',role:'developer',agent_id:'fixture-agent-0001',erp_user:'fixture-ai@example.invalid',repository:'https://github.com/example/synthetic',environment:'isolated-test',tool_scopes:['execute_development','read_project'],work_packet_digest:'b'.repeat(64),invocation_id:'fixture-invocation-0001',run_id:'fixture-run-0001',job_id:'aibit_'+'c'.repeat(48)};
  const now=Math.floor(Date.now()/1000);
  const proof={protocol_version:1,scope,scope_digest:wire.digest(scope),enforced:true,read_only:true,default_deny:true,enforcement_mode:wire.MODE,enforcement_boundary:wire.BOUNDARY,excluded_surfaces:wire.EXCLUDED,gateway_session_id:'agws_fixture_opaque_admitted_capability',enforced_tool_scopes:scope.tool_scopes,external_write_allowlist:['execute_development'],proof_id:'fixture-preflight-proof-001',issued_at:now,expires_at:now+30};
  const value={protocol_version:1,scope,preflight_proof:proof,submission_proof:{...proof,proof_id:'fixture-submission-proof-002'}};
  return {owner:scope.owner,account_id:scope.account_id,job_id:scope.job_id,message:'Original synthetic governed task',governance_scope:value};
}
function rpc(changes={}) {
  return {jsonrpc:'2.0',id:'fixture-rpc-0001',method:'tools/call',params:{name:edgeModule.TOOL,
    arguments:{session_id:'agws_fixture_opaque_admitted_capability',call_id:'fixture-call-0001',operation:'context.read',arguments:{},...changes},
    _meta:{'openai/session':'fixture-transport-session-0001'}}};
}
async function tool(value, token='fixture-a') {
  const response=await fetch(publicUrl+'/mcp',{method:'POST',headers:{'content-type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(value)});
  return {status:response.status,body:await response.json()};
}
before(async () => {
  dir=fs.mkdtempSync(path.join(os.tmpdir(),'agov-transports-'));fs.chmodSync(dir,0o700);
  controlFile=path.join(dir,'control');edgeFile=path.join(dir,'edge');
  fs.writeFileSync(controlFile,CONTROL,{mode:0o600});fs.writeFileSync(edgeFile,EDGE,{mode:0o600});
  core=http.createServer(async (req,res) => {
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw);coreCalls.push({url:req.url,body,headers:req.headers,raw});
    const route=req.url.split('/').at(-1);let result;
    if(route==='call') {
      assert.equal(req.headers.authorization,undefined);
      const payload={body,stamp:req.headers['x-abit-gateway-time'],signature:req.headers['x-abit-gateway-signature'],secret:EDGE};
      const checked=execFileSync('/usr/bin/python3',['-c',"import sys,json,hmac,hashlib;v=json.load(sys.stdin);s=v['stamp']+'\\n'+json.dumps(v['body'],sort_keys=True,separators=(',',':'),ensure_ascii=False);print(hmac.compare_digest(hmac.new(v['secret'].encode(),s.encode(),hashlib.sha256).hexdigest(),v['signature']))"],{input:JSON.stringify(payload),encoding:'utf8'}).trim();
      assert.equal(checked,'True','Actual Python canonical verifier must match OAuth-edge MAC');
      result={call_id:body.call_id,state:'completed',result:{observed:'synthetic scoped HTTP'},replayed:false};
    } else {
      assert.equal(req.headers['x-abit-erp-control'],CONTROL);
      assert.equal(raw.includes('Original synthetic governed task'),false,'No model prompt goes to proof/admission endpoints');
      if(route==='proof') {
        const sampleBody=sample();result={...sampleBody.governance_scope.preflight_proof,scope:body.scope,scope_digest:wire.digest(body.scope)};
      } else if(route==='admit') {admissions.set(wire.digest(body),expectedReceipt(body));result=admissions.get(wire.digest(body));}
      else if(route==='receipt') {result=admissions.get(wire.digest(body));if(!result){res.writeHead(404);res.end('{}');return;}}
      else {res.writeHead(404);res.end('{}');return;}
    }
    if(mutation)result=mutation(result,route,req,res);
    if(res.destroyed||res.writableEnded)return;
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));
  });
  base=await listen(core);adapter=createGatewayAdapter({coreUrl:base,tokenFile:controlFile});
  edgeModule=await import(pathToFileURL(path.join(root,'edge.mjs')));
  const edge=edgeModule.createGatewayEdge({coreUrl:base,secretFile:edgeFile});
  const { requireBearerAuth }=await import(pathToFileURL(installed.resolve('@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js')));
  const { InvalidTokenError }=await import(pathToFileURL(installed.resolve('@modelcontextprotocol/sdk/server/auth/errors.js')));
  const verifier={async verifyAccessToken(token) {
    if(['fixture-a','fixture-a-refreshed'].includes(token))return authInfo('fixture-family-A');
    if(token==='fixture-b')return authInfo('fixture-family-B');
    throw new InvalidTokenError('Synthetic token denied');
  }};
  const {McpServer}=await import(pathToFileURL(installed.resolve('@modelcontextprotocol/sdk/server/mcp.js')));
  const {StreamableHTTPServerTransport}=await import(pathToFileURL(installed.resolve('@modelcontextprotocol/sdk/server/streamableHttp.js')));
  const backendApp=express();backendApp.use(express.json());
  backendApp.post('/mcp',async(req,res)=>{
    const server=new McpServer({name:'controlled-existing-backend',version:'1.0.0'});
    for(const name of ['normal_read','normal_write'])server.registerTool(name,{description:'Existing synthetic '+name,inputSchema:{}},async()=>({content:[{type:'text',text:name}]}));
    const transport=new StreamableHTTPServerTransport({sessionIdGenerator:undefined,enableJsonResponse:true});
    res.on('close',()=>{transport.close();server.close();});
    await server.connect(transport);await transport.handleRequest(req,res,req.body);
  });
  backend=http.createServer(backendApp);backendUrl=await listen(backend);
  const app=express();app.post('/mcp',requireBearerAuth({verifier,requiredScopes:['development'],expectedResource:new URL('https://synthetic.invalid/mcp')}),express.raw({type:'application/json',limit:'512kb'}),async(req,res)=>{
    const result=await edge(req.body,req.auth);
    if(result===null){
      ordinary++;
      if(JSON.parse(req.body).method==='tools/list'){
        const reply=await fetch(backendUrl+'/mcp',{method:'POST',headers:{'content-type':'application/json',accept:'application/json, text/event-stream'},body:req.body});
        return res.status(reply.status).type('application/json').send(edgeModule.augmentToolList(req.body,Buffer.from(await reply.arrayBuffer()),req.auth));
      }
      res.json({ordinary:true});
    }else res.json(result);
  });
  front=http.createServer(app);publicUrl=await listen(front);
});
after(async()=>{await close(front);await close(core);await close(backend);fs.rmSync(dir,{recursive:true,force:true});});
beforeEach(()=>{coreCalls=[];admissions.clear();mutation=null;ordinary=0;});

test('actual Core proof HTTP is exact, control-authenticated and locally pinned',async()=>{
  const body=sample(),s=body.governance_scope.scope;
  const result=await adapter.proof({protocol_version:1,scope:s,scope_digest:wire.digest(s)},{owner:s.owner,dotId:s.dot_id});
  assert.equal(result.gateway_session_id,'agws_fixture_opaque_admitted_capability');assert.equal(coreCalls.length,1);
  assert.throws(()=>createGatewayAdapter({coreUrl:'https://external.invalid',tokenFile:controlFile}));
  await assert.rejects(adapter.proof({protocol_version:1,scope:s,scope_digest:wire.digest(s)},{owner:'another-owner'}));
  assert.equal(coreCalls.length,1);
});
test('admission binds exact packet/proofs and appends only scoped capability reference',async()=>{
  const body=sample(),original=JSON.stringify(body);const admitted=await adapter.admit(body);
  assert.deepEqual(admitted.receipt,expectedReceipt(body.governance_scope));assert.equal(JSON.stringify(body),original);
  assert.equal(admitted.payload.message.includes(admitted.receipt.gateway_session_id),true);
  assert.equal(admitted.payload.message.includes(CONTROL),false);assert.equal(admitted.payload.message.includes(EDGE),false);
  assert.equal(coreCalls[0].url,'/api/v1/agentic/gateway/admit');
});
test('composer preparation does not admit; awaited sending admits only immediately before Send',async()=>{
  const body=sample();const prepared=adapter.prepare(body);assert.equal(coreCalls.length,0);
  const now=Date.now;Date.now=()=>now()+10000;
  let sends=0;
  try{
    const lifecycle={async sending(){await adapter.admit(body);}};
    await lifecycle.sending();sends++;
    assert.equal(sends,1);assert.equal(coreCalls.length,1);
    assert.equal(prepared.payload.message,(await adapter.observe(body)).payload.message);
  }finally{Date.now=now;}
});
test('failed Send-boundary admission prevents any browser submission',async()=>{
  const body=sample();adapter.prepare(body);let sends=0;
  mutation=(result,route,req,res)=>{if(route==='admit')res.destroy();return result;};
  await assert.rejects((async()=>{await adapter.admit(body);sends++;})());
  assert.equal(sends,0);assert.equal(coreCalls.length,1);
});
test('wrong response identity/hash/session cannot authorize a browser send',async()=>{
  for(const key of ['job_id','work_packet_digest','submission_proof_sha256','gateway_session_id']){
    mutation=(result,route)=>route==='admit'?{...result,[key]:'mismatch-fixture'}:result;
    await assert.rejects(adapter.admit(sample()));
  }
});
test('read-only original observation after TTL reconstructs identical sent prompt',async()=>{
  const body=sample(),admitted=await adapter.admit(body);
  const old=Date.now;Date.now=()=>old()+300000;
  try{const observed=await adapter.observe(body);assert.equal(observed.payload.message,admitted.payload.message);assert.deepEqual(observed.receipt,admitted.receipt);await assert.rejects(adapter.admit(body));}
  finally{Date.now=old;}
  assert.deepEqual(coreCalls.map(x=>x.url.split('/').at(-1)),['admit','receipt']);
});
test('lost admission ACK reconciles receipt without another admit or model action',async()=>{
  const body=sample();let cut=true;
  mutation=(result,route,req,res)=>{if(route==='admit'&&cut){cut=false;res.destroy();}return result;};
  await assert.rejects(adapter.admit(body));
  const observed=await adapter.observe(body);assert.deepEqual(observed.receipt,expectedReceipt(body.governance_scope));
  assert.deepEqual(coreCalls.map(x=>x.url.split('/').at(-1)),['admit','receipt']);
});
test('actual SDK bearer middleware rejects unauthenticated/invalid callers before Core',async()=>{
  assert.equal((await tool(rpc(),null)).status,401);assert.equal((await tool(rpc(),'bad-fixture')).status,401);
  assert.equal(coreCalls.length,0);
});
test('authenticated tools/list retains actual SDK normal descriptors and adds exactly one edge tool',async()=>{
  const request={jsonrpc:'2.0',id:'fixture-list-001',method:'tools/list',params:{}};
  assert.equal((await tool(request,null)).status,401);
  const actual=await tool(request);assert.equal(actual.status,200);
  const direct=await fetch(backendUrl+'/mcp',{method:'POST',headers:{'content-type':'application/json',accept:'application/json, text/event-stream'},body:JSON.stringify(request)});
  const original=await direct.json();const tools=actual.body.result.tools;
  assert.deepEqual(tools.filter(item=>item.name!==edgeModule.TOOL),original.result.tools);
  assert.equal(tools.filter(item=>item.name===edgeModule.TOOL).length,1);
  assert.deepEqual(tools.find(item=>item.name===edgeModule.TOOL),edgeModule.governedToolDescriptor());
  assert.equal(coreCalls.length,0);
});
test('tool pagination preserves ordinary descriptors/cursor and appends owned descriptor only on terminal page',()=>{
  const request={jsonrpc:'2.0',id:'fixture-list-002',method:'tools/list',params:{cursor:'page-one'}};
  const first={jsonrpc:'2.0',id:request.id,result:{tools:[{name:'normal_alpha',description:'Keep unchanged'},
    {name:edgeModule.TOOL,description:'Untrusted claimed broad identity'}],nextCursor:'page-two',_meta:{preserve:true}}};
  const page=edgeModule.augmentToolList(request,first,authInfo('fixture-family-A'));
  assert.deepEqual(page.result,{...first.result,tools:[first.result.tools[0]]});
  const second={jsonrpc:'2.0',id:request.id,result:{tools:[{name:'normal_beta',description:'Keep this too'}]}};
  const terminal=edgeModule.augmentToolList(request,second,authInfo('fixture-family-A'));
  const all=[...page.result.tools,...terminal.result.tools];
  assert.equal(all.filter(x=>x.name===edgeModule.TOOL).length,1);
  assert.deepEqual(all.filter(x=>x.name!==edgeModule.TOOL),[first.result.tools[0],second.result.tools[0]]);
  assert.throws(()=>edgeModule.augmentToolList(request,second,null));
  const unrelated={...request,method:'tools/call'};
  assert.equal(edgeModule.augmentToolList(unrelated,second,null),second);
});
test('direct actual SDK backend cannot execute the edge-only advertised tool',async()=>{
  const reply=await fetch(backendUrl+'/mcp',{method:'POST',headers:{'content-type':'application/json',accept:'application/json, text/event-stream'},body:JSON.stringify(rpc())});
  const result=await reply.json();assert.ok(result.error||result.result?.isError);
  assert.equal(coreCalls.length,0);
});
test('edge MAC uses verified connection and opaque transport session, never model identities',async()=>{
  const response=await tool(rpc());assert.equal(response.status,200);assert.equal(response.body.result.isError,undefined);
  const call=coreCalls[0];assert.equal(call.body.connection_hash,wire.digest(['fixture-existing-client','fixture-family-A']));
  assert.equal(call.body.client_session_hash,wire.sha256('fixture-transport-session-0001'));
  assert.equal(call.raw.includes('fixture-transport-session-0001'),false);assert.equal(call.raw.includes('fixture-family-A'),false);
  assert.equal(call.raw.includes('fixture-not-forwarded-token'),false);assert.equal(JSON.stringify(response).includes(EDGE),false);
});
test('token refresh preserves connection identity while a distinct family cannot impersonate it',async()=>{
  await tool(rpc(),'fixture-a');await tool(rpc(),'fixture-a-refreshed');await tool(rpc(),'fixture-b');
  assert.equal(coreCalls[0].body.connection_hash,coreCalls[1].body.connection_hash);
  assert.notEqual(coreCalls[0].body.connection_hash,coreCalls[2].body.connection_hash);
});
test('missing session metadata or forged scope identities fail before a signed Core request',async()=>{
  const missing=rpc();delete missing.params._meta;
  assert.equal((await tool(missing)).body.result.isError,true);
  const forged=rpc({connection_hash:'a'.repeat(64),role:'administrator'});
  assert.equal((await tool(forged)).body.result.isError,true);assert.equal(coreCalls.length,0);
});
test('ordinary owner tools stay on their existing forwarding route',async()=>{
  const other=rpc();other.params.name='workspace_info';assert.deepEqual((await tool(other)).body,{ordinary:true});
  assert.equal(ordinary,1);assert.equal(coreCalls.length,0);
});
test('actual HTTP canonical MAC supports Unicode and denies lossy numeric inputs',async()=>{
  const response=await tool(rpc({arguments:{names:{'\u{1f600}':'emoji','\ue000':'BMP'},count:42,enabled:true}}));
  assert.equal(response.body.result.isError,undefined);assert.equal(coreCalls.length,1);
  assert.equal((await tool(rpc({arguments:{fraction:0.1}}))).body.result.isError,true);
  assert.equal((await tool(rpc({arguments:{number:Number.MAX_SAFE_INTEGER+1}}))).body.result.isError,true);
  assert.equal(coreCalls.length,1);
});
test('mismatched Core call receipt is not reported as a completed tool',async()=>{
  mutation=(result,route)=>route==='call'?{...result,call_id:'other-original-call'}:result;
  assert.equal((await tool(rpc())).body.result.isError,true);
});
test('uncertain action returns its same identity without automatic transport replay',async()=>{
  mutation=(result,route)=>route==='call'?{...result,state:'uncertain',result:null}:result;
  const response=await tool(rpc());const result=JSON.parse(response.body.result.content[0].text);
  assert.equal(result.state,'uncertain');assert.equal(result.call_id,'fixture-call-0001');assert.equal(coreCalls.length,1);
});
test('direct local backend stub can never execute even with forged identity-like inputs',async()=>{
  let registered;edgeModule.registerGovernedTool({registerTool(name,spec,handler){registered={name,spec,handler};}},z);
  assert.equal(registered.name,'agentic_governance_action');
  const result=await registered.handler({...rpc().params.arguments,connection_hash:'forged'});
  assert.equal(result.isError,true);assert.equal(coreCalls.length,0);
});
test('batch attempts cannot slip the governed tool through the unauthenticated backend',async()=>{
  const response=await tool([rpc()]);assert.equal(response.body.error.code,-32600);assert.equal(coreCalls.length,0);
});
test('bounded sanitized ERP conflict detail reaches MCP with its actual HTTP status',async()=>{
  mutation=(result,route,req,res)=>{if(route==='call'){res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'ERP TimestampMismatchError: Task changed'}));}return result;};
  const response=await tool(rpc());assert.equal(response.body.result.isError,true);
  assert.equal(response.body.result.content[0].text,'Core HTTP 409: ERP TimestampMismatchError: Task changed');
});
test('HTML, traceback, credential-like and oversized Core failures never echo into MCP',async()=>{
  for(const value of ['<html>Traceback Authorization: Bearer '+CONTROL+'</html>',
    'Authorization: Bearer '+CONTROL,'secret='+EDGE,'Traceback (most recent call last)','x'.repeat(1501)]){
    mutation=(result,route,req,res)=>{if(route==='call'){res.writeHead(409,{'Content-Type':value.startsWith('<')?'text/html':'application/json'});res.end(value.startsWith('<')?value:JSON.stringify({detail:value}));}return result;};
    const response=await tool(rpc());const text=response.body.result.content[0].text;
    assert.equal(response.body.result.isError,true);assert.equal(text.includes(CONTROL),false);assert.equal(text.includes(EDGE),false);assert.equal(text.includes('Traceback'),false);assert.equal(text.includes('<html>'),false);assert.equal(text.includes('x'.repeat(1501)),false);
  }
});
test('authenticated transport diagnostic returns hashes only and never calls Core or admits a job',async()=>{
  const input=rpc({session_id:'diagnostic-bootstrap',operation:'transport.describe',arguments:{}});
  let response=await tool(input);let facts=JSON.parse(response.body.result.content[0].text);
  assert.deepEqual(facts,{protocol_version:1,enforcement_mode:wire.MODE,connection_hash:wire.digest(['fixture-existing-client','fixture-family-A']),provider_session_metadata_present:true,client_session_hash:wire.sha256('fixture-transport-session-0001')});
  delete input.params._meta;response=await tool(input);facts=JSON.parse(response.body.result.content[0].text);
  assert.equal(facts.provider_session_metadata_present,false);assert.equal(facts.client_session_hash,undefined);
  assert.equal(JSON.stringify(facts).includes('fixture-family-A'),false);assert.equal(JSON.stringify(facts).includes('fixture-existing-client'),false);
  assert.equal(coreCalls.length,0);assert.equal(admissions.size,0);
  assert.equal((await tool(input,null)).status,401);
});
test('diagnostic cannot manufacture another identity, select job scope or bypass missing job metadata',async()=>{
  const input=rpc({session_id:'diagnostic-bootstrap',operation:'transport.describe',arguments:{connection_hash:'f'.repeat(64)}});
  assert.equal((await tool(input)).body.result.isError,true);
  const missing=rpc();delete missing.params._meta;
  assert.equal((await tool(missing)).body.result.isError,true);assert.equal(coreCalls.length,0);
});

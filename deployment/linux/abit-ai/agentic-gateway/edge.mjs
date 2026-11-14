import crypto from 'node:crypto';
import wire from './wire.cjs';
const CORE = 'http://127.0.0.1:8888';
const EDGE_FILE = '/var/lib/abit-ai-agentic-gateway/edge-token';
export const TOOL = 'agentic_governance_action';
const DESCRIPTION='Execute a server-admitted ERP governance operation using the current job capability. Start with context.read. Keep the same call_id when reconciling an uncertain logical action. The server owns project, task, role, repository, resource scope and approvals; caller identities or URLs cannot grant authority.';
function authenticated(authInfo) {
  return authInfo && typeof authInfo.clientId==='string' && authInfo.clientId.length>0 && authInfo.clientId.length<=512 && typeof authInfo.family==='string' && authInfo.family.length>0 && authInfo.family.length<=512 && Array.isArray(authInfo.scopes) && authInfo.scopes.includes('development') && Number.isSafeInteger(authInfo.expiresAt) && authInfo.expiresAt>Math.floor(Date.now()/1000);
}
export function governedToolDescriptor() {
  return {name:TOOL,description:DESCRIPTION,inputSchema:{type:'object',properties:{
    session_id:{type:'string',pattern:wire.ID.source},call_id:{type:'string',pattern:wire.ID.source},
    operation:{type:'string',maxLength:80},arguments:{type:'object',additionalProperties:{}}},
    required:['session_id','call_id','operation','arguments'],additionalProperties:false},
    annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:false,openWorldHint:true},
    _meta:{securitySchemes:[{type:'oauth2',scopes:['development']}]}};
}
export function augmentToolList(raw, response, authInfo) {
  let rpc;
  try { rpc=Buffer.isBuffer(raw)?JSON.parse(raw.toString('utf8')):raw; } catch { return response; }
  if(rpc?.method!=='tools/list' || Array.isArray(rpc))return response;
  if(!authenticated(authInfo))throw wire.fail('Authenticated tool discovery is required');
  let reply;
  try { reply=Buffer.isBuffer(response)?JSON.parse(response.toString('utf8')):response; } catch { return response; }
  if(rpc.jsonrpc!=='2.0' || (typeof rpc.id!=='string' && !Number.isSafeInteger(rpc.id)) || reply?.jsonrpc!=='2.0' || reply.id!==rpc.id || reply.error || !wire.object(reply.result) || !Array.isArray(reply.result.tools))return response;
  const cursor=reply.result.nextCursor;
  if(cursor!==undefined && (typeof cursor!=='string' || !cursor || cursor.length>4096))throw wire.fail('Backend tool pagination is invalid');
  if(reply.result.tools.length>2000 || reply.result.tools.some(item=>!wire.object(item)||typeof item.name!=='string'))throw wire.fail('Backend tool catalogue is invalid');
  // Preserve every ordinary descriptor and pagination field. Only the owned
  // tool is canonicalized, once on the terminal page of the backend listing.
  const tools=reply.result.tools.filter(item=>item.name!==TOOL);
  if(cursor===undefined)tools.push(governedToolDescriptor());
  const result={...reply,result:{...reply.result,tools}};
  return Buffer.isBuffer(response)?Buffer.from(JSON.stringify(result)):result;
}
const error = (id, message) => ({ jsonrpc:'2.0', id:id ?? null, result:{ isError:true, content:[{type:'text',text:message}] } });
export function createGatewayEdge({ coreUrl = CORE, secretFile = EDGE_FILE, fetchImpl = fetch } = {}) {
  const base = wire.localBase(coreUrl);
  return async function governedRpc(raw, authInfo) {
    let rpc;
    try { rpc = Buffer.isBuffer(raw) ? JSON.parse(raw.toString('utf8')) : raw; } catch { return null; }
    if (Array.isArray(rpc)) {
      if (rpc.some(x => x?.method === 'tools/call' && x.params?.name === TOOL)) return {jsonrpc:'2.0',id:null,error:{code:-32600,message:'Governed calls require one original JSON-RPC request'}};
      return null;
    }
    if (rpc?.method !== 'tools/call' || rpc.params?.name !== TOOL) return null;
    try {
      if (rpc.jsonrpc !== '2.0' || (typeof rpc.id !== 'string' && !Number.isSafeInteger(rpc.id)) || (typeof rpc.id === 'string' && rpc.id.length > 160)) throw wire.fail();
      wire.exact(rpc.params,Object.hasOwn(rpc.params,'_meta')?['name','arguments','_meta']:['name','arguments']);
      const input = rpc.params.arguments;wire.exact(input,['session_id','call_id','operation','arguments']);
      const clientSession = rpc.params._meta?.['openai/session'];
      if (!authenticated(authInfo)) throw wire.fail('Authenticated governed connection is unavailable');
      const connectionHash=wire.digest([authInfo.clientId,authInfo.family]);
      if(input.operation==='transport.describe'){
        if(input.session_id!=='diagnostic-bootstrap' || typeof input.call_id!=='string' || !wire.ID.test(input.call_id) || !wire.object(input.arguments) || Object.keys(input.arguments).length)throw wire.fail();
        const present=typeof clientSession==='string' && clientSession.length>0 && clientSession.length<=512;
        const facts={protocol_version:1,enforcement_mode:wire.MODE,connection_hash:connectionHash,
          provider_session_metadata_present:present,...(present?{client_session_hash:wire.sha256(clientSession)}:{})};
        return {jsonrpc:'2.0',id:rpc.id,result:{content:[{type:'text',text:JSON.stringify(facts)}]}};
      }
      if (typeof clientSession !== 'string' || !clientSession || clientSession.length > 512 || typeof input.session_id !== 'string' || typeof input.call_id !== 'string' || !wire.ID.test(input.session_id) || !wire.ID.test(input.call_id) || typeof input.operation !== 'string' || !/^[A-Za-z][A-Za-z0-9_.:-]{0,79}$/.test(input.operation) || !wire.object(input.arguments)) throw wire.fail('Governed session metadata and exact operation are required');
      const body = { connection_hash:connectionHash,
        client_session_hash:wire.sha256(clientSession), session_id:input.session_id,
        call_id:input.call_id, operation:input.operation, arguments:input.arguments };
      const stamp = String(Math.floor(Date.now()/1000));const secret = wire.secretFile(secretFile);
      const signature = crypto.createHmac('sha256',secret).update(stamp+'\n'+wire.canonical(body)).digest('hex');
      const result = await wire.post(base,'call',body,{'X-ABIT-Gateway-Time':stamp,'X-ABIT-Gateway-Signature':signature},{fetchImpl,timeoutMs:60000,sensitiveValues:[secret]});
      wire.exact(result,['call_id','state','result','replayed']);
      if (result.call_id !== input.call_id || !['completed','uncertain'].includes(result.state) || typeof result.replayed !== 'boolean') throw wire.fail();
      return {jsonrpc:'2.0',id:rpc.id,result:{content:[{type:'text',text:JSON.stringify(result)}]}};
    } catch (failure) { return error(rpc.id,failure?.safeCoreDetail===true ? failure.message : 'Governed action denied or its original outcome is unknown. Preserve session_id/call_id and reconcile; do not create a replacement action.'); }
  };
}
export function registerGovernedTool(server, z) {
  server.registerTool(TOOL, {
    description:DESCRIPTION,
    inputSchema:{session_id:z.string().regex(wire.ID),call_id:z.string().regex(wire.ID),operation:z.string().max(80),arguments:z.record(z.string(),z.unknown())},
    annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:false,openWorldHint:true},
    _meta:{securitySchemes:[{type:'oauth2',scopes:['development']}]},
  }, async () => ({isError:true,content:[{type:'text',text:'Governed actions require the authenticated OAuth gateway edge. Direct backend execution is denied.'}]}));
}

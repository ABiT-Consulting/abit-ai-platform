'use strict';
// Shared transport encoding. No credential, endpoint or execution authority.
const crypto = require('node:crypto');
const fs = require('node:fs');
const MODE = 'governed_action_gateway';
const BOUNDARY = 'gateway_operations_and_managed_erp';
const EXCLUDED = ['owner_account_plugins'];
const MAX_BYTES = 300000;
const ID = /^[A-Za-z0-9_.:@+-]{8,160}$/;
const HASH = /^[a-f0-9]{64}$/;
function fail(message = 'Governed gateway request could not be verified') {
  return Object.assign(new Error(message), { statusCode: 424, notSubmitted: true });
}
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function exact(value, keys) {
  if (!object(value) || Object.keys(value).sort().join('\n') !== [...keys].sort().join('\n')) throw fail();
}
function compareUnicode(a, b) {
  const x = [...a].map(c => c.codePointAt(0)), y = [...b].map(c => c.codePointAt(0));
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] - y[i];
  return x.length - y.length;
}
function canonical(value, depth = 0) {
  if (depth > 30) throw fail('Gateway structured input is too deep');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  // Current fixed operations need integral counts/versions only. Reject rather
  // than round or sign a number differently from Python's canonical encoder.
  if (typeof value === 'number' && Number.isSafeInteger(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(x => canonical(x, depth + 1)).join(',') + ']';
  if (object(value) && Object.getPrototypeOf(value) === Object.prototype) return '{' + Object.keys(value).sort(compareUnicode).map(k => JSON.stringify(k) + ':' + canonical(value[k], depth + 1)).join(',') + '}';
  throw fail('Gateway input supports finite safe integers and JSON values only');
}
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const digest = value => sha256(canonical(value));
function secretFile(filename) {
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const st = fs.fstatSync(fd);
    if (!st.isFile() || st.size < 32 || st.size > 4096 || (st.mode & 0o027)) throw fail('Gateway server credential file is unavailable');
    const secret = fs.readFileSync(fd, 'utf8').trim();
    if (secret.length < 32 || secret.length > 4096) throw fail('Gateway server credential file is unavailable');
    return secret;
  } finally { fs.closeSync(fd); }
}
function localBase(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || !url.port || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw fail('Gateway Core endpoint must be fixed local HTTP');
  return url.origin;
}
async function post(base, route, body, headers, { fetchImpl = fetch, timeoutMs = 15000, sensitiveValues = [] } = {}) {
  const raw = canonical(body);
  if (Buffer.byteLength(raw) > MAX_BYTES) throw fail('Gateway request exceeds its transport bound');
  let response;
  try { response = await fetchImpl(localBase(base) + '/api/v1/agentic/gateway/' + route, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: raw,
    redirect: 'error', signal: AbortSignal.timeout(timeoutMs),
  }); } catch { throw Object.assign(fail('Governed gateway Core response is unavailable; preserve the original request'),{notSubmitted:route!=='call'}); }
  if (!response.ok) {
    let detail='';
    if ((response.headers.get('content-type')||'').toLowerCase().includes('application/json') && response.body) {
      const reader=response.body.getReader();const chunks=[];let size=0;
      try { while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;if(size>16384)break;chunks.push(Buffer.from(part.value));}
        if(size<=16384){const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));const text=value?.detail;
          const secrets=[...Object.values(headers),...sensitiveValues].filter(x=>typeof x==='string'&&x.length>=16);
          if(typeof text==='string' && text.trim() && Buffer.byteLength(text)<=1500 && !secrets.some(x=>text.includes(x)) && !/[<>\u0000-\u0008\u000b-\u001f]|traceback|authorization|bearer\s|(?:api[_-]?key|token|secret|password|cookie|credential)\s*[:=]/i.test(text))detail=text.trim();}
      } catch {} finally {await reader.cancel().catch(()=>{});}
    } else if(response.body) await response.body.cancel().catch(()=>{});
    const failure=Object.assign(fail('Core HTTP '+response.status+(detail?': '+detail:': governed request rejected; preserve the original request')),{notSubmitted:route!=='call'});
    if(detail)failure.safeCoreDetail=true;
    throw failure;
  }
  const reader = response.body.getReader();let size = 0;const chunks = [];
  try { while (true) { const { value, done } = await reader.read();if (done) break;size += value.length;if (size > MAX_BYTES) throw fail();chunks.push(Buffer.from(value)); } }
  finally { await reader.cancel().catch(() => {}); }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw fail('Governed gateway returned an invalid receipt'); }
}
module.exports = { MODE, BOUNDARY, EXCLUDED, MAX_BYTES, ID, HASH, object, exact, canonical, sha256, digest, secretFile, localBase, post, fail };

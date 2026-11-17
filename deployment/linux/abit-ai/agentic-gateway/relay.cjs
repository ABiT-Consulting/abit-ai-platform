'use strict';
// Installed only by the reviewed relay release. No browser/account mutation.
const w = require('./wire.cjs');
const DEFAULT_CORE = 'http://127.0.0.1:8888';
const CONTROL_FILE = '/srv/abit-dev-linux/abit-ai/config/erp-assignment/control-token';
const SCOPE_KEYS = ['owner','account_id','dot_id','project_id','task_id','role','agent_id','erp_user','repository','environment','tool_scopes','work_packet_digest','invocation_id','run_id','job_id'];
const PROOF_KEYS = ['protocol_version','scope','scope_digest','enforced','read_only','default_deny','enforcement_mode','enforcement_boundary','excluded_surfaces','gateway_session_id','enforced_tool_scopes','external_write_allowlist','proof_id','issued_at','expires_at'];
function scope(value) {
  w.exact(value, SCOPE_KEYS);
  if (SCOPE_KEYS.filter(x => x !== 'tool_scopes').some(x => typeof value[x] !== 'string') || !w.HASH.test(value.account_id) || !w.HASH.test(value.work_packet_digest) || !/^aibit_[a-f0-9]{48}$/.test(value.job_id) || !Array.isArray(value.tool_scopes) || value.tool_scopes.some(x => typeof x !== 'string')) throw w.fail();
  return value;
}
function proof(value, expected, fresh) {
  w.exact(value, PROOF_KEYS);const now = Math.floor(Date.now() / 1000);
  if (value.protocol_version !== 1 || w.canonical(value.scope) !== w.canonical(expected) || value.scope_digest !== w.digest(expected) || value.enforced !== true || value.read_only !== true || value.default_deny !== true || value.enforcement_mode !== w.MODE || value.enforcement_boundary !== w.BOUNDARY || w.canonical(value.excluded_surfaces) !== w.canonical(w.EXCLUDED) || !w.ID.test(value.gateway_session_id) || !w.ID.test(value.proof_id) || w.canonical(value.enforced_tool_scopes) !== w.canonical(expected.tool_scopes) || !Array.isArray(value.external_write_allowlist) || new Set(value.external_write_allowlist).size !== value.external_write_allowlist.length || value.external_write_allowlist.some(x => !expected.tool_scopes.includes(x)) || !Number.isSafeInteger(value.issued_at) || !Number.isSafeInteger(value.expires_at) || value.issued_at > now + 5 || value.expires_at <= value.issued_at || value.expires_at > value.issued_at + 60 || (fresh && (value.issued_at < now - 5 || value.expires_at <= now))) throw w.fail('Gateway scope proof does not authorize this original job');
}
function binding(body, fresh = true) {
  const value = body.governance_scope;
  w.exact(value, ['protocol_version','scope','preflight_proof','submission_proof']);
  const s = scope(value.scope);
  if (value.protocol_version !== 1 || body.owner !== s.owner || body.account_id !== s.account_id || body.job_id !== s.job_id) throw w.fail();
  proof(value.preflight_proof, s, false);proof(value.submission_proof, s, fresh);
  if (value.preflight_proof.gateway_session_id !== value.submission_proof.gateway_session_id || value.submission_proof.issued_at < value.preflight_proof.issued_at) throw w.fail();
  return value;
}
function expectedReceipt(value) {
  const s = value.scope, p = value.submission_proof;
  return { protocol_version:1, owner:s.owner, account_id:s.account_id, job_id:s.job_id,
    invocation_id:s.invocation_id, run_id:s.run_id, work_packet_digest:s.work_packet_digest,
    scope_digest:w.digest(s), preflight_proof_sha256:w.digest(value.preflight_proof),
    submission_proof_sha256:w.digest(p), proof_id:p.proof_id, gateway_session_id:p.gateway_session_id,
    enforcement_mode:w.MODE, enforcement_boundary:w.BOUNDARY, excluded_surfaces:w.EXCLUDED, accepted:true };
}
function checkedReceipt(result, value) {
  if (w.canonical(result) !== w.canonical(expectedReceipt(value))) throw w.fail('Gateway receipt belongs to another job, packet or session');
  return result;
}
function toolContext(message, receipt) {
  if (typeof message !== 'string' || !message.trim() || !w.ID.test(receipt.gateway_session_id)) throw w.fail();
  const text = message + '\n\nServer-admitted governed tool context:\n' +
    'Use the existing ABIT-DEV-LINUX tool agentic_governance_action for governed operations. ' +
    'Supply session_id=' + receipt.gateway_session_id + '. Keep a stable call_id for each logical operation; ' +
    'repeating an uncertain operation requires its SAME call_id. Begin with operation=context.read, arguments={}. ' +
    'This is a short-lived job capability, not ERP or account credentials. The server validates role, packet, lease, ' +
    'resources and approvals on each call. Unsupported actions fail closed. Owner account plugins are outside this gateway attestation.';
  if (text.length > 160000) throw w.fail('Governed tool context exceeds the existing relay prompt bound');
  return text;
}
function createGatewayAdapter({ coreUrl = DEFAULT_CORE, tokenFile = CONTROL_FILE, fetchImpl = fetch } = {}) {
  const base = w.localBase(coreUrl);
  const request = (route, body) => w.post(base, route, body, { 'X-ABIT-ERP-Control':w.secretFile(tokenFile) }, { fetchImpl,timeoutMs:30000 });
  return {
    prepare(body) {
      // Called at relay intake. Composer preparation may take time, so this
      // only derives the not-yet-admitted capability reference; no Core admit.
      const value = binding(body);const receipt = expectedReceipt(value);
      return { payload:{ ...body, message:toolContext(body.message, receipt) } };
    },
    async proof(body, configured = {}) {
      w.exact(body, ['protocol_version','scope','scope_digest']);scope(body.scope);
      if (body.protocol_version !== 1 || body.scope_digest !== w.digest(body.scope)) throw w.fail();
      if (configured.owner && body.scope.owner !== configured.owner) throw w.fail();
      if (configured.dotId && body.scope.dot_id !== configured.dotId) throw w.fail();
      const result = await request('proof', body);proof(result, body.scope, true);return result;
    },
    async admit(body) {
      // Called from awaited lifecycle.sending, after account/composer setup and
      // immediately before any browser Send. Initial freshness was checked at
      // intake; expiry, exact proof and current native authority still apply.
      const value = binding(body, false);
      if (value.submission_proof.expires_at <= Math.floor(Date.now()/1000)) throw w.fail('Original gateway admission proof expired before Send');
      const receipt = checkedReceipt(await request('admit', value), value);
      return { payload:{ ...body, message:toolContext(body.message, receipt) }, receipt };
    },
    async observe(body) {
      const value = binding(body, false);const receipt = checkedReceipt(await request('receipt', value), value);
      return { payload:{ ...body, message:toolContext(body.message, receipt) }, receipt };
    },
  };
}
module.exports = { createGatewayAdapter, expectedReceipt, binding, toolContext };

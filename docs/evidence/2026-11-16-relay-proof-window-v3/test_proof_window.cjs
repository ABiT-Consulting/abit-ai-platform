'use strict';
// Actual module, synthetic inputs, frozen clock: no credential or network access.
const assert = require('node:assert/strict');
const { createGatewayAdapter } = require('./relay.cjs');
const w = require('./wire.cjs');
const realNow = Date.now;
const issued = 1800000000;
const scope = Object.fromEntries(['owner','account_id','dot_id','project_id','task_id','role',
  'agent_id','erp_user','repository','environment','work_packet_digest','invocation_id','run_id','job_id']
  .map(k => [k, k + '-synthetic']));
Object.assign(scope, { account_id:'a'.repeat(64), work_packet_digest:'b'.repeat(64),
  job_id:'aibit_'+'c'.repeat(48), tool_scopes:['context.read'] });
function body() {
  const proof = { protocol_version:1, scope:structuredClone(scope), scope_digest:w.digest(scope),
    enforced:true, read_only:true, default_deny:true, enforcement_mode:w.MODE,
    enforcement_boundary:w.BOUNDARY, excluded_surfaces:w.EXCLUDED,
    gateway_session_id:'session-synthetic', enforced_tool_scopes:scope.tool_scopes,
    external_write_allowlist:[], proof_id:'proof-synthetic', issued_at:issued, expires_at:issued+60 };
  return { owner:scope.owner, account_id:scope.account_id, job_id:scope.job_id,
    message:'Synthetic original task', governance_scope:{ protocol_version:1, scope:structuredClone(scope),
      preflight_proof:structuredClone(proof), submission_proof:structuredClone(proof) } };
}
let networkCalls = 0;
const adapter = createGatewayAdapter({ fetchImpl:async () => {networkCalls++; throw Error('Network forbidden');} });
const cases=[];
function check(name, run) { run(); cases.push(name); }
(async () => {
  try {
    Date.now = () => (issued+1)*1000;
    check('fresh proof accepted', () => assert.ok(adapter.prepare(body()).payload));
    Date.now = () => (issued+33)*1000;
    check('proof accepted at 33 seconds within unchanged expiry', () => {
      const b=body(); assert.ok(b.governance_scope.submission_proof.issued_at < Date.now()/1000-5);
      assert.ok(adapter.prepare(b).payload);
    });
    Date.now = () => (issued+60)*1000;
    check('expired intake denied', () => assert.throws(() => adapter.prepare(body())));
    Date.now = () => (issued+1)*1000;
    check('proof longer than 60 seconds denied', () => {
      const b=body(); b.governance_scope.submission_proof.expires_at=issued+61;
      assert.throws(() => adapter.prepare(b));
    });
    Date.now = () => (issued-6)*1000;
    check('future-issued proof denied', () => assert.throws(() => adapter.prepare(body())));
    Date.now = () => (issued+1)*1000;
    check('changed Task scope denied', () => {
      const b=body(); b.governance_scope.scope.task_id='TASK-OTHER';
      assert.throws(() => adapter.prepare(b));
    });
    Date.now = () => (issued+60)*1000;
    await assert.rejects(adapter.admit(body()), /expired before Send/);
    assert.equal(networkCalls,0); cases.push('expired Send admission denied before credential/network access');
    console.log(JSON.stringify({passed:cases.length,cases,networkCalls}));
  } finally {Date.now=realNow;}
})().catch(e=>{console.error(e);process.exitCode=1;});

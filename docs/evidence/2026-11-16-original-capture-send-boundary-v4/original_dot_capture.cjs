'use strict';
// A terminal read may finish its ORIGINAL capture, never another browser Send.
const crypto = require('node:crypto');
const {dotResponseLocationMatches} = require('./aibit_dot_handoff.cjs');
const canonical = value => JSON.stringify(value, function (key, item) {
  return item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item;
});
const digest = value => crypto.createHash('sha256').update(canonical(value)).digest('hex');
const text = value => typeof value === 'string' && value.length > 0;
function identity(body, verified) {
  if (!body.governance_scope || !text(verified.thread_id) || !text(verified.url) ||
      !text(body.owner) || !text(body.account_id) || !text(body.job_id) || !text(verified.message))
    throw Error('Original governed capture identity is required');
  return Object.freeze({owner:body.owner, account_id:body.account_id, job_id:body.job_id,
    thread_id:verified.thread_id, url:verified.url,
    message_sha256:digest(verified.message), scope_sha256:digest(body.governance_scope)});
}
function createCaptureRegistry() {
  const active = new Map();
  const key = value => digest([value.owner, value.account_id, value.job_id]);
  return {
    register(value) {
      const id = key(value);
      if (active.has(id)) throw Error('Original capture already exists');
      const entry = {identity:canonical(value), phase:'preparing', receipt:null,
        target:null, root:null, wake:null, result:null, signal:Error('Original capture settled by verified observation')};
      // This private error is not a CDP disconnect and must never trigger reconnect.
      entry.signal.code = 'DOT_ORIGINAL_CAPTURE_OBSERVED';
      active.set(id,entry);
      const handle = {
        admit(receipt) {
          if (entry.phase !== 'preparing' || !receipt || receipt.accepted !== true)
            throw Error('Original admitted capture receipt is required');
          entry.receipt = canonical(receipt);
        },
        begin({targetId, rootId, wake}) {
          if (active.get(id) !== entry || entry.phase !== 'preparing' || !entry.receipt ||
              !text(targetId) || typeof wake !== 'function') throw Error('Only the admitted submitted original may capture');
          entry.phase='capturing';entry.target=targetId;entry.wake=wake;
          if (rootId) handle.rememberRoot(rootId);
        },
        rememberRoot(rootId) {
          if (!text(rootId) || (entry.root && entry.root !== rootId))
            throw Error('Original capture root changed');
          entry.root=rootId;
        },
        guard() { if (entry.result) throw entry.signal; },
        resultFor(error) { return error === entry.signal ? entry.result : null; },
        complete() {
          if (entry.result) throw entry.signal;
          entry.phase='completed';if(active.get(id)===entry)active.delete(id);
        },
        close() { entry.phase='closed';if(active.get(id)===entry)active.delete(id); },
      };
      return handle;
    },
    settle(value, observation, receipt) {
      const entry=active.get(key(value)), result=observation && observation.result;
      if (!entry || entry.identity!==canonical(value) || entry.phase!=='capturing' || entry.result ||
          observation.idle!==true || observation.target_id!==entry.target ||
          observation.thread_id!==value.thread_id || !result || result.ok!==true ||
          result.read_only!==true || result.dot_handoff!==true || result.owner!==value.owner ||
          result.account_id!==value.account_id || result.job_id!==value.job_id ||
          !text(result.response) || !text(result.root_message_id) || !text(result.reply_message_id) ||
          (entry.root && result.root_message_id!==entry.root) ||
          !dotResponseLocationMatches(result.conversation_url,value.url) ||
          canonical(receipt)!==entry.receipt) return false;
      entry.root=result.root_message_id;
      entry.result=Object.freeze({...result,scope_receipt:JSON.parse(entry.receipt)});
      // Wakes only its read-side CDP session. The ORIGINAL handler owns release().
      entry.wake(entry.signal);
      return true;
    },
  };
}
module.exports={createCaptureRegistry,identity};

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
const TOO_LONG = 'The message you submitted was too long, please edit it and resubmit.';
function rejectedBeforeModel(result) {
  const r=result && result.rejection;
  return Boolean(result && result.ok===true && result.read_only===true && result.dot_handoff===false &&
    result.response==='' && result.reply_message_id===null && r &&
    r.code==='CHATGPT_MESSAGE_TOO_LONG' && r.not_submitted===true && r.dialog===TOO_LONG &&
    ((result.protocol==='v4_outgoing_rejection' && text(result.root_message_id) && result.root_failure &&
      result.root_failure.root_id===result.root_message_id && result.root_failure.original_envelope_matched===true &&
      result.root_failure.failed_to_send===true && result.root_failure.failed_send_label==='Failed to send. Click to retry' &&
      result.root_failure.generating===false) ||
     (result.protocol===undefined && result.root_message_id===null && r.original_user_visible===false &&
      r.feed_present===false && r.generating===false && r.users===0 && r.assistants===0)));
}
function identity(body, verified) {
  if (!body.governance_scope || !text(verified.thread_id) || !text(verified.url) ||
      !text(body.owner) || !text(body.account_id) || !text(body.job_id) || !text(verified.message))
    throw Error('Original governed capture identity is required');
  return Object.freeze({owner:body.owner, account_id:body.account_id, job_id:body.job_id,
    thread_id:verified.thread_id, url:verified.url,
    message_sha256:digest(verified.message), scope_sha256:digest(body.governance_scope)});
}
function originalIntakeFailure(error) {
  if (error?.code!=='DOT_ORIGINAL_ALREADY_BOUND' && !/^DOT_BINDING_/.test(error?.code||'')) return null;
  return {status:Number(error.statusCode||409),body:{ok:false,code:'DOT_OUTCOME_UNCERTAIN',
    safe_to_fallback:false,message:'Original Dot state must be reconciled without resending.'}};
}
function createCaptureRegistry({bindings} = {}) {
  const active = new Map();
  const key = value => digest([value.owner, value.account_id, value.job_id]);
  return {
    register(value) {
      // Guard before allocation/navigation/composer changes, not just before Send.
      if (bindings && bindings.lookup(value)) throw Object.assign(
        Error('The original governed job already has a durable binding.'),
        {code:'DOT_ORIGINAL_ALREADY_BOUND',statusCode:409});
      const id = key(value);
      if (active.has(id)) throw Error('Original capture already exists');
      const entry = {identity:canonical(value), phase:'preparing', receipt:null,
        target:null, root:null, wake:null, result:null, rejection:null, sendAttempted:false,accepted:false,
        rejectionBaseline:false, terminalCandidate:null,
        signal:Error('Original capture settled by verified observation'),
        rejectSignal:Object.assign(Error('Original owned Dot message was rejected as too long before model execution'),
          {code:'DOT_MESSAGE_TOO_LONG_REJECTED',statusCode:424,notSubmitted:true})};
      // This private error is not a CDP disconnect and must never trigger reconnect.
      entry.signal.code = 'DOT_ORIGINAL_CAPTURE_OBSERVED';
      active.set(id,entry);
      const handle = {
        admit(receipt) {
          if (entry.phase !== 'preparing' || !receipt || receipt.accepted !== true)
            throw Error('Original admitted capture receipt is required');
          entry.receipt = canonical(receipt);
        },
        begin({targetId, rootId, wake, baseline}) {
          if (active.get(id) !== entry || entry.phase !== 'preparing' || !entry.receipt ||
              !text(targetId) || typeof wake !== 'function') throw Error('Only the admitted submitted original may capture');
          // Durable identity is committed before the single original Send.
          // Existing records reject another submission after a process restart.
          if (bindings) bindings.begin(value,targetId);
          entry.phase='capturing';entry.target=targetId;entry.wake=wake;
          entry.rejectionBaseline=Boolean(baseline && baseline.users===0 && baseline.count===0 &&
            baseline.generating===false && baseline.tooLongDialog===false);
          if (rootId) handle.rememberRoot(rootId);
        },
        rememberRoot(rootId, conversationUrl, terminal) {
          if (!text(rootId) || (entry.root && entry.root !== rootId))
            throw Error('Original capture root changed');
          if (bindings) bindings.rememberRoot(value,{target_id:entry.target,
            root_id:rootId,conversation_url:conversationUrl});
          entry.root=rootId;
          if (terminal && terminal.complete===true && text(terminal.reply_id) && text(terminal.response))
            entry.terminalCandidate={root_id:rootId,reply_id:terminal.reply_id,
              response:terminal.response,conversation_url:conversationUrl};
        },
        sent() {
          if (active.get(id)!==entry || entry.phase!=='capturing') throw Error('Only the active admitted original may record Send');
          entry.sendAttempted=true;
        },
        accepted() { entry.accepted=true; },
        guard() { if (entry.rejection) throw entry.rejectSignal;if (entry.result) throw entry.signal; },
        resultFor(error) { return error === entry.signal ? entry.result : null; },
        rejectionFor(error) { return error === entry.rejectSignal ? entry.rejection : null; },
        complete() {
          if (entry.rejection) throw entry.rejectSignal;
          if (entry.result) throw entry.signal;
          if (bindings && entry.terminalCandidate) bindings.terminal(value,entry.terminalCandidate);
          entry.phase='completed';if(active.get(id)===entry)active.delete(id);
        },
        // Timeout/disconnect only releases this request's memory. Durable
        // selectors and the unresolved tab pin survive for normal observation.
        close() { entry.phase='closed';if(active.get(id)===entry)active.delete(id); },
      };
      return handle;
    },
    reject(value, observation, receipt) {
      const entry=active.get(key(value)),result=observation && observation.result;
      const failedOutgoing=result && result.protocol==='v4_outgoing_rejection';
      if (!entry || entry.identity!==canonical(value) || entry.phase!=='capturing' || entry.result ||
          entry.rejection || (!failedOutgoing && (!entry.sendAttempted || entry.accepted || !entry.rejectionBaseline || entry.root)) ||
          (failedOutgoing && entry.root && entry.root!==result.root_message_id) || observation.idle!==true ||
          observation.target_id!==entry.target || observation.thread_id!==value.thread_id ||
          !rejectedBeforeModel(result) || result.owner!==value.owner || result.account_id!==value.account_id ||
          result.job_id!==value.job_id || !dotResponseLocationMatches(result.conversation_url,value.url) ||
          canonical(receipt)!==entry.receipt) return false;
      const {observed,...proof}=result;
      entry.rejection=Object.freeze({...proof,scope_receipt:JSON.parse(entry.receipt),
        ...(failedOutgoing?{}:{rejection_capture:{send_attempted:true,baseline_empty:true,baseline_rejection_absent:true,accepted_observed:false}})});
      // This original handler alone releases its reservation. No new browser action.
      entry.wake(entry.rejectSignal);
      return true;
    },
    settle(value, observation, receipt) {
      const entry=active.get(key(value)), result=observation && observation.result;
      if (!entry || entry.identity!==canonical(value) || entry.phase!=='capturing' || entry.result || entry.rejection ||
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
module.exports={createCaptureRegistry,identity,rejectedBeforeModel,TOO_LONG,originalIntakeFailure};

'use strict';
// Fixed DOM reader for the owner-authenticated Dot message feed. It never sends.
// Keep helpers inside this function: readDotFeed serializes it into the DOM scope.
function dotFeedSnapshotDOM(expected, markdown, knownRootId, nativeSource) {
  const source = String(expected);
  const outerLabel = 'Original project, instructions, prompt, prior chat and exact run idempotency key (JSON):';
  // Python MAX_MESSAGE allows 160,000 code points for legacy originals, up to
  // twice as many UTF-16 units. Governed intake independently retains its tighter
  // 160,000-unit post-tool-suffix cap; this reader does not change either limit.
  const maxContextUnits = 160000 * 2;
  const maxResultBytes = 200000;
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const nonempty = value => typeof value === 'string' && value.length > 0;
  function stripResultSpace(value) {
    // Python str.strip(), used by Core, differs from JS trim for NEL, control
    // separators and BOM. Keep payload selection consistent without rewriting it.
    const space = /[\t\n\v\f\r\x1c-\x20\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/;
    let start = 0, end = value.length;
    while (start < end && space.test(value[start])) start++;
    while (end > start && space.test(value[end - 1])) end--;
    return value.slice(start, end);
  }

  // Read one complete object, without consuming the server's appended tool or
  // role instructions. Braces and labels inside JSON strings are only data.
  function objectAt(text, position) {
    const limit = Math.min(text.length, position + maxContextUnits);
    while (position < limit && /\s/.test(text[position])) position++;
    if (text[position] !== '{') throw Error('Expected context object');
    const start = position;
    let depth = 0, quoted = false, escaped = false;
    for (; position < limit; position++) {
      const c = text[position];
      if (quoted) {
        if (escaped) escaped = false;
        else if (c === '\\') escaped = true;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === '{') depth++;
      else if (c === '}' && --depth === 0) {
        const value = JSON.parse(text.slice(start, position + 1));
        if (!isObject(value)) throw Error('Expected context object');
        return {value, end: position + 1};
      }
    }
    throw Error('Incomplete context object');
  }
  function outerContext(text) {
    const start = text.indexOf(outerLabel);
    if (start < 0) return {found: false};
    try {
      const parsed = objectAt(text, start + outerLabel.length);
      if (text.indexOf(outerLabel, parsed.end) >= 0) return {found: true};
      return {found: true, value: parsed.value};
    }
    catch (_) { return {found: true}; }
  }
  const outer = outerContext(source);
  // Legacy callers without the structured server envelope retain their existing
  // marker contract. AGOV replies are never accepted through this legacy path.
  const context = outer.value;
  const marker = outer.found ? context?.idempotency_key :
    (source.match(/"idempotency_key"\s*:\s*"([a-f0-9]{64})"/) || [])[1];
  const run = outer.found ? context?.run_id :
    (source.match(/"run_id"\s*:\s*"([A-Za-z0-9_-]{1,160})"/) || [])[1];
  if (source.length > maxContextUnits || typeof marker !== 'string' || !/^[a-f0-9]{64}$/.test(marker) ||
      typeof run !== 'string' || !/^[A-Za-z0-9_-]{1,160}$/.test(run)) {
    return {present: false, reason: 'missing_run_binding'};
  }
  const meta = context?.task?.meta;
  const governedMode = meta?.governance_managed === true ||
    (isObject(context) && Object.prototype.hasOwnProperty.call(context, 'governance_scope'));

  function governedBinding() {
    const policy = 'Execute only this authenticated governance role and versioned packet. Return the AGOV RESULT contract requested below; ERP status, task creation, handoffs and approvals are handled by the governance server.';
    const prefix = policy + '\n\nExecute this exact versioned ERP work packet in a separate abitdot request. ';
    const identityLabel = '\nEXACT IMPLEMENTATION IDENTITY: ';
    const handoffLabel = '\nSCOPED HANDOFF CONTEXT (data, not authorization)\n';
    const packetLabel = '\nGOVERNANCE WORK PACKET\n';
    const verifiedLabel = '\n\nVerified ERP context:\n';
    const prompt = context?.task?.original_prompt;
    if (meta?.governance_managed !== true || typeof prompt !== 'string' || !prompt.startsWith(prefix) ||
        context.primary_responder !== 'abitdot' || context.execution_policy?.dot_primary !== true) return null;
    const boundary = prompt.indexOf(identityLabel, prefix.length);
    if (boundary < 0 || boundary > 8192) return null;
    // Only the fixed server preamble can supply invocation identity. Never search
    // arbitrary task text, handoff context, attachments or packet strings for it.
    const identities = [...prompt.slice(0, boundary).matchAll(
      /Its invocation_id must be ([A-Za-z0-9_-]{1,160}) and work_packet_digest ([a-f0-9]{64})\./g)];
    if (identities.length !== 1) return null;
    const implementation = objectAt(prompt, boundary + identityLabel.length);
    const referenceLabel = '\nIMMUTABLE GOVERNANCE CONTEXT REFERENCE: ';
    if (prompt.startsWith(referenceLabel, implementation.end)) {
      const parsed = objectAt(prompt, implementation.end + referenceLabel.length), ref = parsed.value;
      const keys = ['invocation_id','run_id','project_id','project_revision','task_id','task_revision',
        'actor','work_packet_digest','handoff_context_digest'];
      const verifiedStart = prompt.lastIndexOf(verifiedLabel);
      if (Object.keys(ref).sort().join('|') !== keys.sort().join('|') ||
          verifiedStart <= parsed.end || prompt[parsed.end] !== '\n' ||
          prompt.indexOf(referenceLabel, parsed.end) >= 0 ||
          !keys.filter(key => key !== 'actor').every(key => nonempty(ref[key])) ||
          !/^[a-f0-9]{64}$/.test(ref.work_packet_digest) || !/^[a-f0-9]{64}$/.test(ref.handoff_context_digest)) return null;
      const verified = objectAt(prompt, verifiedStart + verifiedLabel.length).value;
      const actor = ref.actor;
      if (!isObject(actor) || !['project_id','role','agent_id','erp_user'].every(key => nonempty(actor[key])) ||
          ref.invocation_id !== identities[0][1] || ref.run_id !== run ||
          ref.work_packet_digest !== identities[0][2] || ref.work_packet_digest !== meta.governance_packet_digest ||
          ref.project_id !== meta.governance_project || ref.task_id !== meta.erp_task ||
          actor.project_id !== ref.project_id || actor.role !== meta.erp_role || actor.agent_id !== context.automation_id ||
          meta.erp_revision?.run_id !== run || verified.latest_run?.run_id !== run ||
          verified.role !== actor.role || verified.work_packet_digest !== ref.work_packet_digest ||
          verified.task?.name !== ref.task_id || verified.task?.project !== ref.project_id ||
          verified.project?.name !== ref.project_id || verified.project?.revision !== ref.project_revision ||
          verified.context_source !== 'agentic_governance_action context.read: work_packet and handoff_context') return null;
      const last = objectAt(prompt, verifiedStart + verifiedLabel.length);
      if (prompt.slice(last.end).trim()) return null;
      return {invocation:ref.invocation_id,packet:{schema_version:1,project_id:ref.project_id,
        task_id:ref.task_id,task_revision:ref.task_revision,binding:actor,content_sha256:ref.work_packet_digest}};
    }
    if (!prompt.startsWith(handoffLabel, implementation.end)) return null;
    // Handoff context is arbitrary prose or JSON, so a first label match could
    // select quoted task data. The server appends the actual packet after that
    // context, then fixed role guidance and the final JSON verified context.
    // JSON string newlines are escaped. Use those final server boundaries.
    const verifiedStart = prompt.lastIndexOf(verifiedLabel);
    if (verifiedStart < implementation.end + handoffLabel.length) return null;
    const verified = objectAt(prompt, verifiedStart + verifiedLabel.length);
    if (prompt.slice(verified.end).trim()) return null;
    const packetStart = prompt.lastIndexOf(packetLabel, verifiedStart);
    if (packetStart < implementation.end + handoffLabel.length) return null;
    const parsedPacket = objectAt(prompt, packetStart + packetLabel.length);
    if (parsedPacket.end > verifiedStart) return null;
    const packet = parsedPacket.value;
    const actor = packet.binding;
    // This is transport identity comparison, not packet/result business
    // validation. Core alone validates evidence, outcomes, plans and acceptance.
    if (packet.schema_version !== 1 || !isObject(actor) ||
        !['project_id', 'task_id', 'task_revision'].every(key => nonempty(packet[key])) ||
        !['project_id', 'role', 'agent_id', 'erp_user'].every(key => nonempty(actor[key])) ||
        packet.content_sha256 !== identities[0][2] || packet.content_sha256 !== meta.governance_packet_digest ||
        packet.project_id !== meta.governance_project || packet.task_id !== meta.erp_task ||
        actor.project_id !== packet.project_id || actor.role !== meta.erp_role ||
        actor.agent_id !== context.automation_id ||
        (meta.erp_revision?.run_id !== undefined && meta.erp_revision.run_id !== run)) return null;
    return {invocation: identities[0][1], packet};
  }
  let governed = null;
  if (governedMode) try { governed = governedBinding(); } catch (_) {}

  const rows = Array.from(document.querySelectorAll('article.message-row[data-message-id]'));
  const expectedContext = context && JSON.stringify(context);
  let roots = rows.filter(row => {
    if (!row.classList.contains('self')) return false;
    const text = String(row.textContent || '');
    if (!governedMode) return text.includes(marker);
    const visible = outerContext(text);
    // The complete public envelope must match, not merely a quoted digest or an
    // invocation borrowed from a different project. A cached root is only used
    // after readDotFeed verified this exact expected message in this session,
    // or the owner/scope-verified observer restored its immutable original root.
    if (visible.value) return JSON.stringify(visible.value) === expectedContext;
    return !visible.found && knownRootId && row.getAttribute('data-message-id') === knownRootId;
  });
  if (!governedMode && !roots.length && knownRootId) roots = rows.filter(row =>
    row.classList.contains('self') && row.getAttribute('data-message-id') === knownRootId);
  if (roots.length !== 1) return {present: false, reason: roots.length ? 'ambiguous_run_binding' : 'not_visible'};
  const root = roots[0], start = rows.indexOf(root), rootId = root.getAttribute('data-message-id');
  if (governedMode && knownRootId && rootId !== knownRootId) {
    return {present: false, reason: 'different_original_root'};
  }
  const originalTurn = [];
  for (const row of rows.slice(start + 1)) {
    // Even an empty/virtualized later user row ends this original's reply scope.
    if (row.classList.contains('self')) break;
    originalTurn.push(row);
  }
  let selectedRows = originalTurn, terminalSelection = false, rootLinkedOutsideTurn = false;
  // The native source path reuses the existing original-turn selection. These
  // helpers do not grant alternate DOM attributes any selection authority.
  const exposed = row => {
      for (let node = row; node; node = node.parentElement) {
        if (node.hidden || node.inert || node.getAttribute?.('aria-hidden') === 'true') return false;
        if (typeof getComputedStyle === 'function') {
          const style = getComputedStyle(node);
          if (style.display === 'none' || ['hidden','collapse'].includes(style.visibility) || style.opacity === '0') return false;
        }
      }
      return true;
    };
    const references = row => typeof row?.querySelectorAll === 'function'
      ? Array.from(row.querySelectorAll('button.reply-reference.reply-chain-reference[data-message-id]'))
        .map(node => node.getAttribute('data-message-id')).filter(Boolean) : [];
  if (governedMode && governed) {
    // Diagnostic only: observe whether a mounted, exposed candidate names the
    // exact original root beyond the next-user boundary. Do NOT select it or
    // parse it as the original's result; the existing reply window is unchanged.
    rootLinkedOutsideTurn = rows.slice(start + 1 + originalTurn.length).some(row => {
      if (row.classList.contains('self') || !exposed(row) || !references(row).includes(rootId)) return false;
      const body = row.querySelector('.message-body[data-message-id]') || row;
      const text = String(body.innerText || body.textContent || '');
      // A marker is only a candidate, never a parsed/accepted result. Ordinary
      // root-linked commentary must not hide the actual original-turn reason.
      return text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
        .some(line => line.trimStart().startsWith('AGOV RESULT:'));
    });
    const nonemptyRows = originalTurn.filter(row => exposed(row) &&
      String((row.querySelector('.message-body[data-message-id]') || row).textContent || '').trim());
    const terminal = nonemptyRows[nonemptyRows.length - 1], refs = references(terminal);
    // Select the response message before parsing its contract. A positive UI
    // reply-reference binds the terminal message to the exact original root;
    // earlier unlinked commentary is not a competing final response. Without
    // this evidence retain the existing strict multiple-result behavior.
    if (refs.length || originalTurn.some(row => references(row).length)) {
      const linked = nonemptyRows.filter(row => references(row).includes(rootId));
      const branching = terminal?.querySelectorAll?.('[data-branch],[data-branch-id],[data-variant],[data-variant-id]');
      const generating = typeof document.querySelector !== 'function' || Boolean(document.querySelector(
        '[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]'));
      if (!terminal || !refs.length || !refs.every(value => value === rootId) || linked.length !== 1 ||
          linked[0] !== terminal || branching?.length || generating ||
          ['data-branch','data-branch-id','data-variant','data-variant-id'].some(key => terminal.getAttribute?.(key))) {
        selectedRows = [];
      } else {
        selectedRows = [terminal];
        terminalSelection = true;
      }
    }
  }
  const bodyOf = row => row.querySelector('.message-body[data-message-id]') || row;
  const markerLines = text => text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
    .filter(line => line.trimStart().startsWith('AGOV RESULT:'));
  const nativeMode = nativeSource?.enabled === true;
  const markerRows = selectedRows.filter(row => markerLines(String(bodyOf(row).innerText || bodyOf(row).textContent || '')).length);
  const renderedMarkerCount = selectedRows.reduce((count,row) => count + markerLines(String(bodyOf(row).innerText || bodyOf(row).textContent || '')).length,0);
  const nativeRow = markerRows.length === 1 ? markerRows[0] : null;
  const nativeRowId = nativeRow?.getAttribute('data-message-id');
  const branchSelector = '[data-branch],[data-branch-id],[data-variant],[data-variant-id]';
  const nativeIdle = typeof document.querySelector === 'function' && !document.querySelector(
    '[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]');
  const nativeEligible = Boolean(governedMode && governed && nativeIdle && !rootLinkedOutsideTurn &&
    renderedMarkerCount === 1 && nativeRow && exposed(nativeRow) && exposed(bodyOf(nativeRow)) && nativeRowId &&
    rows.filter(row => row.getAttribute('data-message-id') === nativeRowId).length === 1 &&
    !nativeRow.querySelectorAll?.(branchSelector)?.length &&
    !['data-branch','data-branch-id','data-variant','data-variant-id'].some(key => nativeRow.getAttribute?.(key)));
  const candidate = nativeEligible ? {root_id:rootId,reply_id:nativeRowId,
    selection_mode:terminalSelection?'linked_terminal':'bounded_original_turn_candidate'} : null;
  const supplied = nativeSource?.reply;
  const sourceMatches = Boolean(nativeMode && nativeEligible && supplied &&
    supplied.root_id === rootId && supplied.reply_id === nativeRowId && typeof supplied.text === 'string' &&
    supplied.witness?.root_text === String(root.textContent || '') &&
    supplied.witness?.body_text === String(bodyOf(nativeRow).innerText || bodyOf(nativeRow).textContent || '') &&
    supplied.text.length <= 250000 && new TextEncoder().encode(supplied.text).length <= 250000);
  let reply = null, contractCount = 0, contractRejection = '';
  for (const row of selectedRows) {
    const body = bodyOf(row);
    const text = sourceMatches && row === nativeRow ? supplied.text : String(body.innerText || body.textContent || '');
    if (!text.trim()) continue;
    if (governedMode) {
      // Match Python str.splitlines(), including CR-only and Unicode separators.
      // Do not promote an indented first line by trimming the whole response.
      const contracts = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
        .filter(line => line.trimStart().startsWith('AGOV RESULT:'));
      contractCount += contracts.length;
      if (!governed || contracts.length !== 1) continue;
      if (!contracts[0].startsWith('AGOV RESULT:')) {
        contractRejection = 'contract_indented'; continue;
      }
      const json = stripResultSpace(contracts[0].slice('AGOV RESULT:'.length));
      if (json.length > maxResultBytes || new TextEncoder().encode(json).length > maxResultBytes) {
        contractRejection = 'contract_oversized'; continue;
      }
      try {
        const result = JSON.parse(json), packet = governed.packet;
        if (!isObject(result) || result.schema_version !== 1 || result.invocation_id !== governed.invocation ||
            !['project_id', 'task_id', 'task_revision'].every(key => result[key] === packet[key]) ||
            result.work_packet_digest !== packet.content_sha256 || !isObject(result.actor) ||
            !['project_id', 'role', 'agent_id', 'erp_user'].every(key => result.actor[key] === packet.binding[key]) ||
            (result.run_id !== undefined && result.run_id !== run)) {
          contractRejection = 'contract_identity_mismatch'; continue;
        }
        // Return the original JSON line unchanged. Markdown rendering can escape
        // its bytes. The caller retains its existing stable-text completion wait.
        reply = {response: text, reply_id: row.getAttribute('data-message-id')};
      } catch (_) { contractRejection = 'contract_malformed'; }
    } else {
      const legacyText = text.trim();
      const lines = legacyText.split('\n').map(line => line.trim()).filter(Boolean);
      const developer = /^ERP RESULT: (READY FOR REVIEW|CONTINUE|BLOCKED)$/.test(lines.at(-1) || '');
      let qa = false;
      for (const line of lines) if (line.startsWith('ERP QA RESULT:')) try {
        const result = JSON.parse(line.slice('ERP QA RESULT:'.length).trim());
        qa = result.run_id === run && ['pass', 'fail', 'blocked'].includes(result.status);
      } catch (_) {}
      if (developer || qa) reply = {response: typeof markdown === 'function' ? markdown(body) : legacyText,
        reply_id: row.getAttribute('data-message-id')};
    }
  }
  // Exactly one contract is required inside the selected reply. Without a
  // positively root-linked terminal reply, multiple result rows stay ambiguous.
  if (governedMode && contractCount !== 1) reply = null;
  // The optional original-observation native path never accepts rendered text.
  // Source bytes are supplied only by its acknowledged Copy reader and remain
  // unchanged; the same complete contract/actor/context checks above apply.
  if (nativeMode && !sourceMatches) reply = null;
  let rootFailure;
  // A failed optimistic user post is not an accepted model turn. Require the
  // complete visible original envelope and its own visible retry control;
  // global failure banners or a cached/virtualized root cannot supply proof.
  const rootContext = governedMode && outerContext(String(root.textContent || '')).value;
  if (governedMode && governed && rootContext && JSON.stringify(rootContext) === expectedContext &&
      typeof root.querySelectorAll === 'function' && typeof document.querySelector === 'function') {
    const failed = Array.from(root.querySelectorAll('button[aria-label]')).filter(button => {
      if (button.getAttribute('aria-label') !== 'Failed to send. Click to retry') return false;
      const rect=button.getBoundingClientRect(),style=getComputedStyle(button);
      return rect.width>0 && rect.height>0 && style.display!=='none' && style.visibility!=='hidden';
    });
    const generating=Boolean(document.querySelector('[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]'));
    if(failed.length===1 && !reply && generating===false) rootFailure={root_id:rootId,
      original_envelope_matched:true,failed_to_send:true,
      failed_send_label:'Failed to send. Click to retry',generating:false};
  }
  // These bounded labels explain an incomplete read only. They confer no
  // receipt, root selection, authority, retry or completion permission.
  const replyDiagnosticReason = !governed ? 'protocol_unrecognized' :
    rootLinkedOutsideTurn ? 'root_linked_candidate_outside_turn' :
    originalTurn.length === 0 ? 'reply_rows_absent' :
    selectedRows.length === 0 ? 'root_link_selection_rejected' :
    contractCount === 0 ? 'contract_absent' :
    contractCount !== 1 ? 'contract_multiple' : contractRejection || 'unclassified';
  const count = value => Math.min(65535,value);
  const turnRefs = originalTurn.flatMap(references);
  const alternateKeys = ['data-message-author-role','data-content-search-unit-key','data-chatgpt-search-message-ids','data-testid'];
  let alternateAttributes=0;
  for(const row of originalTurn) for(let i=0;i<alternateKeys.length;i++) {
    const value=row.getAttribute?.(alternateKeys[i]);
    if(typeof value === 'string' && value && (i!==3 || value.startsWith('conversation-turn-'))) alternateAttributes |= 1<<i;
  }
  // Counts and known attribute-presence bits are diagnostics, never authority.
  const selection = {mode:terminalSelection?'linked_terminal':
      turnRefs.length?'linked_selection_rejected':'bounded_original_turn_candidate',
    root_source:rootContext&&JSON.stringify(rootContext)===expectedContext?'full_envelope':'restored_root',
    turn_rows:count(originalTurn.length),selected_rows:count(selectedRows.length),
    visible_rows:count(originalTurn.filter(row=>exposed(row)&&String(bodyOf(row).textContent||'').trim()).length),
    root_references:count(turnRefs.filter(value=>value===rootId).length),
    unmatched_references:count(turnRefs.filter(value=>value!==rootId).length),
    agov_markers:count(renderedMarkerCount),alternate_attributes:alternateAttributes,
    candidate: nativeEligible,stage:'selection'};
  return {present: true, root_id: rootId, run_id: run, ...(reply || {}), complete: Boolean(reply),
    ...(governedMode && governed && !reply ? {selection} : {}),
    ...(nativeMode && candidate ? {native_source_candidate:candidate} : {}),
    ...(nativeMode && supplied ? {native_source_matches:sourceMatches} : {}),
    ...(governedMode && !reply ? {reply_diagnostic_reason:replyDiagnosticReason} : {}),
    // Internal candidate for a read-only capability probe, never a response ID
    // or authority. Only the existing positively root-linked terminal qualifies.
    ...(governedMode && governed && terminalSelection && selectedRows.length===1 &&
        !reply && replyDiagnosticReason==='contract_malformed' ? {copy_probe_candidate:{
          root_id:rootId,reply_id:selectedRows[0].getAttribute('data-message-id')
        }} : {}),
    full_envelope_matched:Boolean(governedMode && governed && rootContext &&
      JSON.stringify(rootContext) === expectedContext),
    ...(rootFailure?{root_failure:rootFailure}:{}),
    ...(terminalSelection && reply ? {terminal_selection:'root_linked_terminal_reply'} : {})};
}
async function readDotFeed(session, message, markdown) {
  const expected = String(message);
  const known = session.dotFeedBinding?.expected === expected ? session.dotFeedBinding.root_id : '';
  let result = await session.evaluate(`(${dotFeedSnapshotDOM.toString()})(${JSON.stringify(expected)},${typeof markdown === 'function' ? markdown.toString() : 'null'},${JSON.stringify(known)})`, 10000);
  if (result?.present && result.root_id) session.dotFeedBinding = {expected, root_id: result.root_id};
  if (result?.terminal_selection === 'root_linked_terminal_reply' && result.complete) {
    const prior = session.dotFeedTerminal, now = Date.now();
    const unchanged = prior && prior.expected === expected && prior.root_id === result.root_id &&
      prior.reply_id === result.reply_id && prior.response === result.response;
    if (!unchanged) session.dotFeedTerminal = {expected, root_id:result.root_id,
      reply_id:result.reply_id, response:result.response, since:now};
    if (!unchanged || now - prior.since < 900) return {...result, complete:false, terminal_pending:true,
      reply_diagnostic_reason:'terminal_unstable'};
  } else {
    delete session.dotFeedTerminal;
  }
  if(result?.reply_diagnostic_reason==='contract_malformed' && result.copy_probe_candidate){
    const {probeOriginalCopyCapability}=require('./original_dot_copy_probe.cjs');
    const probe=await probeOriginalCopyCapability(session,expected,known,result.copy_probe_candidate,dotFeedSnapshotDOM);
    const {copy_probe_candidate,...bounded}=result;
    result={...bounded,reply_diagnostic_reason:'native_copy_'+probe.status};
  }
  return result || {present: false};
}
module.exports = {dotFeedSnapshotDOM, readDotFeed};

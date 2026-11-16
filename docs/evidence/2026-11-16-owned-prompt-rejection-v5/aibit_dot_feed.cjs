'use strict';
// Fixed DOM reader for the owner-authenticated Dot message feed. It never sends.
// Keep helpers inside this function: readDotFeed serializes it into the DOM scope.
function dotFeedSnapshotDOM(expected, markdown, knownRootId) {
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
    // after readDotFeed verified this exact expected message in this session.
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
  let reply = null, contractCount = 0;
  for (const row of rows.slice(start + 1)) {
    // Even an empty/virtualized later user row ends this original's reply scope.
    if (row.classList.contains('self')) break;
    const body = row.querySelector('.message-body[data-message-id]') || row;
    const text = String(body.innerText || body.textContent || '');
    if (!text.trim()) continue;
    if (governedMode) {
      // Match Python str.splitlines(), including CR-only and Unicode separators.
      // Do not promote an indented first line by trimming the whole response.
      const contracts = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
        .filter(line => line.trimStart().startsWith('AGOV RESULT:'));
      contractCount += contracts.length;
      if (!governed || contracts.length !== 1 || !contracts[0].startsWith('AGOV RESULT:')) continue;
      const json = stripResultSpace(contracts[0].slice('AGOV RESULT:'.length));
      if (json.length > maxResultBytes || new TextEncoder().encode(json).length > maxResultBytes) continue;
      try {
        const result = JSON.parse(json), packet = governed.packet;
        if (!isObject(result) || result.schema_version !== 1 || result.invocation_id !== governed.invocation ||
            !['project_id', 'task_id', 'task_revision'].every(key => result[key] === packet[key]) ||
            result.work_packet_digest !== packet.content_sha256 || !isObject(result.actor) ||
            !['project_id', 'role', 'agent_id', 'erp_user'].every(key => result.actor[key] === packet.binding[key]) ||
            (result.run_id !== undefined && result.run_id !== run)) continue;
        // Return the original JSON line unchanged. Markdown rendering can escape
        // its bytes. The caller retains its existing stable-text completion wait.
        reply = {response: text, reply_id: row.getAttribute('data-message-id')};
      } catch (_) {}
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
  // Multiple result rows, including a malformed/foreign result followed by a
  // valid one, are ambiguous. A streaming update to the same row is still one.
  if (governedMode && contractCount !== 1) reply = null;
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
  return {present: true, root_id: rootId, run_id: run, ...(reply || {}), complete: Boolean(reply),
    ...(rootFailure?{root_failure:rootFailure}:{})};
}
async function readDotFeed(session, message, markdown) {
  const expected = String(message);
  const known = session.dotFeedBinding?.expected === expected ? session.dotFeedBinding.root_id : '';
  const result = await session.evaluate(`(${dotFeedSnapshotDOM.toString()})(${JSON.stringify(expected)},${typeof markdown === 'function' ? markdown.toString() : 'null'},${JSON.stringify(known)})`, 10000);
  if (result?.present && result.root_id) session.dotFeedBinding = {expected, root_id: result.root_id};
  return result || {present: false};
}
module.exports = {dotFeedSnapshotDOM, readDotFeed};

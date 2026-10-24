'use strict';
// Use the same project endpoints as ChatGPT's signed-in web client. The session
// and access token stay INSIDE the browser. Never confuse a Library Space
// (page_space_...) with a ChatGPT Project (g-p-...). No sidebar selectors needed.
const ID = /^g-p-[A-Za-z0-9_-]{5,190}$/;
const error = (message, notSubmitted = false, stage = 'verifying_project') =>
  Object.assign(new Error(message), { statusCode: 502, notSubmitted, creationStage: stage });

async function projectOperationInPage(input) {
  const result = (ok, extra = {}) => ({ ok, ...extra });
  let sent = false;
  if (location.origin !== 'https://chatgpt.com') return result(false, { notSubmitted: true, stage: 'authentication' });
  try {
    const response = await fetch('/api/auth/session', {
      credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(12000)
    });
    if (!response.ok) return result(false, { notSubmitted: true, stage: 'authentication', status: response.status });
    const auth = await response.json();
    if (!auth.user?.email || !auth.accessToken) return result(false, { notSubmitted: true, stage: 'authentication' });
    if (input.account) {
      const bytes = new TextEncoder().encode(String(auth.user.email).toLowerCase().trim());
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      if (fingerprint !== input.account) return result(false, { notSubmitted: true, stage: 'account_changed' });
    }
    const headers = { Authorization: 'Bearer ' + auth.accessToken, 'Content-Type': 'application/json' };
    const request = async (method, pathname, body) => {
      if (method !== 'GET') sent = true;
      const response = await fetch('/backend-api' + pathname, {
        method, credentials: 'same-origin', cache: 'no-store', headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(40000)
      });
      const payload = await response.json().catch(() => null);
      return { status: response.status, ok: response.ok, payload };
    };
    const identity = payload => {
      const gizmo = payload?.resource?.gizmo || payload?.gizmo;
      if (!gizmo || !/^g-p-[A-Za-z0-9_-]{5,190}$/.test(String(gizmo.id || '')) ||
          typeof gizmo.display?.name !== 'string' || gizmo.is_archived) return null;
      return { id: gizmo.id, chatgpt_project_id: gizmo.id, name: gizmo.display.name };
    };
    if (input.operation === 'create') {
      const created = await request('POST', '/projects', { name: input.name, instructions: input.instructions });
      if (!created.ok || created.payload?.error != null) {
        return result(false, { status: created.status, stage: 'submitting',
          notSubmitted: [400,401,403,404,405,422,429].includes(created.status) });
      }
      const project = identity(created.payload);
      if (!project || project.name !== input.name) return result(false, { notSubmitted: false, stage: 'creation_response', status: created.status });
      return result(true, { project, status: created.status });
    }
    if (!/^g-p-[A-Za-z0-9_-]{5,190}$/.test(String(input.projectId || ''))) {
      return result(false, { notSubmitted: true, stage: 'project_identity' });
    }
    const current = await request('GET', '/gizmos/' + encodeURIComponent(input.projectId));
    const project = identity(current.payload);
    if (!current.ok || !project || project.id !== input.projectId || (input.name && project.name !== input.name)) {
      return result(false, { notSubmitted: true, stage: 'project_identity', status: current.status });
    }
    const gizmo = current.payload.gizmo;
    if (input.operation === 'verify') {
      return result(true, { project, instructions_match: gizmo.instructions === input.instructions });
    }
    if (input.operation !== 'instructions') return result(false, { notSubmitted: true, stage: 'invalid_operation' });
    if (gizmo.instructions !== input.instructions) {
      // Preserve name, appearance and server-side memory defaults. Only the
      // requested instructions change; this is the web client's PATCH shape.
      const saved = await request('PATCH', '/projects/' + encodeURIComponent(input.projectId), {
        name: project.name, instructions: input.instructions,
        emoji: gizmo.display.emoji ?? null, theme: gizmo.display.theme ?? null
      });
      if (!saved.ok || saved.payload?.error != null) return result(false, { stage: 'instructions_save', status: saved.status, notSubmitted: false });
    }
    const verified = await request('GET', '/gizmos/' + encodeURIComponent(input.projectId));
    const verifiedProject = identity(verified.payload);
    return result(Boolean(verified.ok && verifiedProject?.id === input.projectId && verifiedProject?.name === project.name && verified.payload?.gizmo?.instructions === input.instructions), {
      project: verifiedProject, instructions_match: verified.payload?.gizmo?.instructions === input.instructions,
      stage: 'instructions_verify', status: verified.status
    });
  } catch (_) {
    return result(false, { notSubmitted: !sent, stage: sent ? 'response_unavailable' : 'authentication' });
  }
}

async function operation(session, input) {
  let response;
  try { response = await session.call('Runtime.evaluate', {
    expression: `(${projectOperationInPage.toString()})(${JSON.stringify(input)})`,
    returnByValue: true, awaitPromise: true, userGesture: true
  }, 75000); } catch (_) { throw error('ChatGPT browser response timed out; submission status is uncertain.'); }
  if (response.exceptionDetails || !response.result?.value || typeof response.result.value !== 'object') {
    throw error('ChatGPT project response could not be verified. Sync before retrying.');
  }
  return response.result.value;
}
function assertResult(result) {
  if (result.ok !== true) {
    const suffix = Number.isInteger(result.status) ? ' HTTP ' + result.status : '';
    const stage = /^[a-z_]{1,40}$/.test(result.stage || '') ? result.stage : 'verifying_project';
    throw error(`ChatGPT project request failed at ${stage}${suffix}.` +
      (result.notSubmitted === true ? ' No project was submitted; retry this request.' : ' Retry will not submit a duplicate.'), result.notSubmitted === true, stage);
  }
}
async function createProjectInBrowser(session, name, options = {}) {
  if (typeof name !== 'string' || !name.trim() || name.length > 120) throw error('Invalid project name.', true, 'validation');
  const instructions = options.instructions || '';
  await options.beforeSubmit?.();
  const response = await operation(session, { operation: 'create', name, instructions, account: options.account });
  assertResult(response);
  const project = response.project;
  if (!ID.test(project?.id) || project?.name !== name) throw error('ChatGPT returned an invalid created project.');
  // Save the returned stable ID immediately, before read-back can fail.
  await options.onCreated?.(project);
  const verified = await operation(session, { operation: 'verify', projectId: project.id, name, instructions, account: options.account });
  assertResult(verified);
  return project;
}
async function saveProjectInstructionsInBrowser(session, input) {
  const result = await operation(session, { operation: 'instructions', projectId: input.project_id,
    name: input.project_name, instructions: String(input.instructions || ''), account: input.account_id });
  assertResult(result);
  return { success: true, instructions_saved: result.instructions_match === true,
    verified: result.instructions_match === true, project: result.project };
}
module.exports = { createProjectInBrowser, saveProjectInstructionsInBrowser, projectOperationInPage };

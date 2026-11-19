(() => {
  'use strict';

  const uuid = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i;
  const params = new URLSearchParams(location.search);
  const agentId = params.get('agent')?.toLowerCase();
  const runId = params.get('run')?.toLowerCase();
  const find = (id) => document.getElementById(id);
  const status = find('status');
  const retry = find('refresh');
  const signIn = find('sign-in');
  const record = find('record');
  const labels = new Map([
    ['running', 'Running'], ['queued', 'Queued'], ['pending', 'Queued'],
    ['success', 'Run finished'], ['error', 'Failed'], ['failed', 'Failed'],
    ['blocked', 'Blocked'], ['deferred', 'Waiting'], ['cancelled', 'Stopped'],
    ['canceled', 'Stopped'],
  ]);

  if (!uuid.test(agentId || '') || !uuid.test(runId || '')) {
    status.textContent = 'This agent run link is invalid.';
    return;
  }

  const back = find('back');
  back.href = '/automations/' + agentId;
  back.textContent = 'Back to agent';
  signIn.href = '/auth?redirect=' + encodeURIComponent(location.pathname + location.search);

  async function findRun() {
    const headers = {};
    const token = localStorage.getItem('token');
    if (token) headers.Authorization = 'Bearer ' + token;
    const options = { credentials: 'same-origin', cache: 'no-store', headers };
    const limit = 50;
    let previousLastId = '';
    for (let skip = 0; ; skip += limit) {
      const response = await fetch(`/api/v1/automations/${agentId}/runs?skip=${skip}&limit=${limit}`, options);
      if (!response.ok) {
        return { error: response.status };
      }
      const runs = await response.json();
      if (!Array.isArray(runs)) throw new Error('Invalid run history');
      const run = runs.find((entry) => entry.id === runId && entry.automation_id === agentId);
      if (run) return { run };
      if (runs.length < limit) return {};
      const lastId = runs[runs.length - 1]?.id;
      if (!lastId || lastId === previousLastId) throw new Error('Run history did not advance');
      previousLastId = lastId;
    }
  }

  function showRun(run) {
    if (typeof run.chat_id === 'string' && uuid.test(run.chat_id)) {
      status.textContent = 'Opening the saved conversation…';
      location.replace('/c/' + run.chat_id);
      return;
    }

    find('run-id').textContent = runId;
    find('state').textContent = labels.get(run.status) || run.status || 'Unknown';
    const timestamp = new Date(Number(run.created_at) / 1_000_000);
    const created = find('created');
    if (Number.isFinite(timestamp.getTime())) {
      created.dateTime = timestamp.toISOString();
      created.textContent = timestamp.toLocaleString();
    } else {
      created.removeAttribute('datetime');
      created.textContent = 'Unavailable';
    }

    const error = typeof run.error === 'string' ? run.error
      .replace(/(bearer\s+)[\w.~-]+/gi, '$1[redacted]')
      .replace(/((?:token|password|secret|api_key)\s*[:=]\s*)\S+/gi, '$1[redacted]') : '';
    find('error').textContent = error;
    find('diagnostics').hidden = !error;
    status.textContent = 'No chat has been saved for this run.';
    retry.textContent = 'Refresh run';
    retry.hidden = false;
    record.hidden = false;
  }

  async function refresh() {
    status.textContent = 'Loading this run…';
    signIn.hidden = true;
    retry.hidden = true;
    retry.textContent = 'Try again';
    record.hidden = true;
    try {
      const { run, error } = await findRun();
      if (run) {
        showRun(run);
      } else if (error === 401) {
        status.textContent = 'Sign in to view this run.';
        signIn.hidden = false;
      } else if (error === 403 || error === 404) {
        status.textContent = 'You do not have access to this run.';
      } else {
        status.textContent = error ? 'Run history is unavailable.' : 'This run is not in ABIT.AI history yet.';
        retry.hidden = false;
      }
    } catch {
      status.textContent = 'Could not load this run.';
      retry.hidden = false;
    }
  }

  retry.addEventListener('click', refresh);
  refresh();
})();

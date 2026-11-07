/* ABiT AI: Play on legacy agent-map cards. Native governance controls take precedence.
 * Uses the existing owner-scoped run endpoint; never enables or changes a schedule.
 */
(() => {
  'use strict';
  if (window.__abitCardPlay20261107) return;
  window.__abitCardPlay20261107 = true;

  const selector = '[data-testid="context-map"] article.agent-card';
  const idPattern = /^\/automations\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i;
  const pending = new Map();
  const running = new Set();
  let frame = false;
  let pollTimer = null;
  let noticeTimer = null;

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const identity = card => {
    const link = card.querySelector('a.agent-name');
    if (!link) return null;
    let url;
    try { url = new URL(link.getAttribute('href'), location.origin); } catch (_) { return null; }
    const match = url.origin === location.origin && !url.search && !url.hash && url.pathname.match(idPattern);
    return match ? { id: match[1], name: link.textContent.trim() || 'agent' } : null;
  };
  const notify = (message, error = false) => {
    let notice = document.getElementById('abit-card-play-notice');
    if (!notice) {
      notice = element('div', 'abit-card-play-notice');
      notice.id = 'abit-card-play-notice';
      notice.setAttribute('role', 'status');
      notice.setAttribute('aria-live', 'polite');
      document.body.appendChild(notice);
    }
    notice.textContent = message;
    notice.dataset.error = String(error);
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, error ? 12000 : 7000);
  };
  const jsonRequest = async (path, method = 'GET') => {
    let token;
    try { token = localStorage.getItem('token'); } catch (_) {}
    if (!token) throw new Error('Please sign in again to start this agent.');
    const response = await fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', authorization: 'Bearer ' + token }
    });
    if (!response.ok) {
      const messages = {
        401: 'Please sign in again to start this agent.',
        403: 'You do not have permission to start this agent.',
        404: 'This agent is no longer available.',
        409: 'This agent is already processing a request. Open its chat for progress.'
      };
      const error = new Error(messages[response.status] || 'The agent could not be started (' + response.status + ').');
      error.httpStatus = response.status;
      throw error;
    }
    if (!/\bapplication\/(?:[\w.+-]*\+)?json\b/i.test(response.headers.get('content-type') || '')) {
      throw new Error('The application returned an unexpected response. Check the agent chat before trying again.');
    }
    return response.json();
  };
  const readAgents = async () => {
    const result = await jsonRequest('/api/v1/automations/context-map');
    if (!Array.isArray(result.agents)) throw new Error('Agent status is unavailable. Please refresh and try again.');
    return result.agents;
  };
  const findAgent = (agents, id) => agents.find(agent => agent.id === 'automation:' + id && agent.kind === 'scheduled');
  const latestRun = async id => {
    const rows = await jsonRequest('/api/v1/automations/' + encodeURIComponent(id) + '/runs?limit=1');
    if (!Array.isArray(rows)) throw new Error('Run history is unavailable. Please refresh and try again.');
    return rows[0] || null;
  };
  const readChatTasks = async chatId => {
    const result = await jsonRequest('/api/tasks/chat/' + encodeURIComponent(chatId));
    if (!Array.isArray(result.task_ids)) throw new Error('Agent task status is unavailable.');
    return result.task_ids;
  };
  const chatIsComplete = async chatId => {
    const [tasks, chat] = await Promise.all([
      readChatTasks(chatId),
      jsonRequest('/api/v1/chats/' + encodeURIComponent(chatId))
    ]);
    const history = chat?.chat?.history;
    const current = history?.messages?.[history?.currentId];
    return tasks.length === 0 &&
      current?.role === 'assistant' && current.done === true;
  };

  const refresh = () => {
    frame = false;
    if (!/^\/automations\/?$/.test(location.pathname)) return;
    for (const card of document.querySelectorAll(selector)) {
      let button = card.querySelector('[data-abit-card-play]');
      if (card.querySelector('[data-testid="agent-play"]:not([data-abit-card-play])')) {
        if (button) button.remove();
        card.classList.remove('abit-card-has-play');
        continue;
      }
      const agent = identity(card);
      if (!agent) continue;
      if (!button) {
        button = element('button', 'abit-card-play');
        button.type = 'button';
        button.setAttribute('data-abit-card-play', 'true');
        button.setAttribute('data-testid', 'agent-play');
        button.addEventListener('click', event => {
          event.preventDefault();
          event.stopPropagation();
          const current = identity(card);
          if (current) void start(card, current);
        });
        card.appendChild(button);
        card.classList.add('abit-card-has-play');
      }
      const state = card.getAttribute('data-agent-status');
      const request = pending.get(agent.id);
      const isRunning = state === 'running' || running.has(agent.id);
      const knownState = ['idle', 'stale', 'disabled', 'running'].includes(state);
      const label = isRunning ? agent.name + ' is running'
        : request ? (request.accepted ? 'Start requested for ' : 'Starting ') + agent.name
        : knownState ? 'Play ' + agent.name : 'Status unavailable for ' + agent.name;
      const glyph = request ? (request.accepted ? '◷' : '…') : isRunning ? '◷' : '▶';
      if (button.textContent !== glyph) button.textContent = glyph;
      button.disabled = Boolean(request || isRunning || !knownState);
      if (button.getAttribute('aria-label') !== label) button.setAttribute('aria-label', label);
      button.title = request ? 'Start requested. Open the agent chat to follow progress.'
        : isRunning ? 'This agent is already running'
        : state === 'disabled' ? 'Run this agent once; its schedule stays disabled' : 'Run this agent now';
      button.setAttribute('aria-busy', String(Boolean(request && !request.accepted)));
    }
  };
  const queueRefresh = () => {
    if (!frame) { frame = true; requestAnimationFrame(refresh); }
  };
  const schedulePoll = () => {
    if (!pollTimer && pending.size) pollTimer = setTimeout(poll, 3000);
  };
  const poll = async () => {
    pollTimer = null;
    if (!pending.size) return;
    if (document.hidden || !/^\/automations\/?$/.test(location.pathname)) { schedulePoll(); return; }
    try {
      const agents = await readAgents();
      for (const [id, request] of pending) {
        if (!request.accepted) continue;
        const agent = findAgent(agents, id);
        if (!agent) { pending.delete(id); running.delete(id); continue; }
        if (agent.status === 'running') { running.add(id); continue; }
        else running.delete(id);
        // Manual runs do not update automation.last_run_at. Use an actual new run
        // record, then verify any background chat has really finished.
        try {
          if (request.existingChat) {
            if (await chatIsComplete(request.existingChat)) { pending.delete(id); running.delete(id); }
            else running.add(id);
            continue;
          }
          if (request.watchExisting) { pending.delete(id); running.delete(id); continue; }
          const run = await latestRun(id);
          if (!run || run.id === request.baselineRunId) continue;
          if (run.status === 'error') {
            pending.delete(id);
            running.delete(id);
            notify('The run for ' + request.name + ' failed. Open the agent activity for details.', true);
          } else if (run.status === 'success') {
            const chatId = run.chat_id;
            const normalChat = typeof chatId === 'string' && /^[0-9a-f-]{36}$/i.test(chatId);
            if (normalChat) {
              if (!(await chatIsComplete(chatId))) { running.add(id); continue; }
            } else if (chatId) {
              // Channel runs do not expose a Chat completion record. Keep the
              // accepted state instead of treating an empty task list as proof.
              continue;
            }
            pending.delete(id);
            running.delete(id);
          }
        } catch (_) { /* Do not clear a request while its outcome is unknown. */ }
      }
    } catch (_) { /* Keep the pending guard if status cannot be verified. */ }
    queueRefresh();
    schedulePoll();
  };
  const start = async (card, agent) => {
    if (pending.has(agent.id) || running.has(agent.id) || card.getAttribute('data-agent-status') === 'running') return;
    // Set this synchronously, before the first await, to prevent double clicks.
    const request = { accepted: false, baselineRunId: null, name: agent.name };
    pending.set(agent.id, request);
    refresh();
    let submitted = false;
    try {
      const current = findAgent(await readAgents(), agent.id);
      if (!current || !['idle', 'stale', 'disabled', 'running'].includes(current.status)) {
        throw new Error('Agent status is unavailable. Please refresh and try again.');
      }
      const chatMatch = typeof current.chat_href === 'string' && current.chat_href.match(/^\/c\/([0-9a-f-]{36})$/i);
      const chatId = chatMatch ? chatMatch[1] : null;
      const hasTasks = chatId ? (await readChatTasks(chatId)).length > 0 : false;
      if (current.status === 'running' || hasTasks) {
        request.accepted = true;
        request.existingChat = chatId;
        request.watchExisting = true;
        running.add(agent.id);
        notify(agent.name + ' is already running.');
        return;
      }
      request.baselineRunId = (await latestRun(agent.id))?.id || null;
      submitted = true;
      const response = await jsonRequest('/api/v1/automations/' + encodeURIComponent(agent.id) + '/run', 'POST');
      if (!response || response.id !== agent.id) throw new Error('Unexpected start response.');
      request.accepted = true;
      notify('Start requested for ' + agent.name + '. Open its chat to follow progress.');
    } catch (error) {
      // A transport failure after POST is ambiguous; do not invite duplicate submission.
      if (submitted && (!error.httpStatus || error.httpStatus >= 500 || error.httpStatus === 408)) {
        request.accepted = true;
        notify('The start request could not be confirmed. Check the agent chat before trying again.', true);
      } else {
        pending.delete(agent.id);
        notify(error.message || 'Unable to start this agent.', true);
      }
    } finally {
      refresh();
      schedulePoll();
    }
  };

  const boot = () => {
    const style = element('style');
    style.id = 'abit-card-play-style';
    style.textContent = `
      ${selector}.abit-card-has-play .agent-name { padding-inline-end: 42px; }
      ${selector} .abit-card-play { position:absolute;inset-inline-end:12px;top:34px;z-index:2;
        display:flex;align-items:center;justify-content:center;width:32px;height:32px;padding:0;
        border:1px solid #cbd5e1;border-radius:9px;background:#fff;color:#15803d;
        font-family:inherit;font-size:15px;line-height:1;cursor:pointer;touch-action:manipulation; }
      .dark ${selector} .abit-card-play { background:#152a24;border-color:#36564b;color:#86efac; }
      ${selector} .abit-card-play:hover:not(:disabled) { background:#dcfce7;border-color:#16a34a; }
      .dark ${selector} .abit-card-play:hover:not(:disabled) { background:#214a39;border-color:#4ade80; }
      ${selector} .abit-card-play:focus-visible { outline:2px solid #38bdf8;outline-offset:3px; }
      ${selector} .abit-card-play:disabled { cursor:default;color:#94a3b8;background:transparent;border-color:#64748b; }
      #abit-card-play-notice { position:fixed;z-index:1000;bottom:20px;inset-inline-end:20px;
        max-width:min(360px,calc(100vw - 40px));padding:12px 16px;border:1px solid #cbd5e1;
        border-radius:12px;background:#fff;color:#0f172a;box-shadow:0 8px 30px #0002;
        font:500 13px/1.5 system-ui,sans-serif;overflow-wrap:anywhere; }
      .dark #abit-card-play-notice { background:#20252e;color:#f1f5f9;border-color:#475569; }
      #abit-card-play-notice[data-error="true"] { border-color:#ef4444; }
      #abit-card-play-notice[hidden] { display:none; }
      @media(pointer:coarse) {
        ${selector}.abit-card-has-play .agent-name { padding-inline-end:54px; }
        ${selector} .abit-card-play { width:44px;height:44px;top:30px; }
      }
    `;
    document.head.appendChild(style);
    new MutationObserver(queueRefresh).observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['data-agent-status']
    });
    window.addEventListener('popstate', queueRefresh);
    refresh();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();

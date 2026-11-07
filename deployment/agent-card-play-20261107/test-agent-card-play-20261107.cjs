'use strict';
// Isolated behavior tests: minimal DOM, virtual timers and mocked fetch only.
// This file never opens a browser, contacts the network or starts a real agent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const sourcePath = require('node:path').join(__dirname, 'agent-card-play-20261107.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
const CHAT = '33333333-3333-4333-8333-333333333333';
const OLD_RUN = '44444444-4444-4444-8444-444444444444';
const NEW_RUN = '55555555-5555-4555-8555-555555555555';
const CARD_SELECTOR = '[data-testid="context-map"] article.agent-card';

function simpleMatches(element, expression) {
  let expr = expression.trim();
  const nots = [...expr.matchAll(/:not\(([^)]+)\)/g)].map(x => x[1]);
  if (nots.some(excluded => simpleMatches(element, excluded))) return false;
  expr = expr.replace(/:not\([^)]+\)/g, '');
  const attributes = [...expr.matchAll(/\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g)];
  for (const [, name, value] of attributes) {
    if (!element.hasAttribute(name)) return false;
    if (value !== undefined && element.getAttribute(name) !== value) return false;
  }
  expr = expr.replace(/\[[^\]]+\]/g, '');
  for (const [, cls] of expr.matchAll(/\.([\w-]+)/g)) {
    if (!element.classList.contains(cls)) return false;
  }
  for (const [, id] of expr.matchAll(/#([\w-]+)/g)) if (element.id !== id) return false;
  const tag = expr.match(/^[\w-]+/);
  return !tag || element.tagName.toLowerCase() === tag[0].toLowerCase();
}

function matches(element, selector) {
  const parts = selector.trim().split(/\s+/);
  if (!simpleMatches(element, parts.pop())) return false;
  let parent = element.parentNode;
  while (parts.length) {
    const part = parts.pop();
    while (parent && !simpleMatches(parent, part)) parent = parent.parentNode;
    if (!parent) return false;
    parent = parent.parentNode;
  }
  return true;
}

function environment(options = {}) {
  let time = 0, nextTimer = 1;
  const timers = new Map(), frames = [], observers = [], mutations = new Set();
  const calls = [], events = new Map();
  let fetchHandler = options.fetch || (() => { throw new Error('Unexpected mocked request'); });
  const contains = (root, node) => {
    for (let current = node; current; current = current.parentNode) if (current === root) return true;
    return false;
  };
  const mutation = (target, type, attributeName) => {
    for (const observer of observers) {
      if (!observer.target || !contains(observer.target, target)) continue;
      if (type === 'childList' && observer.options.childList) mutations.add(observer);
      if (type === 'attributes' && observer.options.attributes &&
          (!observer.options.attributeFilter || observer.options.attributeFilter.includes(attributeName))) mutations.add(observer);
    }
  };
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.attributes = new Map(); this.children = [];
      this.parentNode = null; this.listeners = new Map(); this._text = '';
      this.disabled = false; this.hidden = false; this.dataset = {};
      this.classList = {
        add: (...classes) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...classes])].join(' '); },
        remove: (...classes) => { this.className = this.className.split(/\s+/).filter(x => x && !classes.includes(x)).join(' '); },
        contains: cls => this.className.split(/\s+/).includes(cls)
      };
    }
    get className() { return this.getAttribute('class') || ''; }
    set className(value) { this.setAttribute('class', value); }
    get id() { return this.getAttribute('id') || ''; }
    set id(value) { this.setAttribute('id', value); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) {
      this.children.forEach(child => { child.parentNode = null; });
      this.children = []; this._text = String(value); mutation(this, 'childList');
    }
    get isConnected() { return contains(document.body, this) || contains(document.head, this); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); mutation(this, 'attributes', name); }
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    hasAttribute(name) { return this.attributes.has(name); }
    removeAttribute(name) { this.attributes.delete(name); mutation(this, 'attributes', name); }
    appendChild(child) {
      if (child.parentNode) child.remove();
      this.children.push(child); child.parentNode = this; mutation(this, 'childList'); return child;
    }
    append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
    remove() {
      if (!this.parentNode) return;
      const parent = this.parentNode;
      parent.children = parent.children.filter(child => child !== this);
      this.parentNode = null; mutation(parent, 'childList');
    }
    replaceChildren(...children) {
      this.children.forEach(child => { child.parentNode = null; });
      this.children = []; this._text = '';
      children.forEach(child => this.appendChild(child)); mutation(this, 'childList');
    }
    addEventListener(name, listener) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(listener);
    }
    dispatchClick() {
      const event = { target: this, preventDefault() {}, stopPropagation() {} };
      for (const callback of this.listeners.get('click') || []) callback(event);
    }
    click() { if (!this.disabled) this.dispatchClick(); }
    querySelectorAll(selector) {
      const found = [];
      const visit = parent => {
        for (const child of parent.children) {
          if (matches(child, selector)) found.push(child);
          visit(child);
        }
      };
      visit(this); return found;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const document = {
    readyState: 'complete',
    createElement: tag => new Element(tag),
    addEventListener() {},
    querySelectorAll(selector) {
      return [...this.head.querySelectorAll(selector), ...this.body.querySelectorAll(selector)];
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    getElementById(id) { return this.querySelector('#' + id); }
  };
  document.head = new Element('head'); document.body = new Element('body');
  const map = new Element('section'); map.setAttribute('data-testid', 'context-map');
  document.body.appendChild(map);
  function card(id = A, status = 'idle', name = 'First agent', href = '/automations/' + id) {
    const article = new Element('article'); article.className = 'agent-card';
    article.setAttribute('data-agent-status', status);
    const link = new Element('a'); link.className = 'agent-name'; link.setAttribute('href', href); link.textContent = name;
    article.appendChild(link); map.appendChild(article); return article;
  }
  const window = {
    addEventListener(name, callback) { if (!events.has(name)) events.set(name, []); events.get(name).push(callback); }
  };
  const location = { origin: 'https://abit.ai', pathname: options.pathname || '/automations' };
  const globals = {
    window, document, location, URL, Map, Set, Error, console,
    localStorage: { getItem: () => options.token === undefined ? 'unit-test-only-token' : options.token },
    fetch: async (path, init) => { calls.push({ path, init }); return fetchHandler(path, init); },
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    setTimeout: (callback, delay) => { const id = nextTimer++; timers.set(id, { callback, at: time + delay }); return id; },
    clearTimeout: id => { timers.delete(id); },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe(target, opts) { this.target = target; this.options = opts; }
      disconnect() { this.target = null; }
    }
  };
  const context = vm.createContext(globals);
  function load() { vm.runInContext(source, context, { filename: sourcePath }); }
  async function drain() {
    for (let pass = 0; pass < 50; pass++) {
      await new Promise(resolve => setImmediate(resolve));
      const dirty = [...mutations]; mutations.clear();
      dirty.forEach(observer => observer.callback([]));
      const queued = frames.splice(0); queued.forEach(callback => callback(time));
      if (!frames.length && !mutations.size && pass > 4) return;
    }
    throw new Error('DOM enhancement did not settle; possible observer loop');
  }
  async function advance(milliseconds) {
    const end = time + milliseconds;
    for (;;) {
      const due = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, timer] = due; timers.delete(id); time = timer.at;
      await timer.callback(); await drain();
    }
    time = end; await drain();
  }
  return {
    document, map, card, load, drain, advance, calls, location, context,
    handler(fn) { fetchHandler = fn; },
    newElement: tag => new Element(tag),
    button(article) { return article.querySelector('[data-abit-card-play]'); },
    notice() { return document.getElementById('abit-card-play-notice'); },
    popstate() { for (const callback of events.get('popstate') || []) callback(); }
  };
}

function response(body, status = 200, type = 'application/json') {
  return { ok: status >= 200 && status < 300, status,
    headers: { get: name => name.toLowerCase() === 'content-type' ? type : null },
    json: async () => body };
}
function agent(id = A, status = 'idle', last = 100) {
  return { id: 'automation:' + id, kind: 'scheduled', status, last_activity: last };
}
function standardHandler(state = {}) {
  return (path, init) => {
    if (path === '/api/v1/automations/context-map') return response({ agents: state.agents || [agent(A), agent(B)] });
    if (/\/runs(?:\?|$)/.test(path)) return response(state.runs || [{ id: OLD_RUN, status: 'success', chat_id: CHAT, created_at: 100000000000 }]);
    if (/^\/api\/tasks\/chat\//.test(path)) return response(state.tasks || { task_ids: [] });
    if (/^\/api\/v1\/chats\//.test(path)) return response(state.chat || { chat: { history: { currentId: 'answer', messages: { answer: { id: 'answer', role: 'assistant', done: true } } } } });
    const run = path.match(/^\/api\/v1\/automations\/([^/]+)\/run$/);
    if (run && init.method === 'POST') return response({ id: run[1] });
    throw new Error('Unexpected mocked endpoint ' + path);
  };
}
const posts = env => env.calls.filter(call => call.init.method === 'POST');

test('mounts one accessible Play per scheduled card without starting agents', async () => {
  const env = environment(); const first = env.card(A); const second = env.card(B, 'disabled', 'Second agent');
  env.load(); await env.drain(); env.load(); await env.drain();
  assert.equal(first.querySelectorAll('[data-abit-card-play]').length, 1);
  assert.equal(second.querySelectorAll('[data-abit-card-play]').length, 1);
  assert.equal(env.document.querySelectorAll('#abit-card-play-style').length, 1);
  assert.equal(env.button(first).type, 'button');
  assert.equal(env.button(first).getAttribute('aria-label'), 'Play First agent');
  assert.equal(env.button(first).disabled, false);
  assert.match(env.button(second).title, /schedule stays disabled/);
  assert.equal(env.calls.length, 0);
});

test('correct UUID, same-origin auth and rapid double click cause exactly one POST and no toggle', async () => {
  const env = environment({ fetch: standardHandler() }); env.card(A); const second = env.card(B, 'idle', 'Target agent');
  env.load(); const button = env.button(second);
  button.dispatchClick(); button.dispatchClick(); await env.drain();
  assert.equal(posts(env).length, 1); assert.equal(posts(env)[0].path, '/api/v1/automations/' + B + '/run');
  assert.equal(posts(env)[0].init.credentials, 'same-origin');
  assert.equal(posts(env)[0].init.headers.authorization, 'Bearer unit-test-only-token');
  assert.ok(env.calls.every(call => call.path.startsWith('/api/')));
  assert.ok(env.calls.every(call => !call.path.includes('/toggle')));
  assert.equal(button.disabled, true); assert.match(env.notice().textContent, /Start requested for Target agent/);
  assert.match(button.getAttribute('aria-label'), /requested/i);
});

test('fresh running preflight prevents a run despite stale idle card', async () => {
  const env = environment({ fetch: standardHandler({ agents: [agent(A, 'running')] }) }); const card = env.card(A);
  env.load(); env.button(card).click(); await env.drain();
  assert.equal(posts(env).length, 0); assert.equal(env.button(card).disabled, true);
  assert.match(env.notice().textContent, /already running/);
});

test('running or unknown DOM status starts no request and remains disabled', async () => {
  const env = environment(); const running = env.card(A, 'running'); const unknown = env.card(B, 'unavailable');
  env.load(); env.button(running).click(); env.button(unknown).click(); await env.drain();
  assert.equal(env.calls.length, 0); assert.equal(env.button(running).disabled, true); assert.equal(env.button(unknown).disabled, true);
});

test('preflight permission rejection shows safe text, sends no POST and resets button', async () => {
  const env = environment({ fetch: () => response({ detail: '<script>secret server payload</script>' }, 403) }); const card = env.card();
  env.load(); env.button(card).click(); await env.drain();
  assert.equal(posts(env).length, 0); assert.equal(env.button(card).disabled, false);
  assert.match(env.notice().textContent, /permission/); assert.doesNotMatch(env.notice().textContent, /script|secret/);
});

test('POST permission rejection resets the pending lock without exposing response details', async () => {
  const normal = standardHandler();
  const env = environment({ fetch: (path, init) => init.method === 'POST' ? response({ detail: 'private backend text' }, 403) : normal(path, init) });
  const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  assert.equal(posts(env).length, 1); assert.equal(env.button(card).disabled, false);
  assert.match(env.notice().textContent, /permission/); assert.doesNotMatch(env.notice().textContent, /private/);
});

test('ambiguous POST transport failure keeps guard and reports uncertainty', async () => {
  const normal = standardHandler();
  const env = environment({ fetch: (path, init) => { if (init.method === 'POST') throw new Error('Network down'); return normal(path, init); } });
  const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  env.button(card).dispatchClick(); await env.drain();
  assert.equal(posts(env).length, 1); assert.equal(env.button(card).disabled, true);
  assert.match(env.notice().textContent, /could not be confirmed/);
});

test('ambiguous HTTP 500 response keeps guard against duplicate starts', async () => {
  const normal = standardHandler();
  const env = environment({ fetch: (path, init) => init.method === 'POST' ? response({ detail: 'internal text' }, 500) : normal(path, init) });
  const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  env.button(card).dispatchClick(); await env.drain();
  assert.equal(posts(env).length, 1); assert.equal(env.button(card).disabled, true);
});

test('native Play already present is preserved and receives no additional control', async () => {
  const env = environment(); const card = env.card(); const native = env.newElement('button');
  native.setAttribute('data-testid', 'agent-play'); native.textContent = 'Native play'; card.appendChild(native);
  env.load(); await env.drain();
  assert.equal(env.button(card), null); assert.equal(card.querySelectorAll('[data-testid="agent-play"]').length, 1);
  assert.equal(native.textContent, 'Native play'); assert.equal(env.calls.length, 0);
});

test('native controls arriving later replace additive control without duplicates', async () => {
  const env = environment(); const card = env.card(); env.load(); await env.drain();
  assert.ok(env.button(card)); const native = env.newElement('button'); native.setAttribute('data-testid', 'agent-play'); card.appendChild(native);
  await env.drain();
  assert.equal(env.button(card), null); assert.equal(card.querySelectorAll('[data-testid="agent-play"]').length, 1);
  assert.equal(card.classList.contains('abit-card-has-play'), false);
});

test('filter/remount recreates one guarded button for the same pending agent', async () => {
  const env = environment({ fetch: standardHandler() }); let card = env.card(); env.load(); env.button(card).click(); await env.drain();
  card.remove(); card = env.card(A, 'idle', 'Renamed agent'); await env.drain();
  assert.equal(card.querySelectorAll('[data-abit-card-play]').length, 1); assert.equal(env.button(card).disabled, true);
  assert.match(env.button(card).getAttribute('aria-label'), /Renamed agent/);
  env.button(card).dispatchClick(); await env.drain(); assert.equal(posts(env).length, 1);
});

test('malformed and foreign hrefs are ignored without preventing valid later cards', async () => {
  const env = environment();
  const foreign = env.card(A, 'idle', 'Foreign', 'https://example.invalid/automations/' + A);
  const malformed = env.card(A, 'idle', 'Malformed', 'https://[');
  const query = env.card(A, 'idle', 'Query', '/automations/' + A + '?token=do-not-use');
  const wrong = env.card(A, 'idle', 'Not an ID', '/automations/not-an-id');
  const valid = env.card(B, 'idle', 'Valid later');
  env.load(); await env.drain();
  for (const card of [foreign, malformed, query, wrong]) assert.equal(env.button(card), null);
  assert.ok(env.button(valid)); assert.equal(env.calls.length, 0);
});

test('status changes update disabled state without duplicated handlers or observer loops', async () => {
  const env = environment(); const card = env.card(); env.load(); await env.drain();
  card.setAttribute('data-agent-status', 'running'); await env.drain(); assert.equal(env.button(card).disabled, true);
  card.setAttribute('data-agent-status', 'idle'); await env.drain(); assert.equal(env.button(card).disabled, false);
  assert.equal(env.button(card).listeners.get('click').length, 1); assert.equal(env.calls.length, 0);
});

test('SPA return mounts newly rendered cards, while other routes receive no enhancement', async () => {
  const env = environment({ pathname: '/c/' + CHAT }); const card = env.card(); env.load(); await env.drain(); assert.equal(env.button(card), null);
  env.location.pathname = '/automations'; env.popstate(); await env.drain(); assert.ok(env.button(card));
  assert.equal(env.calls.length, 0);
});

test('missing authentication never sends a network request', async () => {
  const env = environment({ token: null }); const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  assert.equal(env.calls.length, 0); assert.equal(env.button(card).disabled, false); assert.match(env.notice().textContent, /sign in/);
});

test('activity timestamp alone never releases an accepted request without a new run', async () => {
  const state = { agents: [agent()] }; const env = environment({ fetch: standardHandler(state) }); const card = env.card();
  env.load(); env.button(card).click(); await env.drain();
  state.agents = [agent(A, 'idle', 999)]; await env.advance(3000);
  assert.equal(env.button(card).disabled, true); assert.equal(posts(env).length, 1);
});

test('new successful run stays locked through active tasks and unfinished assistant, then unlocks on verified completion', async () => {
  const state = {}; const env = environment({ fetch: standardHandler(state) }); const card = env.card();
  env.load(); env.button(card).click(); await env.drain();
  state.runs = [{ id: NEW_RUN, status: 'success', chat_id: CHAT, created_at: 200000000000 }];
  state.tasks = { task_ids: ['background-task-1'] };
  state.chat = { chat: { history: { currentId: 'answer', messages: { answer: { role: 'assistant', done: false } } } } };
  await env.advance(3000);
  assert.equal(env.button(card).disabled, true); assert.match(env.button(card).getAttribute('aria-label'), /is running/);
  state.tasks = { task_ids: [] }; await env.advance(3000); assert.equal(env.button(card).disabled, true);
  state.chat.chat.history.messages.answer.done = true; await env.advance(3000);
  assert.equal(env.button(card).disabled, false); assert.equal(env.button(card).getAttribute('aria-label'), 'Play First agent');
  assert.equal(posts(env).length, 1);
  assert.ok(env.calls.some(call => call.path === '/api/tasks/chat/' + CHAT));
  assert.ok(env.calls.some(call => call.path === '/api/v1/chats/' + CHAT));
});

test('new failed run releases button with a safe failure notice', async () => {
  const state = {}; const env = environment({ fetch: standardHandler(state) }); const card = env.card();
  env.load(); env.button(card).click(); await env.drain();
  state.runs = [{ id: NEW_RUN, status: 'error', error: '<script>private exception content</script>' }];
  await env.advance(3000);
  assert.equal(env.button(card).disabled, false); assert.match(env.notice().textContent, /run for First agent failed/);
  assert.doesNotMatch(env.notice().textContent, /script|private exception/); assert.equal(posts(env).length, 1);
});

test('unavailable reconciliation leaves accepted request guarded', async () => {
  const env = environment({ fetch: standardHandler() }); const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  env.handler(() => response({ detail: 'unavailable' }, 503));
  await env.advance(3000); env.button(card).dispatchClick(); await env.drain();
  assert.equal(env.button(card).disabled, true); assert.equal(posts(env).length, 1);
});

test('disabled schedule with active chat tasks cannot be double-started', async () => {
  const state = { agents: [{ ...agent(A, 'disabled'), chat_href: '/c/' + CHAT }], tasks: { task_ids: ['task-1'] } };
  const env = environment({ fetch: standardHandler(state) }); const card = env.card(A, 'disabled');
  env.load(); env.button(card).click(); await env.drain();
  assert.equal(posts(env).length, 0); assert.equal(env.button(card).disabled, true); assert.match(env.notice().textContent, /already running/);
  state.tasks = { task_ids: [] }; await env.advance(3000);
  assert.equal(env.button(card).disabled, false); assert.equal(posts(env).length, 0);
  assert.match(env.button(card).title, /schedule stays disabled/);
});

test('unavailable run history aborts before POST', async () => {
  const normal = standardHandler(); const env = environment({ fetch: (path, init) => path.includes('/runs?') ? response({ detail: 'bad schema' }) : normal(path, init) });
  const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  assert.equal(posts(env).length, 0); assert.equal(env.button(card).disabled, false); assert.match(env.notice().textContent, /Run history is unavailable/);
});

test('unverifiable channel completion remains accepted instead of guessing success', async () => {
  const state = {}; const env = environment({ fetch: standardHandler(state) }); const card = env.card();
  env.load(); env.button(card).click(); await env.drain(); state.runs = [{ id: NEW_RUN, status: 'success', chat_id: 'channel:private-id' }];
  await env.advance(3000); assert.equal(env.button(card).disabled, true); assert.equal(posts(env).length, 1);
  assert.ok(env.calls.every(call => !call.path.startsWith('/api/v1/chats/channel')));
});

test('successful response with non-JSON body is treated as ambiguous, with no retry POST', async () => {
  const normal = standardHandler(); const env = environment({ fetch: (path, init) => init.method === 'POST' ? response('web page', 200, 'text/html') : normal(path, init) });
  const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  env.button(card).dispatchClick(); await env.drain();
  assert.equal(env.button(card).disabled, true); assert.equal(posts(env).length, 1); assert.match(env.notice().textContent, /could not be confirmed/);
});

test('missing or user-only current message does not count as completed', async () => {
  const state = {}; const env = environment({ fetch: standardHandler(state) }); const card = env.card();
  env.load(); env.button(card).click(); await env.drain();
  state.runs = [{ id: NEW_RUN, status: 'success', chat_id: CHAT }];
  state.chat = { chat: { history: { currentId: 'question', messages: { question: { role: 'user', done: true } } } } };
  await env.advance(3000); assert.equal(env.button(card).disabled, true);
  state.chat = { chat: {} }; await env.advance(3000); assert.equal(env.button(card).disabled, true);
});

test('pending polling pauses in hidden tabs and other routes, then resumes on the visible map', async () => {
  const env = environment({ fetch: standardHandler() }); const card = env.card(); env.load(); env.button(card).click(); await env.drain();
  const before = env.calls.length;
  env.document.hidden = true; await env.advance(3000); assert.equal(env.calls.length, before);
  env.document.hidden = false; env.location.pathname = '/c/' + CHAT; await env.advance(3000); assert.equal(env.calls.length, before);
  env.location.pathname = '/automations'; await env.advance(3000); assert.ok(env.calls.length > before);
  assert.equal(env.button(card).disabled, true); assert.equal(posts(env).length, 1);
});

// Exporting the harness allows focused reconciliation tests to be appended without
// changing the implementation or substituting a real DOM/network environment.
module.exports = { environment, response, agent, standardHandler, posts, A, B, CHAT, OLD_RUN, NEW_RUN };

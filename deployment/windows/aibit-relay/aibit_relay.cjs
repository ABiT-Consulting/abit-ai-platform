'use strict';

const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const { existingChrome } = require('./existing_chrome_cdp.cjs');
const USE_EXISTING_CHROME = Boolean(process.env.CHATGPT_EXISTING_CHROME_USER_DATA_DIR);
const { withSharedBrowserLock } = require('./shared_chatgpt_browser_lock.cjs');

const HOST = process.env.ABIT_ERP_CHATGPT_RELAY_HOST || '127.0.0.1';
const PORT = Number(process.env.ABIT_ERP_CHATGPT_RELAY_PORT || 8794);
const CDP_URL = (process.env.ABIT_ERP_CHATGPT_CDP_URL || 'http://127.0.0.1:9226').replace(/\/$/, '');
const RELAY_TOKEN = String(process.env.ABIT_ERP_CHATGPT_RELAY_TOKEN || '').trim();
const EXTRA_RELAY_TOKEN_FILE = String(process.env.ABIT_ERP_CHATGPT_EXTRA_TOKEN_FILE || '').trim();
const SECONDARY_LISTEN_PORT = Number(process.env.BIGRRO_SECONDARY_LISTEN_PORT || 0);
const RELAY_TOKENS = [RELAY_TOKEN];
if (EXTRA_RELAY_TOKEN_FILE) {
  try {
    const extra = String(fs.readFileSync(EXTRA_RELAY_TOKEN_FILE, 'utf8') || '').trim();
    if (extra && !RELAY_TOKENS.includes(extra)) RELAY_TOKENS.push(extra);
  } catch (_) {}
}
const REQUEST_LIMIT = Number(process.env.ABIT_ERP_CHATGPT_RELAY_BODY_LIMIT || 4 * 1024 * 1024);
const RESPONSE_TIMEOUT_MS = Number(process.env.ABIT_ERP_CHATGPT_TIMEOUT_MS || 180000);
const HERMES_PROXY_URL = (process.env.ABIT_HERMES_PROXY_URL || 'http://127.0.0.1:8645').replace(/\/$/, '');
const HERMES_MODEL = String(process.env.ABIT_HERMES_MODEL || 'nous/welcome').trim() || 'nous/welcome';
const HERMES_TIMEOUT_MS = Number(process.env.ABIT_HERMES_TIMEOUT_MS || 45000);
const PREFIX = '/chatgpt-relay';
const DEDICATED_WINDOW_NAME = String(process.env.ABIT_ERP_CHATGPT_WINDOW_NAME || 'ABIT_ERP_CHATGPT_RELAY').trim();
const DEDICATED_TARGET_FILE = process.env.ABIT_ERP_CHATGPT_TARGET_FILE || 'C:\\ProgramData\\ABiT\\BIGRRORelay\\target-id.txt';

const RATE_LIMIT_STATE_FILE = process.env.ABIT_ERP_CHATGPT_RATE_LIMIT_STATE_FILE || 'C:\\ProgramData\\ABiT\\BIGRRORelay\\rate-limit-state.json';
const SECONDARY_RELAY_URL = String(process.env.BIGRRO_SECONDARY_RELAY_URL || '').trim().replace(/\/$/, '');
const SECONDARY_RELAY_TOKEN_FILE = String(process.env.BIGRRO_SECONDARY_RELAY_TOKEN_FILE || '').trim();
const SECONDARY_RELAY_TIMEOUT_MS = Number(process.env.BIGRRO_SECONDARY_RELAY_TIMEOUT_MS || 180000);
const RATE_LIMIT_BACKOFF_MS = [15, 30, 60, 120].map((minutes) => minutes * 60 * 1000);
let rateLimitState = { failures: 0, until: 0, lastDetectedAt: 0 };
try {
  const stored = JSON.parse(fs.readFileSync(RATE_LIMIT_STATE_FILE, 'utf8'));
  if (stored && typeof stored === 'object') rateLimitState = {
    failures: Math.max(0, Number(stored.failures || 0)),
    until: Math.max(0, Number(stored.until || 0)),
    lastDetectedAt: Math.max(0, Number(stored.lastDetectedAt || 0)),
  };
} catch (_) {}
function persistRateLimitState() {
  try {
    fs.mkdirSync(require('path').dirname(RATE_LIMIT_STATE_FILE), { recursive: true });
    fs.writeFileSync(RATE_LIMIT_STATE_FILE, JSON.stringify(rateLimitState), { encoding: 'utf8' });
  } catch (_) {}
}
function rateLimitCircuitOpen() { return Number(rateLimitState.until || 0) > Date.now(); }
function rateLimitRetryAfterSeconds() { return Math.max(0, Math.ceil((Number(rateLimitState.until || 0) - Date.now()) / 1000)); }
function markChatGptRateLimited() {
  const failures = Math.max(1, Number(rateLimitState.failures || 0) + 1);
  const index = Math.min(failures - 1, RATE_LIMIT_BACKOFF_MS.length - 1);
  const now = Date.now();
  rateLimitState = { failures, until: now + RATE_LIMIT_BACKOFF_MS[index], lastDetectedAt: now };
  persistRateLimitState();
  return rateLimitState;
}
function clearChatGptRateLimit() {
  if (!rateLimitState.failures && !rateLimitState.until) return;
  rateLimitState = { failures: 0, until: 0, lastDetectedAt: 0 };
  persistRateLimitState();
}
function chatGptRateLimitError() {
  const error = new Error('ChatGPT web session is in protective cooldown after a rate-limit response.');
  error.statusCode = 429;
  error.retryAfterSeconds = rateLimitRetryAfterSeconds();
  return error;
}

let queueTail = Promise.resolve();
let dedicatedTargetId = '';
try { dedicatedTargetId = String(fs.readFileSync(DEDICATED_TARGET_FILE, 'utf8') || '').trim(); } catch (_) {}

function persistDedicatedTargetId(value) {
  dedicatedTargetId = String(value || '').trim();
  try {
    fs.mkdirSync(require('path').dirname(DEDICATED_TARGET_FILE), { recursive: true });
    if (dedicatedTargetId) fs.writeFileSync(DEDICATED_TARGET_FILE, dedicatedTargetId, { encoding: 'utf8' });
    else if (fs.existsSync(DEDICATED_TARGET_FILE)) fs.unlinkSync(DEDICATED_TARGET_FILE);
  } catch (_) {}
}
const PROJECT_CACHE_FILE = process.env.ABIT_ERP_CHATGPT_PROJECT_CACHE || 'C:\\ProgramData\\ABiT\\BIGRRORelay\\projects.json';
let projectCatalogCache = { expiresAt: 0, projects: [] };
try {
  const stored = JSON.parse(fs.readFileSync(PROJECT_CACHE_FILE, 'utf8'));
  const projects = Array.isArray(stored && stored.projects) ? stored.projects.filter((row) => /^g-p-/.test(String(row && (row.chatgpt_project_id || row.id) || ''))) : [];
  if (projects.length) projectCatalogCache = { expiresAt: 0, projects };
} catch (_) {}

function persistProjectCatalog(projects) {
  try {
    if (!Array.isArray(projects) || !projects.length) return;
    fs.mkdirSync(require('path').dirname(PROJECT_CACHE_FILE), { recursive: true });
    fs.writeFileSync(PROJECT_CACHE_FILE, JSON.stringify({ updated_at: new Date().toISOString(), projects }), { encoding: 'utf8' });
  } catch (_) {}
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(body);
}

function requestPath(req) {
  const pathname = new URL(req.url || '/', 'http://127.0.0.1').pathname.replace(/\/+$/, '') || '/';
  if (pathname === PREFIX) return '/';
  if (pathname.startsWith(PREFIX + '/')) return pathname.slice(PREFIX.length) || '/';
  return pathname;
}

function tokenMatches(candidate) {
  const supplied = Buffer.from(String(candidate || ''), 'utf8');
  return RELAY_TOKENS.some((token) => {
    const expected = Buffer.from(String(token || ''), 'utf8');
    return Boolean(expected.length && supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected));
  });
}

function authorized(req) {
  const auth = String(req.headers.authorization || '');
  const bearer = auth.toLowerCase().startsWith('bearer ') ? auth.slice(7).trim() : '';
  const header = String(req.headers['x-relay-token'] || '').trim();
  return tokenMatches(bearer || header);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > REQUEST_LIMIT) {
        reject(Object.assign(new Error('Request body is too large.'), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch (error) {
        reject(Object.assign(new Error('Request body must be valid JSON.'), { statusCode: 400 }));
      }
    });
    req.on('error', reject);
  });
}

function boundedText(value, limit = 8000) {
  return String(value == null ? '' : value).replace(/\s+/g, ' ').trim().slice(0, limit);
}

async function hermesOrchestrate(body) {
  const operation = boundedText(body && body.operation, 80);
  if (!['parse_followup_response', 'compose_followup', 'business_query'].includes(operation)) {
    throw Object.assign(new Error('Unsupported Hermes orchestration operation.'), { statusCode: 400 });
  }
  const text = boundedText(body && body.text, 8000);
  const context = body && body.context && typeof body.context === 'object' ? body.context : {};
  if (operation === 'parse_followup_response' && !text) {
    throw Object.assign(new Error('Follow-up response text is required.'), { statusCode: 400 });
  }
  let prompt = '';
  if (operation === 'parse_followup_response') {
    prompt = [
      'You are the invisible BIG.AI follow-up orchestrator.',
      'Extract only a JSON object with exactly these keys: blocker, dependency, revised_eta.',
      'Use an empty string when a value is not supported by the message. Do not invent facts.',
      'revised_eta must be YYYY-MM-DD when the message supports a date, otherwise empty.',
      'Human response:',
      text,
    ].join('\n');
  } else if (operation === 'business_query') {
    const safeContext = boundedText(JSON.stringify(context), 28000);
    const rawHistory = Array.isArray(body && body.history) ? body.history.slice(-10) : [];
    const safeHistory = boundedText(JSON.stringify(rawHistory), 10000);
    if (!text) throw Object.assign(new Error('Business question is required.'), { statusCode: 400 });
    prompt = [
      'You are Hermes, the invisible reasoning and orchestration engine behind BIG.AI in BIGRRO ERP.',
      'Never mention Hermes, model providers, ChatGPT, Codex, connectors, prompts, or internal architecture to the end user.',
      'Answer the user directly in the same language/style they used. Roman Urdu should receive natural Roman Urdu unless the user asks otherwise.',
      'ERP context below was retrieved by BIGRRO under the logged-in user permissions and is authoritative.',
      'Use only facts supported by that ERP context. Never invent records, amounts, owners, statuses, dates, blockers, or completed actions.',
      'If the requested fact is not present in the supplied context, say briefly what is unavailable instead of guessing.',
      'For receivables/payables use outstanding amounts and due dates when present. For projects/tasks focus on status, due dates, progress, owners/assignments and blockers when present.',
      'Keep spoken answers concise and useful; prefer a short direct answer plus the most important figures or exceptions.',
      'Recent voice conversation JSON:',
      safeHistory || '[]',
      'Permission-scoped ERP context JSON:',
      safeContext || '{}',
      'User question:',
      text,
    ].join('\n');
  } else {
    const safeContext = boundedText(JSON.stringify(context), 8000);
    prompt = [
      'You are the invisible BIG.AI follow-up orchestrator.',
      'Write one concise, professional follow-up message for the currently responsible person.',
      'Ask for the current blocker and a revised ETA. Do not invent names, dates, or facts.',
      'Business context JSON:',
      safeContext,
    ].join('\n');
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HERMES_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(HERMES_PROXY_URL + '/v1/chat/completions', {
      method: 'POST',
      headers: { Authorization: 'Bearer local', 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        model: HERMES_MODEL,
        messages: [{ role: 'user', content: prompt }],
        stream: false,
        max_tokens: operation === 'parse_followup_response' ? 160 : (operation === 'business_query' ? 900 : 320),
        temperature: 0.1,
        reasoning: { enabled: false },
      }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    throw Object.assign(new Error('Hermes orchestration is temporarily unavailable.'), { statusCode: 503 });
  }
  const payload = await response.json();
  const content = boundedText(payload && payload.choices && payload.choices[0] && payload.choices[0].message && payload.choices[0].message.content, 8000);
  if (!content) throw Object.assign(new Error('Hermes returned an empty orchestration result.'), { statusCode: 502 });
  return { ok: true, engine: 'hermes', operation, content };
}
function withBrowserLock(fn) {
  const run = queueTail.then(
    () => withSharedBrowserLock(fn),
    () => withSharedBrowserLock(fn)
  );
  queueTail = run.catch(() => undefined);
  return run;
}

async function cdpTargets() {
  if (USE_EXISTING_CHROME) return existingChrome.targets();
  // Bounded read-only retry for transient browser HTTP stalls. Do not retry Send.
  let lastError;
  for (let attempt=0;attempt<2;attempt++) {
    try {
      const response = await fetch(`${CDP_URL}/json/list`, { signal: AbortSignal.timeout(7000) });
      if (!response.ok) throw new Error(`Chrome CDP returned HTTP ${response.status}.`);
      return await response.json();
    } catch(error) {
      lastError=error;
      if(attempt===0) await new Promise(resolve=>setTimeout(resolve,500));
    }
  }
  throw lastError;
}

function isChatGptPage(target) {
  return target && target.type === 'page' && /^https:\/\/chatgpt\.com\//i.test(String(target.url || ''));
}

async function targetUiState(target) {
  const session = await new CdpSession(target).open();
  try {
    return await session.evaluate(`(() => {
      const textOf = (node) => String((node && (node.innerText || node.textContent)) || '').replace(/\\s+/g, ' ').trim();
      const projects = Array.from(document.querySelectorAll('button,[role="button"]')).some((node) => /^projects?$/i.test(textOf(node)));
      const composer = Boolean(
        document.querySelector('#prompt-textarea') ||
        document.querySelector('[data-testid*="prompt"][contenteditable="true"]') ||
        document.querySelector('textarea') ||
        document.querySelector('[role="textbox"][contenteditable="true"]')
      );
      return {
        projects,
        composer,
        authenticated: !location.pathname.startsWith('/auth'),
        projectId: (location.pathname.match(/\\/g\\/(g-p-[^/]+)/i) || [])[1] || '',
      };
    })()`);
  } catch (_) {
    return { projects: false, composer: false, authenticated: false, projectId: '' };
  } finally {
    session.close();
  }
}

async function targetWindowName(target) {
  const session = await new CdpSession(target).open();
  try {
    return String(await session.evaluate('window.name') || '');
  } catch (_) {
    return '';
  } finally {
    session.close();
  }
}



async function ensureNormalChatMode(session) {
  const current = String(await session.location().catch(() => '') || '');
  // Keep ChatGPT Projects only when explicitly selected by the ERP request.
  // A plain relay request must never inherit a previous Work/project context.
  if (/^https:\/\/chatgpt\.com\/g\//i.test(current)) {
    const root = 'https://chatgpt.com/';
    await session.navigate(root);
    return root;
  }
  return current;
}
async function createDedicatedTarget() {
  let target;
  if (USE_EXISTING_CHROME) {
    target = await existingChrome.create();
  } else {
    const response = await fetch(`${CDP_URL}/json/new?${encodeURIComponent('https://chatgpt.com/')}`, { method: 'PUT', signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error('Chrome could not create the relay tab.');
    target = await response.json();
  }
  if (!target || !target.id || !target.webSocketDebuggerUrl) throw new Error('Chrome returned an invalid relay tab.');
  persistDedicatedTargetId(target.id);
  const session = await new CdpSession(target).open();
  try {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      if ((await session.location().catch(() => '')).startsWith('https://chatgpt.com/')) break;
      await sleep(250);
    }
    await session.evaluate(`window.name = ${JSON.stringify(DEDICATED_WINDOW_NAME)}; true`);
  } finally {
    session.close();
  }
  persistDedicatedTargetId(target.id);
  return target;
}

async function targetResponsive(target, timeoutMs = 4000) {
  if (!target || !target.webSocketDebuggerUrl) return false;
  const session = new CdpSession(target);
  try {
    await session.open(timeoutMs);
    const result = await session.call('Runtime.evaluate', {
      expression: 'location.href',
      returnByValue: true,
      awaitPromise: false,
    }, timeoutMs);
    const href = String(result && result.result && result.result.value || '');
    return href.startsWith('https://chatgpt.com');
  } catch (_) {
    return false;
  } finally {
    session.close();
  }
}

async function closeChromeTarget(targetId) {
  const id = String(targetId || '').trim();
  if (!id) return;
  if (USE_EXISTING_CHROME) {
    await existingChrome.call('Target.closeTarget', { targetId: id }).catch(() => {});
    return;
  }
  try {
    await fetch(`${CDP_URL}/json/close/${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(5000) });
  } catch (_) {}
}

let targetLookupPromise = null;
async function findChatGptTarget(preferredUrl = '') {
  // Health, project sync, and chat can arrive together. Only one may allocate a tab.
  if (targetLookupPromise) return targetLookupPromise;
  targetLookupPromise = findOrCreateRelayTarget();
  try { return await targetLookupPromise; } finally { targetLookupPromise = null; }
}
async function findOrCreateRelayTarget() {
  const targets = (await cdpTargets()).filter(isChatGptPage);
  if (dedicatedTargetId) {
    const existing = targets.find(row => row.id === dedicatedTargetId);
    // A busy/loading tab is still our tab. Never replace it because a short
    // responsiveness probe failed or ChatGPT cleared window.name on navigation.
    if (existing) return existing;
    persistDedicatedTargetId('');
  }
  for (const target of targets) {
    const name = await targetWindowName(target).catch(() => '');
    if (name !== DEDICATED_WINDOW_NAME) continue;
    persistDedicatedTargetId(target.id);
    return target;
  }
  return createDedicatedTarget();
}

class CdpSession {
  constructor(target) {
    this.target = target;
    this.ws = null;
    this.seq = 0;
    this.pending = new Map();
    this.handlers = new Map();
  }

  async open(timeoutMs = 15000) {
    if (this.target.browserTransport) {
      this.browserSessionId = await existingChrome.attach(this.target.id, message => {
        for (const handler of this.handlers.get(message.method) || []) {
          try { handler(message.params || {}); } catch (_) {}
        }
      });
      try {
        await this.call('Page.enable', {}, timeoutMs);
        // Chrome can freeze a background relay tab while its browser transport
        // remains healthy. Resume the page before waiting for its JS runtime.
        await this.call('Page.setWebLifecycleState', { state: 'active' }, timeoutMs).catch(() => {});
        await this.call('Runtime.enable', {}, timeoutMs);
        return this;
      } catch (error) { this.close(); throw error; }
    }
    this.ws = new WebSocket(this.target.webSocketDebuggerUrl);
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.method && this.handlers.has(message.method)) {
        for (const handler of Array.from(this.handlers.get(message.method))) {
          try { handler(message.params || {}); } catch (_) {}
        }
      }
      if (!message.id || !this.pending.has(message.id)) return;
      const entry = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) entry.reject(new Error(message.error.message || 'CDP command failed.'));
      else entry.resolve(message.result || {});
    };
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error('Unable to connect to the ChatGPT Chrome CDP page.'));
    });
    await this.call('Page.enable', {}, timeoutMs);
    await this.call('Page.setWebLifecycleState', { state: 'active' }, timeoutMs).catch(() => {});
    await this.call('Runtime.enable', {}, timeoutMs);
    return this;
  }

  on(method, handler) {
    if (!this.handlers.has(method)) this.handlers.set(method, new Set());
    this.handlers.get(method).add(handler);
    return () => {
      const set = this.handlers.get(method);
      if (!set) return;
      set.delete(handler);
      if (!set.size) this.handlers.delete(method);
    };
  }

  close() {
    if (this.browserSessionId) { existingChrome.detach(this.browserSessionId); this.browserSessionId = null; return; }
    try { this.ws && this.ws.close(); } catch (_) {}
    this.ws = null;
  }

  call(method, params = {}, timeoutMs = 15000) {
    if (this.browserSessionId) return existingChrome.call(method, params, this.browserSessionId, timeoutMs);
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP ${method} timed out.`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.call('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: true,
    });
    if (result.exceptionDetails) throw new Error('ChatGPT browser evaluation failed.');
    return result.result ? result.result.value : undefined;
  }

  async location() {
    return this.evaluate('location.href');
  }

  async navigate(url) {
    const expected = new URL(String(url));
    const expectedProjectId = parseProjectId(expected.href);
    const expectedConversationId = parseConversationId(expected.href);
    const existing = await this.location().catch(() => '');
    if (existing) {
      try {
        const current = new URL(existing);
        if (current.origin === expected.origin && current.pathname.replace(/\/$/, '') === expected.pathname.replace(/\/$/, '') && current.search === expected.search) {
          return existing;
        }
      } catch (_) {}
    }
    // Prefer the project's own link from an existing conversation. A hard
    // navigation reloads the entire ChatGPT app and can stall on Loading profile.
    let clickedProjectLink = false;
    if (expectedProjectId && !expectedConversationId && expected.pathname.endsWith('/project')) {
      clickedProjectLink = await this.evaluate(`(() => {
        const wanted = ${JSON.stringify(expectedProjectId)};
        const link = Array.from(document.querySelectorAll('a[href]')).find((node) => {
          try {
            const href = new URL(node.href);
            return href.origin === 'https://chatgpt.com' && href.pathname.endsWith('/project') &&
              (href.pathname === '/g/' + wanted + '/project' || href.pathname.startsWith('/g/' + wanted + '-'));
          } catch (_) { return false; }
        });
        if (!link) return false;
        link.click();
        return true;
      })()`).catch(() => false);
    }
    if (!clickedProjectLink) {
      try {
        await this.call('Page.navigate', { url: expected.href });
      } catch (error) {
        // CDP command acknowledgement can time out after the browser has
        // already accepted the navigation. Observe the target URL below
        // instead of retrying Page.navigate or discarding a valid transition.
        // Do NOT swallow unrelated browser errors.
        if (!/CDP Page\.navigate timed out/i.test(String(error && error.message || error))) {
          throw error;
        }
      }
    }
    const deadline = Date.now() + 25000;
    let last = '';
    while (Date.now() < deadline) {
      await sleep(300);
      const current = await this.location().catch(() => '');
      if (!current || current === 'about:blank') continue;
      last = current;
      if (/auth\.openai\.com|chatgpt\.com\/(?:auth\/login|login)(?:[/?#]|$)/i.test(current)) {
        throw Object.assign(new Error('ChatGPT login required. Sign in once in the dedicated relay browser.'), { statusCode: 401 });
      }
      let currentUrl;
      try { currentUrl = new URL(current); } catch (_) { continue; }
      const exactPath = currentUrl.hostname === expected.hostname && currentUrl.pathname.replace(/\/$/, '') === expected.pathname.replace(/\/$/, '');
      const sameProject = Boolean(expectedProjectId && parseProjectId(current) === expectedProjectId);
      const sameConversation = !expectedConversationId || parseConversationId(current) === expectedConversationId;
      const sameTarget = exactPath || (currentUrl.hostname === expected.hostname && sameProject && sameConversation);
      if (!sameTarget) continue;
      const ready = await this.evaluate('document.readyState').catch(() => 'loading');
      if (ready === 'interactive' || ready === 'complete') return current;
    }
    throw new Error(`ChatGPT navigation timed out${last ? ' before target page became ready' : ''}.`);
  }
}

function cleanText(value, maxLength = 240) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, maxLength);
}

function parseProjectId(urlValue) {
  try {
    const url = new URL(String(urlValue || ''));
    if (url.hostname !== 'chatgpt.com') return '';
    const match = url.pathname.match(/\/g\/(g-p-[0-9a-f]{32})(?:[-/]|$)/i);
    return match ? match[1] : '';
  } catch (_) {
    return '';
  }
}

function parseConversationId(urlValue) {
  try {
    const url = new URL(String(urlValue || ''));
    if (url.hostname !== 'chatgpt.com') return '';
    const match = url.pathname.match(/\/c\/([^/?#]+)/i);
    return match ? match[1] : '';
  } catch (_) {
    return '';
  }
}

function opaqueProjectKey(name) {
  return 'name-' + crypto.createHash('sha256').update(String(name || ''), 'utf8').digest('hex').slice(0, 20);
}


function collectSidebarProjects(data) {
  const rows = [];
  const seenObjects = new WeakSet();
  const seenIds = new Set();
  const walk = (value) => {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (typeof value !== 'object') return;
    if (seenObjects.has(value)) return;
    seenObjects.add(value);
    const candidates = [];
    if (value.gizmo && typeof value.gizmo === 'object') candidates.push(value.gizmo);
    candidates.push(value);
    for (const candidate of candidates) {
      const id = cleanText(candidate && candidate.id, 200);
      const name = cleanText(candidate && candidate.display && candidate.display.name, 180);
      const archived = Boolean(candidate && candidate.is_archived);
      if (/^g-p-[A-Za-z0-9_-]+$/.test(id) && name && !archived && !seenIds.has(id)) {
        seenIds.add(id);
        rows.push({ id, name, chatgpt_project_id: id });
      }
    }
    Object.values(value).forEach(walk);
  };
  walk(data);
  rows.sort((a, b) => a.name.localeCompare(b.name));
  return rows;
}

function cachedProjectByName(name) {
  const wanted = cleanText(name, 180).toLocaleLowerCase();
  if (!wanted) return null;
  return (projectCatalogCache.projects || []).find((row) => String(row.name || '').toLocaleLowerCase() === wanted) || null;
}

async function captureSidebarProjects(target) {
  const session = await new CdpSession(target).open();
  let offResponse = () => {};
  let offFinished = () => {};
  try {
    await session.call('Network.enable');
    let sidebarRequestId = '';
    let settled = false;
    let resolveBody;
    let rejectBody;
    const bodyPromise = new Promise((resolve, reject) => {
      resolveBody = resolve;
      rejectBody = reject;
    });
    offResponse = session.on('Network.responseReceived', (params) => {
      try {
        const url = new URL(String(params && params.response && params.response.url || ''));
        if (url.pathname === '/backend-api/gizmos/snorlax/sidebar' && Number(params.response.status) === 200) {
          sidebarRequestId = String(params.requestId || '');
        }
      } catch (_) {}
    });
    offFinished = session.on('Network.loadingFinished', (params) => {
      if (settled || !sidebarRequestId || String(params.requestId || '') !== sidebarRequestId) return;
      settled = true;
      session.call('Network.getResponseBody', { requestId: sidebarRequestId }, 12000)
        .then((result) => resolveBody(String(result && result.body || '')))
        .catch(rejectBody);
    });
    await session.call('Page.reload', { ignoreCache: false }, 20000);
    const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('Timed out reading ChatGPT project catalog.')), 18000));
    const body = await Promise.race([bodyPromise, timeoutPromise]);
    const projects = collectSidebarProjects(JSON.parse(body));
    if (!projects.length) throw new Error('ChatGPT project catalog did not contain any projects.');
    projectCatalogCache = { expiresAt: Date.now() + 60000, projects };
    persistProjectCatalog(projects);
    return projects;
  } finally {
    offResponse();
    offFinished();
    session.close();
  }
}

async function projectCatalog(force = false) {
  if (!force && projectCatalogCache.expiresAt > Date.now() && projectCatalogCache.projects.length) {
    return projectCatalogCache.projects.map((row) => ({ ...row }));
  }
  const targets = (await cdpTargets()).filter(isChatGptPage);
  if (!targets.length) {
    if (projectCatalogCache.projects.length) return projectCatalogCache.projects.map((row) => ({ ...row }));
    throw new Error('No logged-in ChatGPT browser page is available on Dev-02.');
  }
  const ordered = [];
  const seen = new Set();
  const add = (row) => { if (row && !seen.has(row.id)) { seen.add(row.id); ordered.push(row); } };
  targets.filter((row) => String(row.url || '').replace(/\/$/, '') === 'https://chatgpt.com').forEach(add);
  targets.filter((row) => parseProjectId(row.url) && !parseConversationId(row.url)).forEach(add);
  targets.filter((row) => parseProjectId(row.url)).forEach(add);
  targets.forEach(add);
  let lastError = null;
  for (const target of ordered.slice(0, 6)) {
    try {
      const projects = await captureSidebarProjects(target);
      if (projects.length) return projects;
    } catch (error) {
      lastError = error;
    }
  }
  if (projectCatalogCache.projects.length) {
    return projectCatalogCache.projects.map((row) => ({ ...row }));
  }
  throw lastError || new Error('Unable to read ChatGPT project catalog.');
}

const PROJECT_ROWS_EXPRESSION = `(() => {
  const textOf = (node) => String((node && (node.innerText || node.textContent)) || '').replace(/\\s+/g, ' ').trim();
  const projectButton = Array.from(document.querySelectorAll('button,[role="button"]'))
    .find((node) => /^projects?$/i.test(textOf(node)));
  if (!projectButton) return { found: false, expanded: false, projects: [] };
  if (projectButton.getAttribute('aria-expanded') !== 'true') projectButton.click();
  const parents = [projectButton.parentElement, projectButton.parentElement && projectButton.parentElement.parentElement].filter(Boolean);
  let list = null;
  for (const parent of parents) {
    const sibling = parent.nextElementSibling;
    if (sibling && (sibling.tagName === 'UL' || sibling.querySelectorAll('li').length)) { list = sibling; break; }
  }
  if (!list) {
    const nav = projectButton.closest('nav');
    if (nav) {
      const lists = Array.from(nav.querySelectorAll('ul')).map((node) => ({ node, score: node.querySelectorAll('button[aria-label="Open project home"]').length })).filter((row) => row.score > 0);
      const best = lists.sort((a, b) => b.score - a.score)[0];
      list = best ? best.node : null;
    }
  }
  if (!list) return { found: true, expanded: projectButton.getAttribute('aria-expanded') === 'true', projects: [] };
  const ignored = /^(projects?|new project|create project|add project|show more|show less|project options|more)$/i;
  const rows = Array.from(list.children || []);
  const projects = [];
  rows.forEach((row, rowIndex) => {
    const rawLines = String(row.innerText || row.textContent || '').split(/\\n+/).map((v) => v.replace(/\\s+/g, ' ').trim()).filter(Boolean);
    let name = rawLines.find((line) => !ignored.test(line) && line.length <= 180) || '';
    if (!name) {
      const control = Array.from(row.querySelectorAll('a,button,[role="button"],[tabindex]')).find((node) => {
        const label = String(node.getAttribute && (node.getAttribute('aria-label') || node.getAttribute('title')) || '').trim();
        return label && !ignored.test(label) && !/options|menu/i.test(label);
      });
      name = control ? String(control.getAttribute('aria-label') || control.getAttribute('title') || '').trim() : '';
    }
    if (!name || ignored.test(name)) return;
    projects.push({ rowIndex, name: name.slice(0, 180) });
  });
  const unique = [];
  const seen = new Set();
  projects.forEach((row) => {
    const key = row.name.toLocaleLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(row);
  });
  return { found: true, expanded: projectButton.getAttribute('aria-expanded') === 'true', projects: unique };
})()`;

async function projectRows(session) {
  let result = await session.evaluate(PROJECT_ROWS_EXPRESSION);
  if (!result || !result.found) throw new Error('ChatGPT Projects control is not available in the logged-in browser session.');
  if (!result.expanded || !(result.projects || []).length) {
    await sleep(1000);
    result = await session.evaluate(PROJECT_ROWS_EXPRESSION);
  }
  return Array.isArray(result && result.projects) ? result.projects : [];
}

async function listProjectsInternal(force = false) {
  // Read ChatGPT Projects ONLY in our own fourth browser tab. The legacy
  // ChatGPT "Projects" sidebar no longer exists in the current UI.
  // Authorization stays INSIDE the signed-in browser page, never in Node,
  // site settings, logs or tenant databases. Return metadata only.
  if (false && !force && projectCatalogCache.expiresAt > Date.now() && projectCatalogCache.projects.length) {
    return projectCatalogCache.projects.map(row => ({ ...row }));
  }
  const target = await findChatGptTarget();
  const session = await new CdpSession(target).open();
  try {
    const rows = await session.evaluate(`(async () => {
      const info = await fetch('/api/auth/session', {credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(12000)});
      if (!info.ok) throw Error('ChatGPT session unavailable');
      const auth = await info.json();
      if (!auth.user?.email || typeof auth.accessToken !== 'string' || !auth.accessToken) {
        throw Error('ChatGPT authentication required');
      }
      const projects = [], seenIds = new Set(), visited = new WeakSet();
      const scan = (value,depth=0) => {
        if (!value || typeof value !== 'object' || depth > 11 || projects.length >= 5000) return;
        if (visited.has(value)) return;
        visited.add(value);
        if (Array.isArray(value)) {
          for (const item of value.slice(0,350)) scan(item,depth+1);
          return;
        }
        const id = typeof value.id === 'string' ? value.id : '';
        const name = String(value.display?.name || '').trim();
        if (/^g-p-[A-Za-z0-9_-]+$/.test(id) && name && name.length <= 180 &&
            !value.is_archived && !seenIds.has(id)) {
          seenIds.add(id);
          projects.push({id,name,chatgpt_project_id:id});
        }
        for (const [key,child] of Object.entries(value)) {
          if (/token|cookie|secret|authorization/i.test(key)) continue;
          scan(child,depth+1);
        }
      };
      // ChatGPT paginates sidebar results. Default first page has only 5
      // items; the supported limit=50 still requires cursor iteration.
      const visitedCursors = new Set();
      let cursor = null;
      let complete = false;
      for (let page = 0; page < 100; page++) {
        const params = new URLSearchParams({limit:'50'});
        if (cursor !== null) params.set('cursor',cursor);
        const ctrl = new AbortController();
        const deadline = setTimeout(() => ctrl.abort(),20000);
        let response;
        try {
          response = await fetch('/backend-api/gizmos/snorlax/sidebar?' + params, {
            credentials:'same-origin',cache:'no-store',
            headers:{Authorization:'Bearer ' + auth.accessToken},
            signal:ctrl.signal
          });
        } finally { clearTimeout(deadline); }
        if (!response.ok) throw Error('ChatGPT Projects page ' + (page+1) + ' HTTP ' + response.status);
        const payload = await response.json();
        if (!payload || !Array.isArray(payload.items)) throw Error('Malformed ChatGPT Projects page');
        scan(payload.items);
        const next = payload.cursor;
        if (next == null || next === '') { complete = true; break; }
        if (typeof next !== 'string' || next === cursor || visitedCursors.has(next)) {
          throw Error('ChatGPT Projects pagination cursor invalid/repeated');
        }
        visitedCursors.add(next);
        cursor = next;
      }
      if (!complete) throw Error('ChatGPT Projects pagination exceeded safe page limit');
      return projects.sort((a,b)=>a.name.localeCompare(b.name));
    })()`);
    if (!Array.isArray(rows) || !rows.length) throw new Error('No active ChatGPT Projects were returned.');
    const projects = rows.filter(row => /^g-p-[A-Za-z0-9_-]+$/.test(String(row.id || '')) &&
      typeof row.name === 'string' && row.name.length && row.name.length <= 180)
      .map(row => ({id:row.id,name:row.name,chatgpt_project_id:row.id}));
    if (!projects.length) throw new Error('ChatGPT Projects metadata was not valid.');
    projectCatalogCache = { expiresAt: Date.now() + 60000, projects };
    persistProjectCatalog(projects);
    return projects.map(row => ({ ...row }));
  } finally {
    session.close();
  }
}

async function selectProject(session, projectName, projectId = '') {
  const requestedName = cleanText(projectName, 180);
  const requestedId = cleanText(projectId, 200);
  if (requestedId && /^g-p-[A-Za-z0-9_-]+$/.test(requestedId)) {
    const existing = await session.location().catch(() => '');
    if (parseProjectId(existing) === requestedId) {
      return { url: existing, project_id: requestedId, project_name: requestedName };
    }
    const url = `https://chatgpt.com/g/${encodeURIComponent(requestedId)}/project`;
    const current = await session.navigate(url);
    return { url: current, project_id: parseProjectId(current) || requestedId, project_name: requestedName };
  }
  if (!requestedName) throw new Error('A ChatGPT project must be selected.');
  const cached = cachedProjectByName(requestedName);
  if (cached && /^g-p-[A-Za-z0-9_-]+$/.test(String(cached.chatgpt_project_id || cached.id || ''))) {
    const resolvedId = String(cached.chatgpt_project_id || cached.id);
    const current = await session.navigate(`https://chatgpt.com/g/${encodeURIComponent(resolvedId)}/project`);
    return { url: current, project_id: parseProjectId(current) || resolvedId, project_name: requestedName };
  }
  const rows = await projectRows(session);
  if (!rows.some((row) => String(row.name || '').toLocaleLowerCase() === requestedName.toLocaleLowerCase())) {
    throw new Error(`ChatGPT project '${requestedName}' was not found.`);
  }

  const hit = await session.evaluate(`(() => {
    const wanted = ${JSON.stringify(requestedName)}.toLocaleLowerCase();
    const textOf = (node) => String((node && (node.innerText || node.textContent)) || '').replace(/\\s+/g, ' ').trim();
    const projectButton = Array.from(document.querySelectorAll('button,[role="button"]')).find((node) => /^projects?$/i.test(textOf(node)));
    if (!projectButton) return null;
    const parents = [projectButton.parentElement, projectButton.parentElement && projectButton.parentElement.parentElement].filter(Boolean);
    let list = null;
    for (const parent of parents) {
      const sibling = parent.nextElementSibling;
      if (sibling && (sibling.tagName === 'UL' || sibling.querySelectorAll('li').length)) { list = sibling; break; }
    }
    if (!list) {
      const nav = projectButton.closest('nav');
      const lists = nav ? Array.from(nav.querySelectorAll('ul')).map((node) => ({ node, score: node.querySelectorAll('button[aria-label="Open project home"]').length })).filter((row) => row.score > 0) : [];
      const best = lists.sort((a, b) => b.score - a.score)[0];
      list = best ? best.node : null;
    }
    if (!list) return null;
    for (const row of Array.from(list.children || [])) {
      const lines = String(row.innerText || row.textContent || '').split(/\\n+/).map((v) => v.replace(/\\s+/g, ' ').trim()).filter(Boolean);
      if (!lines.some((line) => line.toLocaleLowerCase() === wanted)) continue;
      const open = row.querySelector('button[aria-label="Open project home"]');
      const node = open || row.querySelector('[role="button"][tabindex="0"]') || row;
      try { node.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (_) {}
      const rect = node.getBoundingClientRect();
      if (rect.width <= 2 || rect.height <= 2) return null;
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }
    return null;
  })()`);  if (!hit || !Number.isFinite(hit.x) || !Number.isFinite(hit.y)) {
    throw new Error(`ChatGPT project '${requestedName}' could not be selected.`);
  }

  await session.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hit.x, y: hit.y });
  await session.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: hit.x, y: hit.y, button: 'left', buttons: 1, clickCount: 1 });
  await session.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: hit.x, y: hit.y, button: 'left', buttons: 0, clickCount: 1 });

  const deadline = Date.now() + 15000;
  let current = '';
  while (Date.now() < deadline) {
    await sleep(350);
    current = await session.location().catch(() => '');
    if (parseProjectId(current)) break;
  }
  const actualId = parseProjectId(current);
  if (!actualId) throw new Error(`ChatGPT did not open project '${requestedName}'.`);
  return { url: current, project_id: actualId, project_name: requestedName };
}

function projectRootUrl(urlValue) {
  const url = new URL(String(urlValue || ''));
  if (url.hostname !== 'chatgpt.com') throw new Error('Invalid ChatGPT project URL.');
  const projectId = parseProjectId(url.href);
  if (!projectId) throw new Error('The selected ChatGPT page is not inside a project.');
  const conversationIndex = url.pathname.indexOf('/c/');
  if (conversationIndex >= 0) url.pathname = url.pathname.slice(0, conversationIndex).replace(/\/$/, '');
  // A project's bare /g/... route can stall on "Loading profile". New turns
  // must open its project home, including when starting from a saved chat URL.
  url.pathname = url.pathname.replace(/\/$/, '');
  if (!url.pathname.endsWith('/project')) url.pathname += '/project';
  url.search = '';
  url.hash = '';
  return url.href;
}

async function openConversation(session, conversationUrl, expectedProjectId = '') {
  const url = new URL(String(conversationUrl || ''));
  if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com' || !parseConversationId(url.href)) {
    throw new Error('Stored ChatGPT conversation URL is invalid.');
  }
  const projectId = parseProjectId(url.href);
  if (expectedProjectId && /^g-p-/.test(expectedProjectId) && expectedProjectId !== projectId) {
    throw new Error('Stored ChatGPT conversation belongs to a different project.');
  }
  await session.navigate(url.href);
  return { url: await session.location(), project_id: projectId };
}

async function rateLimitOverlay(session) {
  return session.evaluate(`(() => {
    const visible = (node) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      const opacity = Number.parseFloat(style.opacity || '1');
      return style.display !== 'none' && style.visibility !== 'hidden' && opacity > 0.02 &&
        rect.width > 2 && rect.height > 2 && rect.bottom > 0 && rect.right > 0 &&
        rect.top < window.innerHeight && rect.left < window.innerWidth && node.getAttribute('aria-hidden') !== 'true';
    };
    const matchesRateLimit = (node) => {
      if (!visible(node)) return false;
      const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
      return /Too many requests/i.test(text) && /temporarily limited access to your conversations/i.test(text);
    };
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
    let overlay = dialogs.find(matchesRateLimit) || null;
    if (!overlay) {
      const nodes = Array.from(document.querySelectorAll('body *'));
      overlay = nodes.find((node) => {
        if (!matchesRateLimit(node)) return false;
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return (style.position === 'fixed' || style.position === 'absolute') && rect.width > 400 && rect.height > 200;
      }) || null;
    }
    if (!overlay) return { active: false, canDismiss: false };
    const gotIt = Array.from(overlay.querySelectorAll('button,[role="button"]')).find((node) =>
      visible(node) && /^got it$/i.test(String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim())
    );
    return { active: true, canDismiss: Boolean(gotIt) };
  })()`);
}

async function dismissKnownRateLimitOverlay(session) {
  const state = await rateLimitOverlay(session).catch(() => ({ active: false, canDismiss: false }));
  if (!state || !state.active) return false;
  const clicked = await session.evaluate(`(() => {
    const visible = (node) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      const opacity = Number.parseFloat(style.opacity || '1');
      return style.display !== 'none' && style.visibility !== 'hidden' && opacity > 0.02 &&
        rect.width > 2 && rect.height > 2 && rect.bottom > 0 && rect.right > 0 &&
        rect.top < window.innerHeight && rect.left < window.innerWidth && node.getAttribute('aria-hidden') !== 'true';
    };
    const matchesRateLimit = (node) => {
      if (!visible(node)) return false;
      const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
      return /Too many requests/i.test(text) && /temporarily limited access to your conversations/i.test(text);
    };
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
    let overlay = dialogs.find(matchesRateLimit) || null;
    if (!overlay) {
      const nodes = Array.from(document.querySelectorAll('body *'));
      overlay = nodes.find((node) => {
        if (!matchesRateLimit(node)) return false;
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return (style.position === 'fixed' || style.position === 'absolute') && rect.width > 400 && rect.height > 200;
      }) || null;
    }
    if (!overlay) return false;
    const button = Array.from(overlay.querySelectorAll('button,[role="button"]')).find((node) =>
      visible(node) && /^got it$/i.test(String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim())
    );
    if (!button) return false;
    button.click();
    return true;
  })()`);
  if (!clicked) return false;
  const deadline = Date.now() + 3500;
  while (Date.now() < deadline) {
    await sleep(250);
    const after = await rateLimitOverlay(session).catch(() => ({ active: false }));
    if (!after || !after.active) return true;
  }
  return false;
}

async function waitForComposer(session, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let last = { prompt: 0, textarea: 0, editable: 0, textbox: 0, pageKind: 'unknown' };
  while (Date.now() < deadline) {
    last = await session.evaluate(`(() => ({
      prompt: document.querySelectorAll('#prompt-textarea').length,
      textarea: document.querySelectorAll('textarea').length,
      editable: document.querySelectorAll('[contenteditable="true"]').length,
      textbox: document.querySelectorAll('[role="textbox"]').length,
      pageKind: location.pathname.includes('/c/') ? 'conversation' : (location.pathname.includes('/g/g-p-') ? 'project' : 'other'),
    }))()`).catch(() => last);
    if (last.prompt || last.textarea || last.editable || last.textbox) return last;
    await sleep(300);
  }
  throw new Error(`ChatGPT message composer did not become ready (page=${last.pageKind}, prompt=${last.prompt}, textarea=${last.textarea}, editable=${last.editable}, textbox=${last.textbox}).`);
}

async function composerSnapshot(session) {
  return session.evaluate(`(() => {
    const roleNodes = (role) => {
      const legacy = Array.from(document.querySelectorAll('[data-message-author-role="' + role + '"]'));
      if (legacy.length) return legacy;
      return Array.from(document.querySelectorAll('[data-content-search-unit-key$=":' + role + '"]'));
    };
    const assistants = roleNodes('assistant');
    const users = roleNodes('user');
    const assistant = assistants.length ? assistants[assistants.length - 1] : null;
    const user = users.length ? users[users.length - 1] : null;
    const messageKey = (node) => node ? String(node.getAttribute('data-message-id') ||
      node.getAttribute('data-chatgpt-search-message-ids') || node.getAttribute('data-content-search-unit-key') ||
      node.closest('[data-testid^="conversation-turn-"]')?.getAttribute('data-testid') || '') : '';
    const text = assistant ? String(assistant.innerText || assistant.textContent || '').trim() : '';
    let turn = assistant;
    const completionLabels = /^(?:Copy(?: response)?|Rate response|Good response|Bad response|Regenerate(?: response)?|Retry|More actions)$/i;
    while (turn && turn !== document.body) {
      const testid = String(turn.getAttribute && turn.getAttribute('data-testid') || '');
      const cls = String(turn.className || '');
      if (/^conversation-turn-/i.test(testid) || /(^|\\s)agent-turn(\\s|$)/.test(cls)) break;
      if (turn.querySelectorAll('[data-message-author-role="assistant"]').length > 1 ||
          turn.querySelectorAll('[data-content-search-unit-key$=":assistant"]').length > 1) { turn = null; break; }
      // New ChatGPT turns use semantic search-unit attributes, with actions in
      // a sibling below the message. Stop at that turn, never at the whole page.
      if (Array.from(turn.querySelectorAll('button')).some((node) =>
        completionLabels.test(String(node.getAttribute('aria-label') || node.getAttribute('title') || node.innerText || '').trim())
      )) break;
      turn = turn.parentElement;
    }
    if (turn === document.body) turn = null;
    const actionLabels = turn ? Array.from(turn.querySelectorAll('button')).map((node) =>
      String(node.getAttribute('aria-label') || node.getAttribute('title') || node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim()
    ).filter(Boolean) : [];
    const complete = actionLabels.some((label) => completionLabels.test(label));
    const stop = Boolean(document.querySelector('[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]'));
    return { count: assistants.length, users: users.length, lastUserKey: messageKey(user), lastAssistantKey: messageKey(assistant), last: text, complete, generating: stop };
  })()`);
}

async function stableComposerBaseline(session) {
  const currentUrl = await session.location().catch(() => '');
  const existingConversation = Boolean(parseConversationId(currentUrl));
  const deadline = Date.now() + (existingConversation ? 7000 : 2200);
  let latest = { count: 0, users: 0, last: '' };
  let previousSignature = '';
  let stableReads = 0;
  while (Date.now() < deadline) {
    await sleep(350);
    latest = await composerSnapshot(session).catch(() => latest);
    const signature = `${Number(latest.users || 0)}|${Number(latest.count || 0)}|${String(latest.last || '')}`;
    if (signature === previousSignature) stableReads += 1;
    else { previousSignature = signature; stableReads = 0; }
    const hasExistingTurn = Number(latest.users || 0) > 0 || Number(latest.count || 0) > 0;
    if (stableReads >= 2 && (!existingConversation || hasExistingTurn)) return latest;
  }
  return latest;
}

async function submissionState(session) {
  return session.evaluate(`(() => {
    const composer = document.querySelector('#prompt-textarea') ||
      document.querySelector('[data-testid*="prompt"][contenteditable="true"]') ||
      document.querySelector('textarea[placeholder*="Message"]') ||
      document.querySelector('[role="textbox"][contenteditable="true"]');
    const legacyUsers = document.querySelectorAll('[data-message-author-role="user"]');
    const userNodes = legacyUsers.length ? legacyUsers : document.querySelectorAll('[data-content-search-unit-key$=":user"]');
    const user = userNodes.length ? userNodes[userNodes.length - 1] : null;
    const lastUserKey = user ? String(user.getAttribute('data-message-id') || user.getAttribute('data-chatgpt-search-message-ids') ||
      user.getAttribute('data-content-search-unit-key') || user.closest('[data-testid^="conversation-turn-"]')?.getAttribute('data-testid') || '') : '';
    const text = composer ? String(('value' in composer ? composer.value : (composer.innerText || composer.textContent || '')) || '').trim() : '';
    return { users: userNodes.length, lastUserKey, text, url: location.href };
  })()`);
}

async function waitForSubmission(session, baseline, timeoutMs = 7000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(250);
    const limited = await rateLimitOverlay(session).catch(() => ({ active: false }));
    if (limited && limited.active) {
      markChatGptRateLimited();
      throw chatGptRateLimitError();
    }
    const state = await submissionState(session).catch(() => ({ users: 0, text: '', url: '' }));
    const newConversation = !parseConversationId(baseline.url || '') && Boolean(parseConversationId(state.url || ''));
    const newUserMessage = Boolean(state.lastUserKey && state.lastUserKey !== baseline.lastUserKey);
    if (newUserMessage || Number(state.users || 0) > Number(baseline.users || 0) || newConversation) return true;
  }
  return false;
}

async function submitPrompt(session, message, onProgress) {
  const safeMessage = String(message || '').trim();
  if (!safeMessage) throw new Error('Message is required.');
  const baseline = await stableComposerBaseline(session);
  baseline.url = await session.location().catch(() => '');
  const expression = `(() => {
    const message = ${JSON.stringify(safeMessage)};
    const composer = document.querySelector('#prompt-textarea') ||
      document.querySelector('[data-testid*="prompt"][contenteditable="true"]') ||
      document.querySelector('textarea[placeholder*="Message"]') ||
      document.querySelector('[role="textbox"][contenteditable="true"]');
    if (!composer) return { ok: false, reason: 'composer' };
    composer.focus();
    if ('value' in composer) {
      const proto = Object.getPrototypeOf(composer);
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) descriptor.set.call(composer, message); else composer.value = message;
      composer.dispatchEvent(new Event('input', { bubbles: true }));
      composer.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      composer.textContent = '';
      try { document.execCommand('insertText', false, message); } catch (_) { composer.textContent = message; }
      if (!String(composer.innerText || composer.textContent || '').trim()) composer.textContent = message;
      composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: message }));
    }
    const send = Array.from(document.querySelectorAll('[data-testid="send-button"],button[aria-label*="Send"],button[data-testid*="send"]')).find((button) => {
      const rect = button.getBoundingClientRect();
      const style = window.getComputedStyle(button);
      return !button.disabled && button.getAttribute('aria-disabled') !== 'true' && rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    }) || null;
    const overlays = Array.from(document.querySelectorAll('body *')).filter((node) => {
      const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return /Too many requests/i.test(text) && /temporarily limited access to your conversations/i.test(text) &&
        (style.position === 'fixed' || style.position === 'absolute') && rect.width > 400 && rect.height > 200;
    });
    overlays.forEach((overlay) => {
      const button = Array.from(overlay.querySelectorAll('button,[role="button"]')).find((node) => /^got it$/i.test(String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim()));
      if (button) button.click();
    });
    if (send && !send.disabled) {
      const rect = send.getBoundingClientRect();
      return { ok: true, submitted: false, send: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } };
    }
    return { ok: true, submitted: false, send: null };
  })()`;
  const filled = await session.evaluate(expression);
  if (!filled || !filled.ok) {
    const state = await waitForComposer(session, 1500).catch((error) => ({ error: String(error.message || error) }));
    if (state && state.error) throw new Error(state.error);
    throw new Error('ChatGPT message composer was not found after it became ready.');
  }
  await sleep(300);
  const formSubmitted = await session.evaluate(`(() => {
    const composer = document.querySelector('#prompt-textarea') ||
      document.querySelector('[data-testid*="prompt"][contenteditable="true"]') ||
      document.querySelector('textarea[placeholder*="Message"]') ||
      document.querySelector('[role="textbox"][contenteditable="true"]');
    const send = Array.from(document.querySelectorAll('[data-testid="send-button"],button[aria-label*="Send"],button[data-testid*="send"]')).find((button) => {
      const rect = button.getBoundingClientRect();
      const style = window.getComputedStyle(button);
      return !button.disabled && button.getAttribute('aria-disabled') !== 'true' && rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    }) || null;
    const overlays = Array.from(document.querySelectorAll('body *')).filter((node) => {
      const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return /Too many requests/i.test(text) && /temporarily limited access to your conversations/i.test(text) &&
        (style.position === 'fixed' || style.position === 'absolute') && rect.width > 400 && rect.height > 200;
    });
    overlays.forEach((overlay) => {
      const button = Array.from(overlay.querySelectorAll('button,[role="button"]')).find((node) => /^got it$/i.test(String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim()));
      if (button) button.click();
    });
    const form = composer && composer.closest('form');
    if (!composer || !send || send.disabled || !form || typeof form.requestSubmit !== 'function') return false;
    form.requestSubmit(send);
    return true;
  })()`).catch(() => false);
  filled.submitted = Boolean(formSubmitted);
  if (!filled.submitted) {
    if (filled.send && Number.isFinite(filled.send.x) && Number.isFinite(filled.send.y)) {
      await session.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: filled.send.x, y: filled.send.y });
      await session.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: filled.send.x, y: filled.send.y, button: 'left', buttons: 1, clickCount: 1 });
      await session.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: filled.send.x, y: filled.send.y, button: 'left', buttons: 0, clickCount: 1 });
    } else {
      await session.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
      await session.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    }
  }

  let submitted = await waitForSubmission(session, baseline, 7000);
  if (!submitted) {
    const current = await submissionState(session).catch(() => ({ text: '' }));
    // A cleared composer may already have submitted. Never refill and replay it
    // merely because virtualized message counts did not increase.
    // Never resend after an ambiguous submit; even a retained draft can coexist
    // with an accepted send. A later read-only reconciliation must decide.
    if (String(current.text || '').trim() === safeMessage) {
      throw Object.assign(new Error('Submission acknowledgement uncertain; no resend permitted.'), {statusCode:409});
    }
    submitted = await waitForSubmission(session, baseline, 9000);
  }
  if (!submitted) throw new Error('ChatGPT message submission did not start.');
  if (onProgress) await onProgress({state:'message_submitted'});
  if (onProgress) await onProgress({state:'waiting_for_response'});
  const start = Date.now();
  let lastText = '';
  let stableSince = 0;
  while (Date.now() - start < RESPONSE_TIMEOUT_MS) {
    await sleep(1000);
    const limited = await rateLimitOverlay(session).catch(() => ({ active: false }));
    if (limited && limited.active) {
      const error = new Error('ChatGPT web session is temporarily rate limited.');
      error.statusCode = 429;
      throw error;
    }
    const snapshot = await composerSnapshot(session).catch(() => ({ count: 0, last: '' }));
    const text = String(snapshot.last || '').trim();
    const isNew = Boolean(snapshot.lastAssistantKey && snapshot.lastAssistantKey !== baseline.lastAssistantKey) ||
      snapshot.count > Number(baseline.count || 0) || (text && text !== String(baseline.last || '').trim());
    if (!isNew || !text) continue;
    if (onProgress && text !== lastText) await onProgress({state:'receiving_response',response:text,conversation_url:await session.location().catch(()=>''),partial:true});
    if (text === lastText) {
      if (!stableSince) stableSince = Date.now();
      const stableFor = Date.now() - stableSince;
      if (snapshot.complete && !snapshot.generating && stableFor >= 900) return text;
      // Compatibility fallback for unexpected ChatGPT DOM changes: never accept
      // a short pause as completion. Require a long stable window instead.
      if (!snapshot.generating && stableFor >= 10000) return text;
    } else {
      lastText = text;
      stableSince = Date.now();
    }
  }
  if (lastText) return lastText;
  throw new Error('Timed out waiting for the ChatGPT response.');
}

async function ensureComposerWithRecovery(session, recoveryUrl) {
  try {
    return await waitForComposer(session, 18000);
  } catch (firstError) {
    if (recoveryUrl) {
      await session.navigate(recoveryUrl).catch(() => '');
    } else {
      await session.call('Page.reload', { ignoreCache: false }, 15000).catch(() => {});
    }
    await sleep(1000);
    return waitForComposer(session, 22000);
  }
}

async function relayMessage(body, onProgress) {
  if (rateLimitCircuitOpen()) throw chatGptRateLimitError();
  const message = String(body.message || body.prompt || '').trim();
  const projectName = cleanText(body.project_name || body.projectName, 180);
  const storedProjectId = cleanText(body.chatgpt_project_id || body.project_id || body.projectId, 200);
  const conversationUrl = String(body.conversation_url || body.conversationUrl || '').trim();
  if (!message) throw Object.assign(new Error('Message is required.'), { statusCode: 400 });


  const target = await findChatGptTarget(conversationUrl);
  const login = await browserLoginState(target);
  if (!login.authenticated) throw Object.assign(new Error('ChatGPT login required. Sign in once in the dedicated relay browser.'), { statusCode: 401 });
  const session = await new CdpSession(target).open();
  try {
    // Foreground only the owned relay tab while processing a real turn. Health
    // probes must not steal focus, but Chrome can suspend background navigation.
    await session.call('Page.bringToFront');
    await session.call('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    const limitedBeforeNavigation = await rateLimitOverlay(session).catch(() => ({ active: false }));
    if (limitedBeforeNavigation && limitedBeforeNavigation.active) {
      markChatGptRateLimited();
      throw chatGptRateLimitError();
    }
    let project;
    if (conversationUrl) {
      const opened = await openConversation(session, conversationUrl, storedProjectId);
      project = { url: opened.url, project_id: opened.project_id, project_name: projectName };
    } else if (projectName || storedProjectId) {
      project = await selectProject(session, projectName, /^g-p-/.test(storedProjectId) ? storedProjectId : '');
      // selectProject already opened the exact project. A second forced
      // navigation can stall during ChatGPT hydration before submitPrompt.
      // Verify identity, then wait for the composer without re-navigation.
      const openedUrl = await session.location();
      if (parseProjectId(openedUrl) !== storedProjectId) {
        throw new Error('ChatGPT selected a different project; no message submitted.');
      }
      // A new AIBIT chat must NOT reuse the previous conversation already
      // showing in our owned tab. Reset only when the tab is on a conversation;
      // when on project home, avoid the redundant navigation that caused stalls.
      if (parseConversationId(openedUrl)) {
        await session.navigate(projectRootUrl(openedUrl));
      }
    }
    if (!project) { await session.navigate('https://chatgpt.com/'); project = {url:'https://chatgpt.com/', project_id:'', project_name:''}; }
    const recoveryUrl = conversationUrl || (project.project_id ? projectRootUrl(project.url) : project.url);
    await ensureComposerWithRecovery(session, recoveryUrl);
    const inputAttachments = body.input_attachments || body.input_files || [];
    if (inputAttachments.length) await require('./relay_input_files.cjs').upload(session,inputAttachments);
    const response = await submitPrompt(session, message, onProgress);
    const rich = await captureAssistantRichResponse(session).catch(() => ({html:'',attachments:[]}));
    let currentUrl = await session.location();
    const deadline = Date.now() + 10000;
    while (!parseConversationId(currentUrl) && Date.now() < deadline) {
      await sleep(300);
      currentUrl = await session.location();
    }
    const projectId = parseProjectId(currentUrl) || project.project_id || storedProjectId;
    if ((storedProjectId || projectName) && !projectId) throw new Error('ChatGPT response did not remain inside the selected project.');
    clearChatGptRateLimit();
    return {
      success: true,
      response,
      response_html: rich.html || '',
      attachments: rich.attachments || [],
      conversation_title: await session.evaluate("document.title.replace(/\\s*[-–]\\s*ChatGPT$/, '').trim()").catch(()=>''),
      project: {
        id: projectId,
        name: projectName || project.project_name || '',
      },
      conversation_url: currentUrl,
      conversation_id: parseConversationId(currentUrl),
    };
  } finally {
    session.close();
  }
}

async function createProjectInternal(name) {
  const projectName = cleanText(name, 120);
  if (!projectName) throw Object.assign(new Error('Project name is required.'), { statusCode: 400 });
  const target = await findChatGptTarget();
  const session = await new CdpSession(target).open();
  try {
    await dismissKnownRateLimitOverlay(session).catch(() => false);
    const limitedAtStart = await rateLimitOverlay(session).catch(() => ({ active: false }));
    if (limitedAtStart && limitedAtStart.active) {
      const error = new Error('ChatGPT web session is temporarily rate limited.');
      error.statusCode = 429;
      throw error;
    }
    const clickNewProject = async () => {
      await projectRows(session);
      return session.evaluate(`(() => {
        const label = (node) => String(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').replace(/\\s+/g, ' ').trim();
        const candidates = Array.from(document.querySelectorAll('button,[role="button"],a'));
        const control = candidates.find((node) => /^(new project|create project|add project)$/i.test(label(node)));
        if (!control) return false;
        control.click();
        return true;
      })()`);
    };
    if (!(await clickNewProject())) throw new Error('ChatGPT New project control was not found.');
    await sleep(650);
    const introDismissed = await session.evaluate(`(() => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;
      const button = Array.from(dialog.querySelectorAll('button,[role="button"]')).find((node) => /^got it$/i.test(String(node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim()));
      if (!button) return false;
      button.click();
      return true;
    })()`);
    if (introDismissed) {
      await sleep(650);
      if (!(await clickNewProject())) throw new Error('ChatGPT New project control was not found after the Projects introduction.');
      await sleep(650);
    }
    const filled = await session.evaluate(`(() => {
      const name = ${JSON.stringify(projectName)};
      const dialog = document.querySelector('[role="dialog"]') || document;
      const visible = (node) => {
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      };
      const candidates = Array.from(dialog.querySelectorAll('input[type="text"],input:not([type]),textarea,[contenteditable="true"]')).filter(visible);
      const input = candidates.find((node) => /project|name/i.test(String(node.getAttribute('placeholder') || node.getAttribute('aria-label') || node.getAttribute('data-testid') || ''))) || candidates[0];
      if (!input) return false;
      input.focus();
      if ('value' in input) {
        const proto = Object.getPrototypeOf(input);
        const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
        if (descriptor && descriptor.set) descriptor.set.call(input, name); else input.value = name;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        input.textContent = '';
        try { document.execCommand('insertText', false, name); } catch (_) { input.textContent = name; }
        if (!String(input.innerText || input.textContent || '').trim()) input.textContent = name;
        input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: name }));
      }
      return true;
    })()`);
    if (!filled) throw new Error('ChatGPT project name field was not found.');
    await sleep(500);
    let clicked = false;
    for (let attempt = 0; attempt < 8 && !clicked; attempt += 1) {
      clicked = await session.evaluate(`(() => {
        const dialog = document.querySelector('[role="dialog"]') || document;
        const text = (node) => String(node.innerText || node.textContent || node.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
        const buttons = Array.from(dialog.querySelectorAll('button,[role="button"]'));
        const create = buttons.find((node) => /^(create|save|continue|done|create project)$/i.test(text(node)) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
        if (!create) return false;
        create.click();
        return true;
      })()`);
      if (!clicked) await sleep(350);
    }
    if (!clicked) throw new Error('ChatGPT project creation button was not available after entering the project name.');
    // Do not trust the current URL after Create. ChatGPT can leave the browser on
    // the previously selected project for a short period. The fresh project catalog
    // is authoritative for name -> g-p ID mapping.
    let match = null;
    let projects = [];
    for (let attempt = 0; attempt < 3 && !match; attempt += 1) {
      if (attempt) await sleep(700);
      projectCatalogCache.expiresAt = 0;
      projects = await captureSidebarProjects(target).catch(() => []);
      match = projects.find((row) => String(row.name || '').trim().toLocaleLowerCase() === projectName.trim().toLocaleLowerCase()) || null;
    }
    const projectId = match ? String(match.chatgpt_project_id || match.id || '') : '';
    if (!/^g-p-/.test(projectId)) throw new Error('ChatGPT did not confirm the new project in the project catalog.');
    const created = { id: projectId, name: projectName, chatgpt_project_id: projectId };
    const remaining = (projectCatalogCache.projects || []).filter((row) =>
      String(row.id || '') !== projectId && String(row.name || '').toLocaleLowerCase() !== projectName.toLocaleLowerCase()
    );
    projectCatalogCache = { expiresAt: Date.now() + 300000, projects: remaining.concat([created]).sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''))) };
    persistProjectCatalog(projectCatalogCache.projects);
    return created;
  } finally {
    session.close();
  }
}
async function setProjectInstructionsInternal(body) {
  const projectId = cleanText(body && (body.project_id || body.chatgpt_project_id), 220);
  const projectName = cleanText(body && (body.project_name || body.name), 180);
  const instructions = String(body && body.instructions || '').trim().slice(0, 12000);
  const verifyExisting = Boolean(body && body.verify_existing);
  if (!/^g-p-/.test(projectId)) throw Object.assign(new Error('A valid ChatGPT Project is required.'), { statusCode: 400 });
  if (!instructions && !verifyExisting) throw Object.assign(new Error('Project instructions are required.'), { statusCode: 400 });

  const target = await findChatGptTarget();
  const session = await new CdpSession(target).open();

  const rateLimited = async () => {
    for (let pass = 0; pass < 4; pass += 1) {
      const limited = await rateLimitOverlay(session).catch(() => ({ active: false }));
      if (!limited || !limited.active) return false;
      const dismissed = await dismissKnownRateLimitOverlay(session).catch(() => false);
      if (!dismissed) break;
      await sleep(450);
    }
    const limited = await rateLimitOverlay(session).catch(() => ({ active: false }));
    if (!limited || !limited.active) return false;
    const error = new Error('ChatGPT web session is temporarily rate limited.');
    error.statusCode = 429;
    throw error;
  };

  const clickVisibleProjectSettings = async () => session.evaluate(`(() => {
    const label = n => String((n && (n.innerText || n.textContent || (n.getAttribute && n.getAttribute('aria-label')))) || '').replace(/\\s+/g, ' ').trim();
    const visible = n => {
      if (!n || !n.getBoundingClientRect) return false;
      const rect = n.getBoundingClientRect();
      const style = getComputedStyle(n);
      return rect.width > 2 && rect.height > 2 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    };
    const nodes = Array.from(document.querySelectorAll('[role="menuitem"],button,[role="button"]'))
      .filter(n => visible(n) && /Project settings/i.test(label(n)))
      .sort((a,b) => {
        const ar = a.getAttribute('role') === 'menuitem' ? 0 : 1;
        const br = b.getAttribute('role') === 'menuitem' ? 0 : 1;
        if (ar !== br) return ar - br;
        return label(a).length - label(b).length;
      });
    const item = nodes[0];
    if (!item) return false;
    item.click();
    return true;
  })()`).catch(() => false);

  const clickVisibleProjectDetails = async () => {
    const hit = await session.evaluate(`(() => {
      const nodes = Array.from(document.querySelectorAll('button[aria-label="Show project details"],[role="button"][aria-label="Show project details"]'));
      const visible = nodes.filter(node => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 2 && rect.height > 2 && style.display !== 'none' && style.visibility !== 'hidden';
      });
      if (!visible.length) return null;
      const button = visible.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0];
      const rect = button.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`).catch(() => null);
    if (!hit || !Number.isFinite(hit.x) || !Number.isFinite(hit.y)) return false;
    await session.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hit.x, y: hit.y });
    await session.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: hit.x, y: hit.y, button: 'left', buttons: 1, clickCount: 1 });
    await session.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: hit.x, y: hit.y, button: 'left', buttons: 0, clickCount: 1 });
    return true;
  };

  const waitForInstructionsField = async (timeoutMs = 12000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(250);
      const ready = await session.evaluate(`Boolean(document.querySelector('textarea[aria-label="Instructions"]'))`).catch(() => false);
      if (ready) return true;
      await rateLimited();
    }
    return false;
  };

  const openSettings = async () => {
    let lastDetailsClicked = false;
    let lastSettingsClicked = false;
    let lastAttempt = -1;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      lastAttempt = attempt;
      const alreadyOpen = await session.evaluate(`Boolean(document.querySelector('textarea[aria-label="Instructions"]'))`).catch(() => false);
      if (alreadyOpen) return true;

      // If the menu is already open, consume its Project settings item first.
      // Direct DOM click matches the current ChatGPT React menu behavior more
      // reliably than clicking the element's screen-center coordinates.
      let settingsClicked = await clickVisibleProjectSettings();
      lastSettingsClicked = Boolean(settingsClicked);
      if (!settingsClicked) {
        let detailsClicked = false;
        const detailsDeadline = Date.now() + 12000;
        while (Date.now() < detailsDeadline && !detailsClicked) {
          detailsClicked = await clickVisibleProjectDetails();
          lastDetailsClicked = Boolean(detailsClicked);
          if (detailsClicked) break;
          await rateLimited();
          await sleep(300);
        }
        if (!detailsClicked) {
          if (attempt < 2) { await sleep(700); continue; }
          break;
        }
        const menuDeadline = Date.now() + 9000;
        while (Date.now() < menuDeadline && !settingsClicked) {
          await sleep(250);
          settingsClicked = await clickVisibleProjectSettings();
          lastSettingsClicked = Boolean(settingsClicked);
          if (settingsClicked) break;
          await rateLimited();
        }
      }

      if (!settingsClicked) {
        if (attempt < 2) { await sleep(700); continue; }
        break;
      }
      if (await waitForInstructionsField(12000)) return true;
      if (attempt < 2) await sleep(700);
    }

    const stateDiag = await session.evaluate(`(() => {
      const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
      const kinds = dialogs.map(d => {
        const text = String(d.innerText || d.textContent || '');
        if (/Too many requests|temporarily limited access to conversations/i.test(text)) return 'rate-limit';
        if (/Project settings/i.test(text)) return 'project-settings';
        return 'other';
      });
      const menus = Array.from(document.querySelectorAll('[role="menu"]'));
      const instructionsReady = Boolean(document.querySelector('textarea[aria-label="Instructions"]'));
      return { kinds, menus: menus.length, instructions: instructionsReady };
    })()`).catch(() => ({ kinds: ['unknown'], menus: 0, instructions: false }));
    if ((stateDiag.kinds || []).includes('rate-limit')) {
      const error = new Error('ChatGPT web session is temporarily rate limited.');
      error.statusCode = 429;
      throw error;
    }
    throw new Error(`ChatGPT Project settings could not be opened (attempt=${lastAttempt + 1}, details_clicked=${lastDetailsClicked}, settings_clicked=${lastSettingsClicked}, dialogs=${(stateDiag.kinds || []).join(',') || 'none'}, menus=${Number(stateDiag.menus || 0)}, instructions=${Boolean(stateDiag.instructions)}).`);
  };

  const closeSettings = async () => session.evaluate(`(() => {
    const dialog = Array.from(document.querySelectorAll('[role="dialog"]')).find(d => d.querySelector('textarea[aria-label="Instructions"]'));
    if (!dialog) return false;
    const button = dialog.querySelector('button[aria-label="Close"],button[data-testid="close-button"]');
    if (!button) return false;
    button.click();
    return true;
  })()`).catch(() => false);

  const setInstructionsValue = async (value) => {
    const targetValue = String(value || '');
    const hit = await session.evaluate(`(() => {
      const field = document.querySelector('textarea[aria-label="Instructions"]');
      if (!field) return null;
      const rect = field.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + Math.min(rect.height / 2, 36) };
    })()`).catch(() => null);
    if (!hit || !Number.isFinite(hit.x) || !Number.isFinite(hit.y)) return false;
    await session.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hit.x, y: hit.y });
    await session.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: hit.x, y: hit.y, button: 'left', buttons: 1, clickCount: 1 });
    await session.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: hit.x, y: hit.y, button: 'left', buttons: 0, clickCount: 1 });
    await session.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Control', code: 'ControlLeft', windowsVirtualKeyCode: 17, modifiers: 2 });
    await session.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
    await session.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
    await session.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Control', code: 'ControlLeft', windowsVirtualKeyCode: 17 });
    if (targetValue) await session.call('Input.insertText', { text: targetValue });
    else await session.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
    await sleep(250);
    const current = await session.evaluate(`String((document.querySelector('textarea[aria-label="Instructions"]') || {}).value || '')`).catch(() => null);
    return String(current == null ? '' : current) === targetValue;
  };

  const submitSettingsForm = async () => session.evaluate(`(() => {
    const field = document.querySelector('textarea[aria-label="Instructions"]');
    if (!field) return false;
    const form = field.closest('form') || document.querySelector('form[aria-label="Project settings"]');
    if (!form || typeof form.requestSubmit !== 'function') return false;
    form.requestSubmit();
    return true;
  })()`).catch(() => false);

  const submitSettingsFormAndVerifyNetwork = async (expected) => {
    await session.call('Network.enable').catch(() => ({}));
    const wantedPath = '/backend-api/projects/' + projectId;
    let requestId = '';
    let responseStatus = 0;
    let bodyChecked = false;
    let bodyMatched = false;
    let offRequest = () => {};
    let offResponse = () => {};
    try {
      offRequest = session.on('Network.requestWillBeSent', (params) => {
        try {
          const request = params && params.request || {};
          const url = new URL(String(request.url || ''));
          if (request.method === 'PATCH' && url.hostname === 'chatgpt.com' && url.pathname === wantedPath) {
            requestId = String(params.requestId || '');
          }
        } catch (_) {}
      });
      offResponse = session.on('Network.responseReceived', (params) => {
        if (!requestId || String(params && params.requestId || '') !== requestId) return;
        responseStatus = Number(params && params.response && params.response.status || 0);
      });
      const submitted = await submitSettingsForm();
      if (!submitted) return { verified: false, submitted: false, status: 0, bodyMatched: false };
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        if (requestId && !bodyChecked) {
          try {
            const post = await session.call('Network.getRequestPostData', { requestId }, 5000);
            const data = JSON.parse(String(post && post.postData || '{}'));
            bodyMatched = String(data && data.instructions || '') === String(expected || '');
            bodyChecked = true;
          } catch (_) {}
        }
        if (bodyChecked && bodyMatched && responseStatus >= 200 && responseStatus < 300) {
          return { verified: true, submitted: true, status: responseStatus, bodyMatched: true };
        }
        if (responseStatus >= 400) break;
        await sleep(150);
      }
      return { verified: false, submitted: true, status: responseStatus, bodyMatched };
    } finally {
      offRequest();
      offResponse();
    }
  };

  const waitForSettingsClosed = async (timeoutMs = 10000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const open = await session.evaluate(`Boolean(document.querySelector('textarea[aria-label="Instructions"]'))`).catch(() => false);
      if (!open) return true;
      await sleep(200);
    }
    return false;
  };

  const verifyProjectInstructionsApi = async (expected) => session.evaluate(`(async () => {
    const expected = ${JSON.stringify(expected)};
    const id = ${JSON.stringify(projectId)};
    const response = await fetch('/backend-api/projects/' + encodeURIComponent(id), { credentials: 'include', cache: 'no-store' });
    if (!response.ok) return { ok: false, status: response.status, matched: false, candidates: 0 };
    let data = {};
    try { data = await response.json(); } catch (_) { return { ok: false, status: response.status, matched: false, candidates: 0 }; }
    const values = [];
    const seen = new Set();
    const walk = (value, depth) => {
      if (depth > 7 || value == null || seen.has(value)) return;
      if (typeof value === 'object') seen.add(value);
      if (Array.isArray(value)) { value.slice(0, 100).forEach((item) => walk(item, depth + 1)); return; }
      if (typeof value !== 'object') return;
      for (const [key, child] of Object.entries(value)) {
        if (/instruction/i.test(String(key)) && typeof child === 'string') values.push(child);
        walk(child, depth + 1);
      }
    };
    walk(data, 0);
    return { ok: true, status: response.status, matched: values.some((value) => String(value) === String(expected)), candidates: values.length };
  })()`).catch(() => ({ ok: false, status: 0, matched: false, candidates: 0 }));

  try {
    const currentLocation = await session.location().catch(() => '');
    let selectedProject = null;
    if (parseProjectId(currentLocation) === projectId) {
      selectedProject = { url: currentLocation, project_id: projectId, project_name: projectName };
    } else {
      selectedProject = await selectProject(session, projectName, projectId);
      const selectedRoot = projectRootUrl(selectedProject.url);
      const afterSelect = await session.location().catch(() => '');
      let afterRoot = '';
      try { afterRoot = parseProjectId(afterSelect) === projectId ? projectRootUrl(afterSelect) : ''; } catch (_) {}
      if (afterRoot !== selectedRoot) {
        await session.navigate(selectedRoot);
        await sleep(750);
      }
    }
    await openSettings();

    const existingValue = await session.evaluate(`String((document.querySelector('textarea[aria-label="Instructions"]') || {}).value || '')`);
    const targetInstructions = verifyExisting ? String(existingValue || '') : String(instructions || '');
    if (body && body.dry_run) {
      await closeSettings();
      return { success: true, project: { id: projectId, name: projectName || projectId }, instructions_editor_found: true, dry_run: true, closed: true };
    }

    const filled = await setInstructionsValue(targetInstructions);
    if (!filled) throw new Error('ChatGPT Project Instructions field was not updated.');
    const saveResult = await submitSettingsFormAndVerifyNetwork(targetInstructions);
    if (!saveResult.verified) {
      throw new Error(`ChatGPT Project instructions save was not confirmed (status=${Number(saveResult.status || 0)}, body_match=${Boolean(saveResult.bodyMatched)}).`);
    }
    await waitForSettingsClosed(10000).catch(() => false);
    await closeSettings().catch(() => false);
    const verified = true;

    if (!verified) {
      if (!verifyExisting && String(existingValue || '') !== String(targetInstructions || '')) {
        await openSettings().catch(() => false);
        const restored = await setInstructionsValue(String(existingValue || '')).catch(() => false);
        if (restored) {
          await submitSettingsForm().catch(() => false);
          await waitForSettingsClosed(8000).catch(() => false);
        }
        await closeSettings().catch(() => false);
      }
      throw new Error('ChatGPT Project Instructions could not be verified after saving.');
    }

    return {
      success: true,
      project: { id: projectId, name: projectName || projectId },
      instructions_saved: true,
      verified: true,
      verify_existing: verifyExisting,
    };
  } finally {
    session.close();
  }
}


async function captureAssistantRichResponse(session, baselineAssetSignature = '') {
  const baselineAssets = String(baselineAssetSignature || '').split('|').filter(Boolean);
  const snapshot = await session.evaluate(`(async () => {
    const baseline = new Set(${JSON.stringify(baselineAssets)});
    const assistants = Array.from(document.querySelectorAll('[data-message-author-role="assistant"], [data-content-search-unit-key$=":assistant"]'));
    const node = assistants.length ? assistants[assistants.length - 1] : document.createElement('div');
    const text = String(node.innerText || node.textContent || '').trim();

    const clone = node.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,svg,button,img,picture,source,video,audio,canvas').forEach((el) => el.remove());
    const allowed = new Set(['P','DIV','SPAN','H1','H2','H3','H4','H5','H6','PRE','CODE','UL','OL','LI','BLOCKQUOTE','TABLE','THEAD','TBODY','TR','TH','TD','STRONG','B','EM','I','S','BR','HR','A']);
    Array.from(clone.querySelectorAll('*')).forEach((el) => {
      const tag = String(el.tagName || '').toUpperCase();
      if (!allowed.has(tag)) {
        const fragment = document.createDocumentFragment();
        while (el.firstChild) fragment.appendChild(el.firstChild);
        el.replaceWith(fragment);
        return;
      }
      Array.from(el.attributes || []).forEach((attr) => {
        const name = String(attr.name || '').toLowerCase();
        if (tag === 'A' && name === 'href') {
          try {
            const value = new URL(String(attr.value || ''), location.href);
            if (!/^https?:$/.test(value.protocol)) el.removeAttribute(attr.name);
            else el.setAttribute('href', value.href);
          } catch (_) { el.removeAttribute(attr.name); }
        } else {
          el.removeAttribute(attr.name);
        }
      });
      if (tag === 'A' && el.getAttribute('href')) {
        el.setAttribute('target', '_blank');
        el.setAttribute('rel', 'noopener noreferrer');
      }
    });
    const html = String(clone.innerHTML || '').trim();

    const candidates = [];
    const seen = new Set();
    const add = (item) => {
      const url = String(item && item.url || '').trim();
      const rect = item && item.rect;
      const key = url || (rect ? [item.kind, Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)].join(':') : '');
      if (!key || seen.has(key)) return;
      if (url && !/^(https?:|blob:|data:)/i.test(url)) return;
      seen.add(key);
      candidates.push(item);
    };
    const imgInfo = (img, index, source) => {
      const rect = img.getBoundingClientRect();
      const src = String(img.currentSrc || img.src || img.getAttribute('src') || '').trim();
      if (!src || (rect.width < 128 && rect.height < 128 && (img.naturalWidth < 256 || img.naturalHeight < 256))) return;
      const alt = String(img.getAttribute('alt') || '').replace(/\s+/g, ' ').trim();
      add({
        kind: 'image',
        url: src,
        name: (alt && alt.length <= 120 ? alt : ('chatgpt-generated-image-' + (index + 1))) + '.png',
        content_type: 'image/png',
        source,
        rect: {
          x: Math.max(0, rect.left + window.scrollX),
          y: Math.max(0, rect.top + window.scrollY),
          width: Math.max(1, rect.width),
          height: Math.max(1, rect.height)
        }
      });
    };

    Array.from(node.querySelectorAll('img[src],img[srcset]')).forEach((img, index) => imgInfo(img, index, 'assistant'));

    Array.from(node.querySelectorAll('a[href]')).forEach((link, index) => {
      const href = String(link.href || '').trim();
      const download = String(link.getAttribute('download') || '').trim();
      const label = String(link.innerText || link.textContent || '').replace(/\s+/g, ' ').trim();
      if (!href || !(download || /download|attachment|file/i.test(label) || /\.(pdf|docx?|xlsx?|csv|zip|txt|md|json|png|jpe?g|webp|gif)(?:[?#]|$)/i.test(href))) return;
      let filename = download || label || ('chatgpt-attachment-' + (index + 1));
      filename = filename.replace(/[\\/:*?"<>|]+/g, '-').slice(0, 140);
      add({ kind: /\.(png|jpe?g|webp|gif)(?:[?#]|$)/i.test(href) ? 'image' : 'file', url: href, name: filename, content_type: '', source: 'assistant-link' });
    });

    let totalBytes = 0;
    const maxTotal = 18 * 1024 * 1024;
    const maxOne = 12 * 1024 * 1024;
    const attachments = [];
    for (const item of candidates.slice(0, 8)) {
      const out = Object.assign({}, item, { data_url: '' });
      if (item.url) {
        try {
          const response = await fetch(item.url, { credentials: 'include' });
          if (response.ok) {
            const blob = await response.blob();
            out.content_type = String(blob.type || out.content_type || '');
            if (blob.size > 0 && blob.size <= maxOne && totalBytes + blob.size <= maxTotal) {
              totalBytes += blob.size;
              out.data_url = await new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result || ''));
                reader.onerror = () => resolve('');
                reader.readAsDataURL(blob);
              });
            }
          }
        } catch (_) {}
      }
      attachments.push(out);
    }
    return { text, html, attachments };
  })()`);

  const attachments = Array.isArray(snapshot && snapshot.attachments) ? snapshot.attachments : [];
  for (const item of attachments) {
    if (!item || item.kind !== 'image' || item.data_url || !item.rect) continue;
    const rect = item.rect;
    try {
      const width = Math.min(1800, Math.max(1, Number(rect.width) || 1));
      const height = Math.min(1800, Math.max(1, Number(rect.height) || 1));
      if (width < 64 || height < 64) continue;
      const shot = await session.call('Page.captureScreenshot', {
        format: 'png',
        fromSurface: true,
        captureBeyondViewport: true,
        clip: {
          x: Math.max(0, Number(rect.x) || 0),
          y: Math.max(0, Number(rect.y) || 0),
          width,
          height,
          scale: 1
        }
      }, 30000);
      if (shot && shot.data) {
        item.data_url = 'data:image/png;base64,' + shot.data;
        item.content_type = 'image/png';
        item.size = Math.floor(String(shot.data).length * 0.75);
        if (!/\.png$/i.test(String(item.name || ''))) item.name = String(item.name || 'chatgpt-generated-image') + '.png';
      }
    } catch (_) {}
  }
  attachments.forEach((item) => {
    if (item && Object.prototype.hasOwnProperty.call(item, 'rect')) delete item.rect;
    if (item && Object.prototype.hasOwnProperty.call(item, 'source')) delete item.source;
  });
  return {
    text: String(snapshot && snapshot.text || '').trim(),
    html: String(snapshot && snapshot.html || '').trim(),
    attachments
  };
}


async function browserLoginState(target) {
  const session = await new CdpSession(target).open();
  try {
    return await session.evaluate(`(async()=>{
      try {
        const visible=node=>Boolean(node.getClientRects().length);
        const loginControl=Array.from(document.querySelectorAll('button,a')).some(node=>visible(node)&&/^log in$/i.test(String(node.innerText||'').trim()));
        if(location.pathname.startsWith('/auth/')||loginControl)return {authenticated:false,status:'Login required'};
        const response=await fetch('/api/auth/session',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(7000)});
        if(!response.ok)return {authenticated:false,status:'Login required'};
        const data=await response.json();
        const authenticated=Boolean(data.user?.email)&&(!data.expires||Date.parse(data.expires)>Date.now());
        return {authenticated,status:authenticated?'Ready':'Login required'};
      } catch(_){return {authenticated:false,status:'Session check unavailable'};}
    })()`);
  } finally {session.close();}
}

async function healthPayload() {
  try {
    const targets = (await cdpTargets()).filter(isChatGptPage);
    let targetReady = false;
    let login = {authenticated:false,status:'Browser unavailable'};
    if (RELAY_TOKEN) {
      const target = await findChatGptTarget().catch(() => null);
      targetReady = Boolean(target && await targetResponsive(target, 3000));
      if (targetReady) login = await browserLoginState(target);
    }
    return {
      ok: true,
      service: 'abit-erp-chatgpt-relay',
      write_bridge_version: 'task04222-generic-write-v2',
      ready: Boolean(RELAY_TOKEN && targetReady && login.authenticated),
      authenticated: login.authenticated,
      status: login.status,
      target_ready: targetReady,
      authenticated_endpoint: Boolean(RELAY_TOKEN),
      chatgpt_pages: targets.length,
      rate_limited: rateLimitCircuitOpen(),
      retry_after_seconds: rateLimitRetryAfterSeconds(),
      rate_limit_failures: Number(rateLimitState.failures || 0),
    };
  } catch (error) {
    return { ok: false, service: 'abit-erp-chatgpt-relay', ready: false, target_ready: false, message: error.message };
  }
}


async function connectBrowser(){ return healthPayload(); }
// TASK-2026-04463: isolated development cancellation and durable identity.
// This does not alter the legacy normal /relay request envelope.
const DEV_RUN_PATH = require('path').join(__dirname,'development-relay-runs.json');
const DEV_PROJECT='g-p-6ad33f589cbc8191b52523fffd061dae';
function devLoad(){try{return JSON.parse(fs.readFileSync(DEV_RUN_PATH,'utf8'))}catch(e){if(e.code==='ENOENT')return {};throw e}}
function devSave(records){const tmp=DEV_RUN_PATH+'.'+process.pid+'.tmp';fs.writeFileSync(tmp,JSON.stringify(records),{mode:0o600});fs.renameSync(tmp,DEV_RUN_PATH)}
function devRecord(body){const id=String(body.run_id||'');if(!/^[a-f0-9]{32}$/.test(id))throw Object.assign(Error('Invalid development run ID'),{statusCode:400});const all=devLoad();return {id,all,row:all[id]}}
function devOwner(body,row){if(!row||row.owner!==body.owner||row.project_id!==DEV_PROJECT||body.project_id!==DEV_PROJECT)throw Object.assign(Error('Development request ownership mismatch'),{statusCode:403})}
async function devCancel(body){
 const {id,all,row}=devRecord(body);devOwner(body,row);
 if(row.state==='cancelled')return {accepted:true,confirmed:true,state:'cancelled'};
 if(row.state==='pending'){
   all[id]={...row,state:'cancelled',cancel_at:Date.now(),confirmed_at:Date.now()};devSave(all);
   return {accepted:true,confirmed:true,state:'cancelled',upstream_sent:false};
 }
 all[id]={...row,state:'cancel_requested',cancel_at:Date.now()};devSave(all);
 // Never stop a global/unrelated conversation, even if the browser changes tabs.
 if(!row.conversation_url||!row.target_id||!row.submission_acknowledged)
   return {accepted:true,confirmed:false,state:'reconciliation_required'};
 let target;
 try{target=(await cdpTargets()).find(t=>t.id===row.target_id&&isChatGptPage(t)&&t.url===row.conversation_url)}catch(e){return {accepted:true,confirmed:false,state:'browser_unavailable'}}
 if(!target)return {accepted:true,confirmed:false,state:'target_changed'};
 const session=await new CdpSession(target).open();
 try{
   const marker='[ABIT-RUN:'+id+']';
   const observed=await session.evaluate(`(()=>({url:location.href,last:[...document.querySelectorAll('[data-message-author-role=user]')].slice(-1)[0]?.innerText||'',generating:!!document.querySelector('[data-testid="stop-button"]')}))()`);
   if(observed.url!==row.conversation_url||!observed.last.includes(marker))return {accepted:true,confirmed:false,state:'conversation_mismatch'};
   if(observed.generating){
     await session.evaluate(`(()=>{const b=document.querySelector('[data-testid="stop-button"]');if(!b)throw Error('Matching Stop absent');b.click()})()`);
   }
   let settled=0;
   for(let i=0;i<12;i++){
     await new Promise(resolve=>setTimeout(resolve,500));
     const check=await session.evaluate(`(()=>({url:location.href,last:[...document.querySelectorAll('[data-message-author-role=user]')].slice(-1)[0]?.innerText||'',generating:!!document.querySelector('[data-testid="stop-button"]')}))()`);
     if(check.url!==row.conversation_url||!check.last.includes(marker))return {accepted:true,confirmed:false,state:'conversation_changed'};
     settled=check.generating?0:settled+1;
     if(settled>=3){const updated=devLoad();updated[id]={...updated[id],state:'cancelled',confirmed_at:Date.now()};devSave(updated);return {accepted:true,confirmed:true,state:'cancelled'}}
   }
   return {accepted:true,confirmed:false,state:'stop_not_confirmed'};
 }finally{session.close()}
}

// AIBIT uses only its own browser target. Return a nonreversible account
// fingerprint, never the account email or ChatGPT session token.
async function aibitAccountFingerprint() {
  const target = await findChatGptTarget();
  const session = await new CdpSession(target).open();
  try {
    const account = await session.evaluate(`(async () => {
      const response = await fetch('/api/auth/session', {
        credentials: 'same-origin', cache: 'no-store',
        signal: AbortSignal.timeout(12000)
      });
      if (!response.ok) throw Error('ChatGPT session unavailable');
      const info = await response.json();
      if (!info.user?.email || !info.accessToken) throw Error('ChatGPT login required');
      return String(info.user.email).toLowerCase().trim();
    })()`);
    if (!account || typeof account !== 'string') throw Error('ChatGPT account verification failed');
    return crypto.createHash('sha256').update(account, 'utf8').digest('hex');
  } finally { session.close(); }
}

const server = http.createServer(async (req, res) => {
  const path = requestPath(req);
  if (req.method === 'GET' && path === '/health') {
    const payload = await healthPayload();
    return json(res, payload.ok ? 200 : 503, payload);
  }
  if (!authorized(req)) return json(res, 401, { ok: false, message: 'Unauthorized.' });

  try {
    if (req.method === 'POST' && path === '/development/register') {
      const body=await readJson(req);const {id,all,row}=devRecord(body);
      if(body.project_id!==DEV_PROJECT||!body.owner||String(body.owner).length>200)throw Object.assign(Error('Invalid development owner/project'),{statusCode:400});
      if(row&&row.owner!==body.owner)throw Object.assign(Error('Development ID owned by another user'),{statusCode:403});
      if(!row){all[id]={owner:String(body.owner),project_id:DEV_PROJECT,state:'pending',created_at:Date.now(),target_id:null,conversation_url:null,submission_acknowledged:false};devSave(all)}
      return json(res,200,{ok:true,run_id:id,state:all[id].state});
    }
    if (req.method === 'POST' && path === '/development/ack') {
      const body=await readJson(req);const {id,all,row}=devRecord(body);devOwner(body,row);
      const stage=String(body.state||'');
      if(!['submitted','generating','implementing','deploying','verifying'].includes(stage))throw Object.assign(Error('Invalid development state'),{statusCode:400});
      const conversation=String(body.conversation_url||'');const target=String(body.target_id||'');
      if(!/^https:\/\/chatgpt\.com\/g\/g-p-6ad33f589cbc8191b52523fffd061dae[^/]*\/c\//.test(conversation)||! /^[A-Fa-f0-9]{16,64}$/.test(target))throw Object.assign(Error('Matching conversation and target required'),{statusCode:400});
      if(row.cancel_at||row.state==='cancelled')throw Object.assign(Error('Development request revoked'),{statusCode:409});
      if(row.conversation_url&&row.conversation_url!==conversation||row.target_id&&row.target_id!==target)throw Object.assign(Error('Conflicting acknowledgement'),{statusCode:409});
      all[id]={...row,state:stage,conversation_url:conversation,target_id:target,submission_acknowledged:true,updated_at:Date.now()};devSave(all);
      return json(res,200,{ok:true,run_id:id,state:stage});
    }
    if (req.method === 'POST' && path === '/development/status') {
      const body=await readJson(req);const {id,row}=devRecord(body);devOwner(body,row);
      return json(res,200,{ok:true,run_id:id,state:row.state,cancel_at:row.cancel_at||null,confirmed_at:row.confirmed_at||null});
    }
    if (req.method === 'POST' && path === '/development/cancel') {
      const body=await readJson(req);return json(res,200,{ok:true,...await devCancel(body)});
    }
    if (req.method === 'GET' && path === '/connect') {
      return json(res, 200, await connectBrowser());
    }
    if (req.method === 'GET' && path === '/account') {
      const account_id = await withBrowserLock(aibitAccountFingerprint);
      return json(res, 200, {ok: true, account_id});
    }
    if (req.method === 'GET' && path === '/projects') {
      const catalog = await withBrowserLock(async () => ({
        projects: await listProjectsInternal(true),
        account_id: await aibitAccountFingerprint()
      }));
      return json(res, 200, {ok: true, complete: true, ...catalog});
    }
    if (req.method === 'POST' && path === '/projects/create') {
      const body = await readJson(req);
      const project = await withBrowserLock(() => createProjectInternal(body.name));
      return json(res, 200, { ok: true, project });
    }
    if (req.method === 'POST' && path === '/projects/instructions') {
      const body = await readJson(req);
      const result = await withBrowserLock(() => setProjectInstructionsInternal(body));
      return json(res, 200, { ok: true, ...result });
    }
    if (req.method === 'POST' && path === '/hermes/orchestrate') {
      const body = await readJson(req);
      const result = await hermesOrchestrate(body);
      return json(res, 200, result);
    }    if (req.method === 'POST' && path === '/relay') {
      const body = await readJson(req);
      if (body.stream === true) {
        res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no' });
        const emit = (type, data) => { if (!res.destroyed && !res.writableEnded) res.write(JSON.stringify({ type, ...data }) + '\n'); };
        const heartbeat = setInterval(() => emit('heartbeat', {}), 5000);
        try {
          const result = await withBrowserLock(async () => {
            emit('status', { state: 'opening_project' });
            const account_id = await aibitAccountFingerprint();
            const reply = await relayMessage(body, (partial) => emit('progress', partial));
            return { ...reply, account_id };
          });
          emit('complete', { result });
        } catch (error) {
          emit('error', { message: String(error.message || 'Relay failed'), status: Number(error.statusCode || 502) });
        } finally { clearInterval(heartbeat); res.end(); }
        return;
      }
      const result = await withBrowserLock(async () => {
        const account_id = await aibitAccountFingerprint();
        const answer = await relayMessage(body);
        return {...answer, account_id};
      });
      return json(res, 200, result);
    }
    return json(res, 404, { ok: false, message: 'Not found.' });
  } catch (error) {
    const status = Number(error.statusCode || 502);
    return json(res, status, { ok: false, success: false, message: String(error.message || 'Relay request failed.').slice(0, 500) });
  }
});

server.listen(PORT, HOST, () => {
  // Do not log prompts, responses, tokens, browser profile paths, or project names.
  console.log(`ABIT ERP ChatGPT relay listening on http://${HOST}:${PORT}`);
});

process.on('SIGTERM', () => server.close(() => process.exit(0)));
process.on('SIGINT', () => server.close(() => process.exit(0)));

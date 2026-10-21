'use strict';
// Chrome's opt-in "Allow remote debugging" exposes only the browser WebSocket.
// Keep one approved transport alive and multiplex short-lived page sessions.
const fs = require('fs');
const path = require('path');
function browserEndpoint() {
  const directory = process.env.CHATGPT_EXISTING_CHROME_USER_DATA_DIR;
  if (!directory) throw new Error('Existing Chrome user data directory is not configured.');
  let lines;
  try { lines = fs.readFileSync(path.join(directory, 'DevToolsActivePort'), 'utf8').trim().split(/\r?\n/); }
  catch (_) { throw new Error('Open your signed-in Chrome and enable remote debugging at chrome://inspect/#remote-debugging.'); }
  const port = Number(lines[0]);
  const route = String(lines[1] || '').trim();
  if (!Number.isInteger(port) || port < 1 || port > 65535 || !/^\/devtools\/browser\/[a-zA-Z0-9-]+$/.test(route)) {
    throw new Error('Chrome DevToolsActivePort is invalid.');
  }
  return 'ws://127.0.0.1:' + port + route;
}
class ExistingChrome {
  constructor() { this.ws = null; this.connecting = null; this.seq = 0; this.pending = new Map(); this.listeners = new Map(); this.retryAfter = 0; this.keepalive = null; }
  async connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
    if (this.connecting) return this.connecting;
    if (Date.now() < this.retryAfter) throw new Error('Chrome connection requires Allow in the signed-in browser. Retry shortly.');
    this.connecting = new Promise((resolve, reject) => {
      let ws;
      let localBroker = false;
      try {
        const endpoint = browserEndpoint();
        localBroker = new URL(endpoint).pathname === '/devtools/browser/shared';
        ws = new WebSocket(endpoint);
      } catch (error) { reject(error); return; }
      this.ws = ws;
      const timer = setTimeout(() => { fail(new Error('Chrome connection approval timed out. Click Allow in your signed-in Chrome.')); }, 120000);
      const fail = error => {
        clearTimeout(timer);
        if (this.keepalive) { clearInterval(this.keepalive); this.keepalive = null; }
        if (this.ws !== ws) { reject(error); return; }
        this.ws = null;
        // Reconnecting to our local broker does not request Chrome consent.
        // Preserve the long backoff only for direct browser approval failures.
        this.retryAfter = Date.now() + (localBroker ? 1000 : 300000);
        for (const entry of this.pending.values()) entry.reject(error);
        this.pending.clear(); this.listeners.clear();
        try { ws.close(); } catch (_) {}
        reject(error);
      };
      ws.onopen = () => {
        clearTimeout(timer);
        this.retryAfter = 0;
        if (this.keepalive) clearInterval(this.keepalive);
        this.keepalive = setInterval(() => {
          if (this.ws !== ws || ws.readyState !== WebSocket.OPEN) return;
          this.call('Browser.getVersion', {}, undefined, 5000).catch(() => {});
        }, 15000);
        if (this.keepalive && typeof this.keepalive.unref === 'function') this.keepalive.unref();
        resolve();
      };
      ws.onerror = () => fail(new Error('Chrome connection unavailable or not approved. Use Allow in your signed-in Chrome.'));
      ws.onclose = () => fail(new Error('Chrome connection closed. Reopen your signed-in browser and approve reconnect.'));
      ws.onmessage = event => {
        let message;
        try { message = JSON.parse(event.data); } catch (_) { return; }
        if (message.id && this.pending.has(message.id)) {
          const entry = this.pending.get(message.id); this.pending.delete(message.id);
          if (message.error) entry.reject(new Error(message.error.message || 'Chrome command failed.'));
          else entry.resolve(message.result || {});
        } else if (message.method && message.sessionId) {
          const handler = this.listeners.get(message.sessionId);
          if (handler) handler(message);
        }
      };
    });
    try { await this.connecting; } finally { this.connecting = null; }
  }
  async call(method, params = {}, sessionId, timeoutMs = 15000) {
    await this.connect();
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('CDP ' + method + ' timed out.')); }, timeoutMs);
      this.pending.set(id, {
        resolve: value => { clearTimeout(timer); resolve(value); },
        reject: error => { clearTimeout(timer); reject(error); }
      });
      try { this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })); }
      catch (error) { this.pending.get(id).reject(error); this.pending.delete(id); }
    });
  }
  async targets() {
    const result = await this.call('Target.getTargets');
    return result.targetInfos.map(info => ({ ...info, id: info.targetId, browserTransport: true, webSocketDebuggerUrl: 'existing-browser' }));
  }
  async create(url = 'https://chatgpt.com/') {
    const targetUrl = String(url || 'https://chatgpt.com/');
    const { targetId } = await this.call('Target.createTarget', { url: targetUrl });
    return { id: targetId, type: 'page', url: targetUrl, browserTransport: true, webSocketDebuggerUrl: 'existing-browser' };
  }
  async attach(targetId, handler) {
    const { sessionId } = await this.call('Target.attachToTarget', { targetId, flatten: true });
    this.listeners.set(sessionId, handler);
    return sessionId;
  }
  detach(sessionId) {
    this.listeners.delete(sessionId);
    // Never reconnect just to detach an already dead session.
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.call('Target.detachFromTarget', { sessionId }).catch(() => {});
  }
}
module.exports = { browserEndpoint, ExistingChrome, existingChrome: new ExistingChrome() };

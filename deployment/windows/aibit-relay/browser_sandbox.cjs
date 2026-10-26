'use strict';

/**
 * Remote view/control of the *existing* ChatGPT relay tab.
 * Only the ABiT backend may call these token-protected routes. It resolves the
 * user-owned chat/account/conversation from its ledger, not from browser input.
 * No CDP endpoint, cookies, or ChatGPT auth credentials are exposed.
 */
function createBrowserSandbox({ getTarget, createSession, accountFingerprint, withLock, isBusy, isRelinking, isActiveFor = () => true }) {
  const fail = (statusCode, message) => {
    throw Object.assign(new Error(message), { statusCode });
  };

  function approved(body) {
    const expected = String(body && body.conversation_url || '');
    const account = String(body && body.account_id || '');
    if (!/^[a-f0-9]{64}$/.test(account)) fail(400, 'Invalid relay account.');
    let url;
    try { url = new URL(expected); } catch (_) { fail(400, 'Invalid relay conversation.'); }
    if (url.origin !== 'https://chatgpt.com' || url.username || url.password ||
        url.search || url.hash || url.port) fail(400, 'Untrusted conversation URL.');
    const parts = url.pathname.split('/').filter(Boolean);
    const id = parts[parts.length - 1];
    if (!/^[a-zA-Z0-9_-]{5,190}$/.test(id) ||
        !((parts.length === 2 && parts[0] === 'c') ||
          (parts.length === 4 && parts[0] === 'g' &&
           /^g-p-[a-zA-Z0-9_-]{5,190}$/.test(parts[1]) && parts[2] === 'c'))) {
      fail(400, 'Invalid relay conversation path.');
    }
    return { account, url, parts };
  }

  function sameConversation(actual, expected) {
    let current;
    try { current = new URL(actual); } catch (_) { return false; }
    if (current.origin !== 'https://chatgpt.com') return false;
    const parts = current.pathname.split('/').filter(Boolean);
    if (parts.length !== expected.parts.length ||
        parts[parts.length - 1] !== expected.parts[expected.parts.length - 1]) return false;
    if (parts.length === 2) return parts[0] === 'c';
    if (parts[0] !== 'g' || parts[2] !== 'c') return false;
    const projectKey = p => (p.match(/^g-p-[0-9a-f]{32}(?=-|$)/i) || [p])[0];
    return projectKey(parts[1]) === projectKey(expected.parts[1]);
  }

  async function sessionFor(body, { requireSame = true } = {}) {
    if (isRelinking()) fail(423, 'ChatGPT account linking is in progress.');
    if (isBusy() && !isActiveFor(body)) {
      fail(423, 'The relay is processing another conversation. The sandbox cannot take control of it.');
    }
    const expected = approved(body);
    const target = await getTarget();
    if (!target) fail(503, 'The relay browser is not connected. Connect it in the client environment.');
    const currentAccount = await accountFingerprint(target);
    if (currentAccount !== expected.account) fail(403, 'The relay browser is logged into another ChatGPT account.');
    const session = await createSession(target);
    try {
      const actual = await session.location();
      if (requireSame && !sameConversation(actual, expected)) {
        fail(409, 'The relay is showing another conversation. Reopen this conversation when the relay is idle.');
      }
      return { session, expected, actual };
    } catch (error) {
      session.close();
      throw error;
    }
  }

  async function view(body) {
    const { session } = await sessionFor(body);
    try {
      const size = await session.evaluate('({width: innerWidth, height: innerHeight})');
      const shot = await session.call('Page.captureScreenshot',
        { format: 'jpeg', quality: 68, captureBeyondViewport: false }, 17000);
      if (!shot || typeof shot.data !== 'string' || !shot.data.length) {
        fail(503, 'The relay browser could not provide a screenshot.');
      }
      // A relay send can navigate during a concurrent capture. Never return
      // a screenshot if the browser switched away during that capture.
      if (!sameConversation(await session.location(), approved(body))) {
        fail(409, 'The relay switched conversations while capturing. Refresh when it is idle.');
      }
      return {
        ok: true, image: 'data:image/jpeg;base64,' + shot.data,
        width: Number(size && size.width) || 1280,
        height: Number(size && size.height) || 720
      };
    } finally { session.close(); }
  }

  async function open(body) {
    // First try the owned target in-place, even while a response is running.
    const initial = await sessionFor(body, { requireSame: false });
    try {
      if (sameConversation(initial.actual, initial.expected)) return { ok: true, already_open: true };
    } finally { initial.session.close(); }
    if (isBusy()) fail(423, 'The relay is processing another request. Wait before switching conversations.');
    // Navigation must obey the existing shared browser lock. Recheck ownership
    // and busy state *inside* the lock to avoid disrupting a newly started send.
    return withLock(async () => {
      if (isBusy() || isRelinking()) fail(423, 'The relay is busy. Try again shortly.');
      const { session, expected } = await sessionFor(body, { requireSame: false });
      try {
        await session.navigate(expected.url.href);
        const actual = await session.location();
        if (!sameConversation(actual, expected)) fail(409, 'ChatGPT did not open the requested conversation.');
        return { ok: true, already_open: false };
      } finally { session.close(); }
    });
  }

  async function input(body) {
    const { session } = await sessionFor(body);
    try {
      const kind = String(body.kind || '');
      if (kind === 'click' || kind === 'double_click' || kind === 'scroll') {
        const x = Number(body.x), y = Number(body.y);
        const size = await session.evaluate('({width: innerWidth, height: innerHeight})');
        if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 ||
            x > size.width || y > size.height) fail(400, 'Pointer outside the relay browser.');
        if (kind === 'scroll') {
          const direction = body.direction === -1 ? -1 : 1;
          await session.call('Input.dispatchMouseEvent',
            { type: 'mouseWheel', x, y, deltaX: 0, deltaY: direction * 380 });
        } else {
          const clickCount = kind === 'double_click' ? 2 : 1;
          await session.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
          await session.call('Input.dispatchMouseEvent',
            { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount });
          await session.call('Input.dispatchMouseEvent',
            { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount });
        }
      } else if (kind === 'text') {
        const value = body.text;
        if (typeof value !== 'string' || !value.length || value.length > 12000) {
          fail(400, 'Text must contain 1 to 12,000 characters.');
        }
        await session.call('Input.insertText', { text: value });
      } else if (kind === 'key') {
        const original = String(body.key || '');
        const ctrlA = original === 'Ctrl+A';
        const allowed = {
          Enter: [13, 'Enter'], Tab: [9, 'Tab'], Escape: [27, 'Escape'],
          Backspace: [8, 'Backspace'], Delete: [46, 'Delete'],
          ArrowLeft: [37, 'ArrowLeft'], ArrowRight: [39, 'ArrowRight'],
          ArrowUp: [38, 'ArrowUp'], ArrowDown: [40, 'ArrowDown'],
        };
        if (!ctrlA && !allowed[original]) fail(400, 'Unsupported remote key.');
        if (ctrlA) await session.call('Input.dispatchKeyEvent',
          { type: 'keyDown', key: 'Control', code: 'ControlLeft', windowsVirtualKeyCode: 17, modifiers: 2 });
        const [windowsVirtualKeyCode, code] = ctrlA ? [65, 'KeyA'] : allowed[original];
        const key = ctrlA ? 'a' : original;
        const params = { key, code, windowsVirtualKeyCode, ...(ctrlA ? { modifiers: 2 } : {}) };
        try {
          await session.call('Input.dispatchKeyEvent', { type: 'keyDown', ...params });
          await session.call('Input.dispatchKeyEvent', { type: 'keyUp', ...params });
        } finally {
          if (ctrlA) await session.call('Input.dispatchKeyEvent',
            { type: 'keyUp', key: 'Control', code: 'ControlLeft', windowsVirtualKeyCode: 17 });
        }
      } else {
        fail(400, 'Unsupported relay browser action.');
      }
      return { ok: true };
    } finally { session.close(); }
  }

  return { open, view, input, approved, sameConversation };
}

module.exports = { createBrowserSandbox };

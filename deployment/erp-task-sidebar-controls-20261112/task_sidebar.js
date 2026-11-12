// ERPNext Client Script: Task — ABIT.AI Agent Controls
// Designed to coexist with "ABIT Task Sidebar User Control" (do not replace it).
(function () {
  "use strict";
  if (window.__abitTaskAgentControlV1) return;
  window.__abitTaskAgentControlV1 = true;
  const SELECTOR = ".abit-task-agent-controls";
  const STYLE = "abit-task-agent-controls-style";
  const REFRESH_MS = 12000;
  const TASK_RE = /^TASK-[A-Za-z0-9-]{1,64}$/;
  const esc = value => String(value == null ? "" : value)
    .replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const state = { name: "", busy: false, info: null, timer: null, request: 0 };

  function safeLink(raw, site, kind) {
    if (!raw) return "";
    try {
      const u = new URL(String(raw));
      if (u.protocol !== "https:" || u.username || u.password || u.search || u.hash) return "";
      if (kind === "agent")
        return u.origin === "https://abit.ai" && /^\/automations\/[0-9a-f-]{32,36}$/i.test(u.pathname) ? u.href : "";
      if (kind === "abit")
        return u.origin === "https://abit.ai" && /^\/c\/[0-9a-f-]{32,36}$/i.test(u.pathname) ? u.href : "";
      if (kind === "chatgpt")
        return ["https://chatgpt.com","https://chat.openai.com"].includes(u.origin) &&
          /^\/(?:c\/[0-9a-f-]{32,36}|g\/g-p-[a-z0-9-]{32,150}\/c\/[0-9a-f-]{32,36})$/i.test(u.pathname) ? u.href : "";
    } catch (_) { /* Bad or incomplete URL */ }
    return "";
  }

  function sidebar(frm) {
    const candidates = [
      frm && frm.page && frm.page.wrapper,
      frm && frm.wrapper,
      frm && frm.$wrapper,
      frm && frm.layout && frm.layout.wrapper
    ].map(node => node && (node[0] || node)).filter(Boolean);
    for (const base of candidates) {
      const root = base.querySelector(".layout-side-section") ? base :
        base.closest && base.closest(".page-container");
      const side = root && root.querySelector(".layout-side-section");
      if (side) return side.querySelector(".form-sidebar") || side;
    }
    return null;
  }
  function style() {
    if (document.getElementById(STYLE)) return;
    const tag = document.createElement("style"); tag.id = STYLE;
    tag.textContent = `
      ${SELECTOR} { margin: 12px 8px; padding: 12px; border: 1px solid var(--border-color,#ddd);
        border-radius: 10px; background: var(--card-bg,var(--control-bg,#fff)); }
      ${SELECTOR} .abit-agent-title { font-size: 13px; font-weight: 700; margin-bottom: 8px; }
      ${SELECTOR} .abit-agent-status { font-size: 12px; margin: 0 0 10px; word-break: break-word; }
      ${SELECTOR} .abit-agent-actions { display: flex; flex-wrap: wrap; gap: 6px; }
      ${SELECTOR} button, ${SELECTOR} a { border: 1px solid var(--border-color,#ccc); border-radius: 7px;
        padding: 7px 10px; background: var(--control-bg,#fff); color: var(--text-color,#222);
        font-size: 12px; text-align: center; text-decoration: none; cursor: pointer; flex: 1; min-width: 83px; }
      ${SELECTOR} button:disabled { cursor: not-allowed; opacity: .5; }
      ${SELECTOR} a:hover, ${SELECTOR} button:hover:not(:disabled) { border-color: var(--primary,#2490ef); }
      ${SELECTOR} button:focus-visible, ${SELECTOR} a:focus-visible { outline: 2px solid var(--primary,#2490ef); outline-offset: 2px; }
      ${SELECTOR} .abit-agent-help { margin: 8px 0 0; font-size: 11px; opacity: .75; }
    `;
    document.head.appendChild(tag);
  }
  function currentForm(frm) {
    return frm && frm.doctype === "Task" && frm.doc && !frm.is_new() &&
      state.name === frm.doc.name && frappe.get_route()[0] === "Form" &&
      frappe.get_route()[1] === "Task" && frappe.get_route()[2] === state.name;
  }
  function render(frm) {
    const side = sidebar(frm);
    if (!side) return;
    style();
    let panel = side.querySelector(SELECTOR);
    if (!panel) {
      panel = document.createElement("section");
      panel.className = SELECTOR.slice(1);
      panel.setAttribute("aria-label", "ABIT.AI Agent Controls");
      side.appendChild(panel);
    }
    const info = state.info || {};
    const taskStatus = String(frm.doc.custom_ai_relay_execution_status || "Not Started");
    const status = String(info.status || taskStatus);
    const connected = Boolean(info.connected);
    const available = connected && !state.busy;
    const canStart = available && info.can_start === true;
    const canStop = available && info.can_stop === true;
    const canRestart = available && info.can_restart === true;
    const agentUrl = safeLink(info.agent_url || frm.doc.custom_abit_ai_agent_url, "abit.ai", "agent");
    const abitUrl = safeLink(info.abit_chat_url || frm.doc.custom_ai_relay_conversation_url, "abit.ai", "abit");
    const chatgptUrl = safeLink(info.chatgpt_chat_url || frm.doc.custom_chatgpt_chat_url, "chatgpt.com", "chatgpt");
    const button = (action, label, enabled) =>
      `<button type="button" data-agent-action="${action}" ${enabled ? "" : "disabled"}>${label}</button>`;
    const link = (url, label) => url ?
      `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label}</a>` : "";
    panel.innerHTML =
      `<div class="abit-agent-title">ABIT.AI task agent</div>
      <div class="abit-agent-status" role="status" aria-live="polite"><strong>Status:</strong> ${esc(status)}${info.progress ? " · " + esc(info.progress) : ""}</div>
      <div class="abit-agent-actions">${button("start","▶ Start",canStart)}
      ${button("stop","■ Stop",canStop)}
      ${button("restart","↻ Restart",canRestart)}</div>
      <div class="abit-agent-actions" style="margin-top:8px">${link(abitUrl,"Open ABIT.AI chat")}
      ${link(chatgptUrl,"Open ChatGPT chat")}
      ${link(agentUrl,"Open agent")}</div>
      ${connected ? "" : '<p class="abit-agent-help">Live agent controls unavailable. Chat links remain accessible when recorded.</p>'}`;
    panel.querySelectorAll("[data-agent-action]").forEach(node => node.addEventListener("click", async () => {
      const action = node.dataset.agentAction;
      if (action === "stop" || action === "restart") {
        const confirmed = await new Promise(resolve => frappe.confirm(
          action === "stop" ? "Request cancellation of the active background agent?" :
          "Restart this agent only after the previous run is confirmed stopped?", () => resolve(true), () => resolve(false)));
        if (!confirmed) return;
      }
      await execute(frm, action);
    }));
  }

  async function api(name, action) {
    const response = await frappe.call({
      method: "abit_erp_agent_control",
      args: { task_id: name, action },
      freeze: action !== "status",
      freeze_message: action !== "status" ? "Contacting ABIT.AI agent..." : undefined
    });
    return response && response.message || {};
  }
  async function refresh(frm) {
    if (!currentForm(frm) || state.busy) return;
    const generation = ++state.request;
    try {
      const next = await api(state.name, "status");
      if (!currentForm(frm) || generation !== state.request) return;
      state.info = Object.assign({connected:true}, next);
    } catch (_) {
      if (generation !== state.request) return;
      state.info = {connected:false};
    }
    if (currentForm(frm)) render(frm);
  }
  async function execute(frm, action) {
    if (!currentForm(frm) || state.busy) return;
    const allowed = {start:"can_start",stop:"can_stop",restart:"can_restart"};
    if (!state.info || !state.info.connected || state.info[allowed[action]] !== true) return;
    state.busy = true; render(frm);
    try {
      const data = await api(state.name, action);
      if (!currentForm(frm)) return;
      const message = data.message || (action === "stop" ?
        "Cancellation requested. Confirmation may still be pending." : "Agent request accepted.");
      frappe.show_alert({message, indicator:"blue"});
    } catch (error) {
      frappe.msgprint({title:"Agent control failed", message:esc(error && (error.message || error.exc || error) || "Request failed"), indicator:"red"});
    } finally {
      state.busy = false;
      if (currentForm(frm)) await refresh(frm);
    }
  }
  function start(frm) {
    if (!frm || !frm.doc || frm.is_new() || !TASK_RE.test(frm.doc.name)) return;
    const changed = state.name !== frm.doc.name;
    if (changed) { state.name = frm.doc.name; state.info = null; ++state.request; }
    render(frm);
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(() => {
      if (!currentForm(frm)) { clearInterval(state.timer); state.timer = null; return; }
      refresh(frm);
    }, REFRESH_MS);
    refresh(frm);
  }
  frappe.ui.form.on("Task", { refresh(frm) { start(frm); } });
})();
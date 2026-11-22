/* ABIT-AGENT-BULK-DELETE-20261122: additive legacy-map adapter. Source branch abit-projects at 847e68ce5ab67884d289822acf1f119a28b27f1b; exact compiled input hashes in source-manifest.json. */
"use strict";
(() => {
  // src/lib/constants.ts
  var WEBUI_BASE_URL = "";
  var WEBUI_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1`;
  var OLLAMA_API_BASE_URL = `${WEBUI_BASE_URL}/ollama`;
  var OPENAI_API_BASE_URL = `${WEBUI_BASE_URL}/openai`;
  var AUDIO_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1/audio`;
  var IMAGES_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1/images`;
  var RETRIEVAL_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1/retrieval`;

  // src/lib/apis/automations/index.ts
  var deleteAutomationById = async (token2, id) => {
    let error = null;
    const res = await fetch(`${WEBUI_API_BASE_URL}/automations/${id}/delete`, {
      method: "DELETE",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        authorization: `Bearer ${token2}`
      }
    }).then(async (res2) => {
      if (!res2.ok) throw await res2.json();
      return res2.json();
    }).catch((err) => {
      error = err.detail;
      console.error(err);
      return null;
    });
    if (error) {
      throw error;
    }
    return res;
  };

  // src/lib/utils/automations/bulkDelete.ts
  async function deleteAgentsSequentially(targets, remove, options = {}) {
    const result = { deletedIds: [], failures: [] };
    const uniqueTargets = [...new Map(targets.map((target) => [target.id, { ...target }])).values()];
    for (const [index, target] of uniqueTargets.entries()) {
      if (options.shouldStop?.()) break;
      try {
        if (target.disabled || options.canDelete && !options.canDelete(target.id)) {
          throw new Error("This agent is no longer available for deletion. Refresh and review it.");
        }
        if (await remove(target.id) !== true) {
          throw new Error("Deletion was not confirmed. Refresh before retrying.");
        }
        result.deletedIds.push(target.id);
      } catch (error) {
        result.failures.push({
          ...target,
          error: error instanceof Error ? error.message : typeof error === "string" ? error : "Unable to delete this agent. Refresh before retrying."
        });
      }
      options.onProgress?.(index + 1);
    }
    return result;
  }
  function isAgentKnownRunning(agent) {
    return ["running", "handoff"].includes(agent.last_run?.status ?? "") || agent.meta?._agent_governance?.control?.state === "running";
  }

  // src/agent-bulk-delete/entry.ts
  var ROOT = '[data-testid="context-map"]';
  var OWN = "[data-abit-bulk-root]";
  var CARD = "article.agent-card";
  var NATIVE = '[data-testid="bulk-agent-actions"]:not([data-abit-bulk-root])';
  var SELECTABLE = /* @__PURE__ */ new Set(["idle", "stale", "disabled", "paused", "stopped", "completed", "failed"]);
  var BUSY_STATUSES = /* @__PURE__ */ new Set(["running", "pending", "queued", "handoff"]);
  var deletedIds = /* @__PURE__ */ new Set();
  var protectedIds = /* @__PURE__ */ new Set();
  var adapter = null;
  var framePending = false;
  function element(tag, className = "", text2 = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text2) node.textContent = text2;
    return node;
  }
  function text(node, value) {
    if (node.textContent !== value) node.textContent = value;
  }
  function identity(card) {
    const link = card.querySelector("a.agent-name");
    if (!link) return null;
    try {
      const url = new URL(link.getAttribute("href") || "", location.origin);
      const match = url.pathname.match(/^\/automations\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i);
      if (url.origin !== location.origin || url.search || url.hash || !match) return null;
      return { id: match[1], name: link.textContent?.trim() || "agent", card };
    } catch {
      return null;
    }
  }
  function atAgentMap() {
    return /^\/automations\/?$/.test(location.pathname);
  }
  function nativePresent(root) {
    return Boolean(root.querySelector(NATIVE));
  }
  function canSelect(agent) {
    return agent.card.isConnected && SELECTABLE.has(agent.card.getAttribute("data-agent-status") || "") && !deletedIds.has(agent.id) && !protectedIds.has(agent.id) && !agent.card.querySelector("[data-abit-card-delete][disabled], [data-abit-card-play][disabled]");
  }
  function token() {
    const value = localStorage.getItem("token");
    if (!value) throw new Error("Please sign in again.");
    return value;
  }
  async function readJson(path) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", authorization: "Bearer " + token() }
    });
    if (!response.ok) throw new Error(
      response.status === 409 ? "This agent is protected or managed through ERP." : "Agent status could not be verified (" + response.status + "). No deletion was submitted."
    );
    if (!/\bapplication\/(?:[\w.+-]*\+)?json\b/i.test(response.headers.get("content-type") || "")) {
      throw new Error("Unexpected server response. Refresh before retrying.");
    }
    return response.json();
  }
  function schedule() {
    if (!framePending) {
      framePending = true;
      requestAnimationFrame(reconcile);
    }
  }
  function reconcile() {
    framePending = false;
    const root = atAgentMap() ? document.querySelector(ROOT) : null;
    if (adapter && (adapter.root !== root || !root?.isConnected || nativePresent(root))) {
      adapter.destroy();
      adapter = null;
    }
    if (root && !nativePresent(root) && !adapter && root.querySelector(".map-toolbar")) {
      adapter = mount(root);
    }
    adapter?.refresh();
  }
  function mount(root) {
    const toolbar = root.querySelector(".map-toolbar");
    const panel = element("div", "abit-bulk-actions");
    panel.dataset.abitBulkRoot = "true";
    panel.dataset.testid = "bulk-agent-actions";
    panel.setAttribute("aria-label", "Bulk agent actions");
    const controls = element("div", "abit-bulk-controls");
    const selectLabel = element("label", "abit-bulk-select-all");
    const selectAll = element("input");
    selectAll.type = "checkbox";
    selectAll.dataset.testid = "bulk-agent-select-all";
    selectAll.setAttribute("aria-label", "Select all filtered");
    const selectText = element("span");
    selectLabel.append(selectAll, selectText);
    const count = element("span", "abit-bulk-count", "0 selected");
    count.dataset.testid = "bulk-agent-selected-count";
    count.setAttribute("role", "status");
    count.setAttribute("aria-live", "polite");
    const clear = element("button", "abit-bulk-clear", "Clear selection");
    clear.type = "button";
    clear.dataset.testid = "bulk-agent-clear";
    const remove = element("button", "abit-bulk-delete", "Delete selected (0)");
    remove.type = "button";
    remove.dataset.testid = "bulk-agent-delete";
    controls.append(selectLabel, count, clear, remove);
    const help = element(
      "p",
      "abit-bulk-help",
      "Select filtered agents to delete. Running, protected or unverified agents cannot be selected."
    );
    const result = element("p", "abit-bulk-result");
    result.dataset.testid = "bulk-agent-result";
    result.setAttribute("role", "status");
    result.tabIndex = -1;
    result.hidden = true;
    const failures = element("details", "abit-bulk-failures");
    failures.dataset.testid = "bulk-agent-failures";
    failures.hidden = true;
    failures.open = true;
    panel.append(controls, help, result, failures);
    toolbar.after(panel);
    const dialog = element("dialog", "abit-bulk-dialog");
    dialog.id = "abit-bulk-delete-confirm";
    dialog.setAttribute("aria-labelledby", "abit-bulk-delete-title");
    dialog.setAttribute("aria-describedby", "abit-bulk-delete-description");
    const heading = element("h2", "", "Delete selected agents?");
    heading.id = "abit-bulk-delete-title";
    const description = element("p");
    description.id = "abit-bulk-delete-description";
    const names = element("ul", "abit-bulk-targets");
    names.setAttribute("aria-label", "Agents selected for deletion");
    const warning = element("p", "", "Deleting an agent does not cancel work already sent to an external service.");
    const dialogActions = element("div", "abit-bulk-dialog-actions");
    const cancel = element("button", "abit-bulk-cancel", "Cancel");
    cancel.type = "button";
    cancel.autofocus = true;
    cancel.dataset.testid = "bulk-agent-cancel";
    const confirm = element("button", "abit-bulk-delete");
    confirm.type = "button";
    confirm.dataset.testid = "bulk-agent-confirm";
    dialogActions.append(cancel, confirm);
    dialog.append(heading, description, names, warning, dialogActions);
    document.body.append(dialog);
    let selected = /* @__PURE__ */ new Map();
    let pendingTargets = [];
    let eligible = /* @__PURE__ */ new Map();
    let busy = false;
    let destroyed = false;
    let previousScope = scope();
    let previousFocus = null;
    let refreshTimer;
    function scope() {
      return JSON.stringify([
        toolbar.querySelector('input[aria-label="Search agents"]')?.value || "",
        toolbar.querySelector('select[aria-label="Select context"]')?.value || "",
        toolbar.querySelector('select[aria-label="Stale threshold"]')?.value || ""
      ]);
    }
    function currentAgent(id) {
      for (const card of root.querySelectorAll(CARD)) {
        const agent = identity(card);
        if (agent?.id === id) return agent;
      }
      return null;
    }
    function shouldStop() {
      return destroyed || !root.isConnected || !atAgentMap() || nativePresent(root);
    }
    function available(id) {
      if (shouldStop()) return false;
      const agent = currentAgent(id);
      return Boolean(agent && canSelect(agent));
    }
    function clearSelection(clearResult = true) {
      if (busy) return;
      selected.clear();
      pendingTargets = [];
      if (dialog.open) dialog.close("cancel");
      if (clearResult) {
        result.hidden = true;
        failures.hidden = true;
      }
      update();
    }
    function update() {
      if (destroyed) return;
      const selectedCount = selected.size;
      const total = eligible.size;
      text(selectText, "Select all filtered (" + total + ")");
      text(count, selectedCount + " selected");
      selectAll.checked = total > 0 && selectedCount === total;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < total;
      selectAll.disabled = busy || total === 0;
      clear.disabled = busy || selectedCount === 0;
      remove.disabled = busy || selectedCount === 0;
      text(remove, busy ? "Deleting\u2026" : "Delete selected (" + selectedCount + ")");
      panel.setAttribute("aria-busy", String(busy));
      for (const card of root.querySelectorAll(CARD)) {
        const agent = identity(card);
        const checkbox = card.querySelector("input[data-abit-bulk-select]");
        if (!agent || !checkbox) continue;
        checkbox.checked = selected.has(agent.id);
        checkbox.disabled = busy || !canSelect(agent);
        card.classList.toggle("abit-bulk-selected", selected.has(agent.id));
      }
    }
    function refresh() {
      if (shouldStop()) return;
      const nextScope = scope();
      if (nextScope !== previousScope) {
        previousScope = nextScope;
        clearSelection();
      }
      eligible = /* @__PURE__ */ new Map();
      for (const card of root.querySelectorAll(CARD)) {
        const agent = identity(card);
        if (!agent) continue;
        if (deletedIds.has(agent.id)) card.setAttribute("data-abit-bulk-deleted", "true");
        if (canSelect(agent)) eligible.set(agent.id, agent);
        let checkbox = card.querySelector("input[data-abit-bulk-select]");
        if (!checkbox) {
          const label = element("label", "abit-bulk-card-selection");
          label.dataset.abitBulkSelectWrap = "true";
          checkbox = element("input");
          checkbox.type = "checkbox";
          checkbox.dataset.abitBulkSelect = "true";
          checkbox.dataset.testid = "bulk-agent-select";
          label.append(checkbox);
          card.append(label);
          card.classList.add("abit-bulk-has-select");
          for (const event of ["click", "keydown", "pointerdown"]) {
            label.addEventListener(event, (e) => e.stopPropagation());
          }
          label.addEventListener("dragstart", (e) => {
            e.preventDefault();
            e.stopPropagation();
          });
          checkbox.addEventListener("change", () => {
            const now = identity(card);
            if (!now || busy || !canSelect(now)) {
              update();
              return;
            }
            if (checkbox.checked) selected.set(now.id, now.name);
            else selected.delete(now.id);
            update();
          });
        }
        checkbox.setAttribute("aria-label", "Select agent " + agent.name);
        checkbox.title = canSelect(agent) ? "Select for bulk deletion" : "Agent is running, protected or awaiting verification";
      }
      if (!busy) {
        for (const id of selected.keys()) if (!eligible.has(id)) selected.delete(id);
      }
      update();
    }
    function filterChanged(event) {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (toolbar.contains(target) && target.matches("input, select")) clearSelection();
    }
    function metricChanged(event) {
      if (event.target instanceof Element && event.target.closest(".metrics button")) clearSelection();
    }
    root.addEventListener("input", filterChanged, true);
    root.addEventListener("change", filterChanged, true);
    root.addEventListener("click", metricChanged, true);
    selectAll.addEventListener("change", () => {
      if (busy) return;
      selected = selectAll.checked ? new Map([...eligible.values()].map((agent) => [agent.id, agent.name])) : /* @__PURE__ */ new Map();
      update();
    });
    clear.addEventListener("click", () => clearSelection());
    remove.addEventListener("click", () => {
      refresh();
      if (busy || !selected.size || shouldStop()) return;
      pendingTargets = Object.freeze(
        [...selected.keys()].filter((id) => eligible.has(id)).map((id) => Object.freeze({ id, name: eligible.get(id).name }))
      );
      if (!pendingTargets.length) return;
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      text(description, "Permanently delete " + pendingTargets.length + (pendingTargets.length === 1 ? " selected agent" : " selected agents") + " and their recorded run history? This cannot be undone.");
      text(confirm, "Delete " + pendingTargets.length + (pendingTargets.length === 1 ? " agent" : " agents"));
      names.replaceChildren(...pendingTargets.map((target) => element("li", "", target.name)));
      dialog.showModal();
      cancel.focus();
    });
    cancel.addEventListener("click", () => dialog.close("cancel"));
    dialog.addEventListener("cancel", () => {
      pendingTargets = [];
    });
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "delete") {
        pendingTargets = [];
        if (previousFocus?.isConnected && !previousFocus.hasAttribute("disabled")) previousFocus.focus();
      }
    });
    confirm.addEventListener("click", () => {
      if (busy || !pendingTargets.length || shouldStop()) return;
      const targets = pendingTargets;
      pendingTargets = [];
      dialog.close("delete");
      void deleteConfirmed(targets);
    });
    async function removeOne(id) {
      if (!available(id)) throw new Error("This agent is no longer available for deletion. Refresh and review it.");
      const item = await readJson("/api/v1/automations/" + encodeURIComponent(id));
      if (!item || item.id !== id || typeof item.name !== "string") {
        throw new Error("Agent identity could not be verified. No deletion was submitted.");
      }
      const meta = item.meta || {};
      if (meta.governance_managed === true || meta.erp_task && (meta.erp_managed === true || id.startsWith("erp-") && meta.erp_managed !== false)) {
        protectedIds.add(id);
        throw new Error("This agent is managed through ERP. Use its ERP project authorization.");
      }
      if (isAgentKnownRunning(item)) throw new Error("This agent has running or unconfirmed delegated work.");
      const runs = await readJson("/api/v1/automations/" + encodeURIComponent(id) + "/runs?limit=1");
      if (!Array.isArray(runs) || runs.some((run) => !run || typeof run.status !== "string")) {
        throw new Error("Run status could not be verified. No deletion was submitted.");
      }
      if (runs.some((run) => BUSY_STATUSES.has(run.status))) {
        throw new Error("This agent has running or unconfirmed delegated work.");
      }
      if (!available(id)) throw new Error("The page or agent status changed. No deletion was submitted.");
      const response = await deleteAutomationById(token(), id);
      if (response === true) {
        deletedIds.add(id);
        schedule();
      }
      return response;
    }
    function refreshNative(attempt = 0) {
      if (shouldStop()) return;
      const refreshButton = [...toolbar.querySelectorAll("button")].find((button) => button.textContent?.trim() === "Refresh");
      if (refreshButton && !refreshButton.disabled) refreshButton.click();
      else if (attempt < 20) refreshTimer = setTimeout(() => refreshNative(attempt + 1), 300);
    }
    async function deleteConfirmed(targets) {
      if (busy || shouldStop()) return;
      busy = true;
      result.hidden = false;
      failures.hidden = true;
      text(result, "Deleting 0 of " + targets.length + " agents\u2026");
      update();
      try {
        const outcome = await deleteAgentsSequentially(targets, removeOne, {
          canDelete: available,
          shouldStop,
          onProgress: (completed) => {
            if (!destroyed) text(result, "Deleting " + completed + " of " + targets.length + " agents\u2026");
          }
        });
        if (destroyed) return;
        selected = new Map(outcome.failures.filter((failure) => available(failure.id)).map((failure) => [failure.id, failure.name]));
        text(result, outcome.deletedIds.length + (outcome.deletedIds.length === 1 ? " agent deleted." : " agents deleted.") + (outcome.failures.length ? " " + outcome.failures.length + " deletions not confirmed. Review the failures before retrying." : ""));
        failures.replaceChildren();
        if (outcome.failures.length) {
          const summary = element("summary", "", "Deletion failures (" + outcome.failures.length + ")");
          const list = element("ul");
          for (const failure of outcome.failures) {
            list.append(element("li", "", failure.name + " \u2014 " + failure.error));
          }
          failures.append(summary, list);
          failures.hidden = false;
        }
      } finally {
        busy = false;
        if (!destroyed) {
          refresh();
          result.focus({ preventScroll: true });
          refreshNative();
        }
      }
    }
    function destroy() {
      if (destroyed) return;
      destroyed = true;
      selected.clear();
      pendingTargets = [];
      clearTimeout(refreshTimer);
      root.removeEventListener("input", filterChanged, true);
      root.removeEventListener("change", filterChanged, true);
      root.removeEventListener("click", metricChanged, true);
      if (dialog.open) dialog.close("cancel");
      dialog.remove();
      panel.remove();
      for (const node of root.querySelectorAll("[data-abit-bulk-select-wrap]")) node.remove();
      for (const card of root.querySelectorAll(CARD)) {
        card.classList.remove("abit-bulk-has-select", "abit-bulk-selected");
        card.removeAttribute("data-abit-bulk-deleted");
      }
    }
    return { root, get busy() {
      return busy;
    }, refresh, destroy };
  }
  function boot() {
    const style = element("style");
    style.id = "abit-bulk-delete-style-20261122";
    style.textContent = [
      "[data-abit-bulk-root]{box-sizing:border-box;margin:12px 0;padding:12px;border:1px solid #d1d5db;border-radius:12px;background:#f9fafb;color:#374151;font:13px/1.5 system-ui,sans-serif}",
      ".abit-bulk-controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}",
      ".abit-bulk-select-all{display:inline-flex;align-items:center;gap:9px;min-height:36px;cursor:pointer}",
      "[data-abit-bulk-root] input,.abit-bulk-card-selection input{width:18px;height:18px;accent-color:#6366f1}",
      "[data-abit-bulk-root] button,.abit-bulk-dialog button{min-height:36px;padding:7px 12px;border:0;border-radius:8px;font:600 13px/1.4 system-ui,sans-serif;cursor:pointer}",
      ".abit-bulk-clear,.abit-bulk-cancel{background:#e5e7eb;color:#1f2937}",
      ".abit-bulk-delete{background:#b91c1c;color:#fff}.abit-bulk-controls>.abit-bulk-delete{margin-inline-start:auto}",
      "[data-abit-bulk-root] button:disabled,[data-abit-bulk-root] input:disabled,.abit-bulk-card-selection input:disabled{opacity:.45;cursor:not-allowed}",
      "[data-abit-bulk-root] button:focus-visible,[data-abit-bulk-root] input:focus-visible,.abit-bulk-card-selection input:focus-visible,.abit-bulk-dialog button:focus-visible{outline:2px solid #6366f1;outline-offset:3px}",
      ".abit-bulk-help,.abit-bulk-result{margin:8px 0 0}.abit-bulk-help{font-size:12px;color:#6b7280}",
      ".abit-bulk-failures{margin-top:8px;color:#b91c1c;overflow-wrap:anywhere}.abit-bulk-failures ul{max-height:160px;overflow:auto;padding-inline-start:20px;list-style:disc}",
      "[data-abit-bulk-root] [hidden]{display:none!important}",
      '[data-testid="context-map"] article.agent-card[data-abit-bulk-deleted="true"]{display:none!important}',
      '[data-testid="context-map"] article.agent-card.abit-bulk-selected{outline:2px solid #6366f1;outline-offset:2px}',
      ".abit-bulk-has-select .agent-top{padding-inline-start:26px}",
      ".abit-bulk-card-selection{position:absolute;inset-inline-start:6px;top:7px;display:flex;align-items:center;justify-content:center;width:32px;height:32px;z-index:3;cursor:pointer}",
      ".abit-bulk-dialog{box-sizing:border-box;width:min(512px,calc(100vw - 24px));max-width:calc(100vw - 24px);max-height:85dvh;overflow:auto;margin:auto;padding:22px;border:1px solid #d1d5db;border-radius:20px;background:#fff;color:#374151;font:14px/1.55 system-ui,sans-serif}",
      ".abit-bulk-dialog::backdrop{background:#0009}.abit-bulk-dialog h2{font-size:18px;font-weight:600;margin:0 0 12px}.abit-bulk-dialog p{margin:10px 0}",
      ".abit-bulk-targets{max-height:28vh;overflow:auto;overflow-wrap:anywhere;border:1px solid #d1d5db;border-radius:8px;padding:10px 10px 10px 28px;list-style:disc}",
      ".abit-bulk-dialog-actions{display:flex;gap:10px;margin-top:18px}.abit-bulk-dialog-actions button{flex:1;min-width:0}",
      ".dark [data-abit-bulk-root]{background:#181d27;border-color:#374151;color:#e5e7eb}.dark .abit-bulk-help{color:#9ca3af}.dark .abit-bulk-failures{color:#fca5a5}",
      ".dark .abit-bulk-dialog{background:#111827;color:#e5e7eb;border-color:#374151}.dark .abit-bulk-targets{border-color:#374151}.dark .abit-bulk-clear,.dark .abit-bulk-cancel{background:#374151;color:#f3f4f6}",
      "@media(max-width:480px){.abit-bulk-controls{gap:8px}.abit-bulk-select-all{flex-basis:100%}.abit-bulk-controls>.abit-bulk-delete{margin-inline-start:0}.abit-bulk-dialog{padding:18px}}"
    ].join("\n");
    document.head.append(style);
    new MutationObserver(schedule).observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-agent-status", "href"]
    });
    const blockDuringBatch = (event) => {
      if (!adapter?.busy || !(event.target instanceof Element)) return;
      if (event.target.closest(OWN + ",#abit-bulk-delete-confirm")) return;
      if (event.type === "submit" || event.target.closest('a,button,input,select,textarea,[role="button"]')) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    document.addEventListener("click", blockDuringBatch, true);
    document.addEventListener("submit", blockDuringBatch, true);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") blockDuringBatch(event);
    }, true);
    window.addEventListener("beforeunload", (event) => {
      if (adapter?.busy) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
    window.addEventListener("popstate", schedule);
    reconcile();
  }
  if (!window.__abitAgentBulkDelete20261122) {
    window.__abitAgentBulkDelete20261122 = true;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
    else boot();
  }
})();

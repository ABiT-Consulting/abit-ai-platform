/* Read-only activity presentation. Execution and task mutations remain on server APIs. */
(() => {
  "use strict";
  if (window.__abitActivityV1) return;
  window.__abitActivityV1 = true;
  let route = "", busy = false, generation = 0;
  const idFromPath = () => {
    const match = location.pathname.match(/^\/automations\/([a-f0-9-]{36})\/?$/i);
    return match ? match[1] : "";
  };
  const text = (value) => String(value || "").replace(/<[^>]*>/g, " ").replace(/\\n/g, "\n").trim();
  function describe(run) {
    const state = String(run?.status || "unknown").toLowerCase();
    const labels = {running:"Running",queued:"Queued",pending:"Queued",success:"Run finished",error:"Failed",failed:"Failed",deferred:"Waiting",cancelled:"Stopped",canceled:"Stopped",blocked:"Blocked"};
    const stale = state === "running" && typeof run?.created_at === "number" && Date.now()-run.created_at/1e6 > 3600000 && !(run.lease_until/1e6 > Date.now());
    let reason = text(run?.error).replace(/(bearer\s+)[\w.~-]+/gi, "$1[redacted]").replace(/((?:token|password|secret|api_key)\s*[:=]\s*)\S+/gi, "$1[redacted]");
    const defaults = {
      deferred:"This attempt was postponed. It did not start a new execution.",
      error:"The run ended with an error, but no explanation was saved.",
      failed:"The run ended with an error, but no explanation was saved.",
      success:"The run finished. Task completion still requires its review and acceptance checks.",
      running:"A background run is in progress.",
      queued:"The run is waiting to start.",
      pending:"The run is waiting to start.",
      blocked:"The run cannot continue until its blocker is resolved."
    };
    return {
      label: stale ? "Needs status check" : labels[state] || "Status unconfirmed",
      reason: (stale ? "The run record still says Running, but recent execution could not be confirmed. Reconcile this run before retrying." : reason || defaults[state] || "A reliable execution status is not available.").slice(0,350),
      next: stale || ["deferred","error","failed","blocked"].includes(state)
        ? "Review the previous run and resolve the blocker before retrying. Do not start a duplicate."
        : state === "success" ? "Check the linked ERP task for the next step or independent review."
        : ["running","queued","pending"].includes(state) ? "Wait for the next saved update."
        : "Refresh status before starting another run.",
      tone: ["error","failed","blocked"].includes(state) ? "#ef4444" : state === "success" ? "#22c55e" : "#d99a21"
    };
  }
  function node(tag, value, cls) {
    const n = document.createElement(tag);
    if (value) n.textContent = value;
    if (cls) n.className = cls;
    return n;
  }
  function render(id, runs, automation) {
    if (id !== idFromPath()) return;
    const heading = Array.from(document.querySelectorAll("div")).find(n => n.children.length === 0 && n.textContent?.trim() === "Runs");
    if (!heading) return;
    let panel = document.getElementById("abit-agent-activity");
    if (!panel) {
      panel = node("section"); panel.id = "abit-agent-activity";
      panel.setAttribute("aria-label","Agent activity");panel.setAttribute("aria-live","polite");
      panel.style.cssText = "margin:12px 16px;padding:16px;border:1px solid #7776;border-radius:10px;font:13px/1.6 system-ui;max-width:1000px";
      heading.parentElement.insertBefore(panel,heading);
    }
    panel.replaceChildren();
    const latest = runs[0], info = describe(latest);
    const title = node("strong", info.label);title.style.color=info.tone;panel.append(title);
    panel.append(node("p",info.reason),node("p","Next action: "+info.next));
    if (latest?.created_at) panel.append(node("small","Last update: "+new Date(latest.created_at/1e6).toLocaleString()));
    const details=node("details");details.style.marginTop="10px";details.append(node("summary","Recent run history and technical details"));
    for (const run of runs.slice(0,20)) {
      const d=describe(run),row=node("div");row.style.cssText="margin:8px 0;padding:8px 0;border-top:1px solid #7774;white-space:pre-wrap;overflow-wrap:anywhere";
      row.append(node("strong",d.label+" · "+new Date(run.created_at/1e6).toLocaleString()),node("p",d.reason));
      if (run.error) { const full = node("pre", text(run.error).replace(/(bearer\s+)[\w.~-]+/gi,"$1[redacted]").replace(/((?:token|password|secret|api_key)\s*[:=]\s*)\S+/gi,"$1[redacted]").slice(0,12000)); full.style.cssText="white-space:pre-wrap;font:12px/1.5 monospace"; row.append(full); }
      if (run.chat_id && /^[a-z0-9-]{1,100}$/i.test(run.chat_id)) {
        const a=node("a","Open saved conversation");a.href="/c/"+encodeURIComponent(run.chat_id);a.style.textDecoration="underline";row.append(a);
      }
      details.append(row);
    }
    panel.append(details);
    const originalHistory = heading.nextElementSibling;
    if (originalHistory) {
      heading.hidden=true; originalHistory.hidden=true;
      const showAll=node("button","Show original full run history");showAll.type="button";showAll.style.cssText="display:block;margin-top:8px;text-decoration:underline;font:inherit";
      showAll.addEventListener("click",()=>{originalHistory.hidden=!originalHistory.hidden;heading.hidden=originalHistory.hidden;});details.append(showAll);
    }
    const taskId = String(automation?.name || "").match(/\bTASK-[A-Za-z0-9-]{1,64}\b/)?.[0];
    if (taskId) { const link = node("a", "Open ERP task and next action"); link.href="https://erp.abit.ai/desk/task/"+encodeURIComponent(taskId); link.target="_blank"; link.rel="noopener noreferrer"; link.style.textDecoration="underline"; panel.append(link); }
    // Scheduling permission is separate from a running execution.
    const schedulingLabel = Array.from(document.querySelectorAll("span")).find(n => n.children.length===0 && n.textContent?.trim()==="Status" && n.classList.contains("w-24"));
    if (schedulingLabel && typeof automation?.is_active === "boolean") {
      const value = schedulingLabel.nextElementSibling;
      if (value) { schedulingLabel.textContent="Scheduling"; value.replaceChildren(node("span", automation.meta?.governance_managed ? "API controlled" : automation.is_active ? "Enabled" : "Paused")); value.title="Scheduling permission is separate from the execution status shown below."; }
    }
    // Keep the original audit history intact, but place the human explanation first.
    const promptHeading=Array.from(document.querySelectorAll("div")).find(n=>n.children.length===0&&n.textContent?.trim()==="Prompt");
    if (promptHeading && !document.getElementById("abit-agent-prompt-details")) {
      const original=promptHeading.parentElement;
      const fold=node("details");fold.id="abit-agent-prompt-details";fold.style.margin="12px 16px";
      fold.append(node("summary","Agent instructions (technical details)"));
      original.parentElement.insertBefore(fold,original);fold.append(original);
    }
  }
  async function refresh() {
    const id=idFromPath();
    if(id!==route){route=id;generation++;}
    if(!id||busy||document.hidden)return;
    const token=localStorage.getItem("token");
    if(!token)return;
    const version=generation;busy=true;
    try{
      const options={headers:{Authorization:"Bearer "+token},credentials:"same-origin"};
      const [response, metadata]=await Promise.all([fetch("/api/v1/automations/"+id+"/runs?skip=0&limit=20",options),fetch("/api/v1/automations/"+id,options)]);
      if(!response.ok||!metadata.ok)return;
      const [runs, automation]=await Promise.all([response.json(),metadata.json()]);
      if(version===generation&&Array.isArray(runs)&&automation.id===id)render(id,runs,automation);
    }catch(_){/* Never convert a read failure into a false execution state. */}
    finally{busy=false;}
  }
  window.__abitActivityDescribe=describe;
  setInterval(refresh,15000);
  document.addEventListener("visibilitychange",refresh);
  window.addEventListener("popstate",refresh);
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",refresh,{once:true});else refresh();
})();

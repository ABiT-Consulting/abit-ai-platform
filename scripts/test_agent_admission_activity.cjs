"use strict";
// Exact-source unit execution plus explicitly structural render checks.
// No browser, network, live account, refresh callback or DOM implementation.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
function option(name, fallback) {
  const i = args.indexOf(name);
  return i < 0 ? fallback : args[i + 1];
}
const sourcePath = path.resolve(root, option("--source", "static/agent-activity.js"));
const outputPath = path.resolve(root, option("--evidence",
  "deployment/linkedin-admission-20261122/evidence/agent-admission-activity-v1.json"));
const expectedSha = option("--expected-sha",
  "2e8dda70a9e1d033a71e94b21c3d76fd3473cdf960a47cbf31ff872e542cb78e");
if (fs.existsSync(outputPath)) throw new Error("Evidence already exists; choose a new --evidence path.");
const raw = fs.readFileSync(sourcePath);
const source = raw.toString("utf8");
const sourceSha = crypto.createHash("sha256").update(raw).digest("hex");
const results = [];
function check(kind, name, fn) {
  try {
    fn();
    results.push({kind, name, status: "passed"});
  } catch (error) {
    results.push({kind, name, status: "failed", error: String(error?.message || error)});
  }
}

const registered = [], intervals = [];
let networkCalls = 0, sessionReads = 0;
const context = vm.createContext({
  window: {addEventListener: (name, handler) => registered.push(["window", name, handler])},
  document: {
    readyState: "loading",
    hidden: false,
    addEventListener: (name, handler) => registered.push(["document", name, handler]),
  },
  location: {pathname: "/automations/11111111-1111-4111-8111-111111111111"},
  localStorage: {getItem: () => {sessionReads++; throw new Error("No session access allowed");}},
  fetch: () => {networkCalls++; throw new Error("No network allowed");},
  setInterval: (handler, milliseconds) => {intervals.push([handler, milliseconds]); return 1;},
});
check("unit", "source matches the reviewed frozen artifact", () => assert.equal(sourceSha, expectedSha));
check("unit", "exact script parses and initializes without refresh or network", () => {
  new vm.Script(source, {filename: sourcePath}).runInContext(context, {timeout: 1000});
  assert.equal(networkCalls, 0);
  assert.equal(sessionReads, 0);
  assert.equal(typeof context.window.__abitActivityDescribe, "function");
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0][1], 15000);
  assert.ok(registered.some(([, name]) => name === "DOMContentLoaded"));
});
const describe = context.window.__abitActivityDescribe;
if (typeof describe === "function") {
  check("unit", "historical exact admission error is Waiting for admission", () => {
    const result = describe({status: "error", error: "Maintenance admission denied before execution; original work retained"});
    assert.equal(result.label, "Waiting for admission");
    assert.match(result.next, /admission reason.*original-work evidence/i);
    assert.notEqual(result.tone, "#ef4444");
  });
  check("unit", "new admission_denied record is distinct from execution failure", () => {
    const result = describe({status: "admission_denied", error: "[original_unresolved] Original retained."});
    assert.equal(result.label, "Waiting for admission");
    assert.match(result.reason, /original_unresolved/);
  });
  check("unit", "admission_denied without message explicitly says no worker started", () => {
    const result = describe({status: "admission_denied"});
    assert.match(result.reason, /No new worker was started/);
  });
  check("unit", "executed error remains Failed", () => {
    const result = describe({status: "error", error: "Provider returned an execution error."});
    assert.equal(result.label, "Failed");
    assert.equal(result.tone, "#ef4444");
  });
  check("unit", "similar text does not reclassify an arbitrary execution error", () => {
    const result = describe({status: "error", error: "Provider said: Maintenance admission denied before execution; original work retained"});
    assert.equal(result.label, "Failed");
  });
  check("unit", "success means run finished and retains review requirement", () => {
    const result = describe({status: "success"});
    assert.equal(result.label, "Run finished");
    assert.match(result.reason, /Task completion still requires.*review and acceptance/);
    assert.doesNotMatch(result.label, /accepted|task complete/i);
  });
  check("unit", "bearer credential is redacted from displayed reason", () => {
    const result = describe({status: "error", error: "Failure with Bearer fixture-private-token.abc"});
    assert.ok(!result.reason.includes("fixture-private-token"));
    assert.ok(result.reason.includes("[redacted]"));
  });
  for (const name of ["token", "password", "secret", "api_key"]) {
    check("unit", name + " value is redacted from displayed reason", () => {
      const result = describe({status: "error", error: name + "=fixture-confidential-value"});
      assert.ok(!result.reason.includes("fixture-confidential-value"));
      assert.ok(result.reason.includes("[redacted]"));
    });
  }
  check("unit", "displayed reason remains bounded", () => {
    const result = describe({status: "error", error: "x".repeat(5000)});
    assert.equal(result.reason.length, 350);
  });
  check("unit", "HTML-like diagnostics are displayed as plain text", () => {
    const result = describe({status: "error", error: "<strong>Diagnostic</strong>"});
    assert.equal(result.reason, "Diagnostic");
  });
  check("unit", "unknown record remains unconfirmed", () => {
    assert.equal(describe({status: "unexpected"}).label, "Status unconfirmed");
  });
}
check("unit", "no session, network or registered refresh was executed", () => {
  assert.equal(networkCalls, 0);
  assert.equal(sessionReads, 0);
});

// No DOM library is available in this checkout. These checks verify source
// structure only; they do not claim rendering, click behavior or browser QA.
check("structural", "governed agent excludes ordinary admission before state override", () => {
  const exclusion = source.indexOf("admission = automation?.meta?.governance_managed ? null : admission;");
  const projection = source.indexOf("const blocked = admission");
  assert.ok(exclusion >= 0 && projection > exclusion);
});
check("structural", "original run anchor is guarded by a loaded matching row", () => {
  assert.ok(source.includes("const loaded = runs.some(run => run.id === admission.original_run_id);"));
  assert.ok(source.includes('const original=node(loaded ? "a" : "p",'));
  assert.ok(source.indexOf('if (loaded) {') < source.indexOf('original.href='));
});
check("structural", "click handler opens the original run details", () => {
  assert.ok(source.includes('original.addEventListener("click",'));
  assert.ok(source.includes('row?.closest("details")'));
  assert.ok(source.includes("if(history)history.open=true"));
});
check("structural", "saved conversation link has a same-origin constrained guard", () => {
  const start = source.indexOf('if (typeof admission.original_chat_url === "string"');
  const end = source.indexOf('output.href=admission.original_chat_url', start);
  assert.ok(start >= 0 && end > start);
  const guard = source.slice(start, end);
  assert.ok(guard.includes(".test(admission.original_chat_url)"));
  assert.ok(guard.includes("[a-z0-9-]{1,100}$"));
});
check("structural", "new status fetch is readonly and unavailable status remains blocked", () => {
  assert.ok(source.includes('id+"/admission",options)'));
  assert.ok(source.includes('code:"admission_unavailable"'));
  assert.ok(!/method\s*:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i.test(source));
});
check("structural", "presentation uses textContent and no HTML assignment", () => {
  assert.ok(source.includes("n.textContent = value"));
  assert.ok(!source.includes(".innerHTML"));
});

const counts = {};
for (const kind of ["unit", "structural"]) {
  const cases = results.filter(r => r.kind === kind);
  counts[kind] = {passed: cases.filter(r => r.status === "passed").length,
    failed: cases.filter(r => r.status === "failed").length, total: cases.length};
}
const evidence = {
  schema_version: 1, verification: "exact-source Node VM unit tests and structural checks",
  observed_at_utc: new Date().toISOString(), source_path: sourcePath, source_sha256: sourceSha,
  expected_source_sha256: expectedSha, node_version: process.version,
  browser_tested: false, dom_rendering_tested: false, network_calls: networkCalls,
  live_session_reads: sessionReads, refresh_callbacks_executed: 0,
  counts, results,
};
fs.mkdirSync(path.dirname(outputPath), {recursive: true});
fs.writeFileSync(outputPath, JSON.stringify(evidence, null, 2) + "\n", {flag: "wx"});
console.log(JSON.stringify({source_sha256: sourceSha, evidence: outputPath, counts,
  browser_tested: false, failures: results.filter(r => r.status === "failed")}, null, 2));
if (results.some(r => r.status === "failed")) process.exitCode = 1;

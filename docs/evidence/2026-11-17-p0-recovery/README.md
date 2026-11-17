# P0 autonomous project management recovery — 17 November 2026

This is implemented source and validation evidence. No shared ABIT.AI or production ERP source was deployed by this recovery. A fresh real-agent Developer → independent QA → rework → ERP acceptance cycle is not yet established for these commits.

## Current root causes and observations

The governed project path does not use the legacy task scheduler marker fields. `ff98a46` uses a project lock, current authorization/ownership/dependency checks, durable adoption and signed outbox Start. The legacy scheduler exits early for governed projects. Fresh native reads (Azure read-only releases 7677 and 7678) found 364 acknowledged outbox rows for PROJ-0196 and seven for PROJ-0201; the authenticated Agentic API reports its fixed ERP channel connected and its single reconciliation fallback at 1800 seconds.

PROJ-0196 already automatically adopted TASK-2026-05760 and invoked its project PM. Its original run `8518ddc8-3247-514f-9b7a-92b6088c80cc` is now uncertain with the reason “Original relay delivery unresolved; no replay.” ERP still shows the Task Overdue and has only the original run.started receipt. The missing durable native failure observation is a confirmed visibility defect.

PROJ-0201 currently has 35 tasks: the original 33-task backlog and two architecture anchors. It has 76 unique native dependency edges, and the new complete-graph validator passes on the actual snapshot. Its first architect Task TASK-2026-06374 published a Blocked result after repository preparation/read returned HTTP 424 for an unregistered repository/environment. The profile was registered later. The newer anchor TASK-2026-06375 is queued with zero submission attempts, “Waiting for earlier project task.” Legacy uncertain tool actions retain their original fence; later registration does not settle them.

Hosted GitHub Agentic Governance and API-contract failures were zero-step, runner_id=0 billing-lock failures, not executed test failures. Existing billing issue ABiT-Consulting/abit-ai-agents#20 is assigned to the active organization admin @abitcons. Workflow setup also needed isolated module processes, missing pinned dependencies, and a delegated unprivileged namespace test environment.

## Implemented changes

- ERP: exact `is_active = Yes` eligibility, server-owned project concurrency (default one, bounded 1–8), task-slot accounting, additive legacy-field detection, exact original default-policy packet compatibility, and signed idempotent execution observations. Observation receipts cannot complete work, replace a newer run, overwrite newer human business inputs/ownership, or release an uncertain lease.
- ABIT.AI: both eligibility gates independently require `is_active = Yes`; full dependency/hierarchy graph validation, priority/due-date queue ordering, atomic bounded project slots retaining the task through QA and PM review, original-ID wakeups, and durable native failure-observation retries using the existing recovery loop.
- Transport: packet and handoff payloads over 24,000 UTF-8 bytes use exact immutable references and require the admitted `context.read` grant. That API returns the complete original packet, handoff, hashes and run identity. Small packets retain their existing format. The actual 122-task regression verifies one outgoing original and exact replay/context bytes.
- Gateway: trusted pure repository-registration rejection produces an audited completed receipt declaring rejected/no effects. Handler failures and historical uncertain calls remain fenced. Long repository tests retain their original job/receipt.
- Recovery: existing SQLite lock-preservation and guarded external-human-completion retirement work is retained. Newer strict contract rejection, artifact-publication order, delivery-control and original-result recovery guards were restored after stale dirty overlays were detected. No absent retry-module imports remain.
- Contracts and API: native policy and observation schemas, channel allowlists at both edges, route documentation, generated API verification, and canonical contract synchronization.

Existing work was preserved in the original development branches and private source snapshots. No competing scheduler was added, and no uncertain external action was replayed.

## Validation and release boundary

ERP branch `fix/pm-autonomous-pickup-20261117` contains commits c27d4e4, ce9494c and 235769e, reviewed in [ERP PR #20](https://github.com/ABiT-Consulting/AI_ERPNEXT/pull/20). Its 35 isolated native test modules passed 449 tests; compile and whitespace checks passed. Twenty checks also passed against actual Frappe 16.17.2 in the existing nonproduction canary using a process-only source overlay. The complete transaction was rolled back, and fixture Project, Task, governance record, binding, ToDo and Comment counts remained unchanged. These native checks exercised ERP behavior only; the real-agent acceptance test remains pending.

Backend validation covers **894 cases across 65 modules** in aggregate:757 primary cases (the initial752-case full scan plus2 bounded-transport and3 active-project additions) and137 existing delivery/readiness/format guards. Initial obsolete outer-success assertions were replaced by stronger no-success assertions; the affected11-case and4-case modules passed their complete reruns. Intentional bounded transport was validated across6 focus modules, with a final41/41 dispatch/gateway rerun after correcting one new fixture capability setup. The final active-project gate passed31/31. Original failed receipts and exact rerun/source boundaries are retained in `validation.json` and `logs/`; this is not a claim of one clean65-module invocation.

Fresh final API checks passed13 source-guard,39 backend and37 frontend tests, zero Svelte errors/warnings, and91 isolated full-application checks. All 6 vendored and referenced canonical-source hashes were independently verified; local extensions are explicit. Canonical contract source edcb4f4 passed 120 checks and is reviewed in [canonical PR27](https://github.com/ABiT-Consulting/abit-ai-agents/pull/27). The reviewed CI workflow patch selects all 65 modules with exact focused dependency pins and an unprivileged delegated sandbox.

The initial 406-file full-scan manifest changed only in canonical provenance metadata during that run. Later intentional transport/policy and fixture changes are listed explicitly in `source-manifest.json`; its final 419-file freeze has zero drift. `live-status.json` is a bounded authenticated read-only snapshot; full native business-data snapshots remain private on ABIT-DEV-LINUX.

## Acceptance state

| Requirement | Evidence/state |
| --- | --- |
| Project eligibility and project-specific agents | Both switches, active status, `_assign`, open ToDo and six distinct role bindings verified on existing projects |
| Automatic PM pickup | Observed on PROJ-0196; later original outcome is uncertain |
| Current real dependency graph | PROJ-0201 actual 35 tasks / 76 links passes candidate validation |
| New concurrency, callback idempotence and original-run preservation | Twenty native nonproduction checks passed |
| Historical real Developer/QA/PM | PROJ-0198 TASK-2026-06200 is QA APPROVED with verified QA and completed PM review in the API history |
| Fresh complete P0 Developer → QA rejection/rework → ERP → next task cycle | Not verified for this source release |
| Production activation | Pending explicit required approval; no source deployment performed |

Muhammad Imran (`mimran@abitcons.com`, the native control owner and recovery Task owner) must authorize the shared-service deployment. The platform/operations owner must reconcile exact historical uncertain provider/tool receipts without replacing or blindly replaying them. GitHub hosted CI billing is owned by @abitcons. Existing implementation tracking remains In Progress.

## GitHub workflow publication blocker

GitHub rejected the complete reviewed commit because the OAuth credential lacks `workflow` scope. The application and test changes are published separately. `workflow-reviewed.yaml` contains the exact tested workflow; `workflow-update.patch.gz` preserves its exact patch. Its source hash in the test manifest describes this reviewed artifact, not a published workflow change. Local commit `ebcef3030f394aab95a77ac11b0501a44cedb6ed` and branch `fix/p0-reviewed-workflow-preserved-20261117` retain the complete original reviewed tree. GitHub admin @abitcons must authorize workflow-file writes or apply this exact patch with an authorized identity. Hosted Actions billing remains a separate blocker.

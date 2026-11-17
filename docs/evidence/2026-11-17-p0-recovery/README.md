# P0 autonomous project management recovery — 17 November 2026

This is implemented source and validation evidence. No shared ABIT.AI or production ERP source was deployed by this recovery. A fresh real-agent Developer → independent QA → rework → ERP acceptance cycle is not yet established for these commits.

## Current root causes and observations

The governed project path does not use the legacy task scheduler marker fields. `ff98a46` uses a project lock, current authorization/ownership/dependency checks, durable adoption and signed outbox Start. The legacy scheduler exits early for governed projects. Fresh native reads (Azure read-only releases 7677 and 7678) found 364 acknowledged outbox rows for PROJ-0196 and seven for PROJ-0201; the authenticated Agentic API reports its fixed ERP channel connected and its single reconciliation fallback at 1800 seconds.

PROJ-0196 already automatically adopted TASK-2026-05760 and invoked its project PM. Its original run `8518ddc8-3247-514f-9b7a-92b6088c80cc` is now uncertain with the reason “Original relay delivery unresolved; no replay.” ERP still shows the Task Overdue and has only the original run.started receipt. The missing durable native failure observation is a confirmed visibility defect.

The earlier PROJ-0201 snapshot had 35 tasks (the original 33-task backlog and two architecture anchors) and 76 unique dependency edges; candidate graph validation passed on that complete snapshot. The latest native read-only release 7683 observes 36 tasks, 76 dependency links and SRS revision 4, including TASK-2026-06379. The current 36-row graph has not yet been atomically exported and revalidated. In the earlier snapshot, architect Task TASK-2026-06374 had published a Blocked result after repository preparation/read returned HTTP 424 for an unregistered repository/environment, and TASK-2026-06375 was queued with zero submission attempts. Later profile registration does not settle historical uncertain tool actions; their original fences remain.

The authenticated read-only API snapshot at21:48:31UTC shows TASK-2026-06375 blocked before any submission (zero attempts, no run): ERP409 requires explicit PM replanning because the Task SRS revision changed. TASK-2026-06379 remains queued with zero attempts, waiting for earlier project work; TASK-2026-06374 remains execution_finished. PROJ-0196 original8518ddc8 remains uncertain with no replay. Historical PROJ-0198 QA verification and PM completion remain visible. Health was ready and the ERP channel connected. This read invoked no event, retry, resume, model or source deployment.

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

ERP branch `fix/pm-autonomous-pickup-20261117` contains commits c27d4e4, ce9494c, 235769e and c479b8b, reviewed in [ERP PR #20](https://github.com/ABiT-Consulting/AI_ERPNEXT/pull/20). Its 35 isolated native test modules passed 449 tests; compile and whitespace checks passed. Twenty-two checks also passed against actual Frappe 16.17.2 in the existing nonproduction canary using a process-only source overlay. The complete transaction was rolled back, and fixture Project, Task, governance record, binding, ToDo and Comment counts remained unchanged. These native checks exercised ERP behavior only; the real-agent acceptance test remains pending.

The final frozen backend run passed **915 unittest executions across 66 modules** in one terminal invocation, with no source drift across 438 hashed files. Two separate QA preflight modules passed another 9 executions. Each governance module ran in a fresh Python process in the delegated unprivileged sandbox; no tests were skipped.

The current recovery changes preserve the original Start identity while accepting only the exact immutable native observation-owned Task.modified chain; paginate actionable recovery and pending failure observations under fixed snapshots; reject changes to the current Task's ownership fingerprint even when Task.modified is unchanged; and ignore only the protocol timestamps/MAC when comparing unchanged physical QA host readbacks. Deterministic regressions retain human-edit, uncertain-action, artifact-integrity, lease, Stop, original receipt and actual host-state rejection checks.

Original published validation was an 894-case aggregate across 65 modules. Later interrupted scans, the mixed-source 909-execution run, the 914-execution failed timestamp/planning scan and the inherited-test discovery failure remain explicitly nonpassing in `validation.json` and `logs/`. The final pass is a fresh run after those corrections; stopped shell exit 0 is never counted as a test pass.

Fresh final API checks passed 13 source-guard, 39 backend and 37 frontend tests, zero Svelte errors/warnings, and 91 isolated full-application checks. All 6 actual vendored and exact referenced canonical-source hashes were independently reverified; local extensions remain explicit. Canonical contract source edcb4f4 passed 120 checks and is reviewed in [canonical PR27](https://github.com/ABiT-Consulting/abit-ai-agents/pull/27). The reviewed CI workflow dynamically selects all 66 modules with exact focused dependency pins and an unprivileged delegated sandbox; its source is preserved but unpublished.

Twenty-six separate checks against actual Frappe 16.17.2 established that normal PM→Developer→QA→PM AI-role transitions preserve the ownership fingerprint, while a new human assignee or accountable Developer revocation changes it even without Task.modified changing. The proof transaction was rolled back and fixture counts restored. These checks supplement the 22 native canary checks and 449 native unit tests; they do not establish a fresh full model lifecycle.

The initial 406-file scan's provenance-only metadata drift and later intentional changes remain in `source-manifest.json`; the latest final 438-file freeze has zero drift. `live-status.json` retains the earlier bounded read-only snapshot; `live-status-final.json` contains the bounded authenticated21:48:31UTC history/health read, including06375 blocked before submission for an SRS revision mismatch. Neither is a fresh graph export or full lifecycle acceptance test. Latest known hosted head 8141b67 runs 44171725312 (Governance) and 44171724988 (API) failed before runner allocation, with runner_id 0 and zero steps, due to account billing lock. No workflow-write scope was gained. Full native business-data snapshots remain private on ABIT-DEV-LINUX.

## Acceptance state

| Requirement | Evidence/state |
| --- | --- |
| Project eligibility and project-specific agents | Both switches, active status, `_assign`, open ToDo and six distinct role bindings verified on existing projects |
| Automatic PM pickup | Observed on PROJ-0196; later original outcome is uncertain |
| Native graph observations and validation boundary | Latest 7683: 36 tasks / 76 links / SRS4; earlier complete 35-task snapshot passed graph validation. Latest 36-row graph has not been revalidated. |
| New concurrency, callback idempotence and original-run preservation | Twenty-two native canary checks and26 separate ownership checks passed |
| Historical real Developer/QA/PM | PROJ-0198 TASK-2026-06200 is QA APPROVED with verified QA and completed PM review in the API history |
| Fresh complete P0 Developer → QA rejection/rework → ERP → next task cycle | Not verified for this source release |
| Production activation | Pending explicit required approval; no source deployment performed |

Muhammad Imran (`mimran@abitcons.com`, the native control owner and recovery Task owner) must authorize the shared-service deployment. The platform/operations owner must reconcile exact historical uncertain provider/tool receipts without replacing or blindly replaying them. GitHub hosted CI billing is owned by @abitcons. Existing implementation tracking remains In Progress.

## GitHub workflow publication blocker

GitHub rejected the complete reviewed commit because the OAuth credential lacks `workflow` scope. The application and test changes are published separately. `workflow-reviewed.yaml` contains the exact tested workflow; `workflow-update.patch.gz` preserves its exact patch. Its source hash in the test manifest describes this reviewed artifact, not a published workflow change. Local commit `ebcef3030f394aab95a77ac11b0501a44cedb6ed` and branch `fix/p0-reviewed-workflow-preserved-20261117` retain the complete original reviewed tree. GitHub admin @abitcons must authorize workflow-file writes or apply this exact patch with an authorized identity. Hosted Actions billing remains a separate blocker.

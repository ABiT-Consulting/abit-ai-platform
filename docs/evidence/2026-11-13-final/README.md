# Final candidate verification — 13 November 2026

The hardened source and isolated native ERP canary passed. **The full platform is not merged or deployed to production.** Hosted GitHub Actions is waived by the user because of the billing lock; no hosted green status is claimed.

- Platform code: `d6d9b97498fa02ea05a59eeeef815749af7cab75` (PR4).
- ERP code: `f064e6e007caed3cc43bfd8e1c91be01abe4b453` (PR9, includes unmerged SRS PR8).
- Canonical role contracts: `ee1578dc0c59aab49cbedca54ef1e2f24abf2d5e` (PR16).

## Checks

`validation-summary.json` pins source bytes and the private full test logs. Platform 340, ERP 246 and role 101 tests passed. API verification passed 13 contract tests, 39 developer API tests, 37 frontend tests and 91 isolated app checks, with zero Svelte errors/warnings. The final source guard confirms 601 declared HTTP operations. The final PM fix did not change API/UI implementations.

[Native flow](native-flow/README.md): real Frappe16.17.2 / ERPNext16.16.0 HTTP, private files, planning, callbacks and ledger produced three QA APPROVED Tasks across 11 original architecture/planning/Developer/QA/closing-PM turns. Duplicate callbacks/scans produced no extra Tasks/runs. A separate IT approval/resume test executed one real read-only owned-directory inspection on the same original IT run, then QA and PM; duplicate attempts executed no second action. The UI capture verifies six agents and five controls but intentionally shows an unattached runtime; it does not prove live control dispatch.

[Native security](native-security/README.md): 19 active and 5 final native checks plus 10 host assertions cover persisted Task/ToDo/File scopes, preserved blob bytes, native Pause/Stop vetoes, private PM evidence and generation1 PM continuation. Counts across these suites include nested assertions and should not be summed as independent tests.

All role replies were controlled protocol fixtures. They do not prove actual provider intelligence, business implementation, remote PR-head verification or physical mutating IT operations. Real ChatGPT scoped-tool execution remains blocked in issue19. The supported gateway/principal integration is described in `../../agentic-chatgpt-tool-isolation.md`.

## Failures and recovery

The native canary exposed and corrected initial evidence closure after run.started, loss of canonical evidence IDs, generic PM journal upload permissions and missing original PM start-target recovery. Saved original replies/events were reused; uncertain publications were never reset to pending. A known rejected Developer event was republished once only after explicit operator authorization and locked native absence/current-context proof. Automatic unknown-publication recovery remains read-only.

A test configuration omission initially read the production control-token default and sent that header only to the owned canary upload endpoint. No value was printed or sent to a provider, and no production write occurred. The detailed correction is retained in both native bundles. Subsequent fixtures use explicit private canary tokens, and PM publishing uses its actual scoped ERPBridge secret. Raw logs, credentials/configuration, browser state and databases are excluded.

## Existing live task and release boundary

`original-task-flow-reverified.json` reconciles TASK-2026-05811 and repair06073: native status Completed and one exact original Developer/QA Dot row each, with existing QA journals done/pass. This was read-only receipt reconciliation, not new execution. Its bounded acceptance recorded 1068 backend passes, 60 failures and 4 skips; it does not claim an entirely green backend suite. Historical legacy audit Pending Review is separate from the recovered QA journal.

`live-public-smoke.json` records read-only HTTP200 for the existing health/config/governance endpoints; it is not evidence of this candidate's deployment. The 37-file Linux overlay is staged outside runtime. DevOps 7374/7376 inventory and 7377's safe source-inspection rejection are separate read-only evidence. Production ERP still needs a customization-preserving shared-code release, route/site isolation and migration/configuration, plus source review, consistent backups, a supported drain and rollback. No business-project activation or legacy run replay was performed.

`SHA256SUMS.json` seals every file in this directory except itself. Earlier eight-turn canary evidence remains unchanged in the historical evidence directory.

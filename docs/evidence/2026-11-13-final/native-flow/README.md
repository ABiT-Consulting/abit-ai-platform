# Isolated native ERP canary — 2026-11-13

This evidence proves the frozen candidate against our isolated Frappe/ERPNext stack. It is not a live deployment or proof of actual model execution, business implementation, or remote pull-request head verification.

- Platform candidate: `d6d9b97498fa02ea05a59eeeef815749af7cab75`.
- ERP candidate: `f064e6e007caed3cc43bfd8e1c91be01abe4b453`.
- Frappe 16.17.2 / ERPNext 16.16.0; site `agov-uat.localhost`, owned `agov-uat-20261113-*` containers, internal network, no public ports.
- All 80 committed ERP package files match the candidate. Baseline committed hooks match; unrelated working-tree ownership-policy edits were excluded. All 16 loaded Core modules match the platform candidate.

## Full architecture, developer, QA and PM flow

`core-project-tree-final-report.json` records PROJ-0013 and Tasks TASK-2026-00038/39/40. All three are QA APPROVED. There are exactly 11 distinct original work units: architect → planning PM → child Developer/QA/closing PM → child Developer/QA/closing PM → parent Developer/QA/closing PM. Parent acceptance was executed independently after the children passed. Repeating all published callbacks and scanning twice created zero extra Tasks or runs.

Three concrete native failures were preserved and corrected. The original architecture reply, first Developer event and first closing PM reply were recovered with their existing identities. The known failed Developer transaction was verified absent under native Project/Task locks before one explicitly authorized, audited POST of the exact original event. No uncertain outbox was reset to pending. The final continuation reused the first five controlled replies and added six controlled role replies. See `native-negative-history.json` for bounded failure and recovery details.

## Human-approved IT action

`core-it-same-run-report.json` records separate PROJ-0015 / TASK-2026-00042. One actual read-only inspection of the owned canary runtime waited for a synthetic human approval. AI/service self-approval returned 409. The original IT run resumed after its approval checkpoint, published the trusted receipt through native File guards, then completed independent QA and closing PM review. Duplicate resumes and callbacks created zero second actions or role turns. There were four work units, three controlled role replies, one actual read-only action, and zero host mutations.

## ERP interface

`project-governance-ui.png` is an actual isolated Project dashboard capture, visibly labelled as a synthetic canary. `ui-report.json` verifies the separate PM checkbox (default OFF, no-copy), six verified agents and five controls. Core is intentionally unattached to this native dashboard: its status query displays inline RUNTIME_UNREACHABLE, with no raw Server Error modal. No run-control button was clicked, so this capture does not prove live control dispatch.

## Scope and credential correction

All role replies came from a controlled protocol adapter, with zero external provider/model requests. Native ERP HTTP, assignments, private evidence bytes, callbacks, approvals, Task state transitions, local ledger, planning and coordination were exercised. Developer delivery fixtures reference local commits and synthetic protocol logs; they do not establish real business-code acceptance or remote PR-head verification.

The initial V2 closing-PM artifact harness omitted an explicit bridge token-file configuration. Its default lookup read the existing production control token and sent that header only to the owned isolated canary upload endpoint, which rejected the request. The token was not printed or sent to an external/provider endpoint; no production mutation occurred. Earlier absolute claims that the initial harness used no production credential are corrected here. Every subsequent harness runtime explicitly uses its own mode-0600 token file under a mode-0700 runtime, sourced from the existing canary secret. The fixed PM publisher uses the actual scoped bridge secret. Safe path/hash booleans are in `harness-isolation-correction.json` and `canary-token-path-proof.json`. No credential rotation, permission grants or account changes were made.

Raw request logs, tracebacks, private configuration, session cookies, tokens and ledger databases are intentionally excluded. Raw private canary files remain restricted to mode 0600. This directory contains only selected sanitized receipts, hashes and the labelled UI image. The original failures and private audit records remain preserved outside this publishable bundle.

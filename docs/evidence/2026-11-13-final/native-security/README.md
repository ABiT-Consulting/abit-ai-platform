# Native security and PM continuation canary

Final result: PASS on the isolated `agov-uat.localhost` site, synthetic
`PROJ-0014` / `TASK-2026-00041`. There are **19 active native checks, 5 final
native checks, and 10 host checks**. The host counts include checks that reference
the two native reports; these counts are not independent test totals.

Five distinct original controlled role responses ran in this order:
PM planning → Developer → independent QA → PM `continue` → PM `reviewed`.
The last PM response is a new, bounded **generation1** coordination turn. The
feature Task retained `QA APPROVED`, its original active QA run and assignment.
Replaying every published original receipt and sweeping twice created no extra
response or invocation (final local ledger count:5) and preserved the complete feature Task snapshot.

The actual Frappe controllers, SQL records, private filesystem, native controls
and HTTP publication/download paths were exercised. Persisted original scopes
blocked Task project clearing, ToDo detachment/retargeting and File scope escape.
The early File mixin denied rewrite/delete before filesystem operations; receipts
include equal before/after snapshot hashes and exact original byte hashes. Native
Task Stop and Project Pause denied new PM blobs before bytes were written, with
exact acknowledged Resume restoring permission. PM evidence remained private and
immutable for both AI and human actors. Completed PM/QA uploads were denied,
while an ordinary human source attachment remained allowed after workflow checks.
The fixed PM download RPC returned the exact published private bytes.

`source-manifest.json` pins the final core module hashes to platform commit
`d6d9b97498fa02ea05a59eeeef815749af7cab75` and ERP sources to
`f064e6e007caed3cc43bfd8e1c91be01abe4b453`. The native reports record the exact
runtime permission-module hash. `bounded-negative-history.json` records the
initial metadata rejection and the datetime snapshot-helper correction. The
operator decision records positive native absence/current-context proof and one
identical original ERP-event publication; the uncertain outbox was never reset
and no Developer response was repeated.

The global harness isolation correction is preserved verbatim in
`harness-isolation-correction.json`: an earlier closing-PM harness config lacked
an explicit synthetic token path, so its default lookup was sent only to the
owned isolated canary endpoint; no value was printed. Corrected harness imports
create a mode0600 synthetic token under a mode0700 runtime, and final PM uploads
use the actual configured ERPBridge credential. This evidence does not claim
that the entire earlier harness history never performed a default token lookup.

These are controlled Dot protocol responses with real native execution paths.
They do not prove real model/provider execution, provider tool-scope enforcement,
business implementation, remote PR-head truth, a live abit.ai deployment, or
production acceptance. No production writes or provider requests occurred.
Native bounded PM `continue` was exercised; the blocked-PM human-resume branch
and IT approval-consumption exception are covered separately by their focused
source tests and other integration evidence.

`harness/` contains the exact final execution, probe and explicit recovery scripts.
They depend on private synthetic canary configuration that is intentionally not
included. They are one-use fixture scripts, not an instruction to replay these
completed identities or reset an uncertain run. This bundle contains no raw logs,
private configuration, token values, browser state or SQLite databases.

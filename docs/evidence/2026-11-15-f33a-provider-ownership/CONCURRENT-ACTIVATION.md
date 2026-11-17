# Concurrent ownership/control activation and QA observation

This supplements the earlier accepted `validation.json` without changing its files. The observations below are from 15:28:01 through 15:30:45 UTC on 2026-11-15. They were read-only: no endpoint call, model/provider request, native action, gate transition, database write or service action was performed by this observer.

## Retained activation and live code

The protected rollout is `/var/lib/abit-ai-erp-rollouts/governed-published-ownership-control-route-two-repair-20261115/`. Its completed receipt `ea10514dea5cc121bc0571aff5f2af2bd3a8a8ad7c74cc06fb4cd8624f72fbc7` binds exactly the ownership module `e7b55be7…` and governance router `c1597567…`; both match the live source readback. The ownership bytes are still the previously inspected proposal, not a later strengthened variant.

The retained intent binds controller `058a2c4e…`, matching the actual protected controller file. It records a SEALED27 boundary, zero active admissions, the published f33 result, and the unsubmitted QA successor. Completion records one Main Stop and Start, no QA actions, no native/model/gate actions, eleven backups, and new Main PID 504925/birth 70885830. The actual Main identity matches; QA PID 314775/birth 70770673 is unchanged. Backup entries were counted, not independently rehashed here. Retained source commits are recorded provenance, not an independent approval by this observer.

A separate protected intent records one POST of the existing native Retry14 command to the Project control route at 15:18:59 UTC under OPEN28. Its response is HTTP 202, durable, state `pending`; the intent binds the exact completed activation receipt. The command, endpoint and response are evidence of that other actor's operation. They were not replayed by this observer. The record does not make the earlier controller's `gate_changes=0` claim an account of the later OPEN28 transition.

## One scheduled QA read

At 15:30:44 UTC, the original f33 Developer invocation remained handed off with dispatch count one. The same QA successor was running with run `3d016aab-b569-528f-9837-e46945ebcf51`. The gate was OPEN28 with one active QA admission owned by Main 504925.

The QA counter of two is explained by the retained audit sequence: one `original_unsubmitted_rearmed` event, then one `execution_claimed` event marked attempt two. The source trace independently shows that both `rearm` and `claim_invocation` increment this counter. There is one native start journal, one provider original bound to this exact QA invocation, one scope attempt, and one admitted gateway session. The native start acknowledgement is durable, `duplicate=false`, and records `QA Reviewing` for the same run and Task. These are durable attempt/admission facts, not proof of two model sends.

The QA provider row remains `dispatching`, with no final response or provider receipt. There is no local or canonical QA result. `context.read` completed at 15:19:57 UTC. `repository.verify_delivery` has been `uncertain` since 15:20:07 UTC with no stored result. The gateway audit has one admission, one completed call and two fenced call intents. This is evidence of the original QA's gateway work, while its final provider outcome and verification-call cause remain unproved.

The f33 provider row remains `unknown` without a response or receipt. The same-owner/same-automation guard in `primary.claim` is unchanged; it is distinct from the same-Task ownership check altered by the deployed module. A different QA automation progressing does not verify safe reuse of the original Developer automation.

## Limits

No original relay observation was invoked and no new observer execution baseline was finalized. The previous SEALED27/idle capture failed and remains retained. Active QA must be preserved. The uncertain verification call must not be treated as success, nor retried with a new identity merely because no result is present. This record contains no verified QA verdict, closing-PM result or first-flow closure. It also does not endorse the concurrently deployed ownership policy or replace the separate source review.

`concurrent-activation-validation.json` binds the five new readbacks/source trace and this document. The source and receipt projections contain hashes, identities, counts and selected status fields; prompts, capabilities, credentials, response bodies and private configuration are excluded.

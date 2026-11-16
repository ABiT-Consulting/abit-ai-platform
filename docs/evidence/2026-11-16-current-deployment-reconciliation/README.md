# Current deployment reconciliation — 16 November 2026

This evidence records the observations made between **16:03:00 and 16:07:27 UTC**. It does not authorize a deployment, restart, rearm, provider settlement or replay. The companion `pm-reconciliation.json` retains exact timestamps, tool-operation IDs, terminal outcomes and hashes without raw provider replies, packets, credentials or database files.

## Original PM repair is obsolete for the current live state

The live existing `utils/agentic_relay_scope.py` has SHA-256:

`c3cdd4d3dbb5143ec594c7f9422aae725b1ae192287ecfb7e269fe422d4ff6ea`

The complete file diff against committed candidate `1907f2010399336b0ae9053f66e0ecb3f1d9a560805f7ae07d0a3cc7d83f698f` contains only the local-variable rename `roster` to `project_roster` and removal of its explanatory comment. Both use the complete same-Project roster for strict WorkPacket validation while preserving target binding checks.

The frozen controller at `deployment/linux/pm-review-scope-repair-20261115/apply_scope_repair.py`, SHA-256 `7e2c25060575f59227383ad0c444561a077db798ce6f10d77aa4ff882bd6f4a1`, is **OBSOLETE_FOR_CURRENT_LIVE**. Its original `50fc65ad… → 1907f201…` action and preserved-state assumptions no longer match source, process identities, gate, PM completion or provider history. Keep its immutable source and historical verification. Do not execute or retarget it, and do not rearm the completed PM.

The separately observed external deployment receipt is:

`/var/lib/abit-ai-erp-rollouts/pm-closing-roster-v1-20261115/completed-receipt.json`

SHA-256 `32fa617e4673fe3eda577c3329b0ee4a044fc15089ab75f6f9e58c9cfd2ce7d9`. It records the exact observed live scope hash, one Main restart, no QA/Node actions and SEALED35. Its reported 71-file cohort is the historical controller's scope, not a fresh inventory of all current runtime files. This observer did not perform that deployment.

## Original closing PM completed

The same original invocation `agov-pm-review-9ac3cfd3cc1969734e889cec5e0e4c978dd19ab86738fa5c` is completed, with one dispatch and run `8772c716-5449-559e-9d8f-e5580c68968a`. Its packet remains:

`1fd522a6dd7dbd489f8fe2ef6c4e7b92e0ab6419af553acc23e256ee6efb3f89`

| Evidence | SHA-256 |
| --- | --- |
| Published reviewed result | b089b4db68b0a5193d44a8b06022b608cf0b3ee39700e30a0938a7cdde3117e0 |
| Durable native ACK | 0f1abdf25568e4ff63f4327777024a1c9722e121d46d87b1b0f2812506a9a592 |
| Actual saved provider reply, 1,565 bytes | 7bc0f3e1b453684274d8c28c47af7815f4b41826c8dc48f4572b163cbeb0a3d6 |
| PM review completion receipt | 5412735ba9f3c0897acc73cc4cf884492daf070a304a0035d960d6739cf330ce |

At 16:07:27 UTC, the actual unchanged packet and result passed the current pure strict validator with the actual same-Project registered roster: zero packet errors and zero result errors. The single saved `AGOV RESULT:` object equals both published and local result; its canonical digest and native ACK event/run/Task identities match. The native ACK reports durable QA APPROVED. Both PM review tables record completed generation0.

The successful verification was tool operation `6e59478e-a0a4-48b8-be29-f4697b52cc20`, terminal exit0. This was a read-only actual-input contract/identity check, not a new model request or browser UAT. It did not rederive the transport receipt protocol.

The original QA remains verified/pass with unchanged result `1050b2ef3a6544795ad5ab160c447b2cb40269231e96f43582622f08677481fa` and ACK `3e40cd808a0366f68b5b1b79c86ca95569938bf672278c70df5fd8a4d7b09b6c`. The JSON retains its observed reply, envelope and receipt-field hashes. No fresh full QA contract rerun is claimed.

Hashes of provider `receipt_field` values in the JSON are digests of the stored field, not a replacement receipt or new transport attestation.

## f33 was changed externally

The actual original f33 provider status is now `native_verified`. Comparing the current row against the exact retained before-file showed that only `status`, `reason` and `updated_at` changed. Its envelope is unchanged; raw response, transport receipt and scope receipt remain null.

The external metadata receipt is:

`/var/lib/abit-ai-erp-rollouts/native-verified-original-f33-successor-f6-20261115/completed-receipt.json`

SHA-256 `1c9cfc8c1bf85087c7f3a2c68a61cde6537095b785248178251f695506d61317`. It explicitly states `transport_receipt_claimed=false` and refers to closure hash `9c53adbfb38d396eb4684778b9ca680a4f180d5587559c5fd6827154a10b5e68`.

The authority and independent upstream terminal proof behind that separate closure have not been validated here. Its new status alone is not proof of zero effective ownership. Preserve the observed history; this evidence authorizes no metadata reversal, reclassification or replay.

## Current work prevents stale restart assumptions

At **16:03:56 UTC**, the actual gate was **OPEN62** and 55 admission rows were active or unresolved:

| Kind | State | Rows |
| --- | --- | ---: |
| Automation | Unresolved | 29 |
| Batch | Active | 7 |
| ERP | Unresolved | 1 |
| Governed | Active | 2 |
| QA | Active | 16 |

These are mixed historical and current ledger records; they are not all proven current execution.

There is also actual current governed work: Developer invocation `agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0`, run `5955703a-4a31-5a33-8265-63a5299bdace`, was governance-running, automation-running and provider-dispatching. Its epoch62 admission belonged to Main PID2028435, process birth79574991.

No zero-work restart condition is established. Do not infer settlement from a dead historical process or clear old admissions automatically.

## Observation method and future polling reference

No reusable application helper was executed for current activity. The exact read was inline stdlib Python in operation `5670b832-d3fc-489c-911b-5ea3aef0f720`.

Connections used the existing data root, effective uid999/gid988, SQLite URI `mode=ro`, `PRAGMA query_only=ON`, explicit read transactions, bounded SELECT results and close-on-exit. All four completed current-activity connections reported query_only1 and total_changes0. Individual database snapshots are not cross-database atomic. SQLite may create its own WAL/SHM sidecars as the service account; no schema initialization or application write SQL was used.

The JSON's `polling_reference` identifies the exact existing Developer invocation/run and bounded projections for a separately reviewed future observation. Those projections were not executed again while persisting these files. No application startup, health endpoint, model request, stop/restart, recovery or gate action is part of that polling method.

## Retained diagnostic limitations

The JSON explicitly retains unsuccessful attempts:

- A broad read-only file inventory timed out; it is not successful discovery evidence.
- The first selected-record reader observed the requested PM/QA/f33 records, then hit an invalid PM-session column. Its shell exited0 because the reader retained its own exception; it is explicitly marked incomplete.
- A source-inspection inline script initially had a syntax typo.
- The first isolated validator harness lacked its pure `developer_delivery` dependency and exited1. The corrected actual-input verification above exited0.

The corrected reads, their exact source hashes and terminal results are distinguished from those attempts. No endpoint, database write, provider action, restart or Git mutation was performed during reconciliation or this evidence-only persistence.

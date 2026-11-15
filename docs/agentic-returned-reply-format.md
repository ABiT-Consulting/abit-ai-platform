# Returned reply format recovery

`returned-reply-format.v2` permits the ordinary completion pipeline to read separately captured, format-corrected bytes for one already-submitted Developer run. The input is a Root-owned, per-original proof. Selection preserves the original provider envelope, saved response, messages, WorkPacket, invocation, run, native start and gateway admission. It does not send a model request, create a successor, obtain a new admission or replace an existing result.

The v2 change closes authority gaps in the earlier v1 reader. A Retry cursor and an application audit alone were insufficient: a cursor could lack signed ingress, name a registered agent or refer to a different Task scope. Arrival and application times also did not establish when a native command was authored. V2 binds one exact signed human Retry and independently retained native creation evidence. Unbound v1 files are rejected.

## Selection boundary

The existing recovery entry point reads `/var/lib/abit-ai-agentic-gateway/returned-reply-format/<invocation_id>.json`. The directory must be a real, non-symlink directory owned by `0:988` with mode `0750`; the file must be a regular, single-link file owned by `0:988` with mode `0640`. File size is bounded to 400,000 bytes. The reader checks the opened file identity and reads it without following a symlink. An absent file keeps this optional path disabled.

A protected file does not override current authority. Selection requires all of the following:

- The exact owned Developer invocation is uncertain, was submitted once and retains its original run, lease fence, packet, Task and specialist identity.
- The original native start, provider envelope and original gateway scope agree. Stored `admission_json` is the original scope/preflight/submission object; its derived gateway receipt is verified against that same envelope.
- There is no existing local result, published result or action for this invocation. Every existing gateway call is completed.
- Current Project and Task controls are enabled, the Project gate passes, and maintenance is OPEN both before the awaited readback and again inside the selection transaction.
- A fresh packet preserves the established business-context boundary. Normal completion and publication retain their own validation and current-authority checks.
- The exact bound Retry remains the acknowledged, enabled native cursor for its declared scope, and the signed ingress, human actor, application audit and actual native creation evidence verify.

A closed maintenance or control gate holds the protected path without falling through to browser observation or completion. Selection does not transfer a dead process lease or treat its old admission as permission to start work.

## Closed protected schema

The top-level object has `schema_version: 2`, `purpose: "returned-reply-format.v2"` and exactly the keys checked by `_format_authority`. Unknown or missing keys deny. JSON containing duplicate keys or nonfinite values also denies.

The existing capture fields bind invocation/run/Project/Task/specialist identities, packet hash, native start event and receipt hashes, provider envelope and original admission hashes, owner/account/Dot identity, the exact conversation URL, original and correction message pairs, corrected UTF-8 bytes and hash, observed original visible-text hash, capture intent/release/idle proof hashes, `pause_control_sequence` and integer UTC `captured_at`. All four message IDs must be distinct and share the observed original Calpico room; that room is independent of the Dot URL alias. Corrected text is bounded to 200,000 UTF-8 bytes.

Two additional objects provide command authority:

| Object | Exact fields | Meaning |
| --- | --- | --- |
| `retry_control` | `event_id`, `task_id`, `command_id`, `control_sequence`, `payload_sha256` | Selects one immutable signed ERP event and one exact current native command. `task_id` is either the original Task or the empty string for Project scope. |
| `native_creation` | `schema_version`, `purpose`, `record_response_utf8`, `record_response_sha256`, `timezone_response_utf8`, `timezone_response_sha256`, `observed_at_utc`, `provenance` | Retains the actual native record and site-timezone response bytes and their independent readback provenance. |

`native_creation.schema_version` is `1`; its purpose is `native-control-creation.v1`. The exact provenance label is `Independent native control-intent and site-timezone readbacks; no write.` This label does not replace the actual protected response bytes or their validation.

Each raw response is limited to 65,536 UTF-8 bytes and must hash to its declared SHA-256. It must contain exactly one `data` or `message` envelope holding one object. Preserve the original response bytes, including ordinary extra native document fields; do not reconstruct a selected-field projection and label it a raw response.

## Signed command and native creation proof

`_format_retry_authority` loads the selected inbox row by its exact event key. It rederives the payload hash and checks the Project, Task scope, command, integer sequence, action and actor against the protected binding and current acknowledged native cursor. The action must be Retry. The sequence must be newer than the protected pause boundary. Native sequence allocation is Project-global across Task and Project controls, while the Core cursor and proof remain bound to the exact scope.

The corresponding `erp_event_received` audit must have the `signed-erp` actor and the exact inbox identity/hash. The unique `native_control_applied` audit must have the same human actor, command, scope, sequence and enabled state. Both audit hashes and their actual previous-project-event links are verified. A registered agent ERP user or agent ID, Guest, reserved ingress actor, unsigned inbox, mismatched scope or altered audit cannot authorize selection. The application must follow signed ingress and capture; those delivery times are additional consistency checks, not authorship evidence.

The retained native record must be the actual `AI Governance Record` of kind `control_intent`. Its `name` and `record_key` are rederived from Project and command; its `run_id` is the exact zero-padded 20-digit sequence. Project, Task, owner, payload and content hash must match the signed human command. The complete parsed control payload has exactly `project_id`, `task_id`, `command_id`, `control_sequence`, `action`, `actor` and boolean `retained_veto`. The record owner must equal the signed human actor.

Creation is read from that record's native `creation` field and normalized using the retained `System Settings.time_zone` response. No timezone is assumed. Unknown zones, ambiguous or nonexistent local times, and an explicit offset inconsistent with the retained timezone deny. `observed_at_utc` must be a valid UTC timestamp at or after creation.

The native creation UTC **integer-second bucket must be strictly later than `captured_at`**, and no later than signed ingress. Rejecting the entire capture-second bucket avoids treating a later-looking fraction as post-capture when the capture timestamp was truncated. The existing native control payload and ACK do not expose command creation. Neither an HMAC delivery timestamp, inbox arrival, application time nor a Task/Project modified timestamp substitutes for this proof. The source change adds no native endpoint or protocol and performs no native lookup.

## Persistence and normal completion

Selection atomically appends one `returned_reply_format_selected` audit. It contains safe identity fields and hashes for the protected file, capture, signed ingress, application and creation evidence. The raw native readbacks and corrected text remain private in the protected input. The reader supplies a private in-memory response view to the unchanged completion pipeline; it does not rewrite provider records or create a canonical result itself.

An identical selection is idempotent. A different protected file, corrected response or derived authority cannot replace a prior selection. A prior v1 selection is not silently upgraded to v2. Existing local or published results use their established recovery path and are not replaced by this reader. No migration, audit rewrite or synthetic native approval is provided.

Successful parsing is only a formatting result. Existing role/outcome rules, exact evidence bytes, delivery receipts, publication, independent QA and handoff remain mandatory. The larger registered-command receipt representation introduced independently in commit `0bb85b29b94e70b106de42f71102389a24cc6de5` changes neither execution authority nor this selection policy.

## Validation and release status

The authority bypasses were reproduced against v1 before correction. The focused v2 evidence comprises 18 successful cases plus one separately reproduced and corrected maintenance-race case, for 19 distinct successful cases; it is not a single clean 19-case run. Fixtures use temporary ledgers and synthetic native records, with network and DNS denied. They exercise real signed ingress/application handling, both valid scopes, actual gateway admission representation, stale/missing/drifted authority, native identity/hash/timezone/freshness boundaries, immutable selection, original-record preservation and normal strict decoding.

See [the v2 validation record](evidence/2026-11-15-returned-reply-format-v2/validation.json) for exact source/dependency pins, split test receipts and integrated API gate results. [The v1 evidence](evidence/2026-11-15-returned-reply-format-v1/README.md) remains historical and does not validate the v2 authority boundary.

This source/test change does not acquire or stage a production native creation proof, enable a protected file, change native controls or activate a runtime. Any actual proof must be independently retained and checked before a separately authorized release. Test-native readbacks must never be used as production authority.

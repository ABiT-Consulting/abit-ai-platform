# Governed result provenance compatibility

Policy `agov.sa-provenance-alias.v1` resolves two machine provenance references in a Solution Architect's `ready_for_review` result. It changes no business verdict, requirements, designs, tasks, tests, evidence, actor, or SRS content.

The server reloads the original submitted invocation and its frozen WorkPacket. It verifies the exact Project, Task, specialist, Task revision, native packet digest, complete packet hash, SRS version/hash, activity revision, backlog hashes and original stable automation run. The exact original Task URL may resolve to that packet's Project SRS URL; the exact server-derived stable run UUID may resolve to its invocation ID. Canonical values remain unchanged. Other URLs, runs and identities are rejected.

The raw reply remains in its original run record. Only a deep copy can change these JSON pointers:

- `/architect_plan/metadata/source_url`
- `/architect_plan/metadata/run_id`

The normal contract validator, gateway result checks, real artifact-byte checks, current ERP authorization and signed publication/readback remain mandatory. A provenance projection does not establish completion or permit a new model submission.

When projection occurs, the canonical local result and one hash-chained `result_provenance_projection` event are stored in the same SQLite transaction. The event includes the policy version/hash, original row and packet hashes, raw reply/result hashes, canonical result hash and changed pointer hashes. It exports no raw reply or capability. Exact retries reuse that result and audit; changed raw text, policy, identity or canonical result cannot overwrite them.

This is an internal compatibility policy within existing result/recovery operations. It adds no HTTP operation, schema, permission, lease, provider request or ERP bypass. Existing API authentication and the API & Developers catalogue remain unchanged.

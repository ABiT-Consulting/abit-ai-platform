# Automatic project task execution

For a Project with explicit `is_active: Yes`, an active status, Agentic
Governance, PM Task Automation and the open
ai-projects assignment enabled, the ABIT.AI coordinator reads the complete native
backlog after task events and published outcomes. Existing tasks are selected by
priority, due date and stable Task ID. Explicit prerequisites and direct children
must pass their actual completion gates before a parent receives its own
acceptance work. Parent completion is never inferred from children.

Eligible idle tasks with no human owner are enrolled through the authenticated
native API. ERP records one accountable AI developer, keeps the original Task
identity, and emits the deterministic signed Start event. ABIT.AI receives that
event through its existing event API and runs PM review, developer, independent
QA and PM acknowledgment in Abitdot. No model or executor is added to ERP.

The SQLite claim transaction admits the configured number of independent Tasks
per project. The native server configuration `abit_agentic_project_concurrency`
maps exact Project IDs to integer limits from 1 through 8; the default is 1.
`project_execution_policy.max_concurrency` is included in the authenticated
WorkPacket and complete backlog response. The packet hash binds this policy.
Existing single-task lane records migrate atomically without resetting leases
or invocation history. Each Task retains one slot through Developer, QA and PM
review. Reducing a limit preserves existing executions and denies new slots.
The complete dependency and parent/child graph is checked for missing or
cross-project references and cycles before automatic native pickup. Queued
Tasks are ordered by lifecycle continuation, priority, due date and stable
creation/Task identity. The original queued
invocation retains its ID and zero submission attempts while another Task works.
A result event wakes those exact deferred IDs; no new timer is introduced.
The current Task keeps its lane through QA and PM review. Waiting parents yield
to prerequisite/child work. A durable native blocker may yield to unrelated
eligible work while its Task lease and retry journal remain preserved; unresolved
publication, tool effects or execution uncertainty never count as completion.

## Native API contract

`POST /api/method/abit_ai_assistant.agentic_control.prepare_project_pickup`

Authentication requires the fixed native ERP service identity and existing
authenticated bridge. Project opt-in, PM automation, current control fences,
exact task revision/owner context, hierarchy, active execution and role assignment
are rechecked under the native project lock.

Request fields:

- `project_id`, `task_id`
- `expected_task_revision`, `expected_owner_context_sha256`
- `idempotency_key` derived from those exact identities

The response is `Waiting` while another unresolved task owns the project, or
`Accepted` with the exact project, task, signed `command_id`, current task
revision and `target_role: project_manager`, `executor_role: developer`.
Acceptance means durable queue intake; it never claims execution or completion.

The reverse ERP channel allowlists the exact RPC independently at both edges.
ABIT.AI's existing `POST /api/v1/agentic/events` documentation describes automatic
pickup and bounded project execution. The generated Developers catalogue and OpenAPI
continue to derive their operation schemas from the actual routes.

The human-only enrollment and control APIs retain their guards. The new native
path uses its own project policy provenance and never impersonates a human.
Existing human-owned, active, uncertain, paused/stopped, unpublished or externally
managed work is preserved. Production releases and SAP posting approvals retain
their existing gates.

## Original execution failure visibility

The Core ledger stages an `execution.observation` in the same transaction that
marks an original invocation uncertain or detects its expired heartbeat lease.
The existing immediate execution path and 30-minute recovery process deliver
that immutable event through the authenticated reverse ERP channel to
`POST /api/method/abit_ai_assistant.agentic_control.record_execution_observation`.
The request contains `event`, a canonical JSON string, signed with the existing
bridge HMAC headers. The shared governance schema defines its exact identities
and bounded reason enum; exception text is never sent as business evidence.

ERP matches the original start receipt and frozen native packet digest, marks
only the same current execution Blocked and records an understandable reason.
A newer result, human state or control supersedes a delayed observation. The
response confirms the exact event/project/task/run, `durable: true`, and status
`Recorded` or `Superseded`, plus immutable `previous_modified` and `modified`
for the exact native observation write. Lost acknowledgments leave the Core journal pending;
retries use the same event bytes and ID. No observation releases ownership,
settles uncertain effects, supplies QA proof or completes a business task.

Old frozen WorkPackets may omit `project_execution_policy`. Only adding the
unchanged default `{max_concurrency: 1}` is recognized as a compatible metadata
upgrade. Original packet bytes, IDs and hashes stay intact. A nondefault policy
or any business-input change still requires the existing explicit refresh path.

## Bounded original transport

When the serialized packet plus handoff exceeds 24,000 UTF-8 bytes, the initial
model request carries an immutable context reference with original identities,
revisions, the native packet digest, full ledger packet hash and handoff digest.
Smaller requests retain the existing embedded packet. The admitted
`context.read` operation returns the complete original WorkPacket and handoff,
their hashes, result schemas and the current allowed operation catalogue.
Reference transport never substitutes a truncated packet or creates another
invocation. Current native authority, task ownership, submission fences and
result validation remain enforced against the full frozen original.

Native failure visibility must not invalidate a confirmed original start that
has not submitted its model request. Recovery preserves the original start
receipt and accepts a newer Task modification only through the published,
authenticated observation receipt chain for that same invocation/run/packet.
The exact original Task ownership fingerprint must still match. The final
before-submit guard repeats this provenance and current-business-context check;
only the subsequent result callback uses the derived current modification.
Human edits, owner changes, missing or superseded receipts, and disconnected
modification chains cannot authorize submission. Unrelated backlog changes
remain compatible with an unchanged original Task. No second native Start or
replacement provider request is introduced.

Deploy the native observation receipt upgrade before the CAS-aware Core. Older
Core versions ignore the additive receipt fields. A new Core retains a
Recorded receipt without modification provenance as pending; recovery cannot
invent that metadata or amend a historical start receipt.

Every complete backlog packet must contain exactly one current Task row with
a valid native ownership fingerprint. Submission and gateway admission compare
that fingerprint even when Task.modified is unchanged, because assignment and
open ToDo ownership can change separately. Configured AI role additions retain
the accountable owner fingerprint; unrelated Task changes still do not lock
executor tasks. Historical packets with no backlog retain their existing
comparison contract; incomplete ownership evidence in a backlog fails closed.

Original-run recovery uses an internal actionable-state scan with the Task
filter applied before page limits. A fixed starting rowid watermark and keyset
pages cover every eligible original without replaying terminal history or
following newly created work indefinitely. Public invocation history retains
its existing 100-row bound. No extra reconciliation timer is introduced.

Failure-observation delivery also uses a fixed starting rowid watermark and
keyset pages. Rejected or malformed pending journals remain intact and cannot
prevent later valid failures from reaching ERP. The immediate executor flush
filters the current invocation; the existing reconciliation sweep fairly
visits the whole project backlog. Each pass visits an original row once and
defers rows created during delivery to the next pass.

Independent physical IT QA compares its original artifact to a fresh, authenticated
broker read. Only the broker envelope timestamp/MAC and the immediate
`observed.checked_at` host-read timestamp may differ between observations.
Native proof and every host-state field remain exact; original evidence bytes
are never rewritten to accommodate a later read.

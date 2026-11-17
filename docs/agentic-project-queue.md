# Automatic project task execution

For an active project with Agentic Governance, PM Task Automation and the open
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

The SQLite claim transaction admits one Task per project. The original queued
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
pickup and serial execution. The generated Developers catalogue and OpenAPI
continue to derive their operation schemas from the actual routes.

The human-only enrollment and control APIs retain their guards. The new native
path uses its own project policy provenance and never impersonates a human.
Existing human-owned, active, uncertain, paused/stopped, unpublished or externally
managed work is preserved. Production releases and SAP posting approvals retain
their existing gates.

## Verification and source boundary

The focused source suite passed 174 tests covering concurrent transactional
claims, complete project pickup, original queued identities, dependency/parent
review gates, both reverse-channel edges and multiprocess SQLite lock retention.
`npm run api:verify` passed, including portal type checks; the isolated
`npm run api:verify:integration` passed 91 checks. All changed Python modules
compiled and `git diff --check` passed. These are focused checks, not a claim
that every legacy executor suite is green.

This source change contains only the captured queue, enrollment-guidance,
channel-allowlist and SQLite-lock deltas, reconciled onto the tracked source.
Unrelated current-live feature overlays and terminal-run retirement are separate
changes. Native ERP intake deployment and durable container image publication
remain separate release stages.

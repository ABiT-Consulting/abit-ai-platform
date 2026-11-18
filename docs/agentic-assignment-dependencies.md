# Assignment intake and dependency handoff

Companion ERP assignment intake automatically enrolls a newly human-assigned
AI developer Task and submits the existing signed /api/v1/agentic/events Start
command. The existing project-scoped PM reviews the task before handing it to
its bound executor. Neither a manual enrollment click nor manual Task Start
is required for that assignment.

The atomic PM-to-executor path now applies the same verified prerequisite
rule used by released backlog work. Explicit Task dependencies and group
children must satisfy _task_done before an executor invocation is created.
A governed dependency requires current independent QA and completed PM review;
setting status to Completed is insufficient.

If prerequisites are unfinished, the published PM source remains retained in
a durable atomic_dependencies lifecycle wait. Existing project events,
including completion, scan the saved wait. A successful handoff marks it
resumed. Repeated scans preserve the original successor ID. A paused Task,
changed business scope, missing adoption, or another Project's prerequisite
cannot create executor work. Independent tasks have no artificial
serialization barrier and can dispatch together.

The wait never launches its prerequisite on behalf of another Task and never
changes another Task's owner. Parent/child hierarchy requires children before
the parent's acceptance execution. Dependency edges represent ordering; a
shared parent alone does not force siblings into an invented sequence. The PM
must resolve unclear or unstated relationships using the complete backlog and
escalate unclear scope, rather than claiming missing edges prove independence.

API routes, authentication and payload schemas remain unchanged. The signed
event and native Start/adoption contracts are reused; there is no new executor
or polling service.

Validation recorded with the source candidate: 45 real SQLite lifecycle
tests, including six assignment dependency cases, plus `npm run api:verify`
with 37 frontend tests and a clean developer portal type check.

Independent ABIT-DEV-LINUX recheck on 17 November: all 45 SQLite lifecycle
tests passed, including the six assignment dependency cases; all six queued-
wakeup tests passed. `ABIT_PYTHON=/srv/abit-dev-linux/abit-ai/venv/bin/python
npm run api:verify` passed: 13 API contract tests, 39 developer API/UI tests,
37 frontend tests, and Svelte diagnostics with zero errors or warnings.
Python compilation and `git diff --check` passed.

Approved activation on 17 November 2026 UAE installed the dependency guard
in the existing ABIT.AI runtime, preserving its live-only source additions.
The actual overlay passed all 45 lifecycle tests before cutover. The scoped
Task detail extension was then installed to resolve the observed 119-Task
context blocker. Health and API configuration are HTTP 200. The companion
native ERP deployments 7612 and 7614 succeeded.

The context.read operation preserves its existing empty-argument response and
now accepts exactly task_id and expected_task_revision for a full peer Task
read. The ID/revision must occur in the immutable complete Project overview.
The handler fixes the native Project/path server-side and compares returned
Task identity, business revision and owner hash. Other Project identities,
stale inputs and changed owners fail. The operation remains read-only under
the existing job-bound read capability. Its dynamic model catalogue documents
the new arguments and the explicit Overview only markers.

Large assignment packets retain every Project Task, owner hash, hierarchy and
dependency. Their complete-business-context digest remains intact; overview
content receives its own exact digest. Full selected/dependent Task context
remains present, and other details remain retrievable through the protected
read. No new model-selected endpoint or broad business-write tool is added.

Three focused scoped-detail/catalogue tests passed; API verification passed
again with zero developer portal diagnostics. The complete 24-case gateway
suite encountered one existing service-controller subgroup requirement in a
repository test; it is not claimed green. The changed scoped-read tests pass.

Actual TASK-2026-06332 readback: Working, exactly one PM invocation with original
run 40c31e6f-85c3-5113-bd38-f59563f08e08; completed scoped context reads and a
written review artifact observed. The original native Start recovered without
submitting an extra command or replacement run. Developer execution is gated
behind PM review and verified prerequisites. Business completion is not claimed.

Core source/configuration and SQLite backups were retained for each one-leaf
cutover, with maintenance admission reopened and historical journals unchanged.
Native current-container source installation is verified. Publishing refreshed
native container images remains pending; no container recreation or unrelated
application/customer release is claimed.

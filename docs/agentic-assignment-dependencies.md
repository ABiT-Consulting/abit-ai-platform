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

This branch is source-tested only. Production is unchanged. Release the core
guard before enabling the companion ERP hook, with live-baseline comparison,
preservation backups, exact leaf replacement and normal service health/channel
verification. Actual Dot execution must be verified after approved activation.

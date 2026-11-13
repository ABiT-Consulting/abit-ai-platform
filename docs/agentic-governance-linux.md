# ABiT Agentic Governance — Linux API v1 (source and live rollout)

## ERP ↔ ABIT.AI boundaries

- ERPNext `https://erp.abit.ai` remains authoritative for projects, SRS, AI role users, task descriptions, task status, comments, attachments, approvals and financial/business actions.
- `https://abit.ai` owns **one persistent agent per role per ERP Project**, durable invocation ID / task leases, model execution, internal handoff constraints, and technical receipts.
- Only known ERP projects already present in the legacy resolver's `projects` allowlist or explicitly opted-in with `custom_agentic_governance_enabled` can be provisioned. Every request rereads the actual ERP Project or Task and validates its identity/revision. The API does not create new ERP Users.
- Role users are reused across projects: `solution_architect`/ `project_manager` map to the existing `ai-projects@abitcons.com`, `developer` to `ai-developer@abitcons.com`, `qa` to `ai-qa@abitcons.com`, `operations`/`it_manager` to `ai-operations@abitcons.com`. Mapping can be overridden only through the server-owned ERP resolver `agentic_role_users` configuration after verifying those users exist.
- Each role uses a deterministic ABIT.AI Automation UUID from `(project, role)`. The persistent Automation is enabled but **never scheduled** (`next_run_at=None`); only an authenticated API call creates a run.

## Server-to-server API

API prefix: `/api/v1/agentic`. Private endpoints require the existing ERP server-side credential in header `X-ABIT-ERP-Control`; never place it in browser code, model prompts, issue comments or Git.

| Method / path | Purpose |
|---|---|
| `GET /health` | Unauthenticated non-secret readiness/version and 1800-second recovery interval |
| `POST /projects/{PROJ}/provision` | Verify ERP Project and role users; create/reuse six scoped agents, with deterministic ABIT IDs |
| `GET /projects/{PROJ}/agents` | List only scoped role mappings (no API keys or work packets) |
| `POST /projects/{PROJ}/invoke` | Validate ERP Task and modified revision, authorized source/target role, idempotency key; create durable queued run and execute on event |
| `GET /projects/{PROJ}/invocations` | Read bounded project-scoped technical status without exposing private prompts or context |

`POST /provision` JSON may specify an already-approved ABIT.AI `model_id`. It does **not** start any task.

`POST /invoke` JSON example (replace all IDs/revisions from live ERP; never fabricate them in production):
```json
{
  "invocation_id": "erp-PROJ-0091-TASK-2026-06074-event-0001",
  "erp_task_id": "TASK-2026-06074",
  "from_role": "project_manager",
  "from_agent_id": "<existing deterministic ABIT.AI Project Manager UUID>",
  "target_role": "developer",
  "task_revision": "<exact ERP Task modified timestamp>",
  "context": "<scoped work packet; SRS/version + task activity + acceptance criteria>"
}
```
The API denies unknown projects, invalid/changed ERP tasks, unsupported role paths, duplicate idempotency keys with changed content, task collisions with a `queued/running/uncertain` run and existing ERP `Working` tasks. The target agent may execute tools only within its configured provider and approved policies. API acceptance is **not** verified Task completion.

## Durable execution

A separate SQLite ledger under `open_webui/data/agentic-governance.sqlite` persists project agent registry and job records. Each task has one active or uncertain governance job. For a valid request, ABIT.AI creates a run through the existing Open WebUI completion pipeline and saves technical results. A complete chat turn is never reported as ERPNext business Task completion.

If a server restarts before a **queued** work item is sent, the fallback loop can safely retry it. An already `running` job whose heartbeat is stale is marked `uncertain` after a safe delay and **never resubmitted blindly**. Only one internal 30-minute reconciliation loop runs; normal triggers are API events, not timers.

## Limitations / dependent tasks

- The ERP Task creation API, event outbox, artifact verification, automatic Developer→QA→ERP business status transition, user-facing ERP controls and independent acceptance-test gates are *separate* tracked implementation tasks. The presence of this API does not prove those business workflows are deployed.
- Agent model IDs and provider connections must be validated in a safe project before real work; automatic IT actions still require host identity checks, backup/retention verification and required ERP approvals.
- The source change must be merged/reviewed before becoming durable, and live release must be backed up, restarted with existing service settings, verified and recorded separately.

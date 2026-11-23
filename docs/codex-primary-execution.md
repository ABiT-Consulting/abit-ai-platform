# Codex primary development execution

ABIT.AI remains the goal owner. New governed Developer invocations default to
Codex CLI on ABIT-DEV-LINUX. Planning, IT and independent QA use their existing
specialist routes. The existing ERP WorkPacket, task fence, AutomationRun,
acceptance criteria, result validator and publication bridge remain authoritative.

## Execution and tools

`agentic_execution_router.select_adapter` selects the engine before execution.
`CodexAdapter` checks CLI login and the registered repository/environment before
claiming a run. The CLI receives the complete immutable packet, original handoff
context, goal and logical run IDs, and a workflow covering inspection, planning,
implementation, tests, review and structured reporting.

The CLI's only configured execution interface is a private stdio MCP adapter to
the existing governed gateway. User MCP/plugin configuration, shell, apps,
browser, hooks, web search and sub-agent execution are disabled for that process.
The run's `agentic_action` tool is explicitly approved in the CLI configuration;
this transport setting grants no ERP authority. Every operation still passes
the existing gateway's live ERP checks, role/capability checks, task lease,
operation journal and action-specific approvals.

Repository preparation retains the existing pinned, invocation-specific isolated
repositories and scoped source paths. Code edits, registered sandboxed tests and
draft PR delivery use the same verified gateway handlers as Dot. This change does
not replace those repository snapshots with native CLI-managed Git worktrees.

Extra operations:

- `checkpoint.save` with `{}`: derive a checkpoint from actual source bytes and
  durable operation receipts. A model cannot supply a completed percentage.
- `relay.assist` with `{"question":"..."}`: call the existing ChatGPT Web Relay
  through a local adapter for advice. No executor capability, ERP credential or
  gateway session is sent. Advice is untrusted data and never test evidence.

The controller awaits the complete CLI process group and every local tool
handler before it can hand ownership to another engine.

## Ownership and fallback

The executor journal is stored in the existing ABIT.AI governance database:

- `governance_executor`: engine, epoch, original run, task fence, process identity,
  exit state, latest checkpoint, handoff reason and original reply.
- `governance_executor_event`: append-only execution/admission history.
- `governance_executor_checkpoint`: immutable checkpoint history.
- `governance_execution_policy`: Project policy for future Developer runs.

Default policy is `codex_first` with `fallback_on=["quota"]`.
Only structured provider quota errors or the CLI's explicit usage-limit banner
qualify. A generic 429, failed test, auth error, network error, timeout, malformed
output or service interruption does not. Optional `cli_unavailable` fallback must
be enabled explicitly.

On a confirmed eligible exit, ABIT.AI:

1. Joins the CLI process group and local operations.
2. Captures the current source manifest and completed/uncertain operation receipts.
3. Revalidates current ERP/task authorization.
4. Refuses handoff if any original tool operation is uncertain.
5. Atomically switches executor ownership from Codex epoch 1 to Dot epoch 2,
   keeping the same invocation, WorkPacket, AutomationRun and task fence.
6. Obtains actual relay proofs and transfers gateway conversation admission.
   The old Codex client loses action authority. The previous admission is archived.
7. Sends the verified checkpoint to Dot, which continues the same source and
   reuses original call IDs, successful tests for matching source, and delivery
   receipts. Completed work is not resubmitted.

Checkpoints retain requirement IDs and unfinished acceptance work. A passing test
is reusable only when its receipt's source digest equals the current source.
Implementation/delivery and independent QA still have to be verified.

A crash/unknown process outcome holds original ownership. Recovery can publish a
saved Codex reply without another model invocation. A rejected report keeps the
raw reply and task lease; it does not silently start Dot or manufacture a pass.

## Supported API

All routes are under `/api/v1/agentic` and require the existing server-to-server
`X-ABIT-ERP-Control` authentication and current ERP Project eligibility.

- `GET /projects/{project_id}/execution-policy`
- `PUT /projects/{project_id}/execution-policy`
- `GET /projects/{project_id}/invocations/{invocation_id}/execution`

Example policy request:

```json
{"strategy":"codex_first","fallback_on":["quota"]}
```

`chatgpt_dot_first` selects the existing chain for future development runs.
Policy changes never replace an active/uncertain executor. Status exports only
run/engine/epoch/state/checkpoint identity; source, raw replies and private
operation receipts remain protected. API catalogue, OpenAPI and Postman exports
are generated from the actual FastAPI routes.

Optional server configuration in the existing task-resolution configuration:

```json
{
  "agentic_codex": {
    "binary": "/usr/local/bin/codex",
    "timeout_seconds": 1800
  }
}
```

An optional `codex_home` must be an absolute server-owned path. Credentials are
managed by `codex login`; no credential belongs in this JSON. An optional
`model` pins a server-selected model; otherwise the CLI chooses its default.
The exact execution identity must have a working login and access to its own
auth home. A desktop login in another home is not automatically a service login.

CLI behavior was checked against installed codex-cli 0.169.0 and the official
[non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
and [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp) documentation.

## Validation and activation

Offline tests use temporary databases/source/evidence and controlled ERP/provider
boundaries. They cover quota classification, single ownership, admission
transfer, preserved partial source, test receipt reuse/invalidation, cancellation,
original-reply recovery, auth/project API controls, and the complete
Codex-to-Dot-to-independent-QA-to-ERP-receipt flow.

```sh
/srv/abit-dev-linux/abit-ai/venv/bin/python -m unittest discover -s backend/tests -p 'test_agentic_codex*.py' -q
/srv/abit-dev-linux/abit-ai/venv/bin/python -m unittest discover -s backend/tests -p 'test_agentic_execution_api.py' -q
/srv/abit-dev-linux/abit-ai/venv/bin/python -m unittest discover -s backend/tests -p 'test_agentic_executor_flow.py' -q
ABIT_PYTHON=/srv/abit-dev-linux/abit-ai/venv/bin/python npm run api:verify
ABIT_PYTHON=/srv/abit-dev-linux/abit-ai/venv/bin/python npm run api:verify:integration
```

The optional manual `scripts/test_agentic_codex_live.py` uses the actual CLI and
MCP transport against an isolated synthetic gateway task; it does not modify ERP
or a business repository.

The installed production runtime contains valid changes beyond this checkout.
Activation must use the established maintenance-admission, exact-source backup,
runtime-preservation and drain procedure. Do not copy the entire backend over it
or restart active original runs. This source implementation alone does not claim
production activation or a completed live ERP business task.

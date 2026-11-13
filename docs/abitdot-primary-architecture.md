# ABIT.AI — abitdot as primary execution coordinator

**Requested behavior:** Every ABIT.AI agent run owned by the linked user should be routed to that user's personal **abitdot first**. It is not a ChatGPT Relay fallback. abitdot receives the entire original prompt, related ChatGPT Project identity and saved instructions, earlier conversation context, ERP task metadata, and idempotency identity. abitdot decides which authorized models, tools and servers to use, with **no Work mode** and no periodic Dot polling.

## Execution order

1. Existing ABIT.AI scheduler or Play button creates a normal authorized run. It verifies the same owner and the required automation permissions.
2. If **ABIT_DOT_PRIMARY_ENABLED=true** and the automation's owner matches the verified `ABIT_CHATGPT_DOT_OWNER_ID`, the new Dot-first dispatcher executes *before* any prior model dispatcher.
3. Context is prepared from the exact owner-scoped automation and its owned project folder. The user's full original prompt remains intact. Attached project metadata includes `chatgpt_project_id` and `system_prompt` where saved, and prior automation-owned chat history. For general project coordinators without an ERP task ID, the original prompt remains the project's authoritative description; don't invent an ERP task or project.
4. ABIT.AI submits one immutable run envelope to a **registered, owner-authenticated, documented Dot adapter** and requires a receipt bound to owner, Dot ID, automation ID, original run ID, and idempotency key.
5. On a **verified accepted receipt**, ABIT.AI records `delegated` (not `success`) and leaves the existing ERP task-status subsystem in control of actual completion and evidence.
6. On an unknown delivery outcome, it records `deferred` and **never resubmits** or starts an alternate executor for the same run. The Dot is responsible for checking prior effects and choosing a safe next action.
7. An automation owned by a different ABIT.AI user is never silently forwarded to somebody else's personal Dot. A second owner needs their own authorized Dot mapping.

No new timer, scheduled Dot polling, or ChatGPT Work task is created.

## Source

- `backend/open_webui/utils/abitdot_primary.py` — owner/project validation, full context envelope, secrets redaction, durable SQLite idempotency ledger, authenticated acceptance contract and duplicate suppression.
- `backend/open_webui/utils/automations.py` — early Dot-primary execution hook **before** calling the original configured model. Previous normal model logic, attachments and ERP task-status integration remain unchanged.
- `backend/open_webui/routers/automations.py` — existing authorized `POST /api/v1/automations/{id}/run` API describes the new optional routing semantics.
- `backend/tests/test_abitdot_primary.py` — offline owner isolation, project preservation, receipt, deduplication, no-retry and source-order tests.

The internal ABIT.AI adapter entrypoint is `app.state.ABIT_DOT_PRIMARY_SEND(envelope)`. Its input is the owner-bound envelope and its output must be a verified provider receipt with fields:

```json
{
  "owner_id": "<same verified ABIT.AI user>",
  "automation_id": "<same original automation>",
  "run_id": "<same original run ID>",
  "dot_id": "<same configured owner Dot ID>",
  "idempotency_key": "<same envelope digest>",
  "status": "accepted",
  "receipt_id": "<documented provider delivery identifier>"
}
```

An ordinary LLM answer, undocumented ChatGPT browser request, or Slack bot-authored message is **not** a valid Dot acceptance receipt.

## Activation gate — do not bypass

Only enable `ABIT_DOT_PRIMARY_ENABLED=true` **after** an actual supported, owner-authorized Dot invocation adapter is available, deployed and accepted in a live test. `ABIT_CHATGPT_DOT_URL` identifies the Dot but is a web page, **not** an API. The existing browser relay has repeatedly returned `unknown` outcomes. The earlier Slack messages show no verified owner Dot execution and must not be interpreted as receipts.

No public programmatic personal Dot execution API has been verified in the connected ChatGPT tools or OpenAI Help Center. The official Dot product allows owner-authenticated conversations in ChatGPT and Slack where configured, but only the Dot owner can direct it. Never forge a user message, bypass the ChatGPT host's browser security restriction, or claim that a Slack-bot message started work.

**Until the supported connection exists, leave the production feature flag off.** This branch implements and tests the Dot-primary contract and protects existing agents, but **does not** claim that any Dot task was actually started, executed or completed.

## Production and ERP protection

Server: **ABIT-DEV-LINUX**. Project/source branch: `feature/abitdot-primary-20261113`. The live service remains in `/srv/abit-dev-linux/abit-ai/current/runtime/open_webui`, and its existing `erp_task_runtime.py`, `erp_task_resolution.py`, deployment settings, chats, model/selected-model settings, database and Node relay must not be blindly replaced.

Before any later activation, inspect the live task/agent run, make verified database and file backups, install only changed source files, apply the feature flag only for the verified Dot owner, and test Play, scheduled execution, project context, receipt, deduplication, ERP status and failure cases. Production release approval and financial/stock posting gates remain required.

# ABIT-DEV-LINUX: abitdot relay failover

**Linux source:** `/srv/abit-dev-linux/abit-ai/deployment/abitdot-relay-linux-20261113/source`
**Git branch:** `feature/abitdot-relay-linux-20261113`
**Running system:** `https://abit.ai` (Open WebUI on port 8888)
**ERPNext:** `https://erp.abit.ai` (separate application)

## Routing, in order

1. ABIT.AI executes the ERP-linked agent using its existing ChatGPT Project chat.
2. If the ChatGPT project relay fails or the chat is unreadable, the **failure event** gathers the original prompt, project instructions, saved chat, task ID, run identity, and any earlier relay job.
3. ABIT.AI uses its existing owner-authenticated relay to deliver the context to **abitdot**, in an isolated ChatGPT browser tab associated with the same ChatGPT account.
4. If abitdot definitively did not receive the task (pre-submit failure), the **existing selected-model** fallback remains eligible exactly as before.
5. If either submission outcome is uncertain, don't execute it again. Preserve the event and earlier job for reconciliation. No polling agent or Work mode is created.

Only the actual failing task triggers the Dot. Slack is reserved for a genuine showstopper or an authorization/approval requirement.

## ERP status protection

The Linux-specific `utils/erp_task_runtime.py`, `utils/erp_task_resolution.py` and the server-side status synchronization are **not changed** by this feature.

The existing `_with_erp_task_context`, `_run_scheduled_completion`, scheduled `run_id`, and `_record_run` logic in `utils/automations.py` remain in place. Dot answers are committed only against the original relay job and assistant message ID; a Dot transport acknowledgement alone does not mark an ERP task complete.

An original `queued` or `running` ChatGPT job never gets a competing Dot execution. An `unknown` original job transfers its status and evidence and explicitly instructs abitdot to verify the original attempt and prevent duplicate side effects before acting.

## New Linux files and changed Linux source

- `backend/open_webui/utils/abitdot_failover.py`: owner-scoped, deduplicated failure ledger, complete non-secret context, Dot dispatch, no polling.
- `backend/open_webui/routers/chatgpt_relay.py`: actual relay/chat failure event hooks, Dot-first routing, preserve selected-model fallback and original job recovery.
- `backend/open_webui/utils/automations.py`: one recovery hook in the existing Linux ERP-aware scheduler, no ERP status handling replacement.
- `deployment/windows/aibit-relay/aibit_dot_handoff.cjs`: shared Linux relay helper for owner, ChatGPT account and URL validation.
- `deployment/windows/aibit-relay/aibit_relay.cjs`: **Linux's existing relay code** with one additional owner-authenticated `/dot/handoff` route.
- `backend/open_webui/utils/model_fallback.py`: exact copy of current Linux live implementation; included to preserve the already-selected model behavior, not to change it.
- `backend/tests/test_abitdot_failover_linux.py`, `deployment/windows/aibit-relay/tests/abitdot_handoff.test.cjs`: offline contract tests.

## Configuration required for activation

Both the Python ABIT.AI service and the Node ChatGPT relay service must have:

- `ABIT_CHATGPT_DOT_URL`: the user's owned `https://chatgpt.com/dots/<owner-id>~<dot-id>` URL, without URL parameters.
- `ABIT_CHATGPT_DOT_OWNER_ID`: the **verified ABIT.AI internal user ID**, not an email or Slack ID. Must match the automation owner.

Do not commit those settings or any browser cookies. The existing relay token is kept in the current Linux secrets path and never copied or exposed.

If the Dot route is not configured, eligible work proceeds to the old selected-model fallback. Other users cannot be routed to this user's private abitdot.

## Deployment safety

The live runtime is `/srv/abit-dev-linux/abit-ai/current/runtime/open_webui`. The Node relay currently runs from `/srv/abit-dev-linux/relay-runtime/service`. The live app version is Open WebUI 0.11.4, with local ERPNext status customization.

Do not copy the whole Git worktree over the live backend or replace data. Deploy **only** the specific changed backend modules and the matching Node files after a data and file backup. Before any restarts, inspect active processes and preserve `abit-ai/data/webui.db`, WAL/SHM, uploads, vectors and all current runtime/environment settings. Use the existing systemd services, never kill port 8888 casually.

Verify `https://abit.ai/health`, `https://abit.ai/api/config`, logged-in agent and chat behavior, ERP task status/evidence and the exact Dot handoff. Roll back file-level changes if a smoke test fails.

### Test evidence

- 10 Linux Python offline Dot ledger/routing/ownership tests passed.
- 5 Linux Node tests passed (including existing tab ownership and no-double-send tests).
- 16 existing selected-model fallback tests passed unchanged.
- 7 scheduled-automation attempt tests passed.
- Backend Python modules compile; Linux Node relay passes syntax validation.

**Limitations:** A real ChatGPT Dot browser handoff has *not* been validated with an authorized live session. This source branch is not itself a live deployment and does not claim completion of an ERP task.

### Additional Linux verification and release status

- Targeted offline tests: **10 Dot ledger/retry tests**, **5 Node relay tests**, **16 existing selected-model fallback tests**, **7 scheduled-automation tests**, all passing.
- `npm run api:verify`: API source guard and 39 developer API/UI tests passed; full command blocked by absent `node_modules/vitest` in the isolated Linux worktree. No package installation or live runtime changes were made.
- `npm run api:verify:integration`: API source guard passed (594 operations, four justified hidden operations); the script rejects this isolated Git worktree as a non-authoritative checkout. No live application import was used.
- **No live activation**: this staged source is not proof of a real ChatGPT Dot UI send, and no production restart, database change or runtime replacement was performed.

### Linux-only isolated relay endpoint smoke test

The staged Node relay was started on **127.0.0.1:8877** with a synthetic token, no Dot destination configured, and without contacting ChatGPT or ERP. An unauthenticated POST to `/dot/handoff` returned HTTP 401. The authenticated but unconfigured request returned HTTP 424 with `DOT_NOT_CONFIGURED` and `safe_to_fallback: true`. The synthetic process was stopped afterward. The staged source includes Linux's existing `response_markdown.cjs` dependency for this route to load without changing the live relay.

# ABIT.AI — abitdot as primary via existing ChatGPT Relay

## Execution contract
For owner-enabled automations, the existing **ABIT.AI Play button and scheduler** dispatch to the personal **abitdot first**, before the ChatGPT Project chat or any selected alternative model.

The originating automation owner, project ownership, identity, permission and prompt are verified. The complete original prompt, the related ChatGPT Project ID and saved instructions, previous automation-owned chat context and ERP task metadata travel together. The Dot coordinates authorized tools, models, agents and servers independently; it is not a model fallback. No ChatGPT Work mode or Dot polling.

The existing ERPNext task, assignment, completion and evidence subsystem stays authoritative. A verified *Dot chat reply* means a completed conversation turn, not a verified business-task completion.

## Implementation in ABIT-DEV-LINUX
- `backend/open_webui/utils/abitdot_primary.py`: verified owner/project envelope, deterministic idempotency keys, durable SQLite owner-scoped dispatch ledger, reply storage, duplicate protection and owner-only readback.
- `backend/open_webui/utils/abitdot_primary_relay.py`: concrete sender using the **existing authenticated localhost ABIT.AI ChatGPT Relay endpoint** `POST /dot/handoff`. This is ABIT's own endpoint, **not a public OpenAI Dot API**. It validates the originating user's synced ChatGPT account, sends a stable job ID and the complete source context, and parses a verified final Dot response.
- `backend/open_webui/utils/automations.py`: Dot-first routing before existing relay recovery and model execution; preserves existing task-status sync. The authenticated relay is the default sender; an explicitly registered sender may override it.
- `backend/open_webui/routers/automations.py`: existing owner-authorized Play route `POST /api/v1/automations/{id}/run` remains compatible. A new owner-authorized `GET /api/v1/automations/{id}/dot-primary-runs` exposes status, receipt, reply and conversation URL, not the full private prompt.
- Tests: `backend/tests/test_abitdot_primary.py` and `backend/tests/test_abitdot_primary_relay.py`.

## Verified receipt semantics
The ABIT.AI local relay validates the owner and browser account and returns `dot_handoff:true`, the matching account fingerprint and final response text. The adapter constructs a deterministic response digest plus original owner, run ID, Dot ID and automation ID as the local receipt. This **proves only that the authenticated ABIT relay returned the Dot turn**; it is not an official OpenAI Dot task-acceptance/execution receipt.

The ledger statuses are `prepared`, `dispatching`, `delegated`, `unavailable`, or `unknown`. Repeated Play clicks do not start a second executor while a previous run is unresolved. An unknown outcome never triggers an unsafe replay or alternate executor. The Dot reply remains accessible through the owner-only status endpoint; it does not automatically mark an ERP task completed.

## Rollout and safeguards
The feature flag `ABIT_DOT_PRIMARY_ENABLED` defaults to **false**. The existing owner-specific `ABIT_CHATGPT_DOT_URL` and `ABIT_CHATGPT_DOT_OWNER_ID` determine which user's personal Dot can receive a run. Every other user's existing automation stays on its prior execution path.

The integration uses **ABIT.AI's previously installed authenticated relay**, not a newly invented ChatGPT API. Earlier live browser handoffs have produced uncertain outcomes. The localhost synthetic-HTTP adapter test is **not** a live Dot acceptance test. A real owner-authorized response must be verified before turning on this production routing. Don't bypass ChatGPT browser-host security restrictions, forge the user's messages, or claim task execution without evidence.

Live source is not replaced wholesale; preserve Linux-specific ERP task-status modules, all users/chats/models/files, and the existing selected-model setting. Before any production release obtain its required approval, back up the database and exact files, and verify health, login, project sidebar, Dot reply, task status and other users' unaffected automations. No ChatGPT Work mode.

## Linux source verification
- 62 targeted Python regression tests passed: 15 Dot-primary orchestration, 8 real localhost HTTP adapter tests, 16 older Dot-failover tests, 16 selected-model tests and 7 automation-attempt tests.
- The full npm api:verify source, frontend and Svelte check passed using isolated Node 22 and official npm registry dependencies: 37 frontend tests passed and 0 Svelte diagnostics.
- The api:verify:integration command passed the API source guard (595 declared operations) but refused an isolated Git worktree; it was not reported as a passed application integration run.
- Read-only live InterMarket fixture: complete original prompt 5,425 characters, owned project folder, 5 saved chat messages, one verified owner account and 31,378-character relay payload. No request was submitted to a live ChatGPT Dot during this check.
- Only the source branch is changed. Production remains on the prior model routing because the feature flag remains disabled, and no real Dot execution receipt was observed.

## Source limitations
The existing authenticated relay is a local ABIT.AI browser-relay service. Its prior handoffs have had uncertain outcomes. A verified synthetic HTTP response is NOT a real ChatGPT Dot acceptance. ChatGPT host security policies must be respected; do not bypass browser-access denials with alternative remote control.

## Production canary and submission optimization
- The owner-scoped InterMarket canary 9ad57aa6-ba35-41e8-a8aa-ca45978a6d1a was enabled on ABIT-DEV-LINUX only. The live Play API accepted one run; its primary Dot ledger recorded an ambiguous result and deferred the original ERP task without replaying it.
- Redacted Node relay evidence identified CHATGPT_SUBMISSION_UNCERTAIN, with submitBoundary=true but no verified message_submitted event, after approximately 35 seconds. Do not silently reset that run or replay the same prompt.
- The source now retains the original 5,425-character prompt exactly once and replaces only byte-for-byte duplicates with references to the canonical prompt. The real owner/project envelope was verified to contain 5 previous messages and a 15,088-character relay message.
- The Node relay replaces three expensive full-page DOM overlay candidate scans with targeted dialog/overlay selectors. Its separate page-error detection remains unchanged. This is an optimization; it cannot itself prove whether any earlier prompt was accepted.
- Tests: 16 Dot-primary source/runner tests including deduplication, 8 local HTTP transport tests, and 7 Node/owned-tab tests passed. Service health and ERP authority must be verified again after rollout.
- Only expand from the InterMarket canary to all automations for the verified owner after a live Dot response is confirmed. Other owners need their own mapping. A Dot response is not a business-task completion.

## Linux endpoint/credential correction
The first live Dot-primary InterMarket canary never reached the Node relay. The ABIT.AI Python adapter used a legacy relay URL on localhost port 8819 and an absent legacy Windows-named token file. The actual existing ABIT-DEV-LINUX Node relay listens on loopback port 8810 and uses the protected file /srv/abit-dev-linux/relay-runtime/secrets/relay.token.

The primary adapter now reads ABIT_DOT_PRIMARY_RELAY_URL and ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE from the private runtime environment. It verifies loopback-only delivery and the exact approved Linux secrets-file path. The legacy ChatGPT Project client and its selected model settings are not changed.

Before activating, authenticate a no-submit (invalid-job) POST against the existing Node /dot/handoff route; HTTP 400 proves the server recognized the owner-scoped token while no Dot prompt was sent. A short diagnostic prompt should be attempted only once and its event recorded. If the relay returns unknown after crossing submission, do not blindly retry. Records produced under the provably missing token path may be marked unavailable with an audit reason, not completed.

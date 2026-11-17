# Current Core cancellation-preserving activation package

**Source prepared and validated; not activated.** The current package preserves the externally installed source observed after 23:21 UTC on 16 November 2026. The deployment controller still requires a fresh reviewed runtime binding and an existing authorized Main administrator credential. Neither is included. This work performed no live cutover, gate transition, browser action, model send, ownership release or business operation.

Repository: /srv/abit-dev-linux/abit-ai-platform. Branch: feature/agov-api-live-20261113. Root reported commit 6c153f9b0ed37007436103f6548b252bc1e54dec after its separate evidence commit. No Git mutation was performed by this package preparer.

## Immutable activation artifacts

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| deployment-manifest.json | 172965 | 6eb9e44f54af431b57f2bf40368aca0a0bf28aacca212d1892a64baa9286805e |
| activate_once.py | 24834 | e55353c64be8ef47dd561d080925a8e63355c204b591aad945b343c01fe13145 |
| activation_support.py | 9611 | 177ea12a77c2ce52439d55e02228c651dcfcbca0cfde42433765ee1e5107403c |
| evidence/freeze-validation.json | 916 | a9426d97e44287b587ea18831c04a0b3a3c9038cb6b7e83e2af297a06d269066 |

The activation manifest binds an 80-file primary cohort and 726 additional source pins. Together they cover all 373 captured current runtime source/resource paths. The five replacement runtime paths are excluded from the additional unchanged-dependency map, so post-install checks require their candidate hashes rather than obsolete baseline hashes. Captured source and fixture dependencies remain pinned.

The source snapshot includes 303 original Python modules, migration Python and package resources; the isolated API loader binds 364 Python modules including migrations. Data, uploads, installed frontend/static content, environment files, session data and bytecode are excluded. The exact public package CHANGELOG is retained because current env.py loads it. Source literal review found no credential-pattern flags.

The snapshot is a preservation and test dependency collection. Activation replaces exactly five leaves; it never copies the snapshot wholesale.

## Five source changes

| Candidate under candidate/open_webui/ | SHA-256 |
|---|---|
| utils/agentic_executor.py | 6e6033ab7a6ad0c56842a284b6f113c82e461dcfa5d02543724e06fc5390a2df |
| utils/agentic_result_store.py | 429633df024e05fbac044fbe8e36fb4843464004b3d17a00078c77baa99c11a7 |
| utils/agentic_gateway_contracts.py | 7dad9106e6a806e1e2e91ce595373f74e881616d2750e9bd4745c77977d7ada0 |
| utils/agentic_blocker_review.py | dd228d90bf28d39ef84a219bbc186b81391c69bee2db62411d9d42b20b50d200 |
| routers/agentic_governance.py | 608cd1625e4a5e159797db15008ea25920a00f53cc649bf6e09843bb9087f1a1 |

The executor and result-store changes preserve rejected-contract evidence without completing or releasing a canceled original. They recheck the original provider receipt, packet, ownership and fence before a diagnostic write. Artifact publication must succeed before adapter.record() publishes its outcome. Contract/blocker guidance describes supported outcomes without introducing a QA verdict or bypass.

The router adds only a 367-byte maintenance barrier to reconciliation_loop. A non-open gate holds reconciliation before recovery, bridge or outbox work. The rest of the current router, including the externally added event documentation, remains byte-identical.

Composition proofs and exact diffs are retained. In particular, execute() and the external native PM enrollment instruction remain unchanged, including serialize_project=True. The new project queue, governance admission, SQLite lock fixes, maintenance helper and orchestrator are preserved. The full historical orchestrator baseline was unavailable; no complete historical diff is claimed.

## Controller boundaries

The new exclusive operation namespace is:

/var/lib/abit-ai-erp-rollouts/core-original-hold-cancel-preserving-after2321-20261116-v2

Main is abit-ai.service at http://127.0.0.1:8888. QA is abit-ai-erp-review-qa.service. The pinned relay health endpoint is http://127.0.0.1:8810/health.

The controller requires normal administrator authentication for Main /api/tasks and exactly an empty tasks list before any relay health call. The credential must already exist and be supplied through a private inherited file descriptor. No signing key, token minting, credential discovery or browser login is included.

The relay health endpoint can allocate its dedicated browser tab and check login. This known readiness behavior is explicitly bound in the fresh runtime binding. It sends no model message. The response must be ready, authenticated and target-ready with zero active turns.

The controller preserves the exact original run and invocation:

- Run 5955703a-4a31-5a33-8265-63a5299bdace.
- Invocation agov-parent-be395df9d5464ee35b7564bd2cb62352709bd19a23922db0.
- Governance cancel_requested, one dispatch, original task owner and fence 2.
- Task control stopped; four completed gateway calls; no local/final result.
- Provider delegated with exact retained envelope/response/receipt hashes.
- Original AutomationRun still running.

cancel_requested is not a cancellation acknowledgement. The controller neither confirms cancellation nor releases the task owner/fence, completes the automation, invokes recovery or reuses a provider send.

Before any stop, it requires exact source/process/configuration/database identity bindings, closed admission at the expected epoch, no other active work, and a verified SQLite backup. After backup it refreshes the full quiescent proof: genuine Main tasks, relay state, gate epoch, work counts, original projection and original journals. Gate reopening or a newly active relay turn during backup prevents every stop/install.

It backs up the exact reviewed eleven-database set both before and after stopping Main/QA, using SQLite's backup API and quick_check. Each backup is consistent independently; no cross-database atomic snapshot is claimed. It retains replaced source and generated bytecode, preserves file metadata, and leaves persistent data in place.

The replacement services must retain UID, argv, cgroup and policy while having new process identities and zero automatic restarts. Source, original journals, health/config and authenticated empty tasks must validate before reopening admission. Normal background reconciliation can resume after reopening. Changed-feature and authenticated browser verification remain required after a real activation.

Unknown acknowledgement, changed source, stale identity, nonempty work or failed preservation holds the operation. The controller does not automatically retry or roll back. A consumed namespace must be observed and reconciled before any separately reviewed recovery action.

## Completed current validation

| Validation | Original job | Observed result |
|---|---|---|
| Current candidate and startup tests, initial controller fixtures | 3ff8e598-1e41-438a-9452-6904e7c0d956 | Terminal 0; 3 startup + 18 candidate + 11 controller cases |
| Pre-stop refresh follow-up | fb0bafad-3417-469f-9307-1f36fcaa8f67 | Terminal 0; five flow cases, including gate/relay drift during backup |
| Additional-resource read bound | 0156e6ff-074c-4b22-8a97-284a38044e85 | Terminal 0; exact 1.18 MB resource accepted; changed hash and >2 MiB held; default 1 MiB retained |
| Current isolated API integration | 3afe2cc6-27f4-4b77-81b8-293735b6cd65 | Terminal 0; 91 passed, zero failed; 607 registered API operations |
| Full current api:verify wrapper | a7db9def-8e92-4a06-a39e-0fb8670ff3b9 | Terminal 0; all components passed |

The current integration imported 191 captured modules, including the complete current Main source. Its 767 input pins stayed unchanged. It used fresh fixture data/users and ASGI requests without normal application lifespan, production configuration/data or provider connections. The application audit guard blocks network connections, subprocesses and live application file access. Trusted fixture setup prepares actual stdlib platform metadata before that guard; this can execute read-only uname -p, like the launcher's existing repository-metadata lookup.

The full api:verify used the exact package-script command, node scripts/check-api.mjs --verify, with the unchanged wrapper in a disposable tree containing current captured Python, the five candidates and unchanged frontend source. It passed:

- 635 statically declared HTTP operations, with 12 precise justified HTML-shell hidden operations.
- 15 source-guard unit tests, including two new Slack registration cases.
- 39 current developer API/HTML-helper tests.
- 37 Vitest tests in the two specified suites.
- Developer portal Svelte/TypeScript diagnostics with zero errors and zero warnings.

All 373 current source/resource pins and all full-gate input pins remained unchanged. No existing checks were skipped. API6's passed integration suite was not repeated.

Static union counts include conditional registrations; they are a different measure from the isolated application's registered-operation count. The frozen deployment manifest's earlier 606/4 source metrics describe canonical-tree coverage before the complete current-source gate. verification-supplement.json and evidence/api-verify-complete-current-1.json record the authoritative current 635/12 result without modifying the frozen deployment manifest.

The controller has 14 unique fixture cases across its 11-case initial run, five-case pre-stop follow-up and one-case resource follow-up. Follow-up changes were limited to run() and check_sources(); other controller functions remained byte-exact. The 18+3 product tests were not repeated for harness-only corrections.

## Current-source verification overlay

The unmodified canonical checker initially rejected current utils/slack_ui.py because it recognized only the developer HTML registration helper. That actual failed static gate is retained as f80f05a2-386a-4bc6-bbfe-c88ca6c194c8, terminal 1.

api-verification-current/ contains a separate reviewed verification correction:

- Explicit recognition of register_slack_ui only when called with the Main app.
- Four exact paths: /dot/slack, /dot/slack/, /dot/slack/authorize and /dot/slack/authorize/.
- Only GET and HEAD exceptions for the existing HTMLResponse shell.
- Unknown-router, undocumented-operation and stale-exception failures remain active.
- Tests prove that missing exceptions fail, an extra POST exception fails, and another app target fails.

This overlay changes no API registration, visibility, authentication or runtime file. Canonical checker/policy files and the five deployment candidates remain unchanged. Its exact inverses, source references, diffs and passing full-wrapper evidence are included.

## Retained failures and acknowledgement limits

All five failed integration setup attempts remain as evidence. They were resolved without weakening application isolation or removing assertions:

1. The guard blocked an implicit Ollama port probe. An explicit isolated URL prevents the configuration probe.
2. Mixed canonical/current automation source lacked SCHEDULED_RUN_LEASE_NS. The full actual current code closure was captured.
3. The isolated package lacked its public CHANGELOG resource. Its exact installed bytes were captured.
4. An imported SDK's platform user-agent attempted uname -p under the application process guard. Actual stdlib platform metadata is prepared during trusted fixture setup instead.
5. The fixture serialized an unrefreshed ORM object after closing its session. The current SQL-derived is_stale property exposed that mismatch. The fixture now uses the real Automations.get_by_id() producer, which materializes and serializes within its context; no production model field was initialized or changed to hide the failure.

The first new freeze builder stopped on the older tool-result wrapper format, job 921db88c-d2ab-433f-83af-265e9738d6e8, terminal 1, before writing a deployment manifest. The corrected distinct freeze_current_v2.py produced the immutable manifest and success artifact above.

Its original tool acknowledgement was interrupted before the caller received a job ID or terminal status. No recovered original process exit is claimed. evidence/freeze-acknowledgement-unavailable.json records this limitation. The freeze writer was not rerun. Separate read-only artifact verification and independent acceptance establish the source handoff; they do not fabricate the missing original process acknowledgement.

## Historical package and remaining activation work

The predecessor core-cancel-preserving-activation-after7614-20261116 remains frozen as HELD_STALE_RUNTIME_BASELINE_DO_NOT_EXECUTE. Its manifest SHA-256 is 689d7ef1a8627aaa5fa4e4ef7b7d48048f366557505c84cd598f8ed99866bfc5. Its original failed freeze, completed historical validations and exact old files are retained. It has no deployment manifest and is not an execution candidate.

Root owns Git, any fresh runtime observation and any deployment. This package does not include Root's separate live-health/original-state evidence in its inventory. Before execution, Root must provide a fresh reviewed binding matching binding.schema.json and an existing valid Main administrator credential, recheck current pins and ownership, and verify actual post-activation behavior. No fresh binding, production credential, live cutover or live feature verification is claimed here.

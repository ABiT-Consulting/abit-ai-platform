# Original restored-target diagnostic

This source package adds one explicit read-only diagnostic mode to the existing internal `POST /dot/observe` operation. It answers whether the exact sealed original root and full governed envelope are currently mounted in an already-open target at the configured owner's exact Dot landing URL. A positive result is diagnostic evidence; it does not select a replacement target, recover a result, or authorize another submission.

## Exact runtime scope

Apply only the two paths in `manifest.json`, after verifying every preimage and unchanged prerequisite hash. `forward.patch` and `rollback.patch` were both applied with `--fuzz=0` to a fresh scratch reconstruction; every resulting postimage and restored preimage condition matched. The immutable current runtime preimage is included. The canonical Git source predates several valid runtime changes, so the full branch diff is not a deployment overlay.

| Path | Purpose |
|---|---|
| `aibit_relay.cjs` | Route the explicit mode through existing primary-token authentication and Core original-observation authority. Default original observation remains unchanged. |
| `original_dot_target_probe.cjs` | Perform a bounded, fresh, read-only identity check over existing configured landing targets. |

The eight files under `reference/` are unchanged runtime prerequisites used for isolation and provenance. They are not deployment payloads. There is no Core, schema, configuration, immutable-selector, capture or browser-registry change.

## Internal request and response contract

The caller uses the existing server-assembled original observation payload, including the original owner, account fingerprint, durable job reference, exact message envelope and governance scope, and adds `diagnostic_mode: "restored_target"`. The existing primary relay credential remains server-side. Secondary relay credentials cannot invoke this operation. Unknown diagnostic modes receive HTTP 400. Existing authentication, account-linking and Core scope denials remain effective.

This mode first obtains the existing Core observation proof. The diagnostic requires a matching durable original identity and sealed root whose saved location is the configured Dot landing URL. It neither constructs a replacement original nor accepts a caller-provided target. It does not run for an already-present original target or a saved canonical conversation selector.

Only already-open page targets at the exact configured landing URL, optionally with one trailing slash, are candidates. Canonical conversation URLs, other Dot URLs, nested paths, query strings, fragments and other origins are excluded. The bounds are 128 listed targets, 16 candidate landing targets, and a 40-second diagnostic budget after initial proof; the operator client must allow that bounded diagnostic and its proof requests to finish. Each session is closed, including an attach that resolves after timeout.

Each candidate must pass a fresh account check, two executions of the actual governed DOM reader without a cached root, an unchanged sealed root and full envelope, another account check, and unchanged landing location. The reader filters its output inside the browser. No original prompt, response, root ID, target ID, account fingerprint or URL is returned by the diagnostic. The candidate inventory, immutable binding and Core observation authority are checked again before a complete result.

The response uses `protocol: "v1_restored_target_diagnostic"`, `read_only: true`, `dot_handoff: false`, `safe_to_replay: false`, an empty response and null message IDs. `target_probe` contains a fixed code, `complete`, the scope `currently_mounted_configured_owner_dot_feed`, and bounded counts for candidates, examined targets, exact matches, wrong accounts, different roots, ambiguous roots and unreadable targets.

| Code | Meaning and next supported step |
|---|---|
| `ORIGINAL_RESTORED_TARGET_MATCH` | One exact original was found in the stable, fully inspected candidate set. A separately reviewed selector-continuity change is still required before ordinary observation can use that new target. |
| `ORIGINAL_RESTORED_TARGET_ABSENT` | No exact original was found in the currently mounted, inspected configured-owner feed. This does not establish absence from provider history or virtualized/unloaded content. |
| `ORIGINAL_RESTORED_TARGET_AMBIGUOUS` | More than one matching target or ambiguous full-envelope roots. No replacement is selected. |
| `ORIGINAL_RESTORED_TARGET_ROOT_MISMATCH` / `ORIGINAL_RESTORED_TARGET_ACCOUNT_MISMATCH` | Identity evidence conflicts. Keep the original blocked. |
| `ORIGINAL_TARGET_PROBE_CHANGED` / `ORIGINAL_TARGET_PROBE_INCONCLUSIVE` | A target, account, binding or read could not be held stable. No complete match or absence is claimed. |
| `ORIGINAL_TARGET_PROBE_TIMEOUT` / `ORIGINAL_TARGET_PROBE_LIMIT` | The complete bounded inspection could not finish. No partial result grants authority. |
| `ORIGINAL_TARGET_STILL_PRESENT` / `ORIGINAL_CANONICAL_RECOVERY_AVAILABLE` | The diagnostic is unnecessary; the existing guarded observation path remains the applicable path. |
| `ORIGINAL_TARGET_PROBE_SELECTOR_UNAVAILABLE` / `ORIGINAL_TARGET_PROBE_UNVERIFIED` / `ORIGINAL_TARGET_PROBE_ALREADY_TERMINAL` | Required original evidence or authority is unavailable, or a terminal selector already exists. The diagnostic performs no recovery. |

Only the exact-match and mounted-absence outcomes have `complete: true` and `ok: true`. Results and errors produced by the new probe helper contain only fixed codes, fixed denial text and bounded counts; its operational exceptions never include raw provider, account, credential or transport text. The initial existing Core observation proof occurs before the helper and retains the existing screened gateway-denial handling, which can include an allowed Core detail. Existing authentication and account-linking errors are unchanged.

## Preserved recovery boundaries

`chat_thread_tabs.recover` still requires an immutable canonical original conversation URL. `original_dot_bindings.rememberRoot` still rejects a different target for an original saved only at the landing URL. A successful diagnostic cannot bypass either rule. The focused regression executes both unchanged guards after a positive diagnostic and verifies no allocation or selector mutation.

This mode does not navigate, create or close a browser target, touch the composer, click Copy or Send, run native-source recovery, invoke the ordinary result observer, settle/reject a capture, write a selector, or modify the browser target registry. Session attachment and detachment use the existing relay reader; an existing page may be resumed by the existing session-open implementation. Tests use controlled sessions and do not touch a live browser.

## Verification and release boundary

Run `python3 -B deployment/run_original_target_probe_tests.py` in the owned worktree. It reconstructs only the pinned current modules in a fresh isolated directory, checks both runtime files' syntax, and runs actual DOM-reader, actual relay-handler/authentication and immutable-store regressions against synthetic data. The required API gate uses a separate copy of the already verified current Core API harness, with isolated state, disabled external services and resource limits. See the committed evidence directory for exact commands, hashes, initial fixture failures, corrections and final receipts.

No live activation or original recovery is claimed. Activation requires a separate reviewed relay procedure that preserves the current Chrome process, original bindings/captures and existing account/session, and verifies the actual provider is idle. The original missing-tab record must not be rewritten or replayed.

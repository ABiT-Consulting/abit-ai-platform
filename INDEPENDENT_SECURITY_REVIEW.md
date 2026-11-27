# Independent isolated security review — Prompt 06 ownership recovery

Review target: `efa11f81747239af442c3c7b43a3e361ef0fb591` (parent `d6e95fe46a148ce04efccd0653256173263c4f3d`).

Review method: a separate detached Git worktree at the exact commit, clean at inspection, with independent source reading, AST probes and a fresh execution of the 14-test suite. The detached worktree was not modified. This is an independently isolated review process, not a claim that a second human or separate model identity performed the review.

## Decision

**REJECT — not safe to install or use for production ownership recovery.** The 14 tests pass, but important authorization, fencing, durability and integration guarantees are not implemented by the code they purport to test.

## Findings

1. **Critical — receiver identity is caller asserted.** `recover_operator.py acknowledge --receiver-id` accepts an arbitrary receiver label and calls `acknowledge_recovered_operator` directly. There is no authenticated ABIT.AI user/session boundary. The caller can claim the receiving identity; this is not genuine receiver acknowledgment.
2. **High — administrator identity is not authenticated end-to-end.** The CLI hardcodes `admin_id='jimran@abitcons.com'` and `admin_role='admin'`; the library accepts those strings. The root guard checks only EUID, `SUDO_USER`, and hostname. It does not verify `SUDO_UID` against the OS account or establish the sudo invocation context.
3. **High — public production functions expose test bypasses.** `require_root=False`, caller-supplied lock paths, file paths, trusted UIDs and clock values are public function arguments. A production caller can disable the root guard or redirect writes/locks.
4. **High — process fencing confuses inaccessible with absent.** `_proc_start_ticks` returns `None` for every `OSError`, and `fenced` treats any value other than the old start ticks as proof the process stopped. Permission/read failures therefore satisfy the fence.
5. **High — fence evidence is not verified.** Workstream, owner-session and process evidence hashes are checked only for 64-character syntax. No evidence file is opened or hashed. Provider requests and browser sessions are not separately inventoried.
6. **High — commit audit is one-sided.** A `COMMIT_INTENT` is fsynced, then the owner record is replaced. There is no durable post-replacement commit marker or supported reconciliation operation for a crash between those steps. The owner record labels itself `COMMITTED` before a durable completion receipt exists.
7. **High — production integration is incomplete.** The helper has no authenticated receiver API. The current LinkedIn activation package remains an unbound read-only preflight and does not consume this recovery record. A receiver-scope file is not the activation controller's protected execution binding.
8. **Medium — filesystem path checks are incomplete.** `O_NOFOLLOW` protects only the final path component. Parent components are resolved by ordinary path operations; audit directory checks and writes are vulnerable to parent/path replacement races. Audit file creation fsyncs files but not the containing directory.
9. **Medium — CLI request/install integrity is not checked.** The request is loaded with `Path.read_text()` without no-follow, owner/mode, size or stable-file checks. The CLI does not verify its own root-owned immutable installation metadata.
10. **Medium — generation and replay semantics are underspecified.** Existing `operator_recovery` content is not strictly rejected unless its status happens to be `COMMITTED`; generation is coerced with `int()`. Receiver crash/retry between binding-file creation and owner replacement has no idempotent recovery protocol.

## Required remediation

- Derive the administrator identity from the actual sudo invocation and OS account; remove caller-supplied role/identity and all production test bypass parameters.
- Replace CLI receiver acknowledgment with an authenticated ABIT.AI session endpoint. The receiver principal must come from the authenticated user object, not request data or a CLI flag.
- Verify every evidence file's root ownership, mode, no-follow path, and actual SHA-256. Require complete provider-request and browser-session inventories for every active workstream.
- Distinguish `/proc` absence/PID reuse from permission and read errors. Require exact process start ticks and exact supervisor cgroup identity.
- Anchor filesystem operations to no-follow directory descriptors, validate owner/mode, fsync containing directories, and make request/audit handling bounded and idempotent.
- Use a durable two-phase owner transition with a post-transition commit marker and a root-only reconciliation command. A crash after owner replacement must remain fail-closed until that marker is verified.
- Add adversarial tests for all above, including real concurrent calls and real PID reuse/unreadable process evidence.
- Keep production installation, owner edit, maintenance gate changes and LinkedIn deployment unapproved until a second review of the corrected commit.

## Verification performed

- Detached review worktree: clean, exact target commit.
- Original 14-test suite: 14 passed.
- Independent static probes confirmed caller-supplied receiver ID, hardcoded administrator identity, permissive `/proc` error handling, missing commit marker, and no authenticated receiver route.
- No production files, owner records, maintenance gate, leases, provider state or service were modified.

## Corrective implementation review — 23 November 2026

The original ten findings have been addressed in the corrected source package as follows:

| Original finding | Corrective implementation | Verification |
|---|---|---|
| Caller-asserted receiver identity | Authenticated API dependency supplies the receiver user; API writes a service-owned HMAC receipt; root CLI alone applies it | API route AST contract test; forged receipt and wrong-user tests |
| Hardcoded administrator role/identity | Direct sudo boundary resolves `SUDO_UID` through passwd and checks `SUDO_USER`, host and sudo parent | administrator impersonation tests |
| Test bypass arguments | Removed public `require_root`, arbitrary paths, caller locks, caller clock and trusted-UID overrides | public signatures and source inspection |
| Inaccessible `/proc` treated as absent | Only ENOENT/process lookup means absent; other read failures deny; start ticks and exact cgroup path checked | PID reuse and unreadable-proc tests |
| Evidence hashes were not checked | Root-owned `0600` evidence files are opened no-follow and SHA-256 recomputed; provider and browser inventories are mandatory | forged evidence, missing inventory and preserved provider/browser tests |
| No durable post-replacement receipt | Snapshot/intent/token precede CAS; owner enters `COMMIT_PENDING_AUDIT`; root-owned `0640` commit marker follows; exact-hash reconciliation handles crash | interruption-before/after-replacement and reconciliation tests |
| No authenticated receiver integration | Authenticated ABIT.AI endpoint; HMAC-signed service receipt; root-only apply CLI | authenticated user, wrong-user, forged signature, replay/idempotency tests |
| Path/TOCTOU and missing directory fsync | Component-wise `O_NOFOLLOW` directory opens, dirfd-relative create/replace, stable file checks and fsync of containing directories | symlink evidence test, filesystem-backed transaction tests and source inspection |
| Unchecked request/install integrity | Root-owned `0600` bounded no-follow request; CLI validates root-owned `0700` installation and `0500` source before importing helper | CLI source inspection and compilation; protected-install checks are not exercised on production files |
| Generation/retry ambiguity | Generation fixed to 1 for the exact unrecovered owner; existing recovery records reject; ack receipt and binding writes are idempotent; no old-owner restoration | already-recovered, generation tamper, crash/retry and stale-owner tests |

The corrected source was copied into a new detached review worktree for a second read-only review and fresh test execution. This is an independently isolated review process, not a claim that a second human or separate model identity performed the review. Production acceptance remains withheld until that second review completes and the API integration suite can run in a Python environment containing FastAPI/httpx.

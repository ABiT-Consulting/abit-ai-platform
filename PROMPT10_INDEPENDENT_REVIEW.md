# PROMPT 10 — Independent security review result

**Decision: NOT ACCEPTED. Do not commit, push, open a release PR, install or use this recovery control.**

Review date: 23 November 2026
Host: `abit-dev-linux`
Review target base: `efa11f81747239af442c3c7b43a3e361ef0fb591`
Review target parent: `d6e95fe46a148ce04efccd0653256173263c4f3d`
Review candidate: `/srv/abit-dev-linux/tmp/operator-recovery-p09-review-candidate-20261123`
Manifest: `deployment/operator-recovery-manifest.sha256`

## Independence and scope

The candidate was inspected in a detached worktree, separate from the implementation worktree. This report is an isolated technical review by the current ChatGPT execution, not an attestation by a different human reviewer or a separately authenticated reviewer identity. A genuinely independent human/security reviewer should still review the corrected follow-up before production approval.

The exact six-file manifest verified successfully. The backend and standalone recovery helper are byte-identical. Python compilation passed. A fresh run of the focused ownership-recovery suite passed 26/26 tests. These positive results do not override the blocking findings below.

## Blocking findings

### SEC-01 — API-key authentication is accepted for a session-only receiver acknowledgment

Severity: **High**
Source: `backend/open_webui/routers/agentic_governance.py:489-507`; `backend/open_webui/utils/auth.py:393-436`.

The acknowledgment endpoint depends on `get_current_user`, but that dependency accepts both JWT/cookie sessions and API keys. Its `sk-` branch resolves a user and sets `request.state.auth_type = 'api_key'`. The endpoint does not receive `Request` or reject `auth_type == 'api_key'`; it passes the resolved user directly to `record_receiver_acknowledgment`.

Consequently, a valid API key belonging to the enrolled receiving principal can satisfy the receiver identity check and write the HMAC acknowledgment receipt. This does not meet the specified requirement that receiver acknowledgment originate from an authenticated ABIT.AI user session. The existing AST regression only checks `Depends(get_current_user)` and caller-ID absence, so it misses this supported authentication path.

Required correction:
- Require the intended interactive ABIT.AI session authentication mode (JWT/session cookie as defined by the application) and explicitly reject API-key authentication for this route.
- Add route-level integration tests proving a valid receiver API key is rejected, while the authenticated receiver's normal session is accepted.
- Verify alternate middleware token sources cannot bypass this distinction.

### SEC-02 — Fenced previous-owner session inventory is passed with the wrong type

Severity: **Medium (fail-closed availability defect)**
Source: `backend/open_webui/utils/operator_ownership_recovery.py:261-270`; `_validate_processes` at lines 167-195.

For `owner_session.disposition == 'fenced'`, the caller invokes `_validate_processes({'processes': session_processes}, 'fenced')`. `_validate_processes` requires its first argument to be a list and immediately raises `RecoveryDenied` for this dictionary. Therefore every valid receipt that identifies and fences previous-owner processes is rejected. Only the `absent` branch can pass this part of the validation.

Required correction:
- Pass `session_processes` directly to `_validate_processes`.
- Add a regression test for a fenced owner session with an absent original PID, and another test proving a still-live matching PID is rejected.

This is fail-closed, but it makes an explicitly supported and documented recovery path unusable and is not covered by the current 26 tests (the fixture only tests `owner_session.disposition == 'absent'`).

## Six original security findings

| Required area | Review result |
|---|---|
| Receiver identity from authenticated session | **Not accepted** — API-key path is also accepted (SEC-01). Identity itself is taken from the resolved user, not caller-supplied input. |
| Administrator identity from actual sudo and OS account | Source checks `SUDO_UID`, passwd account, `SUDO_USER`, EUID, hostname and parent executable; focused identity tests pass. **Source-supported, not production-host exercised.** |
| Unreadable process information fails closed | `_proc_start_ticks` distinguishes missing process from other `OSError`; `_proc_cgroup` denies read errors. PID-reuse/unreadable tests pass. **Source-supported.** |
| Root-owned fencing evidence verified against actual files and hashes | No-follow regular-file reads, root ownership/mode and recomputed SHA-256 are enforced; receipt binds owner hash and exact workstream/job inventories. **Source-supported; no live root receipt exists and no production evidence was exercised.** |
| Durable intent, commit and crash reconciliation | Snapshot/intent/token precede owner replacement; post-replacement marker and exact-hash reconciliation exist; crash tests pass. **Source-supported in isolated filesystem tests.** |
| No test-only bypass in production authorization paths | Production functions do not expose `require_root=False`, caller paths/locks, trusted UID or caller clock. Test patches are in tests. **Source-supported.** |

## Other controls inspected

- HMAC-SHA256 binds receiver ID, owner record hash, token hash, recovery ID and commit-marker hash; root CLI verifies the signature and receipt scope.
- Challenge token is high entropy, stored root-only, hash-bound and consumed through an exclusive receipt creation path.
- Recovery ID is UUID-normalized; existing owner recovery state and generation changes are rejected.
- Owner replacement is protected by the existing lock set, exact hash/inode recheck, atomic replacement and fsync.
- Stale previous-owner identity is rejected; receiver binding is bound to recovery ID/generation/scope/manifest and exact receipts.
- The new owner still requires a separate LinkedIn activation binding; no publication or agent execution is authorized by this helper.
- No live owner record, maintenance gate, lease, provider state, service, or admission was modified during this review.

## Verification evidence

- `sha256sum -c deployment/operator-recovery-manifest.sha256`: all six entries passed.
- `python3 -m py_compile` on backend helper, standalone helper, CLI and governance router: passed.
- `python3 -m unittest -v backend.tests.test_operator_ownership_recovery`: 26/26 passed.
- Source inspection reproduced SEC-01 directly from the existing authentication dependency and route wiring.
- Source inspection reproduced SEC-02 directly from the argument type and callee guard.

## Required next step

Return SEC-01 and SEC-02 to the implementation owner. Make the corrections in the original author worktree, add the missing route and fenced-session tests, regenerate the manifest, and prepare a new review candidate. Any source change invalidates this review target and requires fresh review of the exact new snapshot.

Do not commit or push this rejected candidate. Do not create a PR or trigger CI as a substitute for security acceptance. Do not install the control or alter the live owner record. Shared infrastructure custody, root-owned fencing receipt, measured staging headroom and separate production installation approval remain IT-owned gates.

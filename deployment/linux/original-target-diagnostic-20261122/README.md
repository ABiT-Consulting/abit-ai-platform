# Fixed original-target observation

This operator is restricted to the already-submitted `TASK-2026-06448` invocation `agov-step-e5f3296880a510bb5b63b738cefb0c203e32a5f9175690fe`, run `341ed593-70fb-59bf-83c2-80cbf6f6cbf6`. It is not a dispatch or recovery API.

## Observed result

The single live request completed at **2026-11-22 13:12:39 UTC**, exit 0 and HTTP 200:

- `ORIGINAL_RESTORED_TARGET_ABSENT`, complete diagnostic.
- Zero currently mounted targets matched the configured owner Dot landing feed; zero targets were examined and zero exact roots matched.
- Original invocation, admission, envelope, account binding, selectors, configuration, journals, browser target inventory and catalog remained unchanged. The seven preserved processes and the new relay process retained their exact identities.
- One diagnostic POST; zero new proof, admission, provider-send, navigation, selector-write, settlement or business-ledger operations.

The result is limited to already-open configured-owner landing targets at observation time. It does not prove that the original submission failed, establish a provider rejection, or permit replay. The original remains uncertain.

The root-owned result is `/var/lib/abit-ai-restored-target-activation-20261122/original-06448-diagnostic.result.json`, SHA-256 `6a4b0d6b26378d2cd25937bd5648285a32a4a75b8998a796cef836b95fbf2cfe`. The protected one-use intent is already present. Do not remove it or run another observation through this wrapper.

## Authorization and preservation path

The wrapper pins the independently verified deployed relay, its source/dependency closure, the current process identity, the protected activation manifest and live readback receipt. It reuses the previously reviewed root helpers for bounded file reads, query-only SQLite reads, owner/process checks and preservation hashes. No application/database startup is imported.

The actual pinned `verified_dot_payload(envelope, account, admission=False)` validates and reconstructs the saved original. It does not invent or refresh an admission capability. The request adds only `diagnostic_mode: "restored_target"` and uses the existing fixed local relay credential in memory. It posts only to authenticated `POST http://127.0.0.1:8810/dot/observe`.

The deployed relay freshly checks the exact original receipt through `gateway.observe` → `POST /api/v1/agentic/gateway/receipt` both before and after its diagnostic. Original proof expiry does not invalidate that historical receipt lookup; it still cannot authorize a new send. No `/gateway/proof`, `/gateway/admit`, provider handoff or settlement callback is called by the wrapper.

The original envelope, credentials, messages, target IDs and private raw HTTP responses are never emitted or saved by this wrapper. Results accept only fixed protocol fields, fixed error/result codes and bounded counts. A request failure is not retried. All protected intent/result writes are operator audit records, not business records.

## Validation and retained holds

Six offline checks pass using the actual pinned payload validator and the real bounded HTTP wrapper with a controlled transport. They cover expired-original versus new-admission validation, owner/account/scope/run tampering, exact diagnostic shape, private response suppression, a single fixed POST and connection cleanup without retries.

Two initial wrapper assumptions held before any diagnostic POST. The first expected a root-owned manifest at the ordinary source location; it was aligned to the existing protected `manifest.json`. The second incorrectly applied the separate gateway-control credential's file-mode rule to the existing relay credential. It was aligned to the observed fixed relay credential owner/group/mode, without changing permissions or credential bytes. Final preflight includes that check. Both holds and their zero-dispatch evidence are retained; no failed or uncertain HTTP request was replayed.

Final operator SHA-256: `d01a110351d0e365fc61ed74ef7e92767b3ea3740d6ef373f85aaac1767ff42a`. The final six tests and read-only preflight correlate to this hash. The live original rows and all preservation checks also matched after the request.

## TASK-2026-06455

Its original invocation remains `blocked`, with zero dispatch attempts and no submission timestamp. The current `/dot/scope` → `/gateway/proof` authority path correctly accepts only genuinely queued/running invocations. A call while blocked would report `INVOCATION_INELIGIBLE` before reaching the historical gateway-profile failure and would not reproduce that earlier failure.

No fake queued row, replacement invocation, administrative rearm, altered scope or proof request was created. The deployed diagnostic improvement can identify the first failing guard during a future legitimately authorized queue transition; this observation grants no such transition.

## Evidence

Sanitized source-test, preflight, terminal and protected-readback receipts are in `docs/evidence/2026-11-22-original-target-diagnostic`. The earlier activation source and evidence remain unchanged at commit `57bf8d4bc1d6120d34a0427663ad747266501213`.

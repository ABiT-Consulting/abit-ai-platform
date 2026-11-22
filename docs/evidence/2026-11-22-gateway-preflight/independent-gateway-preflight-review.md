# Independent gateway preflight review

**Accepted at the source and isolated package validation stage; no blocking findings.** Review covers package manifest `554945a9863b64f5a28de4ecfcd30e574f047458ecb605bc958c5cc0d975dd5a`, based on `b25b1b0790f73b2071e0f20a4038b7bb90478ed0`. The author was architecture_run_links; this review was performed independently by status_evidence_review.

## Behavior and authority

The proof endpoint maps only exact known failures to fixed diagnostic codes while retaining authentication, read-only proof semantics and HTTP 409/424 outcomes. Admission behavior is unchanged. The relay preflight parser reads at most 4,097 bytes, accepts only fixed error codes/stages and bounded HTTP status values, and does not persist upstream text, packet contents, credentials, or exception chains. Valid proofs remain unchanged, and cancellation still propagates. The longest possible fixed stored reason is 229 characters, below the existing 500-character persistence cap.

The actual composed executor path was reviewed through `require_relay_scope` → `execute` → `settle_exception`. Failure precedes claim, start publication, provider preparation and submission. The existing identity/attempt fence persists the fixed reason, and the unchanged retry classifier leaves `error_code` and `next_eligible_recovery_at` null for this capability failure. Diagnostic codes do not confer retry authority. The package also preserves the separately reviewed PR25 guard that sends accepted work back to same-original recovery.

## Evidence checked

- Candidate, package payloads, package preimages and the isolated full composition match all three manifest hashes. Both patch hashes match their manifest entries. At 10:32:56 UTC, normally readable live files still matched all three preimages; no installed postimage was observed by this review.
- **53 distinct final focused methods passed** across 57 executions: 14 new diagnostic cases, 23 existing relay-scope cases, 10 accepted-original cases and six transport rejection cases. The initial four accepted-original fixture failures remain retained. The corrected fixture adds the actual installed reconciliation-wakeup function, asyncio and synthetic app.state. All **42 assertion ASTs** were independently compared and remain identical; no runtime behavior was modified to satisfy those cases.
- `api:verify` exited 0: 18 source tests, 48 developer/API tests and 37 frontend tests; Svelte reported 0 errors and 0 warnings. Nonfatal tooling/dependency warnings remain in the raw log.
- `api:verify:integration` exited 0 with **104 checks passed, zero failed and 615 registered HTTP operations**. Both API receipts report no source drift. The exact receipt/log hashes are indexed in the accompanying JSON.

The new tests cover the actual proof controller and persisted preclaim failure path, malformed/oversized/private response bodies, unknown Core exceptions, profile/native-boundary classifications, cancellation, no provider dispatch and unchanged successful proof output. This review did not rerun tests or change owner source, live state, credentials, services, or protected files.

## Scope of acceptance

This is evidence for the exact source package and isolated behavior. A more specific diagnostic does not itself repair native-boundary configuration or resolve an original uncertain execution. Root owns the protected release, original-request reconciliation and live functional verification. No deployment or native worker acceptance is claimed here.

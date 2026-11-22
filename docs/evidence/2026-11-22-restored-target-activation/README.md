# Relay lifecycle and restored-target activation evidence

**Accepted at the source and isolated-validation boundary.** This package combines the previously reviewed restored-target diagnostic with a relay shutdown correction and an explicitly bound Node-only operator. The operator remains `UNBOUND` in Git. Production binding and activation are separate root-owned operations.

The source base is `26a374594f9d6f7b477d5f440fc36ae1b41a4390`. The runtime manifest is `d6001e29d6b3ef1b5b6c3ba544e92526080e6745b1d0e4279c11eab0d293adc1`. The complete operating procedure is in [the operator README](../../../deployment/linux/restored-target-activation-20261122/README.md); [operator-source-freeze.json](operator-source-freeze.json) binds the final source, tests and evidence. The earlier source freeze is retained separately.

## Change and preservation

The runtime now tracks actual HTTP handler promises, browser-turn reservations, ERP turns and ERP inspections. Shutdown seals new work synchronously and waits for all admitted work to finish. Disconnected clients no longer hide continuing asynchronous work from shutdown.

The first upgrade cannot depend on code that the old process has not loaded. Its operator therefore uses the existing Core maintenance CAS, an exact narrow nonroot-SYN firewall seal, the application's shared browser lock, natural connection draining, a guarded readiness observation and one identity-bound pidfd SIGTERM. The unchanged watchdog owns the successor. Unknown command, signal or keeper outcomes remain held and are never repeated speculatively. Rollback distinguishes a keeper spawn that was proven unissued from one whose invocation may have started.

The guarded old health probe is not described as universally read-only: root authorized expiry of the rebuildable project catalog cache only, with exact protected backup, bounded shape/timestamp validation and sanitized before/after evidence. Original selectors, configuration, browser targets, historical invocation/delivery rows and the seven preserved processes are independently bound. No source test sends a provider prompt, rebinds an original, clears an uncertain job or changes the host firewall.

[Source preservation](source-preservation.json) records 85 byte-identical original named functions, the identical original authenticated business-handler AST and 60 unchanged diagnostic assertion ASTs. The diagnostic fixture adjustment only selects the newly named handler. [Strict patch reconstruction](patch-roundtrip.json) verifies all three forward postimages and rollback preimages, including the new file's absence, with zero patch fuzz.

## Verification

| Scope | Accepted result | Main receipts |
| --- | --- | --- |
| Operator | 28 distinct methods; changed final rollback group 4/4 | `operator-final-1795351278.json`, `final-selected-1795351473.json`, `review-rollback-correction-1795351813.json` |
| Runtime lifecycle | 12 methods | `source-focused-1795350455.json` |
| Actual shared-lock implementation | 3 methods | `source-focused-1795350455.json`, `final-selected-1795351473.json` |
| Restored-target diagnostic composition | 26 methods | `diagnostic-composition-1795351476.json` |
| Kernel firewall behavior | 5 assertion groups in a verified different network namespace | `final-selected-1795351473.json` |
| API verification | 18 contract, 48 backend and 37 frontend; Svelte 0 errors/0 warnings | `api-verify.json` |
| Isolated API integration | 104 passed, 0 failed; 615 registered HTTP operations | `api-verify-integration.json` |

The focused coverage totals **69 distinct methods**, plus five kernel assertion groups. Counts do not add repeated executions. API gates used 1,300 pinned source inputs with zero drift. Integration finished at 12:48:01 UTC, taking 86.57 seconds and peaking at 968,920 KiB process-tree RSS. Its disposable application data and disabled external services did not touch the live database.

The final authorized source-only read at **12:52:07 UTC** found all three original runtime preimage conditions, zero installed postimages and all 23 dependencies unchanged: [final-live-source-comparison.json](final-live-source-comparison.json). This is a timestamped source observation, not a claim about a later root-owned activation.

Early private-network tests exposed an expected nft JSON representation mismatch; the actual symbolic flag representation was captured and the strict expectation corrected. All failed, intermediate and final receipts are preserved. Initial receipts retain their actual earlier source hashes and are not relabeled as final-source runs.

## Independent review and limits

[Independent review](independent-operator-review.md) accepted the package with no open source blockers. Its one rollback finding was corrected, and the affected four-case group passed on helper SHA `4cbf14905acd67d0acabded257b9090443aa023ca1038d424c0baea83c764481`. The reviewer separately correlated the payload, patches, API gates and exact final source hashes. The review JSON retains the reviewer's own normal-identity dependency-read limitation; the separate authorized 23-dependency readback above completed successfully.

The tests include real harmless child/pidfd behavior, actual shared locking, disposable HTTP connections and isolated kernel nft behavior. Full private authority and browser state use controlled fixtures where required. These receipts do not establish live account login, provider completion, original-target recovery or a production rollout.

The source index deliberately excludes private activation drafts/manifests, bound operator copies, databases, generated caches and private operator input. Root's protected deployment receipts are a separate record. [artifact-index.json](artifact-index.json) lists every intentional source/evidence file with its exact hash; the commit itself supplies the index's durable Git identity.

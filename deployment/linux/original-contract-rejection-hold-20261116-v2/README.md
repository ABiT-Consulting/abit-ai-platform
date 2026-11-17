# Original result validation — four-file candidate v2

Status: source and isolated candidate checks passed; prepared for selective commit and deployment review. This patch has not been deployed.

## Exact scope

This candidate starts from the independently installed PM blocker-routing cohort. It replaces only:

| Runtime file | Installed SHA-256 prefix | Candidate SHA-256 prefix |
| --- | --- | --- |
| `utils/agentic_executor.py` | c91461e659d3 | d14747f2eb00 |
| `utils/agentic_result_store.py` | 5068586c80e3 | 429633df024e |
| `utils/agentic_gateway_contracts.py` | fa8e57cbbfd8 | 7dad9106e6a8 |
| `utils/agentic_blocker_review.py` | 17f72d771d81 | dd228d90bf28 |

manifest.json contains complete hashes, unchanged dependency pins and the corresponding authoritative source files. composition-proof.json and the four adjacent diffs show the bounded changes. The external result_event function is byte-exact; existing compact dispatch, registered test profiles and transport handling are preserved.

## Behavior

Automation success follows existing artifact verification/publication and precedes the separate business result callback. A truly invalid Developer result creates an idempotent exact-original governance HOLD, retaining the provider reply, automation state, task fence, admission and cancellation intent. No valid replacement Result is fabricated.

The externally deployed proof-bound Developer failure path remains available: actual completed registered test evidence can classify an existing raw fail as an implementation blocker with no QA verdict. Its diagnostic audit now rechecks exact ownership, run, packet and fence in the same transaction. Receipt, authority and I/O errors are not converted into contract rejections.

Future dispatch and context.read share guidance to use continue, blocked or ready_for_review and retain genuine failed-test evidence. Server-only handling of previously captured failures is explicitly distinct from the requested future result contract.

## Verification

- Canonical targeted suites: 98 passes — 42 executor/proof/order, 11 actual dispatch/context, 6 PM blocker routing, 39 orchestration.
- api:verify: passed; 606 declared HTTP operations, 4 justified hidden operations, 37 frontend tests, zero portal typecheck errors or warnings.
- Isolated api:verify:integration: 91 assertions passed, zero failed.
- Candidate with installed helper modules and isolated boundary fixtures: 18 passes, zero failures, errors or skips.

Canonical operation: 43310010-78d7-4251-8618-e9ecb54645a0, exit 0. Candidate operation: efb78a38-e7cf-44a7-b0ce-90ba74526ecd, exit 0. Exact log hashes and expected negative-fixture errors are qualified in validation.json and the source evidence receipt. No live DB or provider/model call was used.

## Deployment readiness

No installer or activation command is included. A fresh authenticated administrator /api/tasks count of zero, no older than 120 seconds, remains unsatisfied. Database/history counts cannot substitute. Root must also recheck current runtime hashes/process identities and preserve the existing data, configuration, active work and admissions under the applicable Main/QA procedure.

The four Python modules affect imported executor/recovery/orchestrator and gateway-context behavior. Replacing files does not update objects already imported by running processes; all consumers of the shared runtime need explicit handling under that procedure. No restart was performed here.

The historical v1 package and its failed baseline guard remain unchanged. Never apply v1 or reuse the obsolete scope controller, consumed observation controller, or previous browser checkpoint for this patch.

The original Developer run and newer checkpoint conflict remain a separate governed reconciliation task. This package performs no recovery, replay, successor dispatch or ownership settlement.

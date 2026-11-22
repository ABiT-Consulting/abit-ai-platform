# Independent relay activation review

**Verdict: source accepted for fresh root binding and preflight.** No open source blocker remains in the reviewed scope. The activation manifest remains unbound; this review does not claim staging, activation or provider recovery.

Reviewed base: `26a374594f9d6f7b477d5f440fc36ae1b41a4390`. Runtime package manifest: `d6001e29d6b3ef1b5b6c3ba544e92526080e6745b1d0e4279c11eab0d293adc1`.

| Operator source | Final SHA-256 |
|---|---|
| release_relay.py | `4cbf14905acd67d0acabded257b9090443aa023ca1038d424c0baea83c764481` |
| relay_bootstrap.py | `b4f61c78dd0933eafbbb8e8dbca7431558efa9315e60a87cba11472f6c251c23` |
| relay_runtime_guard.py | `cc3fd5538ed797f89770751e80bbc5b53b95c67b1fa5374db0cbb76218c2afde` |
| browser_lock_keeper.cjs | `116da8eec4ac60eb83c04cc0aace3bbf705442947e158add006c3bab82bdb2dc` |

## Finding corrected during review

`begin_rollback` borrowed an apply browser-lock intent even when interruption occurred before keeper spawn. Rollback could then wait indefinitely for a held receipt that could not exist. The owner corrected the branch so only `invocation_may_have_started` borrows the original keeper; proven-unissued phases allow a fresh rollback keeper. Unknown phases are denied, and an invoked/uncertain keeper remains readback-only.

The reviewer verified that this guard is the sole text change from reviewed helper `ab49ad7f…` to final `4cbf1490…`. The four focused rollback cases passed on the final hash, including both `intent_saved` and `precondition_failed`. The correction does not change runtime payloads, signal authority or historical records.

## Reviewed behavior

The global release lock, operator lease, exact process/source identities, accepted Core release and eleven verified database backups precede activation. The existing maintenance CLI closes the exact Core epoch using an explicit operator actor. A single owned nft table blocks new nonroot connections to the loopback relay while retaining existing connections and unrelated firewall rules. The actual application browser lock remains held by a verified keeper across operator disconnects.

Before the original Node receives its one pidfd SIGTERM, the helper waits for usable ingress and Node network activity to cease naturally, checks the bounded Hermes interval, then verifies current provider readiness and zero active turns. The root health probe may expire only the explicitly authorized derived catalog cache; original bytes are backed up, the exact empty-cache shape/time is checked, and authoritative state plus target inventory are rechecked. Core leases, governed/Codex work, legacy ERP/development journals and historical full-row digests remain guarded.

The helper records invocation intent before every external action. Unknown outcomes are observed through original receipts and identities; they are not replayed. The unchanged watchdog owns the replacement. Its process identity, parent, start ordering and listener are verified, and the first verified successor is retained. Rollback restores only known source preimages and the one original absence, preserving databases and original jobs. No whole-unit restart, forced termination, duplicate signal, foreign-file deletion or stale-record settlement is introduced.

The runtime delta keeps disconnected HTTP handler promises, scoped turns, ERP turns and inspections owned until completion. Drain denies fresh requests and delayed reservations. The restored-target diagnostic preserves the existing primary authorization and exact Core/root/account/envelope checks; it does not send, navigate, bind, capture or authorize replay.

## Evidence correlated

| Gate | Terminal result |
|---|---|
| Operator boundaries | 28 distinct methods covered across retained runs; final affected rollback group 4/4 |
| Runtime lifecycle | 12/12 |
| Actual shared lock | 3/3 |
| Original-target diagnostic | 26/26 |
| Private kernel namespace | 5 assertion groups passed; caller namespace unchanged |
| API verification | 18 contract + 48 backend + 37 frontend; Svelte 0 errors, 0 warnings |
| Full API integration | 104 passed, 0 failed; 615 registered operations |
| Source package | Three exact preimages/payloads and working-source hashes matched; both patch hashes and retained forward/rollback reconstruction correlated |

The JSON review contains exact source, receipt and log hashes. The reviewer read the four complete operator components, source changes, tests and terminal receipts. No unchanged suite was rerun.

## Remaining execution checks

Fresh binding must still verify the actual process cohort, gate epoch, backup receipt, configuration/queue digests and all 23 unchanged dependency pins. A normal-identity live hash sweep stopped at permission denied for `agentic-gateway/relay.cjs`; no alternate identity or bypass was attempted. The authorized root helper explicitly enforces this complete closure before activation.

This review performed no live mutation, privileged execution, provider request or browser action. Component tests and isolated API integration are source evidence; production activation and any original-run recovery require their own observed receipts. Commands whose outcomes cannot be proved intentionally remain held.

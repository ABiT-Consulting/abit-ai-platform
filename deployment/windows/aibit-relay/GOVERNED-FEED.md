The shared source Dot relay feed reader captures a completed `AGOV RESULT:` JSON line from the reply after the uniquely bound original message. It retains the original root and reply IDs, stops at the next user message, and checks the result's common identity against the already approved envelope and WorkPacket. The native packet digest is distinct from the saved ledger packet hash.

Governed JSON is returned unchanged. Recognizing a terminal reply does not accept its business result: the existing Core result parser validates the complete schema, architecture plan, evidence and handoffs. Invalid results retain their actual validation errors. Legacy Developer and QA terminal markers keep their existing behavior.

This changes response recognition within the existing relay handoff and read-only observation contracts. It adds no endpoint, submission, retry, capability, or privileged action. The owner/account/tab checks remain in the caller.


The shared module is kept in this historical source directory and targets the
existing Linux runtime `/srv/abit-dev-linux/relay-runtime/service/aibit_dot_feed.cjs`.
The hardening adds exact-envelope anchoring, an immutable verified original root,
Core-compatible line/byte boundaries and cross-row ambiguity rejection. It does
not add a second business validator or change either message-intake size limit.

Run both the original and supplemental pure DOM/Node VM suites from the repository
root:

```sh
node --test deployment/windows/aibit-relay/tests/aibit_dot_feed*.test.cjs
```

See [AGOV-RESULT-TRANSPORT.md](../../linux/aibit-relay/AGOV-RESULT-TRANSPORT.md)
for baseline provenance, retained-fixture versus rendered-DOM verification limits,
and release requirements that preserve an active original. No runtime copy,
hot reload, service restart or original-run reconciliation was performed as part
of this hardening.

## Read-only restored-target diagnostic

The internal `POST /dot/observe` operation accepts the explicit `diagnostic_mode: "restored_target"` on the existing server-assembled original payload. It retains the primary-token and fresh Core original-scope checks, then reports only bounded codes/counts for an exact sealed root plus complete envelope in an already-open configured-owner Dot landing target. It does not select a target, accept a result, change an immutable selector, or submit another prompt. Ordinary observation remains the default.

A complete negative result concerns only currently mounted inspected content, not provider history or virtualized messages. Ambiguous, partial, changed, unauthorized or timed-out reads cannot report a complete match or absence. A positive diagnostic leaves the existing canonical-URL and changed-target guards in force.

See `deployment/original-restored-target-diagnostic-20261122/README.md` for the exact internal contract, limits, fixed result codes and separate activation boundary.

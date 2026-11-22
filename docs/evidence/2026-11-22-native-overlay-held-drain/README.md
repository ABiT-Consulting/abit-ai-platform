# Continuous native overlay hold: source and recovery contract

## Scope and current boundary

The holder keeps the existing Core admission close, one exact relay ingress table and the existing shared-browser lock continuously owned for one independently reviewed native leaf operation. It does not call Azure, alter application source, restart Core/relay, issue business authority, or perform the native mutation. This package is source-only until the root reviewer binds the final native controller, native manifest, preflight and restore receipts, current Core/relay baseline, holder hash, and current operator lease. The archived 17:04 draft observation explicitly has `live_hold_authorized=false`; it is a fixture and provenance receipt, not an executable release manifest.

The original proposed holder `d055a1eb…` is retained byte-for-byte. This repair changes only five existing functions, adds nine bounded recovery helpers, and retains fifteen existing function ASTs exactly, including execute, finish, source input loading, process preservation, native reconciliation and the actual quiet-work observation. All five execution dependencies remain exactly as supplied by the root reviewer. `source-preservation.json` records the full hashes and AST relationships. The retained bootstrap/keeper adaptation differs from the prior relay operator only in fixed native table/marker and stage bindings; the precise deltas are archived.

## One-use publication

Signing enters a durable `signing_may_have_started` phase. That phase is never retried or renewed. The exact signed JSON, `HELD.json`, its hash and the journal are durably written before publication. The holder then saves a durable publication phase and `ADMISSION_PUBLICATION_INTENT.json` before creating `native-admission.txt` with exclusive creation. The text is the final carrier-visible artifact.

An interruption after publication intent is conservative: missing text is an unknown publication outcome, not proof of nonpublication. Valid existing text can be read back against its exact signed JSON, HELD hash, journal hashes and publication marker. Neither observation nor recovery signs, renews or republishes it. The original 120-second admission, 600-second nonrenewable operation, 420-second native body and 60-second holder reserve remain unchanged.

## Supported modes

| Mode | Effect and required evidence |
|---|---|
| `--execute` | Creates the fresh stage once; closes, seals and acquires only the original commands, observes actual quiescence, publishes one bound receipt and keeps the hold. A reused stage is rejected. |
| `--observe` | Reads exact protected inputs and classifies the original journal, gate, table, keeper and publication state. It works before HELD and during partial cleanup. It takes no release lock, changes no journal, creates no failure receipt, and issues no command. A before/after journal check marks concurrent transitions. Its output grants no recovery permission. |
| `--finalize` | After the original holder ceases, uses only the original native outcome reconciliation, result and definition-restoration receipts. Lost ACKs are reconciled from original receipts; no close, seal, keeper acquisition or admission publication is repeated. |
| `--abort-unpublished` | After original holder cessation and a separately prepared root-owned disposition, proves publication never reached the possible-publication phase, positively resolves every invoked original command, releases only the owned keeper, removes only the owned table and reopens only the exact owned Core epoch. It performs no native action. |

Every mode requires the exact `--manifest-sha256` and the protected fixed entrypoint under `/var/lib/abit-ai-native-overlay-held-drain-source-20261122`. Mutating modes retain the existing global release lock. Read-only observation does not compete with the live holder for that lock and reports ambiguity rather than interpreting it as permission.

The exact existing watchdog drop-in `/etc/systemd/system/aibit-linux-relay-watchdog.service.d/20-abit-xvfb-private-tmp.conf` is added to the allowed source set. No general unit-directory or arbitrary-path allowance is added. The actual baseline's 39 fixed source leaves pass the allowlist fixture; an adjacent unreviewed drop-in is rejected.

## Explicit unpublished abort

The operator may prepare only the fixed file `unpublished-abort-reconciliation.json` in the original root-private stage, owned by root with mode 0600. `unpublished-abort.template.json` is deliberately unbound. It requires the exact manifest, native operation, original journal and original holder identity hashes, plus explicit facts that admission was never published, no carrier/native action began, no retry occurred, every invoked operation is resolved, and original intake should be restored.

The source independently checks those bindings, original holder cessation, absence of both publication marker and text, the durable admission phase, preserved processes/source and current gate/table/keeper outcomes. A root Boolean does not override a missing audit, missing keeper receipt, changed source/identity, unknown seal outcome or possible publication. Unknown outcomes remain held. An uninvoked close requires the original gate to remain open; no synthetic close/open cycle is created. Initial stages with no journal can be observed but cannot be adopted or automatically retried.

After the disposition is accepted, its immutable hash remains bound to the original journal and operation during cleanup. A crash after keeper release, table removal, or a successful but unacknowledged open resumes only the original receipts. A command proved never invoked may be issued once to complete authorized cleanup. A command whose invocation may have begun is never issued again. No private lock file or historical queue row is removed, cleared, expired or relabeled.

## Validation

Normal and optimized runs each passed 37 distinct methods, with zero failure, error, skip or source drift. They use real ordinary temporary journals, the retained `command_once` and `control_receipt`, the actual nft document/readback helper, and controlled external service/kernel/keeper boundaries. Socket, subprocess launch, shell and signal operations outside those fixtures are denied. Cases include unknown/lost close, seal, keeper release, unseal and open ACKs; never-invoked cleanup recovery; publication ordering; interrupted signing and publication; missing/corrupt published text; original-holder/manifest/journal replacement; read-only observation before HELD and after partial cleanup; and finalization through the original native outcome.

The first run retained two fixture metadata errors when repeated completion reached the unchanged root-only file validator on ordinary temporary files. The fixture alone was corrected to require the invoking test UID/GID inside its unique temporary directory. All 37 test method ASTs and production source were unchanged by that correction. The failed receipt, log and original fixture are retained.

The required isolated `npm run api:verify` passed on the retained 381-source API composition: 18 source tests, 48 backend/contract tests and 37 frontend tests; Svelte reported zero errors and zero warnings. The source-only fresh harness emitted nonfatal Git HEAD/version warnings because it has no commit and a pre-existing Svelte package export warning; those logs are retained. No backend route, model, authorization, registration or API export changed in this operator repair, so the unchanged full-application integration suite was not repeated.

These are source/isolated-validation results. They do not claim host nft operations, a real root keeper, a live hold, native mutation, provider execution or an end-to-end business task.

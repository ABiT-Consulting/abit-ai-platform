# Restored original target diagnostic — source evidence

The source adds an explicit read-only diagnostic to the existing authenticated relay observation operation. The two-file package is reconstructed from the current installed relay preimage, with eight unchanged runtime prerequisites. The complete package contract and activation boundary are in `deployment/original-restored-target-diagnostic-20261122/README.md`.

## Completed verification

- **26 distinct focused tests passed across 28 executions.** The first run passed 23 and exposed two fixture omissions: the extracted actual HTTP handler needed its existing inactive account-linking state. That fixture was corrected; both original assertions remained in place and a positive account-linking denial regression was added. The selected final run passed all three. Both original failures and the exact initial test bytes are retained.
- Both runtime files passed Node syntax checks. The tested source hashes did not change between the two focused runs.
- `npm run api:verify` passed on a fresh isolated copy of the accepted current Core source plus the relay candidate: **18 contract, 48 backend/developer and 37 frontend tests**, Svelte **0 errors / 0 warnings**, **635 declared HTTP operations** and four existing documented exclusions. All **1,298 source hashes** were unchanged after the gate. No Core/public FastAPI route, model, authentication or registration changed; no unchanged full Core integration run is represented as new evidence here.
- The exact two-file forward and rollback patches passed a strict `--fuzz=0` scratch reconstruction, verifying both resulting postimages and restored preimage conditions.
- Source-span comparison confirms that the main relay changed only `observeOriginalDot`; the existing server handler, authentication functions, capture/Send logic and all other top-level source nodes remain byte-identical to the current installed preimage.

The earliest runner launch failed before any product tests because its cleared PATH omitted the existing Node installation. The corrected runner resolves that existing executable before clearing the environment. The launch-error receipt and empty initial log are retained; no package installation was needed.

## Evidence limits

The tests execute the actual serialized DOM parser, relay observation function, HTTP handler, timing-safe authentication functions, immutable binding implementation and canonical recovery guard. Browser/session activity and Core authority transport are controlled fixtures using synthetic data. No live browser, actual private message, provider, protected ledger, selector or original invocation was inspected or changed by this work.

A positive diagnostic establishes a currently mounted exact match under the checked scope. It does not perform recovery or authorize a new read selector or submission. A negative diagnostic does not establish absence from provider history or virtualized content. Live activation and actual original recovery remain separate operator steps.

Independent agent review accepted the source/isolated-validation package with no blocking findings. Its receipt is `independent-live-erp-status-review.json`; it also verifies the unchanged existing gateway proof/receipt source under the separately authorized source-only administrative read. The final artifact index binds all committed deliverable files.

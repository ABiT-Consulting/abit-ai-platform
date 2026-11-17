# Canonical control and QA preflight reconciliation

Source-only reconciliation on `feature/agov-api-live-20261113`, based on
`844870369abfa0afdfa02f1c0043c34629b69ff3`. No runtime files, services,
business ledgers, provider requests, native controls or Git commits were changed.

## Selected changes

| Canonical path | Reconciled behavior | Retained source |
| --- | --- | --- |
| `backend/open_webui/routers/agentic_governance.py` | Strict required native `control_sequence`; exact current intent checked before durable inbox insertion; bounded background diagnostics; explicit QA preflight recovery branch | PR5 `76f2f049` and PR6 `37ae81ea` selected exports |
| `backend/open_webui/utils/agentic_recovery.py` | Exact deployed empty, published QA preflight proof and one-use successor transaction | PR6 `37ae81ea` selected recovery export |

PR6 carried runtime exports and regression tests rather than modifications to
these canonical product paths. This change ports only the selected behavior.
The external ownership exception and a historical completion policy are excluded.

The actual control route documentation describes the required existing native
sequence and QA eligibility. Callers must provide the exact current human ERP
intent; omission of `control_sequence` is a request-validation error. The route
does not create a native intent or substitute its actor.

An explicit Retry can create a new independent QA unit only for the exact empty
published Blocked preflight checkpoint, with no existing workspace or independent
work, unchanged implementation, current Pending Review readiness, and a newer
acknowledged native Retry. The original result, receipt and uncertain read are
preserved. Task ownership transfers through the existing atomic ledger primitives.

## Preservation proof

The new QA functions, recovery branch, control model and diagnostic functions
match the selected deployed source by AST. The control route body matches except
for expanded documentation. All 25 pre-existing recovery top-level nodes and 40
other router classes/functions remain unchanged, including the current signed
Task delivery-control event handler.

The operations file remains exactly `fa2c899f…`, preserving prepare → verify →
independent tests → verify QA guidance, artifact prevalidation, current profile
behavior and delivery guidance. Ownership remains `dba829be…`. The complete
canonical v2 returned-reply authority code is preserved.

## Verification

- Three pre-change cases reproduced two expected assertion failures and the
  absent QA helper error.
- **34 distinct focused cases passed across 33 + 1 successful executions.**
  The initial 34-case run had 33 passes and one test-runner path-guard error.
  A first affected rerun exposed the read-only ancestor traversal requirement;
  the final affected rerun passed. Product and canonical test bytes did not change.
  A clean 34-case run is not claimed.
- The focused scope includes 22 ported control/QA/diagnostic cases, two actual
  recovery-route cases, six existing queued-recovery cases, one actual signed
  delivery-control path, and three v2 authority preservation cases.
- `npm run api:verify` exited 0: 13 source checks, 39 backend cases, 37 frontend
  cases; Svelte reported zero errors and warnings.
- `npm run api:verify:integration` exited 0: **91 passed, 0 failed**, with
  586 registered HTTP operations.
- Eight selected source/test files compiled; `git diff --check` passed.
  The original index hash and base commit remained unchanged.

All fixture attempts are retained, including the initial dependency-read guard
and inherited setgid-directory setup failures. The focused runner confines SQLite
and file writes to private temporary fixtures and denies network/DNS/process
execution and live runtime reads. Descriptor-relative reads may traverse only
the exact fixture directory ancestors; writes remain inside the fixture.

The integration gate uses the existing isolated launcher with temporary
data/static directories, synthetic users, providers disabled, external connection
denial and no normal application lifespan. These results do not establish live
recovery, production concurrency, native authority availability or first-flow
closure. Python 3.12 unittest destructor and slow-task diagnostics are retained;
no clean-stderr claim is made.

## Evidence

`validation.json` records full source hashes, the exact upstream Git provenance,
test IDs, terminal process observations, source preservation, and both API gate
receipts. `records/` contains exact portable copies of the validation outputs.
The required gate and focused source pins remained unchanged throughout their
executions. Approval-image and publication-controller work is separate.

# Original Developer delivery continuation

A Developer delivery may outlive its original model turn. A published `continue`
checkpoint can produce a newly admitted turn for the same Task. The continuation
may finish the original delivery only when Core proves the complete bounded
server lifecycle chain, current registered Developer, unchanged business inputs
and repository policy, and the original source and test bytes.

## Operations

`repository.delivery_status` accepts `{}`. It performs GitHub reads and may
record exact reconciliation receipts in the original journal. It never posts to
GitHub, imports evidence files, or creates a current delivery receipt. A fresh
response reports the current invocation separately from
`original_invocation_id`. It advertises either:

- `repository.delivery_status` with `arguments: {}` when the original remote
  outcome remains unknown; or
- `repository.deliver` with the exact original `test_call_ids` when those frozen
  steps can continue or the completed original can be imported.

`repository.deliver` retains its existing `test_call_ids` argument. There is no
caller-selected ancestor, workspace, phase, branch, commit or PR. For an inherited
delivery the IDs must exactly match the immutable original test evidence. Core
reuses every original step key, commit date, Git object, branch and PR marker. It
can POST only a step that has never had an intent. Current Project, Task, session
and lease authority is checked before reserving each such intent. An existing
uncertain step can only be reconciled by reading its exact remote object.

GitHub may present a commit message without one final LF. That presentation is
accepted only together with the full commit SHA computed from the unchanged
original request bytes, the same SHA in the GET response, and exact tree and
parents. No commit request, message bytes, date or existing SHA is rewritten.

## Evidence and acceptance

The write operation reads the original completed command receipts and log files,
checks the current approved command, exact source snapshot and acceptance mapping,
and verifies the completed remote commit, branch and draft PR before import.
Each imported file retains its exact original bytes. The current completed
receipt and its immutable `delivery_continuation` audit commit in one SQLite
transaction. The audit binds the source chain, policy version, original input,
original test call and session identities, original result hashes and per-file
origin/current mapping. `delivery_provenance.original_tests_reexecuted` is
`false`; no current `repository.test` call is fabricated.

A crash before the transaction may leave exact immutable imported files. A later
authorized call can reuse those bytes, verify the same origin and commit the
receipt once. A missing or conflicting receipt/audit fails closed. A completed
adopted receipt is always resolved back to its original journal; it cannot become
an independent original.

Developer readiness rederives that binding and hashes both original and current
files. QA revalidates inherited provenance before its normal GitHub checks and
must still provide its own current test receipts on the verified PR-head source.
Original Developer observations never substitute for QA execution. Result and
WorkPacket schemas, publication approval rules and QA completion gates are
unchanged. Models copy the returned `delivery`, `evidence` and `test_results`
into their current result while retaining the current invocation identity.

## Bounded lifecycle policy

Only exact `parent_acceptance_resumed` events with deterministic same-Task,
same-Project, same-Developer successors prove continuation. Published original
result/event/native receipt identities must agree. A recovered, definitively
denied start may have an independently audited native readiness frame. In that
case Core reconstructs only the two authorized transport digest pointers,
rederives the projection through the original start receipt and local result,
and requires the complete saved native event and one exact hashed
`readiness_native_result` audit from the server readiness policy. The original
WorkPacket and Core result remain unchanged. Lifecycle edges hash the actual
saved native event. Partial projections, other field changes, missing or
conflicting audits, and canonical replays that discard an audited frame are
rejected.

Unknown edges, cycles,
multiple independent delivery journals, changed agent/SRS/acceptance/scopes,
profile or source drift, missing tests and changed evidence are rejected.

A zero-defect blocked checkpoint can receive one server-audited
`delivery` successor only when its inherited original delivery is still prepared
or pending reconciliation. That successor is dedicated to finishing the frozen
delivery. An already completed original does not automatically create a new
blocked-task successor.

Managed packet changes are bounded separately. The complete Project backlog must
retain its business context and structural fields; snapshot/task readback fields
are revalidated by the existing WorkPacket contract. Added private attachments
must correspond to a prior published chain artifact, its exact ERP File identity,
and actual bytes verified by SHA-256 and the native attachment content hash.
Both the native integer private flag `1` and boolean `true` are accepted; false
and zero are rejected.

An exact server-verified repair/action boundary starts fresh implementation,
including when the predecessor has a completed delivery. Core verifies its
hashed deterministic lifecycle event, published issue, resumed dependency wait,
confirmed source requests and independently verified children. It preserves the
old journal and requires the new turn's own source and command receipts. A later
continue can inherit only that new original; the older journal is outside its
continuation segment. QA rejection likewise starts fresh work through the QA to
Developer handoff. Unknown or conflicting boundary proofs fail closed.

Ordinary work with no original or adopted delivery continues to create its own
workspace, current tests and original journal. The special inheritance policy is
applied only when a delivery is actually inherited.

The inherited-delivery path currently supports repository-only acceptance.
Physical dependency acceptance cannot borrow historical infrastructure proof.
Ordinary original Developer and QA dependency workflows are unchanged.

## Verification scope

The regression suite uses temporary ledgers and artifact roots with socket and DNS
operations denied. An in-memory Git protocol fixture reproduces stored mutations
with lost acknowledgements and GitHub's trailing-LF presentation. It exercises
actual orchestration, gateway proof/admission/call serialization, immutable old
call replay, missing-step continuation, atomic audit failure, changed source and
evidence, and independent QA acceptance. It makes no claim of live deployment or
external GitHub mutation.

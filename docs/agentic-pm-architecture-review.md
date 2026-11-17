# PM architecture review v1

A Project Manager may return the unchanged `reviewed` verdict for the explicit server-derived `architecture-review.v1` phase. This is a completed positive architecture review and planning readiness, not independent QA, implementation acceptance, human approval, or business completion.

The server derives this phase only from the original submitted PM invocation, its frozen WorkPacket, and its direct handed-off Solution Architect predecessor. The predecessor must have an exact published `run.ready` result and durable `Pending Review` receipt. The actor, Project, Task, revisions, SRS, plan, published artifacts, backlog context, original run and review context must agree. Caller-supplied booleans or audit dictionaries do not authorize a phase.

The PM result retains its original bytes and verdict. It requires real run-owned checkpoint evidence and cannot declare QA tests/verdicts, defects, implementation identities, QA acknowledgments, delivery, actions, or a replacement architecture plan. Current controls, gateway authority, actual artifact bytes, private native File verification and signed result/readback remain mandatory.

The canonical local result and an `architecture_review_phase` hash-chained audit are recorded atomically. The audit binds policy, original row/packet, raw/result hashes and exact published predecessor evidence. Planning and recovery rederive that phase before materialization; exact retries reuse existing publication and Task requests.

The native callback uses the existing `run.ready` event and unchanged result contract, with `acceptance_passed=false`. Its native status is `Pending Review`; it cannot produce `QA APPROVED` or `Completed`. A stopped submitted original can retain its local result and audit, while current state/control gates prevent publication and successors.

Ordinary atomic PM delegation continues to use `ready_for_review`. Closing PM QA coordination still requires a bound `qa_review`, exact acknowledgment and implementer, observed passing review tests and real review evidence. The default validator retains those requirements. Model guidance and tool catalogues distinguish these phases; `pm_review.write` is advertised only for QA coordination.

This adds no API operation, request-trusted phase flag, timer, database schema, lease reset or provider submission. Existing authenticated result/recovery and status/audit APIs carry the behavior.

Selected runtime exports under `docs/evidence/2026-11-15-pm-architecture-review-v1/runtime` preserve the actual deployed baseline plus the reviewed phase increment. Concurrent Source changes are preserved separately and are not implicit deployment authority.

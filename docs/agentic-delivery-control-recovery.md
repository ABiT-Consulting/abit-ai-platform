# Delivery recovery after a newer human Task control

The generic blocked-delivery continuation remains one-use per original delivery.
A separately reviewed platform correction can authorize one additional delivery
unit through the exact signed human Task Retry or Resume event. Deployment,
startup polling, status reads, a stored control cursor, and Project-level Retry
cannot create this additional unit.

## Required authority

The original Developer must have a published zero-defect, zero-action-request
Blocked result, retain the current Task fence, and belong to the same bounded
continuation segment as one prepared original delivery. Its generic delivery
recovery marker must already exist. All retained original remote steps must be
completed, including the exact tree and commit; an unknown original remote
outcome does not authorize another unit.

The explicit path also requires the protected per-invocation operator resolution
introduced for the exact single-final-LF commit presentation. It binds the
original blocked result/native receipt, frozen delivery input, four completed
steps, commit request and SHA, retained GET proof, and the installed delivery
consumer bytes. The directory/file ownership and no-symlink checks remain in
force. This resolution is default-off and grants no automatic lifecycle work.

The human control must satisfy all of the following:

- It is a Task-scoped Retry or Resume received through the HMAC-authenticated
  ERP event endpoint. Its immutable inbox bytes and signed-ingress audit agree.
- Its actor is human, its exact native command is currently acknowledged and
  enabled, and the hashed application audit matches the Task, command, sequence
  and actor. Fresh native GETs confirm that command, its durable application
  acknowledgment, and absence of both Project and Task vetoes.
- Its signed Task revision matches the new native WorkPacket. Its signed
  `source_modified` equals the actual current Open Task and fresh packet and is
  strictly later than the exact published Blocked receipt. The Task still
  names the blocked source run as its native active run.
- The current Project, Task, registered role, repository profile, SRS,
  acceptance mapping, source snapshot and original command/log receipts remain
  valid. Managed packet changes are checked through the existing full schema,
  business scope and published-attachment byte checks.
- Maintenance admission is OPEN for this Project before the authorization is
  consumed. A command received while admission is closed or outside the allowed
  scope stays inert and is acknowledged; another fresh command is required
  after admission is ready. A still-Blocked native Task is likewise inert.

Native control sequences are monotonic within a Project across Project and Task
commands. Core retains current cursors per `(project, task)`. The product has no
incident-specific minimum such as 10. The protected operator resolution carries
a nonnegative reviewed floor, and its `control_task` must equal this exact Task.

## Atomic successor and immutable provenance

The successor ID is deterministic from the blocked source, the
`delivery_control` edge kind and Developer role. The source fence transfers in
the same SQLite transaction that creates the successor and appends the
`delivery_control_recovery` authorization. That audit binds the exact source
result/event/receipt, source identity and lease hash, original input and retained
completed-step hashes, original test provenance, signed control, native
readiness frame and protected resolution hash.

The existing `delivery_checkpoint_resumed` marker is neither reset nor duplicated.
A second authorization for the same original or command is rejected. A replay
of the same consumed control resolves to its existing unit. An audit failure
rolls back the successor and fence transfer together. The previous published
Blocked response, original packet, Git requests, step keys, commit date and
completed remote objects remain unchanged.

The ordinary gateway then performs only missing delivery steps under fresh
current authority. It copies verified original evidence only in the write
operation and records that the original tests were not rerun. Lost
acknowledgments keep their normal GET-only reconciliation fence.

The new edge is independently revalidated by later delivery, status, readiness
and QA consumers. Historical validation checks the immutable signed ingress,
application and consumption audits and exact retained completed prefix. It
permits the original journal to acquire the missing reference/PR steps and
complete. It does not require an old command to remain the latest cursor, an
old Root resolution file to remain installed, or the Task to remain Open.
Current operation authority still comes from current controls and lease gates.
Independent QA must execute its own current tests.

## Operator resolution compatibility

The historical preparation artifact under
`docs/evidence/2026-11-15-github-commit-readback-recovery-v1` is retained as evidence
of that earlier proposal. Its Project-scoped output and source hash are not an
activation artifact for this corrected consumer. A newly reviewed proposal must
preserve the immutable original proof fields, bind the final installed delivery
source SHA, set `control_task` to the exact Task, and record the verified
nonnegative native control floor. The corrected consumer rejects a legacy
Project-scoped proposal.

Preparing or deploying code does not trigger the human command. Actual Open
readiness and a genuinely newer signed Task Retry/Resume are separate native
actions.

## Verification

`test_delivery_control_recovery.py` exercises the canonical router event
callback, actual HMAC/inbox/native control application, complete backlog packets,
deterministic successor, real gateway admission/calls, lost-ACK reconciliation,
atomic rollback, later control changes and independent QA. Native ERP is a
controlled test boundary and Git is in memory; sockets and DNS are denied.
The former generic-authorization scenarios are superseded by these stricter
event-path tests. The two commit representation tests remain in
`test_commit_readback_recovery.py`. Historical runtime test receipts are not
rewritten as evidence for the corrected source.

# Preserved historical delivery evidence

The historical reader recognizes an already-created delivery continuation from
`single-lf-delivery-recovery.v1` and its completed
`original-delivery-continuation.v1` gateway receipt. It preserves that earlier
representation directly. It does not create a successor, authorize a delivery
write, insert a replacement canonical receipt, import evidence, or rewrite any
original row, result, audit or remote step.

This is distinct from the current signed Task-control recovery policy. The
legacy policy used a protected operator resolution and an acknowledged human
Project or Task Retry above its declared sequence floor. Its historical Project
command is not represented as a newer Task-scoped command. The generic recovery
creation guard and the current Task-control guard remain unchanged. Two existing
legacy markers cannot consume another additional-unit budget.

## Exact existing history

Recognition requires all of the following existing evidence:

- A bounded deterministic same-Project, same-Task, same-Developer continuation
  segment, with published predecessor results, exact native receipts, unchanged
  business context, repository policy and source/test bytes. A repair, action or
  control boundary cannot be crossed to borrow an older delivery.
- Exactly the original generic marker and the one historical additional marker,
  plus the original hashed lifecycle, authorization and completed-continuation
  audits. Their actors, scope, order, hashes, original input and blocked result
  must agree. A third marker or another authorization denies recognition.
- The unchanged protected operator bytes whose SHA-256 is already recorded in
  the authorization audit. The exact signed inbox key and payload hash, ingress
  audit, human application audit and durable native acknowledgment must match.
- The two supported historical producer hashes and source commit, proven by the
  actual Root deployment records from before signed ingress. The native start
  request/receipt and original Open WorkPacket are rederived as historical facts.
- The original four completed Git steps and the subsequently completed reference
  and pull-request steps, with the original input, commit request, exact computed
  commit SHA, tree and parents. The supported GitHub presentation differs only by
  the one final LF already bound by the old proof.
- The exact old completed gateway call and its immutable session scope, original
  test call IDs, request/result hashes, gateway audits and copied evidence bytes.
  The archive binds the original `(session_id, call_id)` pair. Later legitimate
  calls may record their own normal receipts without replacing this binding.

Later delivery, status, readiness and QA checks rederive those immutable facts.
They do not require an old control to remain the latest cursor, the native Task
to remain Open, or the old mutable operator slot to remain installed. Normal
current lease, Project/Task controls, maintenance admission and repository checks
continue to apply at their existing operation boundaries. Current commit/branch/PR
readback and independent QA source/head/tests remain mandatory. Historical
Developer test evidence never substitutes for new QA execution.

A verified cached legacy response is returned unchanged. No current
`repository.test` execution is fabricated, and the old audit is not silently
converted to the newer adoption policy. Status/readiness acceptance writes no
canonical delivery row or evidence file. A fresh normal gateway call may still
record its own ordinary immutable call receipt.

## Protected history archive

The fixed runtime directory is:

`/var/lib/abit-ai-agentic-gateway/delivery-recovery-history`

It must be Root-owned with group 988 and mode `0750`. Every object is a regular,
single-link, Root-owned file with group 988 and mode `0640`; symlinks and oversized
files are rejected. The index is named
`<existing-authorization-sha256>.history.json`, with a 65,536-byte bound. It has a
closed schema and names exactly three content-addressed `<sha256>.bin` objects,
each bounded to 262,144 bytes:

| Object | Purpose |
| --- | --- |
| Original operator bytes | Preserve the unchanged approval already hashed by the old authorization audit. |
| Raw native acknowledgment | Preserve the exact durable receipt for the original human command. |
| Minimal deployment projection | Bind the two producer hashes, source commit and exact original Root record hashes/times. |

The index also binds the rederived existing lifecycle, journal, test, native-start
and completed-call facts. Its timestamp identifies when evidence was retained,
not when an earlier approval was created. The original producer files, full
rollout receipts and process/backup metadata remain Root-only. They are validated
by the preparer, not copied into the service-readable archive.

## Offline preparation and retention

`scripts/prepare_delivery_history.py` has two separate modes. It uses selected
pure source functions, disables network access, and reads the governance ledger
through a read-only/query-only SQLite transaction. It does not import the
application or initialize a ledger schema. When run as Root, the ledger read
uses the existing service UID/GID before restoring Root.

First prepare a new private proposal using the already-retained exact input
files and existing child identity:

```sh
python3 -I -B scripts/prepare_delivery_history.py prepare \
  --child EXISTING_CHILD_INVOCATION \
  --operator-file /absolute/path/to/original-protected-operator.json \
  --native-ack-file /absolute/path/to/original-native-ack.json \
  --deployment-proof-file /absolute/path/to/verified-deployment-proof.json \
  --delivery-producer /absolute/path/to/preserved-agentic_gateway_delivery.py \
  --orchestrator-producer /absolute/path/to/preserved-agentic_orchestrator.py \
  --output /absolute/path/to/new-private-proposal
```

The preparer verifies the actual original Root rollout records, their exact
hashes and original modification times, and their complete selected structure.
It rejects merely self-declared producer hashes. The records must predate the
signed ingress. Original record paths must remain available; copied-now mtimes
are not treated as old deployment times. All validation occurs before the
private proposal is written. Preparing a proposal does not install runtime
evidence or grant permission for work.

After independent review, retention uses the exact returned index SHA-256:

```sh
python3 -I -B scripts/prepare_delivery_history.py retain \
  --proposal /absolute/path/to/reviewed-private-proposal \
  --expected-index-sha256 REVIEWED_INDEX_SHA256
```

Retention revalidates every object and the complete immutable ledger binding
before creating any archive file. It preflights existing bytes, refuses
conflicting evidence without replacement, reuses identical objects and installs
the index last. If interrupted before the index, the incomplete archive remains
unusable. An exact retry may finish that same archive. Neither mode changes the
governance database, native controls, maintenance gates, services or GitHub.

The archive is retrospective preservation of existing evidence. Missing
original authority cannot be reconstructed by this tool or by a current control
cursor. Archive preparation/retention is not a new approval, a model invocation,
a deployment, or live end-to-end verification.

## Verification

`test_legacy_delivery_history.py` uses the frozen historical producer functions,
complete WorkPackets, real temporary ledgers, actual gateway admission/calls and
an in-memory Git protocol. It covers old-call replay, a new call ID followed by
status/readiness/independent QA, immutable history, signed/native/operator drift,
missing objects, extra markers, current revocation, later cursor changes, and a
fresh QA repair boundary with its own tests. The prior helper rejects the same
positive fixture at its one-marker guard.

`test_prepare_delivery_history.py` exercises real temporary-file no-follow,
size/link, exclusive-create, conflict, interruption and index-last behavior.
Ownership/chown metadata is explicitly simulated without privileged actions.
The actual pure loader also validates a full temporary ledger and proposal after
a newer gateway receipt, proving the original archived call remains selected.
These tests make no claim of archive installation, external publication or live
QA completion.

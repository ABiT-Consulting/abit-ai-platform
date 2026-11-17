# Fixed physical IT execution

Physical package installation, service restart and offline ext4 image growth
require a separately reviewed host resource profile and local Root broker.
Registration is absent/off by default. The initial registry accepts only owned
development/test resources; production resources remain disabled.

Core keeps its existing `mcpdev` identity, `NoNewPrivileges=yes` and filesystem
protections. It cannot supply a shell, command, URL, unit, package version or
volume path. A Task selects a registered resource ID. Root-owned server code
derives the fixed command and rechecks its physical identity before dispatch.

## A fresh child starts once

The producer prepares a physical plan before creating any IT invocation. The
native `agentic_it_plan.bind_it_plan` POST adds only its absent `plan_digest` to
the same fresh managed Open IT child. It verifies its original producer request,
assignment, SRS, Task/Project revisions, current controls and absence of native
or legacy execution history. The producer also rejects any Core run history.
An existing Task is never automatically replanned or reassigned.

Native Task/ToDo events and run admission cannot dispatch an unbound physical
child. The binding write emits no second event. The producer fetches the bound
packet before deriving its stable child invocation key. A lost POST response is
reconciled through `get_it_plan_binding_receipt` GET; the POST is not retried.
The plan authority excludes its own Task revision update and the volatile free
byte sample. Current capacity must still meet the pinned minimum at preparation,
admission and execution. Filesystem identity, geometry, threshold, backup,
restore, retention, impact, official signer and target pins remain authoritative.

## Host authority and recovery

The AF_UNIX broker verifies the kernel peer UID, actual fixed Core MainPID/start
identity and unit hardening, plus authenticated bounded
frames. Each request receives a fresh challenge. Core verifies its exact current
ERP role, run, Task lease/control fence and consumed one-use human approval
before Root admits a physical action. Root independently recomputes current
safety from its protected profile and readers.

A deterministic independent systemd worker has a durable immutable launch
record before StartUnit. It reads an authenticated original request, checks the
short admission deadline, then executes the registered resource handler. The
host journal deduplicates the action/invocation/run/plan tuple and fences an
uncertain underlying physical resource against another mutation. Worker units
have no restart policy and survive loss of the Core/broker connection.

Unknown StartUnit or host acknowledgements are never automatically executed
again. Recovery reads the exact protected authenticated original host receipt
and native consumed approval, then CAS-reconciles the original Core action.
Matching current host state alone cannot settle an unknown action. Hold/Stop
blocks new work while allowing these read-only original receipts; an already
accepted operation may finish its safe checkpoint after a later control change.

## Concrete implementations and verification limits

The package handler verifies a pinned Ubuntu/Debian archive signature chain,
index checksum and exact package/version/architecture/bytes. It installs only
inside its immutable-identity isolated container, with no host namespace,
credential/socket mount or writable host bind. The service handler pins unit
and health-program bytes plus actual PID/start/invocation health. The capacity
handler accepts an exclusive regular offline ext4 image, rejects live loop
backing, and verifies growth and read-only fsck without targeting block devices.

Recovery proofs contain actual protected backup and distinct restored bytes,
an internal functional restore probe, retention/impact hashes and authenticated
receipts. A serialized `verified: true` value cannot create these capabilities.

For these physical actions, independent QA uses
`infrastructure.verify_original`. Native `get_it_implementation_receipt`
derives its actual published IT predecessor, private File bytes and consumed
approval. The broker verifies the original authenticated host receipt and makes
a separate current postcondition read. QA records its own actual artifact; its
final acceptance checks that evidence and reads the host again. Developer PR
acceptance stays unchanged. Routine Operations and legacy IT inspect/cleanup QA
have no new typed acceptance path in this increment and remain unverified.

Unit tests use real temporary files, subprocesses, cryptographic signatures,
AF_UNIX sockets and SQLite journals. Their native ERP/model peers and launcher
are controlled. They do not by themselves prove Root/Core identity reachability
or a real ERP Developer → IT → independent QA flow. The staged owned-resource
canary must supply those separate receipts before live registration. No
production package, service or storage action is enabled by this source change.

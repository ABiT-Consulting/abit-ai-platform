# Per-invocation native verification admission

Status: source implementation and recorded isolated validation complete for the
reviewed candidate; production activation remains unbound. This document
does not authorize registration, keys, service startup, a production release,
ERP approval, or native execution. Existing V6 source, policy, execution records,
900-second gate and original attempts remain unchanged.

## Real call path

The existing ERP action remains `verify_native_source`, privileged, with parameters
exactly `{"profile_id":"bigrro-proj0202-fixed-v1"}`. It belongs to the registered
IT-manager actor, PROJ-0202, the fixed BIGRRO repository, and ABIT-DEV-LINUX.

1. NativeVerificationAdapter preflight calls the root broker's read-only prepare.
   In the explicitly registered `per_invocation_v2` mode it returns a static
   subject hash and no nonce, gate, capacity reservation or authority.
2. Existing execute_action reads the actual current ERP approval, validates
   owner/task/packet/Stop and then atomically consumes the human action through
   ERP. The returned consumption receipt is persisted in governance_action.
3. NativeRuntime.admit_once reads that durable original action/consumption and a
   fresh ERP approval whose used_by_action_id is the same action. It persists a
   unique Core admission intent before requesting root admission.
4. The authenticated broker challenge makes Core repeat current Project/Task,
   packet, start/approval-checkpoint CAS, owner, lease/fence, Stop, approval,
   consumed receipt and maintenance-admission checks. One grant expires within
   15 seconds. A replaced Core process fails the root peer check.
5. Root holds the existing native global flock, validates the immutable reviewed
   source/control subject, checks retained pending attempts and fresh capacity,
   and selects one fresh nonce itself. It writes immutable original intent,
   attestation, capacity, unchanged-v4 authority and schema-3 execution records.
   The exact Core gate is the final publication barrier.
6. Existing bind_original, launch intent and separately challenged launch grant
   still run. The controller receives only a nonce and exact execution digest.
   It revalidates the per-run records before provider release. The unchanged
   result publication and independent QA/PM path remain required afterward.

The Core schema table is additive. Invocation, run, action and ERP consumption
receipt are unique. Immutable triggers forbid rebinding and deletion. Admission
or launch acknowledgement loss permits original observation only. Neither
timeout nor process restart issues another nonce or repeats the original call.

## New native namespace and versions

Native prefix:
`/var/lib/bigrro-verification/proj0202-native-per-run-20261123`

Shared controller lock (unchanged):
`/var/lib/bigrro-verification/bigrro-proj0202-native-worker.lock`

Existing global EXECUTION schema 2 and native authority v4 remain their existing
contracts. The new static subject and per-run execution each have schema 3 and
distinct kind values. The new reader is explicit; it does not reinterpret a
schema-2 file or replace a global policy/execution gate.

| Record | Exact fields |
| --- | --- |
| SUBJECT.json | schema_version=3; kind=bigrro_native_static_subject; payload; reviews |
| Static payload | Legacy BUNDLE_KEYS excluding schema_version and gates; fixed worker, plan, profile, runtime, six controls, source/provider/phase-A pins and no-retry/output-capture scope |
| Static reviews | isolation, security, dba; each exact receipt_sha256 and subject_sha256 |
| executions/nonce/EXECUTION.json | schema_version=3; kind=bigrro_native_per_run_execution; nonce; subject_sha256; admission_sha256; authority_sha256; capacity_sha256; issued_at; expires_at |
| executions/nonce/ADMISSION.json | Exact Core attestation schema 2 described below |
| executions/nonce/CAPACITY.json | schema_version=1; kind=bigrro_native_per_run_capacity; nonce; subject_sha256; admission_sha256; observed_at; expires_at; memory_available_bytes; memory_required_bytes; free_output_capture_bytes; required_output_capture_bytes; concurrent_native_attempts; rootfs_copy_bytes; required_copy_inodes; available_inodes; filesystem_block_bytes |
| authority/nonce.json | Unchanged authority v4, exact fixed ten-operation plan, two disposable databases, one attempt, zero additional spend and no publication |

All new records are root-owned 0400 canonical UTF-8 JSON without a newline. Hashes
are hashes of those actual bytes. In per-run records and the Core attestation,
subject_sha256 pins the **full SUBJECT.json**, including its exact reviewed
receipt bindings. The policy SUBJECT_MANIFEST_SHA256 pins the same full file.
Only the static review receipts use a reviews-excluded projection:
digest({schema_version:3,kind,payload}), avoiding a circular hash.

The policy source hash normalizes only the one SUBJECT_MANIFEST_SHA256 literal
to PENDING_DENY. The existing EXECUTION literal/reader stays unchanged. Source and
provider snapshots are immutable reviewed copies; this change does not stage or
modify them.

## Core attestation schema 2

Exact top-level keys are schema_version=2, kind=abit_native_admission, scope,
subject_sha256, action, approval, consumption, lease_token_sha256,
lease_expires_at, start_receipt_sha256, erp_baseline_sha256,
maintenance_admission, and prepared_at.

Scope is the existing 17-field scope: invocation/run, project/task/revisions,
registered agent/ERP user/role, packet/context/SRS digests, current lease fence,
repository, environment and fixed profile. The action is the actual canonical
12-field ERP action. Approval is the actual 10-field canonical ERP decision;
consumption is the actual 10-field receipt including receipt ID, run/invocation,
project/task and a boolean duplicate indicator. A duplicate ERP receipt is never
a new root execution grant.

Root accepts this only through its existing HMAC challenge and exact current
SO_PEERCRED PID/UID/GID/start/cmdline/boot registration. It is not a model argument
or public API, and pure schema validation is not authentication.

## Time and capacity

The attestation must be at most 30 seconds old at issuance; the challenged grant
lasts at most 15 seconds. The actual Core lease lasts 120 seconds and is renewed
every 30 seconds by the executor. Its original expiry proves current dispatch
ownership at issuance, and a separate fresh challenge revalidates owner, fence,
lease, Stop, approval and maintenance immediately before the one launch. It is
not a delegated worker deadline. A later heartbeat never changes any frozen
native record.

The new execution and unchanged v4 authority share one immutable interval of at
most 7200 seconds, capped by the original consumed human approval expiry. Native
worker runtime remains 3600 seconds, provider-copy 180, start 15 and cleanup 65;
the outer window grants no additional operations, attempt or worker runtime.
The new per-run root controller has a 4200-second service ceiling, covering the
3860-second sum of those four phase bounds with 340 seconds of setup margin.
The legacy controller remains 3800 seconds. This ceiling does not extend any
human approval, capacity admission or worker runtime.
CAPACITY has the same issued/observed time but its own expiry no more than 900
seconds later, and never beyond execution expiry. Native admission checks both
windows before Start and HOST_ACK. The short preparation freshness check applies
at issuance, not again after provider copying. Original-only reconciliation may
inspect expired records but cannot launch or release a worker.

Per-run NativeRuntime waits for the original terminal receipt using bounded
read-only polls. This keeps the existing executor or approval-resume heartbeat
alive while the native controller runs. An acknowledged Start is never treated
as success. Lost Start acknowledgement is followed only by original observation.
A timeout or Core interruption leaves the original durable intent for the
existing automatic reconciliation loop, whose normal fallback is 30 minutes.
A completed original still requires the ordinary result-publication, independent
QA and PM handoff gates. Current Stop, changed owner/fence/packet, approval
revocation or lease loss prevents publication. Stop does not synchronously kill
already released native work; that work remains bounded by its unchanged runtime
and original owned cleanup. This source introduces no unsafe cross-lock
cancellation call.

Capacity comes from bounded kernel and statvfs reads under the native flock:
2 GiB plus 128 MiB available memory; no populated native worker/controller
cgroups; enough space for a fresh provider copy plus the existing archive budget
and filesystem-block allowance; and enough inodes for the complete copy plus 16.
This is observed capacity, not a claim that host RAM was allocated. No reserve
has been lowered. The native controller retains its own final capacity gates.

## Configuration and source installation boundary

The current disabled/legacy configuration remains the default. A future reviewed
installation must opt into admission_mode=per_invocation_v2 for exactly PROJ-0202
and a root registration schema 2 with exact new module and native-control pins.
Unknown modes and unbound subject hashes fail closed. Registration remains tied
to the actual Core peer after a controlled restart; no synthetic identity is
accepted.

Selective installation must preserve all valid live-only code/configuration and
the existing backup/restart discipline. This package does not include an actual
production approval, a live registration, key bytes, an ERP manager decision, or
a fresh business Task. New native control hashes, static independent reviews and
actual source/provider readback remain separately reviewed installation inputs.

## Failure and original observation

Partial admission records are retained and block a conflicting next attempt.
Repeated identical requests observe the original; conflicting requests fail.
Only an exact completed or retained-failure native outcome with confirmed cleanup
can clear the concurrency check for a distinct later invocation. An expired
unlaunched or partial original requires explicit original reconciliation; it is
not deleted, adopted or automatically retried. The public business Task is never
completed by an admission receipt or standalone technical result.

## Retained-history verification and timing gate

A locked new admission verifies the static subject and provider once, then uses
that exact in-call proof for historical records only. Each prior record still
passes the native root-0400 canonical JSON reader, full immutable schema/hash
validator, private parent/nonce directory identity fences, and positive original
cleanup proof. The scope cannot escape the admission call, authorize a new run,
or survive another subject load. No cross-call cache is introduced.

Before the new gate is published, the unchanged full native current reader
verifies the static subject/provider and current capacity again. Consequently,
completed history does not add full provider scans. Historical local-record and
cleanup checks still grow with the retained index; no records are removed or
implicitly cleared. Activation must measure complete fresh-grant latency with
representative retained history under the unchanged 15-second grant, not only
with an empty index. Source/fixture counts do not prove that live timing gate.

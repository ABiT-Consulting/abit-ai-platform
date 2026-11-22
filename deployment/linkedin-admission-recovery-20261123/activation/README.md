# Exact seven-file activation source

## Status and scope

This is callable activation/backup/rollback source, independently reviewed and
tested on isolated files and SQLite fixtures. Production execution remains
**UNBOUND**. No private production backup, service-identity operation, gate change,
restart, activation, original reconciliation or agent execution was performed by
this Chat.

The only product payload is commit
8c1bc2308fdf693659520103559863682333299b, manifest SHA-256
955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250.
The seven candidate files, manifest and earlier preflight are unchanged.

- controller.py: exact backup/apply/observe/finalize/rollback operations.
- service_sqlite.py: fixed read-only live queries and bounded SQLite snapshots
  under the existing service UID999/GID988.
- test_controller.py: isolated fixtures, real temporary file/SQLite operations,
  actual same-UID POSIX children and a deterministic service lifecycle emulator.
- evidence/activation-tests.json: completed 36-case activation suite, exit0.
- evidence/activation-final-guards.json: four additional final-guard tests, exit0,
  explicitly linked to the accepted 36-case evidence.
- independent-review.json: independent review and separately reproduced findings.

The foreground tool initially timed out after35 completed passing cases. The
bounded background continuation ran the complete36-case suite in64.364seconds.
After two final review guards were added, four focused tests passed in1.816seconds.
No unchanged product test/build suite was rerun.

## Accountable operator prerequisite

The root coordinator verified no callable owner-message, execution-dispatch or
scoped handoff API for this Chat. A comment on TASK-2026-06457 is a coordination
request, not a queued release or a transfer of the active operator lease.
See ../OWNER_SESSION_ACTION.md and ../evidence/operator-access-20261123.json.

The existing owner must execute the approved scope itself or provide a genuine
recorded scoped handoff. Its acknowledgement must identify this product commit and
manifest, the accountable executor, Core-only backup/graceful stop/start/rollback
scope, retained original work and an explicit execution window.

The exact bounded diagnostic can be run only by that existing operator through
its established service-identity route:

    /usr/bin/sudo -n -u mcpdev -- /usr/bin/python3 -I -S -B \
      /srv/abit-dev-linux/tmp/linkedin-admission-recovery-20261123/observe_original.py

Diagnostic source SHA-256:
6bb51c3b738ebda7687b8c875f310028168d66eb8e095b8977d65fc6ce3f1ffe.

This Chat did not execute that command. Its gate/target-lease/audit output does not
itself prove provider completion, external-worker quiescence or safe Core shutdown.
Obtain positive original/provider and current-worker evidence through the supported
lifecycle. Unknown historical records remain retained; do not clear them to make
an idle check pass.

## Production binding; no invented acknowledgement protocol

The source uses the existing reviewed-manifest pattern. A separately reviewing
operator must inspect genuine current records and protected evidence, create the
exact operator-binding.json, review its bytes, then bind its SHA-256 into a reviewed
copy of controller.py's EXECUTION_BINDING_SHA. Until then every production action
fails before private access, stage creation, lock acquisition or systemctl.

There is no --ack, --force, --test, arbitrary command, gate action or execution
override. A hash of an invented acknowledgement is not authority. The evidence
references below are manual review prerequisites with immutable hash checks;
the controller does not pretend to authenticate an ERP acknowledgement automatically.

Required binding content:

| Field | Required real evidence |
| --- | --- |
| product_commit, product_manifest_sha256 | Exact approved values above |
| runtime_root, stage | Fixed resolved live release and fixed fresh private stage |
| operator_session_sha256 | Exact current operator-session.json bytes |
| operator_owner, operator_state, operator_phase | Exact current accountable identity/phase |
| execution_expires_at | Explicit approved execution-window expiry, timezone aware |
| evidence | Exactly owner_acknowledgement, original_diagnostic, original_provider, uploads_preservation, vector_preservation; each actual root-private0600 artifact path and SHA-256 |
| closed_gate | Exact currently closed state, epoch, command_id and actor |
| gate_control_payload_sha256 | Canonical hash of the matching existing operator_control audit payload |
| core_before | Actual PID, start_ticks, UID, cmdline SHA and exact cgroup |
| preserved_processes | Fresh QA, actual relay child, relay watchdog, browser/support identities; no obsolete relay PID |
| unit | Exact User, Group, WorkingDirectory, FragmentPath and complete active DropInPaths |
| configuration | Private runtime-environment.json, final-snapshot.json, webui-secret-key, original launcher, unit and every active drop-in; exact hashes, UID/GID/mode/device/inode |
| files | Exact seven paths, before hashes/two absences, per-file UID/GID/decimal mode and parent device/inode/UID/GID/decimal mode |
| override_parent | Actual existing reviewed runtime drop-in directory metadata |
| work | Fresh historical lease/cohort hashes and exact relay/Dot cohort counts/hashes |
| private_gid | 0 for root-private production artifacts |

The binding format uses integer modes, unlike the old observation-only
bindings.json. That old file is not a production execution binding.

An actual acknowledgement and provider/preservation artifacts have not been
supplied. No sample production binding or fabricated receipt is included.
Uploads/vector preservation must already have been performed and verified through
the established operator procedure; this narrow controller does not create a new
filesystem-backup platform. The bound preservation evidence must identify the
actual private backup, manifest/integrity/readback checks and any writer limitation.

The owner/session byte hash and expiry are checked repeatedly. An owner renewal,
phase update, changed gate or expired execution window holds continuation and
rollback. The private one-use stage binds its original manifest bytes. Do not
silently change pins, reuse the stage with another binding or pretend automatic
continuation after expiry; obtain a separately reviewed continuation from the
accountable operator, preserving every old receipt and unknown command outcome.

## Fixed operator package layout

Before privileged invocation, the existing operator must verify the final reviewed
source hashes and put controller.py and service_sqlite.py in its established
root-owned0700 protected runner directory, with source files0600. Execute that
sealed copy, not a caller-writable workspace file. The helper bytes are read from
the same protected runner copy and supplied to the service interpreter through
stdin. This Chat has not created or invoked that protected runner.

After authentic binding/review, the existing operator stages the reviewed source
under:

    /srv/abit-dev-linux/abit-ai/deployment/linkedin-admission-recovery-20261123/activation/

Its product/ subdirectory contains the exact approved manifest.json and
candidate/open_webui seven-file payload. operator-binding.json must be an actual
protected root-owned0600 file. The existing global root-owned0600 lock is
/var/lock/abit-ai-governance-release.lock. The new one-use root-owned0700 stage is
/var/lib/abit-ai-linkedin-admission-20261123.

The source has no arbitrary package/stage/runtime arguments. It never modifies
the old controllers, receipts or private stages. Existing valid ABiT source and
customizations outside the seven targets are preserved.

The frontend retains UID722237112, GID988, mode0644. The Python files retain
UID999, GID988, mode0644; the two new modules use their adjacent module metadata.
Parent device/inode and directory metadata are checked through held directory
descriptors before privileged source replacement. Atomic no-replace linking for
new files has exact journal/inode-based crash reconciliation.

## Backup and live SQLite ownership

The controller holds the existing global flock, verifies payload/owner/closed
gate/original evidence/runtime/processes and records one-use backup intent before
creating private material. It backs up all eleven fixed databases, five source
preimages/two absences, candidate bytes and exact private startup configuration.

Every live SQLite connection is made by service_sqlite.py under service identity,
using normal read-only SQLite with WAL visibility. Root never opens live SQLite.
This prevents newly opened WAL/SHM auxiliaries from being owned by root.
No immutable=1 shortcut or production chmod/chown is used.

The root controller reads and verifies the helper source bytes, then supplies that
exact code through stdin to the established sudo -u mcpdev /usr/bin/python3
-I -S -B route. This avoids a mutable script-path execution race. Snapshot bytes
and the bounded receipt use standard stdout/stderr descriptors opened privately
by the parent; no special sudo closefrom/inherited-descriptor permission is needed.

Snapshots are internally consistent per database. Each child is bounded to512MiB
snapshot bytes,1536MiB address space, actual snapshot-page progress, available
memory reserve and a60second operation deadline. Source main+WAL sizing, private
filesystem free space, and a1GiB reserve are checked. Output uses actual fstatvfs
on the already open descriptor before and during1MiB chunks. Partial files and
child/intent evidence are retained on failure; no stage is reused or retried.

Root performs integrity/schema/table-count checks and an isolated restore exercise
only on private copied databases. All eleven actual receipt-map entries and their
backup/restore hashes are required. No automatic business-database restore exists.

QA and other preserved writers may remain active. The collection is not a global
atomic snapshot. Historical unknown leases and provider records remain untouched.

## Graceful activation and exact rollback

The fixed commands are backup, apply, observe, finalize and rollback. They are
operator actions after binding, not ready-to-run commands for this Chat.

Apply rechecks current Core identity, original-worker quiescence, exact gate/audit,
preserved process and historical cohorts, unit/configuration, all backups and
source hashes. Current Core-owned active/unresolved leases, current governance
work, current Codex work or a busy/unreachable relay block the stop. The normal
connector's inability to reach port8810 is not permission to skip this guard.

The owned temporary drop-in sets TimeoutStopSec=infinity, SendSIGKILL=no,
KillMode=mixed and Restart=no. Each reload/stop/start intent is fsynced before
submission. Unknown outcomes are observed, never replayed. There is no forced
Core/provider signal. A bounded observation timeout retains the original intent.

Before each source write the original PID must be absent, MainPID0, unit inactive
or settled failed, no pending unit job and every Core cgroup descendant absent.
Only exact before/candidate bytes with owned journal evidence can be reconciled.
The seven installed hashes and original metadata are checked before startup.

A new Core must match the original UID, launcher hash and exact cgroup, be newer
than the one-use start intent, and pass /health, /api/config and the actual served
/agent-activity.js?v=102e794ad614 body hash. Requests bypass caches and redirects.
The index is not replaced; authenticated browser body/behavior checks remain
root's separate live-verification task.

After local startup checks, the controller removes only its own exact override
and restores the original effective unit settings. Admission stays closed.
A result explicitly records that original work and marketing execution remain
incomplete and authenticated UI verification is required.

Rollback uses the same owner/gate/work/global-lock discipline. It restores only
verified old code and original metadata, deleting a new module only with its
exact candidate hash plus recorded absent preimage. An uncertain apply stop is
never stopped again. A pending/uncertain start is observed or refused; a settled
failed start permits explicit code rollback only with no unit job, MainPID0 and
an empty cgroup. It never restores business data, settles leases or reopens intake.

## Verification limits

The completed tests use actual temporary-file and SQLite operations, service
state emulation, fault injection for crashes/space loss, and actual same-UID child
processes for WAL visibility/auxiliary ownership. No UID function was mocked for
those POSIX ownership checks.

The real root-to-UID999 transition, root-private stdout/stderr descriptor handoff,
protected owner/gate/provider evidence, actual systemd stop/start, production
backup/activation/rollback, authenticated UI and controlled draft execution remain
unverified until the accountable operator performs the authorized work.

The saved marketing prompt requests publication. This controller has no route to
run it and does not create a draft-only capability. It never publishes, sends
messages, changes models, resets attempts, disables admission checks or edits
agent identity/configuration/history.

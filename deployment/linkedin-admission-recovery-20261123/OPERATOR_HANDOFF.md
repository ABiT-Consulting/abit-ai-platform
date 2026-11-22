# LinkedIn admission release: operator binding required

Approved product commit: 8c1bc2308fdf693659520103559863682333299b.
Approved seven-file manifest SHA-256:
955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250.

This package implements the explicitly requested nonmutating fallback. It contains
a read-only preflight and tests, not an activation controller. A matching product
manifest or a user release approval does not establish that the active maintenance
owner has yielded the current operation. The existing operator record has no
supported LinkedIn scoped handoff binding. No apply, backup, restart, rollback,
gate-opening or agent-execution command is exposed. The preflight exits 2 even
when every ordinary read matches, because activation remains blocked.

## Current evidence and missing owner input

At 2026-11-22 20:53 UTC, all five installed preimages matched the approved manifest
and the two new modules were absent. Core was PID3481989, start ticks131898701,
service abit-ai.service, running the existing run-production.py launcher from
/srv/abit-dev-linux/abit-ai. The runtime resolved to
/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui.

The accountable owner in the existing operator-session.json record was
chat-authorized-root-orchestration-recovery-20261122T1007, state
ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY, with lease expiry
2026-11-22T22:24:48.603297+00:00. Its active workstreams include original recovery.
An absent or expired lease is not permission to seize ownership or clear work.

The root coordinator must obtain the following through the existing owner and
ERP task TASK-2026-06457; a caller-supplied JSON acknowledgement is insufficient:

1. A fresh recorded owner acknowledgement or handoff specifically naming this
   commit, the seven-file manifest, the Core-only graceful stop/start scope, the
   responsible operator, and whether the existing owner executes it or yields.
   Supply the existing system-of-record comment/receipt identity and the operator
   record that positively binds it. Neither this preflight nor a new field name
   establishes a new handoff protocol.
2. A protected current gate observation proving admission is closed, with its
   epoch and original work/lease references. Preserve unknown historical cohorts
   and immutable evidence. State explicitly which live Core-owned workers must
   finish before the controlled stop. Do not settle unknown work by inference.
3. A fresh preserved process cohort. QA PID1404536/start84911733 is active.
   Relay watchdog PID2954626/start127496566 is active, but the old pinned relay
   child PID2957025 has exited. Bind the actual relay child and browser workers
   rather than copying the obsolete process manifest.
4. A fresh private backup and restore-check receipt covering all required data,
   configuration and rollback files below, with hashes and protected metadata.
5. A separately reviewed, newly bound one-use activation controller using the
   established operator route, global release lock and graceful mechanism.
   Preserve all old manifests, receipts and controllers.

The present JSON binding records observations, not authority. Any drift requires
reinspection and a newly reviewed binding; do not edit the expected PID/hash until
a mismatch passes. The product manifest's historical deployment_approved=false
field remains unchanged; subsequent user approval is a separate record.

## Ordinary read-only command

From an isolated export containing both sibling package directories:

    python3 deployment/linkedin-admission-recovery-20261123/preflight.py preflight \
      --package deployment/linkedin-admission-20261122

The script prints safe hashes/status only, creates no stage, acquires no release
lock, opens no business database, imports no live package, and performs only
systemctl show and filesystem/proc reads. Its fixed result cannot authorize any
action. A matching snapshot does not reserve the runtime against concurrent work.
The authenticated operator must repeat authoritative checks under the established
exclusive release lock immediately before any eventual mutation.

Run the focused tests against the isolated export, never against production data:

    PYTHONDONTWRITEBYTECODE=1 python3 \
      deployment/linkedin-admission-recovery-20261123/test_preflight.py

Tests inspect the approved candidate bytes, temporary files and synthetic
observation records. They do not call real systemctl or access live data/session
state. They distinguish read-only guard coverage from an activation test.

## Seven exact files and metadata

| Runtime relative path | Required installed preimage | Metadata to preserve |
| --- | --- | --- |
| frontend/agent-activity.js | Approved manifest hash | UID722237112, GID988, 0644 |
| models/automation_admission.py | Absent | UID999, GID988, 0644, from adjacent models/automations.py |
| models/automations.py | Approved manifest hash | UID999, GID988, 0644 |
| routers/automations.py | Approved manifest hash | UID999, GID988, 0644 |
| utils/agentic_maintenance.py | Approved manifest hash | UID999, GID988, 0644 |
| utils/automation_admission.py | Absent | UID999, GID988, 0644, from adjacent utils/automations.py |
| utils/automations.py | Approved manifest hash | UID999, GID988, 0644 |

Parent device/inode and directory metadata, unit/drop-in files, launcher bytes and
Core process identity are pinned in bindings.json. Do not normalize the frontend
owner to the backend owner. Verify permissions and all parent identities again
during install. The live frontend index currently requests
/agent-activity.js?v=102e794ad614; authenticated postrelease verification must prove
the new served body hash and actual page behavior rather than assume an old
query token is invalidated.

## Existing procedure references; do not run them for this package

The following controllers are already bound to different releases and stages.
They are reference implementations, not generic commands with a manifest option.

- Backup:
  /srv/abit-dev-linux/abit-ai/deployment/orchestration-recovery-20261122T1015/backup-current-core.py
  SHA-256 a8d387a87ef503474bc6198b650a4a23bbeaf36fa3958bad5dc55972043e6209.
- Graceful activation:
  /srv/abit-dev-linux/abit-ai/deployment/orchestration-recovery-20261122T1015/release-core-graceful.bound.py
  SHA-256 4307136a592ace372c2e99af40bb22a0f44dcc3be1b7ea11a90069c354162bd4.
- Shared lock: /var/lock/abit-ai-governance-release.lock, root-owned 0600.
- Proposed new private stage: /var/lib/abit-ai-linkedin-admission-20261123.
  Its path is a proposal; this package never creates it. If it already exists,
  observe the existing intent/result instead of reusing, deleting or retrying it.

The old controllers require their exact owner, old manifest, original identities,
backup receipts and source baselines. Do not relax any of those checks or replace
their files. They do not accept this seven-file release.

## Required backup and preservation package

Use a fresh root-private 0700 stage and exclusive 0600 files through the established
operator route. Journal the backup intent before work. Preserve a partial failed
stage and report its state; never reuse it or infer success from its existence.

For each database below, use the established SQLite backup API with a read-only
source, bounded progress/deadline, a verified backup hash, integrity check, and an
isolated restore exercise. Compare restored schema and table-count hashes with the
backup. Preserve receipt timestamps and per-database results. Never copy a changing
SQLite main file alone or claim that a copied WAL supplies cross-database atomicity.

- webui.db
- agentic-governance.sqlite
- abitdot-primary.sqlite
- aibit-chatgpt-relay.sqlite
- abit-slack.sqlite
- abit-telegram.sqlite
- codex-workspace-runs.sqlite
- erp-dot-issues.sqlite
- erp-pending-review-qa.sqlite
- erp-task-resolution.sqlite
- maintenance-admission/gate.sqlite

QA and other writers remain active. Each SQLite backup can be internally
consistent; the collection is not a cross-database atomic snapshot. Preserve the
original ledgers and never restore them automatically as part of source rollback.

Preserve runtime-environment.json, final-snapshot.json, webui-secret-key,
run-production.py, the systemd unit and every active drop-in in private storage.
Record hashes/metadata without printing secret values. Back up the five exact
preimage files with their per-file UID/GID/mode and record absence of the new two
modules. Bind backup source hashes to the approved manifest.

Before activation, bind the existing verified backup or a fresh protected
preservation receipt for uploads and vector_db. Inventory their size and active
writers before selecting the established snapshot/archive mechanism; verify its
file/hash manifest and isolated readback, preserve metadata, and report any
concurrent-writer limitation. The old eleven-database backup script does not cover
these directories. This source-only change must never overwrite, prune or replace
either directory or the data root. Check available space against measured backup
size and retain business/audit/security data and historical backups.

## Eventual activation and rollback requirements

These are requirements for the owner-reviewed controller after handoff, not
executable approval or a replacement for its original guards.

1. Hold the existing global flock. Revalidate the exact owner acknowledgement,
   original work state, closed gate epoch, runtime, PID/start ticks, seven preimages,
   protected backup and restore receipts, individual metadata and preserved process
   cohort. Refuse an existing or ambiguous one-use journal.
2. Reuse the established graceful service mechanism: verified temporary systemd
   override with TimeoutStopSec=infinity, SendSIGKILL=no, KillMode=mixed and
   Restart=no. Record/fsync each unique lifecycle intent before submission.
   A command with uncertain outcome is observed, never replayed. Do not kill
   providers, force signals, or use a casual systemctl restart.
3. Request one graceful Core stop. On timeout retain the journal and wait for the
   accountable operator. Before replacing code, prove the original PID has ceased,
   MainPID is zero, the unit is inactive and its cgroup/descendants are empty.
   Preserve relay/browser/QA identities. Do not claim a global drain.
4. Install only the seven approved candidate bytes atomically, with parent identity
   and exact preimage checks immediately before each write; preserve per-file
   metadata. Journal each replacement so partial results remain distinguishable.
5. Request one controlled Core start using the original launcher, private
   configuration, data path and secret. Verify actual identity, health, config,
   source hashes and expected application behavior. Preserve old receipts.
6. Remove only the controller's own exact temporary override through the established
   once-only finalization sequence when safe. Admission remains closed. Never
   reopen the gate automatically, settle retained original work, or trigger the
   marketing agent. Root coordinates the supported recovery and separately
   controlled draft-only run after deployment and correct original reconciliation.

Rollback is code-only, under the same owner/lock discipline. Require exact current
candidate hashes for every installed target before restoring its verified preimage
and original metadata. Remove either newly introduced module only if it is the
exact approved candidate and its recorded preimage was absent. Preserve partial
or ambiguous state for operator review instead of deleting unknown content.
Use the same graceful lifecycle requirements; never restore business databases,
reset histories/counters, clear leases, reopen admission or replay original work.

## Verification scope

The tests cover exact payload binding, real temporary-file hash/metadata/parent
drift, symlinks/hardlinks, missing/new targets, Core PID reuse, preserved QA drift,
unit/configuration drift, read failure, unknown/expired owner, untrusted
acknowledgement, an existing/unknown one-use stage and repeated read-only checks.
Mutating action names and acknowledgement overrides are rejected by the parser.

No backup, activation, rollback, protected gate read, service restart, UI browser
test or controlled marketing execution is performed by this fallback package.

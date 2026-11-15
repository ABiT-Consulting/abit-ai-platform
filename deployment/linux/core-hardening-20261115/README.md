# Core increment controller preparation

This directory is source preparation. The controller has PLAN_SHA = 'UNFROZEN'
and refuses execution. No Core deployment, gate change or service action has
been performed by its author.

The selected release consists of eleven exact Python files: the six previously
reviewed hardening leaves, delivery and orchestrator corrections, the current
start-recovery correction, and two new helpers:
agentic_delivery_continuation.py and agentic_delivery_control.py.
The final source commit, candidate hashes, runtime snapshot, actual-overlay test
receipt and activation baseline must be frozen and independently reviewed before
the plan hash can be bound.

## Preserved boundaries

- The actual live fixed Project profiles and workspace behavior remain in the
  candidate. Repository templates and repository-registration features are excluded.
- Current native result-store behavior, architecture-review phase rules, immutable
  packets, original responses, receipts, admission history and all unselected
  Core files remain intact.
- The completed relay detector ddf85a4e, Node 1273362 / birth 68729026,
  Chrome, watchdog, Xvfb, egress, relay tunnel and MCP auth/backend/tunnel
  identities are protected. The controller performs no relay or browser action.
- Main and QA unit files, existing drop-ins, launchers, fixed process arguments,
  stable environment hashes and service security settings are preserved.
  Generation-specific systemd environment values alone are excluded from
  the stable environment digest.
- A fresh authenticated admin browser observation of https://abit.ai/api/tasks
  must report exactly zero tasks for the exact current Main PID and birth, at
  most 120 seconds old. Anonymous health, a different process, a stale observation,
  or extracted credentials cannot satisfy that gate.

Root observed a separate Core activation at 09:26:38–09:26:43 UTC. The current
Main/QA identities are 1362365 and 1363297, and two live files changed. An attempted
read-only activation-baseline capture subsequently denied on real work: at
09:46:47 the gate was OPEN 21 with one active original Developer invocation.
The old idle epoch-20 baseline is invalid. That execution must retain its
original identity and reach a supported settled outcome before activation
readiness can be frozen. Offline source and fixture preparation may continue.

## Execution contract after review

The controller obtains the existing release lock followed by the existing
dispatch lock, without creating or clearing either lock. It checks the complete
147-file source cohort, exact parents, service and process identities, protected
configuration, relay idleness, legacy journals and zero current work.

A preflight has no mutation path. Execution permits only an exact reviewed
DRAINING-to-SEALED transition at the pinned epoch with zero active/unresolved
leases, or an already exact SEALED state. It uses the supported immutable
maintenance helper and one durable seal intent. Lost acknowledgement is reconciled
from the same gate transaction and exact audit row; no second command or OPEN
operation is provided. It does not assert that the first acceptance flow is closed.

Execution archives the exact original/candidate source, configuration and metadata
in a new root-private operation directory. It then makes eleven SQLite backup-API
copies before service stops, with integrity checks, hashes, fsync, capacity
reserve and a bounded deadline. These are individually consistent database
backups; no cross-database transaction or database-restore path is claimed.

Both current units have SendSIGKILL=yes and a 90-second stop timeout. A normal
systemctl stop could therefore force-kill after its timeout. The controller
creates exact owned temporary runtime drop-ins setting only SendSIGKILL=no,
along with the reviewed Main start fence and a narrowly scoped QA start fence.
It verifies their effective settings before any Stop. All other unit security
properties remain unchanged. The original unit configuration is restored only
after confirmed startup and verification. Held or uncertain outcomes retain
owned protection for review.

Main and QA each receive at most one Stop and one Start, after create-only durable
intents. The existing lifecycle helpers perform original PID, empty cgroup,
new process birth and health readback. A timeout/nonzero acknowledgement cannot
authorize another service action. No force-kill, automatic rollback, admission
settlement, ERP callback, model send or business replay is present.

## Source creation and Python caches

Existing targets retain their exact owner, group, mode, xattrs and nanosecond
mtime through the controller's bounded atomic replacement. The temporary source
has all metadata applied and fsynced before its exact compare-and-swap rename. Each new helper requires a proved absent path and the exact parent
device, inode, owner, group, mode and ACL. Creation uses an atomic no-replace hard
link from a fully written/fsynced temporary file, so a concurrently arriving file
is preserved and denies installation.

Preserving source timestamps can leave timestamp-based Python bytecode valid
when the new source has the same size. The guard tests reproduce that condition:
Python loads old code until the selected cache is removed from its import path.
The controller therefore archives only the selected modules' regular,
single-link __pycache__ and legacy .pyc entries, including orphan helper
caches, while both services are stopped. Every archive has exact metadata/hash
evidence; unrelated caches remain untouched. Selected caches must be absent
before startup. A nondefault PYTHONPYCACHEPREFIX is not silently accommodated.

The post-install cohort must contain exactly 149 files: the original 147 plus
the two approved helpers. Every unchanged file and every candidate hash/metadata
must match.

## Preserved historical delivery inputs

The final activation baseline must bind the separately prepared historical
archive for the exact original authorization. This is one protected index and
three content-addressed objects under
/var/lib/abit-ai-agentic-gateway/delivery-recovery-history/: the unchanged
operator bytes, exact durable native ACK bytes and minimal deployment projection.
The directory must remain Root-owned 0:988/0750; every object must be a regular
single-link file 0:988/0640 without symlinks. The index is bounded to 64 KiB and
each object to 256 KiB.

These four files and the directory metadata are required protected inputs.
Their exact hashes and metadata are checked under the release locks, before
replacement/start and after activation. Private copies are retained in the
operation evidence. The controller does not construct the archive, infer
historical authority, edit it or write any business ledger. The committed
runtime reader remains responsible for its closed-schema and provenance checks.
The runtime selection remains eleven files and two new helpers, 147 to 149.

## Rollback and held outcomes

There is no automatic rollback or database restore. The operation retains
original source, metadata, selected bytecode, private configuration and database
backups plus a concrete rollback plan. A rollback requires a separate fresh,
reviewed binding to the then-current processes, browser proof and sealed idle
state.

Existing source can be restored from exact .before artifacts using the same
metadata-preserving replacement primitive. A new helper may be removed only by
the bounded rollback primitive after its exact installed proof and parent
identity still match; it cannot unlink another path or changed file. Candidate
caches must first be archived under the same scope. No rollback may replay an
existing Stop/Start intent or alter business history.

## Verification

Run the pure controller guard suite:

    /srv/abit-dev-linux/abit-ai/venv/bin/python -B deployment/linux/core-hardening-20261115/test_apply_core_hardening.py

The accepted first controller checkpoint passed 49 distinct tests using temporary files and fake
lifecycle/gate objects with socket/DNS access blocked. Cases cover browser proof,
scope and metadata, existing versus absent targets, concurrent file arrival,
both helper rollback boundaries, real stale bytecode behavior, exact full cohort,
preserved environment/process identities, graceful unit policies, durable
one-time service intents and lost-acknowledgement seal readback. Additional
regressions verify existing-source installation with a deliberately old mtime,
source/parent drift rejection, and that all nested lifecycle gate loads reuse
the same verified immutable maintenance module.

Six additional focused archive guards verify valid read-only inputs and denial
of absent, changed, symlinked, unreviewed or incorrectly protected files and
changed parent metadata. Their receipt is retained separately from the accepted
49-case checkpoint.

Evidence is retained under .git/core-hardening-controller-validation/.
These guard tests do not replace the final actual-runtime overlay regression
suite, independent release review, fresh browser proof or actual live verification.

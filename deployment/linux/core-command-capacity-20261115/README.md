# Core registered-command capacity: two existing files

This controller prepares a narrow Core contract replacement while retaining the exact submitted original, its unresolved admission, and its immutable format selection. It is **UNFROZEN and has not been executed**. `PLAN_SHA = 'UNFROZEN'` rejects both execution and preflight before any helper loading or lifecycle action.

The current original has an immutable `returned-reply-format.v1` selection. Its corrected registered-command receipt has 4,409 characters and 4,420 UTF-8 bytes. The finite shared-contract bound is 90,091 characters. This cutover changes that capacity contract only; it does not install the later format-authority policy or alter the selected reply.

## Exact replacement scope

| Existing runtime leaf, relative to `open_webui` | Kind | Candidate SHA-256 |
| --- | --- | --- |
| `utils/agentic_contracts/developer_delivery.py` | RuntimePython | `90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210` |
| `utils/agentic_contracts/schemas/governance_v1.schema.json` | RuntimeJSON | `5131fd838234f34173b4a7b30360d3c53d3d18f1fcdbc2afb079ba15db8d897c` |

The runtime remains 147 files: 144 Python and three JSON files. Both selected files must already exist, and both must require replacement. The complete remaining cohort stays byte-for-byte and metadata-identical. No new runtime helper is installed. The source in this directory is release tooling, outside the runtime cohort.

The shared contract comes from platform commit `0bb85b29b94e70b106de42f71102389a24cc6de5`. The native publication prerequisite independently binds the native helper, actual service IDs/images, preserved configuration and authenticated replacement-channel receipt. Build success or a queued publication cannot satisfy it.

## Preserved original and maintenance state

`preserved_original.py` reads five databases through the reviewed service-identity, query-only SQLite helper, with an explicit read transaction for each database. It preserves the exact original invocation/run, frozen packet and Task fence, positive native start, three completed gateway calls, unresolved provider record, already recorded AutomationRun success, Retry 14 cursor and the existing sequence-150 format-selection audit. The protected correction file and historical delivery archive retain their actual bytes, owner, mode, parent identity and hashes.

Admission remains **DRAINING, epoch 24, one unchanged original admission**. The original admission belongs to the exact dead process birth at epoch 21. A dead owner is not settlement: its lease, state, reference and audit remain untouched. The controller has no gate-transition, admission-transfer, original-recovery, format-selection, native-publication, model-request or database-restore operation.

Every invocation, Task lease, start, result, action, delivery journal, delivery step, gateway session/call, native control and Project/Task control table is hashed and compared. The whole governance audit remains exact. All old maintenance leases and audit entries remain exact.

One narrowly identified exception concerns bookkeeping for an already completed QA receipt. At most 64 additional **finished** QA leases may appear, tied to the exact terminal QA reference and receipt hash, epoch 24, no parent, and either the original QA process birth or its independently verified replacement. Each requires the exact two receipt-settlement audit payloads. Old-QA additions must precede its stop acknowledgement; before that acknowledgement they are bounded by the current readback time. No active QA lease, different receipt, new work, foreign owner or unrelated audit is accepted.

The frozen baseline retains its preservation snapshot and canonical hash. Preflight compares it with the first locked readback using this same narrow rule, allowing benign terminal-QA polling without accepting business drift. Later readbacks compare against the first locked snapshot. Separate SQLite transactions provide consistency within each database; no cross-database atomic-snapshot claim is made.

## Required release inputs

A subsequent, separately reviewed freeze must provide all of the following. None is fabricated by this preparation.

- A committed source revision, exact package manifest, successful offline candidate-test receipt and exact controller/helper pins.
- A fresh 147-file runtime baseline, all 11 process identities, stable Main/QA environment hashes, static unit properties, protected configuration, relay detector/registry, historical archive, protected v1 correction and original preservation snapshot.
- An exact Root-private deployment binding with current Main/QA PID, process birth and entrypoint hash, and a new Root-private operation directory.
- A fresh authenticated **administrative browser** observation of `https://abit.ai/api/tasks`, actual task count zero, exact current Main PID/birth, no extracted credential and age at most 120 seconds. `admin_authenticated` must be true. This proof is rechecked immediately before Stop; a health probe or anonymous page is insufficient.
- Actual Root-private positive native publication and authenticated new-channel receipts, with exact file hashes. `NATIVE_FIXED` pins native/shared revisions, helper hash, capacity and unchanged configuration hashes. `CHANNEL_FIXED` retains the physical Start/Stop history from the reviewed native workflow. The closed expected projections also bind the exact three running native services: `backend`, `governance` and `scheduler`. A missing receipt denies.
- Adequate free capacity for all eleven consistent SQLite backup-API copies, source/cache/configuration preservation, bounded private evidence, and the existing one-GiB remaining-space floor. No log, audit, backup or business-data cleanup is performed.

The closed release-plan schema is enforced by `validate_plan`; the closed two-file manifest by `validate_manifest`. Their actual values remain unbound. The native positive receipt has not yet been supplied, and fresh admin browser access is still required.

The aggregate receipt vocabulary is pinned to the existing preparation-only `CORE_PUBLICATION_CONTRACT.json` (SHA-256 `e3cafbc61a5ad67c6f8162343a3d89da8066f6745c9c2f3974cb781625e04d0e`). Its `governance` entry must be produced by an explicit verified projection of the actual native `agentic-governance-worker`, retaining that container’s ID/image, loaded contract and coverage evidence. The controller accepts only the aggregate key `governance`; it never accepts both aliases interchangeably. Creating and reviewing that actual aggregate producer remains a separate required work item. No positive publication receipt exists in this source preparation.

## Lifecycle and failure behavior

The controller acquires the existing release lock followed by the existing dispatch lock. It validates the complete source/process/configuration cohort, idle relay/browser state, exact retained original and closed gate. It creates a private, create-only operation intent and backs up all eleven databases before stopping a service. Each SQLite backup uses the backup API, a deadline, `quick_check`, fsync and a retained digest; no source database is restored or copied over.

Before either Stop, exact owned runtime drop-ins load `SendSIGKILL=no` and fence manual/startup service starts. Their loaded properties, static configuration and paths are checked. The controller sends at most one Stop and one Start per selected service, each preceded by a durable create-only intent. Unknown acknowledgements lead to bounded readback of the original action, never another command. A stopped service must have no original PID/birth and an empty cgroup.

While both services are stopped, selected Python bytecode is quarantined, and the two existing leaves are replaced with parent/source compare-and-swap checks. File owner, group, mode, mtime and extended attributes are preserved. The JSON schema is validated as JSON and is never treated as bytecode. The complete 147-file cohort is checked throughout partial installation and before each next lifecycle boundary.

Replacement Main/QA processes must have new process births and retain the original executable, argument hash, cgroup, parent/UID/GID, stable environment and entrypoint. Health/configuration, relay/channel identity, source bytes, retained history and exact gate are checked after startup. Only the controller's own exact temporary guards are removed; original loaded unit configuration is verified again. No automatic restart, second action, force kill, rollback or cleanup is attempted on failure. A failure retains private evidence and any remaining owned guards for explicit reconciliation.

Successful controller completion would mean only: two capacity files installed, original records/admission/selection retained, original unit configuration restored, and DRAINING24 still active with one original admission. It would **not** mean the governed task completed. The existing v1 recovery path requires OPEN; any later reopen, original admission settlement and same-run completion sequence requires its own reviewed authority and remains outside this controller.

## Source provenance

The external preparation-only controller was read and preserved without modification:

`/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-full-20261113/core-registered-command-contract-rollover-20261115/apply-command-contract.template.py`

SHA-256: `abc0f6ccff456e12e74e56618cb2fe9252701bd6f59e09691a19e26f4246efd1`.

This replacement retains its exact two-leaf intent and original-state restriction while adding the missing authenticated-browser, locking, graceful shutdown, complete-cohort, metadata/CAS, protected-capture and process-environment checks. It borrows exact reviewed primitives from:

- `../core-hardening-20261115/apply_core_hardening.py`, SHA-256 `f6477ec6b7173a7c7dd07a7423c037ea624ca759dc6383d2353f64bbcdd2a0b3`.
- `../core-hardening-20261115/runtime_validation.py`, SHA-256 `32712f068290c2ac4b271ef848f49a52474bb345524cdcd8a6293ec52d7cfe60`.
- `../relay-feed-hardening-20261115/apply_relay_feed.py`, SHA-256 `e12ba354be54848fa7930d8423c7c66064dbf8bb364a7902d3ff778f59c2ed20`.

The first guard module transitively pins the existing reviewed lifecycle, metadata, history, lock and release helpers. This controller does not call the 14-target controller's `main`, `zero_current_work` or `seal_once`, and never reinterprets the unresolved original as idle work.

## Offline evidence

`test_command_capacity.py` uses temporary files and SQLite databases. Network, DNS, subprocesses and live-state file opens are denied. It exercises exact scope/type checks, original-history/admission preservation, terminal-QA boundaries, native-publication denials, browser freshness/admin checks, loaded graceful policy, one-shot unknown-ACK behavior, full 147-file cohort checks, metadata/CAS, bytecode symlink denial, storage reserve and eleven actual SQLite backup-API copies.

The initial 37-case run had 36 passes and one assertion mismatch: a changed original admission was correctly rejected by the earlier immutable-facts guard instead of the later lease guard named by the fixture. Its log and receipt are retained. The corrected and extended full run completed **41 distinct cases, all passing**, with no skips and unchanged source pins. Independent review then identified that the native service-role set also needed to be closed. A new wrong-three-role regression failed on those bytes; the guard was narrowed to exactly backend, governance and scheduler. Only the affected native-prerequisite group was rerun: **7/7 passed**, comprising six repeats and one new case. Evidence therefore covers **42 distinct passing cases across the two runs**, not a single clean 42-case run. The initial assertion mismatch and service-role negative baseline remain retained. An intermediate seven-case run used the container vocabulary; that run is also retained and was superseded after verifying the exact aggregate receipt vocabulary. The final seven-case run denies both an unrelated service triple and the unprojected container alias. `validation.json` records every run and its exact scope. These tests do not execute the production controller or validate an actual cutover.

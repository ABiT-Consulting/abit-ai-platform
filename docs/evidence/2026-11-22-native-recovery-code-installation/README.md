# Protected source installation and read-only Core observation

On 22 November 2026 at 18:45:47UTC, the authorized fixed installer ran once through the existing Jimran sudo route. It created only these source leaves:

- Original holder `214f79fa62136a29019178ee3241d1c6f2ea19855a2f25785d55b3595df0899c` at `/var/lib/abit-ai-native-overlay-held-drain-source-20261122/hold_native_overlay.py`.
- Recovery holder `5e01ac517e7e7c4f961361608ee94444aa5b02f35aef734608d9675a472e1a6a` at `/var/lib/abit-ai-native-overlay-recovery-holder-source-20261122/hold_native_recovery.py`.
- Receiver `b18c9d5ef2c3b8bc245f8011a8ce5aae7f526d8568bfdeae3cd7778d57d02e26` beside the recovery holder.

All three committed input bytes matched source commit `11530ff2c01382bfb35743bf682a25a757b3a314`. All target files were absent beforehand. The installer used exclusive, no-follow creation, root0400 files, a new root0700 code directory, exact root ancestry checks and file/directory fsync. The five existing dependency files and their metadata were unchanged.

A separate interpreter and independent readback algorithm verified all eight protected source/dependency files, their hashes, regular-file ownership, link counts and root0700 directories. No source module was imported or executed. No manifest, operation stage, original-flock witness, signature, barrier or service action was created. These are installed inert source files, not an active holder.

Do not replay the installer. It is create-only and rejects the now-existing targets.

## Actual receipts

- `installation.json`:73befa9b9851ab5fb52a030be2c2b09d54328069b914351e5f1ca64445a4d012.
- `independent-readback.json`:195d1b748cafb3081bdf9ebca4734f2602e73a27f1bbddd453ed946079b110e2.
- Original connector operation:99aefffc-9ea9-4270-adcf-5782824e1ec0, terminal exit 0.

The installer and readback source are retained. They operate only on the fixed reviewed code paths and never invoke either installed holder.

## Current Core observation

The successful read-only observation at 18:48:29UTC found:

- `abit-ai.service` active/running, PID 3481989, start ticks 131898701.
- Port 8888 HTTP reachable; `/health`200/status true; `/api/config`200/version0.11.4.
- Maintenance gate open at epoch 94; zero active/unresolved leases owned by the current Core process.
- Governed ledger: 4 blocked and 2 uncertain, with no running/cancel_requested rows.
- Codex ledger: no submitting/running rows.
- Installed relay's side-effect-free `/runtime-status`: zero HTTP handlers, relay turns, ERP turns and ERP inspections.

Stored relay rows include 6 queued and 13 running; Dot contains 17 prepared. Other process generations still have 28 active and 33 unresolved maintenance rows. These historical records were retained without settlement or cleanup. Runtime counters are a current observation, not proof that those records completed.

Only aggregate state counts and selected public status fields were exported. No job IDs, prompts, credentials, browser login or fabricated session were used. This observation does not seal admission or authorize a restart.

The first observer exited before its first HTTP request because its helper name shadowed the imported HTTP module. Its source, empty output and error are retained. The corrected `observe_core_v2.py` completed once; no production code changed.

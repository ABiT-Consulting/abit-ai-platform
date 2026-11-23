# Codex-first + original delivery continuation — controlled release candidate

**Status:** Source-only, **NOT DEPLOYED**, not independently approved for production, no operator ownership transfer. Do not treat branch publication, tests or this manifest as release authority.

## Scope

- Review branch: `release/codex-first-continuation-20261123` against the pinned `feature/agov-api-live-20261113` base `d6e95fe46a148ce04efccd0653256173263c4f3d`.
- Runtime target: `/srv/abit-dev-linux/abit-ai/current/runtime` on ABIT-DEV-LINUX, service `abit-ai.service` only. Independent QA remains separately running.
- **13 runtime files**, existing current-runtime preimages and candidate SHA-256 in `release-manifest.json`. Four Codex modules and the missing delivery-continuation module are new; eight existing modules/routers are modified.
- A single systemd drop-in `50-agentic-codex.conf` sets service-owned `CODEX_HOME` only. Do not transfer any user credentials.
- GitHub PR #26 and #27 are test-only and **not binary dependencies** of this deployment. Their hosted CI and formal review decisions remain separate.
- The existing `mcpdev` governance database, maintenance admission journal, Chrome profile, relay, core and QA queues **must be preserved**.

## Compose/review evidence

The 11-file Codex candidate was rebased against the actually installed runtime; two router conflicts were resolved to retain native IT execution and the existing original-run wakeup while selecting the configured Developer engine. The original delivery gateway adds a verified, clean three-way merge from ancestor `a60145155`; new continuation module is pulled from the tracked source. Do not apply the old November 18 package directly.

The source requires a fresh independent review of this *combined* composition. All source trees and test fixtures included here are for code review and must not be used as a service replacement while active original executions are uncertain.

## Source lineage compatibility (not part of the 13-file live installation)

The target `feature/agov-api-live-20261113` Git source was behind several already-installed Core utilities. This review branch adds **15 previously runtime-only modules** and synchronizes three existing signatures (`agentic_governance`, `agentic_erp_channel`, `agentic_orchestrator`) to their exact current installed bytes, recorded in `source-parity-manifest.json`. These 18 paths are **source-of-truth/review alignment only**, not new live-file replacements. They must remain hash-pinned and independently reviewed for public source provenance. Do not reinstall them from the source branch if newer live bytes have appeared.

The exact 13-file release manifest is the **only** proposed product-source installation scope. The read-only checker also verifies all 18 runtime dependency hashes. Any mismatch is a hard stop and requires rebase, not overwrite.

## P1 independent review correction — original handoff context survives quota fallback

Review on PR #28 at commit `0e82d26a4` identified a genuine P1 in `agentic_executor.DotAdapter.submit`: it showed only `handoff_context_digest` and incorrectly instructed Dot to retrieve the full handoff from `context.read`, even though that gateway operation returns the frozen WorkPacket and not the transient Codex quota checkpoint. The corrected prompt now carries **the exact bound `row['context']` as untrusted task data** along with its immutable digest/reference; this includes the verified original operation IDs and checkpoint attached by `CodexAdapter.submit` to `fallback_row` on a fenced quota handoff. It explicitly does not confer gateway capability or ERP authority, and no extra provider send or task claim is introduced.

`backend/tests/test_agentic_dot_handoff_context.py` evaluates the actual `DotAdapter.submit` prompt expression in an isolated AST fixture and asserts that the original PM context and Codex checkpoint/call IDs survive, including regular specialist roles. The new test requires no live ERP/ChatGPT session. The revised release manifest is distinct from the original candidate and must receive its own independent review and any fresh production approval; do not apply the old hash.

## Mandatory preflight and authorization

1. The **actual existing root operator** reviews protected gate epoch/owner, unresolved admissions, per-task fences and all uncertain original requests, including `TASK-2026-06448`. Do not seize a lease because its wall-clock expiry passes. Use a supported signed/acknowledged handoff only.
2. Verify `mcpdev` authenticates with `CODEX_HOME=/srv/abit-dev-linux/abit-ai/data/agentic-codex-auth` and that the CLI, isolated stdio gateway, cgroup sandbox, pinned repository access and role authorization are effective under the production service restrictions. The interactive `jimran` login does not count.
3. Have a separate reviewer approve the full current package including native continuation and source/private gateway changes; record exact manifest SHA, role/capability and current SRS/ERP eligibility. Explicit production release authorization for **this** manifest is required.
4. Re-read **every** installed preimage immediately before activation. `python3 deployment/codex-primary-continuation-20261123/verify_release.py` performs read-only hash checks. A changed preimage is a hard **STOP**: rebase again, rerun tests and obtain new approval.
5. Under the established root release procedure, produce and hash fresh consistent SQLite backups of product/governance data and preserve WAL consistency. Make exact same-version copies of the eight existing source preimages, systemd units, drop-ins and configuration. Preserve the original immutable journals. Prove rollback readiness before drain.

## Installation order (only after all gates)

1. Acquire approved maintenance gate and exclusive current root operator ownership; gracefully drain **Core only** without re-sending provider requests. Verify no protected active/unresolved attempt will be orphaned by the operation. Do not stop relay, Chrome, independent QA or unrelated host services.
2. Verify source and runtime file hashes (13 files) plus current Core PID/lease epoch under the release lock. Install missing Codex and continuation modules first, then existing utility patches, then routing/executor modules last. Use protected staged files, no symlinks, no partial replace or shared mutable checkout; preserve ownership, modes and audit.
3. Install the reviewed `50-agentic-codex.conf` only if its existing drop-in preimage still matches; run `systemctl daemon-reload`, restart Core through the reviewed procedure. Open admission only after complete effective-service, original-run and rollback checks.
4. Verify actual installed file and drop-in hashes, effective loaded modules, service-user Codex login, policy API `codex_first` with `fallback_on=["quota"]` and Developer routing via controlled job admission. PM, Architect, IT and independent QA retain their existing specialized routes.
5. Perform one admitted, durable real relay job and loss-of-ack recovery, then one genuinely eligible nonproduction ERP task through governed action receipts, source/tests, independent QA, ERP result readback and automatic successor/idle state. Never claim deployment from GET `/health` alone.

## Rollback

Follow existing reviewed Core-only rollback procedure if post-install tests fail. Under the same root lock, require installed candidate hashes to still match. Gracefully stop only affected Core, restore **exact** saved source preimages and prior systemd drop-ins; remove only newly created files whose hashes match the approved release. Keep all live DBs, WAL, immutable run history, Chrome authentication, task states and original provider requests. Do not restore old business databases over newer live data or resubmit unknown messages. Restart Core and independently verify source identities, health, leases, original-run state and replay fences. If original execution status changes during rollback, keep admission closed pending the actual operator's reconciliation.

## Validation policy

Run focused Codex/QA/recovery/security fixtures, actual synthetic CLI→stdio MCP, contract checks (`npm run api:verify`, `npm run api:verify:integration`) and native sandbox checks with the correct service subgroup. Any unexecuted test must be declared blocked, never reported as a pass. No CI bypass or paid-budget change is authorized by this package.

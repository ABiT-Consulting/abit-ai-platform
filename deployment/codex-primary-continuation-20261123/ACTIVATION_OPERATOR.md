# ABIT-ORCH-05 — Codex-first scoped Core activation controller

**Source-only operational tooling, NOT a production approval and NOT deployed.**

The accepted Core application release candidate is **PR #28, commit `badf6f3290132ab0b65fea941404a4ee5c58ead8`**, with immutable 13-file manifest SHA-256 **`f095e84498e4c888b9c4190520312c3a62a36e6825d4ff175bad4085e8bff047`**. `activation_controller.py` is an **additional, separately reviewable** operator execution package on the dependent branch. Its own exact SHA-256 must be reviewed and bound into Root-protected approval before execution. Nothing in this package grants authority.

## Independent review P1 correction — protected Python source metadata

The initial PR #29 automated review found a genuine P1 in root admission: `root_check()` used the `protected()` helper on its own Python source. That helper validated Root metadata **and parsed the bytes as JSON**, making all mutating actions fail even when their approvals were valid. The corrected implementation separates `protected_metadata(path)` (regular Root-owned 0600 inode with exactly one link, no content decoding) from `protected(path)` (the same metadata gate **plus JSON parsing** for actual approval/journal receipts). `root_check()` now calls `protected_metadata()` only on its own source and independently compares its raw SHA-256 with the separately Root-approved controller hash. Approval data remains parsed and verified by `protected()`. This does not weaken the admin/owner/CI/approval gate.

Added two isolated regression tests: one exercises the real `root_check()` branch with a non-JSON Python source and mocked Root identity, both valid and mismatched controller digest; the other rejects invalid Root file mode. The **14-case** complete controller QA suite must pass before publishing this revision. The earlier initial controller hash/commit is superseded and **not approved for execution**.

## What the controller actually does

| Command | Side effects | Gate |
|---|---|---|
| `preflight` | Read-only: 13 runtime before hashes, 18 unchanged source-lineage files, current drop-in, maintenance epoch and current operator | No approval needed; read maintenance gate under authorized local administrator |
| `stage` | Root-only protected copies of candidate/preimage files, the existing Core unit/drop-ins and **online consistent SQLite backups with `PRAGMA quick_check`**; immutable initial journal | Exact approval + actual owner clearance + sealed maintenance state |
| `apply` | Root-only non-killing graceful stop override; Core-only graceful stop; atomic replacement of exactly 13 files plus reviewed `CODEX_HOME` drop-in; reload and restart **Core only**; hash/health/PID checks | All prior gates plus staged verified backups, actual `mcpdev` authenticated Codex CLI and source preimage reread |
| `rollback` | Core-only graceful stop, restore only the **exact saved preimage** for approved files, remove only newly installed files matching released candidate hashes, restore old drop-in, restart and verify; **no database restore** | Fresh original-operator clearance, maintenance control, known release journal, exact hashes; unknown newer changes hard-stop |
| `finalize` | After full real acceptance and a fresh sealed/drained operator checkpoint, removes only its exact temporary graceful-stop override, reloads systemd and records acceptance | Separate Root-owned **real relay/ERP/QA/successor** acceptance receipt; does **not** reopen admission |

The controller **does not** open the maintenance gate, settle or recreate original invocations, erase jobs, cancel QA, restart the relay/Chrome, alter ERP records, restore older business databases, issue Codex credentials or create a scheduler.

## Production authority boundary

1. Current root owner `chat-authorized-root-orchestration-recovery-20261122T1007` (or a valid receiving operator) must read active work and document an actual acknowledged scoped transfer or execute itself. The retained **TASK-2026-06448** run `341ed593-70fb-59bf-83c2-80cbf6f6cbf6` remains uncertain until authoritative original receipts say otherwise. Its original call must **not** be retried simply because a lease expired.
2. Confirm whether **Core-only** work is drained. The maintenance database's 28 active and 33 unresolved leases include **independent QA and historical owners**; the controller checks actual **Core-cgroup resident** leases rather than blindly demanding an empty global ledger or killing unrelated work. However, the original operator must account for *all* nonterminal leases by immutable SHA-256 and prove Core handlers are drained. No bulk cleanup.
3. Production account `mcpdev` must finish its own supported Codex device login in `/srv/abit-dev-linux/abit-ai/data/agentic-codex-auth`. No copying user auth tokens. Required status: `Logged in using ChatGPT` under actual service UID.
4. GitHub PR #28 has automated Codex review with no findings on the corrected head, but hosted Actions jobs have not executed successfully; organization Actions budget is `$0` with `prevent_further_usage=true`, and exact runner failure causality remains to be established. The approval guard requires **actual required hosted CI pass** and independent code/release review. No zero-budget modification, CI bypass or self-hosted untrusted PR runner authorized.
5. Obtain a distinct package-specific production release approval for the **current manifest SHA** and this reviewed controller SHA. The older three-file Core approval is unrelated. Root-owned approval files must be created by the actual authorized operator/approver through established controls; this repo contains **no such approved receipt**.

### Required protected records (field schema, NOT approvals)

Private directory `/var/lib/abit-ai-codex-p05` must be `root:root` mode `0700`. The independently reviewed `activation_controller.py` must be copied there as `root:root` mode `0600` after its exact reviewed SHA is confirmed. No production mutation may run from a writable checkout. Under that directory the operator/approver must separately create **root-owned 0600 files**:

- `production-approval.json`: `status="APPROVED"`, exact `source_commit`, `manifest_sha256`, `controller_sha256`, `allowed_services=["abit-ai.service"]`, `independent_review_approved=true`, `required_hosted_ci_passed=true`, `production_release_authorized=true`. The operator must independently validate *who* supplied this approval; the controller cannot produce or infer it.
- `operator-execution-clearance.json`: `status="ACKNOWLEDGED_EXECUTION"`, `owner` exact current owner, `manifest_sha256`, `gate_epoch`, `leases_sha256` from fresh controller preflight, booleans `no_original_replay`, `core_only_drain_accepted`, `unaffected_qa_and_historical_leases_preserved`, `current_core_handlers_drained`, `unresolved_originals_reconciled`, `original_06448_replay_forbidden`, `acknowledged_scoped_owner_or_execution` all true **only on real evidence**. The gate must be `sealed` and zero *currently Core-resident* active/unresolved admission holders. Fresh epoch/lease digest changes require new owner clearance. These data fields are not proof of operator acknowledgment without the signed/root-controlled provenance established outside the script.
- `runtime-acceptance.json` only **after** real acceptance: `status="ACCEPTED"`, exact `manifest_sha256`, `operator_owner`, and nonempty verified `independent_qa_receipt`, `real_erp_readback_receipt`, `relay_original_no_replay_receipt`, plus either `automatic_successor_receipt` or a truthful `truthful_idle_receipt`.

Do not create these records speculatively; never claim preparation as their acknowledgment.

## Procedure — executed ONLY by authorized operator after approval

Read-only verification (safe while awaiting approval):

```bash
sudo -n /usr/bin/python3 -I -B /srv/abit-dev-linux/abit-ai-prompt05-activation-controller-20261123/deployment/codex-primary-continuation-20261123/activation_controller.py preflight
```

The approved operator then freezes a reviewed copy, verifies its exact SHA and creates the two Root-controlled authorization/owner records. **Do not run** the next commands without those verified prerequisites. Copying the code into Root ownership does not itself establish the approval.

```bash
sudo -n /usr/bin/python3 -I -S -B /var/lib/abit-ai-codex-p05/activation_controller.py stage
sudo -n /usr/bin/python3 -I -S -B /var/lib/abit-ai-codex-p05/activation_controller.py apply
```

`apply` leaves the maintenance gate closed and a narrow temporary stop override in place until genuine live QA/ERP/relay acceptance. A failure creates a durable **held** journal for the authorized operator to reconcile, not a hidden automatic retry or emergency forced kill. Once a real test admission window and no-duplicate acceptance have passed, the actual operator seals/drains Core again, refreshes the lease clearance and gives the Root-owned acceptance receipt. Only then run `finalize`. If anything fails, use the same scoped owner approval to run `rollback` after inspecting stage journal, installed hashes and current application state:

```bash
sudo -n /usr/bin/python3 -I -S -B /var/lib/abit-ai-codex-p05/activation_controller.py rollback
sudo -n /usr/bin/python3 -I -S -B /var/lib/abit-ai-codex-p05/activation_controller.py finalize
```

`rollback` and `finalize` are alternative terminal paths, **not steps to run together**. The operator independently reopens admission only after verifying the appropriate final receipt. Never restore older SQLite databases over newer live business changes; consistent DB backups are for recovery reference, not automatic rollback. A partially staged or uncertain journal requires operator investigation, never deleting protected data or executing a second apply.

## Test evidence and remaining protected checks

The standalone `test_activation_controller.py` creates a disposable 13-file fake runtime, independent source-lineage fixtures, an isolated SQLite database, a synthetic systemd service, and a fake protected gate. No real service, journal, ERP or GitHub mutation occurs. Tests cover source drift, duplicate activation, wrong approval, stale gate/lease snapshot, preservation of unrelated active leases, unknown post-install edits, original DB continuity, rollback after partial Core stop, inert stop-intent rollback **without interrupting Core**, symlink denial, missing service authentication and acceptance veto.

The previously blocked `test_actual_test_namespace_denies_host_files_network_and_git_mutation` **passed** under a separate bounded `mcpdev` transient systemd service with `Delegate=yes`, `DelegateSubgroup=controller`, `AppArmorProfile=chatgpt`, `NoNewPrivileges=yes`, `ProtectHome=yes`, `ProtectSystem=strict` and resource limits. Its unit finished successfully; this does **not** verify live Codex/ERP delivery.

The official `mcpdev` device login must be completed by the account holder. Source-only tests do not substitute for service-authenticated Codex CLI/MCP, production activation, one real authenticated relay submission with lost-ACK recovery, independent QA, ERP status readback or automatic successor.

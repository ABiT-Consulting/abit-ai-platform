# LinkedIn administrator ownership recovery — production-readiness package

**Status: source implemented and focused-tested; second isolated security review pending; NOT installed; NO live ownership transition performed.**

## 1. Scope and exact release binding

This is a separate ABIT.AI administrator ownership control for the exact expired owner and the seven-file LinkedIn admission correction. It is not an orchestration replacement and it does not change the approved product correction.

- Approved correction commit: `8c1bc2308fdf693659520103559863682333299b`
- Seven-file manifest SHA-256: `955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250`
- Manifest source: `/srv/abit-dev-linux/tmp/linkedin-admission-candidate-20261122/deployment/linkedin-admission-20261122/manifest.json`
- Previous owner: `chat-authorized-root-orchestration-recovery-20261122T1007`
- Receiving operator label: `chat-linkedin-admission-recovery-8c1bc230-20261123`
- Required execution scope: `linkedin-core-seven-file-release:<manifest SHA-256>`

The manifest itself records `deployment_approved: false`; the existing user authorization is a separate control. This recovery-control installation is a new security change and requires its own approval. The existing seven-file product approval remains separate.

### Shared owner-record boundary — LinkedIn-only execution scope

The owner record is shared custody metadata. The fixed `EXPECTED_WORKSTREAMS` inventory includes all four currently declared workstreams, including `proj0202-r4-independent-review-and-new-stage` and `native-erp-readonly-preflight`. A root administrator must positively fence or independently preserve every one of those workstreams and preserve both `REQUIRED_PRESERVED_HOLDS` before the shared owner record can be changed. The recovery operation does not remove, replay, or complete unrelated work.

This implementation is **not** a general PROJ-0202 operator authorization. It is bound to the LinkedIn receiving operator, approved commit `8c1bc2308fdf693659520103559863682333299b`, seven-file manifest `955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250`, and exact scope `linkedin-core-seven-file-release:<manifest SHA-256>`. The shared owner field is updated to that LinkedIn-specific receiver, but the project workstream inventory and unresolved holds remain unchanged.

`validate_recovered_operator` requires the exact LinkedIn scope, manifest, commit, generation and authenticated receiver binding. A caller asking it to validate `proj0202-r4-independent-review-and-new-stage`, `native-erp-readonly-preflight`, or `PROJ-0202` is rejected. Even a successful LinkedIn validation returns `release_activation_binding_required: true`, `publishing_permitted: false`, and `new_agent_execution_started: false`; it does not set `execution_bound` or create a native registration/key. There are currently no runtime consumers of this recovery helper outside its own implementation and tests. Consequently this recovery cannot authorize PROJ-0202 execution. A separate IT custody/FRAPPE activation decision and a separately scoped owner/security approval remain mandatory for PROJ-0202.

## 2. Components

Source changes:

- `backend/open_webui/utils/operator_ownership_recovery.py` — backend helper; must be byte-identical to `deployment/operator_ownership_recovery.py`.
- `backend/open_webui/routers/agentic_governance.py` — authenticated receiver acknowledgment endpoint.
- `deployment/operator_ownership_recovery.py` — standalone protected administrator control.
- `deployment/recover_operator.py` — root-only CLI; validates its own root-owned installation before importing the helper.
- `backend/tests/test_operator_ownership_recovery.py` — isolated adversarial regression suite.
- `INDEPENDENT_SECURITY_REVIEW.md` — findings against the original Prompt 06 commit; preserved, not overwritten.
- `PROMPT07_REVIEW_RESULT.md` — corrected implementation and verification record.

The API receiver route is:

`POST /api/v1/agentic/operator/recover-expired/acknowledge`

It accepts only `expected_record_sha256` and `receiver_token`. The receiver identity comes from `Depends(get_current_user)` and is never accepted as a caller-supplied ID. Because that dependency also accepts API keys, the route additionally requires `request.state.auth_type == "jwt"`; API-key, missing and unclassified authentication modes receive HTTP 403. The API writes a service-owned, HMAC-authenticated receipt; it does not edit the root-owned owner record. A separate root administrator command verifies that receipt and applies the owner-record update.

## 3. Trust boundaries and required filesystem layout

The following are installation prerequisites, not actions performed by this source-only task:

| Path | Owner / mode | Purpose |
|---|---|---|
| `/opt/abit/operator-recovery/20261123/` | `root:root 0700` | Immutable CLI and standalone helper |
| `recover_operator.py` | `root:root 0500` | Root CLI |
| `operator_ownership_recovery.py` in the same directory | `root:root 0500` | CLI helper |
| `/var/lib/abit-ai-operator-recovery/` | `root:root 0700` | Signing key directory |
| `/var/lib/abit-ai-operator-recovery/receiver-ack.key` | `root:mcpdev 0640` | HMAC key shared by ABIT.AI service and root CLI |
| `/srv/abit-dev-linux/abit-ai/data/maintenance-admission/operator-recovery-acks/` | `mcpdev:mcpdev 0700` | Authenticated receiver receipt spool |
| `governance/operator-session-fence-evidence/` | `root:root 0700` | Evidence files named by the fencing receipt |
| `governance/operator-session-fence-receipt.json` | `root:root 0600` | Complete root administrator fencing attestation |
| `governance/operator-session-recovery-audit/` | `root:root 0700` | Snapshot, intent, token, commit and acknowledgment audit |

The existing owner record remains at:

`/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json`

Its existing coordination lock files are used, plus `.operator-session-recovery.lock`. The owner file must remain `jimran@abitcons.com:mcpdev 0640`; its parent must remain owned by the same administrator, group `mcpdev`, mode `2770` (setgid). The helper opens path components without following symlinks, uses no-follow file opens, locks in stable sorted order, compares the exact record hash and inode immediately before replacement, fsyncs files and containing directories, and preserves the original owner/group/mode on owner-record replacement.

### Signing key preparation

Only after installation approval, the host administrator generates a new key locally; never reuse a credential from chat or another environment:

```sh
sudo install -d -o root -g root -m 0700 /var/lib/abit-ai-operator-recovery
sudo sh -c 'umask 077; openssl rand -hex 32 > /var/lib/abit-ai-operator-recovery/receiver-ack.key'
sudo chown root:mcpdev /var/lib/abit-ai-operator-recovery/receiver-ack.key
sudo chmod 0640 /var/lib/abit-ai-operator-recovery/receiver-ack.key
sudo install -d -o mcpdev -g mcpdev -m 0700 /srv/abit-dev-linux/abit-ai/data/maintenance-admission/operator-recovery-acks
```

The key is not returned by the CLI or included in source artifacts. Back it up under the existing host secret-retention policy. The HMAC binds the receiver receipt to the authenticated ABIT.AI user ID, exact owner-record hash, one-use token hash, recovery ID and commit-marker hash.

## 4. Root-owned fencing receipt — schema 2

A lease expiry, an empty job list, or missing Chat metadata is not a fence. Before recovery, the administrator must independently inspect the previous owner session and all four exact workstreams:

1. `original-relay-gateway-recovery`
2. `core-accepted-original-publication`
3. `proj0202-r4-independent-review-and-new-stage`
4. `native-erp-readonly-preflight`

For each workstream, the receipt must explicitly include:

- `disposition`: `fenced` or `preserved`;
- `inventory_complete: true`;
- a real root-owned `0600` evidence file and its exact SHA-256;
- the full process inventory (PID and `/proc/<pid>/stat` start ticks; a preserved process also needs its exact supervisor cgroup);
- `provider_requests`: explicit list of stable request IDs and `fenced`/`preserved` dispositions, each with evidence file/hash;
- `browser_sessions`: explicit list of stable session IDs and `fenced`/`preserved` dispositions, each with evidence file/hash.

An empty provider/browser list is accepted only when the associated evidence file attests that the inventory was completed and none remained. Unknown provider outcomes must be preserved and must never be replayed. A process whose `/proc` entry cannot be read is not considered absent. A reused PID is distinguished by a changed start tick. Preserved work must still be live under its recorded supervisor cgroup; Core-bound processes cannot be marked preserved across the Core restart.

The `owner_session` section must positively verify absence or identify the exact fenced previous-owner processes. The receipt must bind the current owner SHA-256 and previous owner, be issued after lease expiry and no more than five minutes before use, and set `no_replay: true`.

The receipt must explicitly preserve both unresolved originals:

- Maintenance lease: `210450ddbef3399ebc0259a970eb28b3a2aa035a30fa29b2`
- Dot event: `b73e264f-43ba-4596-a6b6-ca97bf8e33a1`

No evidence or process identity is fabricated by this package. The latest observed owner has four active workstreams and an empty in-progress job list; this does not establish process/session absence. The production fencing receipt is currently absent.

## 5. Administrator request and invocation

The request is a root-owned `0600` JSON file. The `receiving_principal_id` must be the actual ABIT.AI user ID of the intended receiver, not the display label. The receiver must sign in as that user to acknowledge.

```json
{
  "expected_owner": "chat-authorized-root-orchestration-recovery-20261122T1007",
  "expected_record_sha256": "606787d50039f25cfa0acb3faf27b182d4499bb603248bede4b7b4de5dde468b",
  "receiving_operator": "chat-linkedin-admission-recovery-8c1bc230-20261123",
  "receiving_principal_id": "<actual registered ABIT.AI receiver user ID>",
  "reason": "Administrator recovery after expiry and positive work-fence verification; previous-owner consent is not asserted.",
  "scope": "linkedin-core-seven-file-release:955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250",
  "execution_expires_at": "<UTC ISO-8601 timestamp no more than one hour ahead>",
  "recovery_id": "<new UUID>"
}
```

The administrator runs exactly once:

```sh
sudo -n /usr/bin/python3 /opt/abit/operator-recovery/20261123/recover_operator.py recover --request /root/linkedin-owner-recovery-request.json
```

The owner record is atomically changed to the receiving operator in `COMMIT_PENDING_AUDIT`. A durable commit marker is then written. The command returns the one-use token file path; the administrator reads and securely forwards that token to the authenticated receiving Chat session. The token itself is not placed in ordinary audit logs or returned by the API.

The receiver submits the token through the authenticated API endpoint. The endpoint writes an HMAC-signed receipt to the service-owned spool and does not edit the owner record. The root administrator then runs:

```sh
sudo -n /usr/bin/python3 /opt/abit/operator-recovery/20261123/recover_operator.py acknowledge --recovery-id <recovery UUID>
```

The CLI verifies the service receipt signature, receiver principal, owner hash, token hash and commit marker, then creates the receiver binding and atomically updates the owner record to `COMMITTED`. The binding is root-owned `0640` and readable by the service group; the detailed audit and token remain root-only `0600`.

### Crash recovery

- Crash before owner replacement: owner remains unchanged; snapshot/intent artifacts remain. Do not retry or delete them. An administrator must reconcile the exact orphaned files and mark the attempt aborted in a separately reviewed procedure.
- Crash after owner replacement but before commit marker: run `reconcile-commit --expected-record-sha256 <exact current owner hash>` under the same root administrator boundary. It verifies the exact intent, snapshot and token before writing the marker. It never renews or reassigns ownership.
- Crash after receiver receipt but before root application: rerun `acknowledge --recovery-id <UUID>`; receipt application is idempotent.
- Crash after receiver binding file but before owner-record acknowledgment update: rerun the same root acknowledgment command; it verifies and reuses the exact binding.
- Never restore the old owner record from the snapshot. That would resurrect stale authority. A post-recovery revocation requires a separately reviewed, fenced administrator revocation operation; this package does not silently roll back ownership.

## 6. Stale-owner invalidation and execution binding

The full owner record changes atomically and the owner label changes. The old owner-record hash and owner identity no longer match. The receiving operator must have an authenticated receiver receipt and the root-owned commit marker. The read-only `validate_recovered_operator()` guard checks the current owner, recovery generation, scope, expiry, commit marker and service-readable receiver binding; the previous owner is rejected.

**Important integration gate:** the existing LinkedIn candidate activation package is currently a read-only preflight; it is not yet an installed mutating release controller and does not yet call this helper. Before any seven-file write, the separately reviewed LinkedIn activation controller must call `validate_recovered_operator()` and then mint its own exact protected activation binding pinned to the new owner-record SHA-256, recovery ID/generation, maintenance epoch and seven-file manifest. The receiver binding created here is not that activation binding. No agent execution is authorized by owner recovery alone.

## 7. Production acceptance after separate installation approval

1. Verify installation directory and file ownership/modes/hashes; verify the HMAC key and spool directory.
2. Verify non-admin CLI invocation, wrong host, wrong sudo principal, symlinked request, wrong request owner/mode and invalid manifest scope are denied.
3. Verify the live owner record hash is still the recorded preimage before any invocation.
4. Verify root-owned schema-2 fencing receipt, every evidence file/hash, all four workstreams, all provider/browser inventories and both unresolved hold IDs.
5. Run the recovery once; verify owner hash, audit intent, previous snapshot, commit marker, token file, mode/ownership and exact recovery generation.
6. Verify only the enrolled authenticated ABIT.AI receiver can create the HMAC receipt; wrong user, wrong token, stale owner hash and forged receipt fail.
7. Verify root acknowledgment updates owner once; replay is idempotent and does not change generation.
8. Verify the old owner fails the execution guard and the new owner still requires the separate LinkedIn activation binding.
9. Verify maintenance gate, all 62 nonterminal lease rows, unknown Dot event, original provider outcomes and unrelated histories are byte-for-byte unchanged.
10. Verify no agent started and no LinkedIn publishing occurred.

### Prompt 09 isolated verification — 23 November 2026

The corrected source was verified without importing the production application or modifying production Python packages. The existing isolated interpreter at `/srv/abit-dev-linux/abit-ai/venv/bin/python` supplied FastAPI 0.136.3, HTTPX 0.28.1 and Pydantic 2.13.4. The existing repository `node_modules` was exposed to this worktree through a temporary symlink for the verification command; the symlink was removed immediately afterward.

- `python3 -m py_compile` for the recovery helper, standalone CLI, backend helper and governance router: passed.
- `python3 -m unittest -v backend.tests.test_operator_ownership_recovery`: 26/26 passed.
- `npm run api:verify`: passed; source guard 607 declared HTTP operations / 4 justified hidden operations; API contract tests 13/13; Developer API/UI backend tests 39/39; Vitest 37/37; `svelte-check` 0 errors and 0 warnings.
- `npm run api:verify:integration`: passed; isolated API integration report 91 passed / 0 failed, 587 registered HTTP operations.
- `git diff --check`: passed.

These results are local source/test evidence only. They do not establish independent security acceptance, exact-commit CI, a remote commit/PR, protected installation, live ownership recovery or production deployment. The corrected implementation still requires a genuinely separate reviewer to issue an explicit ACCEPTED decision before commit/push. The separate production installation approval remains required afterward.

## 8. Rollback boundaries

Before the ownership transition, the new CLI/module/API route can be removed by reverting only the exact installed files through the approved backend rollback procedure; the owner record, maintenance gate, leases and provider state are untouched.

After ownership transition, do not restore the previous owner snapshot. Keep admission held, preserve the recovered record and receipts, and do not create the LinkedIn activation binding. If the recovered operator must be revoked, first fence its current work and obtain a separate reviewed revocation operation. This package deliberately does not offer a command that revives the expired owner.

## 9. Current state

No live installation, HMAC key creation, fencing receipt, owner mutation, maintenance-gate change, service restart, provider reconciliation, activation binding, deployment or publication has been performed. The production administrator must still receive separate explicit approval to install this new ownership/security control. The existing seven-file product approval remains separate and unchanged.

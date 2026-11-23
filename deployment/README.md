# Expired operator recovery — source-only release package

Status: **implemented and isolated-tested; independent security review pending; not installed; no live ownership transition performed**.

## Scope

This is a separate control-plane change for the exact expired owner and approved LinkedIn seven-file release manifest. It is not a replacement orchestration subsystem. It does not mutate the maintenance gate database, admissions, original provider outcomes, run history, agent configuration or scheduling. It does not publish or execute an agent.

The operation uses the existing `operator-session.json` coordination store and its four existing lock files, plus one narrow recovery lock. It compares the complete current record SHA-256, exact owner/state, and expiry under all locks. It preserves every original JSON field, including completed jobs, active/remaining workstreams, admissions references, unknown provider records and history. It changes only owner/phase/window and adds a durable recovery record. The atomic replacement preserves the original owner/group/mode.

The previous operator's old bindings are invalidated because the complete owner-record hash and owner identity change. The LinkedIn activation controller's existing exact `operator_session_sha256` preimage check must reject its old binding. A new receiver must answer the one-use challenge. This package then creates a private receiver-scope binding; **that is not a substitute for the separate exact activation `operator-binding.json` required by the reviewed LinkedIn release controller**. The receiver must still generate that binding through the approved activation procedure, which pins the new owner-record hash and the seven-file manifest.

## Required trusted work-fence receipt

Expiry, an empty `operator_jobs_in_progress`, or missing Chat metadata is not sufficient. Before recovery, a host administrator must produce:

`/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session-fence-receipt.json`

The file must be a regular nonlinked file, root-owned, not group/world writable, and fresh (within five minutes, issued after the expired lease). It must bind the exact owner-record SHA-256 and previous owner, and explicitly include:

- `owner_session`: positive verified absence or fenced original PID/start-tick identities; absence must be attested, never inferred from missing Chat metadata.
- Every entry in `active_workstreams`, each explicitly `fenced` or `preserved`, with evidence SHA-256 and complete PID/start-tick identities.
- Every entry in `operator_jobs_in_progress`, likewise fenced or preserved.
- `preserved_uncertain_work` exactly identifying lease `210450ddbef3399ebc0259a970eb28b3a2aa035a30fa29b2` and Dot event `b73e264f-43ba-4596-a6b6-ca97bf8e33a1`.

A `fenced` process must no longer exist with the recorded PID/start ticks. A `preserved` process must still match those identities and be inside its recorded supervisor cgroup. Work in `/system.slice/abit-ai.service` cannot be marked preserved across this Core restart. The receipt's root ownership is the host-administrator trust boundary; it is not a caller-created acknowledgment. The validator fails closed if any workstream or job is omitted.

At the latest verified read, the owner has four active workstreams:

1. `original-relay-gateway-recovery`
2. `core-accepted-original-publication`
3. `proj0202-r4-independent-review-and-new-stage`
4. `native-erp-readonly-preflight`

The latest record has no `operator_jobs_in_progress`, but that is not used as proof of owner-session absence. The fence receipt must still positively cover the owner session and all four workstreams. No receipt currently exists; therefore the live recovery operation must refuse today.

## Existing administrator authorization boundary

The production CLI requires the existing host administrator boundary:

- Host: `abit-dev-linux`
- Effective UID: `root`
- `SUDO_USER`: `jimran@abitcons.com`

The current Chat MCP identity is not root and has no sudo. This source package does not expand sudoers, change permissions, install a setuid helper or grant a new privilege. The administrator must invoke the separately reviewed root-owned installation after approval.

## Isolated invocation after separate approval and installation

Install only these two source files into a protected root-owned directory, for example `/opt/abit/operator-recovery/20261123/`, preserving hashes and setting the directory `0700`, source/CLI `0500`. The standalone CLI loads its adjacent helper module directly and does not import the Open WebUI package.

The administrator prepares a private request JSON (mode `0600`) with these exact fields; the values below identify the approved scope and receiver, while the reason, receiver principal label, execution expiry and unique UUID must be filled at invocation time:

```json
{
  "expected_owner": "chat-authorized-root-orchestration-recovery-20261122T1007",
  "expected_record_sha256": "606787d50039f25cfa0acb3faf27b182d4499bb603248bede4b7b4de5dde468b",
  "receiving_operator": "chat-linkedin-admission-recovery-8c1bc230-20261123",
  "receiving_principal_id": "chat-linkedin-admission-recovery-8c1bc230-20261123",
  "reason": "Administrator recovery after expiry and positive work-fence verification; previous operator consent was not asserted.",
  "scope": "linkedin-core-seven-file-release:955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250",
  "execution_expires_at": "<UTC ISO-8601 timestamp no more than one hour ahead>",
  "recovery_id": "<new UUID>"
}
```

Run exactly once:

```sh
sudo -n /usr/bin/python3 /opt/abit/operator-recovery/20261123/recover_operator.py recover --request /root/linkedin-owner-recovery-request.json
```

The command returns a one-use receiver acknowledgment token. Deliver it to the named receiver through the intended authenticated Chat session. The receiver returns the token as a challenge response; the administrator then invokes:

```sh
printf '%s\\n' '<receiver-returned-one-use-token>' | sudo -n /usr/bin/python3 /opt/abit/operator-recovery/20261123/recover_operator.py acknowledge --receiver-id chat-linkedin-admission-recovery-8c1bc230-20261123 --expected-record-sha256 '<current-post-recovery-owner-record-sha256>' --token-stdin
```

The receiver challenge is single-use and bound to the exact receiver label, current owner-record hash, recovery ID, manifest and execution window. The resulting receiver-binding is private (`0600`) and proves acknowledgment only. The LinkedIn release controller must still create its own exact protected activation `operator-binding.json` after independently reading the new owner hash and current gate/process evidence. The existing controller preimage guard rejects the previous owner-record hash; no old binding is migrated or reused.

## Failure and rollback semantics

- Concurrent owner renewal, record drift, competing recovery, lock contention, stale fence receipt, missing process identity or missing uncertain-hold preservation all deny before owner replacement.
- The audit intent and byte-exact previous-owner snapshot are fsynced before the atomic owner replacement. If interrupted before replacement, an orphan intent blocks another attempt for the same owner revision until an administrator reconciles it. There is no automatic retry or cleanup.
- If the owner replacement commits, the record contains the new owner, incremented generation, recovery ID and audit hash. The old owner is not restored by rollback: doing so could revive stale bindings. Reversal requires a new administrator recovery generation with fresh fence evidence.
- Gate state, original admissions, unknown provider outcomes, history and agent scheduling are never modified.
- A successful owner recovery does not establish that the LinkedIn agent is operational. Original lease/Dot reconciliation, deployment and controlled nonpublishing execution remain separate acceptance gates.

## Test and review status

The isolated unittest suite covers unauthorized access, expiry-only denial, concurrent renewal/hash drift, workstream/job omissions, owner-session absence, stale/incorrect fence evidence, Core-bound preserved-work rejection, competing recovery, stale-owner binding invalidation, one-use receiver acknowledgment, interrupted transition/orphan intent and preservation of original holds/history. Run with:

```sh
PYTHONPATH=backend python3 -m unittest -v backend.tests.test_operator_ownership_recovery
```

A separate independent security reviewer has not yet reviewed this source. The author-side second-pass review and tests are not represented as independent acceptance. Independent security review and separate installation approval are required. No production installation or live ownership edit is authorized by this source-only task.

## Current host receipt preparation boundary

The fence receipt must be assembled only after the host administrator has independently inspected the actual previous-owner process/session and each of these four workstreams. No PID, start tick, supervisor or evidence hash is supplied by this source package:

- `original-relay-gateway-recovery`
- `core-accepted-original-publication`
- `proj0202-r4-independent-review-and-new-stage`
- `native-erp-readonly-preflight`

For each process, capture `/proc/<pid>/stat` start-time field 22 and `/proc/<pid>/cgroup` at the same observation point. Mark it `fenced` only after that exact PID/start-tick identity is no longer live and its durable work/output is preserved. Mark it `preserved` only if it remains live under a separately supervised cgroup outside `abit-ai.service`; the validator rejects preservation inside the Core service cgroup. The previous Chat owner session must separately be positively verified absent or fenced. Unknown processes or incomplete evidence mean **do not issue the receipt**.

The currently inspected record has four active workstreams and zero listed in-progress jobs. No trusted receipt exists, and this Chat has not established the process/session fence. Consequently the live recovery CLI must currently refuse. This is intentional, not an operational error to bypass.

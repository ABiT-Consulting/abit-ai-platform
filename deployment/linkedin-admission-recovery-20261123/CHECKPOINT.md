# Approved LinkedIn admission recovery — execution checkpoint

## Approval and exact package

The user explicitly approved production deployment of commit
`8c1bc2308fdf693659520103559863682333299b`, its exact seven-file manifest
`955c37f99cc73d392f2248dfe4ea4b2f932d7f3cedd24e36e69571bf43d54250`,
the necessary controlled Core restart, private backups/activation/rollback, and
supported reconciliation of positively identified original work.

That approval remains recorded. No second approval is requested for the same
package. LinkedIn publication, messaging and running the unchanged publishing
prompt as a draft test are excluded.

## Acceptance state

| Stage | Actual evidence |
| --- | --- |
| Source package | Preserved unchanged at the approved commit; Linux export candidates match all seven Git blobs |
| Installed preimages | All five matched; both new modules absent again at 21:44:13 UTC; Core PID 3481989 unchanged |
| Operator coordination | Request published once as Comment `o8q5m2s0gq` on `TASK-2026-06457`; exact readback verified |
| Ownership/handoff | No scoped acknowledgment or handoff observed through 21:36 UTC |
| Private backups | Not performed for this release; no privileged operation started |
| Production deployment/restart | Not performed |
| Original-work diagnostic/reconciliation | Not performed; target maintenance/provider ledgers remain protected from this connector |
| Live authentication/UI | Secure Active Directory authentication succeeded; existing agent, history, project sidebar and retained chat opened |
| History preservation | All 260 earlier rows hash identically; saved configuration digest unchanged at 21:41:07 UTC |
| Scheduler behavior | Still defective live: two new before-execution denials; attempts rose from 258 to 260, history from 260 to 262 |
| Controlled draft execution | Not started; no supported enforced nonpublishing path exists |

All dates above are 22 November 2026 UTC, equivalent to early 23 November in
Asia/Dubai. These are predeployment observations, not acceptance of deployed code.

## Current accountable owner

At 21:41:07 UTC the normal-readable operator record retained:

- Owner: `chat-authorized-root-orchestration-recovery-20261122T1007`.
- State: `ACTIVE_AUTHORIZED_ORCHESTRATION_RECOVERY`.
- Updated: 21:32:16 UTC; lease through 23:32:16 UTC.
- No jobs listed in progress; `released_at` is null.
- SHA-256: `a90259290e3bfa585034e355e625a2b3ad286541317f4b97654909cb9d16363c`.

The prior original-only hold was finalized by that operator, with gate reopening
at epoch 96. Its later checkpoint reports a separate consumed holder aborted before
publication/native dispatch and gate open at epoch 98. This is the operator's
recorded state, not a fresh protected-gate read by this repair.

Those operations neither identify this marketing agent's original admission nor
transfer ownership to this Chat. The current operator continued preparing other
native work and did not acknowledge the LinkedIn request in the inspected task
comments.

## Verified coordination

The existing Operations task is `TASK-2026-06457`, assigned by an open ToDo to
`ai-operations@abitcons.com`. It has been used for scoped shared-operator requests.

The request expressly identifies ChatGPT coordination on the user's instruction.
It does not impersonate Muhammad Imran, modify the task assignment, claim ownership,
or submit an execution job.

- Request SHA-256: `19ac7c461a425d82960b91c04ef03dde28a27cbd5ea437ab3cdf5bf69453f9d1`.
- Comment: `o8q5m2s0gq`.
- Exact content SHA-256: `632dc3417b557e03c921aacbfb385efdcd2e509838f0444b716306e6f8d5630a`.
- Status: `REQUESTED_NOT_ACKNOWLEDGED`.

The task comment is a verified coordination channel, not an automatic execution
queue. No supported generic operator dispatch or acknowledgment API was found.
The next required action is the current operator's execution of the bounded
diagnostic and approved release, or its explicitly recorded scoped handoff.

The exact committed diagnostic is available at:
`/srv/abit-dev-linux/tmp/linkedin-admission-recovery-20261123/observe_original.py`

Its SHA-256 is
`6bb51c3b738ebda7687b8c875f310028168d66eb8e095b8977d65fc6ce3f1ffe`.
A read performed by that existing operator under its hold needs no transfer of
ownership. Our request does not give this connector permission to impersonate that
service identity or take over its active session.

## Service and installed-source readback

At 21:44:13 UTC, all five installed source hashes still matched the approved
preimages and both new modules remained absent. Core PID 3481989 remained active;
/health returned HTTP 200 with status true, and /api/config returned HTTP 200,
ABiT AI version 0.11.4. See [installed hashes and health](evidence/installed-preimages-latest.json).
No approved candidate target was installed.

## New live evidence

Two additional scheduled denial records appeared after the earlier hold reopened:

| Run ID | Result |
| --- | --- |
| `8adbb014-e51f-4fd5-ab9b-b977241bec3e` | Generic maintenance denial before execution; no chat link |
| `281f8a63-88e2-427a-b3a2-99601f163f9d` | Generic maintenance denial before execution; no chat link |

These are not manually initiated test executions. The page shows the latest at
23 November, 01:00:31 Dubai time, still labeled Failed. The approved correction has
not been installed, so no live duplicate-prevention success is claimed.

The prior 260-row history SHA-256 remains:
`0d91fcfcd1f1e11be273f3f85d654b797fc1e11da0ca3234f4b6d0c6e9a788b7`.

The saved configuration SHA-256 remains:
`7216e45cabe06e51e617e47e1a9807690b4b9eaabfb07f1445194f12e0c29663`.

The browser opened the existing marketing conversation
`4f21c199-7193-4970-a4c9-07a7a1b4666d`. It visibly retains both the text-only
provider's inability to publish and the subsequent uncertain Dot outcome.
Neither record establishes the exact protected lease binding.

## Operational preparation

The old release controllers are pinned to different packages, stages, process
identities and metadata. They cannot safely receive this seven-file package by
merely changing a command argument.

A new nonmutating preflight binds the approved payload and current observed
runtime/process/file identities. It exposes no deployment, backup, restart, gate
or acknowledgment override. It deliberately returns
`BLOCKED_OPERATOR_BINDING_REQUIRED` until the current owner provides the genuine
handoff and protected evidence needed to bind a one-use activation.

31 new isolated preflight tests passed. Independent source review found no
material mutation, permission or acknowledgment-bypass flaw. These are preflight
guard tests, not activation or rollback tests. The accepted product tests/build
from the approved commit were reused.

The frontend's UID differs from the Python files; the package records their
individual metadata. QA remains a possible shared-database writer, so the backup
procedure must preserve internally consistent SQLite backups and avoid claiming
cross-database atomicity. See [OPERATOR_HANDOFF.md](OPERATOR_HANDOFF.md).

## Enforced draft-path finding

The current ordinary Run endpoint has no draft request body or execution profile.
It uses the saved publishing prompt and may dispatch to Dot or the relay with
inherited capabilities. A prompt-only instruction or unknown `draft_only` field
would not enforce nonpublication.

An existing text-only Codex component may be reusable, but its effective isolation
and integration with this agent's admission, original identity and durable output
must be verified before treating it as an enforced draft path. Directly calling
another model would not verify recovery of this saved agent.

Per the user's requested order, no separate draft runtime change has been
implemented or released before the approved correction is deployed and checked.
A successful future draft would establish draft-generation/output persistence,
not LinkedIn publishing capability.

## Continuation

1. Obtain the actual current operator acknowledgment/execution. Do not infer
   ownership from idle jobs, gate openness, expired timestamps or our posted request.
2. Run the exact bounded diagnostic through that operator and correlate positive
   original provider evidence; unknown outcomes stay protected.
3. Rebind current lifecycle/backup receipts, verify preimages under the existing
   release lock, and install only the seven approved files through graceful Core
   activation. No automatic gate reopening or marketing execution.
4. Verify installed/served hashes, normal startup, API and authenticated UI,
   unchanged history and blocked scheduling behavior.
5. After supported original reconciliation, implement/test any necessary separate
   enforced draft feature and present its exact additional release scope.

The existing deployment approval is sufficient for its stated seven-file scope.
The outstanding dependency is the accountable operator's execution or scoped
handoff, followed by actual private diagnostic/backup/activation evidence.

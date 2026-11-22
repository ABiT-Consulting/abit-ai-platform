# LinkedIn Product Marketing Agent — tested correction and recovery blockers

Agent: https://abit.ai/automations/d4a6e561-cb2a-41d9-93b5-2888624cf8d6

## Delivery state

| Stage | Verified outcome |
| --- | --- |
| Source | Correction preserved in the instructed Windows checkout and an exact, additive Linux runtime candidate |
| Focused tests | 23 admission-state cases, 16 independent composition cases, 17 activity-presentation unit cases, and 6 structural UI checks passed |
| API gates | `npm run api:verify` passed; `npm run api:verify:integration` passed 84 checks |
| Frontend build | `npm run build` passed, exit 0, completed 23 November 2026 at 00:27:40 Dubai time |
| Build asset | Generated `build/agent-activity.js` exactly matches the tested canonical source and candidate |
| Deployment | Not performed |
| Live recovery | Not performed; a bounded lookup by the existing service operator is still required |
| Live UI and controlled execution | Not verified; browser navigation was unavailable, and no marketing run was started |

No LinkedIn post or message was sent. See [verification evidence](evidence/verification.json), [live observation](evidence/live-observation.json), and [operator procedure](OPERATIONS.md).

## Confirmed implementation defect

The actual Linux runtime's `utils/automations.py:_spawn_claimed_automation` catches
`MaintenanceDenied` before creating a worker, discards the exception reason, and
finishes the newly claimed run as an error with the displayed generic message.

`models/automations.py:claim_due` creates that run and increments `attempts_used`
**before per-agent admission**. It excludes ordinary running rows, but does not
exclude historical active/unresolved maintenance ownership. A rejected claim becomes
an error, so an unchanged original blocker can produce another claim every hour.

This proves the retry and classification defect. It does not independently prove
which guard rejected this particular automation's admission. The exact target
lease, or its absence, has not been observed.

The [review patch](additive.patch) records the changes against the actual installed
source snapshots. [manifest.json](manifest.json) identifies all preimages and candidates.

## Actual history and configuration

Read-only observation at 23 November 2026, 00:23:44 Dubai time:

- `attempts_used = 258`; the configured limit of 0 means unlimited.
- 260 run rows: 26 success, 30 deferred, 204 error.
- 162 error rows contain the exact before-execution maintenance denial.
- The other 42 error rows comprise 17 Codex host-login responses, 9 unloaded
  conversations, 9 prior-relay-active responses, 4 scheduler-stop records,
  1 uncertain relay result, 1 uncertain Dot result, and 1 database/disk-full record.
- Denials span 16 November at 06:00 through 22 November at 23:00, Dubai time.
- Latest denial: `861a054f-2d94-4ba3-b074-9ad596b1bf7e`; its `chat_id` is null.
- The agent remains enabled and hourly, with the same owner, instructions,
  destination, models, history and configuration hash.

The displayed 258 is not a count of failed provider executions. Historical rows
and counters were not rewritten.

### Retained uncertain originals

| Saved run | Related evidence | Observed state |
| --- | --- | --- |
| `592b9869-f60e-456a-9dfa-4d413fe3deb9` | Relay job `aibit_b4456a1b2ded5ebff7ce0671a57c3476b1a8dd00c40a4c4e`; chat `8e892f28-e0f4-4fac-8c96-8741e694cbc8` | Unknown / opening_project |
| `0f536bf0-4d6c-404d-8ece-e7504a1cbd7e` | Dot event `b73e264f-43ba-4596-a6b6-ca97bf8e33a1`; chat `4f21c199-7193-4970-a4c9-07a7a1b4666d` | Unknown; no final response |

These correlations do not establish either as the exact unread maintenance lease.
Legacy leases bind the automation and process identity without an original run ID.
Choosing the newest history row would not establish original ownership.

The last transport-success run, `281167ec-fec8-4b9b-8084-c7186f41a7a5`, on
13 November at 19:00 Dubai time, saved a response saying it could not inspect the
server, LinkedIn or inventory and that no post was published. No saved LinkedIn
post URL was found across the two inspected chats. This is not a claim about
unobserved external publishing.

## Why the model route remains unchanged

`omniroute.abit-chatgpt-relay/project-management` is the shared relay model ID.
The saved folder and owner-scoped binding select the actual ChatGPT project.
Both mappings point to **ABIT - SOCIAL MEDIA MARKETING**, project
`g-p-6a790313597881918f94be0f0bcfd2b0`.

The route is intentional. There is no demonstrated reason to change either model.

## Current ownership and diagnostic limit

The operator record was reread at 23 November 2026, 00:31:51 Dubai time.
The accountable owner remained
`chat-authorized-root-orchestration-recovery-20261122T1007`, with its lease through
02:24:48 Dubai time. It reported the original Core holder ceased while intake
remained closed at epoch 95 and the owned browser/firewall barriers remained held.

Corrected native observation 7908 verified that original 7906's E2BIG rejection
caused no native mutation. The earlier 7907 metadata mismatch was traced to rounded
timestamp fields; definition 950 was restored at revision 47. Original-only release
finalization remained pending. This separate maintenance recovery does not settle
the marketing agent's original work or explain all preceding hourly denials.

Historical global evidence from 16 November records an open, unrestricted gate at
epoch 48 with 26 unresolved leases. Recent zero-count receipts count only the
current Core process, not all historical leases. They cannot prove that no original
admission remains.

The service-owned maintenance directory and `abitdot-primary.sqlite` are not
readable by this connector identity. The ordinary automation database was readable.
No permissions were changed and no alternate privileged route was used.

The precise missing evidence is a bounded lookup for any active/unresolved lease
with `kind=automation` and this agent UUID, establishing its exact row or absence,
then its owner PID/start, epoch, state, parent and immutable original audit binding.
No exported target-specific row was found.

[observe_original.py](observe_original.py) supplies the bounded, read-only operation
for the existing authorized service operator. It does not clear or settle a lease.
[Ownership evidence](evidence/operator-observation.json) records the observed source
paths and hashes without copying protected ledgers.

## Implemented correction

1. Read active/unresolved automation references before claim selection.
2. Exclude those references before the query limit and in the transactional
   compare-and-set update. Unchanged blockers create no new run or attempt.
3. Preserve the final atomic admission guard for scheduler/manual races.
   A lost race becomes `admission_denied`, with a safe typed reason and recovery
   action; only that unstarted new claim is finished.
4. Log a global blocking transition once while observational polling continues.
5. Bind future scheduled admissions to a verified committed run ID in immutable
   audit. Missing, wrong-agent or terminal run IDs cannot acquire a bound lease.
6. Add owner-authorized `GET /api/v1/automations/{id}/admission` with typed status,
   gate/operator, original evidence and recovery action. The endpoint is read-only.
7. Keep governed agents on their existing Project admission view.
8. Show Waiting/Blocked separately from executed failures. Preserve historical raw
   records and use original links only when exact evidence establishes them.

The correction does not expire ownership, replay unknown work, change models or
schedules, reset counters, fabricate receipts or disable admission checks.

## Validation scope

The independent 16-case suite executed actual candidate SQLAlchemy model methods,
real SQLite transactions, the complete maintenance module, and exact extracted
scheduler/API functions. Environment integrations were fixtures.

It covered 24 hourly polls preserving 258 attempts, blocked agents ahead of the
query limit, closed and project-restricted gates, both overlap orderings, a true
two-thread SQLite admission race, one-time positive fixture reconciliation,
continued fencing of unknown results, exact run binding, three proof-read failures,
owner access, and the governed-agent boundary.

Positive receipt/output rows were synthetic fixtures, not live results.
The 17 UI unit cases execute the exact source in Node VM. Six rendering checks are
structural; no DOM or browser verification is claimed.

The package validator matched all five existing live preimages and confirmed the
two new module paths absent without modifying production. Health and
`/api/config` remained HTTP 200; the installed application is version 0.11.4.

The full Windows development build succeeded. Its standalone activity asset matches
SHA-256 `2e8dda70a9e1d033a71e94b21c3d76fd3473cdf960a47cbf31ff872e542cb78e`.
Only this asset is proposed for frontend deployment; the full build includes
unrelated development changes.

## Source and release contents

Authoritative checkout: `C:\Code\open-webui-custom`, branch `abit-projects`.
Actual runtime: Linux release 0.11.4 under `/srv/abit-dev-linux/abit-ai/current`.

The Windows checkout lacks the installed maintenance subsystem. The correction
therefore includes canonical observer/API/UI sources and a versioned, reproducible
runtime overlay composed from exact Linux preimages. Whole-folder deployment from
the Windows checkout would erase valid live customizations.

| Runtime target | Change |
| --- | --- |
| `utils/automation_admission.py` | New read-only observer/filter |
| `models/automation_admission.py` | New typed API contract |
| `models/automations.py` | Preclaim exclusions |
| `utils/automations.py` | Scheduler exclusion and distinct denial |
| `utils/agentic_maintenance.py` | Verified future run binding |
| `routers/automations.py` | Owner status API and compatible manual denial detail |
| `frontend/agent-activity.js` | Admission presentation and original links |

The canonical API client, HTML loader, endpoint integration, focused tests,
candidate payload, exact patch, preimage hashes and rollback sources are included.
Unrelated dirty changes and concurrently committed bulk-delete work are retained.

[OPERATIONS.md](OPERATIONS.md) specifies owner handoff, bounded diagnosis, private
consistent backups, drift rejection, seven-file activation, verification and
code-only rollback. Restoring an old business database is not part of rollback.

## Remaining completion gates

1. The existing owner finishes its maintenance finalization and provides the
   bounded target lease/provider evidence through the supported route.
2. Obtain specific production approval for this seven-file candidate.
3. Back up, apply only allowlisted bytes, gracefully activate through the owner,
   and verify actual API/UI, authentication, existing features and unchanged history.
4. Reconcile the retained original using positive evidence.
5. Complete one controlled draft-only execution through an existing supported path.
   The saved prompt requests publishing; running it unchanged is not a draft test.
   No supported draft-only path was established, and this package does not add one.
   This remains an execution-verification blocker until an enforced draft path is
   available, without silently changing the saved instructions.

# Core-only graceful activation helper

## Scope and source identity

The candidate is `deployment/orchestration-recovery-20261122/release-core-graceful.py`, SHA256 `d98a77389a7cf3300aedd4f336ab5557d3f28e042c469e425e18fb87fb234a8a`. It is source-tested and intentionally has `MANIFEST_SHA = 'UNBOUND'`. The separately reviewing operator must bind the final manifest before any live action. No live service operation was executed by this source author.

The unchanged original helper is retained as `release-core.original.py`, SHA256 `e618469129e424ac14330b5faa9802441aa303d4d02df6b3de7f3c040e7fa111`. Nineteen original function/class definitions are structurally identical, including source, configuration, backup, process, HTTP, file-install and manifest validation; only the original manifest function is renamed `base_manifest`. The old helper and existing package were not modified.

The target remains only `abit-ai.service` on ABIT-DEV-LINUX and the exact three reviewed runtime files. The manifest still binds all 373 source preimages, configuration hashes, 11 consistent database backups, the current Core process and six preserved relay/browser/QA process identities. It additionally pins the unit, both permanent drop-ins, their original effective stop/restart settings, the exact maintenance module, current gate epoch and historical provider cohorts. The independent effective-unit backup receipt is pinned to `0d726255171007401daf8c53651b7b530368111dc0b20ba6946b468648cc4805`.

## Operator lifecycle

The helper requires the existing authorized root route with `SUDO_USER=jimran@abitcons.com`, the exact hostname, and the active operator-session owner/lease. It acquires the existing global `/var/lock/abit-ai-governance-release.lock` before the per-release stage lock. Both must be root-owned 0600 regular single-link files, opened without following a symlink; their contents and metadata are not rewritten.

| Action | Behavior |
|---|---|
| `preflight` | Verifies the bound manifest, backups, source, process identities, initial open epoch, exact historical provider records and current idle observations. It performs no service control. |
| `apply` | Records a new protected lifecycle, backs up unit inputs, closes managed admission with the supported CLI, gracefully stops Core once, verifies actual cessation, replaces only three exact files, starts Core once, verifies health/config, removes only its own temporary drop-in and reopens its exact gate epoch. |
| `finalize` | Reconciles the existing lifecycle and continues only from proven states. It never repeats a command whose invocation may have started. A durable precondition-failed state proves no command was issued and may safely recheck those preconditions. |
| `rollback` | Adds one explicit source-only rollback cycle. It observes any already requested stop instead of repeating it, restores only the old reviewed source, and can restore old-runtime health while admission remains held if provider evidence changed. It never restores a database or clears original work. |

The supported maintenance CLI runs only the exact pinned live `agentic_maintenance.py` as its actual `mcpdev` service owner, with the actual data directory, exact epoch CAS, unique command ID and explicit operator-session actor. This is a fixed service-administration command; no interactive service-user shell or web-user impersonation is used. The helper independently verifies both the immutable control audit and current gate command/epoch after close or open.

A single owned runtime drop-in contains exactly:

```ini
[Service]
TimeoutStopSec=infinity
SendSIGKILL=no
KillMode=mixed
Restart=no
```

Its path is `/run/systemd/system/abit-ai.service.d/zzzz-orchestration-recovery-20261122.conf`. The permanent unit and two existing drop-ins remain unchanged. Effective properties are read back before stopping. This prevents timeout escalation, sends the initial termination signal only to Core's main process and prevents an unsuccessful start from automatically producing repeated processes. After verified health the helper removes only this exact root-owned, hash-matched file, reloads systemd once and verifies the original on-failure/10s restart policy and original stop behavior.

Each close, stop, start, reload and open records a protected intent before possible invocation. The invocation marker is fsynced before launching a command. Unknown outcomes are read back; they are never repeated. The two fixed local override-file operations can resume from exact absence or unchanged owned-file readback without authorizing another external command.

## Required stop and preservation evidence

Stopping is observed for at most 150 seconds per invocation. Timeout leaves the existing graceful drain running under the no-force override; it sends no extra signal. Source replacement requires the original PID/start identity to have ceased, unit inactive/dead, MainPID 0 and the whole Core cgroup empty, including descendants through `cgroup.events populated=0`. The current Core process is in `/system.slice/abit-ai.service/controller`; the preserved relay and four browsers are in the separate relay-watchdog cgroup, and QA has its own unit.

The 19 queued/running relay rows and 17 prepared Dot rows predate both current process cohorts. Their complete rows are hashed internally and only aggregate metadata/digests are recorded. Expected digests are `accbb7ef0d5a1226c6ad58906c4c08f4046ef23c70d7a8985f80a5a4dc49fc1d` and `06f10340c5d440c3758b387a2903fe799cb27516b75df74441ccc7714c45ef04`. Their status is not treated as completed. There must be no current Core lease, no active governed/Codex execution and an idle actual relay. After actual Core cessation these checks run again, including any new lease retained under the stopped original identity, before new code is installed.

The supported maintenance gate does not cover every ordinary interactive relay request, and the application has detached observers. This helper therefore does not claim a globally sealed provider drain. A changed post-stop cohort blocks new source installation. The explicit rollback path can restore old-source availability without changing those records, but leaves admission held until the strict reopen evidence succeeds. Unknown outcomes, other active/unresolved leases, original provider processes and business data remain preserved.

## Verification

The exact final source passed 30/30 normal and 30/30 optimized finite lifecycle tests, source drift 0. Test SHA256 is `814e3b72009695a7c2b3b921e3911c79d2e7be022043eb8e5104bb2d21ae1ac3`. Cases cover unknown and proven-never-invoked control outcomes, exact audit/actor/epoch checks, wrong process identity, global-lock contention, nonempty descendant cgroups, post-stop cohort and dead-owner lease drift, interruption during override removal/reload, one-use stop/start, and source-only rollback preserving unknown evidence. The real filesystem case uses only temporary ordinary-user files.

External process execution, network connections and production SQLite access are denied by the test harness except explicitly mocked commands. No sudo, systemd, provider or production database operation occurred. Both final receipts and logs, plus the earlier 25-pass receipt/log at source `ef635bd1677a182ec877f941eda1603e5fcaf8f0477db88b9cbc0921eecf74b4`, are retained. The unchanged three runtime payloads retain their separate API/source validation; this operator-helper change did not rerun those unchanged suites.

Final operator input metadata was independently captured by the root reviewer at `PACKAGE/graceful-manifest-inputs.json`, SHA256 `88ae39ccfc60ac3d97e29198b08dd9702eaf8215301c600395eb850c76962dc9`. The final release manifest and the actual bound helper require that reviewer's separate pinning and live preflight. A successful source test is not a deployment or live verification.

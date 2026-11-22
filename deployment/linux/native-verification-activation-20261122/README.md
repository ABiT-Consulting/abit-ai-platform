# Native verification — default-off Core installation

This package stages the accepted native verification lane without enabling it.
The exact source package is commit
`6f376d916b88f2e3f4b2eb37ca3d1aae2cf80f7c` on
`feat/native-verification-binding-20261122`, with manifest
`77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6`.
Its environment is the existing Project value **ABIT-DEV-LINUX**. No Project,
original Task, SRS, provider job, retry state or queued proof is changed here.

## Exact installation boundary

| Component | Treatment |
|---|---|
| Core source | Six selected leaves; three exact existing preimages and three absence preconditions |
| Other Core source | All 373 existing Python files and both contract schemas checked; existing metadata retained |
| Process cohort | Exact current Core generation plus seven preserved non-Core generations, including relay, browser, QA and watchdog |
| Databases | Eleven individual online SQLite backups and isolated restore checks; no automatic restore |
| Configuration | Five fixed private configuration/launcher files backed up and hash-bound, including the actual resolver path |
| Unit | Main unit and both current drop-ins backed up, hash-bound and retained |
| Maintenance | Exact fresh close audit/epoch, then current owned lease and execution checks; historical unknown provider rows preserved by full-row digest |
| Restart | One Core stop and one Core start with durable intents; no force kill, provider restart or blind retry |
| Broker | Three root source modules, disabled registration and inactive unit only; no key, socket, task gate, authority, nonce or service start |

The actual Core resolver is
`/srv/abit-dev-linux/abit-ai/config/erp-assignment/resolver.json`.
It is checked privately and must have the native section absent or
`enabled: false`. Its contents are never exported. The running
Core environment must resolve that same path before and after installation.

## Fixed paths and commands

Ordinary input package:

`/srv/abit-dev-linux/abit-ai/deployment/native-verification-20261122T1615`

Fresh protected backup/activation stage:

`/var/lib/abit-ai-native-verification-20261122T1615`

The 17 source-to-target mappings and every hash are in
`operator-input-index.json`. The source launcher has no imported
workspace dependencies; it uses the isolated system Python standard library.

### 1. Fresh backup and draft binding

After the operator has reviewed the source and installed the exact bound
preparation file as root:root mode0400 at the fixed target:

```bash
sudo -n /usr/bin/python3.12 -I -S -B /var/lib/abit-ai-native-verification-20261122T1615-prepare.py
```

The bound file differs from the tested preparation source only in
`PREPARATION_SHA`. It verifies the operator lease, shared Core
release lock, all current source/process/unit/configuration conditions, Core
health and the side-effect-free relay runtime status before allocating a new
stage. It requires adequate disk reserve. A prior or partial stage is never
reused.

The producer backs up these databases with SQLite's online backup API:

```text
webui.db
agentic-governance.sqlite
abitdot-primary.sqlite
aibit-chatgpt-relay.sqlite
abit-slack.sqlite
abit-telegram.sqlite
codex-workspace-runs.sqlite
erp-dot-issues.sqlite
erp-pending-review-qa.sqlite
erp-task-resolution.sqlite
maintenance-admission/gate.sqlite
```

Each backup is integrity-checked and restored into a distinct temporary SQLite
file; schema/count digests must match. The original databases remain read-only.
This does not claim a cross-database atomic snapshot.

Outputs are the protected `backup-receipt.json` and
`release-manifest.draft.json`. The draft deliberately sets
`live_activation_authorized: false` and
`validation.reviewed: false`. Failure retains all completed and
partial evidence and cannot trigger service control.

### 2. Reviewed Core activation

The root operator verifies the new protected backup, current conditions and draft,
records the independent operator review, and produces a final
`release-manifest.json` in the fixed package. It must retain the
six exact pre/postimages, all current source/configuration/process bindings and
the default-off condition. Only the separately reviewed activation authorization
and completed review fields are finalized.

The operator then binds only `MANIFEST_SHA` in a new root-owned
mode0400 copy of `release_core.py`. The commands are:

```bash
sudo -n /usr/bin/python3.12 -I -S -B /var/lib/abit-ai-native-verification-20261122T1615-release.py preflight
sudo -n /usr/bin/python3.12 -I -S -B /var/lib/abit-ai-native-verification-20261122T1615-release.py apply
```

Do not run `apply` again after an unknown outcome. Reconcile its
original durable intent:

```bash
sudo -n /usr/bin/python3.12 -I -S -B /var/lib/abit-ai-native-verification-20261122T1615-release.py finalize
```

The temporary runtime override is restricted to:

```ini
[Service]
TimeoutStopSec=infinity
SendSIGKILL=no
KillMode=mixed
Restart=no
```

The helper closes current admission with the existing exact service-owner CLI,
waits for the original Core process to cease and requires the service inactive,
MainPID zero and cgroup empty. It then replaces only the six source leaves,
starts Core once, verifies the same new process generation and final runtime
identity, checks health/configuration, restores only its own temporary drop-in,
and reopens its exact owned epoch.

A same-generation launcher may be observed before its final exec. That
unverified identity is only waited on for a bounded interval. It is never
accepted as the runtime, and PID/generation replacement is rejected.

### 3. Explicit source-only rollback when needed

```bash
sudo -n /usr/bin/python3.12 -I -S -B /var/lib/abit-ai-native-verification-20261122T1615-release.py rollback
```

Rollback uses the same graceful lifecycle and reconciles existing stop/start
intents. It restores the three old files and removes a new file only when it
matches the exact installed postimage and owned metadata. It never restores a
database or changes a provider record. If current work changed after stop, it
can restore old-runtime availability while keeping admission closed for review.

### 4. Optional disabled root-broker staging

After verified Core installation, the operator may install the reviewed
`stage_broker.py` as another fixed root-owned mode0400 launcher
and run it once. It stages only:

```text
/var/lib/abit/native-verification/service/agentic_native_contract.py  0400 root:root
/var/lib/abit/native-verification/service/agentic_native_broker.py    0400 root:root
/var/lib/abit/native-verification/service/agentic_native_service.py   0400 root:root
/var/lib/abit/native-verification/service/registration.json           0400 root:root, disabled/unbound
/etc/systemd/system/abit-native-verification-broker.service           0644 root:root
```

The create-only intent permits reconciliation of exact owned partial copies.
Unknown existing files or prefixes are not adopted. No daemon reload, enable or
start is issued. The later enabled registration must bind the actual new Core
PID/start/command identity and separate transport credentials. That later
activation, an exact per-task gate and the original human IT approval remain
separate requirements.

## Validation and remaining acceptance

- **56/56 normal and 56/56 optimized finite checks passed**, zero skips/errors or
  source drift. The 30 existing lifecycle methods are retained, with one
  expected-diagnostic update for the deliberate launcher wait.
- Tests include actual temporary files, exclusive-link races, removal of only
  the exact new source, partial broker-copy reconciliation, no-adoption holds,
  and all eleven real synthetic SQLite backup/restore operations.
- Forty-three existing graceful operator functions are AST-identical.
- Thirteen backup functions and the complete original eleven-database
  online-backup/isolated-restore loop are AST-identical.
- The original 51-case attempt with one diagnostic-message fixture mismatch is
  retained. No product source was changed to satisfy that mismatch.
- Host systemd, root file ownership, live data backup, real service activation and
  native worker/provider acceptance are **not** claimed by these isolated tests.

The accepted Core feature payload separately passed45 focused checks,
API verification18+48+37 and integration104/0 with616 HTTP operations.

A successful default-off install is not autonomous development completion. A
fresh authorized native IT verification Task, exact ERP packet/action, human
approval consumption, per-run root gate, broker registration, one-use worker
authority and independent result/QA acceptance still have to pass. The new
native lane does not repair or replay the original PM relay failures06448/06455.

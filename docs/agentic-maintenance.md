# Maintenance admission and release drain

The local maintenance gate stops new Automation Play, scheduled automation claims,
ERP intake, QA tests and governed invocation claims. It does not cancel, replay or
expire an original execution. Publication callbacks from already started governed
runs can settle their leases after admission closes. The independent native ERP
Project/Task control fence still applies to actions and evidence.

Known before-provider rejections settle only through fixed server call sites and
committed exact AutomationRun readback. Arbitrary `error` text is insufficient.
Exact Dot busy/rejection or completed-reply receipts can settle their own original;
accepted-without-reply, prepared-without-proof and unknown outcomes stay fenced.
Terminal legacy ERP publication may recover its persisted finished original while
closed, but that receipt cannot authorize a reset or another provider start.

`DATA_DIR/maintenance-admission/gate.sqlite` is shared by the main and QA services.
Its directory must be a real directory owned by their service user with mode0700;
the database must be a regular file of that user with mode0600. A symlink, wrong
owner/mode, invalid state, database error or unresolved lease prevents admission or
a successful drain. Run the CLI as the same service user, using the exact reviewed
module and the actual service data directory. Do not create a second gate under a
different `DATA_DIR`.

## Explicit local controls

These commands are examples for a reviewed installation; substituting the paths
and operator identity is an operator action. No HTTP control or model tool can
open this gate.

```bash
sudo -n -u mcpdev python3 /REVIEWED/open_webui/utils/agentic_maintenance.py status --data-dir /ACTUAL/DATA_DIR
sudo -n -u mcpdev python3 /REVIEWED/open_webui/utils/agentic_maintenance.py close --data-dir /ACTUAL/DATA_DIR --expected-epoch 0 --command-id release-UNIQUE-close --actor OPERATOR
sudo -n -u mcpdev python3 /REVIEWED/open_webui/utils/agentic_maintenance.py seal --data-dir /ACTUAL/DATA_DIR --expected-epoch 1 --command-id release-UNIQUE-seal --actor OPERATOR
sudo -n -u mcpdev python3 /REVIEWED/open_webui/utils/agentic_maintenance.py open --data-dir /ACTUAL/DATA_DIR --expected-epoch 2 --command-id release-UNIQUE-open --actor OPERATOR
```

Read the current epoch before each change. Each new command needs a unique ID;
repeating the exact same command is idempotent, while reusing its ID for changed
content fails. `close` moves `open` to `draining`. Fresh work cannot enter, but an
already admitted scheduler batch can transfer its claimed jobs to tracked child
leases. A recovery may enter only through an internal factory that reads the
exact original persisted receipt; a caller boolean is insufficient.

`seal` checks zero active **and** unresolved admissions and changes the state in
the same SQLite transaction. Only `sealed` with `drained:true` establishes the gate
drain. An unlocked zero count in `draining` is insufficient: original receipt
recovery could still enter. Sealing also blocks new recovery admission. Leases
from crashes never expire by age, PID death or a timer. Preserve unknown originals
and reconcile their receipts; do not delete their rows to obtain a zero count.

`open` explicitly resumes admission at the exact current epoch. It neither clears
old leases nor replays queued, failed, submitted or uncertain work. It can also
abort a drain while original work continues. The audit table is append-only.

## Release use

Close admission, let known original runs settle, then seal. Independently verify
the real relay/CLI provider queues, native intake/QA publication state and HTTP
requests; the local gate cannot prove a legacy external job has ended. A stale
`running`, delegated, unknown or deferred row is an uncertainty to resolve, not
proof that a provider is active or inactive.

The first deployment needs a bootstrap drain because the old processes do not
contain this gate. The existing live `automations.enable` CAS blocks both generic
scheduler claims and manual Play. It does not alone stop ERP intake or QA. Hold
the existing shared ERP Dot dispatch lock while checking exact provider state,
finish pending original callbacks, and gracefully stop the two owned services
only after positive idle evidence. Abort if the lock or idle proof is unavailable;
never cancel or reset an uncertain original. Create and seal this gate as the
service user while those writers are stopped, before starting the new code.

Take one consistent stopped-writer backup of all associated databases, artifacts,
uploads, configuration and exact overlay targets. Preserve owner/mode, WAL history,
absence markers and SHA256 manifest privately. Copy only the reviewed allowlist
whose original hashes still match. Start with admission sealed; read-only checks
and receipt recovery must not trigger a new provider run. Open only after the
reviewed gateway, native ERP guards and intended release have passed their checks.

Code rollback keeps admission closed and restores the exact prior allowlisted
files. Preserve current business databases and result journals. Restoring an old
database after new business writes would discard receipts and can duplicate work;
it is not an automatic rollback operation. Unrelated services, timers and source
files remain outside the overlay.

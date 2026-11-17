# Isolated Agentic Governance Frappe UAT

This directory contains synthetic test scripts and non-secret receipts for the
13 November 2026 canary. It is not a production deployment. No actual model
provider or real AbitDot model request was used. The controlled Dot adapter
returns schema-validated fixture responses and records the submission boundary.

## Isolation and framework

- Own Docker network `agov-uat-20261113-network`, internal only.
- Own containers `agov-uat-20261113-{app,db,redis,web}`; no existing service mounts,
  database, credentials, network or container was changed.
- Container memory caps total 2,624 MiB. No scheduler or queue worker runs.
- New site `agov-uat.localhost`; Frappe 16.17.2 and ERPNext 16.16.0 from the local
  `frappe/erpnext:v16.16.0` image. Only synthetic company, projects and tasks.
- Host test transport is restricted to the own internal Nginx address. Redirects
  and environment proxies are disabled. No main application/live DB is imported.
- UI uses an own headless Chrome profile; existing Chrome/relay is untouched.
  Outbound browser traffic is blocked by a local dead proxy and CDP origin gate.

The original app installation and migrations passed against the actual framework.
`source-final-manifest.json` identifies the exact frozen ERP source files.
`setup-receipt.json` records framework/image/resource setup. Reports include the
exact core-module hashes where the real core modules are imported.

## Executed scenarios

- `site_uat.py`: actual installation, metadata/migration idempotence, default
  opt-in gates, six distinct bindings per project, SRS, signed callbacks, native
  Task group behavior, task idempotency, transactional outbox and immutable audit.
- `pm_checkbox_uat.py`: exact separate PM checkbox, default OFF/no-copy, no
  migration auto-enrollment, native create/assignment bypass and self-enable denial.
- `native_security_uat.py`: all four shared role users denied native Task/ToDo
  writes with PM permission OFF; forged flags, direct Task edits/assignments,
  cancellation/unassignment, role mismatch, private active-run evidence, exact
  binary hashing, immutable evidence and denied deletion retaining original bytes.
- `journal_guard_uat.py`: native JSON insert through `frappe.client.insert` with
  forged document flags is denied; the test always rolls back and confirms zero
  committed mutations.
- `core_http_uat.py`: actual core ledger/bridge/executor/artifacts/outbox/coordinator
  against real ERP HTTP. Lost acknowledgment after a committed result reconciles
  without replay; duplicate execution/advance/scans add no calls; Developer/QA
  rejection and correction succeed; real private PNG bytes are read via Nginx;
  disabling PM permission while queued returns ERP 409 and sends zero models.
- `core_adoption_it_uat.py`: authorized human Task adoption stays Open until
  explicit Start, then PM reviews before the bound Developer or IT role. Actual
  read-only IT gateway inspects only this owned directory; privileged inspection
  waits for a real synthetic human ERP approval, consumes the exact one-use
  authority, resumes the original run, and receives independent QA. Duplicate
  resume/advance adds no host or model operation.
- `core_project_tree_uat.py`: actual architecture artifacts, PM review, two-task
  dependency-ordered backlog and child/parent acceptance. The first pass exposed
  the native Task five-attachment cap during final parent QA.
  `recover_project_tree_uat.py` published that original saved result after the
  bounded evidence-limit fix, with no resubmission. Both children and parent are
  QA APPROVED across eight distinct original work units; sweeping all original
  results and recovery again adds no tasks or runs.
- `ui_cdp_uat.mjs`: actual Project form, exact PM checkbox, governance menu,
  six-agent dashboard, SRS/results/tasks and controls. It does not click run
  controls. Core is intentionally not attached to this UI; the explicit runtime
  unavailable message is expected. Frappe audio autoplay exceptions are unrelated.

`flow_uat.py` is the earlier native callback fixture. Its old synthetic PM child
setup predates the final stricter role provenance fence and is superseded by the
human-adoption and full-project-tree cases for the final implementation.

## Secret handling and reuse

Do not commit `secrets.json`, `core-private.json`, `ui-private.json`, `.app.env`,
`.mariadb.env`, browser profile, raw databases or source archives. They are local
private canary material. Do not print their contents. Reports and checked scripts
contain no production secrets or business data.

The existing canary may be inspected with read-only Docker commands scoped to
its exact names. Native scripts run only in the own application container using
the actual Frappe environment. Core scripts use the existing host Python venv:

```sh
docker exec --user frappe --workdir /home/frappe/frappe-bench \
  agov-uat-20261113-app /home/frappe/frappe-bench/env/bin/python /tmp/native_security_uat.py
/srv/abit-dev-linux/abit-ai/venv/bin/python core_adoption_it_uat.py
/srv/abit-dev-linux/abit-ai/venv/bin/python ui_launch.py
node ui_cdp_uat.mjs
```

Core execution fixtures are one-use original runs. Do not rerun an old Task to
obtain another response. Create a fresh synthetic fixture or use original-receipt
recovery. Scripts that disable a permission must run sequentially with execution
tests. All reports must be read with their source receipt and limitations.

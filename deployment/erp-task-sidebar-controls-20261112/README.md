# ERP Task sidebar → ABIT.AI agent controls

**Deployment: 2026-11-12**. Target: ERP project **PROJ-0197**, with reusable controls on the Task form.

## Components
- `task_sidebar.js`: a separate enabled Frappe Client Script named **ABIT Task Agent Sidebar Controls**. Coexists with the existing sidebar show/hide script.
- `erp_server_script.py`: an enabled ERPNext API Server Script named **ABIT Task Agent Control API**, method `abit_erp_agent_control`. Checks Task read/write permissions and calls the ABIT.AI backend through the existing server-side Webhook credential (not exposed to the browser).
- `backend/open_webui/routers/erp_task_controls.py`: backend route `POST /api/v1/erp-task-agent-control/{task_id}`. Authenticates a fixed, server-side header and verifies the exact Task-to-agent binding, configured owner and ERP scheduler.
- `backend/open_webui/main.py`: registers the route.

## Behavior
- **Start**: for an eligible unlinked AEO/GEO Task currently Open, requests the existing ERPNext AI-developer ToDo assignment. For an already linked, stopped Task, queues a restart through the existing scheduler.
- **Stop**: requests the managed scheduler to cancel both workers and waits for confirmation. For standard ABIT.AI jobs, cancels WebUI chat tasks and disables rescheduling; external ChatGPT work may remain **Stopping** until confirmed by the relay.
- **Restart**: blocks while the previous execution or external stop is unconfirmed.
- **Links**: Open ABIT.AI agent, ABIT.AI chat, and verified ChatGPT conversation when available. Does not invent missing chat links.
- The AEO/GEO charter task TASK-2026-05935 is currently Working without a linked agent; an unlinked Working Task cannot be started without changing human task state. Other AEO/GEO Open tasks can request Start.

## Verification
- Isolated Python tests: 10 passed; backend Python compile and frontend Node syntax check passed.
- Live service restarted; `/health` and `/api/config` returned HTTP 200.
- Live ERPNext API Server Script status request for TASK-2026-05936 returned HTTP 200, `Not Started`, `can_start=true`; ABIT.AI backend rejected a bad control token with HTTP 401.
- ERPNext Client Script and Server Script persisted with enabled flags and matching source.
- A browser smoke test was attempted via a new, disposable CDP tab; its navigation failed, so the Task sidebar was **not browser-visually verified**.
- Actual production Start/Stop/Restart mutation flows were **not exercised**, to avoid unintentionally changing human Task assignments or interrupting unrelated agents.

## Rollback and preservation
Production backup: `/srv/abit-dev-linux/abit-ai/deployment/erp-task-sidebar-controls-20261112/backup/`; includes the original Linux `main.py`, a consistent SQLite database backup, and WAL/SHM snapshots. The live database and data directory were never overwritten. To disable without changing database records, disable the two new Frappe scripts first; then restore the backed-up Linux `main.py`, remove the new route module and restart the existing `abit-ai.service` in a controlled window.

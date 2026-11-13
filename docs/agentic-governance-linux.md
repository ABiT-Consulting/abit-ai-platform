# ERP-controlled Agentic Governance

ERPNext is the system of record for Project authorization, SRS, Tasks, assignments, human approvals, private evidence and business status. ABIT.AI owns persistent project specialists, immutable execution packets, fenced original runs and authenticated handoffs. A returned model reply is a technical result; independent QA and verified ERP readback are required for business completion.

## Authorization and identity

Every new governed action requires an active Project, `custom_agentic_governance_enabled`, the separate default-OFF `custom_ai_project_manager_task_automation_enabled` checkbox, `ai-projects@abitcons.com` in BOTH Project `_assign` and an open Project ToDo, a current registered project/role binding, and a valid current Task packet. The former pilot-project allowlist does not bypass these checks. ERP-native Task and assignment guards are supplied by the companion AI_ERPNEXT change.

Six deterministic ABIT Automation UUIDs persist per Project: Architect, PM, Developer, independent QA, Operations and IT Manager. Shared ERP AI Users are reused. Existing IDs are reconciled; conflicting ownership or bindings fail. Ordinary automation Play/update/delete and stale scheduled execution cannot invoke these specialists. They have no scheduled `next_run_at`.

## Lifecycle

1. An authorized Project event reconciles its six specialists and asks ERP to create/reuse one architecture anchor per versioned SRS. Human Pause/Stop survives edits and provisioning.
2. Architect receives the SRS, instructions, activity, private attachment manifest, repository, environment and acceptance tests. Its plan must reference real hashed design/mockup bytes. Bounded tasks trace SRS requirements and include observable acceptance, parentage and an acyclic dependency graph.
3. PM reviews the exact published architect predecessor. Backlog creation is journaled by immutable idempotency keys; an unknown creation acknowledgment is reconciled through ERP readback. Only the complete validated backlog is released for dispatch.
4. Dependency-ready work receives a project-scoped original invocation. Only one current or uncertain execution owns a Task. Developer/Operations/IT ready results hand off to a separate QA identity. Passing technical execution alone does not complete a Task.
5. QA verifies the exact implementer/run and acceptance evidence. Failure yields bounded rework; concrete blockers produce deduplicated Developer repair children, maximum five issues and two repair levels. A parent waits for required child QA and then performs its own acceptance work. Action requests use structured IT children and current human approval where required.
6. Normal delivery is event-driven. ONE global 1800-second ABIT recovery loop recovers the ERP outbox, queued events and original receipts. ERP adds no competing governance timer.

## API and controls

The registered FastAPI routes are authoritative and feed the API & Developers catalogue, OpenAPI and Postman exports. Private governance APIs use server-to-server `X-ABIT-ERP-Control`, not browser/API-key authentication. ERP event requests instead require the exact timestamped HMAC event body, five-minute validity and durable event identity. No credential is returned to a browser/model.

| Operation | Contract |
|---|---|
| `GET /api/v1/agentic/health` | Public non-secret API readiness, role names and recovery interval |
| `POST /projects/{project}/provision` | Reconcile six exact project bindings; only supported model is `abitdot` |
| `GET /projects/{project}/agents` | Project-scoped discovery without credentials or prompts |
| `POST /projects/{project}/invoke` | Current versioned packet, immutable request ID and verified finished predecessor for agent handoffs |
| `POST /events` | Signed ERP events durably accepted before asynchronous execution |
| `GET /projects/{project}/status` | ERP-only controls, task controls, bounded run history and append-only audit |
| `POST /projects/{project}/control` | Idempotent Start/Pause/Resume/Stop/Retry; optional Task scope remains confined to that Task |
| `GET /projects/{project}/invocations` | Bounded technical history; no private packet, prompt or lease token |

All paths after health in the table share the `/api/v1/agentic` prefix. ERP UI calls its permission-checked server proxy; secrets remain on the server. Stop while an upstream operation is unconfirmed is shown as requested, never as confirmed cancellation. Paused queued work resumes its unchanged original ID only after revalidation. Retry observes original submitted work and saved results; it cannot blindly resubmit a model or host operation.

## Durable state and evidence

`DATA_DIR/agentic-governance.sqlite` stores project registry, control state, immutable packets, task lease/fence, replay inbox, original results, publication phase, approval waits, action receipts and a hash-chained append-only audit. Do not delete or reset it on deployment. Model submission is fenced inside the shared relay dispatch slot after a fresh Project/Task check. Existing legacy Task ownership is checked under the same Task write lock.

`utils/agentic_contracts/provenance.json` records exact vendored role validator/schema hashes and source commits. Contract validation includes current registered bindings, exact QA implementation identity, requirement coverage and real artifact association. Artifact URIs identify one protected run-scoped local filename. Files are read without symlink traversal, bounded, hashed, uploaded privately and verified by ERP File identity, size and content hash. Active/external SVG content is rejected. Stopped/revoked work preserves its original reply locally before any new ERP artifact/status writes.

A result publication is marked uncertain BEFORE its first callback. Unknown acknowledgment recovers only the original ERP receipt. A restarted relay execution observes the original owner/account/job/root/reply and completes the same original AutomationRun; no replacement prompt is sent. An unknown host action remains fenced. Approval drafts and checkpoints have a separate durable wait/outbox and cannot fabricate completion.

## IT execution boundary

IT never uses a broad owner Dot session. It requires a structured immutable Task `tool_action` and a server-registered trusted adapter/profile on the exact host. Implemented adapters inspect configured resources and quarantine a proven disposable file reversibly. They verify current host/resource facts, disk, active writers, retention, backup/recovery attestation and service impact. Protected logs, security/audit data, databases, uploads and backups are rejected. Production mutation requires current exact-action approval. Approval is atomically consumed by original action/run identity before the first operation; unknown outcomes never authorize another execution.

Profiles are absent by default. Installation, service mutation, production deployment and SAP accounting/stock posting have no generic shell or URL fallback. Unsupported operations fail closed. Adding a trusted adapter requires reviewed server code, current scoped capabilities, explicit environment classification, appropriate approval and acceptance tests.

The Developer/Architect/QA owner relay requires a fresh, read-only, protocol-v1 `/dot/scope` proof before preparing any Run and again inside the actual submission slot. The proof must bind the exact owner/account/job/project/role/repository/environment/packet and permitted scopes to a default-deny scoped tool proxy. It must be current within 60 seconds and enforce every delegated connector/tool call and secret read for that same job. There is no configuration-boolean bypass. Unsupported, expired or expanded scope proof prevents model submission.

The existing owner relay has not supplied this enforcement capability. Prompt `tool_scopes` cannot constrain unrelated broad connected Linux/ERP tools. The verifier implements the negotiation/fail-closed boundary; it does not implement or claim the external tool proxy. Company-wide production rollout and actual governed Dot execution remain blocked until the proxy/isolated scoped identities are implemented and independently verified. The isolated canary uses a controlled Dot adapter and real ERP/IT operations, so it is not evidence of a real model/provider turn.

## Release and validation

The dedicated Agentic Governance CI workflow installs focused dependencies at application pins and runs every `test_agentic*.py` case without live services or providers. Run focused ledger, executor, controls/events, scope transport, planning, lifecycle, gateway and approval tests, then `ABIT_PYTHON=<approved-venv-python> npm run api:verify` and `api:verify:integration`. The API launcher uses this Git worktree's private temporary metadata directory, isolated data/static/environment, no live DB and disabled external providers. Native ERP tests must also run against the actual Frappe/ERPNext versions in isolated namespaced resources; mocks do not reproduce `_assign` serialization, automatic child dependency updates or native permission bypasses.

Source, reviewed/merged PR, deployment, migration and live verified workflow are different release states. The current source is an unmerged release candidate. No full-platform live deployment is claimed. Production ERP currently lacks `abit_ai_assistant.agentic_control`; the authorized bench/SSH route must be restored before installation/migration. GitHub Project 53 access was restored through the authenticated browser; implementation/PR items remain In Progress and scoped-relay blocker #19 is a pending child of #10. The CLI token still lacks Projects scope.

Before a production release: obtain required approval, verify exact live service/source hashes and active jobs, take consistent DB/files/config/ledger backups, stage only reviewed new modules and surgical guards, drain active jobs without cancelling them, preserve all live-only router/model/main customizations, retain rollback, and verify live health/config/authentication, sidebar/projects/chats/models/providers, Task controls and a real approved project through Developer and independent QA. Never overlay the entire source backend over the current Linux runtime. Preserve unrelated legacy timers; exclude governed Tasks from legacy Developer and QA execution paths before enabling the new flow.

## Verified canary evidence (13 November 2026)

See `docs/evidence/2026-11-13-frappe-canary/FINAL-UAT-SUMMARY.json` and its hash manifest for exact per-phase source receipts and the reproducible, synthetic scripts. Frappe 16.17.2 / ERPNext 16.16.0 exercised real HTTP callbacks, native permissions and private file bytes. The final architecture/PM/two-child DAG and parent acceptance reached three QA APPROVED Tasks across eight original work units. Recovery of a saved final QA reply and repeated sweeps of every published result added zero runs or Tasks. Human Task adoption, QA rejection/correction, actual read-only IT inspection and real synthetic human approval were also exercised. Native checks verified early File guards preserve original bytes when deletion is denied; browser checks verified the six-agent controls and inline runtime-unavailable state.

These are nonproduction canary results with a controlled model adapter and synthetic registry/Automation storage. The durable ledger, ERP bridge/HTTP, contracts, evidence, coordinator and read-only IT gateway are real. No paid or real Dot model/provider request occurred. UI runtime control was deliberately unattached, and its unavailable state is tested; a live operational control-plane release is not claimed. Historical failed/superseded fixtures are identified in the evidence README instead of being counted as current passing proof.

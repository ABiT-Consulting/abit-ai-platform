# Typed specialist acceptance and reviewable IT plans

Operations and infrastructure completion use named server connectors, immutable execution receipts and independent QA readback. Developer completion continues to require its original commit/draft-PR/test delivery; missing delivery data never selects a specialist path.

## Human ERP controls

Project → AI Governance → Governance dashboard → approval record displays the full immutable JSON before decision. Physical approvals also show **Prepared IT target and safeguards**, containing the actual bounded plan, original target, backup/restore/impact/capacity/signature data and binding hashes. The server derives this view from the original binding record and compares it again before consumption. Edited dialog fields are never decision arguments. No model-provided plan view grants execution authority.

Historical digest-only physical bindings retain their original read/reconciliation semantics. The UI withholds Approve and server consumption/execution rejects missing original bytes. The existing direct decide API may still record an Approved decision; that decision cannot authorize execution. No automatic backfill or replan fabricates historical bytes.

Project → AI Governance → Register controls audit lets an authorized human register only `erp_project_controls_audit_v1` for exact Operations requirements from the current SRS. Native registration requires Project write permission/CAS and rejects AI/service actors. Its immutable record pins the human, SRS SHA and requirement IDs. Changing those IDs requires a new SRS revision. Registration does not start a Task and is intentionally absent from machine-channel allowlists.

The dedicated `pm_review.write` gateway operation writes only that coordination invocation’s bounded evidence file using existing `read_project`. It verifies the active local lease, actual registered review head/turn and original verified QA, then reads back the exact authenticated native `pm.review.started` event/receipt and fresh packet. A packet flag, caller-selected role/Task, missing journal or saved original result cannot grant a write. Normal `artifact.write`, repository and Task/plan operations retain their original capabilities; special review packets receive no `plan_dependencies` or `propose_tasks` grant. Publication continues through the existing protected PM evidence RPC and preserves feature Task status/QA ownership.

## Concrete Operations connector

`operations.audit_controls` accepts `{}` and invokes the fixed native connector. It reads only current Project status/flags, repository/environment, SRS and six verified agent identities. It writes an immutable governance receipt; it does not edit business documents, communicate externally, post transactions or execute host work. The current managed Operations run, role/binding, assignment, Task revision, capability, SRS requirements and gates must match.

The request key derives from the original invocation/gateway call. An unknown POST acknowledgement reconciles only the original GET receipt. Readiness requires the actual receipt, actual local artifact bytes and current native readback. QA uses `operations.verify_original` to derive the authoritative Operations predecessor, read its original published private File, repeat current scoped native reads and create a separate own artifact. Every pass test must cite that independent evidence, and current readback is checked again before publication.

Other customer, SAP, accounting and messaging workflows require their own reviewed typed connectors. They remain unavailable through this adapter; a generic Operations completion claim is insufficient.

## Independent IT QA

`infrastructure.verify_original` selects only an actual IT predecessor and its immutable operation. Existing physical changes use the authenticated broker original receipt and independent current postcondition checks. Routine inspection uses exact completed action/native ready/private File proof plus a fresh read of only the original fixed roots/services. Exact filesystem device/owner/total geometry and configured minimum capacity are checked; volatile free-byte samples are sampled again rather than compared for equality.

Inspection needs the server profile `routine_qa_policy.inspect` with `mode=inspection_v1`, an exact root→minimum-byte map and explicit boolean `require_healthy_services`. Cleanup QA needs `routine_qa_policy.cleanup_disposable=retained_inode_v1`, exact protected inventory and current minimum capacity. Neither reader accepts model-selected paths, hosts, URLs, services or thresholds.

Quarantine QA performs no rename, deletion, restoration or cleanup. It verifies the server-derived private manifest, original action/invocation/consumed approval, exact retained inode/size/mtime, source absence, protected classification and current bytes/health. Legacy cleanup receipts have no before-content SHA, so QA explicitly proves current retained bytes/identity rather than historical content equality. New or changed evidence cannot replace the original journals.

The existing cleanup writer-inventory check still fails closed when Core cannot inspect every relevant descriptor. No new Core privilege or production resource profile is granted. A separately reviewed trusted writer reader/resource profile is required before that mutation can be enabled; a controlled test callback is not live proof.

## Fixed interfaces and verification

The machine channel adds only POST `agentic_operations.record_controls_audit`; GET `agentic_operations.get_controls_audit_receipt`, `get_controls_audit_current`, `get_controls_audit_for_qa`; and GET `agentic_it_plan.get_it_routine_implementation_receipt`, all under `abit_ai_assistant`. Both channel sides independently enforce exact method/path allowlists and reserved-dispatch rejection. Human connector configuration remains a normal authenticated ERP operation.

Repository tests cover real temporary files/quarantine, SQLite handoffs/immutable journals, lost acknowledgement reconciliation, changed capacity/context/receipts, protected logs and symlinks, original native File/approval provenance, actual registry/catalogue/readiness hooks and the real approval UI executed in a controlled Node VM. Native/HTTP/provider peers in unit tests are controlled. Separate runtime acceptance is required before claiming live provider/native completion. This source change does not enable profiles, dispatch work, alter existing histories or restart services.

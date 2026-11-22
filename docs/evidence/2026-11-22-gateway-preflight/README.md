# Gateway preflight diagnostics and accepted-original Retry

This is a **three-file Core source package**, composed on the installed
21 November runtime. It is tested and independently reviewed; this source
commit does not deploy it or establish recovery of an original task.

## Runtime scope

| File under open_webui | Current preimage SHA256 | Candidate SHA256 |
| --- | --- | --- |
| utils/agentic_transport_retry.py | 8ac1970ddd09e60e42fb93620ade69a67cb4ca1bb8076d951cb0ea01366fdf9f | bf813b34e602017086db6859574e60d7c3e094c6ffb83442a42e75ab78dcbfc3 |
| utils/agentic_relay_scope.py | c3cdd4d3dbb5143ec594c7f9422aae725b1ae192287ecfb7e269fe422d4ff6ea | 8b97ec02bd14262f8a4704cfc57c3483a4d756bd9448392c013d47bad2428d21 |
| routers/agentic_governance.py | 85dbbe0a6d5ead08796b830eba212541016f1f790630ff6ddbcf8829bf6edefc | 460edce0e13c756bb86d9668e86becee8c7b2889c3dcb98ad406d2732d6355f6 |

The transport candidate is byte-identical to the reviewed current-source PR25
candidate, preserving the installed pre-admission additions. Accepted provider
receipt, response or scope evidence returns to the existing same-original
recovery path after the unchanged identity checks. It does not create a
replacement generation.

The proof endpoint now converts known source-level authority failures into
fixed diagnostic codes. The client reads at most 4,097 error bytes, parses only
allowlisted values, and adds fixed stage, relay HTTP status, Core HTTP status
and error code to its existing CapabilityUnavailable reason. Unknown response
or exception contents are discarded. Successful proof bytes, authentication,
HTTP denial statuses, scope checks, admission and provider dispatch are unchanged.

Examples include OWNER_PROFILE_UNAVAILABLE, MCP_CONNECTION_UNVERIFIED,
DOT_BINDING_MISMATCH, WORK_PACKET_CHANGED and NATIVE_BOUNDARY_UNVERIFIED.
Diagnostics carry no task contents, URLs, selectors, response text or credentials.
The unchanged executor and preclaim settlement atomically persist that bounded
reason. The lifecycle retry error_code remains null for a blocked attempt;
the diagnostic suffix grants no retry, fallback or queued-state authority.

## Baseline and review

The Git branch starts at b25b1b0790f73b2071e0f20a4038b7bb90478ed0, which predates
some currently installed source. The canonical backend files therefore include
those already-installed additions. Review and deployment must use the exact
current-source delta in deployment/gateway-preflight-diagnostics-20261122/forward.patch
and its three preimages, rather than treating the entire branch diff as a runtime
overlay. The package contains no relay update, new module, configuration change,
schema migration or old 19-file Codex overlay.

The existing release helpers pin their historical process identities and
preimages. They must not be rerun or retargeted for this package. The coordinating
operator owns a fresh, separately reviewed Core release using current source,
process and backup evidence. Source rollback must preserve business data and
all original execution and observation evidence.

## Verification

The isolated harness captures 373 installed Python sources and the existing
API tooling, then overlays only the three candidate files. Its original and final
source manifests are retained in this evidence folder.

- 53 distinct focused test methods passed: 14 new diagnostics, 23 existing
  relay-scope tests, 10 accepted-original Retry tests and six negative transport
  Retry tests.
- The first combined affected run retained four historical-fixture failures.
  The already-reviewed PR25 fixture compatibility patch adds the actual pinned
  reconciliation wakeup helper and synthetic asynchronous app.state. All 42
  original assertion AST nodes remain identical. Only those four failures and
  the six negative transport tests were run afterward; the initial evidence
  remains unchanged.
- API verification passed: 635 declared HTTP operations with the unchanged
  four documented hidden-operation exceptions; 18 contract tests, 48 backend/
  developer tests and 37 frontend tests. Svelte reported zero errors and warnings.
- Isolated full-application API integration passed 104 checks with zero failures
  and 615 registered operations. Both API gates report zero captured-source drift.
- The new cases exercise the actual Core authority/controller and the actual
  executor/preclaim SQLite persistence path. They cover fixed reason propagation,
  missing profile, changed business packet, blocked-state denial, malformed and
  oversized responses, secret redaction, cancellation and unchanged valid proofs.

ERP and provider transport are controlled fixtures in the focused tests. The
relay failure envelope is modeled from the inspected installed relay transport;
no real model or browser action occurs. The full-application API gate uses its
standard isolated state and disabled external services. These results establish
source behaviour and compatibility, not live original recovery or QA acceptance.

## Evidence and execution recipes

- Package manifest and forward/rollback patches: deployment/gateway-preflight-diagnostics-20261122/.
- New regression: backend/tests/test_agentic_gateway_preflight_diagnostics.py.
- Focused receipts: focused-initial, focused-affected and focused-pr25-current.
- API receipts: api-verify and api-verify-integration.
- Prior fixture proof and exact reuse: pr25-fixture-compatibility/.
- Independent review and final correlation: accompanying JSON/Markdown records.
- Preparation and bounded runners: deployment/prepare_gateway_diagnostic_api.py,
  deployment/run_gateway_diagnostic_focused.py and deployment/run_gateway_diagnostic_api.py.

The source commit is local only. No public repository push, live mutation,
provider send, original replay, fabricated receipt or ledger edit is performed
by this source work.

# Governed relay and existing MCP connection

These are source stages for the existing abitdot browser relay and the existing
ABIT-DEV-LINUX OAuth MCP connection. They do not create an account, change the
owner's plugin settings, substitute a different execution provider, or install
anything automatically.

The boundary is `governed_action_gateway` over
`gateway_operations_and_managed_erp`. The owner's normal account plugins and
existing root-administrator privileges are explicitly outside this attestation.
The server still checks current native ERP authority and the original execution
lease for each new governed operation. A tool description or role prompt does
not grant an operation.

## Transport

- `relay.cjs`: `/dot/scope` forwards the exact scope to the fixed local Core
  `/api/v1/agentic/gateway/proof`. Intake validates the complete proof. Composer
  preparation derives a scoped job-capability reference without admitting it.
  Immediately before any browser Send, the relay **awaits** Core `/gateway/admit`
  and attaches the exact returned receipt to the original response. Failed or
  expired admission prevents Send. No admission proof is renewed silently.
- `/dot/observe` uses Core `/gateway/receipt`, which reads the exact original
  admission after expiry or Stop. It reconstructs the identical submitted prompt
  and observes the original tab; it never sends another message.
- `edge.mjs`: the existing OAuth bearer middleware runs first. The edge alone
  hashes its verified `clientId` and token `family`, hashes supported
  `_meta["openai/session"]`, and signs the exact Core `/gateway/call` envelope.
  A model supplies only the opaque admitted `session_id`, stable `call_id`,
  operation and structured arguments. It cannot select owner, role, repository,
  environment, credentials or Core URL.
  First use pins the authenticated client's session metadata. This proves
  possession of the admitted job capability on that connection, rather than
  independent attestation of a browser conversation ID.
- The new `agentic_governance_action` backend tool is a denying stub. Only the
  authenticated edge intercepts it. Ordinary tools keep their existing route.
  Missing supported session metadata denies new job actions. An authenticated
  `transport.describe` with `session_id="diagnostic-bootstrap"`, a stable call ID
  and `arguments={}` returns hashes/protocol availability only; it cannot admit
  a job or call Core.
- The authenticated edge augments `tools/list` with the exact governed tool
  descriptor on the terminal page. Existing ordinary descriptors and pagination
  remain intact. Auth-only activation leaves the backend process and its active
  jobs running; the backend patch remains staged for a later natural restart.
- The wire supports JSON with safe integral numbers. Fractional/unsafe numbers
  are rejected instead of being signed differently by JavaScript and Python.
  Calls have a 60-second transport bound; proof/admit have a 30-second bound.

Core errors expose only a bounded JSON string detail from the fixed trusted
endpoint. HTML, tracebacks, credential-like values and known transport secrets
are suppressed. An unconfirmed action keeps its original identity; transport
errors never authorize a replacement write.

## Credentials

Core control authentication uses the existing fixed server file. The new MAC
key uses `/var/lib/abit-ai-agentic-gateway/edge-token`. The fixed GitHub credential
uses `/var/lib/abit-ai-agentic-gateway/github-token`. Neither
key is placed in work packets, model prompts, diagnostics or API responses. The
only prompt reference is a narrow admitted job capability; active native
authority and authenticated MCP transport are also required to use it.

Deployment must preserve the verified service identities and protected new-file
permissions. This does not claim secrecy from the owner's existing authorized
root-administrator tools, which remain outside the boundary. No global account,
plugin or sudo policy is changed here.

## Reviewed application points

`relay-integration.patch`, `gateway.js.patch` and `workspace.js.patch` apply only
to the exact installed baselines in `stage-manifest.json`. The staged relay
retains its installed Dot observation/feed modules and other customizations.
No browser profiles, state, credentials or logs are in the stage. Reject a
baseline mismatch instead of overwriting newer customization.

The associated server-owned delivery module uses fixed GitHub REST operations,
immutable per-invocation journals, actual private command logs, exact file/Git
hashes and draft PRs. Unknown write acknowledgments permit only original-object
GET reconciliation. QA reads the actual PR head independently and tests source
bytes matching that head. Repository preparation downloads only approved source
and test paths, excludes credential filenames, and atomically installs a private
working copy without replacing an unknown directory.

## Verification and current limits

Run `node --test scripts/test_agentic_gateway_transports.cjs`. The 24 focused
cases use actual loopback HTTP, the installed MCP SDK bearer middleware and an
independent Python HMAC check. Python tests cover proof/receipt propagation,
saved-original recovery, delivery, independent QA and workspace provenance.

These tests use controlled role and GitHub protocol fixtures. They do not prove
live provider execution, live GitHub entitlement, actual connected-client
metadata availability, production ERP permissions, or live activation. A real
authorized nonproduction end-to-end run remains necessary after the reviewed
release procedure and native source/permission checks.

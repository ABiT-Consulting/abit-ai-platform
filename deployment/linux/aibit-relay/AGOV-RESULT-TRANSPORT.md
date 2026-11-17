# Governed Dot result transport

## Scope and provenance

The shared [aibit_dot_feed.cjs](../../windows/aibit-relay/aibit_dot_feed.cjs)
under `deployment/windows/aibit-relay/` is the existing source-owned DOM reader
used by the Node relay's
existing completion loop. It recognizes a reply; it does not send a message,
create a browser session, navigate, admit a job, publish a result, or change ERP.
This hardening is source-only until a separately approved, verified release.
The historical source directory does not select a Windows deployment: the runtime
target remains the Linux service file below, with one shared module.

The original runtime module was copied unchanged as the implementation baseline
from `/srv/abit-dev-linux/relay-runtime/service/aibit_dot_feed.cjs`:

- Baseline SHA-256: `e56ea1566e6fefd184cd3ff864fd622b42f17b235d6687bfbce29861d03eea58`.
- Its positive governed-result fixture failed before editing: `complete=false`.
- The independently staged recovery candidate was reviewed at SHA-256
  `1ce594960acc19485d54db9d2be21e037992f7d55adacceffd1c8aab7fdf350e`.
  Its proposal files and protected evidence were preserved. Commit `f34bb5b`
  introduced that candidate at the existing shared source path. This hardening
  retains its AGOV/plain-text approach with the binding checks below; the temporary
  duplicate Linux source and test were removed after source backups.

The baseline recognized only `ERP RESULT` and `ERP QA RESULT`. A correct
`AGOV RESULT` line could therefore remain unrecognized by the streaming relay.

## Transport contract

The expected message comes from the authenticated original request. The reader
parses the server's outer JSON envelope, obtaining the top-level run and
idempotency key rather than searching arbitrary task strings for the first match.
For a governed request, a unique self-message must contain the same complete
public envelope. JSON formatting and whitespace after the fixed envelope heading
are tolerated. Different context, a quoted digest alone, a missing original, or
multiple matching originals cannot establish completion.

A virtualized original can reuse a root ID only after this session has verified
that exact expected message. A marker-only cache from another request is not
sufficient, and conflicting visible envelope content cannot use the cache. Once
verified, the original root ID is immutable for that expected message. If it
disappears, a later row containing the same envelope cannot replace it.

The fixed server preamble supplies the invocation ID. The final server-appended
WorkPacket supplies its project, task, revision, digest and actor binding; those
identities must agree with available envelope metadata. Handoff context can be
prose or JSON. Its quoted headings cannot replace the final packet. Only the first
complete JSON object at the actual packet boundary is consumed, so subsequent
Architect/PM guidance and appended JSON guidance remain outside the packet.
These boundaries follow `DotAdapter.submit`, `dispatch_task_dot` and
`verified_dot_payload`; changing their serialization requires a compatibility test.

Only assistant rows after that original and before the next user row are eligible.
An empty later user row still ends the scope. Exactly one column-zero `AGOV RESULT:` line is
required across that segment. Governed line splitting and payload whitespace
selection follow Python's `splitlines` and `strip`; an indented first line is not
promoted by whole-response trimming. Malformed, truncated, duplicate and ambiguous
cross-row results remain incomplete. The JSON payload must be a version-1 object
no larger than 200,000 UTF-8 bytes, matching Core's `decode_result` bound. Its
invocation, project, task, revision, digest and actor must match the original;
an explicit optional top-level run ID must also match.

The original response text, including its surrounding whitespace, is returned
without Markdown escaping or whole-response trimming. Core remains
solely responsible for full result schemas, evidence, plan metadata, verdicts,
approvals, acceptance and publication. In particular, transport does not interpret
`architect_plan.metadata.run_id`, source URLs or outcome semantics. A bound result
can be transported and still be rejected by Core.

Legacy ERP/QA terminal recognition and Markdown conversion are retained. Governed
requests cannot complete through a legacy marker. The main response loop is
unchanged: it retains the existing 1,800 ms stable-text wait and lifecycle guard.
The parser adds no polling, retry, timeout, recovery or model-dispatch behavior.

The reader ceiling is 320,000 JavaScript UTF-16 units: the backend's existing
160,000-code-point Python limit can require two units per character for legacy
originals. The gateway's separate governed intake limit remains 160,000 units
including its tool-context suffix. Neither intake limit changes. JSON object
scans are bounded by the reader ceiling without an arbitrary allowance for
surrounding DOM controls.

Compared with the inspected staged candidate, this source strengthens full
original-envelope/cache binding, final packet-boundary selection, byte-based
bounds and ambiguity handling across rows, and leaves outcome validation to Core.

## Offline verification

Run from the repository root:

```sh
node --test deployment/windows/aibit-relay/tests/aibit_dot_feed*.test.cjs
npm run api:verify
```

The Node suite uses synthetic DOM objects and a serialized Node VM; it does not
connect to a browser. It covers all six governed roles, PM review, malformed and
oversized contracts, wrong identities, forged context labels, later users,
ambiguous results, streaming updates of one row, cache scope, appended Architect
JSON guidance and legacy ERP/QA compatibility. An explicit regression verifies
that nested plan metadata errors remain Core's decision. The original eight tests
retain their role, identity, JSON, later-user and legacy assertions. Their minimal
fixtures now use the real server boundaries; cache setup establishes a verified
original, and outcome rejection was moved to an explicit Core-only assertion.
Additional cases cover original-root disappearance, raw response preservation,
Python line boundaries, Unicode message limits and the unchanged admitted suffix.

A separate read-only fixture check used the protected saved original AGOV reply
and the exact generated original envelope with its admitted tool-context suffix.
The baseline remained incomplete; this source recognized the original pair and
returned the reply unchanged. No browser, sending, admission or runtime API calls
were made. The protected fixture SHA-256 was
`02a355e386928b21f8f44c74846ffc55ccfedc2d3940cd479220d4cccfb9605c`;
the independently retained formal-result SHA-256 was
`e2b2422fcf59dc5c36f4646dec70d5d7472628da4bf16b053dc0e73307406fd3`.
Private fixture content and capabilities are not included in this repository.

This fixture check is not full rendered-DOM verification. The saved observer's
matching user-row projection had empty prefix/tail strings, and its body tail did
not retain the full original message. A future authorized live verification must
confirm that the rendered original envelope is recoverable without relaxing its
binding. The saved formal result's business validity is a separate Core check.

## Release and original-run preservation

Use the established release approval, backup and admission/drain procedure. The
presence of this source file does not change the runtime module or an already
loaded Node module. Coordinate the current gate and active-run owner before any
activation; do not hot-reload the parser, bypass a stop gate or interrupt an active
original to force recognition.

Preserve the exact original invocation/run, envelope, message IDs, gateway session,
receipts and unpublished response. Any supported reconciliation must operate on
that original evidence and retain Core validation. Do not resend the request,
create a Retry/new logical run, clear an uncertain call ledger, rewrite a result,
or synthesize publication. A successful offline fixture check is neither a
production release nor proof of semantic acceptance.

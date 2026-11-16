# Preserve the existing compact Dot transport binding

The short Task-link prompt keeps the existing Node reader's fixed preamble and
a newline between the immutable reference and the final verified ERP context.
The reader requires both boundaries before it accepts a reply as belonging to
the original run. No packet data is added back to the prompt.

This package changes only `utils/agentic_executor.py` from the actual compact
relay live baseline `053abd04a51a7a0b16cab78bc0455699886a2664eec438064052769e2ff463fd`
to `454d43e886d433b157e37a9fb533e466d843147362e34b717125aeac23da6bf8`.
Canonical Source retains its existing planning guidance; this runtime leaf
preserves actual live guidance. The added fixed preamble and reference newline
do not change ownership, role authority, scope, approvals, result contracts,
server-held context, run identity or admission behavior.

The cross-component regression passes the actual Python-produced compact relay
message directly to the actual Node `dotFeedSnapshotDOM`, with controlled DOM
rows and an exact synthetic result. It accepts the bound reply and rejects a
changed fixed preamble. A 104,594-character packet and 30,016-character original
handoff remain available through `context.read`; the actual relay message is
5,789 characters. The complete frozen suite passes 12 tests, including existing
run deduplication, owner/account selection and role identities.

```sh
/srv/abit-dev-linux/abit-ai/venv/bin/python -I -B docs/evidence/2026-11-16-task-link-prefix-compatibility/verify_frozen_transport.py
```

`activate.py` derives from the reviewed compact Core controller, pins its current
72-file cohort and service births, and permits only this executor leaf. It
preserves runtime configuration exactly. The existing gate closes50→51, database
backups and original lease/provider journals are retained, Main and QA each
receive one stop/start, and verified readback opens51→52. The controller sends
no model requests, ERP writes or task retries and does not settle any original.
Actual activation evidence is recorded separately by the deployment owner.

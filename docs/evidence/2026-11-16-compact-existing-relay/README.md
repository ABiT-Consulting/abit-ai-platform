# Compact existing governed relay

The original PM handoff exceeded the ChatGPT message limit because it embedded
the entire WorkPacket and original handoff context. The existing ERP → ABIT.AI
API → ChatGPT relay → Dot path now sends immutable identity and digest references.
The first admitted `agentic_governance_action` call is `context.read` with `{}`;
its response retains the complete original `work_packet`, `handoff_context`,
role schemas and operation catalogue. No SRS, backlog or checkpoint is truncated.
Task creation, approvals, status updates and handoffs remain server controlled.

Project selection keeps the explicit Native Project URL first, then an
owner-verified automation folder, then the optional server environment setting
`ABIT_AGENTIC_CHATGPT_PROJECT_ID`. Every selection uses the existing exact
owner/account synchronization catalogue and owner-scoped folder accessor. A
missing setting retains the existing standalone flow; an unsynced, ambiguous
or foreign selection fails before submission. This grants no account access.

`runtime/utils` contains exactly three leaf increments applied to actual live
baselines. `manifest.json` records their before/after SHA-256 and canonical Source
hashes. Prior Source-only planning PM guidance remains in canonical Source and
is deliberately absent from this live-adapted increment. Actual live wording
and all other implementation bytes are preserved. Preparing this package
performed no deployment, restart, enrollment or task replay.

Verification on 2026-11-16:

- Canonical actual bound-dispatch suite: 18 tests passed, including all role
  identities, QA predecessor identity, unchanged immutable packet, and one
  original submission after repeated dispatch.
- Frozen live-adapted suite: 12 tests passed. A 104,594-character packet and
  30,016-character handoff produced a 5,711-character final relay message;
  `context.read` returned both complete originals without inline payload copies.
- `ABIT_PYTHON=/srv/abit-dev-linux/abit-ai/venv/bin/python npm run api:verify`
  passed (13 source tests, 39 Python API tests, 37 TypeScript tests and no Svelte
  diagnostics).
- The corresponding `npm run api:verify:integration` passed 91 isolated checks
  with zero failures. No live application data or external services were used.
- AST compilation and `git diff --check` passed.

Reproduce the frozen transport verification with the existing project venv:

```sh
/srv/abit-dev-linux/abit-ai/venv/bin/python -I -B docs/evidence/2026-11-16-compact-existing-relay/verify_frozen_transport.py
```

The supported existing `/gateway/call` API remains backward compatible.
Canonical route documentation now describes the added `handoff_context` field;
the runtime operation adds this response field without changing admission,
capability checks, call identity, permissions or write behavior.

`activate.py` is the explicitly invoked controller derived from the existing
Core72 activation procedure. It requires the reviewed controller/manifest hashes,
the exact completed Node cutover PID/start, acknowledged DRAIN49 proof and fresh
authenticated empty Main task proof. It permits only the known held original
run to remain orphaned, preserves all unknown originals and every admission
lease, backs up databases, installs the exact three leaves, and adds only the
existing synced Project default to runtime environment JSON. It preserves file
ownership, mode and extended attributes. After one Main/QA stop/start each, it
verifies the full72-file cohort and unchanged originals before opening49→50.
No controller action was executed while preparing or testing this package.

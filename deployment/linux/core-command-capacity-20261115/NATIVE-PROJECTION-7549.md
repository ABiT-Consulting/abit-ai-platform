# Native 7549 receipt projection

This is a pure, tested historical receipt projection. It does not install code, retain a Root archive, change a release plan or authorize another capacity cutover. The independently observed Core runtime already contained the two capacity files at 15:06 UTC on 2026-11-15; the original Developer result was handed off and the gate was SEALED at epoch 27. The earlier capacity controller remains pinned and UNFROZEN, and its DRAINING24/uncertain-original prerequisites are no longer current.

## Exact conversion

`project_native7549.py` accepts the exact bytes of the reviewed controller and five pinned retained inputs. It reads only selected constant assignments from the controller AST; it never imports or executes its lifecycle. It preserves all five input byte strings and produces one deterministic eight-file offline proposal.

The native publication names `backend`, `agentic-governance-worker` and `scheduler`. The Core consumer expects `backend`, `governance` and `scheduler`. The adapter performs that explicit mapping, changes `container_id` to `id` and `image_id` to `image`, and retains the running flag. It binds the original channel receipt to a proposed local path. Private-spec hashes, loaded-worker contract evidence and the original publication fields remain in the projection provenance. No generic aliases or user-selected paths are accepted.

The unchanged Core `native_positive` function accepts the projected fixture and rejects the raw native shape and ambiguous aliases. That is isolated compatibility evidence. It does not make the fixture a live Root-private input.

## Verification

The focused process completed with exit 0: 10 tests in 1.073 seconds. The exact ten IDs are derived from the frozen single TestCase class and recorded in `native7549-projection-evidence/assembly.json`; the retained unittest output uses the default dot format, not verbose names. Independent review reproduced all eight output files exactly and checked the original five inputs against their canonical retained bytes.

The mandatory `npm run api:verify` initially failed because the default system Python lacked FastAPI. That failed process and log are retained. The supported interpreter override `ABIT_PYTHON=/srv/abit-dev-linux/abit-ai/venv/bin/python`, with `PYTHONDONTWRITEBYTECODE=1`, completed with exit 0: 13 source guards, 39 backend tests, 37 frontend tests and Svelte checks with zero errors or warnings. Source coverage found 606 declared HTTP operations and four justified hidden operations. All selected source hashes were unchanged. No backend route, model, authorization, export or runtime source changed in this tooling increment, so the separate integration gate was not invoked.

Reproduce the focused fixture only from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s deployment/linux/core-command-capacity-20261115 -p test_native7549_projection.py
```

The exact source, fixture, output, process and log hashes are in `native7549-projection-validation.json`. The offline proposal used 61,204 bytes; 1,474,965,504 bytes were available before that copy and a 1 GiB remaining floor was checked. No broader runtime archive was made.

## Retention boundary

The adapter returns a proposal only. Any future authorized retention would require a new, absent directory under an approved Root-owned parent, directory ownership 0:0 and mode 0700, and all eight files created exactly once as regular, single-link, non-symlink files owned by 0:0 with mode 0600. Existing or partial output must cause a hold; it must not be overwritten, merged or filled in automatically. The reviewed create-only helper uses O_EXCL and O_NOFOLLOW, then fsyncs the file and parent. Every retained byte and metadata field must be read back and bound to an independent Root retention receipt before any release plan can refer to it.

The existing Core consumer rechecks the aggregate and channel receipt. It does not itself rehash every provenance copy; the separate all-file retention readback is therefore required. No protected archive was created by this work. No controller guard, fresh browser proof, current backup, process identity, maintenance state, original admission or policy check is supplied or replaced by this projection.

## Limits and subsequent state

7549 reconciled the one historical 7547 Start and appended two evidence files. It did not issue another Start, Stop, reload, CAS, business mutation or model call. Its native worker/history review preserves unresolved Task and ToDo history qualifications. It is not a fresh native Task read, frontend acceptance, independent QA verdict or closing-PM completion.

The separately timestamped Core readbacks retained here show the subsequent capacity cutover and Developer publication. The canonical Developer result is `ready_for_review`, with a durable native `Pending Review` acknowledgement. Its independent QA successor has zero dispatches and is blocked by an unresolved original ERP/Dot request; no closing-PM successor exists in that observation. The provider row remains `unknown`, so a native publication acknowledgement must not be described as a verified original provider receipt. These later facts supersede the former cutover baseline; they do not retroactively change the frozen adapter inputs or tests.

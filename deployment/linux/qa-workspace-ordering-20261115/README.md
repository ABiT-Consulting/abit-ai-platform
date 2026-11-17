# QA workspace ordering candidate

Status: source-only proposal. No canonical product file or live file was changed, and no gateway/provider call, recovery, admission, gate or service action was performed.

## Smallest correction

The only proposed product change is one QA clause in `context().workflow`:

1. `repository.prepare({})` creates this QA invocation's server-selected workspace at the immutable Developer delivery head. The existing handler independently checks the exact repository, PR, head and base before installation.
2. `repository.verify_delivery({})` verifies that prepared source against the actual head.
3. `repository.test({"test_id": "<registered ID>"})` runs only approved independent tests.
4. `repository.verify_delivery({})` reads the head again and composes the QA invocation's actual test/evidence receipts.

Preparation or head verification alone is not a QA pass. All role, profile, authority, original-identity, source, test and final-result checks remain unchanged. The wording explicitly applies to Developer QA; the existing IT and Operations verification guidance is preserved.

`canonical.patch` targets the canonical operations module at source commit `e5f953675aed74add64f41a28fba95832cb85385`. `live-preserving.patch` demonstrates the same text-only delta against the captured live operations module; it is not an activation script. The candidates do not import canonical profile/continuation features into the older live code. Full AST comparison, after reversing the single guidance constant, is identical for each operations module; both delivery handlers are unchanged.

## Evidence

The exact original completed `context.read` result was re-read by its retained SHA256. It contains the old verify-before-prepare instruction and the exact QA WorkPacket identity. At the bounded 15:39:28 UTC observation, the original QA had no workspace, preparation receipt, QA delivery receipt, test call or result; its artifact directory was empty. The original verification call remained uncertain.

A cold fixture using the actual captured verifier, exact synthetic PR/head readbacks, and real workspace accessor reproduces `FileNotFoundError` after the PR and commit reads. This proves a missing prerequisite. It does **not** identify the original exception: an earlier remote read could also have failed, and the bounded service log windows did not retain a correlated gateway exception.

The separate durable supplement is `docs/evidence/2026-11-15-qa-workspace-ordering/`. Previously accepted provider-ownership evidence is unchanged.

## Tests and limits

Eight distinct tests pass against each of two source variants, for 16 final passing executions and two observed process exits of zero. The canonical variant uses captured canonical operations/delivery; the live variant uses captured live operations/delivery with the same isolated canonical supporting modules. This is not a complete live-runtime overlay test.

The tests exercise actual context/registry, preparation, workspace/source checks, SQLite records, test snapshot/artifact construction, receipt composition and QA acceptance. GitHub GET responses and the sandbox process/budget are controlled fixture boundaries. No real GitHub request or test subprocess runs. Temporary SQLite/files are isolated under the repository's private validation directory; file access to live application state, network connections/DNS and subprocess launch are denied by the runner. This is a Python guard, not an OS-level sandbox.

Coverage includes the corrected order, cold missing workspace, exact preparation, no pass without own tests, head/base drift, changed source after tests, unregistered test denial, unchanged six-role catalog/packet output, and unchanged replay behavior for an already-uncertain original call.

Old canonical/live sources each fail the ordering assertion and pass the missing-workspace reproducer. All earlier fixture failures remain retained: the initial setup attempted an unnecessary admission; the corrected handoff fixture now seeds only a synthetic completed predecessor. The first candidate runs exposed missing explicit fixture test paths and a guard that did not recognize a temporary read-only SQLite URI. Those fixture/runner issues were corrected without product changes; both final eight-test runs are clean.

To reproduce a selected variant, use a new evidence label:

```bash
/srv/abit-dev-linux/abit-ai/venv/bin/python -I -B \
  deployment/linux/qa-workspace-ordering-20261115/run_focused.py \
  --mode candidate --flavor canonical --label review-canonical
```

Use `--flavor live` for the captured live handlers. Evidence labels are create-only.

No API gate was claimed for a promoted product change: this remains an owned candidate. Any canonical integration requires the repository's API checks; activation requires independent review and the existing live preservation/authority procedure.

## Current uncertain original

This candidate cannot rewrite the original saved context or clear its uncertain verification call. The actual gateway returns the same uncertain state for the same immutable call ID without executing the handler again; that behavior has a regression test here. There is no dedicated QA verification-status reconciliation operation and no saved QA verification result to adopt in the retained observation.

A separately reviewed exact-original QA provider observation may obtain an existing final response/receipt. Its identity, process, scope, incidental registry/schema effects and private response-retention bounds must be bound independently. It must not retry the tool, resend the model request, rearm QA or reinterpret a missing receipt as success.

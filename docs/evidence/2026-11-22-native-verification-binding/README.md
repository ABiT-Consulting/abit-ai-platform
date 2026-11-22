# Native verification Core binding — exact source evidence

The candidate adds a default-off fixed native verification lane. It retains ordinary Dot/IT routing, current ERP ownership and human approval, immutable WorkPacket/context, original task lease and maintenance admission, one-use root launch intent, and independent result/QA publication gates. It does not authorize arbitrary development commands.

## Accepted source boundary

- Six selective Core files and a separate three-file root service bundle are indexed by `deployment/native-verification-binding-20261122/manifest.json` (SHA256 `77f192a6b5b3fd170b11951445f7f800d22ab077bc3de8505e3002e97f6ad3b6`). The pure contract is shared by both bundles.
- All live preimages were normally readable and matched before source packaging. Exact forward and rollback patch application with `--fuzz=0` reconstructed all six postimages and restored all original preimages/absences. See `package-roundtrip-environment-corrected.json`.
- `validation-final.json` indexes 45 focused methods in one terminal run, including the actual-ledger queued→claim→start→approval→submit transition and the exact current Project environment regression. The transition case proves the same immutable root gate, already-owned fence and stable run ID survive claim; no gate reissue or weaker matching is used.
- Mandatory `api:verify` passed 18 source checks, 48 backend/contract checks and 37 frontend checks; Svelte reported zero errors/warnings. The mandatory full-app integration passed 104 checks with zero failures and 616 registered HTTP operations.
- Both mandatory gates used the same 381-file backend source composition with zero drift. `composed-backend-source.tar.gz` retains those exact source files, two public migration resources and two focused fixture files: 385 files total, no production data or configuration.

## Independent reviews

`independent-core-native-review.json` accepts the Core action/approval/start/checkpoint/current-lease/maintenance binding. `independent-root-broker-review.json` and `.md` accept the fixed standalone root bundle and unknown-acknowledgment behavior. Both reviews correlate exact source and existing test receipts; reviewers did not rerun tests or perform native actions.

The ERP issuer is a separate package. Its common and worker variants were independently reviewed at source level; installed backend composition and rollout are distinct gates. See the deployment package's `integration-closure.json` for concrete dependencies and the legitimate fresh Task route.

## Retained limitations and failures

All initial failing receipts, logs and source/fixture snapshots remain here. Corrections addressed the actual full WorkPacket digest, exact published approval checkpoint CAS, valid maintenance bootstrap epoch zero, and a fake-root fixture that had incorrectly returned completion without a successful launch grant. The API harness's first integration attempt lacked public Alembic resources; the exact retained resources were added without copying live configuration. Final source hashes and passed receipts supersede earlier attempts without deleting them.

The fixtures exercise actual Core ledger/action/adapter/status functions with isolated SQLite and controlled ERP/root observations. They do not execute a protected native provider, issue an authority, consume a real nonce, start a system unit, or establish production functionality. Root kernel identity tests inspect only the ordinary test process; wire tests exchange in-memory frames. The complete ten-operation native worker, live broker, native issuer and fresh scheduler-to-QA flow still require their separate actual acceptance.

The previously observed TASK-2026-06455 is a project-manager relay preclaim blocker. This IT-only route cannot recover it by resetting attempts or manufacturing a queue acknowledgment. TASK-2026-06448 and all original attempts remain unchanged.

## Actual Project environment correction

The earlier9c37 source candidate required an artificial environment value. Root’s authenticated Project read showed both current fields are `ABIT-DEV-LINUX`; the one exact contract comparison was corrected, with no Project change or broader allowlist. The old artificial value, wrong case and production value are rejected. The exact fixed profile, repository, IT role, development/test gateway and native isolation requirements remain. The prior source candidate is retained and superseded for activation. `independent-core-environment-delta-review.json` separately accepts the byte-exact correction and45-method receipt.

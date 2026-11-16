# TASK-2026-06203 original test recovery

Explicit operator recovery authorized by the owner on 2026-11-17 (Asia/Dubai).

The original calls were denied by the memory preflight before spawning any test. The recovered registration requires 4 GiB per test plus 1 GiB host reserve, 16 GiB virtual address space, 300 CPU seconds, 420 wall seconds, 128 processes and 512 open files. The current registration differs (3 GiB and different command arrays), so it was not substituted for the frozen original registration.

Original profile SHA-256: `e094f5706fc0f9ffcc7e3c342fb6645e8f067dda4643d383fab9e4957882010a`.
Pinned baseline: `82ef12862e0986845560e5c9158e3b6f2f043c77`.
Source snapshot including the existing developer runbook: `306a96004265bc553fe4cc6e26d6e4f95b25eca995c5df9dfdb85c5ee9e4d8b5`.

The gateway's original uncertain receipts are retained. The old developer session is handed off, so no AI session is re-admitted and no gateway database row is rewritten. Separate immutable operator recovery receipts retain the two logical call IDs. The operator runner uses the application's real `repository_test`, resource guard, read-only source snapshot and isolated offline sandbox. Tests execute sequentially. An intent without a result holds for reconciliation; it is never automatically replayed.

The first operator attempt failed inside bubblewrap before launching the test commands: `Failed RTM_NEWADDR: Operation not permitted`. Kernel audit identified AppArmor's `unprivileged_userns` denial. The application already uses `AppArmorProfile=chatgpt`; applying this same existing profile to the temporary runner resolves the startup mismatch. No system AppArmor policy or kernel setting is changed. First-attempt receipts remain intact; a distinct directory holds the subsequent attempt. The runner verifies both previous failures match this exact startup denial before admitting the next attempt.

The temporary runner runs as mcpdev with `Delegate=yes`, `DelegateSubgroup=controller`, `AppArmorProfile=chatgpt`, `MemoryMax=6G`, `MemorySwapMax=0`, `TasksMax=160`, `RuntimeMaxSec=960`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`, and writable access limited to the application data and dedicated output directory. Each actual test retains its original 4 GiB memory cgroup and 128-process cap. No application restart or live deployment occurs.

The next operator attempt completed its security process but could not retain its result because its copied configuration still referenced the first attempt's immutable artifact directory. This was a recovery-runner configuration error, not an application test verdict. After confirming the service had exited and its cgroup no longer existed, a new attempt used a verified dedicated evidence directory. The runner now validates both the configured and resolved artifact paths before execution. No prior evidence was overwritten.

Private operator input and final evidence remain on ABIT-DEV-LINUX under `/srv/abit-dev-linux/abit-ai/deployment/original-test-recovery-06203-20261117-final`. Private work packets, tokens and ERP data are excluded from this repository. Both earlier attempt directories are preserved.

`resume_original.py` is a scope-pinned recovery tool, not a general queue worker. It must not be repurposed to bypass uncertain-call handling or run arbitrary model-selected commands. Completion of offline source tests does not establish deployed DEV acceptance, SAP acceptance, or QA approval. See `result-summary.json` for measured results.

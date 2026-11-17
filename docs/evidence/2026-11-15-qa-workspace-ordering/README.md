# Original QA ordering diagnosis

The original QA invocation `agov-step-28b6ec836fbb90da84623ee37fc3cc65ce1037c6ec522b5c`, run `3d016aab-b569-528f-9837-e46945ebcf51`, received a completed context response instructing verification before preparation. The retained response hash is `8fbdb172acca8686bcc7c724f65f25259b1f862e5eb5c3c0c7d239ea40570460`; the exact immutable response was checked without exporting its prompt/context bytes.

At 15:39:28 UTC, its `repository.verify_delivery({})` call remained uncertain with no result. The server-selected QA workspace, workspace journal and QA verification record were absent; there were no test calls or QA results, and the artifact directory was empty. The provider remained dispatching. OPEN28 had one active admission. Main was PID504925/birth70885830. These are timestamped observations, not current liveness assertions.

Both actual canonical and captured live handlers require a prepared QA workspace. A controlled cold fixture reproduces a missing-directory exception only after successful exact synthetic PR/commit reads. This establishes the prerequisite mismatch, not the original HTTP409 exception. The limited service journal windows did not establish an exact correlated exception, and their returned record limit prevents a complete-log claim. The handler launches no test worker; the observed lack of Main child processes does not attribute unrelated workers.

The source-only proposal under `deployment/linux/qa-workspace-ordering-20261115/` changes only the workflow clause to prepare → verify → registered independent tests → verify. Eight tests pass against both handler variants, with controlled network/process boundaries. The existing uncertain-call replay fence remains unchanged. No endpoint, tool retry, model request, gate transition, admission change or service action occurred.

The existing original-result recovery path and a separately bounded original-QA observation must be assessed before any live action. Guidance cannot retroactively change the original saved context or manufacture a verification/test receipt. Independent QA verdict and closing PM completion remain unproven.

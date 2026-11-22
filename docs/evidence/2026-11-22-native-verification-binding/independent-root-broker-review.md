# Independent fixed root broker review

**Accepted at the source boundary, with no blocking finding.** This review covers the complete `agentic_native_broker.py` and `agentic_native_service.py` freeze and its pure contract interaction. It does not activate the route or issue native authority.

The standalone entrypoint reads the fixed root-owned registration and two source leaves through bounded, nofollow checks. It verifies both registered hashes before compiling either module, then executes those same bytes. The broker's standalone import path uses stdlib and the preloaded pure contract; no service-user-writable Core application import is needed.

The peer check binds the Core PID, start generation, all UID/GID fields, command digest and host boot identity. That proof is repeated during the authenticated request challenge. The six retained wire helpers are AST-identical to the existing authenticated broker framing, and the handler admits only the fixed three operations.

The root service revalidates the exact gate, binding, maintenance admission, native authority and prepared control hashes. It writes the immutable original binding and launch intent before the single fixed systemd client call. Lost or partial acknowledgements retain the original attempt; the broker never creates a replacement nonce or retries the unit. Accepted launch tasks remain shielded, closure waits them, and socket cleanup requires its original inode.

Completion requires the root-owned context, exact execution and authority identities, fixed worker/reservation proof and confirmed cleanup. No claimed result is derived from a missing process alone.

The existing terminal receipt reports **43 passed, zero failures, zero source drift**. I independently rehashed the three reviewed leaves, receipt and log, and compared the six framing helper ASTs. I did not rerun the suite or perform a native/provider/root action.

The test boundary remains explicit: real self-process kernel identity and actual wire codec functions are exercised; protected files, systemd calls and lifecycle execution use controlled fixtures. Live registration, Core pins, root dependency hashes, service installation and native/QA acceptance remain separate operator gates. Exact hashes and evidence paths are in `independent-root-broker-review.json`.

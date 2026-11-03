# ABiT multi-product ChatGPT relay migration

## Scope and confirmed topology (2026-11-03)

- `abit.ai` is already hosted on `ABIT-DEV-LINUX` as `mcpdev`; its browser relay listens on Linux loopback `8810` with Chrome CDP on `9228`. Preserve it.
- On Windows Dev-02, BIGRRO currently listens on loopback `8793` and `8795`, ERP on `8794`, and SBODesk gateway on `8788`. They use the signed-in Chrome CDP `9227` and a shared browser lock. Do not stop them during staging.
- Each Linux product process has its own dedicated browser tab/window name, target-id file, project cache, rate-limit state and loopback endpoint. All share **the already authenticated** Linux Chrome profile/lock. No browser profile is uploaded from a running Windows browser.
- Payroll ChatGPT App (`7791`) is an MCP service, **not** a ChatGPT browser relay. It is not included in this browser migration without a separate impact and database connectivity review.

## Source-only package

Run `deployment/windows/aibit-relay/package-product-relays.ps1` on Dev-02. It copies only the approved live relay `.cjs`/`.mjs` modules plus this reviewed Linux orchestration into a restricted staging folder and records SHA-256 hashes. It does **not** export Chrome profiles, cookies, relay tokens, private keys, chats, logs or database files. Never commit the generated package.

The bundle layout is `service/` and `systemd/`. The Linux service launcher reads these approved filenames from `/srv/abit-dev-linux/relay-runtime/service`, preserving existing ABIT.AI files. The bundled `package.json` pins the matching Windows Playwright version; install Linux-native modules without copying Windows `node_modules`.

## Linux rollout conditions (not yet executed)

1. Verify an authorized Linux shell as `mcpdev` and an administrator capable of installing systemd units. Direct Dev-02 `mcpdev@192.168.253.20` SSH returned `Permission denied (publickey)` on 2026-11-03. The existing Dev-02 `ABiTLinuxRelaySSH` listener on port `2226` is a **reverse tunnel** and does not grant access to Linux.
2. Before any copying, back up the exact Linux service files that would be replaced, retain the existing Chrome profile and ABIT.AI state, and verify the package hashes. Copy only bundled modules/scripts into the service folder, avoiding overwrites of unrelated files.
3. Provision matching ERP and BIGRRO relay authorization tokens through an encrypted, authorized transfer as private `mcpdev`-owned files (`secrets/erp-relay.token`, `secrets/bigrro-relay.token`, mode 0600). Tokens are never logged or committed. SBODesk preserves its existing company authorization behavior.
4. Validate Linux Node version, pinned Linux Playwright module, AppArmor Chrome sandbox, CDP `9228`, and both CJS and MJS browser lock helpers. Set executable mode on the launchers. Do not alter existing `aibit-linux-relay-watchdog.service` or restart the existing Chrome.
5. Install `abit-chatgpt-product@.service` as a root-owned systemd template; run under `mcpdev`. Start `@erp`, `@bigrro`, `@sbodesk` individually. Each starts automatically on boot and restarts after exit; product endpoints stay bound to `127.0.0.1`.
6. On each new Linux instance verify `GET /health` and readiness/authentication flags, then product-specific account/project reads, isolated dedicated tab reuse, project creation/readback, message request/response and error handling with authorized sessions. Do **not** call browser message endpoints while another user's relay job is active. Run `verify-product-relays.sh` read-only.
7. Only after live Linux feature checks pass, configure the owning product integration to point to its authorized Linux relay route (private tunnel or approved authenticated network path), run a real product-level smoke test and confirm no duplicate messages. Product repository or application changes require separate scope authorization.
8. Drain the moved Windows product's in-flight work; disable **only** that product's Windows startup; verify Linux handles new requests and Windows no longer receives them. Preserve the Windows runtime for rollback. Never cut over several products simultaneously.

## Existing-service safety and rollback

- All products use a shared Chrome session but separate tab IDs and a shared lock; this prevents two chat submissions racing through a single session. Readiness and authentication, not just HTTP 200 or an active systemd status, are required to declare a product migrated.
- If Linux readiness or user workflow fails, leave or restore the old Windows endpoint and restart its existing task only if it was deliberately stopped. Do not copy an older session over active Linux browser state.
- If any product integration requires code in another repository, do not modify that repository as part of this source-only migration without explicit authorization.

## Current state

The source-side launcher, service template, health verifier and package script are staged; Linux deployment, product integration routing and final acceptance have **not** been verified. No root password is required in chat.

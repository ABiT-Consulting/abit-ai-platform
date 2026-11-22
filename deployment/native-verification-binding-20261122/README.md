# Fixed native verification lane — source candidate

This package contains six exact Core file changes and a separate three-file root broker bundle. It is default-off. It has not installed or started a Core/root service, issued an authority, consumed a native nonce, changed an ERP Task, or run the fixed native verification worker.

## Exact route

The new `verify_native_source` operation accepts only the privileged profile `bigrro-proj0202-fixed-v1`, repository `https://github.com/asma2rjoub-dotcom/bigrro-platform`, environment `native-verification`, and a currently registered `it_manager` binding. Its parameters contain only `profile_id`. The unchanged published worker is `0dcaa6f4b003cbd2a9408c24c9302dcbfd189dc4`; its fixed plan has ten operations, two synthetic databases, zero extra spend and no publication authorization.

The existing ERP scheduler/event and Core claim path supply the exact WorkPacket and run. The existing human action approval path must approve and consume the exact action. Core then binds the original start receipt, any exact published approval checkpoint, current actor/Task/project/SRS/packet/context/fence, lease-secret hash and original maintenance admission to a root-owned gate. A second final challenge rechecks current ERP authority and the exact consumed approval, plus the current Core lease, project/Task controls and maintenance lease. The one-use grant is persisted before it can be transmitted.

The broker accepts only a kernel-verified configured Core process over its dedicated authenticated Unix socket. It reads the root-owned gate and the unchanged v4 authority, independently checks the fixed producer source/manifest, creates a one-use root attempt, and writes its launch intent before the sole `systemd-run` command. A lost acknowledgment is retained as an unknown original. The socket may be observed again; the controller is not submitted again.

Completion uses the actual root-owned context, outcome, bootstrap result, pre-execution and reservation receipts. It requires the exact source/authority/manifest/nonce plus successful output capture, descendant cessation and controller cleanup. Core stores a bounded immutable receipt and produces `ready_for_review`; it does not mark Task acceptance tests passed or grant QA approval. Stop, expiry or Hold may still permit original receipt observation, while existing result-publication gates continue to control handoff.

Ordinary Dot, project-manager, developer and the existing three physical IT operations retain their current route. A PM preclaim failure such as TASK-2026-06455 is not repaired by this IT-only verification route. This package does not replay TASK-2026-06448 or reset either original attempt.

## Core payload and actual API

`manifest.json` binds the current normally readable live preimages and all six postimages. `core-forward.patch` and `core-rollback.patch` are exact file deltas; valid live-only schema/validator changes are retained. The main Git source therefore also incorporates those already-live preimage differences. Only the six listed Core files belong in a Core activation.

The added authenticated read-only API is:

`GET /api/v1/agentic/projects/{project_id}/native-verification/{invocation_id}`

It uses the existing ERP-control authentication, exact project ownership and typed response schema. Missing or invalid credentials are rejected, another project's invocation is rejected, and no POST/dispatch route is added. It exposes only bounded state and receipt hashes; it does not expose raw leases, authority documents, logs or database content.

The Core configuration template remains disabled and contains no key. Enabling requires an explicit allowed Project, the fixed broker socket, a service-owned 32-byte client key file and a current tool-gateway profile allowing this operation only in a development/test environment. Merge the registration into current configuration; do not replace other Project or provider configuration.

## Root bundle and installation boundary

The root bundle consists of `agentic_native_service.py`, `agentic_native_broker.py` and the pure `agentic_native_contract.py`. It uses only the standard library; it never imports the writable Core installation. The fixed loader validates all module bytes before compiling either module. Six existing authenticated framing helpers remain AST-identical to the existing Core IT broker, so the unchanged Core client speaks the same bounded protocol using a separate key/socket.

Required operator-owned paths are fixed:

| Path | Required ownership and purpose |
| --- | --- |
| `/var/lib/abit/native-verification` and `gates`, `attempts` | Root-owned private directories; gates and original broker receipts |
| `/var/lib/abit/native-verification/service` | Root-owned private directory containing the exact three modules and registration |
| Modules and `registration.json` | Root-owned, single-link, no-follow regular files, mode `0400` |
| `/etc/abit/native-verification-broker/key` | Root-owned, single-link, 32-byte mode `0600` key; never stored in this repository |
| `/run/abit-native-verification` | Root-owned mode `2710`, group set to the verified Core GID |
| `/run/abit-native-verification/broker.sock` | Created once by the fixed service, root-owned mode `0660`; an existing socket is never replaced automatically |

`root-registration.template.json` is intentionally invalid for activation: disabled, zero process IDs and `PENDING_DENY` identity pins. A separately reviewed operator action must bind the current post-deployment Core PID, UID, GID, start ticks, command-line digest and boot ID. The server verifies that exact process before and after every authorization challenge. A Core restart therefore requires a fresh reviewed peer registration; a stale PID or reused process generation fails closed.

The supplied systemd unit is source only. It uses the system manager's default root identity, isolated Python without site imports, no automatic restart and graceful broker shutdown. The separate fixed controller unit owns cleanup independently of the broker socket. The broker itself provides no shell, arbitrary command or service-management API.

## Native producer and remaining execution gates

The broker pins controller `773bf0adea5ef2c885adbd0124050a302066c1a41513c65c063d94625c03894b`. Root reported its separately reviewed single-file installation and exact readback in receipt `ca240b6abc3969d4fa3872d4f1dd2a51d4d2020656be679b5023fde93c33b714`; the original controller is retained. This source task did not perform that activation. The other five producer controls retain their published hashes.

Before any native launch, the protected producer still requires its exact bound `EXECUTION.json`, reviewed security/DBA/capacity/isolation evidence, a current unchanged v4 authority, and the one-use reservation/FD3 protocol. No template in this package grants those facts. The independently passing R8 inert boundary and preservation receipts prove the reused boundary primitives; they do not prove this larger provider rootfs, the ten-operation worker or the new broker/ERP/Core route.

Native ERP issuer changes are a separate source package owned by the ERP issuer task. Both exact backend and worker installed preimages must be composed and reviewed before rollout. A legitimate fresh native verification Task must have current SRS requirements, exact IT operation, registered actor, explicit Task start, normal scheduler/claim/start receipt and human action approval. Only then can a current root gate bind that exact queued/run context. Independent QA and PM acceptance remain subsequent actual steps.

## Validation and preservation

The focused source suite executes the actual ledger, start/approval/checkpoint binding, adapter and status/routing code with isolated SQLite and controlled ERP/root fixtures. It covers one-use launch, lost ACK, changed lease/fence/actor, human wait/resume, Stop/Hold, maintenance missing/sealed/replaced/process-generation cases, draining original preservation, immutable receipts, root source loading, kernel peer identity, wire interoperability and accepted-launch shutdown. No provider or protected unit is executed by those tests.

All failed test attempts and their source/fixture snapshots are retained. The first packet hash mismatch, approval-checkpoint CAS issue and maintenance epoch-zero mismatch were source corrections. The optional root receipt simulation was corrected to return absence if no successful final grant existed. An early API integration setup failed because two public Alembic resource files were missing from the isolated source harness; those exact retained resources were added, with no production configuration copied.

See `../../docs/evidence/2026-11-22-native-verification-binding/` for final exact-source receipts, independent reviews, mandatory API gates and source-only validation archive. Source validation does not claim installation, issuance, native verification, scheduler-to-worker completion or production acceptance.

# Fixed native broker v4 binding and activation review

## Source correction

The installed broker was pinned to the v1 protected provider prefix. The v4 producer is a different, deliberately preserved attempt with its own fixed prefix and controller/policy hashes. Registration has no provider-selection field, so changing registration alone cannot select v4.

This change updates exactly three literals in `agentic_native_broker.py`: the fixed prefix, controller hash and unbound worker-policy hash. All 30 function ASTs remain identical to installed source 702568f7. The other four control hashes, fixed published worker, ten-operation plan, v4 authority schema, two-database limit, zero extra spend, no publication, one-use launch, peer authentication and Task approval checks remain unchanged.

| Runtime input | Exact value |
| --- | --- |
| Broker preimage | 702568f7f12ce420dbac407736cef3081cb8ac5f8869c7cf33c21d2aff348eb4 |
| Broker candidate | 1343cd1508e0f3619d816c1f699003d4272b8f8d1a6e8798cd417b57e808120d |
| Fixed provider prefix | /var/lib/bigrro-verification/proj0202-native-worker-20261122-v4 |
| v4 source commit | 280c1b091ef68501df1170e628b2e6f87bad8fc7 |
| Unbound execution base | 787766861cd3b065e438aa64557111739d2766996da56a4750e17e200b62e061 |
| Rootfs manifest | c526b8b7541dbd37e1e56c9b03c7ebd04c5844e4df787d24bf3c63c37da8c664 |
| Controller | 110c644a88f80df88985ff213fd0903598da3b5109081d45753af1a2c64d8c05 |
| Unbound worker policy | 58fddb8d6d061390e99a0abea26ff572e1e629bcd643e42009858c7a89579361 |

The existing six-file Core deployment and previous root bundle remain preserved. Runtime deployment needs only the separately reviewed root broker leaf and its root-owned module registration. No Core Python replacement or Core restart is required for this correction.

## Observed startup prerequisites

The read-only observation at 19:15:33UTC found the broker loaded/inactive/dead, MainPID0, boot enablement disabled. All three staged modules and disabled registration retain their original hashes. The gates/attempt directories, broker key directory/key, socket directory/socket and proposed Core client key are absent. The actual resolver configuration has no native lane and no PROJ-0202 tool-gateway profile.

The actual Core generation was PID3481989/start131898701, all four UIDs999 and all four GIDs988. Its exact command digest and boot ID are in `startup-facts.stdout.json`. These are observed inputs for review, not a guarantee they remain current. The later operator must read and compare them again before registering or starting the broker.

No key contents were read. The resolver was parsed internally only to export its native-lane and PROJ-0202 gateway fields; all other configuration values remain private.

## Three separate admission layers

| Layer | What it needs | What remains denied when absent |
| --- | --- | --- |
| Broker daemon startup | Exact immutable modules and enabled registration for the current Core kernel identity; root private service/gates/attempt directories; root-owned 32-byte key; root/Core-group socket parent | Startup holds on any missing or stale structural prerequisite. No ERP Task or action approval is needed merely to listen. |
| Core route selection | A current configuration entry allowing only PROJ-0202, fixed socket and Core-owned key; development tool-gateway profile with verify_native_source | Ordinary roles and existing physical IT actions retain their route. Missing native registration/profile denies this lane. |
| One native request | Exact current IT WorkPacket and actor; project/Task/Stop/maintenance gates; immutable run/fence/context; root per-invocation gate; exact fresh native authority; consumed current human approval before launch | Missing, expired, replaced or mismatched evidence denies the request. Daemon readiness does not issue authority, create a Task, grant approval or consume a nonce. |

The broker's constructor reads only its three private directories. It does not read a Task gate, native authority or provider during startup. `prepare` and `execute` enforce those later requirements. The dedicated service may therefore be activated after operator and producer acceptance while individual business approvals remain pending.

## Key ownership and transport

The server's fixed key is `/etc/abit/native-verification-broker/key`, root-owned, single-link regular mode0600, exactly32bytes. The Core client separately requires a canonical key file owned by the effective Core UID, also mode0600/single-link/exactly32bytes. The proposed client file is `/srv/abit-dev-linux/abit-ai/config/erp-assignment/native-verification-key`, UID999/GID988.

The two files must contain the same newly generated private bytes. They cannot be one file or a hard link because the ownership requirements differ. No key bytes or key digest belongs in public evidence. Creation must be journalled once, with private in-memory equality verification and exact metadata readback. An unknown partial key installation is reconciled before another action; no automatic rotation.

The socket directory must be root:988 mode2710. The service creates the socket as root:988 mode0660 and refuses an existing path. Kernel SO_PEERCRED is compared with the exact registered Core PID/UID/GID, start ticks, command digest and boot ID before and after its authenticated challenge. A later Core restart requires a reviewed new registration; stale process pins deny requests.

## Configuration reload behavior

The actual existing governance router `_config()` reads the resolver JSON anew. `_deliver`, approval resumption and each reconciliation invocation obtain that configuration before constructing a native client. The focused test executes this exact existing loader and confirms a later atomic replacement is observed.

The native client retains its key and configuration for its invocation. Do not rotate or replace a key/configuration underneath an admitted original. Initial setup has no native originals. Merge only the values at JSON pointers `/agentic_native_verification` and `/agentic_tool_gateway/projects/PROJ-0202` from `core-config.merge-candidate.json` into a fresh private copy of the resolver; preserve all sibling projects and other gateway keys, compare the original full-file digest, preserve unrelated fields/metadata, then atomically replace and read back. Do not change the existing Project environment or broaden other operation lists. The later operator must freshly check artifact-directory access for this Core UID before the first real Task.

A Core restart is unnecessary for this configuration change. The server registration is read once by its process, so broker re-registration is a separate controlled broker lifecycle. Boot enablement is separate from starting the current daemon: the supplied fixed registration binds one Core generation and cannot silently rebind after reboot.

## Concrete controlled activation sequence

All steps below are preparation for independent root review. This branch has performed no production writes or service actions.

1. Require the actual v4 standalone outcome and complete preservation proof, and positively reconciled ERP holder/native cleanup. These facts were still pending when this source package was prepared; do not substitute isolated tests for them.
2. Reobserve current Core identity, broker inactivity, absent socket, existing5staged source/registration/unit hashes, resolver digest and current coordination lease. Retain the old v1 source and disabled registration in a new private operator backup before replacing the one broker leaf. Any unexpected original broker gates/attempts must be reconciled before changing fixed provider binding.
3. Verify candidate1343 against the accepted source review. Install only that root0400 broker leaf and a root0400 registration with the new broker digest and freshly confirmed Core kernel identity. The unchanged service3710 and contract44c1 stay pinned. The ordinary candidate registration contains the observed identity only; the final operator must compare actual current values.
4. Create the missing root0700 gates/attempt directories, root0700 key parent, separate matching root0600/Core0600 key files and root:988 mode2710 socket parent. These are startup artifacts; create no Task gate, native authority, reservation or nonce.
5. Confirm the effective unchanged systemd unit. If manager reload is required for an installed unit file, journal that exact command. Save a durable one-use start intent, start only `abit-native-verification-broker.service`, and reconcile the original unit/process/socket if the acknowledgement is uncertain. Never repeat an unknown start or unlink an existing socket by assumption. No Core/relay restart is part of this action.
6. Positively verify the broker process, source, unit and socket ownership. Keep the Core route off until this succeeds. Then merge the reviewed PROJ-0202 native-lane/development-profile config under the current resolver CAS, and verify the next Core read sees exactly the new fields while all unrelated configuration is preserved.
7. Validate denial through the accepted isolated tests and bounded runtime metadata. Do not impersonate the Core process or manufacture a queued invocation merely to probe the socket. A legitimate fresh Task is the later full-flow test.
8. For that fresh Task, use the normal native ERP issuer/SRS/role/explicit-start path. Derive the exact gate from the actual queued scope; the existing claim retains its stable run/fence. Require an independently issued unused authority and actual human approval consumption before launch. The broker's durable intent forbids replay after unknown ACK. Successful fixed-plan receipts yield ready_for_review and subsequent independent QA, not arbitrary development completion.

## Consumed provider rootfs caveat

The unchanged native `admission.verify_rootfs` demands exactly the original manifest entry set. The unit binds the nonce-specific attempt directory into its rootfs; systemd can retain that empty target scaffold. The unchanged cleanup proves unit/cgroup cessation and does not remove that original scaffold.

A standalone v4 pass therefore does not establish that its consumed rootfs can accept a second governed nonce. Root must inspect the actual full post-run entry set. If retained scaffold makes it non-pristine, preserve it and prepare a separate reviewed fresh integration target from the same immutable provider bytes with exact new controller/policy/broker binding. Never delete old scaffolds, reuse the consumed nonce, weaken the manifest verifier or reinterpret the standalone result as a prior Core launch. Daemon startup with per-request denial can proceed independently of that later execution prerequisite.

## Verification

The original7025 source fails the new exact-v4 path regression; that expected failure and its fixture are retained. Corrected source passes20/20 normal and20/20 optimized tests with zero drift. Cases exercise the actual six frozen v4 control bytes, policy normalization, stale manifest/control/authority/gate denial, fixed argv, unchanged function ASTs, startup without business authority, registration pinning, key metadata and actual Core config reload.

The required `npm run api:verify` passed:18 source-contract checks,48 developer/backend checks,37 frontend tests and Svelte0errors/0warnings. The isolated381-file composition differs from the retained accepted harness in exactly the broker file and has zero source drift. The harness has no Git commit, so Vite emitted its existing missing-HEAD diagnostic while still passing; a dependency emitted its existing Svelte export-condition notice. No application/configuration/live database or provider was used.

No API route, model, authentication function, permission predicate or export changed. The earlier full integration evidence remains unchanged; the broad integration suite was not repeated for three fixed producer literals. Runtime source/registration installation, service startup, real key exchange, native provider acceptance and fresh scheduler-to-QA flow remain separate actual stages.

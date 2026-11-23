# Fixed PR241 AUTH follow-on release parent

Status: source preparation; not installed, registered, admitted, approved or deployed. The historical PR237 protocol and package remain unchanged.

## Exact application and executable bindings

| Binding | Value |
| --- | --- |
| Project / host | PROJ-0196 / ABIT-DEV-LINUX (abit-dev-linux) |
| Dedicated service | intermarket-sbodesk.service |
| New fixed resource | intermarket-pr241-auth-guard |
| Application implementation | 922218853c587d2d1c5c7ff41332078b67c7455c |
| Application manifest SHA256 | 7bf2c4e8adbcc197ebf58b3da7c10068da4932dfc28b244422b08334aec33675 |
| Frontend index SHA256 | b33393b92dd11754843add693b88f42d6d6f34e47d9226dda010fb8e9d299322 |
| Unchanged backend DLL SHA256 | b4fe119151fddcf9674796f04840abeefee195066b185ca62bf0415e9ad67e5c |
| Active phase controller SHA256 | 51fce4c13f2300bc0a23e3162fbe139a78f17cad56a2e64409fc7d116117cb58 |
| Controller executable | /usr/libexec/abit/intermarket-pr241-auth-guard/package_controller.py |
| Controller filesystem library SHA256 | 13439494da4fc4c9146b22c87ce783c8f5faab8e0ff035a4f0f113c5274142a9 |
| Shared fixed restart sampler SHA256 | 685f8bb73ad68225f8d97d0734ac3b69f0066c0b46b27dbd1d6390ed63544d6e |
| Core source base | 5697c52d690b827d368a3057d130aff006577248 (prepared Core PR30) |
| ERP companion source base | df855412c0126406a8110a5172f11b1ed942505c (prepared ERP PR37) |

The active executable is the phase controller. The previous monolithic helper remains its pinned filesystem library; its missing maintenance API interface is not represented as an installed route.

The new Core protocol and ERP target validator require the exact controller hash. Before every controller invocation, Core verifies the actual root-protected executable bytes. The controller pins its library, sampler and artifact/input metadata. Native human approval covers the complete immutable plan, including controller, rollback policy, target, resource and policy digests. Consumption and every phase revalidate that original action and plan. An old PR237 approval cannot authorize the new resource.

## Original authority and service ownership

The fixed registry selects only the historical PR237 module or this exact follow-on module. It accepts no module, command, path or artifact tuple selected by the caller. Existing project, task, actor, assignment, run, invocation, current approval, consumed receipt, maintenance lease, epoch and native control checks remain. No SRS acceptance criterion or FR-001 parser rule changes.

Both release resources use physical identity systemd:intermarket-sbodesk.service. One existing protected broker profile supplies the same execution_state_root, original-operations journal and unique unsettled service reservation. This also conflicts with the registered restart-service operation. No per-package journal or lock is introduced.

Installation must reuse and read back the actual existing broker, profile, worker journal and execution journal while preserving unresolved originals. Source inventory does not prove that registration occurred or prevent an operator from incorrectly replacing the profile's root.

The worker retains its private network namespace, AF_UNIX restriction and empty capability bounding set. Its writable paths for this resource are exactly the existing two private journal roots, /var/lib/intermarket-pr241-auth-guard-activation and /etc/systemd/system/intermarket-sbodesk.service.d. Inputs remain read-only. This preparation does not grant permissions or create any installed resource.

## Mutation and restart sequence

1. Preflight/publication use the private original-worker phase channel, with fixed current native decisions and host observations at every required boundary.
2. The broker verifies complete prior/runtime/configuration state and current memory, disk and inode reserves.
3. Before each restart, the parent journals a full original restart-preimage observation.
4. It obtains one fresh native phase-entry mutation decision.
5. A compact final-restart-resources read samples fresh headroom and service identity and references the exact heavy observation by digest. It does not retimestamp cached application health or inventory evidence.
6. The confined parent repeats the pinned lightweight local unit/process/drop-in sampler, compares the complete identity, rechecks the original mutation grant's expiry and submits its sole manager job.
7. The unchanged fixed D-Bus worker captures the matching original job acknowledgement, JobRemoved terminal and resulting InvocationID. Uncertain outcomes remain observe-only.
8. Conditional rollback requires an attributed rejection, preserved preimage and owned selector, distinct original rollback job under the same reservation, fresh authority and verified prior readback.

The Core parent is the only restart owner. The phase controller never submits a service restart.

The final sampler reads only the fixed service's public process/manager identity and unit/drop-in hashes and physical tokens. It reads no credentials, process environment or private configuration contents. Fresh private-storage headroom comes from the authenticated original host observer because the confined parent cannot traverse those ancestors. No new capability, supplementary group or broad writable path is added.

## Concrete broker source inventory

The source map, code manifest and inventory in this directory identify all 21 mandatory broker files and empty package markers. The refresh script produces source evidence only; it is not an installer.

For the new resource, entry.py checks complete required inventory before imports. It retains the existing 128000-byte protected profile bound and old-profile inventory contract. Installed files still require protected ownership and exact digest readback.

The legitimate owner must preserve unrelated installed customizations, review exact composition/preimages, retain the existing protected journals and register actual physical mirror, activation, recovery and rollback-policy identities. This package invents none of those facts.

## Verification and limits

Retained receipts cover 114 existing IT tests, 13 follow-on protocol tests, 14 ERP plan tests, 24 ERP approval tests, required API verification and 91 isolated Core API integration cases. Negative cases include both release directions and restart conflicts, duplicate uncertain originals, wrong package/controller, changed approval/policy, revocation/expiry, process/selector drift, low current headroom, compact worker relay, complete conditional rollback and denied rollback dispatch.

An initial broad IT run used system Python and an inherited group-writable umask; its dependency/protection failures are recorded separately. The passing run used the documented existing interpreter and process-local 0077 umask. No runtime environment was modified.

All evidence is source, fixture or isolated Core API evidence. It is not authenticated InterMarket candidate-backend company QA, current FR-001 admission, release approval, ERP acceptance or customer sign-off. Real company acceptance remains zero. The 09:00 Dubai target has passed; preparation does not certify project completion.

## Remaining release gates

The legitimate current operator must complete required runtime composition, original native lifecycle and genuine admission; install/register/read back this exact reviewed package; provide approved isolated actual-backend/nonproduction-company QA access; and obtain exact scoped DEV approval. Deployment uses one original admitted job and actual terminal readback. Public authenticated DEV acceptance and independent QA/PM/eligible ERP lifecycle decisions follow those gates.

# Bulk agent deletion — verified source and additive release

## Status
Implemented in the authoritative Dev-02 checkout on branch `abit-projects`.
Built and verified in isolation. No production deployment or deletion of live agents has occurred.

The live service is on ABIT-DEV-LINUX, at:
`/srv/abit-dev-linux/abit-ai/current/runtime/open_webui/frontend`.
Dev-02's documented port 8888 has no listener; public abit.ai health/config returned HTTP 200.

## Scope
The native source supports selection, filtered select-all on Agent map, accurately labeled page selection on Manage agents, immutable confirmation, run-history warning, serial deletion, explicit success, partial failures, retry and accessible keyboard behavior.

The immediate live candidate is `additive/agent-bulk-delete-20261122.js`.
It enhances the current legacy Agent map only, reuses the existing batch helper and authenticated deletion API, and preserves the live backend's ERP/governance restrictions.
It defers if native bulk controls are present.

The full generated frontend is NOT the live release candidate: the installed Linux shell contains additional customizations absent from this Windows checkout. The approved additive release must change only the versioned asset and its exact script tag, retaining all existing scripts, CSS and compiled entries. It requires no service restart.

## Accepted verification
- `npm run api:verify`: exit 0; coverage of 605 HTTP operations, 4 justified hidden exceptions; 13 contract tests, 39 developer API/UI Python tests, 37 frontend tests; developer type check 0 errors/warnings.
- Shared destructive-action helper: 5/5 passed.
- Native compiled-source browser acceptance: 17/17 passed.
- Legacy additive bundle browser acceptance: 23/23 passed.
- Offline release delta/rollback checks: 5/5 passed.
- `npm run build`: exit 0 with `NODE_OPTIONS=--max-old-space-size=6144`; static output written to build/. Completed 23 November 2026, 00:04 Dubai.
- 50 actual native fixture source files and 5 additive input files independently reconciled against SHA256 manifests with zero drift.

The initial 4 GB full build failed with V8 heap exhaustion (exit 134); its original logs are retained as `build-4096mb-failed.*`.
The initial native browser run was 14/15; its sole remaining failure was a harness lookup of the old button label after the application correctly changed it to disabled “Deleting…”. Initial artifacts remain as `initial-*`. The accepted 17-case run uses the stable test ID and includes two independently reviewed race/selection corrections.

## Source preservation
The checkout was already extensively modified and diverged from its remote before this task (HEAD 847e68ce5ab67884d289822acf1f119a28b27f1b; ahead 15 / behind 14).
Unrelated changes are preserved. Existing modified/untracked native page/map work is not swept into the scoped source commit.
The native integration is applied in the working tree and also retained as `native-integration.patch` with exact preimage/after hashes in `native-source-manifest.json`.
Before images remain under `before/` locally.
The additive release carries exact five source input snapshots under `additive/source-inputs/`, including the pre-existing API-client dependency bytes, so its accepted source is retained independently of unrelated working-tree changes.

## Release after explicit production authorization
1. Reconcile the current frontend operator/lease and verify ABIT-DEV-LINUX identity.
2. Stage the unchanged additive package through the established authorized route.
3. Run `python3 deploy_additive.py` inside the package for read-only preflight.
4. Only with production release approval, run `python3 deploy_additive.py --apply`.
5. Verify public delivery and an authenticated Agent map selection/cancel smoke, existing features and health. Do not delete real agents merely to test the option.
6. Rollback is `python3 deploy_additive.py --rollback`; it removes only the exact owned script tag and preserves later unrelated shell changes.

The installer aborts on pinned installed-source drift. No credential, database, account or backend replacement is part of this package.

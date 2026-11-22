# Agent bulk delete — additive release candidate

Status: built candidate, not deployed. No production agents were deleted by this builder.

## Scope

The native source implementation remains in the authoritative Dev-02 checkout.
This standalone candidate supports the observed Linux legacy Agent map only.
It defers completely when a native bulk-agent-actions component exists.
The native Manage agents page has its separately verified feature; this legacy adapter does not alter the legacy list.

Batch execution reuses src/lib/utils/automations/bulkDelete.ts and DELETE uses the existing automation API client.
The adapter adds namespaced selection controls and a native modal confirmation; it does not duplicate the batch engine.
Fresh reads check identity, latest run status and ERP/governance metadata before each submitted DELETE.
These checks do not create an atomic server-side guarantee against an agent starting immediately afterward.

## Observed legacy DOM contract

- Root: [data-testid="context-map"].
- Toolbar: .map-toolbar; mount immediately after it.
- Cards: article.agent-card; identity from a.agent-name and exact same-origin /automations/UUID.
- State: data-agent-status; unknown and running are ineligible.
- Search: input[aria-label="Search agents"].
- Context: select[aria-label="Select context"]; threshold: select[aria-label="Stale threshold"].
- Status filters: .metrics button; viewport: .map-viewport.
- Refresh: .map-toolbar button with textContent.trim() equal to Refresh.
- Native deferral: [data-testid="bulk-agent-actions"]:not([data-abit-bulk-root]).

The overlay clears selection on filter/view changes and never auto-selects newly appearing cards.
Confirmed-deletion tombstones suppress stale cards using a namespaced attribute/CSS; Svelte cards and counters are not removed or rewritten.
The existing Refresh action updates authoritative map state after a batch.
Links and interactive controls are guarded during the batch, beforeunload requests browser confirmation, and detached/changed views stop unsent requests.
There is no fake or independently bundled SvelteKit beforeNavigate shim.

## Release procedure for the accountable operator

1. Verify current ABIT-DEV-LINUX identity, active release, current operator ownership, service health and existing source-bound authorization.
2. Verify source-manifest.json and isolated browser acceptance against these exact bytes.
3. Pin the current index.html preimage and preserve every existing script/style/compiled entry; abort on drift.
4. Back up that exact shell, install only this versioned asset, then add one deferred script tag to the current shell atomically.
5. Verify the served asset hash, additive-only shell change, health, authenticated map controls and retained live features.
6. Do not replace the frontend directory, backend, data, compiled entry or existing enhancements; no service restart is required.

Exact installer tag: <script src="/agent-bulk-delete-20261122.js" defer crossorigin="use-credentials"></script>

Rollback removes only this exact tag from the then-current shell after a compare check. Do not overwrite later work using an old shell backup.
The unused versioned asset can remain after rollback.

## Provenance

Authoritative branch: abit-projects. HEAD: 847e68ce5ab67884d289822acf1f119a28b27f1b.
Exact dirty-source input hashes and generated asset hash: source-manifest.json.
Read-only live compatibility baseline: frontend/index.html SHA256 766cf5aa0a442b58c227df5ea833d0910d7f2863e896782a0311ba7b78e2303f.
Observed legacy node: _app/immutable/nodes/22.AbitErpRuns20261108.js.
Prior additive reference: /srv/abit-dev-linux/abit-ai/deployment/agent-card-play-20261107/deploy_agent_card_play.py.

Production activation has not been approved in this task. Run the installer without flags for read-only preflight; --apply is reserved for the approved release.

Exact five source inputs are retained under source-inputs/. Their original working-tree baseline was dirty; no unrelated source is to be overwritten. The full frontend build is not the Linux deployment candidate.

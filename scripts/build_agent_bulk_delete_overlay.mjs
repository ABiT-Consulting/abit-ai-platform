/**
 * Build a single additive legacy-map enhancement from authoritative source.
 * Does not change application build/, any existing asset, or any remote runtime.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { build, version as esbuildVersion } from 'esbuild';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(repo, 'deployment/bulk-agent-delete-20261122/additive');
const assetName = 'agent-bulk-delete-20261122.js';
const branch = execFileSync('git', ['branch', '--show-current'], { cwd: repo, encoding: 'utf8' }).trim();
const commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim();
if (branch !== 'abit-projects') throw new Error('Build requires the authoritative abit-projects branch.');
const pkg = JSON.parse(await fs.readFile(path.join(repo, 'package.json'), 'utf8'));
const sourcePaths = [
	'src/agent-bulk-delete/entry.ts',
	'src/lib/utils/automations/bulkDelete.ts',
	'src/lib/apis/automations/index.ts',
	'src/lib/constants.ts',
	'scripts/build_agent_bulk_delete_overlay.mjs'
];
const beforeSources = new Map(await Promise.all(sourcePaths.map(async (relative) =>
	[relative, await fs.readFile(path.join(repo, relative))]
)));
await fs.mkdir(output, { recursive: true });
await build({
	absWorkingDir: repo,
	entryPoints: ['src/agent-bulk-delete/entry.ts'],
	outfile: path.join(output, assetName),
	bundle: true,
	format: 'iife',
	platform: 'browser',
	target: ['es2020'],
	minify: false,
	sourcemap: false,
	treeShaking: true,
	legalComments: 'inline',
	alias: { '$lib': path.join(repo, 'src/lib') },
	define: {
		APP_VERSION: JSON.stringify(pkg.version),
		APP_BUILD_HASH: JSON.stringify(commit)
	},
	banner: {
		js: '/* ABIT-AGENT-BULK-DELETE-20261122: additive legacy-map adapter. Source branch ' +
			branch + ' at ' + commit + '; exact compiled input hashes in source-manifest.json. */'
	}
});
execFileSync(process.execPath, ['--check', path.join(output, assetName)], { cwd: repo, stdio: 'pipe' });
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const source_files = await Promise.all(sourcePaths.map(async (relative) => {
	const bytes = await fs.readFile(path.join(repo, relative));
	if (!bytes.equals(beforeSources.get(relative))) throw new Error('Source changed during build: ' + relative);
	return { path: relative, sha256: sha256(bytes), bytes: bytes.length };
}));
const assetPath = 'deployment/bulk-agent-delete-20261122/additive/' + assetName;
const assetBytes = await fs.readFile(path.join(repo, assetPath));
const manifest = {
	release: 'ABIT-AGENT-BULK-DELETE-20261122',
	status: 'built-not-deployed',
	source_branch: branch,
	source_commit: commit,
	source_files,
	asset: { path: assetPath, sha256: sha256(assetBytes), bytes: assetBytes.length },
	build: { tool: 'esbuild', version: esbuildVersion, format: 'iife', target: 'es2020' },
	runtime_contract: {
		api: 'Existing authenticated per-agent DELETE /api/v1/automations/{id}/delete; explicit true required',
		preflight: 'Owner-visible GET identity and latest run status; ERP/governance protections retained',
		backend_changed: false,
		service_restart: false,
		sveltekit_navigation_shim: false
	}
};
await fs.writeFile(path.join(output, 'source-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
const notes = [
	'# Agent bulk delete — additive release candidate',
	'',
	'Status: built candidate, not deployed. No production agents were deleted by this builder.',
	'',
	'## Scope',
	'',
	'The native source implementation remains in the authoritative Dev-02 checkout.',
	'This standalone candidate supports the observed Linux legacy Agent map only.',
	'It defers completely when a native bulk-agent-actions component exists.',
	'The native Manage agents page has its separately verified feature; this legacy adapter does not alter the legacy list.',
	'',
	'Batch execution reuses src/lib/utils/automations/bulkDelete.ts and DELETE uses the existing automation API client.',
	'The adapter adds namespaced selection controls and a native modal confirmation; it does not duplicate the batch engine.',
	'Fresh reads check identity, latest run status and ERP/governance metadata before each submitted DELETE.',
	'These checks do not create an atomic server-side guarantee against an agent starting immediately afterward.',
	'',
	'## Observed legacy DOM contract',
	'',
	'- Root: [data-testid="context-map"].',
	'- Toolbar: .map-toolbar; mount immediately after it.',
	'- Cards: article.agent-card; identity from a.agent-name and exact same-origin /automations/UUID.',
	'- State: data-agent-status; unknown and running are ineligible.',
	'- Search: input[aria-label="Search agents"].',
	'- Context: select[aria-label="Select context"]; threshold: select[aria-label="Stale threshold"].',
	'- Status filters: .metrics button; viewport: .map-viewport.',
	'- Refresh: .map-toolbar button with textContent.trim() equal to Refresh.',
	'- Native deferral: [data-testid="bulk-agent-actions"]:not([data-abit-bulk-root]).',
	'',
	'The overlay clears selection on filter/view changes and never auto-selects newly appearing cards.',
	'Confirmed-deletion tombstones suppress stale cards using a namespaced attribute/CSS; Svelte cards and counters are not removed or rewritten.',
	'The existing Refresh action updates authoritative map state after a batch.',
	'Links and interactive controls are guarded during the batch, beforeunload requests browser confirmation, and detached/changed views stop unsent requests.',
	'There is no fake or independently bundled SvelteKit beforeNavigate shim.',
	'',
	'## Release procedure for the accountable operator',
	'',
	'1. Verify current ABIT-DEV-LINUX identity, active release, current operator ownership, service health and existing source-bound authorization.',
	'2. Verify source-manifest.json and isolated browser acceptance against these exact bytes.',
	'3. Pin the current index.html preimage and preserve every existing script/style/compiled entry; abort on drift.',
	'4. Back up that exact shell, install only this versioned asset, then add one deferred script tag to the current shell atomically.',
	'5. Verify the served asset hash, additive-only shell change, health, authenticated map controls and retained live features.',
	'6. Do not replace the frontend directory, backend, data, compiled entry or existing enhancements; no service restart is required.',
	'',
	'Suggested tag: <script src="/' + assetName + '?v=' + manifest.asset.sha256.slice(0, 12) + '" defer crossorigin="use-credentials"></script>',
	'',
	'Rollback removes only this exact tag from the then-current shell after a compare check. Do not overwrite later work using an old shell backup.',
	'The unused versioned asset can remain after rollback.',
	'',
	'## Provenance',
	'',
	'Authoritative branch: ' + branch + '. HEAD: ' + commit + '.',
	'Exact dirty-source input hashes and generated asset hash: source-manifest.json.',
	'Read-only live compatibility baseline: frontend/index.html SHA256 766cf5aa0a442b58c227df5ea833d0910d7f2863e896782a0311ba7b78e2303f.',
	'Observed legacy node: _app/immutable/nodes/22.AbitErpRuns20261108.js.',
	'Prior additive reference: /srv/abit-dev-linux/abit-ai/deployment/agent-card-play-20261107/deploy_agent_card_play.py.',
	''
].join('\n');
await fs.writeFile(path.join(output, 'README.md'), notes);
process.stdout.write(JSON.stringify(manifest, null, 2) + '\n');

<script lang="ts">
	import { onDestroy } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import { deleteAutomationById } from '$lib/apis/automations';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import {
		deleteAgentsSequentially,
		type BulkAgentTarget,
		type BulkAgentDeleteResult
	} from '$lib/utils/automations/bulkDelete';

	export let items: BulkAgentTarget[] = [];
	export let selectedIds: string[] = [];
	export let busy = false;
	export let disabled = false;
	export let scope = '';
	export let selectAllLabel = 'Select all filtered';
	export let onDeleted: (ids: string[]) => Promise<void> = async () => {};

	let previousScope = scope;
	let showConfirm = false;
	let pendingTargets: BulkAgentTarget[] = [];
	let completed = 0;
	let targetCount = 0;
	let result: BulkAgentDeleteResult | null = null;
	let refreshError = '';
	let destroyed = false;

	$: eligibleItems = [...new Map(items.filter((item) => !item.disabled).map((item) => [item.id, item])).values()];
	$: eligibleIds = new Set(eligibleItems.map((item) => item.id));
	$: if (scope !== previousScope) {
		previousScope = scope;
		selectedIds = [];
		if (!busy) {
			showConfirm = false;
			pendingTargets = [];
			result = null;
			refreshError = '';
		}
	}
	$: if (!busy && selectedIds.some((id) => !eligibleIds.has(id))) {
		selectedIds = selectedIds.filter((id) => eligibleIds.has(id));
	}
	$: selectedTargets = eligibleItems.filter((item) => selectedIds.includes(item.id));
	$: allSelected = eligibleItems.length > 0 && selectedTargets.length === eligibleItems.length;
	$: partlySelected = selectedTargets.length > 0 && !allSelected;

	beforeNavigate((navigation) => {
		if (busy) navigation.cancel();
	});
	onDestroy(() => {
		destroyed = true;
	});

	function confirmSelection() {
		if (busy || disabled || !selectedTargets.length) return;
		pendingTargets = selectedTargets.map(({ id, name }) => ({ id, name }));
		result = null;
		refreshError = '';
		showConfirm = true;
	}

	async function deleteConfirmed() {
		if (busy || disabled || !pendingTargets.length) return;
		// Consume the exact reviewed snapshot before awaiting any work.
		const targets = pendingTargets;
		pendingTargets = [];
		busy = true;
		completed = 0;
		targetCount = targets.length;
		try {
			result = await deleteAgentsSequentially(
				targets,
				(id) => deleteAutomationById(localStorage.token, id),
				{
					canDelete: (id) => items.some((item) => item.id === id && !item.disabled),
					shouldStop: () => destroyed,
					onProgress: (count) => (completed = count)
				}
			);
			selectedIds = result.failures.map((failure) => failure.id);
			if (result.deletedIds.length) {
				try {
					await onDeleted(result.deletedIds);
				} catch {
					refreshError = 'The list could not be refreshed. Reload to check the current agents.';
				}
			}
		} finally {
			busy = false;
		}
	}
</script>

<div class="bulk-agent-actions" data-testid="bulk-agent-actions" aria-busy={busy}>
	<div class="selection-controls">
		<label class="select-all">
			<input
				type="checkbox"
				aria-label={selectAllLabel}
				checked={allSelected}
				indeterminate={partlySelected}
				disabled={busy || disabled || !eligibleItems.length}
				on:change={(event) => {
					selectedIds = event.currentTarget.checked ? eligibleItems.map((item) => item.id) : [];
				}}
			/>
			<span>{selectAllLabel} ({eligibleItems.length})</span>
		</label>
		<span class="selection-count" role="status" aria-live="polite">{selectedTargets.length} selected</span>
		<button
			type="button"
			class="clear-selection"
			disabled={busy || !selectedTargets.length}
			on:click={() => (selectedIds = [])}
		>Clear selection</button>
		<button
			type="button"
			class="delete-selected"
			data-testid="bulk-agent-delete"
			disabled={busy || disabled || !selectedTargets.length}
			on:click={confirmSelection}
		>
			<GarbageBin className="size-4" />
			{busy ? 'Deleting…' : 'Delete selected (' + selectedTargets.length + ')'}
		</button>
	</div>
	{#if items.some((item) => item.disabled)}
		<p class="selection-help">Agents with running or unconfirmed delegated work cannot be selected.</p>
	{/if}
	{#if busy}
		<p class="result" role="status">Deleting {completed} of {targetCount} agents…</p>
	{:else if result}
		<p class="result" role="status">
			{result.deletedIds.length} {result.deletedIds.length === 1 ? 'agent deleted' : 'agents deleted'}.
			{#if result.failures.length}
				{result.failures.length} {result.failures.length === 1 ? 'deletion' : 'deletions'} not confirmed.
				Review the failures before retrying.
			{/if}
		</p>
		{#if result.failures.length}
			<details class="failures" open>
				<summary>Deletion failures ({result.failures.length})</summary>
				<ul>
					{#each result.failures as failure (failure.id)}
						<li><strong>{failure.name}</strong> — {failure.error}</li>
					{/each}
				</ul>
			</details>
		{/if}
		{#if refreshError}<p class="failures" role="alert">{refreshError}</p>{/if}
	{/if}
</div>

<ConfirmDialog
	bind:show={showConfirm}
	title="Delete selected agents?"
	confirmLabel={'Delete ' + pendingTargets.length + (pendingTargets.length === 1 ? ' agent' : ' agents')}
	on:cancel={() => (pendingTargets = [])}
	on:confirm={deleteConfirmed}
>
	<div class="bulk-delete-confirmation">
		<p>
			Permanently delete {pendingTargets.length}
			{pendingTargets.length === 1 ? 'selected agent' : 'selected agents'} and their recorded run history?
			This cannot be undone.
		</p>
		<ul aria-label="Agents selected for deletion">
			{#each pendingTargets as target (target.id)}
				<li>{target.name}</li>
			{/each}
		</ul>
		<p>Deleting an agent does not cancel work already sent to an external service.</p>
	</div>
</ConfirmDialog>

<style>
	.bulk-agent-actions {
		margin: 12px 0;
		padding: 12px;
		border: 1px solid #d1d5db;
		border-radius: 12px;
		background: #f9fafb;
		color: #374151;
		font-size: 13px;
	}
	.selection-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
	.select-all { display: inline-flex; align-items: center; gap: 9px; min-height: 36px; cursor: pointer; }
	input { width: 18px; height: 18px; accent-color: #6366f1; }
	.selection-count { font-variant-numeric: tabular-nums; }
	button { min-height: 36px; padding: 7px 12px; border-radius: 8px; font-weight: 600; }
	button:disabled { opacity: .45; cursor: not-allowed; }
	button:focus-visible, input:focus-visible { outline: 2px solid #6366f1; outline-offset: 3px; }
	.clear-selection:hover:not(:disabled) { background: #e5e7eb; }
	.delete-selected { display: inline-flex; align-items: center; justify-content: center; gap: 7px; margin-left: auto; background: #b91c1c; color: #fff; }
	.delete-selected:hover:not(:disabled) { background: #991b1b; }
	.selection-help, .result { margin-top: 8px; line-height: 1.5; }
	.selection-help { color: #6b7280; font-size: 12px; }
	.failures { margin-top: 8px; color: #b91c1c; overflow-wrap: anywhere; }
	.failures ul { max-height: 160px; overflow-y: auto; padding-left: 20px; list-style: disc; }
	.bulk-delete-confirmation { font-size: 14px; line-height: 1.55; color: #4b5563; }
	.bulk-delete-confirmation ul { max-height: min(240px, 30vh); overflow-y: auto; padding: 8px 8px 8px 24px; margin: 12px 0; border: 1px solid #d1d5db; border-radius: 8px; list-style: disc; overflow-wrap: anywhere; }
	:global(.dark) .bulk-agent-actions { background: #181d27; border-color: #374151; color: #e5e7eb; }
	:global(.dark) .clear-selection:hover:not(:disabled) { background: #374151; }
	:global(.dark) .selection-help { color: #9ca3af; }
	:global(.dark) .failures { color: #fca5a5; }
	:global(.dark) .bulk-delete-confirmation { color: #d1d5db; }
	:global(.dark) .bulk-delete-confirmation ul { border-color: #374151; }
	@media (max-width: 480px) {
		.selection-controls { gap: 8px; }
		.select-all { flex-basis: 100%; }
		.delete-selected { margin-left: 0; }
	}
</style>

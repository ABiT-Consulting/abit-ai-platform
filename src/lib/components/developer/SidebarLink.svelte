<script lang="ts">
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { user } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import CodeIcon from '$lib/components/layout/Sidebar/icons/Code.svelte';

	export let collapsed = false;
	export let onNavigate: () => void = () => {};
	const i18n: any = getContext('i18n');
	$: active = $page.url.pathname === '/developers';
	function navigate(event: MouseEvent) {
		event.stopImmediatePropagation();
		event.preventDefault();
		goto('/developers');
		onNavigate();
	}
</script>

{#if $user?.role === 'admin' || $user?.role === 'user'}
	{#if collapsed}
		<Tooltip content={$i18n.t('API & Developers')} placement="right">
			<a
				href="/developers"
				class="group flex size-8 items-center justify-center rounded-lg transition hover:bg-gray-100 dark:hover:bg-gray-800 {active
					? 'bg-gray-100 dark:bg-gray-800'
					: ''}"
				aria-label={$i18n.t('API & Developers')}
				aria-current={active ? 'page' : undefined}
				on:click={navigate}
				draggable="false"><CodeIcon className="size-4" /></a
			>
		</Tooltip>
	{:else}
		<div class="px-1 text-gray-700 dark:text-gray-300">
			<a
				id="sidebar-developers-button"
				href="/developers"
				class="flex min-h-9 items-center gap-2 rounded-xl px-2 py-1.5 transition hover:bg-gray-100 dark:hover:bg-gray-900 {active
					? 'bg-black/[0.035] dark:bg-white/[0.045]'
					: ''}"
				aria-current={active ? 'page' : undefined}
				on:click={navigate}
				draggable="false"
				><CodeIcon className="size-4" /><span class="text-[0.8125rem] leading-5"
					>{$i18n.t('API & Developers')}</span
				></a
			>
		</div>
	{/if}
{/if}

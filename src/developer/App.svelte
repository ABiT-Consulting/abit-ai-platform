<script lang="ts">
	import { onMount, onDestroy, setContext } from 'svelte';
	import { writable } from 'svelte/store';
	import { Toaster } from 'svelte-sonner';
	import Developers from '../routes/(app)/developers/+page.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { WEBUI_NAME } from './stores';
	import { getDeveloperSession, readSessionToken } from './session';

	// Developer API descriptions and examples use their source English labels.
	// This matches the page's existing fallback strings without bundling the
	// entire application and its locale assets into a scoped module release.
	setContext('i18n', writable({ t: (label: string) => label }));
	let state: 'loading' | 'active' | 'pending' | 'error' = 'loading';
	let error = '';
	let request: AbortController | undefined;
	let disposed = false;
	const dark = document.documentElement.classList.contains('dark');

	async function checkSession() {
		request?.abort();
		request = new AbortController();
		state = 'loading';
		error = '';
		try {
			const result = await getDeveloperSession(readSessionToken(), request.signal);
			if (disposed) return;
			if (result === 'sign-in') {
				window.location.replace('/auth?redirect=%2Fdevelopers');
				return;
			}
			state = result;
		} catch (failure) {
			if (disposed || request.signal.aborted) return;
			state = 'error';
			error = failure instanceof Error ? failure.message : 'Unable to verify your session.';
		}
	}
	onMount(checkSession);
	onDestroy(() => {
		disposed = true;
		request?.abort();
	});
</script>

<main id="main-content">
	{#if state === 'active'}
		<Developers standalone />
	{:else}
		<div
			class="flex min-h-screen items-center justify-center bg-white px-5 text-gray-900 dark:bg-gray-900 dark:text-gray-100"
		>
			<div class="w-full max-w-lg rounded-2xl border border-gray-200 p-6 dark:border-gray-700">
				<h1 class="text-lg font-semibold">API &amp; Developers</h1>
				{#if state === 'loading'}
					<div class="mt-5 flex items-center gap-3 text-sm text-gray-500" role="status">
						<Spinner /><span>Checking your session…</span>
					</div>
				{:else if state === 'pending'}
					<p class="mt-4 text-sm text-gray-600 dark:text-gray-400" role="alert">
						Your account must be approved before you can view the API documentation.
					</p>
				{:else}
					<p class="mt-4 text-sm text-gray-600 dark:text-gray-400" role="alert">{error}</p>
					<button
						class="mt-4 rounded-lg border border-gray-300 px-4 py-2 text-sm dark:border-gray-700"
						type="button"
						on:click={checkSession}>Try again</button
					>
				{/if}
				<a href="/" class="mt-5 inline-flex text-sm font-medium underline">Back to {$WEBUI_NAME}</a>
			</div>
		</div>
	{/if}
</main>
<Toaster theme={dark ? 'dark' : 'light'} closeButton richColors />

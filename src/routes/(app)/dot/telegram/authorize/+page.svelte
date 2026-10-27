<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	type ConsentRequest = {
		request_id: string;
		client_name: string;
		scopes: string[];
		expires_at: number;
		account_name: string;
		account_email: string;
	};

	let details: ConsentRequest | null = null;
	let loading = true;
	let submitting = false;
	let error = '';
	let requestId = '';

	const permissions: Record<string, string> = {
		'telegram:read': 'Read messages from your linked Telegram conversation and receive new-message events.',
		'telegram:reply': 'Send replies to messages in your linked Telegram conversation.'
	};

	async function api(path: string, options: RequestInit = {}) {
		const response = await fetch(`${WEBUI_API_BASE_URL}/telegram/oauth/${path}`, {
			...options,
			headers: {
				Authorization: `Bearer ${localStorage.token}`,
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			cache: 'no-store'
		});
		const body = await response.json().catch(() => ({}));
		if (!response.ok) {
			throw new Error(typeof body.detail === 'string' ? body.detail : 'Unable to authorize this connection. Start again in ChatGPT.');
		}
		return body;
	}

	onMount(() => {
		requestId = $page.url.searchParams.get('request') || '';
		if (!/^[A-Za-z0-9_-]{43}$/.test(requestId)) {
			error = 'This authorization link is invalid. Start the connection again in ChatGPT.';
			loading = false;
			return;
		}
		void api(`requests/${encodeURIComponent(requestId)}`)
			.then((body) => { details = body; })
			.catch((cause: unknown) => {
				error = cause instanceof Error ? cause.message : 'Unable to load authorization.';
			})
			.finally(() => { loading = false; });
	});

	async function respond(approve: boolean) {
		if (submitting || !details) return;
		submitting = true;
		error = '';
		try {
			const result = await api('approve', {
				method: 'POST',
				body: JSON.stringify({ request_id: requestId, approve })
			});
			const destination = new URL(result.redirect_url);
			if (
				destination.protocol !== 'https:' || destination.host !== 'chatgpt.com' ||
				destination.username || destination.password || destination.hash ||
				!(destination.pathname === '/connector_platform_oauth_redirect' ||
					/^\/connector\/oauth\/[A-Za-z0-9_-]{1,200}$/.test(destination.pathname))
			) throw new Error('The authorization destination is invalid. Start again in ChatGPT.');
			window.location.assign(destination.href);
		} catch (cause: unknown) {
			error = cause instanceof Error ? cause.message : 'Unable to authorize this connection.';
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Connect abitdot to Telegram</title>
	<meta name="robots" content="noindex, nofollow" />
	<meta name="referrer" content="no-referrer" />
</svelte:head>

<main class="flex h-full w-full items-start justify-center overflow-y-auto px-4 py-10 sm:py-16">
	<section class="w-full max-w-lg rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900 sm:p-8" aria-labelledby="telegram-consent-title">
		<div class="mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-300" aria-hidden="true">
			<svg width="27" height="27" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m21 3-7.5 18-3.8-7.7L2 9.5 21 3Z" stroke-linejoin="round" /><path d="m21 3-11.3 10.3" /></svg>
		</div>
		<h1 id="telegram-consent-title" class="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-100">Connect abitdot to Telegram</h1>
		{#if loading}
			<p class="mt-5 text-sm text-gray-600 dark:text-gray-400" role="status">Loading connection details…</p>
		{:else if details}
			<p class="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-400">Allow {details.client_name} to access your linked Telegram conversation through ABiT AI.</p>
			<div class="mt-5 rounded-xl bg-gray-50 p-4 dark:bg-gray-850">
				<p class="text-xs font-medium uppercase tracking-wide text-gray-500">Your ABiT AI account</p>
				<p class="mt-1 text-sm font-semibold text-gray-900 dark:text-gray-100">{details.account_name}</p>
				<p class="mt-1 break-all text-sm text-gray-600 dark:text-gray-400">{details.account_email}</p>
			</div>
			<h2 class="mt-6 text-sm font-semibold text-gray-900 dark:text-gray-100">Permissions</h2>
			<ul class="mt-3 space-y-3 text-sm leading-6 text-gray-600 dark:text-gray-400">
				{#each details.scopes as scope}
					<li class="flex gap-3"><span class="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-blue-500" aria-hidden="true"></span><span>{permissions[scope] || scope}</span></li>
				{/each}
			</ul>
			<p class="mt-5 text-xs leading-5 text-gray-500 dark:text-gray-400">You can disconnect this access from abitdot → Telegram in ABiT AI. After connecting, open abitdot and enable monitoring for your Telegram messages.</p>
			<div class="mt-7 flex gap-3">
				<button type="button" class="flex-1 rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800" disabled={submitting} on:click={() => respond(false)}>Cancel</button>
				<button id="telegram-oauth-approve" type="button" class="flex-1 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50" disabled={submitting} on:click={() => respond(true)}>{submitting ? 'Returning to ChatGPT…' : 'Allow connection'}</button>
			</div>
		{/if}
		{#if error}
			<div class="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-200" role="alert">{error}</div>
		{/if}
		{#if !loading && !details}
			<a href="/dot/telegram" class="mt-6 inline-block text-sm font-medium text-blue-600 hover:underline dark:text-blue-400">Return to Telegram settings</a>
		{/if}
	</section>
</main>

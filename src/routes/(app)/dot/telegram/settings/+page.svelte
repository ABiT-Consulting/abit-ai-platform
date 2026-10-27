<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { user } from '$lib/stores';
	import {
		TelegramApiError,
		getTelegramStatus,
		getTelegramBotHealth,
		configureTelegramBot,
		type TelegramStatus,
		type TelegramBotHealth
	} from '$lib/apis/telegram';

	let status: TelegramStatus | null = null;
	let health: TelegramBotHealth | null = null;
	let loading = true;
	let busy = false;
	let botToken = '';
	let publicUrl = '';
	let webhookConflict = false;
	let replaceWebhook = false;
	let error = '';
	let healthError = '';
	let notice = '';
	let stopped = false;
	let controller: AbortController | null = null;

	const token = () => localStorage.getItem('token') || '';
	const message = (value: unknown) =>
		value instanceof Error ? value.message : 'The Telegram service could not complete this request.';

	async function load() {
		if (stopped) return;
		controller?.abort();
		const next = new AbortController();
		controller = next;
		loading = true;
		error = '';
		healthError = '';
		try {
			const result = await getTelegramStatus(token(), next.signal);
			if (stopped || next.signal.aborted) return;
			status = result;
			publicUrl = result.public_url || (location.protocol === 'https:' ? location.origin : '');
			health = null;
			if (result.configured && $user?.role === 'admin') {
				try {
					health = await getTelegramBotHealth(token(), next.signal);
				} catch (failure) {
					if (!next.signal.aborted) healthError = message(failure);
				}
			}
		} catch (failure) {
			if (!next.signal.aborted && !stopped) {
				status = null;
				error = message(failure);
			}
		} finally {
			if (controller === next) {
				loading = false;
				controller = null;
			}
		}
	}

	async function saveBot() {
		if (busy || $user?.role !== 'admin') return;
		busy = true;
		error = '';
		notice = '';
		try {
			await configureTelegramBot(token(), {
				token: botToken.trim(),
				public_url: publicUrl.trim(),
				replace_existing_webhook: webhookConflict && replaceWebhook
			});
			botToken = '';
			webhookConflict = false;
			replaceWebhook = false;
			notice = 'Telegram bot settings saved. The webhook has been registered.';
			await load();
		} catch (failure) {
			if (failure instanceof TelegramApiError && failure.status === 409 &&
				message(failure).toLowerCase().includes('another webhook')) {
				webhookConflict = true;
				replaceWebhook = false;
			}
			error = message(failure);
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		stopped = false;
		void load();
	});
	onDestroy(() => {
		stopped = true;
		controller?.abort();
		botToken = '';
	});
</script>

<svelte:head>
	<title>Telegram bot settings | ABiT AI</title>
</svelte:head>

<div class="h-full w-full overflow-y-auto text-gray-900 dark:text-gray-100">
	<div class="mx-auto max-w-3xl px-5 py-8 sm:px-8 sm:py-12">
		<div class="mb-7 flex flex-wrap items-center justify-between gap-3">
			<a href="/dot/telegram" class="text-sm font-medium text-sky-600 hover:underline dark:text-sky-400">← Telegram</a>
			<button type="button" class="secondary-button" disabled={loading || busy} on:click={() => void load()}>Refresh status</button>
		</div>
		<header class="mb-7">
			<p class="mb-2 text-xs font-bold uppercase tracking-widest text-sky-600 dark:text-sky-400">abitdot / Integration</p>
			<h1 class="text-3xl font-semibold tracking-tight">Telegram bot settings</h1>
			<p class="mt-2 text-sm leading-6 text-gray-500 dark:text-gray-400">Connect your BotFather bot to ABiT AI, register its secure webhook, and then link your personal Telegram account.</p>
		</header>

		{#if error}
			<div role="alert" class="mb-5 rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">{error}</div>
		{/if}
		{#if notice}
			<div role="status" class="mb-5 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">{notice}</div>
		{/if}

		{#if loading}
			<section class="card text-sm text-gray-500" aria-live="polite">Checking your Telegram connection…</section>
		{:else if !status}
			<section class="card">
				<h2 class="mb-2 font-semibold">Telegram service unavailable</h2>
				<p class="mb-4 text-sm leading-6 text-gray-500 dark:text-gray-400">Bot settings require the Telegram backend on the server. Retry after the service is enabled; no bot token has been saved.</p>
				<button class="primary-button" on:click={() => void load()}>Try again</button>
			</section>
		{:else if $user?.role !== 'admin'}
			<section class="card">
				<h2 class="mb-2 font-semibold">Administrator access required</h2>
				<p class="text-sm leading-6 text-gray-500 dark:text-gray-400">An ABiT AI administrator configures the bot once. Return to Telegram to link your own account when it is ready.</p>
				<a class="secondary-button mt-4" href="/dot/telegram">Back to Telegram</a>
			</section>
		{:else}
			<section class="card mb-5" aria-labelledby="bot-connect-title">
				<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
					<div>
						<p class="eyebrow">Step 1 · Configure</p>
						<h2 id="bot-connect-title" class="text-lg font-semibold">{status.configured ? 'Manage your bot' : 'Connect your bot'}</h2>
					</div>
					<span class:connected={status.configured} class="status-pill">{status.configured ? 'Configured' : 'Not configured'}</span>
				</div>
				{#if status.configured}
					<p class="mb-5 text-sm leading-6 text-gray-500 dark:text-gray-400">Current bot: <strong class="text-gray-900 dark:text-gray-100">@{status.bot_username}</strong>. Enter a fresh token only if you want to update its configuration.</p>
				{:else}
					<p class="mb-5 text-sm leading-6 text-gray-500 dark:text-gray-400">Open <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer" class="text-sky-600 underline dark:text-sky-400">@BotFather</a> in Telegram, create a bot with <code>/newbot</code>, and copy the API token.</p>
				{/if}
				<form class="space-y-5" on:submit|preventDefault={saveBot}>
					<div>
						<label class="field-label" for="bot-token">Telegram bot token</label>
						<input id="bot-token" name="bot-token" type="password" class="field" bind:value={botToken} autocomplete="new-password" spellcheck="false" required disabled={busy} placeholder="Paste the BotFather token" />
						<p class="mt-2 text-xs leading-5 text-gray-500">The token is encrypted on the server and is never shown again. Do not share it in a chat.</p>
					</div>
					<div>
						<label class="field-label" for="bot-public-url">Public ABiT AI URL</label>
						<input id="bot-public-url" name="bot-public-url" type="url" class="field" bind:value={publicUrl} placeholder="https://abit.ai" pattern="https://.*" required disabled={busy} />
						<p class="mt-2 text-xs leading-5 text-gray-500">Must be a public HTTPS origin reachable by Telegram.</p>
					</div>
					{#if webhookConflict}
						<label class="flex gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
							<input type="checkbox" bind:checked={replaceWebhook} disabled={busy} class="mt-1" />
							<span>This bot currently delivers messages to another service. Replace its webhook with ABiT AI.</span>
						</label>
					{/if}
					<button class="primary-button" type="submit" disabled={busy || !botToken.trim() || !publicUrl.trim() || (webhookConflict && !replaceWebhook)}>
						{busy ? 'Connecting…' : status.configured ? 'Save bot settings' : 'Connect bot'}
					</button>
				</form>
			</section>

			<section class="card mb-5" aria-labelledby="webhook-title">
				<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
					<div>
						<p class="eyebrow">Step 2 · Verify</p>
						<h2 id="webhook-title" class="text-lg font-semibold">Telegram delivery status</h2>
					</div>
					{#if health}
						<span class:connected={health.webhook_matches} class="status-pill">{health.webhook_matches ? 'Webhook registered' : 'Needs attention'}</span>
					{/if}
				</div>
				{#if !status.configured}
					<p class="text-sm leading-6 text-gray-500 dark:text-gray-400">Configure your bot above to enable webhook verification.</p>
				{:else if healthError}
					<p role="alert" class="text-sm leading-6 text-amber-700 dark:text-amber-400">{healthError}</p>
				{:else if health}
					<p class="text-sm leading-6 text-gray-500 dark:text-gray-400">{health.webhook_matches ? 'Telegram reports the correct ABiT AI webhook.' : 'Telegram does not report the expected webhook. Save the bot settings to register it again.'}</p>
					<p class="mt-3 text-sm text-gray-500">Pending Telegram updates: <strong>{health.pending_update_count}</strong></p>
					{#if health.last_error_date}
						<p class="mt-2 text-sm text-amber-700 dark:text-amber-400">Telegram reported a delivery issue on {new Date(health.last_error_date * 1000).toLocaleString()}. Check that the HTTPS tunnel and backend route are reachable.</p>
					{/if}
				{:else}
					<p class="text-sm text-gray-500">Webhook status has not been checked yet.</p>
				{/if}
			</section>

			<section class="card">
				<p class="eyebrow">Step 3 · Start chatting</p>
				<h2 class="mb-3 text-lg font-semibold">Link Telegram and connect abitdot</h2>
				<p class="mb-4 text-sm leading-6 text-gray-500 dark:text-gray-400">Generate a QR code, confirm your Telegram identity, then authorize the Telegram Bridge connector in ChatGPT and subscribe abitdot to messages. A configured bot alone does not activate the dot reply connection.</p>
				<a class="primary-button" href="/dot/telegram">Continue to QR linking</a>
			</section>
		{/if}
	</div>
</div>

<style>
	.card { border: 1px solid rgb(229 231 235); border-radius: 1rem; padding: 1.5rem; }
	.eyebrow { margin-bottom: .4rem; font-size: .65rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: rgb(107 114 128); }
	.field-label { display: block; margin-bottom: .5rem; font-size: .8rem; font-weight: 600; }
	.field { width: 100%; border: 1px solid rgb(209 213 219); border-radius: .65rem; background: transparent; padding: .65rem .75rem; outline: none; }
	.field:focus { border-color: rgb(2 132 199); box-shadow: 0 0 0 2px rgb(14 165 233 / .15); }
	.primary-button, .secondary-button { display: inline-flex; align-items: center; justify-content: center; border-radius: .65rem; padding: .6rem .85rem; font-size: .8rem; font-weight: 600; line-height: 1.25rem; }
	.primary-button { background: rgb(2 132 199); color: white; }
	.primary-button:hover { background: rgb(3 105 161); }
	.secondary-button { border: 1px solid rgb(209 213 219); }
	.secondary-button:hover { background: rgb(243 244 246); }
	button:disabled, input:disabled { cursor: not-allowed; opacity: .5; }
	.status-pill { display: inline-flex; border-radius: 9999px; background: rgb(243 244 246); padding: .3rem .6rem; color: rgb(107 114 128); font-size: .7rem; font-weight: 600; }
	.status-pill.connected { background: rgb(236 253 245); color: rgb(4 120 87); }
	:global(.dark) .card { border-color: rgb(55 65 81); }
	:global(.dark) .field, :global(.dark) .secondary-button { border-color: rgb(75 85 99); }
	:global(.dark) .secondary-button:hover, :global(.dark) .status-pill { background: rgb(31 41 55); }
	:global(.dark) .status-pill { color: rgb(156 163 175); }
	:global(.dark) .status-pill.connected { background: rgb(6 78 59 / .4); color: rgb(110 231 183); }
	@media (max-width: 640px) { .card { padding: 1.1rem; } }
</style>

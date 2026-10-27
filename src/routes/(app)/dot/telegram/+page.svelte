<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { user } from '$lib/stores';
	import {
		getTelegramStatus,
		getTelegramMessages,
		createTelegramPairing,
		confirmTelegramPairing,
		cancelTelegramPairing,
		unlinkTelegram,
		disconnectTelegramDot,
		type TelegramStatus,
		type TelegramMessage,
		type TelegramPairingResult
	} from '$lib/apis/telegram';

	const setupPrompt = 'Monitor telegram.message_received from the ABiT Telegram Bridge for my linked account. Treat each text as my message, process it using your existing context and permissions, and return your response with reply_to_telegram_message using its message_id. Check recent messages when context is needed. Do not send proactive Telegram messages beyond replies unless I ask.';

	let status: TelegramStatus | null = null;
	let messages: TelegramMessage[] = [];
	let pairingView: TelegramPairingResult | null = null;
	let loading = true;
	let refreshing = false;
	let busy = false;
	let error = '';
	let notice = '';
	let disconnectChoice: 'telegram' | 'dot' | null = null;
	let now = Date.now();
	let lastPoll = 0;
	let destroyed = false;
	let pollController: AbortController | null = null;
	let actionController: AbortController | null = null;
	let timer: ReturnType<typeof setInterval> | undefined;
	let refreshVersion = 0;

	const asMilliseconds = (value: number) => value < 1e12 ? value * 1000 : value;
	const formatDate = (value: number) => new Date(asMilliseconds(value)).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
	const authToken = () => localStorage.getItem('token') || '';
	const isAbort = (value: unknown) => value instanceof Error && value.name === 'AbortError';
	const errorMessage = (value: unknown) => value instanceof Error ? value.message : 'The connection could not be updated. Please try again.';

	$: activePairing = status?.pairing ?? null;
	$: secondsLeft = activePairing ? Math.max(0, Math.ceil((asMilliseconds(activePairing.expires_at) - now) / 1000)) : 0;
	$: countdown = `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, '0')}`;
	$: visibleQr = pairingView?.id === activePairing?.id && secondsLeft > 0 && activePairing?.status === 'pending' ? pairingView : null;
	$: safeDeepLink = visibleQr && /^https:\/\/t\.me\/[A-Za-z0-9_]+\?start=[A-Za-z0-9_-]{1,64}$/.test(visibleQr.deep_link) ? visibleQr.deep_link : null;
	$: safeQr = visibleQr && /^data:image\/svg\+xml(?:;charset=utf-8)?(?:;base64)?,/.test(visibleQr.qr_data_url) ? visibleQr.qr_data_url : null;

	async function refresh() {
		if (destroyed || refreshing || busy) return;
		refreshing = true;
		lastPoll = Date.now();
		const version = ++refreshVersion;
		const controller = new AbortController();
		pollController = controller;
		try {
			const next = await getTelegramStatus(authToken(), controller.signal);
			if (destroyed || version !== refreshVersion) return;
			status = next;
			error = '';
			if (next.linked || next.pairing?.id !== pairingView?.id) pairingView = null;
			const transcript = await getTelegramMessages(authToken(), controller.signal);
			if (!destroyed && version === refreshVersion) messages = transcript.messages;
		} catch (failure) {
			if (!destroyed && !isAbort(failure) && version === refreshVersion) error = errorMessage(failure);
		} finally {
			if (version === refreshVersion) {
				refreshing = false;
				loading = false;
				pollController = null;
			}
		}
	}

	async function runAction(action: (signal: AbortSignal) => Promise<void>) {
		if (busy || destroyed) return;
		busy = true;
		error = '';
		notice = '';
		refreshVersion += 1;
		pollController?.abort();
		refreshing = false;
		const controller = new AbortController();
		actionController = controller;
		try {
			await action(controller.signal);
		} catch (failure) {
			if (!destroyed && !isAbort(failure)) error = errorMessage(failure);
		} finally {
			busy = false;
			actionController = null;
			if (!destroyed) await refresh();
		}
	}

	function generatePairing() {
		void runAction(async (signal) => {
			const result = await createTelegramPairing(authToken(), signal);
			if (destroyed) return;
			pairingView = result;
			if (status) status = { ...status, pairing: result };
			now = Date.now();
		});
	}

	function confirmPairing() {
		const id = activePairing?.id;
		if (!id || secondsLeft <= 0) return;
		void runAction(async (signal) => {
			await confirmTelegramPairing(authToken(), id, signal);
			pairingView = null;
			notice = 'Telegram account linked.';
		});
	}

	function cancelPairing() {
		const id = activePairing?.id;
		if (!id) return;
		void runAction(async (signal) => {
			await cancelTelegramPairing(authToken(), id, signal);
			pairingView = null;
			notice = 'Link request cancelled.';
		});
	}

	function disconnect() {
		const choice = disconnectChoice;
		if (!choice) return;
		void runAction(async (signal) => {
			if (choice === 'telegram') await unlinkTelegram(authToken(), signal);
			else await disconnectTelegramDot(authToken(), signal);
			disconnectChoice = null;
			pairingView = null;
			notice = choice === 'telegram' ? 'Telegram account disconnected.' : 'Dot connector access revoked.';
		});
	}

	async function copyText(value: string, label: string) {
		try {
			await navigator.clipboard.writeText(value);
			notice = `${label} copied.`;
		} catch {
			error = 'Copy is unavailable. Select and copy the text shown below.';
		}
	}

	onMount(() => {
		destroyed = false;
		void refresh();
		timer = setInterval(() => {
			now = Date.now();
			const interval = status?.pairing && !status.linked ? 3000 : 10000;
			if (document.visibilityState === 'visible' && now - lastPoll >= interval) void refresh();
		}, 1000);
	});

	onDestroy(() => {
		destroyed = true;
		refreshVersion += 1;
		pollController?.abort();
		actionController?.abort();
		if (timer) clearInterval(timer);
		pairingView = null;
	});
</script>

<svelte:head><title>Telegram · abitdot</title></svelte:head>

<div class="telegram-page h-full w-full overflow-y-auto text-gray-900 dark:text-gray-100">
	<div class="mx-auto max-w-5xl px-5 py-8 sm:px-8 sm:py-12">
		<header class="mb-8 flex items-start justify-between gap-4">
			<div>
				<div class="mb-3 flex items-center gap-2 text-sm font-medium text-sky-600 dark:text-sky-400">
					<svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m21 3-7 18-4-7-7-4L21 3Z" stroke-linecap="round" stroke-linejoin="round"/><path d="m10 14 5-5" stroke-linecap="round"/></svg>
					<span>abitdot</span>
				</div>
				<h1 class="text-3xl font-semibold tracking-tight">Telegram</h1>
				<p class="mt-2 max-w-xl text-sm leading-6 text-gray-500 dark:text-gray-400">Link your Telegram account, connect abitdot, and keep the conversation going from your bot.</p>
			</div>
			<button class="secondary-button mt-1 shrink-0 whitespace-nowrap" disabled={busy || refreshing} on:click={() => { error = ''; void refresh(); }} aria-label="Refresh Telegram connection">{refreshing ? 'Refreshing…' : 'Refresh'}</button>
		</header>

		{#if error}<div class="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200" role="alert">{error}</div>{/if}
		{#if notice}<div class="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200" role="status">{notice}</div>{/if}

		{#if loading}
			<div class="card py-12 text-center text-sm text-gray-500" aria-live="polite">Loading your connection…</div>
		{:else if status}
			{#if status.configured}
				<div class="mb-6 flex flex-wrap items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
					<span class="h-2 w-2 rounded-full bg-emerald-500" aria-hidden="true"></span>
					<span>Telegram bot configured</span>
					{#if status.bot_username}<span class="font-medium text-gray-700 dark:text-gray-200">@{status.bot_username}</span>{/if}
					{#if $user?.role === 'admin'}<a class="secondary-button ml-auto" href="/dot/telegram/settings">Bot settings</a>{/if}
				</div>
			{:else}
				<section class="card mb-6" aria-labelledby="telegram-setup-heading">
					<h2 id="telegram-setup-heading" class="section-title mb-2">Connect a Telegram bot first</h2>
					<p class="mb-4 text-sm leading-6 text-gray-500 dark:text-gray-400">An administrator needs to register the bot with BotFather and configure its secure webhook before users can link their Telegram accounts.</p>
					{#if $user?.role === 'admin'}<a class="primary-button" href="/dot/telegram/settings">Open bot settings</a>{:else}<p class="text-sm text-gray-500">Ask an abit.ai administrator to connect the bot.</p>{/if}
				</section>
			{/if}

			<div class="grid items-start gap-6 lg:grid-cols-2">
				<section class="card" aria-labelledby="account-heading">
					<p class="eyebrow">Step 1</p><div class="mb-5 flex flex-wrap items-center justify-between gap-2"><h2 id="account-heading" class="section-title">Link your Telegram</h2><span class:connected={status.linked} class="status-pill">{status.linked ? 'Linked' : 'Not linked'}</span></div>
					{#if status.linked && status.binding}
						<div class="rounded-xl bg-gray-50 p-4 dark:bg-gray-900"><p class="font-medium">{status.binding.display_name || 'Telegram account'}</p>{#if status.binding.telegram_username}<p class="mt-1 text-sm text-gray-500">@{status.binding.telegram_username}</p>{/if}<p class="mt-3 text-xs text-gray-500">Linked {formatDate(status.binding.linked_at)}</p></div>
						<p class="my-4 text-sm leading-6 text-gray-500 dark:text-gray-400">Messages sent privately to your bot are associated with this abit.ai account.</p>
						<button class="secondary-button" disabled={busy} on:click={() => disconnectChoice = 'telegram'}>Disconnect Telegram</button>
					{:else if activePairing && secondsLeft > 0 && activePairing.status === 'claimed'}
						<div class="rounded-xl border border-sky-200 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950/30"><p class="mb-2 text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300">Confirm this account</p><p class="font-semibold">{activePairing.display_name || 'Telegram account'}</p>{#if activePairing.telegram_username}<p class="mt-1 text-sm text-gray-600 dark:text-gray-300">@{activePairing.telegram_username}</p>{/if}</div>
						<p class="my-4 text-sm leading-6 text-gray-500 dark:text-gray-400">Only confirm if this is your Telegram account. The link request expires in {countdown}.</p>
						<div class="flex flex-wrap gap-2"><button class="primary-button" disabled={busy} on:click={confirmPairing}>Confirm linking</button><button class="secondary-button" disabled={busy} on:click={cancelPairing}>Cancel</button></div>
					{:else if visibleQr && safeQr}
						<div class="mx-auto w-fit max-w-full rounded-2xl border border-gray-200 bg-white p-4"><img src={safeQr} alt="Scan this QR code to open your Telegram bot and start account linking" width="224" height="224" class="block h-auto w-56 max-w-full" /></div>
						<ol class="my-5 list-decimal space-y-2 pl-5 text-sm leading-6 text-gray-600 dark:text-gray-300"><li>Scan with your phone’s camera.</li><li>Tap <strong>Start</strong> in Telegram.</li><li>Return here and confirm your Telegram identity.</li></ol>
						<div class="flex flex-wrap items-center gap-3">{#if safeDeepLink}<a class="primary-button" href={safeDeepLink} target="_blank" rel="noopener noreferrer">Open Telegram</a>{/if}<button class="secondary-button" disabled={busy} on:click={cancelPairing}>Cancel</button><span class="text-xs text-gray-500">Expires in {countdown}</span></div>
					{:else}
						<p class="mb-5 text-sm leading-6 text-gray-500 dark:text-gray-400">{activePairing && secondsLeft <= 0 ? 'This link request has expired. Generate a new QR code to continue.' : activePairing ? 'A link request is waiting. Complete it in Telegram, or generate a new QR code.' : 'Generate a QR code, scan it on your phone, and confirm the Telegram account you want to use.'}</p>
						<button class="primary-button" disabled={busy || !status.configured} on:click={generatePairing}>{busy ? 'Please wait…' : activePairing ? 'Generate new QR code' : 'Generate QR code'}</button>
					{/if}
				</section>

				<section class="card" aria-labelledby="dot-heading">
					<p class="eyebrow">Step 2</p><div class="mb-5 flex flex-wrap items-center justify-between gap-2"><h2 id="dot-heading" class="section-title">Connect abitdot</h2><span class:connected={status.dot_subscription_active} class="status-pill">{status.dot_subscription_active ? 'Subscription active' : status.dot_connected ? 'Authorized' : 'Not connected'}</span></div>
					<p class="mb-4 text-sm leading-6 text-gray-500 dark:text-gray-400">Add the ABiT Telegram Bridge to your ChatGPT connectors using the address below, authorize this abit.ai account, then give abitdot the setup prompt.</p>
					{#if status.mcp_url}
						<label class="field-label" for="telegram-mcp-url">Connector URL</label><div class="flex gap-2"><input id="telegram-mcp-url" class="field min-w-0 flex-1 text-xs" value={status.mcp_url} readonly /><button class="secondary-button shrink-0" on:click={() => copyText(status?.mcp_url || '', 'Connector URL')}>Copy</button></div>
					{:else}<p class="rounded-xl bg-gray-50 p-3 text-sm text-gray-500 dark:bg-gray-900">The connector address will appear after the bot is configured.</p>{/if}
					<div class="mt-5"><div class="mb-2 flex items-center justify-between gap-2"><label class="field-label mb-0" for="telegram-setup-prompt">Message to send to abitdot</label><button class="text-xs font-semibold text-sky-700 hover:underline dark:text-sky-400" on:click={() => copyText(setupPrompt, 'Setup prompt')}>Copy prompt</button></div><textarea id="telegram-setup-prompt" class="field resize-none text-xs leading-5" rows="7" readonly value={setupPrompt}></textarea></div>
					{#if status.dot_subscription_active}<p class="mt-4 text-sm leading-6 text-emerald-700 dark:text-emerald-400">An incoming-message subscription is active. Send a message to your bot to check the full conversation.</p>{:else if status.dot_connected}<p class="mt-4 text-sm leading-6 text-amber-700 dark:text-amber-400">The connector is authorized. Ask abitdot to monitor incoming messages using the setup prompt.</p>{/if}
					{#if status.dot_connected || status.dot_subscription_active}<button class="secondary-button mt-4" disabled={busy} on:click={() => disconnectChoice = 'dot'}>Disconnect dot connector</button>{/if}
				</section>
			</div>

			{#if disconnectChoice}
				<section class="card mt-6 border-amber-200 dark:border-amber-900" role="alert" aria-labelledby="disconnect-heading"><h2 id="disconnect-heading" class="font-semibold">{disconnectChoice === 'telegram' ? 'Disconnect your Telegram account?' : 'Revoke dot connector access?'}</h2><p class="my-3 text-sm text-gray-500 dark:text-gray-400">{disconnectChoice === 'telegram' ? 'Your Telegram account will stop receiving replies through this connection. You can link it again with a new QR code.' : 'The connector and its incoming-message subscriptions will be revoked. Authorize it again to resume.'}</p><div class="flex gap-2"><button class="primary-button" disabled={busy} on:click={disconnect}>Disconnect</button><button class="secondary-button" disabled={busy} on:click={() => disconnectChoice = null}>Keep connected</button></div></section>
			{/if}

			<section class="card mt-6" aria-labelledby="messages-heading">
				<div class="mb-5 flex items-center justify-between gap-3"><div><p class="eyebrow">Conversation</p><h2 id="messages-heading" class="section-title">Recent messages</h2></div><span class="text-xs text-gray-500">Latest 30</span></div>
				{#if messages.length === 0}<div class="rounded-xl bg-gray-50 px-5 py-8 text-center text-sm leading-6 text-gray-500 dark:bg-gray-900">Your Telegram messages and replies will appear here.</div>
				{:else}<div class="space-y-5">{#each messages as message (message.id)}<article class="border-b border-gray-100 pb-5 last:border-b-0 last:pb-0 dark:border-gray-800"><div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500"><span>Telegram · {formatDate(message.created_at)}</span><span class="rounded-full bg-gray-100 px-2 py-1 dark:bg-gray-800">{message.status.replace(/_/g, ' ')}</span></div><p class="whitespace-pre-wrap break-words text-sm leading-6">{message.text}</p>{#if message.response_text}<div class="mt-3 rounded-xl bg-sky-50 px-4 py-3 dark:bg-sky-950/30"><p class="mb-1 text-xs font-semibold text-sky-700 dark:text-sky-400">abitdot reply</p><p class="whitespace-pre-wrap break-words text-sm leading-6">{message.response_text}</p></div>{/if}{#if message.error}<p class="mt-2 text-xs leading-5 text-red-700 dark:text-red-400">{message.error}</p>{/if}</article>{/each}</div>{/if}
			</section>
		{:else}
			<section class="card" role="alert">
				<h2 class="section-title mb-2">Telegram connection unavailable</h2>
				<p class="mb-4 text-sm leading-6 text-gray-500 dark:text-gray-400">The Telegram API is not responding correctly. The server must have the Telegram backend enabled before bot setup or account linking can work.</p>
				<div class="flex flex-wrap gap-2"><button class="primary-button" on:click={() => void refresh()} disabled={refreshing}>Retry connection</button>{#if $user?.role === 'admin'}<a class="secondary-button" href="/dot/telegram/settings">Bot settings</a>{/if}</div>
			</section>
		{/if}
	</div>
</div>

<style>
	.card { border: 1px solid rgb(229 231 235); border-radius: 1rem; padding: 1.5rem; }
	.eyebrow { margin-bottom: .4rem; font-size: .65rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: rgb(107 114 128); }
	.section-title { font-size: 1.1rem; font-weight: 650; letter-spacing: -.015em; }
	.field-label { display: block; margin-bottom: .5rem; font-size: .8rem; font-weight: 600; }
	.field { width: 100%; border: 1px solid rgb(209 213 219); border-radius: .65rem; background: transparent; padding: .65rem .75rem; outline: none; }
	.field:focus { border-color: rgb(2 132 199); box-shadow: 0 0 0 2px rgb(14 165 233 / .15); }
	.primary-button, .secondary-button { display: inline-flex; align-items: center; justify-content: center; border-radius: .65rem; padding: .6rem .85rem; font-size: .8rem; font-weight: 600; line-height: 1.25rem; transition: background .15s; }
	.primary-button { background: rgb(2 132 199); color: white; border: 1px solid transparent; }
	.primary-button:hover { background: rgb(3 105 161); }
	.secondary-button { border: 1px solid rgb(209 213 219); }
	.secondary-button:hover { background: rgb(243 244 246); }
	button:disabled, input:disabled { cursor: not-allowed; opacity: .5; }
	button:focus-visible, a:focus-visible { outline: 2px solid rgb(14 165 233); outline-offset: 3px; }
	.status-pill { display: inline-flex; border-radius: 9999px; background: rgb(243 244 246); padding: .3rem .55rem; color: rgb(107 114 128); font-size: .65rem; font-weight: 600; }
	.status-pill.connected { background: rgb(236 253 245); color: rgb(4 120 87); }
	:global(.dark) .card { border-color: rgb(55 65 81); }
	:global(.dark) .field, :global(.dark) .secondary-button { border-color: rgb(75 85 99); }
	:global(.dark) .secondary-button:hover, :global(.dark) .status-pill { background: rgb(31 41 55); }
	:global(.dark) .status-pill { color: rgb(156 163 175); }
	:global(.dark) .status-pill.connected { background: rgb(6 78 59 / .4); color: rgb(110 231 183); }
	@media (max-width: 640px) { .card { padding: 1.1rem; } }
</style>

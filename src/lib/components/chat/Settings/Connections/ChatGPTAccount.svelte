<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { user } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	let connected = false;
	let accountHint: string | null = null;
	let linking = false;
	let loading = false;
	let refreshing = false;
	let image = '';
	let imageWidth = 1280;
	let imageHeight = 720;
	let secretInput = '';
	let showInput = false;
	let error = '';
	let initialized = false;
	let destroyed = false;
	let flowVersion = 0;
	let otherAdministrator = false;
	let syncing = false;
	let poll: ReturnType<typeof setInterval> | undefined;

	const request = async (path: string, body?: Record<string, unknown>) => {
		const response = await fetch(WEBUI_API_BASE_URL + '/chatgpt-relay/' + path, {
			method: body ? 'POST' : 'GET',
			cache: 'no-store',
			headers: {
				Authorization: 'Bearer ' + localStorage.token,
				...(body ? { 'Content-Type': 'application/json' } : {})
			},
			...(body ? { body: JSON.stringify(body) } : {})
		});
		const data = await response.json().catch(() => ({}));
		if (!response.ok)
			throw Object.assign(new Error(data.detail || data.message || 'ChatGPT connection failed'), {
				status: response.status
			});
		return data;
	};

	const loadStatus = async () => {
		try {
			const result = await request('account/status');
			connected = result.connected === true;
			accountHint = result.account_hint || null;
			otherAdministrator = result.linking && !result.owned;
			error = otherAdministrator ? 'Another administrator is changing the shared account.' : '';
			if (result.linking && result.owned && !destroyed) {
				linking = true;
				await refreshSignIn();
			}
		} catch (e) {
			error = e instanceof Error ? e.message : 'Unable to check ChatGPT account';
		} finally {
			initialized = true;
		}
	};

	const releaseOnClose = () => {
		void fetch(WEBUI_API_BASE_URL + '/chatgpt-relay/account/relink/cancel', {
			method: 'POST',
			keepalive: true,
			cache: 'no-store',
			headers: {
				Authorization: 'Bearer ' + localStorage.token,
				'Content-Type': 'application/json'
			},
			body: '{}'
		}).catch(() => undefined);
	};

	const syncProjects = async () => {
		if (syncing) return;
		syncing = true;
		try {
			const result = await request('sync', {});
			if (!destroyed) {
				error = '';
				toast.success('Synced ' + result.synced + ' ChatGPT projects');
				window.dispatchEvent(new Event('aibit-chatgpt-account-switched'));
			}
		} catch (e) {
			if (!destroyed)
				error =
					'Account connected; synchronization needs retry: ' +
					(e instanceof Error ? e.message : 'Unknown error');
		} finally {
			syncing = false;
		}
	};

	const refreshSignIn = async () => {
		if (!linking || refreshing || destroyed) return;
		const currentFlow = flowVersion;
		refreshing = true;
		try {
			const result = await request('account/relink/view', {});
			if (destroyed || currentFlow !== flowVersion) return;
			if (result.connected) {
				linking = false;
				image = '';
				secretInput = '';
				connected = true;
				accountHint = result.account_hint || null;
				toast.success('ChatGPT account linked');
				error = '';
				await syncProjects();
			} else {
				image = result.image || '';
				imageWidth = result.width || 1280;
				imageHeight = result.height || 720;
				error = '';
			}
		} catch (e) {
			if (destroyed || currentFlow !== flowVersion) return;
			if ((e as { status?: number })?.status === 403) {
				linking = false;
				image = '';
				secretInput = '';
			}
			error = e instanceof Error ? e.message : 'Could not display ChatGPT sign-in';
		} finally {
			refreshing = false;
		}
	};

	const beginLink = async () => {
		if (loading) return;
		if (
			!confirm(
				'Switch the shared ChatGPT account used by ABIT.ai? Finish active ChatGPT requests first. Signing out interrupts the current connection. Existing ABIT projects and chats will be preserved.'
			)
		)
			return;
		loading = true;
		error = '';
		try {
			await request('account/relink/start', {});
			if (destroyed) {
				releaseOnClose();
				return;
			}
			flowVersion += 1;
			linking = true;
			await refreshSignIn();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not begin account change';
		} finally {
			loading = false;
		}
	};

	const sendAction = async (action: Record<string, unknown>) => {
		if (!linking || loading) return;
		loading = true;
		error = '';
		secretInput = '';
		try {
			await request('account/relink/input', action);
			await new Promise((resolve) => setTimeout(resolve, 450));
			await refreshSignIn();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Sign-in action failed';
		} finally {
			loading = false;
		}
	};

	const clickScreen = (event: MouseEvent) => {
		const img = (event.currentTarget as HTMLElement).querySelector('img');
		if (!img || !linking) return;
		const rect = img.getBoundingClientRect();
		const x = Math.max(
			0,
			Math.min(imageWidth, ((event.clientX - rect.left) / rect.width) * imageWidth)
		);
		const y = Math.max(
			0,
			Math.min(imageHeight, ((event.clientY - rect.top) / rect.height) * imageHeight)
		);
		void sendAction({ kind: 'click', x, y });
	};

	const stopLink = async () => {
		flowVersion += 1;
		loading = true;
		try {
			await request('account/relink/cancel', {});
			linking = false;
			image = '';
			secretInput = '';
			await loadStatus();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not close sign-in';
		} finally {
			loading = false;
		}
	};

	onMount(() => {
		if ($user?.role === 'admin') void loadStatus();
		poll = setInterval(() => {
			if (linking && !loading) void refreshSignIn();
		}, 3500);
		return () => {
			destroyed = true;
			flowVersion += 1;
			if (poll) clearInterval(poll);
			secretInput = '';
			image = '';
			if (linking) releaseOnClose();
		};
	});
</script>

{#if $user?.role === 'admin'}
	<section
		class="mt-5 border-t border-gray-200 pt-4 dark:border-gray-700"
		aria-labelledby="chatgpt-account-title"
	>
		<div class="flex flex-wrap items-center justify-between gap-3">
			<div>
				<h3 id="chatgpt-account-title" class="text-sm font-medium text-gray-900 dark:text-gray-100">
					Linked ChatGPT account
				</h3>
				<p class="mt-1 text-xs text-gray-600 dark:text-gray-400">
					{#if !initialized}Checking connection…{:else if connected}Connected · Account ending {accountHint}{:else}Not
						connected{/if}
				</p>
			</div>
			{#if !linking}
				<button
					type="button"
					class="rounded-lg border border-gray-300 px-3 py-2 text-xs font-medium text-gray-900 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-100 dark:hover:bg-gray-800"
					disabled={loading || otherAdministrator || syncing}
					on:click={beginLink}
				>
					{connected ? 'Change ChatGPT account' : 'Connect ChatGPT account'}
				</button>
			{/if}
		</div>
		{#if !linking}
			<div class="mt-2 flex gap-3 text-xs">
				<button
					type="button"
					class="text-gray-700 underline dark:text-gray-300"
					disabled={loading || syncing}
					on:click={loadStatus}>Refresh status</button
				>
				{#if connected}<button
						type="button"
						class="text-gray-700 underline dark:text-gray-300"
						disabled={syncing}
						on:click={syncProjects}>{syncing ? 'Syncing projects…' : 'Sync projects'}</button
					>{/if}
			</div>
		{/if}
		<p class="mt-2 text-xs leading-relaxed text-gray-600 dark:text-gray-400">
			Sign in through the secure ChatGPT browser below. ABIT.ai does not save your ChatGPT password.
			Account changes affect the shared ChatGPT Relay connection.
		</p>

		{#if linking}
			<div class="mt-4 space-y-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<p class="text-xs text-gray-700 dark:text-gray-200">
						Select your ChatGPT profile, log out, then log into the new account.
					</p>
					<div class="flex gap-2">
						<button
							type="button"
							class="rounded-md border border-gray-300 px-2 py-1 text-xs dark:border-gray-600"
							disabled={loading || refreshing}
							on:click={refreshSignIn}
						>
							Refresh view
						</button>
						<button
							type="button"
							class="rounded-md border border-gray-300 px-2 py-1 text-xs dark:border-gray-600"
							disabled={loading}
							on:click={stopLink}
						>
							Stop
						</button>
					</div>
				</div>
				{#if image}
					<button
						type="button"
						class="block w-full cursor-crosshair overflow-hidden rounded-lg border border-gray-300 bg-white text-left dark:border-gray-600"
						aria-label="Interact with the ChatGPT login browser"
						disabled={loading}
						on:click={clickScreen}
					>
						<img
							src={image}
							alt="Live ChatGPT sign-in browser; click the location you want to select"
							class="block h-auto w-full"
							draggable="false"
						/>
					</button>
				{:else}
					<p class="py-6 text-center text-xs text-gray-600 dark:text-gray-400">
						Loading secure sign-in browser…
					</p>
				{/if}
				<label class="block space-y-1">
					<span class="text-xs font-medium text-gray-700 dark:text-gray-300">
						Text for selected ChatGPT input field
					</span>
					<div class="flex gap-2">
						<input
							bind:value={secretInput}
							type={showInput ? 'text' : 'password'}
							autocomplete="off"
							on:keydown={(event) => {
								if (event.key === 'Enter') {
									event.preventDefault();
									void sendAction({ kind: 'text', text: secretInput });
								}
							}}
							placeholder="Email, password or verification code"
							class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-hidden focus:border-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
						/>
						<button
							type="button"
							class="rounded-lg bg-gray-900 px-3 py-2 text-xs text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
							disabled={!secretInput || loading}
							on:click={() => sendAction({ kind: 'text', text: secretInput })}
						>
							Type
						</button>
					</div>
				</label>
				<label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400">
					<input type="checkbox" bind:checked={showInput} />
					Show typed text
				</label>
				<div class="flex flex-wrap gap-2">
					{#each ['Enter', 'Tab', 'Backspace', 'Ctrl+A', 'Escape'] as key}
						<button
							type="button"
							class="rounded-md border border-gray-300 px-3 py-1 text-xs text-gray-800 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
							disabled={loading}
							on:click={() => sendAction({ kind: 'key', key })}
						>
							{key}
						</button>
					{/each}
					<button
						type="button"
						class="rounded-md border border-gray-300 px-3 py-1 text-xs text-gray-800 dark:border-gray-600 dark:text-gray-200"
						disabled={loading}
						on:click={() =>
							sendAction({ kind: 'scroll', x: imageWidth / 2, y: imageHeight / 2, direction: -1 })}
						>Scroll up</button
					>
					<button
						type="button"
						class="rounded-md border border-gray-300 px-3 py-1 text-xs text-gray-800 dark:border-gray-600 dark:text-gray-200"
						disabled={loading}
						on:click={() =>
							sendAction({ kind: 'scroll', x: imageWidth / 2, y: imageHeight / 2, direction: 1 })}
					>
						Scroll down
					</button>
				</div>
			</div>
		{/if}
		{#if error}
			<p class="mt-2 text-xs text-red-700 dark:text-red-300" role="alert">{error}</p>
		{/if}
	</section>
{/if}

<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_NAME, mobile, showSidebar, showSettings } from '$lib/stores';
	import {
		getDeveloperToken,
		getDeveloperMetadata,
		getDeveloperSchema,
		getDeveloperPostman,
		type DeveloperMetadata
	} from '$lib/apis/developer';
	import {
		listOperations,
		resolveSchema,
		schemaType,
		generateCodeExample,
		type ApiOperation,
		type OpenApiSchema
	} from '$lib/utils/developer';
	import { copyToClipboard } from '$lib/utils';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import CodeIcon from '$lib/components/layout/Sidebar/icons/Code.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SchemaViewer from '$lib/components/developer/SchemaViewer.svelte';

	export let standalone = false;
	const i18n: any = getContext('i18n');
	let metadata: DeveloperMetadata | null = null;
	let apiSchema: OpenApiSchema | null = null;
	let loading = true;
	let error = '';
	let disposed = false;
	let baseUrl = '';
	let tab: 'reference' | 'authentication' | 'guides' = 'reference';
	let query = '';
	let group = '';
	let method = '';
	let selectedId = '';
	let language: 'curl' | 'python' | 'javascript' = 'curl';
	let downloading = '';
	let operations: ApiOperation[] = [];
	const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'];
	const languages = [
		{ id: 'curl', label: 'cURL' },
		{ id: 'python', label: 'Python' },
		{ id: 'javascript', label: 'JavaScript' }
	] as const;

	$: operations = apiSchema ? listOperations(apiSchema) : [];
	$: groups = Array.from(new Set(operations.map((operation) => operation.group))).sort();
	$: filtered = operations.filter((operation) => {
		const needle = query.trim().toLowerCase();
		return (
			(!group || operation.group === group) &&
			(!method || operation.method === method) &&
			(!needle ||
				[
					operation.path,
					operation.method,
					operation.summary,
					operation.group,
					operation.operation.description ?? ''
				]
					.join(' ')
					.toLowerCase()
					.includes(needle))
		);
	});
	$: selected = filtered.find((operation) => operation.id === selectedId) ?? filtered[0] ?? null;
	$: requestBody =
		selected && apiSchema ? resolveSchema(selected.operation.requestBody ?? {}, apiSchema) : {};
	$: requestContents = Object.entries(requestBody?.content ?? {}) as [
		string,
		Record<string, any>
	][];
	$: responseEntries = Object.entries(selected?.operation.responses ?? {}) as [
		string,
		Record<string, any>
	][];
	$: codeExample =
		selected && apiSchema ? generateCodeExample(selected, apiSchema, baseUrl, language) : '';

	const groupLabel = (id: string) =>
		metadata?.groups?.find((item) => item.id === id)?.label ?? id.replace(/[-_]/g, ' ');

	const accessLabel = (access: string) =>
		({
			public: $i18n.t('Public'),
			authenticated: $i18n.t('Signed-in user'),
			admin: $i18n.t('Administrator'),
			custom: $i18n.t('Service authentication')
		})[access] ?? $i18n.t('Endpoint permissions apply');

	const apiKeyLabel = (access: string) =>
		({
			allowed: $i18n.t('API key supported'),
			restricted: $i18n.t('Restricted by API-key policy'),
			conditional: $i18n.t('API key allowed for selected resource paths'),
			disabled: $i18n.t('API key access unavailable'),
			custom: $i18n.t('Separate authentication required'),
			'not-required': $i18n.t('No authentication required')
		})[access] ?? $i18n.t('Check authentication requirements');

	async function load() {
		loading = true;
		error = '';
		try {
			const [info, schema] = await Promise.all([
				getDeveloperMetadata(getDeveloperToken()),
				getDeveloperSchema(getDeveloperToken())
			]);
			if (disposed) return;
			metadata = info;
			apiSchema = schema;
			// Always use the actual application origin, never a server-provided external host.
			const prefix =
				typeof info.base_url === 'string' && /^\/(?!\/)/.test(info.base_url)
					? info.base_url.replace(/\/$/, '')
					: '';
			baseUrl = window.location.origin + prefix;
		} catch (err) {
			if (!disposed) error = err instanceof Error ? err.message : String(err);
		} finally {
			if (!disposed) loading = false;
		}
	}

	async function copy(value: string) {
		try {
			await copyToClipboard(value);
			toast.success($i18n.t('Copied to clipboard'));
		} catch {
			toast.error($i18n.t('Unable to copy. Select the text and copy it manually.'));
		}
	}

	async function download(format: 'openapi' | 'postman') {
		if (!apiSchema || downloading) return;
		downloading = format;
		try {
			let payload: Record<string, any>;
			if (format === 'postman') {
				payload = await getDeveloperPostman(getDeveloperToken());
				payload.variable = (payload.variable ?? []).map((variable: Record<string, any>) =>
					variable.key === 'base_url' ? { ...variable, value: baseUrl } : variable
				);
			} else {
				payload = JSON.parse(JSON.stringify(apiSchema));
				payload.servers = [{ url: baseUrl, description: 'This ABIT.AI application' }];
			}
			const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const anchor = window.document.createElement('a');
			anchor.href = url;
			anchor.download =
				format === 'postman' ? 'abit-ai.postman_collection.json' : 'abit-ai.openapi.json';
			window.document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
			toast.success($i18n.t('Download ready'));
		} catch (err) {
			toast.error(err instanceof Error ? err.message : $i18n.t('Download failed'));
		} finally {
			downloading = '';
		}
	}

	function openGroup(id: string) {
		tab = 'reference';
		group = id;
		query = '';
		method = '';
		selectedId = '';
	}

	onMount(load);
	onDestroy(() => {
		disposed = true;
	});
</script>

<svelte:head><title>{$i18n.t('API & Developers')} · {$WEBUI_NAME}</title></svelte:head>

<div
	class="api-module flex h-screen max-h-[100dvh] w-full min-w-0 flex-col bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100 {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''}"
>
	<header class="shrink-0 border-b border-gray-100 px-4 py-4 dark:border-gray-800 sm:px-6">
		<div class="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
			<div class="flex min-w-0 items-center gap-3">
				{#if standalone}
					<a
						href="/"
						class="api-icon-button shrink-0 hover:bg-gray-100 dark:hover:bg-gray-800"
						aria-label={$i18n.t('Back to') + ' ' + $WEBUI_NAME}
						title={$i18n.t('Back to') + ' ' + $WEBUI_NAME}
					>
						<svg
							class="size-5 rtl:rotate-180"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							aria-hidden="true"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M19 12H5m7-7-7 7 7 7" />
						</svg>
					</a>
				{:else if $mobile}
					<button
						class="api-icon-button"
						type="button"
						aria-label={$i18n.t('Toggle sidebar')}
						on:click={() => showSidebar.set(!$showSidebar)}
					>
						<SidebarIcon className="size-5" />
					</button>
				{/if}
				<div
					class="hidden size-11 items-center justify-center rounded-xl bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200 sm:flex"
				>
					<CodeIcon className="size-6" />
				</div>
				<div class="min-w-0">
					<h1 class="text-lg font-semibold tracking-tight">{$i18n.t('API & Developers')}</h1>
					<p class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Connect your applications to ABIT.AI')}
					</p>
				</div>
			</div>
			{#if apiSchema}
				<div class="flex flex-wrap gap-2">
					<button
						class="api-button"
						type="button"
						disabled={!!downloading}
						on:click={() => download('openapi')}
						>{downloading === 'openapi'
							? $i18n.t('Preparing…')
							: $i18n.t('Download OpenAPI')}</button
					>
					<button
						class="api-button"
						type="button"
						disabled={!!downloading}
						on:click={() => download('postman')}
						>{downloading === 'postman'
							? $i18n.t('Preparing…')
							: $i18n.t('Download Postman')}</button
					>
				</div>
			{/if}
		</div>
	</header>

	<div class="min-h-0 flex-1 overflow-y-auto">
		<div class="mx-auto max-w-[1600px] px-4 py-5 sm:px-6">
			{#if loading}
				<div class="flex min-h-64 items-center justify-center gap-3" role="status">
					<Spinner /><span class="text-sm text-gray-500"
						>{$i18n.t('Loading API documentation…')}</span
					>
				</div>
			{:else if error}
				<div
					class="mx-auto max-w-xl rounded-2xl border border-gray-200 p-6 dark:border-gray-700"
					role="alert"
				>
					<h2 class="font-semibold">{$i18n.t('API documentation could not be loaded')}</h2>
					<p class="mt-2 break-words text-sm text-gray-600 dark:text-gray-400">{error}</p>
					<button class="api-button mt-4" type="button" on:click={load}
						>{$i18n.t('Try again')}</button
					>
				</div>
			{:else if metadata && apiSchema}
				<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
					<div class="flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
						<span
							class="rounded-full bg-gray-100 px-3 py-1.5 font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200"
							>{operations.length} {$i18n.t('endpoints')}</span
						>
						<span>{groups.length} {$i18n.t('modules')}</span>
						<span aria-hidden="true">·</span><span>v{metadata.version}</span>
					</div>
					<button
						class="flex min-h-10 max-w-full items-center gap-2 rounded-lg px-2 text-xs text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800"
						type="button"
						on:click={() => copy(baseUrl)}
						aria-label={$i18n.t('Copy API base URL')}
					>
						<span class="shrink-0">{$i18n.t('Base URL')}</span><code
							class="break-all text-gray-700 dark:text-gray-200">{baseUrl}</code
						><span aria-hidden="true">⧉</span>
					</button>
				</div>

				<nav
					class="mb-5 flex flex-wrap gap-1 border-b border-gray-200 dark:border-gray-800"
					aria-label={$i18n.t('Developer sections')}
				>
					{#each [{ id: 'reference', label: 'API reference' }, { id: 'authentication', label: 'Authentication' }, { id: 'guides', label: 'Getting started' }] as item}
						<button
							class="api-tab {tab === item.id ? 'api-tab-active' : ''}"
							type="button"
							aria-pressed={tab === item.id}
							on:click={() => {
								tab = item.id as typeof tab;
							}}>{$i18n.t(item.label)}</button
						>
					{/each}
				</nav>

				{#if tab === 'reference'}
					<div
						class="mb-4 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,12rem)_7rem]"
					>
						<div class="min-w-0">
							<label class="sr-only" for="api-search">{$i18n.t('Search endpoints')}</label>
							<input
								id="api-search"
								class="api-input"
								type="search"
								bind:value={query}
								placeholder={$i18n.t('Search endpoints, actions or paths…')}
							/>
						</div>
						<div>
							<label class="sr-only" for="api-group">{$i18n.t('Module')}</label>
							<select id="api-group" class="api-input" bind:value={group}>
								<option value="">{$i18n.t('All modules')}</option>
								{#each groups as id}<option value={id}>{groupLabel(id)}</option>{/each}
							</select>
						</div>
						<div>
							<label class="sr-only" for="api-method">{$i18n.t('HTTP method')}</label>
							<select id="api-method" class="api-input" bind:value={method}
								><option value="">{$i18n.t('All methods')}</option>{#each methods as value}<option
										{value}>{value}</option
									>{/each}</select
							>
						</div>
					</div>

					<div class="api-reference-grid">
						<section
							class="min-w-0 overflow-hidden rounded-2xl border border-gray-200 dark:border-gray-800"
							aria-label={$i18n.t('Endpoint catalogue')}
						>
							<div
								class="border-b border-gray-100 px-4 py-3 text-xs font-medium text-gray-500 dark:border-gray-800 dark:text-gray-400"
								aria-live="polite"
							>
								{filtered.length}
								{$i18n.t('matching endpoints')}
							</div>
							<div class="api-endpoint-list overflow-y-auto">
								{#each filtered as operation (operation.id)}
									<button
										class="api-endpoint w-full border-b border-gray-100 px-4 py-3 text-start last:border-b-0 dark:border-gray-800 {selected?.id ===
										operation.id
											? 'api-endpoint-selected'
											: ''}"
										type="button"
										aria-pressed={selected?.id === operation.id}
										on:click={() => {
											selectedId = operation.id;
										}}
									>
										<div class="mb-1.5 flex items-center gap-2">
											<span class="api-method" data-method={operation.method}
												>{operation.method}</span
											>
											<span class="truncate text-[11px] capitalize text-gray-500 dark:text-gray-400"
												>{groupLabel(operation.group)}</span
											>
										</div>
										<code class="block break-all text-xs leading-5">{operation.path}</code>
										<span class="mt-1 block truncate text-xs text-gray-500 dark:text-gray-400"
											>{operation.summary}</span
										>
									</button>
								{:else}
									<div class="p-6 text-center">
										<p class="text-sm text-gray-500">
											{$i18n.t('No endpoints match your filters.')}
										</p>
										<button
											class="api-button mt-3"
											type="button"
											on:click={() => {
												query = '';
												group = '';
												method = '';
											}}>{$i18n.t('Clear filters')}</button
										>
									</div>
								{/each}
							</div>
						</section>

						{#if selected}
							<section
								class="min-w-0 rounded-2xl border border-gray-200 p-4 dark:border-gray-800 sm:p-6"
								aria-label={$i18n.t('Endpoint details')}
							>
								<div class="mb-4 flex flex-wrap items-center gap-2">
									<span class="api-method" data-method={selected.method}>{selected.method}</span>
									<code class="min-w-0 flex-1 break-all text-sm font-medium">{selected.path}</code>
									<button
										class="api-button"
										type="button"
										on:click={() => copy(baseUrl + selected.path)}>{$i18n.t('Copy URL')}</button
									>
								</div>
								<h2 class="text-xl font-semibold tracking-tight">{selected.summary}</h2>
								<div class="mt-3 flex flex-wrap gap-2">
									<span class="api-badge">{accessLabel(selected.operation['x-abit-access'])}</span>
									<span class="api-badge"
										>{apiKeyLabel(selected.operation['x-abit-api-key-access'])}</span
									>
									{#if selected.operation.deprecated}<span
											class="api-badge text-amber-700 dark:text-amber-300"
											>{$i18n.t('Deprecated')}</span
										>{/if}
								</div>
								{#if selected.operation.description}<p
										class="mt-4 whitespace-pre-wrap break-words text-sm leading-6 text-gray-600 dark:text-gray-400"
									>
										{selected.operation.description}
									</p>{/if}
								{#if selected.operation['x-abit-auth-note']}<p
										class="mt-3 rounded-xl bg-gray-50 p-3 text-xs leading-5 text-gray-600 dark:bg-gray-950 dark:text-gray-400"
									>
										{selected.operation['x-abit-auth-note']}
									</p>{/if}

								{#if selected.parameters.length}
									<h3 class="mb-3 mt-7 text-sm font-semibold">{$i18n.t('Parameters')}</h3>
									<div
										class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700"
									>
										<table class="w-full text-start text-xs">
											<thead class="bg-gray-50 text-gray-600 dark:bg-gray-950 dark:text-gray-400"
												><tr
													><th class="px-3 py-2 text-start">{$i18n.t('Name')}</th><th
														class="px-3 py-2 text-start">{$i18n.t('Location / type')}</th
													><th class="px-3 py-2 text-start">{$i18n.t('Description')}</th></tr
												></thead
											>
											<tbody>
												{#each selected.parameters as parameter}
													<tr class="border-t border-gray-100 align-top dark:border-gray-800">
														<td class="px-3 py-3"
															><code class="break-all font-medium">{parameter.name}</code
															>{#if parameter.required}<span
																	class="mt-1 block text-rose-700 dark:text-rose-300"
																	>{$i18n.t('Required')}</span
																>{/if}</td
														>
														<td class="px-3 py-3 text-gray-500 dark:text-gray-400"
															>{parameter.in}<br />{schemaType(
																parameter.schema ?? {},
																apiSchema
															)}</td
														>
														<td
															class="min-w-32 break-words px-3 py-3 text-gray-600 dark:text-gray-400"
															>{parameter.description ??
																parameter.schema?.description ??
																'—'}{#if parameter.schema?.default !== undefined}<span
																	class="mt-1 block"
																	>{$i18n.t('Default')}:
																	<code>{JSON.stringify(parameter.schema.default)}</code></span
																>{/if}</td
														>
													</tr>
												{/each}
											</tbody>
										</table>
									</div>
								{/if}

								{#if requestContents.length}
									<h3 class="mb-3 mt-7 text-sm font-semibold">
										{$i18n.t('Request body')}{#if requestBody.required}<span
												class="ms-2 text-xs font-normal text-rose-700 dark:text-rose-300"
												>{$i18n.t('Required')}</span
											>{/if}
									</h3>
									{#each requestContents as [mediaType, content]}
										<details
											class="mb-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700"
											open
										>
											<summary
												class="cursor-pointer font-mono text-xs text-gray-600 dark:text-gray-400"
												>{mediaType}</summary
											>
											<div class="mt-3">
												<SchemaViewer schema={content.schema ?? {}} document={apiSchema} />
											</div>
										</details>
									{/each}
								{/if}

								<h3 class="mb-2 mt-7 text-sm font-semibold">{$i18n.t('Request example')}</h3>
								<p class="mb-3 text-xs leading-5 text-gray-500 dark:text-gray-400">
									{$i18n.t(
										'Replace sample values with your data. Examples are not executed in this page.'
									)}
								</p>
								<div class="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
									<div
										class="flex flex-wrap items-center justify-between gap-1 border-b border-gray-200 bg-gray-50 px-2 dark:border-gray-700 dark:bg-gray-950"
									>
										<div class="flex">
											{#each languages as item}<button
													class="min-h-11 px-3 text-xs {language === item.id
														? 'font-semibold text-gray-900 dark:text-white'
														: 'text-gray-500 dark:text-gray-400'}"
													type="button"
													aria-pressed={language === item.id}
													on:click={() => {
														language = item.id;
													}}>{item.label}</button
												>{/each}
										</div>
										<button
											class="min-h-11 px-3 text-xs text-gray-600 dark:text-gray-300"
											type="button"
											on:click={() => copy(codeExample)}>{$i18n.t('Copy example')}</button
										>
									</div>
									<pre
										class="max-h-[32rem] overflow-auto bg-gray-50/50 p-4 text-xs leading-6 text-gray-800 dark:bg-gray-950 dark:text-gray-200"><code
											>{codeExample}</code
										></pre>
								</div>

								{#if responseEntries.length}
									<h3 class="mb-3 mt-7 text-sm font-semibold">{$i18n.t('Responses')}</h3>
									{#each responseEntries as [status, rawResponse]}
										{@const response = resolveSchema(rawResponse, apiSchema)}
										<details
											class="mb-2 rounded-xl border border-gray-200 p-3 dark:border-gray-700"
										>
											<summary class="cursor-pointer text-sm"
												><code class="me-2 font-semibold">{status}</code><span
													class="text-gray-600 dark:text-gray-400"
													>{response.description ?? $i18n.t('Response')}</span
												></summary
											>
											{#each Object.entries(response.content ?? {}) as [mediaType, rawContent]}
												{@const content = rawContent as Record<string, any>}
												<div class="mt-3">
													<p class="mb-2 font-mono text-xs text-gray-500">{mediaType}</p>
													<SchemaViewer schema={content.schema ?? {}} document={apiSchema} />
												</div>
											{/each}
											{#if !response.content}<p class="mt-2 text-xs text-gray-500">
													{$i18n.t(
														'No response schema declared. Check the endpoint description for its response format.'
													)}
												</p>{/if}
										</details>
									{/each}
								{/if}
							</section>
						{/if}
					</div>
				{:else if tab === 'authentication'}
					<div class="grid min-w-0 gap-5 lg:grid-cols-2">
						<section
							class="min-w-0 rounded-2xl border border-gray-200 p-5 dark:border-gray-800 sm:p-6"
						>
							<div class="mb-4 flex flex-wrap items-center justify-between gap-2">
								<h2 class="text-lg font-semibold">{$i18n.t('Your API access')}</h2>
								<span class="api-badge"
									>{metadata.authentication.api_keys_enabled
										? $i18n.t('API keys enabled')
										: $i18n.t('API keys disabled')}</span
								>
							</div>
							<p class="text-sm leading-6 text-gray-600 dark:text-gray-400">
								{$i18n.t(
									'API requests use your account permissions. Access to other users’ chats, projects and files is checked by each endpoint.'
								)}
							</p>
							{#if metadata.authentication.can_manage_api_key}
								<p class="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-400">
									{$i18n.t(
										'Create, view or revoke your personal API key in Account settings. Revoking a key stops integrations that use it.'
									)}
								</p>
								<button
									class="api-primary mt-4"
									type="button"
									on:click={() => showSettings.set('account')}>{$i18n.t('Manage API key')}</button
								>
							{:else}
								<p
									class="mt-3 rounded-xl bg-gray-50 p-4 text-sm text-gray-600 dark:bg-gray-950 dark:text-gray-400"
								>
									{$i18n.t(
										'Ask your administrator to enable API keys and grant API-key access to your account.'
									)}
								</p>
							{/if}
							<h3 class="mb-2 mt-6 text-sm font-semibold">{$i18n.t('Authorization header')}</h3>
							<pre class="overflow-x-auto rounded-xl bg-gray-50 p-4 text-xs dark:bg-gray-950"><code
									>Authorization: Bearer YOUR_API_KEY</code
								></pre>
							<p class="mt-3 text-xs leading-5 text-gray-500 dark:text-gray-400">
								{$i18n.t(
									'Store keys on your server or in a secret manager. Keep them out of source code, URLs and shared collections. Downloaded examples contain placeholders only.'
								)}
							</p>
						</section>
						<section
							class="min-w-0 rounded-2xl border border-gray-200 p-5 dark:border-gray-800 sm:p-6"
						>
							<h2 class="mb-3 text-lg font-semibold">{$i18n.t('Endpoint permissions')}</h2>
							<p class="text-sm leading-6 text-gray-600 dark:text-gray-400">
								{$i18n.t(
									'The reference marks administrator actions and services with separate authentication. Signing in to view documentation does not grant additional access.'
								)}
							</p>
							<div class="mt-4 rounded-xl bg-gray-50 p-4 dark:bg-gray-950">
								<h3 class="text-sm font-medium">
									{metadata.authentication.endpoint_restrictions
										? $i18n.t('API-key endpoint restrictions are enabled')
										: $i18n.t('Account and resource permissions apply')}
								</h3>
								{#if metadata.authentication.endpoint_restrictions}
									<p class="mt-2 text-xs leading-5 text-gray-500 dark:text-gray-400">
										{$i18n.t('Your administrator allows API-key requests only under these paths:')}
									</p>
									{#each metadata.authentication.allowed_endpoints ?? [] as path}<code
											class="mt-2 block break-all text-xs">{path}</code
										>{:else}<p class="mt-2 text-xs">
											{$i18n.t('No paths are currently allowed.')}
										</p>{/each}
								{/if}
							</div>
							<h3 class="mb-3 mt-6 text-sm font-semibold">{$i18n.t('Common responses')}</h3>
							<dl class="space-y-3 text-sm">
								<div class="flex gap-3">
									<dt class="font-mono font-semibold">401</dt>
									<dd class="text-gray-600 dark:text-gray-400">
										{$i18n.t('Authentication is missing, invalid or expired.')}
									</dd>
								</div>
								<div class="flex gap-3">
									<dt class="font-mono font-semibold">403</dt>
									<dd class="text-gray-600 dark:text-gray-400">
										{$i18n.t(
											'Your account, API key or resource permissions do not allow this action.'
										)}
									</dd>
								</div>
								<div class="flex gap-3">
									<dt class="font-mono font-semibold">404</dt>
									<dd class="text-gray-600 dark:text-gray-400">
										{$i18n.t('The resource is unavailable or cannot be accessed by your account.')}
									</dd>
								</div>
								<div class="flex gap-3">
									<dt class="font-mono font-semibold">422</dt>
									<dd class="text-gray-600 dark:text-gray-400">
										{$i18n.t(
											'A required value is missing or the request does not match the schema.'
										)}
									</dd>
								</div>
								<div class="flex gap-3">
									<dt class="font-mono font-semibold">429</dt>
									<dd class="text-gray-600 dark:text-gray-400">
										{$i18n.t(
											'A request or provider limit was reached. Follow the returned retry guidance.'
										)}
									</dd>
								</div>
							</dl>
						</section>
					</div>
				{:else}
					<section class="mb-5 rounded-2xl border border-gray-200 p-5 dark:border-gray-800 sm:p-6">
						<h2 class="text-lg font-semibold">{$i18n.t('Build your first integration')}</h2>
						<ol class="mt-4 grid list-none gap-5 text-sm md:grid-cols-3">
							<li>
								<span class="api-step">1</span>
								<h3 class="mt-3 font-semibold">{$i18n.t('Set up authentication')}</h3>
								<p class="mt-2 leading-6 text-gray-600 dark:text-gray-400">
									{$i18n.t(
										'Create a personal API key in Account settings, then set ABIT_API_KEY in your server environment.'
									)}
								</p>
								<button
									class="mt-2 min-h-11 text-sm font-medium underline underline-offset-4"
									type="button"
									on:click={() => {
										tab = 'authentication';
									}}>{$i18n.t('View authentication')}</button
								>
							</li>
							<li>
								<span class="api-step">2</span>
								<h3 class="mt-3 font-semibold">{$i18n.t('Choose an endpoint')}</h3>
								<p class="mt-2 leading-6 text-gray-600 dark:text-gray-400">
									{$i18n.t(
										'Search the reference, review required fields and permissions, and copy a cURL, Python or JavaScript example.'
									)}
								</p>
							</li>
							<li>
								<span class="api-step">3</span>
								<h3 class="mt-3 font-semibold">{$i18n.t('Connect your tools')}</h3>
								<p class="mt-2 leading-6 text-gray-600 dark:text-gray-400">
									{$i18n.t(
										'Import the OpenAPI specification or Postman collection. Set your own key and replace sample values before sending a request.'
									)}
								</p>
							</li>
						</ol>
					</section>
					<div class="mb-5 grid min-w-0 gap-4 sm:grid-cols-2">
						<section class="rounded-2xl border border-gray-200 p-5 dark:border-gray-800">
							<h3 class="font-semibold">{$i18n.t('Streaming and background work')}</h3>
							<p class="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-400">
								{$i18n.t(
									'Chat completion requests may return a server-sent event stream when stream is true. Task and agent actions can continue in the background; use their status and history endpoints to check completion. Socket.IO events use the existing signed-in realtime connection and are described separately from HTTP endpoints.'
								)}
							</p>
							{#if metadata.realtime}<details class="mt-3">
									<summary class="cursor-pointer text-xs font-medium"
										>{$i18n.t('Realtime connection details')}</summary
									>
									<pre
										class="mt-2 max-h-60 overflow-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-950"><code
											>{JSON.stringify(metadata.realtime, null, 2)}</code
										></pre>
								</details>{/if}
						</section>
						<section class="rounded-2xl border border-gray-200 p-5 dark:border-gray-800">
							<h3 class="font-semibold">{$i18n.t('Documentation stays current')}</h3>
							<p class="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-400">
								{$i18n.t(
									'This catalogue is generated from the routes registered by this installation. Available modules depend on enabled features and your role. Request and response details reflect the schemas declared by the application; flexible payloads and service-specific behavior are described by their endpoints.'
								)}
							</p>
						</section>
					</div>
					<h2 class="mb-3 text-base font-semibold">{$i18n.t('Explore modules')}</h2>
					<div class="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
						{#each groups as id}
							<button
								class="flex min-w-0 items-center justify-between gap-3 rounded-xl border border-gray-200 p-4 text-start transition hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800"
								type="button"
								on:click={() => openGroup(id)}
								><span class="truncate text-sm font-medium capitalize">{groupLabel(id)}</span><span
									class="shrink-0 text-xs text-gray-500"
									>{operations.filter((item) => item.group === id).length} →</span
								></button
							>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>

<style>
	.api-reference-grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 1rem;
		align-items: start;
	}
	.api-endpoint-list {
		max-height: 20rem;
	}
	.api-button {
		min-height: 2.75rem;
		padding: 0.5rem 0.8rem;
		border: 1px solid #d1d5db;
		border-radius: 0.65rem;
		font-size: 0.75rem;
		font-weight: 500;
		transition: background 0.15s;
	}
	.api-button:hover {
		background: #f3f4f6;
	}
	.api-button:disabled {
		opacity: 0.5;
		cursor: wait;
	}
	.api-primary {
		min-height: 2.75rem;
		padding: 0.6rem 1rem;
		border-radius: 0.7rem;
		background: #111827;
		color: white;
		font-size: 0.875rem;
		font-weight: 500;
	}
	.api-icon-button {
		display: flex;
		width: 2.75rem;
		height: 2.75rem;
		align-items: center;
		justify-content: center;
		border-radius: 0.65rem;
	}
	.api-input {
		width: 100%;
		min-width: 0;
		min-height: 2.75rem;
		padding: 0.65rem 0.8rem;
		border: 1px solid #d1d5db;
		border-radius: 0.7rem;
		background: transparent;
		font-size: 0.8rem;
	}
	.api-tab {
		min-height: 2.75rem;
		padding: 0.65rem 0.9rem;
		margin-bottom: -1px;
		border-bottom: 2px solid transparent;
		color: #6b7280;
		font-size: 0.875rem;
	}
	.api-tab-active {
		border-bottom-color: #111827;
		color: #111827;
		font-weight: 600;
	}
	.api-endpoint:hover {
		background: #f9fafb;
	}
	.api-endpoint-selected {
		background: #f3f4f6;
		box-shadow: inset 3px 0 #111827;
	}
	.api-method {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 3.5rem;
		padding: 0.2rem 0.45rem;
		border-radius: 0.35rem;
		background: #f3f4f6;
		color: #374151;
		font-size: 0.65rem;
		font-weight: 700;
		letter-spacing: 0.02em;
	}
	.api-method[data-method='GET'] {
		background: #ecfdf5;
		color: #047857;
	}
	.api-method[data-method='POST'] {
		background: #eff6ff;
		color: #1d4ed8;
	}
	.api-method[data-method='PUT'],
	.api-method[data-method='PATCH'] {
		background: #fffbeb;
		color: #92400e;
	}
	.api-method[data-method='DELETE'] {
		background: #fff1f2;
		color: #be123c;
	}
	.api-badge {
		display: inline-flex;
		padding: 0.3rem 0.6rem;
		border-radius: 999px;
		background: #f3f4f6;
		color: #4b5563;
		font-size: 0.7rem;
	}
	.api-step {
		display: inline-flex;
		width: 1.9rem;
		height: 1.9rem;
		align-items: center;
		justify-content: center;
		border-radius: 50%;
		background: #f3f4f6;
		font-size: 0.8rem;
		font-weight: 600;
	}
	.api-module :global(a:focus-visible),
	.api-module :global(button:focus-visible),
	.api-module :global(input:focus-visible),
	.api-module :global(select:focus-visible),
	.api-module :global(summary:focus-visible) {
		outline: 2px solid #0d9488;
		outline-offset: 3px;
	}
	:global(.dark) .api-button,
	:global(.dark) .api-input {
		border-color: #374151;
	}
	:global(.dark) .api-button:hover,
	:global(.dark) .api-endpoint:hover {
		background: #1f2937;
	}
	:global(.dark) .api-primary {
		background: #f9fafb;
		color: #111827;
	}
	:global(.dark) .api-input option {
		background: #111827;
		color: #f3f4f6;
	}
	:global(.dark) .api-tab {
		color: #9ca3af;
	}
	:global(.dark) .api-tab-active {
		border-color: #e5e7eb;
		color: #f9fafb;
	}
	:global(.dark) .api-endpoint-selected {
		background: #1f2937;
		box-shadow: inset 3px 0 #e5e7eb;
	}
	:global(.dark) .api-method,
	:global(.dark) .api-badge,
	:global(.dark) .api-step {
		background: #1f2937;
		color: #d1d5db;
	}
	:global(.dark) .api-method[data-method='GET'] {
		background: #064e3b;
		color: #a7f3d0;
	}
	:global(.dark) .api-method[data-method='POST'] {
		background: #1e3a8a;
		color: #bfdbfe;
	}
	:global(.dark) .api-method[data-method='PUT'],
	:global(.dark) .api-method[data-method='PATCH'] {
		background: #78350f;
		color: #fde68a;
	}
	:global(.dark) .api-method[data-method='DELETE'] {
		background: #881337;
		color: #fecdd3;
	}
	@media (min-width: 1024px) {
		.api-reference-grid {
			grid-template-columns: minmax(17rem, 22rem) minmax(0, 1fr);
		}
		.api-endpoint-list {
			max-height: calc(100dvh - 20rem);
			min-height: 20rem;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.api-module :global(*) {
			transition: none !important;
		}
	}
</style>

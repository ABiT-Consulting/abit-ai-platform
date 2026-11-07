<script lang="ts">
	import { getContext } from 'svelte';
	import { resolveSchema, schemaType, type OpenApiSchema } from '$lib/utils/developer';

	export let schema: Record<string, any> = {};
	export let document: OpenApiSchema;
	export let depth = 0;

	const i18n: any = getContext('i18n');
	$: resolved = resolveSchema(schema, document) ?? {};
	$: properties = Object.entries(resolved.properties ?? {}) as [string, Record<string, any>][];
	$: required = resolved.required ?? [];
	$: alternatives = resolved.oneOf ?? resolved.anyOf ?? resolved.allOf ?? [];
</script>

<div class="schema-view min-w-0">
	{#if resolved.description}
		<p class="mb-3 whitespace-pre-wrap text-sm text-gray-600 dark:text-gray-400">
			{resolved.description}
		</p>
	{/if}
	{#if properties.length}
		<div
			class="divide-y divide-gray-100 rounded-xl border border-gray-200 dark:divide-gray-800 dark:border-gray-700"
		>
			{#each properties as [name, property]}
				{@const detail = resolveSchema(property, document) ?? {}}
				<div class="min-w-0 px-3 py-3">
					<div class="flex flex-wrap items-center gap-2 text-xs">
						<code class="break-all font-semibold text-gray-900 dark:text-gray-100">{name}</code>
						<span
							class="rounded-md bg-gray-100 px-1.5 py-0.5 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
							>{schemaType(property, document)}</span
						>
						{#if required.includes(name)}<span class="text-rose-700 dark:text-rose-300"
								>{$i18n.t('Required')}</span
							>{/if}
						{#if detail.readOnly}<span class="text-gray-500">{$i18n.t('Read only')}</span>{/if}
					</div>
					{#if detail.description}<p
							class="mt-1 break-words text-xs leading-5 text-gray-600 dark:text-gray-400"
						>
							{detail.description}
						</p>{/if}
					{#if detail.enum}<p class="mt-1 break-all text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('Allowed values')}: <code>{JSON.stringify(detail.enum)}</code>
						</p>{/if}
					{#if detail.default !== undefined}<p
							class="mt-1 break-all text-xs text-gray-600 dark:text-gray-400"
						>
							{$i18n.t('Default')}: <code>{JSON.stringify(detail.default)}</code>
						</p>{/if}
					{#if depth < 4 && (detail.properties || detail.items || detail.oneOf || detail.anyOf || detail.allOf)}
						<details class="mt-2">
							<summary class="cursor-pointer text-xs font-medium text-gray-700 dark:text-gray-300"
								>{$i18n.t('Nested schema')}</summary
							>
							<div class="mt-2"><svelte:self schema={detail} {document} depth={depth + 1} /></div>
						</details>
					{/if}
				</div>
			{/each}
		</div>
	{:else if resolved.items && depth < 4}
		<p class="mb-2 text-xs text-gray-500">{$i18n.t('Array items')}</p>
		<svelte:self schema={resolved.items} {document} depth={depth + 1} />
	{:else if !alternatives.length || depth >= 4}
		<p class="text-xs text-gray-600 dark:text-gray-400">
			{schemaType(schema, document)}
			{#if resolved.additionalProperties}
				· {$i18n.t('Additional fields supported')}{/if}
		</p>
	{/if}
	{#if alternatives.length && depth < 4}
		{#each alternatives as option, index}
			<details class="mb-2 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
				<summary class="cursor-pointer text-xs font-medium"
					>{$i18n.t('Schema')} {index + 1}: {schemaType(option, document)}</summary
				>
				<div class="mt-3"><svelte:self schema={option} {document} depth={depth + 1} /></div>
			</details>
		{/each}
	{/if}
	{#if depth === 0}
		<details class="mt-3">
			<summary class="cursor-pointer text-xs font-medium text-gray-600 dark:text-gray-400"
				>{$i18n.t('View JSON schema')}</summary
			>
			<pre
				class="mt-2 max-h-72 overflow-auto rounded-xl bg-gray-50 p-3 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200"><code
					>{JSON.stringify(resolved, null, 2)}</code
				></pre>
		</details>
	{/if}
</div>

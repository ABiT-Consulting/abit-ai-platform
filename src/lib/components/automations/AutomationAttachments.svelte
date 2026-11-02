<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getFileById, uploadFile } from '$lib/apis/files';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import type { AutomationAttachment } from '$lib/apis/automations';

	export let attachments: AutomationAttachment[] = [];
	export let blocked = false;
	export let disabled = false;

	type Kind = 'image' | 'document';
	type Item = {
		key: number;
		id?: string;
		file?: File;
		name: string;
		kind?: Kind;
		preview?: string;
		status: 'pending' | 'ready' | 'error';
		error?: string;
	};
	const MAX_COUNT = 10;
	const MAX_FILE_BYTES = 10 * 1024 * 1024;
	const IMAGE_TYPES = ['image/png', 'image/jpeg'];
	const DOCUMENT_TYPES: Record<string, string[]> = {
		'.pdf': ['application/pdf'],
		'.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
		'.xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
		'.txt': ['text/plain'],
		'.md': ['text/markdown', 'text/plain', 'text/x-markdown'],
		'.csv': ['text/csv', 'text/plain', 'application/vnd.ms-excel'],
		'.json': ['application/json', 'text/plain']
	};

	let items: Item[] = [];
	let nextKey = 0;
	let alive = true;
	let picker: HTMLInputElement;
	const controllers = new Set<AbortController>();
	const active = (item: Item) => alive && items.includes(item);

	function kindOf(name: string, type: string): Kind | null {
		const mime = (type || '').toLowerCase();
		if (IMAGE_TYPES.includes(mime)) return 'image';
		const extension = (name.match(/\.[a-z0-9]+$/i)?.[0] ?? '').toLowerCase();
		const accepted = DOCUMENT_TYPES[extension];
		return accepted && (accepted.includes(mime) || !mime || mime === 'application/octet-stream')
			? 'document'
			: null;
	}

	function sync() {
		items = [...items];
		attachments = items.filter((item) => item.id).map((item) => ({ id: item.id! }));
		blocked = items.some((item) => item.status !== 'ready');
	}

	async function loadPreview(item: Item) {
		const controller = new AbortController();
		controllers.add(controller);
		try {
			const response = await fetch(
				WEBUI_API_BASE_URL + '/files/' + encodeURIComponent(item.id!) + '/content',
				{ headers: { Authorization: 'Bearer ' + localStorage.token }, signal: controller.signal }
			);
			if (!response.ok) throw new Error('Image is missing or no longer accessible.');
			const blob = await response.blob();
			if (!IMAGE_TYPES.includes(blob.type) || blob.size > MAX_FILE_BYTES || blob.size === 0) {
				throw new Error('Only PNG/JPEG images up to 10 MB are supported.');
			}
			if (active(item)) {
				if (item.preview) URL.revokeObjectURL(item.preview);
				item.preview = URL.createObjectURL(blob);
			}
		} finally {
			controllers.delete(controller);
		}
	}

	async function process(item: Item) {
		if (!active(item) || disabled) return;
		item.status = 'pending';
		item.error = undefined;
		sync();
		try {
			if (!item.id && item.file) {
				// Images are kept as binary for vision; documents must finish text extraction.
				const isImage = item.kind === 'image';
				const uploaded = await uploadFile(
					localStorage.token, item.file, null, !isImage, !isImage
				);
				if (!uploaded?.id || uploaded.error) {
					throw new Error(uploaded?.error || 'Upload failed. Please retry.');
				}
				if (!active(item)) return;
				item.id = uploaded.id;
			}

			let result;
			try {
				result = await getFileById(localStorage.token, item.id!);
			} catch {
				throw new Error('Attachment is missing or no longer accessible.');
			}
			if (!result || !active(item)) {
				if (active(item)) throw new Error('Attachment is missing or no longer accessible.');
				return;
			}
			item.name = result.filename || item.name;
			const resolvedKind = kindOf(item.name, result.meta?.content_type ?? '');
			if (!resolvedKind) throw new Error('Unsupported file format.');
			item.kind = resolvedKind;
			if (!Number.isFinite(Number(result.meta?.size)) ||
				Number(result.meta?.size) <= 0 || Number(result.meta?.size) > MAX_FILE_BYTES) {
				throw new Error('Attachment must be between 1 byte and 10 MB.');
			}

			if (item.kind === 'image') {
				await loadPreview(item);
			} else if (result.data?.status !== 'completed' ||
				typeof result.data?.content !== 'string' || !result.data.content.trim()) {
				throw new Error('Document text extraction did not finish. Retry or choose another file.');
			} else if (result.data.content.length > 50_000) {
				throw new Error('Document text exceeds 50,000 characters.');
			}

			if (active(item)) item.status = 'ready';
		} catch (error) {
			if (active(item)) {
				item.status = 'error';
				item.error = error instanceof Error ? error.message : String(error);
			}
		} finally {
			if (active(item)) sync();
		}
	}

	export function addFiles(files: File[]) {
		if (disabled) return;
		for (const file of files) {
			if (items.length >= MAX_COUNT) {
				toast.error('Automations support at most 10 attachments.');
				break;
			}
			const kind = kindOf(file.name, file.type);
			if (!kind) {
				toast.error('Supported: PNG, JPEG, PDF, DOCX, XLSX, TXT, MD, CSV and JSON.');
				continue;
			}
			if (!file.size || file.size > MAX_FILE_BYTES) {
				toast.error('Each attachment must be between 1 byte and 10 MB.');
				continue;
			}
			const item: Item = {
				key: nextKey++,
				file,
				name: file.name,
				kind,
				preview: kind === 'image' ? URL.createObjectURL(file) : undefined,
				status: 'pending'
			};
			items = [...items, item];
			void process(item);
		}
		sync();
	}

	function remove(item: Item) {
		if (disabled) return;
		if (item.preview) URL.revokeObjectURL(item.preview);
		items = items.filter((entry) => entry !== item);
		sync();
	}

	function handleDrop(event: DragEvent) {
		if (event.dataTransfer?.files?.length) {
			event.preventDefault();
			event.stopPropagation();
			addFiles(Array.from(event.dataTransfer.files));
		}
	}

	onMount(() => {
		items = attachments.map(({ id }) => ({
			key: nextKey++,
			id,
			name: 'Saved attachment',
			status: 'pending'
		}));
		sync();
		for (const item of items) void process(item);
	});

	onDestroy(() => {
		alive = false;
		for (const controller of controllers) controller.abort();
		for (const item of items) if (item.preview) URL.revokeObjectURL(item.preview);
	});
</script>

<div
	class="px-5 pb-3"
	on:dragover={(event) => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); }}
	on:drop={handleDrop}
	role="group"
	aria-label="Automation attachments"
>
	<input
		bind:this={picker}
		type="file"
		accept=".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.txt,.md,.csv,.json"
		multiple
		class="hidden"
		aria-label="Choose automation attachments"
		{disabled}
		on:change={() => {
			addFiles(Array.from(picker.files ?? []));
			picker.value = '';
		}}
	/>
	<button
		type="button"
		class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 dark:border-gray-700 px-2.5 py-1.5 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-800"
		{disabled}
		on:click={() => picker.click()}
	>
		<span aria-hidden="true">+</span> Attach files
	</button>
	<p class="text-xs text-gray-500 mt-1.5">
		Paste screenshots, choose files or drag them here. PNG/JPEG, PDF, DOCX, XLSX, TXT, MD, CSV and JSON; max 10 files, 10 MB each.
	</p>
	{#if items.length}
		<ul class="flex flex-wrap gap-2 mt-2" aria-label="Automation attachments">
			{#each items as item (item.key)}
				<li class="rounded-lg border border-gray-200 dark:border-gray-700 px-2.5 py-2 max-w-48 min-w-24">
					{#if item.preview}
						<img src={item.preview} alt={item.name} class="h-20 w-28 object-contain" />
					{:else if item.kind === 'document'}
						<span class="text-[11px] font-semibold uppercase text-gray-600 dark:text-gray-300">
							{item.name.split('.').pop()}
						</span>
					{/if}
					<p class="text-xs truncate mt-1" title={item.name}>{item.name}</p>
					{#if item.status === 'pending'}
						<p role="status" class="text-xs text-gray-500">Uploading or checking...</p>
					{:else if item.status === 'error'}
						<p role="alert" class="text-xs text-red-500">{item.error}</p>
						<button type="button" class="text-xs underline mr-2" {disabled} on:click={() => process(item)}>
							Retry
						</button>
					{/if}
					<button
						type="button"
						class="text-xs underline"
						{disabled}
						aria-label={'Remove ' + item.name}
						on:click={() => remove(item)}
					>Remove</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

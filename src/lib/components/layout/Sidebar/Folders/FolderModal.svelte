<script lang="ts">
	import { getContext, createEventDispatcher, onMount, tick } from 'svelte';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '../icons/XMark.svelte';

	import { toast } from 'svelte-sonner';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { user, config } from '$lib/stores';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import Knowledge from '$lib/components/workspace/Models/Knowledge.svelte';
	import { getFolderById } from '$lib/apis/folders';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { projectCreationRequest } from '$lib/utils/chatgptProject';
	const i18n = getContext('i18n');

	export let show = false;
	export let onSubmit: Function = (e) => {};

	export let folderId = null;
	export let parentId = null;
	export let edit = false;

	let folder = null;
	let name = '';
	let meta = {
		background_image_url: null
	};
	let data = {
		system_prompt: '',
		files: []
	};

	let loading = false;
	let submitError = '';
	$: syncCreation = !edit && !parentId && $user?.role === 'admin';

	const submitHandler = async () => {
		if (loading) return;
		loading = true;
		submitError = '';

		if ((data?.files ?? []).some((file) => file.status === 'uploading')) {
			toast.error($i18n.t('Please wait until all files are uploaded.'));
			loading = false;
			return;
		}

		// Check folder max file count limit
		const maxFileCount = $config?.features?.folder_max_file_count ?? '';
		if (maxFileCount && (data?.files ?? []).length > maxFileCount) {
			toast.error(
				$i18n.t('Maximum number of files per folder is {{max}}.', { max: maxFileCount ?? 0 })
			);
			loading = false;
			return;
		}

		try {
			if (syncCreation) {
				const owner = $user?.id;
				if (!owner) throw new Error('Sign in before creating a project.');
				const retry = await projectCreationRequest(owner, name, data.system_prompt || '');
				const response = await fetch(WEBUI_API_BASE_URL + '/chatgpt-relay/projects/create', {
					method: 'POST',
					headers: {
						Authorization: 'Bearer ' + localStorage.token,
						'Content-Type': 'application/json'
					},
					body: JSON.stringify({
						name: name.trim(),
						instructions: data.system_prompt || '',
						request_id: retry.requestId
					})
				});
				const result = await response.json().catch(() => ({}));
				if (!response.ok) throw new Error(result.detail || 'ChatGPT project creation failed');
				if (result?.ok !== true || typeof result?.folder_id !== 'string' || !result.folder_id.trim()) {
					throw new Error('Project creation was not confirmed. Retry the same request.');
				}
				await onSubmit({ synced: true, folder_id: result.folder_id });
				// Keep the idempotency key if the sidebar refresh fails after creation.
				sessionStorage.removeItem(retry.key);
				toast.success('Project created in ChatGPT and synchronized');
			} else {
				await onSubmit({ name, meta, data, parent_id: edit ? undefined : parentId });
			}
			show = false;
		} catch (error) {
			submitError = error instanceof Error ? error.message : 'Could not create project';
			toast.error(submitError);
		} finally {
			loading = false;
		}
	};

	const init = async () => {
		if (folderId) {
			folder = await getFolderById(localStorage.token, folderId).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			name = folder.name;
			meta = folder.meta || {
				background_image_url: null
			};
			data = folder.data || {
				system_prompt: '',
				files: []
			};
		}

		focusInput();
	};

	const focusInput = async () => {
		await tick();
		const input = document.getElementById('folder-name') as HTMLInputElement;
		if (input) {
			input.focus();
			input.select();
		}
	};

	$: if (show) {
		init();
	}

	$: if (!show && !edit) {
		name = '';
		meta = {
			background_image_url: null
		};
		data = {
			system_prompt: '',
			files: []
		};
	}
</script>

<Modal size="md" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-4 pt-3 pb-1">
			<div class=" text-sm self-center">
				{#if edit}
					Edit Project
				{:else}
					Create Project
				{/if}
			</div>
			<button
				aria-label={$i18n.t('Close')}
				disabled={loading}
				class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-4'} />
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-4 pb-4 md:space-x-4 dark:text-gray-200">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<form
					class="flex flex-col w-full"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div class="flex flex-col w-full mt-1">
						<div class=" mb-1 text-xs text-gray-500">Project Name</div>

						<div class="flex-1">
							<input
								id="folder-name"
								class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden"
								type="text"
								bind:value={name}
								required
								disabled={loading}
								maxlength={syncCreation ? 120 : undefined}
								placeholder="Enter project name"
								autocomplete="off"
							/>
						</div>
					</div>

					{#if !syncCreation}
						<input
							id="folder-background-image-input"
							type="file"
							hidden
							accept="image/*"
							on:change={(e) => {
								const inputFiles = e.target.files;

								let reader = new FileReader();
								reader.onload = (event) => {
									let originalImageUrl = `${event.target.result}`;
									meta.background_image_url = originalImageUrl;
								};

								if (
									inputFiles &&
									inputFiles.length > 0 &&
									['image/gif', 'image/webp', 'image/jpeg', 'image/png'].includes(
										inputFiles[0]['type']
									)
								) {
									reader.readAsDataURL(inputFiles[0]);
								} else {
									console.log(`Unsupported File Type '${inputFiles[0]['type']}'.`);

									// clear the input
									e.target.value = '';
								}
							}}
						/>

						<div class="flex justify-between w-full mt-1 items-center">
							<div class="text-xs text-gray-500">{$i18n.t('Folder Background Image')}</div>

							<div class="">
								<button
									aria-labelledby="chat-background-label background-image-url-state"
									class="p-1 px-3 text-xs flex rounded-sm transition"
									on:click={() => {
										if (meta?.background_image_url !== null) {
											meta.background_image_url = null;
										} else {
											const input = document.getElementById('folder-background-image-input');
											if (input) {
												input.click();
											}
										}
									}}
									type="button"
								>
									<span class="ml-2 self-center" id="background-image-url-state"
										>{(meta?.background_image_url ?? null) === null
											? $i18n.t('Upload')
											: $i18n.t('Reset')}</span
									>
								</button>
							</div>
						</div>

						<hr class=" border-gray-50 dark:border-gray-850/30 my-2.5 w-full" />
					{/if}

					{#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
						<div class="my-1">
							<div class="mb-1 text-xs text-gray-500">Project Instructions</div>
							<div class="mb-2 text-xs text-gray-500">
								{#if !edit && !parentId && $user?.role === 'admin'}
									Created in ChatGPT with these instructions. ChatGPT Relay will be locked as its
									model.
								{:else}
									Applied automatically to every chat in this project. Add your repository, safety
									rules, and MCP connector instructions here.
								{/if}
							</div>
							<div>
								{#if syncCreation}
									<textarea
										aria-label="Project Instructions"
										rows="6"
										maxlength="12000"
										bind:value={data.system_prompt}
										disabled={loading}
										placeholder="How should ChatGPT respond in this project?"
										class="w-full resize-y rounded-lg border border-gray-200 bg-transparent p-3 text-sm text-gray-900 outline-hidden focus:border-gray-500 dark:border-gray-700 dark:text-gray-100"
									></textarea>
									<p class="mt-1 text-right text-xs text-gray-500 dark:text-gray-400">
										{data.system_prompt.length}/12000
									</p>
								{:else}
									<Textarea
										className=" text-sm w-full bg-transparent outline-hidden "
										placeholder={$i18n.t(
											'Write project instructions here\ne.g.) Use the Development Server Dev-02 connector. Inspect before changing files; never deploy or push without approval.'
										)}
										maxSize={200}
										bind:value={data.system_prompt}
									/>
								{/if}
							</div>
						</div>
					{/if}

					{#if !syncCreation}
						<div class="my-2">
							<Knowledge bind:selectedItems={data.files}>
								<div slot="label">
									<div class="flex w-full justify-between">
										<div class=" text-xs text-gray-500">
											{$i18n.t('Knowledge')}
										</div>
									</div>
								</div>
							</Knowledge>
						</div>
					{/if}
					{#if submitError}<p role="alert" class="mt-3 text-xs text-red-700 dark:text-red-300">
							{submitError}
						</p>{/if}

					<div class="flex justify-end pt-3 text-sm font-normal gap-1.5">
						<button
							class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-950 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {loading
								? ' cursor-not-allowed'
								: ''}"
							type="submit"
							disabled={loading}
						>
							{syncCreation
								? loading
									? 'Creating and syncing…'
									: 'Create project'
								: $i18n.t('Save')}

							{#if loading}
								<div class="ml-2 self-center">
									<Spinner />
								</div>
							{/if}
						</button>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>

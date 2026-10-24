<script lang="ts">
	import { safeChatGPTConversationUrl } from '$lib/utils/chatgptConversation';

	export let url: string | null = null;
	$: conversationUrl = safeChatGPTConversationUrl(url);
</script>

<div class="mb-2">
	{#if conversationUrl}
		<a
			href={conversationUrl}
			target="_blank"
			rel="noopener noreferrer"
			data-testid="open-chatgpt-conversation"
			aria-label="Open in ChatGPT (new tab)"
			title="Open this conversation in a new tab. Use the same ChatGPT account as the relay."
			class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
		>
			Open in ChatGPT
			<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
				<path d="M15 3h6v6M10 14 21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
			</svg>
		</a>
	{:else}
		<button
			type="button"
			disabled
			data-testid="open-chatgpt-conversation"
			title="The conversation link is not available yet. It appears once ChatGPT confirms the chat URL."
			class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-500 opacity-60 disabled:cursor-not-allowed dark:border-gray-700 dark:text-gray-400"
		>Open in ChatGPT</button>
	{/if}
</div>

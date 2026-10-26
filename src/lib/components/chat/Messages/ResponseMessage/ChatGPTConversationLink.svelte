<script lang="ts">
  import { safeChatGPTConversationUrl } from '$lib/utils/chatgptConversation';
  import ChatGPTBrowserSandbox from './ChatGPTBrowserSandbox.svelte';

  export let url: string | null = null;
  export let chatId = '';

  let sandboxOpen = false;
  $: conversationUrl = safeChatGPTConversationUrl(url);
</script>

<div class="mb-2">
  {#if conversationUrl && chatId}
    <button
      type="button"
      data-testid="open-chatgpt-conversation"
      aria-label="Show ChatGPT relay browser in sandbox"
      title="Control the same logged-in ChatGPT relay browser here, without opening another tab."
      class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
      on:click={() => (sandboxOpen = true)}
    >
      Show ChatGPT in sandbox
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M12 4v16M16 9h2M16 13h2" />
      </svg>
    </button>
  {:else}
    <button
      type="button" disabled data-testid="open-chatgpt-conversation"
      title="The sandbox becomes available once ChatGPT confirms this conversation's URL."
      class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-500 opacity-60 disabled:cursor-not-allowed dark:border-gray-700 dark:text-gray-400"
    >Show ChatGPT in sandbox</button>
  {/if}
  {#if sandboxOpen && conversationUrl && chatId}
    <ChatGPTBrowserSandbox {chatId} on:close={() => (sandboxOpen = false)} />
  {/if}
</div>

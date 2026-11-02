<script lang="ts">
  import { safeChatGPTConversationUrl } from '$lib/utils/chatgptConversation';
  import ChatGPTBrowserSandbox from './ChatGPTBrowserSandbox.svelte';

  export let url: string | null = null;
  export let chatId = '';

  let sandboxOpen = false;
  $: conversationUrl = safeChatGPTConversationUrl(url);
</script>

<div class="mb-2">
  {#if chatId}
    <button
      type="button"
      data-testid="open-chatgpt-conversation"
      aria-label="Show ChatGPT relay browser in sandbox"
      title={conversationUrl
        ? 'Show this verified ChatGPT conversation inside ABiT AI.'
        : 'Check the linked ChatGPT conversation. Opening the sandbox never resends your message.'}
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
  {/if}
  {#if sandboxOpen && chatId}
    <ChatGPTBrowserSandbox {chatId} on:close={() => (sandboxOpen = false)} />
  {/if}
</div>

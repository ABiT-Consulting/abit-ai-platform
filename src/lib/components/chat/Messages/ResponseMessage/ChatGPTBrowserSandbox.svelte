<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import { WEBUI_API_BASE_URL } from '$lib/constants';

  export let chatId: string;

  const dispatch = createEventDispatcher<{ close: void }>();
  let image = '';
  let imageWidth = 1280;
  let imageHeight = 720;
  let imageElement: HTMLImageElement;
  let draft = '';
  let error = '';
  let ready = false;
  let opening = false;
  let refreshing = false;
  let acting = false;
  let destroyed = false;
  let expanded = false;
  let poll: ReturnType<typeof setInterval> | undefined;

  const request = async (operation: string, payload: Record<string, unknown>) => {
    const response = await fetch(`${WEBUI_API_BASE_URL}/chatgpt-relay/browser/${operation}`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.token}`
      },
      body: JSON.stringify({ ...payload, chat_id: chatId })
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.message || 'Relay browser unavailable');
    return body;
  };

  const refresh = async () => {
    if (!ready || refreshing || acting || destroyed) return;
    refreshing = true;
    try {
      const result = await request('view', {});
      if (destroyed) return;
      const screenshot = String(result.image || '');
      if (!screenshot.startsWith('data:image/jpeg;base64,')) {
        throw new Error('Invalid relay browser screenshot');
      }
      image = screenshot;
      imageWidth = Number(result.width) || 1280;
      imageHeight = Number(result.height) || 720;
      error = '';
    } catch (e) {
      if (!destroyed) {
        image = ''; // Never retain a frame after losing conversation authorization.
        error = e instanceof Error ? e.message : 'Unable to view the relay';
      }
    } finally {
      refreshing = false;
    }
  };

  const connect = async () => {
    if (opening || destroyed) return;
    opening = true;
    error = '';
    try {
      await request('open', {});
      if (destroyed) return;
      ready = true;
      await refresh();
    } catch (e) {
      if (!destroyed) {
        ready = false;
        image = '';
        error = e instanceof Error ? e.message : 'Could not open the relay conversation';
      }
    } finally {
      opening = false;
    }
  };

  const action = async (payload: Record<string, unknown>) => {
    if (!ready || acting || destroyed || !image) return;
    acting = true;
    try {
      await request('input', payload);
      error = '';
    } catch (e) {
      if (!destroyed) {
        image = '';
        ready = false;
        error = e instanceof Error ? e.message : 'Could not control the relay browser';
      }
    } finally {
      acting = false;
    }
    if (ready && !destroyed) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      await refresh();
    }
  };

  const coordinates = (event: MouseEvent | WheelEvent) => {
    if (!imageElement) return null;
    const bounds = imageElement.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return null;
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom) return null;
    return {
      x: Math.max(0, Math.min(imageWidth, ((event.clientX - bounds.left) / bounds.width) * imageWidth)),
      y: Math.max(0, Math.min(imageHeight, ((event.clientY - bounds.top) / bounds.height) * imageHeight))
    };
  };

  const clickScreen = (event: MouseEvent) => {
    const location = coordinates(event);
    if (location) void action({ kind: 'click', ...location });
  };

  const wheelScreen = (event: WheelEvent) => {
    const location = coordinates(event);
    if (location && event.deltaY !== 0) {
      void action({ kind: 'scroll', direction: event.deltaY < 0 ? -1 : 1, ...location });
    }
  };

  const keyboardScreen = (event: KeyboardEvent) => {
    if (!image) return;
    const key = event.ctrlKey && event.key.toLowerCase() === 'a' ? 'Ctrl+A' : event.key;
    if (['Ctrl+A', 'Enter', 'Tab', 'Escape', 'Backspace', 'Delete',
      'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) {
      event.preventDefault();
      void action({ kind: 'key', key });
    } else if (!event.ctrlKey && !event.metaKey && !event.altKey && key.length === 1) {
      event.preventDefault();
      void action({ kind: 'text', text: key });
    }
  };

  const typeDraft = async () => {
    if (!draft || acting) return;
    const text = draft;
    await action({ kind: 'text', text });
    if (ready) draft = '';
  };

  onMount(() => {
    void connect();
    poll = setInterval(() => {
      if (ready && !opening) void refresh();
    }, 2800);
    return () => {
      destroyed = true;
      image = '';
      draft = '';
      if (poll) clearInterval(poll);
    };
  });
</script>

<!-- A same-page remote viewport, not a ChatGPT iframe or a new browser tab. -->
<div class="fixed inset-0 z-[100] flex justify-end bg-black/50" data-testid="relay-sandbox">
  <div
    class="flex h-full min-w-0 flex-col overflow-hidden border-l border-gray-300 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-950 {expanded ? 'w-full' : 'w-full lg:w-[min(78vw,1050px)]'}"
    role="dialog"
    aria-modal="true"
    aria-label="ChatGPT relay browser sandbox"
  >
    <header class="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 dark:border-gray-800">
      <div class="min-w-0">
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">ChatGPT relay browser sandbox</h2>
        <p class="text-xs text-gray-500 dark:text-gray-400">
          Same client-side relay session · click approvals or edit prompts here
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <button type="button" class="rounded-lg border border-gray-300 px-2 py-1.5 text-xs dark:border-gray-700" on:click={() => (expanded = !expanded)}>
          {expanded ? 'Dock right' : 'Expand'}
        </button>
        <button type="button" class="rounded-lg border border-gray-300 px-2 py-1.5 text-xs dark:border-gray-700"
          aria-label="Close relay sandbox" data-testid="close-relay-sandbox" on:click={() => dispatch('close')}>✕</button>
      </div>
    </header>

    <div class="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto bg-gray-50 p-3 dark:bg-gray-900">
      {#if error}
        <div role="alert" class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
          <span>{error}</span>
          <button type="button" class="rounded border border-current px-2 py-1" on:click={connect} disabled={opening}>Reconnect</button>
        </div>
      {/if}
      <div class="flex items-center justify-between gap-2 text-xs text-gray-600 dark:text-gray-300">
        <span aria-live="polite">{opening ? 'Opening the relay tab…' : image ? 'Live relay browser' : 'Waiting for the relay browser…'}</span>
        <button type="button" class="rounded-md border border-gray-300 px-2 py-1 dark:border-gray-700"
          disabled={!ready || acting || refreshing} on:click={refresh}>Refresh screen</button>
      </div>
      <div class="flex min-h-[220px] w-full flex-1 items-center justify-center overflow-auto rounded-xl border border-gray-300 bg-gray-950 dark:border-gray-700">
        {#if image}
          <button type="button" class="block w-full cursor-crosshair text-left outline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Interactive relay browser screen" title="Click to interact; focus and type on your keyboard"
            disabled={acting} on:click={clickScreen} on:wheel|preventDefault={wheelScreen} on:keydown={keyboardScreen}>
            <img bind:this={imageElement} src={image} alt="Current screen of the existing ChatGPT relay tab"
              class="block h-auto w-full" draggable="false" />
          </button>
        {:else}
          <p class="px-4 py-20 text-center text-sm text-gray-400">The relay browser screen will appear here.</p>
        {/if}
      </div>
      <div class="shrink-0 space-y-2 rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-950">
        <label for="relay-browser-text" class="block text-xs font-medium text-gray-700 dark:text-gray-200">
          Type or paste into the selected field in ChatGPT
        </label>
        <div class="flex gap-2">
          <textarea id="relay-browser-text" rows="2" maxlength="12000" bind:value={draft}
            placeholder="Click the target field above, then enter text here"
            class="min-w-0 flex-1 resize-none rounded-lg border border-gray-300 bg-transparent px-2 py-1.5 text-sm dark:border-gray-700"
            on:keydown={(e) => { if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); void typeDraft(); } }}></textarea>
          <button type="button" on:click={typeDraft} disabled={!image || acting || !draft}
            class="rounded-lg bg-gray-900 px-4 py-2 text-xs font-medium text-white disabled:opacity-50 dark:bg-gray-200 dark:text-gray-900">
            Type
          </button>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          {#each ['Ctrl+A', 'Backspace', 'Tab', 'Enter', 'Escape', 'ArrowUp', 'ArrowDown'] as key}
            <button type="button" disabled={!image || acting} on:click={() => action({ kind: 'key', key })}
              class="rounded-md border border-gray-300 px-2.5 py-1.5 text-xs disabled:opacity-50 dark:border-gray-700">{key}</button>
          {/each}
          <span class="ml-auto text-xs text-gray-500 dark:text-gray-400">Scroll on screen · no new tab</span>
        </div>
      </div>
    </div>
  </div>
</div>

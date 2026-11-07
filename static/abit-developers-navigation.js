/**
 * Compatibility navigation for incremental Developers-module releases.
 * The normal Svelte sidebar owns this link after a full application build.
 * This adds only the missing link; it does not replace application UI or state.
 */
(() => {
  'use strict';
  const script = document.currentScript;
  const scriptPath = script && script.src ? new URL(script.src, window.location.href).pathname : '';
  const rootPath = scriptPath.endsWith('/developer-ui/navigation.js')
    ? scriptPath.slice(0, -'/developer-ui/navigation.js'.length)
    : '';
  const href = rootPath + '/developers';
  let scheduled = false;

  function link(collapsed) {
    const wrapper = document.createElement('div');
    wrapper.dataset.abitDeveloperNavigation = 'true';
    wrapper.className = collapsed ? '' : 'px-1 text-gray-700 dark:text-gray-300';
    const anchor = document.createElement('a');
    anchor.id = 'sidebar-developers-button';
    anchor.href = href;
    anchor.setAttribute('data-sveltekit-reload', '');
    anchor.setAttribute('aria-label', 'API & Developers');
    anchor.title = 'API & Developers';
    anchor.draggable = false;
    anchor.className = collapsed
      ? 'group cursor-pointer flex size-8 items-center justify-center rounded-lg transition hover:bg-gray-100 dark:hover:bg-gray-900'
      : 'group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none';
    anchor.style.minHeight = collapsed ? '2rem' : '2.25rem';
    anchor.addEventListener('click', (event) => event.stopPropagation());

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.5');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', 'size-4 shrink-0');
    svg.style.width = '1rem';
    svg.style.height = '1rem';
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'm8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16');
    svg.append(path);
    anchor.append(svg);
    if (!collapsed) {
      const label = document.createElement('span');
      label.className = 'text-[0.8125rem] leading-5';
      label.textContent = 'API & Developers';
      anchor.append(label);
    }
    wrapper.append(anchor);
    return wrapper;
  }

  function ensureLink() {
    scheduled = false;
    const sidebar = document.getElementById('sidebar');
    if (!sidebar || sidebar.querySelector('a[href="' + href + '"]')) return;

    const pinned = sidebar.querySelector('#pinned-menu-items-list');
    if (pinned && pinned.parentElement) {
      pinned.before(link(false));
      return;
    }

    // The collapsed sidebar groups each icon in a two-level tooltip wrapper.
    const telegram = sidebar.querySelector('a[href="' + rootPath + '/dot/telegram"]');
    const reference = telegram || sidebar.querySelector('a[aria-label="New Chat"]');
    const item = reference && reference.parentElement && reference.parentElement.parentElement;
    if (item && item.parentElement && item.parentElement !== sidebar && sidebar.contains(item)) {
      item.after(link(true));
    }
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(ensureLink);
  }

  const observer = new MutationObserver(schedule);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  schedule();
})();

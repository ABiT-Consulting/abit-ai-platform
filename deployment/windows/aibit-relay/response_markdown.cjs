'use strict';

// Self-contained: serialized into the existing browser session, without a
// clipboard read, API request, navigation, or change to current-turn selection.
function assistantMarkdown(assistant) {
  if (!assistant) return '';
  const codeBlocks = [];
  const escapeText = (text) => String(text || '').replace(/\s+/g, ' ')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/[\\`*_[\]~]/g, '\\$&')
    .replace(/(^|\s)(#{1,6}|[-+])(?=\s)/g, '$1\\$2')
    .replace(/(^|\s)(\d+)\.(?=\s)/g, '$1$2\\.');
  const safeUrl = (value) => {
    if (!value) return '';
    try {
      const url = new URL(value, assistant.ownerDocument.baseURI);
      return /^(https?:|mailto:)$/.test(url.protocol)
        ? url.href.replace(/[<>\\\s]/g, (c) => encodeURIComponent(c)) : '';
    } catch (_) { return ''; }
  };
  const skip = (el) => el.matches(
    'script,style,noscript,button,svg,[hidden],[aria-hidden="true"],.sr-only,' +
    '[data-conversation-role],[data-testid="copy-turn-action-button"]'
  ) || el.style.display === 'none' || el.style.visibility === 'hidden';
  const children = (el) => Array.from(el.childNodes).map(render).join('');
  const block = (value) => value.trim() ? '\n\n' + value.trim() + '\n\n' : '';
  const marked = (value, marker) => value.trim()
    ? value.match(/^\s*/)[0] + marker + value.trim() + marker + value.match(/\s*$/)[0] : value;
  function render(el) {
    if (el.nodeType === 3) return escapeText(el.nodeValue);
    if (el.nodeType !== 1 || skip(el)) return '';
    const tag = el.tagName.toLowerCase();
    if (tag === 'pre') {
      const code = el.querySelector('code') || el;
      const text = code.textContent.replace(/\r\n/g, '\n').replace(/\n$/, '');
      const runs = text.match(/`+/g) || [];
      const fence = '`'.repeat(Math.max(3, ...runs.map((s) => s.length + 1)));
      const language = (code.className.match(/(?:^|\s)language-([\w+-]+)/) || [])[1] || '';
      const token = '\uE000RELAY_CODE_' + codeBlocks.length + '\uE001';
      codeBlocks.push(fence + language + '\n' + text + '\n' + fence);
      return block(token);
    }
    if (tag === 'code') {
      const text = el.textContent.replace(/\r?\n/g, ' ');
      if (!text) return '';
      const runs = text.match(/`+/g) || [];
      const fence = '`'.repeat(Math.max(1, ...runs.map((s) => s.length + 1)));
      const pad = /^`|`$/.test(text) || (/^ .* $/.test(text) && text.trim()) ? ' ' : '';
      return fence + pad + text + pad + fence;
    }
    if (el.matches('.katex-display,.katex')) {
      const math = el.querySelector('annotation[encoding="application/x-tex"]');
      if (math) return el.matches('.katex-display')
        ? block('$$\n' + math.textContent + '\n$$') : '$' + math.textContent + '$';
    }
    if (tag === 'table') {
      const rows = Array.from(el.querySelectorAll('tr')).filter((row) => row.closest('table') === el);
      if (!rows.length) return '';
      const cells = rows.map((row) => Array.from(row.children).filter((cell) => /^(TH|TD)$/.test(cell.tagName)));
      const width = Math.max(...cells.map((row) => row.length));
      if (!width) return '';
      const line = (row) => '| ' + Array.from({length: width}, (_, i) =>
        row[i] ? children(row[i]).trim().replace(/\n+/g, '<br>').replace(/\|/g, '\\|') : ''
      ).join(' | ') + ' |';
      const hasHeader = cells[0].some((cell) => cell.tagName === 'TH');
      const header = hasHeader ? cells.shift() : [];
      const aligns = header.length ? header : cells[0];
      const divider = '| ' + Array.from({length: width}, (_, i) => {
        const cell = aligns[i];
        const align = cell && (cell.getAttribute('align') || cell.getAttribute('data-d-align') || cell.style.textAlign);
        return /^(right|end)$/.test(align) ? '---:' : align === 'center' ? ':---:' : '---';
      }).join(' | ') + ' |';
      return block([line(header), divider, ...cells.map(line)].join('\n'));
    }
    if (tag === 'ul' || tag === 'ol') {
      let number = Number(el.getAttribute('start')) || 1;
      const items = Array.from(el.children).filter((node) => node.tagName === 'LI').map((li) => {
        if (skip(li)) return '';
        if (li.hasAttribute('value')) number = Number(li.getAttribute('value')) || number;
        const prefix = tag === 'ol' ? number++ + '. ' : '- ';
        const value = children(li).trim().replace(/\n{3,}/g, '\n\n');
        return value ? prefix + value.replace(/\n/g, '\n' + ' '.repeat(prefix.length)) : '';
      }).filter(Boolean);
      return block(items.join('\n'));
    }
    if (tag === 'br') return '\n';
    if (tag === 'hr') return block('---');
    if (tag === 'img') {
      const url = safeUrl(el.getAttribute('src') || '');
      return url ? '![' + escapeText(el.getAttribute('alt') || '') + '](<' + url + '>)' : '';
    }
    const value = children(el);
    if (tag === 'a') {
      const url = safeUrl(el.getAttribute('href') || '');
      return url && value.trim() ? '[' + value.trim() + '](<' + url + '>)' : value;
    }
    if (tag === 'strong' || tag === 'b' || el.hasAttribute('data-d-default-strong')) return marked(value, '**');
    if (tag === 'em' || tag === 'i' || el.hasAttribute('data-d-default-italic')) return marked(value, '*');
    if (tag === 'del' || tag === 's') return marked(value, '~~');
    if (/^h[1-6]$/.test(tag)) return block('#'.repeat(Number(tag[1])) + ' ' + value.trim());
    if (tag === 'blockquote') return block(value.trim().replace(/^/gm, '> '));
    if (el.getAttribute('data-d-component') === 'badge') return block(marked(value, '**'));
    if (/^(p|div|section|article|figure|figcaption|details|summary|li)$/.test(tag)) return block(value);
    return value;
  }
  const selector = '[data-markdown-text-style="assistant-message"],.markdown,.prose';
  const candidates = assistant.matches(selector) ? [assistant] : Array.from(assistant.querySelectorAll(selector));
  const roots = candidates.filter((node) => !candidates.some((other) => other !== node && other.contains(node)));
  return (roots.length ? roots : [assistant]).map(render).join('\n\n')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
    .replace(/(^[ \t>]*(?:(?:[-+*]|\d+\.) )?)\uE000RELAY_CODE_(\d+)\uE001/gm, (_, prefix, i) => {
      const indent = prefix.replace(/(?:[-+*]|\d+\.) /g, (marker) => ' '.repeat(marker.length));
      return prefix + codeBlocks[Number(i)].replace(/\n/g, '\n' + indent);
    });
}

module.exports = { assistantMarkdown };

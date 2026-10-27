'use strict';

// Read-only conversation fallback for replies missing from the ChatGPT DOM.
// No resend, navigation, session credentials or conversation data leaves Chrome.

function conversationId(url) {
  const match = String(url || '').match(/^https:\/\/chatgpt\.com\/(?:g\/g-p-[a-z0-9_-]+\/)?c\/([a-z0-9_-]{5,190})(?:[/?#]|$)/i);
  return match ? match[1] : '';
}

async function browserConversationTail(id, expectedUrl) {
  try {
    // Never read a different project/thread, even if a shared Chrome tab moves.
    const expected = new URL(expectedUrl);
    if (location.origin !== 'https://chatgpt.com' ||
        location.pathname !== expected.pathname ||
        expected.origin !== location.origin) return { status: 409 };

    const auth = await fetch('/api/auth/session', {
      credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.timeout(7000),
    });
    if (!auth.ok) return { status: auth.status };
    const identity = await auth.json();
    if (!identity.accessToken || !identity.user?.email) return { status: 401 };
    const response = await fetch('/backend-api/conversation/' + encodeURIComponent(id), {
      credentials: 'same-origin', cache: 'no-store',
      headers: { Authorization: 'Bearer ' + identity.accessToken },
      signal: AbortSignal.timeout(9000),
    });
    if (!response.ok) return { status: response.status };
    const data = await response.json();
    if (data.conversation_id && data.conversation_id !== id) return { status: 409 };
    if (!data.mapping || !data.current_node) return { status: 200 };

    // Never accept a terminal answer while the current branch ends at a
    // tool/user message or any unfinished child node.
    const leafRole = String(data.mapping[data.current_node]?.message?.author?.role || '');
    const seen = new Set();
    const assistants = [];
    let nodeId = data.current_node;
    for (let i = 0; i < 600 && nodeId && !seen.has(nodeId); i++) {
      seen.add(nodeId);
      const node = data.mapping[nodeId];
      if (!node) break;
      const message = node.message;
      if (message?.author?.role === 'user') {
        return {
          status: 200,
          leafRole,
          userId: String(message.id || node.id || nodeId),
          userText: Array.isArray(message.content?.parts)
            ? message.content.parts.filter(part => typeof part === 'string').join('\n').trim() : '',
          assistants: assistants.reverse(),
        };
      }
      if (message?.author?.role === 'assistant') {
        assistants.push({
          id: String(message.id || node.id || nodeId),
          recipient: message.recipient || '',
          status: message.status || '',
          endTurn: message.end_turn === true,
          hidden: message.metadata?.is_visually_hidden_from_conversation === true,
          text: Array.isArray(message.content?.parts)
            ? message.content.parts.filter(part => typeof part === 'string').join('\n').trim() : '',
        });
      }
      nodeId = node.parent;
    }
    return { status: 200 };
  } catch (_) {
    return { status: 0 };
  }
}

async function readConversationTail(session, url) {
  const id = conversationId(url);
  if (!id) return { status: 0 };
  // All upstream session tokens are used within the existing browser tab only.
  const expression = '(' + browserConversationTail.toString() + ')(' +
    JSON.stringify(id) + ',' + JSON.stringify(String(url)) + ')';
  return session.evaluate(expression);
}

function verifiedCurrentReply(tail, prompt, baselineUserId, isNewConversation) {
  if (!tail || tail.status !== 200 || !tail.userId || tail.leafRole !== 'assistant') return '';
  // A follow-up cannot be reconciled unless its pre-submit user ID was read.
  if (!isNewConversation && (!baselineUserId || tail.userId === baselineUserId)) return '';
  const normalize = value => String(value || '').replace(/\r\n/g, '\n').trim();
  if (!normalize(prompt) || normalize(tail.userText) !== normalize(prompt)) return '';

  const assistants = Array.isArray(tail.assistants) ? tail.assistants : [];
  const last = assistants.length ? assistants[assistants.length - 1] : null;
  if (last?.recipient !== 'all' || last.hidden) return '';
  // Require explicit terminal evidence. Do not return analysis, tool output,
  // partial text or a completed answer belonging to a previous user turn.
  return last && last.endTurn === true && last.status === 'finished_successfully' &&
    typeof last.text === 'string' && last.text.trim() ? normalize(last.text) : '';
}

module.exports = {
  conversationId,
  browserConversationTail,
  readConversationTail,
  verifiedCurrentReply,
};

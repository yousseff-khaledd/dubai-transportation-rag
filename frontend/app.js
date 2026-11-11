const storageKey = 'route-chat-v1';
const elements = Object.fromEntries([
  'new-chat', 'mode', 'mode-description', 'top-k', 'top-k-value', 'year', 'document-type',
  'settings-fields', 'settings-note', 'copy-thread', 'thread-id', 'connection', 'connection-label',
  'welcome', 'messages', 'thinking', 'notice', 'clarification-banner', 'chat-form', 'message',
  'composer-mode', 'character-count', 'send', 'conversation-area',
].map(id => [id, document.getElementById(id)]));

const modeDescriptions = {
  hybrid: 'Combines word matches and meaning.',
  lexical: 'Matches words using BM25 ranking.',
  semantic: 'Finds similar meaning using embeddings.',
};
const statusLabels = {
  answered: 'Cited answer', needs_clarification: 'Clarification',
  insufficient_evidence: 'Insufficient evidence', out_of_scope: 'Outside the collection', error: 'Request failed',
};

function newConversation(settings = { mode: 'hybrid', top_k: 10, year: '', type: '' }) {
  return { threadId: crypto.randomUUID(), messages: [], pendingInterrupt: null, settings, inFlight: false };
}

function activeFilters() {
  const filters = [];
  if (conversation.settings.year) filters.push(`year ${conversation.settings.year}`);
  if (conversation.settings.type) filters.push(conversation.settings.type);
  return filters;
}

function loadConversation() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    if (saved && typeof saved.threadId === 'string' && Array.isArray(saved.messages) && saved.settings) return saved;
  } catch { /* A fresh session also works when browser storage is unavailable. */ }
  return newConversation();
}

let conversation = loadConversation();
let busy = false;

function showNotice(message) {
  elements.notice.textContent = message;
  elements.notice.hidden = !message;
}

function saveConversation() {
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(conversation));
  } catch {
    showNotice('Browser storage is unavailable or full. Keep this tab open to preserve the conversation.');
  }
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function addMessage(message) {
  const article = createElement('article', `message message-${message.role}${message.status === 'error' ? ' message-error' : ''}`);
  const header = createElement('div', 'message-header');
  header.append(createElement('span', 'avatar', message.role === 'user' ? 'Y' : '↗'));
  header.append(createElement('span', 'message-author', message.role === 'user' ? 'You' : 'Route'));
  if (message.status) header.append(createElement('span', 'message-tag', statusLabels[message.status] || message.status));
  article.append(header, createElement('div', 'message-content', message.text));

  if (message.citations?.length) {
    const sources = createElement('div', 'source-list');
    sources.setAttribute('aria-label', 'Cited source chunks');
    message.citations.forEach((citation, index) => {
      const source = createElement('div', 'source');
      source.append(createElement('strong', '', `${index + 1}. Document ${citation.document_id}`));
      source.append(createElement('span', '', `PDF page ${citation.locator}`));
      sources.append(source);
    });
    article.append(sources);
  }

  if (message.response) {
    const details = createElement('details', 'response-details');
    details.append(createElement('summary', '', 'Response details'));
    details.append(createElement('pre', '', JSON.stringify(message.response, null, 2)));
    article.append(details);
  }
  elements.messages.append(article);
}

function refreshControls() {
  const pending = Boolean(conversation.pendingInterrupt);
  elements['settings-fields'].disabled = busy || pending;
  elements['settings-note'].hidden = !pending;
  elements['clarification-banner'].hidden = !pending;
  elements['new-chat'].disabled = busy;
  elements.message.disabled = busy;
  elements.send.disabled = busy || !elements.message.value.trim();
  elements.thinking.hidden = !busy;
  elements.welcome.hidden = conversation.messages.length > 0;
  elements['thread-id'].textContent = conversation.threadId;
  elements['thread-id'].title = conversation.threadId;
  const filters = activeFilters();
  elements['composer-mode'].textContent = pending ? 'Clarification reply'
    : `${elements.mode.selectedOptions[0].text} search${filters.length ? ` · ${filters.join(', ')}` : ' · all documents'}`;
  elements.message.placeholder = pending ? 'Add the missing context to continue…' : 'Ask about Dubai roads and transport…';
  elements['character-count'].textContent = `${elements.message.value.length.toLocaleString()} / 2,000`;
  elements['mode-description'].textContent = modeDescriptions[elements.mode.value];
  elements['top-k-value'].value = elements['top-k'].value;
}

function renderConversation() {
  elements.mode.value = conversation.settings.mode;
  elements['top-k'].value = conversation.settings.top_k;
  elements.year.value = conversation.settings.year;
  elements['document-type'].value = conversation.settings.type;
  elements.messages.replaceChildren();
  conversation.messages.forEach(addMessage);
  refreshControls();
}

function scrollToLatest() {
  if (window.matchMedia('(max-width: 720px)').matches) {
    const latest = elements.thinking.hidden ? elements.messages.lastElementChild : elements.thinking;
    latest?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  } else {
    elements['conversation-area'].scrollTop = elements['conversation-area'].scrollHeight;
  }
}

function appendMessage(message) {
  conversation.messages.push(message);
  addMessage(message);
  saveConversation();
}

async function checkConnection() {
  elements['connection-label'].textContent = 'Connecting';
  try {
    const response = await fetch('/api/health', { signal: AbortSignal.timeout(7000) });
    const health = await response.json();
    if (!response.ok || !health.ready) throw new Error('API unavailable');
    elements.connection.dataset.state = health.index_available ? 'online' : 'offline';
    elements['connection-label'].textContent = health.index_available ? 'API connected' : 'Index unavailable';
  } catch {
    elements.connection.dataset.state = 'offline';
    elements['connection-label'].textContent = 'API offline · retry';
  }
}

elements['chat-form'].addEventListener('submit', async event => {
  event.preventDefault();
  const message = elements.message.value.trim();
  if (busy || !message) return;
  if (!conversation.pendingInterrupt && !elements.year.reportValidity()) return;
  showNotice('');
  const interruptId = conversation.pendingInterrupt;
  const request = { thread_id: conversation.threadId, message };
  if (interruptId) {
    request.interrupt_id = interruptId;
  } else {
    request.mode = conversation.settings.mode;
    request.top_k = Number(conversation.settings.top_k);
    request.filters = {};
    if (conversation.settings.year !== '') request.filters.year = Number(conversation.settings.year);
    if (conversation.settings.type) request.filters.type = conversation.settings.type;
  }

  busy = true;
  conversation.inFlight = true;
  appendMessage({ role: 'user', text: message });
  elements.message.value = '';
  refreshControls();
  scrollToLatest();
  try {
    const response = await fetch(interruptId ? '/api/chat/resume' : '/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    });
    const result = await response.json();
    if (!response.ok || result.status === 'error') {
      const code = result.error?.code;
      if (code === 'invalid_resume') conversation.pendingInterrupt = null;
      const recovery = ['invalid_resume', 'clarification_pending'].includes(code)
        ? ' Start a new conversation if you cannot continue this question.' : '';
      appendMessage({ role: 'assistant', status: 'error', text: (result.error?.message || `Request failed (${response.status}).`) + recovery, response: result });
      elements.message.value = message;
    } else {
      conversation.pendingInterrupt = result.status === 'needs_clarification' ? result.interrupt_id : null;
      const filterDescription = activeFilters().join(', ');
      const text = result.status === 'needs_clarification' ? result.clarification
        : result.status === 'out_of_scope' ? 'This question is outside the Dubai Roads and Transport collection. Try asking about transport legislation.'
        : result.status === 'insufficient_evidence' ? filterDescription
          ? `I couldn’t establish a supported answer with ${filterDescription}. Clear the source filters or start a new conversation to search all documents.`
          : 'I couldn’t establish a supported answer from the indexed documents. Try naming the activity or document.'
        : result.answer;
      appendMessage({ role: 'assistant', status: result.status, text: text || 'No answer was returned.', citations: result.citations, response: result });
    }
  } catch {
    appendMessage({ role: 'assistant', status: 'error', text: 'The connection was interrupted. The API may still be processing your request. Check the server terminal before retrying; start a new conversation if the saved question cannot continue.' });
    elements.message.value = message;
  } finally {
    busy = false;
    conversation.inFlight = false;
    saveConversation();
    refreshControls();
    scrollToLatest();
    elements.message.focus({ preventScroll: true });
    checkConnection();
  }
});

elements.message.addEventListener('input', refreshControls);
elements.message.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements['chat-form'].requestSubmit();
  }
});

for (const id of ['mode', 'top-k', 'year', 'document-type']) {
  elements[id].addEventListener('input', () => {
    conversation.settings = { mode: elements.mode.value, top_k: Number(elements['top-k'].value),
      year: elements.year.value, type: elements['document-type'].value };
    saveConversation();
    refreshControls();
  });
}

elements['new-chat'].addEventListener('click', () => {
  conversation = newConversation();
  elements.message.value = '';
  showNotice('');
  saveConversation();
  renderConversation();
  elements.message.focus();
});

elements['copy-thread'].addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(conversation.threadId);
    elements['copy-thread'].textContent = 'Copied';
    setTimeout(() => { elements['copy-thread'].textContent = 'Copy ID'; }, 1800);
  } catch {
    showNotice(`Conversation ID: ${conversation.threadId}`);
  }
});

document.querySelectorAll('[data-question]').forEach(button => {
  button.addEventListener('click', () => {
    elements.message.value = button.dataset.question;
    refreshControls();
    elements.message.focus();
  });
});
elements.connection.addEventListener('click', checkConnection);

if (window.matchMedia('(max-width: 720px)').matches) document.querySelector('.search-settings').open = false;
renderConversation();
if (conversation.inFlight) {
  showNotice('This page was refreshed during a request. It may still be running. Check the server terminal; start a new conversation if it cannot continue.');
  conversation.inFlight = false;
}
saveConversation();
checkConnection();

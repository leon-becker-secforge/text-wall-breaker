// Live ask (ADR 0005): a thought-bubble on each option starts a discussion with the agent
// in a side panel; the operator still sends the answer with the page's own buttons. Runs
// after the ask script and works through its window.twbAsk hooks; the server side is
// live_ask.LiveAsk. A page opened with `live=true` starts in the calling state.
(function () {
  var A = window.twbAsk;
  if (!A || A.liveMode === 'off') return;
  var LIVE_URL = A.statusUrl.replace('/status?', '/live?');
  var SILENT_AFTER = 30;  // seconds of no agent call before the panel says so
  var POLL_MS = 1000;
  var ICON = __ICON__;

  var state = 'offer';    // offer | calling | joined | working | silent | ended
  var ready = false;      // the server has created the session
  var joinedOnce = false;
  var timer = null;
  var queue = [];         // messages typed before the session existed

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ---- the panel ----
  var panel = el('aside', 'twb-live-panel');
  panel.id = 'twb-live-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Discussion with the agent');
  // The page's Ctrl/Cmd+Enter shortcut skips events from inside this marker.
  panel.setAttribute('data-twb-no-shortcuts', '');
  // The chat belongs to one question: the panel shows the thread of the step on screen,
  // and the header names that question.
  var head = el('div', 'twb-live-head');
  head.appendChild(el('span', 'twb-live-ring'));
  head.appendChild(el('span', 'twb-live-title', 'Discuss with the agent'));
  var stepLabel = el('span', 'twb-live-step');
  head.appendChild(stepLabel);
  var subject = el('div', 'twb-live-subject');
  var status = el('div', 'twb-live-status');
  var list = el('div', 'twb-live-list');
  list.setAttribute('role', 'log');
  list.setAttribute('aria-live', 'polite');
  var inRow = el('div', 'twb-live-in');
  var prompt = el('span', 'twb-live-prompt', '›');
  prompt.setAttribute('aria-hidden', 'true');
  var box = el('textarea');
  box.rows = 1;
  box.placeholder = 'Ask the agent…';
  box.setAttribute('aria-label', 'Message to the agent');
  var sendBtn = el('button', 'twb-live-send');
  sendBtn.type = 'button';
  sendBtn.setAttribute('aria-label', 'Send message');
  sendBtn.title = 'Send (Enter)';
  sendBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" ' +
    'aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
  var hint = el('div', 'twb-live-hint', 'Enter to send · Shift+Enter for a new line');
  inRow.appendChild(prompt);
  inRow.appendChild(box);
  inRow.appendChild(sendBtn);
  [head, subject, status, list, inRow, hint].forEach(function (n) { panel.appendChild(n); });
  // Inside <main>: on a narrow viewport the panel flows under the form, in its column.
  (document.querySelector('main') || document.body).appendChild(panel);
  head.addEventListener('click', function () { panel.classList.toggle('is-folded'); });

  var TEXT = {
    calling: 'Calling the agent…',
    joined: 'The agent is here.',
    working: 'The agent is working on it…',
    silent: 'The agent has not joined yet. You can still answer below.',
    ended: 'Discussion ended.'
  };
  function setState(next) {
    state = next;
    panel.setAttribute('data-state', next);
    status.textContent = TEXT[next] || '';
    box.disabled = next === 'ended';
    sendBtn.disabled = next === 'ended' || !box.value.trim();
    redraw();
  }

  var scopes = document.querySelectorAll('.twb-step');
  if (!scopes.length) scopes = [document];
  function active() { return A.currentStep(); }
  function scopeOf(q) { return scopes[q] || document; }
  function titleOf(q) {
    // The bold lead sentence only: the rest of the question is context, too long for a header.
    var node = scopeOf(q).querySelector('.twb-question');
    if (!node) return '';
    var lead = node.querySelector('strong, b, p, h1, h2, h3') || node;
    return lead.textContent.replace(/\s+/g, ' ').trim();
  }
  function optionExists(q, id) {
    return Array.prototype.some.call(scopeOf(q).querySelectorAll('[data-option-id]'),
      function (n) { return n.dataset.optionId === id; });
  }
  function messageQuestion(m) { return m.q !== undefined ? m.q : (m.ref ? m.ref.question : 0); }

  function message(m) {
    var mine = m.from !== 'agent';
    var wrap = el('div', 'twb-live-msg ' + (mine ? 'op' : 'ag'));
    wrap.appendChild(el('div', 'twb-live-who', mine ? 'You' : 'Agent'));
    var bubble = el('div', 'twb-live-bubble');
    var optionId = m.option || (m.ref && m.ref.option);
    // A message names its option in its own text. Only an option the agent has since
    // replaced is quoted, struck through, since it is gone from the page.
    if (optionId && !optionExists(messageQuestion(m), optionId)) {
      var gone = el('div', 'twb-live-gone');
      gone.appendChild(el('span', '', 'earlier option'));
      gone.appendChild(el('s', '', m.label || (m.ref && m.ref.label) || optionId));
      bubble.appendChild(gone);
    }
    var text = el('div', 'twb-live-text' + (m.html ? ' md' : ''));
    if (m.html) text.innerHTML = m.html; else text.textContent = m.text;
    bubble.appendChild(text);
    wrap.appendChild(bubble);
    return wrap;
  }
  function typingRow() {
    var wrap = el('div', 'twb-live-msg ag twb-live-typing');
    wrap.appendChild(el('div', 'twb-live-who', 'Agent'));
    var bubble = el('div', 'twb-live-bubble');
    var dots = el('span', 'twb-live-dots');
    dots.setAttribute('role', 'img');
    dots.setAttribute('aria-label', 'The agent is working on it');
    for (var i = 0; i < 3; i++) dots.appendChild(el('i'));
    bubble.appendChild(dots);
    wrap.appendChild(bubble);
    return wrap;
  }
  // The list is the server's chat plus the operator's messages it has not acknowledged
  // yet, so a sent message shows at once and the server's copy replaces it. Only the
  // thread of the question on screen is drawn.
  var serverChat = [];
  var pending = [];
  function redraw() {
    var q = active();
    stepLabel.textContent = A.stepCount > 1 ? (q + 1) + ' of ' + A.stepCount : '';
    subject.textContent = titleOf(q);
    subject.title = subject.textContent;
    var stick = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
    list.textContent = '';
    var thread = serverChat.concat(pending).filter(function (m) { return messageQuestion(m) === q; });
    thread.forEach(function (m) { list.appendChild(message(m)); });
    // The agent owes an answer when the thread ends with the operator's message.
    var last = thread[thread.length - 1];
    if (last && last.from !== 'agent' && (state === 'calling' || state === 'working')) {
      list.appendChild(typingRow());
    }
    if (stick) list.scrollTop = list.scrollHeight;
  }
  function render(chat) {
    serverChat = chat;
    redraw();
  }
  // Moving to another question swaps the thread, and what was typed stays with its question.
  var drafts = {};
  var shown = active();
  A.onStep = function () {
    drafts[shown] = box.value;
    shown = active();
    box.value = drafts[shown] || '';
    grow();
    sendBtn.disabled = state === 'ended' || !box.value.trim();
    redraw();
  };

  async function post(data) {
    try {
      var res = await fetch(A.submitUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({ kind: 'live', data: data })
      });
      return res.ok;
    } catch (e) { return false; }
  }

  // A message is drawn as soon as it is typed or pressed, then sent once the session
  // exists; the next poll brings the server's copy, and `pending` drops this one.
  // `target` is the question and, from a bubble, the option the message is about.
  function enqueue(text, target) {
    var entry = {
      from: 'operator', text: text,
      ref: { question: target.question, option: target.option, label: target.label }
    };
    pending.push(entry);
    queue.push(entry);
    redraw();
    flush();
  }
  var sending = false;
  function flush() {
    if (sending || !ready || !queue.length) return;
    sending = true;
    var next = queue[0];
    post({ type: 'chat', text: next.text, ref: next.ref }).then(function (ok) {
      sending = false;
      if (!ok) return;
      queue.shift();
      pending.splice(pending.indexOf(next), 1);
      poll();
      flush();
    });
  }
  function submitMessage() {
    var text = box.value.trim();
    if (!text || state === 'ended') return;
    enqueue(text, { question: active() });
    box.value = '';
    grow();
    sendBtn.disabled = true;
  }
  function grow() {
    box.style.height = 'auto';
    box.style.height = Math.min(box.scrollHeight, 120) + 'px';
  }
  sendBtn.addEventListener('click', function () { submitMessage(); box.focus(); });
  box.addEventListener('input', function () {
    grow();
    sendBtn.disabled = state === 'ended' || !box.value.trim();
  });
  // Enter sends, Shift+Enter breaks the line. Every key stays in the box: the page's own
  // Ctrl/Cmd+Enter shortcut moves the wizard on or sends the answer, which a chat message
  // must never do (the page's handler also skips events from the panel).
  box.addEventListener('keydown', function (e) {
    e.stopPropagation();
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    submitMessage();
  });

  // ---- polling ----
  async function poll() {
    var body = null;
    try {
      var res = await fetch(LIVE_URL);
      if (res.ok) body = await res.json();
    } catch (e) {
      // The session closes its listener once it ends; after an answer that is expected.
      if (A.isFrozen()) { setState('ended'); stop(); }
      return;
    }
    if (!body || !body.session) return;
    if (body.generation !== A.generation) { reloadForRefresh(); return; }
    ready = true;
    render(body.chat || []);
    flush();
    if (body.ended) { setState('ended'); stop(); return; }
    if (body.agent.waiting) { joinedOnce = true; setState('joined'); }
    else if (joinedOnce) setState('working');
    else if (body.agent.seconds > SILENT_AFTER) setState('silent');
    else setState('calling');
  }
  // The agent replaced the question or options: the server holds the new page. What the
  // operator picked, typed in the answer and typed in the chat survives the reload.
  var DRAFT_KEY = 'twb-ask-live-draft';
  function reloadForRefresh() {
    stop();
    try {
      var answer = A.collect();
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({
        selected: answer.selected, free_text: answer.free_text, typed: box.value
      }));
    } catch (e) { /* storage refused: the reload still shows the new page */ }
    location.reload();
  }
  function restoreDraft() {
    var draft = null;
    try {
      draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY));
      sessionStorage.removeItem(DRAFT_KEY);
    } catch (e) { return; }
    if (!draft) return;
    Array.prototype.forEach.call(document.querySelectorAll('input[data-option-id]'), function (i) {
      var pick = draft.selected.indexOf(i.dataset.optionId) >= 0;
      if (i.checked !== pick) { i.checked = pick; i.dispatchEvent(new Event('change', { bubbles: true })); }
    });
    var free = document.getElementById('twb-free-text');
    if (free && draft.free_text) { free.value = draft.free_text; free.dispatchEvent(new Event('input', { bubbles: true })); }
    box.value = draft.typed || '';
    grow();
  }
  function start() { if (timer === null) { poll(); timer = setInterval(poll, POLL_MS); } }
  function stop() { if (timer !== null) { clearInterval(timer); timer = null; } }

  function open() {
    panel.hidden = false;
    panel.classList.remove('is-folded');
    box.focus();
  }

  // ---- entry: the bubble ----
  // `node` is an option row, a ranking row or a tier chip: whatever holds the label. The
  // whole label goes into the message, so two options sharing a prefix stay apart in the chat.
  function labelOf(node, id) {
    var label = node.querySelector('.twb-option-label');
    // The label holds the Recommended tag and Details pill; their text is not the name.
    var text = label.cloneNode(true);
    var tags = text.querySelector('.twb-option-tags');
    if (tags) tags.remove();
    return text.textContent.trim() || id;  // an image-only label has no text
  }
  // A press on a bubble is the question: it sends one at once, so the agent answers
  // without the operator typing. The text names the option, since the chat has no other
  // marker of what it is about; the server gets the option's id in `ref`.
  function askNow(next) {
    enqueue(next.option ? 'Tell me about “' + next.label + '”.' : 'Tell me about this question.', next);
  }
  async function begin(next) {
    if (state !== 'offer') { open(); askNow(next); return; }
    A.liveStarted = true;
    setState('calling');
    open();
    askNow(next);  // drawn now, sent once the session exists
    if (!(await A.send('live_start', A.collect()))) {
      A.liveStarted = false;
      queue.length = 0;
      pending.length = 0;
      redraw();
      setState('offer');
      panel.hidden = true;
      return;
    }
    start();
  }
  function bubble(onClick, label) {
    var b = el('button', 'twb-live-ask');
    b.type = 'button';
    b.innerHTML = ICON;
    b.setAttribute('aria-label', label);
    b.title = label;
    b.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); onClick(); });
    return b;
  }
  Array.prototype.forEach.call(scopes, function (scope, index) {
    var rows = scope.querySelectorAll('.twb-option');
    Array.prototype.forEach.call(rows, function (row) {
      var input = row.querySelector('input[data-option-id]');
      if (!input) return;
      var label = labelOf(row, input.dataset.optionId);
      row.appendChild(bubble(function () {
        begin({ question: index, option: input.dataset.optionId, label: label });
      }, 'Ask the agent about ' + label));
    });
    // A ranking row or tier chip carries its option id itself; the question keeps its own
    // bubble too, for the order as a whole.
    var ranked = scope.querySelectorAll('.twb-rank-item, .twb-tier-chip');
    Array.prototype.forEach.call(ranked, function (node) {
      var label = labelOf(node, node.dataset.optionId);
      node.appendChild(bubble(function () {
        begin({ question: index, option: node.dataset.optionId, label: label });
      }, 'Ask the agent about ' + label));
    });
    // A question without options has no row to hang the bubble on.
    var q = scope.querySelector('.twb-question');
    if (!rows.length && q) {
      q.appendChild(bubble(function () { begin({ question: index }); },
        'Ask the agent about this question'));
    }
  });

  restoreDraft();
  if (A.liveMode === 'on') {
    A.liveStarted = true;
    setState('calling');
    open();
    start();
  } else {
    setState('offer');
  }
}());

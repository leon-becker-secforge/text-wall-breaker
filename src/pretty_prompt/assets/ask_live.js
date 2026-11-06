// Live ask (ADR 0005): a thought-bubble on each option starts a discussion with the agent
// in a side panel; the operator still sends the answer with the page's own buttons. Runs
// after the ask script and works through its window.ppAsk hooks; the server side is
// live_ask.LiveAsk. A page opened with `live=true` starts in the calling state.
(function () {
  var A = window.ppAsk;
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
  var ref = null;         // {question, option, label}, what the next message is about
  var shown = 0;          // chat entries already rendered

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ---- the panel ----
  var panel = el('aside', 'pp-live-panel');
  panel.id = 'pp-live-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Discussion with Claude');
  var head = el('div', 'pp-live-head');
  head.appendChild(el('span', 'pp-live-ring'));
  head.appendChild(el('span', '', 'Discuss with Claude'));
  var status = el('div', 'pp-live-status');
  var list = el('div', 'pp-live-list');
  list.setAttribute('aria-live', 'polite');
  var refBox = el('div', 'pp-live-ref');
  refBox.hidden = true;
  var inRow = el('div', 'pp-live-in');
  var box = el('textarea');
  box.rows = 2;
  box.placeholder = 'Ask Claude about this question…';
  var sendBtn = el('button', 'pp-live-send', 'Send');
  sendBtn.type = 'button';
  inRow.appendChild(box);
  inRow.appendChild(sendBtn);
  [head, status, list, refBox, inRow].forEach(function (n) { panel.appendChild(n); });
  // Inside <main>: on a narrow viewport the panel flows under the form, in its column.
  (document.querySelector('main') || document.body).appendChild(panel);
  head.addEventListener('click', function () { panel.classList.toggle('is-folded'); });

  var TEXT = {
    calling: 'Calling Claude…',
    joined: 'Claude is here.',
    working: 'Claude is working on it…',
    silent: 'Claude has not joined yet. You can still answer below.',
    ended: 'Discussion ended.'
  };
  function setState(next) {
    state = next;
    panel.setAttribute('data-state', next);
    status.textContent = TEXT[next] || '';
    box.disabled = next === 'ended';
    sendBtn.disabled = next === 'ended';
  }

  function message(m) {
    var wrap = el('div', 'pp-live-msg');
    wrap.appendChild(el('div', 'pp-live-who' + (m.from === 'agent' ? ' agent' : ''),
      m.from === 'agent' ? 'Claude' : 'You'));
    if (m.label) wrap.appendChild(el('span', 'pp-live-chip', 're: ' + m.label));
    var text = el('div', 'pp-live-text' + (m.html ? ' md' : ''));
    if (m.html) text.innerHTML = m.html; else text.textContent = m.text;
    wrap.appendChild(text);
    return wrap;
  }
  function render(chat) {
    if (chat.length === shown) return;
    var stick = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
    for (var i = shown; i < chat.length; i++) list.appendChild(message(chat[i]));
    shown = chat.length;
    if (stick) list.scrollTop = list.scrollHeight;
  }

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

  // The operator's own messages come back through poll(), so none is drawn locally:
  // what shows is what the server holds.
  function flush() {
    if (!ready || !queue.length) return;
    var next = queue.shift();
    post({ type: 'chat', text: next.text, ref: next.ref }).then(function (ok) {
      if (!ok) queue.unshift(next);
      else flush();
    });
  }
  function submitMessage() {
    var text = box.value.trim();
    if (!text || state === 'ended') return;
    queue.push({ text: text, ref: ref ? { question: ref.question, option: ref.option } : null });
    box.value = '';
    clearRef();
    flush();
  }
  sendBtn.addEventListener('click', submitMessage);
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitMessage(); }
  });

  function setRef(next) {
    ref = next;
    refBox.hidden = false;
    refBox.textContent = '';
    var chip = el('span', 'pp-live-chip', 're: ' + next.label + ' ×');
    chip.title = 'Click to remove';
    chip.addEventListener('click', clearRef);
    refBox.appendChild(chip);
  }
  function clearRef() { ref = null; refBox.hidden = true; }

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
    ready = true;
    render((body.chat || []).map(function (m) {
      return m.from === 'operator' ? Object.assign({ label: labelFor(m) }, m) : m;
    }));
    flush();
    if (body.ended) { setState('ended'); stop(); return; }
    if (body.agent.waiting) { joinedOnce = true; setState('joined'); }
    else if (joinedOnce) setState('working');
    else if (body.agent.seconds > SILENT_AFTER) setState('silent');
    else setState('calling');
  }
  function labelFor(m) {
    // The server names the option by id; the page shows the option's own label.
    if (!m.option) return null;
    var input = document.querySelector('input[data-option-id="' + CSS.escape(m.option) + '"]');
    return input ? labelOf(input) : m.option;
  }
  function start() { if (timer === null) { poll(); timer = setInterval(poll, POLL_MS); } }
  function stop() { if (timer !== null) { clearInterval(timer); timer = null; } }

  function open(next) {
    panel.hidden = false;
    panel.classList.remove('is-folded');
    if (next) setRef(next);
    box.focus();
  }

  // ---- entry: the bubble ----
  function labelOf(input) {
    var row = input.closest('.pp-option');
    var text = row && row.querySelector('.pp-option-label');
    return text ? text.textContent.trim().split(':')[0] : input.dataset.optionId;
  }
  async function begin(next) {
    if (state !== 'offer') { open(next); return; }
    A.liveStarted = true;
    setState('calling');
    open(next);
    if (!(await A.send('live_start', A.collect()))) {
      A.liveStarted = false;
      setState('offer');
      panel.hidden = true;
      return;
    }
    start();
  }
  function bubble(onClick, label) {
    var b = el('button', 'pp-live-ask');
    b.type = 'button';
    b.innerHTML = ICON;
    b.setAttribute('aria-label', label);
    b.title = label;
    b.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); onClick(); });
    return b;
  }
  var scopes = document.querySelectorAll('.pp-step');
  if (!scopes.length) scopes = [document];
  Array.prototype.forEach.call(scopes, function (scope, index) {
    var rows = scope.querySelectorAll('.pp-option');
    Array.prototype.forEach.call(rows, function (row) {
      var input = row.querySelector('input[data-option-id]');
      if (!input) return;
      var label = labelOf(input);
      row.appendChild(bubble(function () {
        begin({ question: index, option: input.dataset.optionId, label: label });
      }, 'Ask Claude about ' + label));
    });
    // A question without options has no row to hang the bubble on.
    var q = scope.querySelector('.pp-question');
    if (!rows.length && q) {
      q.appendChild(bubble(function () { begin(null); }, 'Ask Claude about this question'));
    }
  });

  if (A.liveMode === 'on') {
    A.liveStarted = true;
    setState('calling');
    open(null);
    start();
  } else {
    setState('offer');
  }
}());

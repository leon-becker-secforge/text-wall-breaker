// Live review (ADR 0004): the session card with its chat, threads under comments, the
// comment box's "Ask the agent now" switch, updates held while that box is open,
// outdated comments, and the "since you looked" view. Runs after the review script and
// works through its window.ppReview hooks; the server side is live.LiveSession.
(function () {
  var R = window.ppReview;
  if (!R || R.liveMode === 'off') return;
  var LIVE_URL = R.statusUrl.replace('/status?', '/live?');
  var SILENT_AFTER = 180;
  var POLL_MS = 1500;

  var active = R.liveMode === 'on';
  var state = null;          // the last GET /live body
  var held = false;          // a new generation waits for the comment box to close
  var askNow = true;         // the comment box's switch
  var chatOpen = false;
  var frozen = false;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function message(m) {
    var box = el('div', 'pp-live-msg');
    var author = el('div', 'pp-live-author' + (m.from === 'agent' ? ' agent' : ''),
      m.from === 'agent' ? 'Agent' : 'You');
    var text = el('div', 'pp-live-text' + (m.html ? ' md' : ''));
    if (m.html) text.innerHTML = m.html; else text.textContent = m.text;
    box.appendChild(author);
    box.appendChild(text);
    return box;
  }
  async function post(data) {
    try {
      var res = await fetch(R.submitUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({ kind: 'live', data: data })
      });
      return res.ok ? await res.json() : null;
    } catch (e) { return null; }
  }

  // ---- the session card, fixed at the top of the file list ----
  var nav = document.getElementById('pp-files');
  var card = el('div', 'pp-live-card');
  card.innerHTML =
    '<div class="pp-live-head">Live session</div>' +
    '<div class="pp-live-state"><span class="pp-live-dot"></span><span></span></div>' +
    '<p class="pp-live-desc"></p>';
  var stateText = card.querySelector('.pp-live-state span:last-child');
  var desc = card.querySelector('.pp-live-desc');
  var start = el('button', 'pp-live-start');
  start.type = 'button';
  start.innerHTML = '<span class="pp-live-dot"></span>Start live session';
  var chat = el('div', 'pp-live-chat is-folded');
  chat.innerHTML =
    '<div class="pp-live-chat-list"></div>' +
    '<button type="button" class="pp-live-chat-toggle"></button>' +
    '<div class="pp-live-in pp-live-chat-in">' +
    '<textarea placeholder="Message the agent about the whole change"></textarea>' +
    '<button type="button" class="pp-live-btn">Send</button></div>';
  var chatList = chat.querySelector('.pp-live-chat-list');
  var chatToggle = chat.querySelector('.pp-live-chat-toggle');
  var chatText = chat.querySelector('textarea');
  card.appendChild(start);
  card.appendChild(chat);
  nav.insertBefore(card, nav.firstChild);

  start.addEventListener('click', async function () {
    start.disabled = true;
    if (await R.send('live_start', {})) {
      active = true;
      renderCard();
      poll(true);
    } else start.disabled = false;
  });
  chatToggle.addEventListener('click', function () {
    chatOpen = !chatOpen;
    renderChat();
    if (chatOpen) chatText.focus();
  });
  async function sendChat() {
    var text = chatText.value.trim();
    if (!text || frozen) return;
    if (await post({ type: 'chat', text: text })) {
      chatText.value = '';
      poll(false);
    }
  }
  chat.querySelector('.pp-live-btn').addEventListener('click', sendChat);
  chatText.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      sendChat();
    }
  });

  function agentState() {
    if (!state || !state.session) return ['working', 'Connecting', 'Waiting for the agent to attach.'];
    if (state.agent.waiting) {
      return ['waiting', 'Waiting for you', 'The agent answers as soon as you send something.'];
    }
    var minutes = Math.floor(state.agent.seconds / 60);
    if (state.agent.seconds >= SILENT_AFTER) {
      return ['silent', 'Agent silent for ' + minutes + ' min',
        'It may be busy elsewhere. Send review still works.'];
    }
    return ['working', 'Agent working',
      (minutes ? 'Since ' + minutes + ' min. ' : '') + 'Keep reviewing; nothing is lost.'];
  }
  function renderCard() {
    start.hidden = active;
    chat.hidden = !active;
    document.body.classList.toggle('pp-live-on', active);
    if (!active) {
      card.dataset.state = 'offer';
      desc.textContent = 'Ask the agent about lines and let it change code while you review.';
      return;
    }
    var s = agentState();
    card.dataset.state = s[0];
    stateText.textContent = s[1];
    desc.textContent = s[2];
    renderChat();
  }
  function renderChat() {
    var messages = (state && state.chat) || [];
    chat.classList.toggle('is-folded', !chatOpen);
    chatList.textContent = '';
    if (!messages.length && !chatOpen) {
      chatList.appendChild(el('div', 'pp-live-empty',
        'Chat: requests about the whole change. Questions about a line go in a comment.'));
    }
    (chatOpen ? messages : messages.slice(-1)).forEach(function (m) {
      chatList.appendChild(message(m));
    });
    chatToggle.textContent = chatOpen ? 'Fold chat'
      : messages.length > 1 ? 'Open chat (' + messages.length + ' messages)'
      : 'Write to the agent';
    if (chatOpen) chatList.scrollTop = chatList.scrollHeight;
  }

  // ---- the comment box: "Ask the agent now" and the held-update notice ----
  var row = document.querySelector('.pp-dock-row');
  var sw = el('span', 'pp-live-switch is-on');
  sw.innerHTML = '<i></i>Ask the agent now';
  sw.setAttribute('role', 'switch');
  sw.tabIndex = 0;
  row.insertBefore(sw, row.firstChild);
  var heldNote = el('div', 'pp-live-held',
    '↻ The agent updated the review. The new version loads once you add or clear this comment.');
  heldNote.hidden = true;
  R.dockText.parentNode.insertBefore(heldNote, R.dockText);
  function relabel() {
    sw.hidden = !active;
    sw.classList.toggle('is-on', askNow);
    sw.setAttribute('aria-checked', askNow ? 'true' : 'false');
    if (!R.editing()) R.dockAdd.textContent = active && askNow ? 'Send to agent' : 'Comment';
  }
  function toggleSwitch() { askNow = !askNow; relabel(); }
  sw.addEventListener('click', toggleSwitch);
  sw.addEventListener('keydown', function (event) {
    if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); toggleSwitch(); }
  });

  // ---- threads ----
  function threadOf(id) {
    return state && state.threads.filter(function (t) { return t.id === id; })[0];
  }
  function decorate(c) {
    if (!c.thread || !c.el) return;
    var t = threadOf(c.thread);
    var old = c.el.querySelector('.pp-live-body');
    if (old) old.remove();
    c.el.querySelectorAll('.pp-live-x').forEach(function (e) { e.remove(); });
    c.el.classList.add('pp-live-thread');
    var messages = t ? t.messages : [{ from: 'operator', text: c.text }];
    var resolved = !!(t && t.resolved);
    c.el.classList.toggle('pp-live-resolved', resolved);
    var actions = c.el.querySelector('.pp-comment-actions');
    if (resolved) {
      var last = messages[messages.length - 1];
      c.el.querySelector('.pp-comment-head b').insertAdjacentElement('afterend',
        el('span', 'pp-live-muted pp-live-x', 'Resolved · ' +
          (last.from === 'agent' ? 'Agent: ' : 'You: ') + last.text));
      var reopen = el('button', 'pp-live-x', 'Reopen');
      reopen.type = 'button';
      reopen.addEventListener('click', function () { act('reopen', c.thread); });
      actions.insertBefore(reopen, actions.firstChild);
      return;
    }
    var body = el('div', 'pp-live-body');
    messages.forEach(function (m) { body.appendChild(message(m)); });
    var strip = el('div', 'pp-live-strip');
    var lastMsg = messages[messages.length - 1];
    var resolve = el('button', 'pp-live-btn', 'Resolve');
    resolve.type = 'button';
    resolve.addEventListener('click', function () { act('resolve', c.thread); });
    if (t && t.done) {
      strip.appendChild(el('span', '', '✓ The agent marked this done.'));
    } else if (lastMsg.from !== 'agent') {
      strip.appendChild(el('span', 'pp-live-muted', 'Sent to the agent, no reply yet'));
      resolve.classList.add('ghost');
    } else {
      strip.appendChild(el('span', ''));
      resolve.classList.add('ghost');
    }
    strip.appendChild(resolve);
    body.appendChild(strip);
    var input = el('div', 'pp-live-in');
    input.innerHTML = '<input placeholder="Reply"><button type="button" class="pp-live-btn">Send</button>';
    var field = input.querySelector('input');
    async function reply() {
      var text = field.value.trim();
      if (!text || frozen) return;
      if (await post({ type: 'reply', thread: c.thread, text: text })) { field.value = ''; poll(false); }
    }
    input.querySelector('button').addEventListener('click', reply);
    field.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); reply(); }
    });
    body.appendChild(input);
    c.el.appendChild(body);
  }
  async function act(kind, id) {
    if (frozen) return;
    if (await post({ type: kind, thread: id })) poll(false);
  }
  function decorateAll() {
    R.comments.forEach(decorate);
  }

  // ---- after a refresh: changed files, outdated comments, the interdiff ----
  function slideOf(path) {
    var diff = document.querySelector('.pp-review-diff[data-path="' + CSS.escape(path) + '"]');
    return diff && diff.closest('.deck-slide');
  }
  function fileButton(slide) {
    var index = Array.prototype.indexOf.call(document.querySelectorAll('.deck-slide'), slide);
    return document.querySelector('.pp-file[data-index="' + index + '"]');
  }
  function label(o) { return (o.side || 'new') + ' ' + o.line; }
  function showOutdated(list) {
    list.forEach(function (o) {
      var slide = slideOf(o.path);
      if (!slide || !o.near) return;
      var diff = slide.querySelector('.pp-review-diff');
      var after = diff.querySelector('.pp-review-row-wrap[data-side="' + o.near.side +
        '"][data-line="' + o.near.line + '"]');
      if (!after) return;
      while (after.nextElementSibling &&
          after.nextElementSibling.matches('.pp-comment, .pp-note, .pp-live-outdated')) {
        after = after.nextElementSibling;
      }
      var t = o.thread && threadOf(o.thread);
      var box = el('details', 'pp-live-outdated');
      var summary = el('summary');
      summary.appendChild(el('b', '', label(o)));
      summary.appendChild(document.createTextNode('outdated · ' +
        (t ? 'thread, ' + t.messages.length + ' messages · ' : '') + o.text));
      box.appendChild(summary);
      var body = el('div', 'pp-live-outdated-body');
      body.appendChild(el('span', 'pp-live-old', o.context));
      (t ? t.messages : [{ from: 'operator', text: o.text }]).forEach(function (m) {
        body.appendChild(message(m));
      });
      var drop = el('button', 'pp-live-btn ghost', 'Delete');
      drop.type = 'button';
      drop.addEventListener('click', async function () {
        if (frozen) return;
        if (await post({ type: 'drop', path: o.path, text: o.text, context: o.context })) box.remove();
      });
      body.appendChild(drop);
      box.appendChild(body);
      after.insertAdjacentElement('afterend', box);
    });
  }
  function interdiffBlock(rows) {
    var box = el('div', 'pp-review-diff pp-live-interdiff');
    box.hidden = true;
    var changed = rows.filter(function (r) { return r.kind === 'add' || r.kind === 'del'; }).length;
    box.appendChild(el('div', 'pp-live-interdiff-note', 'Only what the agent changed since you ' +
      'last looked (' + changed + ' lines). Left numbers: the version you saw; right: now.'));
    rows.forEach(function (r) {
      var line = el('div', 'pp-review-row ' + r.kind);
      if (r.kind === 'hunk') { line.textContent = r.text; box.appendChild(line); return; }
      line.appendChild(el('span', 'pp-review-gutter', r.old == null ? '' : String(r.old)));
      line.appendChild(el('span', 'pp-review-gutter', r.new == null ? '' : String(r.new)));
      line.appendChild(el('span', 'pp-review-text', r.text || ' '));
      box.appendChild(line);
    });
    return { box: box, changed: changed };
  }
  function showInterdiffs(map) {
    Object.keys(map || {}).forEach(function (path) {
      var slide = slideOf(path);
      if (!slide) return;
      var file = fileButton(slide);
      if (file && !file.classList.contains('is-current')) {
        var dot = el('span', 'pp-live-changed');
        file.title += '\nChanged by the agent since you last looked';
        file.querySelector('.pp-file-name').insertAdjacentElement('afterbegin', dot);
      }
      var whole = slide.querySelector('.pp-views') || slide.querySelector('.pp-review-diff');
      var made = interdiffBlock(map[path]);
      whole.insertAdjacentElement('afterend', made.box);
      var head = slide.querySelector('.pp-review-path');
      head.classList.add('pp-live-has-switch');
      var seg = el('span', 'pp-live-seg');
      seg.innerHTML = '<button type="button" class="is-on">Whole change</button>' +
        '<button type="button">Since you looked (' + made.changed + ')</button>';
      seg.addEventListener('click', function (event) {
        var b = event.target.closest('button');
        if (!b) return;
        var since = b === seg.lastChild;
        seg.firstChild.classList.toggle('is-on', !since);
        seg.lastChild.classList.toggle('is-on', since);
        whole.style.display = since ? 'none' : '';
        made.box.hidden = !since;
      });
      head.appendChild(seg);
    });
  }
  function markSeen() {
    var slide = document.querySelector('.deck-slide.is-active');
    var diff = slide && slide.querySelector('.pp-review-diff[data-path]');
    if (!diff) return;
    var file = fileButton(slide);
    var dot = file && file.querySelector('.pp-live-changed');
    if (dot) dot.remove();
    if (active && !frozen) post({ type: 'seen', path: diff.dataset.path });
  }
  document.addEventListener('pp-file-change', function () { setTimeout(markSeen, 0); });

  function restore(r) {
    (r.comments || []).forEach(R.restore);
    var notes = {};
    (Array.isArray(r.page_notes) ? r.page_notes : []).forEach(function (n) { notes[n.path] = n.text; });
    document.querySelectorAll('.pp-page-note').forEach(function (t) {
      if (notes[t.dataset.path]) t.value = notes[t.dataset.path];
    });
    if (typeof r.overall_note === 'string') R.overallNote.value = r.overall_note;
    R.commentsChanged();
    showOutdated(r.outdated || []);
    showInterdiffs(r.interdiffs);
  }

  // ---- polling ----
  var timer = null;
  var version = -1;
  var VIEW_KEY = 'pp-live:view';
  async function reloadNow() {
    await R.send('progress', R.collect());
    var slide = document.querySelector('.deck-slide.is-active');
    var diff = slide && slide.querySelector('.pp-review-diff[data-path]');
    try {
      sessionStorage.setItem(VIEW_KEY, JSON.stringify({
        path: diff ? diff.dataset.path : null, y: window.scrollY
      }));
    } catch (e) { /* not kept: the reload starts at the first file */ }
    location.reload();
  }
  // Back on the file and scroll position the operator had before a refresh reloaded the page.
  function restoreView() {
    var view;
    try {
      view = JSON.parse(sessionStorage.getItem(VIEW_KEY) || 'null');
      sessionStorage.removeItem(VIEW_KEY);
    } catch (e) { return; }
    if (!view || !view.path) return;
    var slide = slideOf(view.path);
    var file = slide && fileButton(slide);
    if (file && !file.classList.contains('is-current')) file.click();
    window.scrollTo(0, view.y || 0);
  }
  async function poll(first) {
    if (frozen) return;
    var body;
    try {
      var res = await fetch(LIVE_URL + (first ? '&restore=1' : ''));
      if (!res.ok) return;
      body = await res.json();
    } catch (e) { return; }
    if (!body.session) return;
    var attaching = !state;
    state = body;
    if (first && body.restore) restore(body.restore);
    if (first) restoreView();
    if (attaching) markSeen();
    if (body.generation !== R.generation) {
      if (R.dockOpen()) { held = true; heldNote.hidden = false; }
      else { stopPolling(); reloadNow(); return; }
    }
    if (body.version !== version) {
      version = body.version;
      decorateAll();
    }
    renderCard();
  }
  function stopPolling() { if (timer) { clearInterval(timer); timer = null; } }

  window.ppLive = {
    added: async function (c) {
      if (!active || !askNow || frozen) return;
      var spec = R.spec(c);
      delete spec.thread;
      var answer = await post({ type: 'thread', comment: spec });
      if (answer && answer.id) {
        c.thread = answer.id;
        await poll(false);
        decorate(c);
        R.reportSoon();
      }
    },
    decorate: function (c) { if (state) decorate(c); },
    dockOpened: relabel,
    dockClosed: function () {
      if (held) { held = false; stopPolling(); reloadNow(); }
    },
    frozen: function () { frozen = true; stopPolling(); }
  };

  renderCard();
  relabel();
  if (active) poll(true);
  timer = setInterval(function () { if (active) poll(false); }, POLL_MS);
}());

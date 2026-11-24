// Export at will: a button beside the theme toggle opens the answer as the JSON the page
// shows when Claude Code stops waiting, so the operator can hand it over before anything
// breaks. Runs after the page script and reads its collect() hook; the page stays live
// and nothing is sent.
(function () {
  var H = window.twbAsk || window.twbReview;
  var main = document.querySelector('main');
  if (!H || !main) return;
  var noun = window.twbAsk ? 'answer' : 'review';
  var ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/>' +
    '<path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>';

  function text() { return JSON.stringify({ kind: 'submit', data: H.collect() }, null, 2); }

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'twb-export-btn';
  btn.title = 'Export my ' + noun;
  btn.setAttribute('aria-label', btn.title);
  btn.setAttribute('aria-expanded', 'false');
  btn.setAttribute('aria-controls', 'twb-export');
  btn.innerHTML = ICON;

  var box = document.createElement('div');
  box.id = 'twb-export';
  box.hidden = true;
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', 'Your ' + noun + ' so far');
  box.innerHTML = '<strong></strong><p>The page stays open. If the connection to Claude Code ' +
    'breaks, paste this to it.</p><textarea readonly rows="7"></textarea>' +
    '<div class="twb-export-row"><button type="button" data-act="close">Close</button>' +
    '<button type="button" class="primary" data-act="copy">Copy</button></div>';
  box.querySelector('strong').textContent = 'Your ' + noun + ' so far';
  var area = box.querySelector('textarea');
  var copy = box.querySelector('[data-act="copy"]');

  function show(open) {
    box.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      area.value = text();
      area.select();
    }
  }

  btn.addEventListener('click', function () { show(box.hidden); });
  box.querySelector('[data-act="close"]').addEventListener('click', function () {
    show(false);
    btn.focus();
  });
  copy.addEventListener('click', function () {
    area.value = text();
    area.select();
    function done() {
      copy.textContent = 'Copied';
      setTimeout(function () { copy.textContent = 'Copy'; }, 1500);
    }
    // A click is the operator asking, so writing to the clipboard is not a surprise here.
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(area.value).then(done, function () {
        if (document.execCommand('copy')) done();
      });
    } else if (document.execCommand('copy')) {
      done();
    }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !box.hidden) {
      show(false);
      btn.focus();
    }
  });
  document.addEventListener('mousedown', function (event) {
    if (!box.hidden && !box.contains(event.target) && !btn.contains(event.target)) show(false);
  });

  main.appendChild(btn);
  main.appendChild(box);
}());

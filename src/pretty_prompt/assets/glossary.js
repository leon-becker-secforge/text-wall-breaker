(function () {
  var entries = window.__PP_GLOSSARY__ || [];
  if (!entries.length) return;
  var escape = function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); };
  var byTerm = {};
  entries.forEach(function (e) { byTerm[e.term.toLowerCase()] = e; });
  var names = entries.map(function (e) { return e.term; })
    .sort(function (a, b) { return b.length - a.length; });
  var rx = new RegExp("\\b(" + names.map(escape).join("|") + ")(?:s|es)?\\b", "gi");
  var skip = "code, pre, a, button, textarea, input, label.pp-free-text, .pp-term";

  var pop = document.createElement("div");
  pop.id = "pp-glossary-pop";
  pop.setAttribute("role", "tooltip");
  document.body.appendChild(pop);
  function show(span) {
    var e = byTerm[span.dataset.term];
    pop.textContent = "";
    var title = document.createElement("b");
    title.textContent = e.term;
    pop.appendChild(title);
    pop.appendChild(document.createTextNode(e.definition));
    pop.style.display = "block";
    var r = span.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8)) + "px";
    var below = r.bottom + 6;
    pop.style.top = (below + pop.offsetHeight > innerHeight ? r.top - pop.offsetHeight - 6 : below) + "px";
  }
  function hide() { pop.style.display = "none"; }

  // One marked occurrence per term and per step, so a repeated term does not underline
  // the whole page.
  var roots = document.querySelectorAll(".pp-step");
  if (!roots.length) roots = [document.querySelector("main") || document.body];
  Array.prototype.forEach.call(roots, function (root) {
    var seen = {};
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      if (node.parentElement.closest(skip)) return;
      var text = node.textContent;
      rx.lastIndex = 0;
      var frag = document.createDocumentFragment();
      var pos = 0, m, hit = false;
      while ((m = rx.exec(text))) {
        var key = names.filter(function (n) { return n.toLowerCase() === m[1].toLowerCase(); })[0].toLowerCase();
        if (seen[key]) continue;
        seen[key] = true;
        hit = true;
        frag.appendChild(document.createTextNode(text.slice(pos, m.index)));
        var span = document.createElement("span");
        span.className = "pp-term";
        span.dataset.term = key;
        span.tabIndex = 0;
        span.textContent = m[0];
        span.addEventListener("mouseenter", function () { show(this); });
        span.addEventListener("focus", function () { show(this); });
        span.addEventListener("mouseleave", hide);
        span.addEventListener("blur", hide);
        frag.appendChild(span);
        pos = m.index + m[0].length;
      }
      if (!hit) return;
      frag.appendChild(document.createTextNode(text.slice(pos)));
      node.replaceWith(frag);
    });
  });
})();

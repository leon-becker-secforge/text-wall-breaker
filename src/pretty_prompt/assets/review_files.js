(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.deck-slide'));
  var files = Array.prototype.slice.call(document.querySelectorAll('.pp-file'));
  var nav = document.getElementById('pp-files');
  var backdrop = document.getElementById('pp-files-backdrop');
  var toggle = document.getElementById('pp-files-toggle');
  var barName = document.getElementById('pp-filebar-name');
  var barPos = document.getElementById('pp-filebar-pos');
  var total = document.getElementById('pp-files-total');
  var filter = document.getElementById('pp-files-filter');
  var onlyMarked = document.getElementById('pp-files-marked');
  var empty = document.getElementById('pp-files-empty');
  var index = 0;

  function plural(n) { return n + (n === 1 ? ' comment' : ' comments'); }

  function refresh() {
    var sum = 0;
    slides.forEach(function (slide, i) {
      var n = slide.querySelectorAll('.pp-comment').length;
      sum += n;
      var count = files[i].querySelector('.pp-file-count');
      count.textContent = n;
      count.title = plural(n);
      count.classList.toggle('is-zero', n === 0);
      files[i].querySelector('.pp-file-note').hidden =
        slide.querySelector('.pp-page-note').value.trim() === '';
    });
    total.textContent = plural(sum);
    applyFilter();
  }

  // The filter only hides rows of the list; stepping with the arrows still visits every file.
  function applyFilter() {
    if (!filter) return;
    var needle = filter.value.trim().toLowerCase(), shown = 0;
    files.forEach(function (row) {
      var marked = row.querySelector('.pp-file-count').textContent !== '0' ||
        !row.querySelector('.pp-file-note').hidden;
      var ok = (!needle || row.title.toLowerCase().indexOf(needle) >= 0) &&
        (!onlyMarked.checked || marked || row.classList.contains('is-current'));
      row.hidden = !ok;
      if (ok) shown++;
    });
    empty.hidden = shown > 0;
  }

  function setOpen(open) {
    nav.classList.toggle('is-open', open);
    backdrop.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function show(next, scroll) {
    // The comment box is placed on the page, not in its file: it would be left floating.
    document.dispatchEvent(new CustomEvent('pp-file-change'));
    slides[index].classList.remove('is-active');
    files[index].classList.remove('is-current');
    index = (next + slides.length) % slides.length;
    slides[index].classList.add('is-active');
    files[index].classList.add('is-current');
    barName.textContent = files[index].querySelector('.pp-file-name').textContent;
    barPos.textContent = (index + 1) + ' of ' + slides.length;
    if (scroll) window.scrollTo(0, 0);
    // A long list keeps the current file in sight.
    files[index].scrollIntoView({ block: 'nearest' });
  }

  files.forEach(function (row, i) {
    row.addEventListener('click', function () { show(i, true); setOpen(false); });
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-file-step]'), function (b) {
    b.addEventListener('click', function () {
      show(index + parseInt(b.dataset.fileStep, 10), true);
    });
  });
  toggle.addEventListener('click', function () { setOpen(!nav.classList.contains('is-open')); });
  backdrop.addEventListener('click', function () { setOpen(false); });
  document.addEventListener('input', refresh);
  if (filter) onlyMarked.addEventListener('change', applyFilter);
  document.addEventListener('pp-comments', refresh);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { setOpen(false); return; }
    if (event.target.matches('input, textarea, [contenteditable]')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'ArrowRight' || event.key === 'j') show(index + 1, true);
    if (event.key === 'ArrowLeft' || event.key === 'k') show(index - 1, true);
  });

  refresh();
  show(0, false);
}());

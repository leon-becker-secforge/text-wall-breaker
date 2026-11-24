(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.deck-slide'));
  var progressLabel = document.getElementById('twb-deck-progress-label');
  var progressFill = document.getElementById('twb-deck-progress-fill');
  var index = 0;
  function show(next) {
    slides[index].classList.remove('is-active');
    index = (next + slides.length) % slides.length;
    slides[index].classList.add('is-active');
    if (progressLabel) progressLabel.textContent = 'Slide ' + (index + 1) + ' of ' + slides.length;
    if (progressFill) {
      progressFill.style.width = Math.round((index + 1) / slides.length * 100) + '%';
    }
  }
  var prevBtn = document.querySelector('[data-deck-prev]');
  var nextBtn = document.querySelector('[data-deck-next]');
  if (prevBtn) prevBtn.addEventListener('click', function () { show(index - 1); });
  if (nextBtn) nextBtn.addEventListener('click', function () { show(index + 1); });
  document.addEventListener('keydown', function (event) {
    if (event.target.matches('input, textarea, [contenteditable]')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'ArrowRight') show(index + 1);
    if (event.key === 'ArrowLeft') show(index - 1);
  });
}());

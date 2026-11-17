(function () {
  var dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  mermaid.initialize({
    startOnLoad: false,
    theme: dark ? 'dark' : 'default',
    securityLevel: 'strict',
  });
  // A diagram is drawn once it is on screen: inside a hidden page or slide it has no size,
  // and mermaid would measure its labels as zero and draw an empty box.
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      mermaid.run({ nodes: [entry.target] });
    });
  });
  document.querySelectorAll('.mermaid').forEach(function (el) {
    observer.observe(el);
  });
}());

(function () {
  var dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  mermaid.initialize({
    startOnLoad: false,
    theme: dark ? 'dark' : 'default',
    securityLevel: 'strict',
  });
  document.querySelectorAll('.mermaid').forEach(function (el) {
    mermaid.run({ nodes: [el] });
  });
}());

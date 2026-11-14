// The μ dashboard's theme, set before first paint (docs/design/dashboard.md §5.3). A classic script served as a file:
// the CSP allows no inline script. It sets data-theme on <html> from the stored choice ("mu.theme": "light" or
// "dark"; anything else follows the system), and follows the system while no choice is stored. The page's toggle
// writes "mu.theme" and sets the attribute itself. No HTML sink, no global.
(function () {
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function stored() {
    try {
      var v = window.localStorage.getItem("mu.theme");
      return v === "light" || v === "dark" ? v : null;
    } catch {
      return null;
    }
  }
  function apply() {
    root.setAttribute("data-theme", stored() || (mq && mq.matches ? "dark" : "light"));
  }
  apply();
  if (mq) {
    var onChange = function () {
      if (!stored()) apply();
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
})();

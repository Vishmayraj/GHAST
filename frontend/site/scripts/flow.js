/* Flow page. The line fills with scroll and each point fades in and out around the middle of the screen.
   The page is white for the first three points and black after. The shift happens in the gap between
   point 3 and point 4, where both are faded out, so no text is ever on screen at mid-gray. */
(function () {
  var root = document.documentElement, flow = document.querySelector("[data-flow]");
  if (!flow) return;
  var pts = [].slice.call(flow.querySelectorAll(".pt")), pending = false;
  var SWITCH = 3; /* the first point (0-based) that sits on black */
  var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.body.classList.add("flow-page");
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function smooth(x) { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); }
  function center(el) { var b = el.getBoundingClientRect(); return b.top + b.height / 2; }

  function theme(t) {
    var g = Math.round(255 * (1 - t)), dark = t > 0.5, ink = dark ? "255,255,255" : "10,26,43", s = root.style;
    s.setProperty("--paper", "rgb(" + g + "," + g + "," + g + ")");
    s.setProperty("--paper-deep", "rgba(" + ink + ",0.06)");
    s.setProperty("--ink", "rgb(" + ink + ")");
    s.setProperty("--ink-2", "rgba(" + ink + ",0.82)");
    s.setProperty("--ink-3", "rgba(" + ink + ",0.64)");
    s.setProperty("--rule", "rgba(" + ink + ",0.2)");
    s.setProperty("--signal", dark ? "#ff6a45" : "#d8431f");
  }

  function frame() {
    pending = false;
    var vh = window.innerHeight, r = flow.getBoundingClientRect();
    flow.style.setProperty("--fill", clamp((vh * 0.5 - r.top) / r.height, 0, 1));
    if (!still) {
      var gap = (center(pts[SWITCH - 1]) + center(pts[SWITCH])) / 2;
      theme(smooth(0.5 + (vh * 0.5 - gap) / (vh * 0.1)));
    }
    pts.forEach(function (pt) {
      var d = (center(pt) - vh / 2) / (vh * 0.26), o = still ? 1 : clamp((1 - Math.abs(d)) * 2.5, 0, 1);
      pt.style.setProperty("--o", o.toFixed(3));
      pt.style.setProperty("--dy", (clamp(d, -1, 1) * 30).toFixed(1));
      pt.classList.toggle("is-active", o > 0.6);
      pt.classList.toggle("is-passed", d < 0.05);
    });
  }
  function queue() { if (!pending) { pending = true; requestAnimationFrame(frame); } }
  window.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue, { passive: true });
  frame();
})();

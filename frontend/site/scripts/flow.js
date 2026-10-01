/* Flow page: the line fills with scroll, each point fades in and out around the middle of the screen,
   and the page shifts from white to black. Colors are set on :root so the header follows. */
(function () {
  var root = document.documentElement, flow = document.querySelector("[data-flow]");
  if (!flow) return;
  var pts = [].slice.call(flow.querySelectorAll(".pt")), fill = flow.querySelector(".flow__fill");
  var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches, pending = false;
  document.body.classList.add("flow-page");
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function smooth(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }

  function frame() {
    pending = false;
    var vh = window.innerHeight, r = flow.getBoundingClientRect(), p = clamp((vh * 0.5 - r.top) / r.height, 0, 1);
    flow.style.setProperty("--fill", clamp((vh * 0.5 - r.top) / r.height, 0, 1));
    if (!still) {
      var t = smooth(0.14, 0.8, p), g = Math.round(255 * (1 - t)), dark = g < 128;
      var ink = dark ? "255,255,255" : "10,26,43";
      var s = root.style;
      s.setProperty("--paper", "rgb(" + g + "," + g + "," + g + ")");
      s.setProperty("--paper-deep", dark ? "rgba(255,255,255,0.06)" : "rgba(10,26,43,0.05)");
      s.setProperty("--ink", "rgb(" + ink + ")");
      s.setProperty("--ink-2", "rgba(" + ink + ",0.82)");
      s.setProperty("--ink-3", "rgba(" + ink + ",0.64)");
      s.setProperty("--rule", "rgba(" + ink + ",0.18)");
    }
    pts.forEach(function (pt) {
      var b = pt.getBoundingClientRect(), d = (b.top + b.height / 2 - vh / 2) / (vh * 0.4), o = clamp((1 - Math.abs(d)) * 2, 0, 1);
      if (still) o = 1;
      pt.style.setProperty("--o", o.toFixed(3));
      pt.style.setProperty("--dy", (clamp(d, -1, 1) * 36).toFixed(1));
      pt.classList.toggle("is-active", o > 0.6);
      pt.classList.toggle("is-passed", d < 0.05);
    });
  }
  function queue() { if (!pending) { pending = true; requestAnimationFrame(frame); } }
  window.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue, { passive: true });
  frame();
})();

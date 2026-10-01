/* Inertial wheel scrolling: the page keeps gliding after the wheel stops and slowly loses speed.
   Touch, keyboard, scrollbar drag and anchor jumps stay native. Off for reduced motion. */
(function () {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (window.matchMedia("(pointer: coarse)").matches) return;
  var root = document.documentElement, cur = window.scrollY, target = cur, raf = 0, own = false;
  var EASE = 0.048;
  root.style.scrollBehavior = "auto";
  function max() { return root.scrollHeight - window.innerHeight; }
  function tick() {
    var d = target - cur;
    if (Math.abs(d) < 0.4) { cur = target; own = true; window.scrollTo(0, cur); raf = 0; return; }
    cur += d * EASE; own = true; window.scrollTo(0, cur);
    raf = requestAnimationFrame(tick);
  }
  window.addEventListener("wheel", function (e) {
    if (e.ctrlKey || e.defaultPrevented) return;
    var t = e.target;
    if (t.closest && t.closest("textarea, select, [data-native-scroll]")) return;
    e.preventDefault();
    var dy = e.deltaMode === 1 ? e.deltaY * 32 : e.deltaMode === 2 ? e.deltaY * window.innerHeight : e.deltaY;
    target = Math.max(0, Math.min(max(), target + dy));
    if (!raf) raf = requestAnimationFrame(tick);
  }, { passive: false });
  window.addEventListener("scroll", function () {
    if (own) { own = false; return; }
    cur = target = window.scrollY;
  }, { passive: true });
})();

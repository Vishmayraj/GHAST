/* Scroll-scrubbed hero. Scroll sets a target progress; the video and the title lines
   ease toward it every frame, so the footage glides instead of stepping. */
(function () {
  "use strict";
  var hero = document.querySelector("[data-hero]");
  var video = document.querySelector("[data-hero-video]");
  var scrim = document.querySelector("[data-hero-scrim]");
  var lines = [].slice.call(document.querySelectorAll("[data-hero-line]"));
  if (!hero || !video) return;
  var SPLIT = 0.5;

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    video.setAttribute("autoplay", ""); video.loop = false; video.play().catch(function () {}); return;
  }

  var duration = 0, shown = 0, target = 0, running = false;
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function meta() { duration = video.duration || duration; }
  video.addEventListener("loadedmetadata", meta);
  video.addEventListener("canplay", meta);

  function readTarget() {
    var scrollable = hero.offsetHeight - window.innerHeight;
    target = scrollable > 0 ? clamp(-hero.getBoundingClientRect().top / scrollable, 0, 1) : 1;
    if (!running) { running = true; requestAnimationFrame(frame); }
  }

  function frame() {
    shown += (target - shown) * 0.14;
    var settled = Math.abs(target - shown) < 0.0004;
    if (settled) shown = target;
    if (duration && !video.seeking) {
      var t = shown * duration;
      if (Math.abs(video.currentTime - t) > 0.016) video.currentTime = t;
    }
    var q = clamp((shown - SPLIT) / (1 - SPLIT), 0, 1);
    if (scrim) scrim.style.setProperty("--scrim", Math.min(1, q * 1.6));
    lines.forEach(function (l) {
      var r = (l.getAttribute("data-hero-line") || "0,1").split(",");
      l.style.setProperty("--reveal", clamp((q - +r[0]) / (+r[1] - +r[0]), 0, 1));
    });
    if (settled) { running = false; return; }
    requestAnimationFrame(frame);
  }

  window.addEventListener("scroll", readTarget, { passive: true });
  window.addEventListener("resize", readTarget, { passive: true });
  video.play().then(function () { video.pause(); readTarget(); }, readTarget);
  readTarget();
})();

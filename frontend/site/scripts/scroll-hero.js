/**
 * scroll-hero.js
 *
 * Two-phase scroll-scrubbed hero:
 *
 *   Phase A: scroll progress 0 -> PHASE_SPLIT maps to video time
 *   0 -> the anomaly-flagged moment. No overlay.
 *
 *   Phase B: PHASE_SPLIT -> 1 continues scrubbing the video to its
 *   end while each [data-hero-line] fades/slides up into place,
 *   driven by the exact same scroll read — so it reads as one
 *   continuous scroll, not a scene cut.
 *
 * The wheel/touch scroll itself is never intercepted: this only
 * *reads* window scroll position (rAF-throttled) and writes derived
 * values (video.currentTime, a --reveal custom property per line,
 * scrim opacity) back as plain style updates.
 */

(function () {
  "use strict";

  var hero = document.querySelector("[data-hero]");
  var video = document.querySelector("[data-hero-video]");
  var scrim = document.querySelector("[data-hero-scrim]");
  var lines = Array.prototype.slice.call(document.querySelectorAll("[data-hero-line]"));

  if (!hero || !video) return;

  // Fraction of the video's duration at which the anomaly is
  // flagged on screen — the rest of the runtime is the reveal
  // window. 5.5s into a 10s clip unless the source changes.
  var PHASE_SPLIT = 0.55;

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduceMotion) {
    video.setAttribute("autoplay", "");
    video.loop = false;
    video.play().catch(function () {
      /* autoplay can be blocked; poster/first frame remains, which is fine */
    });
    return;
  }

  var duration = 0;
  var ready = false;
  var ticking = false;
  var lastProgress = -1;

  video.addEventListener("loadedmetadata", function () {
    duration = video.duration || 0;
    ready = duration > 0;
    render();
  });

  video.addEventListener("canplay", function () {
    if (!ready && video.duration) {
      duration = video.duration;
      ready = true;
      render();
    }
  });

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function getProgress() {
    var rect = hero.getBoundingClientRect();
    var scrollable = hero.offsetHeight - window.innerHeight;
    if (scrollable <= 0) return 1;
    var scrolled = -rect.top;
    return clamp(scrolled / scrollable, 0, 1);
  }

  function render() {
    ticking = false;
    var progress = getProgress();
    if (progress === lastProgress) return;
    lastProgress = progress;

    if (ready) {
      var target = progress * duration;
      if (Math.abs(video.currentTime - target) > 0.03) {
        video.currentTime = target;
      }
    }

    // q: 0 at the flag moment, 1 at video end. Clamped below 0 for
    // all of phase A, so overlay stays fully hidden until then.
    var q = clamp((progress - PHASE_SPLIT) / (1 - PHASE_SPLIT), 0, 1);

    if (scrim) scrim.style.setProperty("--scrim", q > 0 ? Math.min(1, q * 1.6) : 0);

    lines.forEach(function (line) {
      var range = (line.getAttribute("data-hero-line") || "0,1").split(",");
      var start = parseFloat(range[0]);
      var end = parseFloat(range[1]);
      var local = clamp((q - start) / (end - start), 0, 1);
      line.style.setProperty("--reveal", local);
    });
  }

  function onScroll() {
    if (!ticking) {
      window.requestAnimationFrame(render);
      ticking = true;
    }
  }

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });

  // Some mobile browsers need an explicit play/pause cycle before
  // currentTime seeks take effect.
  video.play().then(function () {
    video.pause();
    render();
  }).catch(function () {
    render();
  });

  render();
})();

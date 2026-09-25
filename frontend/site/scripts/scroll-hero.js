/**
 * scroll-hero.js
 *
 * Drives the hero video's currentTime from ordinary page scroll
 * progress. The wheel/touch scroll itself is never intercepted —
 * we only read scroll position on the normal document and derive a
 * 0..1 progress value from how far the user has moved through the
 * tall .hero wrapper. position:sticky (see hero.css) does the
 * pinning; this script only owns "which video frame is showing".
 */

(function () {
  "use strict";

  var hero = document.querySelector("[data-hero]");
  var video = document.querySelector("[data-hero-video]");
  var content = document.querySelector("[data-hero-content]");

  if (!hero || !video) return;

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduceMotion) {
    // Respect the user's preference: play once, normally, no scrubbing.
    video.setAttribute("autoplay", "");
    video.loop = false;
    video.play().catch(function () {
      /* autoplay can be blocked; poster frame remains, which is fine */
    });
    if (content) content.classList.add("is-active");
    return;
  }

  var duration = 0;
  var ready = false;
  var ticking = false;
  var lastProgress = -1;

  video.addEventListener("loadedmetadata", function () {
    duration = video.duration || 0;
    ready = duration > 0;
  });

  // Some browsers report duration slightly late even after
  // loadedmetadata on compressed h264; readyState check is a
  // cheap secondary confirmation.
  video.addEventListener("canplay", function () {
    if (!ready && video.duration) {
      duration = video.duration;
      ready = true;
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

  function updateContentVisibility(progress) {
    if (!content) return;
    // Text is on-screen for the first ~22% and fades back out by
    // ~30%, staying clear of the frame for most of the anomaly
    // sequence so the footage carries the story on its own.
    var active = progress > 0.02 && progress < 0.3;
    content.classList.toggle("is-active", active);
  }

  function render() {
    ticking = false;
    var progress = getProgress();

    if (progress === lastProgress) return;
    lastProgress = progress;

    updateContentVisibility(progress);

    if (ready) {
      var target = progress * duration;
      // Avoid redundant seeks — some browsers drop frames when
      // currentTime is written every single rAF tick.
      if (Math.abs(video.currentTime - target) > 0.03) {
        video.currentTime = target;
      }
    }
  }

  function onScroll() {
    if (!ticking) {
      window.requestAnimationFrame(render);
      ticking = true;
    }
  }

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });

  // Video needs at least one explicit play/pause cycle on some
  // mobile browsers before currentTime seeks take effect.
  video.play().then(function () {
    video.pause();
    render();
  }).catch(function () {
    render();
  });
})();

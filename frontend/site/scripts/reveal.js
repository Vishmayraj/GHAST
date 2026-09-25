/**
 * reveal.js
 *
 * Progressive enhancement only: [data-reveal] elements are visible
 * by default (see base.css), and this only adds the fade/rise-in
 * once an element nears the viewport. If IntersectionObserver is
 * unavailable, elements simply stay visible with no animation.
 */

(function () {
  "use strict";

  if (!("IntersectionObserver" in window)) return;

  var targets = document.querySelectorAll("[data-reveal]");
  if (!targets.length) return;

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15, rootMargin: "0px 0px -8% 0px" }
  );

  targets.forEach(function (el) {
    observer.observe(el);
  });
})();

/* Header theme over the hero, mobile menu, reveals. No scroll listeners. */
(function () {
  var header = document.querySelector(".header");
  var sentinel = document.querySelector("[data-hero-end]");
  if (header && sentinel && "IntersectionObserver" in window) {
    header.setAttribute("data-theme", "hero");
    new IntersectionObserver(function (e) {
      header.setAttribute("data-theme", e[0].isIntersecting || e[0].boundingClientRect.top > 0 ? "hero" : "page");
    }).observe(sentinel);
  }

  var btn = document.querySelector(".header__menu");
  var links = document.getElementById("nav-links");
  function setMenu(open) {
    if (!btn) return;
    links.setAttribute("data-open", open);
    btn.setAttribute("aria-expanded", open);
    btn.textContent = open ? "Close" : "Menu";
  }
  if (btn) {
    btn.addEventListener("click", function () { setMenu(links.getAttribute("data-open") !== "true"); });
    links.addEventListener("click", function (e) { if (e.target.tagName === "A") setMenu(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { setMenu(false); btn.focus(); } });
  }

  var targets = document.querySelectorAll("[data-reveal]");
  if (!("IntersectionObserver" in window)) { targets.forEach(function (t) { t.classList.add("is-visible"); }); return; }
  var io = new IntersectionObserver(function (es) {
    es.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add("is-visible"); io.unobserve(x.target); } });
  }, { threshold: 0.12 });
  targets.forEach(function (t) { io.observe(t); });
})();

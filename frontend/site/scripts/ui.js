/* Header theme over the hero, mobile menu, split headlines, reveals and staggers. */
(function () {
  var header = document.querySelector(".header");
  var sentinel = document.querySelector("[data-hero-end]");
  if (header && sentinel && "IntersectionObserver" in window) {
    header.setAttribute("data-theme", "hero");
    new IntersectionObserver(function (e) {
      header.setAttribute("data-theme", e[0].isIntersecting || e[0].boundingClientRect.top > 0 ? "hero" : "page");
    }).observe(sentinel);
  }

  var btn = document.querySelector(".header__menu"), links = document.getElementById("nav-links");
  function setMenu(open) {
    if (!btn) return;
    links.setAttribute("data-open", open); btn.setAttribute("aria-expanded", open); btn.textContent = open ? "Close" : "Menu";
  }
  if (btn) {
    btn.addEventListener("click", function () { setMenu(links.getAttribute("data-open") !== "true"); });
    links.addEventListener("click", function (e) { if (e.target.tagName === "A") setMenu(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { setMenu(false); btn.focus(); } });
  }

  /* headlines: each word rises out of a mask */
  document.querySelectorAll("[data-split]").forEach(function (el) {
    el.setAttribute("aria-label", el.textContent.replace(/\s+/g, " ").trim());
    var kids = [].slice.call(el.childNodes), i = 0;
    el.textContent = "";
    function word(node) {
      var w = document.createElement("span"), s = document.createElement("span");
      w.className = "w"; w.setAttribute("aria-hidden", "true"); s.style.setProperty("--d", i++ * 70 + "ms");
      s.appendChild(node); w.appendChild(s); el.appendChild(w); el.appendChild(document.createTextNode(" "));
    }
    kids.forEach(function (n) {
      if (n.nodeType === 3) n.textContent.split(/\s+/).filter(Boolean).forEach(function (t) { word(document.createTextNode(t)); });
      else if (n.nodeType === 1) n.textContent.split(/\s+/).filter(Boolean).forEach(function (t) { var c = n.cloneNode(false); c.textContent = t; word(c); });
    });
  });

  var targets = document.querySelectorAll("[data-reveal], [data-stagger], [data-split], [data-draw]");
  document.querySelectorAll("[data-stagger]").forEach(function (p) {
    [].forEach.call(p.children, function (c, i) { c.style.setProperty("--d", i * 110 + "ms"); });
  });
  if (!("IntersectionObserver" in window)) { targets.forEach(function (t) { t.classList.add("is-visible"); }); return; }
  var io = new IntersectionObserver(function (es) {
    es.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add("is-visible"); io.unobserve(x.target); } });
  }, { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
  targets.forEach(function (t) { io.observe(t); });
})();

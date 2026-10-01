/* The only thing UI code calls. Picks sample or live from GHAST_CONFIG. */
(function () {
  var cfg = window.GHAST_CONFIG, m = /[?&]source=(sample|live)/.exec(location.search);
  if (m) cfg.source = m[1];
  window.GHAST = { source: cfg.source, data: cfg.source === "live" ? window.GHAST_LIVE : window.GHAST_SAMPLE };
})();

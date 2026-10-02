/* Live implementation. Calls backend/api (see docs/backend-and-frontend.md). */
(function () {
  var cfg = window.GHAST_CONFIG;
  function get(path, params) {
    var q = [];
    Object.keys(params || {}).forEach(function (k) { if (params[k] !== undefined && params[k] !== "" && params[k] !== null) q.push(encodeURIComponent(k) + "=" + encodeURIComponent(params[k])); });
    return call(path + (q.length ? "?" + q.join("&") : ""), { method: "GET" });
  }
  function call(path, opts) {
    opts.headers = Object.assign({ "X-API-Key": cfg.apiKey, "Content-Type": "application/json" }, opts.headers || {});
    return fetch(cfg.apiBase + path, opts).then(function (r) {
      if (!r.ok) throw new Error("API " + r.status);
      return r.json();
    });
  }
  window.GHAST_LIVE = {
    health: function () { return get("/health"); },
    incidents: function (p) { return get("/incidents", p); },
    incident: function (id) { return get("/incidents/" + id); },
    track: function (mmsi, p) { return get("/vessels/" + mmsi + "/track", { from: p && p.from, to: p && p.to, include_historical: p && p.includeHistorical }); },
    vesselIncidents: function (mmsi) { return get("/vessels/" + mmsi + "/incidents"); },
    zones: function () { return get("/zones"); },
    review: function (id, body) { return call("/incidents/" + id + "/review", { method: "POST", body: JSON.stringify(body) }); },
    reviewStats: function () { return get("/review-stats"); },
    thresholds: function () { return get("/thresholds"); }
  };
})();

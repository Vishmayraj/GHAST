/* Console modules. Each renders into its [data-module] and handles loading, empty and error. */
(function () {
  var api = window.GHAST.data;
  var $ = function (m) { return document.querySelector('[data-module="' + m + '"]'); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var HYP = { jamming: "Area jamming", targeted_spoof: "Targeted spoof", equipment_fault: "Equipment fault", freeze_replay: "Frozen or replayed track", benign: "Benign" };
  var VERDICT = { confirmed_spoof: "Confirmed spoof", jamming: "Jamming", equipment_fault: "Equipment fault", benign: "Benign", unclear: "Unclear" };
  var STATUS = { reported: "Reported", escalated: "Escalated", resolved: "Resolved" };
  var state = { status: "", hypothesis: "", selected: null, items: [] };

  function ago(iso) {
    var s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (s < 90) return Math.round(s) + " s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }
  var age = function (s) { return s == null ? "none yet" : ago(new Date(Date.now() - s * 1000).toISOString()); };

  /* status-strip */
  function renderStatus() {
    var el = $("status-strip"); if (!el) return;
    api.health().then(function (h) {
      el.innerHTML = '<div class="wrap strip__in"><span class="strip__item">Database<b>' + esc(h.database) + '</b></span><span class="strip__item">Latest position<b class="num">' + age(h.latest_position_age_s) +
        '</b></span><span class="strip__item">Latest incident<b class="num">' + age(h.latest_incident_age_s) + '</b></span>' + (window.GHAST.source === "sample" ? '<span class="tag">Sample data</span>' : "") + "</div>";
    }, function () { el.innerHTML = '<div class="wrap strip__in"><span class="strip__item">The service did not answer.</span></div>'; });
  }

  /* incident-queue */
  function renderQueue() {
    var el = $("incident-queue"), list = el.querySelector("[data-list]");
    list.innerHTML = '<p class="queue__msg">Loading incidents.</p>';
    api.incidents({ status: state.status, hypothesis: state.hypothesis, limit: 50 }).then(function (r) {
      state.items = r.items;
      if (!r.items.length) { list.innerHTML = '<p class="queue__msg">No incidents match these filters.</p>'; return; }
      list.innerHTML = r.items.map(function (i) {
        return '<button class="row" data-id="' + i.id + '"' + (i.id === state.selected ? ' aria-current="true"' : "") + '><span class="row__dot' + (i.status === "resolved" ? " row__dot--off" : "") + '" aria-label="' + (i.status === "resolved" ? "Reviewed" : "Unreviewed") + '"></span>' +
          '<span class="row__name">' + esc(i.vessel_name || i.mmsi) + '</span><span class="row__num num">' + Math.round(i.confidence * 100) + '%</span>' +
          '<span class="row__sub">' + esc(HYP[i.hypothesis]) + ' / ' + ago(i.flagged_at) + '</span></button>';
      }).join("");
    }, function () { list.innerHTML = '<p class="queue__msg">The service did not answer. <button class="link" data-retry style="background:none;border:0;padding:0;cursor:pointer">Try again</button></p>'; });
  }

  /* map */
  function renderMap(el, track, zones) {
    var W = 640, H = 300, p = 28;
    if (!track.length) { el.innerHTML = '<p class="empty">No track for this vessel.</p>'; return; }
    var la = [], lo = [];
    track.forEach(function (t) { la.push(t.lat, t.predicted_lat); lo.push(t.lon, t.predicted_lon); });
    var a0 = Math.min.apply(null, la), a1 = Math.max.apply(null, la), o0 = Math.min.apply(null, lo), o1 = Math.max.apply(null, lo);
    var sc = Math.min((W - 2 * p) / Math.max(o1 - o0, 1e-6), (H - 2 * p) / Math.max(a1 - a0, 1e-6));
    var X = function (o) { return p + (o - o0) * sc; }, Y = function (a) { return H - p - (a - a0) * sc; };
    var g = "";
    for (var x = 0; x <= W; x += 80) g += '<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + H + '" stroke="#0a2036" stroke-opacity=".08"/>';
    for (var y = 0; y <= H; y += 75) g += '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="#0a2036" stroke-opacity=".08"/>';
    var land = '<path d="M0 0H210C190 40 160 60 120 70C80 82 40 100 0 96Z" fill="#ece8de"/>';
    var zs = ((zones && zones.features) || []).map(function (f) {
      var ring = f.geometry && f.geometry.coordinates && f.geometry.coordinates[0]; if (!ring) return "";
      return '<polygon points="' + ring.map(function (c) { return X(c[0]).toFixed(1) + "," + Y(c[1]).toFixed(1); }).join(" ") + '" fill="#5a6674" fill-opacity=".12" stroke="#5a6674" stroke-dasharray="4 3"/>';
    }).join("");
    var line = track.map(function (t) { return X(t.lon).toFixed(1) + "," + Y(t.lat).toFixed(1); }).join(" ");
    var pred = track.filter(function (t, i) { return i % 3 === 0; }).map(function (t) { return '<circle cx="' + X(t.predicted_lon).toFixed(1) + '" cy="' + Y(t.predicted_lat).toFixed(1) + '" r="3.5" fill="none" stroke="#5a6674" stroke-width="1.3"/>'; }).join("");
    var fl = track.filter(function (t) { return t.flagged; }).map(function (t) { return '<circle cx="' + X(t.lon).toFixed(1) + '" cy="' + Y(t.lat).toFixed(1) + '" r="6" fill="#d8431f"/>'; }).join("");
    el.innerHTML = '<svg class="map" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Vessel track with the flagged report marked">' + g + land + zs +
      '<polyline class="map-track" points="' + line + '" fill="none" stroke="#1f4e79" stroke-width="2"/>' + pred + fl + "</svg>" +
      '<div class="map__legend"><span>Solid line: reported track</span><span>Open circles: predicted positions</span><span>Red: flagged report</span></div>';
  }

  /* fleet-context */
  function renderFleet(el, f) {
    if (!f) { el.hidden = true; return; }
    var dots = f.isolated ? [[.2,.3,0],[.4,.6,0],[.6,.25,0],[.7,.7,1],[.85,.4,0]] : [[.4,.4,1],[.5,.3,1],[.55,.55,1],[.35,.6,1],[.65,.4,1],[.1,.2,0]];
    var svg = '<svg viewBox="0 0 160 110" role="img" aria-label="' + (f.isolated ? "One vessel flagged" : "Many vessels flagged") + '"><rect width="160" height="110" fill="#e3eaef"/>' +
      dots.map(function (d) { return '<circle cx="' + d[0] * 160 + '" cy="' + d[1] * 110 + '" r="' + (d[2] ? 5 : 3.5) + '" fill="' + (d[2] ? "#d8431f" : "#5a6674") + '"' + (d[2] ? "" : ' opacity=".55"') + "/>"; }).join("") + "</svg>";
    el.innerHTML = '<h3>Fleet context</h3><div class="fleet-ctx">' + svg + "<p>" + (f.isolated ? "Only this vessel flagged within " + f.radius_km + " km. That points to something aimed at it." :
      f.cluster_vessels + " vessels flagged within " + f.radius_km + " km of each other. That points to an area problem.") + "</p></div>";
  }

  /* detector-votes */
  function renderVotes(el, votes) {
    el.innerHTML = '<h3>Detector votes</h3><table class="tbl"><thead><tr><th>Detector</th><th>Result</th><th class="r">Value</th></tr></thead><tbody>' + votes.map(function (v) {
      var res = v.label ? esc(v.label.replace(/_/g, " ")) + (v.confidence ? " (" + Math.round(v.confidence * 100) + "%)" : "") : (v.fired ? "Fired" : "Quiet");
      var val = v.value == null ? "" : v.value + (v.threshold != null ? " / " + v.threshold : "");
      return "<tr><td>" + esc(v.detector) + (v.mode === "shadow" ? ' <span class="tag">shadow</span>' : "") + "</td><td>" + res + '</td><td class="r num">' + esc(val) + "</td></tr>";
    }).join("") + "</tbody></table>";
  }

  /* review-form */
  function renderReview(el, d) {
    if (d.review_verdict) { el.innerHTML = '<h3>Review</h3><p>Verdict: ' + esc(VERDICT[d.review_verdict]) + (d.reviewed_by ? " by " + esc(d.reviewed_by) : "") + (d.reviewed_at ? ", " + ago(d.reviewed_at) : "") + ".</p>"; return; }
    el.innerHTML = '<h3>Review</h3><form><fieldset style="border:0;padding:0;margin:0"><legend class="sr">Verdict</legend><div class="verdicts">' + Object.keys(VERDICT).map(function (k, i) {
      return '<label><input type="radio" name="verdict" value="' + k + '"' + (i === 0 ? " required" : "") + "> " + VERDICT[k] + "</label>"; }).join("") +
      '</div></fieldset><label class="sr" for="notes">Notes</label><textarea id="notes" class="notes" placeholder="Notes (optional)"></textarea>' +
      '<button class="btn" type="submit">Record verdict</button> <span class="muted" data-msg style="margin-left:.75rem;font-size:var(--fs-small)"></span></form>';
    el.querySelector("form").addEventListener("submit", function (e) {
      e.preventDefault();
      var f = e.target, btn = f.querySelector("button"), msg = f.querySelector("[data-msg]");
      btn.disabled = true; msg.textContent = "Saving.";
      api.review(d.id, { verdict: f.verdict.value, notes: f.notes.value }).then(function () {
        renderQueue(); openIncident(d.id);
      }, function () { btn.disabled = false; msg.textContent = "Could not save the verdict. Try again."; });
    });
  }

  /* incident-detail */
  function openIncident(id) {
    state.selected = id;
    document.querySelector(".console").setAttribute("data-view", "detail");
    document.querySelectorAll(".row").forEach(function (r) { r.toggleAttribute("aria-current", Number(r.dataset.id) === id); if (Number(r.dataset.id) === id) r.setAttribute("aria-current", "true"); });
    var el = $("incident-detail");
    el.innerHTML = '<p class="skeleton">Loading incident.</p>';
    Promise.all([api.incident(id), api.zones().catch(function () { return null; })]).then(function (res) {
      var d = res[0], open = d.status !== "resolved";
      el.innerHTML = '<button class="detail__back" data-back>Back to incidents</button>' +
        '<div class="detail__meta num"><span>MMSI ' + esc(d.mmsi) + "</span><span>Flagged " + esc(new Date(d.flagged_at).toISOString().replace("T", " ").slice(0, 16)) + ' UTC</span><span>' + esc(STATUS[d.status]) + "</span></div>" +
        "<h2>" + esc(d.vessel_name || d.mmsi) + ": <span" + (open && d.hypothesis !== "benign" ? ' class="flag"' : "") + ">" + esc(HYP[d.hypothesis].toLowerCase()) + "</span>, " + Math.round(d.confidence * 100) + " percent sure</h2>" +
        '<section class="block" data-module="map"><h3>Track</h3><div data-map></div></section>' +
        '<section class="block"><h3>Evidence</h3><ul>' + d.evidence.map(function (e) { return "<li>" + esc(e.text) + "</li>"; }).join("") + "</ul></section>" +
        '<section class="block" data-module="detector-votes"></section><section class="block" data-module="fleet-context"></section>' +
        '<section class="block"><h3>Agent log</h3><ol class="log">' + d.tool_call_log.map(function (t) { return '<li><span class="mono">' + esc(t.tool) + "</span><span>" + esc(t.summary) + "</span></li>"; }).join("") + "</ol></section>" +
        '<section class="block"><h3>Report</h3><p class="report">' + esc(d.report_text) + "</p></section>" +
        '<section class="block" data-module="review-form"></section>';
      renderMap(el.querySelector("[data-map]"), d.track || [], res[1]);
      renderVotes(el.querySelector('[data-module="detector-votes"]'), d.votes || []);
      renderFleet(el.querySelector('[data-module="fleet-context"]'), d.fleet_context);
      renderReview(el.querySelector('[data-module="review-form"]'), d);
      if (window.GHAST.source === "sample" && !d.review_verdict) el.querySelector("[data-msg]").textContent = "Recorded in this session only.";
    }, function () { el.innerHTML = '<p class="skeleton">Could not load this incident. Pick it again from the list.</p>'; });
  }

  document.addEventListener("click", function (e) {
    var row = e.target.closest(".row"); if (row) openIncident(Number(row.dataset.id));
    if (e.target.closest("[data-back]")) { document.querySelector(".console").setAttribute("data-view", "queue"); }
    if (e.target.closest("[data-retry]")) renderQueue();
  });
  document.querySelector('[data-module="incident-queue"]').addEventListener("change", function (e) {
    state[e.target.name] = e.target.value; renderQueue();
  });
  renderStatus(); renderQueue();
})();

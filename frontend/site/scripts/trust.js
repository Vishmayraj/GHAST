/* Trust page modules: alert-budget and review-stats. Both handle empty and error. */
(function () {
  var api = window.GHAST.data;
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var tag = window.GHAST.source === "sample" ? ' <span class="tag">Sample data</span>' : "";

  var budget = document.querySelector('[data-module="alert-budget"]');
  api.thresholds().then(function (t) {
    if (t.active) {
      var a = t.active;
      budget.innerHTML = '<table class="tbl"><thead><tr><th>Alert level</th><th class="r">Threshold</th></tr></thead><tbody><tr><td>Incident on the console (A)</td><td class="r num">' + esc(a.threshold_a) + '</td></tr><tr><td>Report drafted automatically (B)</td><td class="r num">' + esc(a.threshold_b) + '</td></tr></tbody></table><p class="muted" style="margin-top:.75rem;font-size:var(--fs-small)">Set by ' + esc(a.set_by) + (a.model_version ? " for model " + esc(a.model_version) : "") + (a.reason ? ". " + esc(a.reason) : "") + "." + tag + "</p>";
      return;
    }
    if (!t.options) { budget.innerHTML = '<p class="empty">No thresholds have been recorded yet. The scorer is observing.</p>'; return; }
    budget.innerHTML = '<table class="tbl"><thead><tr><th>Flags per 1,000 reports</th><th class="r">Threshold</th></tr></thead><tbody>' + t.options.map(function (o) {
      var chosen = t.chosen === o.flag_rate;
      return "<tr><td>" + (o.flag_rate * 10) + (chosen ? " (chosen)" : "") + '</td><td class="r num">' + (o.threshold == null ? "not measured yet" : esc(o.threshold)) + "</td></tr>";
    }).join("") + "</tbody></table><p class=\"muted\" style=\"margin-top:.75rem;font-size:var(--fs-small)\">" + (t.chosen == null ? "No rate has been chosen yet." : "") + tag + "</p>";
  }, function () { budget.innerHTML = '<p class="empty">The alert budget is not available right now.</p>'; });

  var stats = document.querySelector('[data-module="review-stats"]');
  api.reviewStats().then(function (s) {
    if (s.reviewed < s.minimum || !s.rows.length) {
      stats.innerHTML = '<p class="empty">Too few reviewed incidents to trust a rate. <span class="num">' + s.reviewed + " of " + s.minimum + "</span> reviewed so far." + tag + "</p>"; return;
    }
    stats.innerHTML = '<table class="tbl"><thead><tr><th>Hypothesis</th><th class="r">Reviewed</th><th class="r">Precision</th></tr></thead><tbody>' + s.rows.map(function (r) {
      return "<tr><td>" + esc(r.hypothesis) + '</td><td class="r num">' + r.reviewed + '</td><td class="r num">' + (r.precision == null ? "n/a" : Math.round(r.precision * 100) + "%") + "</td></tr>";
    }).join("") + "</tbody></table>" + (tag ? "<p>" + tag + "</p>" : "");
  }, function () { stats.innerHTML = '<p class="empty">Review statistics are not available right now.</p>'; });
})();

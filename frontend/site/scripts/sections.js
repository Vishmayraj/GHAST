/**
 * sections.js
 *
 * Builds the "looks like real product output" pieces of the page
 * (incident card, fleet maps, agent trace, dashboard preview) from
 * window.GHAST_MOCK. Nothing here knows the data is mock — swapping
 * mock-data.js for a fetch() against the real scoring/incident API
 * shouldn't require touching this file.
 */

(function () {
  "use strict";

  var mock = window.GHAST_MOCK;
  if (!mock) return;

  function el(tag, className, html) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  function pct(n) {
    return Math.round(n * 100) + "%";
  }

  // ---- fleet maps (isolated vs widespread) ----
  function renderFleetMap(mount, example) {
    if (!mount) return;
    var w = 320, h = 240;
    var dots = example.vessels.map(function (v) {
      var cx = v.x * w, cy = v.y * h;
      var fill = v.flagged ? "var(--color-anomaly)" : "var(--ocean-400)";
      var r = v.flagged ? 7 : 5;
      var ring = v.flagged
        ? '<circle cx="' + cx + '" cy="' + cy + '" r="' + (r + 5) + '" fill="none" stroke="var(--color-anomaly)" stroke-width="1.5" opacity="0.45" />'
        : "";
      return ring + '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="' + fill + '" />';
    }).join("");

    mount.querySelector(".fleet-map__svg").innerHTML =
      '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="' + example.label + '">' + dots + '</svg>';
    mount.querySelector(".fleet-map__result").textContent = example.label;
  }

  renderFleetMap(document.getElementById("fleet-targeted"), mock.fleetExamples.targeted);
  renderFleetMap(document.getElementById("fleet-jamming"), mock.fleetExamples.jamming);

  // ---- agent trace ----
  var traceMount = document.getElementById("agent-trace-mount");
  if (traceMount) {
    mock.incident.agentTrace.forEach(function (step) {
      var li = el("li", "agent-trace__step");
      li.innerHTML =
        '<div class="agent-trace__tool">' + step.tool + '()</div>' +
        '<div class="agent-trace__note">' + step.note + '</div>';
      traceMount.appendChild(li);
    });
    var gateLi = el("li", "agent-trace__step");
    var gateText = mock.incident.escalated
      ? "Confidence " + pct(mock.incident.confidence) + " — below threshold, escalated to a human analyst with partial evidence attached."
      : "Confidence " + pct(mock.incident.confidence) + " — above threshold, structured incident report generated.";
    gateLi.innerHTML =
      '<div class="agent-trace__tool">confidence_gate()</div>' +
      '<div class="agent-trace__gate">' + gateText + '</div>';
    traceMount.appendChild(gateLi);
  }

  // ---- incident report card ----
  var cardMount = document.getElementById("incident-card-mount");
  if (cardMount) {
    var inc = mock.incident;
    cardMount.innerHTML =
      '<div class="incident-card__head">' +
        '<div>' +
          '<div class="incident-card__vessel">' + inc.vessel.name + '</div>' +
          '<div class="incident-card__meta">MMSI ' + inc.vessel.mmsi + ' · ' + inc.vessel.class + '</div>' +
        '</div>' +
        '<span class="incident-card__badge">' + inc.hypothesisLabel + '</span>' +
      '</div>' +
      '<div class="incident-card__confidence">' +
        '<div class="incident-card__meta">Agent confidence &mdash; ' + pct(inc.confidence) + '</div>' +
        '<div class="confidence-bar"><div class="confidence-bar__fill" style="width:' + pct(inc.confidence) + '"></div></div>' +
      '</div>' +
      '<ul class="incident-card__evidence">' +
        inc.evidence.map(function (e) { return "<li>" + e + "</li>"; }).join("") +
      '</ul>' +
      '<p class="incident-card__note">Illustrative report — generated here from mock data, not a live detection.</p>';
  }

  // ---- dashboard preview: incident feed ----
  var feedMount = document.getElementById("dashboard-feed-mount");
  if (feedMount) {
    mock.incidentFeed.forEach(function (row) {
      var div = el("div", "feed-row");
      div.innerHTML =
        '<div><div class="feed-row__vessel">' + row.vessel + '</div>' +
        '<div class="feed-row__type">' + row.type + ' · ' + row.time + '</div></div>' +
        '<div class="feed-row__score">' + row.score.toFixed(2) + '</div>' +
        '<div class="feed-row__status">' + row.status + '</div>';
      feedMount.appendChild(div);
    });
  }

  // ---- dashboard preview: deviation chart ----
  var chartMount = document.getElementById("dashboard-chart-mount");
  if (chartMount && window.GHAST_CHARTS) {
    var series = mock.deviationSeries;
    var thresholdIndex = series.indexOf(Math.max.apply(null, series));
    window.GHAST_CHARTS.lineChart(chartMount, series, {
      ariaLabel: "Deviation from predicted position over time",
      thresholdIndex: thresholdIndex
    });
  }
})();

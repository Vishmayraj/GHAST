/* Sample implementation. Same methods and field names as the live one.
   Vessels are fictional. Times are relative to now so nothing looks stale. */
(function () {
  var MIN = 60000, HR = 60 * MIN, now = Date.now();
  function ago(ms) { return new Date(now - ms).toISOString(); }

  var votes = {
    pred: function (v, t) { return { detector: "Prediction error", fired: true, value: v, threshold: t, label: null, confidence: null, mode: "active" }; },
    freeze: function (fired, v) { return { detector: "Freeze check", fired: fired, value: v, threshold: 3, label: null, confidence: null, mode: "active" }; },
    laya: function (label, c) { return { detector: "Pattern classifier", fired: label !== "normal_track", value: null, threshold: null, label: label, confidence: c, mode: "shadow" }; }
  };

  var raw = [
    { id: 1048, mmsi: "244710820", vessel_name: "MV Kestrel Bay", mins: 14, hypothesis: "targeted_spoof", confidence: 0.81, status: "escalated", rv: null,
      votes: [votes.pred(0.0187, 0.0049), votes.freeze(false, 0), votes.laya("teleport_jump", 0.88)],
      fleet: { cluster_vessels: 1, radius_km: 25, isolated: true },
      evidence: ["The reported position jumped about 340 m in 12 seconds, more than this vessel can turn or travel at its speed.", "No other vessel within 25 km flagged in the same period.", "The position is outside every known jamming area.", "No earlier incident on this vessel."] },
    { id: 1047, mmsi: "538007112", vessel_name: "Northern Tern", mins: 52, hypothesis: "jamming", confidence: 0.77, status: "reported", rv: null,
      votes: [votes.pred(0.0094, 0.0049), votes.freeze(false, 0), votes.laya("gradual_drift", 0.74)],
      fleet: { cluster_vessels: 7, radius_km: 18, isolated: false },
      evidence: ["Seven vessels within 18 km flagged in the same 10 minutes.", "Reported positions drift in the same direction on all seven."] },
    { id: 1045, mmsi: "636019330", vessel_name: "Alder Point", mins: 3 * 60 + 10, hypothesis: "freeze_replay", confidence: 0.69, status: "reported", rv: null,
      votes: [votes.pred(0.0061, 0.0049), votes.freeze(true, 6), votes.laya("freeze_replay", 0.91)],
      fleet: { cluster_vessels: 1, radius_km: 25, isolated: true },
      evidence: ["Six consecutive reports repeat the same position while reported speed stays above 8 knots.", "No other vessel nearby is affected."] },
    { id: 1043, mmsi: "219884005", vessel_name: "Sofie Marit", mins: 6 * 60, hypothesis: "equipment_fault", confidence: 0.58, status: "escalated", rv: null,
      votes: [votes.pred(0.0072, 0.0049), votes.freeze(false, 0), votes.laya("impossible_kinematics", 0.66)],
      fleet: { cluster_vessels: 1, radius_km: 25, isolated: true },
      evidence: ["Speed over ground reads above 60 knots for two reports, then returns to normal.", "Position stays consistent with the earlier track."] },
    { id: 1040, mmsi: "477123900", vessel_name: "Harbour Wren", mins: 9 * 60, hypothesis: "benign", confidence: 0.41, status: "reported", rv: null,
      votes: [votes.pred(0.0052, 0.0049), votes.freeze(false, 0), votes.laya("normal_track", 0.93)],
      fleet: { cluster_vessels: 1, radius_km: 25, isolated: true },
      evidence: ["Only the prediction error detector fired, and only just over its threshold.", "The vessel was drifting at anchor."] },
    { id: 1031, mmsi: "311042700", vessel_name: "Calder Reach", mins: 30 * 60, hypothesis: "targeted_spoof", confidence: 0.84, status: "resolved", rv: "confirmed_spoof",
      votes: [votes.pred(0.0213, 0.0049), votes.freeze(false, 0), votes.laya("teleport_jump", 0.9)],
      fleet: { cluster_vessels: 1, radius_km: 25, isolated: true },
      evidence: ["Position moved 2.1 km between two reports 20 seconds apart.", "Neighbouring vessels were unaffected."] },
    { id: 1024, mmsi: "563201880", vessel_name: "Tamsin Ray", mins: 52 * 60, hypothesis: "jamming", confidence: 0.72, status: "resolved", rv: "unclear",
      votes: [votes.pred(0.0088, 0.0049), votes.freeze(false, 0), votes.laya("gradual_drift", 0.6)],
      fleet: { cluster_vessels: 4, radius_km: 20, isolated: false },
      evidence: ["Four vessels within 20 km flagged together.", "Not enough reports to rule out a shared receiver fault."] }
  ];

  var names = { jamming: "Area jamming", targeted_spoof: "Targeted spoof", equipment_fault: "Equipment fault", freeze_replay: "Frozen or replayed track", benign: "Benign" };

  function track(item, seed) {
    var pts = [], n = 24, lat = 51.2 + (seed % 7) * 0.31, lon = 2.4 + (seed % 5) * 0.4;
    for (var i = 0; i < n; i++) {
      var t = now - item.mins * MIN - (n - i) * 5 * MIN;
      var pl = lat + i * 0.004, po = lon + i * 0.009 + Math.sin(i / 5) * 0.002;
      var flagged = i >= n - 3, off = flagged ? (i - (n - 4)) * 0.016 : 0;
      pts.push({ time: new Date(t).toISOString(), lat: pl + off, lon: po - off * 0.8, predicted_lat: pl, predicted_lon: po, flagged: flagged && i === n - 1 });
    }
    return pts;
  }

  function summary(r) {
    return { id: r.id, mmsi: r.mmsi, vessel_name: r.vessel_name, flagged_at: ago(r.mins * MIN), hypothesis: r.hypothesis, hypothesis_label: names[r.hypothesis],
      confidence: r.confidence, status: r.status, review_verdict: r.rv, reviewed_by: r.rv ? "analyst" : null, reviewed_at: r.rv ? ago((r.mins - 40) * MIN) : null };
  }
  function detail(r) {
    var d = summary(r);
    d.window_start = ago(r.mins * MIN + 100 * MIN); d.window_end = ago(r.mins * MIN);
    d.votes = r.votes; d.fleet_context = r.fleet;
    d.evidence = r.evidence.map(function (t) { return { text: t }; });
    d.tool_call_log = [
      { tool: "track_history", summary: "Pulled the last 20 reports for this vessel.", at: ago(r.mins * MIN - 20000) },
      { tool: "jamming_zones", summary: "Checked the position against known jamming areas.", at: ago(r.mins * MIN - 22000) },
      { tool: "incident_history", summary: "Looked for earlier incidents on this vessel and the same pattern elsewhere.", at: ago(r.mins * MIN - 25000) },
      { tool: "form_hypothesis", summary: names[r.hypothesis] + " at " + Math.round(r.confidence * 100) + " percent.", at: ago(r.mins * MIN - 26000) }
    ];
    d.report_text = "Sample report. " + d.evidence.map(function (e) { return e.text; }).join(" ");
    d.track = track(r, r.id);
    return d;
  }
  function wait(v) { return new Promise(function (ok) { setTimeout(function () { ok(v); }, 120); }); }

  window.GHAST_SAMPLE = {
    health: function () { return wait({ database: "ok", latest_position_age_s: 4, latest_incident_age_s: raw[0].mins * 60 }); },
    incidents: function (p) {
      p = p || {};
      var rows = raw.filter(function (r) { return (!p.status || r.status === p.status) && (!p.hypothesis || r.hypothesis === p.hypothesis) && (!p.mmsi || r.mmsi === p.mmsi); });
      return wait({ items: rows.map(summary) });
    },
    incident: function (id) {
      var r = raw.filter(function (x) { return x.id === Number(id); })[0];
      return r ? wait(detail(r)) : Promise.reject(new Error("Incident not found"));
    },
    track: function (mmsi) { var r = raw.filter(function (x) { return x.mmsi === mmsi; })[0]; return wait(r ? track(r, r.id) : []); },
    vesselIncidents: function (mmsi) { return wait({ items: raw.filter(function (r) { return r.mmsi === mmsi; }).map(summary) }); },
    zones: function () { return wait({ type: "FeatureCollection", features: [] }); },
    review: function (id, body) {
      var r = raw.filter(function (x) { return x.id === Number(id); })[0];
      r.status = "resolved"; r.rv = body.verdict; return wait(summary(r));
    },
    reviewStats: function () { return wait({ reviewed: 2, minimum: 30, rows: [] }); },
    thresholds: function () {
      return wait({ chosen: null, options: [{ flag_rate: 5, threshold: null }, { flag_rate: 1, threshold: null }, { flag_rate: 0.5, threshold: null }, { flag_rate: 0.1, threshold: null }] });
    }
  };
})();

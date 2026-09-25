/**
 * mock-data.js
 *
 * Illustrative data only — GHAST's ingestion/detection/agent layers
 * are not wired to this site. Everything here is shaped the way the
 * HLD's scoring endpoint, incident feed and agent report generator
 * are expected to respond, specifically so the rendering code in
 * sections.js / charts.js doesn't have to change when a real
 * fetch("/api/...") replaces window.GHAST_MOCK.
 */

window.GHAST_MOCK = {
  // A single flagged incident, shaped like the agent's structured
  // report output (HLD §4).
  incident: {
    vessel: { name: "MV KESTREL BAY", mmsi: "244" + "710" + "820", class: "Bulk carrier" },
    flaggedAt: "2026-09-14T02:41:00Z",
    anomalyScore: 0.93,
    hypothesis: "targeted_spoof",
    hypothesisLabel: "Targeted spoof candidate",
    confidence: 0.81,
    escalated: false,
    evidence: [
      "Reported position deviated 340m from Bi-LSTM predicted state in 12s — exceeds vessel's physical turn-rate limit at reported speed.",
      "DBSCAN fleet-wide check: no other vessel in a 15nm radius shows a coincident anomaly — isolated, not area jamming.",
      "No match against 6 known jamming/spoofing zones on file.",
      "No prior incident on this MMSI in the last 180 days."
    ],
    agentTrace: [
      { tool: "track_history", note: "Pulled 6h trajectory, 14 nm track" },
      { tool: "jamming_zones", note: "Checked against 6 curated zones — no match" },
      { tool: "incident_history", note: "Checked MMSI + 15nm radius — no related incidents" }
    ]
  },

  // Fleet-wide differentiation examples (HLD §3.2 / §4.1): the same
  // detector output routed two different ways by the DBSCAN check.
  fleetExamples: {
    targeted: {
      label: "Isolated — targeted spoofing candidate",
      vessels: [
        { x: 0.22, y: 0.35, flagged: false }, { x: 0.31, y: 0.52, flagged: false },
        { x: 0.44, y: 0.28, flagged: false }, { x: 0.58, y: 0.63, flagged: true },
        { x: 0.67, y: 0.41, flagged: false }, { x: 0.78, y: 0.55, flagged: false },
        { x: 0.19, y: 0.68, flagged: false }, { x: 0.85, y: 0.3, flagged: false }
      ]
    },
    jamming: {
      label: "Widespread — area jamming candidate",
      vessels: [
        { x: 0.4, y: 0.4, flagged: true }, { x: 0.5, y: 0.35, flagged: true },
        { x: 0.46, y: 0.52, flagged: true }, { x: 0.58, y: 0.46, flagged: true },
        { x: 0.36, y: 0.55, flagged: true }, { x: 0.53, y: 0.6, flagged: true },
        { x: 0.1, y: 0.2, flagged: false }, { x: 0.9, y: 0.8, flagged: false }
      ]
    }
  },

  // Deviation-vs-time series for the dashboard preview's per-vessel
  // drilldown chart. Units: meters of deviation from predicted
  // next-state, sampled roughly every 30s.
  deviationSeries: [
    2, 3, 2, 4, 3, 5, 4, 3, 6, 5, 4, 7, 6, 5, 8, 9, 7, 12, 45, 210, 340, 298, 150, 60, 22, 10, 6, 4, 3
  ],

  // Incident feed for the dashboard preview list.
  incidentFeed: [
    { vessel: "MV KESTREL BAY", type: "Targeted spoof", score: 0.93, status: "Escalated to analyst", time: "02:41 UTC" },
    { vessel: "MV NORTHERN TIDE", type: "Area jamming", score: 0.88, status: "Report generated", time: "22:07 UTC" },
    { vessel: "MV SEA ARBITER", type: "Equipment fault", score: 0.41, status: "Auto-dismissed", time: "19:53 UTC" },
    { vessel: "MV HALCYON REACH", type: "Targeted spoof", score: 0.76, status: "Report generated", time: "18:12 UTC" }
  ]
};

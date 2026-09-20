# agent/

The investigation layer: a bounded, tool-using agent that turns a flagged anomaly into an evidence-backed incident report. This is the "agentic" requirement from the proposal — bounded, evidence-gathering security work, not open-ended chat.

- `orchestrator/` — the state machine driving the agent workflow.
- `tools/` — the three Stage 1 tools: track history, jamming-zone lookup, incident history.
- `report_generator/` — drafts the structured incident report.

Design principles (carried over from the proposal, MIP section 4.2): the agent never auto-declares "this is spoofing" — it produces a scored hypothesis with evidence attached, escalation is confidence-driven rather than magnitude-driven, and every tool call is logged so reports stay auditable.

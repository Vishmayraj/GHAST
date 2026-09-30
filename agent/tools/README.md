# agent/tools/

The agent tools (three Stage 1 tools plus the optional Laya classifier):

- `track_history` — pulls a vessel's recent and full trajectory.
- `jamming_zones` — checks the flagged position/time against known jamming/spoofing zones (`data/jamming_zones/`).
- `incident_history` — checks for similar past incidents.

- `pattern_classifier` — optional Laya vote on the flagged 20-report window; a neutral stub without a model (see `docs/laya_pattern_classifier.md`).

Each tool call and its result gets logged by the orchestrator for auditability.

# agent/tools/

The three Stage 1 agent tools:

- `track_history` — pulls a vessel's recent and full trajectory.
- `jamming_zones` — checks the flagged position/time against known jamming/spoofing zones (`data/jamming_zones/`).
- `incident_history` — checks for similar past incidents.

Each tool call and its result gets logged by the orchestrator for auditability.

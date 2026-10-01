# agent/tools/

The agent tools (three Stage 1 tools plus the optional Laya classifier):

- `track_history`: a vessel's recent trajectory up to and including the flag; reports after the flag only on request (`after_hours`).
- `jamming_zones`: checks the flagged position/time against known jamming/spoofing zones (`data/jamming_zones/`).
- `incident_history`: earlier incidents on the same vessel (`same_vessel`) and the same pattern on other vessels (`same_pattern_elsewhere`), kept apart.

- `pattern_classifier`: optional Laya vote on the flagged 20-report window; a neutral stub without a model (see `docs/laya_pattern_classifier.md`).

Two helpers are not tool calls: `freeze_corroboration` and `stationary` are computed by the orchestrator from `track_history`'s own result.

Each tool call and its result gets logged by the orchestrator for auditability.

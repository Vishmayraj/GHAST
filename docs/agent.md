# Agent and orchestrator

The investigation layer in `agent/`. It takes one flagged report from the live scorer, gathers evidence with four tools, applies fixed rules to form a hypothesis and a confidence, writes an `incidents` row, and optionally has an LLM draft a text report. Only the report is model-written. Everything that decides the hypothesis is deterministic code.

Entry point: `agent.orchestrator.state_machine.investigate()`. Its caller is `scoring/live_scorer.py` (`docs/scoring-and-evaluation.md`). Nothing else in the repo calls it except tests.

## What is deterministic and what is not

| Step | Deterministic | Model or external |
|---|---|---|
| detector votes, score, flag | yes (BiLSTM inference plus thresholds, in the scorer) | BiLSTM weights |
| `track_history`, `jamming_zones`, `incident_history` | yes, SQL against PostgreSQL | none |
| `pattern_classifier` | code is deterministic | Laya model, optional |
| freeze corroboration | yes | none |
| hypothesis and confidence | yes, `form_hypothesis` | none |
| report or escalate | yes, confidence at or above 0.7 | none |
| report text | no | Groq chat model, optional |

No web search or external lookup exists anywhere in the agent. The only external call is the Groq report request. "Jamming zone" knowledge comes from the `jamming_zones` table, which nothing populates (`docs/data-pipeline.md`).

## Flow

```text
FlaggedAnomaly (mmsi, flagged_at, score, anomaly_type, lat, lon, detector_votes)
  1. track_history        -> evidence["track_history"]
  2. jamming_zones        -> evidence["jamming_zones"]
  3. incident_history     -> evidence["incident_history"]
  4. pattern_classifier   -> evidence["pattern_classifier"]   (only if the tool is registered)
  5. freeze corroboration -> evidence["freeze_corroboration"] (computed from step 1, not a tool call)
  6. detector votes       -> evidence["detector_corroboration"]
  7. form_hypothesis(anomaly, evidence) -> (hypothesis, confidence)
  8. confidence >= 0.7 ? status "reported" (+ optional LLM report) : status "escalated"
  9. persist one incidents row
```

Each of steps 1 to 4 is appended to `tool_call_log` with its full result. Step 4 is wrapped in a try/except: an exception becomes `{"available": False, ...}`. Steps 1 to 3 are not wrapped, so an exception from a database tool propagates out of `investigate`; the scorer catches it, counts the vessel as `failed`, and moves on without persisting anything for that flag.

## The "state machine"

`InvestigationState` defines six values (`received`, `gathering_evidence`, `hypothesizing`, `reporting`, `escalating`, `done`) but `investigate()` is a straight-line function. It never sets the first three. It picks `REPORTING` or `ESCALATING` from the confidence, and returns `DONE`. Only `reporting` versus `escalating` has an effect, and it becomes the `status` column value (`reported` or `escalated`). Nothing branches on the other states and nothing retries or loops. The name overstates it; there is no LangGraph or graph runtime, despite `agent/orchestrator/README.md` mentioning it as an option.

## Tools

All take the `FlaggedAnomaly` and return a dict. In production they are closures over an asyncpg pool built by `scoring/live_scorer.py::build_tools`.

| Tool | Query | Result |
|---|---|---|
| `track_history` | rows for the MMSI with `received_at` within 24 hours before and 24 hours after `flagged_at`, ordered by time, columns `received_at, latitude, longitude, sog_knots, cog_deg` | `{"positions": [...]}` |
| `jamming_zones` | first active zone with `ST_Contains(zone, point)` whose `first_seen`/`last_seen` bracket `flagged_at` (nulls allowed) | `{"matched": bool, "zone": {name, confidence} or None}` |
| `incident_history` | up to 10 `incidents` rows where `mmsi` matches OR `anomaly_type` matches, newest first | `{"similar_incidents": [...]}` |
| `pattern_classifier` | in-process Laya call on the summary of the 20 reports ending at the flag | see `docs/laya_pattern_classifier.md` |

Behavior worth knowing:

- `track_history` reads all sources and looks forward as well as back. The positions it returns can include reports after `flagged_at`, so evidence and the LLM report's "last position" can describe times after the flag. In `ghast_latest_report.md` the flag is at 19:41:47 and the last position is at 19:45:10.
- The freeze corroboration runs on that whole 48 hour span, not on the 20-report window the detectors scored. The same report shows 23 pairs for 24 positions over about 6 hours.
- `incident_history` matches on `anomaly_type` alone across all vessels. `anomaly_type` is the sorted vote names joined with `+` (`prediction_error`, `freeze_replay+prediction_error`, ...). After the first incident of a given type is stored, every later flag of that type, on any vessel, finds a non-empty history. See the tier table below for what that does. It also includes incidents of any status and any age.
- `jamming_zones` has no data source in the repo, so on a database where nobody inserted zones, `matched` is always false.

## Hypothesis rules (`form_hypothesis`)

Evaluated top to bottom, first match returns. `OPERATING_THRESHOLD` is 0.004946 from `ml/models/bilstm/threshold.py`.

| # | Condition | Hypothesis | Confidence | Cap logic applied |
|---|---|---|---|---|
| 1 | jamming zone matched | `jamming` | 0.85 | never |
| 2 | `OPERATING_THRESHOLD is None` | `unresolved` | 0.0 | no |
| 3 | score below threshold and no detector other than `prediction_error` voted | `benign` | 0.75 | never |
| 4 | freeze corroboration matched | `freeze_replay` | 0.8 | yes, but ignores a Laya contradiction |
| 5 | no similar incidents | `targeted_spoof` | 0.72 | yes |
| 6 | otherwise | `equipment_fault` | 0.55 | yes |

Cap logic (`_apply_single_detector_cap`): if exactly one detector voted (`len(detector_votes) == 1`), confidence is capped at 0.5 unless Laya confidently agrees. A confident Laya `normal_track` caps at 0.5 regardless of the vote count (except in row 4). An empty `detector_votes` (callers that do not track it) is never treated as a single vote.

`REPORT_CONFIDENCE_THRESHOLD = 0.7` decides the outcome. Combined with the tier table:

- `jamming` (0.85), `freeze_replay` (0.8) and `targeted_spoof` (0.72) are reported when uncapped.
- `equipment_fault` (0.55) is escalated always, and any capped result (0.5) is escalated.
- The reported/escalated split therefore encodes "how many independent signals agreed and whether this vessel or flag type has been seen", not severity.

Consequences of the rules as written, none of them fixed:

- Row 3 cannot be reached from the live scorer. A flag is only produced when at least one detector voted, and the `prediction_error` vote requires a score above the threshold, so a flag with a below-threshold score must carry a `freeze_replay` or `speed_jump` vote, which blocks row 3. `benign` occurs only in direct calls and tests. The scorer therefore never records a benign incident, only flags that pass some detector.
- Row 5 versus 6 depends on `incident_history`, and because that tool matches `anomaly_type` across all vessels, `targeted_spoof` is only reachable for the first incident of each `anomaly_type` string. After that, flags of that type on other vessels resolve to `equipment_fault` and are escalated, unless freeze corroboration matched (row 4). Whether this is intended is not stated anywhere; the docstrings describe it as "check for similar past incidents".
- Row 1 outranks everything, and a matched zone gives a report even for a flag with a single vote.
- There is no rule that produces `resolved` status. Nothing in the repo sets it (`docs/data-pipeline.md`).
- The confidences (0.85, 0.8, 0.72, 0.55, 0.75, cap 0.5) and the 0.7 report threshold are hand-set. `REPORT_CONFIDENCE_THRESHOLD` is marked in the code as an uncalibrated Stage 1 placeholder; nothing has calibrated the others against reviewed incidents.

## Freeze corroboration

`agent/tools/freeze_corroboration.py::corroborate_freeze_replay(track_history)` walks consecutive rows. For each pair with a non-null `sog_knots` on the later row and a valid time step, it computes position-implied speed (`features.extract.implied_speed_knots`). A pair counts as frozen when implied speed is at most 0.5 knots and reported SOG is above 0.5. `matched` is true if at least one pair is frozen. Output: `{matched, frozen_reports, total_pairs}`.

One frozen pair anywhere in 48 hours is enough. In the report in `ghast_latest_report.md` the evidence was 2 frozen pairs out of 23. A vessel that anchors, moors, or reports a wrong SOG at rest gets the same signal. There is no minimum count or fraction.

## Report generation

`agent/report_generator/report.py::draft_report(incident, client)` is called only for `reported` incidents, and only if the scorer built a Groq client (`GROQ_API_KEY` set). The scorer wraps it so any exception returns an empty string; the incident is stored with `report_text` NULL.

- Client: `groq.AsyncGroq(max_retries=0)`. Model: `GHAST_REPORT_MODEL`, default `openai/gpt-oss-120b`. Output cap: `GHAST_REPORT_MAX_TOKENS`, default 1200. (`docs/DEVELOPER_GUIDE.md` says 2000; the code and `agent/report_generator/README.md` say 1200.)
- The prompt is fixed text (`REPORT_PROMPT_PREFIX`) followed by compact JSON: the decision fields plus a summary of evidence (position count, first and last position of `track_history`, and the full zone, history, freeze, detector and classifier results). Full track history is deliberately left out.
- The prompt asks for a fixed markdown structure with a small mermaid diagram and at most three analyst actions. It tells the model not to invent facts. Nothing checks the output against the input. The analyst actions in `ghast_latest_report.md` (request more AIS, cross-check radar or VMS) are model-written suggestions, not agent output.
- Reports are not used by any other code.

## Persistence

`persist_incident` inserts one row: `mmsi, flagged_at, flagged_position, anomaly_score, anomaly_type, hypothesis, confidence, status, evidence, tool_call_log, report_text`. Evidence and log are JSON with `default=str` for datetimes. `window_start`, `window_end` and `updated_at` are never set. The insert is one statement on a pool connection; the agent does not wrap the tool queries and the insert in a transaction.

## Tests

`agent/tests` (43 tests): state machine and threshold boundary, single-detector cap, freeze tiering, jamming priority, evidence logging, Laya vote handling and failure isolation, freeze corroboration, report input compaction and error handling, tool queries against fake connections. See `docs/testing.md` for the run and skip conditions. Not tested: real SQL against PostGIS, `find_similar_incidents`, `persist_incident` against a real table, Groq calls, and the `incident_history` cross-vessel behavior above.

## Status

| Piece | State |
|---|---|
| tools, `form_hypothesis`, `investigate`, persistence | implemented, unit tested with fakes |
| Groq report drafting | implemented, tested with a fake client; one real report is committed as `ghast_latest_report.md` |
| Laya vote | implemented, tested with fakes; no fine-tuned model runs by default (`docs/laya_pattern_classifier.md`) |
| confidence values and thresholds | hand-set, uncalibrated |
| escalation workflow (a human acting on `escalated`) | not implemented, the status is only a column value |
| `resolved` status | never set |
| dashboard or API reading incidents | not implemented (`docs/backend-and-frontend.md`) |
| `jamming_zones` data | none |

Documentation that is out of date relative to this code: `agent/README.md` (three tools; says a valid run has "exactly three" audit entries, it now has four when the classifier tool is registered), `agent/orchestrator/README.md` (four hypotheses, no `freeze_replay`; LangGraph), `agent/tools/README.md` (three tools).

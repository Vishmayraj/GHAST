# Agent and orchestrator

The investigation layer in `agent/`. It takes one flagged report from the live scorer, gathers evidence with four tools, applies fixed rules to form a hypothesis and a confidence, and writes an `incidents` row. No LLM runs during the investigation. An analyst can ask for an LLM-drafted text report on a stored incident afterwards, and only the report is model-written. Everything that decides the hypothesis is deterministic code.

Entry point: `agent.orchestrator.state_machine.investigate()`. Its caller is `scoring/live_scorer.py` (`docs/scoring-and-evaluation.md`). Nothing else in the repo calls it except tests.

## What is deterministic and what is not

| Step | Deterministic | Model or external |
|---|---|---|
| detector votes, score, flag | yes (BiLSTM inference plus thresholds, in the scorer) | BiLSTM weights |
| `track_history`, `jamming_zones`, `incident_history` | yes, SQL against PostgreSQL | none |
| review verdict (`agent/review.py`) | no, a human | analyst |
| `pattern_classifier` | code is deterministic | Laya model, optional |
| freeze corroboration | yes | none |
| hypothesis and confidence | yes, `form_hypothesis` | none |
| reported or escalated | yes, confidence at or above 0.7 | none |
| report text, on request only | no | Groq chat model, optional |

No web search or external lookup exists anywhere in the agent. The only external call is the Groq report request, made only when an analyst asks for a report. "Jamming zone" knowledge comes from the `jamming_zones` table, which nothing populates (`docs/data-pipeline.md`).

## Flow

```text
FlaggedAnomaly (mmsi, flagged_at, score, anomaly_type, lat, lon, detector_votes)
  1. track_history        -> evidence["track_history"]
  2. jamming_zones        -> evidence["jamming_zones"]
  3. incident_history     -> evidence["incident_history"]
  4. pattern_classifier   -> evidence["pattern_classifier"]   (only if the tool is registered)
  5. freeze corroboration -> evidence["freeze_corroboration"] (computed from the last 20 reports of step 1, not a tool call)
  6. detector votes       -> evidence["detector_corroboration"]
  7. form_hypothesis(anomaly, evidence) -> (hypothesis, confidence)
  8. confidence >= 0.7 ? status "reported" : status "escalated" (no report text either way)
  9. persist one incidents row (including window_start and window_end from the scored window)
```

Each of steps 1 to 4 is appended to `tool_call_log` with its full result. Step 4 is wrapped in a try/except: an exception becomes `{"available": False, ...}`. Steps 1 to 3 are not wrapped, so an exception from a database tool propagates out of `investigate`; the scorer catches it, counts the vessel as `failed`, and moves on without persisting anything for that flag.

## The "state machine"

`InvestigationState` defines six values (`received`, `gathering_evidence`, `hypothesizing`, `reporting`, `escalating`, `done`) but `investigate()` is a straight-line function. It never sets the first three. It picks `REPORTING` or `ESCALATING` from the confidence, and returns `DONE`. Only `reporting` versus `escalating` has an effect, and it becomes the `status` column value (`reported` or `escalated`). Nothing branches on the other states and nothing retries or loops. The name overstates it; there is no LangGraph or graph runtime, despite `agent/orchestrator/README.md` mentioning it as an option.

## Tools

All take the `FlaggedAnomaly` and return a dict. In production they are closures over an asyncpg pool built by `scoring/live_scorer.py::build_tools`.

| Tool | Query | Result |
|---|---|---|
| `track_history` | rows for the MMSI with `received_at` within 24 hours before `flagged_at` and no later than `flagged_at`, ordered by time, columns `received_at, latitude, longitude, sog_knots, cog_deg`. A separate query for reports after the flag runs only when the caller passes `after_hours` | `{"positions": [...]}`, plus `"after": [...]` only when asked for |
| `jamming_zones` | first active zone with `ST_Contains(zone, point)` whose `first_seen`/`last_seen` bracket `flagged_at` (nulls allowed) | `{"matched": bool, "zone": {name, confidence} or None}` |
| `incident_history` | two queries, up to 10 rows each, newest first: incidents for the same `mmsi`, and incidents with the same `anomaly_type` on other vessels | `{"same_vessel": [...], "same_pattern_elsewhere": [...]}` |
| `pattern_classifier` | in-process Laya call on the summary of the 20 reports ending at the flag | see `docs/laya_pattern_classifier.md` |

Behavior worth knowing:

- `track_history` reads all sources but only up to and including `flagged_at`, so evidence and the LLM report's "last position" cannot describe times after the flag. `positions` is the recent trajectory (24 hours back by default). The scorer does not ask for the `after` slice, so nothing currently reads it.
- Freeze corroboration runs on the last 20 reports of `positions`, the same window length the detectors scored. Because `positions` ends at the flagged report, this window ends at the flag, while the scored window ends at the newest report in it; the two can differ by the reports after the flag inside the scored window.
- `incident_history` keeps "this vessel has been here before" (`same_vessel`, matches `mmsi`) apart from "this pattern has been seen elsewhere" (`same_pattern_elsewhere`, matches `anomaly_type` on other vessels). `anomaly_type` is the sorted vote names joined with `+` (`prediction_error`, `freeze_replay+prediction_error`, ...). Both include incidents of any status and any age. Only `same_vessel` feeds a hypothesis rule; `same_pattern_elsewhere` is recorded in evidence for the analyst and the report.
- `jamming_zones` stays empty until someone curates a file and runs `scripts/load_jamming_zones.py` (format in `data/jamming_zones/README.md`). No file is committed, so on a database where nobody loaded zones, `matched` is always false.

## Hypothesis rules (`form_hypothesis`)

Evaluated top to bottom, first match returns. `OPERATING_THRESHOLD` is 0.004946 from `ml/models/bilstm/threshold.py`.

| # | Condition | Hypothesis | Confidence | Cap logic applied |
|---|---|---|---|---|
| 1 | jamming zone matched | `jamming` | 0.85 | never |
| 2 | `OPERATING_THRESHOLD is None` | `unresolved` | 0.0 | no |
| 3 | score below threshold and no detector other than `prediction_error` voted | `benign` | 0.75 | never |
| 4 | freeze corroboration matched (at least `MIN_FROZEN_PAIRS` frozen pairs in the last 20 reports) | `freeze_replay` | 0.8 | yes, but ignores a Laya contradiction |
| 4b | weak isolated flag: `detector_votes` is exactly `{prediction_error}`, score at most `BENIGN_MAX_SCORE_MULTIPLE` times the threshold, stationary window, no confident Laya label for a spoofing pattern | `benign` | 0.75 | never |
| 5 | no earlier incident on this vessel (`same_vessel` empty) | `targeted_spoof` | 0.72 | yes |
| 6 | otherwise | `equipment_fault` | 0.55 | yes |

Cap logic (`_apply_single_detector_cap`): if exactly one detector voted (`len(detector_votes) == 1`), confidence is capped at 0.5 unless Laya confidently agrees. A confident Laya `normal_track` caps at 0.5 regardless of the vote count (except in row 4). An empty `detector_votes` (callers that do not track it) is never treated as a single vote.

Laya "agrees" only when its confident label is the one the hypothesis implies, from the one dict `HYPOTHESIS_IMPLIED_PATTERNS` in `state_machine.py`:

| Hypothesis | Labels that agree |
|---|---|
| `freeze_replay` | `freeze_replay` |
| `targeted_spoof` | `teleport_jump`, `gradual_drift`, `impossible_kinematics` |
| `equipment_fault` | none (Laya has no label for it) |

A confident label for a different pattern is neither agreement nor contradiction and changes nothing.

"Stationary window" (row 4b) means at least `STATIONARY_MIN_SOG_READINGS` (10) reported SOG values among the last 20 reports with a median at or below `STATIONARY_MEDIAN_SOG_KNOTS` (0.5). `BENIGN_MAX_SCORE_MULTIPLE` is 2.0. All of these, and `MIN_FROZEN_PAIRS` (3), are uncalibrated starting values. A benign result is stored as `reported` (0.75 is over the 0.7 line) but no LLM report is drafted for it. The rule is a proposal pending owner sign-off.

`REPORT_CONFIDENCE_THRESHOLD = 0.7` decides the outcome. Combined with the tier table:

- `jamming` (0.85), `freeze_replay` (0.8) and `targeted_spoof` (0.72) are reported when uncapped.
- `equipment_fault` (0.55) is escalated always, and any capped result (0.5) is escalated.
- The reported/escalated split therefore encodes "how many independent signals agreed and whether this vessel or flag type has been seen", not severity.

Consequences of the rules as written:

- Row 3 still cannot be reached from the live scorer. A flag is only produced when at least one detector voted, and the `prediction_error` vote requires a score above the threshold, so a flag with a below-threshold score must carry a `freeze_replay` or `speed_jump` vote, which blocks row 3. Row 4b is what makes `benign` reachable from a real flag: it needs a `prediction_error`-only vote barely over the threshold on a stationary window.
- Row 5 versus 6 depends only on this vessel's own history. A first incident for a vessel is `targeted_spoof` whatever the fleet has seen. A second incident on the same vessel resolves to `equipment_fault` and is escalated, unless freeze corroboration matched (row 4). Whether "a vessel with earlier incidents is more likely faulty equipment" is the right reading is a judgement the reviewed incidents should test, not a measured fact.
- Row 1 outranks everything, and a matched zone gives a reported, report-eligible incident even for a flag with a single vote.
- `resolved` is set only by an analyst verdict through `agent/review.py`. No rule in the agent sets it.
- The confidences (0.85, 0.8, 0.72, 0.55, 0.75, cap 0.5), the 0.7 report threshold, and the constants listed under the tier table are hand-set. `scoring/review_stats.py` is where they get checked against reviewed incidents; nothing has calibrated them yet.

## Freeze corroboration

`agent/tools/freeze_corroboration.py::corroborate_freeze_replay(track_history)` takes the last `FREEZE_WINDOW_REPORTS` (20) positions and walks consecutive rows. For each pair with a non-null `sog_knots` on the later row and a valid time step, it computes position-implied speed (`features.extract.implied_speed_knots`). A pair counts as frozen when implied speed is at most 0.5 knots and reported SOG is above 0.5. `matched` is true when at least `MIN_FROZEN_PAIRS` (3) pairs are frozen. Output: `{matched, frozen_reports, total_pairs}`.

The minimum keeps one bad SOG at rest (a single frozen pair) from matching. 3 is uncalibrated; the code comment says so. The earlier committed report in `ghast_latest_report.md` had 2 frozen pairs out of 23 over a 48 hour span, which under these rules would not match.

## Report generation

Nothing is drafted while the scorer runs. A report is drafted when an incident is **tier B** (its score is at or above threshold B, so `agents.pipeline` drafts it right after the incident is stored) or when an analyst asks for one on a **tier A** incident (the console's "generate report" button; today `python review.py report <id>`). Confidence does not gate this any more: the old `REPORT_DRAFT_CONFIDENCE_THRESHOLD` is gone.

`agent/report_generator/on_demand.py::generate_report(db, incident_id, client, force=False, auto=False, verifier_client=None, verifier_model=None)` is the one place a report is written. It:

1. loads the stored incident (not found: `not_found`),
2. returns the stored text if there is one (`already_drafted`, no model call) unless `force` is set,
3. drafts with `draft_report(incident, client)`; an exception or empty text gives `failed` and stores nothing,
4. runs the **report verifier agent** on the draft (below). A failing draft is redrafted once with the verifier's issues as feedback. If it still fails it is stored with a visible `> WARNING: this draft did not pass verification` line at the top, and `report_verification` says why,
5. stores `report_text`, `report_generated_at`, `report_auto`, `report_expires_at` (now + 24 hours) and `report_verification`, only while `report_text` is still NULL unless `force` (a concurrent loser gets the winner's text back as `already_drafted`).

**Lifecycle.** A report lives 24 hours. `purge_expired_reports` (run every 15 minutes by the scorer, `--retention-interval-seconds`) clears `report_text` after that and sets `report_delete_reason = 'expired'`. An analyst deleting one (`python review.py delete-report <id>`, later the console's delete button) sets it to `'analyst'`. Either way the incident, its evidence, its verdict and its score are kept forever: they are the data later training needs. A report can be generated again after it expired or was deleted. **Note for the frontend:** a deleted report must stay gone until someone asks for a new one; it is never regenerated automatically, tier B included.

- Client: `groq.AsyncGroq(max_retries=0)` for the CLI. Model: `GHAST_REPORT_MODEL`, default `openai/gpt-oss-120b`. Output cap: `GHAST_REPORT_MAX_TOKENS`, default 1200.
- The prompt is fixed text (`REPORT_PROMPT_PREFIX`) followed by compact JSON: the decision fields, the tier, a summary of evidence, the fleet-context result and the challenger's argument. Full track history is deliberately left out.

## Agents

An agent here means something that chooses which tools to call and when it has seen enough, then commits to a decision, as opposed to a function with a fixed sequence. They all run on one small runtime, `agent/runtime/agent.py`:

- the model calls read-only tools, then a required `finish` tool; code **validates** every decision (hard bounds) before anything acts on it, and a rejected decision goes back to the model once;
- the loop is capped (6 tool rounds, 700 tokens per call);
- with no `GROQ_API_KEY`, a provider error, a missing `finish`, or two rejected decisions, the agent's **deterministic baseline** decides from the same evidence and the run is marked `fallback`. A missing key degrades GHAST; it never stops it;
- every run (task, each tool call and result, decision, mode) is stored in `agent_runs`.

| Agent | Where | Decides | Cannot do |
|---|---|---|---|
| Threshold | `agent/threshold_agent` | Thresholds A and B for the current model version, from the live score distribution the scorer records (`scoring_stats`), offline `score_checkpoint` reports, incident volume and analyst verdicts. Runs on a schedule and right after a model change. | Move A more than 3x on the same model, put B under 1.5x A, leave 0.0001 to 10 degrees, retune on under 50,000 scored reports (unless it adopts a value from an offline report), or act on a change under 25 percent. |
| Fleet context | `agents/fleet_context.py` | One vessel or an area: neighbours reporting near the flag (PostGIS), and a DBSCAN cluster (`ml/models/clustering/fleet_cluster.py`) of this vessel's flag with other vessels' incidents from the same hour. Stored in `evidence.fleet_context` with `cluster_vessels`. | Call a vessel isolated with fewer than 3 neighbours, or an area without a cluster of at least 3 vessels (this one included). Counts come from the tools, not the model. |
| Challenger | `agents/challenger.py` | The strongest honest benign argument and a `benign_likelihood`, stored in `incidents.challenge`. | Change the hypothesis or close anything. Triage can lower priority by it at most a quarter. |
| Triage | `agents/triage.py` | `priority` 0 to 1 within a tier, from confidence, how far the score is between A and B, scope, the challenge and queue pressure. | Hide or close an incident. It sets order only. |
| Report verifier | `agents/report_verifier.py` | Whether a drafted report agrees with the stored incident. | Pass a draft that fails the code-side checks (wrong MMSI, hypothesis, confidence, an invented status word, "confirmed spoofing" without an analyst verdict). |

The threshold agent is an agent because the right thresholds depend on the model: every Laya fine-tune or BiLSTM retrain shifts the score scale. `scoring/live_scorer.py` re-reads `threshold_config` every cycle and compares the model version it loaded with the one the active thresholds were set for. `python scoring/thresholds.py show | history | retune | set` is the manual side. The alert budgets it aims for (`--budget-a`, `--budget-b`, defaults 1e-4 and 1e-5 of scoreable reports) are placeholders for the owner's decision.

After an incident is stored, `agents/pipeline.py` runs fleet context and the challenger in parallel, then triage, then (tier B only) the report. Each step is isolated: one failing agent is logged and skipped. The incident is already stored before any of them run, and none of them can change `hypothesis` or `status`.

So the `jamming` hypothesis still comes only from a curated zone match. A cluster is evidence the analyst sees on the console, not a rule in `form_hypothesis`. This departs from ImplementationPlans/03 part B4, on purpose: the cluster is computed after the incident is stored, and it only sees incidents that already exist, so the first vessel to flag in an area is judged alone and the later ones see it. Putting that into the deterministic rules would make the hypothesis depend on which vessel flagged first. Moving clustering ahead of persistence, or re-running fleet context when a neighbour flags, is the open design question.

## Persistence

`persist_incident` inserts one row: `mmsi, flagged_at, flagged_position, anomaly_score, anomaly_type, hypothesis, confidence, status, evidence, tool_call_log, window_start, window_end, tier` and returns the new incident id. `tier` is `A` or `B`; `report_text`, `report_generated_at` and the report lifecycle columns stay NULL until a report is drafted; `priority` and `challenge` are filled by the agents above. Evidence and log are JSON with `default=str` for datetimes. `window_start` and `window_end` are the first and last report times of the scored window, carried on the `FlaggedAnomaly` by `scoring/live_scorer.py::evaluate_window`; they are NULL for callers that do not set them, and for incidents stored before this change. `updated_at` is left to its column default. The insert is one statement on a pool connection; the agent does not wrap the tool queries and the insert in a transaction.

## Analyst review

`agent/review.py` is a small CLI over incidents that already exist. It never creates one.

```text
cd agent
python review.py list [--limit 25]
python review.py show <incident-id>
python review.py verdict <incident-id> <verdict> [--notes "..."] [--reviewer NAME] [--force]
```

- `list`: incidents with `review_verdict IS NULL`, tier B first, then triage priority, then newest, each with its tier, priority, hypothesis, confidence, status, votes (`anomaly_type`), the scored window span, and the report state (stored, none yet, expired after 24h, or deleted by an analyst).
- `report`: drafts the LLM report for any incident on request (`--force` drafts again), needs `GROQ_API_KEY`, verifies it, and prints the text.
- `delete-report`: removes a stored report on purpose. The incident is kept.
- `show`: the stored evidence (the raw position list collapsed to its count and first and last rows; `tool_call_log` is left out because it repeats the evidence), the 20-report summary text the Laya classifier reads, rebuilt from the stored `track_history` with `features.summary.summarize_rows`, and the stored report. If fewer than 20 reports are stored up to the flag it says so instead of summarizing.
- `verdict`: one of `confirmed_spoof`, `jamming`, `equipment_fault`, `benign`, `unclear`. It sets `review_verdict`, `reviewed_by` (defaults to the OS user), `reviewed_at`, `review_notes`, and `status = 'resolved'`. An existing verdict is not replaced unless `--force` is given.

`status = 'resolved'` means "has a verdict". The live scorer's debounce query only skips vessels with a non-resolved incident, so recording a verdict lifts the database side of the debounce for that vessel. The scorer also keeps an in-memory per-vessel cooldown for the same `--debounce-hours`, which a verdict does not touch. That is a decision, not an oversight: a vessel that has just been flagged should have people on the investigation, and a repeat flag for it in the meantime would only clog the flagging pipeline. Do not clear the cooldown on a verdict without revisiting that. The cooldown lives in one process and is lost on restart, so after a restart only the database side of the debounce applies. Precision numbers come from `scoring/review_stats.py` (`docs/scoring-and-evaluation.md`).

## Tests

`agent/tests` (146 tests): state machine and threshold boundary, single-detector cap, freeze tiering, jamming priority, evidence logging, Laya vote handling (including the hypothesis-to-label mapping) and failure isolation, freeze corroboration (minimum pairs, 20-report window), the weak-isolated-flag benign rule, window columns on the persisted row, report input compaction and error handling, tool queries against fake connections (`track_history` bounds and the opt-in `after` slice, the two `incident_history` queries), the report lifecycle (any tier on request, auto flag, stored text reused, force, verification and one redraft, warning banner, expiry and delete keep the incident, provider failure stores nothing, concurrent loser), the agent runtime (tools, validation, rejection, fallback, step limit), each agent's validation bounds and baseline, the incident pipeline's isolation, the threshold agent's bounds and baseline, the Laya-missing log line, and the review CLI functions against a fake connection. See `docs/testing.md` for the run and skip conditions. Not covered by the committed tests: real SQL against PostGIS and TimescaleDB, real model calls (Groq) by any agent, and Laya. The new incidents SQL (schema columns, the history and review queries, `persist_incident` with window columns) and the new `track_history` queries were run by hand against a scratch PostgreSQL 16 with PostGIS and a plain `vessel_position` table, which is not TimescaleDB.

## Status

| Piece | State |
|---|---|
| tools, `form_hypothesis`, `investigate`, persistence | implemented, unit tested with fakes |
| Groq report drafting | implemented, on request only (`review.py report`, threshold 0.8), tested with a fake client; one real report from the earlier automatic path is committed as `ghast_latest_report.md` |
| Laya vote | implemented, tested with fakes; no fine-tuned model runs by default (`docs/laya_pattern_classifier.md`) |
| confidence values and thresholds | hand-set, uncalibrated |
| analyst review (`agent/review.py`, `scoring/review_stats.py`) | implemented, unit tested with fakes; no incident has been reviewed yet |
| `resolved` status | set only by a recorded verdict |
| dashboard or API reading incidents | not implemented (`docs/backend-and-frontend.md`) |
| `jamming_zones` data | loader written, no curated file |

Documentation that is out of date relative to this code: `agent/README.md` (three tools; says a valid run has "exactly three" audit entries, it now has four when the classifier tool is registered), `agent/orchestrator/README.md` (LangGraph).

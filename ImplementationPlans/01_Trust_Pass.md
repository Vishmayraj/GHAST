# 01 Trust pass: real threshold, honest agent rules, analyst review loop

Depends on: nothing. Do this first. Everything else assumes the system can say how often it is right.

## Why

The only quality number the project ever had (F1 0.424) came from labels made by a synthetic injector that is now removed. `OPERATING_THRESHOLD = 0.004946` is a placeholder from that measurement. The agent's confidences are hand-set, several rules misbehave (`docs/agent.md`, "Consequences of the rules as written"), and `escalated` ends in a column nobody reads. This plan replaces the placeholder with something measured on real traffic and adds the loop that produces real precision numbers.

## Part A: measure real traffic (needs the database and `epoch_010.pt`)

1. Run `python -m evaluation.score_checkpoint` twice from `ml/`, once `--source historical --eval-start 2026-04-01 --eval-end 2026-04-16` and once `--source live` over the full live range. Use `--out reports/<source>_epoch_010.json --no-mlflow`.
2. Commit both JSON files under `ml/reports/`. They are small and they are the provenance for the new threshold.
3. Compare the two reports: `flag_rate_at_operating_threshold`, the underway versus stationary split, and `threshold_for_flag_rate`. Write the comparison in `docs/research_notes/threshold-recalibration.md` (numbers and two paragraphs, not an essay).

## Part B: pick the threshold by alert budget

Decision for the owner: how many prediction-error flags per 1,000 reports can be reviewed. The report prints the threshold for 5%, 1%, 0.5% and 0.1%. Pick one, and decide whether live and historical get different thresholds (`docs/ml-pipeline.md` explains why they might need to).

- Set `OPERATING_THRESHOLD` in `ml/models/bilstm/threshold.py` and rewrite its provenance block completely (checkpoint, source, dates, chosen flag rate, report file names).
- If per-source thresholds are chosen, the scorer only ever sees live rows, so use the live threshold and keep the historical one as documentation.
- Update `docs/scoring-and-evaluation.md` status table and `docs/architecture.md` known problem 5.

## Part C: fix the agent rules that break its own stated principles

Each fix gets a test in `agent/tests/`. Fakes only, no database.

1. `incident_history`: split the query. "Same vessel" (matching `mmsi`) and "same pattern elsewhere" (matching `anomaly_type`, other vessels) are different evidence. Return both under separate keys. `form_hypothesis` row 5 (`targeted_spoof`) should depend on the vessel having no prior incidents, not on the type string being new fleet-wide.
2. `track_history`: read only up to `flagged_at`, and add a separate `after` slice if the report wants it. Freeze corroboration should run on the scored 20-report window (or 6 hours back), not 48 hours.
3. Freeze corroboration: require a minimum count of frozen pairs (start with 3) so one bad SOG at rest does not match. Make the number a named constant with an "uncalibrated" comment.
4. Laya vote: "agrees" only when the label matches what the hypothesis implies (`freeze_replay` hypothesis needs a `freeze_replay` label, and so on). Add the mapping as one dict.
5. `benign`: make it reachable. A flag whose only vote is `prediction_error` slightly above threshold, on a stationary window, with no corroboration, should resolve to `benign` or `unresolved` instead of escalating forever. Decide the rule with the owner; write it down in `docs/agent.md`.
6. Write `window_start` and `window_end` on `incidents` (the columns exist and nothing sets them). The scorer already has the window.

## Part D: the review loop

1. Schema (`backend/models/schema.sql`, idempotent `ADD COLUMN IF NOT EXISTS` on `incidents`): `review_verdict TEXT` with a CHECK in (`confirmed_spoof`, `jamming`, `equipment_fault`, `benign`, `unclear`), `reviewed_by TEXT`, `reviewed_at TIMESTAMPTZ`, `review_notes TEXT`. Keep `status = 'resolved'` meaning "has a verdict".
2. `agent/review.py`, a small CLI: `list` (unreviewed, newest first, with hypothesis, confidence, votes, span and a link to the stored report), `show <id>` (evidence plus the 20-report summary text), `verdict <id> <verdict> [--notes ...]`. It sets `status = 'resolved'`, which also lifts the debounce for that vessel.
3. `scoring/review_stats.py`: from reviewed incidents, print precision per hypothesis and per detector-vote combination, with counts and a plain "too few to trust" note under 30 reviewed. Put the output shape in `docs/scoring-and-evaluation.md`.
4. Only real incidents are reviewed. Nothing here generates incidents.

## Done when

- Threshold provenance block cites real report files, and the flag rate is a chosen number, not a leftover.
- Agent tests cover each Part C fix; `docs/agent.md` tier table and "consequences" list match the new rules.
- A reviewer can list, inspect and close an incident from the CLI, and `review_stats` runs (even if it prints "no reviewed incidents").
- `docs/` updated, test counts in `docs/testing.md` updated.

## Not run without the database

Part A needs Postgres and the checkpoint. Part C and the CLI structure can be written and tested with fakes; say which parts were only unit-tested.

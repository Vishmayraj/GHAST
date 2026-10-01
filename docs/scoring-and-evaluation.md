# Scoring and evaluation

Two separate things share the word "scoring" in this repo:

- Offline evaluation, `ml/evaluation/score_checkpoint.py`: scores real, unlabeled windows from the database with a checkpoint and reports rates (error percentiles, detector vote rates, flag rates, the threshold that gives a chosen flag rate). It reports no precision, recall or F1, because real data has no labels.
- Live scoring, `scoring/live_scorer.py`: polls new live reports, scores the latest window per vessel, and hands flagged reports to the agent.

The model and features are in `docs/ml-pipeline.md`. What the agent does with a flag is in `docs/agent.md`.

## Offline evaluation

### What it does

`score_checkpoint.run()`:

1. Loads a checkpoint (`load_checkpoint`: rebuilds `BiLSTMNextDelta(8)`, loads `model_state`). Device is `--device` or CUDA if available, else CPU. Prints `epoch`, `train_loss`, `val_loss` and, if present, the training `source`, `start`, `end`.
2. Streams real windows from `stream_feature_windows(dsn, eval_start, eval_end, source=...)`.
3. By default keeps only windows from validation vessels: `training.dataset_cache.is_validation_vessel(mmsi)`, the same hash split training used (about 20% of vessels). `--include-training-vessels` keeps everything.
4. Scores batches of `--batch-size` windows (default 1024) with `prediction_errors_batch` and keeps flat numpy arrays of the per-report error, reported speed, position-implied speed and speed change for reports 1 to 19 of each window. There are no per-report Python objects, so memory is a few arrays, not gigabytes of dataclasses.
5. Builds a report (`build_report`) and prints it. `--out` also writes it as JSON; MLflow logging is on unless `--no-mlflow`.

Nothing is injected or altered. The model sees exactly the windows the database holds.

### Command

```text
cd ml
python -m evaluation.score_checkpoint \
    --dsn postgresql://ghast:ghast@localhost:5432/ghast \
    --checkpoint checkpoints/epoch_010.pt \
    --source historical --eval-start 2026-04-01 --eval-end 2026-04-16 \
    --out reports/historical_epoch_010.json --no-mlflow

python -m evaluation.score_checkpoint ... --source live --eval-start 2026-09-01 --eval-end 2026-09-30 \
    --out reports/live_epoch_010.json --no-mlflow
```

Flags: `--source {live,historical}` (default `live`), `--eval-start`, `--eval-end` (ISO date or datetime, both required), `--batch-size`, `--device {cpu,cuda}`, `--include-training-vessels`, `--max-windows` (development cap), `--freeze-replay-threshold` (0.5, same as the live scorer), `--speed-jump-threshold` (off unless given, same as the live scorer), `--out`, `--no-mlflow`.

Run it once per source and compare the two JSON reports. That comparison is the first real look at the drift problem: the threshold came from historical data and the live feed has different reporting intervals and populated columns.

Requirements that are not in `--help`:

- Working directory must be `ml/` (flat imports). The checkpoint path is relative to wherever you run it.
- `torch`, `numpy`, `asyncpg` and `psutil` are needed. `mlflow` is imported only when logging is on. The MLflow store defaults to `sqlite:///mlruns/mlflow.db`, experiment `bilstm-real-data-scoring`.
- Historical rows exist only for April 2026 (the importer loads `ais-2026-04-*.csv`). A historical run over another month reads no rows and exits with "no windows found" (exit code 1).

### What the report contains

| Field | Meaning |
|---|---|
| `error_percentiles` | p50, p90, p95, p99, p99.9 of the per-report prediction error, in degrees |
| `detector_vote_rates` | share of reports each detector flags: `prediction_error` at `OPERATING_THRESHOLD`, `freeze_replay` at its threshold, `speed_jump` only if a threshold is given |
| `any_detector_flag_rate`, `two_or_more_detectors_flag_rate` | share of reports with at least one vote, and with at least two (what the container's `--min-votes 2` requires) |
| `flag_rate_at_operating_threshold` | share of reports with prediction error above `OPERATING_THRESHOLD` |
| `by_motion` | that flag rate split into underway windows (any report implying more than 1 knot) and stationary windows |
| `threshold_for_flag_rate` | the prediction-error threshold that flags 5%, 1%, 0.5% and 0.1% of these reports |

The rule detectors (`freeze_replay`, `speed_jump`) are computed with vectorised versions of `evaluation.baselines`; `test_score_checkpoint.py` checks they agree with the baseline functions over a grid of inputs.

### How to read it

- On real traffic these are rates, not accuracy. If real spoofing is rare, the flag rate is an upper bound on the false positive rate: almost everything flagged is a false positive or at least unreviewed. It is not a detection score and it says nothing about recall.
- Picking a threshold by flag rate is an alert budget: "how many flags per thousand reports can an analyst review". It is a policy choice the reports make visible, not a measurement of quality.
- The by-motion split matters because a stationary vessel gives the freeze detector and the model very little to work with, and anchored or moored vessels are a large share of real traffic.
- Detection quality (precision, recall) becomes measurable only from reviewed incidents. That loop is `ImplementationPlans/01_Trust_Pass.md`.

### Same-set caveats that still apply

- Evaluation windows come from the same date range the checkpoint trained on. The independence between training and evaluation is only at the vessel level (validation buckets 800 to 999, never trained on). There is no disjoint time range for the historical import.
- The historical and live reports differ in reporting interval and populated columns (`docs/data-pipeline.md`), so a historical-derived threshold is not automatically right for live.

### The old synthetic evaluation

The previous version injected fake spoofs and reported F1 against them. It is removed (`docs/ml-pipeline.md`, "No synthetic spoofs"). Its numbers are kept here only as history, because they are still quoted in `threshold.py` and the old plans:

| Item | Value | What it measured |
|---|---|---|
| chosen threshold | 0.004946 (max F1), now `OPERATING_THRESHOLD` | recovery of injected spoofs on validation vessels |
| prediction error F1 / speed jump baseline F1 | 0.424 / 0.224 | same |
| per-pattern F1 | freeze_replay 0.244, gradual_drift 0.793, impossible_kinematics 0.161, teleport_jump 0.312 | same |
| control false positive rate | 19.4% of clean reports flagged | measured on the set the threshold was tuned on |

None of these say anything about real spoofing, none could be reproduced from the repo, and the threshold is a placeholder until it is re-derived from the real-traffic report above.

### The public labeled dataset

`python -m evaluation.harness --dataset gps_spoofing_mass --path ... --detector prediction_error --threshold ...` still works. It scores the pre-computed `prediction_error` column of the public IEEE-derived file against its own labels. That checks the harness, not this model.

### Tests

`ml/evaluation/tests/test_score_checkpoint.py` (13 test functions, 57 cases with the parametrized parity grid) uses a small untrained model and hand-built windows: flattening, missing values, underway flag, batched scoring equals single-window scoring, vectorised detectors equal the baseline detectors, flag rate and threshold-for-flag-rate, report consistency. `test_metrics.py`, `test_harness.py` and `test_datasets.py` use the 25-row public dataset fixture. Not tested: `run()`, `main()`, MLflow logging, the streaming path against a database.

## Live scoring

`scoring/live_scorer.py`. Glue over the existing feature, model, detector and agent code; no separate modeling.

### Running it

```text
python scoring/live_scorer.py --dsn $POSTGRES_DSN --checkpoint ml/checkpoints/epoch_010.pt --min-votes 2
python scoring/live_scorer.py ... --once --initial-lookback-minutes 360
```

Flags: `--dsn` (or `POSTGRES_DSN`), `--checkpoint` (or `GHAST_CHECKPOINT`), `--poll-interval-seconds` (30), `--debounce-hours` (6), `--min-votes` (1 by default; the Docker `CMD` passes 2), `--freeze-replay-threshold` (0.5), `--speed-jump-threshold` (unset), `--max-investigations-per-cycle` (25), `--initial-lookback-minutes` (30), `--laya-model` (or `GHAST_LAYA_MODEL`), `--device` (default `cpu`), `--once`. It exits with code 2 if `OPERATING_THRESHOLD` is `None`.

Startup: loads the repo-root `.env` if present, loads the checkpoint on CPU once, creates an `AsyncGroq` client if `GROQ_API_KEY` is set, loads Laya if configured (a load failure is logged and the tool stays a neutral stub), opens an asyncpg pool (1 to 3 connections).

### One cycle (`poll_once`)

1. `since` is the watermark from the previous cycle, or on the first cycle the database clock minus `--initial-lookback-minutes`. The database clock (`SELECT now()`) is used so watermarks match `received_at`.
2. Vessels with any live report newer than `since` are selected (`message_type IS DISTINCT FROM 'historical'`).
3. For each vessel, the latest 20 live reports newer than `now - 6 hours` are fetched. Fewer than 20 counts as `short_history` and the vessel is skipped.
4. The 20 rows become one `FeatureWindow` through `window_rows`, the same code as training. If the newest report is not newer than `max(since, last scored time for the vessel)`, the vessel is skipped.
5. `prediction_errors(model, window)` runs on CPU in a worker thread.
6. `evaluate_window` looks at reports 1 to 19 that are newer than the cutoff. Each report gets a vote set: `prediction_error` if error is above `OPERATING_THRESHOLD`; `freeze_replay` if `freeze_replay_detector` output exceeds the freeze threshold; `speed_jump` only if a speed jump threshold is given. Reports with fewer votes than `--min-votes` are ignored. Of the rest, the strongest by (number of votes, prediction error, index) becomes a `FlaggedAnomaly`, so votes always describe one report.
7. The `FlaggedAnomaly` carries `anomaly_type` as the sorted votes joined with `+` and `anomaly_score` as that report's prediction error, whichever detector voted.
8. Candidates are sorted by (votes, score, time), strongest first. Each is checked for debounce: skipped if this process investigated the vessel within `--debounce-hours` (in-memory cooldown), or if the `incidents` table has any row for the MMSI with `status <> 'resolved'` created within that window. Nothing in the code ever sets `resolved`, so in practice any recent incident, reported or escalated, suppresses new ones.
9. Up to `--max-investigations-per-cycle` candidates go to `investigate()`. The rest are held in an in-memory deferred dictionary and retried next cycle. A restart loses that dictionary.
10. The watermark moves to `cycle_started - 30 s` (an overlap for rows that commit late). The cooldown and last-scored maps are pruned.

`run_forever()` wraps each cycle so a database error is logged and the next interval retries. An exception inside one investigation is caught, counted as `failed`, and does not stop the cycle.

The scorer is not multi-threaded across vessels: it issues one query per active vessel per cycle, sequentially.

### Points that differ from offline evaluation

- Features are computed from the positions that actually arrived. Offline scoring reads the same windows from the database, so the two now see the same kind of input.
- The model runs on exactly 20 reports ending at the newest one. Offline windows come from non-overlapping chunks of a longer range.
- The freeze detector here sees real `implied_speed`, so its live behavior is not what its offline evaluation measured. As written it votes whenever the reported SOG exceeds the position-implied speed by more than 0.5 knots on a report whose implied speed is 0.5 knots or less. How often that fires on real moored or anchored traffic has not been measured.
- The threshold was chosen on historical data with different reporting intervals and a different set of populated columns (`docs/ml-pipeline.md`). Live false positive rate is unmeasured.
- The scorer uses live rows only. The agent's `track_history` tool uses all rows for the MMSI.

### Tests

`scoring/tests/test_live_scorer.py`: 9 tests with fake store, fake scorer and fake `investigate`. High error triggers, low does not, freeze votes on zero error, `--min-votes 2` needs agreement, open incident debounce, no re-investigation on the next poll, cap then retry, short history skipped, tool registration. Not tested: `PostgresStore` queries, `load_model_scorer`, `_serve`, real checkpoint loading, Groq, Laya loading, the watermark and overlap logic across multiple cycles beyond what those tests touch.

## Status

| Piece | State |
|---|---|
| `score_checkpoint.py` | implemented, ran at scale elsewhere; unit tested at the function level, not end to end |
| threshold `OPERATING_THRESHOLD` | placeholder from the removed synthetic evaluation; re-derive by flag rate from real traffic |
| `freeze_replay_detector` evaluation | experimental; only its live vote rate is measurable, on real traffic |
| live-data evaluation | not done |
| `live_scorer.py` | implemented, tested with fakes; a live run producing a persisted incident is recorded in `ghast_latest_report.md` (see `docs/agent.md`) |
| threshold calibration for live | not done |
| per-vessel-class or per-region thresholds | proposed in code comments, not implemented |
| harness CLI | works for `gps_spoofing_mass`; the broken `injected_synthetic` option was removed with the injector |

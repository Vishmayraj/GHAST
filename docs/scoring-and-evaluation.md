# Scoring and evaluation

Two separate things share the word "scoring" in this repo:

- Offline evaluation, `ml/evaluation/score_checkpoint.py`: injects synthetic spoofs into held-out real windows, scores them with a checkpoint, sweeps thresholds, and reports precision, recall and F1. This is where `OPERATING_THRESHOLD` came from.
- Live scoring, `scoring/live_scorer.py`: polls new live reports, scores the latest window per vessel, and hands flagged reports to the agent.

The model and features are in `docs/ml-pipeline.md`. What the agent does with a flag is in `docs/agent.md`.

## Offline evaluation

### What it does

`score_checkpoint.run()`:

1. Loads a checkpoint (`load_checkpoint`: rebuilds `BiLSTMNextDelta(8)`, loads `model_state`). Device is `--device` or CUDA if available, else CPU. Prints `epoch`, `train_loss`, `val_loss` and, if present, the training `source`, `start`, `end`.
2. Streams windows from `stream_feature_windows(dsn, eval_start, eval_end, source=...)`.
3. Keeps only windows from validation vessels: `training.dataset_cache.is_validation_vessel(mmsi)`, the same hash split training used (about 20% of vessels). Everything else is skipped.
4. Collects held-out windows into batches of `--batch-size` (default 1024). For each batch: `build_synthetic_dataset(batch, seed=seed + batches_scored)`, then `score_injected_windows`, which runs `prediction_errors_batch` once and flattens every report of every window into an `AISObservation`.
5. After the stream ends, sweeps thresholds for three detectors, picks the best-F1 point for each, prints a per-pattern breakdown, prints a verdict against the speed-jump baseline, and logs to MLflow unless `--no-mlflow`.
6. Prints a reminder to copy the chosen threshold into `ml/models/bilstm/threshold.py`. It does not write the file.

### Command

```text
cd ml
python -m evaluation.score_checkpoint \
    --dsn postgresql://ghast:ghast@localhost:5432/ghast \
    --checkpoint checkpoints/epoch_010.pt \
    --source historical \
    --eval-start 2026-04-01 --eval-end 2026-04-16 \
    --seed 0 --no-mlflow
```

Flags: `--source {live,historical}` (default `live`), `--eval-start`, `--eval-end` (ISO date or datetime, both required), `--seed` (default 0), `--batch-size` (default 1024), `--device {cpu,cuda}`, `--no-mlflow`.

Requirements that are not in `--help`:

- Working directory must be `ml/` (flat imports). The checkpoint path is relative to wherever you run it, so `checkpoints/epoch_010.pt` means `ml/checkpoints/epoch_010.pt`.
- `torch`, `numpy`, `asyncpg` and `psutil` are needed (`psutil` is imported inside `run()`). `mlflow` is imported only when logging is on.
- The MLflow store defaults to `sqlite:///mlruns/mlflow.db` relative to the working directory. The code creates `mlruns/` only when the default URI is in use.
- The example command in `docs/DEVELOPER_GUIDE.md` and the module docstring use `--eval-start 2026-05-01 --eval-end 2026-05-16`. The importer only loads `ais-2026-04-*.csv`, so a historical run over May reads no rows and exits with "no clean windows found" (exit code 1). Use an April range, or a live range if live data exists.

### Which data it evaluates on

Evaluation windows come from the same date range the checkpoint trained on. The independence between training and evaluation is only at the vessel level: evaluation uses the validation vessels (hash buckets 800 to 999) that `train_from_shards` used for `val_loss` and never trained on. There is no disjoint time range, because the historical import covers one two-week span. The recorded holdout method string is `validation_vessel_split (fraction=0.2)`.

What follows from that:

- The same vessels' traffic patterns, weather and season are on both sides of the split. Vessel-level separation stops memorization of specific tracks but not a shared distribution.
- The threshold was chosen as the best-F1 point on this same evaluation set, and the F1 reported for it is measured on that set. There is no second set that the threshold was not tuned on.
- Evaluation has only run on historical data (per the repo docs). No live-data evaluation is recorded.

### What counts as a prediction and a label

Both are per report, not per window.

- Label: `is_spoofed` for a report, from the injector (see the table in `docs/ml-pipeline.md`). A window with no injection (25% of windows) has no positives.
- Prediction: `detector(observation) > threshold`, strict inequality.
- Detectors (`ml/evaluation/baselines.py`), each `AISObservation -> float`:

| Detector | Score | Uses |
|---|---|---|
| `prediction_error_detector` | the BiLSTM error for this report (degrees) | `prediction_error` |
| `speed_jump_detector` | absolute change in reported SOG since the previous report | `acceleration` (derived in the scorer) |
| `freeze_replay_detector` | `max(0, sog - implied_speed)` when `0 <= implied_speed <= 0.5` knots, else 0 | `sog`, `implied_speed` (the feature column) |

Metrics (`metrics.py`): confusion matrix over all reports, precision, recall, F1, accuracy. Ratios with a zero denominator are 0.0.

Threshold candidates (`threshold_candidates`): 25 values, the 1st to 99.5th percentiles (evenly spaced) of the positive scores of that detector over all observations. `best_by_f1` picks the max-F1 candidate.

Per-pattern breakdown (`per_pattern_breakdown`): observations are grouped by the injected pattern (or `control`), and each group is scored at the chosen threshold. Each pattern group contains all the reports of the windows that received that pattern, including the reports that were not altered. So a pattern's precision is affected by false positives on the clean reports next to the spoofed ones, and the `control` group has no positives, so its precision, recall and F1 print as 0 and only its false positive count is informative.

### Recorded results

These numbers are quoted from `ml/models/bilstm/threshold.py`, `docs/DEVELOPER_GUIDE.md` and `ImplementationPlans/`. The run that produced them was executed on another machine. No MLflow database, run ID, or result file is in the repository, so none of them can be reproduced or checked from this checkout. `threshold.py` says as much and notes that precision and recall at the chosen threshold were not recorded.

| Item | Value |
|---|---|
| checkpoint | `epoch_010.pt`, epoch 10, train loss 0.0006055, val loss 0.0007673 |
| scale | about 6.4 million scored observations, validation vessels only |
| chosen threshold | 0.004946 (max F1), promoted to `OPERATING_THRESHOLD` |
| prediction error F1 | 0.424 |
| speed jump baseline F1 | 0.224 |
| per-pattern F1 for prediction error | freeze_replay 0.244, gradual_drift 0.793, impossible_kinematics 0.161, teleport_jump 0.312 |
| control false positive rate | 19.4% (a figure carried through the plans and code comments) |
| earlier, superseded run | F1 0.396 vs 0.214, threshold 0.005451, 1,000 windows, overlapping data |

The per-pattern and control numbers say the detector is much better at gradual drift than at anything else, and that about one in five clean reports is flagged at this threshold. `docs/DEVELOPER_GUIDE.md` already states that this is not an autonomous spoofing verdict.

`freeze_replay_detector` is swept in every run but no recorded result for it is in the repo.

### Problems in the evaluation itself

Found by reading the code. None were fixed.

1. The freeze detector cannot see injected freezes. `freeze_replay_detector` reads `implied_speed` from the feature column, and the injectors do not recompute that column (`docs/ml-pipeline.md`). On injected windows it holds the value from the clean track. The detector's evaluated F1 on the injected `freeze_replay` class therefore says little about whether it detects the injected behavior. The comment in `laya_pattern_classifier.md` about a possible mismatch is consistent with this, but the cause is the stale feature column and not only the pattern shape.
2. Teleport precision is capped by construction. A teleported report produces a large error at that report and at the next one (the step back). Only the first is labeled, so if both are flagged, half the flags on that pattern are counted as false positives.
3. Feature/position inconsistency (same root as item 1): the model is scored on windows whose motion features describe the clean track and whose positions describe the altered one. In live use these are consistent, and the model can also see the implied speed of the following report through its bidirectional layer (`docs/ml-pipeline.md`). The offline number is a measurement of a somewhat different task from live scoring; how different has not been measured.
4. Reproducibility: injection seeds are `seed + batch_index`, and the generator is consumed window by window, so results depend on `--batch-size` and on the order windows stream in. `--seed` alone does not fix the output if the batch size changes.
5. The threshold is tuned on the set it is reported on (above).
6. `evaluation.harness` has a CLI whose `--dataset` choices include `injected_synthetic`, but the loader for it is `async` and takes different arguments, so `python -m evaluation.harness --dataset injected_synthetic ...` does not work. The module docstring of `score_checkpoint.py` notes this and that the script deliberately bypasses the harness CLI. `--dataset gps_spoofing_mass` works, and its result is the pre-computed `prediction_error` column of that public file.

### Why an evaluation can look like it hangs

`score_checkpoint.py` is a single-process script with long quiet stretches. Nothing here is a deadlock; these are the places it goes silent, roughly in order of when you meet them:

1. Before the first row. The query orders by `(mmsi, received_at)` over the whole range, and the index is `(mmsi, received_at DESC)`. The planner probably has to sort the range first. The first `loading:` line is only printed after 50,000 rows. Reasoning only, not confirmed with `EXPLAIN`.
2. During streaming and scoring it prints a line at the first batch and then only every 10 batches (10,240 held-out windows at the default batch size).
3. Memory. `observations` keeps every `AISObservation` (a frozen dataclass with about 17 fields plus two strings) in a Python list until the end. Six million of them is likely several gigabytes; on a small machine this turns into swapping. Estimated from the object shape, not measured.
4. After `scored N observations ...` the script does the threshold work in pure Python with no output: `threshold_candidates` calls each detector over every observation (3 passes), then each of the 3 sweeps evaluates 25 thresholds, and each `evaluate` call builds two lists over every observation, calling the detector again. That is about 5 x 10^8 detector calls at 6.4 million observations, single-threaded. It is likely to take a long time and prints nothing until the first sweep finishes. Not timed here.
5. Empty ranges are not a hang but look like one: a wrong `--source` or a range with no rows streams nothing and ends with `no clean windows found ...`.
6. GPU: the model is small and the forward pass is one call per batch. On a machine with CUDA the GPU is mostly idle because the injection, flattening and sweeps are on the CPU. `torch.cuda.utilization` needs `pynvml`; if it is missing the profile line prints `gpu_utilization=unavailable`.

### Tests

`ml/evaluation/tests/test_score_checkpoint.py` (11 tests) uses a small untrained model and fixtures: batched scoring matches single-window scoring, first report has zero error, pattern carried through, threshold candidates, sweep, per-pattern breakdown, both baselines run on scored observations. `test_metrics.py`, `test_harness.py` and `test_datasets.py` use the 25-row public dataset fixture. Not tested: `run()`, `main()`, MLflow logging, the streaming path against a database.

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

- Features are computed from the positions that actually arrived, so they are consistent with them (the offline injected windows are not).
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
| recorded F1 and threshold | reported in docs and code comments; not reproducible from the repo |
| `freeze_replay_detector` evaluation | experimental; the injected data does not exercise it (problem 1) |
| live-data evaluation | not done |
| `live_scorer.py` | implemented, tested with fakes; a live run producing a persisted incident is recorded in `ghast_latest_report.md` (see `docs/agent.md`) |
| threshold calibration for live | not done |
| per-vessel-class or per-region thresholds | proposed in code comments, not implemented |
| harness CLI for `injected_synthetic` | currently broken |

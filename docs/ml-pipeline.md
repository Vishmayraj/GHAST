# ML pipeline

The BiLSTM detector as it exists in `ml/`: how a window of AIS reports becomes model input, what the model predicts, how it is trained, and what a checkpoint contains. Scoring and threshold selection are in `docs/scoring-and-evaluation.md`. Laya is in `docs/laya_pattern_classifier.md`.

All commands run with `ml/` as the working directory, because the package uses flat imports (`from features.extract import ...`).

## Source data, derived data, model artifacts

These are three different things and the repo uses the words loosely in places. This is the split used in every doc here.

| Kind | What | Where | In git? |
|---|---|---|---|
| Source data | AIS position reports, live and historical | `vessel_position`, `vessel_static` in TimescaleDB | no (database volume) |
| Source data | MarineCadastre CSVs the historical rows came from | `data/raw/` | no (gitignored) |
| Source data | public labeled dataset for the harness smoke test | `data/research_datasets/gps_spoofing_mass/` via `fetch.sh` | no (gitignored) |
| Derived data | training shard cache, `.npz` files of windows and delta targets | a `tempfile.mkdtemp` directory, or `--cache-dir` | no, and never deleted by the code |
| Derived data | Laya review queue and, once a person has labeled it, `train.jsonl`, `holdout.jsonl`, `manifest.json` | `data/laya/` | no (none exist yet) |
| Model artifact | BiLSTM checkpoints `epoch_NNN.pt`, `latest.pt` | `ml/checkpoints/` | no (`*.pt` gitignored) |
| Model artifact | Laya fine-tuned model directory | `ml/laya_model/` | partly: config, tokenizer and a benchmark report are committed, `model.safetensors` is ignored |
| Model artifact | MLflow runs from `score_checkpoint.py` | `ml/mlruns/` | no |
| Derived parameter | `OPERATING_THRESHOLD = 0.004946` | `ml/models/bilstm/threshold.py` | yes, a constant in code |

`data/laya/*.jsonl`, when it exists, is a sample of real window summaries with human labels. It is not "the GHAST dataset", and it is not what the BiLSTM trained on. There is no synthetic data anywhere in the pipeline: the injector, the injected evaluation set and the injected Laya snapshot were removed (see "No synthetic spoofs" below).

## Features

`ml/features/extract.py::extract_features(rows, ship_type)` turns one vessel's time-ordered rows into an `(n, 8)` float32 array. Pure function, no I/O.

| Index | Name | Value | Missing |
|---|---|---|---|
| 0 | sog | `sog_knots` | -1 |
| 1 | cog | `cog_deg` | -1 |
| 2 | heading | `true_heading_deg` | -1, and mask (6) is 1 |
| 3 | rate of turn | `rate_of_turn` | -1, and mask (7) is 1 |
| 4 | vessel class | `ship_type` from `vessel_static` | -1 |
| 5 | implied speed | haversine distance to the previous report, divided by elapsed time, in knots | -1 for the first row or a non-positive time step |
| 6 | heading missing mask | 0 or 1 | |
| 7 | rate of turn missing mask | 0 or 1 | |

Notes on what this does and does not do:

- No normalization or scaling. Speeds are in knots, course and heading in degrees (up to 360 and the AIS value 511), rate of turn as the raw integer, class as an id.
- AIS "not available" values (SOG 102.3, COG 360, heading 511) are not translated to missing. Only Python `None` is.
- SOG and COG have no missing mask, so a genuine -1 is indistinguishable from a null.
- Latitude and longitude are not features. They are carried on the window as `positions` and used only for targets.
- There is no time-step feature. `implied_speed` depends on the elapsed time, but the elapsed time itself is not an input.
- `FREEZE_DISPLACEMENT_EPSILON_KNOTS = 0.5` also lives in this module; it is the shared threshold for "the position implies no motion", used by the freeze detector, the agent's corroboration and the Laya summary.

## Windows

`ml/features/pipeline.py`. A `FeatureWindow` holds `mmsi`, `window_start`, `window_end`, `features (20, 8)`, `positions (20, 2)` as float64 lat/lon, and `timestamps`.

- `WINDOW_LENGTH = MINIMUM_REPORTS_PER_VESSEL = 20`.
- Rows for one vessel are ordered by `received_at` and cut into non-overlapping chunks of 20. A vessel with fewer than 20 reports in the queried range yields nothing. Leftover reports at the end of a range are dropped.
- There is no constraint on the time span of a window. Twenty reports from a moored vessel can span many hours. (The Laya export contains a sample with a 1,013 minute span.) The live scorer, by contrast, refuses to use a window whose oldest report is more than 6 hours old.
- `ship_type` for the whole window is taken from the first row's join to `vessel_static`.
- Reading is done by `stream_feature_windows`, which opens one asyncpg connection, runs `POSITION_QUERY` (live) or `HISTORICAL_POSITION_QUERY` (historical) through a server-side cursor with `prefetch=2000`, and yields windows as each vessel's contiguous run of rows ends. `ORDER BY mmsi, received_at` guarantees the contiguity. Memory is bounded by one vessel's rows plus the prefetch buffer.
- Development caps `max_vessels`, `max_windows`, `max_rows` truncate the stream. Leave unset for real runs.
- `fetch_training_windows` and `load_training_windows` return a full list by draining the same generator. `score_checkpoint.py` and `laya_export.py` do not use it; they stream.
- `estimate_row_count` runs a `count(*)` under a 15 second `statement_timeout` for progress display and returns `None` on timeout.

The `vessel_position_mmsi_time_idx` index is `(mmsi, received_at DESC)`, while the query orders by `mmsi ASC, received_at ASC`. That mixed ordering cannot be produced by scanning that index in either direction, so the planner will most likely sort the selected rows before returning the first one. On a 31 million row range that would be a long silent start. This is reasoning from the definitions; it has not been checked with `EXPLAIN` on a live database.

## Model

`ml/models/bilstm/model.py::BiLSTMNextDelta(feature_size=8, hidden_size=64)`:

```text
vessel class id -> clamp(0, 100) -> Embedding(101, 8)
concat(features (8), class embedding (8)) -> LSTM(16 -> 64, bidirectional, 1 layer, batch_first)
LSTM output (128 per step) -> Linear(128 -> 2)
output: one 2-vector per timestep
```

By arithmetic from the definition that is 43,050 parameters. The class id is read from feature column 4 and cast to a long; -1 (missing) is clamped to 0, so "missing" and AIS ship type 0 share an embedding, and any type above 100 shares the last one. The class id also stays in the feature vector as a raw number, alongside its embedding.

Target and prediction: the output at step `t` is meant to be the latitude/longitude delta from report `t` to report `t+1`, in degrees. Training compares `output[:, :-1]` against `np.diff(positions, axis=0)` (19 deltas per 20 report window) with `MSELoss`.

Two properties of this setup that are visible in the code and that nobody has ablated:

- The LSTM is bidirectional over the whole window. The output at step `t` can use feature rows `t+1` through 19, and row `t+1` includes `implied_speed`, which is computed from the same displacement the model is being asked to predict. So the task is closer to "reconstruct the step given its neighbours" than "forecast the next step". Whether this changes detection quality has not been measured.
- The target is a raw degree delta per report step, but elapsed time between reports is not an input and the delta is not scaled by it. What counts as a normal-sized error therefore depends on how often a vessel reports. The two data sources have very different reporting patterns (MarineCadastre versus a live receiver feed), and the operating threshold was chosen on historical data only. Effect on live scoring: unmeasured.

Other consequences of the historical-only training data: `rate_of_turn` is NULL in every imported row, so the checkpoint has only ever seen features 3 and 7 as constant (`-1` and `1`). Live rows carry real rate of turn values and a mask of 0, which is input the model never trained on. The class id feature is populated for historical vessels only if their MMSI also appeared in a live `ShipStaticData` message (the importer does not fill `vessel_static`); otherwise it is -1.

Inference (`models/bilstm/infer.py`): `prediction_errors(model, window)` and the batched `prediction_errors_batch(model, windows)` run one forward pass and return an array of length 20 per window. Entry 0 is 0.0. Entry `t >= 1` is the Euclidean norm, in degrees, of `predicted[t-1] - (position[t] - position[t-1])`. Latitude and longitude degrees are treated as equal units. The batched version requires equally sized windows and moves tensors to the model's device.

## Training

`ml/training/train.py`, entry point `python -m training.train`.

Flow:

1. Choose the range. `--source live` calls `select_live_window`, which resolves dates from the observed live coverage (`window_policy.py`): `--live-window initial` needs the first live report plus 14 days to have been reached and 15 days to have elapsed since the first report, and trains on exactly that 14 day range; `rolling` uses `latest_live_report - 14 days` to latest. `--source historical` requires explicit `--start` and `--end`.
2. `estimate_row_count` for progress, then `materialize_to_shards` streams windows once, sends each vessel to `train` or `validation` by `is_validation_vessel` (SHA-256 of the MMSI, bucket of 1000, buckets 800 and above are validation, so about 20% of vessels), and writes `.npz` shards of 2,048 windows each (`features`, `classes`, `targets`). Shards are uncompressed.
3. `train_from_shards` trains with Adam (`--learning-rate`, default 1e-3), batch size 256, 10 epochs by default. Each epoch shuffles shard order and rows within a shard, holding one shard in memory at a time. There is no LR schedule, gradient clipping, weight decay, early stopping or feature scaling.
4. After every epoch it writes `checkpoints/epoch_NNN.pt` and overwrites `checkpoints/latest.pt`.

Commands as they exist:

```text
cd ml
python -m training.train --dsn postgresql://ghast:ghast@localhost:5432/ghast --source live --live-window initial --epochs 10

python -m training.train --dsn ... --source historical \
    --start 2026-04-01T00:00:00+00:00 --end 2026-04-16T00:00:00+00:00 --epochs 10 \
    --batch-size 256 --max-vessels 500 --checkpoint-dir checkpoints/dev-run
```

Other flags: `--cache-dir`, `--max-windows`, `--max-rows`, `--device {cpu,cuda}`, `--resume-from`. With `--resume-from`, `--epochs` means "this many more epochs", so resuming from epoch 10 with `--epochs 10` runs epochs 11 to 20.

Things that are easy to miss:

- Training uses clean windows from the database as they are. Nothing filters out real anomalies or bad fixes, so any real spoofing or GPS error in the source data is training data.
- Training does not use MLflow, despite `ml/README.md` and `ml/training/README.md` describing MLflow configs. Only `score_checkpoint.py` logs to MLflow.
- The shard cache directory is a temp directory by default and is printed at the start of the run. It is not cleaned up.
- Only real windows are used for training. There is no synthetic data in the repo.
- `split_by_vessel` and `train_model` in `train.py` are the old in-memory path. `train_model` is used by one test. `split_by_vessel` is not called anywhere and uses a different rule (first 80% of sorted MMSIs) from the hash split that training and evaluation share; do not use it.

### Checkpoint contents

`torch.save` of a dict with `epoch`, `model_state`, `optimiser_state`, `train_loss`, `val_loss`, and, for checkpoints written after commit `9342210`, `source`, `start`, `end` (ISO strings). Loaders rebuild `BiLSTMNextDelta(N_FEATURES)` and call `load_state_dict(checkpoint["model_state"])`. Nothing else about the run (hyperparameters, batch size, commit, feature version) is recorded.

### The current checkpoint

`ml/checkpoints/epoch_010.pt` is the checkpoint everything else refers to: epoch 10, train loss 0.0006055, validation loss 0.0007673 (recorded in `threshold.py` and `docs/DEVELOPER_GUIDE.md`). The file is not in the repository. The repo's docs say it was trained with `--source historical` over `2026-04-01..2026-04-16`; the checkpoint itself predates the provenance fields, so that cannot be confirmed from the file. No live-trained checkpoint is documented.

## No synthetic spoofs

Earlier versions injected fake spoofs (teleport, drift, freeze/replay, impossible kinematics) into clean real windows to get labeled data for evaluation and for Laya. That code (`ml/features/inject.py`), the injected evaluation loader, and the committed injected Laya snapshot (`data/laya/*.jsonl`) have been removed. They are recoverable from git history (the snapshot is in commit `c835f59`).

Why it went: the labels described the injector, not real spoofing. Every number built on them (F1 0.424, the 19.4% control flag rate, Laya's 0.83 holdout accuracy) measured how well a model recovered the injector's own patterns. The injectors also left motion features stale next to altered positions, so offline scoring saw a different input than live scoring does.

What replaced it:

- Evaluation runs on real, unlabeled windows and reports rates: prediction-error percentiles, how often each detector votes, how often two agree, flag rate at the operating threshold, and the threshold that gives a chosen flag rate (`docs/scoring-and-evaluation.md`). Real data has no labels, so this is not precision or recall.
- Laya training data comes from a review queue of real windows plus labels written by a person (`docs/laya_pattern_classifier.md`).
- The one labeled dataset that exists is the public `gps_spoofing_mass` file, scored through `evaluation.harness`.

Hand-built altered tracks still appear in unit tests (`ml/features/tests/test_summary.py`) as fixtures for the summary function. They are not a data source.

## Tests

`ml/features/tests` (extract, streaming pipeline with a fake connection, summary), `ml/training/tests` (vessel split, shard writer and batch iterator, live window policy), `ml/models/bilstm/tests` (one test: the in-memory `train_model` loop reduces loss on one repeated hand-built window). Not covered: `train_from_shards`, `_run_epoch`, `run_training`, `select_live_window`, `estimate_row_count`, `materialize_to_shards` end to end, `infer.prediction_errors` beyond its use in `score_checkpoint` tests, and the CLI. See `docs/testing.md`.

## Status

| Piece | State |
|---|---|
| feature extraction, windowing, streaming reads | implemented, tested against fakes |
| shard-cached mini-batch training | implemented, used for `epoch_010.pt`; the epoch loop itself is untested |
| `epoch_010.pt` | trained and evaluated (see scoring doc); file not in repo |
| live-source training (`--source live`) | implemented, window policy tested, no documented run |
| training provenance in checkpoints | implemented for new checkpoints only |
| MLflow in training | not implemented |
| CUDA | optional, used when present |

Known limitations, from the code:

- Bidirectional context and the implied speed feature (above), not ablated.
- No time-step input; error scale depends on reporting interval.
- Historical-trained model has never seen rate of turn or the mask varying.
- No feature scaling; sentinel values (511, 360, 102.3) are treated as real numbers.
- Windows have no maximum time span in training.

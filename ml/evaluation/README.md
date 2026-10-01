# ml/evaluation/

Evaluation code. Two different jobs, because labeled and unlabeled data need different tools:

- Labeled data (the public `gps_spoofing_mass` file): precision/recall/F1 through `harness.py`.
- Real, unlabeled data from the database: rates through `score_checkpoint.py` (flag rates, detector vote rates, error percentiles, the threshold for a chosen flag rate). No F1, because real windows have no labels. There is no synthetic spoof injection anywhere.

- `datasets.py` — `AISObservation` (the shared record shape) and dataset loaders. One loader: `load_gps_spoofing_mass`, for the dataset in `data/research_datasets/gps_spoofing_mass/` (see that directory's README for what it is and how to fetch it).
- `metrics.py` — precision/recall/F1/accuracy from a confusion matrix. Stdlib only.
- `baselines.py` — three detectors: `prediction_error_detector`, `speed_jump_detector` and `freeze_replay_detector`. `score_checkpoint.py` reports the vote rate of each (vectorised versions, tested against these); the live scorer uses the first and third (and the second if given a threshold).
- `harness.py` — `evaluate()` runs a detector against a labeled dataset and returns the metrics; also a CLI.
- `score_checkpoint.py` — scores real windows with a checkpoint and prints a rates report (`docs/scoring-and-evaluation.md`).
- `laya_export.py` — review queue and human-labeled Laya training files from real windows (`docs/laya_pattern_classifier.md`).

## Try it

```
cd ml
python -m evaluation.harness --dataset gps_spoofing_mass \
    --path ../data/research_datasets/gps_spoofing_mass/gps_spoofing_data.csv \
    --detector prediction_error --threshold 0.00001
```

Against the real dataset this prints precision = recall = f1 = 1.000 - `prediction_error` happens to separate Normal/Spoofed almost perfectly in this dataset (it's near-zero for every genuine point and a small positive value for spoofed ones), which is exactly why it's useful as a first harness smoke test: it confirms the scoring pipeline is correct using a signal we already know is strong, before ml/models/bilstm/ has to prove itself on a harder one.

## Tests

```
cd ml
python -m pytest evaluation/tests/ -v
```

36 tests in this directory (the metrics, harness and dataset tests run against a 25-row fixture sampled from the public dataset (`evaluation/tests/fixtures/`); the rest use hand-built windows and a small untrained model) - no network, database or full dataset download needed to run them.

# ml/evaluation/

The evaluation harness: precision/recall/F1 against labeled AIS spoofing data, so every later model iteration (starting with ml/models/bilstm/, Weeks 7-10) has a number attached. Built first, deliberately, per Weeks 5-6 of `ImplementationPlans/Sem5IP.md`.

- `datasets.py` — `AISObservation` (the shared labeled-record shape) and dataset loaders. Currently one: `load_gps_spoofing_mass`, for the dataset in `data/research_datasets/gps_spoofing_mass/` (see that directory's README for what it is and how to fetch it).
- `metrics.py` — precision/recall/F1/accuracy from a confusion matrix. Stdlib only.
- `baselines.py` — two trivial detectors (`prediction_error_detector`, `speed_jump_detector`) used to prove the harness itself is correct before any real model exists. Not meant to be competitive.
- `harness.py` — `evaluate()` runs a detector against a dataset and returns the metrics; also a CLI.

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

13 tests, all against a 25-row fixture sampled from the real dataset (`evaluation/tests/fixtures/`) - no network or full dataset download needed to run them.

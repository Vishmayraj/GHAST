# Tests

Map of the test suite as it exists. Nothing was executed while writing this document. The counts below come from counting `def test_` definitions (`parametrize` appears only in `ml/evaluation` and `backend/tests`), so they are static counts, not pass counts. A `pytest` run may report a different number if collection changes them, and nothing here says the suite is green.

## Counts and layout

| Package | Test files | Tests | Run from |
|---|---|---|---|
| `ingestion/tests` | `test_normalize.py` | 8 | `ingestion/` |
| `ml/features/tests` | `test_extract`, `test_pipeline_streaming`, `test_quality`, `test_score_histogram`, `test_summary` | 45 | `ml/` |
| `ml/training/tests` | `test_dataset_cache`, `test_window_policy` | 7 | `ml/` |
| `ml/models/bilstm/tests` | `test_model.py` | 1 | `ml/` |
| `ml/evaluation/tests` | `test_datasets`, `test_harness`, `test_laya_export`, `test_metrics`, `test_score_checkpoint` | 37 | `ml/` |
| `agent/tests` | `test_state_machine`, `test_pattern_classifier`, `test_freeze_corroboration`, `test_report`, `test_jamming_zones`, `test_track_history`, `test_incident_history`, `test_review`, `test_on_demand_report`, `test_agent_runtime`, `test_threshold_agent`, `test_incident_agents`, `test_incident_pipeline` | 146 | `agent/` |
| `scoring/tests` | `test_live_scorer.py`, `test_review_stats.py`, `test_thresholds_cli.py` | 37 | `scoring/` |
| `backend/tests` | `test_api.py` | 22 (29 cases, one test is parametrized over 8 routes) | `backend/` |
| `scripts/tests` | `test_load_jamming_zones.py` | 15 (25 cases, a few are parametrized) | `scripts/` |
| total | 32 files | 318 | |

The ML total is 90. `test_score_checkpoint` has a parametrized parity grid, so pytest reports 58 cases for its 14 functions. The newest of those, `test_quality_gate_leaves_out_unscorable_reports_and_counts_them`, needs torch and was written without being run. There are no tests for `frontend/`, `infra/`, or `scripts/import_marinecadastre.py`. The frontend modules were exercised once under jsdom against the real API app with a fake pool; that check is not in the repo.

Each package has a `pytest.ini` with a `pythonpath` line so that bare `pytest` resolves the flat imports (`ml/pytest.ini`: `.`; `agent/pytest.ini`: `. ../ml`; `scoring/pytest.ini`: `. ../ml ../agent`). `ingestion/` has no `pytest.ini`; its tests import `normalizer.normalize` and work when run from `ingestion/`.

```text
cd ingestion && pytest tests -v
cd ml        && pytest features/tests training/tests models/bilstm/tests evaluation/tests -v
cd agent     && pytest tests -v
cd scoring   && pytest tests -v
cd backend   && pytest tests -v
cd scripts   && pytest tests -v
```

## What needs what

None of the tests touch PostgreSQL, MinIO, the network, a real checkpoint, Groq, or Laya. Database access is replaced by fake connections and fake stores, the BiLSTM is a small untrained model, and the Laya predictor is an injected function.

| Suite | Third-party packages actually needed |
|---|---|
| `ingestion/tests` | `pytest` only (the normalizer imports nothing outside the stdlib). `requirements-dev.txt` also installs `websockets`, `asyncpg`, `minio`, which the tests do not import. |
| `ml/*` | `torch`, `numpy`, `asyncpg` (imported by `features.pipeline`), `pytest`. `requirements-dev.txt` also brings `mlflow`, `scikit-learn`, `pandas`, `psycopg`, `tqdm`, `psutil`; only `psutil` is imported by code under test (inside `score_checkpoint.run`, which is not tested). |
| `agent/tests` | `numpy`, `asyncpg`, `pytest`, `pytest-asyncio`. No torch: `state_machine` imports only `models.bilstm.threshold`, which is a constant. |
| `scoring/tests` | same as agent. `live_scorer` imports torch only inside `load_model_scorer`, which is not called. |
| `scripts/tests` | `pytest`, `pytest-asyncio`. The loader imports `asyncpg` only inside `_run`, which the tests replace. |
| `backend/tests` | `fastapi`, `httpx`, `pytest`, `pydantic` (all in `backend/requirements-dev.txt`). `asyncpg` is imported only when the app builds its own pool, which the tests never do. |

`ml/requirements.txt` says `torch>=2.2` with no index URL, so on Linux CI it pulls the default CUDA build. That works but is a large download for every ML workflow.

## CI

`.github/workflows/` has nine workflows, each installing the matching `requirements-dev.txt` on Python 3.12 and running one test directory:

| Workflow | Runs | Triggers on changes to |
|---|---|---|
| `ingestion-tests` | `ingestion/tests` | `ingestion/**` |
| `ml-features-tests` | `ml/features/tests` | `ml/features/**`, `ml/requirements*.txt` |
| `ml-training-tests` | `ml/training/tests` | `ml/training/**`, `ml/features/**` |
| `ml-model-tests` | `ml/models/bilstm/tests` | `ml/models/bilstm/**`, `ml/training/**`, `ml/features/**` |
| `ml-evaluation-tests` | `ml/evaluation/tests` | `ml/evaluation/**`, `ml/features/**`, `ml/models/bilstm/**` |
| `agent-tests` | `agent/tests` | `agent/**`, `ml/requirements*.txt` |
| `scripts-tests` | `scripts/tests` | `scripts/**`, `backend/models/schema.sql` |
| `backend-tests` | `backend/tests` | `backend/**`, `scoring/review_stats.py` |
| `scoring-tests` | `scoring/tests` | `scoring/**`, `agent/**`, `ml/features/**`, `ml/evaluation/**`, `ml/models/bilstm/threshold.py` |

Gaps in the path filters, from the lists themselves: a change to `ml/features/**` does not run `agent-tests`, although `agent` imports `features.extract` and `features.summary`. A change to `ml/training/**` does not run `ml-evaluation-tests`, although `score_checkpoint` imports `training.dataset_cache`. `ml-model-tests` uses `ml/training/**`, so it covers that import for the model test only.

## What is covered

Ingestion: `normalize_envelope` and `parse_time_utc` (nanosecond truncation, missing fractions, bad format, both metadata casings, unsupported types, missing MMSI).

ML features: implied speed with irregular time steps, mask columns, window sizing and minimum length; streaming windows flushed at vessel boundaries, `max_vessels`, `max_windows`, `max_rows`, progress callback, against a fake connection; the summary text (determinism, teleport, freeze, replay, kinematics, no dependence on a cached implied-speed column, short tracks), using hand-built altered tracks as fixtures.

ML training: vessel hash split (determinism, roughly 20%), shard writer and shard batch iterator shapes, live window policy (14 day and 15 day gates, rolling window).

BiLSTM: `models/bilstm/tests/test_model.py` runs the small in-memory training loop for 10 epochs on one repeated hand-built window and asserts the loss goes down. This is the only test that trains anything.

Evaluation: precision, recall, F1 and confusion counts; the harness on a 25-row fixture from the public dataset; the Laya exporter's core logic on hand-built real-shaped windows (queue sampling, vessel split, label validation, the eight-column row schema, unlabeled rows ignored, no vessel in both splits); `score_checkpoint` helpers (flattening real windows, missing values, batched scoring equals single-window scoring, vectorised rule detectors equal the baseline detectors, flag rate and threshold-for-flag-rate, report consistency).

Agent: `form_hypothesis` tiers, the exact boundary around `OPERATING_THRESHOLD`, single-detector cap, detector corroboration in evidence, freeze tiering and its backward compatibility, jamming priority, `investigate` audit trail and persistence, the Laya tool (stub, prediction passthrough, shared summary text, too little history, exceptions) and the vote rules, report input compaction and error handling, track history and jamming zone queries via fake connections.

Agent additions in the trust pass: the freeze minimum (3 frozen pairs) and 20-report window, `track_history` stopping at the flag with an opt-in `after` slice, the two `incident_history` queries and `targeted_spoof` depending only on `same_vessel`, Laya agreeing only with the hypothesis's own label, the weak-isolated-flag `benign` rule, no drafted report for `benign`, `window_start`/`window_end` on the persisted row, the review CLI functions (list, show, verdict, refusing to overwrite, unknown ids and verdicts) against a fake connection, and the on-demand report gate (`test_on_demand_report`: threshold, stored text reused, `--force`, provider failure stores nothing, concurrent loser).

Scoring: the flag carries the scored window's bounds, a high error triggers `investigate`, a low error does not, a frozen position votes with zero error, `--min-votes 2`, open-incident debounce, no re-investigation on the next poll, per-cycle cap with deferral and retry, short-history skip, `build_tools` registering the classifier stub. `test_review_stats.py` covers the two precision definitions, `unclear` leaving the denominator, the `unresolved` hypothesis never being a hit, and the "too few to trust" and per-row markers.

## Requires external services or cannot run in a clean checkout

By the code, not by trying:

- Nothing in the test suite needs an external service.
- Everything that is not a test does: ingestion needs an AISStream key and a database; training, evaluation, export and the scorer need a populated database; the scorer needs `ml/checkpoints/epoch_010.pt`; the Laya path needs the weights file. None of those files is in the repository.
- The importer, `score_checkpoint.run/main`, `train.run_training/main`, `laya_export._queue`, `live_scorer._serve` and `PostgresStore` are the largest pieces of real logic that no test executes.

## Gaps that matter

- No test runs SQL. The four large streaming queries (`POSITION_QUERY` and friends), `RECENT_REPORTS_QUERY`, `ACTIVE_VESSELS_QUERY`, `OPEN_INCIDENT_QUERY`, `JAMMING_ZONE_QUERY`, `TRACK_HISTORY_QUERY`, the incident insert, and `schema.sql` itself are exercised only through fakes that do not check the SQL. A typo would be found first against a real database.
- `train_from_shards`, `_run_epoch` and `run_training` have no test. `materialize_to_shards` is not tested against a stream. The shard writer and batch iterator are.
- `incident_history.find_similar_incidents`, `persist_incident` and the review queries are tested only against fakes. The new incidents SQL and the new `track_history` queries were also run once by hand against a scratch PostgreSQL 16 with PostGIS (a plain `vessel_position` table, not TimescaleDB); that run is not part of the suite.
- No test covers the collector's reconnect logic, `IngestionConfig`, `TimescaleWriter`, `RawArchiver`, or `ingestion/main.py` batching.
- `load_laya_predictor` is never run against the real library, so a mismatch with Laya's API would show up only when the model is enabled.
- `live_scorer`'s watermark and overlap logic is only exercised over one or two polls. Nothing tests a late-committed row landing inside the overlap.
- There is no end-to-end test that runs flag, investigate, persist against a database. The single recorded live report (`ghast_latest_report.md`) is the only evidence of that path, and it is not a test.
- No test measures detection quality. The offline F1 numbers come from `score_checkpoint.py` runs outside the suite.

## Stale comments and docs

- `agent/tests/test_state_machine.py` and `test_pattern_classifier.py` carry `skipif(OPERATING_THRESHOLD is None)` guards and comments saying the threshold is still `None`. It is 0.004946, so the guards never skip and the comments are out of date. `scoring/tests/test_live_scorer.py` has the same guard as a module-level `pytestmark`.
- `ml/evaluation/README.md` says "13 tests"; there are 36 in that directory.
- `ml/evaluation/README.md` describes two baseline detectors and one dataset loader; there are three detectors and a second loader.

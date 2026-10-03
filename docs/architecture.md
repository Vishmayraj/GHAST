# GHAST architecture

How the pieces fit together as of this checkout. Written from the code; where it disagrees with a README or plan, the code wins and the disagreement is listed in "Documentation that no longer matches" below. Detail lives in the per-area documents:

| Document | Covers |
|---|---|
| `docs/data-pipeline.md` | ingestion, historical import, MinIO archive, schema, live versus historical rows |
| `docs/infrastructure.md` | compose stack, services, environment variables, what a local run needs |
| `docs/ml-pipeline.md` | features, windows, BiLSTMNextDelta, training, checkpoints |
| `docs/scoring-and-evaluation.md` | `score_checkpoint`, detectors, threshold, live scorer |
| `docs/agent.md` | investigation flow, tools, hypothesis rules, report drafting |
| `docs/laya_pattern_classifier.md` | Laya export, model, benchmark, how the agent uses its vote |
| `docs/backend-and-frontend.md` | there is no backend; the site is static |
| `docs/testing.md` | test map, CI, gaps |
| `docs/DEVELOPER_GUIDE.md` | older recovery guide (partly superseded by the above) |
| `docs/HLD/`, `docs/hld-vs-current.md`, `ImplementationPlans/` | design intent, the gap between it and now, and the plans to close it; not descriptions of the current system |

## What GHAST is right now

A pipeline that stores AIS position reports, trains a small BiLSTM to predict each vessel's next position step, flags reports whose prediction error (or a simple rule) looks abnormal, and runs a fixed-rule investigation that writes an incident row. It has never been evaluated against real spoofing: real traffic has no labels, and the synthetic-spoof evaluation it used to have was removed. A committed incident report (`ghast_latest_report.md`) suggests it has produced at least one incident on live data; the repo does not record how that run was made. There is no API and no dashboard.

## End-to-end flow, corrected

The diagram in the task brief has the right ingredients in a slightly wrong order. This is what the code does:

```text
                 LIVE PATH (runs continuously)

AISStream websocket
    -> ingestion/           normalize, batch
    -> vessel_position, vessel_static     (message_type = AIS message name)
    -> MinIO ndjson archive               (write-only)

vessel_position (live rows)
    -> scoring/live_scorer.py, poll every 30 s
         latest 20 live reports per active vessel -> FeatureWindow
         BiLSTMNextDelta (CPU) -> per-report prediction error
         three detectors vote per report:
            prediction_error > OPERATING_THRESHOLD (0.004946)
            freeze_replay (reported SOG vs position-implied speed)
            speed_jump (off unless a threshold is given)
         strongest report with enough votes -> FlaggedAnomaly
    -> agent investigate()
         tools: track_history, jamming_zones, incident_history,
                pattern_classifier (Laya, optional)   <- inside the agent, before the decision
         freeze corroboration from track_history
         form_hypothesis: fixed rules -> hypothesis, confidence
         confidence >= 0.7 -> status reported (no report text; drafted later on request)
         otherwise         -> status escalated
    -> incidents table row

                 OFFLINE PATH (run by hand)

MarineCadastre CSV -> scripts/import_marinecadastre.py -> vessel_position (message_type = 'historical')

vessel_position (historical or live rows)
    -> ml/features: 20-report windows
    -> ml/training: shard cache, mini-batch train -> checkpoints/epoch_NNN.pt
    -> ml/evaluation/score_checkpoint.py: held-out vessels, real windows only, rates report
         (error percentiles, vote rates, flag rate, threshold for a target flag rate)
         -> value copied by hand into ml/models/bilstm/threshold.py
    -> ml/evaluation/laya_export.py queue: real window summaries -> review queue
         -> a person labels it -> laya_export.py build -> data/laya/*.jsonl
         -> fine-tune Laya elsewhere (Kaggle) -> ml/laya_model/ (weights not in repo)

                 NOT CONNECTED

backend/ (no code) and frontend/ (static site, mock data): nothing reads incidents
```

What differs from the brief's diagram:

1. Training and scoring are two separate processes joined only by a checkpoint file and a constant. "BiLSTMNextDelta then scoring" happens twice: offline in `score_checkpoint.py` on real held-out windows, and live in `live_scorer.py` on real reports.
2. The BiLSTM is one of three detectors, not the whole detector. The rule detectors run on the same window.
3. Laya is not a stage after the agent. It is one of the agent's tools, called before `form_hypothesis`, and its vote can only lift or apply a confidence cap.
4. The agent has no separate "hypothesis handling" stage after detection. It gathers evidence and forms the hypothesis in one call.
5. The final result is a row in `incidents`. Backend and frontend are not downstream of it.
6. Laya's training data is meant to come from human-labeled real windows. The model currently in use was trained on injected windows that have since been removed, so it has never seen a real labeled event.

## Components and where they live

```text
ingestion/            AISStream client, normalizer, TimescaleDB writer, MinIO archiver
scripts/              import_marinecadastre.py (historical loader)
backend/models/       schema.sql (the only backend content)
ml/features/          extract, pipeline (windows), summary (Laya text)
ml/models/bilstm/     model, infer, threshold
ml/training/          train, dataset_cache, window_policy
ml/evaluation/        score_checkpoint, baselines, harness, datasets, metrics, laya_export
ml/laya_model/        Laya config, tokenizer, benchmark (no weights)
agent/                orchestrator/state_machine, tools/, report_generator/
scoring/              live_scorer.py, Dockerfile
data/laya/            exported Laya train/holdout snapshot
frontend/site/        static site (mock data)
infra/docker/         compose stack, .env.example, MinIO build
docs/, ImplementationPlans/
```

Imports are flat per package (`from features.extract import ...`), and `scoring/live_scorer.py` and the agent reuse `ml/` code in place. There is one copy of feature extraction, used by training, evaluation and live scoring.

## Data, derived data, model artifacts

The full table is in `docs/ml-pipeline.md`. In short: PostgreSQL holds source data; the Laya review queue and labeled files (once they exist) and shard caches are derived experiment data; `epoch_NNN.pt`, the Laya model directory and MLflow runs are model artifacts. None of the model artifacts that matter are in git.

## Current status by component

| Component | Implemented | Tested | Used in a real run | Notes |
|---|---|---|---|---|
| live ingestion | yes | normalizer only | yes | no dedupe, no coordinate checks |
| MinIO raw archive | yes | no | yes | write-only |
| historical importer | yes | no | yes (April 2026) | not idempotent |
| schema | yes | no | yes | no migrations |
| feature extraction and windows | yes | yes (fakes) | yes | |
| BiLSTM training | yes | partly (helpers, not the epoch loop) | yes | `epoch_010.pt`, file not in repo |
| `score_checkpoint` | yes | helpers only | yes, elsewhere | results not reproducible from repo |
| threshold 0.004946 | yes | boundary tests | yes | chosen and reported on the same set |
| live scorer | yes | with fakes | yes | one recorded incident report |
| agent and tools | yes | with fakes | yes | rules and confidences are hand-set |
| Groq report | yes | fake client | yes | one committed report |
| Laya export (review queue, human labels) | yes | hand-built windows | no | `queue` not yet run against the database; no labels exist |
| Laya model | trained once, on removed injected data | benchmark recorded (injected data) | not by default | weights not in repo; uncalibrated threshold; never saw real labels |
| backend API | no | no | no | README only |
| dashboard | no | no | no | README only |
| landing site | yes | no | serves static files | mock data |

"Used in a real run" is taken from the repo's own records (threshold provenance, dev guide, committed report, Laya benchmark). It was not verified here.

## Implemented, experimental, proposed, broken

Implemented and working as far as the code and tests show: the ingestion path, feature and window code, the training loop, offline scoring code, the live scorer's polling and debounce logic, the agent's rules and persistence.

Experimental (in the code but unvalidated on real events): the freeze detector and corroboration, the single-detector confidence cap, the Laya vote, all confidence values.

Proposed and not built: per-vessel-class or per-region thresholds, tuning of the fleet clustering parameters, transformer models, automated jamming zone ingestion, database migrations.

Currently broken or misleading:

- Historical evaluation commands must use an April 2026 range (only April was imported).
- `notebooks/laya_finetune_ghast_kaggle_2xT4.ipynb` reads `data/laya/train.jsonl` and `holdout.jsonl`, which no longer exist until real labeled data is built.
- `agent/README.md`, `agent/orchestrator/README.md`, `agent/tools/README.md`, `ml/README.md`, `ml/training/README.md`, `infra/docker/README.md`, `README.md` describe an earlier state (list below).

## Known problems, separate from what works

Modeling and evaluation:

1. There is no quality measurement. The synthetic-spoof evaluation was removed, real traffic has no labels, and `OPERATING_THRESHOLD` is a placeholder from the removed evaluation. Offline scoring now reports rates only.
2. The BiLSTM is bidirectional and gets implied speed as an input, so its "next step prediction" can see the step it predicts through the following row. Not ablated.
3. The historical-trained model never saw `rate_of_turn` vary, and live rows contain it.
4. Prediction error is in raw degrees with no time-step input, so its scale depends on reporting interval, which differs by source. The threshold was chosen on historical data only.
5. The threshold came from F1 against injected spoofs (0.424, tuned and reported on the same set), with 19.4% of clean reports flagged. That number no longer has a source in the repo; re-derive it with `score_checkpoint`'s flag-rate report.
6. Live flag rate has not been measured. Run `score_checkpoint` with `--source live` and `--source historical` and compare.

Agent behavior:

7. (Fixed in the trust pass, kept for history.) `incident_history` matched `anomaly_type` across all vessels, so `targeted_spoof` was reachable only for the first incident of each type string. It now returns `same_vessel` and `same_pattern_elsewhere` separately and only `same_vessel` decides.
8. The original `benign` branch (score below threshold, no other vote) is still unreachable from the live scorer. A second rule makes `benign` reachable: a lone, barely-over-threshold `prediction_error` flag on a stationary window with no corroboration. Its constants are uncalibrated and the rule is pending owner sign-off.
9. (Fixed in the trust pass, kept for history.) `track_history` used to read 24 hours forward as well as back and freeze corroboration ran over all of it, so one frozen pair in 48 hours matched. It now stops at the flag, corroboration uses the last 20 reports, and it needs at least 3 frozen pairs (uncalibrated).
10. `status = 'resolved'` is now set only by an analyst verdict (`agent/review.py`). Until an incident is reviewed, any recent non-resolved incident still debounces new ones for that vessel, and no incident has been reviewed yet. The scorer's in-memory cooldown is intentionally left alone by a verdict (see `docs/agent.md`, "Analyst review").
11. (Fixed in the trust pass, kept for history.) Laya used to "agree" with any non-normal label. It now agrees only when the label is the one the hypothesis implies.

Operations:

12. `ingestion` stops on a database error (compose restarts it), and has no dedupe. A malformed timestamp now drops one message instead.
13. Compose has no healthchecks; `scoring` may start before the schema exists and retries.
14. `ml/checkpoints/epoch_010.pt` and the Laya weights exist only outside the repo.
15. The importer is not idempotent.

## What a local run actually requires

- To run tests: Python 3.12 and the package's requirements. No services.
- To collect live data: Docker, an AISStream key.
- To reproduce the model: the April 2026 MarineCadastre CSVs in `data/raw/`, the importer, then training from `ml/`.
- To run live scoring: live data in Postgres, plus `ml/checkpoints/epoch_010.pt` (not in the repo).
- To use Laya: the weights file and the `laya` package.

Details and variables: `docs/infrastructure.md`.

## Documentation that no longer matches the code

| File | What is wrong |
|---|---|
| `README.md` | layout table describes `backend/` as a FastAPI app (there is no app); says the compose stack brings up "TimescaleDB and MinIO" (five services now); says the HLD is at `/HLD` (it is `docs/HLD/`) |
| `docs/README.md` | says the HLD lives at repo-root `HLD/`; it is `docs/HLD/` |
| `docs/DEVELOPER_GUIDE.md` | report token default stated as 2000; code says 1200. Also has no pointer to the newer documents |
| `docs/laya_pattern_classifier.md` (previous version) | said no checkpoint had been fine-tuned or evaluated; a benchmark exists. Rewritten |
| `infra/docker/README.md` | wrong path for the MinIO Dockerfile; says `ml/` and `frontend/` are empty |
| `agent/README.md` | three tools and "exactly three" audit entries; four tools since the classifier |
| `agent/orchestrator/README.md` | four hypotheses without `freeze_replay`; suggests LangGraph |
| `agent/tools/README.md` | lists three tools |
| `ml/README.md`, `ml/training/README.md` | describe MLflow configs for training; training does not use MLflow |
| `backend/models/README.md` | its second paragraph describes only `vessel_position` and `vessel_static`; the schema also has `incidents` and `jamming_zones` |
| `ImplementationPlans/old/*` | superseded plans, kept for history; several status lines are stale and they describe the removed synthetic evaluation. Current plans are in `ImplementationPlans/` |
| `frontend/site/scripts/data/sample.js` | sample incidents with fictional vessels, tagged "Sample data" in the console; some evidence sentences (meters, seconds) describe things no stored field holds |

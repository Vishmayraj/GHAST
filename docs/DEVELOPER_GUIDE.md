# GHAST developer recovery guide

This is the current Stage 1 pipeline, based on the code in this checkout:

    MarineCadastre CSV / AIS Stream
      -> vessel_position + vessel_static in PostgreSQL/TimescaleDB
      -> features.pipeline feature windows
      -> BiLSTMNextDelta training checkpoint
      -> held-out, synthetic-injection checkpoint evaluation
      -> prediction error + detector votes
      -> scoring/live_scorer.py
      -> bounded agent investigation
      -> incidents table
      -> optional Groq report draft

## Get the infrastructure and data path running

From C:\Projects\GHAST\infra\docker, copy .env.example to .env, set the AIS
Stream key, then run docker compose up -d. The compose stack starts
TimescaleDB/PostGIS, MinIO, and ingestion; ingestion/storage.py applies
backend/models/schema.sql on startup.

Historical data is separate from live collection. Put MarineCadastre files named
ais-2026-04-*.csv under data/raw, set POSTGRES_DSN if the default is not right,
and run this from the repository root:

    python scripts/import_marinecadastre.py

The importer filters to the East Coast/Mid-Atlantic bounds, writes historical
rows with message_type = historical, and uses PostgreSQL COPY. The
vessel_position hypertable holds dynamic reports; vessel_static holds latest
static vessel data. incidents stores the decision, evidence, audit log, and
optional report text.

## Features, training, and the evaluated checkpoint

ml/features/extract.py derives numeric trajectory features.
ml/features/pipeline.py groups each vessel's ordered reports into non-overlapping
20-report FeatureWindows. Database reads use a server-side cursor and explicitly
select either live (not historical) or historical; the sources are never silently
mixed.

Install the ML test/runtime environment only when you are ready:

    cd C:\Projects\GHAST
    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r ml\requirements-dev.txt

Training is ml/training/train.py; the documented live command is:

    cd C:\Projects\GHAST\ml
    $env:POSTGRES_DSN = "postgresql://ghast:ghast@localhost:5432/ghast"
    python -m training.train --dsn $env:POSTGRES_DSN --source live --live-window initial --epochs 10

For historical development, --source historical requires explicit --start and
--end. Training materializes bounded .npz shards, uses a stable vessel-level
validation split, and writes checkpoints such as ml/checkpoints/epoch_010.pt.
A checkpoint contains model_state, optimizer state, epoch, and losses; loading
reconstructs models.bilstm.model.BiLSTMNextDelta(N_FEATURES).

The completed Stage 1 result is epoch_010.pt (epoch 10, train loss 0.0006055,
validation loss 0.0007673). Do not retrain merely to recover this state.

## Held-out evaluation and threshold

ml/evaluation/score_checkpoint.py selects held-out vessels, injects four
synthetic patterns into clean windows, calculates BiLSTM prediction errors, and
sweeps thresholds. It also measures prediction_error_detector,
speed_jump_detector, and freeze_replay_detector from evaluation/baselines.py.

Run a new evaluation only for a new checkpoint or source range:

    cd C:\Projects\GHAST\ml
    python -m evaluation.score_checkpoint --dsn $env:POSTGRES_DSN --checkpoint checkpoints/epoch_010.pt --source historical --eval-start 2026-05-01 --eval-end 2026-05-16 --seed 0

The live/historical choice is --source and evaluation must be independent of
training. The promoted value is OPERATING_THRESHOLD = 0.004946 in
ml/models/bilstm/threshold.py, chosen for F1 on the corrected full held-out run.
It produced F1 0.424, versus 0.224 for speed jump. Per-pattern F1: freeze/replay
0.244, gradual drift 0.793, impossible kinematics 0.161, teleport jump 0.312.
The control false-positive rate remains material, so this is not an autonomous
spoofing verdict.

## Live scoring and the agent

scoring/live_scorer.py is the integration entry point. It loads the checkpoint
once on CPU by default, queries live reports only, builds the same feature
window as training, scores prediction errors, and casts per-report votes.
Prediction error over OPERATING_THRESHOLD votes; freeze/replay uses the shared
displacement epsilon; speed jump is disabled unless --speed-jump-threshold is set.

It rescans a short overlap for late database commits, keeps a per-vessel
watermark to avoid duplicate scoring, skips vessels with a recent open incident,
bounds investigations per cycle, and retains cooldown after failed persistence.
It chooses the strongest new report in each window, so agreeing votes concern
the same report. Start with one database cycle:

    cd C:\Projects\GHAST
    python -m pip install -r ml\requirements.txt -r agent\requirements.txt
    $env:POSTGRES_DSN = "postgresql://ghast:ghast@localhost:5432/ghast"
    $env:GHAST_CHECKPOINT = "C:\Projects\GHAST\ml\checkpoints\epoch_010.pt"
    python scoring\live_scorer.py --once --min-votes 2

--min-votes 2 is the safer initial live setting. The default of one preserves
recall but single-detector flags are escalated rather than auto-reported.

The scorer permits 25 investigations per polling cycle by default
(`--max-investigations-per-cycle`). Strongest candidates run first; candidates beyond
the cap are retained in an in-memory deferred queue and retried on the next completed
cycle. Debounced candidates are skipped rather than queued, and a process restart
does not persist the transient queue.

For each flag, agent/orchestrator/state_machine.py calls track_history,
jamming_zones, and incident_history, adds sequence-based freeze/replay
corroboration, logs votes, forms an auditable hypothesis, then persists it.
Jamming can report at 0.85 confidence; freeze/replay corroboration at 0.8;
a single detector caps other non-benign hypotheses at 0.5. Below-threshold is
benign only when no other detector voted.

Reports are the only LLM call. The scorer loads the repository-root ignored .env
without overriding process environment values. If GROQ_API_KEY is present,
live_scorer creates Groq AsyncGroq and report_generator/report.py uses
openai/gpt-oss-120b by default. Set GHAST_REPORT_MODEL to select a Groq model and
GHAST_REPORT_MAX_TOKENS to change the 2000-token default. Without a key,
incidents persist without report_text.

## Laya pattern classifier (optional)

agent/tools/pattern_classifier.py is a fourth evidence source. Without a fine-tuned model it
is a neutral stub and nothing changes. See docs/laya_pattern_classifier.md to export data,
fine-tune, evaluate, and switch it on.

## Scoring as a container

The compose stack has a `scoring` service (scoring/Dockerfile, CPU-only torch). It mounts
ml/checkpoints read-only at /checkpoints, so copy epoch_010.pt there first (*.pt is
gitignored). It runs with --min-votes 2 by default; edit the Dockerfile CMD to change that.

## Tests and current status

CPU-fast checks do not need PostgreSQL, CUDA, MLflow, or LLM credentials:

    cd C:\Projects\GHAST\agent
    python -m pytest tests -v
    cd C:\Projects\GHAST\scoring
    python -m pytest tests -v
    cd C:\Projects\GHAST\ml
    python -m pytest features\tests training\tests models\bilstm\tests evaluation\tests -v

The agent CI workflow installs agent/requirements-dev.txt; this contains the
asyncpg and NumPy imports the agent reaches through ML feature corroboration.
ML CI installs ml/requirements-dev.txt and runs on CPU; CUDA is optional.

Complete: held-out evaluation, threshold provenance, detector implementations,
bounded agent logic, incident schema, and live scorer polling-to-incident path.
Implemented but requiring infrastructure: live scoring, checkpoint loading, and
incident persistence. Experimental: threshold calibration, freeze/replay
corroboration, and the single-vote confidence policy. Not implemented: a running
backend API despite its README scaffold, dashboard, and
automated database migrations. External live operation needs the AIS feed,
database, checkpoint, and (for reports) Groq key.
